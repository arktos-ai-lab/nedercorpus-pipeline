"""
Download Flemish Parliament journaallijn (debate item) transcripts via the open-data API.

The Vlaams Parlement open-data API exposes individual debate items (journaallijnen)
that contain verbatim speaker transcripts (sprekertekst) not available in the
handelingen bulk-download.

Strategy
--------
1. Iterate month-by-month from START_DATE to today using the date-range search endpoint:
   GET /verg/zoek/datums?datumVan=ddMMyyyy&datumTot=ddMMyyyy
   Returns XML with <vergadering id="N"> elements containing real meeting IDs.
2. Fetch /verg/{id}/hand — the handelingen (proceedings) XML that lists journaallijn IDs.
3. Parse <journaallijn id="N"> from that response.
4. Fetch /jln/{id} for each journaallijn and save as jln_{id:08d}.xml.

Resume support
--------------
- Already-saved jln files are skipped.
- Processed month ranges are recorded in months_done.txt (YYYY-MM format).

Rate limiting
-------------
0.3 s sleep between every HTTP request (configurable).

License: CC-BY (Vlaams Parlement open data)
API base: https://ws.vlpar.be/e/opendata
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

SOURCE  = "vlpar_extra"
LICENSE = "CC-BY"
COUNTRY = "BE"
DOMAIN  = "parliamentary"
LANG    = "NL-BE"

VLPAR_BASE    = "https://ws.vlpar.be/e/opendata"
PORTAL_BASE   = "https://www.vlaamsparlement.be"
REQUEST_DELAY = 0.3  # seconds between requests
START_DATE    = datetime.date(1995, 1, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes | None:
    """Fetch url. Returns None on 404/204/410; raises on other errors after retries."""
    headers = {
        "User-Agent": "NederCorpus/0.1 (academic research; open government data)",
        "Accept": "application/xml, text/xml, */*",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if len(data) <= 2:  # empty or near-empty (204 No Content via TCP)
                    return None
                return data
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 204, 410):
                return None
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("Retry %d/%d for %s: HTTP %d (wait %.1fs)",
                           attempt + 1, retries, url, exc.code, wait)
            time.sleep(wait)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("Retry %d/%d for %s: %s (wait %.1fs)",
                           attempt + 1, retries, url, exc, wait)
            time.sleep(wait)
    return None


def _month_ranges(start: datetime.date, end: datetime.date):
    """Yield (first_day, last_day) tuples for each calendar month in [start, end]."""
    cur = start.replace(day=1)
    while cur <= end:
        # last day of month
        if cur.month == 12:
            last = cur.replace(day=31)
        else:
            last = (cur.replace(month=cur.month + 1, day=1) - datetime.timedelta(days=1))
        last = min(last, end)
        yield cur, last
        # advance to next month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            cur = cur.replace(month=cur.month + 1, day=1)


def _verg_ids_from_datums(xml_bytes: bytes) -> list[int]:
    """Parse <vergadering id="N"> elements from /verg/zoek/datums response."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ids = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local.lower() in ("vergadering", "verg"):
            vid = el.get("id")
            if vid is None:
                vid = el.get("verg_id")
            if vid and vid.isdigit():
                ids.append(int(vid))
    return ids


def _jln_ids_from_hand_xml(xml_bytes: bytes) -> list[int]:
    """Parse <journaallijn id="N"> elements from /verg/{id}/hand response."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ids = []
    seen: set[int] = set()
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local.lower() in ("journaallijn", "jln"):
            jid = el.get("id")
            if jid and jid.isdigit():
                n = int(jid)
                if n not in seen:
                    seen.add(n)
                    ids.append(n)
    return ids


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    request_delay: float = REQUEST_DELAY,
    dry_run: bool = False,
) -> None:
    """
    Iterate month-by-month, fetch meeting IDs via /verg/zoek/datums,
    collect journaallijn IDs from /verg/{id}/hand, and save each /jln/{id}.xml.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resume: months already processed
    done_file = output_dir / "months_done.txt"
    done_months: set[str] = set()
    if done_file.exists():
        for line in done_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                done_months.add(line)

    # Already-saved jln IDs
    saved_jln: set[int] = {
        int(p.stem.split("_")[1])
        for p in output_dir.glob("jln_*.xml")
        if len(p.stem.split("_")) >= 2 and p.stem.split("_")[1].isdigit()
    }

    logger.info("[vlpar_extra] Resuming: %d months done, %d jln already saved",
                len(done_months), len(saved_jln))

    total_jln = len(saved_jln)
    today = datetime.date.today()

    for month_start, month_end in _month_ranges(START_DATE, today):
        month_key = month_start.strftime("%Y-%m")
        if month_key in done_months:
            continue

        datum_van = month_start.strftime("%d%m%Y")
        datum_tot = month_end.strftime("%d%m%Y")
        search_url = (
            f"{VLPAR_BASE}/verg/zoek/datums"
            f"?datumVan={datum_van}&datumTot={datum_tot}"
        )

        if dry_run:
            logger.info("[dry-run] Would fetch %s", search_url)
            done_months.add(month_key)
            continue

        try:
            search_bytes = _fetch(search_url, timeout=30)
        except Exception as exc:
            logger.warning("[vlpar_extra] date search %s error: %s", month_key, exc)
            time.sleep(request_delay)
            continue

        if not search_bytes:
            logger.debug("[vlpar_extra] %s: no meetings found", month_key)
            done_months.add(month_key)
            with done_file.open("a", encoding="utf-8") as f:
                f.write(f"{month_key}\n")
            time.sleep(request_delay * 0.5)
            continue

        verg_ids = _verg_ids_from_datums(search_bytes)
        logger.info("[vlpar_extra] %s: %d meetings", month_key, len(verg_ids))
        time.sleep(request_delay)

        for verg_id in verg_ids:
            hand_url = f"{VLPAR_BASE}/verg/{verg_id}/hand"
            try:
                hand_bytes = _fetch(hand_url, timeout=30)
            except Exception as exc:
                logger.warning("[vlpar_extra] hand %d error: %s", verg_id, exc)
                time.sleep(request_delay)
                continue

            if not hand_bytes:
                time.sleep(request_delay * 0.5)
                continue

            jln_ids = _jln_ids_from_hand_xml(hand_bytes)
            time.sleep(request_delay)

            for jln_id in jln_ids:
                if jln_id in saved_jln:
                    continue

                jln_url = f"{VLPAR_BASE}/jln/{jln_id}"
                try:
                    jln_bytes = _fetch(jln_url, timeout=60)
                except Exception as exc:
                    logger.warning("[vlpar_extra] jln %d fetch error: %s", jln_id, exc)
                    time.sleep(request_delay)
                    continue

                if not jln_bytes or len(jln_bytes) < 50:
                    time.sleep(request_delay * 0.5)
                    continue

                dest = output_dir / f"jln_{jln_id:08d}.xml"
                dest.write_bytes(jln_bytes)
                saved_jln.add(jln_id)
                total_jln += 1
                _log(output_dir, f"{PORTAL_BASE}/nl/journaallijn/{jln_id}")
                time.sleep(request_delay)

        done_months.add(month_key)
        with done_file.open("a", encoding="utf-8") as f:
            f.write(f"{month_key}\n")

    logger.info("[vlpar_extra] Done. %d journaallijn files collected.", total_jln)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Vlaams Parlement journaallijn debate transcripts (CC-BY)"
    )
    parser.add_argument("--output",  required=True, type=Path,
                        help="Output directory for jln_*.xml files")
    parser.add_argument("--delay",   type=float, default=REQUEST_DELAY,
                        help=f"Seconds between requests (default: {REQUEST_DELAY})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log what would be fetched without making any requests")
    args = parser.parse_args()
    download(args.output, request_delay=args.delay, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
