"""
Download Brieven als Buit (Letters as Loot) — 17th/18th century Dutch correspondence.

Transcriptions of letters written by Dutch merchant sailors, intercepted by the
British Navy and preserved in British archives (Prize Papers, HCA 30). Transcribed
by citizen scientists and researchers via a Huygens Institute / KNAW project.

This is authentic Early Modern Dutch in a colloquial, informal register — the
single most valuable source for historical normalization training because it
shows real, everyday 17th-century Dutch spelling variation (as opposed to the
formal Statenvertaling or literary plays).

Source: https://brievenalsbuit.nl / https://github.com/knaw-huc/brieven-als-buit
License: CC-BY 4.0
Domain: historical correspondence
Period: ~1650–1800 (peak VOC era)
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "brieven_als_buit"
LICENSE = "CC-BY"
DOMAIN  = "historical"

# Download URL candidates in priority order.
# The repository was originally at CLARIAH/brieven-als-buit and knaw-huc/brieven-als-buit
# on GitHub, but those repos were archived or renamed.  We now also try:
#  - The HuC/KNAW TextRepoClient GitHub org (possible migration target)
#  - A versioned release ZIP on the knaw-huc repo (v1.0 tag)
#  - The Zenodo deposit associated with the Brieven als Buit project (DOI 10.5281/zenodo.7038860)
# NOTE: If all URLs fail, the data can be manually downloaded from:
#   https://brievenalsbuit.nl/downloads  or  https://zenodo.org/records/7038860
_GITHUB_URLS = [
    # Current canonical location — huygens-institute org (post-KNAW reorganisation)
    "https://github.com/HuygensING/brieven-als-buit/archive/refs/heads/main.zip",
    "https://github.com/HuygensING/brieven-als-buit/archive/refs/heads/master.zip",
    # Original CLARIAH/knaw-huc locations (may still work)
    "https://github.com/CLARIAH/brieven-als-buit/archive/refs/heads/main.zip",
    "https://github.com/knaw-huc/brieven-als-buit/archive/refs/heads/main.zip",
    "https://github.com/knaw-huc/brieven-als-buit/archive/refs/heads/master.zip",
    # Tagged release (v1.0) on knaw-huc
    "https://github.com/knaw-huc/brieven-als-buit/archive/refs/tags/v1.0.zip",
    # Zenodo deposit: Brieven als Buit transcriptions (CC BY 4.0), DOI 10.5281/zenodo.7038860
    # The actual file URL resolves via the Zenodo record API below.
]

# Zenodo record URL as last-resort fallback
_ZENODO_RECORD_URL = "https://zenodo.org/api/records/7038860"

_TEXT_EXTS = {".xml", ".txt", ".csv", ".tsv", ".json"}


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download Brieven als Buit letter transcriptions from the Huygens Institute."""
    output_dir.mkdir(parents=True, exist_ok=True)
    letters_dir = output_dir / "letters"

    logger.info("[brieven_als_buit] License: CC-BY 4.0 — Early Modern Dutch letters (1650–1800)")

    if dry_run:
        logger.info("[brieven_als_buit] [dry-run] skipping")
        return

    if letters_dir.exists() and any(letters_dir.iterdir()):
        logger.info("[brieven_als_buit] Already downloaded — skipping")
        return

    letters_dir.mkdir(exist_ok=True)
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; Brieven als Buit CC-BY)"}

    import json as _json

    zip_data: bytes | None = None
    used_url = ""
    for url in _GITHUB_URLS:
        logger.info("[brieven_als_buit] Trying %s …", url)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                zip_data = resp.read()
            used_url = url
            break
        except Exception as exc:
            logger.warning("[brieven_als_buit] %s failed: %s", url, exc)

    # Last resort: look up the Zenodo record API for a downloadable file
    if not zip_data:
        logger.info("[brieven_als_buit] Trying Zenodo record %s …", _ZENODO_RECORD_URL)
        try:
            req = urllib.request.Request(_ZENODO_RECORD_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                record = _json.loads(resp.read().decode("utf-8"))
            files = record.get("files", [])
            # Prefer ZIP files
            for f in sorted(files, key=lambda x: (0 if x.get("key", "").endswith(".zip") else 1)):
                file_url = f.get("links", {}).get("self", "")
                if not file_url:
                    continue
                logger.info("[brieven_als_buit] Downloading Zenodo file %s …", f.get("key"))
                req2 = urllib.request.Request(file_url, headers=headers)
                with urllib.request.urlopen(req2, timeout=300) as resp2:
                    zip_data = resp2.read()
                used_url = file_url
                break
        except Exception as exc:
            logger.warning("[brieven_als_buit] Zenodo fallback failed: %s", exc)

    if not zip_data:
        logger.error(
            "[brieven_als_buit] All download URLs failed — manual action required.\n"
            "  WARNING: brieven_als_buit needs manual investigation.\n"
            "  Download the data from one of these sources and place files in %s:\n"
            "    https://brievenalsbuit.nl/downloads\n"
            "    https://zenodo.org/records/7038860\n"
            "    https://github.com/HuygensING/brieven-als-buit",
            letters_dir,
        )
        return

    extracted = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext in _TEXT_EXTS:
                    parts = name.split("/")
                    # Flatten directory structure, keep last 2 path components
                    filename = "_".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
                    dest = letters_dir / filename
                    dest.write_bytes(zf.read(name))
                    extracted += 1
    except Exception as exc:
        logger.error("[brieven_als_buit] ZIP extraction failed: %s", exc)
        return

    logger.info("[brieven_als_buit] Extracted %d files", extracted)
    _log(output_dir, used_url)
