"""
Download Dutch National Ombudsman reports and publications.

The Nationale Ombudsman investigates citizens' complaints against government bodies.
Reports are in the public domain (Art. 11 Auteurswet — government publications).

The publications page is paginated HTML; each entry links to a PDF or HTML report.

License: PD (Art. 11 Auteurswet)
URL:     https://www.nationaleombudsman.nl/publicaties

Usage:
  python -m pipeline acquire --sources nationale_ombudsman
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.request
import urllib.error
import urllib.parse
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "nationale_ombudsman"
LICENSE = "PD"
DOMAIN  = "government"
COUNTRY = "NL"

_BASE  = "https://www.nationaleombudsman.nl"
_UA    = "NederCorpus/0.1 (academic research; contact: corpus@example.org)"
_DELAY = 1.0
_MAX   = 5_000

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _get_html(url: str, retries: int = 3) -> str | None:
    headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[ombudsman] %s: %s", url, exc)
            time.sleep(2.0 * (attempt + 1))
    return None


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&[a-z]{2,6};", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _scrape_publication_list(page: int) -> list[dict]:
    """Scrape one page of the publications listing. Returns list of {title, url}."""
    # page=0 → ?page=0 is first page on Drupal sites; also try without page param for p=0
    url = f"{_BASE}/publicaties?page={page}" if page > 0 else f"{_BASE}/publicaties"
    html = _get_html(url)
    if not html:
        return []

    results = []
    seen: set[str] = set()

    # Pattern 1: /publicaties/TYPE/SLUG (original)
    for m in re.finditer(r'href="(/publicaties/(?:rapporten|brieven|onderzoeken|jaarverslagen|adviezen)/[^"?#]+)"', html):
        path = m.group(1).rstrip("/")
        if path in seen:
            continue
        seen.add(path)
        slug = path.rstrip("/").split("/")[-1]
        results.append({"url": f"{_BASE}{path}", "title": slug.replace("-", " ")})

    # Pattern 2: any /publicaties/... path (broader catch)
    if not results:
        for m in re.finditer(r'href="(/publicaties/[a-z0-9][^"?#]{5,})"', html):
            path = m.group(1).rstrip("/")
            if path in seen or path == "/publicaties":
                continue
            seen.add(path)
            slug = path.rstrip("/").split("/")[-1]
            results.append({"url": f"{_BASE}{path}", "title": slug.replace("-", " ")})

    # Pattern 3: absolute URLs with nationaleombudsman domain
    if not results:
        for m in re.finditer(r'href="(https://www\.nationaleombudsman\.nl/publicaties/[^"?#]+)"', html):
            url2 = m.group(1).rstrip("/")
            if url2 in seen:
                continue
            seen.add(url2)
            slug = url2.rstrip("/").split("/")[-1]
            results.append({"url": url2, "title": slug.replace("-", " ")})

    return results


def _scrape_report_page(url: str) -> str:
    """Extract the main text content from a report page."""
    html = _get_html(url)
    if not html:
        return ""
    # Try to find main content div
    for pattern in [
        r'<div[^>]+class="[^"]*(?:content|body|rapport|tekst|article)[^"]*"[^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
        r'<main[^>]*>(.*?)</main>',
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            text = _strip_tags(m.group(1))
            if len(text) > 300:
                return text

    # Fallback: strip full page
    # Remove navigation, header, footer blocks first
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    return _strip_tags(html)


def download(output_dir: Path, max_docs: int = _MAX, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[ombudsman] [dry-run] would download up to %d reports", max_docs)
        return

    out_path = output_dir / "ombudsman_reports.jsonl"
    seen_urls: set[str] = set()
    total_saved = 0

    if out_path.exists():
        with out_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    doc = json.loads(line)
                    seen_urls.add(doc.get("url", ""))
                    total_saved += 1
                except Exception:
                    pass
        logger.info("[ombudsman] Resuming — %d reports already saved", total_saved)

    with out_path.open("a", encoding="utf-8") as f:
        page = 0
        consecutive_empty = 0
        while total_saved < max_docs:
            pubs = _scrape_publication_list(page)
            if not pubs:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    logger.info("[ombudsman] No more pages at page=%d", page)
                    break
                page += 1
                continue
            consecutive_empty = 0

            for pub in pubs:
                if total_saved >= max_docs:
                    break
                url = pub["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                text = _scrape_report_page(url)
                if len(text) < 300:
                    logger.debug("[ombudsman] Too short, skipping: %s", url)
                    time.sleep(_DELAY * 0.5)
                    continue

                doc = {
                    "id":               url.rstrip("/").split("/")[-1],
                    "title":            pub["title"],
                    "text":             text[:100_000],  # cap at 100K chars
                    "url":              url,
                    "acquisition_date": acq_date,
                }
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                total_saved += 1
                if total_saved % 50 == 0:
                    logger.info("[ombudsman] %d reports saved", total_saved)
                time.sleep(_DELAY)

            page += 1
            time.sleep(_DELAY)

    logger.info("[ombudsman] Done. %d reports → %s", total_saved, out_path)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": _BASE, "license": LICENSE,
            "acquisition_date": acq_date, "total_documents": total_saved,
        }, ensure_ascii=False) + "\n")
