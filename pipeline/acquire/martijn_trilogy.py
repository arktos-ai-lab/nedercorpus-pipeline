"""
Download the Martijn Trilogy Manuscripts dataset from Zenodo.

Open transcriptions of 17 manuscript witnesses of Jacob van Maerlant's
Martijn Trilogy (13th-century Middle Dutch poetry). Contains 15,814 verses /
79,359 tokens in plain text, XML, HTML, and XLSX formats.

Only the plain_txt/ files are ingested.

License: CC BY-SA 4.0
  https://zenodo.org/records/10409449
Repository: https://github.com/SofieMoors/martijnmanuscripts

Size: ~3.1 MB ZIP
Domain: literary / historical
Language: historical NL (Middle Dutch, Flemish, 13th century)
Country: NL/BE
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "martijn_trilogy"
LICENSE = "CC-BY-SA"
COUNTRY = "NL"
DOMAIN  = "literary"

_ZENODO_URL = "https://zenodo.org/records/10409449/files/SofieMoors/martijnmanuscripts-2.zip?download=1"
_ZIP_NAME   = "martijnmanuscripts.zip"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / _ZIP_NAME

    if zip_path.exists():
        logger.info("[martijn_trilogy] ZIP already present — skipping download")
    elif dry_run:
        logger.info("[martijn_trilogy] DRY RUN — would download %s", _ZENODO_URL)
        return
    else:
        logger.info("[martijn_trilogy] Downloading %s …", _ZENODO_URL)
        headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
        req = urllib.request.Request(_ZENODO_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            with zip_path.open("wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
        logger.info("[martijn_trilogy] Downloaded to %s", zip_path)

    # Extract plain_txt/ files
    txt_dir = output_dir / "plain_txt"
    if txt_dir.exists() and any(txt_dir.iterdir()):
        logger.info("[martijn_trilogy] Already extracted — skipping")
        _log(output_dir, _ZENODO_URL)
        return

    txt_dir.mkdir(exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            # path inside zip: SofieMoors-martijnmanuscripts-*/data/plain_txt/*.txt
            if "/data/plain_txt/" in member and member.endswith(".txt"):
                fname = Path(member).name
                dest  = txt_dir / fname
                if not dest.exists():
                    dest.write_bytes(zf.read(member))
                    extracted += 1

    logger.info("[martijn_trilogy] Extracted %d plain-text files to %s", extracted, txt_dir)
    _log(output_dir, _ZENODO_URL)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Martijn Trilogy manuscripts (Zenodo)")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
