"""
sample_local_gptnl.py — Reservoir-sample a token budget from the already-extracted,
already-deduped local GPT-NL dump (tier_splits/gptnl_all_nl_fy.jsonl, ~165GB) without
re-streaming from HuggingFace.

Produces gptnl_14b.jsonl: a ~14B whitespace-token slice, size-matched to the rebuilt
full-corpus NederCorpus tier3_all split, for a fair size-matched embedding comparison.
The full gptnl_all_nl_fy.jsonl is embedded separately as the "full GPT-NL" corpus.

Same A-Res weighted reservoir algorithm as sample_gptnl.py, single local file, no
dataset_name stratification (source is already homogeneous: NL+FY only).

Usage:
  python sample_local_gptnl.py --target_tokens 14000000000
"""
from __future__ import annotations

import argparse
import heapq
import json
import random
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TIER_DIR = Path(__file__).resolve().parent / "tier_splits"
SRC = TIER_DIR / "gptnl_all_nl_fy.jsonl"
SEED = 42


def approx_tokens(text: str) -> int:
    return len(text.split())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", type=Path, default=SRC)
    p.add_argument("--out", type=Path, default=TIER_DIR / "gptnl_14b.jsonl")
    p.add_argument("--target_tokens", type=int, default=14_000_000_000)
    p.add_argument("--seed", type=int, default=SEED)
    args = p.parse_args()

    rng = random.Random(args.seed)
    reservoir: list[tuple[float, int, str, int]] = []
    token_count = 0
    total_seen = 0

    print(f"Sampling {args.target_tokens/1e9:.1f}B tokens from {args.src.name} ...", flush=True)

    with open(args.src, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total_seen += 1
            try:
                text = json.loads(line).get("text", "")
            except json.JSONDecodeError:
                continue
            if not text:
                continue
            n = approx_tokens(text)
            if n < 10:
                continue

            priority = rng.random() ** (1.0 / max(n, 1))

            if token_count < args.target_tokens:
                heapq.heappush(reservoir, (priority, total_seen, line, n))
                token_count += n
            else:
                if reservoir and priority > reservoir[0][0]:
                    _, _, _, old_n = heapq.heapreplace(reservoir, (priority, total_seen, line, n))
                    token_count = token_count - old_n + n

            if total_seen % 1_000_000 == 0:
                print(f"  ... seen {total_seen:,} lines | reservoir {token_count/1e9:.2f}B tokens "
                      f"({len(reservoir):,} docs)", flush=True)

    print(f"\nTotal lines seen: {total_seen:,}")
    print(f"Final sample: {len(reservoir):,} docs, {token_count/1e9:.2f}B tokens", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fout:
        for _, _, line, _ in reservoir:
            fout.write(line + "\n")

    size_gb = args.out.stat().st_size / 1e9
    print(f"Wrote -> {args.out} ({size_gb:.2f} GB)", flush=True)


if __name__ == "__main__":
    main()
