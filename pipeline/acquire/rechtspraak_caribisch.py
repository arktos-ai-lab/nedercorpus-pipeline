"""
Download Caribbean court decisions from the Rechtspraak.nl Open Data API.

The Gemeenschappelijk Hof van Justitie (Joint Court of Justice) serves
Aruba, Curaçao, Sint Maarten, and the BES islands (Bonaire, Sint Eustatius,
Saba). Decisions are published on rechtspraak.nl in the same XML format
as Dutch mainland decisions, but with Caribbean ECLI prefixes.

ECLI court prefixes covered:
  OGHACMB  — Hof van Justitie (Court of Appeal) for Aruba, Curaçao, Sint Maarten, BES
  OGEABES  — Gerecht in eerste aanleg BES (Bonaire, Sint Eustatius, Saba)
  OGEAC    — Gerecht in eerste aanleg Curaçao
  OGEAA    — Gerecht in eerste aanleg Aruba
  OGEAM    — Gerecht in eerste aanleg Sint Maarten

Discovery strategy:
  The rechtspraak.nl subject= filter does not index Caribbean courts (returns 0).
  We scan the generic feed year by year (2000–present) and filter ECLIs locally
  by Caribbean court prefix. Direct content fetch per ECLI works fine.

License: Public Domain
  Rechtspraak.nl publishes decisions under its open data policy, consistent
  with Dutch government open data (CC0 equivalent). Court decisions are
  explicitly excluded from copyright under Article 11 of the Dutch Copyright Act.

Language variant: NL (Caribbean Dutch legal register; same as mainland Dutch
  formal legal text with occasional Caribbean administrative terms)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

OPEN_DATA_BASE = "https://data.rechtspraak.nl/uitspraken/zoeken"
CONTENT_API    = "https://data.rechtspraak.nl/uitspraken/content"
SOURCE         = "rechtspraak_caribisch"
LICENSE        = "PD"
COUNTRY        = "NL"
DOMAIN         = "legal"

NS_ATOM = "http://www.w3.org/2005/Atom"

# Caribbean court ECLI prefix → human-readable label
CARIBBEAN_COURTS: list[tuple[str, str]] = [
    ("OGHACMB", "Gemeenschappelijk Hof van Justitie (Caribbean Court of Appeal)"),
    ("OGEABES", "Gerecht in eerste aanleg BES (Bonaire/Sint Eustatius/Saba)"),
    ("OGEAC",   "Gerecht in eerste aanleg Curaçao"),
    ("OGEAA",   "Gerecht in eerste aanleg Aruba"),
    ("OGEAM",   "Gerecht in eerste aanleg Sint Maarten"),
]

# Set of all Caribbean ECLI prefixes for fast local filtering
_CARIBBEAN_PREFIXES = frozenset(p for p, _ in CARIBBEAN_COURTS)

# Regex to extract ECLI from generic feed results
_ECLI_RE = re.compile(r"ECLI:NL:([A-Z0-9]+):\d{4}:[A-Z0-9.]+")


def _log(output_dir: Path, url: str, sub: str = "") -> None:
    entry = {
        "source": f"{SOURCE}:{sub}" if sub else SOURCE,
        "url": url,
        "license": LICENSE,
        "country": COUNTRY,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _feed_url(offset: int, page_size: int, start_date: str) -> str:
    """Build a feed URL for all decisions modified on/after start_date."""
    return (
        f"{OPEN_DATA_BASE}"
        f"?return=DOC&max={page_size}&from={offset}"
        f"&type=uitspraak&modified={start_date}"
    )


def _parse_feed(feed_bytes: bytes) -> list[str]:
    """Extract ECLI IDs from a rechtspraak.nl Atom feed response."""
    eclis: list[str] = []

    try:
        root = ET.fromstring(feed_bytes)
    except ET.ParseError as exc:
        logger.warning("Feed XML parse error: %s", exc)
        for m in _ECLI_RE.finditer(feed_bytes.decode("utf-8", errors="replace")):
            eclis.append(m.group(0))
        return eclis

    # Standard Atom namespace
    for entry in root.findall(f"{{{NS_ATOM}}}entry"):
        id_el = entry.find(f"{{{NS_ATOM}}}id")
        if id_el is not None and id_el.text:
            eclis.append(id_el.text.strip())

    if not eclis:
        for el in root.iter():
            if el.tag.split("}")[-1] == "id" and el.text and el.text.strip().startswith("ECLI:"):
                eclis.append(el.text.strip())

    if not eclis:
        for m in _ECLI_RE.finditer(feed_bytes.decode("utf-8", errors="replace")):
            candidate = m.group(0)
            if candidate not in eclis:
                eclis.append(candidate)

    return eclis


def _is_caribbean(ecli: str) -> bool:
    """Return True if the ECLI belongs to a Caribbean court."""
    m = _ECLI_RE.match(ecli)
    if not m:
        return False
    return m.group(1) in _CARIBBEAN_PREFIXES


def collect_caribbean_eclis(
    output_dir: Path,
    start_year: int = 2000,
    page_size: int = 1000,
) -> list[str]:
    """
    Scan the generic rechtspraak.nl feed (single pass, paginated) and collect Caribbean ECLIs.

    The subject= filter returns 0 for Caribbean courts, so we pull all decisions
    modified since start_year and filter locally by ECLI court prefix.

    Progress is checkpointed to {output_dir}/scan_progress.json so partial runs resume.
    Total feed size is ~2M decisions (~2000 pages at max=1000). Runtime ~30-50 min.
    """
    progress_file = output_dir / "scan_progress.json"
    start_date = f"{start_year}-01-01"
    all_caribbean: list[str] = []
    seen: set[str] = set()
    offset = 0

    # Resume from checkpoint
    if progress_file.exists():
        try:
            state = json.loads(progress_file.read_text(encoding="utf-8"))
            if state.get("start_date") == start_date:
                offset = state.get("offset", 0)
                saved_eclis = state.get("eclis", [])
                all_caribbean = saved_eclis
                seen = set(saved_eclis)
                logger.info(
                    "[rechtspraak_caribisch] Resuming scan at offset=%d (%d ECLIs so far)",
                    offset, len(all_caribbean),
                )
        except Exception:
            pass

    page = offset // page_size
    while True:
        url = _feed_url(offset, page_size, start_date)
        logger.debug("[rechtspraak_caribisch] scan offset=%d", offset)

        try:
            feed_data = _fetch(url, timeout=60)
        except Exception as exc:
            logger.warning("[rechtspraak_caribisch] Feed fetch offset=%d: %s", offset, exc)
            break

        eclis = _parse_feed(feed_data)
        if not eclis:
            logger.info("[rechtspraak_caribisch] Feed exhausted at offset=%d", offset)
            break

        new_count = 0
        for ecli in eclis:
            if _is_caribbean(ecli) and ecli not in seen:
                seen.add(ecli)
                all_caribbean.append(ecli)
                new_count += 1

        offset += len(eclis)
        page += 1

        if page % 100 == 0:
            logger.info(
                "[rechtspraak_caribisch] Scanned %d pages (%d decisions), %d Caribbean ECLIs",
                page, offset, len(all_caribbean),
            )
            # Save checkpoint
            progress_file.write_text(
                json.dumps({"start_date": start_date, "offset": offset, "eclis": all_caribbean},
                           ensure_ascii=False),
                encoding="utf-8",
            )

        if len(eclis) < page_size:
            break  # Last page

        time.sleep(0.25)

    # Save final checkpoint
    progress_file.write_text(
        json.dumps({"start_date": start_date, "offset": offset, "eclis": all_caribbean},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return all_caribbean


def download(
    output_dir: Path,
    courts: list[str] | None = None,
    start_year: int = 2000,
    dry_run: bool = False,
) -> None:
    """
    Download Caribbean court decisions by scanning the generic feed year by year.

    Phase 1: collect Caribbean ECLIs from yearly feed scans (local filtering).
    Phase 2: download individual decision XML files for each ECLI.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = set(courts) if courts else _CARIBBEAN_PREFIXES

    if dry_run:
        logger.info("[rechtspraak_caribisch] [dry-run] would scan feed years %d–present", start_year)
        return

    decisions_dir = output_dir / "decisions"
    decisions_dir.mkdir(exist_ok=True)

    # Phase 1: collect ECLIs
    logger.info("[rechtspraak_caribisch] Phase 1: scanning feed from %d-01-01 …", start_year)
    all_eclis = collect_caribbean_eclis(output_dir, start_year=start_year)

    # Filter to selected courts
    if courts:
        all_eclis = [e for e in all_eclis if _is_caribbean(e) and _ECLI_RE.match(e) and _ECLI_RE.match(e).group(1) in selected]

    logger.info("[rechtspraak_caribisch] Phase 1 done: %d Caribbean ECLIs found", len(all_eclis))
    _log(output_dir, OPEN_DATA_BASE, "scan")

    # Phase 2: download individual decision XMLs
    logger.info("[rechtspraak_caribisch] Phase 2: downloading %d decisions …", len(all_eclis))
    downloaded = 0
    for ecli in all_eclis:
        safe_name = ecli.replace(":", "_").replace("/", "_")
        dest = decisions_dir / f"{safe_name}.xml"
        if dest.exists():
            continue

        content_url = f"{CONTENT_API}?id={urllib.parse.quote(ecli)}"
        try:
            xml_data = _fetch(content_url, timeout=30)
            if len(xml_data) < 100:
                continue
            dest.write_bytes(xml_data)
            downloaded += 1
            logger.debug("[rechtspraak_caribisch] Saved %s", safe_name)
        except Exception as exc:
            logger.warning("[rechtspraak_caribisch] Failed %s: %s", ecli, exc)

        time.sleep(0.2)

    logger.info("[rechtspraak_caribisch] Phase 2 done: %d new XMLs downloaded", downloaded)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Caribbean court decisions from rechtspraak.nl (public domain)"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--courts",      nargs="+", default=None,
                        choices=[c for c, _ in CARIBBEAN_COURTS],
                        help="Specific courts to download (default: all)")
    parser.add_argument("--start-year",  type=int, default=2000)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    download(args.output, courts=args.courts, start_year=args.start_year, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
