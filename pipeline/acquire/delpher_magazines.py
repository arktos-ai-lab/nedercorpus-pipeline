"""
Download pre-1880 Dutch magazines from Delpher (KB National Library).

This is a SEPARATE source from `delpher` (newspapers).  The KB has a distinct
magazines collection at Delpher covering periodicals 1816–1900+, with the
pre-1880 titles explicitly placed under CC0 / Public Domain.

OAI-PMH: https://services.kb.nl/mdo/oai  (same host as newspapers)
Set:      TIJDSCHRIFTEN  (parent set; KB sub-sets harvested only)
Metadata: dcx format (oai_dc/oai is not supported; dc returns noRecordsMatch)

Only records with publication year ≤ 1880 are downloaded (pre-1880 = PD/CC0).
Only KB-owned records (URN prefix MMKB/KB*) are harvested — other institutions
require an API key.

OCR text is fetched via resolver.kb.nl using the pattern:
  urn={record_urn}:00001:ocr (5-digit zero-padded page number)

License: CC0 / Public Domain
  KB explicitly places pre-copyright digitised magazines under CC0.
  Source: https://www.kb.nl/en/research-find/datasets/delpher-magazines

Usage:
  python -m pipeline acquire --sources delpher_magazines
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE      = "delpher_magazines"
LICENSE     = "PD"
COUNTRY     = "NL"
DOMAIN      = "news"
MAX_YEAR    = 1880     # only harvest pre-1880 (PD / CC0)
MAX_RECORDS = 500_000  # safety cap
MAX_PAGES   = 5        # OCR pages per issue to fetch

_OAI_BASE   = "https://services.kb.nl/mdo/oai"
_RESOLVER   = "http://resolver.kb.nl/resolve"
_USER_AGENT = "NederCorpus/0.1 (academic research; Dutch historical corpus)"
_MAX_RETRIES = 3

# KB-owned magazine sub-sets of TIJDSCHRIFTEN (freely accessible without API key)
# Using sub-sets directly avoids wading through ~170k non-KB records in the parent set.
_KB_SETS = [
    "TIJDSCHRIFTEN:KBDBNL02",
    "TIJDSCHRIFTEN:KBDBVL02",
    "TIJDSCHRIFTEN:KBDC001",
    "TIJDSCHRIFTEN:KBKWG02",
    "TIJDSCHRIFTEN:KBNA001",
    "TIJDSCHRIFTEN:KBVRNL01",
    "TIJDSCHRIFTEN:MMKB07",
    "TIJDSCHRIFTEN:MMKB10",
    "TIJDSCHRIFTEN:MMKB13",
    "TIJDSCHRIFTEN:MMKB14",
    "TIJDSCHRIFTEN:MMKB16",
    "TIJDSCHRIFTEN:MMKB26",
    "TIJDSCHRIFTEN:MMKB30",
    "TIJDSCHRIFTEN:MMKB34",
    "TIJDSCHRIFTEN:MMKB37",
]
_OAI_FORMAT = "dcx"

# KB-owned record URN prefixes (freely accessible without API key)
_KB_URN_PREFIXES = ("MMKB", "KBDBNL", "KBDBVL", "KBDC", "KBKWG", "KBNA", "KBVRNL")

# DCX namespace for KB-specific elements
_TEL_NS = "http://krait.kb.nl/coop/tel/handbook/telterms.html"

_YEAR_RE = re.compile(r"(\d{4})")


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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410, 204):
                raise
            if exc.code in (429, 503):
                wait = 60.0 * (attempt + 1)
                logger.warning("[delpher_magazines] HTTP %d — wait %.0fs", exc.code, wait)
                time.sleep(wait)
            elif attempt == _MAX_RETRIES - 1:
                raise
            else:
                time.sleep(10.0 * (attempt + 1))
        except Exception:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(10.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _oai_list_records(oai_set: str, resumption_token: str | None = None) -> bytes:
    if resumption_token:
        params = {"verb": "ListRecords", "resumptionToken": resumption_token}
    else:
        params = {"verb": "ListRecords", "metadataPrefix": _OAI_FORMAT, "set": oai_set}
    url = _OAI_BASE + "?" + urllib.parse.urlencode(params)
    return _fetch(url, timeout=90)


def _parse_year(s: str) -> int | None:
    m = _YEAR_RE.search(s)
    return int(m.group(1)) if m else None


def _parse_dcx_record(record) -> dict | None:
    """
    Parse a dcx-format OAI record.
    Returns a dict with identifier, record_urn, title, year, n_pages,
    or None if the record should be skipped.
    """
    # Skip deleted records
    header = record.find("{http://www.openarchives.org/OAI/2.0/}header")
    if header is not None and header.get("status") == "deleted":
        return None

    # Check for API-key requirement in the about element
    about_el = record.find("{http://www.openarchives.org/OAI/2.0/}about")
    if about_el is not None:
        about_text = ET.tostring(about_el, encoding="unicode")
        if "API-key" in about_text or "api-key" in about_text.lower():
            return None

    # OAI identifier
    id_el = record.find(".//{http://www.openarchives.org/OAI/2.0/}identifier")
    identifier = (id_el.text.strip() if id_el is not None and id_el.text else "")

    # KB recordIdentifier (e.g. "MMKB07:000653004")
    rec_id_els = record.findall(f".//{{{_TEL_NS}}}recordIdentifier")
    record_urn = ""
    for el in rec_id_els:
        if el.text and ":" in el.text:
            candidate = el.text.strip()
            if any(candidate.startswith(p) for p in _KB_URN_PREFIXES):
                record_urn = candidate
                break

    # Only process KB-owned records (others require API key)
    if not record_urn:
        return None

    # Title (dc element — no namespace in dcx)
    title = ""
    for tag in ("title", "{http://purl.org/dc/elements/1.1/}title"):
        t = record.find(f".//{tag}")
        if t is not None and t.text:
            title = t.text.strip()
            break

    # Year: prefer publicationdateYear, fall back to issued date
    year = None
    py_el = record.find(f".//{{{_TEL_NS}}}publicationdateYear")
    if py_el is not None and py_el.text:
        try:
            year = int(py_el.text.strip())
        except ValueError:
            pass
    if year is None:
        for tag in ("issued", "{http://purl.org/dc/elements/1.1/}date"):
            d = record.find(f".//{tag}")
            if d is not None and d.text:
                year = _parse_year(d.text.strip())
                break

    # Number of pages (extent element)
    n_pages = 0
    ext_el = record.find(f".//{{{_TEL_NS}}}extent")
    if ext_el is not None and ext_el.text:
        try:
            n_pages = int(ext_el.text.strip())
        except ValueError:
            pass

    return {
        "identifier": identifier,
        "record_urn": record_urn,
        "title": title,
        "year": year,
        "n_pages": n_pages,
    }


def _fetch_ocr_text(record_urn: str, n_pages: int) -> str:
    """
    Fetch OCR plain text for a magazine issue via resolver.kb.nl.
    Uses :00001:ocr, :00002:ocr, ... pattern (5-digit zero-padded).
    Fetches up to MAX_PAGES pages or n_pages (whichever is less).
    Returns joined text or empty string.
    """
    texts = []
    limit = min(n_pages, MAX_PAGES) if n_pages > 0 else MAX_PAGES

    for page in range(1, limit + 1):
        url = f"{_RESOLVER}?urn={record_urn}:{page:05d}:ocr"
        try:
            data = _fetch(url, timeout=30)
            root = ET.fromstring(data)
            paragraphs = [
                el.text.strip()
                for el in root.iter("p")
                if el.text and el.text.strip()
            ]
            page_text = " ".join(paragraphs)
            if page_text:
                texts.append(page_text)
        except urllib.error.HTTPError:
            break  # No more pages
        except Exception as exc:
            logger.debug("[delpher_magazines] OCR page %d for %s: %s", page, record_urn, exc)
            break

    return "\n\n".join(texts)


def _harvest_set(oai_set: str, seen: set[str], out_file: Path,
                 progress_dir: Path, max_records: int) -> tuple[int, int]:
    """Harvest one KB magazine sub-set. Returns (written, skipped)."""
    set_key = oai_set.replace(":", "_")
    token_file = progress_dir / f"token_{set_key}.txt"

    resumption_token: str | None = None
    if token_file.exists():
        resumption_token = token_file.read_text(encoding="utf-8").strip() or None
        if resumption_token:
            logger.info("[delpher_magazines] %s: resuming from token", oai_set)

    written = 0
    skipped = 0

    while True:
        try:
            data = _oai_list_records(oai_set, resumption_token)
        except Exception as exc:
            logger.error("[delpher_magazines] %s OAI request failed: %s", oai_set, exc)
            break

        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            logger.error("[delpher_magazines] %s XML parse error: %s", oai_set, exc)
            break

        error_el = root.find(".//{http://www.openarchives.org/OAI/2.0/}error")
        if error_el is not None:
            code = error_el.get("code", "")
            if code == "noRecordsMatch":
                logger.info("[delpher_magazines] %s: no records", oai_set)
            else:
                logger.error("[delpher_magazines] %s OAI error %s: %s", oai_set, code, error_el.text)
            break

        records = root.findall(".//{http://www.openarchives.org/OAI/2.0/}record")
        logger.info("[delpher_magazines] %s: batch %d records", oai_set, len(records))

        for record in records:
            if written + skipped >= max_records:
                break

            parsed = _parse_dcx_record(record)
            if parsed is None:
                skipped += 1
                continue

            identifier = parsed["identifier"]
            if identifier in seen:
                skipped += 1
                continue

            year = parsed["year"]
            if year is None or year > MAX_YEAR:
                skipped += 1
                seen.add(identifier)
                continue

            record_urn = parsed["record_urn"]
            alto_text = _fetch_ocr_text(record_urn, parsed["n_pages"])
            time.sleep(0.3)

            if not alto_text and not parsed["title"]:
                skipped += 1
                seen.add(identifier)
                continue

            rec = {
                "identifier": identifier,
                "record_urn": record_urn,
                "title": parsed["title"],
                "year": year,
                "n_pages": parsed["n_pages"],
                "text": alto_text or parsed["title"],
            }
            seen.add(identifier)
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _log(out_file.parent, f"{_RESOLVER}?urn={record_urn}")
            written += 1

            if written % 100 == 0:
                logger.info("[delpher_magazines] %s: %d written, %d skipped", oai_set, written, skipped)

        if written + skipped >= max_records:
            break

        token_el = root.find(".//{http://www.openarchives.org/OAI/2.0/}resumptionToken")
        if token_el is not None and token_el.text and token_el.text.strip():
            resumption_token = token_el.text.strip()
            token_file.write_text(resumption_token, encoding="utf-8")
            time.sleep(2.0)
        else:
            token_file.write_text("", encoding="utf-8")
            logger.info("[delpher_magazines] %s: enumeration complete", oai_set)
            break

    return written, skipped


def download(output_dir: Path, max_records: int = MAX_RECORDS, **_kwargs) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "delpher_magazines_records.jsonl"

    # Track completed sub-sets
    done_file = output_dir / "delpher_magazines_sets_done.txt"
    done_sets: set[str] = set()
    if done_file.exists():
        for line in done_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                done_sets.add(line)

    # Load seen identifiers for dedup
    seen: set[str] = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                seen.add(json.loads(line).get("identifier", ""))
            except json.JSONDecodeError:
                pass
    logger.info("[delpher_magazines] %d records already collected, %d sets done",
                len(seen), len(done_sets))

    total_written = 0
    total_skipped = 0

    for oai_set in _KB_SETS:
        if oai_set in done_sets:
            logger.info("[delpher_magazines] %s: already done — skipping", oai_set)
            continue

        logger.info("[delpher_magazines] Processing set: %s", oai_set)
        written, skipped = _harvest_set(oai_set, seen, out_file, output_dir, max_records)
        total_written += written
        total_skipped += skipped
        logger.info("[delpher_magazines] %s: done (+%d written)", oai_set, written)

        # Mark set as complete
        with done_file.open("a", encoding="utf-8") as f:
            f.write(f"{oai_set}\n")
        done_sets.add(oai_set)

    logger.info(
        "[delpher_magazines] Done. Written=%d  Skipped=%d",
        total_written, total_skipped,
    )
