"""
Download Dutch administrative court decisions from data.rechtspraak.nl.

Covers three top-level Dutch administrative courts:
  RVS   — Raad van State (Afdeling bestuursrechtspraak)         ~5,000/year
  CRVB  — Centrale Raad van Beroep (social security law)        ~3,000/year
  CBB   — College van Beroep voor het bedrijfsleven              ~500/year

Discovery strategy:
  The data.rechtspraak.nl search feed does NOT index RvS/CRvB/CBB ECLIs
  (only lower courts appear). However the content API returns full XML for
  any valid ECLI including administrative courts.

  We enumerate ECLIs sequentially: ECLI:NL:{COURT}:{YEAR}:{N} for each
  court and year. A configurable consecutive-miss threshold stops enumeration
  once the end of a year's numbering is reached. Already-downloaded files
  are skipped (resume-safe).

  Estimated total: ~125,000 RvS + ~75,000 CRvB + ~12,000 CBB decisions.
  At ~0.1s/request (~20% hit rate at upper range): ~4–6 hours.

License: Public Domain (Art. 11 Auteurswet — official government texts)
Domain: legal
Country: NL

Usage:
  python -m pipeline acquire --sources raad_van_state
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CONTENT_API = "https://data.rechtspraak.nl/uitspraken/content"
SOURCE      = "raad_van_state"
LICENSE     = "PD"
COUNTRY     = "NL"
DOMAIN      = "legal"

# Courts to enumerate, with estimated max ECLI number per year
# (set conservatively high; we stop after MAX_CONSECUTIVE_MISS misses)
_COURTS: list[tuple[str, int]] = [
    ("RVS",  7000),   # Raad van State — ~5K/year, some years higher
    ("CRVB", 5000),   # Centrale Raad van Beroep — ~3K/year
    ("CBB",  1500),   # College van Beroep — ~500/year
]

# Stop scanning a year once this many consecutive numbers return 404
MAX_CONSECUTIVE_MISS = 200

_UA = "NederCorpus/0.1 (academic research; public domain court decisions)"


def _log(output_dir: Path) -> None:
    entry = {
        "source":           SOURCE,
        "url":              CONTENT_API,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch_content(ecli: str, timeout: int = 20) -> bytes | None:
    """Fetch ECLI XML from content API. Returns None on 404, raises on other errors."""
    url = f"{CONTENT_API}?id={urllib.parse.quote(ecli)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except Exception:
        raise


def _ecli_filename(court: str, year: int, num: int) -> str:
    return f"ECLI_NL_{court}_{year}_{num:06d}.xml"


def download(
    output_dir: Path,
    start_year: int = 2000,
    dry_run: bool = False,
) -> None:
    """
    Enumerate and download Dutch administrative court decisions by ECLI number.

    Saves individual XML files to output_dir/decisions/{court}_{year}_{num}.xml.
    Resume-safe: skips already-downloaded files. Writes year sentinel files
    to skip years that completed cleanly on a previous run.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    decisions_dir = output_dir / "decisions"
    decisions_dir.mkdir(exist_ok=True)

    if dry_run:
        logger.info("[raad_van_state] [dry-run] Would enumerate RvS/CRvB/CBB %d–present",
                    start_year)
        return

    end_year = datetime.date.today().year
    total_new = 0

    for court, max_per_year in _COURTS:
        for year in range(start_year, end_year + 1):
            sentinel = output_dir / f"{court}_{year}_done.flag"
            if sentinel.exists():
                continue

            year_new = 0
            consecutive_miss = 0
            n = 1

            while n <= max_per_year:
                fname = _ecli_filename(court, year, n)
                dest = decisions_dir / fname

                if dest.exists():
                    n += 1
                    consecutive_miss = 0
                    continue

                ecli = f"ECLI:NL:{court}:{year}:{n}"
                try:
                    xml_bytes = _fetch_content(ecli)
                except Exception as exc:
                    logger.debug("[raad_van_state] fetch error %s: %s", ecli, exc)
                    n += 1
                    time.sleep(0.3)
                    continue

                if xml_bytes is None:
                    consecutive_miss += 1
                    if consecutive_miss >= MAX_CONSECUTIVE_MISS:
                        logger.debug("[raad_van_state] %s:%d: %d consecutive misses, stopping year",
                                     court, year, MAX_CONSECUTIVE_MISS)
                        break
                else:
                    dest.write_bytes(xml_bytes)
                    year_new += 1
                    total_new += 1
                    consecutive_miss = 0
                    time.sleep(0.08)

                n += 1

            logger.info("[raad_van_state] %s %d: %d new decisions", court, year, year_new)
            if year < end_year:
                sentinel.write_text(str(year_new), encoding="utf-8")

    _log(output_dir)
    logger.info("[raad_van_state] Done. %d total new decisions downloaded.", total_new)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Raad van State / CRvB / CBB decisions (PD, sequential ECLI enumeration)"
    )
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(args.output, start_year=args.start_year, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
