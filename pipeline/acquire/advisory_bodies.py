"""
Download publications from Dutch advisory bodies.

Two acquisition strategies:

1. Sitemap-based (CPB, PBL): sitemap.xml accessible → paginate URLs → scrape HTML.
2. CMS API-based (WRR, Rekenkamer, SCP, Gezondheidsraad, Onderwijsraad, Raad voor
   Cultuur, ROB): all share a common Dutch government CMS with a POST /api/search
   endpoint (React Server Components, Elastic App Search backend). Sitemap
   sub-pages are blocked by Cloudflare (503) but the API is accessible.

Bodies and licenses:
  WRR              — Wetenschappelijke Raad voor het Regeringsbeleid  (CC-BY)
  CPB              — Centraal Planbureau                              (PD)
  PBL              — Planbureau voor de Leefomgeving                  (CC-BY)
  Rekenkamer       — Algemene Rekenkamer                              (CC0)
  SCP              — Sociaal en Cultureel Planbureau                  (CC0)
  Gezondheidsraad  — Gezondheidsraad                                  (CC0)
  Onderwijsraad    — Onderwijsraad                                    (CC0)
  Raad voor Cultuur — Raad voor Cultuur                               (CC0)
  ROB              — Raad voor het Openbaar Bestuur                   (CC0)

NOTE: SER excluded — their copyright page imposes a no-literal-copy condition
on "these documents", which fails the permissive-only licence policy.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# NOTE: Many Dutch government advisory body sites protect sitemap sub-pages
# (/sitemap/1.xml etc.) with Cloudflare bot protection (503). The top-level
# sitemap.xml is accessible but leads to 503 sub-sitemaps. If a body returns
# 0 publications, this is typically the cause — not a content or licence issue.
# The code registers the bodies and will ingest them once access is available.

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"

# ---------------------------------------------------------------------------
# Configuration per body
# ---------------------------------------------------------------------------

_BODIES: dict[str, dict] = {
    "wrr": {
        "sitemap":     "https://www.wrr.nl/sitemap.xml",
        "base_url":    "https://www.wrr.nl",
        "pub_pattern": r"/(documenten|adviezen|publicaties)/",
        "license":     "CC-BY",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "wrr_records.jsonl",
    },
    "cpb": {
        "sitemap":     "https://www.cpb.nl/sitemap.xml",
        "base_url":    "https://www.cpb.nl",
        "pub_pattern": r"/publicatie/",
        "license":     "PD",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "cpb_records.jsonl",
    },
    "pbl": {
        "sitemap":     "https://www.pbl.nl/sitemap.xml",
        "base_url":    "https://www.pbl.nl",
        "pub_pattern": r"/publicaties/",
        "license":     "CC-BY",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "pbl_records.jsonl",
    },
    # CC0 advisory and regulatory bodies
    "rekenkamer": {
        "sitemap":     "https://www.rekenkamer.nl/sitemap.xml",
        "base_url":    "https://www.rekenkamer.nl",
        "pub_pattern": r"/(publicaties|rapporten|adviezen)/",
        "license":     "CC0",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "rekenkamer_records.jsonl",
    },
    "scp_nl": {
        "sitemap":     "https://www.scp.nl/sitemap.xml",
        "base_url":    "https://www.scp.nl",
        "pub_pattern": r"/publicaties/",
        "license":     "CC0",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "scp_nl_records.jsonl",
    },
    "gezondheidsraad": {
        "sitemap":     "https://www.gezondheidsraad.nl/sitemap.xml",
        "base_url":    "https://www.gezondheidsraad.nl",
        "pub_pattern": r"/(adviezen|publicaties|rapporten)/",
        "license":     "CC0",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "gezondheidsraad_records.jsonl",
    },
    "onderwijsraad": {
        "sitemap":     "https://www.onderwijsraad.nl/sitemap.xml",
        "base_url":    "https://www.onderwijsraad.nl",
        "pub_pattern": r"/(publicaties|adviezen|rapporten)/",
        "license":     "CC0",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "onderwijsraad_records.jsonl",
    },
    "raad_voor_cultuur": {
        "sitemap":     "https://www.raadvoorcultuur.nl/sitemap.xml",
        "base_url":    "https://www.raadvoorcultuur.nl",
        "pub_pattern": r"/(adviezen|publicaties|aanbevelingen)/",
        "license":     "CC0",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "raad_voor_cultuur_records.jsonl",
    },
    "rob": {
        "sitemap":     "https://www.raadopenbaarbestuur.nl/sitemap.xml",
        "base_url":    "https://www.raadopenbaarbestuur.nl",
        "pub_pattern": r"/(adviezen|publicaties|rapporten)/",
        "license":     "CC0",
        "domain":      "government",
        "country":     "NL",
        "out_file":    "rob_records.jsonl",
    },
}

# Bodies that use the shared Dutch government CMS (/api/search POST endpoint).
# Sitemap sub-pages for these are blocked by Cloudflare; the API is accessible.
_BODIES_API: dict[str, dict] = {
    "wrr": {
        "base_url": "https://www.wrr.nl",
        "license":  "CC-BY",
        "domain":   "government",
        "country":  "NL",
        "out_file": "wrr_records.jsonl",
        # Only include substantive publication types (skip generic web pages/news)
        "content_types": ["Publicatie", "Rapport", "Advies", "Verkenning", "Briefadvies"],
    },
    "rekenkamer": {
        "base_url": "https://www.rekenkamer.nl",
        "license":  "CC0",
        "domain":   "government",
        "country":  "NL",
        "out_file": "rekenkamer_records.jsonl",
        "content_types": ["Publicatie", "Rapport", "Brief", "Kamerstuk", "Brochure", "Jaarverslag"],
    },
    "scp_nl": {
        "base_url": "https://www.scp.nl",
        "license":  "CC0",
        "domain":   "government",
        "country":  "NL",
        "out_file": "scp_nl_records.jsonl",
        "content_types": ["Publicatie", "Rapport", "Nieuwsbrief", "Brochure"],
    },
    "gezondheidsraad": {
        "base_url": "https://www.gezondheidsraad.nl",
        "license":  "CC0",
        "domain":   "government",
        "country":  "NL",
        "out_file": "gezondheidsraad_records.jsonl",
        "content_types": ["Publicatie", "Rapport", "Advies", "Brief", "Signalement"],
    },
    "onderwijsraad": {
        "base_url": "https://www.onderwijsraad.nl",
        "license":  "CC0",
        "domain":   "government",
        "country":  "NL",
        "out_file": "onderwijsraad_records.jsonl",
        "content_types": ["Publicatie", "Rapport", "Advies", "Verkenning", "Brief"],
    },
    "raad_voor_cultuur": {
        "base_url": "https://www.raadvoorcultuur.nl",
        "license":  "CC0",
        "domain":   "government",
        "country":  "NL",
        "out_file": "raad_voor_cultuur_records.jsonl",
        "content_types": ["Publicatie", "Rapport", "Advies", "Aanbeveling", "Brief"],
    },
    "rob": {
        "base_url": "https://www.raadopenbaarbestuur.nl",
        "license":  "CC0",
        "domain":   "government",
        "country":  "NL",
        "out_file": "rob_records.jsonl",
        "content_types": ["Publicatie", "Rapport", "Advies", "Brief"],
    },
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, retries: int = 3, timeout: int = 30) -> bytes:
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}/"
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.5,en;q=0.3",
        "Referer": base,
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _get_sitemap_urls(sitemap_url: str) -> list[str]:
    """Recursively collect all <loc> URLs from an XML sitemap (handles sitemapindex)."""
    try:
        xml = _fetch(sitemap_url).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Sitemap fetch failed %s: %s", sitemap_url, exc)
        return []

    # Check if it's a sitemap index
    sub_sitemaps = re.findall(r'<sitemap>\s*<loc>(.*?)</loc>', xml, re.DOTALL)
    if sub_sitemaps:
        all_urls: list[str] = []
        for sub in sub_sitemaps:
            all_urls.extend(_get_sitemap_urls(sub.strip()))
        return all_urls

    # Regular sitemap
    return [u.strip() for u in re.findall(r'<loc>(https?://[^<]+)</loc>', xml)]


def _extract_text(html: str, url: str) -> tuple[str, str, int | None]:
    """
    Extract (title, body_text, year) from a publication page.
    Works for server-rendered HTML; tries meta tags then body paragraphs.
    """
    # Remove scripts/styles
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)

    # Title: meta og:title or <title>
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', clean, re.IGNORECASE)
    if not m:
        m = re.search(r'<title[^>]*>([^<]+)</title>', clean, re.IGNORECASE)
    title = m.group(1).strip() if m else ""
    # Strip site name suffixes
    title = re.sub(r'\s*[\|–—-]\s*(CPB|PBL|WRR|CBS|Rekenkamer|Gezondheidsraad|Onderwijsraad)[^\|–—]*$', '', title).strip()

    # Meta description
    dm = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', clean, re.IGNORECASE)
    description = dm.group(1).strip() if dm else ""

    # Body paragraphs
    paragraphs = re.findall(r'<(?:p|h[1-6])[^>]*>(.*?)</(?:p|h[1-6])>', clean, flags=re.DOTALL | re.IGNORECASE)
    body_parts: list[str] = []
    for p in paragraphs:
        t = re.sub(r'<[^>]+>', ' ', p)
        t = re.sub(r'&[a-z#0-9]+;', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        if len(t) > 40:
            body_parts.append(t)

    full_text = "\n".join(x for x in [title, description] + body_parts[:30] if x)

    # Year from URL or content
    year: int | None = None
    ym = re.search(r'\b(19|20)\d{2}\b', url + " " + full_text[:500])
    if ym:
        year = int(ym.group(0))

    return title, full_text, year


# ---------------------------------------------------------------------------
# CMS API search downloader (for Cloudflare-blocked bodies)
# ---------------------------------------------------------------------------

def _api_search(base_url: str, page: int = 1, results_per_page: int = 100,
                content_types: list[str] | None = None) -> dict:
    """
    POST to {base_url}/api/search and return the JSON response.

    This endpoint is used by the Dutch government CMS shared by WRR, Rekenkamer,
    SCP, Gezondheidsraad, Onderwijsraad, Raad voor Cultuur, and ROB.
    """
    filters: list[dict] = []
    if content_types:
        filters.append({"field": "information_type", "values": content_types, "type": "any"})

    body = {
        "requestState": {
            "searchTerm": "",
            "filters":    filters,
            "resultsPerPage": results_per_page,
            "current": page,
        },
        "queryConfig": {
            "filters": [],
            "result_fields": {
                "url":              {"raw": {}},
                "page_title":       {"raw": {}},
                "meta_description": {"raw": {}},
                "information_type": {"raw": {}},
            },
            "disjunctiveFacets": ["information_type"],
            "facets": {
                "information_type": {"type": "value", "size": 100},
            },
        },
    }
    data = json.dumps(body).encode("utf-8")
    url  = f"{base_url}/api/search"
    req  = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent":   _UA,
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "Referer":      f"{base_url}/publicaties",
            "Origin":       base_url,
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            if attempt == 2:
                raise
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def download_body_api(body: str, output_dir: Path, dry_run: bool = False) -> int:
    """
    Download publications from a CMS-API-based advisory body.

    Uses POST /api/search to enumerate all publications page by page,
    then fetches and extracts text from each publication page.
    """
    cfg = _BODIES_API[body]
    body_dir = output_dir / body
    body_dir.mkdir(parents=True, exist_ok=True)

    out_file = body_dir / cfg["out_file"]
    if out_file.exists() and out_file.stat().st_size > 0:
        logger.info("[%s] Already acquired — skipping.", body)
        return 0

    base_url      = cfg["base_url"]
    content_types = cfg.get("content_types")
    acq_date      = datetime.date.today().isoformat()

    if dry_run:
        # Just check total count
        try:
            resp = _api_search(base_url, page=1, results_per_page=1, content_types=content_types)
            logger.info("[%s] [dry-run] %d publications via API", body, resp.get("totalResults", 0))
        except Exception as exc:
            logger.warning("[%s] [dry-run] API check failed: %s", body, exc)
        return 0

    # Enumerate all pages
    results_per_page = 100
    all_items: list[dict] = []
    page = 1
    while True:
        try:
            resp = _api_search(base_url, page=page, results_per_page=results_per_page,
                               content_types=content_types)
        except Exception as exc:
            logger.warning("[%s] API page %d failed: %s", body, page, exc)
            break
        items = resp.get("results", [])
        all_items.extend(items)
        total = resp.get("totalResults", 0)
        total_pages = resp.get("totalPages", 1)
        logger.debug("[%s] Page %d/%d: %d items (total %d)", body, page, total_pages, len(items), total)
        if page >= total_pages or not items:
            break
        page += 1
        time.sleep(0.5)

    logger.info("[%s] API enumerated %d items", body, len(all_items))

    saved = 0
    with out_file.open("w", encoding="utf-8") as fout:
        for i, item in enumerate(all_items):
            rel_url = (item.get("url") or {}).get("raw", "")
            title   = (item.get("page_title") or {}).get("raw", "")
            desc    = (item.get("meta_description") or {}).get("raw", "")
            info_type = (item.get("information_type") or {}).get("raw", "")

            if not rel_url:
                continue

            # Build absolute URL
            if rel_url.startswith("http"):
                pub_url = rel_url
            else:
                pub_url = base_url + rel_url

            # Fetch full page for body text
            try:
                html = _fetch(pub_url, timeout=20).decode("utf-8", errors="replace")
            except Exception as exc:
                logger.debug("[%s] page fetch failed %s: %s", body, pub_url, exc)
                # Fall back to description only
                full_text = "\n".join(x for x in [title, desc] if x)
                if len(full_text) < 80:
                    time.sleep(0.3)
                    continue
                html = ""

            if html:
                _, full_text, year = _extract_text(html, pub_url)
                if not full_text:
                    full_text = "\n".join(x for x in [title, desc] if x)
            else:
                full_text = "\n".join(x for x in [title, desc] if x)
                year = None

            if not year:
                ym = re.search(r"\b(19|20)\d{2}\b", pub_url + " " + full_text[:300])
                year = int(ym.group(0)) if ym else None

            if len(full_text) < 80:
                time.sleep(0.2)
                continue

            record = {
                "title":            title,
                "text":             full_text,
                "year":             year,
                "source_url":       pub_url,
                "information_type": info_type,
                "license":          cfg["license"],
                "country":          cfg["country"],
                "domain":           cfg["domain"],
                "acquisition_date": acq_date,
            }
            fout.write(json.dumps(record, ensure_ascii=False) + "\n")
            saved += 1

            if (i + 1) % 100 == 0:
                logger.info("[%s] %d/%d done (%d saved)", body, i + 1, len(all_items), saved)

            time.sleep(0.3)

    logger.info("[%s] Done. %d publications saved.", body, saved)
    return saved


# ---------------------------------------------------------------------------
# Per-body downloader (sitemap-based, for CPB/PBL)
# ---------------------------------------------------------------------------

def download_body(body: str, output_dir: Path, dry_run: bool = False) -> int:
    cfg = _BODIES[body]
    body_dir = output_dir / body
    body_dir.mkdir(parents=True, exist_ok=True)

    out_file = body_dir / cfg["out_file"]
    if out_file.exists() and out_file.stat().st_size > 0:
        logger.info("[%s] Already acquired — skipping.", body)
        return 0

    acq_date = datetime.date.today().isoformat()
    pub_pattern = re.compile(cfg["pub_pattern"])

    logger.info("[%s] Fetching sitemap: %s", body, cfg["sitemap"])
    if dry_run:
        logger.info("[%s] [dry-run] stopping", body)
        return 0

    all_urls = _get_sitemap_urls(cfg["sitemap"])
    pub_urls = [u for u in all_urls if pub_pattern.search(u)]
    # Deduplicate
    seen: set[str] = set()
    pub_urls = [u for u in pub_urls if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

    logger.info("[%s] %d publication URLs found in sitemap", body, len(pub_urls))

    if not pub_urls:
        logger.warning("[%s] No matching URLs for pattern %s", body, cfg["pub_pattern"])
        return 0

    saved = 0
    with out_file.open("w", encoding="utf-8") as fout:
        for i, pub_url in enumerate(pub_urls):
            try:
                html = _fetch(pub_url, timeout=20).decode("utf-8", errors="replace")
            except Exception as exc:
                logger.debug("[%s] %s: %s", body, pub_url, exc)
                time.sleep(0.5)
                continue

            title, full_text, year = _extract_text(html, pub_url)
            if len(full_text) < 100:
                time.sleep(0.3)
                continue

            record = {
                "title": title,
                "text": full_text,
                "year": year,
                "source_url": pub_url,
                "license": cfg["license"],
                "country": cfg["country"],
                "domain": cfg["domain"],
                "acquisition_date": acq_date,
            }
            fout.write(json.dumps(record, ensure_ascii=False) + "\n")
            saved += 1

            if (i + 1) % 100 == 0:
                logger.info("[%s] %d/%d done (%d saved)", body, i + 1, len(pub_urls), saved)

            time.sleep(0.3)

    logger.info("[%s] Done. %d publications saved.", body, saved)
    return saved


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    bodies: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    all_known = set(_BODIES.keys()) | set(_BODIES_API.keys())
    selected = bodies or sorted(all_known)
    for body in selected:
        if body in _BODIES_API:
            download_body_api(body, output_dir, dry_run=dry_run)
        elif body in _BODIES:
            download_body(body, output_dir, dry_run=dry_run)
        else:
            logger.warning("Unknown advisory body: %s", body)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Dutch advisory body publications (WRR, CPB, PBL, Rekenkamer, SCP, Gezondheidsraad, Onderwijsraad, Raad voor Cultuur, ROB)"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--bodies",   nargs="+", default=None,
                        choices=sorted(set(_BODIES.keys()) | set(_BODIES_API.keys())))
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, bodies=args.bodies, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
