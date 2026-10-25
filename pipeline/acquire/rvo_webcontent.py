"""
Download RVO (Rijksdienst voor Ondernemend Nederland) open webcontent.

RVO is the Netherlands Enterprise Agency, a government agency that declares
all its web information as freely reusable ("openbaar") — effectively CC0/PD
under the Dutch Government Open Data policy.

Strategy:
  1. Query data.overheid.nl CKAN API for RVO datasets (package_search?q=rvo)
  2. For each package with CC0 or PD license, download CSV/JSON resource files
     and extract any text fields as document records
  3. Fallback: crawl https://www.rvo.nl/sitemap.xml to discover article URLs,
     fetch each page and strip HTML to extract Dutch body text

Output:
  rvo_webcontent_articles.jsonl
  Each record: {id, title, body, url, date, acquisition_date}

License: CC0 (Rijksoverheid / RVO open data policy)

Usage:
  python -m pipeline acquire --sources rvo_webcontent
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
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "rvo_webcontent"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "government"

_CKAN_BASE     = "https://data.overheid.nl/api/3/action"
_SITEMAP_URL   = "https://www.rvo.nl/sitemap.xml"
_UA            = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY         = 2.0   # seconds between HTTP requests (polite)
_MAX_PACKAGES  = 200   # maximum CKAN packages to inspect
_MAX_ARTICLES  = 5_000 # maximum sitemap articles to fetch

# Accepted license identifiers from data.overheid.nl
_ALLOWED_LICENSES = {
    "cc0",
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "public domain",
    "publicdomain",
    "pd",
    "https://creativecommons.org/publicdomain/zero/1.0/",
    "http://www.opendefinition.org/licenses/cc-zero",
}

# Sitemap XML namespace
_SM_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

HEADERS = {
    "User-Agent": _UA,
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
                raise  # permanent errors — don't retry
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("[rvo_webcontent] HTTP %d for %s, retry %d (wait %.1fs)",
                           exc.code, url, attempt + 1, wait)
            time.sleep(wait)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("[rvo_webcontent] Fetch error for %s: %s, retry %d",
                           url, exc, attempt + 1)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _fetch_json(url: str, timeout: int = 30) -> object:
    """Fetch URL and parse as JSON."""
    raw = _fetch(url, timeout=timeout)
    return json.loads(raw.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

# Tags whose content we want to discard entirely
_DISCARD_TAGS = re.compile(
    r"<(script|style|nav|header|footer|aside|noscript|form|button|select|option)"
    r"(\s[^>]*)?>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Any remaining HTML tag
_HTML_TAG     = re.compile(r"<[^>]+>", re.DOTALL)
# HTML entities
_HTML_ENT_NUM = re.compile(r"&#(\d+);")
_HTML_ENT_HEX = re.compile(r"&#x([0-9a-fA-F]+);")
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&apos;": "'", "&nbsp;": " ", "&ndash;": "–", "&mdash;": "—",
    "&lsquo;": "'", "&rsquo;": "'", "&ldquo;": "\u201c", "&rdquo;": "\u201d",
    "&euro;": "€", "&copy;": "©", "&reg;": "®",
}
_WHITESPACE   = re.compile(r"\s{2,}")


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities, returning plain text."""
    text = _DISCARD_TAGS.sub(" ", html)
    text = _HTML_TAG.sub(" ", text)
    for ent, repl in _HTML_ENTITIES.items():
        text = text.replace(ent, repl)
    text = _HTML_ENT_NUM.sub(lambda m: chr(int(m.group(1))), text)
    text = _HTML_ENT_HEX.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def _extract_title(html: str) -> str:
    """Extract <title> or first <h1> from HTML."""
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return _strip_html(m.group(1))
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_html(m.group(1))
    return ""


def _extract_main_body(html: str) -> str:
    """
    Extract the main article body from an RVO page.
    Tries common content containers before falling back to full-page strip.
    """
    # Try <main ...>, <article ...>, <div class="...content..."> etc.
    for pattern in (
        r'<main[^>]*>(.*?)</main>',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*class="[^"]*(?:content|body|artikel|main)[^"]*"[^>]*>(.*?)</div>',
    ):
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            body = _strip_html(m.group(1))
            if len(body) >= 100:
                return body
    # Fallback: strip entire page
    return _strip_html(html)


def _extract_date(html: str, url: str) -> str:
    """Best-effort date extraction from page HTML or URL."""
    # meta tag
    m = re.search(
        r'<meta[^>]+(?:name|property)="[^"]*(?:date|published|modified)[^"]*"[^>]*'
        r'content="([0-9]{4}-[0-9]{2}-[0-9]{2})',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # URL path component
    m = re.search(r'/(20\d{2})[/_-](\d{2})[/_-](\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # time element
    m = re.search(r'<time[^>]+datetime="([0-9]{4}-[0-9]{2}-[0-9]{2})', html, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# CKAN / data.overheid.nl path
# ---------------------------------------------------------------------------

def _is_allowed_license(pkg: dict) -> bool:
    """Return True if the package license is CC0 or public domain."""
    for field in ("license_id", "license_title", "license_url"):
        val = (pkg.get(field) or "").lower().strip()
        if val in _ALLOWED_LICENSES:
            return True
        for allowed in _ALLOWED_LICENSES:
            if allowed in val:
                return True
    return False


def _ckan_search_rvo(rows: int = 50, start: int = 0) -> list[dict]:
    """Query CKAN package_search for RVO datasets, return package list."""
    params = urllib.parse.urlencode({
        "q":    "rvo",
        "rows": rows,
        "start": start,
    })
    url = f"{_CKAN_BASE}/package_search?{params}"
    try:
        data = _fetch_json(url)
    except Exception as exc:
        logger.warning("[rvo_webcontent] CKAN search failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    return data.get("result", {}).get("results", [])


def _process_ckan_resource(resource: dict, seen_urls: set[str]) -> list[dict]:
    """
    Download a single CKAN resource (CSV or JSON) and extract text records.
    Returns a list of article dicts.
    """
    url   = resource.get("url") or resource.get("download_url") or ""
    fmt   = (resource.get("format") or "").lower()
    name  = resource.get("name") or resource.get("description") or ""

    if not url or fmt not in ("csv", "json", "jsonl", "txt"):
        return []
    if url in seen_urls:
        return []

    logger.info("[rvo_webcontent] CKAN resource: %s (%s)", name, url)
    try:
        raw = _fetch(url, timeout=60)
    except Exception as exc:
        logger.warning("[rvo_webcontent] Resource download failed %s: %s", url, exc)
        return []

    seen_urls.add(url)
    records = []
    today   = datetime.date.today().isoformat()

    if fmt in ("json", "jsonl"):
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return []
        items = obj if isinstance(obj, list) else obj.get("records", obj.get("data", []))
        if not isinstance(items, list):
            return []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            # Best-effort: join all string values as body text
            parts = [str(v) for v in item.values() if v and isinstance(v, str) and len(str(v)) > 10]
            body  = " | ".join(parts)
            if not body:
                continue
            title = (item.get("title") or item.get("naam") or item.get("name")
                     or item.get("omschrijving") or name or f"record_{i}")
            records.append({
                "id":               f"ckan_{_safe_id(url)}_{i}",
                "title":            str(title)[:500],
                "body":             body,
                "url":              url,
                "date":             str(item.get("date") or item.get("datum") or ""),
                "acquisition_date": today,
            })

    elif fmt == "csv":
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            return []
        headers = [h.strip() for h in lines[0].split(",")]
        for i, line in enumerate(lines[1:], 1):
            cols  = line.split(",")
            row   = {headers[j]: cols[j].strip() if j < len(cols) else "" for j in range(len(headers))}
            parts = [v for v in row.values() if v and len(v) > 10]
            body  = " | ".join(parts)
            if not body:
                continue
            title = (row.get("title") or row.get("naam") or row.get("name")
                     or row.get("omschrijving") or name or f"row_{i}")
            records.append({
                "id":               f"ckan_{_safe_id(url)}_{i}",
                "title":            str(title)[:500],
                "body":             body,
                "url":              url,
                "date":             str(row.get("date") or row.get("datum") or ""),
                "acquisition_date": today,
            })

    elif fmt == "txt":
        body = raw.decode("utf-8", errors="replace").strip()
        if body:
            records.append({
                "id":               f"ckan_{_safe_id(url)}_0",
                "title":            name or url,
                "body":             body,
                "url":              url,
                "date":             "",
                "acquisition_date": today,
            })

    return records


def _download_ckan(
    output_path: Path,
    seen_urls:   set[str],
    max_packages: int = _MAX_PACKAGES,
    dry_run: bool = False,
) -> int:
    """
    Enumerate RVO packages on data.overheid.nl and download their resources.
    Returns number of records written.
    """
    logger.info("[rvo_webcontent] Querying CKAN for RVO datasets …")
    written = 0
    start   = 0
    rows    = 50

    while start < max_packages:
        pkgs = _ckan_search_rvo(rows=rows, start=start)
        if not pkgs:
            break

        for pkg in pkgs:
            if not _is_allowed_license(pkg):
                continue
            org   = ((pkg.get("organization") or {}).get("name") or "").lower()
            title = (pkg.get("title") or "").lower()
            # Keep only packages that are clearly RVO-related
            if "rvo" not in org and "rvo" not in title and "rijksdienst voor ondernemend" not in title:
                continue

            for resource in pkg.get("resources", []):
                if dry_run:
                    logger.info("[rvo_webcontent] [dry-run] would fetch %s", resource.get("url"))
                    continue
                records = _process_ckan_resource(resource, seen_urls)
                for rec in records:
                    if rec["url"] in seen_urls and rec["url"] != resource.get("url"):
                        continue
                    with output_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                time.sleep(_DELAY)

        start += rows
        if len(pkgs) < rows:
            break
        time.sleep(1.0)

    logger.info("[rvo_webcontent] CKAN path: %d records written", written)
    return written


# ---------------------------------------------------------------------------
# Sitemap / scraping fallback
# ---------------------------------------------------------------------------

def _parse_sitemap(raw: bytes) -> list[str]:
    """
    Parse a sitemap XML (or sitemap index) and return all <loc> URLs.
    Handles both sitemap index files and regular sitemaps.
    """
    urls: list[str] = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.warning("[rvo_webcontent] Sitemap XML parse error: %s", exc)
        return urls

    tag = root.tag.lower()
    ns  = ""
    m   = re.match(r"\{([^}]+)\}", root.tag)
    if m:
        ns = m.group(1)

    def _find_locs(el: ET.Element) -> list[str]:
        locs = []
        for loc_el in el.iter(f"{{{ns}}}loc" if ns else "loc"):
            loc = (loc_el.text or "").strip()
            if loc:
                locs.append(loc)
        return locs

    # Sitemap index: recurse into each child sitemap
    if "sitemapindex" in tag or root.find(f"{{{ns}}}sitemap" if ns else "sitemap") is not None:
        for sm_el in root.iter(f"{{{ns}}}sitemap" if ns else "sitemap"):
            loc_el = sm_el.find(f"{{{ns}}}loc" if ns else "loc")
            if loc_el is not None and loc_el.text:
                child_url = loc_el.text.strip()
                try:
                    logger.info("[rvo_webcontent] Child sitemap: %s", child_url)
                    child_raw = _fetch(child_url, timeout=30)
                    urls.extend(_parse_sitemap(child_raw))
                    time.sleep(0.5)
                except Exception as exc:
                    logger.warning("[rvo_webcontent] Child sitemap fetch failed %s: %s",
                                   child_url, exc)
    else:
        urls.extend(_find_locs(root))

    return urls


def _is_article_url(url: str) -> bool:
    """Heuristic: keep RVO URLs that look like content pages (not assets/downloads)."""
    lower = url.lower()
    if any(ext in lower for ext in (".pdf", ".zip", ".xlsx", ".csv", ".xml",
                                     ".png", ".jpg", ".gif", ".svg")):
        return False
    if any(seg in lower for seg in ("/api/", "/sitemap", "/cdn-cgi/", "/?", "/zoeken",
                                     "/english/", "/en/")):
        return False
    return "rvo.nl" in lower


def _scrape_article(url: str) -> dict | None:
    """Fetch a single RVO article page and extract text. Returns dict or None."""
    try:
        raw  = _fetch(url, timeout=30)
        html = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("[rvo_webcontent] Fetch failed %s: %s", url, exc)
        return None

    body = _extract_main_body(html)
    if len(body) < 100:
        return None

    title = _extract_title(html)
    date  = _extract_date(html, url)

    return {
        "id":               f"rvo_{_safe_id(url)}",
        "title":            title,
        "body":             body,
        "url":              url,
        "date":             date,
        "acquisition_date": datetime.date.today().isoformat(),
    }


def _download_sitemap(
    output_path: Path,
    seen_urls:   set[str],
    max_articles: int = _MAX_ARTICLES,
    dry_run: bool = False,
) -> int:
    """
    Crawl RVO sitemap and scrape article pages.
    Returns number of records written.
    """
    logger.info("[rvo_webcontent] Fetching sitemap: %s", _SITEMAP_URL)
    try:
        raw = _fetch(_SITEMAP_URL, timeout=30)
    except Exception as exc:
        logger.error("[rvo_webcontent] Sitemap fetch failed: %s", exc)
        return 0

    all_urls = _parse_sitemap(raw)
    article_urls = [u for u in all_urls if _is_article_url(u) and u not in seen_urls]
    article_urls = list(dict.fromkeys(article_urls))  # deduplicate, preserve order

    logger.info("[rvo_webcontent] Sitemap: %d article URLs (after dedup/filter)", len(article_urls))

    written = 0
    for url in article_urls[:max_articles]:
        if url in seen_urls:
            continue
        if dry_run:
            logger.info("[rvo_webcontent] [dry-run] would scrape %s", url)
            seen_urls.add(url)
            continue

        rec = _scrape_article(url)
        seen_urls.add(url)
        if rec is None:
            time.sleep(_DELAY)
            continue

        with output_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written += 1

        if written % 100 == 0:
            logger.info("[rvo_webcontent] Sitemap scrape: %d articles written", written)

        time.sleep(_DELAY)

    logger.info("[rvo_webcontent] Sitemap path: %d articles written", written)
    return written


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _safe_id(s: str) -> str:
    """Convert arbitrary string to safe filename/ID component."""
    return re.sub(r"[^\w\-]", "_", s)[:80]


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


def _log(output_dir: Path, total: int) -> None:
    """Append an acquisition log entry."""
    entry = {
        "source":           SOURCE,
        "url":              _CKAN_BASE,
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
    max_packages: int  = _MAX_PACKAGES,
    max_articles: int  = _MAX_ARTICLES,
    dry_run:      bool = False,
    ckan_only:    bool = False,
    sitemap_only: bool = False,
) -> None:
    """
    Download RVO open webcontent.

    1. CKAN data.overheid.nl: structured datasets (CC0/PD packages only).
    2. Sitemap scrape: rvo.nl article pages (fallback / supplement).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "rvo_webcontent_articles.jsonl"

    seen_urls = _load_seen_urls(output_path)
    logger.info("[rvo_webcontent] %d URLs already seen (resume)", len(seen_urls))

    total = 0

    if not sitemap_only:
        total += _download_ckan(
            output_path, seen_urls,
            max_packages=max_packages,
            dry_run=dry_run,
        )

    if not ckan_only:
        total += _download_sitemap(
            output_path, seen_urls,
            max_articles=max_articles,
            dry_run=dry_run,
        )

    _log(output_dir, total)
    logger.info("[rvo_webcontent] Done. Total records written: %d", total)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download RVO (Rijksdienst voor Ondernemend Nederland) open webcontent (CC0)"
    )
    parser.add_argument("--output",       required=True, type=Path,
                        help="Output directory")
    parser.add_argument("--max-packages", type=int, default=_MAX_PACKAGES,
                        help="Max CKAN packages to inspect")
    parser.add_argument("--max-articles", type=int, default=_MAX_ARTICLES,
                        help="Max sitemap articles to scrape")
    parser.add_argument("--ckan-only",    action="store_true",
                        help="Only run CKAN path (skip sitemap scrape)")
    parser.add_argument("--sitemap-only", action="store_true",
                        help="Only run sitemap scrape (skip CKAN)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Log what would be fetched without writing")
    args = parser.parse_args()
    download(
        args.output,
        max_packages=args.max_packages,
        max_articles=args.max_articles,
        dry_run=args.dry_run,
        ckan_only=args.ckan_only,
        sitemap_only=args.sitemap_only,
    )


if __name__ == "__main__":
    main()
