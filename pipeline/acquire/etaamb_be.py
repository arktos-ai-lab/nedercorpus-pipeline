"""
Download Belgian Official Gazette (Belgisch Staatsblad) texts via etaamb.openjustice.be.

etaamb.openjustice.be is an OpenJustice.be project that provides free, normalized
XML access to the Belgian Official Gazette (Belgisch Staatsblad / Moniteur Belge).
The site covers publications from 1997 onward.

The existing `belgian` source covers the official belgisch-staatsblad.be HTML pages.
etaamb provides complementary access with:
  - Cleaner XML structure (no navigation boilerplate)
  - Better historical coverage (pre-2000 content indexed separately)
  - Normalized metadata (date, publication number, document type)
  - Dutch + French text separated per article

API: https://etaamb.openjustice.be/nl/
    - Search: /nl/search/?q=&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    - Document XML: /nl/document/NNNNNN

License: Public Domain
  Belgian government publications have no copyright under Article 8 of
  the Belgian Copyright Act (wet van 30 juni 1994).

Note: etaamb does not publish a bulk download API. We paginate the search
results to collect document IDs, then fetch individual documents.
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

SOURCE  = "etaamb_be"
LICENSE = "PD"
COUNTRY = "BE"
DOMAIN  = "government"

_BASE_URL   = "https://etaamb.openjustice.be"
# Browse-based URL: /nl/pub/{year}/{month:02d}.html lists documents per month
_PUB_URL    = f"{_BASE_URL}/nl/pub"
_USER_AGENT = "NederCorpus/0.1 (academic; CC0/PD Belgian gazette; openjustice)"

# Date ranges to harvest — go year by year from 1997 (earliest available)
_START_YEAR = 2000  # Focus on more recent years; pre-2000 overlap with belgian source
_END_YEAR   = 2024


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, retries: int = 3, timeout: int = 30) -> bytes:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept":     "text/html,application/xhtml+xml,application/xml",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 3.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _extract_doc_links(html: str) -> list[tuple[str, str]]:
    """
    Extract (doc_url, doc_id) pairs from an etaamb day-level index page.

    etaamb day pages contain absolute document URLs like:
      https://etaamb.openjustice.be/nl/wet-van-26-juni-2022_n2022034799.html

    Numac IDs (6-14 digits) appear in the _n{numac} suffix.
    Returns list of (full_url, doc_id) tuples.
    """
    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    # Primary pattern: absolute URLs with _n{numac} suffix (day-level pages)
    for m in re.finditer(
        re.escape(_BASE_URL) + r'/nl/[^"\'<\s]+_n(\d{6,14})\.html',
        html,
        re.IGNORECASE,
    ):
        full   = m.group(0)
        doc_id = m.group(1)
        if doc_id not in seen:
            seen.add(doc_id)
            results.append((full, doc_id))

    # Fallback: relative /nl/ paths with _n{numac} suffix
    if not results:
        for m in re.finditer(
            r'href=["\'](?:' + re.escape(_BASE_URL) + r')?(/nl/[^"\'?#\s]+_n(\d{6,14})(?:\.html)?)["\']',
            html,
            re.IGNORECASE,
        ):
            path   = m.group(1)
            doc_id = m.group(2)
            full   = f"{_BASE_URL}{path}"
            if doc_id not in seen:
                seen.add(doc_id)
                results.append((full, doc_id))

    return results


def _extract_day_links(html: str, year: int, month: int) -> list[str]:
    """
    Extract day-page URLs from a month index page.

    Month pages link to day pages like:
      /nl/pub/{year}/{month}/{day}.html  (relative)
      https://etaamb.openjustice.be/nl/pub/{year}/{month}/{day}.html  (absolute)
    """
    day_pat = re.compile(
        r'(?:' + re.escape(_BASE_URL) + r')?'
        r'(/nl/pub/' + str(year) + r'/' + f"{month:02d}" + r'/\d{1,2}\.html)',
        re.IGNORECASE,
    )
    seen: set[str] = set()
    result: list[str] = []
    for m in day_pat.finditer(html):
        path = m.group(1)
        full = f"{_BASE_URL}{path}"
        if full not in seen:
            seen.add(full)
            result.append(full)
    return result


def _extract_doc_text(html: bytes, doc_id: str) -> dict | None:
    """
    Extract Dutch text and metadata from an etaamb document page.

    Returns dict with keys: id, title, date, text, url
    """
    text = html.decode("utf-8", errors="replace")

    # Extract title from <h1> or <title>
    title_m = re.search(r'<h1[^>]*>(.*?)</h1>', text, re.DOTALL | re.IGNORECASE)
    title = ""
    if title_m:
        title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()

    # Extract date
    date_m = re.search(r'(\d{1,2})\s+(januari|februari|maart|april|mei|juni|juli|augustus|'
                       r'september|oktober|november|december)\s+(\d{4})', text, re.IGNORECASE)
    pub_year = None
    if date_m:
        _months = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5,
                   "juni": 6, "juli": 7, "augustus": 8, "september": 9, "oktober": 10,
                   "november": 11, "december": 12}
        pub_year = int(date_m.group(3))

    # Extract main Dutch article text
    # Look for the Dutch content section (nl-content or article body)
    # etaamb marks Dutch sections; strip HTML tags from the content block
    body_m = re.search(
        r'(?:<div[^>]*class="[^"]*(?:content|artikel|body|tekst)[^"]*"[^>]*>)(.*?)(?:</div>)',
        text, re.DOTALL | re.IGNORECASE
    )
    if body_m:
        body_html = body_m.group(1)
    else:
        # Fall back: take content between <article> tags
        body_m = re.search(r'<article[^>]*>(.*?)</article>', text, re.DOTALL | re.IGNORECASE)
        body_html = body_m.group(1) if body_m else ""

    if not body_html:
        # Last resort: strip all HTML, take remaining text
        body_html = text

    # Clean HTML to plain text
    body_text = re.sub(r'<[^>]+>', ' ', body_html)
    body_text = re.sub(r'&nbsp;', ' ', body_text)
    body_text = re.sub(r'&amp;', '&', body_text)
    body_text = re.sub(r'&lt;', '<', body_text)
    body_text = re.sub(r'&gt;', '>', body_text)
    body_text = re.sub(r'\s+', ' ', body_text).strip()

    if len(body_text) < 200:
        return None

    return {
        "id":               doc_id,
        "title":            title,
        "publication_year": pub_year,
        "text":             body_text,
        "url":              f"{_BASE_URL}/nl/document/{doc_id}",
        "acquisition_date": datetime.date.today().isoformat(),
    }


def download_year(
    output_dir: Path,
    year: int,
    max_docs_per_year: int = 500,
) -> int:
    """
    Download Belgian Gazette documents for one year.

    The site uses a 3-level hierarchy:
      /nl/pub/{year}/{month:02d}.html       — month page: links to day pages
      /nl/pub/{year}/{month:02d}/{day}.html — day page:   links to documents
      /nl/{type}-van-{date}_n{numac}.html   — document page

    Discovers all day-page URLs via month pages, then collects document links
    from each day page, then fetches individual documents.
    Returns the number of documents saved.
    """
    year_dir = output_dir / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)

    all_doc_links: list[tuple[str, str]] = []   # (doc_url, doc_id)
    seen_ids: set[str] = set()

    for month in range(1, 13):
        month_url = f"{_PUB_URL}/{year}/{month:02d}.html"
        try:
            month_html = _fetch(month_url).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("[etaamb_be] %d-%02d: fetch failed: %s", year, month, exc)
            time.sleep(0.3)
            continue

        day_urls = _extract_day_links(month_html, year, month)
        logger.debug("[etaamb_be] %d-%02d: %d day pages", year, month, len(day_urls))

        for day_url in day_urls:
            try:
                day_html = _fetch(day_url).decode("utf-8", errors="replace")
            except Exception as exc:
                logger.debug("[etaamb_be] day page fetch failed %s: %s", day_url, exc)
                time.sleep(0.3)
                continue

            links = _extract_doc_links(day_html)
            new = [(u, i) for u, i in links if i not in seen_ids]
            for u, i in new:
                seen_ids.add(i)
            all_doc_links.extend(new)
            time.sleep(0.2)

        time.sleep(0.3)

    logger.info("[etaamb_be] Year %d: %d document links found", year, len(all_doc_links))

    saved = 0
    for doc_url, doc_id in all_doc_links[:max_docs_per_year]:
        dest = year_dir / f"{doc_id}.json"
        if dest.exists():
            saved += 1
            continue

        try:
            doc_html = _fetch(doc_url)
        except Exception as exc:
            logger.warning("[etaamb_be] Doc %s fetch failed: %s", doc_id, exc)
            continue

        record = _extract_doc_text(doc_html, doc_id)
        if record is None:
            logger.debug("[etaamb_be] Doc %s had no extractable text", doc_id)
            continue

        dest.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        _log(output_dir, doc_url)
        saved += 1
        time.sleep(0.3)

    logger.info("[etaamb_be] Year %d: %d documents saved", year, saved)
    return saved


def download(
    output_dir: Path,
    start_year: int = _START_YEAR,
    end_year: int = _END_YEAR,
    max_docs_per_year: int = 500,
    dry_run: bool = False,
) -> None:
    """
    Download Belgian Official Gazette Dutch texts from etaamb.openjustice.be.

    Iterates over years from start_year to end_year, harvesting document texts.

    Args:
        start_year:        First year to harvest (default 2000).
        end_year:          Last year to harvest (default 2024).
        max_docs_per_year: Max documents per year (default 500).
        dry_run:           Log only, do not fetch.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info(
            "[etaamb_be] [dry-run] would harvest %d–%d from etaamb.openjustice.be",
            start_year, end_year,
        )
        return

    total = 0
    for year in range(start_year, end_year + 1):
        total += download_year(output_dir, year, max_docs_per_year=max_docs_per_year)
        time.sleep(1.0)

    logger.info("[etaamb_be] Done. Total documents: %d", total)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Belgian Gazette texts from etaamb.openjustice.be (PD)"
    )
    parser.add_argument("--output",            required=True, type=Path)
    parser.add_argument("--start-year",        type=int, default=_START_YEAR)
    parser.add_argument("--end-year",          type=int, default=_END_YEAR)
    parser.add_argument("--max-docs-per-year", type=int, default=500)
    parser.add_argument("--dry-run",           action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        start_year=args.start_year,
        end_year=args.end_year,
        max_docs_per_year=args.max_docs_per_year,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
