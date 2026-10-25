"""
Download the NCHLT Afrikaans Named Entity Annotated Corpus from SADiLaR.

A NER-annotated corpus of Afrikaans text based on South African government-domain
documents. Annotations follow the NCHLT annotation scheme (PER, ORG, LOC, MISC).

For NederCorpus we extract only the underlying Afrikaans text (word tokens),
stripping NE and POS annotations so the result is clean plain text.

License: CC BY 2.5 South Africa
  https://repo.sadilar.org/items/7f3e18f7-491d-4801-b6ff-be54eecfe28e

Size: ~24.6 MB (compressed)
Domain: government / NER annotation
Language: Afrikaans (AF)
Country: ZA
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

logger = logging.getLogger(__name__)

SOURCE  = "nchlt_af_ner"
LICENSE = "CC-BY"
COUNTRY = "ZA"
DOMAIN  = "government"

# DSpace 7 item UUID from repo.sadilar.org
_ITEM_UUID   = "7f3e18f7-491d-4801-b6ff-be54eecfe28e"
_DSPACE_BASE = "https://repo.sadilar.org/server/api"
_ARCHIVE_NAME = "nchlt_af_ner.zip"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

_UA = "NederCorpus/0.1 (academic research; Afrikaans NLP; SADiLaR NCHLT NER)"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 300, retries: int = 3) -> bytes:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 10.0 * (attempt + 1)
            logger.warning("[nchlt_af_ner] Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _resolve_zip_url() -> str | None:
    """Resolve SADiLaR item UUID to ZIP download URL via DSpace 7 REST API."""
    bundles_url = f"{_DSPACE_BASE}/core/items/{_ITEM_UUID}/bundles"
    try:
        data = json.loads(_fetch(bundles_url, timeout=60))
    except Exception as exc:
        logger.warning("[nchlt_af_ner] Bundle fetch failed: %s", exc)
        return None

    for bundle in data.get("_embedded", {}).get("bundles", []):
        if bundle.get("name") != "ORIGINAL":
            continue
        bits_href = bundle.get("_links", {}).get("bitstreams", {}).get("href", "")
        if not bits_href:
            continue
        try:
            bits = json.loads(_fetch(bits_href, timeout=60))
            for bit in bits.get("_embedded", {}).get("bitstreams", []):
                if bit.get("name", "").endswith(".zip"):
                    return bit["_links"]["content"]["href"]
        except Exception as exc:
            logger.warning("[nchlt_af_ner] Bitstream fetch failed: %s", exc)
    return None


def _extract_text_from_zip(zip_path: Path, out_dir: Path) -> int:
    """Extract annotated corpus files from ZIP into out_dir.

    Saves the raw annotated files as-is; parsing into plain text is done
    by the parser step to keep acquisition and parsing decoupled.

    Returns number of files written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.startswith("__MACOSX") or member.endswith("/"):
                continue
            dest_name = member.replace("/", "_").replace("\\", "_")
            dest = out_dir / dest_name
            if dest.exists():
                continue
            dest.write_bytes(zf.read(member))
            written += 1
    return written


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download NCHLT Afrikaans NER corpus from SADiLaR and extract files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir  = output_dir / "corpus"
    archive_path = output_dir / _ARCHIVE_NAME

    if corpus_dir.exists() and any(corpus_dir.iterdir()):
        logger.info("[nchlt_af_ner] Already extracted — skipping")
        return

    if dry_run:
        logger.info("[nchlt_af_ner] [dry-run] would download NCHLT Afrikaans NER corpus from SADiLaR")
        return

    if not archive_path.exists():
        url = _resolve_zip_url()
        if not url:
            logger.error("[nchlt_af_ner] Could not resolve download URL — aborting")
            return

        logger.info("[nchlt_af_ner] Downloading NCHLT Afrikaans NER corpus (~25 MB) …")
        try:
            data = _fetch(url, timeout=300)
            archive_path.write_bytes(data)
            _log(output_dir, url)
            logger.info("[nchlt_af_ner] Saved %s (%.1f MB)", _ARCHIVE_NAME, len(data) / 1e6)
        except Exception as exc:
            logger.error("[nchlt_af_ner] Download failed: %s", exc)
            archive_path.unlink(missing_ok=True)
            return
    else:
        logger.info("[nchlt_af_ner] Archive already exists: %s", archive_path)

    logger.info("[nchlt_af_ner] Extracting corpus files …")
    try:
        n = _extract_text_from_zip(archive_path, corpus_dir)
        logger.info("[nchlt_af_ner] Extracted %d files to %s", n, corpus_dir)
    except zipfile.BadZipFile as exc:
        logger.error("[nchlt_af_ner] Bad ZIP: %s — deleting so next run re-downloads", exc)
        archive_path.unlink(missing_ok=True)
        return

    logger.info("[nchlt_af_ner] Done.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download NCHLT Afrikaans NER corpus (CC BY 2.5 ZA) from SADiLaR"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
