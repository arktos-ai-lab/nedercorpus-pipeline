"""
Download Belgian Constitutional Court (Grondwettelijk Hof) decisions.

Decisions are published in Dutch at nl.const-court.be.  As court judgments
they are in the public domain under Art. XI.172 of the Belgian WER.

URL pattern:
  https://nl.const-court.be/public/n/{year}/{year}-{num:03d}n.pdf

Coverage: 1985 – present, arrest numbers 001-200 per year.
~6,400 Dutch-language PDFs total.

Usage:
  python -m pipeline acquire --sources grondwettelijk_hof
"""
from __future__ import annotations

import datetime
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "grondwettelijk_hof"
LICENSE = "PD"
COUNTRY = "BE"
DOMAIN  = "legal"

_BASE_URL = "https://nl.const-court.be/public/n/{year}/{year}-{num:03d}n.pdf"
_USER_AGENT = "NederCorpus/0.1 (academic research; Belgian PD court decisions)"


def _pdf_url(year: int, num: int) -> str:
    return _BASE_URL.format(year=year, num=num)


def _log(output_dir: Path, year: int, num: int, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
        "year":             year,
        "num":              num,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    year_start: int = 1985,
    year_end: int | None = None,
    max_decisions: int = 10_000,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch-language Constitutional Court decisions as PDFs.

    Saves files as ghcc_{year}_{num:03d}.pdf in output_dir.
    Skips files already downloaded.  Silently ignores 404s and other
    per-file errors.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if year_end is None:
        year_end = datetime.date.today().year

    if dry_run:
        logger.info(
            "[grondwettelijk_hof] [dry-run] would download years %d-%d",
            year_start, year_end,
        )
        return

    total_downloaded = 0

    for year in range(year_start, year_end + 1):
        for num in range(1, 201):
            if total_downloaded >= max_decisions:
                logger.info(
                    "[grondwettelijk_hof] max_decisions (%d) reached", max_decisions
                )
                return

            filename = f"ghcc_{year}_{num:03d}.pdf"
            dest = output_dir / filename

            if dest.exists():
                continue

            url = _pdf_url(year, num)
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

            try:
                with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                    data = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    pass  # decision doesn't exist; normal gap in numbering
                else:
                    logger.debug(
                        "[grondwettelijk_hof] HTTP %d for %s", exc.code, url
                    )
                time.sleep(0.5)
                continue
            except Exception as exc:
                logger.debug("[grondwettelijk_hof] Error fetching %s: %s", url, exc)
                time.sleep(0.5)
                continue

            dest.write_bytes(data)
            _log(output_dir, year, num, url)
            total_downloaded += 1
            logger.info(
                "[grondwettelijk_hof] saved %s (%d bytes)", filename, len(data)
            )

            time.sleep(0.5)

    logger.info("[grondwettelijk_hof] Done. %d decisions downloaded.", total_downloaded)


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Belgian Constitutional Court decisions (PD)"
    )
    parser.add_argument("--output",        required=True, type=Path)
    parser.add_argument("--year-start",    type=int, default=1985)
    parser.add_argument("--year-end",      type=int, default=None)
    parser.add_argument("--max-decisions", type=int, default=10_000)
    parser.add_argument("--dry-run",       action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        year_start=args.year_start,
        year_end=args.year_end,
        max_decisions=args.max_decisions,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
