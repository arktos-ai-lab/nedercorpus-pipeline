"""
Download individual Dutch speaker datasets from Mozilla Data Collective.

These are single-speaker read-speech corpora released as CC0.
Only the sentence transcripts are extracted — audio is never stored.

Datasets:
  nathalie          — "Nathalie 1.0"                        (cmiugnlxi01c5nv07yt3kxwhx)
  flemishguy        — "Flemishguy 1.0"                      (cmiupbboq01i4mf071clmd43n)  [Flemish]
  ronnie            — "Ronnie 1.0"                          (cmiujt8r401d2nv07avt6446s)
  pim               — "Pim 1.0"                             (cmiugo0h501exmf073drr56rr)
  cv_frisian_spont  — "CV Spontaneous Speech 2.0 - Frisian" (cmj8u48ai0029nxzp3figruz1)  [Frisian]
  cv_frisian_script — "CV Scripted Speech 24.0 - Frisian"   (cmj8u3p4d008tnxxbxw3bkw70)  [Frisian]

Requires CV_API_KEY in .env (same key as Common Voice).

Usage:
  python -m pipeline acquire --sources mozilla_dc
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import logging
import os
import tarfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "mozilla_dc"
LICENSE = "CC0"
DOMAIN  = "spoken"

_CV_API_BASE = "https://datacollective.mozillafoundation.org/api"

# (dataset_name, dataset_id, locale)
DATASETS: list[tuple[str, str, str]] = [
    ("nathalie",          "cmiugnlxi01c5nv07yt3kxwhx", "nl"),
    ("flemishguy",        "cmiupbboq01i4mf071clmd43n",  "nl-BE"),
    ("ronnie",            "cmiujt8r401d2nv07avt6446s",  "nl"),
    ("pim",               "cmiugo0h501exmf073drr56rr",  "nl"),
    ("cv_frisian_spont",  "cmj8u48ai0029nxzp3figruz1",  "fy-NL"),
    ("cv_frisian_script", "cmj8u3p4d008tnxxbxw3bkw70",  "fy-NL"),
]

_SHARD_SIZE = 10_000

# Column names to look for in transcript TSVs/CSVs (checked in order)
_TEXT_COLUMNS = ["sentence", "text", "transcript", "utterance", "transcription"]


# ---------------------------------------------------------------------------
# API — get signed download URL
# ---------------------------------------------------------------------------

def _get_signed_url(dataset_id: str, api_key: str) -> str | None:
    url = f"{_CV_API_BASE}/datasets/{dataset_id}/download"
    payload = json.dumps({}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "NederCorpus/0.1 (academic research; Mozilla Data Collective CC0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            signed_url = data.get("downloadUrl")
            if not signed_url:
                logger.error("[mozilla_dc] API response missing 'downloadUrl': %s", data)
            return signed_url
    except Exception as exc:
        logger.error("[mozilla_dc] API call failed for dataset %s: %s", dataset_id, exc)
        return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _stream_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NederCorpus/0.1 (academic research; Mozilla Data Collective CC0)"},
    )
    logger.info("[mozilla_dc] Downloading → %s …", dest.name)
    with urllib.request.urlopen(req, timeout=600) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    logger.info("  %.1f%%  (%d / %d MB)", downloaded / total * 100,
                                downloaded >> 20, total >> 20)
    logger.info("[mozilla_dc] Download complete: %s", dest)


# ---------------------------------------------------------------------------
# Extract transcripts from tarball
# ---------------------------------------------------------------------------

def _extract_sentences(tarball: Path, dataset_name: str) -> list[str]:
    """
    Extract unique sentence transcripts from a Mozilla Data Collective tarball.

    Looks for TSV/CSV files with text columns; falls back to plain .txt files.
    """
    sentences: list[str] = []

    logger.info("[mozilla_dc] Extracting transcripts from %s", tarball.name)
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            members = tf.getmembers()

            # Prefer TSV/CSV files with known text columns
            tsv_members = [
                m for m in members
                if m.name.endswith((".tsv", ".csv")) and m.isfile()
            ]
            # Sort: prefer files named 'validated', 'sentences', 'metadata'
            def _tsv_priority(m: tarfile.TarInfo) -> int:
                n = m.name.lower()
                if "validated" in n:  return 0
                if "sentence"  in n:  return 1
                if "metadata"  in n:  return 2
                if "transcript" in n: return 3
                return 9
            tsv_members.sort(key=_tsv_priority)

            seen: set[str] = set()

            for member in tsv_members:
                f = tf.extractfile(member)
                if f is None:
                    continue
                raw = f.read().decode("utf-8", errors="replace")
                delim = "\t" if member.name.endswith(".tsv") else ","
                try:
                    reader = csv.DictReader(io.StringIO(raw), delimiter=delim)
                    col = next(
                        (c for c in (reader.fieldnames or []) if c.lower() in _TEXT_COLUMNS),
                        None,
                    )
                    if col is None:
                        continue
                    logger.info("[mozilla_dc] Reading '%s' column from %s", col, member.name)
                    for row in reader:
                        s = (row.get(col) or "").strip()
                        if s and s not in seen:
                            seen.add(s)
                            sentences.append(s)
                except Exception:
                    continue

            # Fallback: plain .txt files (one sentence per line)
            if not sentences:
                txt_members = [m for m in members if m.name.endswith(".txt") and m.isfile()]
                for member in txt_members:
                    f = tf.extractfile(member)
                    if f is None:
                        continue
                    for line in f.read().decode("utf-8", errors="replace").splitlines():
                        s = line.strip()
                        if s and s not in seen:
                            seen.add(s)
                            sentences.append(s)

    except Exception as exc:
        logger.error("[mozilla_dc] Extraction failed for %s: %s", tarball.name, exc)
        return []

    logger.info("[mozilla_dc] %s: extracted %d unique sentences", dataset_name, len(sentences))
    return sentences


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def _save_sentences(dest_dir: Path, dataset_name: str, locale: str, sentences: list[str]) -> None:
    shard_num = 0
    for i in range(0, len(sentences), _SHARD_SIZE):
        shard = sentences[i: i + _SHARD_SIZE]
        path = dest_dir / f"sentences_{shard_num:04d}.tsv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["sentence", "dataset", "locale"])
            for s in shard:
                writer.writerow([s, dataset_name, locale])
        shard_num += 1
    logger.info("[mozilla_dc] Saved %d sentences (%d shard(s)) for %s",
                len(sentences), shard_num, dataset_name)


def _log(output_dir: Path, dataset_name: str, locale: str) -> None:
    entry = {
        "source": SOURCE,
        "dataset": dataset_name,
        "locale": locale,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    cv_api_key: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download all Mozilla Data Collective Dutch speaker datasets.

    Requires CV_API_KEY in .env or passed via cv_api_key.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[mozilla_dc] [dry-run] skipping")
        return

    api_key = cv_api_key or os.environ.get("CV_API_KEY")
    if not api_key:
        logger.error(
            "[mozilla_dc] CV_API_KEY not set.\n"
            "  Add CV_API_KEY=your_key to your .env file."
        )
        return

    for dataset_name, dataset_id, locale in DATASETS:
        dest_dir = output_dir / dataset_name
        dest_dir.mkdir(exist_ok=True)

        if list(dest_dir.glob("sentences_*.tsv")):
            logger.info("[mozilla_dc] %s already extracted — skipping", dataset_name)
            continue

        logger.info("[mozilla_dc] === %s (locale: %s) ===", dataset_name, locale)

        signed_url = _get_signed_url(dataset_id, api_key)
        if not signed_url:
            logger.error("[mozilla_dc] Could not get download URL for %s — skipping", dataset_name)
            continue

        tarball = output_dir / f"{dataset_name}.tar.gz"
        if not tarball.exists():
            try:
                _stream_download(signed_url, tarball)
            except Exception as exc:
                logger.error("[mozilla_dc] Download failed for %s: %s", dataset_name, exc)
                tarball.unlink(missing_ok=True)
                continue

        sentences = _extract_sentences(tarball, dataset_name)
        if not sentences:
            logger.warning("[mozilla_dc] No sentences extracted from %s", dataset_name)
            continue

        _save_sentences(dest_dir, dataset_name, locale, sentences)
        _log(output_dir, dataset_name, locale)
