"""
Harvest Stellenbosch University SUNScholar repository via OAI-PMH.

SUNScholar (scholar.sun.ac.za) is the institutional repository of
Stellenbosch University. It publishes primarily Afrikaans and English
academic output under open-access policies.  We collect Afrikaans and Dutch
language records under CC-BY or CC0 / open-access rights.

OAI-PMH base: https://scholar.sun.ac.za/server/oai/request  (DSpace 7)
License: CC-BY 4.0 (Stellenbosch Open Access policy)
Country: ZA
Domain: academic

Strategy:
  1. OAI-PMH ListRecords (metadataPrefix=oai_dc) with resumptionToken paging.
  2. Language filter: "af", "afr", "afrikaans", "nl", "nld", "dut", "dutch",
     any nl-* / af-* prefix, OR empty language field.
  3. Rights filter: skip if rights contains "nc" or "nd" (case-insensitive).
  4. Save metadata to sunscholar_metadata.jsonl (one record per line).
  5. Download PDFs for records whose dc:identifier is a direct PDF URL.
  6. Save resumption state to harvest_state.json for restart capability.
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

SOURCE  = "sunscholar"
LICENSE = "CC-BY"
COUNTRY = "ZA"
DOMAIN  = "academic"

_OAI_BASE   = "https://scholar.sun.ac.za/server/oai/request"
_USER_AGENT = "NederCorpus/0.1 (academic research; Afrikaans corpus; scholar.sun.ac.za)"
_DELAY      = 1.0   # seconds between OAI pages

# OAI-PMH / Dublin Core XML namespaces
_NS_OAI    = "http://www.openarchives.org/OAI/2.0/"
_NS_DC     = "http://purl.org/dc/elements/1.1/"
_NS_OAI_DC = "http://www.openarchives.org/OAI/2.0/oai_dc/"


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                logger.debug("[sunscholar] HTTP %d for %s", exc.code, url)
                return None
            if attempt == retries - 1:
                logger.warning("[sunscholar] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[sunscholar] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _log(output_dir: Path, url: str, identifier: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "identifier":       identifier,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _build_url(**kwargs: str) -> str:
    return f"{_OAI_BASE}?{urllib.parse.urlencode(kwargs)}"


def _dc_values(record_el: ET.Element, tag: str) -> list[str]:
    """Extract all Dublin Core values for a given tag from a record element."""
    results: list[str] = []
    # Try within oai_dc:dc container
    for dc_el in record_el.findall(f".//{{{_NS_OAI_DC}}}dc"):
        for el in dc_el.findall(f"{{{_NS_DC}}}{tag}"):
            if el.text:
                results.append(el.text.strip())
    # Fallback: scan all elements by local name
    if not results:
        for el in record_el.iter():
            local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if local == tag and el.text:
                results.append(el.text.strip())
    return results


def _record_id(record_el: ET.Element) -> str:
    header = record_el.find(f"{{{_NS_OAI}}}header")
    if header is None:
        header = record_el.find("header")
    if header is not None:
        ident = header.find(f"{{{_NS_OAI}}}identifier")
        if ident is None:
            ident = header.find("identifier")
        if ident is not None and ident.text:
            return ident.text.strip()
    return ""


def _is_deleted(record_el: ET.Element) -> bool:
    header = record_el.find(f"{{{_NS_OAI}}}header")
    if header is None:
        header = record_el.find("header")
    return header is not None and header.get("status") == "deleted"


def _is_target_language(languages: list[str]) -> bool:
    """Accept Afrikaans, Dutch, or empty language field."""
    if not languages:
        return True  # be permissive — multilingual repo
    for lang in languages:
        lo = lang.lower().strip()
        if lo in ("af", "afr", "afrikaans", "nl", "nld", "dut", "dutch"):
            return True
        if lo.startswith("nl-") or lo.startswith("af-"):
            return True
    return False


def _is_allowed_rights(rights_list: list[str]) -> bool:
    """Skip records whose rights clearly say NC or ND."""
    for r in rights_list:
        lo = r.lower()
        if "nc" in lo or "nd" in lo:
            return False
    return True


def _safe_id(record_id: str) -> str:
    safe = re.sub(r"^[a-z]+://", "", record_id)
    safe = re.sub(r"[/\\:*?\"<>|]", "_", safe)
    return safe[:100]


def _find_pdf_url(identifiers: list[str]) -> str | None:
    for ident in identifiers:
        if ident.startswith("http") and ".pdf" in ident.lower():
            return ident
    return None


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_token": None, "count": 0}


def _save_state(state_path: Path, token: str | None, count: int) -> None:
    state_path.write_text(
        json.dumps({"last_token": token, "count": count}, ensure_ascii=False),
        encoding="utf-8",
    )


def download(
    output_dir: Path,
    max_records: int = 50_000,
    dry_run: bool = False,
    **kwargs,
) -> None:
    """
    Harvest SUNScholar via OAI-PMH (oai_dc).

    Saves:
      sunscholar_metadata.jsonl  — one JSON line per accepted record
      pdfs/{safe_id}.pdf         — PDFs where dc:identifier is a PDF URL
      harvest_state.json         — resumption state (last_token, count)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir   = output_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)
    meta_path  = output_dir / "sunscholar_metadata.jsonl"
    state_path = output_dir / "harvest_state.json"
    acq_date   = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[sunscholar] [dry-run] would harvest OAI-PMH from %s", _OAI_BASE)
        return

    # Load seen record IDs for deduplication / resuming
    seen_ids: set[str] = set()
    if meta_path.exists():
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen_ids.add(json.loads(line)["record_id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_ids:
            logger.info("[sunscholar] Resuming — %d records already saved", len(seen_ids))

    # Load harvest state (resumption token)
    state = _load_state(state_path)
    last_token = state.get("last_token")

    if last_token:
        logger.info("[sunscholar] Resuming with token: %s…", last_token[:40])
        start_url = _build_url(verb="ListRecords", resumptionToken=last_token)
    else:
        start_url = _build_url(verb="ListRecords", metadataPrefix="oai_dc")

    total_records = len(seen_ids)
    total_pdf     = sum(1 for _ in pdfs_dir.glob("*.pdf"))
    page_num      = 0
    current_url   = start_url

    with meta_path.open("a", encoding="utf-8") as fout:
        while current_url and total_records < max_records:
            page_num += 1
            logger.debug("[sunscholar] OAI page %d: %s", page_num, current_url[:120])

            raw = _fetch(current_url, timeout=90)
            if raw is None:
                logger.warning("[sunscholar] OAI request failed at page %d", page_num)
                break

            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                logger.warning("[sunscholar] XML parse error at page %d: %s", page_num, exc)
                break

            list_records = root.find(f"{{{_NS_OAI}}}ListRecords")
            if list_records is None:
                list_records = root.find("ListRecords")
            if list_records is None:
                error_el = root.find(f".//{{{_NS_OAI}}}error")
                if error_el is None:
                    error_el = root.find(".//error")
                if error_el is not None:
                    code = error_el.get("code", "")
                    msg  = error_el.text or ""
                    if code == "noRecordsMatch":
                        logger.info("[sunscholar] noRecordsMatch — harvest complete")
                    else:
                        logger.warning("[sunscholar] OAI error %s: %s", code, msg)
                break

            new_count = 0
            for record_el in list_records.findall(f"{{{_NS_OAI}}}record"):
                if total_records >= max_records:
                    break
                if _is_deleted(record_el):
                    continue

                rec_id = _record_id(record_el)
                if not rec_id or rec_id in seen_ids:
                    continue

                languages  = _dc_values(record_el, "language")
                rights     = _dc_values(record_el, "rights")

                if not _is_target_language(languages):
                    continue
                if not _is_allowed_rights(rights):
                    continue

                titles      = _dc_values(record_el, "title")
                creators    = _dc_values(record_el, "creator")
                descriptions = _dc_values(record_el, "description")
                identifiers = _dc_values(record_el, "identifier")
                dates       = _dc_values(record_el, "date")

                source_url = next(
                    (i for i in identifiers if i.startswith("http") and ".pdf" not in i.lower()),
                    next((i for i in identifiers if i.startswith("http")), ""),
                )

                row = {
                    "record_id":        rec_id,
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
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                seen_ids.add(rec_id)
                total_records += 1
                new_count     += 1

                # Download PDF if a direct PDF URL is present
                pdf_url = _find_pdf_url(identifiers)
                if pdf_url:
                    safe   = _safe_id(rec_id)
                    dest   = pdfs_dir / f"{safe}.pdf"
                    if not dest.exists():
                        pdf_data = _fetch(pdf_url, timeout=180)
                        if pdf_data and pdf_data[:4] == b"%PDF":
                            dest.write_bytes(pdf_data)
                            _log(output_dir, pdf_url, rec_id)
                            total_pdf += 1
                            logger.info("[sunscholar] PDF: %s", dest.name)
                        elif pdf_data:
                            logger.debug("[sunscholar] Not a PDF at %s", pdf_url)

            logger.info(
                "[sunscholar] Page %d: +%d records (total=%d, pdfs=%d)",
                page_num, new_count, total_records, total_pdf,
            )

            # Resumption token
            token_el = list_records.find(f"{{{_NS_OAI}}}resumptionToken")
            if token_el is None:
                token_el = list_records.find("resumptionToken")

            next_token: str | None = None
            if token_el is not None and token_el.text and token_el.text.strip():
                next_token = token_el.text.strip()

            _save_state(state_path, next_token, total_records)

            if next_token:
                current_url = _build_url(verb="ListRecords", resumptionToken=next_token)
                time.sleep(_DELAY)
            else:
                current_url = ""

    # Final acquisition log entry
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

    logger.info("[sunscholar] Done. %d records, %d PDFs.", total_records, total_pdf)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Harvest SUNScholar (Afrikaans/Dutch) via OAI-PMH (CC-BY)"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--max-records", type=int, default=50_000)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    download(args.output, max_records=args.max_records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
