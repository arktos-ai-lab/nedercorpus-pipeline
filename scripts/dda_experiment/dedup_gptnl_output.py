"""
dedup_gptnl_output.py — one-time cleanup: remove duplicate lines from
gptnl_all_nl_fy.jsonl caused by a watchdog bug that briefly ran multiple
extractor instances concurrently (each writing to the same output file).

Fast pass only: hash raw line bytes, write unique lines through. No JSON
parsing, no tokenization here — token totals are recomputed in a separate
final pass (count_gptnl_tokens.py) once the whole file is complete, the same
way NederCorpus's own recount was done (that approach hit a healthy pace;
doing per-line json.loads + tiktoken inline here was the reason the first
version of this script projected 16+ hours).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

TIER_DIR = Path(__file__).resolve().parent / "tier_splits"
SRC = TIER_DIR / "gptnl_all_nl_fy.jsonl"
DST = TIER_DIR / "gptnl_all_nl_fy.dedup.jsonl"


def main():
    seen: set[bytes] = set()
    total_in = 0
    total_out = 0

    with open(SRC, "r", encoding="utf-8", newline="") as fin, \
         open(DST, "w", encoding="utf-8", newline="") as fout:
        for line in fin:
            total_in += 1
            h = hashlib.blake2b(line.encode("utf-8"), digest_size=16).digest()
            if h in seen:
                continue
            seen.add(h)
            fout.write(line)
            total_out += 1

            if total_in % 1_000_000 == 0:
                print(f"  ... read {total_in:,}, kept {total_out:,} "
                      f"(dupes removed: {total_in - total_out:,})", flush=True)

    print(f"\nTotal lines read:    {total_in:,}")
    print(f"Total lines kept:    {total_out:,}")
    print(f"Duplicates removed:  {total_in - total_out:,}")

    SRC.unlink()
    DST.rename(SRC)
    print(f"Replaced {SRC.name} with deduped version.")


if __name__ == "__main__":
    main()
