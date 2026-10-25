"""
train_tier_resumable.py: train_tier.py with crash-safe resumption (repo CLAUDE.md).

Same model, tokenizer and hyperparameters as train_tier.py (GPT-2-small from scratch,
GroNLP/gpt2-small-dutch tokenizer, seq 1024, batch 4 x accum 8, lr 5e-4 cosine,
500 warmup steps, wd 0.01, fp16, 1 epoch, 1.1B-token buffer, seed 42). Differences:
  * the tokenized buffer is cached to <ckpt_dir>/tokens.npy (written atomically), so a
    restart does not re-tokenize;
  * chunks are served from a memory-mapped array instead of a datasets.Dataset copy;
  * save_steps=500 and training resumes from the newest checkpoint-* automatically;
  * --max_steps allows a short timing run.

Usage:
  python train_tier_resumable.py --input tier_splits_v2/tier1_nl.jsonl --label v2_tier1_nl
"""
import argparse, json, os, sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOKENIZER_ID = "GroNLP/gpt2-small-dutch"
SEQ_LEN = 1024
BATCH_SIZE = 4
GRAD_ACCUM = 8
LR = 5e-4
WARMUP_STEPS = 500


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--label", required=True)
    p.add_argument("--out_dir", default="checkpoints", type=Path)
    p.add_argument("--seed", default=42, type=int)
    p.add_argument("--max_tokens", default=1_100_000_000, type=int)
    p.add_argument("--max_steps", default=-1, type=int, help="timing runs only")
    p.add_argument("--save_steps", default=500, type=int)
    return p.parse_args()


def build_token_cache(jsonl_path, tokenizer, max_tokens, cache):
    import numpy as np
    if cache.exists():
        print(f"  Using cached tokens {cache}", flush=True)
        return
    buf = np.empty(max_tokens, dtype=np.int32)
    pos = docs = 0
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
            docs += 1
            if docs % 100_000 == 0:
                print(f"    {docs:,} docs | {pos // 1_000_000}M tokens ...", flush=True)
    print(f"  Total: {pos:,} tokens from {docs:,} docs", flush=True)
    if pos < max_tokens * 0.5:
        print("  WARNING: split exhausted well under the buffer size.", flush=True)
    tmp = cache.with_name("tokens.tmp.npy")
    np.save(tmp, buf[:pos])
    os.replace(tmp, cache)
    (cache.parent / "tokens_meta.json").write_text(json.dumps({"tokens": int(pos), "docs": docs}))


class ChunkDataset:
    """Serves fixed-length chunks from a memory-mapped token array (opened lazily per worker)."""
    def __init__(self, path, seq_len):
        import numpy as np
        self.path, self.seq_len = str(path), seq_len
        self.n = len(np.load(self.path, mmap_mode="r")) // seq_len
        self._arr = None

    def __len__(self):
        return self.n

    def __getitem__(self, i):
        import numpy as np, torch
        if self._arr is None:
            self._arr = np.load(self.path, mmap_mode="r")
        row = torch.from_numpy(np.asarray(self._arr[i * self.seq_len:(i + 1) * self.seq_len], dtype=np.int64))
        return {"input_ids": row, "attention_mask": torch.ones_like(row), "labels": row.clone()}

    def __getstate__(self):
        d = dict(self.__dict__); d["_arr"] = None; return d


def main():
    args = parse_args()
    import torch
    from transformers import AutoTokenizer, GPT2Config, GPT2LMHeadModel, Trainer, TrainingArguments, set_seed

    set_seed(args.seed)
    ckpt_dir = args.out_dir / f"checkpoint_{args.label}_seed{args.seed}"
    if (ckpt_dir / "final" / "train_meta.json").exists() and args.max_steps < 0:
        print(f"[skip] {ckpt_dir/'final'} already complete"); return
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_ID)
    tokenizer.pad_token = tokenizer.eos_token
    cache = ckpt_dir / "tokens.npy"
    build_token_cache(args.input, tokenizer, args.max_tokens, cache)
    dataset = ChunkDataset(cache, SEQ_LEN)
    actual_tokens = json.loads((ckpt_dir / "tokens_meta.json").read_text())["tokens"]

    config = GPT2Config(
        vocab_size=tokenizer.vocab_size, n_positions=SEQ_LEN,
        n_embd=768, n_layer=12, n_head=12, n_inner=3072,
        resid_pdrop=0.1, embd_pdrop=0.1, attn_pdrop=0.1,
        bos_token_id=tokenizer.bos_token_id, eos_token_id=tokenizer.eos_token_id,
    )
    model = GPT2LMHeadModel(config)
    n_params = sum(p.numel() for p in model.parameters())
    total_steps = len(dataset) // (BATCH_SIZE * GRAD_ACCUM)
    print(f"Training {args.label}: {len(dataset):,} chunks, {total_steps:,} steps, "
          f"{actual_tokens/1e9:.2f}B tokens, {n_params/1e6:.0f}M params, seed={args.seed}", flush=True)

    out = ckpt_dir if args.max_steps < 0 else ckpt_dir / "_timing"
    training_args = TrainingArguments(
        output_dir=str(out), num_train_epochs=1, max_steps=args.max_steps,
        per_device_train_batch_size=BATCH_SIZE, gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, lr_scheduler_type="cosine", warmup_steps=WARMUP_STEPS,
        weight_decay=0.01, fp16=torch.cuda.is_available(), logging_steps=100 if args.max_steps < 0 else 10,
        save_steps=args.save_steps, save_total_limit=2, seed=args.seed,
        dataloader_num_workers=2, report_to="none",
    )
    trainer = Trainer(model=model, args=training_args, train_dataset=dataset)
    last = None
    if args.max_steps < 0:
        cks = sorted(ckpt_dir.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
        cks = [c for c in cks if (c / "trainer_state.json").exists()]
        last = str(cks[-1]) if cks else None
    print(f"Starting training (resume_from_checkpoint={last}) ...", flush=True)
    trainer.train(resume_from_checkpoint=last)
    if args.max_steps > 0:
        print("timing run done", flush=True); return
    trainer.save_model(str(ckpt_dir / "final"))
    tokenizer.save_pretrained(str(ckpt_dir / "final"))
    meta = {"label": args.label, "input": str(args.input), "seed": args.seed,
            "actual_tokens": actual_tokens, "n_params": n_params}
    (ckpt_dir / "final" / "train_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\nCheckpoint saved -> {ckpt_dir / 'final'}", flush=True)


if __name__ == "__main__":
    main()
