"""
Download Tweede Kamer (Dutch House of Representatives) open data.

The Tweede Kamer publishes its complete parliamentary record as open data
under CC0 1.0. This includes:
  - Plenary session reports (Kamerstuk/Handeling)
  - Parliamentary bills and motions
  - Committee meeting reports
  - Questions and answers (Kamervragen)

Endpoint: https://opendata.tweedekamer.nl/
SyncFeed XML API: https://gegevensmagazijn.tweedekamer.nl/SyncFeed/2.0/Feed
License: CC0 1.0 Universal

Covers: 2010 to present (current legislature and archives).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import ssl
import time
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE   = "tweede_kamer_opendata"
LICENSE  = "CC0"
COUNTRY  = "NL"
DOMAIN   = "parliamentary"

_FEED_BASE     = "https://gegevensmagazijn.tweedekamer.nl/SyncFeed/2.0/Feed"
_RESOURCE_BASE = "https://gegevensmagazijn.tweedekamer.nl/SyncFeed/2.0/Resources"
_USER_AGENT    = "NederCorpus/0.1 (academic research; Dutch parliament; open data CC0)"

# Document categories whose entities have downloadable VLOS XML at /Resources/{uuid}.
# Only "vergadering" (plenary/committee meeting) and "handeling" (official transcript)
# entries are served as full VLOS XML. Other categories (document, verslag, kamerstuk,
# kamervraag, motie, amendement) are either PDFs or metadata-only and return 404.
_RELEVANT_CATEGORIES = {
    "vergadering", "handeling",
    "Vergadering", "Handeling",
}


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
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 10.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _feed_url(skip: int) -> str:
    return f"{_FEED_BASE}?skip={skip}"


def _collect_resource_uuids(output_dir: Path) -> list[str]:
    """
    Parse all tk_feed_*.xml files and return UUIDs for relevant-category entries
    that haven't been downloaded yet as tk_doc_{uuid}.xml.

    Each feed page contains ~250 <entry> elements. We split on <entry> boundaries
    using regex, then for each entry extract the Entiteiten UUID and category from
    the portion BEFORE the <content type="application/xml"> block (which may
    itself contain Atom-like markup we don't want to confuse with the entry header).
    """
    import re
    _ENTRY_RE = re.compile(r"<entry\b[^>]*>(.*?)</entry>", re.I | re.S)
    _UUID_RE = re.compile(
        r"<id[^>]*>\s*https?://[^<]*/Entiteiten/([0-9a-f-]{36})\s*</id>",
        re.I,
    )
    _CAT_RE = re.compile(r'<category\s+term="([^"]+)"', re.I)

    _RELEVANT = {c.lower() for c in _RELEVANT_CATEGORIES}
    seen: set[str] = set()
    uuids: list[str] = []

    for xml_path in sorted(output_dir.glob("tk_feed_*.xml")):
        try:
            text = xml_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for entry_m in _ENTRY_RE.finditer(text):
            entry_text = entry_m.group(1)
            # Only look at text before the <content> block to avoid false matches
            content_start = entry_text.find('<content type="application/xml">')
            header = entry_text[:content_start] if content_start != -1 else entry_text

            m_id = _UUID_RE.search(header)
            m_cat = _CAT_RE.search(header)
            if not m_id or not m_cat:
                continue
            if m_cat.group(1).lower() not in _RELEVANT:
                continue

            uid = m_id.group(1)
            if uid in seen:
                continue
            seen.add(uid)

            dest = output_dir / f"tk_doc_{uid}.xml"
            if dest.exists():
                continue
            uuids.append(uid)

    return uuids


def _purge_duplicate_feed_files(output_dir: Path) -> None:
    """
    Delete feed files that are all identical (from the broken skip= pagination).
    Keeps only pages that have a unique UUID; detects the duplicate run by
    checking if all pages share the same Entiteiten UUID.
    """
    import re
    _UUID_RE = re.compile(r'Entiteiten/([0-9a-f-]{36})', re.I)
    files = sorted(output_dir.glob("tk_feed_*.xml"))
    if not files:
        return
    uids = set()
    for f in files[:20]:  # sample first 20
        try:
            m = _UUID_RE.search(f.read_text(encoding="utf-8", errors="replace")[:2000])
            if m:
                uids.add(m.group(1))
        except OSError:
            pass
    if len(uids) <= 1:
        # All duplicates — delete the lot so Phase 1 reruns with correct skiptoken
        logger.warning(
            "[tweede_kamer_opendata] Detected %d duplicate feed files (skip= pagination bug) — purging",
            len(files),
        )
        for f in files:
            f.unlink(missing_ok=True)
        logger.info("[tweede_kamer_opendata] Purged %d duplicate feed files", len(files))


def download(
    output_dir: Path,
    max_pages: int = 100_000,
    dry_run: bool = False,
) -> None:
    """
    Download Tweede Kamer SyncFeed Atom pages via correct skiptoken cursor pagination.

    The SyncFeed API uses token-based (opaque cursor) pagination via
    <link rel="next" href="...?skiptoken=NNN" />. Using skip=0,20,40...
    returns the same first page every time. This implementation follows
    the next-link chain correctly.

    Saves each page as tk_feed_{page:06d}.xml. Resumes by reading the
    last saved page to extract the next skiptoken.
    """
    import re
    _NEXT_RE = re.compile(r'<link[^>]+rel="next"[^>]+href="([^"]+)"', re.I)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Purge any stale duplicate-filled feed files from the broken skip= run
    _purge_duplicate_feed_files(output_dir)

    if dry_run:
        logger.info("[tweede_kamer_opendata] [dry-run] would download TK SyncFeed")
        return

    # Find resume point: highest existing page + extract its next skiptoken
    existing = sorted(output_dir.glob("tk_feed_*.xml"))
    if existing:
        last_file = existing[-1]
        page = int(last_file.stem.split("_")[-1]) + 1
        try:
            last_text = last_file.read_text(encoding="utf-8", errors="replace")
            m = _NEXT_RE.search(last_text)
            next_url = m.group(1) if m else None
        except OSError:
            next_url = None
        if next_url:
            logger.info(
                "[tweede_kamer_opendata] Resuming from page %d, next url: %s",
                page, next_url[-60:],
            )
        else:
            logger.warning(
                "[tweede_kamer_opendata] Last page %d has no next link — feed may be exhausted",
                page - 1,
            )
            next_url = None
    else:
        page = 0
        next_url = _feed_url(0)
        _log(output_dir, next_url)

    while page < max_pages and next_url:
        dest = output_dir / f"tk_feed_{page:06d}.xml"

        logger.info("[tweede_kamer_opendata] Fetching feed page %d: %s", page, next_url[-60:])
        try:
            data = _fetch(next_url, timeout=60)
        except Exception as exc:
            logger.error("[tweede_kamer_opendata] Failed page %d: %s", page, exc)
            break

        text = data.decode("utf-8", errors="replace")
        if len(data) < 200 or "<entry>" not in text:
            logger.info("[tweede_kamer_opendata] Feed exhausted at page %d", page)
            break

        dest.write_bytes(data)

        m = _NEXT_RE.search(text)
        next_url = m.group(1) if m else None

        page += 1
        time.sleep(0.5)

        if page % 500 == 0:
            logger.info("[tweede_kamer_opendata] Phase 1: %d feed pages downloaded", page)

    logger.info("[tweede_kamer_opendata] Phase 1 done: %d feed pages total", page)

    # Phase 2: fetch individual resource XMLs for relevant-category entries
    logger.info("[tweede_kamer_opendata] Phase 2: collecting resource UUIDs from feed pages …")
    uuids = _collect_resource_uuids(output_dir)
    logger.info("[tweede_kamer_opendata] %d resource XMLs to fetch", len(uuids))

    for i, uid in enumerate(uuids):
        dest = output_dir / f"tk_doc_{uid}.xml"
        if dest.exists():
            continue
        url = f"{_RESOURCE_BASE}/{uid}"
        try:
            data = _fetch(url, timeout=60, retries=1)
        except Exception as exc:
            logger.debug("[tweede_kamer_opendata] Skip %s: %s", uid, exc)
            continue
        if len(data) < 100:
            continue
        dest.write_bytes(data)
        if i % 500 == 0:
            logger.info("[tweede_kamer_opendata] Phase 2: %d/%d fetched", i + 1, len(uuids))
        time.sleep(0.3)

    logger.info("[tweede_kamer_opendata] Phase 2 complete.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Tweede Kamer open data SyncFeed (CC0)"
    )
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-pages",  type=int, default=5000,
                        help="Maximum feed pages to download (default: 5000)")
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(args.output, max_pages=args.max_pages, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
