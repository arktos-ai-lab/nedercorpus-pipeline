"""
Download Dutch local official gazettes via the overheid.nl SRU API.

Covers four publication types NOT in the existing Staatscourant source:
  gmb  — Gemeenteblad        (municipal gazette, 342 municipalities)
  prb  — Provincieblad       (provincial gazette, 12 provinces)
  wsb  — Waterschapsblad     (water board gazette, 21 water boards)
  bgr  — Blad Gemeenschappelijke Regelingen (shared arrangements gazette)

These contain locally adopted ordinances, zoning plans, environmental
permits, and all formal local government announcements.

License: Public Domain (Art. 11 Auteurswet)

SRU endpoint: https://repository.overheid.nl/sru
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

SOURCE  = "officiele_bekendmakingen"
LICENSE = "PD"
SRU_BASE = "https://repository.overheid.nl/sru"
NS = {"srw": "http://www.loc.gov/zing/srw/"}

# (product-area-code, subdirectory-name, human-readable-name)
PRODUCT_AREAS = [
    ("gmb", "gemeenteblad",     "Gemeenteblad"),
    ("prb", "provincieblad",    "Provincieblad"),
    ("wsb", "waterschapsblad",  "Waterschapsblad"),
    ("bgr", "bgr",              "Blad Gemeenschappelijke Regelingen"),
]


def _log(output_dir: Path, url: str, area: str) -> None:
    entry = {
        "source": f"{SOURCE}:{area}",
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _sru_url(product_area: str, start: int, page_size: int) -> str:
    params = {
        "operation":      "searchRetrieve",
        "version":        "2.0",
        "query":          f"dt.identifier any {product_area}",
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


def _download_area(
    area_code: str,
    area_dir: Path,
    max_records: int,
    page_size: int,
    label: str,
) -> None:
    area_dir.mkdir(parents=True, exist_ok=True)
    start = 1
    fetched = 0

    logger.info("[officiele_bekendmakingen] %s — max %d records", label, max_records)

    while fetched < max_records:
        batch_file = area_dir / f"batch_{start:08d}.xml"
        if batch_file.exists():
            fetched += page_size
            start += page_size
            continue

        url = _sru_url(area_code, start, page_size)
        try:
            data = _fetch(url)
        except Exception as exc:
            logger.warning("[officiele_bekendmakingen] %s failed at start=%d: %s", label, start, exc)
            break

        total = _total_records(data)
        if total == 0 and start == 1:
            logger.warning("[officiele_bekendmakingen] No records for %s", label)
            break

        batch_file.write_bytes(data)
        _log(area_dir.parent, url, area_code)
        logger.info("[officiele_bekendmakingen] %s: %s (total: %d)", label, batch_file.name, total)

        fetched += page_size
        start += page_size

        if start > min(total, max_records):
            logger.info("[officiele_bekendmakingen] %s: done at %d records.", label, fetched)
            break

        time.sleep(0.5)


def download(
    output_dir: Path,
    max_records_per_area: int = 5_000_000,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    """Download all four local official gazette types."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[officiele_bekendmakingen] [dry-run] skipping")
        return

    for area_code, subdir, label in PRODUCT_AREAS:
        _download_area(
            area_code=area_code,
            area_dir=output_dir / subdir,
            max_records=max_records_per_area,
            page_size=page_size,
            label=label,
        )

    logger.info("[officiele_bekendmakingen] All areas done.")
