"""
Download Dutch municipal council documents via the Open Raadsinformatie (ORI) API.

Open Raadsinformatie is run by the Open State Foundation and aggregates publicly
available municipal council documents from Dutch municipalities.

License: Public Domain (Art. 11 Auteurswet — government documents)
API:     https://api.openraadsinformatie.nl/v1/

The ORI API is Elasticsearch-based. We POST to the search endpoint with
match_all and paginate using from/size. If the search endpoint is unavailable
we fall back to probing the root endpoint for available resources.

Usage:
  python -m pipeline acquire --sources open_raadsinformatie
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time

import urllib.request
import urllib.error
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "open_raadsinformatie"
LICENSE = "PD"
DOMAIN  = "government"
COUNTRY = "NL"

_BASE    = "https://api.openraadsinformatie.nl/v1/elastic"
_UA      = "NederCorpus/0.1 (academic research; open government data; contact: corpus@example.org)"
_DELAY   = 1.0    # seconds between requests (polite)
_PAGE    = 1_000   # documents per scroll batch
_MAX     = 3_000_000  # max documents to fetch (ORI has 7.3M total)


_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _request_json(url: str, data: bytes | None = None, retries: int = 3) -> dict | list | None:
    """Perform a GET or POST request, return parsed JSON or None on failure."""
    headers = {
        "User-Agent":   _UA,
        "Accept":       "application/json",
        "Content-Type": "application/json",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.warning("[ori] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1)
            if exc.code in (400, 404, 410):
                return None  # not retryable
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[ori] Request error for %s: %s (attempt %d)", url, exc, attempt + 1)
            time.sleep(2.0 * (attempt + 1))
    return None


def _probe_api(output_dir: Path) -> dict:
    """
    Probe the ORI API root and discover available endpoints.
    Saves the raw response for inspection and returns it as a dict.
    """
    result = _request_json(f"{_BASE}/")
    if result is None:
        # Try without trailing slash
        result = _request_json(_BASE)
    if result is not None:
        probe_path = output_dir / "ori_api_probe.json"
        probe_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[ori] API probe saved to %s", probe_path)
    else:
        logger.warning("[ori] Could not reach API root at %s", _BASE)
    return result or {}


def _extract_text(hit: dict) -> str:
    """Extract the best available text field from a search hit."""
    src = hit.get("_source", hit)  # Elasticsearch wraps in _source
    for field in ("text", "description", "body", "content", "summary", "title"):
        val = src.get(field)
        if val is None:
            continue
        # text field is a list of page strings in the ORI elastic API
        if isinstance(val, list):
            joined = "\n\n".join(s.strip() for s in val if isinstance(s, str) and s.strip())
            if len(joined) >= 100:
                return joined
        elif isinstance(val, str) and len(val.strip()) >= 100:
            return val.strip()
    # Combine title + description if neither alone is long enough
    parts = []
    for field in ("title", "description", "summary"):
        val = src.get(field)
        if val and isinstance(val, str):
            parts.append(val.strip())
    return "\n\n".join(parts)


def _normalise_hit(hit: dict) -> dict | None:
    """
    Normalise an Elasticsearch hit or plain API record into a uniform dict.
    Returns None if the document has too little text.
    """
    src = hit.get("_source", hit)
    text = _extract_text(hit)
    if len(text) < 100:
        return None

    doc_id   = hit.get("_id") or src.get("id") or src.get("identifier") or ""
    title    = src.get("title") or src.get("naam") or ""
    date     = src.get("date") or src.get("datum") or src.get("created") or ""
    muni     = (src.get("municipality") or src.get("gemeente")
                or src.get("organization") or src.get("organisatie") or "")
    url      = src.get("url") or src.get("ori_url") or src.get("source_url") or ""

    return {
        "id":           doc_id,
        "title":        title,
        "text":         text,
        "date":         date,
        "municipality": muni,
        "url":          url,
    }


def _search_after(after_id: str | None, size: int) -> list | None:
    """
    Fetch a page of documents using Elasticsearch search_after pagination.
    Sorts by _id asc so results are stable and resumable.
    Returns list of hits or None on failure.
    """
    body: dict = {
        "query": {"match_all": {}},
        "size":  size,
        "sort":  [{"_id": "asc"}],
        "_source": ["name", "text", "md_text", "url", "last_discussed_at",
                    "has_organization_name", "content_type", "file_name", "@type"],
    }
    if after_id is not None:
        body["search_after"] = [after_id]

    url = f"{_BASE}/ori_*/_search"
    result = _request_json(url, data=json.dumps(body).encode("utf-8"))
    if result is None:
        return None

    if after_id is None:
        # Log total on first call
        total = result.get("hits", {}).get("total", {})
        if isinstance(total, dict):
            total = total.get("value", "?")
        logger.info("[ori] Elasticsearch reports %s total documents", total)

    return result.get("hits", {}).get("hits", [])


def download(
    output_dir: Path,
    max_docs: int = _MAX,
    page_size: int = _PAGE,
    dry_run: bool = False,
) -> None:
    """Download Open Raadsinformatie via search_after pagination (bypasses 10K from/size limit)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[ori] [dry-run] would download up to %d ORI documents", max_docs)
        return

    out_path      = output_dir / "ori_documents.jsonl"
    cursor_path   = output_dir / "ori_cursor.txt"  # last _id seen for resume

    # Resume: read cursor
    after_id    = None
    total_saved = 0
    if cursor_path.exists():
        after_id    = cursor_path.read_text(encoding="utf-8").strip() or None
        total_saved = sum(1 for _ in out_path.open(encoding="utf-8", errors="replace")) if out_path.exists() else 0
        logger.info("[ori] Resuming from id=%s (%d docs already saved)", after_id, total_saved)

    _probe_api(output_dir)

    logger.info("[ori] Starting search_after pagination (max=%d) …", max_docs)
    batch_num = 0

    with out_path.open("a", encoding="utf-8") as f:
        while total_saved < max_docs:
            hits = _search_after(after_id, page_size)
            if hits is None:
                logger.warning("[ori] search_after failed at batch=%d after_id=%s", batch_num, after_id)
                break
            if not hits:
                logger.info("[ori] No more hits at batch=%d — exhausted index", batch_num)
                break

            batch_new = 0
            for hit in hits:
                if total_saved >= max_docs:
                    break
                doc = _normalise_hit(hit)
                # Advance cursor regardless of whether doc passes filter
                after_id = hit.get("sort", [None])[-1]
                if doc is None:
                    continue
                doc["acquisition_date"] = acq_date
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                total_saved += 1
                batch_new   += 1

            batch_num += 1
            if batch_num % 100 == 0:
                logger.info("[ori] batch=%d +%d saved (total=%d, cursor=%s)",
                            batch_num, batch_new, total_saved, after_id)
                # Save cursor for resume
                if after_id is not None:
                    cursor_path.write_text(str(after_id), encoding="utf-8")

            time.sleep(_DELAY)

    if after_id is not None:
        cursor_path.write_text(str(after_id), encoding="utf-8")

    logger.info("[ori] Done. %d documents saved to %s", total_saved, out_path)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _BASE,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_documents":  total_saved,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Open Raadsinformatie municipal council documents (Public Domain)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--max-docs",  type=int, default=_MAX)
    parser.add_argument("--page-size", type=int, default=_PAGE)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, page_size=args.page_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
