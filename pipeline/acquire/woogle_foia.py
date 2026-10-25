"""
Download Dutch FOIA (Woo/Wob) documents from DANS Dataverse.

Woogle is a search engine for Woo/Wob (Government Information Act) requests.
The dataset on DANS Dataverse contains OCR'd body text of FOIA documents —
approximately 378M words of Dutch government communications.

License: CC BY 4.0
DANS DOI: doi:10.17026/dans-zau-e3rk
Dataset: https://dans.datastations.nl/dataset.xhtml?persistentId=doi:10.17026/dans-zau-e3rk

Strategy:
  1. Query the Dataverse API for the dataset's file list
  2. Download each CSV file into woogle_foia_files/
  3. Resume: skip files that already exist

Usage:
  python -m pipeline acquire --sources woogle_foia
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "woogle_foia"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "government"

_DATAVERSE_BASE = "https://ssh.datastations.nl"  # dans.datastations.nl DNS fails from NL hosts
_PERSISTENT_ID  = "doi:10.17026/dans-zau-e3rk"
_UA             = "NederCorpus/0.1 (academic; Dutch corpus; contact: corpus@example.org)"
_DELAY          = 2.0   # seconds between file downloads
_TIMEOUT        = 120   # seconds per request


def _fetch_bytes(url: str, retries: int = 3) -> bytes:
    """GET url, return raw bytes. Retries on transient errors."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = _DELAY * (2 ** attempt)
                logger.warning("[woogle_foia] HTTP %d for %s — retrying in %.0fs", exc.code, url, wait)
                time.sleep(wait)
            else:
                raise
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = _DELAY * (attempt + 1)
            logger.warning("[woogle_foia] Error fetching %s: %s — retry %d", url, exc, attempt + 1)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")


def _get_dataset_files() -> list[dict]:
    """
    Query Dataverse API for the dataset's file list.

    Returns a list of dicts, each with:
      - id:        dataFile.id
      - filename:  dataFile.filename
      - filesize:  dataFile.filesize
    """
    url = (
        f"{_DATAVERSE_BASE}/api/datasets/:persistentId/"
        f"?persistentId={urllib.parse.quote(_PERSISTENT_ID, safe=':/')}"
    )
    logger.info("[woogle_foia] Fetching dataset metadata from %s", url)
    try:
        data = json.loads(_fetch_bytes(url))
    except Exception as exc:
        logger.error("[woogle_foia] Cannot fetch dataset metadata: %s", exc)
        return []

    files = []
    try:
        for f in data["data"]["latestVersion"]["files"]:
            df = f.get("dataFile", {})
            files.append({
                "id":       df.get("id"),
                "filename": df.get("filename", f"file_{df.get('id', 'unknown')}"),
                "filesize": df.get("filesize", 0),
            })
    except (KeyError, TypeError) as exc:
        logger.error("[woogle_foia] Unexpected dataset metadata structure: %s", exc)

    return files


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download Woogle FOIA dataset files from DANS Dataverse.

    Saves files into output_dir/woogle_foia_files/{filename}.
    Resumes automatically: existing files are skipped.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    files_dir = output_dir / "woogle_foia_files"
    files_dir.mkdir(exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info(
            "[woogle_foia] [dry-run] would download Woogle FOIA dataset "
            "from DANS (doi:%s)", _PERSISTENT_ID
        )
        return

    # Step 1: get file list
    dataset_files = _get_dataset_files()
    if not dataset_files:
        logger.error("[woogle_foia] No files found in dataset — aborting.")
        return

    logger.info("[woogle_foia] Dataset has %d file(s)", len(dataset_files))

    total_downloaded = 0
    total_skipped    = 0

    for file_info in dataset_files:
        file_id  = file_info["id"]
        filename = file_info["filename"]
        filesize = file_info["filesize"]

        dest_path = files_dir / filename

        # Resume: skip if already fully downloaded
        if dest_path.exists() and dest_path.stat().st_size > 0:
            # Accept as resumed if size matches (or if filesize unknown)
            if filesize == 0 or dest_path.stat().st_size == filesize:
                logger.info("[woogle_foia] Skipping (already downloaded): %s", filename)
                total_skipped += 1
                continue

        logger.info(
            "[woogle_foia] Downloading %s (id=%s, size=%d bytes)",
            filename, file_id, filesize,
        )

        download_url = f"{_DATAVERSE_BASE}/api/access/datafile/{file_id}"
        try:
            data = _fetch_bytes(download_url)
        except Exception as exc:
            logger.warning("[woogle_foia] Failed to download %s: %s", filename, exc)
            continue

        dest_path.write_bytes(data)
        logger.info("[woogle_foia] Saved %s (%d bytes)", filename, len(data))
        total_downloaded += 1

        time.sleep(_DELAY)

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":            SOURCE,
            "url":               f"{_DATAVERSE_BASE}/dataset.xhtml?persistentId={_PERSISTENT_ID}",
            "license":           LICENSE,
            "acquisition_date":  acq_date,
            "total_downloaded":  total_downloaded,
            "total_skipped":     total_skipped,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(
        "[woogle_foia] Done. Downloaded=%d, skipped=%d.",
        total_downloaded, total_skipped,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Woogle FOIA dataset from DANS Dataverse (CC BY 4.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
