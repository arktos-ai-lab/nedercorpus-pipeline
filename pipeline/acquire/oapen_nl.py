"""
Download Dutch open-access academic books from OAPEN (CC-BY only) via OAI-PMH.

OAPEN (https://library.oapen.org/) is the Open Access Publishing in European
Networks repository. It hosts thousands of peer-reviewed academic books under
open licences. We collect only Dutch-language books with a CC-BY licence
(not NC, not ND) to comply with the NederCorpus licence policy.

OAI-PMH: https://library.oapen.org/oai/request
License: CC-BY (filtered; NC/ND excluded)
Set: com_20.500.12657_5 (Books)

Strategy:
  1. OAI-PMH ListRecords (set=Books, metadataPrefix=oai_dc) with resumptionToken paging
  2. Filter: dc:language starts "nl" or "dut" or "nld"
  3. Filter: dc:rights is CC-BY without NC/ND
  4. Save metadata as oapen_metadata.jsonl
  5. Download PDF if dc:identifier contains a PDF URL

Usage:
  python -m pipeline acquire --sources oapen_nl
"""
from __future__ import annotations

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

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE   = "oapen_nl"
LICENSE  = "CC-BY"
COUNTRY  = "NL"
DOMAIN   = "academic"

_OAI_BASE   = "https://library.oapen.org/oai/request"
_SET        = "com_20.500.12657_5"   # Books collection
_UA         = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY_OAI  = 1.5   # seconds between OAI page requests
_DELAY_PDF  = 2.0   # seconds between PDF downloads

# OAI-PMH / Dublin Core namespaces
_NS_OAI    = "http://www.openarchives.org/OAI/2.0/"
_NS_DC     = "http://purl.org/dc/elements/1.1/"
_NS_OAI_DC = "http://www.openarchives.org/OAI/2.0/oai_dc/"


def _build_url(**kwargs: str) -> str:
    return f"{_OAI_BASE}?{urllib.parse.urlencode(kwargs)}"


def _fetch_xml(url: str, timeout: int = 60) -> ET.Element:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        return ET.fromstring(resp.read())


def _dc_values(record_el: ET.Element, tag: str) -> list[str]:
    results: list[str] = []
    for dc in record_el.findall(f".//{{{_NS_OAI_DC}}}dc"):
        for el in dc.findall(f"{{{_NS_DC}}}{tag}"):
            if el.text:
                results.append(el.text.strip())
    if not results:
        for el in record_el.iter():
            local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if local == tag and el.text:
                results.append(el.text.strip())
    return results


def _record_id(record_el: ET.Element) -> str:
    header = record_el.find(f"{{{_NS_OAI}}}header") or record_el.find("header")
    if header is not None:
        ident = header.find(f"{{{_NS_OAI}}}identifier") or header.find("identifier")
        if ident is not None and ident.text:
            return ident.text.strip()
    return ""


def _is_deleted(record_el: ET.Element) -> bool:
    header = record_el.find(f"{{{_NS_OAI}}}header") or record_el.find("header")
    return header is not None and header.get("status") == "deleted"


def _is_dutch(languages: list[str]) -> bool:
    for lang in languages:
        l = lang.lower()
        if l.startswith("nl") or l in ("dut", "nld", "dutch", "nederlands"):
            return True
    return False


def _is_allowed_license(rights_list: list[str]) -> bool:
    """CC-BY or CC-BY-SA without NC or ND. Also checks licenseCondition fields."""
    for rights in rights_list:
        r = rights.lower().strip()
        # Skip obviously non-CC entries
        if r in ("open access", "n/a", "", "openaccess", "info:eu-repo/semantics/openaccess"):
            continue
        # Human-readable CC names used in OAPEN licenseCondition field:
        # "Attribution 4.0 International" = CC-BY, "Attribution-ShareAlike 4.0" = CC-BY-SA
        # Reject if NonCommercial or NoDerivatives appear
        if r.startswith("attribution"):
            if "noncommercial" in r or "noderivatives" in r or "noderiv" in r:
                continue
            return True
        # CC URL form: https://creativecommons.org/licenses/by/...
        if "creativecommons" in r or "creative commons" in r or r.startswith("http"):
            if "/by" in r or " by " in r:
                if "nc" not in r and "nd" not in r:
                    return True
        # "CC BY" or "cc-by" shorthand
        if re.search(r'\bcc[\s\-]by\b', r) and "nc" not in r and "nd" not in r:
            return True
    return False


def _find_pdf_url(identifiers: list[str]) -> str | None:
    for ident in identifiers:
        if ident.startswith("http") and ".pdf" in ident.lower():
            return ident
    return None


def _safe_id(record_id: str) -> str:
    safe = re.sub(r'^[a-z]+://', '', record_id)
    safe = re.sub(r'[/:\\?*|"<>]', '_', safe)
    return safe[:100]


def _download_pdf(url: str, dest: Path, timeout: int = 180) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            dest.write_bytes(resp.read())
        return True
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 404):
            logger.warning("[oapen_nl] PDF download HTTP %d: %s", exc.code, url)
        return False
    except Exception as exc:
        logger.warning("[oapen_nl] PDF error: %s — %s", url, exc)
        return False


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download Dutch CC-BY OAPEN books via OAI-PMH.

    Saves:
      oapen_metadata.jsonl   — one JSON object per record
      pdfs/{record_id}.pdf   — PDFs where available
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir = output_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)
    records_path = output_dir / "oapen_metadata.jsonl"
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[oapen_nl] [dry-run] would harvest OAI-PMH from %s", _OAI_BASE)
        return

    # Resume: load already-saved identifiers
    seen_ids: set[str] = set()
    if records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen_ids.add(json.loads(line)["record_id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_ids:
            logger.info("[oapen_nl] Resuming — %d records already saved", len(seen_ids))

    total_records = len(seen_ids)
    total_pdf = sum(1 for _ in pdfs_dir.glob("*.pdf"))

    # OAI-PMH harvest (Books set)
    url = _build_url(verb="ListRecords", metadataPrefix="oai_dc", set=_SET)
    page_num = 0

    with records_path.open("a", encoding="utf-8") as fout:
        while url:
            page_num += 1
            logger.debug("[oapen_nl] Fetching OAI page %d: %s", page_num, url[:120])

            try:
                root = _fetch_xml(url)
            except Exception as exc:
                logger.warning("[oapen_nl] OAI request failed (page %d): %s", page_num, exc)
                time.sleep(_DELAY_OAI * 5)
                break

            list_records = root.find(f"{{{_NS_OAI}}}ListRecords")
            if list_records is None:
                list_records = root.find("ListRecords")
            if list_records is None:
                error_el = root.find(f".//{{{_NS_OAI}}}error")
                if error_el is None:
                    error_el = root.find(".//error")
                if error_el is not None:
                    logger.warning(
                        "[oapen_nl] OAI error (page %d): %s — %s",
                        page_num, error_el.get("code", ""), error_el.text or "",
                    )
                break

            new_count = 0
            for record_el in list_records.findall(f"{{{_NS_OAI}}}record"):
                if _is_deleted(record_el):
                    continue
                rec_id = _record_id(record_el)
                if not rec_id or rec_id in seen_ids:
                    continue

                languages     = _dc_values(record_el, "language")
                rights_list   = _dc_values(record_el, "rights")
                license_cond  = _dc_values(record_el, "licenseCondition")
                combined_rights = rights_list + license_cond

                if not _is_dutch(languages) or not _is_allowed_license(combined_rights):
                    continue

                titles      = _dc_values(record_el, "title")
                creators    = _dc_values(record_el, "creator")
                descriptions= _dc_values(record_el, "description")
                identifiers = _dc_values(record_el, "identifier")
                dates       = _dc_values(record_el, "date")

                year: int | None = None
                for d in dates:
                    m = re.search(r'\b(19|20)\d{2}\b', d)
                    if m:
                        year = int(m.group(0))
                        break

                source_url = ""
                for ident in identifiers:
                    if ident.startswith("http") and ".pdf" not in ident.lower():
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

                # Download PDF
                pdf_url = _find_pdf_url(identifiers)
                if pdf_url:
                    safe_id  = _safe_id(rec_id)
                    pdf_path = pdfs_dir / f"{safe_id}.pdf"
                    if not pdf_path.exists():
                        if _download_pdf(pdf_url, pdf_path):
                            total_pdf += 1
                        time.sleep(_DELAY_PDF)

            logger.info(
                "[oapen_nl] Page %d: +%d records (total=%d, pdfs=%d)",
                page_num, new_count, total_records, total_pdf,
            )

            token_el = list_records.find(f"{{{_NS_OAI}}}resumptionToken")
            if token_el is None:
                token_el = list_records.find("resumptionToken")
            if token_el is not None and token_el.text and token_el.text.strip():
                url = _build_url(verb="ListRecords", resumptionToken=token_el.text.strip())
                time.sleep(_DELAY_OAI)
            else:
                url = ""

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

    logger.info("[oapen_nl] Done. %d records, %d PDFs.", total_records, total_pdf)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Dutch CC-BY books from OAPEN via OAI-PMH")
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
