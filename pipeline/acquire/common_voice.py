"""
Download Dutch Common Voice sentence transcripts.

Mozilla Common Voice is an open-source voice dataset. The sentence
transcripts used as recording prompts are licensed CC0 (public domain).

Supported locales:
  nl    — Dutch (Netherlands): ~939 validated hours, 208k+ clips (v17)
  nl-BE — Flemish Dutch (Belgium): ~57 validated hours, 9k+ clips (v17)

HOW TO DOWNLOAD — Mozilla Data Collective API (recommended)
------------------------------------------------------------
1. Log in at https://commonvoice.mozilla.org and get your API key
2. Set CV_API_KEY in your environment (or pass --cv-api-key)
3. Run:
   CV_API_KEY=your_key python -m pipeline acquire --sources common_voice --cv-locale both

The acquirer calls the Data Collective API to get a signed download URL,
streams the tarball, and extracts only the sentence TSV — no audio stored.

ALTERNATIVE: Signed Mozilla S3 URL (browser copy)
--------------------------------------------------
1. Go to https://commonvoice.mozilla.org/en/datasets
2. Select Dutch, accept terms, click Download
3. Copy the URL from your browser address bar
4. Run: python -m pipeline acquire --sources common_voice \
       --cv-download-url "https://mozilla-common-voice-datasets..." --cv-locale nl

ALTERNATIVE: HuggingFace token
--------------------------------
  HF_TOKEN=hf_... python -m pipeline acquire --sources common_voice
  Requires: pip install datasets huggingface_hub
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import logging
import os
import tarfile
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "common_voice"
LICENSE = "CC0"
DOMAIN  = "spoken"

DEFAULT_HF_DATASET = "mozilla-foundation/common_voice_17_0"
_SHARD_SIZE = 10_000

# Mozilla Data Collective API
_CV_API_BASE = "https://datacollective.mozillafoundation.org/api"

# Dataset IDs per locale (from datacollective.mozillafoundation.org)
_CV_DATASET_IDS: dict[str, str] = {
    "nl":    "cmj8u3pjj00j5nxxbnk8ajqgc",
    "nl-BE": "cmj8u3pjj00j5nxxbnk8ajqgc",  # update if Mozilla uses a separate ID for nl-BE
}


def _log(output_dir: Path, locale: str, source_desc: str) -> None:
    entry = {
        "source": SOURCE,
        "locale": locale,
        "source_desc": source_desc,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Mozilla Data Collective API — get a signed download URL
# ---------------------------------------------------------------------------

def _get_signed_url_via_api(locale: str, api_key: str) -> str | None:
    """
    Call the Mozilla Data Collective API to obtain a signed S3 download URL.

    POST /api/datasets/{dataset_id}/download
    Authorization: Bearer <api_key>
    Returns JSON: {"downloadUrl": "https://..."}
    """
    dataset_id = _CV_DATASET_IDS.get(locale)
    if not dataset_id:
        logger.error("[common_voice] No dataset ID known for locale '%s'", locale)
        return None

    url = f"{_CV_API_BASE}/datasets/{dataset_id}/download"
    payload = json.dumps({}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "NederCorpus/0.1 (academic research; Mozilla Common Voice CC0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            signed_url = data.get("downloadUrl")
            if not signed_url:
                logger.error("[common_voice] API response missing 'downloadUrl': %s", data)
                return None
            logger.info("[common_voice] Got signed URL from Data Collective API for locale %s", locale)
            return signed_url
    except Exception as exc:
        logger.error("[common_voice] Data Collective API call failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Download from a signed Mozilla S3 URL
# ---------------------------------------------------------------------------

def _stream_download(url: str, dest: Path, timeout: int = 600) -> None:
    """Stream-download url to dest file."""
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; Mozilla Common Voice CC0)"}
    req = urllib.request.Request(url, headers=headers)
    logger.info("[common_voice] Downloading from URL …")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(1 << 20)  # 1 MB
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    logger.info("  %.1f%%  (%d / %d MB)", pct, downloaded >> 20, total >> 20)
    logger.info("[common_voice] Download complete: %s", dest)


def _extract_from_tarball(tarball: Path, locale: str) -> list[str]:
    """Extract validated sentences from a Common Voice tarball (any version)."""
    sentences: list[str] = []

    # Common path patterns across CV versions
    tsv_candidates = [
        f"{locale}/validated.tsv",
        f"cv-corpus-17.0-2024-03-15/{locale}/validated.tsv",
        f"cv-corpus-23.0-2025-09-05/{locale}/validated.tsv",
        "validated.tsv",
    ]

    logger.info("[common_voice] Opening tarball: %s", tarball)
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            members = {m.name: m for m in tf.getmembers()}

            tsv_member = None
            for candidate in tsv_candidates:
                if candidate in members:
                    tsv_member = members[candidate]
                    break
            if tsv_member is None:
                # Fallback: find any validated.tsv
                for name, member in members.items():
                    if name.endswith("validated.tsv"):
                        tsv_member = member
                        break

            if tsv_member is None:
                logger.error("[common_voice] No validated.tsv found in %s", tarball)
                return []

            logger.info("[common_voice] Reading %s", tsv_member.name)
            f = tf.extractfile(tsv_member)
            if f is None:
                return []

            text = f.read().decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            seen: set[str] = set()
            for row in reader:
                s = (row.get("sentence") or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    sentences.append(s)

    except Exception as exc:
        logger.error("[common_voice] Tarball extraction failed: %s", exc)
        return []

    logger.info("[common_voice] Extracted %d unique sentences", len(sentences))
    return sentences


# ---------------------------------------------------------------------------
# HuggingFace streaming download (sentences only, no audio)
# ---------------------------------------------------------------------------

def _download_via_hf(
    locale: str,
    hf_token: str,
    hf_dataset: str,
) -> list[str]:
    """Download sentences via HuggingFace datasets (streaming, no audio)."""
    try:
        from datasets import load_dataset
    except ImportError:
        logger.error(
            "datasets library not installed. Run: pip install datasets huggingface_hub"
        )
        return []

    logger.info("[common_voice] Streaming from HuggingFace: %s locale=%s", hf_dataset, locale)
    sentences: list[str] = []
    try:
        ds = load_dataset(
            hf_dataset,
            name=locale,
            split="validation+train",
            streaming=True,
            token=hf_token,
            trust_remote_code=False,
        )
        seen: set[str] = set()
        for row in ds:
            s = (row.get("sentence") or "").strip()
            if s and s not in seen:
                seen.add(s)
                sentences.append(s)
    except Exception as exc:
        logger.error("[common_voice] HuggingFace download failed: %s", exc)
        return []

    logger.info("[common_voice] Retrieved %d unique sentences for %s", len(sentences), locale)
    return sentences


# ---------------------------------------------------------------------------
# Save sentences to sharded TSVs
# ---------------------------------------------------------------------------

def _save_sentences(locale_dir: Path, locale: str, sentences: list[str]) -> None:
    shard_num = 0
    for i in range(0, len(sentences), _SHARD_SIZE):
        shard = sentences[i: i + _SHARD_SIZE]
        shard_path = locale_dir / f"sentences_{shard_num:04d}.tsv"
        with shard_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["sentence", "locale"])
            for s in shard:
                writer.writerow([s, locale])
        shard_num += 1

    logger.info(
        "[common_voice] Saved %d sentences in %d shard(s) for locale %s",
        len(sentences), shard_num, locale,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    locale: str = "nl",
    cv_api_key: str | None = None,
    download_url: str | None = None,
    hf_token: str | None = None,
    hf_dataset: str = DEFAULT_HF_DATASET,
    tarball: Path | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download/extract Common Voice sentence transcripts for a Dutch locale.

    Priority:
      1. Mozilla Data Collective API key (CV_API_KEY env var or --cv-api-key)
      2. Signed Mozilla S3 URL (--cv-download-url) — copy from browser after login
      3. HuggingFace token (HF_TOKEN env var or --hf-token)
      4. Pre-downloaded tarball (--tarball)
      5. Error with instructions

    Args:
        output_dir:    Root output directory.
        locale:        'nl' or 'nl-BE'.
        cv_api_key:    Mozilla Data Collective API key (or set CV_API_KEY env var).
        download_url:  Signed S3 URL copied from Mozilla's download page.
        hf_token:      HuggingFace read token (or set HF_TOKEN env var).
        hf_dataset:    HuggingFace dataset ID.
        tarball:       Path to a pre-downloaded .tar.gz.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[common_voice] [dry-run] skipping")
        return

    locale_dir = output_dir / locale.replace("-", "_")
    locale_dir.mkdir(exist_ok=True)

    if list(locale_dir.glob("sentences_*.tsv")):
        logger.info("[common_voice] Already extracted for locale %s — skipping", locale)
        return

    sentences: list[str] = []
    source_desc = ""

    # ---- Method 1: Mozilla Data Collective API key ----
    api_key = cv_api_key or os.environ.get("CV_API_KEY")
    if api_key:
        signed_url = _get_signed_url_via_api(locale, api_key)
        if signed_url:
            archive_path = output_dir / f"{locale.replace('-', '_')}.tar.gz"
            if not archive_path.exists():
                try:
                    _stream_download(signed_url, archive_path)
                except Exception as exc:
                    logger.error("[common_voice] Download failed: %s", exc)
                    archive_path.unlink(missing_ok=True)
                    return
            sentences = _extract_from_tarball(archive_path, locale)
            source_desc = f"mozilla_api:{locale}"

    # ---- Method 2: Signed Mozilla URL (browser copy) ----
    if not sentences and download_url:
        archive_path = output_dir / f"{locale.replace('-', '_')}.tar.gz"
        if not archive_path.exists():
            try:
                _stream_download(download_url, archive_path)
            except Exception as exc:
                logger.error("[common_voice] Download from URL failed: %s", exc)
                archive_path.unlink(missing_ok=True)
                return
        sentences = _extract_from_tarball(archive_path, locale)
        source_desc = f"mozilla_signed_url:{locale}"

    # ---- Method 3: HuggingFace token ----
    if not sentences:
        token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if token:
            sentences = _download_via_hf(locale, token, hf_dataset)
            source_desc = f"huggingface:{hf_dataset}:{locale}"

    # ---- Method 4: Pre-downloaded tarball ----
    if not sentences and tarball and tarball.exists():
        sentences = _extract_from_tarball(tarball, locale)
        source_desc = f"tarball:{tarball.name}:{locale}"

    # ---- No method available ----
    if not sentences and not source_desc:
        logger.error(
            "[common_voice] No download method found for locale '%s'.\n\n"
            "  Option 1 — Mozilla Data Collective API key (recommended):\n"
            "    Set CV_API_KEY=your_key in your environment, then:\n"
            "    python -m pipeline acquire --sources common_voice --cv-locale both\n\n"
            "  Option 2 — Signed browser URL:\n"
            "    1. Go to https://commonvoice.mozilla.org/en/datasets\n"
            "    2. Select Dutch (%s), accept terms, click Download\n"
            "    3. Copy URL from browser, then:\n"
            "    python -m pipeline acquire --sources common_voice \\\n"
            "            --cv-download-url \"<paste-url>\" --cv-locale %s\n\n"
            "  Option 3 — HuggingFace token:\n"
            "    Set HF_TOKEN=hf_... in your environment\n",
            locale, locale, locale,
        )
        return

    if not sentences:
        return

    _save_sentences(locale_dir, locale, sentences)
    _log(output_dir, locale, source_desc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Common Voice Dutch sentence transcripts (CC0)"
    )
    parser.add_argument("--output",       required=True, type=Path)
    parser.add_argument("--locale",       default="nl", choices=["nl", "nl-BE"])
    parser.add_argument("--cv-api-key",   default=None,
                        help="Mozilla Data Collective API key (or set CV_API_KEY env var)")
    parser.add_argument("--download-url", default=None,
                        help="Signed Mozilla S3 URL (copy from browser after clicking Download)")
    parser.add_argument("--hf-token",     default=None,
                        help="HuggingFace read token (alternative to Mozilla URL)")
    parser.add_argument("--hf-dataset",   default=DEFAULT_HF_DATASET)
    parser.add_argument("--tarball",      default=None, type=Path,
                        help="Path to pre-downloaded .tar.gz")
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        locale=args.locale,
        cv_api_key=args.cv_api_key,
        download_url=args.download_url,
        hf_token=args.hf_token,
        hf_dataset=args.hf_dataset,
        tarball=args.tarball,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
