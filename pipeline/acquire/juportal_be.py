"""
Download Belgian court decisions in Dutch from JuPortal (juportal.be).

JuPortal is the official portal for Belgian court decisions. Dutch-language decisions
are Public Domain under Belgian copyright law (Art. 8 Copyright Act — government acts).

Strategy:
  1. Try the JuPortal REST API at /api/ if it exists
  2. Search via the NL-language search page /nl/content/search
  3. For each ECLI found, fetch the decision page and extract Dutch text
  4. Rate-limit to 2s between requests

Language filter: only save decisions where Dutch text is detected (NL-language field).

Usage:
  python -m pipeline acquire --sources juportal_be
"""
from __future__ import annotations

import argparse
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

SOURCE  = "juportal_be"
LICENSE = "PD"
DOMAIN  = "legal"
COUNTRY = "BE"

_BASE     = "https://juportal.be"
_UA       = "NederCorpus/0.1 (academic research; open government data; contact: corpus@example.org)"
_DELAY    = 2.0   # seconds between requests
_MAX_DOCS = 20_000
_PAGE     = 25    # results per page

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ECLI pattern for Belgian courts: ECLI:BE:court:year:number
_ECLI_RE = re.compile(r'ECLI:BE:[A-Z0-9]+:\d{4}:[A-Z0-9._-]+', re.IGNORECASE)

# Dutch text indicators
_NL_KEYWORDS = [
    "de rechtbank", "het hof", "de wet", "overwegende", "gelet op",
    "beslist", "uitspraak", "arrest", "vonnis", "eiser", "verweerder",
]


def _fetch(url: str, retries: int = 3, return_bytes: bool = False) -> bytes | str | None:
    """Fetch URL, return bytes or str. None on failure."""
    headers = {"User-Agent": _UA, "Accept": "text/html,application/json,*/*"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
                data = resp.read()
                return data if return_bytes else data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            logger.warning("[juportal] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1)
            if exc.code in (403, 404, 410, 429):
                if exc.code == 429:
                    time.sleep(10.0)
                return None
            time.sleep(_DELAY * (attempt + 1))
        except Exception as exc:
            logger.warning("[juportal] Error for %s: %s (attempt %d)", url, exc, attempt + 1)
            time.sleep(_DELAY * (attempt + 1))
    return None


def _fetch_json(url: str) -> dict | list | None:
    """Fetch and parse JSON, return None on failure."""
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            time.sleep(_DELAY * (attempt + 1))
        except Exception as exc:
            logger.warning("[juportal] JSON fetch error for %s: %s", url, exc)
            time.sleep(_DELAY * (attempt + 1))
    return None


def _extract_text_from_html(html: str) -> str:
    """
    Extract the main text content from a JuPortal HTML page using
    xml.etree.ElementTree with an HTML-like approach.

    Since ET does not parse HTML, we use regex to strip tags.
    """
    # Remove script and style blocks
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)

    # Try to find the main content div (JuPortal typically uses article/main/div.content)
    content_match = re.search(
        r'<(?:article|main|div)[^>]*(?:class|id)=["\'][^"\']*(?:content|decision|judgment|tekst|text)[^"\']*["\'][^>]*>(.*?)</(?:article|main|div)>',
        text, flags=re.DOTALL | re.IGNORECASE
    )
    if content_match:
        text = content_match.group(1)

    # Strip all remaining HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)

    # Decode HTML entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<')
    text = text.replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)

    # Collapse whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _is_dutch(text: str) -> bool:
    """Heuristic check: does this text contain Dutch court language?"""
    text_lower = text.lower()
    hits = sum(1 for kw in _NL_KEYWORDS if kw in text_lower)
    return hits >= 2


def _extract_metadata_from_html(html: str, ecli: str) -> dict:
    """Extract date and court from HTML meta tags or page content."""
    # Try meta tags
    date = ""
    court = ""

    date_m = re.search(
        r'<meta[^>]+(?:name|property)=["\'](?:date|DC\.date|dcterms\.date)["\'][^>]+content=["\']([0-9\-/]+)["\']',
        html, re.IGNORECASE
    )
    if date_m:
        date = date_m.group(1)
    else:
        # Try visible date pattern in text
        date_m2 = re.search(r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}[/-]\d{2}[/-]\d{2})\b', html)
        if date_m2:
            date = date_m2.group(1)

    # Extract court from ECLI: ECLI:BE:COURT:year:number
    parts = ecli.split(":")
    if len(parts) >= 3:
        court = parts[2]

    title_m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    title = title_m.group(1).strip() if title_m else ecli

    return {"date": date, "court": court, "title": title}


def _try_api(output_dir: Path) -> list[str]:
    """Try the JuPortal API endpoint and collect ECLI identifiers."""
    eclis: list[str] = []

    # Probe API root
    result = _fetch_json(f"{_BASE}/api/")
    if result is not None:
        probe_path = output_dir / "juportal_api_probe.json"
        probe_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[juportal] API probe saved to %s", probe_path)

        # Walk JSON for ECLI identifiers
        raw = json.dumps(result)
        for m in _ECLI_RE.finditer(raw):
            ecli = m.group(0).upper()
            if ecli not in eclis:
                eclis.append(ecli)

    # Try common API search patterns
    api_patterns = [
        f"{_BASE}/api/decisions?language=NL&page=1",
        f"{_BASE}/api/search?language=NL&page=1",
        f"{_BASE}/api/judgments?language=nl&size=25",
    ]
    for url in api_patterns:
        r = _fetch_json(url)
        if r is not None:
            raw = json.dumps(r)
            for m in _ECLI_RE.finditer(raw):
                ecli = m.group(0).upper()
                if ecli not in eclis:
                    eclis.append(ecli)
            if eclis:
                break
        time.sleep(_DELAY)

    return eclis


def _search_page_eclis(page: int) -> list[str]:
    """
    Fetch one page of search results and extract ECLI identifiers.
    Tries both NL-language search URL patterns.
    """
    eclis: list[str] = []

    urls_to_try = [
        f"{_BASE}/nl/content/search?language=NL&page={page}",
        f"{_BASE}/nl/zoeken?taal=NL&pagina={page}",
        f"{_BASE}/search?language=nl&page={page}",
        f"{_BASE}/nl/resultaten?page={page}",
    ]

    for url in urls_to_try:
        html = _fetch(url)
        if html is None:
            continue
        # Look for ECLI patterns in the page
        for m in _ECLI_RE.finditer(html):
            ecli = m.group(0).upper()
            if ecli not in eclis:
                eclis.append(ecli)
        if eclis:
            return eclis
        # Try next URL pattern
        time.sleep(0.5)

    return eclis


def _fetch_decision(ecli: str) -> tuple[str, dict] | None:
    """
    Fetch the decision page for a given ECLI.
    Returns (text, metadata) or None if not Dutch or fetch failed.
    """
    # Try multiple URL patterns for the decision
    urls = [
        f"{_BASE}/content/{urllib.parse.quote(ecli)}",
        f"{_BASE}/nl/content/{urllib.parse.quote(ecli)}",
        f"{_BASE}/{urllib.parse.quote(ecli)}",
    ]

    for url in urls:
        html = _fetch(url)
        if html is None:
            continue

        text = _extract_text_from_html(html)
        if len(text) < 200:
            continue

        if not _is_dutch(text):
            logger.debug("[juportal] Skipping non-Dutch decision: %s", ecli)
            return None

        meta = _extract_metadata_from_html(html, ecli)
        return text, meta

    return None


def download(
    output_dir: Path,
    max_docs: int = _MAX_DOCS,
    dry_run: bool = False,
) -> None:
    """Download Dutch-language Belgian court decisions from JuPortal."""
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[juportal] [dry-run] would download up to %d JuPortal decisions", max_docs)
        return

    out_path = output_dir / "juportal_decisions.jsonl"

    # Resume: collect already-downloaded ECLIs
    seen_eclis: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    seen_eclis.add(json.loads(line)["ecli"])
                except (KeyError, json.JSONDecodeError):
                    pass
        logger.info("[juportal] Resuming — %d decisions already downloaded", len(seen_eclis))

    total_saved = len(seen_eclis)
    eclis_to_fetch: list[str] = []

    # -----------------------------------------------------------------------
    # Step 1: Collect ECLIs via API
    # -----------------------------------------------------------------------
    logger.info("[juportal] Probing JuPortal API...")
    api_eclis = _try_api(output_dir)
    if api_eclis:
        logger.info("[juportal] API returned %d ECLI identifiers", len(api_eclis))
        eclis_to_fetch.extend(api_eclis)
    time.sleep(_DELAY)

    # -----------------------------------------------------------------------
    # Step 2: Collect ECLIs via search pages
    # -----------------------------------------------------------------------
    logger.info("[juportal] Searching for decisions via search pages...")
    page = 1
    consecutive_empty = 0
    while len(eclis_to_fetch) < max_docs * 2 and consecutive_empty < 3:
        page_eclis = _search_page_eclis(page)
        new_eclis = [e for e in page_eclis if e not in seen_eclis and e not in eclis_to_fetch]
        if new_eclis:
            eclis_to_fetch.extend(new_eclis)
            logger.info("[juportal] Search page %d: +%d ECLIs (total queued: %d)",
                        page, len(new_eclis), len(eclis_to_fetch))
            consecutive_empty = 0
        else:
            consecutive_empty += 1
            logger.info("[juportal] Search page %d: no new ECLIs (empty=%d/3)", page, consecutive_empty)
        page += 1
        time.sleep(_DELAY)

    if not eclis_to_fetch:
        logger.warning(
            "[juportal] No ECLI identifiers collected from API or search. "
            "The JuPortal website structure may have changed. "
            "Check juportal_api_probe.json in %s for API details.", output_dir
        )

    # -----------------------------------------------------------------------
    # Step 3: Fetch each decision
    # -----------------------------------------------------------------------
    logger.info("[juportal] Fetching %d decisions...", len(eclis_to_fetch))
    with out_path.open("a", encoding="utf-8") as f:
        for ecli in eclis_to_fetch:
            if ecli in seen_eclis:
                continue
            if total_saved >= max_docs:
                break

            result = _fetch_decision(ecli)
            if result is None:
                seen_eclis.add(ecli)  # mark as attempted
                time.sleep(_DELAY)
                continue

            text, meta = result
            doc = {
                "ecli":             ecli,
                "title":            meta.get("title", ecli),
                "text":             text,
                "date":             meta.get("date", ""),
                "court":            meta.get("court", ""),
                "url":              f"{_BASE}/content/{urllib.parse.quote(ecli)}",
                "acquisition_date": acq_date,
            }
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            seen_eclis.add(ecli)
            total_saved += 1

            if total_saved % 100 == 0:
                logger.info("[juportal] Progress: %d decisions saved", total_saved)
            time.sleep(_DELAY)

    logger.info("[juportal] Done. %d Dutch decisions saved to %s", total_saved, out_path)

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _BASE,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_documents":  total_saved,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Belgian court decisions in Dutch from JuPortal (Public Domain)"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=_MAX_DOCS)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
