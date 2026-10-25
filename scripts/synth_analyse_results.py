"""
Aggregate synth_results.csv into a model × variant × method comparison table.

Usage:
    python scripts/synth_analyse_results.py
    python scripts/synth_analyse_results.py --method baseline
    python scripts/synth_analyse_results.py --variant LI
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

RESULTS_CSV = Path("F:/NederCorpus/experiments/synth_results.csv")


def load(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--method", default=None)
    p.add_argument("--variant", default=None)
    args = p.parse_args()

    rows = load(RESULTS_CSV)
    if args.method:
        rows = [r for r in rows if r["method"] == args.method]
    if args.variant:
        rows = [r for r in rows if r["variant"] == args.variant]

    def _fval(row: dict, col: str) -> float:
        v = row.get(col, "") or ""
        try:
            return float(v)
        except ValueError:
            return 0.0

    # Group by variant × model × method → scores
    bs_groups: dict[tuple, list[float]] = defaultdict(list)
    chrf_groups: dict[tuple, list[float]] = defaultdict(list)
    bleu_groups: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        key = (r["variant"], r["model"], r["model_params"], r["model_license"], r["method"])
        bs_groups[key].append(_fval(r, "bertscore_f1"))
        chrf_groups[key].append(_fval(r, "chrf_pp"))
        bleu_groups[key].append(_fval(r, "bleu"))

    has_chrf = any(v > 0 for vals in chrf_groups.values() for v in vals)
    has_bleu = any(v > 0 for vals in bleu_groups.values() for v in vals)

    # Print table
    hdr = f"\n{'Variant':<8} {'Model':<20} {'Size':<6} {'License':<12} {'Method':<10} {'N':>4} {'BERTScore':>10}"
    if has_chrf:
        hdr += f" {'chrF++':>8}"
    if has_bleu:
        hdr += f" {'BLEU':>7}"
    print(hdr)
    print("-" * (len(hdr) - 1))

    sorted_keys = sorted(bs_groups.keys(), key=lambda k: (k[0], k[4], -mean(bs_groups[k])))
    for key in sorted_keys:
        variant, model, params, lic, method = key
        n = len(bs_groups[key])
        line = (f"{variant:<8} {model:<20} {params:<6} {lic:<12} {method:<10} "
                f"{n:>4} {mean(bs_groups[key]):>10.4f}")
        if has_chrf:
            c = mean(chrf_groups[key])
            line += f" {c:>8.1f}" if c > 0 else "        —"
        if has_bleu:
            b = mean(bleu_groups[key])
            line += f" {b:>7.1f}" if b > 0 else "       —"
        print(line)

    # Best model per variant per method (ranked by BERTScore)
    print("\n\n=== BEST MODEL PER VARIANT × METHOD ===\n")
    best: dict[tuple[str, str], tuple] = {}
    for key in bs_groups:
        variant, model, params, lic, method = key
        vk = (variant, method)
        if vk not in best or mean(bs_groups[key]) > mean(bs_groups[best[vk]]):
            best[vk] = key

    hdr2 = (f"{'Variant':<8} {'Method':<10} {'Best Model':<20} {'Size':<6} "
            f"{'License':<12} {'BERTScore':>10}")
    if has_chrf:
        hdr2 += f" {'chrF++':>8}"
    if has_bleu:
        hdr2 += f" {'BLEU':>7}"
    print(hdr2)
    print("-" * (len(hdr2) - 1))
    for (variant, method), key in sorted(best.items()):
        line = (f"{variant:<8} {method:<10} {key[1]:<20} {key[2]:<6} "
                f"{key[3]:<12} {mean(bs_groups[key]):>10.4f}")
        if has_chrf:
            c = mean(chrf_groups[key])
            line += f" {c:>8.1f}" if c > 0 else "        —"
        if has_bleu:
            b = mean(bleu_groups[key])
            line += f" {b:>7.1f}" if b > 0 else "       —"
        print(line)

    # Practical recommendation: smallest model within 0.02 BERTScore of best
    print("\n\n=== PRACTICAL MODEL PER VARIANT (smallest within 0.02 BERTScore of best) ===\n")
    print(f"{'Variant':<8} {'Method':<10} {'Recommended':<20} {'Size':<6} {'License':<12} {'BERTScore':>10}")
    print("-" * 68)
    for (variant, method), best_key in sorted(best.items()):
        best_bs = mean(bs_groups[best_key])
        # Find smallest model within threshold
        threshold = best_bs - 0.02
        candidates = [
            k for k in bs_groups
            if k[0] == variant and k[4] == method and mean(bs_groups[k]) >= threshold
        ]
        if not candidates:
            candidates = [best_key]
        # Sort by param size ascending
        def _size(k):
            try:
                return int(float(k[2].upper().replace("B", "")))
            except ValueError:
                return 9999
        candidates.sort(key=_size)
        rec = candidates[0]
        print(f"{variant:<8} {method:<10} {rec[1]:<20} {rec[2]:<6} {rec[3]:<12} "
              f"{mean(bs_groups[rec]):>10.4f}")


if __name__ == "__main__":
    main()
