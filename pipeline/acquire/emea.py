"""
Download the EMEA Dutch monolingual corpus from OPUS.

The EMEA corpus contains Dutch translations of European Medicines Agency
documents (drug product information sheets). These are official EU documents
and are in the public domain. The OPUS version is released under CC-BY 4.0.

Source: https://opus.nlpl.eu/EMEA
OPUS storage: https://object.pouta.csc.fi/OPUS-EMEA/v3/mono/nl.txt.gz
License: CC-BY 4.0 (OPUS release) / PD (underlying EU documents)
Domain: medical / pharmaceutical
Size: ~1M sentences, ~50 MB compressed
"""
from __future__ import annotations

import datetime
import gzip
import json
import logging
import ssl
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "emea"
LICENSE = "CC-BY"
DOMAIN  = "medical"

MONO_URL = "https://object.pouta.csc.fi/OPUS-EMEA/v3/mono/nl.txt.gz"


def _log(output_dir: Path) -> None:
    entry = {
        "source": SOURCE,
        "url": MONO_URL,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download the EMEA Dutch monolingual corpus from OPUS.

    Decompresses the .gz file and saves nl.txt (one sentence per line).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "nl.txt"

    logger.info("[emea] License: CC-BY 4.0 (EU PD documents via OPUS)")

    if dry_run:
        logger.info("[emea] [dry-run] skipping")
        return

    if dest.exists():
        logger.info("[emea] Already downloaded: %s", dest)
        return

    logger.info("[emea] Downloading EMEA Dutch corpus (~50 MB compressed) …")
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; OPUS CC-BY)"}
    req = urllib.request.Request(MONO_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600, context=_SSL_CTX) as resp:
            compressed = resp.read()
        logger.info("[emea] Decompressing …")
        text_bytes = gzip.decompress(compressed)
        dest.write_bytes(text_bytes)
        _log(output_dir)
        logger.info("[emea] Saved %d lines → %s",
                    text_bytes.count(b"\n"), dest)
    except Exception as exc:
        logger.error("[emea] Download failed: %s", exc)
        dest.unlink(missing_ok=True)
