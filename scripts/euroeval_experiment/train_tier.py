"""
train_tier.py — Train GPT-2-small from scratch on one tier-split for the
license-tier / data-abundance ablation.

Generalizes variety_experiment/train_lm.py to take an arbitrary input JSONL
(any of tier1_nl.jsonl ... tier3_all.jsonl, gptnl_1b.jsonl, gptnl_8b.jsonl)
instead of hardcoding two fixed splits.

Usage:
  python train_tier.py --input tier_splits/tier1_nl.jsonl  --label tier1_nl
  python train_tier.py --input tier_splits/gptnl_8b.jsonl  --label gptnl_8b --max_tokens 8_500_000_000

Requirements:
  pip install transformers datasets accelerate torch
"""

import argparse
import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOKENIZER_ID = "GroNLP/gpt2-small-dutch"
SEQ_LEN      = 1024
BATCH_SIZE   = 4
GRAD_ACCUM   = 8
LR           = 5e-4
WARMUP_STEPS = 500


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input",      required=True, type=Path,
                    help="Path to a tier-split JSONL (e.g. tier_splits/tier1_nl.jsonl)")
    p.add_argument("--label",      required=True,
                    help="Checkpoint label, e.g. tier1_nl, gptnl_8b")
    p.add_argument("--out_dir",    default="checkpoints", type=Path)
    p.add_argument("--epochs",     default=1, type=int)
    p.add_argument("--seed",       default=42, type=int)
    p.add_argument("--max_tokens", default=1_100_000_000, type=int,
                    help="Buffer cap. Set to ~9_000_000_000 for gptnl_8b / gptnl_36b "
                         "(those are near-full-corpus splits, NOT 1B-token splits — "
                         "training on them costs ~8x a tier1-3 run. Budget accordingly.")
    return p.parse_args()


def stream_tokenize_to_dataset(jsonl_path: Path, tokenizer, seq_len: int, max_tokens: int):
    import numpy as np
    from datasets import Dataset

    buf = np.empty(max_tokens, dtype=np.int32)
    pos = 0
    doc_count = 0

    print(f"  Streaming tokenise from {jsonl_path} ...", flush=True)
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
            if pos + n + 1 > max_tokens:
                print(f"  Buffer full at {pos:,} tokens -- stopping early.", flush=True)
                break
            buf[pos:pos + n] = ids
            pos += n
            buf[pos] = tokenizer.eos_token_id
            pos += 1
            doc_count += 1
            if doc_count % 100_000 == 0:
                print(f"    {doc_count:,} docs | {pos // 1_000_000}M tokens ...", flush=True)

    print(f"  Total: {pos:,} tokens from {doc_count:,} docs", flush=True)
    if pos < max_tokens * 0.5:
        print(f"  WARNING: split exhausted at {pos/1e9:.2f}B tokens, well under the "
              f"{max_tokens/1e9:.2f}B buffer -- this split does not contain enough eligible "
              f"data to reach the intended budget. Record the actual token count used.",
              flush=True)

    n_chunks = pos // seq_len
    chunks = buf[:n_chunks * seq_len].reshape(n_chunks, seq_len)
    print(f"  Chunks of {seq_len}: {n_chunks:,}", flush=True)

    def _gen():
        for i in range(n_chunks):
            row = chunks[i].tolist()
            yield {"input_ids": row, "attention_mask": [1] * seq_len, "labels": row}

    dataset = Dataset.from_generator(_gen)
    del buf, chunks
    return dataset, pos


def main():
    args = parse_args()

    import torch
    from transformers import (
        AutoTokenizer, GPT2Config, GPT2LMHeadModel,
        Trainer, TrainingArguments, set_seed,
    )

    set_seed(args.seed)

    print(f"Loading tokenizer from {TOKENIZER_ID} ...")
    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_ID)
    tokenizer.pad_token = tokenizer.eos_token

    config = GPT2Config(
        vocab_size=tokenizer.vocab_size,
        n_positions=SEQ_LEN,
        n_embd=768, n_layer=12, n_head=12, n_inner=3072,
        resid_pdrop=0.1, embd_pdrop=0.1, attn_pdrop=0.1,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
    model = GPT2LMHeadModel(config)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model: GPT-2-small from scratch, {n_params/1e6:.0f}M parameters")

    dataset, actual_tokens = stream_tokenize_to_dataset(
        args.input, tokenizer, SEQ_LEN, args.max_tokens
    )

    ckpt_dir = args.out_dir / f"checkpoint_{args.label}_seed{args.seed}"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    total_steps = (len(dataset) // (BATCH_SIZE * GRAD_ACCUM)) * args.epochs
    print(f"\nTraining {args.label}: {len(dataset):,} chunks, {total_steps:,} steps, "
          f"~{actual_tokens/1e9:.2f}B actual tokens, seed={args.seed}")

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

    trainer = Trainer(model=model, args=training_args, train_dataset=dataset)

    print("Starting training ...")
    trainer.train()
    trainer.save_model(str(ckpt_dir / "final"))
    tokenizer.save_pretrained(str(ckpt_dir / "final"))

    meta = {"label": args.label, "input": str(args.input), "seed": args.seed,
            "actual_tokens": actual_tokens, "n_params": n_params}
    (ckpt_dir / "final" / "train_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\nCheckpoint saved -> {ckpt_dir / 'final'}")


if __name__ == "__main__":
    main()
