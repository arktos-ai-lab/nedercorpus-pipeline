"""
Download the Dutch Renaissance Poetry Corpus.

A collection of 16th-17th century Dutch poetry (alexandrines and iambic
pentameters) with orthographic annotations, funded by KNAW.

Source: https://github.com/mirsdes/Dutch_Renaissance_poetry_corpus
License: CC0 / Public Domain (texts from DBNL)
Domain: literature / historical poetry
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

SOURCE  = "renaissance_poetry"
LICENSE = "CC0"
DOMAIN  = "literature"

REPO_ZIP_URL = (
    "https://github.com/mirsdes/Dutch_Renaissance_poetry_corpus/archive/refs/heads/master.zip"
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
    """Download Dutch Renaissance Poetry Corpus from GitHub."""
    output_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = output_dir / "texts"

    logger.info("[renaissance_poetry] License: CC0 — Dutch Renaissance poetry (1500-1700)")

    if dry_run:
        logger.info("[renaissance_poetry] [dry-run] skipping")
        return

    if texts_dir.exists() and any(texts_dir.iterdir()):
        logger.info("[renaissance_poetry] Already downloaded — skipping")
        return

    texts_dir.mkdir(exist_ok=True)

    logger.info("[renaissance_poetry] Downloading repository ZIP …")
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; CC0 Dutch poetry)"}
    req = urllib.request.Request(REPO_ZIP_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            zip_data = resp.read()
    except Exception as exc:
        logger.error("[renaissance_poetry] Download failed: %s", exc)
        return

    extracted = 0
    text_extensions = (".txt", ".text", ".csv", ".tsv", ".xml")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if not name.endswith("/") and any(name.endswith(e) for e in text_extensions):
                    # Use last two path components to avoid collisions
                    parts = name.split("/")
                    filename = "_".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
                    dest = texts_dir / filename
                    dest.write_bytes(zf.read(name))
                    extracted += 1
    except Exception as exc:
        logger.error("[renaissance_poetry] ZIP extraction failed: %s", exc)
        return

    logger.info("[renaissance_poetry] Extracted %d files", extracted)
    _log(output_dir)
