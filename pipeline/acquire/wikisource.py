"""
Download the Dutch Wikisource XML dump.

Wikisource is the Wikimedia project for public-domain texts. The Dutch
edition (nl.wikisource.org) contains several thousand PD books, poems,
legal texts, and historical documents in WikiText markup.

Dump URL: https://dumps.wikimedia.org/nlwikisource/latest/
          nlwikisource-latest-pages-articles.xml.bz2

The dump is parsed by the existing wiki_dump module using the same
MediaWiki XML streaming parser used for Wikipedia.

License: CC-BY-SA 4.0
  User contributions are CC-BY-SA.  The underlying texts are public domain;
  the CC-BY-SA applies to any editorial additions made by Wikisource editors.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "wikisource"
LICENSE = "CC-BY-SA"
COUNTRY = "NL"
DOMAIN  = "literature"

DUMP_URL = (
    "https://dumps.wikimedia.org/nlwikisource/latest/"
    "nlwikisource-latest-pages-articles.xml.bz2"
)


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_stream(url: str, dest: Path, timeout: int = 600) -> None:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; Wikimedia dump)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download the latest Dutch Wikisource XML dump (~45 MB compressed).

    The .bz2 file is saved to output_dir and parsed by parse_wikisource_dir
    in the parse stage using the existing wiki_dump streaming parser.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "nlwikisource-latest-pages-articles.xml.bz2"

    logger.info("[wikisource] Source: %s", DUMP_URL)
    logger.info("[wikisource] License: CC-BY-SA 4.0")

    if dry_run:
        logger.info("[dry-run] skipping download")
        return

    if dest.exists():
        logger.info("[wikisource] Dump already present: %s", dest)
        return

    logger.info("[wikisource] Downloading dump (~45 MB) …")
    try:
        _fetch_stream(DUMP_URL, dest)
        _log(output_dir, DUMP_URL)
        logger.info("[wikisource] Saved: %s", dest)
    except Exception as exc:
        logger.error("[wikisource] Download failed: %s", exc)
        dest.unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Dutch Wikisource XML dump (CC-BY-SA)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
