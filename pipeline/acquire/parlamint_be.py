"""
Download ParlaMint-BE corpus from CLARIN.SI.

ParlaMint-BE is a corpus of Belgian federal Chamber of Representatives
parliamentary proceedings encoded in TEI XML. It is part of the pan-European
ParlaMint project. The Belgian parliament is bilingual (Dutch and French);
the Dutch (NL-BE) portions are retained after language filtering.

Repository: https://www.clarin.si/repository/xmlui/handle/11356/2004
License: CC BY 4.0

Format: TEI XML files (one per parliamentary session/segment), bundled as
a compressed TAR archive.

Covers: 2015 to 2023 (Belgian federal legislature).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import ssl
import time
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE   = "parlamint_be"
LICENSE  = "CC-BY"
COUNTRY  = "BE"
DOMAIN   = "parliamentary"

# CLARIN.SI direct download handle for ParlaMint-BE v5.0
_HANDLE_BASE = "https://www.clarin.si/repository/xmlui/bitstream/handle/11356/2004"

# Known archive filenames for ParlaMint-BE v5.0.
_ARCHIVE_FILES = [
    "ParlaMint-BE.tgz",
    "ParlaMint-BE.tar.gz",
    "ParlaMint-BE-TEI.zip",
    "ParlaMint-BE-txt.zip",
]

_USER_AGENT = "NederCorpus/0.1 (academic research; parliamentary corpus; CLARIN)"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 300, retries: int = 3) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 10.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _try_download_archive(output_dir: Path) -> Path | None:
    """
    Try each known archive filename to find the correct download.
    Returns the local path of the saved file, or None on failure.
    """
    for filename in _ARCHIVE_FILES:
        url = f"{_HANDLE_BASE}/{filename}?sequence=1&isAllowed=y"
        dest = output_dir / filename
        if dest.exists():
            logger.info("[parlamint_be] Archive already exists: %s", filename)
            return dest
        logger.info("[parlamint_be] Trying: %s", url)
        try:
            data = _fetch(url, timeout=600)
            # Minimal check: at least 1 MB for a real archive
            if len(data) < 1_000_000:
                logger.warning("[parlamint_be] Response too small for %s (%d bytes)", filename, len(data))
                continue
            dest.write_bytes(data)
            _log(output_dir, url)
            logger.info("[parlamint_be] Saved %s (%.1f MB)", filename, len(data) / 1e6)
            return dest
        except Exception as exc:
            logger.warning("[parlamint_be] %s failed: %s", filename, exc)
            continue
    return None


def _extract_archive(archive_path: Path, output_dir: Path) -> None:
    """Extract the downloaded archive into output_dir/tei/."""
    tei_dir = output_dir / "tei"
    tei_dir.mkdir(exist_ok=True)

    name = archive_path.name.lower()
    logger.info("[parlamint_be] Extracting %s → %s", archive_path.name, tei_dir)

    if name.endswith((".tar.gz", ".tgz")):
        import tarfile
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(tei_dir)
    elif name.endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(tei_dir)
    else:
        logger.warning("[parlamint_be] Unknown archive format: %s", archive_path.name)
        return

    logger.info("[parlamint_be] Extraction complete")


def download(
    output_dir: Path,
    extract: bool = True,
    dry_run: bool = False,
) -> None:
    """
    Download ParlaMint-BE TEI corpus from CLARIN.SI.

    Downloads the archive, then extracts TEI XML files into output_dir/tei/.
    The parser reads tei/*.xml (skipping *-ana.xml) to produce documents.

    Args:
        extract:  If True, extract the archive after download (default True).
        dry_run:  Log only, do not download.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[parlamint_be] [dry-run] would download ParlaMint-BE from CLARIN.SI")
        return

    # Check if already extracted
    tei_dir = output_dir / "tei"
    if tei_dir.exists() and any(tei_dir.rglob("*.xml")):
        logger.info("[parlamint_be] TEI files already present in %s", tei_dir)
        return

    archive = _try_download_archive(output_dir)
    if archive is None:
        logger.error(
            "[parlamint_be] Could not download ParlaMint-BE archive from CLARIN.SI.\n"
            "  Manual download: https://www.clarin.si/repository/xmlui/handle/11356/2004\n"
            "  Place the archive in: %s",
            output_dir,
        )
        return

    if extract:
        try:
            _extract_archive(archive, output_dir)
        except Exception as exc:
            logger.error("[parlamint_be] Extraction failed: %s", exc)

    logger.info("[parlamint_be] Done.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download ParlaMint-BE (Belgian Chamber TEI, CC BY 4.0)"
    )
    parser.add_argument("--output",         required=True, type=Path)
    parser.add_argument("--no-extract",     action="store_true",
                        help="Do not extract archive after download")
    parser.add_argument("--dry-run",        action="store_true")
    args = parser.parse_args()
    download(args.output, extract=not args.no_extract, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
