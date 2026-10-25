"""
Download Surinamese Dutch-language government publications.

Sources (all public domain under Surinamese copyright law):

  1. Staatsblad van de Republiek Suriname (official gazette)
     https://gov.sr/staatsblad/
     Also archived at: https://www.loc.gov/item/77640966/ (Library of Congress)

  2. De Nationale Assemblée (DNA) — parliamentary proceedings and legislation
     https://www.dna.sr/

  3. Rechtspraak Suriname — court rulings
     https://rechtspraak.sr/

  4. National Archives Suriname via Openarchieven (CC0)
     https://www.openarchieven.nl/datasets/nas

Legal basis:
  Surinamese copyright law explicitly excludes:
  - General regulations issued by public authorities
  - Judgments and administrative decisions
  These are inherently public domain and freely reusable.
  Source: Wikimedia Commons copyright rules by territory — Suriname
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE = "suriname"
LICENSE = "PD"
COUNTRY = "SR"

HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic research; public data; +https://github.com/nedercorpus)"
}


def _log(output_dir: Path, url: str, sub_source: str) -> None:
    entry = {
        "source": f"{SOURCE}:{sub_source}",
        "url": url,
        "license": LICENSE,
        "country": COUNTRY,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Source 1: Staatsblad
# ---------------------------------------------------------------------------

STAATSBLAD_BASE = "https://gov.sr/staatsblad/"

def download_staatsblad(
    output_dir: Path,
    year_from: int = 1975,   # Independence year
    year_to: int | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download Staatsblad van de Republiek Suriname.

    The gov.sr site lists Staatsblad issues by year as PDF/HTML.
    We iterate year pages and save all linked documents.
    """
    year_to = year_to or datetime.date.today().year
    dest = output_dir / "staatsblad"
    dest.mkdir(parents=True, exist_ok=True)

    for year in range(year_from, year_to + 1):
        url = f"{STAATSBLAD_BASE}{year}/"
        logger.info("[staatsblad] year=%d  %s", year, url)

        if dry_run:
            logger.info("[dry-run] skipping")
            continue

        try:
            html = _fetch(url).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Failed year %d: %s", year, exc)
            time.sleep(1.0)
            continue

        # Save the index page for this year
        index_file = dest / f"staatsblad_{year}_index.html"
        index_file.write_text(html, encoding="utf-8")
        _log(output_dir, url, "staatsblad")

        # Extract and follow PDF/document links
        doc_links = re.findall(r'href=["\']([^"\']*\.pdf)["\']', html, re.IGNORECASE)
        for link in doc_links:
            doc_url = urllib.parse.urljoin(url, link)
            doc_name = Path(urllib.parse.urlparse(doc_url).path).name
            doc_dest = dest / str(year) / doc_name
            doc_dest.parent.mkdir(parents=True, exist_ok=True)

            if doc_dest.exists():
                logger.debug("Already downloaded: %s", doc_name)
                continue

            try:
                doc_data = _fetch(doc_url)
                doc_dest.write_bytes(doc_data)
                doc_dest.with_name(doc_dest.name + ".url").write_text(doc_url, encoding="utf-8")
                _log(output_dir, doc_url, "staatsblad:pdf")
                logger.info("  Saved %s/%s (%d bytes)", year, doc_name, len(doc_data))
            except Exception as exc:
                logger.warning("  Failed %s: %s", doc_url, exc)

            time.sleep(0.5)

        time.sleep(1.0)


# ---------------------------------------------------------------------------
# Source 2: De Nationale Assemblée (DNA)
# ---------------------------------------------------------------------------

DNA_BASE = "https://www.dna.sr"
DNA_SECTIONS = [
    "/wetgeving/",         # Legislation
    "/documenten/",        # Documents
    "/handelingen/",       # Proceedings/debates
]


def download_dna(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download parliamentary proceedings and legislation from dna.sr.

    Fetches index pages for each section and saves linked documents.
    """
    dest = output_dir / "dna"
    dest.mkdir(parents=True, exist_ok=True)

    for section in DNA_SECTIONS:
        section_name = section.strip("/")
        url = f"{DNA_BASE}{section}"
        logger.info("[dna] section=%s  %s", section_name, url)

        if dry_run:
            logger.info("[dry-run] skipping")
            continue

        try:
            html = _fetch(url).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Failed section %s: %s", section, exc)
            continue

        section_dir = dest / section_name
        section_dir.mkdir(parents=True, exist_ok=True)
        index_file = section_dir / "index.html"
        index_file.write_text(html, encoding="utf-8")
        _log(output_dir, url, f"dna:{section_name}")

        # Follow PDF and HTML document links
        doc_links = re.findall(
            r'href=["\']([^"\']*(?:\.pdf|\.html?|/\d+/?)["\'])', html, re.IGNORECASE
        )
        for link_raw in doc_links[:50]:  # cap per section to avoid runaway scraping
            link = link_raw.rstrip('"\'')
            if not link.startswith("http"):
                link = urllib.parse.urljoin(url, link)
            if DNA_BASE not in link:
                continue

            doc_name = re.sub(r"[^\w.\-]", "_", link.replace(DNA_BASE, "").strip("/"))[:120]
            doc_dest = section_dir / doc_name
            if doc_dest.exists():
                continue

            try:
                data = _fetch(link)
                doc_dest.write_bytes(data)
                doc_dest.with_name(doc_dest.name + ".url").write_text(link, encoding="utf-8")
                _log(output_dir, link, f"dna:{section_name}:doc")
                logger.info("  Saved %s (%d bytes)", doc_name, len(data))
            except Exception as exc:
                logger.warning("  Failed %s: %s", link, exc)

            time.sleep(0.5)

        time.sleep(1.0)


# ---------------------------------------------------------------------------
# Source 3: Rechtspraak Suriname
# ---------------------------------------------------------------------------

RECHTSPRAAK_SR_BASE = "https://rechtspraak.sr"


def download_rechtspraak_sr(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download court rulings from rechtspraak.sr.

    Court decisions are public domain under Surinamese copyright law
    (no copyright in judgments and administrative decisions).
    """
    dest = output_dir / "rechtspraak_sr"
    dest.mkdir(parents=True, exist_ok=True)

    # Fetch the main index to discover ruling sections
    url = f"{RECHTSPRAAK_SR_BASE}/uitspraken/"
    logger.info("[rechtspraak_sr]  %s", url)

    if dry_run:
        logger.info("[dry-run] skipping")
        return

    try:
        html = _fetch(url).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Failed to reach rechtspraak.sr: %s", exc)
        return

    (dest / "index.html").write_text(html, encoding="utf-8")
    _log(output_dir, url, "rechtspraak_sr")

    # Extract document links
    doc_links = re.findall(r'href=["\']([^"\']+\.pdf)["\']', html, re.IGNORECASE)
    logger.info("Found %d PDF links", len(doc_links))

    for link in doc_links:
        doc_url = urllib.parse.urljoin(url, link)
        doc_name = Path(urllib.parse.urlparse(doc_url).path).name
        doc_dest = dest / doc_name
        if doc_dest.exists():
            continue

        try:
            data = _fetch(doc_url)
            doc_dest.write_bytes(data)
            doc_dest.with_name(doc_dest.name + ".url").write_text(doc_url, encoding="utf-8")
            _log(output_dir, doc_url, "rechtspraak_sr:pdf")
            logger.info("  Saved %s (%d bytes)", doc_name, len(data))
        except Exception as exc:
            logger.warning("  Failed %s: %s", doc_url, exc)

        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Source 4: National Archives Suriname (CC0 via Openarchieven)
# ---------------------------------------------------------------------------

OPENARCHIEVEN_API = "https://api.openarchieven.nl/v1"


def download_national_archives(
    output_dir: Path,
    max_records: int = 10_000,
    dry_run: bool = False,
) -> None:
    """
    Download National Archives Suriname metadata via Openarchieven (CC0).

    Returns document metadata in JSON; useful for historical Dutch text references.
    """
    dest = output_dir / "national_archives_sr"
    dest.mkdir(parents=True, exist_ok=True)

    page = 0
    fetched = 0
    page_size = 100

    while fetched < max_records:
        params = urllib.parse.urlencode({
            "archive": "nas",
            "page": page,
            "size": page_size,
            "language": "nl",
        })
        url = f"{OPENARCHIEVEN_API}/records?{params}"
        logger.info("[national_archives_sr] page=%d  %s", page, url)

        if dry_run:
            logger.info("[dry-run] skipping")
            break

        try:
            data = _fetch(url)
        except Exception as exc:
            logger.warning("Failed page %d: %s", page, exc)
            break

        if not data or not data.strip():
            logger.warning("[national_archives_sr] Empty response at page %d — stopping.", page)
            break

        try:
            parsed = json.loads(data)
        except Exception as exc:
            logger.warning("[national_archives_sr] JSON parse error at page %d: %s — stopping.", page, exc)
            break

        dest_file = dest / f"nas_page_{page:06d}.json"
        dest_file.write_bytes(data)
        _log(output_dir, url, "national_archives_sr")

        records = parsed.get("records", parsed.get("results", []))
        if not records:
            logger.info("No more records.")
            break

        fetched += len(records)
        page += 1
        time.sleep(0.5)

    logger.info("[national_archives_sr] Done. Fetched ~%d records.", fetched)


# ---------------------------------------------------------------------------
# Combined download
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    sources: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download all Surinamese sources.

    sources: subset of ["staatsblad", "dna", "rechtspraak", "archives"]
             or None for all.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    all_sources = {"staatsblad", "dna", "rechtspraak", "archives"}
    selected = set(sources) if sources else all_sources

    if "staatsblad" in selected:
        download_staatsblad(output_dir, dry_run=dry_run)
    if "dna" in selected:
        download_dna(output_dir, dry_run=dry_run)
    if "rechtspraak" in selected:
        download_rechtspraak_sr(output_dir, dry_run=dry_run)
    if "archives" in selected:
        download_national_archives(output_dir, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Surinamese Dutch government publications (public domain)"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--sources", nargs="+",
        default=["staatsblad", "dna", "rechtspraak", "archives"],
        choices=["staatsblad", "dna", "rechtspraak", "archives"],
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, sources=args.sources, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
