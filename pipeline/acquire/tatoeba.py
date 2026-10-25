"""
Download Dutch, Afrikaans, and Low Saxon sentences from Tatoeba.

Tatoeba is a collaborative database of example sentences and translations.
Dutch (nld): ~500,000+ sentences. Afrikaans (afr): ~30,000+ sentences.
Low Saxon (nds): ~18,000+ sentences.

License: CC-BY 2.0 FR
Download: https://downloads.tatoeba.org/exports/per_language/{lang}/
"""
from __future__ import annotations

import bz2
import datetime
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "tatoeba"
LICENSE = "CC-BY"
DOMAIN  = "general"

LANGUAGES = {
    "nld": "NL",
    "afr": "AF",
    "nds": "NDS-NL",  # Low Saxon ~18K sentences CC-BY 2.0 FR
}

_BASE_URL = "https://downloads.tatoeba.org/exports/per_language/{lang}/{lang}_sentences.tsv.bz2"
_UA = "NederCorpus/0.1 (academic research; Tatoeba CC-BY)"


def _log(output_dir: Path, lang: str, url: str) -> None:
    entry = {
        "source": SOURCE,
        "lang": lang,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download Dutch and Afrikaans Tatoeba sentence files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[tatoeba] License: CC-BY 2.0 FR")

    if dry_run:
        logger.info("[tatoeba] [dry-run] skipping")
        return

    for lang in LANGUAGES:
        dest = output_dir / f"{lang}_sentences.tsv"
        if dest.exists():
            logger.info("[tatoeba] Already downloaded: %s", dest)
            continue

        url = _BASE_URL.format(lang=lang)
        logger.info("[tatoeba] Downloading %s sentences …", lang)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                compressed = resp.read()
            text_bytes = bz2.decompress(compressed)
            dest.write_bytes(text_bytes)
            _log(output_dir, lang, url)
            logger.info("[tatoeba] %s: saved %d bytes → %s", lang, len(text_bytes), dest)
        except Exception as exc:
            logger.error("[tatoeba] Download failed for %s: %s", lang, exc)
            dest.unlink(missing_ok=True)
