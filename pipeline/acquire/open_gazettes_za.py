"""
Download South African Government Gazettes as PDFs from the gazettes.africa API.

Open Gazettes South Africa (opengazettes.org.za / gazettes.africa) indexes
national and provincial South African Government Gazettes.  These are Public
Domain under the South African Copyright Act 1978 (government publications are
not subject to copyright protection).

The gazettes are bilingual Afrikaans/English — valuable for both languages in
the NederCorpus context given the close relationship between Afrikaans and Dutch.

STATUS (2026-04-23): BOTH APIs ARE DOWN.
  - gazettes.africa/api/v1/ → HTTP 404 (API removed)
  - opengazettes.org.za CKAN → HTTP 404 (site restructured)
  The acquirer logs a warning and exits cleanly. Re-investigate when APIs recover
  or find a replacement source (e.g. direct gpwonline.co.za scrape).

Strategy
--------
1. Primary: gazettes.africa REST API
   GET https://gazettes.africa/api/v1/publications/?format=json&country=ZA
       &page_size=100&page=N

2. Fallback: opengazettes.org.za CKAN API
   GET https://opengazettes.org.za/api/3/action/package_search?q=&rows=1000&start=N

License: PD (South African government publications, Copyright Act 1978 s.12)
Country: ZA
Domain:  government
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "open_gazettes_za"
LICENSE = "PD"
COUNTRY = "ZA"
DOMAIN  = "government"
LANG    = "AF"   # Afrikaans primary; English secondary (bilingual docs)

# gazettes.africa/api/v1/ is dead (404 since ~2026-04).
# opengazettes.org.za CKAN API is also dead.
# The archive itself is healthy: stream the JSONL index instead.
_ARCHIVE_INDEX = (
    "https://archive.opengazettes.org.za/index/gazette-index-latest.jsonlines"
)

_USER_AGENT = (
    "NederCorpus/0.1 (academic research; South African PD government gazettes; "
    "opengazettes.org.za CC0/PD)"
)
_DELAY      = 0.3    # seconds between requests
_PDF_MAGIC  = b"%PDF"

# Characters that are safe in filenames
_SAFE_RE = re.compile(r"[^\w\-]+")


def _safe(text: str, max_len: int = 60) -> str:
    """Collapse runs of unsafe characters to underscores; truncate."""
    return _SAFE_RE.sub("_", text).strip("_")[:max_len]


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                logger.debug("[open_gazettes_za] HTTP %d for %s", exc.code, url)
                return None
            if attempt == retries - 1:
                logger.warning("[open_gazettes_za] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 ** attempt)
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[open_gazettes_za] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 ** attempt)
    return None


def _log(output_dir: Path, url: str, identifier: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "identifier":       identifier,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_index(output_dir: Path, record: dict) -> None:
    """Append a metadata record to gazettes_index.jsonl."""
    with (output_dir / "gazettes_index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Archive index strategy (replaces dead gazettes.africa API + CKAN)
# ---------------------------------------------------------------------------

def _iter_archive_index(max_docs: int) -> list[dict]:
    """
    Stream the JSONL gazette index from archive.opengazettes.org.za.

    42,254 records; each has archive_url, unique_id, publication_date,
    jurisdiction_code, publication_title, pagecount.
    """
    import io as _io
    results: list[dict] = []
    logger.info("[open_gazettes_za] Streaming archive index …")
    data = _fetch(_ARCHIVE_INDEX, timeout=120)
    if data is None:
        logger.error("[open_gazettes_za] Archive index unreachable: %s", _ARCHIVE_INDEX)
        return results

    for raw_line in _io.BytesIO(data):
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        file_url = rec.get("archive_url", "")
        if not file_url:
            continue

        results.append({
            "url":      file_url,
            "label":    rec.get("issue_title", rec.get("publication_title", "")),
            "date":     rec.get("publication_date", ""),
            "type":     rec.get("publication_title", "gazette"),
            "country":  "ZA",
            "file_url": file_url,
            "unique_id": rec.get("unique_id", ""),
            "jurisdiction": rec.get("jurisdiction_code", "ZA"),
            "pagecount": rec.get("pagecount", 0),
        })
        if len(results) >= max_docs:
            break

    logger.info("[open_gazettes_za] Archive index: %d records loaded", len(results))
    return results


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    max_docs: int = 5_000,
    dry_run: bool = False,
    **kwargs,
) -> None:
    """
    Download South African Government Gazette PDFs.

    Uses the gazettes.africa API as the primary source; falls back to the
    opengazettes.org.za CKAN API if the primary returns no results.

    Each PDF is saved as ``gazette_{date}_{label}.pdf``.
    Metadata is appended to ``gazettes_index.jsonl``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[open_gazettes_za] [dry-run] skipping")
        return

    records = _iter_archive_index(max_docs)

    if not records:
        logger.error(
            "[open_gazettes_za] No gazette records found — "
            "check network access to archive.opengazettes.org.za"
        )
        return

    logger.info("[open_gazettes_za] %d gazette records to process", len(records))

    total   = 0
    skipped = 0

    for record in records:
        if total >= max_docs:
            break

        file_url = record.get("file_url", "")
        label    = record.get("label", "")
        date_str = record.get("date", "")

        if not file_url:
            continue

        # Build a safe filename
        safe_date  = _safe(date_str[:10]) if date_str else "nodate"
        safe_label = _safe(label) if label else _safe(file_url.rsplit("/", 1)[-1])
        fname      = f"gazette_{safe_date}_{safe_label}.pdf"
        dest       = output_dir / fname

        if dest.exists():
            skipped += 1
            continue

        logger.info("[open_gazettes_za] Downloading %s", fname)
        data = _fetch(file_url, timeout=120)
        if not data:
            logger.debug("[open_gazettes_za] No data for %s", file_url)
            time.sleep(_DELAY * 0.5)
            continue

        # Verify PDF signature
        if data[:4] != _PDF_MAGIC:
            logger.debug(
                "[open_gazettes_za] Not a PDF (magic=%r) for %s", data[:4], file_url
            )
            time.sleep(_DELAY * 0.5)
            continue

        dest.write_bytes(data)

        # Write metadata index entry
        _write_index(output_dir, {
            "filename": fname,
            "url":      record.get("url", file_url),
            "file_url": file_url,
            "label":    label,
            "date":     date_str,
            "type":     record.get("type", ""),
            "country":  record.get("country", "ZA"),
        })

        _log(output_dir, file_url, fname)
        total += 1
        logger.info("[open_gazettes_za] Saved %s (%d KB)", fname, len(data) // 1024)
        time.sleep(_DELAY)

    logger.info(
        "[open_gazettes_za] Done. %d PDFs downloaded, %d skipped.",
        total, skipped,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Download South African Government Gazette PDFs (PD) "
            "from gazettes.africa / opengazettes.org.za"
        )
    )
    parser.add_argument("--output",   required=True, type=Path,
                        help="Directory to save PDFs and metadata")
    parser.add_argument("--max-docs", type=int, default=5_000,
                        help="Maximum number of PDFs to download (default: 5000)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Log actions without downloading anything")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
