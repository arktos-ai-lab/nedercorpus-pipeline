"""
Download Dutch Staatscourant (government gazette) via the SRU API.

The Staatscourant is the official gazette of the Dutch government,
containing ministerial decrees, environmental permits, official notices,
job postings, and all formal government announcements.

SRU endpoint: https://repository.overheid.nl/sru
Query: c.product-area==stcrt

The repository.overheid.nl SRU holds 6.8M+ records across all official
publications. The stcrt (Staatscourant) product-area is one large subset.

License: Public Domain
  Dutch government publications have no copyright under Article 11 of
  the Dutch Copyright Act (Auteurswet), which explicitly excludes
  government acts, decisions, and orders from copyright protection.
"""
from __future__ import annotations

import argparse
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

SOURCE  = "staatscourant"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "government"

SRU_BASE = "https://repository.overheid.nl/sru"

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


def _sru_url(query: str, start: int, max_records: int) -> str:
    params = {
        "operation":      "searchRetrieve",
        "version":        "2.0",
        "query":          query,
        "startRecord":    start,
        "maximumRecords": max_records,
        "recordSchema":   "gzd",
    }
    return f"{SRU_BASE}?{urllib.parse.urlencode(params)}"


def _fetch(url: str, retries: int = 3, backoff: float = 2.0) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; open government data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = backoff * (attempt + 1)
            logger.warning("Retry %d after error: %s (wait %.1fs)", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _count_records(xml_bytes: bytes) -> int:
    try:
        root = ET.fromstring(xml_bytes)
        el = root.find(".//{*}numberOfRecords")
        return int(el.text) if el is not None and el.text else 0
    except Exception:
        return 0


def download(
    output_dir: Path,
    query: str = "dt.identifier any stcrt",
    max_records: int = 500_000,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    """
    Download Staatscourant records via the overheid.nl SRU endpoint.

    Saves paginated SRU response XML as stcrt_batch_{start:08d}.xml.
    The parse stage reads these batch files and extracts document text.

    Args:
        query:       CQL query. Default fetches all Staatscourant records.
        max_records: Maximum total records to download.
        page_size:   Records per SRU request (max 100 per API docs).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    start = 1
    fetched = 0

    logger.info("[staatscourant] Query: %s (max %d records)", query, max_records)

    while fetched < max_records:
        url = _sru_url(query, start, page_size)
        logger.info("[staatscourant] Fetching records %d–%d", start, start + page_size - 1)

        if dry_run:
            logger.info("[dry-run] stopping after first page check")
            break

        # Skip already-downloaded batch files
        batch_file = output_dir / f"stcrt_batch_{start:08d}.xml"
        if batch_file.exists():
            fetched += page_size
            start += page_size
            continue

        try:
            data = _fetch(url)
        except Exception as exc:
            logger.warning("[staatscourant] Request failed at start=%d: %s", start, exc)
            break

        total = _count_records(data)
        if total == 0 and start == 1:
            logger.error("[staatscourant] No records found. Check query: %s", query)
            break

        batch_file.write_bytes(data)
        _log(output_dir, url)
        logger.info("[staatscourant] Saved %s (total available: %d)", batch_file.name, total)

        fetched += page_size
        start += page_size

        if start > min(total, max_records):
            logger.info("[staatscourant] Reached end at %d records.", fetched)
            break

        time.sleep(0.5)

    logger.info("[staatscourant] Done. %d records fetched.", fetched)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Dutch Staatscourant via SRU (public domain)"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--query",       default="dt.identifier any stcrt")
    parser.add_argument("--max-records", type=int, default=500_000)
    parser.add_argument("--page-size",   type=int, default=100)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        query=args.query,
        max_records=args.max_records,
        page_size=args.page_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
