"""
Download Wikiwijs open educational resources via the Kennisnet Edurep SRU API.

Wikiwijs (wikiwijs.nl) is the Dutch national platform for open learning materials,
maintained by Kennisnet. All materials are indexed in the Edurep repository.

Strategy
--------
Phase 1: Paginate the Edurep SRU API to discover arrangements hosted on wikiwijs.nl
         with an open Creative Commons license (CC0, CC-BY, CC-BY-SA only).
         Each record has a URL to the arrangement page on wikiwijs.nl.

Phase 2: Fetch each arrangement HTML page and save it. The arrangement pages
         contain full text: title, introduction, and body paragraphs.

License policy (STRICT — per corpus policy):
  - ALLOWED: CC0, CC-BY (any version), CC-BY-SA (any version)
  - REJECTED: CC-BY-NC, CC-BY-ND, CC-BY-NC-SA, CC-BY-NC-ND, fair-use, unknown

Edurep SRU API: https://api.kennisnet.nl/edurep/2.0/
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "wikiwijs"
LICENSE = "CC-BY"          # conservative label; actual license stored per-doc
COUNTRY = "NL"
DOMAIN  = "education"
LANG    = "NL"

EDUREP_BASE  = "https://api.kennisnet.nl/edurep/2.0/"
WIKIWIJS_URL = "https://www.wikiwijs.nl"
_USER_AGENT  = "NederCorpus/0.1 (academic research; open educational resources)"
_DELAY       = 0.5  # seconds between requests

# Only keep these Creative Commons license variants
_ALLOWED_LICENSE_PATTERNS = [
    re.compile(r"creativecommons\.org/licenses/by(?:-sa)?/", re.I),
    re.compile(r"creativecommons\.org/publicdomain/zero/", re.I),
    re.compile(r"\bCC[- ]BY(?:[- ]SA)?(?:[- ]\d+\.\d+)?\b", re.I),
    re.compile(r"\bCC[- ]?0\b", re.I),
    re.compile(r"\bpublic[- ]?domain\b", re.I),
]
_BLOCKED_LICENSE_PATTERNS = [
    re.compile(r"creativecommons\.org/licenses/by-nc", re.I),
    re.compile(r"creativecommons\.org/licenses/by-nd", re.I),
    re.compile(r"\bNC\b"),
    re.compile(r"\bND\b"),
]

NS_SRW = "http://www.loc.gov/zing/srw/"
NS_DC  = "http://purl.org/dc/elements/1.1/"


def _license_allowed(license_str: str) -> bool:
    if not license_str:
        return False
    for pat in _BLOCKED_LICENSE_PATTERNS:
        if pat.search(license_str):
            return False
    for pat in _ALLOWED_LICENSE_PATTERNS:
        if pat.search(license_str):
            return True
    return False


def _normalize_license(license_str: str) -> str:
    s = license_str.lower()
    if "zero" in s or "cc0" in s or "public domain" in s:
        return "CC0"
    if "by-sa" in s:
        return "CC-BY-SA"
    return "CC-BY"


def _edurep_url(start: int, page_size: int) -> str:
    # Search for Dutch-language arrangements on wikiwijs.nl with CC license
    query = (
        'lom.general.language exact "nl" '
        'and lom.technical.location any "wikiwijs.nl/arrangement" '
        'and lom.rights.description any "creativecommons"'
    )
    params = {
        "operation":      "searchRetrieve",
        "version":        "1.2",
        "query":          query,
        "startRecord":    start,
        "maximumRecords": page_size,
        "recordSchema":   "smbAggregatedData",
        "x-api-key":      "NederCorpus",
    }
    return f"{EDUREP_BASE}?{urllib.parse.urlencode(params)}"


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410, 403):
                return None
            if attempt == retries - 1:
                logger.warning("[wikiwijs] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[wikiwijs] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _parse_edurep_records(xml_bytes: bytes) -> tuple[list[dict], int]:
    """
    Parse Edurep SRU response.
    Returns (list of {url, title, description, license}, total_records).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("[wikiwijs] Edurep XML parse error: %s", exc)
        return [], 0

    total_el = root.find(f"{{{NS_SRW}}}numberOfRecords")
    total = int(total_el.text) if total_el is not None and total_el.text else 0

    records: list[dict] = []
    for record in root.iter(f"{{{NS_SRW}}}record"):
        # Extract fields from the recordData
        rec_data = record.find(f"{{{NS_SRW}}}recordData")
        if rec_data is None:
            continue

        # Flatten all text under recordData
        full_text = ET.tostring(rec_data, encoding="unicode")

        # Extract URL (location)
        url_m = re.search(r'<[^>]*?location[^>]*?>([^<]+)</[^>]+>', full_text, re.I)
        if not url_m:
            continue
        resource_url = url_m.group(1).strip()
        if "wikiwijs.nl" not in resource_url or "/arrangement/" not in resource_url:
            continue

        # Extract license
        license_m = re.search(
            r'<[^>]*?(?:copyright|license|rights\.description)[^>]*?>([^<]*(?:creativecommons|cc[- ]by|cc0)[^<]*)</[^>]+>',
            full_text, re.I
        )
        license_str = license_m.group(1).strip() if license_m else ""

        if not _license_allowed(license_str):
            continue

        # Extract title
        title_m = re.search(
            r'<[^>]*?(?:lom\.general\.title|general\.title|dc:title)[^>]*?>([^<]{3,})</[^>]+>',
            full_text, re.I
        )
        title = title_m.group(1).strip() if title_m else ""

        records.append({
            "url": resource_url,
            "title": title,
            "license": _normalize_license(license_str),
        })

    return records, total


def _log(output_dir: Path, url: str, license_str: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          license_str,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _safe_stem(url: str) -> str:
    """Convert URL to a safe filename stem."""
    return re.sub(r"[^\w\-]", "_", url.split("wikiwijs.nl")[-1].strip("/"))[:200]


def download(
    output_dir: Path,
    max_records: int = 50_000,
    page_size: int = 100,
    dry_run: bool = False,
) -> None:
    """
    Phase 1: Discover arrangement URLs and licenses via Edurep SRU.
    Phase 2: Download arrangement HTML pages.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[wikiwijs] [dry-run] skipping")
        return

    # Load already-discovered URLs from manifest
    manifest_path = output_dir / "manifest.jsonl"
    known_urls: set[str] = set()
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            try:
                known_urls.add(json.loads(line)["url"])
            except Exception:
                pass

    # Phase 1: Discover
    start = 1
    new_records: list[dict] = []

    logger.info("[wikiwijs] Phase 1: discovering via Edurep SRU")
    while start <= max_records:
        url = _edurep_url(start, page_size)
        data = _fetch(url)
        if data is None:
            logger.warning("[wikiwijs] Edurep returned None at start=%d", start)
            break

        records, total = _parse_edurep_records(data)
        if not records and start == 1:
            logger.warning("[wikiwijs] No records found — Edurep query may need adjustment")
            break

        for rec in records:
            if rec["url"] not in known_urls:
                new_records.append(rec)
                known_urls.add(rec["url"])

        logger.info("[wikiwijs] start=%d: %d records this page, %d total available, %d new",
                    start, len(records), total, len(new_records))

        start += page_size
        if start > min(total, max_records):
            break
        if not records:
            break

        time.sleep(_DELAY)

    # Append new records to manifest
    if new_records:
        with manifest_path.open("a", encoding="utf-8") as f:
            for rec in new_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("[wikiwijs] Added %d new arrangement URLs to manifest", len(new_records))

    # Phase 2: Download arrangement HTML pages
    logger.info("[wikiwijs] Phase 2: downloading arrangement HTML pages")
    downloaded = 0
    skipped = 0

    all_records: list[dict] = []
    if not manifest_path.exists():
        logger.info("[wikiwijs] No manifest yet — nothing to download in Phase 2")
        return
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        try:
            all_records.append(json.loads(line))
        except Exception:
            pass

    for rec in all_records:
        stem = _safe_stem(rec["url"])
        dest = output_dir / f"{stem}.html"
        meta_file = output_dir / f"{stem}.json"

        if dest.exists():
            skipped += 1
            continue

        html = _fetch(rec["url"], timeout=30)
        if html is None:
            logger.debug("[wikiwijs] 404/403 for %s", rec["url"])
            continue

        dest.write_bytes(html)
        meta_file.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        _log(output_dir, rec["url"], rec["license"])
        downloaded += 1

        time.sleep(_DELAY)

    logger.info("[wikiwijs] Done. %d downloaded, %d skipped.", downloaded, skipped)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Wikiwijs open educational resources (CC0/CC-BY/CC-BY-SA)"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--max-records", type=int, default=50_000)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    download(args.output, max_records=args.max_records, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
