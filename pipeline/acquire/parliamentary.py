"""
Download Dutch parliamentary proceedings (Kamerstukken / Handelingen).

Source: Officiële Bekendmakingen Open Data (public domain)
Endpoint: https://repository.overheid.nl/sru
Product areas: kamerstuk, handelingen, kamervragen

NOTE: The query format c.product-area=={area} returns 0 results for parliamentary
areas.  The correct query uses c.content-area with the officielepublicaties/{code}
prefix.  Area-to-content-area-code mapping is defined in _AREA_CONTENT_PREFIX below.
The fallback free-text query is used for areas without a known prefix.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SRU_BASE = "https://repository.overheid.nl/sru"
SOURCE = "parliamentary"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN = "parliamentary"

PRODUCT_AREAS = ["kamerstuk", "handelingen", "kamervragen", "staatsblad", "ag"]

# Maps logical area name → c.content-area prefix in repository.overheid.nl.
# Confirmed working via live SRU probes (2026-03).
# c.product-area=={area} returns 0 for all parliamentary areas; use content-area instead.
_AREA_CONTENT_PREFIX = {
    "kamerstuk":   "officielepublicaties/kst",
    "handelingen": "officielepublicaties/h-tk",   # Handelingen Tweede Kamer
    "kamervragen": "officielepublicaties/ah",      # Aanhangsel Handelingen (kamervragen)
    "staatsblad":  "officielepublicaties/stb",
    "ag":          "officielepublicaties/ag",
}

NS_SRW = "http://www.loc.gov/zing/srw/"


def _log(output_dir: Path, url: str, area: str) -> None:
    entry = {
        "source": f"{SOURCE}:{area}",
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _build_url(area: str, start: int, page_size: int = 100) -> str:
    from urllib.parse import urlencode
    # Use c.content-area prefix query if a mapping is known; otherwise fall back
    # to a free-text keyword search.  c.product-area=={area} returns 0 for all
    # parliamentary areas on repository.overheid.nl (confirmed 2026-03).
    prefix = _AREA_CONTENT_PREFIX.get(area)
    if prefix:
        query = f'c.content-area any "{prefix}"'
    else:
        query = area
    params = {
        "operation": "searchRetrieve",
        "version": "1.2",
        "query": query,
        "startRecord": start,
        "maximumRecords": page_size,
        "recordSchema": "gzd",
    }
    return f"{SRU_BASE}?{urlencode(params)}"


def _total_records(xml_bytes: bytes) -> int:
    root = ET.fromstring(xml_bytes)
    # Use namespace-agnostic search — the SRU namespace varies across responses
    el = root.find(".//{*}numberOfRecords")
    if el is None:
        # Fallback to explicit namespace
        el = root.find(f".//{{{NS_SRW}}}numberOfRecords")
    return int(el.text) if el is not None else 0


def download_area(
    area: str,
    output_dir: Path,
    max_records: int = 50_000,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    area_dir = output_dir / area
    area_dir.mkdir(parents=True, exist_ok=True)
    start = 1
    fetched = 0

    while fetched < max_records:
        url = _build_url(area, start, page_size)
        logger.info("[%s] records %d-%d", area, start, start + page_size - 1)

        if dry_run:
            logger.info("[dry-run] skipping download")
            break

        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()

        _log(output_dir, url, area)
        total = _total_records(data)

        batch_file = area_dir / f"batch_{start:08d}.xml"
        batch_file.write_bytes(data)
        logger.info("Saved %s (total available: %d)", batch_file.name, total)

        fetched += page_size
        start += page_size
        if start > total:
            break
        time.sleep(0.5)

    logger.info("[%s] Done. Fetched ~%d records.", area, fetched)


def download(
    output_dir: Path,
    areas: list[str] | None = None,
    max_records: int = 50_000,
    dry_run: bool = False,
) -> None:
    areas = areas or PRODUCT_AREAS
    for area in areas:
        download_area(area, output_dir, max_records=max_records, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Dutch parliamentary proceedings")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--areas", nargs="+", default=PRODUCT_AREAS, choices=PRODUCT_AREAS)
    parser.add_argument("--max-records", type=int, default=50_000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, areas=args.areas, max_records=args.max_records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
