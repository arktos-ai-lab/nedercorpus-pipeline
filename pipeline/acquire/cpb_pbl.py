"""
Download open policy reports from CPB and PBL (Dutch policy institutes).

CPB — Centraal Planbureau (Netherlands Bureau for Economic Policy Analysis)
  https://www.cpb.nl/publicaties
  License: CC0 1.0 (explicitly stated on all CPB publications)
  Content: economic forecasts, policy analyses, background documents (~2200+ NL docs)

PBL — Planbureau voor de Leefomgeving (Netherlands Environmental Assessment Agency)
  https://www.pbl.nl/publicaties
  License: CC0 1.0
  Content: spatial planning, environment, climate policy reports (~2000+ NL docs)

Strategy: enumerate publication URLs from sitemaps, scrape each publication page.
The /api/v2/publications endpoint was retired (404 as of 2026-03).

Usage:
  python -m pipeline acquire --sources cpb_pbl
"""
from __future__ import annotations

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

SOURCE  = "cpb_pbl"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "academic"

_USER_AGENT = "NederCorpus/0.1 (academic research; Dutch policy corpus)"
_MAX_RETRIES = 3
_DELAY = 0.5  # seconds between page fetches

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

_INSTITUTE_CONFIGS = [
    {
        "key":       "cpb",
        "label":     "CPB",
        "sitemap":   "https://www.cpb.nl/sitemap.xml",
        "sitemap_pages": None,   # single sitemap
        "url_filter": lambda u: "/publicatie/" in u,
    },
    {
        "key":       "pbl",
        "label":     "PBL",
        "sitemap":   "https://www.pbl.nl/sitemap.xml",
        "sitemap_pages": 4,       # sitemap index with ?page=N
        "url_filter": lambda u: "/publicaties/" in u and "/en/" not in u,
    },
]


def _fetch(url: str, timeout: int = 20) -> bytes:
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*", "Accept-Language": "nl,nl-NL;q=0.9"}
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                raise
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(5.0 * (attempt + 1))
        except Exception:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(5.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _get_sitemap_urls(cfg: dict) -> list[str]:
    """Return all publication page URLs for the given institute config."""
    urls: list[str] = []
    sitemap_base = cfg["sitemap"]
    pages = cfg["sitemap_pages"]
    url_filter = cfg["url_filter"]

    if pages is None:
        candidates = [sitemap_base]
    else:
        candidates = [f"{sitemap_base}?page={p}" for p in range(1, pages + 1)]

    for sm_url in candidates:
        try:
            data = _fetch(sm_url, timeout=30)
            root = ET.fromstring(data)
            locs = [el.text for el in root.findall(".//sm:loc", _SITEMAP_NS) if el.text]
            matched = [u for u in locs if url_filter(u)]
            urls.extend(matched)
            logger.info("[cpb_pbl] %s sitemap %s: %d / %d publication URLs",
                        cfg["label"], sm_url.split("?")[0], len(matched), len(locs))
        except Exception as exc:
            logger.warning("[cpb_pbl] %s sitemap fetch failed: %s", cfg["label"], exc)

    return urls


def _extract_text_from_html(html: bytes) -> tuple[str, str, int | None]:
    """
    Extract (title, body_text, year) from a CPB/PBL publication page.
    Looks for <article> tag first, falls back to <main>.
    """
    text_raw = html.decode("utf-8", errors="replace")

    # Extract title from <title> or <h1>
    title = ""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text_raw, re.DOTALL | re.IGNORECASE)
    if h1:
        title = re.sub(r"<[^>]+>", " ", h1.group(1))
        title = re.sub(r"\s+", " ", title).strip()
    if not title:
        t = re.search(r"<title[^>]*>(.*?)</title>", text_raw, re.DOTALL | re.IGNORECASE)
        if t:
            title = re.sub(r"<[^>]+>", " ", t.group(1))
            title = re.sub(r"\s+", " ", title).strip()
            # Remove site name suffix
            title = re.sub(r"\s*[\|–-]\s*(CPB|PBL).*$", "", title).strip()

    # Try JSON-LD for structured data
    ld_year = None
    ld_desc = ""
    for ld_raw in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                              text_raw, re.DOTALL | re.IGNORECASE):
        try:
            ld = json.loads(ld_raw)
            if isinstance(ld, list):
                ld = ld[0]
            if isinstance(ld, dict):
                ld_desc = ld.get("description") or ld.get("abstract") or ""
                date_str = ld.get("datePublished") or ld.get("dateCreated") or ""
                m = re.search(r"(\d{4})", date_str)
                if m:
                    ld_year = int(m.group(1))
        except Exception:
            pass
        if ld_desc or ld_year:
            break

    # Extract body from <article> or <main>
    body_text = ""
    for tag in ("article", "main"):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text_raw, re.DOTALL | re.IGNORECASE)
        if m:
            raw = m.group(1)
            # Remove script/style
            raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
            body_text = re.sub(r"<[^>]+>", " ", raw)
            body_text = re.sub(r"&nbsp;", " ", body_text)
            body_text = re.sub(r"&amp;", "&", body_text)
            body_text = re.sub(r"&lt;", "<", body_text)
            body_text = re.sub(r"&gt;", ">", body_text)
            body_text = re.sub(r"\s+", " ", body_text).strip()
            if len(body_text) > 100:
                break

    # Combine: prefer body_text, supplement with ld_desc if short
    text = body_text
    if ld_desc and len(text) < 200:
        text = ld_desc + ("\n\n" + text if text else "")

    if not text and title:
        text = title

    # Extract year from page text if not found in JSON-LD
    year = ld_year
    if year is None:
        m = re.search(r"\b(19[89]\d|20[012]\d)\b", text_raw[:2000])
        if m:
            year = int(m.group(1))

    return title, text, year


def _harvest_institute(cfg: dict, output_dir: Path) -> int:
    """Harvest publications from one institute via sitemap. Returns count written."""
    key   = cfg["key"]
    label = cfg["label"]
    out_file = output_dir / f"cpb_pbl_{key}_records.jsonl"

    # Load already-seen URLs
    seen: set[str] = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                seen.add(json.loads(line).get("url", ""))
            except json.JSONDecodeError:
                pass
    logger.info("[cpb_pbl] %s: %d already collected", label, len(seen))

    pub_urls = _get_sitemap_urls(cfg)
    logger.info("[cpb_pbl] %s: %d publication URLs from sitemap", label, len(pub_urls))

    written = 0
    for i, url in enumerate(pub_urls):
        if url in seen:
            continue

        try:
            html = _fetch(url, timeout=25)
        except urllib.error.HTTPError as exc:
            logger.debug("[cpb_pbl] %s skip %s: HTTP %d", label, url, exc.code)
            seen.add(url)
            continue
        except Exception as exc:
            logger.warning("[cpb_pbl] %s fetch error %s: %s", label, url, exc)
            continue

        title, text, year = _extract_text_from_html(html)
        if len(text) < 50:
            seen.add(url)
            continue

        rec = {
            "pub_id":    url.rstrip("/").split("/")[-1],
            "institute": key,
            "title":     title,
            "year":      year,
            "url":       url,
            "text":      text,
        }
        seen.add(url)
        with out_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written += 1

        if written % 100 == 0:
            logger.info("[cpb_pbl] %s: %d written (%.0f%%)", label, written,
                        100 * (i + 1) / len(pub_urls))
        time.sleep(_DELAY)

    logger.info("[cpb_pbl] %s: %d publications written", label, written)
    return written


def download(output_dir: Path, **_kwargs) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for cfg in _INSTITUTE_CONFIGS:
        total += _harvest_institute(cfg, output_dir)
    logger.info("[cpb_pbl] Total: %d publications", total)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE,
            "url": "https://www.cpb.nl/publicaties + https://www.pbl.nl/publicaties",
            "license": LICENSE,
            "acquisition_date": datetime.date.today().isoformat(),
            "total_documents": total,
        }, ensure_ascii=False) + "\n")
