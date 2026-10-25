"""
Download Onderzoeksraad voor Veiligheid (Dutch Safety Board) investigation reports.

The OVV investigates serious accidents and near-misses in aviation, rail, maritime,
chemical, and other safety-critical domains. Reports are government publications
in the public domain (Art. 11 Auteurswet).

License: PD (Art. 11 Auteurswet)
URL:     https://onderzoeksraad.nl/onderzoeken/

Usage:
  python -m pipeline acquire --sources ovv
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.request
import urllib.error
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "ovv"
LICENSE = "PD"
DOMAIN  = "government"
COUNTRY = "NL"

_BASE  = "https://onderzoeksraad.nl"
_UA    = "NederCorpus/0.1 (academic research; contact: corpus@example.org)"
_DELAY = 0.8
_MAX   = 5_000

_SITEMAPS = [
    "https://onderzoeksraad.nl/investigation-sitemap.xml",
    "https://onderzoeksraad.nl/investigation-sitemap2.xml",
    "https://onderzoeksraad.nl/investigation-sitemap3.xml",
]

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _get_html(url: str, retries: int = 3) -> str | None:
    headers = {"User-Agent": _UA, "Accept": "text/html"}
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
            logger.warning("[ovv] %s: %s (attempt %d)", url, exc, attempt + 1)
            time.sleep(2.0 * (attempt + 1))
    return None


def _strip_tags(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&[a-z]{2,6};", "", html)
    return re.sub(r"\s+", " ", html).strip()


def _collect_sitemap_urls() -> list[str]:
    """Collect all Dutch investigation URLs from the OVV sitemaps."""
    import xml.etree.ElementTree as ET
    urls: list[str] = []
    for sm_url in _SITEMAPS:
        xml_text = _get_html(sm_url)
        if not xml_text:
            continue
        xml_clean = re.sub(r' xmlns="[^"]+"', '', xml_text)
        try:
            root = ET.fromstring(xml_clean)
            for loc in root.findall(".//url/loc"):
                if loc.text:
                    urls.append(loc.text.strip())
        except ET.ParseError:
            for loc in re.findall(r'<loc>([^<]+)</loc>', xml_text):
                urls.append(loc.strip())
        time.sleep(0.5)
    return [u for u in urls if "/onderzoek/" in u and u.count("/") >= 5]


def _scrape_investigation_page_playwright(url: str, page) -> tuple[str, str]:
    """Extract (text, title) from a JS-rendered investigation page using Playwright."""
    try:
        page.goto(url, wait_until="networkidle", timeout=30_000)
    except Exception:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            return "", ""

    # Try to extract structured content
    title = ""
    try:
        h1 = page.locator("h1").first
        title = h1.inner_text(timeout=3_000).strip()
    except Exception:
        pass

    # Try main content selectors (OVV uses React with semantic HTML)
    for selector in ["main", "article", "[class*='content']", "[class*='rapport']", "body"]:
        try:
            el = page.locator(selector).first
            text = el.inner_text(timeout=3_000).strip()
            text = re.sub(r"\s+", " ", text)
            if len(text) > 300:
                return text[:150_000], title
        except Exception:
            continue

    return "", title


def _scrape_investigation_page_http(url: str) -> tuple[str, str]:
    """Fallback HTTP scraper for OVV investigation pages (no Playwright)."""
    html = _get_html(url)
    if not html:
        return "", ""

    title = ""
    tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)
    if tm:
        title = _strip_tags(tm.group(1)).strip()

    # Try structured selectors in order
    for pattern in [
        r'<main[^>]*>(.*?)</main>',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]+class="[^"]*(?:content|body|investigation|rapport|onderzoek)[^"]*"[^>]*>(.*?)</div>',
        r'<section[^>]+class="[^"]*(?:content|body)[^"]*"[^>]*>(.*?)</section>',
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            text = _strip_tags(m.group(1))
            if len(text) > 300:
                return text[:150_000], title

    # Full-page fallback
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = _strip_tags(html)
    return (text[:150_000], title) if len(text) > 300 else ("", title)


def _scrape_with_playwright(all_urls: list[str], out_path: Path, seen_urls: set[str],
                             total_saved: int, max_docs: int, acq_date: str) -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_UA)
        page = context.new_page()

        with out_path.open("a", encoding="utf-8") as f:
            for url in all_urls:
                if total_saved >= max_docs:
                    break
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                text, title = _scrape_investigation_page_playwright(url, page)
                if len(text) < 300:
                    time.sleep(_DELAY * 0.3)
                    continue

                slug = url.rstrip("/").split("/")[-1]
                doc = {
                    "id":               slug,
                    "title":            title or slug.replace("-", " "),
                    "text":             text[:150_000],
                    "url":              url,
                    "acquisition_date": acq_date,
                }
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                total_saved += 1
                if total_saved % 25 == 0:
                    logger.info("[ovv] %d reports saved", total_saved)
                time.sleep(_DELAY)

        context.close()
        browser.close()
    return total_saved


def _scrape_with_http(all_urls: list[str], out_path: Path, seen_urls: set[str],
                      total_saved: int, max_docs: int, acq_date: str) -> int:
    with out_path.open("a", encoding="utf-8") as f:
        for url in all_urls:
            if total_saved >= max_docs:
                break
            if url in seen_urls:
                continue
            seen_urls.add(url)

            text, title = _scrape_investigation_page_http(url)
            if len(text) < 300:
                time.sleep(_DELAY * 0.3)
                continue

            slug = url.rstrip("/").split("/")[-1]
            doc = {
                "id":               slug,
                "title":            title or slug.replace("-", " "),
                "text":             text[:150_000],
                "url":              url,
                "acquisition_date": acq_date,
            }
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            total_saved += 1
            if total_saved % 25 == 0:
                logger.info("[ovv] %d reports saved", total_saved)
            time.sleep(_DELAY)
    return total_saved


def download(output_dir: Path, max_docs: int = _MAX, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[ovv] [dry-run] would download up to %d OVV reports", max_docs)
        return

    out_path = output_dir / "ovv_reports.jsonl"
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
        logger.info("[ovv] Resuming — %d reports already saved", total_saved)

    logger.info("[ovv] Collecting investigation URLs from sitemaps...")
    all_urls = _collect_sitemap_urls()
    logger.info("[ovv] Found %d investigation URLs", len(all_urls))

    if not all_urls:
        logger.warning("[ovv] No URLs found from sitemaps — trying direct page scrape")
        html = _get_html(f"{_BASE}/onderzoeken/")
        if html:
            for m in re.finditer(r'href="(/onderzoek/[^"?#]+)"', html):
                all_urls.append(_BASE + m.group(1))
        logger.info("[ovv] Found %d URLs via direct scrape", len(all_urls))

    try:
        total_saved = _scrape_with_playwright(
            all_urls, out_path, seen_urls, total_saved, max_docs, acq_date
        )
    except Exception as exc:
        logger.warning("[ovv] Playwright unavailable (%s); falling back to HTTP scraper", exc)
        total_saved = _scrape_with_http(
            all_urls, out_path, seen_urls, total_saved, max_docs, acq_date
        )

    logger.info("[ovv] Done. %d reports -> %s", total_saved, out_path)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": _BASE, "license": LICENSE,
            "acquisition_date": acq_date, "total_documents": total_saved,
        }, ensure_ascii=False) + "\n")
