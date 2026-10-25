"""
Download ACM (Autoriteit Consument & Markt) decisions and publications.

ACM is the Dutch competition and consumer protection authority. Its published
decisions, market studies, and enforcement guidelines are government publications
in the public domain (Art. 11 Auteurswet) / CC0.

License: CC0 / PD (Art. 11 Auteurswet)
URL:     https://www.acm.nl/nl/publicaties

Usage:
  python -m pipeline acquire --sources acm
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

SOURCE  = "acm"
LICENSE = "PD"
DOMAIN  = "government"
COUNTRY = "NL"

_BASE        = "https://www.acm.nl"
_SITEMAP_BASE = "https://www.acm.nl/sitemap.xml?page="
_SITEMAP_PAGES = 9
_UA    = "NederCorpus/0.1 (academic research; contact: corpus@example.org)"
_DELAY = 0.8
_MAX   = 10_000

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
            logger.debug("[acm] HTTP %d for %s", e.code, url)
            if e.code in (400, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[acm] %s: %s (attempt %d)", url, exc, attempt + 1)
            time.sleep(2.0 * (attempt + 1))
    return None


def _strip_tags(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&[a-z]{2,8};", "", html)
    return re.sub(r"\s+", " ", html).strip()


def _collect_sitemap_urls() -> list[str]:
    """Collect all Dutch publication URLs from ACM sitemaps."""
    import xml.etree.ElementTree as ET
    urls: list[str] = []
    for page in range(1, _SITEMAP_PAGES + 1):
        xml_text = _get_html(f"{_SITEMAP_BASE}{page}")
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
        time.sleep(0.3)
    return [u for u in urls if "/nl/publicaties/" in u and len(u) > 50]


def _scrape_publication(url: str) -> str:
    """Extract main text from an ACM publication page."""
    html = _get_html(url)
    if not html:
        return ""

    # Remove nav/header/footer
    html = re.sub(r"<(nav|header|footer|script|style|aside)[^>]*>.*?</\1>", " ",
                  html, flags=re.DOTALL | re.IGNORECASE)

    for pat in [
        r'<div[^>]+class="[^"]*(?:article|content|publicatie|tekst|body|main)[^"]*"[^>]*>(.*?)</div\s*>',
        r'<article[^>]*>(.*?)</article>',
        r'<main[^>]*>(.*?)</main>',
    ]:
        m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
        if m:
            text = _strip_tags(m.group(1))
            if len(text) > 300:
                return text

    return _strip_tags(html)


def download(output_dir: Path, max_docs: int = _MAX, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[acm] [dry-run] would download up to %d ACM publications", max_docs)
        return

    out_path = output_dir / "acm_publications.jsonl"
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
        logger.info("[acm] Resuming — %d publications already saved", total_saved)

    logger.info("[acm] Collecting publication URLs from sitemaps…")
    all_urls = _collect_sitemap_urls()
    logger.info("[acm] Found %d publication URLs", len(all_urls))

    with out_path.open("a", encoding="utf-8") as f:
        for url in all_urls:
            if total_saved >= max_docs:
                break
            if url in seen_urls:
                continue
            seen_urls.add(url)

            text = _scrape_publication(url)
            if len(text) < 300:
                time.sleep(_DELAY * 0.3)
                continue

            slug = url.rstrip("/").split("/")[-1]
            doc = {
                "id":               slug,
                "title":            slug.replace("-", " "),
                "text":             text[:200_000],
                "url":              url,
                "acquisition_date": acq_date,
            }
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            total_saved += 1
            if total_saved % 50 == 0:
                logger.info("[acm] %d publications saved", total_saved)
            time.sleep(_DELAY)

    logger.info("[acm] Done. %d publications → %s", total_saved, out_path)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": _BASE, "license": LICENSE,
            "acquisition_date": acq_date, "total_documents": total_saved,
        }, ensure_ascii=False) + "\n")
