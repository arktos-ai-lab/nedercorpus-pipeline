"""
Download Dutch-family dialect corpora from the Leipzig Corpora Collection.

All Leipzig corpora are licensed CC BY (Creative Commons Attribution).
They are built from web and news sources and provide monolingual sentence
samples for low-resource Dutch dialect variants not otherwise covered.

Coverage (language codes per Leipzig naming):
  vls        — West Flemish (Vlaams)
  zea        — Zeelandic (Zeeuws)
  lim / li   — Limburgish (Limburgs)
  nds_NL-DE  — Dutch Low Saxon (Nedersaksisch; covers Gronings, Twents, etc.)
  afr        — Afrikaans
  fy         — West Frisian (Frysk)
  nl         — Standard Dutch (Netherlands) — supplemental web corpus
  nl_BE      — Belgian Dutch — if available

Download URL pattern:
  https://downloads.wortschatz-leipzig.de/corpora/{filename}

License: CC BY (all Leipzig corpora)
  Reference: https://wortschatz.uni-leipzig.de/en/download/
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import tarfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "leipzig"
LICENSE = "CC-BY"

_BASE = "https://downloads.wortschatz-leipzig.de/corpora"

# (local_name, filename, language_variant, description)
# Filenames verified against Leipzig download index (Feb 2026).
# Sizes: 10K/30K/100K/300K/1M sentences; use smallest reliable size.
CORPORA: list[tuple[str, str, str, str]] = [
    (
        "vls",
        "vls_wikipedia_2021_10K.tar.gz",
        "VLS",
        "West Flemish (Vlaams) — Wikipedia corpus 10K sentences",
    ),
    (
        "zea",
        "zea_wikipedia_2021_10K.tar.gz",
        "ZEA",
        "Zeelandic (Zeeuws) — Wikipedia corpus 10K sentences",
    ),
    (
        "lim",
        "lim_wikipedia_2021_10K.tar.gz",
        "LI",
        "Limburgish (Limburgs) — Wikipedia corpus 10K sentences",
    ),
    (
        "nds_nl",
        "nds_wikipedia_2021_30K.tar.gz",
        "NDS-NL",
        "Dutch Low Saxon (Nedersaksisch) — Wikipedia corpus 30K sentences",
    ),
    (
        "afr",
        "afr_wikipedia_2021_100K.tar.gz",
        "AF",
        "Afrikaans — Wikipedia corpus 100K sentences",
    ),
    (
        "fy",
        "fry_wikipedia_2021_10K.tar.gz",
        "FY",
        "West Frisian (Frysk) — Wikipedia corpus 10K sentences",
    ),
]

# Leipzig tar.gz internal file naming: {lang}-sentences.txt
_SENTENCE_SUFFIXES = ["-sentences.txt", "_sentences.txt"]


def _log(output_dir: Path, corpus: str, url: str) -> None:
    entry = {
        "source":           f"{SOURCE}:{corpus}",
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _extract_sentences(tar_path: Path, dest_dir: Path) -> Path | None:
    """
    Extract the sentences file from a Leipzig tar.gz archive.
    Leipzig archives contain files like: {lang}-sentences.txt
    Returns path to extracted sentences file, or None on failure.
    """
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf.getmembers():
                name = member.name
                if any(name.endswith(s) for s in _SENTENCE_SUFFIXES):
                    # Extract just this file flat into dest_dir
                    member.name = Path(member.name).name  # strip subdirs
                    tf.extract(member, dest_dir)
                    return dest_dir / member.name
            # Fallback: extract first .txt file found
            for member in tf.getmembers():
                if member.name.endswith(".txt"):
                    member.name = Path(member.name).name
                    tf.extract(member, dest_dir)
                    return dest_dir / member.name
    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", tar_path.name, exc)
    return None


def download_corpus(
    local_name: str,
    filename: str,
    language_variant: str,
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """Download and extract one Leipzig corpus."""
    corpus_dir = output_dir / local_name
    corpus_dir.mkdir(parents=True, exist_ok=True)

    sentences_file = corpus_dir / "sentences.txt"
    if sentences_file.exists():
        logger.info("[leipzig:%s] Already downloaded — skipping", local_name)
        return

    url = f"{_BASE}/{filename}"
    logger.info("[leipzig:%s] Downloading %s (%s) — %s", local_name, local_name, language_variant, url)

    if dry_run:
        logger.info("[dry-run] skipping")
        return

    tar_dest = corpus_dir / filename
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; Leipzig Corpora)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=300) as resp:
            tar_dest.write_bytes(resp.read())
    except Exception as exc:
        logger.warning("[leipzig:%s] Download failed (%s): %s", local_name, url, exc)
        tar_dest.unlink(missing_ok=True)
        return

    # Extract sentences
    extracted = _extract_sentences(tar_dest, corpus_dir)
    if extracted and extracted != sentences_file:
        extracted.rename(sentences_file)
    elif not extracted:
        logger.warning("[leipzig:%s] No sentences file found in archive", local_name)

    # Clean up archive to save space
    tar_dest.unlink(missing_ok=True)

    if sentences_file.exists():
        line_count = sum(1 for _ in sentences_file.open(encoding="utf-8", errors="replace"))
        logger.info("[leipzig:%s] %d sentences extracted", local_name, line_count)
        _log(output_dir, local_name, url)
    else:
        logger.warning("[leipzig:%s] sentences.txt not created", local_name)


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download all Leipzig Dutch-family dialect corpora."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for local_name, filename, language_variant, _desc in CORPORA:
        download_corpus(local_name, filename, language_variant, output_dir, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Leipzig CC-BY dialect corpora for Dutch language variants"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--corpora",  nargs="+", default=None,
                        choices=[c for c, _, _, _ in CORPORA],
                        help="Specific corpora to download (default: all)")
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.corpora) if args.corpora else None

    for local_name, filename, language_variant, desc in CORPORA:
        if selected and local_name not in selected:
            continue
        download_corpus(local_name, filename, language_variant, output_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
