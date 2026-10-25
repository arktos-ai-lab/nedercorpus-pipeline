"""
Download Eerste Kamer (Dutch Senate) parliamentary proceedings via overheid.nl SRU.

The KB/overheid SRU endpoint indexes Handelingen Eerste Kamer (plenary session
transcripts), published under CC0. This covers 1995-present.

Complementary to:
  - staten_generaal: both chambers 1814-1995 (CC0)
  - tweede_kamer_opendata: Tweede Kamer proceedings 2010-present (CC0)

Product areas used:
  h-ek   — Handelingen Eerste Kamer (plenary transcripts)
  ag-ek  — Vergaderstukken EK (EK procedural documents)

License: CC0 1.0 / Public Domain (Art. 11 Auteurswet)
URL: https://www.eerstekamer.nl/

Usage:
  python -m pipeline acquire --sources eerste_kamer
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "eerste_kamer"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "parliamentary"

_SRU_BASE  = "https://repository.overheid.nl/sru"
_UA        = "NederCorpus/0.1 (academic research; EK proceedings CC0)"
_PAGE_SIZE = 100
_MAX_REC   = 500_000

# Product areas for Eerste Kamer content
_AREAS = [
    ("h-ek",  "handelingen_ek",  "Handelingen Eerste Kamer"),
    ("ag-ek", "agenda_ek",       "Agenda / vergaderstukken EK"),
]

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _UA}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read()
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[ek] fetch failed %s: %s", url[:80], exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _sru_url(area: str, start: int) -> str:
    params = {
        "operation":      "searchRetrieve",
        "version":        "2.0",
        "query":          f"dt.identifier any {area}",
        "startRecord":    start,
        "maximumRecords": _PAGE_SIZE,
        "recordSchema":   "gzd",
    }
    return f"{_SRU_BASE}?{urllib.parse.urlencode(params)}"


def _total_records(xml_bytes: bytes) -> int:
    try:
        root = ET.fromstring(xml_bytes)
        el = root.find(".//{*}numberOfRecords")
        return int(el.text) if el is not None and el.text else 0
    except Exception:
        return 0


def download(output_dir: Path, max_records: int = _MAX_REC, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[ek] [dry-run] would download EK proceedings from SRU")
        return

    total_files = 0
    for area_code, subdir, label in _AREAS:
        area_dir = output_dir / subdir
        area_dir.mkdir(parents=True, exist_ok=True)
        start = 1
        fetched = 0

        logger.info("[ek] %s — acquiring up to %d records", label, max_records)

        while fetched < max_records:
            batch_file = area_dir / f"batch_{start:08d}.xml"
            if batch_file.exists():
                fetched += _PAGE_SIZE
                start += _PAGE_SIZE
                total_files += 1
                continue

            url = _sru_url(area_code, start)
            data = _fetch(url)
            if not data:
                logger.warning("[ek] %s: fetch failed at start=%d", label, start)
                break

            total = _total_records(data)
            if total == 0 and start == 1:
                logger.warning("[ek] %s: 0 records returned — area may not be indexed", label)
                break

            batch_file.write_bytes(data)
            total_files += 1
            logger.info("[ek] %s: batch at start=%d (total=%d)", label, start, total)

            fetched += _PAGE_SIZE
            start += _PAGE_SIZE
            if start > min(total, max_records):
                logger.info("[ek] %s: done (%d records)", label, fetched)
                break
            time.sleep(0.5)

    logger.info("[ek] Done. %d batch XML files saved", total_files)
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": _SRU_BASE, "license": LICENSE,
            "acquisition_date": acq_date, "total_files": total_files,
        }, ensure_ascii=False) + "\n")
