"""
Download DSFSI Mafoko multilingual South African terminology CSVs from GitHub.

The za-mavito repository (Data Science for Social Impact, University of Pretoria)
contains domain-specific terminology glossaries covering South African official
languages, compiled from public South African government terminology resources.

Domains include elections, finance, mathematics, HIV/AIDS, government, and more.
These are valuable for cross-lingual reference and terminology alignment with
Dutch (Afrikaans shares significant vocabulary with Dutch).

Source:  https://github.com/dsfsi/za-mavito
API:     https://api.github.com/repos/dsfsi/za-mavito/contents/data
License: MIT / CC0 (compiled from public SA government terminology)
Country: ZA
Domain:  reference
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

logger = logging.getLogger(__name__)

SOURCE  = "za_mafoko"
LICENSE = "CC0"
COUNTRY = "ZA"
DOMAIN  = "reference"
LANG    = "AF"

_USER_AGENT = (
    "NederCorpus/0.1 (academic research; South African terminology corpus; "
    "github.com/dsfsi/za-mavito)"
)
_DELAY = 0.5

# GitHub Contents API paths to try in order
_GITHUB_API_URLS = [
    "https://api.github.com/repos/dsfsi/za-mavito/contents/data",
    "https://api.github.com/repos/dsfsi/za-mavito/contents/data/glossaries",
]

# Fallback: known CSV files with direct raw URLs
_FALLBACK_CSVS = [
    "https://raw.githubusercontent.com/dsfsi/za-mavito/main/data/elections_glossary.csv",
    "https://raw.githubusercontent.com/dsfsi/za-mavito/main/data/finance_glossary.csv",
    "https://raw.githubusercontent.com/dsfsi/za-mavito/main/data/mathematics_glossary.csv",
    "https://raw.githubusercontent.com/dsfsi/za-mavito/main/data/hiv_aids_glossary.csv",
    "https://raw.githubusercontent.com/dsfsi/za-mavito/main/data/government_glossary.csv",
]

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url: str, retries: int = 3, timeout: int = 30) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 403, 404, 410):
                logger.debug("[za_mafoko] HTTP %d for %s", exc.code, url)
                return None
            if attempt == retries - 1:
                logger.warning("[za_mafoko] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 ** attempt)
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[za_mafoko] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 ** attempt)
    return None


def _log(output_dir: Path, url: str, filename: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "filename":         filename,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _discover_csv_urls() -> list[tuple[str, str]]:
    """
    Try GitHub Contents API to discover CSV files.

    Returns list of (filename, download_url) tuples.
    Falls back to _FALLBACK_CSVS if the API fails or returns nothing.
    """
    for api_url in _GITHUB_API_URLS:
        logger.info("[za_mafoko] Querying GitHub API: %s", api_url)
        data = _fetch(api_url)
        if data is None:
            logger.warning("[za_mafoko] GitHub API unavailable at %s", api_url)
            continue

        try:
            entries = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("[za_mafoko] JSON parse error from %s: %s", api_url, exc)
            continue

        if not isinstance(entries, list):
            # Might be an error object {"message": "...", ...}
            msg = entries.get("message", "") if isinstance(entries, dict) else ""
            logger.warning("[za_mafoko] Unexpected API response at %s: %s", api_url, msg)
            continue

        csv_files = [
            (e["name"], e["download_url"])
            for e in entries
            if isinstance(e, dict)
            and e.get("type") == "file"
            and e.get("name", "").endswith(".csv")
            and e.get("download_url")
        ]

        if csv_files:
            logger.info(
                "[za_mafoko] Found %d CSV file(s) via API at %s",
                len(csv_files), api_url,
            )
            return csv_files

        logger.info("[za_mafoko] No CSV files found at %s, trying next path", api_url)

    # All API paths failed or were empty — use hard-coded fallback list
    logger.warning("[za_mafoko] GitHub API unavailable or empty; using fallback URL list")
    fallback = [
        (url.rsplit("/", 1)[-1], url)
        for url in _FALLBACK_CSVS
    ]
    return fallback


def download(
    output_dir: Path,
    dry_run: bool = False,
    **kwargs,
) -> None:
    """
    Download DSFSI za-mavito terminology CSVs from GitHub.

    Each CSV is saved directly in output_dir using its original filename.
    Discovery is attempted via the GitHub Contents API, with a hard-coded
    fallback list for when the API is rate-limited or unavailable.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[za_mafoko] [dry-run] would download terminology CSVs from GitHub")
        return

    csv_files = _discover_csv_urls()
    if not csv_files:
        logger.error("[za_mafoko] No CSV files discovered — nothing to download")
        return

    total = 0
    skipped = 0

    for filename, download_url in csv_files:
        dest = output_dir / filename
        if dest.exists():
            logger.info("[za_mafoko] Already have %s — skipping", filename)
            skipped += 1
            continue

        logger.info("[za_mafoko] Downloading %s", filename)
        data = _fetch(download_url)
        if data is None:
            logger.warning("[za_mafoko] Could not download %s from %s", filename, download_url)
            continue

        dest.write_bytes(data)
        _log(output_dir, download_url, filename)
        total += 1
        logger.info("[za_mafoko] Saved %s (%d bytes)", filename, len(data))
        time.sleep(_DELAY)

    logger.info(
        "[za_mafoko] Done. %d file(s) downloaded, %d skipped.",
        total, skipped,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download DSFSI za-mavito SA terminology CSVs (CC0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
