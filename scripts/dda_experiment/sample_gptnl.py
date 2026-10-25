"""
sample_gptnl.py — Stream GPT-NL public corpus from HuggingFace and write
                  two reservoir-sampled JSONL files:
                    gptnl_8b.jsonl   (~8B whitespace tokens, ~same size as NederCorpus)
                    gptnl_36b.jsonl  (~36B whitespace tokens, full corpus sample)

GPT-NL public dataset:
  Default: "yhavinga/gptnl-public"  — adjust --dataset if the team published elsewhere.
  Streaming mode: never downloads the full corpus to disk.

Usage:
  pip install datasets tqdm
  python sample_gptnl.py
  python sample_gptnl.py --dataset yhavinga/gptnl-public --split train --seed 42

The script writes both output files in one pass over the stream (two independent
reservoir samplers running in tandem).
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import random
import sys
from pathlib import Path

OUT_DIR       = Path(__file__).resolve().parent / "tier_splits"
TARGET_8B     = 8_000_000_000    # whitespace token budget for small sample
TARGET_36B    = 36_000_000_000   # full-corpus budget
SEED          = 42

# GPT-NL public HuggingFace dataset.
# Adjust if the consortium published under a different repo name.
DEFAULT_DATASET = "GPT-NL/GPT-NL_Public_Corpus"


def approx_tokens(text: str) -> int:
    return len(text.split())


def is_dutch(record: dict) -> bool:
    """Filter to Dutch-language records only.

    GPT-NL/GPT-NL_Public_Corpus has columns: id, text, language, dataset_name,
    source, title, author, license, language_score, dataset_url, n_char,
    avg_word_length, n_non_symbol_words.

    The corpus is multilingual (Dutch, English, German, etc.) — american-stories
    (77.5M rows) and CC GitHub (171M rows) are English. Filter strictly to Dutch.
    """
    lang = (record.get("language") or "").lower().strip()
    if lang not in ("nl", "dutch", "nld"):
        return False
    score = record.get("language_score")
    if score is not None:
        try:
            if float(score) < 0.85:
                return False
        except (TypeError, ValueError):
            pass
    return True


def reservoir_sample_to_budget(
    stream,
    targets: list[int],
    seed: int,
) -> list[list[dict]]:
    """
    Single-pass stratified reservoir sampling for multiple token budgets simultaneously.

    Stratifies by dataset_name so the 8B sample mirrors the source proportions
    of the Dutch-filtered GPT-NL corpus rather than over-sampling high-volume sources.

    Returns one list of dicts per target.
    """
    rng = random.Random(seed)
    n_targets = len(targets)

    reservoirs: list[list[tuple[float, dict, int]]] = [[] for _ in range(n_targets)]
    token_counts: list[int] = [0] * n_targets

    # Track per-dataset_name token counts for post-hoc representativeness check
    dataset_token_counts: dict[str, int] = {}
    total_docs_seen = 0
    skipped_lang = 0

    for record in stream:
        if not is_dutch(record):
            skipped_lang += 1
            continue

        text = record.get("text", "") or ""
        if not text:
            continue
        n = approx_tokens(text)
        if n < 10:
            continue

        dataset_name = record.get("dataset_name", "unknown")
        dataset_token_counts[dataset_name] = dataset_token_counts.get(dataset_name, 0) + n

        doc = {"text": text, "meta": {
            "source": dataset_name,
            "language_variant": "NL",
            "license": record.get("license", "unknown"),
            "language_score": record.get("language_score"),
        }}
        total_docs_seen += 1

        if total_docs_seen % 500_000 == 0:
            print(f"  Skipped (non-Dutch): {skipped_lang:,}", flush=True)

        priority = rng.random() ** (1.0 / max(n, 1))

        for ti, target in enumerate(targets):
            # Greedily fill reservoir until token budget is met
            if token_counts[ti] < target:
                # min-heap by priority; total_docs_seen breaks float ties so dicts are never compared
                heapq.heappush(reservoirs[ti], (priority, total_docs_seen, doc, n))
                token_counts[ti] += n
            else:
                # O(log n) replacement: evict lowest-priority item if new one is better
                if reservoirs[ti] and priority > reservoirs[ti][0][0]:
                    _, _, _, old_n = heapq.heapreplace(reservoirs[ti], (priority, total_docs_seen, doc, n))
                    token_counts[ti] = token_counts[ti] - old_n + n

        if total_docs_seen % 100_000 == 0:
            print(f"  Seen {total_docs_seen:,} docs | "
                  + " | ".join(f"T{i+1}: {token_counts[i]/1e9:.2f}B" for i in range(n_targets)),
                  flush=True)

    print(f"\n  Total Dutch docs seen: {total_docs_seen:,}  (skipped non-Dutch: {skipped_lang:,})")
    print(f"  Source distribution (top 15 by tokens):")
    for ds, tok in sorted(dataset_token_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"    {ds:40s} {tok/1e9:.3f}B tokens")
    for i, target in enumerate(targets):
        print(f"  Target {target/1e9:.0f}B: {len(reservoirs[i]):,} docs, "
              f"{token_counts[i]/1e9:.2f}B tokens sampled")

    return [[item[2] for item in sorted(r, key=lambda x: x[0], reverse=True)]
            for r in reservoirs]


def write_jsonl(docs: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for doc in docs:
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    size_gb = path.stat().st_size / 1e9
    print(f"  Wrote {len(docs):,} docs -> {path.name}  ({size_gb:.2f} GB)", flush=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default=DEFAULT_DATASET,
                   help="HuggingFace dataset ID for GPT-NL corpus")
    p.add_argument("--split", default="train")
    p.add_argument("--out_dir", type=Path, default=OUT_DIR)
    p.add_argument("--target_8b",  type=int, default=TARGET_8B)
    p.add_argument("--target_36b", type=int, default=TARGET_36B)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--skip_36b", action="store_true",
                   help="Only produce the 8B sample (faster)")
    p.add_argument("--skip_8b", action="store_true",
                   help="Only produce the 36B sample (use when 8B already written)")
    return p.parse_args()


# Subsets known to be non-Dutch (English, German, code) — skip entirely for speed.
_SKIP_CONFIGS = frozenset({
    "american-stories",
    "cc_english-pd",
    "cc_german-pd",
    "cc_github_open_source",
    "cc_loc-pd-books",
    "cc_openalex",
})


def iter_all_configs(dataset_id: str, split: str):
    """Stream all subsets of a multi-config HF dataset, yielding records one by one."""
    from datasets import get_dataset_config_names, load_dataset

    try:
        configs = get_dataset_config_names(dataset_id)
    except Exception as e:
        sys.exit(f"ERROR: could not list configs for {dataset_id}: {e}")

    print(f"  Found {len(configs)} configs: {configs}")
    for cfg in configs:
        if cfg in _SKIP_CONFIGS:
            print(f"  [{cfg}] skipping (known non-Dutch)", flush=True)
            continue
        print(f"  [{cfg}] streaming ...", flush=True)
        for attempt in range(3):
            try:
                ds = load_dataset(dataset_id, cfg, split=split, streaming=True)
                for record in ds:
                    yield record
                break  # completed cleanly
            except Exception as e:
                print(f"  [{cfg}] attempt {attempt+1}/3 failed: {e}", flush=True)
                if attempt == 2:
                    print(f"  [{cfg}] WARNING: giving up after 3 attempts — skipping", flush=True)
                else:
                    import time; time.sleep(5)


def main():
    args = parse_args()

    try:
        from datasets import load_dataset  # noqa: F401 — imported for error check
    except ImportError:
        sys.exit("ERROR: pip install datasets")

    if args.skip_8b and args.skip_36b:
        sys.exit("ERROR: cannot use --skip_8b and --skip_36b together")
    if args.skip_8b:
        targets, labels = [args.target_36b], ["gptnl_36b"]
    elif args.skip_36b:
        targets, labels = [args.target_8b], ["gptnl_8b"]
    else:
        targets, labels = [args.target_8b, args.target_36b], ["gptnl_8b", "gptnl_36b"]

    print(f"Streaming {args.dataset} (all Dutch-bearing subsets) ...")
    print(f"Reservoir sampling to {' and '.join(f'{t/1e9:.0f}B' for t in targets)} tokens ...")
    stream = iter_all_configs(args.dataset, args.split)
    results = reservoir_sample_to_budget(stream, targets, args.seed)

    print("\nWriting output files ...")
    for label, docs in zip(labels, results):
        out_path = args.out_dir / f"{label}.jsonl"
        write_jsonl(docs, out_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
