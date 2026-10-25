"""
Download biodiversity publications from Naturalis Biodiversity Center via OAI-PMH.

Naturalis (https://repository.naturalis.nl/) is the research repository of
Naturalis Biodiversity Center in Leiden. Publications are predominantly in Dutch
or English; we collect Dutch-language items and items with a CC licence.

OAI-PMH: https://repository.naturalis.nl/oai/request
License:  CC-BY (as declared in dc:rights per record)

Strategy:
  1. OAI-PMH ListRecords (metadataPrefix=oai_dc) with resumptionToken paging
  2. Filter: dc:language starts with "nl" OR rights contains "creativecommons" / "cc"
  3. Save metadata as naturalis_records.jsonl (one record per line)
  4. Download PDF if dc:identifier contains a .pdf URL → pdfs/{record_id}.pdf

Resume: load existing record identifiers from JSONL before starting.

Usage:
  python -m pipeline acquire --sources naturalis
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

SOURCE  = "naturalis"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "science"

_OAI_BASE  = "https://repository.naturalis.nl/oai"
_UA        = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY_OAI = 1.0   # seconds between OAI page requests
_DELAY_PDF = 2.0   # seconds between PDF downloads

# OAI-PMH / Dublin Core namespaces
_NS_OAI  = "http://www.openarchives.org/OAI/2.0/"
_NS_DC   = "http://purl.org/dc/elements/1.1/"
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
        for el in record_element.findall(f".//*"):
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
        ident = header.find(f"{{{_NS_OAI}}}identifier") or header.find("identifier")
        if ident is not None and ident.text:
            return ident.text.strip()
    return ""


def _is_deleted(record_element: ET.Element) -> bool:
    header = record_element.find(f"{{{_NS_OAI}}}header") or record_element.find("header")
    if header is not None:
        return header.get("status") == "deleted"
    return False


def _should_include(languages: list[str], rights_list: list[str]) -> bool:
    """Return True if the record should be included in the corpus."""
    for lang in languages:
        if lang.lower().startswith("nl"):
            return True
    for rights in rights_list:
        r = rights.lower()
        if "creativecommons" in r or "creative commons" in r or " cc " in r or r.startswith("cc"):
            return True
    return False


def _find_pdf_identifier(identifiers: list[str]) -> str | None:
    """Return the first identifier that looks like a PDF URL."""
    for ident in identifiers:
        if ident.lower().endswith(".pdf") and ident.startswith("http"):
            return ident
        if "pdf" in ident.lower() and ident.startswith("http"):
            return ident
    return None


def _safe_record_id(record_id: str) -> str:
    """Convert OAI identifier to a safe filename (strip scheme, replace / and :)."""
    safe = re.sub(r'^[a-z]+://', '', record_id)
    safe = re.sub(r'[/:\\?*|"<>]', '_', safe)
    return safe[:120]  # cap length


def _download_pdf(url: str, dest: Path, timeout: int = 120) -> bool:
    """Download a PDF to dest. Returns True on success, silently skips 403/404."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dest.write_bytes(resp.read())
        return True
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            pass  # silently skip
        else:
            logger.warning("[naturalis] PDF download failed (%d): %s", exc.code, url)
        return False
    except Exception as exc:
        logger.warning("[naturalis] PDF error: %s — %s", url, exc)
        return False


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download Naturalis repository records via OAI-PMH.

    Saves:
      output_dir/naturalis_records.jsonl   — one JSON object per record
      output_dir/pdfs/{record_id}.pdf      — PDFs where available
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir = output_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)
    records_path = output_dir / "naturalis_records.jsonl"
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[naturalis] [dry-run] would harvest OAI-PMH from %s", _OAI_BASE)
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
            logger.info("[naturalis] Resuming — %d records already saved", len(seen_ids))

    total_records = len(seen_ids)
    total_pdf     = sum(1 for _ in pdfs_dir.glob("*.pdf"))

    # Start OAI-PMH harvest
    url = _build_url(verb="ListRecords", metadataPrefix="oai_dc")
    page_num = 0

    with records_path.open("a", encoding="utf-8") as fout:
        while url:
            page_num += 1
            logger.debug("[naturalis] Fetching OAI page %d: %s", page_num, url[:120])

            try:
                root = _fetch_xml(url)
            except Exception as exc:
                logger.warning("[naturalis] OAI request failed (page %d): %s", page_num, exc)
                time.sleep(_DELAY_OAI * 5)
                break

            # Extract records
            list_records = root.find(f"{{{_NS_OAI}}}ListRecords")
            if list_records is None:
                list_records = root.find("ListRecords")
            if list_records is None:
                # Check for OAI error
                error_el = root.find(f".//{{{_NS_OAI}}}error")
                if error_el is None:
                    error_el = root.find(".//error")
                if error_el is not None:
                    logger.warning(
                        "[naturalis] OAI error (page %d): %s — %s",
                        page_num,
                        error_el.get("code", ""),
                        error_el.text or "",
                    )
                break

            new_count = 0
            for record_el in list_records.findall(f"{{{_NS_OAI}}}record"):
                if _is_deleted(record_el):
                    continue
                rec_id = _record_id(record_el)
                if not rec_id or rec_id in seen_ids:
                    continue

                # Dublin Core fields
                titles      = _dc_values(record_el, "title")
                creators    = _dc_values(record_el, "creator")
                subjects    = _dc_values(record_el, "subject")
                descriptions= _dc_values(record_el, "description")
                rights_list = _dc_values(record_el, "rights")
                languages   = _dc_values(record_el, "language")
                identifiers = _dc_values(record_el, "identifier")
                dates       = _dc_values(record_el, "date")

                if not _should_include(languages, rights_list):
                    continue

                # Publication year
                year: int | None = None
                for d in dates:
                    m = re.search(r'\b(19|20)\d{2}\b', d)
                    if m:
                        year = int(m.group(0))
                        break

                # Best URL: non-PDF identifier, or first http identifier
                source_url = ""
                for ident in identifiers:
                    if ident.startswith("http") and not ident.lower().endswith(".pdf"):
                        source_url = ident
                        break
                if not source_url:
                    for ident in identifiers:
                        if ident.startswith("http"):
                            source_url = ident
                            break

                record_obj = {
                    "record_id":    rec_id,
                    "titles":       titles,
                    "creators":     creators,
                    "subjects":     subjects,
                    "descriptions": descriptions,
                    "rights":       rights_list,
                    "languages":    languages,
                    "identifiers":  identifiers,
                    "year":         year,
                    "source_url":   source_url,
                    "acquisition_date": acq_date,
                }

                fout.write(json.dumps(record_obj, ensure_ascii=False) + "\n")
                seen_ids.add(rec_id)
                total_records += 1
                new_count += 1

                # Download PDF if available
                pdf_url = _find_pdf_identifier(identifiers)
                if pdf_url:
                    safe_id  = _safe_record_id(rec_id)
                    pdf_path = pdfs_dir / f"{safe_id}.pdf"
                    if not pdf_path.exists():
                        if _download_pdf(pdf_url, pdf_path):
                            total_pdf += 1
                        time.sleep(_DELAY_PDF)

            logger.info(
                "[naturalis] Page %d: +%d records (total=%d, pdfs=%d)",
                page_num, new_count, total_records, total_pdf
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
            "total_pdfs":       total_pdf,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[naturalis] Done. %d records, %d PDFs.", total_records, total_pdf)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Naturalis biodiversity records via OAI-PMH (CC-BY)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
