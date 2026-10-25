"""
Acquire Dutch text from Karin Spaink's blog (spaink.net).

License: Public Domain — by explicit author statement dated 2026-05-08:
  "Het auteursrecht van al mijn gepubliceerde columns, artikelen, lezingen,
   essays, vertalingen en boeken heb ik aan het publieke domein vermaakt."
  Source: https://www.spaink.net/2026/05/08/exit-spaink/

The site footer shows CC BY NC ND; that applies to the WordPress template and
any third-party content.  Spaink's own works (columns, articles, essays,
lectures, translations) are dedicated to the public domain by her statement
above, which supersedes the footer as she is the rights holder.

We use the WordPress REST API (same pattern as global_voices_nl).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import ssl
import time
import urllib.request
import urllib.error
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE   = "spaink"
LICENSE  = "PD"
COUNTRY  = "NL"
DOMAIN   = "journalism"
BASE_URL = "https://www.spaink.net/wp-json/wp/v2/posts"

# The public-domain dedication covers: columns, artikelen, lezingen, essays,
# vertalingen, boeken — i.e., all authored text posts.  Exclude galleries,
# pages that are purely navigation, and any reposts of third-party material.
# We download all posts and note this in the license_matrix for human review.
_PD_NOTICE = (
    "PD by author statement 2026-05-08: "
    "https://www.spaink.net/2026/05/08/exit-spaink/"
)


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "license_note": _PD_NOTICE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_page(page: int, per_page: int = 100, timeout: int = 30) -> list[dict] | None:
    url = f"{BASE_URL}?per_page={per_page}&page={page}&_fields=id,date,title,content,link,excerpt"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NederCorpus/0.1 (research; PD content; contact https://github.com/arktos-ai-lab/nedercorpus-pipeline)"},
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            return []   # past last page
        logger.warning("HTTP %d fetching page %d: %s", exc.code, page, exc)
        return None
    except Exception as exc:
        logger.warning("Failed to fetch page %d: %s", page, exc)
        return None


def download(
    output_dir: Path,
    per_page: int = 100,
    max_pages: int = 500,
    delay: float = 1.0,
    dry_run: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total = 0

    for page in range(1, max_pages + 1):
        logger.info("[spaink] page %d", page)

        if dry_run:
            logger.info("[dry-run] skipping")
            break

        posts = _fetch_page(page, per_page=per_page)
        if posts is None:
            logger.warning("Fetch failed at page %d — stopping.", page)
            break
        if not posts:
            logger.info("No more posts at page %d.", page)
            break

        dest = output_dir / f"spaink_{page:04d}.jsonl"
        with dest.open("w", encoding="utf-8") as f:
            for post in posts:
                f.write(json.dumps(post, ensure_ascii=False) + "\n")

        _log(output_dir, f"{BASE_URL}?page={page}")
        total += len(posts)
        logger.info("  Saved %d posts (total %d)", len(posts), total)

        if len(posts) < per_page:
            break

        time.sleep(delay)

    logger.info("[spaink] Done: %d posts downloaded.", total)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Acquire Karin Spaink blog (PD)")
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(args.output, max_pages=args.max_pages, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
