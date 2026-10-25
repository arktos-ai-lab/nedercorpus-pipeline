"""
Download Afrikaans government cabinet statements from HuggingFace.

The Gov-ZA Cabinet Statements dataset (DSFSI, University of Pretoria) contains
sentence-aligned South African government cabinet statements covering 11 official
SA languages. We download only the Afrikaans (afr) side from the afr-eng config.

Each parquet row contains a list of Afrikaans sentences for one cabinet statement.

Dataset: https://huggingface.co/datasets/dsfsi/govza-sa-cabinet-statements-sentence-aligned
License: CC BY 4.0
Domain: government
Language: AF (Afrikaans)
Country: ZA
"""
from __future__ import annotations

import datetime
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "govza_cabinet_af"
LICENSE = "CC-BY"
COUNTRY = "ZA"
DOMAIN  = "government"
LANG    = "AF"

_HF_PARQUET = (
    "https://huggingface.co/api/datasets/"
    "dsfsi/govza-sa-cabinet-statements-sentence-aligned"
    "/parquet/afr-eng/{split}/0.parquet"
)
_SPLITS = ["train", "test", "validation"]
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
                logger.warning("[govza_cabinet_af] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[govza_cabinet_af] Failed %s: %s", url, exc)
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


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download Afrikaans cabinet statement parquet files from HuggingFace."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[govza_cabinet_af] [dry-run] skipping")
        return

    total = 0
    for split in _SPLITS:
        fname = f"govza_cabinet_afr_{split}.parquet"
        dest  = output_dir / fname
        if dest.exists():
            logger.info("[govza_cabinet_af] Already have %s — skipping", fname)
            continue

        url = _HF_PARQUET.format(split=split)
        logger.info("[govza_cabinet_af] Downloading %s split", split)
        data = _fetch(url, timeout=120)
        if not data:
            logger.warning("[govza_cabinet_af] Could not download %s split", split)
            continue

        dest.write_bytes(data)
        _log(output_dir, url, split)
        total += 1
        logger.info("[govza_cabinet_af] Saved %s (%d KB)", fname, len(data) // 1024)
        time.sleep(_DELAY)

    logger.info("[govza_cabinet_af] Done. %d split files downloaded.", total)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Download govza_cabinet_af corpus")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
