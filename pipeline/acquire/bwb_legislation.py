"""
Download Dutch consolidated legislation (BWB) from HuggingFace.

Dataset: vGassen/Dutch-Basisbestandwetten-Legislation-Laws-XML-Clean
License: PDDL (Public Domain Dedication and License) — effectively PD
Content: Full XML text of the Basis Wetten Bestand (consolidated Dutch laws)
Country: NL, Domain: legal

This complements wetten.py (Staatsblad publications) by providing the
CURRENT consolidated text of laws, not just the individual gazette records.

~1.4 GB across 10 Parquet files.
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "bwb_legislation"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "legal"

_HF_BASE = (
    "https://huggingface.co/datasets/vGassen/"
    "Dutch-Basisbestandwetten-Legislation-Laws-XML-Clean/"
    "resolve/refs%2Fconvert%2Fparquet/default/partial-train/{index:04d}.parquet"
)
_N_FILES = 10
_UA = "NederCorpus/0.1 (academic research; BWB legislation PD)"


def _log(output_dir: Path, url: str, n: int) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "n_files":          n,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False, **kwargs) -> None:
    """Download BWB legislation Parquet files from HuggingFace."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[bwb_legislation] [dry-run] would download %d Parquet files", _N_FILES)
        return

    downloaded = 0
    for i in range(_N_FILES):
        dest = output_dir / f"bwb_{i:04d}.parquet"
        if dest.exists():
            logger.info("[bwb_legislation] %s already exists — skipping", dest.name)
            downloaded += 1
            continue

        url = _HF_BASE.format(index=i)
        logger.info("[bwb_legislation] Downloading %s …", dest.name)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
            dest.write_bytes(data)
            _log(output_dir, url, i)
            logger.info("[bwb_legislation] %s: %d bytes", dest.name, len(data))
            downloaded += 1
        except Exception as exc:
            logger.error("[bwb_legislation] Failed %s: %s", dest.name, exc)

    logger.info("[bwb_legislation] Done — %d/%d files", downloaded, _N_FILES)
