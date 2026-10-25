"""
Download Middle Dutch Manuscripts from the Carthusian Monastery of Herne (Zenodo).

Hyper-diplomatic transcriptions of 18 Middle Dutch manuscripts from the 14th-century
Carthusian monastery of Herne (now Belgium). Texts are primarily religious prose and
translations in Middle Dutch / Old Flemish.

The transcriptions are provided as PAGEXML files (one per manuscript page/folium).
Each PAGEXML contains <TextLine><TextEquiv><Unicode> elements with the transcribed text.
Metadata is in accompanying spreadsheets; we parse only the PAGEXML transcriptions.

License: CC BY 4.0
  https://zenodo.org/records/10005366

Size: ~166 MB (18 ZIPs, one per manuscript)
Domain: literary / historical
Language: historical NL (Middle Dutch, 14th century Flemish)
Country: BE
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "herne_manuscripts"
LICENSE = "CC-BY"
COUNTRY = "BE"
DOMAIN  = "literary"

# Zenodo record 10005366 — the Transcriptions/ folder contains per-manuscript ZIPs.
# We enumerate the record's files via the Zenodo REST API.
_ZENODO_RECORD = "10005366"
_ZENODO_API    = f"https://zenodo.org/api/records/{_ZENODO_RECORD}"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NederCorpus/0.1 (academic research; public data)",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        import json as _json
        return _json.loads(resp.read().decode("utf-8"))


def _download_file(url: str, dest: Path, timeout: int = 300) -> None:
    from urllib.parse import urlparse, urlunparse, quote
    # Percent-encode the path component (handles spaces and non-ASCII in filenames)
    parts = urlparse(url)
    safe_path = quote(parts.path, safe="/:@!$&'()*+,;=")
    safe_url  = urlunparse(parts._replace(path=safe_path))
    req = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "NederCorpus/0.1 (academic research; public data)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with dest.open("wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[herne_manuscripts] Fetching Zenodo record %s …", _ZENODO_RECORD)
    try:
        record = _fetch_json(_ZENODO_API)
    except Exception as exc:
        logger.error("[herne_manuscripts] Failed to fetch Zenodo API: %s", exc)
        return

    files = record.get("files", [])
    # Only download the per-manuscript ZIPs in the Transcriptions folder
    _SKIP_ZIPS = {"code.zip", "spreadsheets.zip"}
    ms_zips = [
        f for f in files
        if f.get("key", "").endswith(".zip")
        and Path(f["key"]).name not in _SKIP_ZIPS
    ]

    logger.info("[herne_manuscripts] Found %d manuscript ZIPs", len(ms_zips))

    pagexml_dir = output_dir / "pagexml"
    pagexml_dir.mkdir(exist_ok=True)
    downloaded = 0

    for file_entry in ms_zips:
        key  = file_entry["key"]            # e.g. "Transcriptions/MS_A.zip"
        url  = file_entry["links"]["self"]
        ms_name = Path(key).stem            # e.g. "MS_A"
        ms_dir  = pagexml_dir / ms_name

        if ms_dir.exists() and any(ms_dir.rglob("*.xml")):
            logger.info("[herne_manuscripts] %s already extracted — skipping", ms_name)
            continue

        if dry_run:
            logger.info("[herne_manuscripts] DRY RUN — would download %s", key)
            continue

        zip_path = output_dir / f"{ms_name}.zip"
        if not zip_path.exists():
            logger.info("[herne_manuscripts] Downloading %s …", key)
            try:
                _download_file(url, zip_path)
            except Exception as exc:
                logger.warning("[herne_manuscripts] Failed to download %s: %s", key, exc)
                continue

        # Extract PAGEXML files (*.xml in any subdirectory)
        ms_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                if member.endswith(".xml") and not member.startswith("__MACOSX"):
                    fname = Path(member).name
                    dest  = ms_dir / fname
                    if not dest.exists():
                        dest.write_bytes(zf.read(member))
        zip_path.unlink(missing_ok=True)   # remove ZIP after extraction to save space
        downloaded += 1

    logger.info("[herne_manuscripts] Done. %d manuscripts extracted to %s", downloaded, pagexml_dir)
    _log(output_dir, f"https://zenodo.org/records/{_ZENODO_RECORD}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Herne manuscripts PAGEXML (Zenodo)")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
