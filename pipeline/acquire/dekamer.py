"""
Download Belgian Chamber of Representatives (De Kamer) plenary transcripts.

Plenary session HTML files are published at dekamer.be under a public-domain
license (parliamentary records, no copyright restriction in Belgium).

URL pattern:
  https://www.dekamer.be/doc/PCRI/html/{leg}/ip{num:03d}x.html

Legislatures covered:
  49 (1995-1999), 50 (1999-2003), 51 (2003-2007), 52 (2007-2010),
  53 (2010-2014), 54 (2014-2019), 55 (2019-2024), 56 (2024-2030)

The HTML files are bilingual (Dutch + French). Dutch language filtering
is applied at parse time.

License: Public Domain (Belgian parliamentary records)

Usage:
  python -m pipeline acquire --sources dekamer
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

SOURCE   = "dekamer"
LICENSE  = "PD"
COUNTRY  = "BE"
DOMAIN   = "parliamentary"

_BASE_URL = "https://www.dekamer.be/doc/PCRI/html/{leg}/ip{num:03d}x.html"
_USER_AGENT = "NederCorpus/0.1 (academic; Belgian PD parliamentary text)"

# Legislature number → (start_year, end_year)
_LEGISLATURES = {
    49: (1995, 1999),
    50: (1999, 2003),
    51: (2003, 2007),
    52: (2007, 2010),
    53: (2010, 2014),
    54: (2014, 2019),
    55: (2019, 2024),
    56: (2024, 2030),
}


def _session_url(leg: int, num: int) -> str:
    return _BASE_URL.format(leg=leg, num=num)


def _fetch_html(url: str, retries: int = 3, timeout: int = 30) -> bytes | None:
    """
    Fetch URL and return raw bytes.

    Returns None on 404. Retries on transient errors.
    """
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == retries - 1:
                logger.warning("[dekamer] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[dekamer] Failed to fetch %s: %s", url, exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _log_entry(output_dir: Path, leg: int, num: int, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
        "legislature":      leg,
        "session_num":      num,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    max_sessions: int = 5000,
    dry_run: bool = False,
) -> None:
    """
    Download De Kamer plenary transcript HTML files.

    For each legislature, tries session numbers 001 to 400 (skips 404s).
    Saves as: dekamer_leg{leg}_ip{num:03d}.html
    Skips already-downloaded files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[dekamer] [dry-run] would download plenary transcripts")
        return

    total = 0

    for leg, (start_year, end_year) in sorted(_LEGISLATURES.items()):
        leg_total = 0
        logger.info(
            "[dekamer] Legislature %d (%d-%d)", leg, start_year, end_year
        )

        for num in range(1, 401):
            if total >= max_sessions:
                logger.info("[dekamer] Reached max_sessions=%d, stopping", max_sessions)
                return

            fname = f"dekamer_leg{leg}_ip{num:03d}.html"
            dest  = output_dir / fname

            if dest.exists():
                logger.debug("[dekamer] Skip existing: %s", fname)
                total += 1
                leg_total += 1
                continue

            url  = _session_url(leg, num)
            data = _fetch_html(url)

            if data is None:
                # 404 means no more sessions for this legislature
                logger.debug("[dekamer] 404 at leg=%d num=%d — no more sessions", leg, num)
                break

            dest.write_bytes(data)
            _log_entry(output_dir, leg, num, url)
            logger.info("[dekamer] Downloaded %s", fname)
            total += 1
            leg_total += 1

            time.sleep(1.0)

        logger.info("[dekamer] Legislature %d: %d sessions downloaded", leg, leg_total)

    logger.info("[dekamer] Done. Total files: %d", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download De Kamer plenary transcripts (Belgian PD parliamentary text)"
    )
    parser.add_argument("--output",       required=True, type=Path)
    parser.add_argument("--max-sessions", type=int, default=5000)
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        max_sessions=args.max_sessions,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
