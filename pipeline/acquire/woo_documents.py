"""
Download Dutch Woo (Wet open overheid) open government request documents.

The Woo (Open Government Act) requires Dutch government bodies to proactively
publish documents that have been released via information requests.
All documents are Public Domain (Art. 11 Auteurswet).

Sources tried in order:
  1. Woogle API (api.woogle.nl) — aggregator for Woo documents
  2. Open Overheid Woo register (open.overheid.nl/repository/ronl)
  3. Fallback: search open.overheid.nl for Woo decision metadata

Usage:
  python -m pipeline acquire --sources woo_documents
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

SOURCE  = "woo_documents"
LICENSE = "PD"
DOMAIN  = "government"
COUNTRY = "NL"

_WOOGLE_API   = "https://api.woogle.nl/api/v1"
_OO_BASE      = "https://open.overheid.nl"
_OO_RONL      = "https://open.overheid.nl/repository/ronl"
_UA           = "NederCorpus/0.1 (academic research; open government data; contact: corpus@example.org)"
_DELAY        = 1.0
_PAGE         = 100
_MAX_DOCS     = 50_000

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url: str, data: bytes | None = None, retries: int = 3) -> str | None:
    """Perform GET or POST, return decoded text. None on failure."""
    headers = {
        "User-Agent":   _UA,
        "Accept":       "application/json, text/html, */*",
        "Content-Type": "application/json",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=data, headers=headers,
                method="POST" if data is not None else "GET"
            )
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            logger.warning("[woo] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1)
            if exc.code in (400, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[woo] Error for %s: %s (attempt %d)", url, exc, attempt + 1)
            time.sleep(2.0 * (attempt + 1))
    return None


def _fetch_json(url: str, data: bytes | None = None) -> dict | list | None:
    """Fetch JSON, return parsed object or None."""
    raw = _fetch(url, data=data)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _probe_woogle(output_dir: Path) -> dict:
    """Probe the Woogle API root and save response."""
    result = _fetch_json(f"{_WOOGLE_API}/")
    if result is None:
        result = _fetch_json(_WOOGLE_API)
    if result:
        probe = output_dir / "woo_woogle_probe.json"
        probe.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[woo] Woogle API probe saved to %s", probe)
    return result or {}


def _normalise_woo_doc(item: dict, acq_date: str) -> dict | None:
    """
    Normalise a Woo document record from any API shape.
    Returns None if there is not enough text.
    """
    # Try multiple field names for text content
    text = ""
    for field in ("text", "body", "content", "description", "summary", "inhoud"):
        val = item.get(field)
        if val and isinstance(val, str) and len(val.strip()) >= 100:
            text = val.strip()
            break

    if not text:
        # Combine title + description
        parts = []
        for field in ("title", "titel", "naam", "description", "beschrijving", "summary"):
            val = item.get(field)
            if val and isinstance(val, str):
                parts.append(val.strip())
        text = "\n\n".join(parts)

    if len(text) < 100:
        return None

    doc_id = (item.get("id") or item.get("identifier") or item.get("uuid") or
              item.get("pid") or "")
    title  = (item.get("title") or item.get("titel") or item.get("naam") or "")
    date   = (item.get("date") or item.get("datum") or item.get("publicatieDatum") or
              item.get("publication_date") or item.get("created") or "")
    org    = (item.get("organisation") or item.get("organisatie") or item.get("publisher") or
              item.get("requester") or item.get("verzender") or "")
    url    = (item.get("url") or item.get("source_url") or item.get("link") or
              item.get("uri") or "")

    return {
        "id":           str(doc_id),
        "title":        str(title),
        "text":         text,
        "date":         str(date),
        "organisation": str(org),
        "url":          str(url),
        "acquisition_date": acq_date,
    }


def _woogle_strategy(output_dir: Path, out_path: Path, seen_ids: set,
                     max_docs: int, acq_date: str) -> int:
    """
    Download via Woogle API. Returns number of newly saved documents.
    """
    logger.info("[woo] Trying Woogle API at %s", _WOOGLE_API)

    # Probe first
    probe = _probe_woogle(output_dir)

    saved = 0

    # Try pagination patterns
    page_patterns = [
        lambda p: (f"{_WOOGLE_API}/documents?page={p}&size={_PAGE}", None),
        lambda p: (f"{_WOOGLE_API}/documents?page={p}", None),
        lambda p: (f"{_WOOGLE_API}/search?page={p}&size={_PAGE}", None),
        # Elasticsearch-style POST
        lambda p: (f"{_WOOGLE_API}/search",
                   json.dumps({"query": {"match_all": {}},
                               "size": _PAGE, "from": (p - 1) * _PAGE}).encode("utf-8")),
    ]

    for pattern_fn in page_patterns:
        logger.info("[woo] Testing Woogle pattern: %s", pattern_fn(1)[0])
        url, body = pattern_fn(1)
        result = _fetch_json(url, data=body)
        if result is None:
            continue

        # Save probe
        (output_dir / "woo_woogle_strategy_probe.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Extract items from various response shapes
        items = _extract_items(result)
        if not items:
            continue

        logger.info("[woo] Woogle pattern works: %s — %d items on page 1", url, len(items))

        with out_path.open("a", encoding="utf-8") as f:
            page = 1
            while len(seen_ids) + saved < max_docs:
                url, body = pattern_fn(page)
                result = _fetch_json(url, data=body)
                if result is None:
                    break
                items = _extract_items(result)
                if not items:
                    logger.info("[woo] Woogle: no more items at page %d", page)
                    break

                batch_new = 0
                for item in items:
                    doc = _normalise_woo_doc(item, acq_date)
                    if doc is None:
                        continue
                    if doc["id"] in seen_ids:
                        continue
                    seen_ids.add(doc["id"])
                    f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                    saved += 1
                    batch_new += 1

                logger.info("[woo] Woogle page %d: +%d saved (total=%d)",
                            page, batch_new, len(seen_ids))
                page += 1
                time.sleep(_DELAY)

        return saved

    logger.warning("[woo] Woogle API: no usable endpoint found")
    return 0


def _extract_items(result: dict | list) -> list:
    """Extract a list of document items from various API response shapes."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("results", "data", "items", "documents", "hits", "records",
                    "woo_documents", "besluiten"):
            val = result.get(key)
            if val and isinstance(val, list):
                return val
        # Elasticsearch hits.hits
        hits = result.get("hits")
        if isinstance(hits, dict):
            inner = hits.get("hits")
            if isinstance(inner, list):
                return inner
    return []


def _open_overheid_strategy(output_dir: Path, out_path: Path, seen_ids: set,
                             max_docs: int, acq_date: str) -> int:
    """
    Fallback: Use open.overheid.nl search/SRU for Woo document metadata.
    Returns number of newly saved documents.
    """
    logger.info("[woo] Trying open.overheid.nl Woo register at %s", _OO_RONL)
    saved = 0

    # Try the RONL endpoint
    urls_to_try = [
        f"{_OO_BASE}/api/v0/search?type=woo&format=json&rows={_PAGE}&start=0",
        f"{_OO_BASE}/zoeken?type=woo&format=json",
        f"{_OO_BASE}/repository/ronl?format=json&rows={_PAGE}",
    ]

    for base_url in urls_to_try:
        logger.info("[woo] Probing: %s", base_url)
        result = _fetch_json(base_url)
        if result is None:
            raw = _fetch(base_url)
            if raw:
                (output_dir / "woo_oo_probe.html").write_text(raw, encoding="utf-8")
                logger.info("[woo] Saved HTML probe to woo_oo_probe.html")
            time.sleep(_DELAY)
            continue

        (output_dir / "woo_oo_probe.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("[woo] open.overheid.nl response saved")

        items = _extract_items(result)
        if not items:
            continue

        logger.info("[woo] open.overheid.nl: %d items on first page", len(items))

        # Determine pagination pattern
        # Many OAI/SRU APIs use start/rows or page/size
        with out_path.open("a", encoding="utf-8") as f:
            start = 0
            while len(seen_ids) + saved < max_docs:
                # Try offset-based pagination
                page_url = re.sub(r'start=\d+', f'start={start}', base_url)
                if 'start=' not in page_url:
                    page_url = base_url + f"&start={start}"

                page_result = _fetch_json(page_url)
                if page_result is None:
                    break
                page_items = _extract_items(page_result)
                if not page_items:
                    break

                batch_new = 0
                for item in page_items:
                    doc = _normalise_woo_doc(item, acq_date)
                    if doc is None:
                        continue
                    if doc["id"] in seen_ids:
                        continue
                    seen_ids.add(doc["id"])
                    f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                    saved += 1
                    batch_new += 1

                logger.info("[woo] open.overheid.nl start=%d: +%d saved", start, batch_new)
                start += _PAGE
                time.sleep(_DELAY)

        if saved > 0:
            return saved

    return saved


def download(
    output_dir: Path,
    max_docs: int = _MAX_DOCS,
    dry_run: bool = False,
) -> None:
    """Download Woo open government documents from Woogle and open.overheid.nl."""
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[woo] [dry-run] would download up to %d Woo documents", max_docs)
        return

    out_path = output_dir / "woo_docs.jsonl"

    # Resume: collect already-downloaded IDs
    seen_ids: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    seen_ids.add(json.loads(line)["id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        logger.info("[woo] Resuming — %d documents already downloaded", len(seen_ids))

    total_saved = len(seen_ids)

    # -----------------------------------------------------------------------
    # Strategy 1: Woogle API
    # -----------------------------------------------------------------------
    n = _woogle_strategy(output_dir, out_path, seen_ids, max_docs, acq_date)
    total_saved += n

    # -----------------------------------------------------------------------
    # Strategy 2: open.overheid.nl if Woogle yielded nothing
    # -----------------------------------------------------------------------
    if n == 0:
        n2 = _open_overheid_strategy(output_dir, out_path, seen_ids, max_docs, acq_date)
        total_saved += n2
        if n2 == 0:
            logger.warning(
                "[woo] Both Woogle and open.overheid.nl strategies yielded no documents. "
                "Check woo_woogle_probe.json and woo_oo_probe.* in %s for current "
                "API structure. The Woogle API URL or open.overheid.nl schema may "
                "have changed.", output_dir
            )

    logger.info("[woo] Done. %d Woo documents saved to %s", total_saved, out_path)

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _WOOGLE_API,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_documents":  total_saved,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Woo open government documents (Public Domain)"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=_MAX_DOCS)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
