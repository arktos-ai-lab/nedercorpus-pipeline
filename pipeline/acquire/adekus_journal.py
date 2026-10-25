"""
Harvest the Academic Journal of Suriname (AdeKUS) via OAI-PMH.

The Academic Journal of Suriname is published by the Anton de Kom Universiteit
van Suriname (AdeKUS). It is an open-access journal covering a range of academic
disciplines relevant to Suriname and the wider Caribbean region.

Content in Dutch (NL / NL-Caribbean variety) is of particular interest for the
NederCorpus project. The journal operates under CC BY 4.0 and provides metadata
via a standard OAI-PMH endpoint.

OAI-PMH base URLs (tried in order):
  1. https://adekusjournal.uvs.edu/index.php/AJS/oai
  2. https://adekusjournal.uvs.edu/oai

License: CC BY 4.0
Country: SR (Suriname)
Domain:  academic
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

logger = logging.getLogger(__name__)

SOURCE  = "adekus_journal"
LICENSE = "CC-BY"
COUNTRY = "SR"
DOMAIN  = "academic"
LANG    = "NL"

_OAI_BASES = [
    "https://adekusjournal.uvs.edu/index.php/AJS/oai",
    "https://adekusjournal.uvs.edu/oai",
]
_USER_AGENT = (
    "NederCorpus/0.1 (academic research; Dutch Caribbean corpus; "
    "AdeKUS open-access journal harvest)"
)
_DELAY = 1.0

# OAI-PMH / Dublin Core namespaces
_NS_OAI = "http://www.openarchives.org/OAI/2.0/"
_NS_DC  = "http://purl.org/dc/elements/1.1/"
_NS_OAI_DC = "http://www.openarchives.org/OAI/2.0/oai_dc/"

# Language codes we accept (Dutch variants)
_ACCEPT_LANGS = {"nl", "nld", "dut", "dutch", "nederlands", "nl-sr", "nl-be", "nl-nl"}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, retries: int = 4, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                logger.debug("[adekus_journal] HTTP %d for %s", exc.code, url)
                return None
            if attempt == retries - 1:
                logger.warning("[adekus_journal] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 ** attempt)
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[adekus_journal] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 ** attempt)
    return None


def _log(output_dir: Path, url: str, record_id: str = "", count: int = 0) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "record_id":        record_id,
        "count":            count,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# OAI-PMH helpers
# ---------------------------------------------------------------------------

def _build_url(base: str, **kwargs: str) -> str:
    return f"{base}?{urllib.parse.urlencode(kwargs)}"


def _find_oai_base() -> str | None:
    """Try each known OAI base URL and return the first that responds."""
    for base in _OAI_BASES:
        url = _build_url(base, verb="Identify")
        logger.info("[adekus_journal] Probing OAI endpoint: %s", base)
        data = _fetch(url, retries=2, timeout=30)
        if data is not None:
            try:
                root = ET.fromstring(data)
                # A valid OAI-PMH Identify response has <repositoryName>
                if root.find(f".//{{{_NS_OAI}}}repositoryName") is not None or \
                   root.find(".//repositoryName") is not None:
                    logger.info("[adekus_journal] OAI endpoint OK: %s", base)
                    return base
                # Accept any parseable XML from the endpoint — might still work
                logger.info("[adekus_journal] Endpoint returned XML (no repositoryName): %s", base)
                return base
            except ET.ParseError:
                logger.debug("[adekus_journal] Non-XML response from %s", base)
                # Still try this base — might fail on Identify but work on ListRecords
                return base
    logger.error("[adekus_journal] No OAI-PMH endpoint responded — harvest aborted")
    return None


def _dc_values(record_el: ET.Element, tag: str) -> list[str]:
    """Extract all Dublin Core field values for a given tag from a record element."""
    results: list[str] = []
    # Try with explicit namespace
    for dc_container in record_el.findall(f".//{{{_NS_OAI_DC}}}dc"):
        for el in dc_container.findall(f"{{{_NS_DC}}}{tag}"):
            if el.text:
                results.append(el.text.strip())
    # Fallback: iterate all elements and match local name
    if not results:
        for el in record_el.iter():
            local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if local == tag and el.text:
                results.append(el.text.strip())
    return results


def _record_oai_id(record_el: ET.Element) -> str:
    """Return the OAI identifier from a <record><header><identifier> element."""
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
    """Return True if the record header carries status='deleted'."""
    header = record_el.find(f"{{{_NS_OAI}}}header")
    if header is None:
        header = record_el.find("header")
    return header is not None and header.get("status") == "deleted"


def _is_dutch_or_empty(languages: list[str]) -> bool:
    """Accept records whose language is Dutch, unknown, or absent."""
    if not languages:
        return True
    for lang in languages:
        l = lang.lower().strip()
        if not l:
            return True
        if l in _ACCEPT_LANGS or l.startswith("nl"):
            return True
    return False


def _safe_id(oai_id: str) -> str:
    """Convert an OAI identifier to a safe filename stem."""
    safe = re.sub(r'^[a-z]+://', '', oai_id)
    safe = re.sub(r'[/:\\?*|"<>\s]', '_', safe)
    return safe[:120] or "record"


def _find_pdf_url(identifiers: list[str]) -> str | None:
    """Return the first identifier that looks like a direct PDF URL."""
    for ident in identifiers:
        if ident.startswith("http") and ".pdf" in ident.lower():
            return ident
    return None


def _find_article_url(identifiers: list[str]) -> str:
    """Return the first HTTP identifier (prefer non-PDF HTML article pages)."""
    html_url = ""
    pdf_url = ""
    for ident in identifiers:
        if not ident.startswith("http"):
            continue
        if ".pdf" in ident.lower():
            if not pdf_url:
                pdf_url = ident
        else:
            if not html_url:
                html_url = ident
    return html_url or pdf_url


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_token": None, "count": 0}


def _save_state(state_path: Path, last_token: str | None, count: int) -> None:
    state_path.write_text(
        json.dumps({"last_token": last_token, "count": count}, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    dry_run: bool = False,
    **kwargs,
) -> None:
    """
    Harvest the AdeKUS Academic Journal of Suriname via OAI-PMH.

    Saves:
      adekus_metadata.jsonl  — one JSON object per accepted record
      pdfs/{safe_id}.pdf     — PDF files where a direct PDF URL is available
      harvest_state.json     — resumption state (last token + count)
      acquisition_log.jsonl  — standard pipeline log entry
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir = output_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)

    metadata_path = output_dir / "adekus_metadata.jsonl"
    state_path    = output_dir / "harvest_state.json"

    if dry_run:
        logger.info(
            "[adekus_journal] [dry-run] would harvest OAI-PMH from %s",
            " / ".join(_OAI_BASES),
        )
        return

    # Discover working OAI endpoint
    oai_base = _find_oai_base()
    if oai_base is None:
        return

    # Load previously saved record IDs for deduplication
    seen_ids: set[str] = set()
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                seen_ids.add(json.loads(line)["id"])
            except (KeyError, json.JSONDecodeError):
                pass
        if seen_ids:
            logger.info(
                "[adekus_journal] Resuming — %d records already saved", len(seen_ids)
            )

    # Load harvest state (resumption token from a previous interrupted run)
    state = _load_state(state_path)
    total = state["count"] or len(seen_ids)
    total_pdf = sum(1 for _ in pdfs_dir.glob("*.pdf"))

    # Build initial URL — use saved resumption token if available
    if state.get("last_token"):
        current_url = _build_url(
            oai_base,
            verb="ListRecords",
            resumptionToken=state["last_token"],
        )
        logger.info(
            "[adekus_journal] Resuming harvest from token: %s", state["last_token"]
        )
    else:
        current_url = _build_url(
            oai_base,
            verb="ListRecords",
            metadataPrefix="oai_dc",
        )

    page_num = 0

    with metadata_path.open("a", encoding="utf-8") as fout:
        while current_url:
            page_num += 1
            logger.info(
                "[adekus_journal] OAI page %d — %s", page_num, current_url[:120]
            )

            raw = _fetch(current_url, retries=4, timeout=60)
            if raw is None:
                logger.warning(
                    "[adekus_journal] Failed to fetch OAI page %d — stopping", page_num
                )
                break

            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                logger.warning(
                    "[adekus_journal] XML parse error on page %d: %s", page_num, exc
                )
                break

            # Check for OAI error element
            error_el = root.find(f".//{{{_NS_OAI}}}error")
            if error_el is None:
                error_el = root.find(".//error")
            if error_el is not None:
                code = error_el.get("code", "unknown")
                msg  = (error_el.text or "").strip()
                if code == "noRecordsMatch":
                    logger.info("[adekus_journal] noRecordsMatch — harvest complete")
                else:
                    logger.warning(
                        "[adekus_journal] OAI error on page %d: %s — %s",
                        page_num, code, msg,
                    )
                break

            # Find <ListRecords> element
            list_records = root.find(f"{{{_NS_OAI}}}ListRecords")
            if list_records is None:
                list_records = root.find("ListRecords")
            if list_records is None:
                logger.warning(
                    "[adekus_journal] No <ListRecords> on page %d — stopping", page_num
                )
                break

            new_count = 0
            for record_el in list_records.findall(f"{{{_NS_OAI}}}record"):
                if _is_deleted(record_el):
                    continue

                oai_id = _record_oai_id(record_el)
                if oai_id in seen_ids:
                    continue

                languages = _dc_values(record_el, "language")
                if not _is_dutch_or_empty(languages):
                    continue

                titles      = _dc_values(record_el, "title")
                creators    = _dc_values(record_el, "creator")
                descriptions= _dc_values(record_el, "description")
                identifiers = _dc_values(record_el, "identifier")
                dates       = _dc_values(record_el, "date")
                rights_list = _dc_values(record_el, "rights")

                article_url = _find_article_url(identifiers)
                date_str    = dates[0] if dates else ""
                language    = languages[0] if languages else ""
                rights      = rights_list[0] if rights_list else ""

                record_obj = {
                    "id":          oai_id,
                    "title":       titles[0] if titles else "",
                    "creators":    creators,
                    "description": " ".join(descriptions),
                    "identifier":  article_url,
                    "date":        date_str,
                    "language":    language,
                    "rights":      rights,
                    "acquisition_date": datetime.date.today().isoformat(),
                }
                fout.write(json.dumps(record_obj, ensure_ascii=False) + "\n")
                seen_ids.add(oai_id)
                total += 1
                new_count += 1
                _log(output_dir, article_url or oai_id, record_id=oai_id)

                # Download PDF if a direct URL is available
                pdf_url = _find_pdf_url(identifiers)
                if pdf_url:
                    safe = _safe_id(oai_id)
                    pdf_dest = pdfs_dir / f"{safe}.pdf"
                    if not pdf_dest.exists():
                        logger.info("[adekus_journal] Downloading PDF: %s", pdf_url)
                        pdf_data = _fetch(pdf_url, retries=3, timeout=90)
                        if pdf_data:
                            pdf_dest.write_bytes(pdf_data)
                            total_pdf += 1
                            logger.info(
                                "[adekus_journal] Saved PDF %s (%d KB)",
                                pdf_dest.name, len(pdf_data) // 1024,
                            )
                        else:
                            logger.warning(
                                "[adekus_journal] Could not download PDF: %s", pdf_url
                            )
                        time.sleep(_DELAY)

            logger.info(
                "[adekus_journal] Page %d: +%d records (total=%d, pdfs=%d)",
                page_num, new_count, total, total_pdf,
            )

            # Handle resumption token
            token_el = list_records.find(f"{{{_NS_OAI}}}resumptionToken")
            if token_el is None:
                token_el = list_records.find("resumptionToken")

            if token_el is not None and token_el.text and token_el.text.strip():
                next_token = token_el.text.strip()
                _save_state(state_path, next_token, total)
                current_url = _build_url(
                    oai_base,
                    verb="ListRecords",
                    resumptionToken=next_token,
                )
                time.sleep(_DELAY)
            else:
                # No more pages
                _save_state(state_path, None, total)
                current_url = ""

    # Final acquisition log summary
    _log(output_dir, oai_base, record_id="", count=total)
    logger.info(
        "[adekus_journal] Done. %d records harvested, %d PDFs downloaded.",
        total, total_pdf,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Harvest AdeKUS Academic Journal of Suriname via OAI-PMH (CC-BY 4.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
