"""
Download SADiLaR Afrikaans Genre Classification Corpus.

A balanced corpus of Afrikaans text covering 6 genre categories:
  F-DRA — Fiction: Drama
  F-POE — Fiction: Poetry
  F-PRO — Fiction: Prose
  NF-NEU — Non-Fiction: Neutral
  NF-OBJ — Non-Fiction: Objective
  NF-SUB — Non-Fiction: Subjective

Source: SADiLaR Repository, Stellenbosch University
URL: https://repo.sadilar.org/handle/20.500.12185/393
License: CC BY 2.5 ZA
Country: ZA, Language: AF
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "af_genre"
LICENSE = "CC-BY"
COUNTRY = "ZA"
DOMAIN  = "literature"

_DOWNLOAD_URL = (
    "https://repo.sadilar.org/bitstreams/"
    "53957978-48e9-478f-bf4b-1cf15ed31ab4/download"
)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

_GENRE_LABELS = {
    "F-DRA":   "fiction_drama",
    "F-POE":   "fiction_poetry",
    "F-PRO":   "fiction_prose",
    "NF-NEU":  "nonfiction_neutral",
    "NF-OBJ":  "nonfiction_objective",
    "NF-SUB":  "nonfiction_subjective",
}


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download and extract Afrikaans Genre Classification Corpus.

    Saves one JSONL per genre category: af_genre_{category}.jsonl
    Each record: {id, genre, filename, text, acquisition_date}
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[af_genre] [dry-run] would download %s", _DOWNLOAD_URL)
        return

    # Check if already done
    existing = list(output_dir.glob("af_genre_*.jsonl"))
    if len(existing) >= 6:
        total = sum(sum(1 for _ in p.open(encoding="utf-8")) for p in existing)
        logger.info("[af_genre] Already done (%d files, %d records)", len(existing), total)
        return

    logger.info("[af_genre] Downloading ZIP from SADiLaR…")
    req = urllib.request.Request(
        _DOWNLOAD_URL,
        headers={"User-Agent": "NederCorpus/0.1 (academic; CC BY 2.5 ZA open data)"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=120) as resp:
                zip_bytes = resp.read()
            break
        except Exception as exc:
            if attempt == 2:
                raise
            logger.warning("[af_genre] Download attempt %d failed: %s", attempt + 1, exc)
            time.sleep(5 * (attempt + 1))

    logger.info("[af_genre] Download complete (%d MB), extracting…",
                len(zip_bytes) // 1024 // 1024)

    acq_date    = datetime.date.today().isoformat()
    genre_files: dict[str, list[dict]] = {g: [] for g in _GENRE_LABELS}
    total_texts = 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in sorted(zf.namelist()):
            if not name.endswith(".txt"):
                continue
            # Path format: af/{GENRE}/{GENRE}{NN}.txt
            parts = name.strip("/").split("/")
            if len(parts) < 2:
                continue
            genre_code = parts[1]
            if genre_code not in _GENRE_LABELS:
                continue

            raw = zf.read(name)
            for enc in ("utf-8", "latin-1", "cp1252"):
                try:
                    text = raw.decode(enc).strip()
                    break
                except UnicodeDecodeError:
                    continue
            else:
                continue

            if len(text) < 30:
                continue

            rec = {
                "id":               name.replace("/", "_").replace(".txt", ""),
                "genre":            _GENRE_LABELS[genre_code],
                "filename":         name,
                "text":             text,
                "acquisition_date": acq_date,
            }
            genre_files[genre_code].append(rec)
            total_texts += 1

    # Write one JSONL per genre
    for genre_code, records in genre_files.items():
        if not records:
            continue
        label    = _GENRE_LABELS[genre_code]
        out_path = output_dir / f"af_genre_{label}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("[af_genre] %s: %d texts", label, len(records))

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source":           SOURCE,
            "url":              _DOWNLOAD_URL,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_texts":      total_texts,
        }, ensure_ascii=False) + "\n")

    logger.info("[af_genre] Done. %d texts across %d genres.", total_texts, len(genre_files))


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download SADiLaR Afrikaans Genre Classification Corpus (CC BY 2.5 ZA)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
