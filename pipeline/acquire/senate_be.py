"""
Download Belgian Senate (Senaat/Sénat) proceedings in Dutch.

The Belgian Senate was reformed in 2014 from a permanently sitting assembly
to a non-permanent "Senate of the Regions". Pre-2014 proceedings are rich
legislative records; post-2014 proceedings cover constitutional revision,
regional coordination, and international matters.

Sources:
  1. senate.be — full plenary session records (handelingen/annalen)
     Available as HTML and PDF per legislature/session.
  2. BelgiumLex preparatory works — legislative history documents

License: Public Domain
  Belgian federal government publications are public domain (Art. 8, Auteurswet).

Language variant: NL-BE (formal Belgian Dutch / Flemish legislative register)

Note: The Senate is bilingual (Dutch/French). We download only Dutch-language
sections. The proceedings HTML uses language markers to separate NL/FR content.
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

SOURCE  = "senate_be"
LICENSE = "PD"
COUNTRY = "BE"
DOMAIN  = "parliamentary"

HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic research; public data)",
    "Accept-Language": "nl,nl-BE;q=0.9",
}

SENATE_BASE = "https://www.senate.be"

# Legislature numbers and their approximate years
# Senate legislatures (zittingsperiodes)
LEGISLATURES: list[tuple[int, int, int]] = [
    # (legislature_number, start_year, end_year)
    (4,  1995, 1999),
    (5,  1999, 2003),
    (6,  2003, 2007),
    (7,  2007, 2010),
    (8,  2010, 2014),
    (9,  2014, 2019),   # reduced Senate (post-reform)
    (10, 2019, 2024),
    (11, 2024, 2029),
]

# URL patterns for Senate proceedings
# Handelingen: plenary session verbatim records
# The senate.be URL structure changed; try both old and new patterns.
# Old pattern (pre-2023): /nl/vergadering/plenaire-vergadering/handelingen/
# New pattern (post-2023): /nl/activiteiten/plenaire-vergadering/
_PLENARY_LIST_URL = (
    "{base}/nl/vergadering/plenaire-vergadering/handelingen"
    "?legislatuur={leg}&paginanummer={page}"
)
_PLENARY_LIST_URL_ALT = (
    "{base}/nl/activiteiten/plenaire-vergadering"
    "?legislatuur={leg}&page={page}"
)

# Individual session URL: /nl/vergadering/plenaire-vergadering/handelingen/{id}
_SESSION_DOC_URL = "{base}/nl/vergadering/plenaire-vergadering/handelingen/{doc_id}"


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


def _extract_session_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """
    Extract (session_id, url) pairs from a Senate plenary proceedings list page.
    Returns list of (identifier, full_url).

    Handles multiple URL patterns observed on senate.be:
      - /nl/vergadering/plenaire-vergadering/handelingen/{id}   (classic)
      - /nl/activiteiten/plenaire-vergadering/{id}              (post-2023)
      - /nl/vergadering/plenaire-vergadering/{id}               (variant)
    """
    results: list[tuple[str, str]] = []
    seen_paths: set[str] = set()

    # Broad pattern: any /nl/ path containing "plenaire-vergadering" or "handelingen"
    # followed by a slug that looks like a session identifier (digits/hyphens).
    patterns = [
        # Classic: /nl/vergadering/plenaire-vergadering/handelingen/{id}
        r'href=["\'](/nl/vergadering/plenaire-vergadering/handelingen/([\w\-]+))["\']',
        # Post-2023: /nl/activiteiten/plenaire-vergadering/{id}
        r'href=["\'](/nl/activiteiten/plenaire-vergadering/([\w\-]+))["\']',
        # Variant without "handelingen" segment
        r'href=["\'](/nl/vergadering/plenaire-vergadering/([\w\-]+))["\']',
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, html, re.IGNORECASE):
            path       = m.group(1)
            session_id = m.group(2)
            # Skip listing/navigation pages (contain query params)
            if "?" in path:
                continue
            # Skip generic segment names that are not session identifiers
            if session_id.lower() in ("handelingen", "plenaire-vergadering", "vergadering",
                                       "activiteiten", "nl"):
                continue
            full_url = urllib.parse.urljoin(base_url, path)
            if path not in seen_paths:
                seen_paths.add(path)
                results.append((session_id, full_url))

    if not results:
        # Last-resort: find any link that contains a numeric session/document ID
        # typical format: digits optionally preceded by legislature number, e.g. "5-123"
        for m in re.finditer(
            r'href=["\'](/nl/[^"\'?#]*?(\d+-\d+))["\']',
            html, re.IGNORECASE
        ):
            path       = m.group(1)
            session_id = m.group(2)
            full_url   = urllib.parse.urljoin(base_url, path)
            if path not in seen_paths:
                seen_paths.add(path)
                results.append((session_id, full_url))

    return results


def _extract_pdf_links(html: str, base_url: str) -> list[str]:
    """Extract PDF download links from a Senate session page."""
    links: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']*\.pdf)["\']', html, re.IGNORECASE):
        full = urllib.parse.urljoin(base_url, m.group(1))
        if full not in links:
            links.append(full)
    return links


def download_legislature(
    output_dir: Path,
    legislature: int,
    start_year: int,
    dry_run: bool = False,
) -> None:
    """
    Download all Dutch-language plenary session records for one legislature.
    """
    leg_dir = output_dir / f"legislature_{legislature:02d}"
    leg_dir.mkdir(parents=True, exist_ok=True)

    page = 1
    all_session_links: list[tuple[str, str]] = []

    # Collect session links from listing pages
    while True:
        list_url = _PLENARY_LIST_URL.format(
            base=SENATE_BASE, leg=legislature, page=page
        )
        list_url_alt = _PLENARY_LIST_URL_ALT.format(
            base=SENATE_BASE, leg=legislature, page=page
        )
        logger.info("[senate_be] legislature=%d page=%d — %s", legislature, page, list_url)

        if dry_run:
            logger.info("[dry-run] skipping")
            return

        html = None
        used_list_url = list_url
        for attempt_url in (list_url, list_url_alt):
            try:
                html = _fetch(attempt_url).decode("utf-8", errors="replace")
                used_list_url = attempt_url  # use the one that worked for logging
                break
            except Exception as exc:
                logger.warning(
                    "[senate_be] URL attempt failed (leg=%d page=%d): %s — %s",
                    legislature, page, attempt_url, exc,
                )
        if html is None:
            logger.warning(
                "[senate_be] All URL patterns failed for legislature=%d page=%d."
                " The senate.be URL structure may have changed."
                " Tried: %s and %s."
                " Check https://www.senate.be/nl/ manually for current proceedings URL.",
                legislature, page, list_url, list_url_alt,
            )
            break

        _log(output_dir, used_list_url, f"legislature_{legislature}")
        session_links = _extract_session_links(html, SENATE_BASE)

        if not session_links:
            logger.warning(
                "[senate_be] 0 session links found on leg=%d page=%d (%s)."
                " HTML snippet (first 500 chars): %.500s",
                legislature, page, used_list_url, html,
            )
            # On page 1 with 0 results, the listing page structure may have changed entirely.
            break

        new_links = [(sid, surl) for sid, surl in session_links
                     if (sid, surl) not in all_session_links]
        all_session_links.extend(new_links)
        logger.info("  Found %d sessions (total: %d)", len(new_links), len(all_session_links))

        # Check for a "next page" indicator using multiple patterns.
        # senate.be has used: rel="next", paginanummer=N, page=N in query strings.
        has_next = (
            'rel="next"' in html
            or f"paginanummer={page + 1}" in html
            or f"page={page + 1}" in html
        )
        if not has_next:
            break
        page += 1
        time.sleep(0.5)

    logger.info("[senate_be] legislature=%d — %d sessions to download", legislature, len(all_session_links))

    # Download individual session documents (HTML + PDFs)
    for session_id, session_url in all_session_links:
        html_dest = leg_dir / f"session_{session_id}.html"
        if html_dest.exists():
            continue

        try:
            session_html = _fetch(session_url).decode("utf-8", errors="replace")
            html_dest.write_text(session_html, encoding="utf-8")
            html_dest.with_suffix(".url").write_text(session_url, encoding="utf-8")
            _log(output_dir, session_url, f"legislature_{legislature}:session")
            logger.debug("  Saved session %s", session_id)
        except Exception as exc:
            logger.warning("  Failed session %s: %s", session_id, exc)
            time.sleep(0.5)
            continue

        # Download associated PDFs
        pdf_links = _extract_pdf_links(session_html, SENATE_BASE)
        for pdf_url in pdf_links[:5]:  # cap per session
            pdf_name = Path(urllib.parse.urlparse(pdf_url).path).name
            if not pdf_name.endswith(".pdf"):
                pdf_name += ".pdf"
            pdf_dest = leg_dir / f"{session_id}_{pdf_name}"
            if pdf_dest.exists():
                continue
            try:
                pdf_data = _fetch(pdf_url, timeout=60)
                pdf_dest.write_bytes(pdf_data)
                pdf_dest.with_suffix(".url").write_text(pdf_url, encoding="utf-8")
            except Exception as exc:
                logger.debug("  PDF failed %s: %s", pdf_url, exc)

        time.sleep(0.5)


def download(
    output_dir: Path,
    legislature_from: int = 6,
    dry_run: bool = False,
) -> None:
    """
    Download Belgian Senate Dutch-language proceedings.
    Starts from legislature_from (default: 6 = 2003).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for leg_num, start_year, end_year in LEGISLATURES:
        if leg_num < legislature_from:
            continue
        logger.info("=== Senate legislature %d (%d–%d) ===", leg_num, start_year, end_year)
        download_legislature(output_dir, leg_num, start_year, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Belgian Senate Dutch-language proceedings (public domain)"
    )
    parser.add_argument("--output",           required=True, type=Path)
    parser.add_argument("--legislature-from", type=int, default=6,
                        help="Start from this legislature number (default: 6 = 2003)")
    parser.add_argument("--dry-run",          action="store_true")
    args = parser.parse_args()
    download(args.output, legislature_from=args.legislature_from, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
