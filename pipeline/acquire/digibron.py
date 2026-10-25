"""
Download pre-1954 Dutch Protestant periodical articles from Digibron.

Digibron (https://www.digibron.nl/) is the digital archive of the Dutch
Reformed press. Articles published before 1954 are in the public domain
under Dutch copyright law (author died >70 years ago / publication >70 years).

API:  https://www.digibron.nl/api/
License: PD (pre-1954 publications)

Strategy:
  1. Query the search API with date_to=1954-01-01 to retrieve only pre-1954 items
  2. Paginate through all result pages (per_page=10)
  3. Save each article as a line in digibron_articles.jsonl

Estimated volume: tens of thousands of articles.

Usage:
  python -m pipeline acquire --sources digibron
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "digibron"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "news"

_API_BASE      = "https://www.digibron.nl/api"
_UA            = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY         = 1.0    # seconds between requests
_PER_PAGE      = 10
_MAX_PAGES     = 1000
_MAX_EMPTY     = 10     # consecutive empty pages before stopping


def _get_json(url: str, timeout: int = 30) -> object:
    """Fetch URL and return parsed JSON, or raise on HTTP error."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_search_url(page: int, per_page: int) -> str:
    """Build primary search URL: documents endpoint with date filter."""
    params = {
        "query":    "*",
        "page":     page,
        "per_page": per_page,
        "filters[]": "date_to:1954-01-01",
    }
    return f"{_API_BASE}/search/documents?{urllib.parse.urlencode(params)}"


def _build_fallback_url(page: int) -> str:
    """Fallback search URL: simpler /search endpoint."""
    params = {"q": "*", "page": page}
    return f"{_API_BASE}/search?{urllib.parse.urlencode(params)}"


def _extract_records(data: object, page: int) -> list[dict]:
    """
    Try to extract a list of article records from the API response.
    Returns an empty list (and logs a warning) if the format is unexpected.
    """
    if not isinstance(data, dict):
        logger.warning(
            "[digibron] digibron API returned unexpected format, check URL "
            "(page=%d, type=%s)", page, type(data).__name__
        )
        return []

    # Primary format: {"documents": [...], "meta": {...}}
    items = data.get("documents") or data.get("data") or data.get("results") or data.get("items")
    if items is None:
        items = data.get("hits") or data.get("records")
    if items is None:
        logger.warning(
            "[digibron] digibron API returned unexpected format, check URL "
            "(page=%d, keys=%s)", page, list(data.keys())
        )
        return []

    if not isinstance(items, list):
        logger.warning(
            "[digibron] digibron API returned unexpected format, check URL "
            "(page=%d, items type=%s)", page, type(items).__name__
        )
        return []

    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Actual API structure: contents{headline,body,lead}, structure{date,publication}
        contents  = item.get("contents") or {}
        structure = item.get("structure") or {}
        features  = item.get("features") or {}

        record_id = (
            item.get("id") or item.get("uuid") or item.get("identifier") or ""
        )
        title = (
            contents.get("headline") or contents.get("subHeadline")
            or item.get("title") or item.get("titel") or ""
        )
        body = (
            contents.get("body") or contents.get("lead")
            or item.get("body") or item.get("content") or item.get("text") or ""
        )
        date = (
            structure.get("date") or item.get("date") or item.get("datum")
            or item.get("publication_date") or item.get("published_at") or ""
        )
        publication = (
            structure.get("publication") or item.get("publication")
            or item.get("publicatie") or item.get("source") or item.get("journal") or ""
        )
        if isinstance(publication, dict):
            publication = publication.get("name") or publication.get("title") or ""
        url = item.get("url") or item.get("link") or item.get("canonical") or ""

        records.append({
            "id":          str(record_id),
            "title":       str(title),
            "body":        str(body),
            "date":        str(date),
            "publication": str(publication),
            "url":         str(url),
        })
    return records


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download pre-1954 Digibron articles and save as digibron_articles.jsonl.

    Resumes automatically (counts existing lines to determine start page).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path  = output_dir / "digibron_articles.jsonl"
    acq_date  = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[digibron] [dry-run] would download pre-1954 articles from %s", _API_BASE)
        return

    # Resume: count existing lines to know which page to start from
    existing_lines = 0
    if out_path.exists():
        try:
            existing_lines = sum(
                1 for ln in out_path.read_text(encoding="utf-8").splitlines() if ln.strip()
            )
        except OSError:
            existing_lines = 0
        if existing_lines:
            logger.info("[digibron] Resuming — %d articles already saved", existing_lines)

    start_page = max(1, (existing_lines // _PER_PAGE) + 1)
    total      = existing_lines
    empty_run  = 0
    use_fallback = False

    with out_path.open("a", encoding="utf-8") as fout:
        for page in range(start_page, _MAX_PAGES + 1):
            url = _build_fallback_url(page) if use_fallback else _build_search_url(page, _PER_PAGE)
            logger.debug("[digibron] Fetching page %d: %s", page, url)

            try:
                data = _get_json(url)
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 422) and not use_fallback:
                    logger.warning(
                        "[digibron] Primary endpoint returned %d, "
                        "switching to fallback URL", exc.code
                    )
                    use_fallback = True
                    url = _build_fallback_url(page)
                    try:
                        data = _get_json(url)
                    except Exception as exc2:
                        logger.warning("[digibron] Fallback also failed (page %d): %s", page, exc2)
                        empty_run += 1
                        if empty_run >= _MAX_EMPTY:
                            break
                        time.sleep(_DELAY)
                        continue
                else:
                    logger.warning("[digibron] HTTP %s on page %d: %s", exc.code, page, exc)
                    empty_run += 1
                    if empty_run >= _MAX_EMPTY:
                        break
                    time.sleep(_DELAY)
                    continue
            except Exception as exc:
                logger.warning("[digibron] Request failed (page %d): %s", page, exc)
                empty_run += 1
                if empty_run >= _MAX_EMPTY:
                    break
                time.sleep(_DELAY)
                continue

            records = _extract_records(data, page)
            if not records:
                empty_run += 1
                logger.debug("[digibron] Page %d returned 0 records (empty_run=%d)", page, empty_run)
                if empty_run >= _MAX_EMPTY:
                    logger.info("[digibron] %d consecutive empty pages — stopping", _MAX_EMPTY)
                    break
                time.sleep(_DELAY)
                continue

            empty_run = 0
            for rec in records:
                rec["acquisition_date"] = acq_date
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1

            logger.info("[digibron] Page %d: +%d articles (total=%d)", page, len(records), total)
            time.sleep(_DELAY)

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _API_BASE,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_articles":   total,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[digibron] Done. %d articles saved.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Digibron pre-1954 articles (PD)")
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
