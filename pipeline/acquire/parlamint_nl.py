"""
Download ParlaMint-NL corpus from CLARIN.SI.

ParlaMint 4.0 is a multilingual corpus of parliamentary proceedings.
The NL component contains Dutch parliamentary debates (Tweede Kamer, 2015-2022),
annotated as TEI-XML with speaker, party, and agenda-item metadata.
~4.2M tokens of parliamentary Dutch.

Repository: https://www.clarin.si/repository/xmlui/handle/11356/2004
  (ParlaMint-NL.tgz, sequence=21, ~324MB)

License: CC BY 4.0
  Erjavec et al. (2023) ParlaMint 4.0. CLARIN.SI repository.

NOTE: handle 11356/2047 is a Docker evaluator service, NOT the corpus.
      The correct handle for the full corpus is 11356/2004.
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

SOURCE  = "parlamint_nl"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "parliamentary"

# CLARIN.SI direct download — handle 11356/2004 is the full ParlaMint 4.0 corpus.
# ParlaMint-NL.tgz is bitstream sequence=21 (~324MB, confirmed public access).
_HANDLE_BASE   = "https://www.clarin.si/repository/xmlui/bitstream/handle/11356/2004"
_NL_DIRECT_URL = f"{_HANDLE_BASE}/ParlaMint-NL.tgz?sequence=21&isAllowed=y"

# Fallback archive filenames if sequence=21 moves
_ARCHIVE_FILES = [
    "ParlaMint-NL.tgz",
    "ParlaMint-NL.tar.gz",
    "ParlaMint-NL-TEI.zip",
]

# Alternative: GitHub releases on clarin-eric/ParlaMint
# The ParlaMint project publishes country-specific tarballs as GitHub release assets.
_GITHUB_RELEASES_API = (
    "https://api.github.com/repos/clarin-eric/ParlaMint/releases"
)

# Alternative: Zenodo deposit — ParlaMint corpora are often mirrored on Zenodo.
# If the CLARIN.SI handle fails, we also try the Zenodo records API for a
# deposit that mentions "ParlaMint-NL".
_ZENODO_SEARCH_URL = (
    "https://zenodo.org/api/records?q=ParlaMint-NL&sort=mostrecent&size=5"
)

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


def _is_oci_image(data: bytes) -> bool:
    """Return True if the bytes appear to be a Docker/OCI image archive instead of corpus data.

    CLARIN.SI handle 11356/2047 has multiple bitstreams; sequence=1 is a Docker
    eval-service image, not the TEI corpus.  We detect OCI images by checking
    whether the tarball contains an 'oci-layout' entry at its root.
    """
    import io
    import tarfile as _tf
    try:
        with _tf.open(fileobj=io.BytesIO(data), mode="r:*") as t:
            names = {m.name.lstrip("./") for m in t.getmembers()}
        return "oci-layout" in names or "manifest.json" in names and "blobs" in names
    except Exception:
        return False


def _try_clarin_si(output_dir: Path) -> Path | None:
    """Try direct known URL first, then iterate filenames × sequence numbers."""
    # Fast path: known-good direct URL (ParlaMint-NL.tgz, sequence=21, handle 11356/2004)
    dest_direct = output_dir / "ParlaMint-NL.tgz"
    if dest_direct.exists() and dest_direct.stat().st_size > 300_000_000:
        logger.info("[parlamint_nl] Archive already exists: ParlaMint-NL.tgz")
        return dest_direct
    logger.info("[parlamint_nl] Downloading ParlaMint-NL.tgz (~324MB) from CLARIN.SI…")
    try:
        data = _fetch(_NL_DIRECT_URL, timeout=600)
        if len(data) > 100_000_000 and not _is_oci_image(data):
            dest_direct.write_bytes(data)
            _log(output_dir, _NL_DIRECT_URL)
            logger.info("[parlamint_nl] Saved ParlaMint-NL.tgz (%.1f MB)", len(data) / 1e6)
            return dest_direct
    except Exception as exc:
        logger.warning("[parlamint_nl] Direct URL failed: %s", exc)

    # Fallback: iterate filename × sequence
    for filename in _ARCHIVE_FILES:
        dest = output_dir / filename
        if dest.exists() and dest.stat().st_size > 100_000_000:
            return dest
        for seq in range(1, 30):
            url = f"{_HANDLE_BASE}/{filename}?sequence={seq}&isAllowed=y"
            try:
                data = _fetch(url, timeout=600)
                if len(data) < 10_000_000:
                    continue
                if _is_oci_image(data):
                    continue
                dest.write_bytes(data)
                _log(output_dir, url)
                logger.info("[parlamint_nl] Saved %s seq=%d (%.1f MB)", filename, seq, len(data) / 1e6)
                return dest
            except Exception:
                continue
    return None


def _try_github_releases(output_dir: Path) -> Path | None:
    """Try to find a ParlaMint-NL archive in GitHub releases for clarin-eric/ParlaMint."""
    import json as _json
    logger.info("[parlamint_nl] Trying GitHub releases for clarin-eric/ParlaMint...")
    try:
        req = urllib.request.Request(
            _GITHUB_RELEASES_API,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            releases = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("[parlamint_nl] GitHub releases API failed: %s", exc)
        return None

    # Search for a NL-specific asset in the most recent releases
    for release in releases[:5]:
        assets = release.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            if "NL" in name and any(name.endswith(ext) for ext in (".tgz", ".tar.gz", ".zip")):
                url = asset.get("browser_download_url", "")
                if not url:
                    continue
                dest = output_dir / name
                if dest.exists():
                    logger.info("[parlamint_nl] Archive already exists: %s", name)
                    return dest
                logger.info("[parlamint_nl] Downloading GitHub asset: %s", url)
                try:
                    data = _fetch(url, timeout=600)
                    dest.write_bytes(data)
                    _log(output_dir, url)
                    logger.info("[parlamint_nl] Saved %s (%.1f MB)", name, len(data) / 1e6)
                    return dest
                except Exception as exc2:
                    logger.warning("[parlamint_nl] GitHub asset %s failed: %s", name, exc2)
    return None


def _try_zenodo(output_dir: Path) -> Path | None:
    """Search Zenodo for a ParlaMint-NL deposit and download the first matching archive."""
    import json as _json
    logger.info("[parlamint_nl] Searching Zenodo for ParlaMint-NL...")
    try:
        req = urllib.request.Request(
            _ZENODO_SEARCH_URL,
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            results = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("[parlamint_nl] Zenodo search failed: %s", exc)
        return None

    hits = results.get("hits", {}).get("hits", [])
    for hit in hits:
        record_id = hit.get("id") or hit.get("record_id", "")
        if not record_id:
            continue
        # Fetch the record's file list
        try:
            files_url = f"https://zenodo.org/api/records/{record_id}/files"
            req2 = urllib.request.Request(files_url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req2, timeout=20) as resp2:
                files_data = _json.loads(resp2.read().decode("utf-8"))
            entries = files_data.get("entries", files_data.get("files", []))
        except Exception:
            continue

        for f in entries:
            fname = f.get("key", f.get("filename", ""))
            if "NL" in fname and any(fname.endswith(ext) for ext in (".tgz", ".tar.gz", ".zip")):
                links = f.get("links", {})
                file_url = links.get("self", links.get("content", ""))
                if not file_url:
                    continue
                dest = output_dir / fname
                if dest.exists():
                    return dest
                logger.info("[parlamint_nl] Downloading from Zenodo: %s", fname)
                try:
                    data = _fetch(file_url, timeout=600)
                    dest.write_bytes(data)
                    _log(output_dir, file_url)
                    logger.info("[parlamint_nl] Saved %s (%.1f MB)", fname, len(data) / 1e6)
                    return dest
                except Exception as exc2:
                    logger.warning("[parlamint_nl] Zenodo file %s failed: %s", fname, exc2)
    return None


def _try_download_archive(output_dir: Path) -> Path | None:
    """
    Try CLARIN.SI first, then GitHub releases, then Zenodo.

    CLARIN.SI bitstream ?sequence=1 for handle 11356/2047 is a Docker eval-service
    image (OCI format), NOT the TEI corpus.  We iterate sequences 1-8 and reject
    any download that looks like an OCI image.

    Returns the local path of the saved file, or None on all failures.
    """
    archive = _try_clarin_si(output_dir)
    if archive:
        return archive

    logger.warning("[parlamint_nl] CLARIN.SI download failed — trying GitHub releases")
    archive = _try_github_releases(output_dir)
    if archive:
        return archive

    logger.warning("[parlamint_nl] GitHub releases failed — trying Zenodo")
    archive = _try_zenodo(output_dir)
    return archive


def _extract_archive(archive_path: Path, output_dir: Path) -> None:
    """Extract the downloaded archive into output_dir/tei/."""
    tei_dir = output_dir / "tei"
    tei_dir.mkdir(exist_ok=True)

    name = archive_path.name.lower()
    logger.info("[parlamint_nl] Extracting %s into %s", archive_path.name, tei_dir)

    if name.endswith((".tar.gz", ".tgz")):
        import tarfile
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(tei_dir)
    elif name.endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(tei_dir)
    else:
        logger.warning("[parlamint_nl] Unknown archive format: %s", archive_path.name)
        return

    logger.info("[parlamint_nl] Extraction complete")


def download(
    output_dir: Path,
    extract: bool = True,
    dry_run: bool = False,
) -> None:
    """
    Download ParlaMint-NL TEI corpus from CLARIN.SI.

    Downloads the archive, then extracts TEI XML files into output_dir/tei/.
    The parser reads tei/*.xml to produce documents.

    Args:
        extract:  If True, extract the archive after download (default True).
        dry_run:  Log only, do not download.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[parlamint_nl] [dry-run] would download ParlaMint-NL from CLARIN.SI")
        return

    # Check if already extracted
    tei_dir = output_dir / "tei"
    if tei_dir.exists() and any(tei_dir.rglob("*.xml")):
        logger.info("[parlamint_nl] TEI files already present in %s", tei_dir)
        return

    archive = _try_download_archive(output_dir)
    if archive is None:
        logger.error(
            "[parlamint_nl] All download sources failed — manual action required.\n"
            "  WARNING: parlamint_nl needs manual investigation.\n"
            "  Try downloading from one of these sources and place the archive in %s:\n"
            "    https://www.clarin.si/repository/xmlui/handle/11356/2004\n"
            "    https://github.com/clarin-eric/ParlaMint/releases\n"
            "    https://zenodo.org (search for ParlaMint-NL)",
            output_dir,
        )
        return

    if extract:
        try:
            _extract_archive(archive, output_dir)
        except Exception as exc:
            logger.error("[parlamint_nl] Extraction failed: %s", exc)

    logger.info("[parlamint_nl] Done.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download ParlaMint-NL (Tweede Kamer TEI, CC BY 4.0)"
    )
    parser.add_argument("--output",         required=True, type=Path)
    parser.add_argument("--no-extract",     action="store_true",
                        help="Do not extract archive after download")
    parser.add_argument("--dry-run",        action="store_true")
    args = parser.parse_args()
    download(args.output, extract=not args.no_extract, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
