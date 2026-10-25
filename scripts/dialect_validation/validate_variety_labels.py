"""
validate_variety_labels.py — Validate NederCorpus's source-heuristic
language_variant labels for the minority varieties against GlotLID, an
independent open language-identification model (Kargaran et al. 2023,
cis-lmu/glotlid), trained on ~2000 languages including most of the Dutch
dialect continuum.

Motivation: reviewers will be skeptical of minor-variety labels (e.g.
Zeelandic vs West Flemish) that rely on source-level heuristics rather than
per-document verification (see MRL2026_NederCorpus.md §8.3, "Variety
detection accuracy"). This script samples up to N docs per minority variety
from data/deduped/, classifies each with GlotLID, and reports a full
precision/recall/confusion matrix treating NederCorpus's own labels and
GlotLID's predictions as two independent labelings.

Usage:
    pip install fasttext-wheel huggingface_hub   (needs a working C build —
    on this machine that only works under the Python 3.11 venv, not the
    default Python 3.14 install; see .venv_euroeval)

    python validate_variety_labels.py \
        --deduped_dir ../../data/deduped \
        --n_per_variety 500 \
        --out variety_validation_results.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# NederCorpus variety code -> GlotLID label (ISO 639-3 + script).
# NL/NL-BE excluded deliberately — the point is to stress-test the
# dialect-continuum varieties that rely on source heuristics, not the two
# well-separated majority varieties langdetect already handles confidently.
VARIETY_TO_GLOTLID = {
    "FY":     "fry_Latn",
    "AF":     "afr_Latn",
    "LI":     "lim_Latn",
    "VLS":    "vls_Latn",
    "ZEA":    "zea_Latn",
    "NDS-NL": "nds_Latn",
    "FRR":    "frr_Latn",
    "STQ":    "stq_Latn",
    "PAP":    "pap_Latn",
    "SRN":    "srn_Latn",
}
GLOTLID_TO_VARIETY = {v: k for k, v in VARIETY_TO_GLOTLID.items()}
# Labels GlotLID might reasonably emit for these that are NOT a wrong
# answer in the way a random label would be — e.g. Standard Dutch is a
# real, expected confusion for the closer dialects, not a bug. Reported
# separately in the confusion matrix, not folded into "correct".
NL_LABELS = {"nld_Latn"}

MODEL_REPO = "cis-lmu/glotlid"
MODEL_FILE = "model.bin"
TEXT_CHARS_FOR_ID = 3000  # GlotLID needs only a modest sample to classify


def sample_docs(deduped_dir: Path, n_per_variety: int, seed: int = 42):
    """Single pass over every data/deduped/*.jsonl file, reservoir-sampling
    up to n_per_variety documents per target variety."""
    rng = random.Random(seed)
    reservoirs: dict[str, list[dict]] = {v: [] for v in VARIETY_TO_GLOTLID}
    seen_counts: Counter = Counter()

    files = sorted(deduped_dir.glob("*.jsonl"))
    print(f"Scanning {len(files)} deduped files ...", flush=True)
    for fi, fp in enumerate(files, 1):
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    variety = (d.get("meta") or {}).get("language_variant")
                    if variety not in reservoirs:
                        continue
                    text = d.get("text", "")
                    if len(text) < 50:
                        continue
                    seen_counts[variety] += 1
                    res = reservoirs[variety]
                    if len(res) < n_per_variety:
                        res.append({"id": d.get("meta", {}).get("id"), "source": fp.stem, "text": text})
                    else:
                        j = rng.randint(0, seen_counts[variety] - 1)
                        if j < n_per_variety:
                            res[j] = {"id": d.get("meta", {}).get("id"), "source": fp.stem, "text": text}
        except OSError:
            continue
        if fi % 40 == 0:
            print(f"  [{fi}/{len(files)}] scanned ...", flush=True)

    print("Population sizes seen (may exceed n_per_variety):")
    for v in VARIETY_TO_GLOTLID:
        print(f"  {v:8s} seen={seen_counts[v]:>8,}  sampled={len(reservoirs[v])}")
    return reservoirs


def load_glotlid():
    import numpy as np
    import fasttext
    import fasttext.FastText
    from huggingface_hub import hf_hub_download

    # fasttext's predict() calls np.array(probs, copy=False), which numpy>=2.0
    # raises on ("Unable to avoid copy while creating an array as requested")
    # whenever an actual copy is needed — this package hasn't been updated
    # for the numpy 2.0 copy-keyword semantics change. Patch just this one
    # module-local `np.array` reference to fall back to np.asarray, rather
    # than downgrading numpy for the whole venv (euroeval's other deps may
    # want numpy 2.x).
    fasttext.FastText.np.array = lambda obj, *a, **kw: np.asarray(obj)

    print(f"Downloading {MODEL_REPO}/{MODEL_FILE} (first run only, ~1.5GB) ...", flush=True)
    model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
    return fasttext.load_model(model_path)


def classify(model, text: str) -> str:
    clean = text[:TEXT_CHARS_FOR_ID].replace("\n", " ").replace("\r", " ").strip()
    if not clean:
        return "und_Latn"
    labels, _ = model.predict(clean, k=1)
    return labels[0].replace("__label__", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deduped_dir", type=Path, default=Path("../../data/deduped"))
    ap.add_argument("--n_per_variety", type=int, default=500)
    ap.add_argument("--out", type=Path, default=Path("variety_validation_results.json"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    reservoirs = sample_docs(args.deduped_dir, args.n_per_variety, args.seed)

    print("\nLoading GlotLID ...", flush=True)
    model = load_glotlid()

    # Pool everything so precision can be computed across the full sample,
    # not just per-variety recall.
    pooled = []  # (true_variety, glotlid_label, source)
    print("\nClassifying ...", flush=True)
    for variety, docs in reservoirs.items():
        for d in docs:
            pred = classify(model, d["text"])
            pooled.append((variety, pred, d["source"]))
        print(f"  {variety:8s} classified {len(docs)} docs", flush=True)

    # Per-variety recall + confusion breakdown.
    results = {"n_per_variety": args.n_per_variety, "varieties": {}}
    for variety, expected_label in VARIETY_TO_GLOTLID.items():
        rows = [r for r in pooled if r[0] == variety]
        n = len(rows)
        if n == 0:
            continue
        correct = sum(1 for _, pred, _ in rows if pred == expected_label)
        nl_confused = sum(1 for _, pred, _ in rows if pred in NL_LABELS)
        confusion = Counter(pred for _, pred, _ in rows)
        results["varieties"][variety] = {
            "n_sampled": n,
            "expected_glotlid_label": expected_label,
            "recall": correct / n,
            "confused_with_standard_dutch": nl_confused / n,
            "top_confusions": confusion.most_common(5),
        }

    # Precision per variety (pooled across the whole sample: of everything
    # GlotLID called <label>, how much of it did we also call <variety>?).
    for variety, expected_label in VARIETY_TO_GLOTLID.items():
        predicted_as = [r for r in pooled if r[1] == expected_label]
        if predicted_as:
            precision = sum(1 for true_v, _, _ in predicted_as if true_v == variety) / len(predicted_as)
        else:
            precision = None
        if variety in results["varieties"]:
            results["varieties"][variety]["precision"] = precision
            results["varieties"][variety]["n_glotlid_predicted_this_label"] = len(predicted_as)

    args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {args.out}")

    print("\n=== Summary ===")
    print(f"{'Variety':8s} {'N':>5s} {'Recall':>8s} {'Precision':>10s} {'%->NL':>7s}  Top confusions")
    for v, r in results["varieties"].items():
        prec = f"{r['precision']:.3f}" if r["precision"] is not None else "n/a"
        print(f"{v:8s} {r['n_sampled']:>5d} {r['recall']:>8.3f} {prec:>10s} "
              f"{r['confused_with_standard_dutch']*100:>6.1f}%  {r['top_confusions']}")


if __name__ == "__main__":
    main()
