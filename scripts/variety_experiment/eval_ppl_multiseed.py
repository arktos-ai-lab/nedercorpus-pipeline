"""
eval_ppl_multiseed.py — Evaluate per-variety perplexity across multiple training
seeds and report mean ± std, replacing the single-run point estimates in the
original eval_ppl.py.

Fixes two problems in the original experiment:
  1. No variance estimate — a single seed cannot distinguish "multi-variety
     training genuinely transfers" from "this run happened to land there."
     This script evaluates checkpoint_{split}_seedN for every seed found on
     disk and reports mean ± std per (split, variety).
  2. Per-variety PPL reported for holdouts as small as n=17 (SRN) is not a
     stable estimate. This script keeps per-variety numbers but pools every
     variety with fewer than MIN_STABLE_N holdout docs into a single
     "low-n (pooled)" row, and flags every remaining row with its n so
     readers can judge stability themselves.

Usage:
  python eval_ppl_multiseed.py --out_dir experiment_data --seeds 42 43 44

Output:
  experiment_data/results_multiseed.json
  experiment_data/results_multiseed.md
"""

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MIN_DOCS_PER_VARIETY = 10     # skip varieties with fewer holdout docs than this entirely
MIN_STABLE_N         = 100    # below this, pool into "low-n (pooled)" instead of reporting alone
MAX_DOCS_PER_VARIETY = 5000   # cap to avoid OOM on large varieties (NL: 100Ks of docs)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--out_dir", default="experiment_data", type=Path)
    p.add_argument("--seeds",   default=[42, 43, 44], type=int, nargs="+",
                    help="Training seeds to evaluate. A checkpoint_{split}_seed{N}/final "
                         "directory must exist for each (split, seed) pair.")
    p.add_argument("--splits",  default=["nl_only", "full", "gptnl_af_fy"], nargs="+",
                    help="Condition labels to evaluate and compare. Each needs a "
                         "checkpoint_{label}_seed{N}/final directory (produced by "
                         "train_lm.py --split for nl_only/full, or by "
                         "train_tier.py --label gptnl_af_fy --input "
                         "experiment_data/train_gptnl_af_fy.jsonl for the third). "
                         "The first split listed is the delta baseline in the table.")
    p.add_argument("--batch",   default=8, type=int)
    return p.parse_args()


def compute_ppl(model, tokenizer, texts: list[str], seq_len: int, batch_size: int, device) -> tuple[float, int]:
    """Return (per-token perplexity, n_eval_tokens) over texts."""
    import torch

    model.eval()
    total_loss = 0.0
    total_tokens = 0
    chunk = seq_len

    with torch.no_grad():
        buffer_ids: list[int] = []
        for text in texts:
            ids = tokenizer(text, add_special_tokens=False)["input_ids"]
            buffer_ids.extend(ids)
            buffer_ids.append(tokenizer.eos_token_id)

        windows = [buffer_ids[i:i + chunk] for i in range(0, len(buffer_ids) - chunk, chunk)]
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


def load_holdout(holdout_path: Path) -> dict[str, list[str]]:
    by_variety: dict[str, list[str]] = {}
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
    args = parse_args()

    import torch
    from transformers import AutoTokenizer, GPT2LMHeadModel

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    holdout_path = args.out_dir / "holdout.jsonl"
    print(f"Loading holdout from {holdout_path} …")
    by_variety = load_holdout(holdout_path)

    varieties = sorted(v for v, docs in by_variety.items() if len(docs) >= MIN_DOCS_PER_VARIETY)
    doc_counts = {v: len(by_variety[v]) for v in varieties}

    import random as _random
    _rng = _random.Random(42)  # fixed eval-sampling seed — independent of training seed
    for v in varieties:
        if len(by_variety[v]) > MAX_DOCS_PER_VARIETY:
            by_variety[v] = _rng.sample(by_variety[v], MAX_DOCS_PER_VARIETY)

    print(f"Varieties in holdout: {varieties}")
    for v in varieties:
        stable = "stable" if doc_counts[v] >= MIN_STABLE_N else "LOW-N, will be pooled"
        print(f"  {v:8s}  {doc_counts[v]:>6,} docs  ({stable})")

    # raw[split][seed][variety] = (ppl, n_tokens)
    raw: dict[str, dict[int, dict[str, float]]] = {}

    for split in args.splits:
        raw[split] = {}
        for seed in args.seeds:
            ckpt = args.out_dir / f"checkpoint_{split}_seed{seed}" / "final"
            if not ckpt.exists():
                print(f"\n[SKIP] {ckpt} not found — "
                      f"train condition '{split}' with seed {seed} first "
                      f"(train_lm.py --split for nl_only/full, train_tier.py "
                      f"--label for gptnl_af_fy)")
                continue

            print(f"\nEvaluating model: {split} / seed {seed} ({ckpt})")
            tokenizer = AutoTokenizer.from_pretrained(str(ckpt))
            model = GPT2LMHeadModel.from_pretrained(str(ckpt)).to(device)
            tokenizer.pad_token = tokenizer.eos_token

            raw[split][seed] = {}
            for v in varieties:
                ppl, n_tok = compute_ppl(model, tokenizer, by_variety[v],
                                          seq_len=512, batch_size=args.batch, device=device)
                raw[split][seed][v] = ppl
                print(f"  {v:8s}  PPL = {ppl:.1f}  (eval tokens: {n_tok:,})")

            del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    # ---------------------------------------------------------------------
    # Aggregate: mean ± std per (split, variety) across available seeds
    # ---------------------------------------------------------------------
    agg: dict[str, dict[str, dict]] = {}  # split -> variety -> {mean, std, n_seeds}
    for split, by_seed in raw.items():
        agg[split] = {}
        seeds_run = sorted(by_seed.keys())
        for v in varieties:
            vals = [by_seed[s][v] for s in seeds_run if s in by_seed and math.isfinite(by_seed[s].get(v, float("nan")))]
            if not vals:
                continue
            mean = statistics.mean(vals)
            std = statistics.stdev(vals) if len(vals) > 1 else float("nan")
            agg[split][v] = {"mean": round(mean, 1), "std": round(std, 2) if math.isfinite(std) else None,
                              "n_seeds": len(vals)}

    out = {
        "seeds_requested": args.seeds,
        "splits": args.splits,
        "holdout_doc_counts": doc_counts,
        "min_stable_n": MIN_STABLE_N,
        "raw_per_seed": raw,
        "aggregated": agg,
    }
    out_json = args.out_dir / "results_multiseed.json"
    with open(out_json, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nRaw + aggregated results -> {out_json}")

    # ---------------------------------------------------------------------
    # Markdown table: split varieties into "stable" (n>=MIN_STABLE_N) and
    # "low-n pooled" (reported as a single weighted-average row, heavily
    # caveated, never presented as a per-language number). Generalized over
    # however many conditions were actually evaluated (>=1 seed each);
    # the first entry in --splits is the delta baseline.
    # ---------------------------------------------------------------------
    evaluated_splits = [s for s in args.splits if agg.get(s)]
    if len(evaluated_splits) < 2:
        print(f"Need >=2 splits evaluated (>=1 seed each) to produce a comparison table. "
              f"Have: {evaluated_splits}")
        return
    baseline = evaluated_splits[0]
    others = evaluated_splits[1:]

    def has_all(v, splits):
        return all(v in agg[s] for s in splits)

    stable_vars = [v for v in varieties if doc_counts[v] >= MIN_STABLE_N and has_all(v, evaluated_splits)]
    lown_vars = [v for v in varieties if doc_counts[v] < MIN_STABLE_N and has_all(v, evaluated_splits)]

    n_seeds_done = min(
        (agg[baseline][v]["n_seeds"] for v in stable_vars), default=0
    )

    def fmt_cell(d):
        return f"{d['mean']:.1f} ± {d['std']:.1f}" if d["std"] is not None else f"{d['mean']:.1f} (1 seed)"

    header_cells = [f"{s} PPL" for s in evaluated_splits] + [f"Δ ({s} − {baseline})" for s in others]
    lines = [
        "## Multi-variety training experiment — perplexity by language variety (multi-seed)",
        "",
        f"All models: GPT-2-small (117M), {n_seeds_done} training seed(s) "
        f"({', '.join(str(s) for s in args.seeds)}), same fixed holdout across conditions. "
        f"Conditions: {', '.join(evaluated_splits)} "
        f"(baseline for deltas: **{baseline}**). "
        "Lower PPL is better. Cells show mean ± std across seeds; n = holdout document count. "
        "`gptnl_af_fy`, if present, is GPT-NL's own public corpus (nl+fy, incl. "
        "youtube-commons — external data, not NederCorpus) plus NederCorpus's own "
        "NL/FY/AF/LI/ZEA/PAP documents (each capped at 500M tokens) — see "
        "prepare_gptnl_af_fy_split.py for the leakage guard against this same holdout "
        "and the composition breakdown in train_gptnl_af_fy.meta.json.",
        "",
        "### Varieties with a stable holdout (n >= {})".format(MIN_STABLE_N),
        "",
        "| Variety | n (holdout) | " + " | ".join(header_cells) + " |",
        "|---------|------------:|" + "".join(["------------:|"] * len(header_cells)),
    ]
    for v in stable_vars:
        row = [fmt_cell(agg[s][v]) for s in evaluated_splits]
        deltas = [f"{agg[s][v]['mean'] - agg[baseline][v]['mean']:+.1f}" for s in others]
        note = " *" if v not in {"NL", "NL-BE"} else ""
        lines.append(f"| {v}{note} | {doc_counts[v]:,} | " + " | ".join(row + deltas) + " |")

    if lown_vars:
        lines += [
            "",
            f"### Low-n varieties (n < {MIN_STABLE_N}) — pooled, NOT reported individually",
            "",
            "Per-variety PPL on holdouts this small is not a stable estimate and is not "
            "reported as an individual number. Instead: a single document-count-weighted "
            "pooled PPL per condition across all low-n varieties.",
            "",
            "| Pooled group | Varieties | Total n | " + " | ".join(f"{s} PPL" for s in evaluated_splits) + " |",
            "|---|---|---:|" + "".join(["---:|"] * len(evaluated_splits)),
        ]
        total_n = sum(doc_counts[v] for v in lown_vars)
        pooled = {}
        for s in evaluated_splits:
            weighted_sum = sum(agg[s][v]["mean"] * doc_counts[v] for v in lown_vars)
            pooled[s] = weighted_sum / total_n if total_n else float("nan")
        pooled_cells = " | ".join(f"{pooled[s]:.1f}" for s in evaluated_splits)
        lines.append(f"| low-n | {', '.join(lown_vars)} | {total_n:,} | {pooled_cells} |")

    lines += [
        "",
        "* Minority variety — not present in the nl_only training split.",
        "",
        "**Reading the table:** Negative delta = the compared condition beats the baseline. "
        "NL / NL-BE rows show the cost of variety inclusion for the dominant language. "
        "Minority rows show the benefit. Std shown only where >=2 seeds completed for that "
        "condition — run all requested seeds before reporting this table in the paper. "
        "Token budgets differ across conditions by design (nl_only/full are capped at 1B; "
        "gptnl_af_fy's actual token count depends on GPT-NL config availability and the "
        "per-variety NederCorpus caps after holdout-leakage exclusion — see "
        "train_gptnl_af_fy.meta.json for the exact composition) — report each condition's "
        "actual token count from train_meta.json / train_gptnl_af_fy.meta.json alongside "
        "PPL, do not present this as a token-matched comparison.",
    ]

    table = "\n".join(lines)
    out_md = args.out_dir / "results_multiseed.md"
    out_md.write_text(table, encoding="utf-8")
    print(f"Table -> {out_md}")
    print()
    print(table)


if __name__ == "__main__":
    main()
