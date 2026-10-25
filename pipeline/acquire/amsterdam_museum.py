"""
Download Amsterdam Museum collection metadata via OAI-PMH.

The Amsterdam Museum (https://amdata.adlibug.nl/) exposes its collection
through an Adlib-based OAI-PMH endpoint. Records are predominantly Dutch;
we harvest all records where dc:language starts with "nl", "dut", or "nld",
as well as records with no language declaration (collection descriptions
default to Dutch).

OAI-PMH: https://amdata.adlibug.nl/api/oai
License:  CC0
Domain:   cultural

Strategy:
  1. OAI-PMH ListRecords (metadataPrefix=oai_dc) with resumptionToken paging
  2. Filter: dc:language starts with "nl", "dut", or "nld" — OR language absent
  3. Extract: titles, descriptions, materials, techniques, periods, identifiers, dates
  4. Text body = titles + descriptions + materials + techniques (joined with ". ")
  5. Save metadata as amsterdam_museum_records.jsonl (one record per line)

Resume: load existing record identifiers from JSONL before starting.

Usage:
  python -m pipeline acquire --sources amsterdam_museum
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

SOURCE  = "amsterdam_museum"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "cultural"

_OAI_BASE  = "https://amdata.adlibug.nl/api/oai"
_UA        = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY_OAI = 1.5   # seconds between OAI page requests
_MAX_RECORDS = 50_000

# OAI-PMH / Dublin Core namespaces
_NS_OAI    = "http://www.openarchives.org/OAI/2.0/"
_NS_DC     = "http://purl.org/dc/elements/1.1/"
_NS_OAI_DC = "http://www.openarchives.org/OAI/2.0/oai_dc/"


def _build_url(**kwargs: str) -> str:
    params = kwargs
    return f"{_OAI_BASE}?{urllib.parse.urlencode(params)}"


def _fetch_xml(url: str, timeout: int = 60) -> ET.Element:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return ET.fromstring(resp.read())


def _dc_values(record_element: ET.Element, tag: str) -> list[str]:
    """Extract all values of a DC element from a record's metadata."""
    results = []
    # Navigate: record > metadata > oai_dc:dc > dc:tag
    for md in record_element.findall(f".//{{{_NS_OAI_DC}}}dc"):
        for el in md.findall(f"{{{_NS_DC}}}{tag}"):
            if el.text:
                results.append(el.text.strip())
    # Fallback: namespace-agnostic search
    if not results:
        for el in record_element.findall(".//*"):
            if el.tag.endswith(f"}}{tag}") or el.tag == tag:
                if el.text:
                    results.append(el.text.strip())
    return results


def _record_id(record_element: ET.Element) -> str:
    """Extract OAI record identifier."""
    header = record_element.find(f"{{{_NS_OAI}}}header")
    if header is None:
        header = record_element.find("header")
    if header is not None:
        ident = header.find(f"{{{_NS_OAI}}}identifier")
        if ident is None:
            ident = header.find("identifier")
        if ident is not None and ident.text:
            return ident.text.strip()
    return ""


def _is_deleted(record_element: ET.Element) -> bool:
    header = record_element.find(f"{{{_NS_OAI}}}header")
    if header is None:
        header = record_element.find("header")
    if header is not None:
        return header.get("status") == "deleted"
    return False


def _should_include(languages: list[str]) -> bool:
    """Return True if the record should be included.

    Include when:
    - dc:language starts with "nl", "dut", or "nld" (ISO 639 codes for Dutch)
    - OR no language is declared (Amsterdam Museum collection descriptions
      default to Dutch)
    """
    if not languages:
        return True  # absent language → Dutch by default
    for lang in languages:
        l = lang.lower().strip()
        if l.startswith("nl") or l.startswith("dut") or l.startswith("nld"):
            return True
    return False


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download Amsterdam Museum collection records via OAI-PMH.

    Saves:
      output_dir/amsterdam_museum_records.jsonl  — one JSON object per record
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "amsterdam_museum_records.jsonl"
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[amsterdam_museum] [dry-run] would harvest OAI-PMH from %s", _OAI_BASE)
        return

    # Resume: load already-saved record identifiers
    seen_ids: set[str] = set()
    if records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen_ids.add(json.loads(line)["record_id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_ids:
            logger.info("[amsterdam_museum] Resuming — %d records already saved", len(seen_ids))

    total_records = len(seen_ids)

    # Start OAI-PMH harvest
    url = _build_url(verb="ListRecords", metadataPrefix="oai_dc")
    page_num = 0

    with records_path.open("a", encoding="utf-8") as fout:
        while url:
            if total_records >= _MAX_RECORDS:
                logger.info("[amsterdam_museum] Reached max records limit (%d).", _MAX_RECORDS)
                break

            page_num += 1
            logger.debug("[amsterdam_museum] Fetching OAI page %d: %s", page_num, url[:120])

            try:
                root = _fetch_xml(url)
            except urllib.error.HTTPError as exc:
                logger.warning(
                    "[amsterdam_museum] HTTP error on page %d (%d): %s",
                    page_num, exc.code, url
                )
                break
            except Exception as exc:
                logger.warning(
                    "[amsterdam_museum] OAI request failed (page %d): %s",
                    page_num, exc
                )
                time.sleep(_DELAY_OAI * 5)
                break

            # Locate ListRecords element
            list_records = root.find(f"{{{_NS_OAI}}}ListRecords")
            if list_records is None:
                list_records = root.find("ListRecords")
            if list_records is None:
                # Check for OAI error element
                error_el = root.find(f".//{{{_NS_OAI}}}error")
                if error_el is None:
                    error_el = root.find(".//error")
                if error_el is not None:
                    logger.warning(
                        "[amsterdam_museum] OAI error (page %d): %s — %s",
                        page_num,
                        error_el.get("code", ""),
                        error_el.text or "",
                    )
                else:
                    logger.warning(
                        "[amsterdam_museum] No ListRecords on page %d — stopping.", page_num
                    )
                break

            # Warn and bail if page 1 returned zero records
            record_elements = list_records.findall(f"{{{_NS_OAI}}}record")
            if page_num == 1 and not record_elements:
                logger.warning(
                    "[amsterdam_museum] 0 records returned on first page — endpoint may be unavailable."
                )
                break

            new_count = 0
            for record_el in record_elements:
                if _is_deleted(record_el):
                    continue
                rec_id = _record_id(record_el)
                if not rec_id or rec_id in seen_ids:
                    continue

                # Dublin Core fields
                titles       = _dc_values(record_el, "title")
                descriptions = _dc_values(record_el, "description")
                languages    = _dc_values(record_el, "language")
                identifiers  = _dc_values(record_el, "identifier")
                dates        = _dc_values(record_el, "date")
                # Museum-specific fields mapped via dc:subject / dc:format / dc:type
                materials    = _dc_values(record_el, "medium")    # dc:medium (Adlib) or fallback
                if not materials:
                    # Adlib often serialises material as dc:format
                    materials = _dc_values(record_el, "format")
                techniques   = _dc_values(record_el, "type")      # dc:type carries technique info
                periods      = _dc_values(record_el, "coverage")  # dc:coverage carries period/place

                if not _should_include(languages):
                    continue

                # Best source URL: first http identifier
                source_url = ""
                for ident in identifiers:
                    if ident.startswith("http"):
                        source_url = ident
                        break

                # Publication year
                year: int | None = None
                for d in dates:
                    m = re.search(r'\b(1[0-9]{3}|20[0-9]{2})\b', d)
                    if m:
                        year = int(m.group(0))
                        break

                record_obj = {
                    "record_id":        rec_id,
                    "titles":           titles,
                    "descriptions":     descriptions,
                    "materials":        materials,
                    "techniques":       techniques,
                    "periods":          periods,
                    "identifiers":      identifiers,
                    "dates":            dates,
                    "year":             year,
                    "source_url":       source_url,
                    "acquisition_date": acq_date,
                }

                fout.write(json.dumps(record_obj, ensure_ascii=False) + "\n")
                seen_ids.add(rec_id)
                total_records += 1
                new_count += 1

                if total_records >= _MAX_RECORDS:
                    break

            logger.info(
                "[amsterdam_museum] Page %d: +%d records (total=%d)",
                page_num, new_count, total_records,
            )

            # Get resumptionToken for next page
            token_el = list_records.find(f"{{{_NS_OAI}}}resumptionToken")
            if token_el is None:
                token_el = list_records.find("resumptionToken")
            if token_el is not None and token_el.text and token_el.text.strip():
                token = token_el.text.strip()
                url = _build_url(verb="ListRecords", resumptionToken=token)
                time.sleep(_DELAY_OAI)
            else:
                url = ""  # no more pages

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _OAI_BASE,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_records":    total_records,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[amsterdam_museum] Done. %d records.", total_records)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Amsterdam Museum collection records via OAI-PMH (CC0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
