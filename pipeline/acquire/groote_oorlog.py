"""
Download "Nieuws van de Groote Oorlog" WWI Flemish newspaper collection.

UGent Boekentoren and meemoo (VIAA) digitized ~16,000 Flemish WWI newspaper
issues (1914–1918). These are Dutch-language pre-1930 publications and are
in the public domain.

Strategies tried in order:
  A — meemoo/VIAA MediaHaven REST API
  B — Delpher (KB) newspaper search
  C — Europeana API (no key required for basic search)
  D — Krant van Toen HTML scrape
  E — UGent / Boekentoren IIIF / catalog JSON

Saves:
  groote_oorlog_metadata.jsonl  — one record per page/article
  articles/{id}.txt             — OCR text where downloadable
  _NOT_FOUND.txt                — written if all strategies fail

License: PD (WWI era, pre-1930 publications)
Country: NL-BE
Domain: news

Usage:
  python -m pipeline acquire --sources groote_oorlog
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
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "groote_oorlog"
LICENSE = "PD"
COUNTRY = "NL-BE"
DOMAIN  = "news"

_USER_AGENT = "NederCorpus/0.1 (academic; Dutch WWI corpus; contact: corpus@example.org)"
_DELAY      = 1.5   # seconds between requests

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_EUROPEANA_SEARCH = "https://api.europeana.eu/record/v2/search.json"
_EUROPEANA_KEY: str | None = None  # set via --europeana-key at runtime
_DELPHER_SRU      = "https://jsru.kb.nl/sru/sru"
_MEDIAHAVEN_API   = (
    "https://archief.viaa.be/mediahaven-rest-api/resources/media/"
    "?q=groote+oorlog&nrOfResults=100&startIndex={start}"
)
_KRANT_VAN_TOEN   = "https://www.krantvantoen.nl/zoeken"
_UGENT_CATALOG    = "https://lib.ugent.be/catalog.json"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    """Fetch URL, return bytes or None on failure."""
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json, text/html, */*"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            logger.warning(
                "[groote_oorlog] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1
            )
            if exc.code in (400, 401, 403, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning(
                "[groote_oorlog] Error fetching %s: %s (attempt %d)", url, exc, attempt + 1
            )
            time.sleep(2.0 * (attempt + 1))
    return None


def _log(output_dir: Path, url: str, identifier: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "identifier":       identifier,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_json(url: str, timeout: int = 60) -> object | None:
    data = _fetch(url, timeout=timeout)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _safe_id(s: str) -> str:
    s = re.sub(r'[/:\\?*|"<>]', '_', s)
    return s[:120]


def _append_metadata(fout, record: dict) -> None:
    fout.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Strategy A — meemoo/VIAA MediaHaven REST API
# ---------------------------------------------------------------------------

def _strategy_a(output_dir: Path, articles_dir: Path, seen_ids: set[str], fout) -> int:
    """Try meemoo MediaHaven REST API. Returns count of records saved."""
    logger.info("[groote_oorlog] Strategy A: meemoo MediaHaven REST API")
    acq_date = datetime.date.today().isoformat()
    total = 0
    start = 0

    while True:
        url = _MEDIAHAVEN_API.format(start=start)
        obj = _fetch_json(url, timeout=30)
        if obj is None:
            logger.info("[groote_oorlog] Strategy A: MediaHaven unavailable or returned no JSON")
            break

        if not isinstance(obj, dict):
            break

        items = obj.get("mediaDataList") or obj.get("results") or obj.get("items") or []
        if not items:
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            rec_id = (
                item.get("externalId")
                or item.get("fragmentId")
                or item.get("id")
                or ""
            )
            if not rec_id or rec_id in seen_ids:
                continue

            title   = item.get("title") or item.get("description") or ""
            date    = item.get("date") or item.get("created") or ""
            view_url = item.get("url") or item.get("previewImagePath") or ""

            record = {
                "id":              rec_id,
                "title":           str(title),
                "date":            str(date),
                "url":             str(view_url),
                "source_platform": "mediahaven",
                "acquisition_date": acq_date,
            }
            _append_metadata(fout, record)
            _log(output_dir, url, rec_id)
            seen_ids.add(rec_id)
            total += 1

        total_results = obj.get("totalNrOfResults") or obj.get("total") or 0
        start += len(items)
        if start >= total_results or not items:
            break
        time.sleep(_DELAY)

    logger.info("[groote_oorlog] Strategy A: %d records", total)
    return total


# ---------------------------------------------------------------------------
# Strategy B — Delpher (KB) SRU
# ---------------------------------------------------------------------------

def _strategy_b(output_dir: Path, articles_dir: Path, seen_ids: set[str], fout) -> int:
    """Try Delpher SRU endpoint for Belgian WWI newspapers."""
    logger.info("[groote_oorlog] Strategy B: Delpher SRU")
    acq_date = datetime.date.today().isoformat()
    total = 0

    # Try several Delpher SRU queries that might surface WWI Flemish papers
    queries = [
        'dc.title any "groote oorlog" AND dc.date within "1914 1918"',
        'dc.subject any "groote oorlog"',
        'dc.publisher any "belgie" AND dc.type=kranten',
    ]

    for query in queries:
        params = {
            "operation":      "searchRetrieve",
            "version":        "1.2",
            "query":          query,
            "x-collection":   "DDD",
            "startRecord":    "1",
            "maximumRecords": "100",
            "recordSchema":   "dcx",
        }
        url = f"{_DELPHER_SRU}?{urllib.parse.urlencode(params)}"
        logger.info("[groote_oorlog] Strategy B: probing query %r", query[:60])
        data = _fetch(url, timeout=30)
        if data is None:
            time.sleep(_DELAY)
            continue

        xml_text = data.decode("utf-8", errors="replace")
        # Save probe
        safe_q = re.sub(r'\W+', '_', query)[:40]
        (output_dir / f"delpher_probe_{safe_q}.xml").write_text(xml_text, encoding="utf-8")

        # Extract records via simple regex (avoid full XML parse complexity)
        ids = re.findall(r'<dc:identifier[^>]*>([^<]+)</dc:identifier>', xml_text)
        titles = re.findall(r'<dc:title[^>]*>([^<]+)</dc:title>', xml_text)
        dates  = re.findall(r'<dc:date[^>]*>([^<]+)</dc:date>', xml_text)

        for i, rec_id in enumerate(ids):
            rec_id = rec_id.strip()
            if rec_id in seen_ids:
                continue
            record = {
                "id":              rec_id,
                "title":           titles[i].strip() if i < len(titles) else "",
                "date":            dates[i].strip() if i < len(dates) else "",
                "url":             rec_id if rec_id.startswith("http") else "",
                "source_platform": "delpher",
                "acquisition_date": acq_date,
            }
            _append_metadata(fout, record)
            _log(output_dir, url, rec_id)
            seen_ids.add(rec_id)
            total += 1

        if total > 0:
            break
        time.sleep(_DELAY)

    logger.info("[groote_oorlog] Strategy B: %d records", total)
    return total


# ---------------------------------------------------------------------------
# Strategy C — Europeana API
# ---------------------------------------------------------------------------

def _strategy_c(output_dir: Path, articles_dir: Path, seen_ids: set[str], fout) -> int:
    """
    Use the Europeana API to find Groote Oorlog newspaper pages.
    Requires an API key (free registration at https://apis.europeana.eu/).
    Pass via --europeana-key or set _EUROPEANA_KEY before calling.
    """
    if not _EUROPEANA_KEY:
        logger.warning(
            "[groote_oorlog] Strategy C: no Europeana API key — skipping. "
            "Register free at https://apis.europeana.eu/ and pass --europeana-key KEY"
        )
        return 0

    logger.info("[groote_oorlog] Strategy C: Europeana API (key: %s…)", _EUROPEANA_KEY[:6])
    acq_date = datetime.date.today().isoformat()
    total = 0

    # Target the meemoo/VIAA Groote Oorlog collection specifically, plus broader queries
    queries = [
        "Nieuws van de Groote Oorlog",
        "groote oorlog newspaper",
        "Belgisch dagblad 1914",
        "Vlaamsche gazet 1914",
        "De Vlaamsche stem 1914",
    ]

    for query in queries:
        start = 1
        while True:
            params = {
                "query":   query,
                "profile": "rich",
                "rows":    "100",
                "start":   str(start),
                "qf":      ["LANGUAGE:nl", "TYPE:TEXT", "YEAR:1914 1915 1916 1917 1918"],
                "wskey":   _EUROPEANA_KEY,
            }
            # urlencode with multi-value qf
            from urllib.parse import urlencode as _urlencode
            param_parts = []
            for k, v in params.items():
                if isinstance(v, list):
                    for vi in v:
                        param_parts.append((k, vi))
                else:
                    param_parts.append((k, v))
            url = f"{_EUROPEANA_SEARCH}?{urllib.parse.urlencode(param_parts)}"
            logger.debug("[groote_oorlog] Europeana page start=%d query=%r", start, query[:50])

            obj = _fetch_json(url, timeout=30)
            if obj is None or not isinstance(obj, dict):
                break

            items = obj.get("items") or []
            if not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue

                rec_id = item.get("id") or item.get("europeanaId") or ""
                if not rec_id or rec_id in seen_ids:
                    continue

                title_list = item.get("title") or item.get("dcTitle") or []
                title = title_list[0] if isinstance(title_list, list) and title_list else str(title_list)

                date_list = item.get("year") or item.get("dctermsIssued") or []
                date = date_list[0] if isinstance(date_list, list) and date_list else str(date_list)

                view_url = ""
                shown_at = item.get("edmIsShownAt") or []
                if isinstance(shown_at, list) and shown_at:
                    view_url = shown_at[0]
                elif isinstance(shown_at, str):
                    view_url = shown_at

                media_url = ""
                shown_by = item.get("edmIsShownBy") or []
                if isinstance(shown_by, list) and shown_by:
                    media_url = shown_by[0]
                elif isinstance(shown_by, str):
                    media_url = shown_by

                provider = item.get("dataProvider") or item.get("provider") or []
                if isinstance(provider, list):
                    provider = provider[0] if provider else ""

                record = {
                    "id":              str(rec_id),
                    "title":           str(title),
                    "date":            str(date),
                    "url":             view_url,
                    "media_url":       media_url,
                    "provider":        str(provider),
                    "source_platform": "europeana",
                    "acquisition_date": acq_date,
                }
                _append_metadata(fout, record)
                _log(output_dir, url, str(rec_id))
                seen_ids.add(rec_id)
                total += 1

                # Try to download plain text if media_url is a text file
                if media_url and any(media_url.lower().endswith(ext) for ext in (".txt", ".xml")):
                    safe = _safe_id(str(rec_id))
                    ext  = ".xml" if media_url.lower().endswith(".xml") else ".txt"
                    dest = articles_dir / f"{safe}{ext}"
                    if not dest.exists():
                        text_data = _fetch(media_url, timeout=60)
                        if text_data:
                            dest.write_bytes(text_data)
                            _log(output_dir, media_url, str(rec_id))
                    time.sleep(_DELAY)

            total_results = obj.get("totalResults") or 0
            start += len(items)
            if start > total_results or len(items) < 100:
                break
            time.sleep(_DELAY)

        if total > 0:
            # Found something from this query — continue with next query for more coverage
            pass
        time.sleep(_DELAY)

    logger.info("[groote_oorlog] Strategy C: %d records", total)
    return total


# ---------------------------------------------------------------------------
# Strategy D — Krant van Toen HTML scrape
# ---------------------------------------------------------------------------

def _strategy_d(output_dir: Path, articles_dir: Path, seen_ids: set[str], fout) -> int:
    """Scrape Krant van Toen search results for WWI Belgian newspapers."""
    logger.info("[groote_oorlog] Strategy D: Krant van Toen")
    acq_date = datetime.date.today().isoformat()
    total = 0

    params = {"q": "groote oorlog", "start": "0"}
    url = f"{_KRANT_VAN_TOEN}?{urllib.parse.urlencode(params)}"
    data = _fetch(url, timeout=30)
    if data is None:
        logger.info("[groote_oorlog] Strategy D: Krant van Toen unavailable")
        return 0

    html = data.decode("utf-8", errors="replace")
    (output_dir / "krantvantoen_probe.html").write_text(html, encoding="utf-8")

    # Extract article links from HTML
    article_links = re.findall(r'href=["\']([^"\']*(?:artikel|article)[^"\']*)["\']', html, re.IGNORECASE)
    article_links = list(dict.fromkeys(article_links))  # deduplicate, preserve order

    base = "https://www.krantvantoen.nl"
    for href in article_links[:200]:
        if href.startswith("/"):
            article_url = base + href
        elif href.startswith("http"):
            article_url = href
        else:
            continue

        rec_id = re.sub(r'[^a-zA-Z0-9_-]', '_', href)[:80]
        if rec_id in seen_ids:
            continue

        article_data = _fetch(article_url, timeout=30)
        if article_data is None:
            time.sleep(_DELAY)
            continue

        article_html = article_data.decode("utf-8", errors="replace")

        # Extract title
        title_m = re.search(r'<h1[^>]*>([^<]+)</h1>', article_html)
        title = title_m.group(1).strip() if title_m else ""

        # Extract date
        date_m = re.search(r'(\d{1,2}[-\s]\w+[-\s]\d{4}|\d{4}-\d{2}-\d{2})', article_html)
        date = date_m.group(1) if date_m else ""

        # Extract article text
        text_m = re.search(r'<div[^>]*class=["\'][^"\']*article[^"\']*["\'][^>]*>(.*?)</div>', article_html, re.DOTALL | re.IGNORECASE)
        if text_m:
            raw = text_m.group(1)
            text = re.sub(r'<[^>]+>', ' ', raw)
            text = re.sub(r'\s+', ' ', text).strip()
        else:
            text = ""

        record = {
            "id":              rec_id,
            "title":           title,
            "date":            date,
            "url":             article_url,
            "source_platform": "krantvantoen",
            "acquisition_date": acq_date,
        }
        _append_metadata(fout, record)
        _log(output_dir, article_url, rec_id)
        seen_ids.add(rec_id)
        total += 1

        if text:
            dest = articles_dir / f"{rec_id}.txt"
            if not dest.exists():
                dest.write_text(text, encoding="utf-8")

        time.sleep(_DELAY)

    logger.info("[groote_oorlog] Strategy D: %d records", total)
    return total


# ---------------------------------------------------------------------------
# Strategy E — UGent / Boekentoren catalog JSON
# ---------------------------------------------------------------------------

def _strategy_e(output_dir: Path, articles_dir: Path, seen_ids: set[str], fout) -> int:
    """Try UGent library catalog JSON search and Boekentoren IIIF manifests."""
    logger.info("[groote_oorlog] Strategy E: UGent catalog")
    acq_date = datetime.date.today().isoformat()
    total = 0

    # Try UGent catalog
    catalog_queries = [
        "https://lib.ugent.be/catalog.json?q=groote+oorlog&format=Tijdschrift",
        "https://lib.ugent.be/catalog.json?q=nieuws+groote+oorlog",
        "https://lib.ugent.be/search.json?q=groote+oorlog&language=nl",
    ]

    for url in catalog_queries:
        logger.info("[groote_oorlog] Strategy E: probing %s", url[:80])
        obj = _fetch_json(url, timeout=30)
        if obj is None:
            time.sleep(_DELAY)
            continue

        # Save probe
        safe_url = re.sub(r'\W+', '_', url)[:60]
        (output_dir / f"ugent_probe_{safe_url}.json").write_text(
            json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        items: list = []
        if isinstance(obj, dict):
            items = obj.get("data") or obj.get("results") or obj.get("items") or []
        elif isinstance(obj, list):
            items = obj

        for item in items:
            if not isinstance(item, dict):
                continue
            rec_id = str(item.get("id") or item.get("ugent_id") or item.get("biblio_id") or "")
            if not rec_id or rec_id in seen_ids:
                continue

            title   = item.get("title") or item.get("name") or ""
            date    = item.get("year") or item.get("date") or ""
            view_url = item.get("url") or item.get("link") or ""
            iiif_url = item.get("iiif_manifest") or item.get("manifest") or ""

            record = {
                "id":              rec_id,
                "title":           str(title),
                "date":            str(date),
                "url":             str(view_url),
                "iiif_manifest":   str(iiif_url),
                "source_platform": "ugent_catalog",
                "acquisition_date": acq_date,
            }
            _append_metadata(fout, record)
            _log(output_dir, url, rec_id)
            seen_ids.add(rec_id)
            total += 1

            # Try to fetch OCR from IIIF manifest
            if iiif_url:
                _try_iiif_ocr(iiif_url, rec_id, articles_dir, output_dir)
                time.sleep(_DELAY)

        if total > 0:
            break
        time.sleep(_DELAY)

    # Try Boekentoren specific URL
    boekentoren_url = "https://boekentoren.gent/en/library/researching/online-collecties"
    logger.info("[groote_oorlog] Strategy E: probing Boekentoren %s", boekentoren_url)
    data = _fetch(boekentoren_url, timeout=30)
    if data:
        (output_dir / "boekentoren_probe.html").write_text(
            data.decode("utf-8", errors="replace"), encoding="utf-8"
        )

    logger.info("[groote_oorlog] Strategy E: %d records", total)
    return total


def _try_iiif_ocr(manifest_url: str, rec_id: str, articles_dir: Path, output_dir: Path) -> None:
    """Attempt to fetch OCR text from a IIIF manifest's seeAlso annotations."""
    obj = _fetch_json(manifest_url, timeout=30)
    if obj is None or not isinstance(obj, dict):
        return

    # Look for seeAlso links pointing to text/plain or ALTO XML
    see_also = obj.get("seeAlso") or []
    if isinstance(see_also, str):
        see_also = [see_also]
    for ref in see_also:
        if not isinstance(ref, str):
            ref = ref.get("@id") or ref.get("id") or "" if isinstance(ref, dict) else ""
        if not ref:
            continue
        if any(ref.lower().endswith(ext) for ext in (".txt", ".xml")):
            text_data = _fetch(ref, timeout=60)
            if text_data:
                safe = _safe_id(rec_id)
                ext  = ".xml" if ref.lower().endswith(".xml") else ".txt"
                dest = articles_dir / f"{safe}_ocr{ext}"
                if not dest.exists():
                    dest.write_bytes(text_data)
                    _log(output_dir, ref, rec_id)


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download(output_dir: Path, dry_run: bool = False, europeana_key: str | None = None, **kwargs) -> None:
    """
    Download Nieuws van de Groote Oorlog WWI newspaper collection.

    Tries five strategies in order; writes _NOT_FOUND.txt if all fail.

    Saves:
      groote_oorlog_metadata.jsonl  — one record per item found
      articles/{id}.txt             — OCR or plain text where available
    """
    global _EUROPEANA_KEY
    if europeana_key:
        _EUROPEANA_KEY = europeana_key

    output_dir.mkdir(parents=True, exist_ok=True)
    articles_dir = output_dir / "articles"
    articles_dir.mkdir(exist_ok=True)

    metadata_path = output_dir / "groote_oorlog_metadata.jsonl"

    if dry_run:
        logger.info("[groote_oorlog] [dry-run] would try 5 strategies for Groote Oorlog corpus")
        return

    # Load already-seen IDs
    seen_ids: set[str] = set()
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen_ids.add(json.loads(line)["id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_ids:
            logger.info("[groote_oorlog] Resuming — %d records already saved", len(seen_ids))

    strategies = [
        ("A (meemoo/VIAA MediaHaven)", _strategy_a),
        ("B (Delpher SRU)",            _strategy_b),
        ("C (Europeana API)",          _strategy_c),
        ("D (Krant van Toen)",         _strategy_d),
        ("E (UGent / Boekentoren)",    _strategy_e),
    ]

    grand_total = 0
    successful_strategies: list[str] = []

    with metadata_path.open("a", encoding="utf-8") as fout:
        for label, fn in strategies:
            logger.info("[groote_oorlog] Trying strategy %s", label)
            try:
                n = fn(output_dir, articles_dir, seen_ids, fout)
            except Exception as exc:
                logger.warning("[groote_oorlog] Strategy %s raised: %s", label, exc)
                n = 0
            if n > 0:
                grand_total += n
                successful_strategies.append(label)
            time.sleep(_DELAY)

    if grand_total == 0:
        msg = (
            "Nieuws van de Groote Oorlog not found via any strategy.\n\n"
            "Strategies tried:\n"
            "  A: meemoo/VIAA MediaHaven REST API "
            "(https://archief.viaa.be/mediahaven-rest-api)\n"
            "  B: Delpher (KB) SRU endpoint (jsru.kb.nl)\n"
            "  C: Europeana API (api.europeana.eu/record/v2/search.json)\n"
            "  D: Krant van Toen HTML scrape (krantvantoen.nl)\n"
            "  E: UGent catalog JSON + Boekentoren IIIF\n\n"
            "Manual steps:\n"
            "  1. Visit https://www.viaa.be/nl/search and search "
            "\"groote oorlog\" — may need institutional login.\n"
            "  2. Visit https://lib.ugent.be/nl/catalog?q=groote+oorlog "
            "for UGent digitized newspapers.\n"
            "  3. Visit https://www.europeana.eu/nl/search?query=groote+oorlog "
            "and filter to newspapers.\n"
            "  4. Contact meemoo (support@meemoo.be) for bulk export.\n"
            f"Date attempted: {datetime.date.today().isoformat()}\n"
        )
        not_found = output_dir / "_NOT_FOUND.txt"
        not_found.write_text(msg, encoding="utf-8")
        logger.warning(
            "[groote_oorlog] All strategies found 0 records. See %s", not_found
        )
    else:
        logger.info(
            "[groote_oorlog] Done. %d records from: %s",
            grand_total, ", ".join(successful_strategies),
        )
        article_count = sum(1 for _ in articles_dir.glob("*"))
        logger.info("[groote_oorlog] %d article files in %s", article_count, articles_dir)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Nieuws van de Groote Oorlog WWI newspaper collection (PD)"
    )
    parser.add_argument("--output",         required=True, type=Path)
    parser.add_argument("--dry-run",        action="store_true")
    parser.add_argument("--europeana-key",  default=None,
                        help="Europeana API key (free: https://apis.europeana.eu/)")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run, europeana_key=args.europeana_key)


if __name__ == "__main__":
    main()
