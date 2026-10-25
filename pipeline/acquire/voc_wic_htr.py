"""
Download VOC/WIC Handwritten Text Recognition (HTR) transcriptions from Zenodo.

The Globalise project (Huygens Institute / University of Amsterdam) produced
machine-generated HTR transcriptions of ~3 million pages of VOC (Dutch East
India Company) and WIC (Dutch West India Company) records from the 17th-19th
centuries. These are Early Modern Dutch handwritten documents.

Zenodo record: https://zenodo.org/records/11209325
License: CC BY 4.0
Format: ZIP archive(s) containing text files or JSONL per inventory

The corpus is very large (~99 GB). Default download is limited to the first
N parts to allow selective acquisition.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "voc_wic_htr"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "historical"

_ZENODO_API       = "https://zenodo.org/api/records/11209325"
_ZENODO_FILES_API = "https://zenodo.org/api/records/11209325/files"
_USER_AGENT = "NederCorpus/0.1 (academic research; Early Modern Dutch corpus)"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, retries: int = 3, timeout: int = 120) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 10.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _get_zenodo_files() -> list[dict]:
    """Fetch the list of files in the Zenodo record via the API.

    Zenodo's InvenioRDM API (v3, deployed ~2023) moved the file listing from
    the record metadata response (``data["files"]``) to a separate endpoint
    at ``/api/records/{id}/files``.  We try the dedicated files endpoint first,
    then fall back to the legacy ``files`` key in the record metadata.

    Each returned dict has at minimum:
      - ``key``  : filename
      - ``links``: {"self": <download URL>}
      - ``size`` : bytes (int)
    """
    logger.info("[voc_wic_htr] Fetching Zenodo file list...")

    # 1. Try the dedicated files endpoint (InvenioRDM / new Zenodo)
    try:
        files_data = json.loads(_fetch(_ZENODO_FILES_API))
        # InvenioRDM returns {"entries": [...], "total": N}
        entries = files_data.get("entries", [])
        if entries:
            logger.info("[voc_wic_htr] Found %d files via /files endpoint", len(entries))
            # Normalise to the shape the download loop expects:
            # each entry has {"key": ..., "links": {"self": ...}, "size": ...}
            normalised = []
            for e in entries:
                key = e.get("key", e.get("filename", ""))
                size = e.get("size", e.get("file_size", 0))
                # InvenioRDM uses "content" link for direct download
                links = e.get("links", {})
                if "content" in links and "self" not in links:
                    links = {"self": links["content"], **links}
                normalised.append({"key": key, "size": size, "links": links})
            return normalised
    except Exception as exc:
        logger.warning("[voc_wic_htr] /files endpoint failed: %s — trying record metadata", exc)

    # 2. Fall back to the legacy record metadata ``files`` key (old Zenodo API)
    logger.info("[voc_wic_htr] Fetching Zenodo record metadata (legacy fallback)...")
    data = json.loads(_fetch(_ZENODO_API))
    files = data.get("files", [])
    if files:
        logger.info("[voc_wic_htr] Found %d files via record metadata (legacy)", len(files))
        return files

    # 3. As a last resort, construct URLs from the record HTML page listing
    logger.warning(
        "[voc_wic_htr] No files found via API — the Zenodo record structure may have "
        "changed again. Check https://zenodo.org/records/11209325 manually."
    )
    return []


def download(
    output_dir: Path,
    max_files: int = 5,
    dry_run: bool = False,
) -> None:
    """
    Download VOC/WIC HTR transcription files from Zenodo.

    Each file is a ZIP or JSONL containing HTR transcriptions of
    Early Modern Dutch archival documents.

    The full dataset is ~99 GB; default max_files=5 downloads a manageable
    sample. Set max_files=-1 (or very large) for full corpus.

    Args:
        max_files: Maximum number of files to download (-1 = all).
        dry_run:   Only log what would be downloaded, do not fetch.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[voc_wic_htr] [dry-run] would download from Zenodo record 11209325")
        return

    try:
        files = _get_zenodo_files()
    except Exception as exc:
        logger.error("[voc_wic_htr] Could not fetch Zenodo metadata: %s", exc)
        return

    if not files:
        logger.warning("[voc_wic_htr] No files found in Zenodo record")
        return

    # Sort by filename for reproducibility; prioritise .txt/.jsonl over ZIPs
    txt_files = [f for f in files if f.get("key", "").endswith((".txt", ".jsonl", ".json"))]
    zip_files = [f for f in files if f.get("key", "").endswith(".zip")]
    other_files = [f for f in files if f not in txt_files and f not in zip_files]
    ordered = txt_files + zip_files + other_files

    limit = len(ordered) if max_files < 0 else max_files
    downloaded = 0

    for file_meta in ordered[:limit]:
        key      = file_meta.get("key", "unknown")
        # Zenodo file download URL is in links.self
        file_url = file_meta.get("links", {}).get("self", "")
        if file_url:
            # Re-encode any spaces or special chars in the path
            from urllib.parse import urlparse, urlunparse, quote
            parsed = urlparse(file_url)
            file_url = urlunparse(parsed._replace(path=quote(parsed.path, safe='/')))
        else:
            # Fall back to constructed URL
            file_url = f"https://zenodo.org/records/11209325/files/{urllib.parse.quote(key, safe='')}"

        dest = output_dir / key
        if dest.exists():
            logger.info("[voc_wic_htr] Already downloaded: %s", key)
            downloaded += 1
            continue

        size_mb = file_meta.get("size", 0) / 1e6
        logger.info("[voc_wic_htr] Downloading %s (%.1f MB)...", key, size_mb)
        try:
            data = _fetch(file_url, timeout=600)  # Large files need long timeout
        except Exception as exc:
            logger.warning("[voc_wic_htr] Failed to download %s: %s", key, exc)
            continue

        dest.write_bytes(data)
        _log(output_dir, file_url)
        logger.info("[voc_wic_htr] Saved %s (%.1f MB actual)", key, len(data) / 1e6)
        downloaded += 1
        time.sleep(1.0)

    logger.info("[voc_wic_htr] Done. %d/%d files downloaded.", downloaded, limit)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download VOC/WIC HTR transcriptions from Zenodo (CC BY 4.0)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--max-files", type=int, default=5,
                        help="Max files to download (-1 = all, full corpus ~99 GB)")
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(args.output, max_files=args.max_files, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
