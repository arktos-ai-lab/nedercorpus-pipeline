"""
Download GLOBALISE VOC transcriptions from IISH Dataverse.

Machine-generated HTR transcriptions of ~4.8M page images from the Dutch East
India Company (VOC) archives (1610-1796). Early Modern Dutch.

Dataset: hdl:10622/JCTCJ2 — "VOC transcriptions - GLOBALISE"
Dataverse: https://datasets.iisg.amsterdam/
License: CC0 1.0 Universal
Format: ZIP of .txt files (one per inventory number)
Size: ~2.2 GB compressed

NOTE: Distinct from voc_wic_htr (Zenodo 11209325) which covers VOC+WIC
and uses a different file structure.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "globalise_voc"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "historical"

_DATAVERSE_API   = "https://datasets.iisg.amsterdam/api"
_PERSISTENT_ID   = "hdl:10622/JCTCJ2"
_FILE_ID         = 14248   # globalise_transcriptions ZIP
_DOWNLOAD_URL    = f"{_DATAVERSE_API}/access/datafile/{_FILE_ID}"
_USER_AGENT      = "NederCorpus/0.1 (academic research; Early Modern Dutch corpus)"

# Max inventory text files to extract (None = all ~10K files)
# Each inventory is typically 100-1000 pages. Defaults to None (all).
_MAX_INVENTORIES: int | None = None

# Minimum text length to keep an inventory file
_MIN_TEXT_LEN = 500


def _fetch_bytes(url: str, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()
    out_file = output_dir / "globalise_voc_records.jsonl"
    done_flag = output_dir / "done.flag"

    if done_flag.exists():
        logger.info("[globalise_voc] Already acquired (done.flag) — skipping.")
        return

    if dry_run:
        logger.info("[globalise_voc] [dry-run] Would download from %s", _DOWNLOAD_URL)
        return

    logger.info("[globalise_voc] Downloading GLOBALISE VOC ZIP (~2.2 GB) from %s", _DOWNLOAD_URL)
    logger.info("[globalise_voc] This will take several minutes...")
    _log(output_dir, _DOWNLOAD_URL)

    # Download the ZIP in streaming fashion
    zip_path = output_dir / "globalise_transcriptions.zip"

    if not zip_path.exists():
        logger.info("[globalise_voc] Downloading ZIP to %s ...", zip_path)
        req = urllib.request.Request(_DOWNLOAD_URL, headers={"User-Agent": _USER_AGENT})
        chunk_size = 1024 * 1024  # 1 MB chunks
        downloaded = 0
        with urllib.request.urlopen(req, timeout=600) as resp, zip_path.open("wb") as fout:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                fout.write(chunk)
                downloaded += len(chunk)
                if downloaded % (50 * 1024 * 1024) == 0:
                    logger.info("[globalise_voc] Downloaded %.0f MB...", downloaded / 1024 / 1024)
        logger.info("[globalise_voc] Download complete: %.1f MB", downloaded / 1024 / 1024)
    else:
        logger.info("[globalise_voc] ZIP already downloaded: %s", zip_path)

    # Extract and write JSONL
    logger.info("[globalise_voc] Extracting inventories from ZIP...")
    total = 0
    skipped = 0

    with out_file.open("w", encoding="utf-8") as fout, \
         zipfile.ZipFile(zip_path, "r") as zf:

        txt_names = [n for n in zf.namelist() if n.endswith(".txt")]
        logger.info("[globalise_voc] %d inventory text files in ZIP", len(txt_names))

        target = txt_names if _MAX_INVENTORIES is None else txt_names[:_MAX_INVENTORIES]

        for i, name in enumerate(target):
            try:
                text = zf.read(name).decode("utf-8", errors="replace").strip()
            except Exception as exc:
                logger.debug("[globalise_voc] Read error %s: %s", name, exc)
                skipped += 1
                continue

            if len(text) < _MIN_TEXT_LEN:
                skipped += 1
                continue

            # Inventory number from filename (e.g. "1.04.02_1053.txt" → 1053)
            inv_id = name.replace(".txt", "").split("/")[-1]

            record = {
                "inventory_id": inv_id,
                "text": text,
                "source_url": f"https://www.nationaalarchief.nl/onderzoeken/archief/1.04.02/",
                "license": LICENSE,
                "country": COUNTRY,
                "domain": DOMAIN,
                "language_variant": "NL",  # Early Modern Dutch
                "acquisition_date": acq_date,
            }
            fout.write(json.dumps(record, ensure_ascii=False) + "\n")
            total += 1

            if (i + 1) % 500 == 0:
                logger.info("[globalise_voc] Extracted %d/%d inventories (%d saved)", i + 1, len(target), total)

    logger.info("[globalise_voc] Done. %d inventories saved (%d skipped).", total, skipped)
    done_flag.write_text(datetime.date.today().isoformat(), encoding="utf-8")
