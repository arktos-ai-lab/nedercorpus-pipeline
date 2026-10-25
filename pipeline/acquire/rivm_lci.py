"""
Download RIVM LCI-richtlijnen (infectious disease guidelines).

LCI (Landelijke Coördinatie Infectieziektebestrijding) is the RIVM unit
responsible for infection disease control guidelines. These guidelines,
draaiboeken, and factsheets are government-authored policy documents and
are in the public domain under Art. 11 of the Dutch Auteurswet (government
works are not copyrightable in the Netherlands).

This acquirer is distinct from rivm.py, which uses the OAI-PMH repository
for scientific reports. LCI content lives at https://lci.rivm.nl/ and is
not accessible via OAI-PMH.

Index pages:
  https://lci.rivm.nl/richtlijnen
  https://lci.rivm.nl/draaiboeken
  https://lci.rivm.nl/factsheets

Output:
  rivm_lci_guidelines.jsonl
  Each record: {id, title, url, body, section_type, date, acquisition_date}

License: PD (Art. 11 Auteurswet — government-authored work)

Usage:
  python -m pipeline acquire --sources rivm_lci
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "rivm_lci"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "science"

_BASE_URL  = "https://lci.rivm.nl"
_UA        = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY     = 2.0   # seconds between HTTP requests — be polite to RIVM

# Index pages to crawl for guideline links
_INDEX_PAGES = [
    ("richtlijn",  f"{_BASE_URL}/richtlijnen"),
    ("draaiboek",  f"{_BASE_URL}/draaiboeken"),
    ("factsheet",  f"{_BASE_URL}/factsheets"),
]

# URL path prefixes that indicate a content document (not a navigation page)
_CONTENT_PREFIXES = ("/richtlijnen/", "/draaiboeken/", "/factsheets/")

HEADERS = {
    "User-Agent":      _UA,
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl,en;q=0.5",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    """Fetch URL bytes with retry on transient errors."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404, 410):
                raise  # permanent — don't retry
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("[rivm_lci] HTTP %d for %s, retry %d (wait %.1fs)",
                           exc.code, url, attempt + 1, wait)
            time.sleep(wait)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("[rivm_lci] Error fetching %s: %s, retry %d", url, exc, attempt + 1)
            time.sleep(wait)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Simple HTML stripper (no BeautifulSoup dependency)
# ---------------------------------------------------------------------------

# Tags whose entire content we discard
_DISCARD_TAG_RE = re.compile(
    r"<(script|style|nav|header|footer|aside|noscript|form|button|select"
    r"|option|iframe|figure|figcaption|img|input|meta|link)"
    r"(\s[^>]*)?>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Self-closing / void tags
_VOID_TAG_RE   = re.compile(r"<(br|hr|img|input|meta|link)[^>]*/?>\s*", re.IGNORECASE)
# Block-level tags — convert to newlines before stripping
_BLOCK_TAG_RE  = re.compile(
    r"</?(?:p|div|li|ul|ol|h[1-6]|section|article|table|tr|td|th|blockquote|pre)[^>]*>",
    re.IGNORECASE,
)
# Any remaining HTML tag
_ANY_TAG_RE    = re.compile(r"<[^>]+>", re.DOTALL)
# Named HTML entities
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&apos;": "'", "&nbsp;": " ", "&ndash;": "–", "&mdash;": "—",
    "&lsquo;": "\u2018", "&rsquo;": "\u2019",
    "&ldquo;": "\u201c", "&rdquo;": "\u201d",
    "&euro;": "€", "&copy;": "©", "&reg;": "®", "&hellip;": "…",
}
_NUM_ENT_RE    = re.compile(r"&#(\d+);")
_HEX_ENT_RE    = re.compile(r"&#x([0-9a-fA-F]+);")
_BLANK_LINES   = re.compile(r"\n{3,}")
_WHITESPACE    = re.compile(r"[ \t]{2,}")


def _strip_html(html: str) -> str:
    """
    Strip all HTML markup from *html* and return clean plain text.
    Block-level tags become newlines; inline tags are removed.
    """
    # Remove discardable sections entirely
    text = _DISCARD_TAG_RE.sub(" ", html)
    # Remove void elements
    text = _VOID_TAG_RE.sub(" ", text)
    # Block-level elements → newline
    text = _BLOCK_TAG_RE.sub("\n", text)
    # Remove all remaining tags
    text = _ANY_TAG_RE.sub(" ", text)
    # Decode entities
    for ent, repl in _HTML_ENTITIES.items():
        text = text.replace(ent, repl)
    text = _NUM_ENT_RE.sub(lambda m: chr(int(m.group(1))), text)
    text = _HEX_ENT_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    # Normalise whitespace
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _extract_title_from_html(html: str) -> str:
    """Extract page title from <title> or first <h1>."""
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return _strip_html(m.group(1))
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_html(m.group(1))
    return ""


def _extract_date_from_html(html: str, url: str) -> str:
    """Best-effort publication date from HTML metadata or URL pattern."""
    # <meta> tags: publication date / last modified
    m = re.search(
        r'<meta[^>]+(?:name|property)=["\'][^"\']*(?:date|published|modified|revision)[^"\']*["\']'
        r'[^>]*content=["\']([0-9]{4}-[0-9]{2}-[0-9]{2})',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # <time datetime="...">
    m = re.search(r'<time[^>]+datetime=["\']([0-9]{4}-[0-9]{2}-[0-9]{2})', html, re.IGNORECASE)
    if m:
        return m.group(1)
    # URL path date
    m = re.search(r'/(20\d{2})[/_-](\d{2})[/_-](\d{2})(?:[/_-]|$)', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _extract_main_content(html: str) -> str:
    """
    Extract the main article content from an LCI page.

    LCI pages use a single-column layout. We try several content
    container patterns in order of specificity, falling back to
    full-page stripping.
    """
    # Prefer <main ...>
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.IGNORECASE | re.DOTALL)
    if m:
        body = _strip_html(m.group(1))
        if len(body) >= 150:
            return body

    # <article ...>
    m = re.search(r"<article[^>]*>(.*?)</article>", html, re.IGNORECASE | re.DOTALL)
    if m:
        body = _strip_html(m.group(1))
        if len(body) >= 150:
            return body

    # div with content-like class
    for cls_pattern in (
        r'class="[^"]*(?:content|article|main|richtlijn|guideline|body)[^"]*"',
        r'id="[^"]*(?:content|main|article)[^"]*"',
    ):
        pattern = r'<div\s+' + cls_pattern + r'[^>]*>(.*?)</div\s*>'
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            body = _strip_html(m.group(1))
            if len(body) >= 150:
                return body

    # Fallback: strip the whole page
    return _strip_html(html)


# ---------------------------------------------------------------------------
# Link discovery
# ---------------------------------------------------------------------------

def _extract_guideline_links(html: str, base_url: str, section_type: str) -> list[tuple[str, str]]:
    """
    Parse all <a href="..."> anchors from *html* that point to guideline
    content pages.

    Returns list of (absolute_url, section_type) tuples.
    """
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1).strip()

        # Resolve relative paths
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            parsed = urllib.parse.urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith("http"):
            href = urllib.parse.urljoin(base_url, href)

        # Normalise: drop fragment and query
        href = re.sub(r"[#?].*$", "", href)

        # Must be an lci.rivm.nl content page
        if "lci.rivm.nl" not in href:
            continue
        parsed = urllib.parse.urlparse(href)
        if not any(parsed.path.startswith(p) for p in _CONTENT_PREFIXES):
            continue
        # Skip bare index pages themselves
        if parsed.path.rstrip("/") in ("/richtlijnen", "/draaiboeken", "/factsheets"):
            continue

        if href in seen:
            continue
        seen.add(href)

        # Infer section_type from URL path
        if "/richtlijnen/" in parsed.path:
            stype = "richtlijn"
        elif "/draaiboeken/" in parsed.path:
            stype = "draaiboek"
        elif "/factsheets/" in parsed.path:
            stype = "factsheet"
        else:
            stype = section_type

        links.append((href, stype))

    return links


# ---------------------------------------------------------------------------
# Resume / output helpers
# ---------------------------------------------------------------------------

def _load_seen_urls(output_path: Path) -> set[str]:
    """Load URLs already present in the output JSONL to support resume."""
    seen: set[str] = set()
    if not output_path.exists():
        return seen
    with output_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                url = rec.get("url") or ""
                if url:
                    seen.add(url)
            except json.JSONDecodeError:
                continue
    return seen


def _safe_id(s: str) -> str:
    """Convert arbitrary string to a safe record ID component."""
    return re.sub(r"[^\w\-]", "_", s)[:80]


def _write_record(output_path: Path, rec: dict) -> None:
    """Append one JSON record to the JSONL output file."""
    with output_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _log(output_dir: Path, total: int) -> None:
    """Append an acquisition log entry."""
    entry = {
        "source":           SOURCE,
        "url":              _BASE_URL,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
        "records":          total,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def download(
    output_dir:   Path,
    max_articles: int  = 2_000,
    dry_run:      bool = False,
) -> None:
    """
    Harvest RIVM LCI guidelines, draaiboeken, and factsheets.

    For each index page (richtlijnen / draaiboeken / factsheets):
      1. Fetch the index page.
      2. Extract all links to content documents.
      3. For each content URL not yet seen, fetch and strip the page.
      4. Write the result to rivm_lci_guidelines.jsonl.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "rivm_lci_guidelines.jsonl"

    seen_urls = _load_seen_urls(output_path)
    logger.info("[rivm_lci] %d URLs already seen (resume)", len(seen_urls))

    # Phase 1: collect all guideline links from all index pages
    all_links: list[tuple[str, str]] = []  # (url, section_type)
    seen_link_urls: set[str] = set()

    for section_type, index_url in _INDEX_PAGES:
        logger.info("[rivm_lci] Fetching index: %s", index_url)
        try:
            raw  = _fetch(index_url, timeout=30)
            html = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("[rivm_lci] Could not fetch index %s: %s", index_url, exc)
            time.sleep(_DELAY)
            continue

        links = _extract_guideline_links(html, index_url, section_type)
        new   = [(u, st) for u, st in links if u not in seen_link_urls]
        for u, st in new:
            seen_link_urls.add(u)
        all_links.extend(new)
        logger.info("[rivm_lci] %s index: %d links found (%d new)", section_type, len(links), len(new))
        time.sleep(_DELAY)

    logger.info("[rivm_lci] Total guideline links collected: %d", len(all_links))

    # Phase 2: fetch and extract each guideline page
    written = 0
    today   = datetime.date.today().isoformat()

    for i, (url, section_type) in enumerate(all_links):
        if written >= max_articles:
            logger.info("[rivm_lci] max_articles=%d reached, stopping.", max_articles)
            break

        if url in seen_urls:
            continue

        if dry_run:
            logger.info("[rivm_lci] [dry-run] would fetch %s (%s)", url, section_type)
            seen_urls.add(url)
            continue

        logger.debug("[rivm_lci] Fetching [%d/%d]: %s", i + 1, len(all_links), url)

        try:
            raw  = _fetch(url, timeout=30)
            html = raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            logger.info("[rivm_lci] HTTP %d for %s, skipping.", exc.code, url)
            seen_urls.add(url)
            time.sleep(_DELAY)
            continue
        except Exception as exc:
            logger.warning("[rivm_lci] Fetch failed %s: %s, skipping.", url, exc)
            seen_urls.add(url)
            time.sleep(_DELAY)
            continue

        body = _extract_main_content(html)
        if len(body) < 100:
            logger.debug("[rivm_lci] Skipping %s: body too short (%d chars)", url, len(body))
            seen_urls.add(url)
            time.sleep(_DELAY)
            continue

        title = _extract_title_from_html(html)
        date  = _extract_date_from_html(html, url)

        rec = {
            "id":               f"rivm_lci_{_safe_id(url)}",
            "title":            title,
            "url":              url,
            "body":             body,
            "section_type":     section_type,
            "date":             date,
            "acquisition_date": today,
        }

        _write_record(output_path, rec)
        seen_urls.add(url)
        written += 1

        if written % 50 == 0:
            logger.info("[rivm_lci] %d guidelines written so far", written)

        time.sleep(_DELAY)

    _log(output_dir, written)
    logger.info("[rivm_lci] Done. %d guidelines written to %s", written, output_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download RIVM LCI infectious disease guidelines (PD — Art. 11 Auteurswet)"
    )
    parser.add_argument("--output",       required=True, type=Path,
                        help="Output directory")
    parser.add_argument("--max-articles", type=int, default=2_000,
                        help="Maximum number of guideline pages to download")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Log what would be fetched without writing")
    args = parser.parse_args()
    download(
        args.output,
        max_articles=args.max_articles,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
