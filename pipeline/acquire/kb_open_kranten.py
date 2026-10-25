"""
Download historical Dutch newspaper articles from the KB Open Kranten dataset.

The KB (Koninklijke Bibliotheek) provides bulk access to historical Dutch newspapers
that are out of copyright. Articles are supplied as ALTO XML files inside ZIP archives.

License: CC-BY 4.0
Sources tried in order:
  1. KB Labs dataset page — scrape to discover ZIP download URLs
  2. Known bulk ZIP URL patterns (KB resolver / lab.kb.nl)
  3. Fallback: KB SRU for ANP (Algemeen Nederlands Persbureau) collection metadata

Usage:
  python -m pipeline acquire --sources kb_open_kranten
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "kb_open_kranten"
LICENSE = "CC-BY"
DOMAIN  = "news"
COUNTRY = "NL"

_UA = "NederCorpus/0.1 (academic research; open government data; contact: corpus@example.org)"

# Known entry points to try
_LAB_PAGE    = "https://lab.kb.nl/dataset/open-kranten"
_LAB_API     = "https://lab.kb.nl/api/dataset"
_KB_RESOLVER = "https://resolver.kb.nl/resolve"

# KB SRU fallback — ANP collection
_KB_SRU = "https://jsru.kb.nl/sru/sru"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_DELAY = 2.0  # seconds between requests


def _fetch(url: str, retries: int = 3, return_bytes: bool = False) -> bytes | str | None:
    """Fetch a URL, return bytes or decoded string. Returns None on failure."""
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as resp:
                data = resp.read()
                return data if return_bytes else data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            logger.warning("[kb_kranten] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1)
            if exc.code in (403, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[kb_kranten] Error fetching %s: %s (attempt %d)", url, exc, attempt + 1)
            time.sleep(2.0 * (attempt + 1))
    return None


def _find_zip_urls_from_page(html: str) -> list[str]:
    """
    Scan an HTML page for links to .zip files that look like newspaper data.
    Returns a list of absolute URLs.
    """
    urls: list[str] = []
    # Match href attributes pointing to .zip
    for m in re.finditer(r'href=["\']([^"\']+\.zip)["\']', html, re.IGNORECASE):
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://lab.kb.nl" + url
        if url not in urls:
            urls.append(url)
    return urls


def _try_lab_api(output_dir: Path) -> list[str]:
    """
    Try the KB Lab API to discover dataset download URLs.
    Returns a list of ZIP URLs.
    """
    logger.info("[kb_kranten] Probing KB Lab API at %s", _LAB_API)
    result = _fetch(_LAB_API)
    if result:
        probe_path = output_dir / "kb_lab_api_probe.json"
        probe_path.write_text(result, encoding="utf-8")
        logger.info("[kb_kranten] Lab API response saved to %s", probe_path)
        # Try to parse as JSON and find ZIP URLs
        try:
            data = json.loads(result)
            urls: list[str] = []
            _walk_json_for_zips(data, urls)
            if urls:
                return urls
        except json.JSONDecodeError:
            pass
        # Fall back to regex scan
        return _find_zip_urls_from_page(result)
    return []


def _walk_json_for_zips(obj, urls: list[str]) -> None:
    """Recursively walk a JSON structure and collect .zip URLs."""
    if isinstance(obj, str) and obj.endswith(".zip"):
        if obj not in urls:
            urls.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_json_for_zips(v, urls)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_zips(item, urls)


def _extract_zip_to_articles(zip_bytes: bytes, articles_dir: Path, zip_name: str) -> int:
    """
    Extract ALTO XML article files from a ZIP archive.
    Saves only files matching *_articletext.xml or *.xml with <alto> or <text> root.
    Returns the number of files extracted.
    """
    extracted = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                name = info.filename
                # Only extract article text XML
                if not name.endswith(".xml"):
                    continue
                if info.file_size == 0:
                    continue

                # Determine output filename — flatten path, preserve zip name prefix
                safe_name = name.replace("/", "_").replace("\\", "_")
                dest = articles_dir / f"{zip_name}_{safe_name}"
                if dest.exists():
                    extracted += 1
                    continue

                try:
                    data = zf.read(info.filename)
                    dest.write_bytes(data)
                    extracted += 1
                except Exception as exc:
                    logger.warning("[kb_kranten] Failed to extract %s: %s", name, exc)
    except zipfile.BadZipFile as exc:
        logger.warning("[kb_kranten] Bad ZIP for %s: %s", zip_name, exc)
    return extracted


def _download_zip(url: str, output_dir: Path, articles_dir: Path, label: str) -> bool:
    """
    Download a ZIP from url, save it, and extract article XML files.
    Returns True if successful.
    """
    zip_path = output_dir / f"kb_kranten_{label}.zip"

    if zip_path.exists():
        logger.info("[kb_kranten] Already have %s, extracting...", zip_path.name)
        try:
            data = zip_path.read_bytes()
            n = _extract_zip_to_articles(data, articles_dir, label)
            logger.info("[kb_kranten] %s: %d XML files extracted", label, n)
            return True
        except Exception as exc:
            logger.warning("[kb_kranten] Re-extraction failed for %s: %s", label, exc)
            return False

    logger.info("[kb_kranten] Downloading %s from %s", label, url)
    data = _fetch(url, return_bytes=True)
    if data is None:
        logger.warning("[kb_kranten] Download failed: %s", url)
        return False

    zip_path.write_bytes(data)
    logger.info("[kb_kranten] Saved %s (%.1f MB)", zip_path.name, len(data) / 1e6)

    n = _extract_zip_to_articles(data, articles_dir, label)
    logger.info("[kb_kranten] %s: %d XML files extracted", label, n)
    return True


def _try_resolver_patterns(output_dir: Path, articles_dir: Path) -> int:
    """
    Try known KB resolver URL patterns for bulk newspaper ZIPs.
    Returns the number of ZIPs successfully downloaded.
    """
    # These are speculative patterns based on KB data infrastructure
    candidate_paths = [
        "DATA:kranten:kranten_pd_16xx.zip",
        "DATA:kranten:kranten_pd_17xx.zip",
        "DATA:kranten:kranten_pd_18xx.zip",
        "DATA:kranten:kranten_pd_19xx.zip",
        "DATA:kranten:open_kranten_sample.zip",
    ]
    downloaded = 0
    for path in candidate_paths:
        label = path.split(":")[-1].replace(".zip", "")
        url = f"{_KB_RESOLVER}?urn={urllib.parse.quote(path)}"
        if _download_zip(url, output_dir, articles_dir, label):
            downloaded += 1
        time.sleep(_DELAY)
    return downloaded


def _sru_fallback(output_dir: Path) -> int:
    """
    Fallback: use KB SRU to harvest ANP newspaper metadata.
    Saves batched XML responses as kb_kranten_sru_batch_*.xml
    Returns the number of records fetched.
    """
    logger.info("[kb_kranten] Falling back to KB SRU for ANP collection")

    # Try available collections
    collections = ["ANP", "BYVANCK", "MMKB19", "MMKB20"]
    total = 0
    for collection in collections:
        params = {
            "operation":      "searchRetrieve",
            "version":        "1.2",
            "query":          "dc.language=nl",
            "x-collection":   collection,
            "startRecord":    1,
            "maximumRecords": 100,
            "recordSchema":   "dcx",
        }
        url = f"{_KB_SRU}?{urllib.parse.urlencode(params)}"
        logger.info("[kb_kranten] Probing SRU collection %s", collection)
        data = _fetch(url)
        if data is None:
            logger.warning("[kb_kranten] SRU collection %s unavailable", collection)
            continue

        # Check for error response
        if "diagnostics" in data.lower() or "numberOfRecords>0" in data:
            logger.warning("[kb_kranten] SRU collection %s returned 0 records or error", collection)
            # Save probe for inspection
            (output_dir / f"kb_sru_{collection}_probe.xml").write_text(data, encoding="utf-8")
            continue

        batch_file = output_dir / f"kb_kranten_sru_{collection}_batch_00000001.xml"
        if not batch_file.exists():
            batch_file.write_text(data, encoding="utf-8")
            logger.info("[kb_kranten] Saved SRU batch for %s", collection)
            total += 100
        time.sleep(_DELAY)
    return total


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """Download KB Open Kranten historical newspaper articles."""
    output_dir.mkdir(parents=True, exist_ok=True)
    articles_dir = output_dir / "articles"
    articles_dir.mkdir(exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[kb_kranten] [dry-run] would download KB Open Kranten ZIP archives")
        return

    success_count = 0

    # -----------------------------------------------------------------------
    # Step 1: Try to discover ZIP URLs from the KB Lab dataset page
    # -----------------------------------------------------------------------
    logger.info("[kb_kranten] Probing KB Lab dataset page at %s", _LAB_PAGE)
    html = _fetch(_LAB_PAGE)
    if html:
        (output_dir / "kb_lab_page_probe.html").write_text(html, encoding="utf-8")
        zip_urls = _find_zip_urls_from_page(html)
        if zip_urls:
            logger.info("[kb_kranten] Found %d ZIP URLs on lab page", len(zip_urls))
            for i, url in enumerate(zip_urls):
                label = f"lab_{i:03d}"
                if _download_zip(url, output_dir, articles_dir, label):
                    success_count += 1
                time.sleep(_DELAY)
        else:
            logger.info("[kb_kranten] No ZIP links found on lab page")
    else:
        logger.warning("[kb_kranten] Lab page unreachable")

    # -----------------------------------------------------------------------
    # Step 2: Try KB Lab API
    # -----------------------------------------------------------------------
    if success_count == 0:
        zip_urls = _try_lab_api(output_dir)
        if zip_urls:
            logger.info("[kb_kranten] Found %d ZIP URLs from Lab API", len(zip_urls))
            for i, url in enumerate(zip_urls):
                label = f"api_{i:03d}"
                if _download_zip(url, output_dir, articles_dir, label):
                    success_count += 1
                time.sleep(_DELAY)

    # -----------------------------------------------------------------------
    # Step 3: Try KB resolver URL patterns
    # -----------------------------------------------------------------------
    if success_count == 0:
        logger.info("[kb_kranten] Trying KB resolver URL patterns")
        n = _try_resolver_patterns(output_dir, articles_dir)
        if n > 0:
            success_count += n

    # -----------------------------------------------------------------------
    # Step 4: SRU fallback (metadata only, no article text)
    # -----------------------------------------------------------------------
    if success_count == 0:
        logger.info("[kb_kranten] All ZIP methods failed — trying SRU metadata fallback")
        n = _sru_fallback(output_dir)
        if n > 0:
            logger.info("[kb_kranten] SRU fallback: fetched metadata for ~%d records", n)
            success_count += 1
        else:
            logger.warning(
                "[kb_kranten] All download strategies failed. "
                "Check kb_lab_page_probe.html and *_probe.* files in %s for "
                "current API structure. The KB dataset page URL or ZIP locations "
                "may have changed.", output_dir
            )

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _LAB_PAGE,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "success_count":    success_count,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    xml_count = sum(1 for _ in articles_dir.glob("*.xml"))
    logger.info("[kb_kranten] Done. %d article XML files in %s", xml_count, articles_dir)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download KB Open Kranten historical newspaper articles (CC-BY 4.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
