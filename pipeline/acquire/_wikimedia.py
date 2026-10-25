"""Shared download helper for Wikimedia XML dumps."""
from __future__ import annotations

import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def download_dump(
    source: str,
    dump_url: str,
    dump_filename: str,
    output_dir: Path,
    license_: str = "CC-BY-SA",
    dry_run: bool = False,
) -> None:
    """Stream-download a Wikimedia bz2 dump to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / dump_filename

    logger.info("[%s] License: %s", source, license_)

    if dry_run:
        logger.info("[%s] [dry-run] skipping", source)
        return

    if dest.exists():
        logger.info("[%s] Already downloaded: %s", source, dest)
        return

    logger.info("[%s] Downloading dump …", source)
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; Wikimedia dump)"}
    req = urllib.request.Request(dump_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        logger.info("[%s] %.1f%%", source, downloaded / total * 100)
        entry = {
            "source": source,
            "url": dump_url,
            "license": license_,
            "acquisition_date": datetime.date.today().isoformat(),
        }
        with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("[%s] Saved: %s", source, dest)
    except Exception as exc:
        logger.error("[%s] Download failed: %s", source, exc)
        dest.unlink(missing_ok=True)
