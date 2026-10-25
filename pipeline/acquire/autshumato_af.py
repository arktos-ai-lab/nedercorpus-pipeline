"""
Download Autshumato English-Afrikaans parallel corpus from SADiLaR.

The Autshumato project (CTexT, North-West University) developed parallel
translation corpora for South African official languages. This corpus contains
aligned English-Afrikaans sentence pairs from South African government texts.

Only the Afrikaans side is ingested (the English side is discarded).

License: CC BY 4.0
  https://repo.sadilar.org/handle/20.500.12185/574

Size: ~102 MB (compressed ZIP).
Format: ZIP containing aligned text files (one sentence per line).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE   = "autshumato_af"
LICENSE  = "CC-BY"
COUNTRY  = "ZA"
DOMAIN   = "government"

# DSpace 7 REST API discovery chain for handle 20.500.12185/574
_HANDLE_HDL   = "hdl:20.500.12185/574"
_DSPACE_BASE  = "https://repo.sadilar.org/server/api"
# Known bitstream UUID resolved via REST API (as of 2026-03)
_FALLBACK_URL = (
    "https://repo.sadilar.org/server/api/core/bitstreams/"
    "22c41cab-8c39-4f9c-8708-690ab08c93e0/content"
)
_ARCHIVE_NAME = "autshumato_af_en.zip"

_USER_AGENT = "NederCorpus/0.1 (academic research; Afrikaans NLP; SADiLaR Autshumato)"


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

    The SADiLaR repository uses DSpace 7, whose frontend is a JavaScript SPA
    that does not embed bitstream hrefs in the raw HTML.  The REST API
    (/server/api/...) is the reliable discovery path.

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
        logger.info("[autshumato_af] Resolving handle via DSpace REST API: %s", pid_url)
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
        logger.info("[autshumato_af] Discovered download URL: %s", download_url)
        return download_url

    except Exception as exc:
        logger.warning(
            "[autshumato_af] REST API discovery failed (%s); using fallback URL.", exc
        )
        return _FALLBACK_URL


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download the Autshumato English-Afrikaans corpus from SADiLaR.

    Discovers the download URL via the DSpace 7 REST API, then downloads and
    extracts text files. Afrikaans side files (*.af, *afr*, *af.*) are preferred;
    all text files are saved and the language filter handles selection.

    Args:
        output_dir: Directory to save files into.
        dry_run:    Log only, do not download.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[autshumato_af] [dry-run] would download Autshumato corpus from SADiLaR")
        return

    archive_path = output_dir / _ARCHIVE_NAME
    if not archive_path.exists():
        download_url = _find_download_url()
        logger.info("[autshumato_af] Downloading corpus from: %s", download_url)
        try:
            data = _fetch(download_url, timeout=600)
            archive_path.write_bytes(data)
            _log(output_dir, download_url)
            logger.info("[autshumato_af] Saved %s (%.1f MB)", _ARCHIVE_NAME, len(data) / 1e6)
        except Exception as exc:
            logger.error("[autshumato_af] Download failed: %s", exc)
            archive_path.unlink(missing_ok=True)
            return
    else:
        logger.info("[autshumato_af] Archive already exists: %s", archive_path)

    # Extract text files from the ZIP.
    # Prefer Afrikaans side files; fall back to all text files.
    logger.info("[autshumato_af] Extracting text files from %s", archive_path)
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            all_members = zf.namelist()

        # Identify Afrikaans side files
        def _is_afrikaans(name: str) -> bool:
            n = name.lower()
            return (
                n.endswith(".af")
                or n.endswith(".afr")
                or re.search(r'[._\-]af[._\-]', n) is not None
                or "afr" in Path(n).stem.lower()
            )

        af_members = [m for m in all_members if _is_afrikaans(m)]
        # If we can identify Afrikaans files, extract only those; otherwise all text files
        text_members = af_members if af_members else [
            m for m in all_members
            if any(m.lower().endswith(ext) for ext in (".txt", ".af", ".afr"))
        ]

        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in text_members:
                filename = Path(member).name
                if not filename:
                    continue
                dest = output_dir / filename
                if dest.exists():
                    continue
                dest.write_bytes(zf.read(member))
                logger.info("[autshumato_af] Extracted: %s", filename)

    except zipfile.BadZipFile as exc:
        logger.error("[autshumato_af] Bad ZIP file: %s", exc)
        return

    logger.info("[autshumato_af] Done.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Autshumato English-Afrikaans corpus (CC BY 4.0) from SADiLaR"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
