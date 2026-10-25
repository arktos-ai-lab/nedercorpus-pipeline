"""
Download Dutch government publications from Rijksoverheid.nl.

Strategy (2026-03 rewrite): the opendata.rijksoverheid.nl/v1/search API is
fully dead (404). The rijksoverheid.nl listing pages are JavaScript-rendered
and do not paginate server-side. The only working structured access is via
the RSS feeds at feeds.rijksoverheid.nl/{type}.rss, which return the 20 most
recent items per feed type. This acquirer fetches each RSS feed, follows each
article link to obtain the full page text, and saves the result as a JSON file
compatible with the existing parse_rijksoverheid_dir parser.

Feeds harvested:
  nieuws         — press releases / news
  kamerstukken   — letters to parliament
  toespraken     — ministerial speeches

License: CC0
  The feeds declare: "Iedere vorm van hergebruik van de inhoud van deze
  feed is toegestaan." Rijksoverheid.nl content is produced by the Dutch
  national government and is in the public domain (CC0) under the Dutch
  Government Open Data policy.

Usage:
  python -m pipeline acquire --sources rijksoverheid
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "rijksoverheid"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "government"

RIJKSOVERHEID_BASE = "https://www.rijksoverheid.nl"
RSS_BASE           = "https://feeds.rijksoverheid.nl"

HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic research; open data)",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9",
}

_FEED_TYPES = ["nieuws", "kamerstukken", "toespraken"]


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
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
            logger.warning("[rijksoverheid] retry %d %s: %s", attempt + 1, url[-50:], exc)
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _strip_html(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&[a-z]+;', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _parse_rss(xml: bytes) -> list[dict]:
    """Parse RSS feed and return list of {title, link, description, pubDate, guid}."""
    text = xml.decode("utf-8", errors="replace")
    items = []
    for item_m in re.finditer(r'<item>(.*?)</item>', text, re.S):
        item_xml = item_m.group(1)
        def _tag(t: str) -> str:
            m = re.search(rf'<{t}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>', item_xml, re.S)
            return m.group(1).strip() if m else ""
        items.append({
            "title":       _tag("title"),
            "link":        _tag("link"),
            "description": _tag("description"),
            "pubDate":     _tag("pubDate"),
            "guid":        _tag("guid"),
        })
    return items


def _extract_article(html: str, url: str) -> dict:
    """
    Extract article fields from a rijksoverheid.nl page.

    Returns dict with keys matching parse_rijksoverheid_dir expectations:
    title, introductie, body, available, canonical, creator.
    """
    # Title from og:title or <title>
    title = ""
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r'<title>([^<]+)</title>', html)
        if m:
            title = m.group(1).split('|')[0].strip()

    # Date from DCTERMS.date or og:updated
    date_str = ""
    for pattern in [
        r'DCTERMS\.date[^>]+content="([^"]+)"',
        r'DCTERMS\.modified[^>]+content="([^"]+)"',
        r'<pubDate>([^<]+)</pubDate>',
    ]:
        m = re.search(pattern, html)
        if m:
            date_str = m.group(1).strip()
            break

    # Creator (ministry)
    creator = ""
    m = re.search(r'RIJKSOVERHEID\.Organisatie[^>]+content="([^"]+)"', html)
    if m:
        creator = m.group(1).strip()

    # Intro: <div class="intro"> or <p class="intro">
    intro = ""
    m = re.search(r'<(?:div|p)[^>]+class="intro"[^>]*>(.*?)</(?:div|p)>', html, re.S)
    if m:
        intro = _strip_html(m.group(1))

    # Body: <div class="article content"> or <div class="content">
    body = ""
    for pattern in [
        r'<div[^>]+class="article content"[^>]*>(.*?)</div>',
        r'<div[^>]+class="[^"]*richText[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]+class="block"[^>]*>(.*?)</div>',
    ]:
        m = re.search(pattern, html, re.S)
        if m:
            body = _strip_html(m.group(1))
            if len(body) > 100:
                break

    return {
        "title":       title,
        "introductie": intro,
        "body":        body,
        "available":   date_str,
        "canonical":   url,
        "creator":     creator,
    }


def _safe_slug(url: str) -> str:
    path = urllib.parse.urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if "/" in path else path
    return re.sub(r"[^\w\-]", "_", slug)[:100] or "article"


def download_feed(
    output_dir: Path,
    feed_type: str,
    dry_run: bool = False,
) -> int:
    """Fetch one RSS feed and download all linked articles."""
    feed_url = f"{RSS_BASE}/{feed_type}.rss"
    dest_dir = output_dir / feed_type
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = 0

    try:
        rss_bytes = _fetch(feed_url)
    except Exception as exc:
        logger.warning("[rijksoverheid] feed %s failed: %s", feed_type, exc)
        return 0

    items = _parse_rss(rss_bytes)
    logger.info("[rijksoverheid] feed %s: %d items", feed_type, len(items))

    for item in items:
        link = item.get("link", "").strip()
        if not link:
            continue

        slug   = _safe_slug(link)
        dest   = dest_dir / f"{slug}.json"
        if dest.exists():
            continue

        if dry_run:
            logger.info("[dry-run] would fetch %s", link)
            saved += 1
            continue

        try:
            html_bytes = _fetch(link, timeout=20)
        except Exception as exc:
            logger.debug("[rijksoverheid] skip %s: %s", link[-60:], exc)
            continue

        html   = html_bytes.decode("utf-8", errors="replace")
        fields = _extract_article(html, link)

        # Fallback: use RSS description if body extraction failed
        if not fields["body"] and item.get("description"):
            fields["body"] = _strip_html(item["description"])
        if not fields["title"] and item.get("title"):
            fields["title"] = item["title"]
        if not fields["available"] and item.get("pubDate"):
            fields["available"] = item["pubDate"]

        text = " ".join(filter(None, [fields["title"], fields["introductie"], fields["body"]]))
        if len(text) < 80:
            continue

        dest.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
        dest.with_suffix(".json.url").write_text(link, encoding="utf-8")
        _log(output_dir, link)
        saved += 1
        time.sleep(0.3)

    logger.info("[rijksoverheid] feed %s: %d new articles saved", feed_type, saved)
    return saved


def download(
    output_dir: Path,
    feed_types: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """Download all RSS feed types from Rijksoverheid.nl."""
    output_dir.mkdir(parents=True, exist_ok=True)
    feeds = feed_types or _FEED_TYPES
    total = 0
    for feed in feeds:
        total += download_feed(output_dir, feed, dry_run=dry_run)
        time.sleep(1.0)
    logger.info("[rijksoverheid] Done. %d new articles total.", total)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Rijksoverheid.nl publications via RSS (CC0)"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--feeds",    nargs="+", default=None, choices=_FEED_TYPES)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, feed_types=args.feeds, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
