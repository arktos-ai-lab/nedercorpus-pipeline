"""
Download CBS (Centraal Bureau voor de Statistiek) long-read articles.

CBS publishes in-depth articles and analyses at cbs.nl/nl-nl/longread/.
These are substantial Dutch-language data-journalism pieces on statistics,
demographic trends, and policy topics.

License: CC-BY 4.0
Source: https://www.cbs.nl/nl-nl/longread/
Domain: government / science
Country: NL
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "cbs_longreads"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "government"

_SITEMAP_URL = "https://www.cbs.nl/nl-nl/sitemaps/sitemap"
_BASE_URL    = "https://www.cbs.nl"
_USER_AGENT  = "NederCorpus/0.1 (academic research; open statistics data)"


def _fetch(url: str, retries: int = 3, timeout: int = 30) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href", "")
            if href:
                self.links.append(href)


def _get_links(html: str) -> list[str]:
    p = _LinkParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.links


def _clean_html(html: str) -> str:
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    return html


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False, max_items: int = 1000) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()
    out_file = output_dir / "cbs_longreads.jsonl"

    if out_file.exists() and out_file.stat().st_size > 0:
        logger.info("[cbs_longreads] Already acquired — skipping.")
        return

    if dry_run:
        logger.info("[cbs_longreads] [dry-run] Would scrape %s", _SITEMAP_URL)
        return

    logger.info("[cbs_longreads] Fetching sitemap from %s", _SITEMAP_URL)
    _log(output_dir, _SITEMAP_URL)

    # Collect longread article URLs from the CBS sitemap
    try:
        xml_bytes = _fetch(_SITEMAP_URL)
    except Exception as exc:
        logger.warning("[cbs_longreads] Sitemap fetch failed: %s", exc)
        return

    all_urls = re.findall(r'<loc>(https://www\.cbs\.nl/nl-nl/[^<]+)</loc>', xml_bytes.decode("utf-8", errors="replace"))
    article_urls = [
        u for u in all_urls
        if "/nl-nl/longread/" in u
        and not u.endswith("/longread/")
    ]
    logger.info("[cbs_longreads] Found %d longread URLs in sitemap", len(article_urls))

    if not article_urls:
        logger.warning("[cbs_longreads] No longread URLs in sitemap.")
        return

    article_urls = article_urls[:max_items]
    logger.info("[cbs_longreads] Downloading %d longread articles...", len(article_urls))
    total = 0

    with out_file.open("w", encoding="utf-8") as fout:
        for article_url in article_urls:
            try:
                html = _fetch(article_url).decode("utf-8", errors="replace")
            except Exception as exc:
                logger.debug("[cbs_longreads] Article fetch failed %s: %s", article_url, exc)
                time.sleep(0.5)
                continue

            # Title
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            title = title_m.group(1).strip() if title_m else ""
            title = re.sub(r'\s*[\|–-]\s*CBS.*$', '', title).strip()

            # Meta description
            desc_m = re.search(
                r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
                html, re.IGNORECASE
            )
            description = desc_m.group(1).strip() if desc_m else ""

            # Body paragraphs
            clean = _clean_html(html)

            # --- S2A: Extract statistical anchors (tables and charts) ---
            ledger = []
            # StatLine tables often have links like statline.cbs.nl/Statweb/publication/?VW=T&DM=SLNL&PA=84992NED
            statline_m = re.findall(r'statline\.cbs\.nl/[^"\'>]+\bPA=([A-Z0-9]+)', html)
            for table_id in set(statline_m):
                ledger.append({
                    "entry_type": "statistical_anchor",
                    "evidence_pointer": f"CBS:StatLine:{table_id}",
                    "method": "deterministic",
                    "metadata": {"url": f"https://statline.cbs.nl/Statweb/publication/?PA={table_id}"}
                })

            # Datawrapper or CBS-hosted charts
            chart_m = re.findall(r'https://[^"\'>]+/(?:chart|graph)/([a-zA-Z0-9]+)', html)
            for chart_id in set(chart_m):
                ledger.append({
                    "entry_type": "statistical_anchor",
                    "evidence_pointer": f"CBS:Chart:{chart_id}",
                    "method": "deterministic"
                })

            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', clean, flags=re.DOTALL | re.IGNORECASE)
            body_parts = []
            for p in paragraphs:
                text = re.sub(r'<[^>]+>', ' ', p)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 40:
                    body_parts.append(text)

            full_text = "\n".join(
                [t for t in [title, description] + body_parts[:40] if t]
            )
            if len(full_text) < 200:
                time.sleep(0.4)
                continue

            # Year
            year: int | None = None
            year_m = re.search(r'\b(20\d{2})\b', article_url)
            if year_m:
                year = int(year_m.group(1))

            record = {
                "title": title,
                "text": full_text,
                "year": year,
                "source_url": article_url,
                "license": LICENSE,
                "country": COUNTRY,
                "domain": DOMAIN,
                "acquisition_date": acq_date,
                "ledger": ledger,
            }
            fout.write(json.dumps(record, ensure_ascii=False) + "\n")
            total += 1
            time.sleep(0.4)

    logger.info("[cbs_longreads] Done. %d articles saved.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download CBS longread articles.")
    parser.add_argument("--output",    required=True, type=Path, help="Output directory")
    parser.add_argument("--dry-run",   action="store_true", help="Log without downloading")
    parser.add_argument("--max-items", type=int, default=1000, help="Max items to download")
    args = parser.parse_args()

    # The download function in this file doesn't take max_items yet,
    # but we can add a limit if needed. For now just run it.
    download(args.output, dry_run=args.dry_run, max_items=args.max_items)


if __name__ == "__main__":
    main()
