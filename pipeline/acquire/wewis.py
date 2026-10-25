"""
Download Vlaamse overheid policy documents from the WEWIS open data API.

WEWIS (Wegwijzer Welzijn en Samenleving) is published by the Departement
Werk en Sociale Economie of the Vlaamse overheid. The dataset
"lijst-beleidsdocumenten" lists policy documents with metadata and, where
available, direct PDF links.

API: Opendatasoft v2.1 at opendata.wewis.vlaanderen.be
Fallback: Opendatasoft v1 API (same platform, older path)
License: Open data Vlaamse overheid (CC0 equivalent)
Country: NL-BE
Domain: government

Strategy:
  1. Paginate the catalog dataset endpoint (v2.1) at limit=100 / offset=N.
     Fallback to v1 /records/1.0/search/ if v2.1 returns no results.
  2. Save all metadata to wewis_metadata.jsonl (skip already-seen records).
  3. Download any PDF linked from a record to pdfs/{safe_id}.pdf.
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
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "wewis"
LICENSE = "CC0"
COUNTRY = "NL-BE"
DOMAIN  = "government"

_BASE_V2   = "https://opendata.wewis.vlaanderen.be/api/explore/v2.1/catalog/datasets/lijst-beleidsdocumenten/records"
_BASE_V1   = "https://opendata.wewis.vlaanderen.be/api/records/1.0/search/"
_DATASET   = "lijst-beleidsdocumenten"
_USER_AGENT = "NederCorpus/0.1 (academic research; Vlaamse overheid open data NL-BE)"
_DELAY      = 0.3   # seconds between requests
_PAGE_SIZE  = 100

# Fields that may carry the document title (try in order)
_TITLE_FIELDS = ("naam", "titel", "title")
# Fields that may carry a PDF / document URL
_URL_FIELDS   = ("url", "document_url", "link", "bestand", "bijlage")
# Fields that may carry a description
_DESC_FIELDS  = ("omschrijving", "beschrijving", "description")
# Fields that may carry a date
_DATE_FIELDS  = ("datum", "date", "publicatiedatum")
# Fields that may carry document type
_TYPE_FIELDS  = ("type_document", "type", "soort")


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                logger.debug("[wewis] HTTP %d for %s", exc.code, url)
                return None
            if attempt == retries - 1:
                logger.warning("[wewis] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[wewis] Failed %s: %s", url, exc)
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


def _safe_filename(name: str, max_len: int = 80) -> str:
    """Derive a filesystem-safe filename stem from a record name/id."""
    safe = re.sub(r"[/\\:*?\"<>|]", "_", name)
    safe = re.sub(r"\s+", "_", safe).strip("_")
    return safe[:max_len] if safe else "record"


def _first_value(record: dict, field_names: tuple[str, ...]) -> str:
    for key in field_names:
        val = record.get(key)
        if val:
            return str(val).strip()
    return ""


def _find_pdf_url(record: dict) -> str | None:
    """Return a PDF URL from the record if one exists."""
    for key in _URL_FIELDS:
        val = record.get(key)
        if not val:
            continue
        val = str(val).strip()
        if val.startswith("http") and val.lower().endswith(".pdf"):
            return val
    # Also check all string values for PDF links as a fallback
    for val in record.values():
        if isinstance(val, str) and val.startswith("http") and ".pdf" in val.lower():
            return val
    return None


def _fetch_page_v2(offset: int) -> tuple[int, list[dict]]:
    """Fetch one page from the v2.1 API. Returns (total_count, results)."""
    params = urllib.parse.urlencode({
        "limit":  _PAGE_SIZE,
        "offset": offset,
    })
    url  = f"{_BASE_V2}?{params}"
    data = _fetch(url)
    if data is None:
        return 0, []
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return 0, []
    total   = obj.get("total_count", 0)
    results = obj.get("results", [])
    return total, results


def _fetch_page_v1(start: int) -> tuple[int, list[dict]]:
    """Fetch one page from the v1 API (fallback). Returns (total_count, records)."""
    params = urllib.parse.urlencode({
        "dataset": _DATASET,
        "rows":    _PAGE_SIZE,
        "start":   start,
    })
    url  = f"{_BASE_V1}?{params}"
    data = _fetch(url)
    if data is None:
        return 0, []
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return 0, []
    total   = obj.get("nhits", 0)
    # v1 wraps each record in {"recordid": ..., "fields": {...}}
    records = [r.get("fields", r) for r in obj.get("records", [])]
    return total, records


def download(
    output_dir: Path,
    max_records: int = 50_000,
    dry_run: bool = False,
    **kwargs,
) -> None:
    """
    Download WEWIS beleidsdocumenten metadata and PDFs.

    Saves:
      wewis_metadata.jsonl   — one JSON line per record
      pdfs/{safe_id}.pdf     — PDFs where available
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir = output_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)
    meta_path = output_dir / "wewis_metadata.jsonl"
    acq_date  = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[wewis] [dry-run] would fetch %s", _BASE_V2)
        return

    # Load already-seen titles to allow resuming
    seen_names: set[str] = set()
    if meta_path.exists():
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen_names.add(json.loads(line)["naam"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_names:
            logger.info("[wewis] Resuming — %d records already saved", len(seen_names))

    total_meta = len(seen_names)
    total_pdf  = sum(1 for _ in pdfs_dir.glob("*.pdf"))
    use_v1     = False   # will flip to True if v2 yields nothing

    # Probe v2 API
    probe_total, probe_results = _fetch_page_v2(0)
    if probe_total == 0 and not probe_results:
        logger.info("[wewis] v2.1 API returned no results; trying v1 fallback")
        use_v1 = True

    with meta_path.open("a", encoding="utf-8") as fout:
        offset = 0
        while offset < max_records:
            if use_v1:
                total_count, results = _fetch_page_v1(offset)
            else:
                total_count, results = _fetch_page_v2(offset)

            if not results:
                logger.info("[wewis] No more results at offset %d", offset)
                break

            new_count = 0
            for record in results:
                naam = _first_value(record, _TITLE_FIELDS) or f"record_{total_meta}"

                if naam in seen_names:
                    continue

                omschrijving = _first_value(record, _DESC_FIELDS)
                datum        = _first_value(record, _DATE_FIELDS)
                type_doc     = _first_value(record, _TYPE_FIELDS)
                doc_url      = _first_value(record, _URL_FIELDS)

                row = {
                    "naam":           naam,
                    "omschrijving":   omschrijving,
                    "datum":          datum,
                    "type_document":  type_doc,
                    "url":            doc_url,
                    "raw":            record,
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                seen_names.add(naam)
                total_meta += 1
                new_count  += 1

                # Download PDF if available
                pdf_url = _find_pdf_url(record)
                if pdf_url:
                    safe_id  = _safe_filename(naam)
                    pdf_dest = pdfs_dir / f"{safe_id}.pdf"
                    if not pdf_dest.exists():
                        pdf_data = _fetch(pdf_url, timeout=120)
                        if pdf_data and pdf_data[:4] == b"%PDF":
                            pdf_dest.write_bytes(pdf_data)
                            _log(output_dir, pdf_url, naam)
                            total_pdf += 1
                            logger.info("[wewis] PDF saved: %s", pdf_dest.name)
                        elif pdf_data:
                            logger.debug("[wewis] Not a PDF at %s", pdf_url)
                        time.sleep(_DELAY)

            logger.info(
                "[wewis] offset=%d total_count=%d +%d records (meta=%d pdfs=%d)",
                offset, total_count, new_count, total_meta, total_pdf,
            )

            offset += len(results)
            if offset >= total_count:
                break

            time.sleep(_DELAY)

    logger.info("[wewis] Done. %d metadata records, %d PDFs.", total_meta, total_pdf)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download WEWIS beleidsdocumenten metadata and PDFs (CC0, NL-BE)"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--max-records", type=int, default=50_000)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    download(args.output, max_records=args.max_records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
