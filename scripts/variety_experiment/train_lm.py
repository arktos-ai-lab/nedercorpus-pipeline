"""
train_lm.py — Train GPT-2-small from scratch on a NederCorpus split.

Uses the GroNLP/gpt2-small-dutch TOKENIZER (Dutch BPE vocabulary) but
initialises the model weights from scratch so both runs start identically.

Usage (run twice — once per split):
  python train_lm.py --split nl_only --out_dir experiment_data
  python train_lm.py --split full    --out_dir experiment_data

Requirements:
  pip install transformers datasets accelerate
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOKENIZER_ID  = "GroNLP/gpt2-small-dutch"
SEQ_LEN       = 1024
BATCH_SIZE    = 16   # 24GB VRAM (RTX 3090/4090): batch=16 seq_len=1024 fp16 fits comfortably.
                     # (previous 12GB/RTX 3060 run used batch=4 — no gradient checkpointing was
                     # ever actually enabled here despite the old comment; batch=4 just fit on its own.)
GRAD_ACCUM    = 2    # effective batch = 32, unchanged from the 1B baseline run for comparability
LR            = 5e-4
WARMUP_STEPS  = 500


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--split",   required=True, choices=["nl_only", "full"])
    p.add_argument("--out_dir", default="experiment_data", type=Path)
    p.add_argument("--epochs",  default=1, type=int)
    p.add_argument("--seed",    default=42, type=int,
                    help="Training seed. Controls weight init AND data shuffling order "
                         "(train_full.jsonl / train_nl_only.jsonl themselves are fixed "
                         "across seeds — only init + batch order vary). Run with "
                         "--seed 42/43/44 to get the 3 replicates results.md needs.")
    p.add_argument("--target_tokens", default=1_000_000_000, type=int,
                    help="Must match the --target_tokens passed to prepare_splits.py for this "
                         "out_dir -- sizes the tokenize buffer with 10 percent headroom. Too "
                         "small silently truncates training data.")
    return p.parse_args()


def stream_tokenize_to_dataset(jsonl_path: Path, tokenizer, seq_len: int, target_tokens: int):
    """Stream JSONL → numpy int32 buffer → HF Dataset. Never holds all text or token IDs as Python objects."""
    import numpy as np
    from datasets import Dataset

    # Allocate with 10% headroom over the split's token budget. At 4 bytes/int32.
    MAX_TOKENS = int(target_tokens * 1.1)
    buf = np.empty(MAX_TOKENS, dtype=np.int32)
    pos = 0
    doc_count = 0

    print(f"  Streaming tokenise from {jsonl_path.name} …", flush=True)
    with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                text = json.loads(line)["text"]
            except (json.JSONDecodeError, KeyError):
                continue
            ids = tokenizer.encode(text, add_special_tokens=False)
            n = len(ids)
            if pos + n + 1 > MAX_TOKENS:
                print(f"  Buffer full at {pos:,} tokens — stopping early.", flush=True)
                break
            buf[pos : pos + n] = ids
            pos += n
            buf[pos] = tokenizer.eos_token_id
            pos += 1
            doc_count += 1
            if doc_count % 100_000 == 0:
                print(f"    {doc_count:,} docs | {pos // 1_000_000}M tokens …", flush=True)

    print(f"  Total: {pos:,} tokens from {doc_count:,} docs", flush=True)

    n_chunks = pos // seq_len
    chunks = buf[:n_chunks * seq_len].reshape(n_chunks, seq_len)
    print(f"  Chunks of {seq_len}: {n_chunks:,}", flush=True)

    # Build Arrow Dataset via generator — avoids materialising list-of-lists
    def _gen():
        for i in range(n_chunks):
            row = chunks[i].tolist()
            yield {"input_ids": row, "attention_mask": [1] * seq_len, "labels": row}

    dataset = Dataset.from_generator(_gen)
    del buf, chunks  # free numpy before training starts
    return dataset


def main():
    args = parse_args()

    import torch
    from transformers import (
        AutoTokenizer,
        GPT2Config,
        GPT2LMHeadModel,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    set_seed(args.seed)

    # Tokenizer (Dutch BPE vocabulary)
    print(f"Loading tokenizer from {TOKENIZER_ID} …")
    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_ID)
    tokenizer.pad_token = tokenizer.eos_token

    # Fresh GPT-2-small weights — same config, random init
    config = GPT2Config(
        vocab_size=tokenizer.vocab_size,
        n_positions=SEQ_LEN,
        n_embd=768,
        n_layer=12,
        n_head=12,
        n_inner=3072,
        resid_pdrop=0.1,
        embd_pdrop=0.1,
        attn_pdrop=0.1,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
    model = GPT2LMHeadModel(config)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model: GPT-2-small from scratch, {n_params/1e6:.0f}M parameters")

    # Load train data (streaming — never loads all text into RAM)
    train_path = args.out_dir / f"train_{args.split}.jsonl"
    print(f"\nTokenising {train_path} …")
    dataset = stream_tokenize_to_dataset(train_path, tokenizer, SEQ_LEN, args.target_tokens)

    # Output dir — seed-suffixed so multiple replicates never collide
    ckpt_dir = args.out_dir / f"checkpoint_{args.split}_seed{args.seed}"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    total_steps = (len(dataset) // (BATCH_SIZE * GRAD_ACCUM)) * args.epochs
    print(f"\nTraining: {len(dataset):,} chunks, {total_steps:,} steps, seed={args.seed}")

    training_args = TrainingArguments(
        output_dir=str(ckpt_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_steps=WARMUP_STEPS,
        weight_decay=0.01,
        fp16=torch.cuda.is_available(),
        logging_steps=100,
        save_steps=2000,
        save_total_limit=2,
        seed=args.seed,
        dataloader_num_workers=2,
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
    )

    last_ckpt = None
    if ckpt_dir.exists():
        from transformers.trainer_utils import get_last_checkpoint
        last_ckpt = get_last_checkpoint(str(ckpt_dir))
        if last_ckpt:
            print(f"Resuming from checkpoint {last_ckpt}")

    print("Starting training …")
    trainer.train(resume_from_checkpoint=last_ckpt)
    trainer.save_model(str(ckpt_dir / "final"))
    tokenizer.save_pretrained(str(ckpt_dir / "final"))
    print(f"\nCheckpoint saved → {ckpt_dir / 'final'}")


if __name__ == "__main__":
    main()
