"""
Download Flemish Parliament proceedings (handelingen) from the open-data API.

The Vlaams Parlement publishes its verbatim parliamentary proceedings via
a public REST API.  Each meeting (vergadering) has a "handelingen" endpoint
that returns the full transcript as XML.

API base: https://ws.vlpar.be/e/opendata
Spec:     https://ws.vlpar.be/e/opendata/api-docs

Coverage: 1995–present (year/month iteration required for the listing endpoint)
License:  CC-BY equivalent: Gratis Open Data Licentie Vlaanderen v1.2 (attribution
          required), per https://www.vlaamsparlement.be/dossiers/open-data. Checked 2026-10-15.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

SOURCE  = "vlpar"
LICENSE = "CC-BY"
COUNTRY = "BE"
DOMAIN  = "parliamentary"

VLPAR_BASE     = "https://ws.vlpar.be/e/opendata"
VLPAR_PORTAL   = "https://www.vlaamsparlement.be"

# Vlaams Parlement started digital records in the 1995–96 legislative term
YEAR_FROM = 1995


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    headers = {
        "User-Agent": "NederCorpus/0.1 (academic research; open government data)",
        "Accept": "application/xml, text/xml, */*",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.1fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _meeting_ids_from_xml(xml_bytes: bytes) -> list[str]:
    """
    Parse /verg/zoek?year=Y&month=M response.

    Returns list of meeting IDs from <list><vergadering id="..."> elements.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug("Meeting list XML parse error: %s", exc)
        return []

    ids = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local.lower() in ("vergadering", "meeting", "item"):
            mid = el.get("id") or el.get("Id")
            if mid:
                ids.append(mid)
    return ids


def download(
    output_dir: Path,
    year_from: int = YEAR_FROM,
    year_to: int | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download Flemish Parliament handelingen (proceedings) XML files.

    Iterates year/month combinations from year_from to year_to (default:
    current year), fetches the meeting list for each month, then downloads
    the handelingen XML for each meeting.

    Each meeting is saved as {meeting_id}.xml with a .url sidecar.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.date.today()
    if year_to is None:
        year_to = today.year

    total_meetings = 0

    for year in range(year_from, year_to + 1):
        last_month = today.month if year == today.year else 12
        for month in range(1, last_month + 1):
            list_url = f"{VLPAR_BASE}/verg/zoek?year={year}&month={month}"
            logger.info("[vlpar] Listing %04d-%02d", year, month)

            if dry_run:
                logger.info("[dry-run] skipping")
                continue

            try:
                xml_bytes = _fetch(list_url, timeout=30)
            except Exception as exc:
                logger.debug("  Meeting list failed %04d-%02d: %s", year, month, exc)
                time.sleep(0.5)
                continue

            meeting_ids = _meeting_ids_from_xml(xml_bytes)
            if not meeting_ids:
                logger.debug("  No meetings %04d-%02d", year, month)
                time.sleep(0.3)
                continue

            logger.info("  %d meetings for %04d-%02d", len(meeting_ids), year, month)

            for mid in meeting_ids:
                dest = output_dir / f"{mid}.xml"
                if dest.exists():
                    total_meetings += 1
                    continue

                hand_url = f"{VLPAR_BASE}/verg/{mid}/hand"
                try:
                    hand_bytes = _fetch(hand_url, timeout=60)
                except Exception as exc:
                    logger.warning("  Handelingen fetch failed for %s: %s", mid, exc)
                    time.sleep(0.5)
                    continue

                # Basic sanity: must look like XML
                if len(hand_bytes) < 50:
                    continue

                dest.write_bytes(hand_bytes)
                canonical = f"{VLPAR_PORTAL}/nl/vergadering/{mid}"
                dest.with_name(dest.name + ".url").write_text(canonical, encoding="utf-8")
                _log(output_dir, canonical)
                total_meetings += 1

                time.sleep(0.3)

            time.sleep(0.5)

        logger.info("[vlpar] Year %d done. Total meetings so far: %d", year, total_meetings)

    logger.info("[vlpar] Done. %d meeting transcripts downloaded.", total_meetings)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Vlaams Parlement proceedings (PD)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--year-from", type=int, default=YEAR_FROM)
    parser.add_argument("--year-to",   type=int, default=None)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        year_from=args.year_from,
        year_to=args.year_to,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
