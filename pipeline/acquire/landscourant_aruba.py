"""
Download Landscourant van Aruba (official gazette of Aruba).

The Landscourant is the official gazette of Aruba, containing:
  - Landsverordeningen (country ordinances)
  - Landsbesluiten (country decrees)
  - Ministerial announcements
  - Public notices

Sources:
  1. gobierno.aw — Current issues (per-year pages with PDF links)
  2. Internet Archive — Historical issues via DWJZ collection
     Collection pattern: DWJZ-LC{YEAR}No.{NN}

License: Public Domain
  Aruban government documents are government works not subject to copyright
  under Aruban intellectual property law (based on Dutch model).

Language variant: NL-AW (Aruban Dutch); some sections in Papiamento.
  The Dutch sections constitute the legally binding text.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "landscourant_aruba"
LICENSE = "PD"
COUNTRY = "AW"
DOMAIN  = "government"

HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic research; public data)",
    "Accept-Language": "nl,nl-AW;q=0.9,en;q=0.8",
}

GOBIERNO_BASE   = "https://www.gobierno.aw"
ARCHIVE_BASE    = "https://archive.org"
ARCHIVE_API     = "https://archive.org/advancedsearch.php"


def _log(output_dir: Path, url: str, sub: str = "") -> None:
    entry = {
        "source":           f"{SOURCE}:{sub}" if sub else SOURCE,
        "url":              url,
        "license":          LICENSE,
        "country":          COUNTRY,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Source 1: gobierno.aw — current issues
# ---------------------------------------------------------------------------

def _gobierno_year_url(year: int) -> str:
    return f"{GOBIERNO_BASE}/en/official-gazettes-{year}"


def _extract_pdf_links(html: str, base_url: str) -> list[str]:
    """Extract PDF links from gobierno.aw official gazette page."""
    links: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']*\.pdf)["\']', html, re.IGNORECASE):
        raw = m.group(1)
        full = urllib.parse.urljoin(base_url, raw)
        if full not in links:
            links.append(full)
    return links


def download_gobierno(
    output_dir: Path,
    year_from: int = 2015,
    year_to: int | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download Landscourant PDFs from gobierno.aw per-year listing pages.
    """
    year_to = year_to or datetime.date.today().year
    dest = output_dir / "gobierno"
    dest.mkdir(parents=True, exist_ok=True)

    for year in range(year_from, year_to + 1):
        url = _gobierno_year_url(year)
        logger.info("[landscourant_aruba:gobierno] year=%d — %s", year, url)

        if dry_run:
            logger.info("[dry-run] skipping")
            continue

        try:
            html = _fetch(url).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Failed to fetch year %d listing: %s", year, exc)
            time.sleep(2.0)
            continue

        _log(output_dir, url, "gobierno")

        pdf_links = _extract_pdf_links(html, url)
        logger.info("  Found %d PDF links for %d", len(pdf_links), year)

        year_dir = dest / str(year)
        year_dir.mkdir(exist_ok=True)

        for pdf_url in pdf_links:
            name = Path(urllib.parse.urlparse(pdf_url).path).name
            if not name.endswith(".pdf"):
                name += ".pdf"
            pdf_dest = year_dir / name
            if pdf_dest.exists():
                continue

            try:
                pdf_data = _fetch(pdf_url, timeout=60)
                pdf_dest.write_bytes(pdf_data)
                pdf_dest.with_suffix(".url").write_text(pdf_url, encoding="utf-8")
                _log(output_dir, pdf_url, f"gobierno:{year}")
                logger.info("  Saved %s/%s (%d bytes)", year, name, len(pdf_data))
            except Exception as exc:
                logger.warning("  Failed %s: %s", pdf_url, exc)

            time.sleep(0.5)

        time.sleep(1.5)


# ---------------------------------------------------------------------------
# Source 2: Internet Archive — historical issues (DWJZ collection)
# ---------------------------------------------------------------------------

def _ia_search_url(query: str, page: int = 0, rows: int = 200) -> str:
    params = {
        "q":      query,
        "fl[]":   ["identifier", "title", "year", "description"],
        "rows":   rows,
        "page":   page + 1,
        "output": "json",
    }
    # urllib doesn't handle list params well; build manually
    base = f"{ARCHIVE_API}?"
    parts = [
        f"q={urllib.parse.quote(query)}",
        "fl[]=identifier",
        "fl[]=title",
        "fl[]=year",
        "fl[]=description",
        f"rows={rows}",
        f"page={page + 1}",
        "output=json",
    ]
    return base + "&".join(parts)


def _ia_download_text(identifier: str, dest_dir: Path) -> bool:
    """
    Download OCR text file for an Internet Archive item.
    IA text files follow: https://archive.org/download/{id}/{id}_djvu.txt
    or {id}_text.pdf or various naming patterns.
    """
    text_url = f"{ARCHIVE_BASE}/download/{identifier}/{identifier}_djvu.txt"
    dest = dest_dir / f"{identifier}.txt"
    if dest.exists():
        return True

    try:
        data = _fetch(text_url, timeout=30)
        dest.write_bytes(data)
        dest.with_suffix(".url").write_text(text_url, encoding="utf-8")
        return True
    except Exception:
        pass

    # Fallback: try _text.txt
    text_url2 = f"{ARCHIVE_BASE}/download/{identifier}/{identifier}_text.txt"
    try:
        data = _fetch(text_url2, timeout=30)
        dest.write_bytes(data)
        dest.with_suffix(".url").write_text(text_url2, encoding="utf-8")
        return True
    except Exception:
        return False


def download_archive(
    output_dir: Path,
    collection: str = "bibliotecanacionalaruba",
    query_extra: str = "landscourant",
    max_items: int = 5_000,
    dry_run: bool = False,
) -> None:
    """
    Download Landscourant van Aruba items from Internet Archive.

    Searches the Biblioteca Nacional Aruba collection for Landscourant issues
    and downloads OCR text files.
    """
    dest = output_dir / "archive"
    dest.mkdir(parents=True, exist_ok=True)

    full_query = f"collection:{collection} AND {query_extra}"
    page = 0
    fetched = 0

    while fetched < max_items:
        url = _ia_search_url(full_query, page)
        logger.info("[landscourant_aruba:archive] page=%d — %s", page, url)

        if dry_run:
            logger.info("[dry-run] skipping")
            return

        try:
            data = _fetch(url, timeout=30)
            result = json.loads(data)
        except Exception as exc:
            logger.warning("IA search failed: %s", exc)
            break

        items = result.get("response", {}).get("docs", [])
        if not items:
            break

        for item in items:
            identifier = item.get("identifier", "")
            if not identifier:
                continue

            meta = {
                "identifier":  identifier,
                "title":       item.get("title", ""),
                "year":        item.get("year", ""),
                "description": item.get("description", ""),
                "source":      SOURCE,
                "license":     LICENSE,
            }
            meta_path = dest / f"{identifier}.json"
            if not meta_path.exists():
                meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            _ia_download_text(identifier, dest)
            _log(output_dir, f"{ARCHIVE_BASE}/details/{identifier}", "archive")
            time.sleep(0.3)

        fetched += len(items)
        page += 1
        time.sleep(1.0)

    logger.info("[landscourant_aruba:archive] Done. Fetched %d items.", fetched)


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    year_from: int = 2000,
    dry_run: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    download_gobierno(output_dir, year_from=year_from, dry_run=dry_run)
    download_archive(output_dir, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Landscourant van Aruba (official gazette, public domain)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--year-from", type=int, default=2000)
    parser.add_argument("--sources",   nargs="+", default=["gobierno", "archive"],
                        choices=["gobierno", "archive"])
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    if "gobierno" in args.sources:
        download_gobierno(output_dir, year_from=args.year_from, dry_run=args.dry_run)
    if "archive" in args.sources:
        download_archive(output_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
