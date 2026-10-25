"""
Download HPLT Dutch monolingual corpus (High Performance Language Technologies).

HPLT is a large multilingual corpus derived from the Internet Archive and
Common Crawl. The Dutch subset (v1.2 cleaned) contains ~30B tokens.

Data: https://data.hplt-project.org/one/monotext/cleaned/
Format: JSONL with zstd compression, one document per line.
Record: {"id": str, "document_lang": str, "scores": list, "text": str, "url": str}

License: CC0 (Creative Commons No Rights Reserved)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE   = "hplt"
LICENSE  = "CC0"
COUNTRY  = "NL"
DOMAIN   = "web"
LANG     = "nl"

# HPLT v1.2 CDN — shards are numbered nl_1, nl_2, ... nl_N
_CDN_BASE = "https://data.hplt-project.org/one/monotext/cleaned"
_MAX_SHARDS = 80  # Dutch v1.2 has ~80 shards


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, retries: int = 3, timeout: int = 120) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; open data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 5.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def download(
    output_dir: Path,
    max_shards: int = 5,
    dry_run: bool = False,
) -> None:
    """
    Download HPLT Dutch JSONL.zst shards.

    Each shard is ~700MB compressed (~3-5GB uncompressed).
    Default max_shards=5 downloads ~3.5GB compressed, ~20M documents.

    Args:
        max_shards: Maximum number of shards to download (1 shard ≈ 5M docs).

    NOTE: Excluded from NederCorpus default build (§17.L).
    HPLT derives from Internet Archive web crawls. CC0 covers dataset curation
    only, not the underlying crawled web content. NederCorpus requires source
    content to be explicitly CC/PD licensed. Use c5_dutch instead.
    """
    logger.warning(
        "[hplt] EXCLUDED by NederCorpus clean-corpus policy (§17.L). "
        "CC0 covers curation only, not underlying web crawl content. "
        "Use c5_dutch instead. Skipping."
    )
    return

    import zlib
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[hplt] [dry-run] would download up to %d Dutch shards", max_shards)
        return

    downloaded = 0
    for shard_num in range(1, _MAX_SHARDS + 1):
        if downloaded >= max_shards:
            break

        url = f"{_CDN_BASE}/{LANG}_{shard_num}.jsonl.zst"
        dest = output_dir / f"hplt_nl_{shard_num:04d}.jsonl"

        if dest.exists():
            logger.info("[hplt] Shard %d already exists, skipping", shard_num)
            downloaded += 1
            continue

        logger.info("[hplt] Downloading shard %d: %s", shard_num, url)
        try:
            compressed = _fetch(url)
        except Exception as exc:
            logger.warning("[hplt] Shard %d not available: %s — stopping", shard_num, exc)
            break  # Shards are contiguous; stop on first missing

        # Decompress zstd — try zstandard first, fall back to saving compressed
        try:
            import zstandard as zstd
            dctx = zstd.ZstdDecompressor()
            decompressed = dctx.decompress(compressed, max_output_size=10 * 1024 ** 3)
        except ImportError:
            logger.warning(
                "[hplt] zstandard not installed; saving compressed shard as .jsonl.zst"
            )
            dest_zst = dest.with_suffix(".jsonl.zst")
            dest_zst.write_bytes(compressed)
            _log(output_dir, url)
            downloaded += 1
            time.sleep(1.0)
            continue
        except Exception as exc:
            logger.warning("[hplt] Decompression failed for shard %d: %s", shard_num, exc)
            continue

        dest.write_bytes(decompressed)
        _log(output_dir, url)
        logger.info("[hplt] Saved shard %d → %s (%.1f MB)", shard_num, dest.name, len(decompressed) / 1e6)

        downloaded += 1
        time.sleep(1.0)

    logger.info("[hplt] Done. %d shards downloaded.", downloaded)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download HPLT Dutch corpus (CC0, ~5M docs per shard)"
    )
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-shards", type=int, default=5,
                        help="Number of shards to download (1 shard ≈ 5M docs, ~700MB)")
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(args.output, max_shards=args.max_shards, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
