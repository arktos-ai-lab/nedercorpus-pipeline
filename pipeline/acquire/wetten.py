"""
Download Dutch legislation from wetten.nl via the SRU/CQL API.

Endpoint: https://repository.overheid.nl/sru
Documentation: https://www.officielebekendmakingen.nl/doe-mee/open-data/
All content is public domain (Dutch government works).

NOTE: The query c.product-area==bwb returns 0 results (2026-03).
BWB (Basis Wetten Bestand) consolidated texts live at wetten.overheid.nl but
are not indexed under that product-area in repository.overheid.nl.
The correct approach is to query Staatsblad publications via
c.content-area any "officielepublicaties/stb" which covers all laws and AMvBs
published in the official gazette (~49k records).
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
SOURCE = "wetten"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN = "legal"

# SRU namespaces
NS = {
    "srw": "http://www.loc.gov/zing/srw/",
    "gzd": "http://standaarden.overheid.nl/sru",
}


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _sru_url(query: str, start: int, max_records: int = 100) -> str:
    from urllib.parse import urlencode
    params = {
        "operation": "searchRetrieve",
        "version": "1.2",
        "query": query,
        "startRecord": start,
        "maximumRecords": max_records,
        "recordSchema": "gzd",
    }
    return f"{SRU_BASE}?{urlencode(params)}"


def _fetch(url: str, retries: int = 6, backoff: float = 10.0) -> bytes:
    import urllib.error
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                wait = backoff * (2 ** attempt)  # exponential: 10, 20, 40, 80, 160, 320s
                logger.warning("Retry %d/%d after 503 (backing off %ds)", attempt + 1, retries, int(wait))
                if attempt == retries - 1:
                    raise
                time.sleep(wait)
            else:
                if attempt == retries - 1:
                    raise
                logger.warning("Retry %d after error: %s", attempt + 1, exc)
                time.sleep(backoff)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            logger.warning("Retry %d after error: %s", attempt + 1, exc)
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError("unreachable")


def _parse_records(xml_bytes: bytes) -> tuple[list[dict], int]:
    """Parse SRU response. Returns (records, total_count)."""
    root = ET.fromstring(xml_bytes)
    # Use namespace-agnostic search first; the SRU namespace varies
    total_el = root.find(".//{*}numberOfRecords")
    if total_el is None:
        total_el = root.find(".//srw:numberOfRecords", NS)
    total = int(total_el.text) if total_el is not None else 0
    records = []
    for rec in root.findall(".//{*}record"):
        rec_data = rec.find(".//{*}recordData")
        if rec_data is None:
            rec_data = rec.find(".//srw:recordData", NS)
        if rec_data is not None:
            records.append({"xml": ET.tostring(rec_data, encoding="unicode")})
    return records, total


def download(
    output_dir: Path,
    query: str = 'c.content-area any "officielepublicaties/stb"',
    max_records: int = 50_000,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch legislation records via SRU.

    Args:
        query: CQL query string.  Default fetches Staatsblad publications
               (laws, AMvBs, etc.).  c.product-area==bwb returns 0 results
               on repository.overheid.nl (confirmed 2026-03).
        max_records: Maximum total records to download.
        page_size: Records per API request.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resume: find the last saved batch file and start from there
    existing = sorted(output_dir.glob("wetten_batch_*.xml"))
    if existing:
        last_start = int(existing[-1].stem.split("_")[-1])
        start = last_start + page_size
        fetched = last_start + page_size - 1
        logger.info("Resuming from record %d (%d batches already saved)", start, len(existing))
    else:
        start = 1
        fetched = 0

    while fetched < max_records:
        url = _sru_url(query, start, page_size)
        logger.info("Fetching records %d-%d — %s", start, start + page_size - 1, url)

        if dry_run:
            logger.info("[dry-run] stopping after first page")
            break

        data = _fetch(url)
        records, total = _parse_records(data)
        _log(output_dir, url)

        if not records:
            logger.info("No more records.")
            break

        batch_file = output_dir / f"wetten_batch_{start:08d}.xml"
        batch_file.write_bytes(data)
        logger.info("Saved %s (%d records, total available: %d)", batch_file.name, len(records), total)

        fetched += len(records)
        start += page_size

        if start > total:
            break

        time.sleep(0.5)  # be polite

    logger.info("Done. Downloaded %d records.", fetched)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Dutch legislation via wetten.nl SRU API")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--query", default='c.content-area any "officielepublicaties/stb"')
    parser.add_argument("--max-records", type=int, default=50_000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, query=args.query, max_records=args.max_records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
