"""
eval_ppl_tiers.py — Fallback comparison metric for the local #3 EuroEval run.

EuroEval's own Dutch downstream benchmark evaluation never worked in this
environment (WSL2/vLLM pinned-memory deadlock, unresolved after multiple
fix attempts — see run_all_tiers_local.sh comments), so the 8 checkpoints
trained by that pipeline (tier1_nl/all, tier2_nl/all, tier3_nl/all,
gptnl_1b, gptnl_8b) have no downstream task scores. This script computes
per-variety perplexity on the same fixed holdout set used by the
variety_experiment ablation (§6.7), giving a real tier-vs-tier and
tier-vs-GPT-NL comparison in place of the EuroEval numbers that couldn't
be produced. Single seed per condition (these were single training runs,
not the 3-seed replication that's #4) — no mean±std here.

Usage:
    python eval_ppl_tiers.py --checkpoints_dir checkpoints \
        --holdout ../../experiment_data/holdout.jsonl \
        --conditions tier1_nl tier1_all tier2_nl tier2_all tier3_nl tier3_all gptnl_1b gptnl_8b
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MIN_DOCS_PER_VARIETY = 10
MIN_STABLE_N = 100
MAX_DOCS_PER_VARIETY = 5000
SEQ_LEN = 512


def compute_ppl(model, tokenizer, texts, batch_size, device):
    import torch

    model.eval()
    total_loss = 0.0
    total_tokens = 0

    with torch.no_grad():
        buffer_ids = []
        for text in texts:
            ids = tokenizer(text, add_special_tokens=False)["input_ids"]
            buffer_ids.extend(ids)
            buffer_ids.append(tokenizer.eos_token_id)

        windows = [buffer_ids[i:i + SEQ_LEN] for i in range(0, len(buffer_ids) - SEQ_LEN, SEQ_LEN)]
        if not windows:
            return float("nan"), 0

        for i in range(0, len(windows), batch_size):
            batch = windows[i:i + batch_size]
            input_ids = torch.tensor(batch, dtype=torch.long, device=device)
            outputs = model(input_ids, labels=input_ids)
            n_tok = input_ids.numel()
            total_loss += outputs.loss.item() * n_tok
            total_tokens += n_tok

    if total_tokens == 0:
        return float("nan"), 0
    return math.exp(total_loss / total_tokens), total_tokens


def load_holdout(holdout_path: Path):
    by_variety = {}
    with open(holdout_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            v = rec.get("variant", "NL")
            t = rec.get("text", "")
            if t:
                by_variety.setdefault(v, []).append(t)
    return by_variety


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoints_dir", type=Path, default=Path("checkpoints"))
    ap.add_argument("--holdout", type=Path, default=Path("../../experiment_data/holdout.jsonl"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--conditions", nargs="+",
                     default=["tier1_nl", "tier1_all", "tier2_nl", "tier2_all",
                              "tier3_nl", "tier3_all", "gptnl_1b", "gptnl_8b"])
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--out_json", type=Path, default=Path("results_tiers_ppl.json"))
    ap.add_argument("--out_md", type=Path, default=Path("results_tiers_ppl.md"))
    args = ap.parse_args()

    import torch
    from transformers import AutoTokenizer, GPT2LMHeadModel

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    print(f"Loading holdout from {args.holdout} …")
    by_variety = load_holdout(args.holdout)
    varieties = sorted(v for v, docs in by_variety.items() if len(docs) >= MIN_DOCS_PER_VARIETY)
    doc_counts = {v: len(by_variety[v]) for v in varieties}

    import random
    rng = random.Random(42)
    for v in varieties:
        if len(by_variety[v]) > MAX_DOCS_PER_VARIETY:
            by_variety[v] = rng.sample(by_variety[v], MAX_DOCS_PER_VARIETY)

    print(f"Varieties in holdout: {varieties}")
    for v in varieties:
        stable = "stable" if doc_counts[v] >= MIN_STABLE_N else "LOW-N, will be pooled"
        print(f"  {v:8s}  {doc_counts[v]:>6,} docs  ({stable})")

    results = {}
    for cond in args.conditions:
        ckpt = args.checkpoints_dir / f"checkpoint_{cond}_seed{args.seed}" / "final"
        if not ckpt.exists():
            print(f"\n[SKIP] {ckpt} not found")
            continue

        print(f"\nEvaluating: {cond} ({ckpt})")
        tokenizer = AutoTokenizer.from_pretrained(str(ckpt))
        model = GPT2LMHeadModel.from_pretrained(str(ckpt)).to(device)
        tokenizer.pad_token = tokenizer.eos_token

        results[cond] = {}
        for v in varieties:
            ppl, n_tok = compute_ppl(model, tokenizer, by_variety[v], args.batch, device)
            results[cond][v] = {"ppl": round(ppl, 1) if math.isfinite(ppl) else None, "n_tokens": n_tok}
            print(f"  {v:8s}  PPL = {ppl:.1f}  (eval tokens: {n_tok:,})")

        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    out = {
        "seed": args.seed,
        "conditions_requested": args.conditions,
        "conditions_evaluated": list(results.keys()),
        "holdout_doc_counts": doc_counts,
        "min_stable_n": MIN_STABLE_N,
        "results": results,
    }
    args.out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out_json}")

    evaluated = list(results.keys())
    stable_vars = [v for v in varieties if doc_counts[v] >= MIN_STABLE_N and all(v in results[c] for c in evaluated)]
    lown_vars = [v for v in varieties if doc_counts[v] < MIN_STABLE_N and all(v in results[c] for c in evaluated)]

    lines = [
        "## Local #3 fallback: per-tier perplexity (EuroEval itself never produced scores)",
        "",
        f"Single seed ({args.seed}) per condition — these are the 8 individual training runs "
        f"from run_all_tiers_local.sh, not the 3-seed replication (that's the separate #4 "
        f"vast.ai run). Same fixed holdout as the §6.7 ablation. Lower PPL is better.",
        "",
        "### Stable varieties (n >= {})".format(MIN_STABLE_N),
        "",
        "| Variety | n | " + " | ".join(evaluated) + " |",
        "|---|---:|" + "".join(["---:|"] * len(evaluated)),
    ]
    for v in stable_vars:
        row = " | ".join(f"{results[c][v]['ppl']:.1f}" if results[c][v]['ppl'] is not None else "n/a" for c in evaluated)
        lines.append(f"| {v} | {doc_counts[v]:,} | {row} |")

    if lown_vars:
        lines += ["", f"### Low-n varieties (n < {MIN_STABLE_N}) — pooled", "",
                  "| Pooled | Varieties | Total n | " + " | ".join(evaluated) + " |",
                  "|---|---|---:|" + "".join(["---:|"] * len(evaluated))]
        total_n = sum(doc_counts[v] for v in lown_vars)
        pooled_cells = []
        for c in evaluated:
            w = sum(results[c][v]["ppl"] * doc_counts[v] for v in lown_vars if results[c][v]["ppl"] is not None)
            pooled_cells.append(f"{w/total_n:.1f}" if total_n else "n/a")
        lines.append(f"| low-n | {', '.join(lown_vars)} | {total_n:,} | " + " | ".join(pooled_cells) + " |")

    table = "\n".join(lines)
    args.out_md.write_text(table, encoding="utf-8")
    print(f"Table -> {args.out_md}")
    print()
    print(table)


if __name__ == "__main__":
    main()
