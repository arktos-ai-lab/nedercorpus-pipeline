"""
Download the Europarl Dutch-language corpus.

Source: European Parliament proceedings in Dutch (1996–2011), Europarl v7
        from the Statistical Machine Translation group at Edinburgh.
        https://www.statmt.org/europarl/

The archive contains one .txt file per parliamentary session (~1 500 files),
each with XML-like chapter/paragraph markup.  Each file becomes one Document
in the parse stage.

License: Public Domain (EU institutional documents, freely reusable under
         the EUR-Lex reuse policy; see https://eur-lex.europa.eu/legal-content/
         EN/TXT/?uri=CELEX:12012E/TXT  and EC Decision 2011/833/EU)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import tarfile
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "europarl"
LICENSE = "PD"
COUNTRY = "EU"
DOMAIN  = "parliamentary"

# Europarl v7 full source archive (≈ 1.5 GB compressed, contains all 21 languages).
# The old monolingual nl.tgz (https://www.statmt.org/europarl/v7/nl.tgz) is no longer
# available (404).  The full source archive remains accessible and contains the Dutch
# sessions under txt/nl/; we extract only those files.
EUROPARL_URL = "https://www.statmt.org/europarl/v7/europarl.tgz"
# Dutch sessions live under this prefix inside the archive:
_NL_PREFIX = "txt/nl/"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_stream(url: str, dest: Path, timeout: int = 300) -> None:
    """Stream-download url to dest (avoids loading the whole file in memory)."""
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(1 << 20)   # 1 MB
                if not chunk:
                    break
                f.write(chunk)


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download and extract the Europarl v7 Dutch corpus.

    The archive is streamed to a temporary .tgz file, then extracted
    into output_dir/sessions/.  Already-extracted sessions are skipped
    on subsequent runs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = output_dir / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    archive_path = output_dir / "europarl.tgz"

    logger.info("[europarl] Source: %s", EUROPARL_URL)

    if dry_run:
        logger.info("[dry-run] skipping download")
        return

    # Download archive if not already present
    if not archive_path.exists():
        logger.info("[europarl] Downloading archive (~1.5 GB, all languages) …")
        try:
            _fetch_stream(EUROPARL_URL, archive_path)
            _log(output_dir, EUROPARL_URL)
            logger.info("[europarl] Archive saved: %s", archive_path)
        except Exception as exc:
            logger.error("Failed to download Europarl archive: %s", exc)
            archive_path.unlink(missing_ok=True)
            return
    else:
        logger.info("[europarl] Archive already present — skipping download")

    # Extract Dutch session files only (txt/nl/ prefix inside the archive)
    logger.info("[europarl] Extracting Dutch session files (prefix: %s) …", _NL_PREFIX)
    extracted = 0
    skipped = 0
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.name.endswith(".txt"):
                    continue
                # Only extract Dutch files; member names like: txt/nl/ep-96-04-15.txt
                if not member.name.startswith(_NL_PREFIX):
                    continue
                session_name = Path(member.name).name
                dest = sessions_dir / session_name
                if dest.exists():
                    skipped += 1
                    continue
                f = tf.extractfile(member)
                if f is None:
                    continue
                dest.write_bytes(f.read())
                extracted += 1
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
        return

    logger.info(
        "[europarl] Extracted %d session files (%d already present).",
        extracted, skipped,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Europarl Dutch corpus (public domain)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
