"""
prepare_splits.py — Create train/holdout splits for the multi-variety experiment.

Two-pass streaming design — never loads more than one line of text at a time:
  Pass 1: index all docs (file, line number, variety, token count) ~600MB RAM
  Pass 2: stream files again, writing only the selected lines to output

Outputs (in out_dir/):
  holdout.jsonl        — 5% stratified holdout by variety
  train_nl_only.jsonl  — NL + NL-BE only, capped at TARGET_TOKENS
  train_full.jsonl     — all 12 varieties, same TOKEN_CAP

Usage:
  python prepare_splits.py [--normalized_dir PATH] [--out_dir PATH] [--target_tokens INT]
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TARGET_TOKENS = 1_000_000_000  # 1B whitespace tokens
HOLDOUT_FRAC  = 0.05
SEED          = 42

NL_DOMINANT = {"NL", "NL-BE"}
MINORITY    = {"FY", "AF", "LI", "VLS", "ZEA", "NDS-NL", "FRR", "STQ", "PAP", "SRN"}


@dataclass
class DocRef:
    file_idx: int   # index into sorted file list
    line_no:  int   # 0-based line number within file
    tokens:   int   # whitespace token count


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--normalized_dir", default="data/normalized", type=Path)
    p.add_argument("--out_dir",        default="experiment_data",  type=Path)
    p.add_argument("--target_tokens",  default=TARGET_TOKENS,      type=int)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Pass 1 — build lightweight index
# ---------------------------------------------------------------------------

def build_index(normalized_dir: Path):
    """
    Return (files, by_variety) where:
      files      — sorted list of Path objects
      by_variety — dict[variety_str, list[DocRef]]
    """
    files = sorted(normalized_dir.glob("*.jsonl"))
    by_variety: dict[str, list[DocRef]] = defaultdict(list)

    total = 0
    for file_idx, path in enumerate(files):
        if file_idx % 20 == 0:
            print(f"  Indexing {file_idx}/{len(files)}: {path.name} …")
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text    = rec.get("text", "")
                variant = (rec.get("meta") or {}).get("language_variant") or "NL"
                if not text:
                    continue
                n_tok = len(text.split())
                by_variety[variant].append(DocRef(file_idx, line_no, n_tok))
                total += 1

    print(f"\nIndexed {total:,} documents across {len(files)} files.")
    return files, by_variety


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------

def select_docs(by_variety, target_tokens, include_variants, rng):
    """
    Return a set of (file_idx, line_no) tuples for the train split,
    and a separate set for the holdout.
    """
    holdout_set: set[tuple[int, int]] = set()
    train_pool: list[DocRef] = []

    for variant, refs in by_variety.items():
        shuffled = list(refs)
        rng.shuffle(shuffled)

        n_hold = max(1, int(len(shuffled) * HOLDOUT_FRAC))
        for ref in shuffled[:n_hold]:
            holdout_set.add((ref.file_idx, ref.line_no))

        if variant in include_variants:
            train_pool.extend(shuffled[n_hold:])

    # Shuffle the combined pool, then fill to token cap
    rng.shuffle(train_pool)
    train_set: set[tuple[int, int]] = set()
    total_tokens = 0
    for ref in train_pool:
        if total_tokens + ref.tokens > target_tokens:
            break
        train_set.add((ref.file_idx, ref.line_no))
        total_tokens += ref.tokens

    return train_set, holdout_set, total_tokens


# ---------------------------------------------------------------------------
# Pass 2 — stream files, write selected lines
# ---------------------------------------------------------------------------

def write_splits(files, by_variety, out_dir, target_tokens, rng_seed):
    rng = random.Random(rng_seed)

    # --- build selections ---
    print("\nSelecting holdout docs (5% stratified) …")
    # First pass: compute holdout for ALL varieties (shared across splits)
    holdout_set: set[tuple[int, int]] = set()
    train_refs_by_variant: dict[str, list[DocRef]] = {}

    for variant, refs in by_variety.items():
        shuffled = list(refs)
        rng.shuffle(shuffled)
        n_hold = max(1, int(len(shuffled) * HOLDOUT_FRAC))
        for ref in shuffled[:n_hold]:
            holdout_set.add((ref.file_idx, ref.line_no))
        train_refs_by_variant[variant] = shuffled[n_hold:]

    # NL-only train set
    print("Selecting train_nl_only docs …")
    nl_pool = []
    for v in NL_DOMINANT:
        nl_pool.extend(train_refs_by_variant.get(v, []))
    rng.shuffle(nl_pool)
    nl_only_set: set[tuple[int, int]] = set()
    nl_tokens = 0
    for ref in nl_pool:
        if nl_tokens + ref.tokens > target_tokens:
            break
        nl_only_set.add((ref.file_idx, ref.line_no))
        nl_tokens += ref.tokens

    # Full train set (all varieties, same token cap)
    print("Selecting train_full docs …")
    full_pool = []
    for v in NL_DOMINANT | MINORITY:
        full_pool.extend(train_refs_by_variant.get(v, []))
    rng.shuffle(full_pool)
    full_set: set[tuple[int, int]] = set()
    full_tokens = 0
    for ref in full_pool:
        if full_tokens + ref.tokens > target_tokens:
            break
        full_set.add((ref.file_idx, ref.line_no))
        full_tokens += ref.tokens

    print(f"\n  Holdout:      {len(holdout_set):>8,} docs")
    print(f"  train_nl_only:{len(nl_only_set):>8,} docs  {nl_tokens/1e9:.3f}B tokens")
    print(f"  train_full:   {len(full_set):>8,} docs  {full_tokens/1e9:.3f}B tokens")

    # Variety breakdown for full split
    full_by_variant: dict[str, int] = defaultdict(int)
    for variant, refs in by_variety.items():
        for ref in refs:
            if (ref.file_idx, ref.line_no) in full_set:
                full_by_variant[variant] += ref.tokens
    print("\n  Variety breakdown of train_full (tokens):")
    for v, t in sorted(full_by_variant.items(), key=lambda x: -x[1]):
        pct = 100 * t / full_tokens if full_tokens else 0
        print(f"    {v:8s}  {t/1e6:7.1f}M tok  ({pct:.1f}%)")

    # --- pass 2: stream files and write ---
    out_dir.mkdir(parents=True, exist_ok=True)
    f_holdout  = open(out_dir / "holdout.jsonl",        "w", encoding="utf-8")
    f_nl_only  = open(out_dir / "train_nl_only.jsonl",  "w", encoding="utf-8")
    f_full     = open(out_dir / "train_full.jsonl",     "w", encoding="utf-8")

    holdout_variant_map: dict[tuple[int,int], str] = {}
    for variant, refs in by_variety.items():
        for ref in refs:
            key = (ref.file_idx, ref.line_no)
            if key in holdout_set:
                holdout_variant_map[key] = variant

    print("\nStreaming files to write output …")
    try:
        for file_idx, path in enumerate(files):
            if file_idx % 20 == 0:
                print(f"  Writing {file_idx}/{len(files)}: {path.name} …")
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh):
                    line_s = line.strip()
                    if not line_s:
                        continue
                    key = (file_idx, line_no)
                    if key in holdout_set:
                        try:
                            rec = json.loads(line_s)
                            variant = holdout_variant_map.get(key, "NL")
                            out = {"text": rec.get("text", ""), "variant": variant}
                            f_holdout.write(json.dumps(out, ensure_ascii=False) + "\n")
                        except json.JSONDecodeError:
                            pass
                    if key in nl_only_set:
                        try:
                            rec = json.loads(line_s)
                            f_nl_only.write(json.dumps({"text": rec.get("text", "")}, ensure_ascii=False) + "\n")
                        except json.JSONDecodeError:
                            pass
                    if key in full_set:
                        try:
                            rec = json.loads(line_s)
                            f_full.write(json.dumps({"text": rec.get("text", "")}, ensure_ascii=False) + "\n")
                        except json.JSONDecodeError:
                            pass
    finally:
        f_holdout.close()
        f_nl_only.close()
        f_full.close()

    print(f"\nDone. Output in {out_dir}/")
    print("  holdout.jsonl, train_nl_only.jsonl, train_full.jsonl")


def main():
    args = parse_args()
    print(f"Pass 1: building index from {args.normalized_dir} …")
    files, by_variety = build_index(args.normalized_dir)

    print("\nVariety totals:")
    for v, refs in sorted(by_variety.items(), key=lambda x: -sum(r.tokens for r in x[1])):
        total_tok = sum(r.tokens for r in refs)
        print(f"  {v:8s}  {len(refs):>8,} docs  {total_tok/1e6:>8.1f}M tokens")

    write_splits(files, by_variety, args.out_dir, args.target_tokens, SEED)


if __name__ == "__main__":
    main()
