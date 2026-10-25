"""
Download SCP (Sociaal en Cultureel Planbureau) publications.

The SCP is the Dutch government's social and cultural policy research bureau.
All publications are CC0 1.0 Universal (confirmed on scp.nl/copyright).

Source: https://www.scp.nl/publicaties
License: CC0 1.0
Domain: social policy research / government
Country: NL
~500+ reports, working papers, and fact sheets in Dutch
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

SOURCE  = "scp"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "government"

_BASE_URL   = "https://www.scp.nl"
_PUBS_URL   = "https://www.scp.nl/publicaties"
_USER_AGENT = "NederCorpus/0.1 (academic research; open government data)"
_MAX_PAGES  = 300


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


def _extract_links(html: str) -> list[str]:
    p = _LinkParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.links


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()
    out_file = output_dir / "scp_records.jsonl"

    if out_file.exists() and out_file.stat().st_size > 0:
        logger.info("[scp] Already acquired: %s — skipping.", out_file)
        return

    if dry_run:
        logger.info("[scp] [dry-run] Would scrape %s", _PUBS_URL)
        return

    logger.info("[scp] Scraping publications from %s", _PUBS_URL)
    _log(output_dir, _PUBS_URL)

    seen_detail_urls: set[str] = set()
    total = 0
    consecutive_empty = 0

    with out_file.open("w", encoding="utf-8") as fout:
        for page_num in range(_MAX_PAGES):
            list_url = f"{_PUBS_URL}?pagina={page_num + 1}" if page_num > 0 else _PUBS_URL

            try:
                html = _fetch(list_url).decode("utf-8", errors="replace")
            except Exception as exc:
                logger.warning("[scp] Page %d fetch failed: %s", page_num, exc)
                break

            # Find publication detail links
            raw_links = _extract_links(html)
            detail_links = []
            for href in raw_links:
                if re.search(r'/publicaties/\S+', href) and href not in ("/publicaties", "/publicaties/"):
                    full = href if href.startswith("http") else _BASE_URL + href
                    if full not in seen_detail_urls:
                        seen_detail_urls.add(full)
                        detail_links.append(full)

            if not detail_links:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    logger.info("[scp] 3 empty pages — done.")
                    break
                page_num_next = page_num + 1
                logger.debug("[scp] Page %d: no new links", page_num)
                time.sleep(0.5)
                continue
            else:
                consecutive_empty = 0

            # Fetch each detail page and extract metadata + abstract
            for detail_url in detail_links:
                try:
                    detail_html = _fetch(detail_url).decode("utf-8", errors="replace")
                except Exception as exc:
                    logger.debug("[scp] Detail %s: %s", detail_url, exc)
                    time.sleep(0.3)
                    continue

                # Extract title from <title> or <h1>
                title_m = re.search(r'<title[^>]*>([^<]+)</title>', detail_html, re.IGNORECASE)
                title = title_m.group(1).strip() if title_m else ""
                # Clean " | SCP" suffix
                title = re.sub(r'\s*[\|–-]\s*SCP.*$', '', title).strip()

                # Extract description/abstract from meta or article body
                desc_m = re.search(
                    r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
                    detail_html, re.IGNORECASE
                )
                description = desc_m.group(1).strip() if desc_m else ""

                # Also pull article text paragraphs (stripping HTML)
                body_text = re.sub(r'<script[^>]*>.*?</script>', '', detail_html,
                                   flags=re.DOTALL | re.IGNORECASE)
                body_text = re.sub(r'<style[^>]*>.*?</style>', '', body_text,
                                   flags=re.DOTALL | re.IGNORECASE)
                paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', body_text,
                                       flags=re.DOTALL | re.IGNORECASE)
                clean_pars = []
                for p in paragraphs:
                    clean = re.sub(r'<[^>]+>', ' ', p)
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    if len(clean) > 40:
                        clean_pars.append(clean)

                text_parts = []
                if title:
                    text_parts.append(title)
                if description:
                    text_parts.append(description)
                text_parts.extend(clean_pars[:20])  # cap at 20 paragraphs
                text = "\n".join(text_parts)

                if len(text) < 100:
                    time.sleep(0.3)
                    continue

                # Extract year
                year: int | None = None
                year_m = re.search(r'\b(19|20)\d{2}\b', detail_html)
                if year_m:
                    year = int(year_m.group(0))

                record = {
                    "title": title,
                    "text": text,
                    "year": year,
                    "source_url": detail_url,
                    "license": LICENSE,
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1
                time.sleep(0.4)

            logger.info("[scp] Page %d: %d total records", page_num + 1, total)
            time.sleep(0.5)

    logger.info("[scp] Done. %d records saved.", total)
