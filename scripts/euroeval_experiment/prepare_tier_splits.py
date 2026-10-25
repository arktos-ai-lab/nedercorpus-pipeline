"""
prepare_tier_splits.py — Build 6 continued-pretraining splits for the EuroEval experiment.

Conditions (3 license tiers × 2 language configs):
  Tier 1 — base:        CC0 + PD
  Tier 2 — permissive:  CC0 + PD + CC-BY
  Tier 3 — full:        CC0 + PD + CC-BY + CC-BY-SA

  Lang A — nl_only:     NL, NL-BE
  Lang B — all:         all 12 NederCorpus varieties

Output: 6 files × ~1B tokens in scripts/euroeval_experiment/tier_splits/
  tier1_nl.jsonl   tier1_all.jsonl
  tier2_nl.jsonl   tier2_all.jsonl
  tier3_nl.jsonl   tier3_all.jsonl

Two-pass streaming — never loads corpus into RAM.
  Pass 1: index all docs (file, line, token_count, license, lang_variant) → ~500MB RAM
  Pass 2: reservoir-sample to TARGET_TOKENS per split, write selected lines

Usage:
  python scripts/euroeval_experiment/prepare_tier_splits.py
  python scripts/euroeval_experiment/prepare_tier_splits.py --target_tokens 500_000_000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# Windows cp1252 consoles/log redirects raise UnicodeEncodeError on "…" etc. and
# silently kill the process mid-run (this is what truncated job_full.log /
# job_1456m.log with no traceback). Force UTF-8 unconditionally.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT   = Path(__file__).resolve().parents[2]
NORM_DIR    = REPO_ROOT / "data" / "normalized"
OUT_DIR     = Path(__file__).resolve().parent / "tier_splits"

TARGET_TOKENS = 1_000_000_000   # 1B tokens per split
SEED          = 42

# ---------------------------------------------------------------------------
# License tiers (inclusive — each tier adds to the previous)
# ---------------------------------------------------------------------------
LICENSE_TIERS: dict[str, frozenset[str]] = {
    "tier1": frozenset({"CC0", "PD"}),
    "tier2": frozenset({"CC0", "PD", "CC-BY"}),
    "tier3": frozenset({"CC0", "PD", "CC-BY", "CC-BY-SA"}),
}

# Language configs
LANG_NL_ONLY: frozenset[str] = frozenset({"NL", "NL-BE"})
LANG_ALL:     frozenset[str] = frozenset({
    "NL", "NL-BE", "AF", "FY", "LI", "VLS", "ZEA",
    "NDS-NL", "PAP", "SRN", "FRR", "STQ",
})

SPLIT_KEYS = [
    ("tier1", "nl"),  ("tier1", "all"),
    ("tier2", "nl"),  ("tier2", "all"),
    ("tier3", "nl"),  ("tier3", "all"),
]


@dataclass
class DocRef:
    file_idx: int
    line_no:  int
    tokens:   int
    license:  str
    lang:     str


# ---------------------------------------------------------------------------
# Token count helper (whitespace approximation — consistent with main pipeline)
# ---------------------------------------------------------------------------

def approx_tokens(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# Pass 1: index
# ---------------------------------------------------------------------------

def build_index(norm_dir: Path) -> tuple[list[Path], list[DocRef]]:
    files = sorted(norm_dir.glob("*.jsonl"))
    if not files:
        sys.exit(f"ERROR: no .jsonl files in {norm_dir}")

    refs: list[DocRef] = []
    total = 0
    for fi, path in enumerate(files):
        print(f"  Indexing [{fi+1}/{len(files)}] {path.name} …", flush=True)
        with open(path, encoding="utf-8", errors="replace") as fh:
            for li, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = obj.get("text", "")
                meta = obj.get("meta", {})
                lic  = (meta.get("license") or "").strip()
                lang = (meta.get("language_variant") or "").strip().upper()
                n    = approx_tokens(text)
                if n < 10:
                    continue
                refs.append(DocRef(fi, li, n, lic, lang))
                total += n
    print(f"  Indexed {len(refs):,} docs, {total/1e9:.2f}B tokens total")
    return files, refs


# ---------------------------------------------------------------------------
# Pass 2: weighted reservoir sample per split, then stream-write
# ---------------------------------------------------------------------------

def select_docs(
    refs: list[DocRef],
    tier: str,
    lang_key: str,
    target: int,
    rng: random.Random,
) -> set[tuple[int, int]]:
    """Return set of (file_idx, line_no) for this split."""
    allowed_licenses = LICENSE_TIERS[tier]
    allowed_langs    = LANG_NL_ONLY if lang_key == "nl" else LANG_ALL

    # Filter eligible docs
    eligible = [
        r for r in refs
        if r.license in allowed_licenses and r.lang in allowed_langs
    ]
    total_eligible = sum(r.tokens for r in eligible)
    print(f"    Eligible: {len(eligible):,} docs, {total_eligible/1e9:.2f}B tokens", flush=True)

    if total_eligible <= target:
        print(f"    Using all eligible docs ({total_eligible/1e9:.2f}B < {target/1e9:.1f}B target)")
        return {(r.file_idx, r.line_no) for r in eligible}

    # Reservoir sample proportional to token count until we hit target
    # Shuffle eligible list, greedily pick until target reached
    indices = list(range(len(eligible)))
    rng.shuffle(indices)
    selected: set[tuple[int, int]] = set()
    accumulated = 0
    for i in indices:
        r = eligible[i]
        selected.add((r.file_idx, r.line_no))
        accumulated += r.tokens
        if accumulated >= target:
            break
    print(f"    Selected: {len(selected):,} docs, ~{accumulated/1e9:.2f}B tokens", flush=True)
    return selected


def write_split(
    files: list[Path],
    selected: set[tuple[int, int]],
    out_path: Path,
) -> None:
    """Stream through files writing only selected lines."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    # Group by file for single-pass read
    by_file: dict[int, set[int]] = defaultdict(set)
    for fi, li in selected:
        by_file[fi].add(li)

    with open(out_path, "w", encoding="utf-8") as fout:
        for fi in sorted(by_file):
            wanted = by_file[fi]
            with open(files[fi], encoding="utf-8", errors="replace") as fh:
                for li, line in enumerate(fh):
                    if li in wanted:
                        fout.write(line if line.endswith("\n") else line + "\n")
                        written += 1
    print(f"    Written {written:,} docs -> {out_path.name}", flush=True)


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def print_split_stats(out_path: Path) -> None:
    tokens = 0
    by_lang: dict[str, int] = defaultdict(int)
    by_lic:  dict[str, int] = defaultdict(int)
    with open(out_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            tokens += approx_tokens(obj.get("text", ""))
            meta = obj.get("meta", {})
            by_lang[meta.get("language_variant", "?")] += 1
            by_lic[meta.get("license", "?")] += 1
    print(f"      Tokens:   {tokens/1e6:.1f}M")
    print(f"      Licenses: {dict(sorted(by_lic.items(), key=lambda x: -x[1]))}")
    top_langs = sorted(by_lang.items(), key=lambda x: -x[1])[:8]
    print(f"      Langs:    {dict(top_langs)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--norm_dir",      type=Path, default=NORM_DIR)
    p.add_argument("--out_dir",       type=Path, default=OUT_DIR)
    p.add_argument("--target_tokens", type=int,  default=TARGET_TOKENS)
    p.add_argument("--stats",         action="store_true",
                   help="Print per-split stats after writing (slow — re-reads output)")
    return p.parse_args()


def main():
    args = parse_args()
    rng  = random.Random(SEED)

    print(f"NederCorpus EuroEval tier-split builder")
    print(f"  Source:  {args.norm_dir}")
    print(f"  Output:  {args.out_dir}")
    print(f"  Target:  {args.target_tokens/1e9:.1f}B tokens per split")
    print(f"  Splits:  {len(SPLIT_KEYS)} ({' | '.join(f'{t}_{l}' for t,l in SPLIT_KEYS)})\n")

    # Pass 1
    print("Pass 1: indexing …")
    files, refs = build_index(args.norm_dir)

    # Pass 2: one split at a time
    print("\nPass 2: selecting & writing splits …")
    for tier, lang_key in SPLIT_KEYS:
        out_path = args.out_dir / f"{tier}_{lang_key}.jsonl"
        if out_path.exists() and out_path.stat().st_size > 1_000_000:
            print(f"  {out_path.name} already exists — skipping")
            continue
        print(f"\n  {tier}_{lang_key}  (licenses={sorted(LICENSE_TIERS[tier])}, "
              f"langs={'NL,NL-BE' if lang_key == 'nl' else 'all 12'})")
        selected = select_docs(refs, tier, lang_key, args.target_tokens, rng)
        write_split(files, selected, out_path)
        if args.stats:
            print_split_stats(out_path)

    print("\nDone.")
    print(f"Splits written to: {args.out_dir}/")


if __name__ == "__main__":
    main()
