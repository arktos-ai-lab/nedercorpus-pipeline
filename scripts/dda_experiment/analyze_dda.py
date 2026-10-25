"""
analyze_dda.py — DDA Experiment Phase 2: compute diversity/coverage metrics
                 from saved embeddings and produce a comparison report.

Reads: embeddings/<label>/<model_key>.npy  (float32 [N, D], L2-normalized)
       embeddings/<label>/<model_key>.json  (metadata)

Metrics per (corpus, model) — then averaged across models:
  vendi_score    — Vendi Score on 5K subsample (exp of kernel matrix entropy)
                   Higher = more diverse.
  mean_nn_dist   — Mean cosine distance to k=10 nearest neighbours (1 - cosine_sim)
                   Higher = less redundant / more spread.
  near_dup_ratio — Fraction of docs with NN cosine_sim > 0.95
                   Lower = cleaner corpus.
  eff_rank       — Effective rank: exp(H(σ²)) where σ are singular values
                   Higher = more dimensions used.
  coverage_vs_*  — Cross-corpus: for each NederCorpus doc, mean NN distance
                   to GPT-NL embedding space (and vice versa).
                   Higher = more unique territory.

Output:
  dda_results/metrics_per_model.json   — raw per (corpus, model) metrics
  dda_results/metrics_summary.json     — averaged across models
  dda_results/report.md                — human-readable comparison table

Usage:
  python analyze_dda.py
  python analyze_dda.py --embed_dir embeddings/ --k 10 --vendi_n 5000
  python analyze_dda.py --reference gptnl_8b    # cross-coverage vs this label

Requirements:
  pip install numpy scipy scikit-learn tqdm
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

EMBED_DIR   = Path(__file__).resolve().parent / "embeddings"
OUT_DIR     = Path(__file__).resolve().parent / "dda_results"

K_NN        = 10       # neighbours for redundancy metrics
VENDI_N     = 5_000    # subsample for Vendi Score (O(N²) kernel computation)
XCOVER_N    = 10_000   # subsample for cross-corpus coverage
EFF_RANK_N  = 20_000   # subsample for SVD-based effective rank
NEAR_DUP_THRESH = 0.95 # cosine similarity threshold for near-duplicate detection


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def vendi_score(vecs: np.ndarray, n: int, rng: np.random.Generator) -> float:
    """
    Vendi Score: exp(H(K/n)) where K_ij = exp(-||x_i - x_j||^2 / 2).
    Computed on a random subsample of n vectors.
    vecs must be L2-normalized (cosine similarity = dot product).
    """
    if len(vecs) > n:
        idx = rng.choice(len(vecs), n, replace=False)
        vecs = vecs[idx]
    n = len(vecs)
    # cosine similarity matrix (vecs are normalized, so dot product = cosine)
    K = vecs @ vecs.T
    # RBF kernel: K_ij = exp((cos_sim - 1) / 0.5)  — maps [−1,1] → (0,1]
    K = np.exp((K - 1.0) / 0.5)
    K /= n
    # Eigenvalues
    eigvals = np.linalg.eigvalsh(K)
    eigvals = eigvals[eigvals > 1e-10]
    eigvals /= eigvals.sum()
    entropy = -float(np.sum(eigvals * np.log(eigvals)))
    return float(math.exp(entropy))


def mean_nn_dist_and_near_dup(
    vecs: np.ndarray, k: int, rng: np.random.Generator, subsample: int = 20_000
) -> tuple[float, float]:
    """
    Returns (mean_cosine_distance_to_k_NN, near_dup_ratio).
    Uses exact brute-force on a subsample.
    vecs must be L2-normalized.
    """
    from sklearn.metrics.pairwise import cosine_similarity

    if len(vecs) > subsample:
        idx = rng.choice(len(vecs), subsample, replace=False)
        vecs = vecs[idx]

    n = len(vecs)
    k = min(k, n - 1)

    # Process in chunks to avoid N×N memory
    chunk = 1000
    nn_dists   = []
    near_dup_count = 0

    for start in range(0, n, chunk):
        end  = min(start + chunk, n)
        sims = cosine_similarity(vecs[start:end], vecs)   # (chunk, N)
        # Zero out self-similarity
        for i in range(end - start):
            sims[i, start + i] = -1.0
        # Top-k neighbours
        top_k = np.partition(sims, -k, axis=1)[:, -k:]
        top_1 = top_k.max(axis=1)

        nn_dists.extend((1.0 - top_k.mean(axis=1)).tolist())
        near_dup_count += int((top_1 >= NEAR_DUP_THRESH).sum())

    return float(np.mean(nn_dists)), near_dup_count / n


def effective_rank(vecs: np.ndarray, rng: np.random.Generator, n: int = EFF_RANK_N) -> float:
    """
    Effective rank: exp(entropy of squared singular values).
    Approximated via randomized SVD on a subsample.
    """
    from sklearn.decomposition import TruncatedSVD

    if len(vecs) > n:
        idx = rng.choice(len(vecs), n, replace=False)
        vecs = vecs[idx]

    d = vecs.shape[1]
    n_components = min(d, 200, len(vecs) - 1)
    svd = TruncatedSVD(n_components=n_components, random_state=42)
    svd.fit(vecs)

    sigma2 = svd.singular_values_ ** 2
    sigma2 = sigma2[sigma2 > 1e-10]
    sigma2 /= sigma2.sum()
    entropy = -float(np.sum(sigma2 * np.log(sigma2)))
    return float(math.exp(entropy))


def cross_coverage(
    vecs_a: np.ndarray,
    vecs_b: np.ndarray,
    rng: np.random.Generator,
    n: int = XCOVER_N,
) -> tuple[float, float]:
    """
    Cross-corpus coverage:
      a_to_b: mean cosine distance from each doc in A to its nearest neighbour in B
      b_to_a: mean cosine distance from each doc in B to its nearest neighbour in A

    Higher a_to_b means A has territory not covered by B (unique content in A).
    Higher b_to_a means B has territory not covered by A.
    vecs must be L2-normalized.
    """
    from sklearn.metrics.pairwise import cosine_similarity

    if len(vecs_a) > n:
        vecs_a = vecs_a[rng.choice(len(vecs_a), n, replace=False)]
    if len(vecs_b) > n:
        vecs_b = vecs_b[rng.choice(len(vecs_b), n, replace=False)]

    def _mean_nn_dist(query, ref):
        chunk = 500
        dists = []
        for start in range(0, len(query), chunk):
            sims = cosine_similarity(query[start:start+chunk], ref)
            dists.extend((1.0 - sims.max(axis=1)).tolist())
        return float(np.mean(dists))

    return _mean_nn_dist(vecs_a, vecs_b), _mean_nn_dist(vecs_b, vecs_a)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_embeddings(embed_dir: Path) -> dict[str, dict[str, np.ndarray]]:
    """Returns {label: {model_key: vecs}} for all discovered .npy files."""
    data: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for label_dir in sorted(embed_dir.iterdir()):
        if not label_dir.is_dir():
            continue
        label = label_dir.name
        for npy_file in sorted(label_dir.glob("*.npy")):
            model_key = npy_file.stem
            if model_key.startswith("_"):
                continue
            vecs = np.load(npy_file)
            data[label][model_key] = vecs
            print(f"  Loaded {label}/{model_key}: {vecs.shape}", flush=True)
    return dict(data)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

TIER_ORDER = [
    "tier1_nl", "tier1_all",
    "tier2_nl", "tier2_all",
    "tier3_nl", "tier3_all",
    "gptnl_8b", "gptnl_36b",
]

TIER_LABELS = {
    "tier1_nl":  "NederCorpus CC0+PD / NL only",
    "tier1_all": "NederCorpus CC0+PD / All 12",
    "tier2_nl":  "NederCorpus +CC-BY / NL only",
    "tier2_all": "NederCorpus +CC-BY / All 12",
    "tier3_nl":  "NederCorpus +CC-BY-SA / NL only",
    "tier3_all": "NederCorpus +CC-BY-SA / All 12",
    "gptnl_8b":  "GPT-NL 8B sample",
    "gptnl_36b": "GPT-NL 36B full",
}


def format_table(summary: dict[str, dict[str, float]], metrics: list[str]) -> str:
    labels = [l for l in TIER_ORDER if l in summary] + \
             [l for l in summary if l not in TIER_ORDER]

    header = "| Corpus | " + " | ".join(metrics) + " |"
    sep    = "|" + "|".join(["---"] * (len(metrics) + 1)) + "|"
    rows   = [header, sep]

    for label in labels:
        m = summary.get(label, {})
        display = TIER_LABELS.get(label, label)
        vals = []
        for metric in metrics:
            v = m.get(metric)
            if v is None:
                vals.append("—")
            elif metric == "near_dup_ratio":
                vals.append(f"{v:.3f}")
            else:
                vals.append(f"{v:.3f}")
        rows.append(f"| {display} | " + " | ".join(vals) + " |")

    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--embed_dir", type=Path, default=EMBED_DIR)
    p.add_argument("--out_dir",   type=Path, default=OUT_DIR)
    p.add_argument("--reference", type=str,  default=None,
                   help="Label to use as reference for cross-coverage (e.g. gptnl_8b)")
    p.add_argument("--k",         type=int,  default=K_NN)
    p.add_argument("--vendi_n",   type=int,  default=VENDI_N)
    p.add_argument("--seed",      type=int,  default=42)
    return p.parse_args()


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    print("Loading embeddings ...")
    data = load_embeddings(args.embed_dir)
    if not data:
        import sys; sys.exit(f"ERROR: no embeddings found in {args.embed_dir}")

    labels     = list(data.keys())
    all_models = sorted({m for d in data.values() for m in d})
    print(f"\nCorpora: {labels}")
    print(f"Models:  {all_models}\n")

    # Intrinsic metrics per (corpus, model)
    per_model: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)

    for label, models in data.items():
        print(f"\n--- {label} ---")
        for model_key, vecs in models.items():
            print(f"  {model_key}: {vecs.shape}", flush=True)

            v = vendi_score(vecs, args.vendi_n, rng)
            mean_nn, nd_ratio = mean_nn_dist_and_near_dup(vecs, args.k, rng)
            eff_r = effective_rank(vecs, rng)

            per_model[label][model_key] = {
                "vendi_score":    v,
                "mean_nn_dist":   mean_nn,
                "near_dup_ratio": nd_ratio,
                "eff_rank":       eff_r,
            }
            print(f"    vendi={v:.3f}  nn_dist={mean_nn:.4f}  "
                  f"near_dup={nd_ratio:.3f}  eff_rank={eff_r:.1f}", flush=True)

    # Cross-corpus coverage vs reference
    ref_label = args.reference
    if ref_label is None:
        # Auto-detect: prefer gptnl_8b, then gptnl_36b
        for candidate in ["gptnl_8b", "gptnl_36b"]:
            if candidate in data:
                ref_label = candidate
                break

    if ref_label and ref_label in data:
        print(f"\nCross-coverage vs {ref_label} ...")
        for label in labels:
            if label == ref_label:
                continue
            shared_models = set(data[label]) & set(data[ref_label])
            if not shared_models:
                continue
            a2b_list, b2a_list = [], []
            for model_key in shared_models:
                a2b, b2a = cross_coverage(
                    data[label][model_key],
                    data[ref_label][model_key],
                    rng,
                )
                a2b_list.append(a2b)
                b2a_list.append(b2a)
                per_model[label][model_key][f"a2ref_dist"] = a2b
                per_model[label][model_key][f"ref2a_dist"] = b2a

            print(f"  {label} -> {ref_label}: {np.mean(a2b_list):.4f}  "
                  f"(ref -> corpus: {np.mean(b2a_list):.4f})")

    # Summarise: average across models
    summary: dict[str, dict[str, float]] = {}
    for label, model_metrics in per_model.items():
        all_metric_keys = {k for m in model_metrics.values() for k in m}
        summary[label] = {}
        for metric in all_metric_keys:
            vals = [m[metric] for m in model_metrics.values() if metric in m]
            if vals:
                summary[label][metric] = float(np.mean(vals))

    # Save raw and summary
    raw_path     = args.out_dir / "metrics_per_model.json"
    summary_path = args.out_dir / "metrics_summary.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(per_model, f, indent=2)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Markdown report
    core_metrics = ["vendi_score", "mean_nn_dist", "near_dup_ratio", "eff_rank"]
    if ref_label:
        core_metrics += ["a2ref_dist", "ref2a_dist"]

    report = f"""# DDA Analysis: NederCorpus vs GPT-NL

Metrics averaged across {len(all_models)} embedding models:
`{", ".join(all_models)}`

**N** = {VENDI_N:,} subsample for Vendi Score, {XCOVER_N:,} for cross-coverage,
k = {args.k} for NN distances. All embeddings L2-normalised.

## Metric Definitions

| Metric | Interpretation |
|---|---|
| `vendi_score` | Diversity (higher = more varied semantic space) |
| `mean_nn_dist` | Mean cosine distance to k-NN (higher = less redundant) |
| `near_dup_ratio` | Fraction with NN similarity > {NEAR_DUP_THRESH} (lower = cleaner) |
| `eff_rank` | Effective dimensionality of embedding space (higher = richer) |
| `a2ref_dist` | Mean NN distance from corpus → {ref_label or 'reference'} (higher = more unique) |
| `ref2a_dist` | Mean NN distance from {ref_label or 'reference'} → corpus |

## Results (averaged across {len(all_models)} models)

{format_table(summary, core_metrics)}

## Per-Model Breakdown

"""
    for label in [l for l in TIER_ORDER if l in per_model] + \
                 [l for l in per_model if l not in TIER_ORDER]:
        report += f"\n### {TIER_LABELS.get(label, label)}\n\n"
        model_rows = []
        model_list = sorted(per_model[label].keys())
        report += "| Model | " + " | ".join(core_metrics) + " |\n"
        report += "|---|" + "|".join(["---"] * len(core_metrics)) + "|\n"
        for mk in model_list:
            m = per_model[label][mk]
            vals = [f"{m.get(met, float('nan')):.3f}" for met in core_metrics]
            report += f"| {mk} | " + " | ".join(vals) + " |\n"

    report_path = args.out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"\n{'='*60}")
    print(report)
    print(f"\nSaved to {args.out_dir}/")
    print(f"  {raw_path.name}     — raw per-model metrics")
    print(f"  {summary_path.name} — averaged summary")
    print(f"  {report_path.name}  — markdown report")


if __name__ == "__main__":
    main()
