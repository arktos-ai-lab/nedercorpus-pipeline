"""
Download Dutch VoxPopuli speech transcripts from HuggingFace.

VoxPopuli is a large-scale multilingual speech corpus from European Parliament
recordings. The Dutch (nl) subset contains ~53 hours of transcribed audio and
~400 hours of unlabelled data from European Parliament plenary sessions 2009–2020.

Only the normalized transcripts are downloaded — no audio files are stored.
The transcript register is formal parliamentary Dutch (European Parliament).

Also extracts West Frisian (fy) labelled data (~16K tokens) from the same dataset.

License: CC0 (speech data; codebase Apache-2.0 but irrelevant for data use)
  https://huggingface.co/datasets/facebook/voxpopuli

Source: https://github.com/facebookresearch/voxpopuli
Size (NL labelled): ~53h audio, ~488K tokens of transcript text
Size (FY labelled): ~16K tokens

Requirements:
  pip install datasets huggingface_hub

Usage:
  python -m pipeline acquire --sources voxpopuli
  HF_TOKEN=hf_... python -m pipeline acquire --sources voxpopuli
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "voxpopuli"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "speech"

_HF_DATASET = "facebook/voxpopuli"
_SHARD_SIZE = 10_000

# Locales to download: (locale_code, hf_config, language_variant, country)
# NOTE: VoxPopuli only provides a "nl" config; "fy-NL" is not available in the dataset.
# The fy locale is skipped — no matching HuggingFace config exists for VoxPopuli.
LOCALES: list[tuple[str, str, str, str]] = [
    ("nl", "nl", "NL", "NL"),   # Dutch (Netherlands) — European Parliament
    # ("fy", "fy-NL", "FY", "NL"),  # West Frisian — not available in VoxPopuli
]


def _log(output_dir: Path, locale: str, total: int) -> None:
    entry = {
        "source": SOURCE,
        "dataset": _HF_DATASET,
        "locale": locale,
        "license": LICENSE,
        "total_utterances": total,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download_locale(
    output_dir: Path,
    locale: str,
    hf_config: str,
    hf_token: str | None = None,
) -> int:
    """
    Download VoxPopuli labelled transcripts for one locale.

    Saves as {locale}_sentences_{shard:04d}.tsv with columns:
      utterance_id, normalized_text, raw_text, year, speaker_id

    Returns total utterances saved.
    """
    locale_dir = output_dir / locale
    locale_dir.mkdir(parents=True, exist_ok=True)

    # Skip if already downloaded
    existing = list(locale_dir.glob(f"{locale}_sentences_*.tsv"))
    if existing:
        logger.info("[voxpopuli:%s] Already downloaded (%d shards)", locale, len(existing))
        return 0

    try:
        from datasets import load_dataset
    except ImportError:
        logger.error(
            "[voxpopuli] datasets library not installed. Run: pip install datasets huggingface_hub"
        )
        return 0

    token = hf_token or os.environ.get("HF_TOKEN")

    utterances: list[dict] = []
    seen_ids: set[str] = set()

    for split in ("train", "validation", "test"):
        logger.info("[voxpopuli:%s] Streaming split: %s ...", locale, split)
        try:
            ds = load_dataset(
                _HF_DATASET,
                hf_config,
                split=split,
                streaming=True,
                token=token,
            )
            # Cast audio column to non-decoded bytes so torchcodec is not required
            try:
                from datasets import Audio as _Audio
                ds = ds.cast_column("audio", _Audio(decode=False))
            except Exception:
                pass
            for row in ds:
                uid = row.get("audio_id") or row.get("id") or ""
                # Prefer normalized text; fall back to raw
                norm = (row.get("normalized_text") or "").strip()
                raw  = (row.get("raw_text") or "").strip()
                text = norm or raw
                if not text or uid in seen_ids:
                    continue
                seen_ids.add(uid)
                utterances.append({
                    "utterance_id": uid,
                    "normalized_text": norm,
                    "raw_text": raw,
                    "year": row.get("year") or "",
                    "speaker_id": row.get("speaker_id") or "",
                })
        except Exception as exc:
            exc_str = str(exc)
            if "torchcodec" in exc_str or "ModuleNotFoundError" in exc_str:
                logger.warning(
                    "[voxpopuli:%s] Split '%s' failed due to missing audio dependency "
                    "(torchcodec). Text-only extraction not possible for this split. "
                    "Install torchcodec or use a text-only dataset mirror. Skipping.",
                    locale, split,
                )
            elif "404" in exc_str or "doesn't exist" in exc_str.lower() or "not found" in exc_str.lower():
                logger.warning(
                    "[voxpopuli:%s] Config '%s' split '%s' not found in dataset. "
                    "This locale may not be available. Skipping.",
                    locale, hf_config, split,
                )
            else:
                logger.warning("[voxpopuli:%s] Split '%s' failed: %s", locale, split, exc)
            continue

    if not utterances:
        logger.warning("[voxpopuli:%s] No utterances retrieved", locale)
        return 0

    shard_num = 0
    for i in range(0, len(utterances), _SHARD_SIZE):
        shard = utterances[i: i + _SHARD_SIZE]
        path = locale_dir / f"{locale}_sentences_{shard_num:04d}.tsv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["utterance_id", "normalized_text", "raw_text", "year", "speaker_id"], delimiter="\t")
            writer.writeheader()
            writer.writerows(shard)
        shard_num += 1

    _log(output_dir, locale, len(utterances))
    logger.info("[voxpopuli:%s] Saved %d utterances in %d shard(s)", locale, len(utterances), shard_num)
    return len(utterances)


def download(
    output_dir: Path,
    locales: list[str] | None = None,
    hf_token: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download VoxPopuli Dutch + Frisian transcripts.

    Creates per-locale subdirectories: output_dir/nl/, output_dir/fy/

    Args:
        locales:  List of locale codes to download (default: ['nl', 'fy']).
        hf_token: HuggingFace API token (or set HF_TOKEN env var).
        dry_run:  Log only, do not download.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    target_locales = set(locales) if locales else None

    if dry_run:
        logger.info("[voxpopuli] [dry-run] would download: %s", [l[0] for l in LOCALES])
        return

    total = 0
    for locale, hf_config, lang_variant, country in LOCALES:
        if target_locales and locale not in target_locales:
            continue
        n = download_locale(output_dir, locale, hf_config, hf_token=hf_token)
        total += n

    logger.info("[voxpopuli] Done. Total utterances: %d", total)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download VoxPopuli Dutch/Frisian speech transcripts (CC0)"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--locales",  nargs="+", default=None,
                        help="Locales to download (default: nl fy)")
    parser.add_argument("--hf-token", default=None,
                        help="HuggingFace API token (or set HF_TOKEN env var)")
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, locales=args.locales, hf_token=args.hf_token, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
