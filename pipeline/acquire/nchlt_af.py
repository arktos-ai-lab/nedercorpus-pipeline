"""
Download NCHLT Afrikaans text corpus from SADiLaR.

The National Centre for Human Language Technology (NCHLT) Afrikaans corpus
is a collection of South African government-domain text. It is part of the
broader NCHLT project to develop language resources for South African languages.

License: CC BY 2.5 South Africa
  https://creativecommons.org/licenses/by/2.5/za/
  SADiLaR repository: https://hdl.handle.net/20.500.12185/293

Size: ~18.77 MB (compressed), government-domain Afrikaans text.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE   = "nchlt_af"
LICENSE  = "CC-BY"
COUNTRY  = "ZA"
DOMAIN   = "government"

# DSpace 7 REST API discovery chain for handle 20.500.12185/293
# Fallback: known working bitstream content URL (DSpace 7 /server/api/core/bitstreams/.../content)
_HANDLE_HDL   = "hdl:20.500.12185/293"
_DSPACE_BASE  = "https://repo.sadilar.org/server/api"
# Known bitstream UUID resolved via REST API (as of 2026-03)
_FALLBACK_URL = (
    "https://repo.sadilar.org/server/api/core/bitstreams/"
    "00da4073-600d-4a78-b6c9-b0043577700d/content"
)
_ARCHIVE_NAME = "nchlt_af_corpus.zip"

_USER_AGENT = "NederCorpus/0.1 (academic research; Afrikaans NLP; SADiLaR NCHLT)"


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
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
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


def _find_download_url() -> str:
    """
    Discover the ZIP download URL via the DSpace 7 REST API.

    Resolution chain:
      1. /server/api/pid/find?id=hdl:... → item UUID
      2. /server/api/core/items/{uuid}/bundles → ORIGINAL bundle UUID
      3. /server/api/core/bundles/{uuid}/bitstreams → bitstream UUID
      4. /server/api/core/bitstreams/{uuid}/content → download URL

    Falls back to the hardcoded _FALLBACK_URL if any step fails.
    """
    try:
        # Step 1: resolve handle → item UUID
        pid_url = f"{_DSPACE_BASE}/pid/find?id={_HANDLE_HDL}"
        logger.info("[nchlt_af] Resolving handle via DSpace REST API: %s", pid_url)
        item_data = json.loads(_fetch(pid_url, timeout=60))
        item_uuid = item_data.get("uuid") or item_data.get("id")
        if not item_uuid:
            raise ValueError("No UUID in pid/find response")

        # Step 2: get bundles, find ORIGINAL
        bundles_url = f"{_DSPACE_BASE}/core/items/{item_uuid}/bundles"
        bundles_data = json.loads(_fetch(bundles_url, timeout=60))
        orig_uuid = None
        for bundle in bundles_data.get("_embedded", {}).get("bundles", []):
            if bundle.get("name") == "ORIGINAL":
                orig_uuid = bundle.get("uuid") or bundle.get("id")
                break
        if not orig_uuid:
            raise ValueError("ORIGINAL bundle not found")

        # Step 3: get bitstreams from ORIGINAL bundle
        bs_url = f"{_DSPACE_BASE}/core/bundles/{orig_uuid}/bitstreams"
        bs_data = json.loads(_fetch(bs_url, timeout=60))
        bitstreams = bs_data.get("_embedded", {}).get("bitstreams", [])
        if not bitstreams:
            raise ValueError("No bitstreams in ORIGINAL bundle")

        # Prefer a .zip file; otherwise take the first bitstream
        bs_uuid = None
        for bs in bitstreams:
            name = bs.get("name", "")
            if name.lower().endswith(".zip"):
                bs_uuid = bs.get("uuid") or bs.get("id")
                break
        if not bs_uuid:
            bs_uuid = bitstreams[0].get("uuid") or bitstreams[0].get("id")

        download_url = f"{_DSPACE_BASE}/core/bitstreams/{bs_uuid}/content"
        logger.info("[nchlt_af] Discovered download URL: %s", download_url)
        return download_url

    except Exception as exc:
        logger.warning(
            "[nchlt_af] REST API discovery failed (%s); using fallback URL.", exc
        )
        return _FALLBACK_URL


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download the NCHLT Afrikaans corpus ZIP from SADiLaR and extract .txt files.

    Args:
        output_dir: Directory to save files into.
        dry_run:    Log only, do not download.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[nchlt_af] [dry-run] would download NCHLT Afrikaans corpus from SADiLaR")
        return

    archive_path = output_dir / _ARCHIVE_NAME
    if not archive_path.exists():
        download_url = _find_download_url()
        logger.info("[nchlt_af] Downloading NCHLT Afrikaans corpus from SADiLaR …")
        try:
            data = _fetch(download_url, timeout=300)
            archive_path.write_bytes(data)
            _log(output_dir, download_url)
            logger.info("[nchlt_af] Saved %s (%.1f MB)", _ARCHIVE_NAME, len(data) / 1e6)
        except Exception as exc:
            logger.error("[nchlt_af] Download failed: %s", exc)
            archive_path.unlink(missing_ok=True)
            return
    else:
        logger.info("[nchlt_af] Archive already exists: %s", archive_path)

    # Extract .txt files from the ZIP
    logger.info("[nchlt_af] Extracting .txt files from %s", archive_path)
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.namelist():
                if member.lower().endswith(".txt"):
                    # Flatten into output_dir (strip directory components)
                    filename = Path(member).name
                    dest = output_dir / filename
                    if dest.exists():
                        continue
                    dest.write_bytes(zf.read(member))
                    logger.info("[nchlt_af] Extracted: %s", filename)
    except zipfile.BadZipFile as exc:
        logger.error("[nchlt_af] Bad ZIP file: %s — deleting so next run re-downloads", exc)
        archive_path.unlink(missing_ok=True)
        return

    logger.info("[nchlt_af] Done.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download NCHLT Afrikaans corpus (CC BY 2.5 ZA) from SADiLaR"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
