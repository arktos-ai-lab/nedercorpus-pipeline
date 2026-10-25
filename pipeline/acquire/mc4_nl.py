"""
Download mC4-nl-cleaned Dutch corpus via HuggingFace (ODC-By).

mC4 (Multilingual C4) is a cleaned version of the Common Crawl multilingual
web dataset. The cleaned Dutch subset `yhavinga/mc4_nl_cleaned` applies
additional quality filters (language detection, heuristic deduplication,
boilerplate removal) yielding ~23 billion words of Dutch web text.

License: ODC-By (Open Data Commons Attribution)
  Compatible with CC-BY for redistribution.

HuggingFace dataset: yhavinga/mc4_nl_cleaned

Requires: pip install datasets huggingface_hub

Usage:
  python -m pipeline acquire --sources mc4_nl
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE     = "mc4_nl"
LICENSE    = "ODC-By"
COUNTRY    = "NL"
DOMAIN     = "web"
HF_DATASET = "yhavinga/mc4_nl_cleaned"


def _log(output_dir: Path) -> None:
    entry = {
        "source":           SOURCE,
        "url":              f"https://huggingface.co/datasets/{HF_DATASET}",
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    split: str = "train",
    max_docs: int = 1_000_000,
    shard_size: int = 100_000,
    dry_run: bool = False,
    hf_token: str | None = None,
) -> None:
    """
    Stream mC4-nl-cleaned and write shards of JSONL files.

    Each record: {text, url, timestamp}.
    Shards saved as mc4_nl_shard_{N:06d}.jsonl.

    Default max_docs=1M is roughly 4% of the full dataset.

    NOTE: Excluded from NederCorpus default build (§17.L).
    mC4 derives from CommonCrawl. ODC-By covers dataset curation only, not the
    underlying web content. NederCorpus requires source content to be explicitly
    CC/PD licensed. Use c5_dutch instead.
    """
    logger.warning(
        "[mc4_nl] EXCLUDED by NederCorpus clean-corpus policy (§17.L). "
        "ODC-By covers curation only, not underlying CommonCrawl web content. "
        "Use c5_dutch instead. Skipping."
    )
    return

    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit("Install 'datasets': pip install datasets")

    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[mc4_nl] [dry-run] would download from %s", HF_DATASET)
        return

    # Skip already-written shards
    existing = len(list(output_dir.glob("mc4_nl_shard_*.jsonl")))
    skip_docs = existing * shard_size
    shard_idx = existing

    logger.info("[mc4_nl] Streaming (max %d docs, skipping %d)…", max_docs, skip_docs)
    ds = load_dataset(
        HF_DATASET,
        split=split,
        streaming=True,
        token=hf_token,
    )

    count = 0
    skipped = 0
    current_shard: list[str] = []

    def flush_shard() -> None:
        nonlocal shard_idx
        path = output_dir / f"mc4_nl_shard_{shard_idx:06d}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for line in current_shard:
                f.write(line + "\n")
        logger.info("[mc4_nl] shard %06d written (%d docs)", shard_idx, len(current_shard))
        current_shard.clear()
        shard_idx += 1

    for example in ds:
        text = (example.get("text") or "").strip()
        if not text:
            continue

        if skipped < skip_docs:
            skipped += 1
            continue

        record = {
            "text":      text,
            "url":       example.get("url", ""),
            "timestamp": example.get("timestamp", ""),
        }
        current_shard.append(json.dumps(record, ensure_ascii=False))
        count += 1

        if len(current_shard) >= shard_size:
            flush_shard()

        if count >= max_docs:
            break

    if current_shard:
        flush_shard()

    _log(output_dir)
    logger.info("[mc4_nl] Done. %d documents downloaded.", count)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download mC4-nl-cleaned Dutch corpus (ODC-By)")
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-docs",   type=int, default=1_000_000)
    parser.add_argument("--shard-size", type=int, default=100_000)
    parser.add_argument("--hf-token",   default=None)
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        max_docs=args.max_docs,
        shard_size=args.shard_size,
        hf_token=args.hf_token,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
