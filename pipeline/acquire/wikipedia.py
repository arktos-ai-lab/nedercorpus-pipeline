"""Download the Dutch Wikipedia XML dump and extract plain text."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

DUMP_INDEX_URL = "https://dumps.wikimedia.org/nlwiki/latest/nlwiki-latest-pages-articles.xml.bz2"
SOURCE = "wikipedia"
LICENSE = "CC-BY-SA"
COUNTRY = "NL"
DOMAIN = "encyclopedic"


def _log_entry(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    log_path = output_dir / "acquisition_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> Path:
    """Download the latest Dutch Wikipedia dump to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "nlwiki-latest-pages-articles.xml.bz2"

    logger.info("Source  : %s", DUMP_INDEX_URL)
    logger.info("License : %s", LICENSE)
    logger.info("Dest    : %s", dest)

    if dry_run:
        logger.info("[dry-run] skipping download")
        return dest

    import urllib.request

    logger.info("Downloading Wikipedia dump (this is large — several GB)...")
    urllib.request.urlretrieve(DUMP_INDEX_URL, dest)
    logger.info("Done: %s", dest)
    _log_entry(output_dir, DUMP_INDEX_URL)
    return dest


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Dutch Wikipedia dump")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
