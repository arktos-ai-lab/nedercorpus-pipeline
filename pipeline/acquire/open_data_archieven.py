"""
Harvest Dutch archive finding aids (EAD) via OAI-PMH from archieven.nl.

Open Data Archieven (archieven.nl) aggregates finding aids from hundreds of
Dutch regional and municipal archives. EAD records contain rich descriptive
prose — scope notes, biographical/historical introductions, access restrictions
descriptions — which constitutes natural Dutch text at varying registers.

License: CC0 (see https://www.archieven.nl/nl/onderzoeken/archieven)
OAI-PMH: https://harvest.archieven.nl/OAI/OAIHandler

Strategy:
  1. OAI-PMH ListRecords with metadataPrefix=oai_ead (fallback: oai_dc)
  2. Extract prose from <scopecontent>, <bioghist>, <odd>, <abstract> elements
  3. Save one JSONL record per EAD finding aid
  4. Resume via resumptionToken saved to archieven_resumption_token.txt

Usage:
  python -m pipeline acquire --sources open_data_archieven
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

SOURCE  = "open_data_archieven"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "cultural"

_OAI_BASE        = "https://harvest.archieven.nl/OAI/OAIHandler"
_UA              = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY           = 1.0    # seconds between OAI-PMH page requests
_TIMEOUT         = 90     # seconds per HTTP request
_RETRY_WAIT_BASE = 5.0    # base wait for transient errors

# OAI-PMH namespace
_NS_OAI = "http://www.openarchives.org/OAI/2.0/"

# EAD namespace (EAD 2002 — the version most commonly used by archieven.nl)
_NS_EAD = "urn:isbn:1-931666-22-9"

# Dublin Core fallback namespace
_NS_DC  = "http://purl.org/dc/elements/1.1/"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _build_url(**kwargs: str) -> str:
    return f"{_OAI_BASE}?{urllib.parse.urlencode(kwargs)}"


def _fetch_xml(url: str, retries: int = 4) -> ET.Element:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
            return ET.fromstring(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = _RETRY_WAIT_BASE * (2 ** attempt)
                logger.warning(
                    "[open_data_archieven] HTTP %d — retrying in %.0fs", exc.code, wait
                )
                time.sleep(wait)
            else:
                raise
        except ET.ParseError as exc:
            logger.warning("[open_data_archieven] XML parse error: %s", exc)
            raise
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = _RETRY_WAIT_BASE * (attempt + 1)
            logger.warning(
                "[open_data_archieven] Error (attempt %d): %s — retry in %.0fs",
                attempt + 1, exc, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")


# ---------------------------------------------------------------------------
# OAI-PMH helpers
# ---------------------------------------------------------------------------

def _find_ns_agnostic(element: ET.Element, *local_names: str) -> list[ET.Element]:
    """Find all child elements matching any of the given local tag names, namespace-agnostic."""
    results = []
    for child in element.iter():
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local in local_names:
            results.append(child)
    return results


_CC_URL_MAP = {
    "creativecommons.org/licenses/by-sa":  "CC-BY-SA",
    "creativecommons.org/licenses/by/":    "CC-BY",
    "creativecommons.org/licenses/by ":    "CC-BY",  # space variant
    "creativecommons.org/publicdomain/zero": "CC0",
    "creativecommons.org/publicdomain/mark": "PD",
}


def _license_from_ead(ead_root: ET.Element) -> str:
    """Extract license from <publicationstmt><p id='copyright'><extref href=...>."""
    for el in ead_root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "extref":
            href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href") or ""
            for pattern, lic in _CC_URL_MAP.items():
                if pattern in href:
                    return lic
            # Fallback: read text content
            text = (el.text or "").strip().upper()
            if "CC0" in text or "PUBLIC DOMAIN" in text:
                return "CC0"
            if "CC BY-SA" in text:
                return "CC-BY-SA"
            if "CC BY" in text:
                return "CC-BY"
    return LICENSE   # default to module constant


def _text_from_paragraphs(container: ET.Element) -> str:
    """
    Recursively collect text from <p> elements inside a container element.
    Falls back to all text content if no <p> elements are found.
    """
    parts = []
    p_elements = _find_ns_agnostic(container, "p")
    if p_elements:
        for p in p_elements:
            t = "".join(p.itertext()).strip()
            if t:
                parts.append(t)
    else:
        t = "".join(container.itertext()).strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


def _extract_ead_record(record_el: ET.Element) -> dict | None:
    """
    Parse a single OAI-PMH <record> element containing EAD metadata.

    Returns a dict with: id, title, description, date, institution, url
    or None if the record is deleted or contains no usable text.
    """
    # Check for deleted status
    header = (
        record_el.find(f"{{{_NS_OAI}}}header")
        or record_el.find("header")
    )
    if header is not None and header.get("status") == "deleted":
        return None

    # OAI record identifier
    rec_id = ""
    if header is not None:
        ident_el = header.find(f"{{{_NS_OAI}}}identifier")
        if ident_el is None:
            ident_el = header.find("identifier")
        if ident_el is not None and ident_el.text:
            rec_id = ident_el.text.strip()

    # datestamp
    date_str = ""
    if header is not None:
        date_el = header.find(f"{{{_NS_OAI}}}datestamp")
        if date_el is None:
            date_el = header.find("datestamp")
        if date_el is not None and date_el.text:
            date_str = date_el.text.strip()

    # Navigate into metadata element
    metadata_el = (
        record_el.find(f"{{{_NS_OAI}}}metadata")
        or record_el.find("metadata")
    )
    if metadata_el is None:
        return None

    # --- Try EAD ---
    title       = ""
    description = ""
    institution = ""
    source_url  = ""
    record_license = LICENSE

    # Find the EAD root element (may be <ead> or wrapped in a container)
    ead_els = list(metadata_el.iter())
    ead_root = None
    for el in ead_els:
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "ead":
            ead_root = el
            break

    if ead_root is not None:
        record_license = _license_from_ead(ead_root)

        # <unittitle> for the collection title (first one, usually in <archdesc>/<did>)
        for ut in _find_ns_agnostic(ead_root, "unittitle"):
            t = "".join(ut.itertext()).strip()
            if t:
                title = t
                break

        # <repository>/<corpname> for institution
        for repo in _find_ns_agnostic(ead_root, "repository"):
            for cn in _find_ns_agnostic(repo, "corpname"):
                t = "".join(cn.itertext()).strip()
                if t:
                    institution = t
                    break
            if institution:
                break

        # <eadid> URL
        for eadid in _find_ns_agnostic(ead_root, "eadid"):
            url_attr = eadid.get("url") or eadid.get("URL") or ""
            if url_attr.startswith("http"):
                source_url = url_attr
            elif eadid.text and eadid.text.strip().startswith("http"):
                source_url = eadid.text.strip()

        # Prose elements: scopecontent, bioghist, odd, abstract
        prose_parts = []
        for local_name in ("scopecontent", "bioghist", "odd", "abstract"):
            for container in _find_ns_agnostic(ead_root, local_name):
                t = _text_from_paragraphs(container)
                if t:
                    prose_parts.append(t)

        description = "\n\n".join(prose_parts)

    else:
        # --- Fallback: Dublin Core ---
        dc_root = None
        for el in ead_els:
            local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if local == "dc":
                dc_root = el
                break

        if dc_root is not None:
            for el in dc_root:
                local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
                if local == "title" and el.text and not title:
                    title = el.text.strip()
                elif local == "description" and el.text:
                    description += (" " if description else "") + el.text.strip()
                elif local == "date" and el.text and not date_str:
                    date_str = el.text.strip()
                elif local == "identifier" and el.text and el.text.strip().startswith("http"):
                    if not source_url:
                        source_url = el.text.strip()
                elif local in ("publisher", "creator") and el.text and not institution:
                    institution = el.text.strip()

    return {
        "id":          rec_id,
        "title":       title,
        "description": description,
        "date":        date_str,
        "institution": institution,
        "url":         source_url,
        "license":     record_license,
    }


# ---------------------------------------------------------------------------
# Main acquirer
# ---------------------------------------------------------------------------

def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Harvest archieven.nl finding aids via OAI-PMH.

    Saves:
      output_dir/archieven_records.jsonl          — one JSON per record
      output_dir/archieven_resumption_token.txt   — last token for resume

    Resume: loads existing record IDs from JSONL, restores resumptionToken.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "archieven_records.jsonl"
    token_path   = output_dir / "archieven_resumption_token.txt"
    acq_date     = datetime.date.today().isoformat()

    if dry_run:
        logger.info(
            "[open_data_archieven] [dry-run] would harvest OAI-PMH from %s", _OAI_BASE
        )
        return

    # Resume: load existing record IDs
    seen_ids: set[str] = set()
    if records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    seen_ids.add(json.loads(line)["id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_ids:
            logger.info(
                "[open_data_archieven] Resuming — %d records already saved", len(seen_ids)
            )

    # Restore resumptionToken if available
    saved_token = ""
    if token_path.exists():
        saved_token = token_path.read_text(encoding="utf-8").strip()
        if saved_token:
            logger.info("[open_data_archieven] Resuming from saved token: %s…", saved_token[:60])

    # Choose starting URL
    if saved_token:
        url = _build_url(verb="ListRecords", resumptionToken=saved_token)
        metadata_prefix = "oai_ead"   # track for fallback detection only
    else:
        url = _build_url(verb="ListRecords", metadataPrefix="oai_ead")
        metadata_prefix = "oai_ead"

    total_records = len(seen_ids)
    page_num      = 0
    using_fallback = False

    with records_path.open("a", encoding="utf-8") as fout:
        while url:
            page_num += 1
            logger.info(
                "[open_data_archieven] OAI page %d (total=%d): %s…",
                page_num, total_records, url[:100],
            )

            try:
                root = _fetch_xml(url)
            except Exception as exc:
                logger.warning(
                    "[open_data_archieven] OAI request failed (page %d): %s",
                    page_num, exc,
                )
                break

            # Check for OAI error
            error_el = root.find(f".//{{{_NS_OAI}}}error") or root.find(".//error")
            if error_el is not None:
                code = error_el.get("code", "")
                msg  = (error_el.text or "").strip()
                logger.warning(
                    "[open_data_archieven] OAI error (page %d): %s — %s", page_num, code, msg
                )
                # If EAD prefix is unsupported, fall back to oai_dc
                if not using_fallback and code in ("cannotDisseminateFormat", "badArgument"):
                    logger.info("[open_data_archieven] Falling back to metadataPrefix=oai_dc")
                    url = _build_url(verb="ListRecords", metadataPrefix="oai_dc")
                    metadata_prefix = "oai_dc"
                    using_fallback  = True
                    page_num = 0
                    continue
                break

            # Locate ListRecords element
            list_records = (
                root.find(f"{{{_NS_OAI}}}ListRecords")
                or root.find("ListRecords")
            )
            if list_records is None:
                logger.warning("[open_data_archieven] No ListRecords element on page %d", page_num)
                break

            # Process records
            new_count = 0
            record_tag_full  = f"{{{_NS_OAI}}}record"
            record_tag_plain = "record"

            record_els = list_records.findall(record_tag_full)
            if not record_els:
                record_els = list_records.findall(record_tag_plain)

            for record_el in record_els:
                try:
                    rec = _extract_ead_record(record_el)
                except Exception as exc:
                    logger.debug("[open_data_archieven] Skipping malformed record: %s", exc)
                    continue

                if rec is None:
                    continue   # deleted
                if rec["id"] and rec["id"] in seen_ids:
                    continue   # already saved

                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if rec["id"]:
                    seen_ids.add(rec["id"])
                total_records += 1
                new_count     += 1

            logger.info(
                "[open_data_archieven] Page %d: +%d new records (total=%d)",
                page_num, new_count, total_records,
            )

            # Get resumptionToken
            # NOTE: use 'if None' not 'or' — Python 3.14 bool(Element) is False for childless elements
            token_el = list_records.find(f"{{{_NS_OAI}}}resumptionToken")
            if token_el is None:
                token_el = list_records.find("resumptionToken")
            if token_el is not None and token_el.text and token_el.text.strip():
                token = token_el.text.strip()
                # Save token for resume
                token_path.write_text(token, encoding="utf-8")
                url = _build_url(verb="ListRecords", resumptionToken=token)
                time.sleep(_DELAY)
            else:
                url = ""   # harvest complete
                # Clear saved token on successful completion
                if token_path.exists():
                    token_path.unlink()

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _OAI_BASE,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_records":    total_records,
            "metadata_prefix":  metadata_prefix,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[open_data_archieven] Done. %d records saved.", total_records)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Harvest archieven.nl EAD finding aids via OAI-PMH (CC0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
