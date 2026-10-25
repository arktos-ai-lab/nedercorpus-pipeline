"""
Download the Dutch Wikibooks XML dump.

Wikibooks hosts free educational textbooks and manuals. The Dutch edition
contains hundreds of books on science, history, languages, and more.

License: CC-BY-SA 3.0 / GFDL (dual-licensed)
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "wikibooks"
LICENSE = "CC-BY-SA"
DOMAIN  = "educational"

DUMP_URL = (
    "https://dumps.wikimedia.org/nlwikibooks/latest/"
    "nlwikibooks-latest-pages-articles.xml.bz2"
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
    """Download the latest Dutch Wikibooks XML dump."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "nlwikibooks-latest-pages-articles.xml.bz2"

    logger.info("[wikibooks] License: CC-BY-SA 3.0")

    if dry_run:
        logger.info("[wikibooks] [dry-run] skipping")
        return

    if dest.exists():
        logger.info("[wikibooks] Already downloaded: %s", dest)
        return

    logger.info("[wikibooks] Downloading dump …")
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
        logger.info("[wikibooks] Saved: %s", dest)
    except Exception as exc:
        logger.error("[wikibooks] Download failed: %s", exc)
        dest.unlink(missing_ok=True)
