"""
Download Brussels Parliament (Parlement Bruxellois) Dutch-language posts via the WordPress REST API.

The Brussels Parliament website (parlement.brussels) is a WordPress site.
The verbatim plenaire vergadering transcripts are in a JavaScript-rendered
media-db plugin database (not accessible without headless browser).

This acquirer harvests the Dutch-language WordPress posts, which include:
  - News items about parliamentary sessions and decisions
  - Committee visit reports and summaries
  - Parliamentary press releases
  - Speeches and policy announcements

These provide Brussels parliamentary Dutch (NL-BE) at moderate volume.

License: Public Domain (Art. 8 Belgian Auteurswet — parliamentary records)
API: https://www.parlement.brussels/wp-json/wp/v2/posts
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import ssl
import time
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE   = "brussels_parlement"
LICENSE  = "PD"
COUNTRY  = "BE"
DOMAIN   = "parliamentary"
LANG     = "NL-BE"

_BASE        = "https://www.parlement.brussels"
_API_POSTS   = (
    _BASE + "/wp-json/wp/v2/posts"
    "?lang=nl&per_page=100&page={page}"
    "&_fields=id,title,content,link,date&orderby=date&order=desc"
)
_USER_AGENT  = "NederCorpus/0.1 (academic research; Belgian PD parliamentary text)"
_DELAY       = 0.5

# Strip leading IP comment that wp-json prepends
_IP_COMMENT = re.compile(r"^<!-- [^>]+ -->")


def _fetch(url: str, retries: int = 3, timeout: int = 30) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                return None
            if attempt == retries - 1:
                logger.warning("[brussels_parlement] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[brussels_parlement] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _log(output_dir: Path, url: str, post_id: int) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "post_id":          post_id,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    max_pages: int = 200,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch-language WordPress posts from the Brussels Parliament website.

    Each post is saved as posts/{post_id}.json containing title, content, date, link.
    Pagination continues until no results or max_pages reached.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    posts_dir = output_dir / "posts"
    posts_dir.mkdir(exist_ok=True)

    if dry_run:
        logger.info("[brussels_parlement] [dry-run] skipping")
        return

    total = 0
    skipped = 0

    for page in range(1, max_pages + 1):
        url = _API_POSTS.format(page=page)
        logger.info("[brussels_parlement] Page %d", page)

        data = _fetch(url)
        if data is None:
            logger.info("[brussels_parlement] No data at page %d — stopping", page)
            break

        # Strip IP comment WP prepends
        text = data.decode("utf-8", errors="replace")
        text = _IP_COMMENT.sub("", text).strip()

        try:
            posts = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("[brussels_parlement] JSON parse error at page %d: %s", page, exc)
            break

        if not posts:
            logger.info("[brussels_parlement] Empty page %d — done", page)
            break

        for post in posts:
            post_id = post.get("id")
            if not post_id:
                continue

            dest = posts_dir / f"{post_id}.json"
            if dest.exists():
                skipped += 1
                continue

            title   = (post.get("title") or {}).get("rendered", "")
            content = (post.get("content") or {}).get("rendered", "")
            link    = post.get("link", "")
            date    = post.get("date", "")

            dest.write_text(
                json.dumps({
                    "id": post_id,
                    "title": title,
                    "content": content,
                    "link": link,
                    "date": date,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            _log(output_dir, link, post_id)
            total += 1

        logger.info("[brussels_parlement] Page %d: %d posts", page, len(posts))
        if len(posts) < 100:
            logger.info("[brussels_parlement] Last page reached")
            break

        time.sleep(_DELAY)

    logger.info(
        "[brussels_parlement] Done. %d posts downloaded, %d skipped.",
        total, skipped,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Brussels Parliament Dutch WP posts (PD)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(args.output, max_pages=args.max_pages, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
