"""
Download BES islands (Bonaire, Sint Eustatius, Saba) official publications
via the overheid.nl SRU endpoint — same infrastructure as staatscourant.py.

The BES islands (Caribisch Nederland) are special municipalities of the
Netherlands. Their official publications appear in overheid.nl under
product areas: ab (Afkondigingsblad BES) and blad (Eilandsblad).

License: Public Domain
  Dutch government publications have no copyright under Article 11 of
  the Dutch Copyright Act (Auteurswet). BES island regulations are enacted
  by the Netherlands and fall under the same open data policy.

Language variant: NL (formal Netherlands Dutch with Caribbean legal context)

SRU endpoint: https://repository.overheid.nl/sru
Product areas:
  ab   — Afkondigingsblad (BES islands combined official gazette)
  blad — Eilandsblad Bonaire / Sint Eustatius / Saba (local ordinances)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "bes_bekendmakingen"
LICENSE = "PD"
COUNTRY = "NL"  # BES is NL municipality
DOMAIN  = "government"

SRU_BASE = "https://repository.overheid.nl/sru"

NS = {
    "srw": "http://www.loc.gov/zing/srw/",
    "gzd": "http://standaarden.overheid.nl/sru",
}

# Product areas covering BES island publications
PRODUCT_AREAS = [
    ("ab",   "Afkondigingsblad BES"),     # Combined official gazette for all BES islands
    ("blad", "Eilandsblad"),               # Individual island council announcements
]


def _log(output_dir: Path, url: str, sub_source: str = "") -> None:
    entry = {
        "source": f"{SOURCE}:{sub_source}" if sub_source else SOURCE,
        "url": url,
        "license": LICENSE,
        "country": COUNTRY,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.1fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _sru_url(product_area: str, start: int, max_records: int) -> str:
    # NOTE: c.product-area==X returns 0 results on overheid.nl SRU 2.0.
    # The correct query is dt.identifier any X (confirmed working for stcrt, gmb etc.)
    params = {
        "operation":      "searchRetrieve",
        "version":        "2.0",
        "query":          f"dt.identifier any {product_area}",
        "startRecord":    str(start),
        "maximumRecords": str(max_records),
        "recordSchema":   "gzd",
    }
    return f"{SRU_BASE}?{urllib.parse.urlencode(params)}"


def _extract_records(xml_bytes: bytes) -> tuple[list[dict], int]:
    """
    Parse an SRU response and extract record metadata.

    Returns (records_list, total_results).
    Each record dict has: identifier, title, date, url, creator.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("SRU XML parse error: %s", exc)
        return [], 0

    ns_dc  = "http://purl.org/dc/elements/1.1/"
    ns_ovl = "http://standaarden.overheid.nl/owms/terms/"

    # Namespace-agnostic lookup (handles both SRU 1.1 and 2.0)
    total_el = root.find(".//{*}numberOfRecords")
    total = int(total_el.text) if total_el is not None and total_el.text else 0

    records: list[dict] = []
    for rec in root.findall(".//{*}recordData"):
        r: dict = {}

        # Identifier / FRBR URL
        id_el = rec.find(f".//{{{ns_dc}}}identifier")
        if id_el is not None:
            r["identifier"] = (id_el.text or "").strip()

        # Title
        title_el = rec.find(f".//{{{ns_dc}}}title")
        if title_el is not None:
            r["title"] = (title_el.text or "").strip()

        # Publication date
        date_el = rec.find(f".//{{{ns_dc}}}date")
        if date_el is not None:
            r["date"] = (date_el.text or "").strip()

        # Creator (island / ministry)
        creator_el = rec.find(f".//{{{ns_dc}}}creator")
        if creator_el is not None:
            r["creator"] = (creator_el.text or "").strip()

        # Source URL (preferred URL from FRBR record)
        url_el = rec.find(f".//{{{ns_ovl}}}sourceUrl")
        if url_el is None:
            url_el = rec.find(f".//{{{ns_dc}}}source")
        if url_el is not None:
            r["url"] = (url_el.text or "").strip()

        if r.get("identifier") or r.get("url"):
            records.append(r)

    return records, total


def _download_full_text(record: dict, dest_dir: Path) -> Path | None:
    """
    Download the full-text XML for a single BES publication record.

    The overheid.nl FRBR tree stores the actual document XML at:
      https://repository.overheid.nl/frbr/officielepublicaties/{product-area}/{id}/1/xml/{id}.xml
    Falls back to the record URL if FRBR path isn't available.
    """
    identifier = record.get("identifier", "")
    doc_url = record.get("url", "")

    # Try FRBR XML URL first
    if identifier:
        # Strip to just the last component (document ID)
        doc_id = identifier.rstrip("/").split("/")[-1]
        safe_name = doc_id.replace("/", "_").replace(":", "_")
        dest = dest_dir / f"{safe_name}.xml"
        if dest.exists():
            return dest

        frbr_url = f"https://repository.overheid.nl/frbr/officielepublicaties/{doc_id}/1/xml/{doc_id}.xml"
        try:
            data = _fetch(frbr_url, timeout=20)
            dest.write_bytes(data)
            dest.with_suffix(".url").write_text(frbr_url, encoding="utf-8")
            return dest
        except Exception:
            pass  # fall through to doc_url

    # Fallback: try the doc_url directly
    if doc_url and doc_url.startswith("http"):
        safe_name = urllib.parse.quote(doc_url, safe="")[-120:]
        dest = dest_dir / f"{safe_name[:80]}.xml"
        if dest.exists():
            return dest
        try:
            data = _fetch(doc_url, timeout=20)
            dest.write_bytes(data)
            dest.with_suffix(".url").write_text(doc_url, encoding="utf-8")
            return dest
        except Exception as exc:
            logger.debug("Could not download %s: %s", doc_url, exc)

    return None


def download_product_area(
    output_dir: Path,
    product_area: str,
    label: str,
    max_records: int = 50_000,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    """
    Download all BES publications for one SRU product area.
    Saves metadata JSONL + individual XML full-texts.
    """
    area_dir = output_dir / product_area
    area_dir.mkdir(parents=True, exist_ok=True)
    xml_dir = area_dir / "xml"
    xml_dir.mkdir(exist_ok=True)
    meta_path = area_dir / "metadata.jsonl"

    start = 0
    fetched = 0

    while fetched < max_records:
        url = _sru_url(product_area, start + 1, page_size)
        logger.info("[bes:%s] records %d–%d — %s", product_area, start, start + page_size, url)

        if dry_run:
            logger.info("[dry-run] skipping")
            return

        try:
            data = _fetch(url)
        except Exception as exc:
            logger.error("SRU fetch failed: %s", exc)
            break

        _log(output_dir, url, product_area)
        records, total = _extract_records(data)

        if not records:
            logger.info("[bes:%s] No more records (total=%d fetched=%d)", product_area, total, fetched)
            break

        with meta_path.open("a", encoding="utf-8") as mf:
            for rec in records:
                mf.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # Phase 2: fetch full-text XML for each record
        for rec in records:
            _download_full_text(rec, xml_dir)
            time.sleep(0.2)

        fetched += len(records)
        start   += page_size
        logger.info("[bes:%s] %d/%d records processed", product_area, fetched, total)

        if fetched >= total:
            break

        time.sleep(1.0)


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download all BES island official publications."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for product_area, label in PRODUCT_AREAS:
        download_product_area(output_dir, product_area, label, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download BES island official publications (overheid.nl SRU)"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--product-area", default=None,
                        help="Single product area (ab or blad); default: all")
    parser.add_argument("--max-records", type=int, default=50_000)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.product_area:
        areas = [(pa, lbl) for pa, lbl in PRODUCT_AREAS if pa == args.product_area]
    else:
        areas = PRODUCT_AREAS

    for product_area, label in areas:
        download_product_area(output_dir, product_area, label,
                              max_records=args.max_records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
