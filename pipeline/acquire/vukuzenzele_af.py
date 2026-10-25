"""
Download the Vuk'uzenzele Afrikaans corpus from HuggingFace.

Vuk'uzenzele is a South African government magazine (2012–2022), published by
the Government Communication and Information System (GCIS). The DSFSI at the
University of Pretoria processed 84 editions into a multilingual corpus
covering 11 official SA languages. We download only the Afrikaans (afr) split.

Dataset: https://huggingface.co/datasets/dsfsi/vukuzenzele-monolingual
License: CC BY 4.0
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.request
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "vukuzenzele_af"
LICENSE = "CC-BY"
COUNTRY = "ZA"
DOMAIN  = "government"
LANG    = "AF"

_HF_PARQUET = (
    "https://huggingface.co/api/datasets/dsfsi/vukuzenzele-monolingual"
    "/parquet/afr/{split}/0.parquet"
)
_SPLITS = ["train", "test", "eval"]
_USER_AGENT = "NederCorpus/0.1 (academic research; CC-BY 4.0 corpus)"
_DELAY = 1.0

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            if attempt == retries - 1:
                logger.warning("[vukuzenzele_af] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[vukuzenzele_af] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _log(output_dir: Path, url: str, split: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "split":            split,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download Afrikaans Parquet split files from HuggingFace.

    Downloads train/test/eval parquet files for the 'afr' language config
    into output_dir/. Files are named vukuzenzele_afr_{split}.parquet.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[vukuzenzele_af] [dry-run] skipping")
        return

    total = 0
    for split in _SPLITS:
        fname = f"vukuzenzele_afr_{split}.parquet"
        dest  = output_dir / fname
        if dest.exists():
            logger.info("[vukuzenzele_af] Already have %s — skipping", fname)
            continue

        url = _HF_PARQUET.format(split=split)
        logger.info("[vukuzenzele_af] Downloading %s split", split)
        data = _fetch(url, timeout=120)
        if not data:
            logger.warning("[vukuzenzele_af] Could not download %s split", split)
            continue

        dest.write_bytes(data)
        _log(output_dir, url, split)
        total += 1
        logger.info("[vukuzenzele_af] Saved %s (%d KB)", fname, len(data) // 1024)
        time.sleep(_DELAY)

    logger.info("[vukuzenzele_af] Done. %d split files downloaded.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Vuk'uzenzele Afrikaans corpus (CC BY 4.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
