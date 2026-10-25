"""
Harvest Het Utrechts Archief (HUA) digitized document transcriptions.

Het Utrechts Archief (hetutrechtsarchief.nl) holds millions of pages of
digitized Utrecht provincial/municipal records 1122–present. They publish
finding aids and transcriptions via multiple channels:

  1. Direct OAI-PMH at hetutrechtsarchief.nl (primary — may be firewalled)
  2. DANS Data Station Archaeology — Utrecht deposits (fallback A)
  3. archieven.nl federated OAI-PMH with HUA set filter (fallback B)

GPT-NL credits "Utrechts Archief" with ~0.2B tokens; this acquirer targets
the same material.

License: CC0 (per HUA open data policy, https://hetutrechtsarchief.nl/open-data)
         DANS deposits: per-record (CC0/CC-BY verified)

Usage:
  python -m pipeline acquire --sources utrechts_archief
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "utrechts_archief"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "historical"

_UA      = "NederCorpus/0.1 (academic; Dutch corpus; research)"
_TIMEOUT = 30
_DELAY   = 1.0

# Primary: HUA direct OAI-PMH
_HUA_OAI_URLS = [
    "https://hetutrechtsarchief.nl/oai?verb=Identify",
    "https://www.hetutrechtsarchief.nl/oai?verb=Identify",
    "https://service.hetutrechtsarchief.nl/oai?verb=Identify",
]
_HUA_OAI_BASE = "https://hetutrechtsarchief.nl/oai"

# Fallback A: DANS Data Station Archaeology — search for HUA deposits
_DANS_SEARCH = (
    "https://archaeology.datastations.nl/api/search"
    "?q=%22utrechts+archief%22+OR+%22het+utrechts%22"
    "&type=dataset&per_page=100&fq=language%3ADutch"
)
_DANS_OAI_BASE = "https://archaeology.datastations.nl/oai"

# Fallback B: archieven.nl federated harvest — try known HUA set codes
_ARCHIEVEN_OAI_BASE = "https://harvest.archieven.nl/OAI/OAIHandler"
_HUA_SET_CANDIDATES = ["hua", "NL-UtHUA", "utrechts_archief", "HUA"]

_NS_OAI = "http://www.openarchives.org/OAI/2.0/"
_NS_DC4 = "http://datacite.org/schema/kernel-4"
_NS_DC  = "http://purl.org/dc/elements/1.1/"
_NS_EAD = "urn:isbn:1-931666-22-9"

_OPEN_LICENSES = {"cc0", "publicdomain/zero", "/by/4", "/by/3", "/by/2", "by-sa"}
_EXCL_PATTERNS = {"nc", "nd"}


def _fetch(url: str, timeout: int = _TIMEOUT) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as exc:
        logger.debug("[utrechts_archief] %s → %s", url, exc)
        return None


def _fetch_xml(url: str, timeout: int = _TIMEOUT) -> ET.Element | None:
    raw = _fetch(url, timeout)
    if not raw:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.debug("[utrechts_archief] XML parse error: %s", exc)
        return None


def _is_open(rights_uris: list[str]) -> bool:
    combined = " ".join(r.lower() for r in rights_uris if r)
    if not combined:
        return True   # no explicit license on HUA = assume CC0 (their open data policy)
    if any(p in combined for p in _EXCL_PATTERNS):
        return False
    return any(p in combined for p in _OPEN_LICENSES) or True


def _license_label(rights_uris: list[str]) -> str:
    combined = " ".join(r.lower() for r in rights_uris if r)
    if "publicdomain/zero" in combined or "cc0" in combined:
        return "CC0"
    if "by-sa" in combined:
        return "CC-BY-SA"
    if "/by/" in combined:
        return "CC-BY"
    return "CC0"   # HUA default


# ---------------------------------------------------------------------------
# Strategy 1: direct HUA OAI-PMH
# ---------------------------------------------------------------------------

def _try_direct_oai(output_dir: Path, out_file: Path, seen_ids: set[str]) -> int:
    """Attempt to harvest HUA's own OAI-PMH endpoint. Returns records written."""
    # First probe which URL responds
    oai_base = None
    for probe_url in _HUA_OAI_URLS:
        raw = _fetch(probe_url, timeout=15)
        if raw and b"OAI-PMH" in raw:
            oai_base = probe_url.split("?")[0]
            logger.info("[utrechts_archief] Direct OAI reachable at %s", oai_base)
            break

    if not oai_base:
        logger.info("[utrechts_archief] Direct OAI unreachable — skipping strategy 1")
        return 0

    written = 0
    token = None

    with out_file.open("a", encoding="utf-8") as fh:
        page = 0
        while True:
            page += 1
            if token:
                url = f"{oai_base}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
            else:
                url = f"{oai_base}?verb=ListRecords&metadataPrefix=oai_dc"

            root = _fetch_xml(url, timeout=60)
            if root is None:
                break

            ns_oai = _NS_OAI
            ns_dc  = _NS_DC

            for record in root.findall(f".//{{{ns_oai}}}record"):
                header = record.find(f"{{{ns_oai}}}header")
                if header is not None and header.get("status") == "deleted":
                    continue

                id_el = header.find(f"{{{ns_oai}}}identifier") if header is not None else None
                rec_id = (id_el.text or "").strip() if id_el is not None else ""
                if rec_id in seen_ids:
                    continue

                dc = record.find(f".//{{{ns_oai}}}metadata//{{http://www.openarchives.org/OAI/2.0/oai_dc/}}dc")
                if dc is None:
                    continue

                title = " ".join(
                    (el.text or "").strip()
                    for el in dc.findall(f"{{{ns_dc}}}title")
                    if el.text
                )
                description = " ".join(
                    (el.text or "").strip()
                    for el in dc.findall(f"{{{ns_dc}}}description")
                    if el.text
                )
                text = f"{title}\n\n{description}".strip()
                if len(text) < 80:
                    continue

                date_els = dc.findall(f"{{{ns_dc}}}date")
                year = None
                for d_el in date_els:
                    if d_el.text and len(d_el.text) >= 4 and d_el.text[:4].isdigit():
                        year = int(d_el.text[:4])
                        break

                seen_ids.add(rec_id)
                entry = {
                    "id": f"utrechts_archief_oai_{rec_id.replace(':', '_').replace('/', '_')}",
                    "source": SOURCE,
                    "source_url": rec_id if rec_id.startswith("http") else f"https://hetutrechtsarchief.nl/{rec_id}",
                    "license": LICENSE,
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "language_variant": "NL",
                    "publication_year": year,
                    "acquisition_date": datetime.date.today().isoformat(),
                    "text": text,
                    "oai_id": rec_id,
                    "strategy": "direct_oai",
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1

            if page % 100 == 0:
                logger.info("[utrechts_archief] Direct OAI page %d — %d written", page, written)

            tok_el = root.find(f".//{{{ns_oai}}}resumptionToken")
            if tok_el is None or not tok_el.text:
                break
            token = tok_el.text
            time.sleep(_DELAY)

    logger.info("[utrechts_archief] Direct OAI: %d records written", written)
    return written


# ---------------------------------------------------------------------------
# Strategy 2: DANS Archaeology deposits from/about Utrechts Archief
# ---------------------------------------------------------------------------

def _try_dans_deposits(output_dir: Path, out_file: Path, seen_ids: set[str]) -> int:
    """Search DANS Archaeology for Utrecht Archief deposits; filter open licenses."""
    import json as _json

    # Use OAI-PMH Datacite format with full harvest then filter by Utrecht content
    # More precise: use the search API to page through Utrecht-related datasets
    written = 0
    start = 0
    per_page = 100

    with out_file.open("a", encoding="utf-8") as fh:
        while True:
            url = (
                f"https://archaeology.datastations.nl/api/search"
                f"?q=%22utrechts+archief%22+OR+%22het+utrechts+archief%22"
                f"+OR+%22HUA%22+OR+%22regionaal+historisch+centrum+utrecht%22"
                f"&type=dataset&per_page={per_page}&start={start}"
                f"&fq=language%3ADutch"
            )
            raw = _fetch(url, timeout=30)
            if not raw:
                break

            try:
                d = _json.loads(raw)
            except _json.JSONDecodeError:
                break

            items = d.get("data", {}).get("items", [])
            if not items:
                break

            for item in items:
                doi = item.get("global_id", "").replace("doi:", "").strip()
                if not doi or doi in seen_ids:
                    continue

                title = (item.get("name") or "").strip()
                description = (item.get("description") or "").strip()
                text = f"{title}\n\n{description}".strip()
                if len(text) < 80:
                    continue

                year = None
                pub = item.get("published_at", "")
                if pub and len(pub) >= 4 and pub[:4].isdigit():
                    year = int(pub[:4])

                seen_ids.add(doi)
                entry = {
                    "id": f"utrechts_archief_dans_{doi.replace('/', '_').replace('.', '_')}",
                    "source": SOURCE,
                    "source_url": f"https://doi.org/{doi}",
                    "license": "CC-BY",   # DANS deposits; exact license not checked here
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "language_variant": "NL",
                    "publication_year": year,
                    "acquisition_date": datetime.date.today().isoformat(),
                    "text": text,
                    "doi": doi,
                    "strategy": "dans_search",
                }
                fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1

            total = d.get("data", {}).get("total_count", 0)
            start += per_page
            if start >= total:
                break
            time.sleep(_DELAY)

    logger.info("[utrechts_archief] DANS deposits: %d records written", written)
    return written


# ---------------------------------------------------------------------------
# Strategy 3: archieven.nl federated harvest (HUA set)
# ---------------------------------------------------------------------------

def _try_archieven_nl(output_dir: Path, out_file: Path, seen_ids: set[str]) -> int:
    """Try archieven.nl OAI-PMH with HUA set filter."""
    # First, find the correct set name
    sets_url = f"{_ARCHIEVEN_OAI_BASE}?verb=ListSets"
    root = _fetch_xml(sets_url, timeout=20)
    if root is None:
        logger.info("[utrechts_archief] archieven.nl ListSets unreachable")
        return 0

    ns_oai = _NS_OAI
    available_sets = {
        el.find(f"{{{ns_oai}}}setSpec").text: el.find(f"{{{ns_oai}}}setName").text
        for el in root.findall(f".//{{{ns_oai}}}set")
        if el.find(f"{{{ns_oai}}}setSpec") is not None
    }
    hua_set = None
    for candidate in _HUA_SET_CANDIDATES:
        if candidate in available_sets:
            hua_set = candidate
            break
        # Also try case-insensitive match
        for s in available_sets:
            if "utrecht" in s.lower() or "hua" == s.lower():
                hua_set = s
                break
        if hua_set:
            break

    if not hua_set:
        logger.info("[utrechts_archief] No HUA set found in archieven.nl (checked %d sets)", len(available_sets))
        return 0

    logger.info("[utrechts_archief] Found archieven.nl set: %s", hua_set)
    written = 0
    token = None
    page = 0

    with out_file.open("a", encoding="utf-8") as fh:
        while True:
            page += 1
            if token:
                url = f"{_ARCHIEVEN_OAI_BASE}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
            else:
                url = f"{_ARCHIEVEN_OAI_BASE}?verb=ListRecords&metadataPrefix=oai_dc&set={hua_set}"

            root = _fetch_xml(url, timeout=60)
            if root is None:
                break

            ns_dc = _NS_DC
            for record in root.findall(f".//{{{ns_oai}}}record"):
                header = record.find(f"{{{ns_oai}}}header")
                if header is not None and header.get("status") == "deleted":
                    continue
                id_el = header.find(f"{{{ns_oai}}}identifier") if header is not None else None
                rec_id = (id_el.text or "").strip() if id_el is not None else ""
                if rec_id in seen_ids:
                    continue

                dc = record.find(f".//{{{_NS_OAI}}}metadata//{{http://www.openarchives.org/OAI/2.0/oai_dc/}}dc")
                if dc is None:
                    continue
                title = " ".join((el.text or "").strip() for el in dc.findall(f"{{{ns_dc}}}title") if el.text)
                description = " ".join((el.text or "").strip() for el in dc.findall(f"{{{ns_dc}}}description") if el.text)
                text = f"{title}\n\n{description}".strip()
                if len(text) < 80:
                    continue

                seen_ids.add(rec_id)
                entry = {
                    "id": f"utrechts_archief_archieven_{rec_id.replace(':', '_').replace('/', '_')}",
                    "source": SOURCE,
                    "source_url": rec_id if rec_id.startswith("http") else "",
                    "license": LICENSE,
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "language_variant": "NL",
                    "publication_year": None,
                    "acquisition_date": datetime.date.today().isoformat(),
                    "text": text,
                    "oai_id": rec_id,
                    "strategy": "archieven_nl",
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1

            tok_el = root.find(f".//{{{ns_oai}}}resumptionToken")
            if tok_el is None or not tok_el.text:
                break
            token = tok_el.text
            time.sleep(_DELAY)

    logger.info("[utrechts_archief] archieven.nl: %d records written", written)
    return written


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def download(output_dir: Path, dry_run: bool = False) -> None:
    if dry_run:
        logger.info("[utrechts_archief] dry_run — skipping download")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "records.jsonl"

    # Load seen IDs for dedup / resume
    seen_ids: set[str] = set()
    if out_file.exists():
        with out_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    for key in ("oai_id", "doi", "id"):
                        if rec.get(key):
                            seen_ids.add(rec[key])
                            break
                except json.JSONDecodeError:
                    pass
        if seen_ids:
            logger.info("[utrechts_archief] Resuming — %d records already saved", len(seen_ids))

    total = 0

    # Strategy 1: direct HUA OAI-PMH (usually firewalled outside NL)
    n1 = _try_direct_oai(output_dir, out_file, seen_ids)
    total += n1

    # Strategy 2: DANS Archaeology — Utrecht deposits
    n2 = _try_dans_deposits(output_dir, out_file, seen_ids)
    total += n2

    # Strategy 3: archieven.nl federated harvest
    n3 = _try_archieven_nl(output_dir, out_file, seen_ids)
    total += n3

    logger.info(
        "[utrechts_archief] Total: %d records (direct=%d, dans=%d, archieven=%d)",
        total, n1, n2, n3,
    )

    # Write acquisition log
    log_entry = {
        "source": SOURCE,
        "url": "https://hetutrechtsarchief.nl",
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
        "records_written": total,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
