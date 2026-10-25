"""
Harvest Dutch-language articles from Global Voices (nl.globalvoices.org).

Global Voices is a citizen journalism platform. The Dutch edition publishes
~2,200 articles translating and contextualising international stories for
Dutch readers. All content licensed CC-BY 3.0.

API: WordPress REST API (/wp-json/wp/v2/posts)

Usage:
  python -m pipeline acquire --sources global_voices_nl
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import html
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "global_voices_nl"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "news"

_BASE_URL  = "https://nl.globalvoices.org/wp-json/wp/v2/posts"
_UA        = "NederCorpus/0.1 (academic; Dutch corpus; research)"
_DELAY     = 0.5
_TIMEOUT   = 30
_PER_PAGE  = 100

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _fetch_json(url: str, retries: int = 4) -> tuple[list | None, dict]:
    """Return (json_data, response_headers)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                headers = dict(resp.headers)
            return data, headers
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = 5 * (2 ** attempt)
                logger.warning("[global_voices_nl] HTTP %d — retry in %.0fs", exc.code, wait)
                time.sleep(wait)
            else:
                raise
        except Exception as exc:
            logger.warning("[global_voices_nl] fetch error: %s", exc)
            if attempt < retries - 1:
                time.sleep(5)
    return None, {}


def download(output_dir: Path, dry_run: bool = False) -> None:
    if dry_run:
        logger.info("[global_voices_nl] dry_run — skipping download")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "records.jsonl"

    seen_ids: set[int] = set()
    if out_file.exists():
        with out_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    if rec.get("wp_id"):
                        seen_ids.add(rec["wp_id"])
                except json.JSONDecodeError:
                    pass
        logger.info("[global_voices_nl] Resuming — %d articles already saved", len(seen_ids))

    written = 0
    skipped = 0
    page = 1

    with out_file.open("a", encoding="utf-8") as fh:
        while True:
            params = urllib.parse.urlencode({
                "per_page": _PER_PAGE,
                "page": page,
                "_fields": "id,date,link,title,content,excerpt",
                "status": "publish",
            })
            url = f"{_BASE_URL}?{params}"
            data, headers = _fetch_json(url)

            if data is None:
                logger.error("[global_voices_nl] Failed on page %d — stopping", page)
                break
            if not data:
                break

            total_pages = int(headers.get("X-WP-TotalPages", 1))

            for post in data:
                wp_id = post.get("id")
                if wp_id in seen_ids:
                    skipped += 1
                    continue

                title = _strip_html(post.get("title", {}).get("rendered", ""))
                content = _strip_html(post.get("content", {}).get("rendered", ""))
                if not title or len(content) < 100:
                    skipped += 1
                    continue

                text = f"{title}\n\n{content}"
                date_str = post.get("date", "")[:10]
                year = int(date_str[:4]) if date_str and date_str[:4].isdigit() else None
                source_url = post.get("link", "")

                entry = {
                    "id": f"global_voices_nl_{wp_id}",
                    "source": SOURCE,
                    "source_url": source_url,
                    "license": LICENSE,
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "language_variant": "NL",
                    "publication_year": year,
                    "acquisition_date": datetime.date.today().isoformat(),
                    "text": text,
                    "title": title,
                    "wp_id": wp_id,
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                seen_ids.add(wp_id)
                written += 1

            logger.info("[global_voices_nl] Page %d/%d — %d written", page, total_pages, written)

            if page >= total_pages:
                break
            page += 1
            time.sleep(_DELAY)

    logger.info("[global_voices_nl] Done — %d articles written (%d skipped)", written, skipped)
