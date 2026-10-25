"""
Download additional Dutch monolingual corpora from OPUS.

All corpora follow the same OPUS object-storage pattern:
  https://object.pouta.csc.fi/OPUS-{CORPUS}/{VERSION}/mono/nl.txt.gz

Corpora (retained — unique content, clean licenses):
  euconst      — EU Constitution documents (PD, EU official text)
  kde4         — KDE4 desktop localization (CC-BY-SA / LGPL)
  ubuntu       — Ubuntu software localization (CC-BY-SA)
  gnome        — GNOME desktop localization (CC-BY-SA)
  php          — PHP documentation translations (CC-BY 3.0)
  ecb          — European Central Bank publications (CC-BY via OPUS)
  global_voices — Global Voices citizen journalism in Dutch (CC-BY 3.0)
  dgt          — EU DGT Translation Memories (PD, EU official)
  eubookshop   — EU Publications Office bookshop texts (PD, EU official)
  # europat    — NOT AVAILABLE: OPUS-EuroPat has no Dutch (nl) monolingual file (v1/v2 both 404)
  qed          — Educational video subtitles (CC-BY)
  bible_uedin  — PD Dutch Bible translations (Christos Christodoulopoulos, PD)
  opus_books   — OPUS Books translated to Dutch (CC-BY-SA)
  tildemodel   — Tilde MODEL EU/government multilingual corpus (CC-BY, 87M NL tokens)
  news_commentary — WMT News Commentary Dutch (CC-BY-SA 4.0)

Removed (licensing / dedup issues):
  opensubtitles — REMOVED: Subtitles are derivative works of copyrighted film/TV content.
                  The CC-BY-SA label is OPUS's compilation license, not the subtitle
                  authors' rights. Fan-translated subtitles have no clear CC license from
                  the people who actually own translation rights. Grey area → excluded.
  wikimatrix   — REMOVED: Dutch side is mined Wikipedia text; fully covered by `wikipedia`.
                  Zero unique text for a monolingual Dutch corpus.
  ccmatrix     — REMOVED: Derived from Common Crawl; web crawl content excluded by policy.
  nllb         — REMOVED: Meta NLLB Dutch data sourced primarily from CCAligned + CC-100
                  (both Common Crawl web crawls). Web crawl content excluded by policy.

Usage:
  python -m pipeline acquire --sources opus_extra
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

SOURCE = "opus_extra"

# (local_name, opus_name, version, license, domain)
# opus_name is the canonical casing used in the OPUS object-storage URL.
CORPORA: list[tuple[str, str, str, str, str]] = [
    ("euconst",       "EUconst",      "v1",       "PD",       "legal"),
    ("kde4",          "KDE4",         "v2",       "CC-BY-SA", "software"),
    ("ubuntu",        "Ubuntu",       "v14.10",   "CC-BY-SA", "software"),
    ("gnome",         "GNOME",        "v1",       "CC-BY-SA", "software"),
    ("php",           "PHP",          "v1",       "CC-BY",    "technical"),
    ("ecb",           "ECB",          "v1",       "CC-BY",    "financial"),
    ("global_voices", "GlobalVoices", "v2018q4",  "CC-BY",    "news"),
    ("dgt",           "DGT",         "v4",       "PD",       "legal"),
    ("eubookshop",    "EUbookshop",  "v2",       "PD",       "legal"),
    # europat: OPUS-EuroPat has no nl.txt.gz (v1 and v2 both 404) — removed

    ("qed",           "QED",         "v2.0a",    "CC-BY",    "educational"),
    ("bible_uedin",   "bible-uedin", "v1",       "PD",       "religious"),
    ("opus_books",    "Books",       "v1",       "CC-BY-SA", "literature"),
    ("tildemodel",    "TildeMODEL",  "v2018",    "CC-BY",    "government"),
    ("news_commentary", "News-Commentary", "v16", "CC-BY-SA", "news"),
]

_BASE = "https://object.pouta.csc.fi/OPUS-{opus_name}/{version}/mono/nl.txt.gz"


def _log(output_dir: Path, corpus: str, url: str, license_: str) -> None:
    entry = {
        "source": f"opus_{corpus}",
        "url": url,
        "license": license_,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download all OPUS Dutch monolingual corpora."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[opus_extra] [dry-run] skipping")
        return

    headers = {"User-Agent": "NederCorpus/0.1 (academic research; OPUS open corpus)"}

    for corpus, opus_name, version, license_, domain in CORPORA:
        dest_dir = output_dir / corpus
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "nl.txt"

        if dest.exists():
            logger.info("[opus_extra] %s already downloaded — skipping", corpus)
            continue

        url = _BASE.format(opus_name=opus_name, version=version)

        logger.info("[opus_extra] Downloading %s (%s) …", corpus, license_)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300, context=_SSL_CTX) as resp:
                compressed = resp.read()
            text_bytes = gzip.decompress(compressed)
            dest.write_bytes(text_bytes)
            _log(output_dir, corpus, url, license_)
            logger.info("[opus_extra] %s: %d lines", corpus, text_bytes.count(b"\n"))
        except Exception as exc:
            logger.warning("[opus_extra] %s failed (%s): %s", corpus, url, exc)
            dest.unlink(missing_ok=True)
