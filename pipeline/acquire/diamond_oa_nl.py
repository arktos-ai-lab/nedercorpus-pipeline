"""
Harvest Dutch Diamond Open Access journal articles via OAI-PMH.

Diamond OA journals are free for readers AND authors, funded by university
consortia. Several Dutch academic journals have flipped to Diamond OA and
are indexed via OAI-PMH endpoints.

Sources tried in order; journals that do not respond to Identify are skipped.

License: varies per journal (CC-BY, CC-BY-SA, CC0 — NC/ND excluded)
Country: NL
Domain: academic

Usage:
  python -m pipeline acquire --sources diamond_oa_nl
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "diamond_oa_nl"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "academic"

_USER_AGENT = "NederCorpus/0.1 (academic; Dutch Diamond OA corpus; contact: corpus@example.org)"
_DELAY = 1.0  # seconds between OAI page requests

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# OAI-PMH namespaces
_NS_OAI    = "http://www.openarchives.org/OAI/2.0/"
_NS_DC     = "http://purl.org/dc/elements/1.1/"
_NS_OAI_DC = "http://www.openarchives.org/OAI/2.0/oai_dc/"

# (name, oai_base_url, set_spec or None)
_JOURNALS: list[tuple[str, str, str | None]] = [
    ("krisis",        "https://krisis.eu/oai",                                   None),
    ("krisis_alt",    "https://www.krisis.eu/index.php/krisis/oai",              None),
    ("tvg",           "https://www.tijdschriftvoorgeschiedenis.nl/oai",           None),
    ("tvg_aup",       "https://www.aup-online.com/oai",                          "com_aup_online_1"),
    ("tva",           "https://www.tijdschriftvoorarbeidsvraagstukken.nl/oai",    None),
    ("genderstudies", "https://www.tijdschriftvoorgenderstudies.nl/oai",          None),
    ("verpleegkunde", "https://www.verpleegkundejournal.nl/oai",                  None),
    ("aup_journals",  "https://www.aup-online.com/oai",                          None),
    ("knaw_journals", "https://knaw.nl/oai",                                     None),
    ("krisis_oj",     "https://www.krisis.eu/oai",                               None),
]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    """Fetch URL, return bytes or None on failure."""
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            logger.warning(
                "[diamond_oa_nl] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1
            )
            if exc.code in (400, 403, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning(
                "[diamond_oa_nl] Error fetching %s: %s (attempt %d)", url, exc, attempt + 1
            )
            time.sleep(2.0 * (attempt + 1))
    return None


def _log(output_dir: Path, url: str, identifier: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "identifier":       identifier,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _oai_url(base: str, **params: str) -> str:
    return f"{base}?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# OAI-PMH helpers
# ---------------------------------------------------------------------------

def _oai_identify(base_url: str) -> bool:
    """Return True if the endpoint responds to Identify with valid OAI-PMH XML."""
    url = _oai_url(base_url, verb="Identify")
    data = _fetch(url, retries=2, timeout=30)
    if data is None:
        return False
    try:
        root = ET.fromstring(data)
        tag = root.tag
        # Accept either namespaced or plain tag
        return "OAI-PMH" in tag or tag.endswith("OAI-PMH")
    except ET.ParseError:
        return False


def _dc_values(metadata_el: ET.Element, tag: str) -> list[str]:
    """Extract all dc:tag text values from a metadata element."""
    values: list[str] = []
    for el in metadata_el.iter(f"{{{_NS_DC}}}{tag}"):
        if el.text and el.text.strip():
            values.append(el.text.strip())
    if not values:
        # Try without namespace
        for el in metadata_el.iter(tag):
            if el.text and el.text.strip():
                values.append(el.text.strip())
    return values


def _record_id(record_el: ET.Element) -> str:
    for path in (
        f"{{{_NS_OAI}}}header/{{{_NS_OAI}}}identifier",
        "header/identifier",
    ):
        el = record_el.find(path)
        if el is not None and el.text:
            return el.text.strip()
    return ""


def _is_deleted(record_el: ET.Element) -> bool:
    header = record_el.find(f"{{{_NS_OAI}}}header") or record_el.find("header")
    return header is not None and header.get("status") == "deleted"


def _is_dutch_language(languages: list[str]) -> bool:
    if not languages:
        return True  # empty language field — accept
    for lang in languages:
        l = lang.lower().strip()
        if l in ("nl", "nld", "dut", "dutch", "nederlands") or l.startswith("nl-"):
            return True
    return False


def _is_allowed_license(rights_list: list[str]) -> bool:
    """Accept CC-BY, CC-BY-SA, CC0. Reject NC, ND, all-rights-reserved."""
    if not rights_list:
        return True  # unknown licence — accept tentatively
    for r in rights_list:
        rl = r.lower()
        if "nc" in rl or "nd" in rl or "noncommercial" in rl or "noderivat" in rl:
            return False
        if (
            "creativecommons" in rl
            or "creative commons" in rl
            or re.search(r'\bcc[\s\-]', rl)
            or "cc0" in rl
            or "public domain" in rl
        ):
            return True
    return False


def _safe_id(record_id: str) -> str:
    safe = re.sub(r'^[a-zA-Z]+://', '', record_id)
    safe = re.sub(r'[/:\\?*|"<>]', '_', safe)
    return safe[:120]


def _find_pdf_url(identifiers: list[str]) -> str | None:
    for ident in identifiers:
        if ident.startswith("http") and ".pdf" in ident.lower():
            return ident
    return None


# ---------------------------------------------------------------------------
# Per-journal harvest
# ---------------------------------------------------------------------------

def _harvest_journal(
    journal_name: str,
    base_url: str,
    set_spec: str | None,
    output_dir: Path,
    pdfs_dir: Path,
    seen_ids: set[str],
    state: dict,
    metadata_fout,
) -> int:
    """
    Harvest one journal via OAI-PMH ListRecords.

    Returns the number of new records saved.
    """
    logger.info("[diamond_oa_nl] Harvesting journal: %s (%s)", journal_name, base_url)

    # Build initial URL
    params: dict[str, str] = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
    if set_spec:
        params["set"] = set_spec

    # Resume from saved token
    saved_token = state.get(journal_name, {}).get("resumption_token")
    if saved_token:
        url = _oai_url(base_url, verb="ListRecords", resumptionToken=saved_token)
        logger.info("[diamond_oa_nl] Resuming %s from token", journal_name)
    else:
        url = _oai_url(base_url, **params)

    page = 0
    new_count = 0
    acq_date = datetime.date.today().isoformat()

    while url:
        page += 1
        logger.debug("[diamond_oa_nl] %s page %d: %s", journal_name, page, url[:120])

        data = _fetch(url)
        if data is None:
            logger.warning("[diamond_oa_nl] Fetch failed for %s page %d", journal_name, page)
            break

        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            logger.warning("[diamond_oa_nl] XML parse error for %s: %s", journal_name, exc)
            break

        # Check for OAI error
        err_el = root.find(f".//{{{_NS_OAI}}}error") or root.find(".//error")
        if err_el is not None:
            code = err_el.get("code", "")
            msg  = err_el.text or ""
            if code == "noRecordsMatch":
                logger.info("[diamond_oa_nl] %s: noRecordsMatch", journal_name)
            else:
                logger.warning("[diamond_oa_nl] OAI error for %s: %s — %s", journal_name, code, msg)
            break

        list_records = (
            root.find(f"{{{_NS_OAI}}}ListRecords")
            or root.find("ListRecords")
        )
        if list_records is None:
            logger.warning("[diamond_oa_nl] No ListRecords element for %s", journal_name)
            break

        for rec_el in list_records.findall(f"{{{_NS_OAI}}}record"):
            if _is_deleted(rec_el):
                continue
            rec_id = _record_id(rec_el)
            if not rec_id or rec_id in seen_ids:
                continue

            metadata_el = rec_el.find(f"{{{_NS_OAI}}}metadata") or rec_el.find("metadata")
            if metadata_el is None:
                continue

            languages  = _dc_values(metadata_el, "language")
            rights     = _dc_values(metadata_el, "rights")

            if not _is_dutch_language(languages):
                continue
            if not _is_allowed_license(rights):
                continue

            titles      = _dc_values(metadata_el, "title")
            creators    = _dc_values(metadata_el, "creator")
            descriptions = _dc_values(metadata_el, "description")
            identifiers = _dc_values(metadata_el, "identifier")
            dates       = _dc_values(metadata_el, "date")

            source_url = next(
                (i for i in identifiers if i.startswith("http") and ".pdf" not in i.lower()),
                next((i for i in identifiers if i.startswith("http")), ""),
            )

            record_obj = {
                "record_id":        rec_id,
                "journal_name":     journal_name,
                "titles":           titles,
                "creators":         creators,
                "descriptions":     descriptions,
                "identifiers":      identifiers,
                "languages":        languages,
                "rights":           rights,
                "dates":            dates,
                "source_url":       source_url,
                "acquisition_date": acq_date,
            }
            metadata_fout.write(json.dumps(record_obj, ensure_ascii=False) + "\n")
            seen_ids.add(rec_id)
            new_count += 1

            # Download PDF if available
            pdf_url = _find_pdf_url(identifiers)
            if pdf_url:
                safe = _safe_id(rec_id)
                pdf_dest = pdfs_dir / f"{safe}.pdf"
                if not pdf_dest.exists():
                    pdf_data = _fetch(pdf_url, timeout=120)
                    if pdf_data:
                        pdf_dest.write_bytes(pdf_data)
                        _log(output_dir, pdf_url, rec_id)
                    time.sleep(_DELAY)

        # Resumption token
        token_el = (
            list_records.find(f"{{{_NS_OAI}}}resumptionToken")
            or list_records.find("resumptionToken")
        )
        if token_el is not None and token_el.text and token_el.text.strip():
            token = token_el.text.strip()
            state.setdefault(journal_name, {})["resumption_token"] = token
            url = _oai_url(base_url, verb="ListRecords", resumptionToken=token)
            time.sleep(_DELAY)
        else:
            # Done with this journal
            state.setdefault(journal_name, {})["resumption_token"] = None
            state[journal_name]["done"] = True
            url = ""

    logger.info("[diamond_oa_nl] %s: +%d new records", journal_name, new_count)
    return new_count


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download(output_dir: Path, dry_run: bool = False, **kwargs) -> None:
    """
    Harvest Dutch Diamond OA journals via OAI-PMH.

    Saves:
      diamond_oa_metadata.jsonl   — one JSON object per accepted record
      pdfs/{safe_id}.pdf          — PDFs where downloadable
      harvest_state.json          — per-journal resumption state
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir = output_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)

    metadata_path = output_dir / "diamond_oa_metadata.jsonl"
    state_path    = output_dir / "harvest_state.json"
    acq_date      = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[diamond_oa_nl] [dry-run] would harvest %d OAI-PMH journals", len(_JOURNALS))
        return

    # Load harvest state
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

    # Load already-seen record IDs
    seen_ids: set[str] = set()
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen_ids.add(json.loads(line)["record_id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_ids:
            logger.info("[diamond_oa_nl] Resuming — %d records already saved", len(seen_ids))

    total_new = 0
    visited_urls: set[str] = set()

    with metadata_path.open("a", encoding="utf-8") as fout:
        for journal_name, base_url, set_spec in _JOURNALS:
            # Deduplicate identical base_url+set_spec combos
            key = f"{base_url}|{set_spec or ''}"
            if key in visited_urls:
                logger.debug("[diamond_oa_nl] Skipping duplicate URL: %s", base_url)
                continue

            # Skip journals already fully harvested
            if state.get(journal_name, {}).get("done"):
                logger.info("[diamond_oa_nl] %s already done, skipping", journal_name)
                visited_urls.add(key)
                continue

            # Probe with Identify
            if not _oai_identify(base_url):
                logger.warning(
                    "[diamond_oa_nl] %s did not respond to Identify at %s — skipping",
                    journal_name, base_url,
                )
                state.setdefault(journal_name, {})["identify_failed"] = True
                state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
                visited_urls.add(key)
                time.sleep(_DELAY)
                continue

            visited_urls.add(key)
            n = _harvest_journal(
                journal_name, base_url, set_spec,
                output_dir, pdfs_dir, seen_ids, state, fout,
            )
            total_new += n

            # Persist state after each journal
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            time.sleep(_DELAY)

    # Final acquisition log entry
    _log(output_dir, "oai-pmh:multi-journal", f"total_records={len(seen_ids)}")

    pdf_count = sum(1 for _ in pdfs_dir.glob("*.pdf"))
    logger.info(
        "[diamond_oa_nl] Done. %d total records, %d PDFs in %s",
        len(seen_ids), pdf_count, output_dir,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Harvest Dutch Diamond OA journals via OAI-PMH"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
