"""
Download the Digital Corpus of the European Parliament (DCEP).

DCEP contains European Parliament documents from 2001–2012 that are NOT
included in the standard Europarl corpus: press releases, legislative
session documents, committee reports, written questions, etc.

License: EU institutional open re-use (Commission Decision 2011/833/EU)
         Free re-use for all purposes (incl. commercial) with attribution.
         Functionally equivalent to CC-BY.

Size: ~1.37 billion words across 23 EU languages; Dutch is included.

Download: JRC Language Technology Resources
  https://wt-public.emm4u.eu/Resources/DCEP-2013/DCEP-Download-Page.html

NOTE: The original URL used here was wrong (.tgz suffix; actual files are .tar.bz2).
Corrected URLs (verified 2026-03-24):
  Stripped plain text: https://wt-public.emm4u.eu/Resources/DCEP-2013/strip/DCEP-strip-NL-pub.tar.bz2
  Sentence-segmented:  https://wt-public.emm4u.eu/Resources/DCEP-2013/sentences/DCEP-sentence-NL-pub.tar.bz2
"""
from __future__ import annotations

import datetime
import json
import logging
import ssl
import tarfile
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

# wt-public.emm4u.eu has a broken/self-signed TLS certificate
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "dcep"
LICENSE = "CC-BY"   # EU open re-use ≈ CC-BY (Commission Decision 2011/833/EU)

_USER_AGENT = "NederCorpus/0.1 (academic research; Dutch parliamentary corpus)"
_MAX_RETRIES = 3

# Candidates in order of preference (plain stripped text first)
_DOWNLOAD_CANDIDATES = [
    "https://wt-public.emm4u.eu/Resources/DCEP-2013/strip/DCEP-strip-NL-pub.tar.bz2",
    "https://wt-public.emm4u.eu/Resources/DCEP-2013/sentences/DCEP-sentence-NL-pub.tar.bz2",
]


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 600) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(10.0 * (attempt + 1))
        except Exception as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(10.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download the DCEP Dutch monolingual corpus."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[dcep] [dry-run] would download from %s", _DOWNLOAD_CANDIDATES[0])
        return

    existing = list(output_dir.glob("**/*.txt"))
    if existing:
        logger.info("[dcep] already downloaded (%d .txt files) — skipping", len(existing))
        return

    for url in _DOWNLOAD_CANDIDATES:
        logger.info("[dcep] Trying %s", url)
        try:
            data = _fetch(url, timeout=600)
        except urllib.error.HTTPError as exc:
            logger.warning("[dcep] %s → HTTP %d", url, exc.code)
            continue
        except Exception as exc:
            logger.warning("[dcep] %s → %s", url, exc)
            continue

        logger.info("[dcep] Downloaded %.1f MB — extracting", len(data) / 1e6)
        try:
            mode = "r:bz2" if url.endswith(".bz2") else "r:gz"
            with tarfile.open(fileobj=BytesIO(data), mode=mode) as tf:
                tf.extractall(output_dir)
        except Exception as exc:
            logger.error("[dcep] Extraction failed: %s", exc)
            continue

        _log(output_dir, url)
        txt_files = list(output_dir.glob("**/*.txt"))
        logger.info("[dcep] Extracted %d .txt files", len(txt_files))
        return

    logger.warning(
        "[dcep] All download URLs failed. The JRC server may be temporarily down "
        "or the archive has moved. See: "
        "https://wt-public.emm4u.eu/Resources/DCEP-2013/DCEP-Download-Page.html"
    )
