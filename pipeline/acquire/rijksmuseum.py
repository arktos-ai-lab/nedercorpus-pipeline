"""
Download Rijksmuseum collection metadata (CC0) via bulk data dump.

The Rijksmuseum releases collection and library metadata under CC0 1.0.
Data dumps: https://data.rijksmuseum.nl/docs/data-dumps

Format: tar.gz archives containing N-Triples (.nt) RDF files.
Dutch-language strings are tagged @nl in N-Triples.

Archives downloaded (most text-rich for Dutch corpus):
  - object.tar.gz   — art objects with Dutch titles/descriptions (~800K)
  - work.tar.gz     — library works with Dutch bibliographic text (~321K)
  - item.tar.gz     — library items (~381K)

License: CC0 1.0 Universal
  https://data.rijksmuseum.nl/
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import html as _html
import re
import tarfile
import time
import urllib.request
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "rijksmuseum"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "cultural_heritage"

_DUMPS_PAGE  = "https://data.rijksmuseum.nl/docs/data-dumps"
_USER_AGENT  = "NederCorpus/0.1 (academic research; Rijksmuseum CC0 open data)"

# Archives to download, in priority order (most Dutch text first)
_TARGET_ARCHIVES = ["object", "work", "item", "exhibition"]


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, retries: int = 3, timeout: int = 300) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 5.0 * (attempt + 1)
            logger.warning("[rijksmuseum] Retry %d: %s (wait %.0fs)", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _find_archive_urls(html: bytes) -> dict[str, str]:
    """
    Parse the data-dumps page to find .tar.gz download URLs.
    Returns dict of archive_name → URL.
    """
    text = html.decode("utf-8", errors="replace")
    # Include SAS query tokens (?se=...&sig=...) which are required for Azure Blob access
    urls = re.findall(r'https?://[^\s"\'<>]+\.tar\.gz(?:\?[^\s"\'<>]*)?', text)
    result: dict[str, str] = {}
    for url in urls:
        # Extract archive name: .../2024-11-07/work.tar.gz?... → "work"
        name = re.search(r'/([^/?]+)\.tar\.gz', url)
        if name:
            result[name.group(1)] = _html.unescape(url)
    return result


def _extract_nt_from_tar(tar_bytes: bytes) -> bytes | None:
    """
    Extract N-Triples content from a Rijksmuseum tar.gz archive.

    The archive contains one file per object (named by numeric ID, no extension),
    each containing N-Triples. Concatenate all member files into one stream.
    """
    try:
        chunks: list[bytes] = []
        with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                f = tf.extractfile(member)
                if f:
                    chunks.append(f.read())
                    chunks.append(b"\n")
        return b"".join(chunks) if chunks else None
    except tarfile.TarError as exc:
        logger.warning("[rijksmuseum] tar extraction error: %s", exc)
    return None


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download Rijksmuseum collection metadata from bulk tar.gz data dumps.

    Fetches the data-dumps page to find current download URLs, then downloads
    and extracts N-Triples files. Saves as rijksmuseum_{name}.nt.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[rijksmuseum] [dry-run] would download bulk dumps from %s", _DUMPS_PAGE)
        return

    logger.info("[rijksmuseum] Fetching dump index: %s", _DUMPS_PAGE)
    try:
        html = _fetch(_DUMPS_PAGE, timeout=30)
    except Exception as exc:
        logger.error("[rijksmuseum] Cannot fetch dump page: %s", exc)
        return

    archive_urls = _find_archive_urls(html)
    if not archive_urls:
        logger.error("[rijksmuseum] No .tar.gz URLs found on %s", _DUMPS_PAGE)
        return

    logger.info("[rijksmuseum] Found archives: %s", sorted(archive_urls))

    for name in _TARGET_ARCHIVES:
        url = archive_urls.get(name)
        if not url:
            logger.info("[rijksmuseum] Archive '%s' not available, skipping", name)
            continue

        nt_dest = output_dir / f"rijksmuseum_{name}.nt"
        if nt_dest.exists():
            logger.info("[rijksmuseum] Already have %s, skipping", nt_dest.name)
            continue

        logger.info("[rijksmuseum] Downloading %s.tar.gz ...", name)
        try:
            tar_bytes = _fetch(url, timeout=600)
        except Exception as exc:
            logger.warning("[rijksmuseum] Failed to download %s: %s", url, exc)
            continue

        nt_bytes = _extract_nt_from_tar(tar_bytes)
        if nt_bytes is None:
            logger.warning("[rijksmuseum] No .nt file in archive: %s", url)
            continue

        nt_dest.write_bytes(nt_bytes)
        _log(output_dir, url)
        logger.info("[rijksmuseum] Saved %s (%.1f MB)", nt_dest.name, len(nt_bytes) / 1e6)
        time.sleep(1.0)

    logger.info("[rijksmuseum] Done.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Rijksmuseum collection metadata from bulk dump (CC0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
