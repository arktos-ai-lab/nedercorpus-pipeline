"""
Download Dutch-language scientific paper metadata and abstracts from OpenAlex.

OpenAlex is an open database of scholarly works. All metadata is released under
the CC0 1.0 Universal license.

Filter: language:nl — Dutch-language academic papers.
Fields acquired: title, abstract (reconstructed from inverted index), year, doi.

API: https://api.openalex.org/works?filter=language:nl
Rate limit: ~100 req/s without API key; 10 req/s polite pool.

License: CC0 (https://docs.openalex.org/how-to-use-the-data/faq#whats-the-license)
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "openalex"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "academic"

_API_BASE = "https://api.openalex.org/works"
_PAGE_SIZE = 200


def _reconstruct_abstract(inverted_index: dict) -> str:
    """
    Reconstruct an abstract string from OpenAlex inverted index format.

    The inverted index maps word → list[position]. We reverse this to
    get a list of (position, word) pairs and join in order.
    """
    if not inverted_index:
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pairs.append((pos, word))
    pairs.sort()
    return " ".join(w for _, w in pairs)


def _build_url(cursor: str, email: str | None) -> str:
    params: dict[str, str] = {
        "filter": "language:nl",
        "select": "id,title,abstract_inverted_index,publication_year,doi",
        "per-page": str(_PAGE_SIZE),
        "cursor": cursor,
    }
    if email:
        params["mailto"] = email
    return f"{_API_BASE}?{urllib.parse.urlencode(params)}"


def _fetch(url: str) -> dict:
    headers = {"User-Agent": "NederCorpus/0.1 (academic; https://github.com/NederCorpus)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download(
    output_dir: Path,
    max_pages: int = 500,
    email: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch scientific paper metadata and abstracts from OpenAlex.

    Pages are saved as openalex_page_{N:06d}.jsonl (one JSON object per line).
    Each object: {id, title, abstract, publication_year, doi}.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[openalex] [dry-run] skipping")
        return

    cursor = "*"
    page_num = 0
    total_saved = 0

    acq_date = datetime.date.today().isoformat()

    while page_num < max_pages:
        url = _build_url(cursor, email)
        logger.info("[openalex] page=%d cursor=%s", page_num, cursor[:30])

        try:
            data = _fetch(url)
        except Exception as exc:
            logger.warning("[openalex] fetch failed: %s", exc)
            break

        results = data.get("results", [])
        if not results:
            logger.info("[openalex] no more results")
            break

        out_path = output_dir / f"openalex_page_{page_num:06d}.jsonl"
        if out_path.exists():
            # advance cursor using saved metadata
            meta = data.get("meta", {})
            cursor = meta.get("next_cursor", "")
            if not cursor:
                break
            page_num += 1
            continue

        lines: list[str] = []
        for work in results:
            abstract_inv = work.get("abstract_inverted_index") or {}
            abstract = _reconstruct_abstract(abstract_inv)
            title = (work.get("title") or "").strip()
            if not title and not abstract:
                continue
            rec = {
                "id":               work.get("id", ""),
                "title":            title,
                "abstract":         abstract,
                "publication_year": work.get("publication_year"),
                "doi":              work.get("doi", ""),
                "acquisition_date": acq_date,
            }
            lines.append(json.dumps(rec, ensure_ascii=False))

        if lines:
            out_path.write_text("\n".join(lines), encoding="utf-8")
            total_saved += len(lines)
            logger.info("[openalex] page %d: %d works", page_num, len(lines))

        meta = data.get("meta", {})
        cursor = meta.get("next_cursor", "")
        if not cursor:
            logger.info("[openalex] cursor exhausted")
            break

        page_num += 1
        time.sleep(0.2)  # polite crawl

    logger.info("[openalex] Done. %d works saved across %d pages.", total_saved, page_num)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Dutch OpenAlex works (CC0)")
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-pages",  type=int, default=500)
    parser.add_argument("--email",      type=str, default=None,
                        help="Contact email for polite API pool")
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(args.output, max_pages=args.max_pages, email=args.email, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
