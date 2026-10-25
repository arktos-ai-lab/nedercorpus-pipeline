"""
Download Curaçao Publicatieblad (P.B.) — official gazette of Curaçao.

The Publicatieblad van Curaçao (P.B.) is the official gazette containing:
  - Landsverordeningen (country ordinances)
  - Landsbesluiten, houdende algemene maatregelen (general measures)
  - Ministerial regulations (Ministeriële regelingen)
  - Official announcements and decrees

Curaçao became an autonomous country within the Kingdom of the Netherlands
on 10 October 2010 (10-10-10). Publications before that date were under
the Netherlands Antilles (P.B. N.A.).

Sources:
  1. gobiernu.cw — current P.B. issues (PDF downloads)
     URL: https://gobiernu.cw/nl/themas/wet-regelgeving/
  2. rechtspraak.cw / overheid.nl — consolidated legislation
  3. Internet Archive — historical P.B. Netherlands Antilles

License: Public Domain
  Curaçao government publications are government works exempt from copyright
  under local intellectual property law derived from the Dutch model.

Language variant: NL (Curaçao formal Dutch with Papiamentu administrative terms)
  The official text of P.B. publications is in Dutch; Papiamentu is used
  for some local government communications but not in the P.B. itself.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "publicatieblad_curacao"
LICENSE = "PD"
COUNTRY = "CW"
DOMAIN  = "government"

HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic research; public data)",
    "Accept-Language": "nl,nl-CW;q=0.9,pap;q=0.8,en;q=0.7",
}

GOBIERNU_BASE    = "https://gobiernu.cw"
GOBIERNU_WP_API  = "https://gobiernu.cw/wp-json/wp/v2/media"
ARCHIVE_BASE     = "https://archive.org"
ARCHIVE_API      = "https://archive.org/advancedsearch.php"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


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
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Source 1: gobiernu.cw — current publications via WordPress media API
# ---------------------------------------------------------------------------

def _wp_media_page(page: int, per_page: int = 100) -> tuple[list[dict], int]:
    """
    Fetch one page of PDF media items from the gobiernu.cw WordPress REST API.

    Returns (items, total_pages).
    gobiernu.cw switched to WordPress; old HTML scraping URLs return 404.
    The WP media API exposes all uploaded PDFs (8000+) with title, date, and URL.
    """
    params = urllib.parse.urlencode({
        "per_page": per_page,
        "mime_type": "application/pdf",
        "page": page,
    })
    url = f"{GOBIERNU_WP_API}?{params}"
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            items = json.loads(resp.read())
            total = int(resp.headers.get("X-WP-TotalPages", 1))
            return items, total
    except Exception as exc:
        logger.warning("[publicatieblad_curacao] WP media API page %d failed: %s", page, exc)
        return [], 0


def download_gobiernu(
    output_dir: Path,
    year_from: int = 2010,
    year_to: int | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download Curaçao government PDFs from gobiernu.cw via WordPress media REST API.

    gobiernu.cw is a WordPress site with 8000+ PDFs accessible through the WP
    media API. Downloads all PDFs into gobiernu/{year}/ subdirectories.
    All Curaçao government publications are Public Domain.
    """
    year_to = year_to or datetime.date.today().year
    dest = output_dir / "gobiernu"
    dest.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[publicatieblad_curacao] [dry-run] would query gobiernu.cw WP media API")
        return

    # Probe first page to confirm API is reachable
    items, total_pages = _wp_media_page(1)
    if not items:
        logger.warning(
            "[publicatieblad_curacao] gobiernu.cw WP media API returned no items."
            " Site may be unreachable. Skipping gobiernu download."
        )
        return

    logger.info(
        "[publicatieblad_curacao] gobiernu.cw WP media API: %d pages of PDFs",
        total_pages,
    )
    _log(output_dir, GOBIERNU_WP_API, "gobiernu:wp_api")

    total_saved = 0
    for page in range(1, total_pages + 1):
        if page > 1:
            items, _ = _wp_media_page(page)
        if not items:
            break

        for item in items:
            pdf_url = item.get("source_url", "")
            if not pdf_url:
                continue

            # Extract year from upload date (ISO format: "2025-11-24T...")
            date_str = item.get("date", "")
            try:
                year = int(date_str[:4])
            except (ValueError, IndexError):
                year = datetime.date.today().year

            if year < year_from or year > year_to:
                continue

            # Derive filename from URL
            raw_name = Path(urllib.parse.urlparse(pdf_url).path).name
            safe_name = re.sub(r"[^\w\-.]", "_", raw_name)[:120]
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"

            year_dir = dest / str(year)
            year_dir.mkdir(exist_ok=True)
            pdf_dest = year_dir / safe_name

            if pdf_dest.exists():
                continue

            try:
                pdf_data = _fetch(pdf_url, timeout=60)
                if len(pdf_data) < 500:
                    continue
                pdf_dest.write_bytes(pdf_data)
                pdf_dest.with_suffix(".url").write_text(pdf_url, encoding="utf-8")
                total_saved += 1
                if total_saved % 100 == 0:
                    logger.info(
                        "[publicatieblad_curacao] gobiernu: %d PDFs saved (page %d/%d)",
                        total_saved, page, total_pages,
                    )
            except Exception as exc:
                logger.debug("[publicatieblad_curacao] Skip %s: %s", safe_name, exc)

            time.sleep(0.5)

        time.sleep(0.3)

    logger.info("[publicatieblad_curacao] gobiernu: %d PDFs saved total", total_saved)


# ---------------------------------------------------------------------------
# Source 2: Internet Archive — historical Netherlands Antilles P.B.
# ---------------------------------------------------------------------------

def _ia_search_na(query: str, page: int, rows: int = 100) -> dict:
    """Search Internet Archive for Netherlands Antilles Publicatieblad."""
    parts = [
        f"q={urllib.parse.quote(query)}",
        "fl[]=identifier",
        "fl[]=title",
        "fl[]=year",
        "fl[]=language",
        f"rows={rows}",
        f"page={page + 1}",
        "output=json",
    ]
    url = f"{ARCHIVE_API}?" + "&".join(parts)
    data = _fetch(url, timeout=30)
    return json.loads(data)


def download_historical(
    output_dir: Path,
    max_items: int = 1_000,
    dry_run: bool = False,
) -> None:
    """
    Download historical Netherlands Antilles Publicatieblad from Internet Archive.
    These are pre-2010 publications covering the Netherlands Antilles period.
    Historical government publications are public domain.
    """
    dest = output_dir / "historical_na"
    dest.mkdir(parents=True, exist_ok=True)

    # Search for Netherlands Antilles official gazette items
    queries = [
        "Publicatieblad Netherlands Antilles",
        "Publicatieblad Nederlandse Antillen",
    ]

    for query in queries:
        page    = 0
        fetched = 0

        while fetched < max_items // len(queries):
            if dry_run:
                logger.info("[dry-run] skipping IA search")
                break

            try:
                result = _ia_search_na(query, page)
            except Exception as exc:
                logger.warning("IA search failed: %s", exc)
                break

            docs = result.get("response", {}).get("docs", [])
            if not docs:
                break

            for item in docs:
                identifier = item.get("identifier", "")
                if not identifier:
                    continue

                # Try to get text
                text_url = f"{ARCHIVE_BASE}/download/{identifier}/{identifier}_djvu.txt"
                dest_file = dest / f"{identifier}.txt"
                if not dest_file.exists():
                    try:
                        data = _fetch(text_url, timeout=30)
                        if len(data) > 200:
                            dest_file.write_bytes(data)
                            dest_file.with_suffix(".url").write_text(text_url, encoding="utf-8")
                            _log(output_dir, f"{ARCHIVE_BASE}/details/{identifier}", "historical_na")
                    except Exception:
                        pass

                time.sleep(0.3)

            fetched += len(docs)
            page    += 1
            time.sleep(1.0)


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    year_from: int = 2010,
    dry_run: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    download_gobiernu(output_dir, year_from=year_from, dry_run=dry_run)
    # Internet Archive historical source always runs regardless of gobiernu.cw status.
    # It covers Netherlands Antilles P.B. (pre-2010) and serves as fallback
    # for recent issues when gobiernu.cw is unreachable.
    download_historical(output_dir, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Curaçao Publicatieblad (official gazette, public domain)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--year-from", type=int, default=2010)
    parser.add_argument("--sources",   nargs="+", default=["gobiernu", "historical"],
                        choices=["gobiernu", "historical"])
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    if "gobiernu" in args.sources:
        download_gobiernu(output_dir, year_from=args.year_from, dry_run=args.dry_run)
    if "historical" in args.sources:
        download_historical(output_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
