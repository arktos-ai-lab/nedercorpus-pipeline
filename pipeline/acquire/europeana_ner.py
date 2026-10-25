"""
Download the Europeana Newspapers NER Corpus — Dutch subset.

Historical Dutch newspaper text from KB (Koninklijke Bibliotheek) with
BIO named-entity annotations (Person, Location, Organization).
OCR text from 19th–early 20th century Dutch newspapers.

Source: https://github.com/EuropeanaNewspapers/ner-corpora
License: CC0 1.0
Domain: news / historical
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

SOURCE  = "europeana_ner"
LICENSE = "CC0"
DOMAIN  = "news"

REPO_ZIP_URL = (
    "https://github.com/EuropeanaNewspapers/ner-corpora/archive/refs/heads/master.zip"
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
    """Download Europeana Newspapers Dutch NER corpus from GitHub."""
    output_dir.mkdir(parents=True, exist_ok=True)
    bio_dir = output_dir / "bio"

    logger.info("[europeana_ner] License: CC0 — historical Dutch newspaper NER corpus")

    if dry_run:
        logger.info("[europeana_ner] [dry-run] skipping")
        return

    if bio_dir.exists() and any(bio_dir.glob("*.bio")):
        logger.info("[europeana_ner] Already downloaded — skipping")
        return

    bio_dir.mkdir(exist_ok=True)

    logger.info("[europeana_ner] Downloading repository ZIP …")
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; Europeana NER CC0)"}
    req = urllib.request.Request(REPO_ZIP_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            zip_data = resp.read()
    except Exception as exc:
        logger.error("[europeana_ner] Download failed: %s", exc)
        return

    extracted = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                # Extract Dutch BIO files only (enp_NL.*.bio)
                basename = name.split("/")[-1]
                if basename.startswith("enp_NL.") and basename.endswith(".bio"):
                    dest = bio_dir / basename
                    dest.write_bytes(zf.read(name))
                    extracted += 1
    except Exception as exc:
        logger.error("[europeana_ner] ZIP extraction failed: %s", exc)
        return

    logger.info("[europeana_ner] Extracted %d Dutch BIO files", extracted)
    _log(output_dir)
