"""
Download Dutch disciplinary tribunal decisions via the overheid.nl SRU API.

Tuchtrecht covers disciplinary proceedings for regulated professions:
  - Medical (KNMG — artsen, specialisten, verpleegkundigen)
  - Legal (Deken — advocaten, notarissen)
  - Accountancy (NBA — accountants, registeraccountants)
  - Press (Raad voor de Journalistiek)
  - Others (dierenartsen, makelaars, etc.)

Decisions are published on tuchtrecht.overheid.nl and indexed in the
overheid.nl repository under product-area 'tuchtrecht'.

License: Public Domain (Art. 11 Auteurswet — government/court texts)

SRU endpoint: https://repository.overheid.nl/sru
Query: c.product-area==tuchtrecht
"""
from __future__ import annotations

import datetime
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "tuchtrecht"
LICENSE = "PD"
SRU_BASE = "https://repository.overheid.nl/sru"
NS = {"srw": "http://www.loc.gov/zing/srw/"}


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _sru_url(start: int, page_size: int) -> str:
    params = {
        "operation":      "searchRetrieve",
        "version":        "2.0",
        "query":          "dt.identifier any tuchtrecht",
        "startRecord":    start,
        "maximumRecords": page_size,
        "recordSchema":   "gzd",
    }
    return f"{SRU_BASE}?{urllib.parse.urlencode(params)}"


def _fetch(url: str, retries: int = 3) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; open government data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _total_records(xml_bytes: bytes) -> int:
    try:
        root = ET.fromstring(xml_bytes)
        el = root.find(".//{*}numberOfRecords")
        return int(el.text) if el is not None and el.text else 0
    except Exception:
        return 0


def download(
    output_dir: Path,
    max_records: int = 500_000,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    """Download tuchtrecht disciplinary decisions via overheid.nl SRU."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[tuchtrecht] [dry-run] skipping")
        return

    start = 1
    fetched = 0

    logger.info("[tuchtrecht] Downloading tuchtrecht decisions (max %d)", max_records)

    while fetched < max_records:
        batch_file = output_dir / f"tucht_batch_{start:08d}.xml"
        if batch_file.exists():
            fetched += page_size
            start += page_size
            continue

        url = _sru_url(start, page_size)
        try:
            data = _fetch(url)
        except Exception as exc:
            logger.warning("[tuchtrecht] Request failed at start=%d: %s", start, exc)
            break

        total = _total_records(data)
        if total == 0 and start == 1:
            logger.error("[tuchtrecht] No records found. Check product-area query.")
            break

        batch_file.write_bytes(data)
        _log(output_dir, url)
        logger.info("[tuchtrecht] Saved %s (total: %d)", batch_file.name, total)

        fetched += page_size
        start += page_size

        if start > min(total, max_records):
            logger.info("[tuchtrecht] Reached end at %d records.", fetched)
            break

        time.sleep(0.5)

    logger.info("[tuchtrecht] Done. %d records fetched.", fetched)
