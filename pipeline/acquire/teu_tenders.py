"""
Download EU procurement tender texts from the PleIAs/TEDEUTenders HuggingFace dataset.

TEDEUTenders contains tender notices from the Tenders Electronic Daily (TED),
the EU's official procurement journal. The dataset is published under CC0.

Source:   HuggingFace PleIAs/TEDEUTenders
License:  CC0
Domain:   government
URL:      https://huggingface.co/datasets/PleIAs/TEDEUTenders

Strategy:
  1. Query the HuggingFace datasets API to list available Parquet shards for the
     default/train split.
  2. Download each shard, naming it teu_tenders_shard_{N:06d}.parquet.
  3. Skip shards that already exist on disk (resume support).

Usage:
  python -m pipeline acquire --sources teu_tenders
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "teu_tenders"
LICENSE = "CC0"
DOMAIN  = "government"
COUNTRY = "EU"

_UA          = "NederCorpus/0.1 (academic; teu_tenders nl corpus; contact: corpus@example.org)"
_API_URL     = "https://huggingface.co/api/datasets/PleIAs/TEDEUTenders/parquet/default/train"
_DL_TIMEOUT  = 300   # large Parquet shards can be several hundred MB


def _get_shard_urls(timeout: int = 30) -> list[str]:
    """
    Query the HuggingFace datasets API and return a list of Parquet shard URLs.

    The API returns a JSON array of objects with at minimum a 'url' key.
    As a fallback it also handles a plain list of URL strings.
    """
    req = urllib.request.Request(_API_URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)

    urls: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict):
                url = item.get("url") or item.get("filename") or ""
                if url:
                    urls.append(url)
    elif isinstance(data, dict):
        # Some HF API versions wrap the list under a key
        for key in ("urls", "files", "parquet_files"):
            if key in data and isinstance(data[key], list):
                for item in data[key]:
                    if isinstance(item, str):
                        urls.append(item)
                    elif isinstance(item, dict):
                        url = item.get("url") or ""
                        if url:
                            urls.append(url)
                break

    return urls


def _fetch_file(url: str, dest: Path, timeout: int = _DL_TIMEOUT) -> bool:
    """Download url → dest. Returns True on success."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return True
    except Exception as exc:
        logger.warning("[teu_tenders] fetch %s failed: %s", url, exc)
        return False


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download all TEDEUTenders Parquet shards.

    Saves files as teu_tenders_shard_000000.parquet, teu_tenders_shard_000001.parquet, ...
    Skips shards that already exist on disk (resume support).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[teu_tenders] [dry-run] would list and download TEDEUTenders Parquet shards")
        return

    logger.info("[teu_tenders] Querying HuggingFace API for shard list...")
    try:
        shard_urls = _get_shard_urls()
    except Exception as exc:
        logger.error("[teu_tenders] Failed to retrieve shard list: %s", exc)
        return

    if not shard_urls:
        logger.warning("[teu_tenders] No shard URLs found — dataset may have moved or be empty")
        return

    logger.info("[teu_tenders] Found %d shard(s)", len(shard_urls))

    downloaded = 0
    skipped    = 0

    for idx, url in enumerate(shard_urls):
        dest = output_dir / f"teu_tenders_shard_{idx:06d}.parquet"

        if dest.exists():
            logger.info("[teu_tenders] %s already exists — skipping", dest.name)
            skipped += 1
            continue

        logger.info("[teu_tenders] Downloading shard %d/%d: %s", idx + 1, len(shard_urls), url)
        if _fetch_file(url, dest):
            logger.info("[teu_tenders] Saved %s (%d bytes)", dest.name, dest.stat().st_size)
            downloaded += 1
        else:
            logger.warning("[teu_tenders] Shard %d download failed, continuing", idx)

    # Acquisition log
    log_entry = {
        "source":           SOURCE,
        "license":          LICENSE,
        "acquisition_date": acq_date,
        "total_shards":     len(shard_urls),
        "downloaded":       downloaded,
        "skipped":          skipped,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    logger.info(
        "[teu_tenders] Done. Downloaded %d new shard(s), skipped %d existing.",
        downloaded, skipped,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download TEDEUTenders Parquet shards (CC0)")
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
