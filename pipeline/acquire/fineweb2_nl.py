"""
Download FineWeb-2 Dutch corpus via HuggingFace (ODC-By 1.0).

FineWeb-2 is a high-quality multilingual web corpus derived from 90+ Common
Crawl dumps with aggressive quality filtering (trafilatura extraction,
language detection, deduplication, quality signals). The Dutch subset
(ssmits/fineweb-2-dutch) contains ~83.5 million documents and is considered
the highest-quality Dutch web text corpus as of 2024.

License: ODC-By 1.0 (Open Data Commons Attribution License)
  https://opendatacommons.org/licenses/by/1-0/
  Requires attribution. Compatible with CC-BY for redistribution.

HuggingFace dataset: ssmits/fineweb-2-dutch

Requires: pip install datasets huggingface_hub

Usage:
  python -m pipeline acquire --sources fineweb2_nl
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE    = "fineweb2_nl"
LICENSE   = "ODC-By"
COUNTRY   = "NL"
DOMAIN    = "web"
HF_DATASET = "ssmits/fineweb-2-dutch"


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
    max_docs: int = 2_000_000,
    shard_size: int = 100_000,
    dry_run: bool = False,
    hf_token: str | None = None,
) -> None:
    """
    Stream FineWeb-2 Dutch and write shards of JSONL files.

    Each record: {text, url, date, id, quality_signals}.
    Shards saved as fineweb2_nl_shard_{N:06d}.jsonl.

    Default max_docs=2M is ~2-3% of the full dataset. Increase for more coverage.

    NOTE: Excluded from NederCorpus default build (§17.L).
    FineWeb-2 derives from CommonCrawl. The ODC-By license covers the dataset
    curation work, NOT the underlying web content. NederCorpus policy requires
    that source content itself be explicitly CC/PD licensed. Only C5 Dutch meets
    this bar (per-page CC license verified from HTML metadata).
    """
    logger.warning(
        "[fineweb2_nl] EXCLUDED by NederCorpus clean-corpus policy (§17.L). "
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
        logger.info("[fineweb2_nl] [dry-run] would download from %s", HF_DATASET)
        return

    # Skip already-written shards
    existing = len(list(output_dir.glob("fineweb2_nl_shard_*.jsonl")))
    skip_docs = existing * shard_size
    shard_idx = existing

    logger.info("[fineweb2_nl] Streaming (max %d docs, skipping %d)…", max_docs, skip_docs)
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
        path = output_dir / f"fineweb2_nl_shard_{shard_idx:06d}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for line in current_shard:
                f.write(line + "\n")
        logger.info("[fineweb2_nl] shard %06d written (%d docs)", shard_idx, len(current_shard))
        current_shard.clear()
        shard_idx += 1

    for example in ds:
        text = (example.get("text") or "").strip()
        if not text:
            continue

        # Resume: skip already-processed docs
        if skipped < skip_docs:
            skipped += 1
            continue

        record = {
            "text": text,
            "url":  example.get("url", ""),
            "date": example.get("date", ""),
            "id":   example.get("id", ""),
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
    logger.info("[fineweb2_nl] Done. %d documents downloaded.", count)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download FineWeb-2 Dutch corpus (ODC-By)")
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-docs",   type=int, default=2_000_000)
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
