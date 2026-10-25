"""
Download the Dutch Statenvertaling (States Bible, 1637).

The Statenvertaling is the first complete Dutch Bible translation, published
in 1637 by order of the States General. It is firmly in the public domain.

Source: open-bibles GitHub project (USFX XML format)
License: Public Domain
Domain: religious
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "statenvertaling"
LICENSE = "PD"
DOMAIN  = "religious"

# Download candidates in priority order.
# The bible-corpus and ConduitIO repos may be archived or have moved files.
# We try multiple well-known PD Bible repositories.

# 1. Christos Christodoulopoulos' bible-corpus (GitHub raw, master branch)
_URL_BIBLE_CORPUS = (
    "https://raw.githubusercontent.com/christos-c/bible-corpus/master/bibles/Dutch.xml"
)
# 2. ConduitIO open-bibles (GitHub raw, main branch)
_URL_CONDUIT = (
    "https://github.com/ConduitIO/open-bibles/raw/main/nl-statenvertaling.xml"
)
# 3. BibleMultiConverter on GitHub — hosts USFX exports for PD Bibles
_URL_BMC = (
    "https://raw.githubusercontent.com/schierlm/BibleMultiConverter/master/biblemulticonverter/src/test/resources/nl-statenvertaling.xml"
)
# 4. eBible.org direct USFX ZIP download (stable, CC0/PD)
#    https://ebible.org/find/details.php?id=nld1637  → USFX ZIP
_URL_EBIBLE_ZIP = (
    "https://ebible.org/Scriptures/nld1637_usfx.zip"
)
# 5. Alternative eBible USFX path variant
_URL_EBIBLE_ZIP2 = (
    "https://ebible.org/Scriptures/nld1637usfx.zip"
)

# Ordered list used by the download loop
_ALL_URLS = [
    _URL_BIBLE_CORPUS,
    _URL_CONDUIT,
    _URL_BMC,
    _URL_EBIBLE_ZIP,
    _URL_EBIBLE_ZIP2,
]

# Legacy names kept for backwards compat with any callers that import them
PRIMARY_URL  = _URL_BIBLE_CORPUS
FALLBACK_URL = _URL_CONDUIT


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
    """Download the Statenvertaling USFX XML file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "nl-statenvertaling.xml"

    logger.info("[statenvertaling] License: Public Domain (1637)")

    if dry_run:
        logger.info("[statenvertaling] [dry-run] skipping")
        return

    if dest.exists():
        logger.info("[statenvertaling] Already downloaded")
        return

    headers = {"User-Agent": "NederCorpus/0.1 (academic research; PD Bible text)"}

    import io
    import zipfile

    for url in _ALL_URLS:
        logger.info("[statenvertaling] Trying: %s", url)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()

            # Handle ZIP archives — extract the first XML file found
            if url.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    xml_names = [n for n in zf.namelist() if n.endswith(".xml")]
                    if not xml_names:
                        logger.warning("[statenvertaling] No XML found in zip from %s", url)
                        continue
                    data = zf.read(xml_names[0])
                    logger.info("[statenvertaling] Extracted %s from ZIP", xml_names[0])

            dest.write_bytes(data)
            _log(output_dir, url)
            logger.info("[statenvertaling] Saved: %s (%d bytes)", dest, len(data))
            return
        except Exception as exc:
            logger.warning("[statenvertaling] Failed (%s): %s", url, exc)

    logger.error(
        "[statenvertaling] All download attempts failed — manual action required.\n"
        "  WARNING: statenvertaling needs manual investigation.\n"
        "  Download the USFX XML from one of these sources:\n"
        "    https://ebible.org/find/details.php?id=nld1637\n"
        "    https://github.com/christos-c/bible-corpus/tree/master/bibles\n"
        "    https://github.com/ConduitIO/open-bibles\n"
        "  Then save as: data/raw/statenvertaling/nl-statenvertaling.xml"
    )
