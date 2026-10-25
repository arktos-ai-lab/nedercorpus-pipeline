"""
Download Belgian Dutch-language official gazette via daily PDF issues.

Sources:
- Belgisch Staatsblad (official gazette): ejustice.just.fgov.be

The mopdf endpoint (https://www.ejustice.just.fgov.be/mopdf/YYYY/MM/DD_N.pdf)
is the only reliably working endpoint as of 2026-03. The CGI search endpoint
(cgi_tsv/tsv_l.pl) and Justel search (cgi_loi/loi_a1.pl) both return
HTTP 500 for all requests.

Each daily PDF contains all Dutch and French publications for that day.
The parser (parse_belgian_dir) handles bilingual extraction and Dutch
paragraph filtering using langdetect.

Publication schedule: Monday–Saturday (except Belgian public holidays).
Issues per day: usually 1 (_1.pdf); occasionally 2 or 3 for large days.

License: Public Domain
  Belgian federal government publications (Art. 8, Belgian Copyright Act):
  works produced by federal public services are exempt from copyright.

Language: NL-BE (formal Belgian Dutch / Flemish legal register)

Usage:
  python -m pipeline acquire --sources belgian
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import ssl
import time
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "belgian"
LICENSE = "PD"
COUNTRY = "BE"
DOMAIN  = "legal"

HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic research; public data)",
    "Accept-Language": "nl,nl-BE;q=0.9",
}

MOPDF_BASE = "https://www.ejustice.just.fgov.be/mopdf"
MIN_PDF_BYTES = 10_000  # skip placeholder/empty PDFs


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


def _fetch_pdf(url: str, timeout: int = 60, retries: int = 3) -> bytes | None:
    """Fetch a PDF URL; return None on 404 or too-small response."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                data = resp.read()
                return data if len(data) >= MIN_PDF_BYTES else None
        except Exception as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                return None
            if attempt == retries - 1:
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _business_dates(year_from: int, year_to: int) -> list[datetime.date]:
    """
    Yield calendar dates Mon–Sat from year_from to year_to inclusive.

    The Belgisch Staatsblad is published Mon–Sat; Sunday and some Belgian
    holidays have no issue. Skipping Sundays is sufficient — missing holidays
    just results in 404 (silently skipped).
    """
    start = datetime.date(year_from, 1, 1)
    end   = datetime.date(year_to, 12, 31)
    dates = []
    cur   = start
    while cur <= end:
        if cur.weekday() < 6:  # Mon=0 … Sat=5
            dates.append(cur)
        cur += datetime.timedelta(days=1)
    return dates


def download_staatsblad(
    output_dir: Path,
    year_from: int = 1990,
    year_to: int | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download daily Belgisch Staatsblad PDFs from the mopdf endpoint.

    Saves PDFs under staatsblad/YYYY/YYYYMMDD_N.pdf.
    Resume: skips dates where YYYYMMDD_1.pdf already exists.
    """
    year_to     = year_to or datetime.date.today().year
    sb_dir      = output_dir / "staatsblad"
    sb_dir.mkdir(parents=True, exist_ok=True)

    all_dates   = _business_dates(year_from, year_to)
    saved_pdfs  = 0
    skipped     = 0

    logger.info(
        "[belgian] staatsblad: %d business days %d–%d to scan",
        len(all_dates), year_from, year_to,
    )

    for date in all_dates:
        year_dir = sb_dir / str(date.year)
        year_dir.mkdir(exist_ok=True)

        # Resume check: if _1.pdf exists (even 0-byte = 404 on that date), skip
        marker = year_dir / f"{date.strftime('%Y%m%d')}_1.pdf"
        marker_404 = year_dir / f"{date.strftime('%Y%m%d')}.404"
        if marker.exists() or marker_404.exists():
            skipped += 1
            continue

        if dry_run:
            logger.info("[dry-run] would fetch %s", date.isoformat())
            continue

        # Try issue numbers _1 through _3
        found_any = False
        for issue_n in range(1, 4):
            url = (
                f"{MOPDF_BASE}/{date.year}/{date.month:02d}"
                f"/{date.day:02d}_{issue_n}.pdf"
            )
            pdf_data = _fetch_pdf(url)
            if pdf_data is None:
                break  # no more issues for this date

            dest = year_dir / f"{date.strftime('%Y%m%d')}_{issue_n}.pdf"
            dest.write_bytes(pdf_data)
            dest.with_suffix(".pdf.url").write_text(url, encoding="utf-8")
            _log(output_dir, url, "staatsblad")
            saved_pdfs += 1
            found_any = True
            time.sleep(0.3)

        if not found_any:
            # Mark as confirmed 404 so we don't re-try on next run
            marker_404.touch()

        # Brief pause between dates
        time.sleep(0.1)

        if saved_pdfs % 100 == 0 and saved_pdfs > 0:
            logger.info("[belgian] staatsblad: %d PDFs saved so far", saved_pdfs)

    logger.info(
        "[belgian] staatsblad done: %d PDFs saved, %d dates skipped (already done)",
        saved_pdfs, skipped,
    )


def download(
    output_dir: Path,
    year_from: int = 1990,
    dry_run: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    download_staatsblad(output_dir, year_from=year_from, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Belgisch Staatsblad daily PDFs (public domain)"
    )
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--year-from",  type=int, default=1990)
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(args.output, year_from=args.year_from, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
