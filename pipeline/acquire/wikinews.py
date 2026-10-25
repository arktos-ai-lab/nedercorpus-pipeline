"""
Download the Dutch Wikinews XML dump.

Wikinews is a free-content news wiki. The Dutch edition contains thousands
of news articles published between 2004 and the present.

License: CC-BY 2.5
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "wikinews"
LICENSE = "CC-BY"
DOMAIN  = "news"

DUMP_URL = (
    "https://dumps.wikimedia.org/nlwikinews/latest/"
    "nlwikinews-latest-pages-articles.xml.bz2"
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


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download the latest Dutch Wikinews XML dump."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "nlwikinews-latest-pages-articles.xml.bz2"

    logger.info("[wikinews] License: CC-BY 2.5")

    if dry_run:
        logger.info("[wikinews] [dry-run] skipping")
        return

    if dest.exists():
        logger.info("[wikinews] Already downloaded: %s", dest)
        return

    logger.info("[wikinews] Downloading dump …")
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; Wikimedia dump)"}
    req = urllib.request.Request(DUMP_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
        _log(output_dir, DUMP_URL)
        logger.info("[wikinews] Saved: %s", dest)
    except Exception as exc:
        logger.error("[wikinews] Download failed: %s", exc)
        dest.unlink(missing_ok=True)
