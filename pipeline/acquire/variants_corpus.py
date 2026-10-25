"""
Download the VariaNTS spoken Dutch corpus from Zenodo.

A spoken Dutch corpus containing talker and linguistic variability.
Includes audio recordings and orthographic transcriptions of read speech
stimuli (sentences and words) from multiple speakers.

For NederCorpus we download only the transcription files — audio is skipped.
The transcriptions are in plain text or CSV/TSV format alongside the audio.

License: CC BY 4.0
  https://zenodo.org/records/3932039

Size: ~1.3 GB (full); transcriptions alone are small
Domain: spoken / read speech
Language: NL (Dutch)
Country: NL
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse, quote

logger = logging.getLogger(__name__)

SOURCE  = "variants_corpus"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "speech"

_ZENODO_RECORD = "3932039"
_ZENODO_API    = f"https://zenodo.org/api/records/{_ZENODO_RECORD}"

# Audio extensions to skip — we only want transcription text
_AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".aiff", ".m4a"}
# Large binary non-text extensions to skip
_SKIP_EXT  = _AUDIO_EXT | {".png", ".jpg", ".jpeg", ".pdf", ".DS_Store"}


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
        headers={
            "User-Agent": "NederCorpus/0.1 (academic research; spoken Dutch NLP; Zenodo)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_file(url: str, dest: Path, timeout: int = 600) -> None:
    parts    = urlparse(url)
    safe_url = urlunparse(parts._replace(path=quote(parts.path, safe="/:@!$&'()*+,;=")))
    req = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "NederCorpus/0.1 (academic research; public data)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with dest.open("wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)


def _extract_text_from_zip(zip_path: Path, out_dir: Path) -> int:
    """Extract only transcription / metadata files from ZIP, skip audio.

    Returns number of files written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.startswith("__MACOSX") or member.endswith("/"):
                continue
            ext = Path(member).suffix.lower()
            if ext in _SKIP_EXT:
                continue
            # Keep text-like files: .txt, .csv, .tsv, .TextGrid, .cha, .eaf, .xml, .json
            dest_name = member.replace("/", "_").replace("\\", "_")
            dest = out_dir / dest_name
            if dest.exists():
                continue
            dest.write_bytes(zf.read(member))
            written += 1
    return written


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download VariaNTS corpus transcription files from Zenodo."""
    output_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = output_dir / "transcriptions"

    logger.info("[variants_corpus] Fetching Zenodo record %s …", _ZENODO_RECORD)
    try:
        record = _fetch_json(_ZENODO_API)
    except Exception as exc:
        logger.error("[variants_corpus] Failed to fetch Zenodo API: %s", exc)
        return

    files = record.get("files", [])
    logger.info("[variants_corpus] Found %d file(s) in Zenodo record", len(files))

    for file_entry in files:
        key = file_entry.get("key", "")
        ext = Path(key).suffix.lower()
        url = file_entry.get("links", {}).get("self", "")
        if not url:
            continue

        if ext in _SKIP_EXT and ext != ".zip":
            logger.debug("[variants_corpus] Skipping non-text file: %s", key)
            continue

        if ext == ".zip":
            # Download ZIP, extract only text files, then delete ZIP
            zip_name = Path(key).name
            zip_path = output_dir / zip_name
            marker   = output_dir / f".extracted_{zip_name}"

            if marker.exists():
                logger.info("[variants_corpus] Already extracted: %s", zip_name)
                continue

            if dry_run:
                logger.info("[variants_corpus] DRY RUN — would download %s", key)
                continue

            if not zip_path.exists():
                logger.info("[variants_corpus] Downloading %s …", key)
                try:
                    _download_file(url, zip_path)
                except Exception as exc:
                    logger.warning("[variants_corpus] Failed: %s — %s", key, exc)
                    continue

            logger.info("[variants_corpus] Extracting text files from %s …", zip_name)
            n = _extract_text_from_zip(zip_path, texts_dir)
            logger.info("[variants_corpus] Extracted %d text file(s) from %s", n, zip_name)
            zip_path.unlink(missing_ok=True)
            marker.touch()
        else:
            # Direct text file — download as-is
            dest = texts_dir / Path(key).name
            if dest.exists():
                logger.info("[variants_corpus] Already have: %s", key)
                continue
            if dry_run:
                logger.info("[variants_corpus] DRY RUN — would download %s", key)
                continue
            texts_dir.mkdir(parents=True, exist_ok=True)
            logger.info("[variants_corpus] Downloading %s …", key)
            try:
                _download_file(url, dest)
            except Exception as exc:
                logger.warning("[variants_corpus] Failed: %s — %s", key, exc)
                continue

    _log(output_dir, f"https://zenodo.org/records/{_ZENODO_RECORD}")
    logger.info("[variants_corpus] Done.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download VariaNTS spoken Dutch corpus (CC BY 4.0, Zenodo)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
