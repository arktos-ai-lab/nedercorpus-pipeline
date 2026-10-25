"""
Download the JRC-Acquis Dutch corpus.

JRC-Acquis is a collection of 23,564 European Union legislative documents
translated into 22 EU languages, prepared by the Joint Research Centre (JRC).
The Dutch subset contains EU directives, regulations, and decisions in TEI XML.

Download URL:
  https://wt-public.emm4u.eu/Acquis/JRC-Acquis.3.0/corpus/jrc-nl.tgz

The archive is ~107 MB compressed, ~330 MB extracted.

License: CC-BY (attribution required)
  The JRC-Acquis usage conditions state that the European Commission owns the
  data and allow reuse on condition that the source is acknowledged
  (EC Decision 2011/833/EU). Checked 2026-10-15.
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

SOURCE  = "jrc"
LICENSE = "CC-BY"
COUNTRY = "EU"
DOMAIN  = "legislation"

JRC_URL = "https://wt-public.emm4u.eu/Acquis/JRC-Acquis.3.0/corpus/jrc-nl.tgz"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_stream(url: str, dest: Path, timeout: int = 600) -> None:
    """Stream-download url to dest."""
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(1 << 20)  # 1 MB chunks
                if not chunk:
                    break
                f.write(chunk)


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download and extract the JRC-Acquis Dutch corpus.

    The archive is streamed to output_dir/jrc-nl.tgz, then extracted to
    output_dir/docs/.  Already-extracted files are skipped on re-runs.

    TEI XML files are saved directly; the parser stage reads them.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = output_dir / "docs"
    docs_dir.mkdir(exist_ok=True)

    archive_path = output_dir / "jrc-nl.tgz"

    logger.info("[jrc] Source: %s", JRC_URL)

    if dry_run:
        logger.info("[dry-run] skipping download")
        return

    # Download archive if not already present
    if not archive_path.exists():
        logger.info("[jrc] Downloading archive (~107 MB) …")
        try:
            _fetch_stream(JRC_URL, archive_path)
            _log(output_dir, JRC_URL)
            logger.info("[jrc] Archive saved: %s", archive_path)
        except Exception as exc:
            logger.error("[jrc] Download failed: %s", exc)
            archive_path.unlink(missing_ok=True)
            return
    else:
        logger.info("[jrc] Archive already present — skipping download")

    # Extract XML files
    logger.info("[jrc] Extracting TEI XML files …")
    extracted = 0
    skipped = 0
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.name.endswith(".xml"):
                    continue
                doc_name = Path(member.name).name
                dest = docs_dir / doc_name
                if dest.exists():
                    skipped += 1
                    continue
                f = tf.extractfile(member)
                if f is None:
                    continue
                dest.write_bytes(f.read())
                extracted += 1
    except Exception as exc:
        logger.error("[jrc] Extraction failed: %s", exc)
        return

    logger.info(
        "[jrc] Extracted %d XML files (%d already present).",
        extracted, skipped,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download JRC-Acquis Dutch corpus (public domain)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
