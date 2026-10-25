"""
Download PUC (Publieke UitvoeringsCommunicatie) documents as PDFs.

PUC contains policy handbooks, work instructions, and announcements from
Dutch executive agencies (IND, NZa, ILT, SVB, UWV, Belastingdienst, etc.)
published at puc.overheid.nl.

Strategy
--------
1. Fetch the main sitemap index: https://puc.overheid.nl/sitemap.xml
2. For each real agency channel (excluding demo/test), fetch the sub-sitemaps.
3. For each document URL found, derive the PDF download URL and save the PDF.

PDF URL derivation from document sitemap URL:
  Input:  https://puc.overheid.nl/{channel}/doc/PUC_{id}_{ver}/{page}/
  PDF:    https://puc.overheid.nl/PUC/Handlers/PDF.ashx
          ?documentIdentifier=PUC_{id}_{ver}&versie={page}
          &pad=../PDF/PUC_{id}_{ver}_{page}.pdf

License: CC0 (KOOP / overheid.nl open government data)
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE    = "puc"
LICENSE   = "CC0"
COUNTRY   = "NL"
DOMAIN    = "government"
_BASE     = "https://puc.overheid.nl"
_SITEMAP  = f"{_BASE}/sitemap.xml"
_PDF_ASHX = (
    "{base}/PUC/Handlers/PDF.ashx"
    "?documentIdentifier={identifier}&versie={versie}"
    "&pad=../PDF/{identifier}_{versie}.pdf"
)
_USER_AGENT = "NederCorpus/0.1 (academic research; open government data)"
_DELAY      = 0.4   # seconds between HTTP requests

# Channels to skip (test/demo environments)
_SKIP_CHANNELS = {"demo", "test"}

# Regex to parse document URLs from sitemap entries like
#   https://puc.overheid.nl/ind/doc/PUC_1404152_1/1/
_DOC_URL_RE = re.compile(
    r"https://puc\.overheid\.nl/([^/]+)/doc/(PUC_[^/]+)/(\d+)/",
    re.IGNORECASE,
)


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


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410, 500):
                logger.debug("[puc] HTTP %d (no PDF) for %s", exc.code, url)
                return None
            if attempt == retries - 1:
                logger.warning("[puc] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[puc] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _sitemap_locs(xml_bytes: bytes) -> list[str]:
    """Extract all <loc> URLs from a sitemap or sitemapindex XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    # Both <sitemap><loc> and <url><loc> patterns
    locs = [
        el.text.strip()
        for el in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        if el.text and el.text.strip()
    ]
    return locs


def _pdf_url(identifier: str, versie: str) -> str:
    return _PDF_ASHX.format(base=_BASE, identifier=identifier, versie=versie)


def _collect_sub_sitemaps(main_xml: bytes) -> list[str]:
    """
    Return sub-sitemap URLs from the main sitemap index, excluding demo/test channels.
    """
    locs = _sitemap_locs(main_xml)
    result = []
    for loc in locs:
        m = re.search(r"kanaalidentifier=([^&\s]+)", loc)
        if m:
            channel = m.group(1).lower()
            if channel in _SKIP_CHANNELS:
                continue
        result.append(loc)
    return result


def download(
    output_dir: Path,
    max_docs: int = 200_000,
    dry_run: bool = False,
) -> None:
    """
    Download PUC documents as PDFs from all agency channels.

    Phase 1: Read sitemap index → collect sub-sitemap URLs per channel.
    Phase 2: Read each sub-sitemap → collect document URLs.
    Phase 3: For each document URL, derive PDF.ashx URL and download PDF.

    Files are saved as {identifier}_{versie}.pdf.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[puc] [dry-run] skipping")
        return

    # Phase 1 — main sitemap
    logger.info("[puc] Fetching main sitemap")
    main_xml = _fetch(_SITEMAP, timeout=30)
    if not main_xml:
        logger.error("[puc] Could not fetch main sitemap")
        return

    sub_sitemaps = _collect_sub_sitemaps(main_xml)
    logger.info("[puc] Found %d sub-sitemaps", len(sub_sitemaps))

    # Phase 2+3 — iterate sub-sitemaps and download documents
    total = 0
    skipped = 0

    for sm_url in sub_sitemaps:
        if total >= max_docs:
            break

        logger.info("[puc] Sub-sitemap: %s", sm_url)
        sm_xml = _fetch(sm_url, timeout=30)
        if not sm_xml:
            continue
        time.sleep(_DELAY)

        doc_locs = _sitemap_locs(sm_xml)
        logger.info("[puc] %d URLs in sub-sitemap", len(doc_locs))

        for doc_url in doc_locs:
            if total >= max_docs:
                break

            m = _DOC_URL_RE.match(doc_url)
            if not m:
                continue

            _channel, identifier, versie = m.group(1), m.group(2), m.group(3)

            # Filename: PUC_1404152_1_1.pdf
            fname = f"{identifier}_{versie}.pdf"
            dest  = output_dir / fname

            if dest.exists():
                skipped += 1
                continue

            pdf_url = _pdf_url(identifier, versie)
            data    = _fetch(pdf_url, timeout=60)
            if not data:
                logger.debug("[puc] No PDF for %s v%s", identifier, versie)
                time.sleep(_DELAY * 0.5)
                continue

            # Sanity: must start with %PDF
            if not data[:4] == b"%PDF":
                logger.debug("[puc] Not a PDF for %s", fname)
                time.sleep(_DELAY * 0.5)
                continue

            dest.write_bytes(data)
            _log(output_dir, doc_url, identifier)
            total += 1
            logger.info("[puc] %s (%d KB)", fname, len(data) // 1024)
            time.sleep(_DELAY)

    logger.info("[puc] Done. %d PDFs downloaded, %d skipped.", total, skipped)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download PUC uitvoeringsinformatie PDFs (CC0) from puc.overheid.nl"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=200_000)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
