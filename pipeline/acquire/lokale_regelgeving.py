"""
Download Dutch local, provincial, and waterboard regulations via the
CVDR SRU API (Centrale Voorziening Decentrale Regelgeving).

Covers:
  - 334,000+ regulations from 342 municipalities, 12 provinces,
    21 water boards, and other decentralised public bodies.

License: Public Domain (Art. 11 Auteurswet — government texts have no copyright)
  Each record explicitly carries: "De tekst in dit document is vrij van
  auteursrecht en databankrecht."

Correct SRU endpoint (confirmed 2026-03-24):
  https://zoekservice.overheid.nl/sru/Search
  x-connection=CVDR, SRU version 1.2

  Previous endpoint (https://repository.overheid.nl/sru with
  query "dt.identifier any cvdr") returns 0 results — retired.

Two-phase acquisition:
  Phase 1: SRU pagination → collect identifier + metadata + publicatieurl_xml
  Phase 2: Fetch each regulation's XML from publicatieurl_xml → extract body text

The regulation XML (<body><artikel><al>…</al>) contains the full regulation text.
Metadata records with no publicatieurl_xml are saved title-only.

Usage:
  python -m pipeline acquire --sources lokale_regelgeving
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

logger = logging.getLogger(__name__)

SOURCE   = "lokale_regelgeving"
LICENSE  = "PD"
COUNTRY  = "NL"
DOMAIN   = "legal"

# Correct CVDR endpoint (zoekservice, not repository)
_SRU_BASE  = "https://zoekservice.overheid.nl/sru/Search"
_SRU_QUERY = "dcterms.modified >= 1900-01-01"   # matches all 334K records
_PAGE_SIZE = 100
_MAX_RETRIES = 3
_USER_AGENT  = "NederCorpus/0.1 (academic research; open government data)"

# CVDR regulation XML namespace
_CVDR_NS = "http://standaarden.overheid.nl/cvdr/terms/"

_ssl_ctx = ssl.create_default_context()


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = 5.0 * (attempt + 1)
            logger.debug("[lokale_regelgeving] Retry %d for %s: %s", attempt + 1, url, exc)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _sru_url(start: int) -> str:
    params = {
        "version":        "1.2",
        "operation":      "searchRetrieve",
        "x-connection":   "CVDR",
        "query":          _SRU_QUERY,
        "startRecord":    start,
        "maximumRecords": _PAGE_SIZE,
    }
    return _SRU_BASE + "?" + urllib.parse.urlencode(params)


def _extract_regulation_text(xml_bytes: bytes) -> str:
    """Extract plain text from a CVDR regulation XML document."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""

    # Collect all <al> (paragraph) elements in document order
    # They live under <body>/<regeling>/<regeling-tekst>/<artikel>/<al>
    # and also under <aanhef>/<preambule>/<al>
    parts: list[str] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]  # strip namespace
        if tag == "al" and el.text and el.text.strip():
            parts.append(el.text.strip())
        # Also grab headings: <kop><titel>
        elif tag == "titel" and el.text and el.text.strip():
            parts.append(el.text.strip())
        elif tag == "intitule" and el.text and el.text.strip():
            parts.append(el.text.strip())

    return "\n".join(parts)


def _parse_sru_page(xml_bytes: bytes) -> tuple[list[dict], int]:
    """
    Parse one SRU response page.
    Returns (records_list, total_records).
    Each record dict has: identifier, title, creator, modified, xml_url.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return [], 0

    total_el = root.find(".//{*}numberOfRecords")
    total = int(total_el.text) if total_el is not None and total_el.text else 0

    records = []
    for rec in root.findall(".//{*}recordData"):
        identifier = ""
        title      = ""
        creator    = ""
        modified   = ""
        xml_url    = ""

        # From originalData/owmskern
        id_el  = rec.find(".//{http://purl.org/dc/terms/}identifier")
        ttl_el = rec.find(".//{http://purl.org/dc/terms/}title")
        crt_el = rec.find(".//{http://purl.org/dc/terms/}creator")
        mod_el = rec.find(".//{http://purl.org/dc/terms/}modified")
        # From enrichedData
        xu_el  = rec.find(".//{*}publicatieurl_xml")

        if id_el  is not None and id_el.text:  identifier = id_el.text.strip()
        if ttl_el is not None and ttl_el.text: title      = ttl_el.text.strip()
        if crt_el is not None and crt_el.text: creator    = crt_el.text.strip()
        if mod_el is not None and mod_el.text: modified   = mod_el.text.strip()
        if xu_el  is not None and xu_el.text:  xml_url    = xu_el.text.strip()

        if identifier:
            records.append({
                "identifier": identifier,
                "title":      title,
                "creator":    creator,
                "modified":   modified,
                "xml_url":    xml_url,
            })

    return records, total


def download(
    output_dir: Path,
    max_records: int = 400_000,
    fetch_text: bool = True,
    dry_run: bool = False,
) -> None:
    """
    Download all CVDR local regulation records.

    Args:
        max_records: Safety cap (default 400K — slightly above current 334K).
        fetch_text:  If True (default), fetch the full regulation XML for each
                     record to extract body text. If False, save metadata only.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[lokale_regelgeving] [dry-run] skipping")
        return

    out_file    = output_dir / "cvdr_records.jsonl"
    resume_file = output_dir / "cvdr_resume_start.txt"

    # Load already-seen identifiers
    seen: set[str] = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
                seen.add(rec.get("identifier", ""))
            except json.JSONDecodeError:
                pass

    # Resume position
    start = 1
    if resume_file.exists():
        try:
            start = int(resume_file.read_text().strip())
        except ValueError:
            pass

    logger.info("[lokale_regelgeving] Starting from record %d (%d already collected)", start, len(seen))

    total_written = 0
    total_records = None

    while start <= max_records:
        url = _sru_url(start)
        try:
            data = _fetch(url, timeout=90)
        except Exception as exc:
            logger.error("[lokale_regelgeving] SRU fetch failed at start=%d: %s", start, exc)
            break

        page_records, total_records = _parse_sru_page(data)

        if not page_records:
            logger.info("[lokale_regelgeving] No records in page at start=%d — done", start)
            break

        if total_records and start == 1:
            logger.info("[lokale_regelgeving] Total records in CVDR: %d", total_records)

        for meta in page_records:
            if meta["identifier"] in seen:
                continue

            text = ""
            if fetch_text and meta["xml_url"]:
                try:
                    xml_bytes = _fetch(meta["xml_url"], timeout=30)
                    text = _extract_regulation_text(xml_bytes)
                except Exception as exc:
                    logger.debug("[lokale_regelgeving] Text fetch failed for %s: %s",
                                 meta["identifier"], exc)

            rec = {
                "identifier": meta["identifier"],
                "title":      meta["title"],
                "creator":    meta["creator"],
                "modified":   meta["modified"],
                "xml_url":    meta["xml_url"],
                "text":       text or meta["title"],
            }

            seen.add(meta["identifier"])
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            total_written += 1

        resume_file.write_text(str(start + _PAGE_SIZE))
        _log(output_dir, url)

        if total_written % 1000 == 0 and total_written > 0:
            logger.info("[lokale_regelgeving] %d written so far", total_written)

        if total_records and start + _PAGE_SIZE > min(total_records, max_records):
            break

        start += _PAGE_SIZE
        time.sleep(0.3)

    resume_file.write_text("")
    logger.info("[lokale_regelgeving] Done. %d records written.", total_written)
