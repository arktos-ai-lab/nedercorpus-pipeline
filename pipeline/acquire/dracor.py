"""
Download the Dutch Drama Corpus (DutchDraCor).

DutchDraCor is a collection of 186 Dutch-language plays from 1500-1800,
encoded in TEI P5 XML and released under CC0.

Source: https://github.com/dracor-org/dutchdracor
License: CC0 (public domain)
Domain: drama / historical literature
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

SOURCE  = "dracor"
LICENSE = "CC0"
DOMAIN  = "literature"

REPO_ZIP_URL = (
    "https://github.com/dracor-org/dutchdracor/archive/refs/heads/main.zip"
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
    """
    Download DutchDraCor TEI XML files from GitHub.

    Downloads the repository ZIP and extracts the tei/ directory containing
    one .xml file per play.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    tei_dir = output_dir / "tei"

    logger.info("[dracor] License: CC0 — Dutch Drama Corpus (186 plays, 1500-1800)")

    if dry_run:
        logger.info("[dracor] [dry-run] skipping")
        return

    if tei_dir.exists() and any(tei_dir.glob("*.xml")):
        logger.info("[dracor] TEI files already present — skipping")
        return

    tei_dir.mkdir(exist_ok=True)

    logger.info("[dracor] Downloading repository ZIP …")
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; DutchDraCor CC0)"}
    req = urllib.request.Request(REPO_ZIP_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            zip_data = resp.read()
    except Exception as exc:
        logger.error("[dracor] Download failed: %s", exc)
        return

    logger.info("[dracor] Extracting TEI XML files …")
    extracted = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                # Extract only tei/*.xml files
                parts = name.split("/")
                if len(parts) >= 3 and parts[1] == "tei" and name.endswith(".xml"):
                    dest = tei_dir / parts[-1]
                    dest.write_bytes(zf.read(name))
                    extracted += 1
    except Exception as exc:
        logger.error("[dracor] ZIP extraction failed: %s", exc)
        return

    logger.info("[dracor] Extracted %d TEI XML plays", extracted)
    _log(output_dir)
