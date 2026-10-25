"""
Download OpenBoek — Dutch public-domain literary texts with annotations.

OpenBoek contains classic Dutch novels, plays, and poetry from Project
Gutenberg with linguistic annotations (coreference, named entities).
We extract only the plain text.

Source: https://github.com/andreasvc/openboek
License: CC-BY 4.0
Domain: literature
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "openboek"
LICENSE = "CC-BY"
DOMAIN  = "literature"

REPO_ZIP_URL = (
    "https://github.com/andreasvc/openboek/archive/refs/heads/master.zip"
)


def _log(output_dir: Path) -> None:
    entry = {
        "source": SOURCE,
        "url": REPO_ZIP_URL,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download OpenBoek text files from GitHub."""
    output_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = output_dir / "texts"

    logger.info("[openboek] License: CC-BY 4.0 — Dutch literary texts")

    if dry_run:
        logger.info("[openboek] [dry-run] skipping")
        return

    if texts_dir.exists() and any(texts_dir.iterdir()):
        logger.info("[openboek] Already downloaded — skipping")
        return

    texts_dir.mkdir(exist_ok=True)

    logger.info("[openboek] Downloading repository ZIP …")
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; OpenBoek CC-BY 4.0)"}
    req = urllib.request.Request(REPO_ZIP_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            zip_data = resp.read()
    except Exception as exc:
        logger.error("[openboek] Download failed: %s", exc)
        return

    extracted = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                # Extract plain text files (.txt) and any readable text format
                if name.endswith((".txt", ".text")) and not name.endswith("/"):
                    filename = Path(name).name
                    dest = texts_dir / filename
                    dest.write_bytes(zf.read(name))
                    extracted += 1
    except Exception as exc:
        logger.error("[openboek] ZIP extraction failed: %s", exc)
        return

    if extracted == 0:
        # Try extracting everything and let the parser handle format detection
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for name in zf.namelist():
                    if not name.endswith("/"):
                        dest = texts_dir / Path(name).name
                        dest.write_bytes(zf.read(name))
                        extracted += 1
        except Exception as exc:
            logger.error("[openboek] Full extraction failed: %s", exc)

    logger.info("[openboek] Extracted %d files", extracted)
    _log(output_dir)
