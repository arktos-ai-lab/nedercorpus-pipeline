"""
Download Dutch parliamentary documents via the overheid.nl SRU API.

Covers six document type areas from the same endpoint:
  kst   — Kamerstukken (brieven, verslagen, nota's, moties)
  ah-tk — Kamervragen Aanhangsel Tweede Kamer (Q&A pairs)
  blg   — Bijlagen (parliamentary annexes)
  tk    — Handelingen TK (verbatim plenary transcripts)
  ek    — Eerste Kamer documents
  ah-ek — Kamervragen Aanhangsel Eerste Kamer

License: Public Domain (Art. 11 Auteurswet)
SRU endpoint: https://repository.overheid.nl/sru
Query pattern: dt.identifier any {area}
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

SOURCE  = "kamerstukken"
LICENSE = "PD"
SRU_BASE = "https://repository.overheid.nl/sru"

# Document types: key → (sru_area, max_records)
# Keys use underscores for filenames; SRU queries use the original hyphenated form.
_TYPES: dict[str, tuple[str, int]] = {
    "kst":   ("kst",   400_000),
    "ah_tk": ("ah-tk", 100_000),
    "blg":   ("blg",   120_000),
    "tk":    ("tk",    200_000),
    "ek":    ("ek",     15_000),
    "ah_ek": ("ah-ek",  1_000),
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


def _sru_url(sru_area: str, start: int, page_size: int) -> str:
    params = {
        "operation":      "searchRetrieve",
        "version":        "2.0",
        "query":          f"dt.identifier any {sru_area}",
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


def _download_type(
    output_dir: Path,
    type_key: str,
    sru_area: str,
    max_records: int,
    page_size: int,
) -> None:
    """Download all pages for a single document type."""
    start = 1
    fetched = 0

    logger.info(
        "[kamerstukken] Downloading type=%s (area=%s, max=%d)",
        type_key, sru_area, max_records,
    )

    while fetched < max_records:
        batch_file = output_dir / f"{type_key}_batch_{start:08d}.xml"
        if batch_file.exists():
            fetched += page_size
            start += page_size
            continue

        url = _sru_url(sru_area, start, page_size)
        try:
            data = _fetch(url)
        except Exception as exc:
            logger.warning(
                "[kamerstukken] Request failed for %s at start=%d: %s",
                type_key, start, exc,
            )
            break

        total = _total_records(data)
        if total == 0 and start == 1:
            logger.error(
                "[kamerstukken] No records found for type=%s (area=%s).",
                type_key, sru_area,
            )
            break

        batch_file.write_bytes(data)
        _log(output_dir, url)
        logger.info(
            "[kamerstukken] Saved %s (total available: %d)",
            batch_file.name, total,
        )

        fetched += page_size
        start += page_size

        if start > min(total, max_records):
            logger.info(
                "[kamerstukken] Reached end for type=%s at %d records.",
                type_key, fetched,
            )
            break

        time.sleep(0.5)

    logger.info("[kamerstukken] type=%s done. %d records fetched.", type_key, fetched)


def download(
    output_dir: Path,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    """Download all Kamerstukken document types via overheid.nl SRU."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[kamerstukken] [dry-run] skipping")
        return

    for type_key, (sru_area, max_records) in _TYPES.items():
        _download_type(
            output_dir=output_dir,
            type_key=type_key,
            sru_area=sru_area,
            max_records=max_records,
            page_size=page_size,
        )

    logger.info("[kamerstukken] All types downloaded.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Dutch parliamentary documents via SRU (public domain)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        page_size=args.page_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
