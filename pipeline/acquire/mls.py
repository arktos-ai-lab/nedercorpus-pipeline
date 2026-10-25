"""
Download Dutch transcripts from Multilingual LibriSpeech (MLS).

MLS is a large multilingual corpus derived from LibriVox audiobooks.
The Dutch portion contains read-speech recordings from PD Dutch audiobooks.
Only the sentence transcripts are downloaded — no audio files.

License: CC-BY 4.0
Source: https://huggingface.co/datasets/facebook/multilingual_librispeech
Domain: literature / read speech
Size: ~50k sentences (Dutch)

NOTE — HIGH OVERLAP:
  LibriVox records PUBLIC DOMAIN books. The Dutch portion of MLS is therefore
  the same source text as `gutenberg` (Project Gutenberg Dutch books) and
  `dutch_pd` (PleIAs Dutch-PD). The deduplication stage will remove nearly all
  MLS text as near-duplicates of those sources.

  MLS provides value ONLY for ASR model training (clean aligned audio+transcript).
  For a TEXT CORPUS, MLS adds essentially zero unique content beyond what
  gutenberg and dutch_pd already cover.

  Recommendation: skip mls in text corpus builds. Retain the acquirer for
  ASR-specific pipelines that need sentence-aligned audio transcripts.
"""
from __future__ import annotations

import csv
import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "mls"
LICENSE = "CC-BY"
DOMAIN  = "literature"

_SHARD_SIZE = 10_000
_HF_DATASET = "facebook/multilingual_librispeech"


def _log(output_dir: Path, total: int) -> None:
    entry = {
        "source": SOURCE,
        "dataset": _HF_DATASET,
        "license": LICENSE,
        "total_sentences": total,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    hf_token: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch MLS transcripts via HuggingFace datasets (no audio).

    Requires: pip install datasets huggingface_hub
    No HF token needed (dataset is public).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[mls] License: CC-BY 4.0 — Multilingual LibriSpeech (Dutch)")

    if dry_run:
        logger.info("[mls] [dry-run] skipping")
        return

    if list(output_dir.glob("sentences_*.tsv")):
        logger.info("[mls] Already downloaded — skipping")
        return

    try:
        from datasets import load_dataset
    except ImportError:
        logger.error("[mls] datasets library not installed. Run: pip install datasets huggingface_hub")
        return

    token = hf_token or os.environ.get("HF_TOKEN")
    sentences: list[str] = []
    seen: set[str] = set()

    for split in ("train", "test", "validation"):
        logger.info("[mls] Streaming split: %s …", split)
        try:
            ds = load_dataset(
                _HF_DATASET,
                "dutch",
                split=split,
                streaming=True,
                token=token,
                trust_remote_code=False,
            )
            for row in ds:
                s = (row.get("sentence") or row.get("text") or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    sentences.append(s)
        except Exception as exc:
            logger.warning("[mls] Split '%s' failed: %s", split, exc)
            continue

    if not sentences:
        logger.error("[mls] No sentences retrieved.")
        return

    shard_num = 0
    for i in range(0, len(sentences), _SHARD_SIZE):
        shard = sentences[i: i + _SHARD_SIZE]
        path = output_dir / f"sentences_{shard_num:04d}.tsv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["sentence", "locale"])
            for s in shard:
                writer.writerow([s, "nl"])
        shard_num += 1

    _log(output_dir, len(sentences))
    logger.info("[mls] Saved %d sentences in %d shard(s)", len(sentences), shard_num)
