"""
Download C5 corpus — CommonCrawl pages with explicit Creative Commons licenses,
extracted by Bram Vanroy (GPL-3.0 corpus structure; CC-licensed documents).

C5 is NederCorpus's ONLY web-crawl source because it is the sole CommonCrawl-derived
corpus where each document carries a verified CC license extracted directly from the
source page's HTML metadata. Every other CommonCrawl corpus (FineWeb-2, HPLT, OSCAR,
mC4) applies a license to the dataset curation work, NOT the underlying web content.

Three language configs downloaded:
  nl  → Dutch (NL)
  af  → Afrikaans (AF, ZA)
  fy  → West Frisian (FY, NL)

License of documents: per-page C5 `license_abbr` (by, by-sa, zero, mark admitted)
  NC, ND, cc-unknown and certification pages are always dropped (never defaulted).
  (fixed 2026-10-10: earlier builds read a non-existent `license` column).

HuggingFace: BramVanroy/CommonCrawl-CreativeCommons
Corpus structure license: GPL-3.0

Requires: pip install datasets huggingface_hub

Usage:
  python -m pipeline acquire --sources c5_dutch
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE    = "c5_dutch"
DOMAIN    = "web"
HF_DATASET = "BramVanroy/CommonCrawl-CreativeCommons"

# Language configs to download: config_name → (language_variant, country)
_CONFIGS = {
    "nld": ("NL", "NL"),
    "afr": ("AF", "ZA"),
    "fry": ("FY", "NL"),
}

# C5 stores the per-page license in `license_abbr` (dataset card, "Data Fields"):
# "cc-unknown", "by", "by-sa", "by-nd", "by-nc", "by-nc-sa", "by-nc-nd", "zero",
# "certification", "mark". There is no `license` column. Map the admissible values
# to the NederCorpus license vocabulary; everything else (NC, ND, cc-unknown,
# certification, missing) is dropped rather than defaulted.
_C5_ABBR_TO_LICENSE = {
    "by":    "CC-BY",
    "by-sa": "CC-BY-SA",
    "zero":  "CC0",
    "mark":  "PD",      # Creative Commons Public Domain Mark
}


def license_from_c5_abbr(abbr: str | None) -> str | None:
    """Return the NederCorpus license for a C5 `license_abbr`, or None to drop the page."""
    if not abbr:
        return None
    return _C5_ABBR_TO_LICENSE.get(abbr.strip().lower())


def _log(output_dir: Path) -> None:
    entry = {
        "source":           SOURCE,
        "url":              f"https://huggingface.co/datasets/{HF_DATASET}",
        "license":          "mixed-CC",
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _download_config(
    config: str,
    output_dir: Path,
    split: str,
    max_docs: int,
    shard_size: int,
    exclude_nc: bool,
    hf_token: str | None,
) -> int:
    """Download one language config and save shards as c5_{config}_shard_{N:06d}.jsonl."""
    from datasets import load_dataset

    existing = len(list(output_dir.glob(f"c5_{config}_shard_*.jsonl")))
    skip_docs = existing * shard_size
    shard_idx = existing

    logger.info("[c5/%s] Streaming (max %d docs, skipping %d)...", config, max_docs, skip_docs)
    ds = load_dataset(HF_DATASET, config, split=split, streaming=True, token=hf_token)

    count = 0
    skipped = 0
    excluded_nc = 0
    current_shard: list[str] = []

    def flush_shard() -> None:
        nonlocal shard_idx
        path = output_dir / f"c5_{config}_shard_{shard_idx:06d}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for line in current_shard:
                f.write(line + "\n")
        logger.info("[c5/%s] shard %06d written (%d docs)", config, shard_idx, len(current_shard))
        current_shard.clear()
        shard_idx += 1

    for example in ds:
        text = (example.get("text") or "").strip()
        if not text:
            continue
        abbr = example.get("license_abbr")
        license_ = license_from_c5_abbr(abbr)
        if license_ is None:
            # NC/ND/unknown pages are never admitted. `exclude_nc=False` is kept for
            # API compatibility only; inadmissible pages are still dropped.
            excluded_nc += 1
            continue
        # Resume: skip the pages already saved in earlier shards (counted after filtering).
        if skipped < skip_docs:
            skipped += 1
            continue
        record = {
            "text":    text,
            "url":     example.get("url") or (example.get("meta") or {}).get("url", ""),
            "license": license_,
            "license_abbr": abbr,
            "license_disagreement": bool(example.get("license_disagreement")),
            "date":    example.get("date", ""),
            "lang":    config,
        }
        current_shard.append(json.dumps(record, ensure_ascii=False))
        count += 1
        if len(current_shard) >= shard_size:
            flush_shard()
        if count >= max_docs:
            break

    if current_shard:
        flush_shard()

    logger.info("[c5/%s] Done. %d docs saved (%d NC excluded).", config, count, excluded_nc)
    return count


def download(
    output_dir: Path,
    split: str = "train",
    max_docs: int = 500_000,
    shard_size: int = 50_000,
    exclude_nc: bool = True,
    dry_run: bool = False,
    hf_token: str | None = None,
) -> None:
    """
    Stream C5 Dutch/Afrikaans/Frisian and write shards of JSONL files.

    Downloads three language configs (nl, af, fy) from BramVanroy/CommonCrawl-CreativeCommons.
    Each document carries its original CC license (NC variants excluded by default).
    Shards saved as c5_{lang}_shard_{N:06d}.jsonl.

    C5 is NederCorpus's only web-crawl source: the only CommonCrawl corpus where
    each document's CC license is verified from the source page's HTML metadata.

    HuggingFace token: --hf-token flag, or HF_TOKEN / HUGGING_FACE_HUB_TOKEN env var.
    """
    import os
    try:
        from datasets import load_dataset  # noqa: F401
    except ImportError:
        raise SystemExit("Install 'datasets': pip install datasets")

    token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")

    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info(
            "[c5_dutch] [dry-run] would download configs %s from %s (token: %s)",
            list(_CONFIGS), HF_DATASET, "set" if token else "not set",
        )
        return

    total = 0
    for config in _CONFIGS:
        total += _download_config(config, output_dir, split, max_docs, shard_size, exclude_nc, token)

    _log(output_dir)
    logger.info("[c5_dutch] All configs done. %d docs total.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download C5 Dutch — CC-licensed CommonCrawl web pages"
    )
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-docs",   type=int, default=500_000)
    parser.add_argument("--shard-size", type=int, default=50_000)
    parser.add_argument("--include-nc", action="store_true",
                        help="Include CC-BY-NC and CC-BY-NC-SA documents")
    parser.add_argument("--hf-token",   default=None)
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        max_docs=args.max_docs,
        shard_size=args.shard_size,
        exclude_nc=not args.include_nc,
        hf_token=args.hf_token,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
