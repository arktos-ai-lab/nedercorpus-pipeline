"""
Download articles from Wikikids (wikikids.nl) via the MediaWiki API.

Wikikids is a Dutch children's encyclopaedia — child-friendly rewrites of
Wikipedia articles. At ~51K articles it is a compact but distinct register:
simplified vocabulary, shorter sentences, written for ages 6-14. That makes
it linguistically complementary to Wikipedia and useful for training models
that should understand plain Dutch.

License: CC BY-SA 3.0 (https://wikikids.nl/Wikikids:Auteursrecht)
API:     https://wikikids.nl/api.php

Strategy:
  1. List all article titles in namespace 0 via action=query&list=allpages
  2. Fetch wikitext in batches of 50 via action=query&prop=revisions&rvprop=content
  3. Save raw wikitext as JSONL (parsed later by parse_wikikids_dir)

Estimated volume: ~51K articles / ~30-40 MB wikitext.

Usage:
  python -m pipeline acquire --sources wikikids
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

SOURCE  = "wikikids"
LICENSE = "CC-BY-SA"
COUNTRY = "NL"
DOMAIN  = "encyclopedic"

_API     = "https://wikikids.nl/api.php"
_UA      = "NederCorpus/0.1 (academic; wikikids nl corpus; contact: corpus@example.org)"
_BATCH   = 50    # titles per revisions request
_DELAY   = 0.5   # seconds between API calls (polite)


def _get(params: dict, timeout: int = 30) -> dict:
    """GET a MediaWiki API request, return parsed JSON."""
    params["format"] = "json"
    url = f"{_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _all_titles() -> list[str]:
    """Yield all article titles in namespace 0 via allpages."""
    titles = []
    params: dict = {"action": "query", "list": "allpages", "apnamespace": "0",
                    "aplimit": "500", "apfilterredir": "nonredirects"}
    while True:
        data = _get(params)
        for page in data.get("query", {}).get("allpages", []):
            titles.append(page["title"])
        cont = data.get("continue", {})
        if not cont:
            break
        params.update(cont)
        time.sleep(_DELAY)
    return titles


def _fetch_wikitext(titles: list[str]) -> list[dict]:
    """
    Fetch wikitext for up to _BATCH titles in one API call.
    Returns list of dicts with keys: pageid, title, wikitext.
    """
    params = {
        "action":  "query",
        "prop":    "revisions",
        "rvprop":  "content",
        "rvslots": "main",
        "titles":  "|".join(titles),
    }
    data = _get(params, timeout=60)
    pages = data.get("query", {}).get("pages", {})
    results = []
    for page_id, page in pages.items():
        if page_id == "-1":
            continue  # missing page
        title = page.get("title", "")
        revs  = page.get("revisions", [])
        if not revs:
            continue
        # MediaWiki 1.32+ uses slots/main/content
        rev = revs[0]
        wikitext = (
            rev.get("slots", {}).get("main", {}).get("*")
            or rev.get("*", "")
        )
        if wikitext:
            results.append({
                "pageid":   int(page_id),
                "title":    title,
                "wikitext": wikitext,
            })
    return results


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download all Wikikids articles via the MediaWiki API.

    Saves wikikids_articles.jsonl with one record per article:
      {"pageid": 123, "title": "Hond", "wikitext": "..."}

    Resumes automatically if the file already exists (skips known page IDs).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "wikikids_articles.jsonl"
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[wikikids] [dry-run] would download ~51K Wikikids articles")
        return

    # Resume: load already-downloaded page IDs
    seen_ids: set[int] = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen_ids.add(json.loads(line)["pageid"])
                except (KeyError, json.JSONDecodeError):
                    pass
        logger.info("[wikikids] Resuming — %d articles already downloaded", len(seen_ids))

    # Step 1: collect all titles
    logger.info("[wikikids] Collecting article titles...")
    all_titles = _all_titles()
    logger.info("[wikikids] Found %d non-redirect articles", len(all_titles))

    # Step 2: fetch wikitext in batches
    total = len(seen_ids)
    with out_path.open("a", encoding="utf-8") as f:
        for i in range(0, len(all_titles), _BATCH):
            batch_titles = all_titles[i : i + _BATCH]
            try:
                records = _fetch_wikitext(batch_titles)
            except Exception as exc:
                logger.warning("[wikikids] Batch %d–%d failed: %s", i, i + _BATCH, exc)
                time.sleep(5)
                continue

            new = 0
            for rec in records:
                if rec["pageid"] in seen_ids:
                    continue
                seen_ids.add(rec["pageid"])
                rec["acquisition_date"] = acq_date
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1
                new += 1

            if new:
                logger.info(
                    "[wikikids] Batch %d–%d: +%d articles (total=%d)",
                    i, i + _BATCH, new, total,
                )
            time.sleep(_DELAY)

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _API,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_articles":   total,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[wikikids] Done. %d articles downloaded.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Wikikids articles (CC BY-SA 3.0)")
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
