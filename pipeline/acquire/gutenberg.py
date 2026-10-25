"""
Download Dutch-language books from Project Gutenberg.

Uses the Gutendex JSON API (gutendex.com) to enumerate Dutch-language books,
then downloads each book as a UTF-8 plain text file.

All texts are in the public domain (pre-copyright or explicitly PD).
License: Public Domain
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "gutenberg"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "literature"

GUTENDEX_BASE = "https://gutendex.com/books/"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.1fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _best_text_url(formats: dict) -> str | None:
    """Pick the best plain-text download URL from a Gutendex formats dict."""
    for mime in (
        "text/plain; charset=utf-8",
        "text/plain; charset=us-ascii",
        "text/plain",
    ):
        if mime in formats:
            return formats[mime]
    # fallback: any text/plain key
    for key, url in formats.items():
        if key.startswith("text/plain"):
            return url
    return None


def download(
    output_dir: Path,
    max_books: int = 2_000,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch-language Project Gutenberg books via the Gutendex API.

    Each book is saved as {pg_id}.txt.  A companion {pg_id}.url sidecar
    records the canonical Gutenberg URL for source_url tracking.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    fetched = 0
    next_url: str | None = f"{GUTENDEX_BASE}?languages=nl"

    while next_url and fetched < max_books:
        logger.info("[gutenberg] Listing: %s", next_url)

        if dry_run:
            logger.info("[dry-run] skipping")
            break

        try:
            raw = _fetch(next_url)
        except Exception as exc:
            logger.warning("Listing page failed: %s", exc)
            break

        try:
            page = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("JSON parse error: %s", exc)
            break

        books = page.get("results", [])
        next_url = page.get("next")

        for book in books:
            if fetched >= max_books:
                break

            pg_id   = book.get("id")
            title   = book.get("title", "")
            formats = book.get("formats", {})
            text_url = _best_text_url(formats)

            if not pg_id or not text_url:
                continue

            # Skip books that are zipped or otherwise non-plain
            if text_url.endswith(".zip"):
                continue

            dest = output_dir / f"{pg_id}.txt"
            if dest.exists():
                fetched += 1
                continue

            logger.info("  [%d] %s — %s", pg_id, title[:60], text_url)

            try:
                text_bytes = _fetch(text_url)
                dest.write_bytes(text_bytes)
                dest.with_name(dest.name + ".url").write_text(
                    f"https://www.gutenberg.org/ebooks/{pg_id}", encoding="utf-8"
                )
                _log(output_dir, text_url)
                fetched += 1
            except Exception as exc:
                logger.warning("  Failed book %s: %s", pg_id, exc)

            time.sleep(0.5)

        logger.info("[gutenberg] %d books so far", fetched)
        time.sleep(1.0)

    logger.info("[gutenberg] Done. %d books downloaded.", fetched)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Dutch Project Gutenberg books (PD)")
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--max-books", type=int, default=2_000)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(args.output, max_books=args.max_books, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
