"""
eval_ppl.py — Evaluate per-variety perplexity for both trained models.

Reads the holdout.jsonl produced by prepare_splits.py and computes
per-token perplexity for each (model, variety) combination.

Usage:
  python eval_ppl.py --out_dir experiment_data

Output:
  experiment_data/results.json   — raw per-variety PPL numbers
  experiment_data/results.md     — formatted table for the paper
"""

import argparse
import json
import math
from pathlib import Path

MIN_DOCS_PER_VARIETY = 10    # skip varieties with too few holdout docs
MAX_DOCS_PER_VARIETY = 5000  # cap to avoid OOM on large varieties (NL: 407K docs)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--out_dir", default="experiment_data", type=Path)
    p.add_argument("--batch",   default=8, type=int)
    return p.parse_args()


def compute_ppl(model, tokenizer, texts: list[str], seq_len: int, batch_size: int, device) -> float:
    """Return per-token perplexity (exp of mean cross-entropy) over texts."""
    import torch

    model.eval()
    total_loss = 0.0
    total_tokens = 0

    chunk = seq_len

    with torch.no_grad():
        # Tokenise in batches
        buffer_ids: list[int] = []
        for text in texts:
            ids = tokenizer(text, add_special_tokens=False)["input_ids"]
            buffer_ids.extend(ids)
            buffer_ids.append(tokenizer.eos_token_id)

        # Slide through the buffer in seq_len windows
        windows = [
            buffer_ids[i : i + chunk]
            for i in range(0, len(buffer_ids) - chunk, chunk)
        ]
        if not windows:
            return float("nan")

        for i in range(0, len(windows), batch_size):
            batch = windows[i : i + batch_size]
            import torch as _t
            input_ids = _t.tensor(batch, dtype=_t.long, device=device)
            outputs = model(input_ids, labels=input_ids)
            loss = outputs.loss  # mean per-token CE over the batch
            n_tok = input_ids.numel()
            total_loss   += loss.item() * n_tok
            total_tokens += n_tok

    return math.exp(total_loss / total_tokens) if total_tokens > 0 else float("nan")


def main():
    args = parse_args()

    import torch
    from transformers import AutoTokenizer, GPT2LMHeadModel

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # Load holdout
    holdout_path = args.out_dir / "holdout.jsonl"
    print(f"Loading holdout from {holdout_path} …")
    by_variety: dict[str, list[str]] = {}
    with open(holdout_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                v = rec.get("variant", "NL")
                t = rec.get("text", "")
                if t:
                    by_variety.setdefault(v, []).append(t)
            except json.JSONDecodeError:
                continue

    varieties = sorted(v for v, docs in by_variety.items() if len(docs) >= MIN_DOCS_PER_VARIETY)
    # Cap each variety to avoid Python list OOM (5K docs × ~500 tok × 28B = ~70 MB)
    import random as _random
    _rng = _random.Random(42)
    for v in varieties:
        if len(by_variety[v]) > MAX_DOCS_PER_VARIETY:
            by_variety[v] = _rng.sample(by_variety[v], MAX_DOCS_PER_VARIETY)
    print(f"Varieties in holdout: {varieties}")
    for v in varieties:
        print(f"  {v:8s}  {len(by_variety[v]):>5,} docs")

    results: dict[str, dict[str, float]] = {}  # split → variety → ppl

    for split in ("nl_only", "full"):
        ckpt = args.out_dir / f"checkpoint_{split}" / "final"
        if not ckpt.exists():
            print(f"\n[SKIP] {ckpt} not found — train_lm.py --split {split} first")
            continue

        print(f"\nEvaluating model: {split} ({ckpt})")
        tokenizer = AutoTokenizer.from_pretrained(str(ckpt))
        model = GPT2LMHeadModel.from_pretrained(str(ckpt)).to(device)
        tokenizer.pad_token = tokenizer.eos_token

        results[split] = {}
        for v in varieties:
            docs = by_variety[v]
            ppl = compute_ppl(model, tokenizer, docs, seq_len=512, batch_size=args.batch, device=device)
            results[split][v] = round(ppl, 1)
            print(f"  {v:8s}  PPL = {ppl:.1f}")

        del model

    # Save raw results
    out_json = args.out_dir / "results.json"
    with open(out_json, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults → {out_json}")

    # Format markdown table
    if len(results) < 2:
        print("Need both splits evaluated to produce comparison table.")
        return

    nl_only = results.get("nl_only", {})
    full    = results.get("full", {})

    lines = [
        "## Multi-variety training experiment — perplexity by language variety",
        "",
        "Lower is better. Both models: GPT-2-small (117M), 1B token budget, same random seed.",
        "",
        f"| Variety | NL-only model PPL | Full NederCorpus PPL | Δ (full − NL-only) |",
        f"|---------|------------------:|---------------------:|-------------------:|",
    ]
    for v in varieties:
        a = nl_only.get(v, float("nan"))
        b = full.get(v, float("nan"))
        delta = b - a if math.isfinite(a) and math.isfinite(b) else float("nan")
        sign = "+" if delta > 0 else ""
        delta_str = f"{sign}{delta:.1f}" if math.isfinite(delta) else "—"
        # Flag minority varieties
        note = " ★" if v not in {"NL", "NL-BE"} else ""
        lines.append(f"| {v}{note} | {a:.1f} | {b:.1f} | {delta_str} |")

    lines += [
        "",
        "★ Minority variety — not present in NL-only training split.",
        "",
        "**Reading the table:** Negative Δ = full model wins. "
        "NL / NL-BE rows show the cost of variety inclusion for the dominant language. "
        "Minority rows show the benefit.",
    ]

    table = "\n".join(lines)
    out_md = args.out_dir / "results.md"
    out_md.write_text(table, encoding="utf-8")
    print(f"Table → {out_md}")
    print()
    print(table)


if __name__ == "__main__":
    main()
