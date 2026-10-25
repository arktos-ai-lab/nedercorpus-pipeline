"""
Download court decisions from Rechtspraak.nl Open Data (public domain).

Three-phase acquisition:

  Phase 0 — Dataverse bulk download (historical, 1905–2021)
    GET https://dataverse.nl/api/datasets/:persistentId/?persistentId=doi:10.34894/H4QURK
    Downloads monthly ZIP archives (YYYYMM.zip), extracts all .xml files to
    decisions/. Sentinel files {yyyymm}_done.flag track completed months.

  Phase 1 — Atom feed pages (ECLI index)
    GET data.rechtspraak.nl/uitspraken/zoeken → Atom XML feed of ECLI IDs.
    Saved as: raw/rechtspraak/feed_page_NNNN.xml

  Phase 2 — Incremental API for 2022+ decisions
    Uses modified-date filter to avoid re-downloading historical data.
    Single-threaded (10 req/s cap). Skips ECLIs already present in decisions/.

NOTE: The old Phase 2 (ThreadPoolExecutor per-ECLI download) is DISABLED.
      The per-decision ECLI content API now returns blanket 403 rate-limits
      for bulk access. Use Dataverse for historical data (pre-2022) instead.

The parse stage (sru.py / parse_rechtspraak_files) reads the individual
decision XMLs from decisions/ to extract text and metadata.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlencode

# Note: threading / concurrent.futures are no longer used — the old
# ThreadPoolExecutor-based Phase 2 has been disabled (blanket 403 from API).

logger = logging.getLogger(__name__)

OPEN_DATA_BASE   = "https://data.rechtspraak.nl/uitspraken/zoeken"
CONTENT_API      = "https://data.rechtspraak.nl/uitspraken/content"
DATAVERSE_API    = "https://dataverse.nl/api/datasets/:persistentId/"
DATAVERSE_DOI    = "doi:10.34894/H4QURK"
DATAVERSE_FILE   = "https://dataverse.nl/api/access/datafile/{file_id}"
SOURCE   = "rechtspraak"
LICENSE  = "CC0"          # Dataverse dataset is CC0; original decisions are PD
COUNTRY  = "NL"
DOMAIN   = "legal"

# 2022-01-01 — decisions from this date onward are fetched via incremental API
INCREMENTAL_FROM = "2022-01-01T00:00:00%2B01:00"

NS_ATOM = "http://www.w3.org/2005/Atom"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _log_entry(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    log_path = output_dir / "acquisition_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("Retry %d after error on %s: %s (wait %.1fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _fetch_stream(url: str, dest_path: Path, timeout: int = 300, chunk: int = 1 << 20) -> None:
    """Stream a large file to dest_path without loading it all into memory."""
    headers = {"User-Agent": "NederCorpus/0.1 (academic research; public data)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with dest_path.open("wb") as fh:
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                fh.write(block)


# ---------------------------------------------------------------------------
# Phase 0: Dataverse bulk download (1905–2021)
# ---------------------------------------------------------------------------

def download_dataverse(
    output_dir: Path,
    decisions_dir: Path,
    dry_run: bool = False,
) -> int:
    """
    Phase 0: download all monthly ZIP archives from Utrecht University
    Dataverse (doi:10.34894/H4QURK) and extract decision XMLs.

    Each ZIP is named YYYYMM.zip and may be several hundred MB.
    A sentinel file {yyyymm}_done.flag is written after successful
    extraction so the month is skipped on resume runs.

    Returns the number of XML files newly extracted.
    """
    decisions_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 1: fetch the dataset metadata to get the file listing ---
    meta_url = f"{DATAVERSE_API}?persistentId={DATAVERSE_DOI}"
    logger.info("[rechtspraak/dataverse] Fetching dataset metadata: %s", meta_url)

    if dry_run:
        logger.info("[dry-run] Would fetch Dataverse metadata from %s", meta_url)
        return 0

    try:
        meta_bytes = _fetch(meta_url, timeout=60)
    except Exception as exc:
        logger.error("[rechtspraak/dataverse] Failed to fetch metadata: %s", exc)
        return 0

    try:
        meta = json.loads(meta_bytes)
    except json.JSONDecodeError as exc:
        logger.error("[rechtspraak/dataverse] Metadata JSON parse error: %s", exc)
        return 0

    files = (
        meta.get("data", {})
            .get("latestVersion", {})
            .get("files", [])
    )
    if not files:
        logger.error("[rechtspraak/dataverse] No files found in dataset metadata.")
        return 0

    logger.info("[rechtspraak/dataverse] Found %d files in dataset.", len(files))

    total_extracted = 0

    for file_obj in files:
        data_file = file_obj.get("dataFile", {})
        file_id   = data_file.get("id")
        filename  = data_file.get("filename", "")
        filesize  = data_file.get("filesize", 0)

        if not file_id or not filename.endswith(".zip"):
            logger.debug("[rechtspraak/dataverse] Skipping non-ZIP entry: %s", filename)
            continue

        # Derive the month token (stem of the filename, e.g. "202101")
        yyyymm = Path(filename).stem

        # Check sentinel — skip if already extracted
        sentinel = decisions_dir / f"{yyyymm}_done.flag"
        if sentinel.exists():
            logger.debug("[rechtspraak/dataverse] %s already extracted — skipping.", filename)
            continue

        dl_url = DATAVERSE_FILE.format(file_id=file_id)
        logger.info(
            "[rechtspraak/dataverse] Downloading %s (%.1f MB) from %s",
            filename, filesize / 1_048_576, dl_url,
        )

        # Stream ZIP to a temp file, then extract
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            _fetch_stream(dl_url, tmp_path, timeout=600)
            _log_entry(output_dir, dl_url)

            logger.info("[rechtspraak/dataverse] Extracting %s …", filename)
            n_extracted = 0
            with zipfile.ZipFile(tmp_path, "r") as zf:
                for member in zf.namelist():
                    if not member.lower().endswith(".xml"):
                        continue
                    # Flatten: write directly into decisions_dir
                    dest_name = Path(member).name
                    dest      = decisions_dir / dest_name
                    if dest.exists():
                        continue
                    data = zf.read(member)
                    dest.write_bytes(data)
                    n_extracted += 1

            # Write sentinel
            sentinel.write_text(
                f"extracted {n_extracted} files from {filename} on "
                f"{datetime.date.today().isoformat()}\n",
                encoding="utf-8",
            )
            total_extracted += n_extracted
            logger.info(
                "[rechtspraak/dataverse] %s → extracted %d XML files (total so far: %d).",
                filename, n_extracted, total_extracted,
            )

        except Exception as exc:
            logger.error("[rechtspraak/dataverse] Error processing %s: %s", filename, exc)
            # Do NOT write sentinel — let it retry next run
        finally:
            # Always clean up the ZIP to conserve disk space
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

        # Polite delay between ZIPs (large files)
        time.sleep(2.0)

    logger.info(
        "[rechtspraak/dataverse] Phase 0 complete. Total XML files extracted: %d.",
        total_extracted,
    )
    return total_extracted


# ---------------------------------------------------------------------------
# Phase 1: Atom feed pages
# ---------------------------------------------------------------------------

def download_feed_pages(
    output_dir: Path,
    max_pages: int = 10,
    page_size: int = 1000,
    dry_run: bool = False,
) -> list[Path]:
    """
    Fetch Atom feed pages from the Rechtspraak Open Data search API.

    Each page contains up to `page_size` ECLI IDs.  The default of
    10 pages × 1000 = 10 000 decisions; set max_pages=0 for unlimited
    (the full corpus is ~4 million decisions — plan for hours of download).

    Returns list of saved feed page paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for page in range(max_pages if max_pages > 0 else 10_000):
        params = {
            "return": "DOC",
            "max":    page_size,
            "from":   page * page_size,
            "type":   "uitspraak",
        }
        url = f"{OPEN_DATA_BASE}?{urlencode(params)}"
        logger.info("[rechtspraak] feed page %d — %s", page, url)

        if dry_run:
            logger.info("[dry-run] stopping after first page announcement")
            break

        try:
            data = _fetch(url)
        except Exception as exc:
            logger.error("Feed page %d failed: %s", page, exc)
            break

        dest = output_dir / f"feed_page_{page:04d}.xml"
        dest.write_bytes(data)
        _log_entry(output_dir, url)
        logger.info("  Saved %s (%d bytes)", dest.name, len(data))
        saved.append(dest)

        # Check whether the feed returned any entries; if empty we're done
        try:
            root = ET.fromstring(data)
            entries = root.findall(f"{{{NS_ATOM}}}entry")
            if not entries:
                logger.info("  Empty page — feed exhausted.")
                break
        except ET.ParseError:
            logger.warning("  Could not parse feed page %d — stopping.", page)
            break

        time.sleep(1.0)  # be polite

    return saved


# ---------------------------------------------------------------------------
# Phase 2 (incremental): 2022+ decisions via modified-date API
# ---------------------------------------------------------------------------

def _eclis_from_feed(xml_bytes: bytes) -> list[str]:
    """Parse an Atom feed page and return the list of ECLI identifiers."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("Feed parse error: %s", exc)
        return []

    eclis: list[str] = []
    for entry in root.findall(f"{{{NS_ATOM}}}entry"):
        id_el = entry.find(f"{{{NS_ATOM}}}id")
        if id_el is not None and id_el.text:
            eclis.append(id_el.text.strip())
    return eclis


def _ecli_to_filename(ecli: str) -> str:
    """ECLI:NL:HR:2023:1 → ECLI_NL_HR_2023_1.xml"""
    return ecli.replace(":", "_") + ".xml"


def download_incremental(
    output_dir: Path,
    decisions_dir: Path,
    dry_run: bool = False,
    delay: float = 0.1,
    page_size: int = 1000,
) -> int:
    """
    Phase 2 (incremental): fetch 2022+ decisions from the Rechtspraak search
    API using a `modified` date filter.

    Single-threaded to respect the ~10 req/s rate limit (100 ms delay).
    Skips decisions whose XML file already exists in decisions_dir.

    Returns the number of decisions newly downloaded.
    """
    decisions_dir.mkdir(parents=True, exist_ok=True)

    total_new = 0
    offset = 0

    while True:
        params = {
            "return":   "DOC",
            "max":      page_size,
            "modified": INCREMENTAL_FROM,
            "type":     "uitspraak",
            "sort":     "ASC",
            "from":     offset,
        }
        # urlencode will percent-encode the '+' in the timestamp; re-insert
        # the already-encoded value by building the URL manually for modified.
        base_params = urlencode({k: v for k, v in params.items() if k != "modified"})
        url = (
            f"{OPEN_DATA_BASE}?{base_params}"
            f"&modified={INCREMENTAL_FROM}"
        )

        logger.info("[rechtspraak/incremental] Fetching offset=%d — %s", offset, url)

        if dry_run:
            logger.info("[dry-run] Would fetch incremental page at offset %d", offset)
            break

        try:
            data = _fetch(url)
        except Exception as exc:
            logger.error("[rechtspraak/incremental] Feed page at offset %d failed: %s", offset, exc)
            break

        eclis = _eclis_from_feed(data)
        logger.info("[rechtspraak/incremental] offset=%d → %d ECLIs", offset, len(eclis))

        if not eclis:
            logger.info("[rechtspraak/incremental] No more results — done.")
            break

        for ecli in eclis:
            if not ecli.startswith("ECLI:"):
                continue
            dest = decisions_dir / _ecli_to_filename(ecli)
            if dest.exists():
                continue

            content_url = f"{CONTENT_API}?id={ecli}"
            try:
                xml_bytes = _fetch(content_url)
                dest.write_bytes(xml_bytes)
                total_new += 1
                _log_entry(output_dir, content_url)
                logger.debug("[rechtspraak/incremental] Saved %s", dest.name)
            except Exception as exc:
                logger.warning("[rechtspraak/incremental] Failed %s: %s", ecli, exc)

            time.sleep(delay)  # ~10 req/s

        offset += page_size
        time.sleep(1.0)  # polite gap between search-API pages

    logger.info(
        "[rechtspraak/incremental] Phase 2 complete. Newly downloaded: %d.", total_new
    )
    return total_new


# ---------------------------------------------------------------------------
# DISABLED: old Phase 2 (ThreadPoolExecutor per-ECLI download)
# ---------------------------------------------------------------------------

def download_decisions(
    feed_dir: Path,
    decisions_dir: Path,
    dry_run: bool = False,
    delay: float = 0.1,
    workers: int = 10,
) -> int:
    """
    DISABLED — the per-decision ECLI content API now returns blanket 403
    rate-limit responses for bulk access.

    Use download_dataverse() for historical data (pre-2022) and
    download_incremental() for 2022+ decisions instead.
    """
    logger.warning(
        "[rechtspraak] download_decisions() (old Phase 2 / ThreadPoolExecutor) is DISABLED. "
        "Use Dataverse download for historical data and download_incremental() for 2022+ data."
    )
    return 0


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    max_pages: int = 10,
    dry_run: bool = False,
    skip_decisions: bool = False,
    skip_dataverse: bool = False,
    skip_incremental: bool = False,
) -> None:
    """
    Full three-phase download.

    Phase 1 — Atom feed pages (ECLI index).
    Phase 0 — Dataverse bulk ZIPs (historical, 1905–2021).
    Phase 2 — Incremental API for 2022+ decisions (single-threaded).

    Args:
        max_pages:        Feed pages to fetch (1 page = 1000 ECLIs).
                          Set to 0 for unlimited (full corpus, hours).
        skip_decisions:   Skip all decision download phases (feed index only).
        skip_dataverse:   Skip Phase 0 (Dataverse bulk download).
        skip_incremental: Skip Phase 2 (incremental 2022+ API).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    decisions_dir = output_dir / "decisions"

    # Phase 1: Atom feed index (provides ECLI metadata; used by parser)
    download_feed_pages(output_dir, max_pages=max_pages, dry_run=dry_run)

    if skip_decisions:
        return

    # Phase 0: Dataverse bulk download (historical 1905–2021)
    if not skip_dataverse:
        download_dataverse(output_dir, decisions_dir, dry_run=dry_run)
    else:
        logger.info("[rechtspraak] Skipping Phase 0 (Dataverse bulk download).")

    # Phase 2: incremental 2022+ decisions
    if not skip_incremental:
        download_incremental(output_dir, decisions_dir, dry_run=dry_run)
    else:
        logger.info("[rechtspraak] Skipping Phase 2 (incremental 2022+ API).")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Rechtspraak open data (3-phase: feed + Dataverse + incremental)"
    )
    parser.add_argument("--output",             required=True, type=Path)
    parser.add_argument("--max-pages",          type=int, default=10,
                        help="Feed pages (1 page = 1000 ECLIs). 0 = unlimited.")
    parser.add_argument("--skip-decisions",     action="store_true",
                        help="Only download feed index, skip all decision XMLs.")
    parser.add_argument("--skip-dataverse",     action="store_true",
                        help="Skip Phase 0 (Dataverse bulk ZIPs).")
    parser.add_argument("--skip-incremental",   action="store_true",
                        help="Skip Phase 2 (incremental 2022+ API).")
    parser.add_argument("--dry-run",            action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
        skip_decisions=args.skip_decisions,
        skip_dataverse=args.skip_dataverse,
        skip_incremental=args.skip_incremental,
    )


if __name__ == "__main__":
    main()
