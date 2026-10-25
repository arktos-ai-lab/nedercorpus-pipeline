"""
Download the OSCAR Dutch corpus subset via the HuggingFace Hub.

OSCAR is a quality-filtered Common Crawl subset released by the OSCAR project (INRIA).

NOTE — LICENSE CAVEAT:
  OSCAR-2301 is currently GATED on HuggingFace (manual access approval required).
  The dataset card explicitly states: "Only the metadata and annotations have a cc0-1.0
  license, and the rest of the content is crawled data derived from Common Crawl,
  for which the authors of OSCAR do not hold any copyright whatsoever."

  The CC0 label covers the OSCAR project's own curation work (language detection,
  annotations, deduplication) — NOT the underlying web content. This is the same
  provenance situation as FineWeb-2 (ODC-By) and HPLT (CC0): the corpus project
  dedicates its own contributions, not the crawled text.

  Legal basis for use: EU DSM Directive Art. 3-4 TDM research exception.
  For a corpus paper, disclose the provenance honestly: "web crawl derivative
  subject to national copyright law TDM exceptions."

  Recommendation: prefer FineWeb-2 (better quality filtering) + C5 Dutch
  (per-document CC license verification) as primary web sources. OSCAR adds
  ~10B tokens of additional Dutch web text but with weaker legal provenance.

  If you need a HuggingFace token for gated access, set HF_TOKEN in environment
  or pass --hf-token. Access is currently granted for academic research use.

Requires: pip install datasets huggingface_hub
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE = "oscar"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN = "web"
HF_DATASET = "oscar-corpus/OSCAR-2301"
HF_CONFIG = "nl"


def _log(output_dir: Path) -> None:
    entry = {
        "source": SOURCE,
        "url": f"https://huggingface.co/datasets/{HF_DATASET}",
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    split: str = "train",
    max_docs: int = 500_000,
    shard_size: int = 100_000,
    dry_run: bool = False,
    hf_token: str | None = None,
) -> None:
    """
    Stream the OSCAR Dutch dataset and write shards of JSONL files.

    Args:
        max_docs: Maximum number of documents to download.
        shard_size: Documents per output shard file.
        hf_token: HuggingFace token for gated datasets.

    NOTE: Excluded from NederCorpus default build (§17.L).
    OSCAR-2301 is explicitly gated by HuggingFace pending legal clarification.
    The CC0 label covers dataset curation only, not the underlying CommonCrawl
    web content. NederCorpus requires source content to be explicitly CC/PD.
    """
    logger.warning(
        "[oscar] EXCLUDED by NederCorpus clean-corpus policy (§17.L). "
        "OSCAR-2301 is gated pending legal clarification; CC0 covers curation "
        "only, not underlying web content. Use c5_dutch instead. Skipping."
    )
    return

    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit("Install 'datasets': pip install datasets")

    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[dry-run] would download OSCAR Dutch from %s", HF_DATASET)
        return

    logger.info("Streaming OSCAR Dutch (max %d docs)...", max_docs)
    ds = load_dataset(
        HF_DATASET,
        HF_CONFIG,
        split=split,
        streaming=True,
        token=hf_token,
        trust_remote_code=True,
    )

    shard_idx = 0
    count = 0
    current_shard: list[str] = []

    def flush_shard() -> None:
        nonlocal shard_idx
        path = output_dir / f"oscar_nl_shard_{shard_idx:06d}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for line in current_shard:
                f.write(line + "\n")
        logger.info("Wrote shard %s (%d docs)", path.name, len(current_shard))
        current_shard.clear()
        shard_idx += 1

    for example in ds:
        text = example.get("text", "").strip()
        if not text:
            continue
        record = {
            "text": text,
            "meta": example.get("meta", {}),
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
    logger.info("Done. Downloaded %d documents.", count)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download OSCAR Dutch corpus")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=500_000)
    parser.add_argument("--shard-size", type=int, default=100_000)
    parser.add_argument("--hf-token", default=None, help="HuggingFace token for gated datasets")
    parser.add_argument("--dry-run", action="store_true")
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
