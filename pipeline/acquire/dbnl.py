"""
Download public-domain Dutch literary texts from DBNL.

The Digitale Bibliotheek voor de Nederlandse Letteren (DBNL) provides
a bulk ZIP download of all public-domain works in plain-text and XML.
~3,000 works covering Dutch literature from the Middle Ages to early 20th c.

Bulk download URLs (confirmed working):
  TXT: https://www.dbnl.org/letterkunde/pd/txt_pd.zip
  XML: https://www.dbnl.org/letterkunde/pd/xml_pd.zip

Metadata CSV: https://www.dbnl.org/extern/titels_pd.php

License: Public Domain
  Works are explicitly released as "auteursrechtvrij" (copyright-free)
  for any purpose. See https://www.dbnl.org/letterkunde/pd/
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import logging
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "dbnl"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "literature"

DBNL_TXT_ZIP  = "https://www.dbnl.org/letterkunde/pd/txt_pd.zip"
DBNL_META_CSV = "https://www.dbnl.org/extern/titels_pd.php"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_stream(url: str, dest: Path, timeout: int = 300) -> None:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public domain texts)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)


def _fetch(url: str, timeout: int = 60) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public domain texts)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return resp.read()


def _download_metadata(output_dir: Path) -> dict[str, dict]:
    """
    Download and parse the DBNL PD metadata CSV.

    Returns dict mapping dbnl_id → {title, author, year, ...}.
    """
    meta_path = output_dir / "titels_pd.csv"
    if meta_path.exists():
        logger.info("[dbnl] Metadata CSV already present")
    else:
        logger.info("[dbnl] Downloading metadata CSV …")
        try:
            raw = _fetch(DBNL_META_CSV)
            meta_path.write_bytes(raw)
        except Exception as exc:
            logger.warning("[dbnl] Metadata download failed: %s", exc)
            return {}

    metadata: dict[str, dict] = {}
    try:
        text = meta_path.read_text(encoding="utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        for row in reader:
            # Common field names in the DBNL CSV
            dbnl_id = (
                row.get("id") or row.get("titelopmaak") or row.get("tekstid") or ""
            ).strip()
            if dbnl_id:
                metadata[dbnl_id] = {k.strip(): v.strip() for k, v in row.items()}
    except Exception as exc:
        logger.warning("[dbnl] Metadata CSV parse error: %s", exc)

    logger.info("[dbnl] Metadata loaded: %d entries", len(metadata))
    return metadata


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download DBNL public-domain Dutch literary texts.

    Downloads the bulk TXT ZIP (~50-200 MB), extracts each work as a
    separate .txt file into output_dir/texts/.  A metadata CSV is also
    downloaded to output_dir/titels_pd.csv for parser use.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = output_dir / "texts"
    texts_dir.mkdir(exist_ok=True)

    archive_path = output_dir / "txt_pd.zip"

    logger.info("[dbnl] Source: %s", DBNL_TXT_ZIP)
    logger.info("[dbnl] License: Public Domain")

    if dry_run:
        logger.info("[dry-run] skipping download")
        return

    # Metadata CSV (small, always re-download if missing)
    _download_metadata(output_dir)

    # Download ZIP archive
    if archive_path.exists():
        logger.info("[dbnl] Archive already present — skipping download")
    else:
        logger.info("[dbnl] Downloading TXT archive …")
        try:
            _fetch_stream(DBNL_TXT_ZIP, archive_path)
            _log(output_dir, DBNL_TXT_ZIP)
            logger.info("[dbnl] Archive saved: %s", archive_path)
        except Exception as exc:
            logger.error("[dbnl] Download failed: %s", exc)
            archive_path.unlink(missing_ok=True)
            return

    # Extract TXT files
    logger.info("[dbnl] Extracting text files …")
    extracted = skipped = 0
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for name in zf.namelist():
                if not name.endswith(".txt"):
                    continue
                file_name = Path(name).name
                dest = texts_dir / file_name
                if dest.exists():
                    skipped += 1
                    continue
                dest.write_bytes(zf.read(name))
                extracted += 1
    except Exception as exc:
        logger.error("[dbnl] Extraction failed: %s", exc)
        return

    logger.info("[dbnl] Extracted %d files (%d already present).", extracted, skipped)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download DBNL public-domain Dutch literary texts"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
