"""
Download Dutch-language open-access articles from PubMed Central (PMC).

PMC publishes a bulk open-access subset via FTP/S3. We filter for articles
tagged xml:lang="nl" (Dutch). The Dutch subset is small (~5K–30K articles)
but covers a unique register: peer-reviewed biomedical and life sciences
literature in Dutch.

Bulk dataset:
  https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_bulk/
  File list: https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_comm_use_file_list.txt

The file list is a TSV: File Path, Article Citation, Accession ID, LastUpdated, PMID, License

License: CC-BY / CC0 (comm_use subset only — explicitly "Commercial Use Allowed").
  Individual article licenses are embedded in the JATS XML.

Format: JATS XML (Journal Article Tag Suite) — standard biomedical article format.

Note: The full dataset is ~100GB. This acquirer only downloads files whose
PMC XML metadata indicates Dutch (`xml:lang="nl"`). Without pre-filtering,
we download the file list (~50MB) and then fetch only matching articles.

Since most PMC articles are English, we use a two-step approach:
1. Download the file list and filter by known Dutch journals / Accession IDs
2. Download only the matching article ZIPs (~100-200MB total for Dutch)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "pmc_nl"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "academic"

_FTP_BASE     = "https://ftp.ncbi.nlm.nih.gov/pub/pmc"
# PMC OA file list URLs — primary is the stable flat file at the root of the
# pmc FTP tree (oa_file_list.txt). The oa_bulk sub-paths change between PMC
# data releases, so we keep the root-level file as the primary and fall back
# to two plausible oa_bulk locations.
_FILE_LIST_URL = f"{_FTP_BASE}/oa_file_list.txt"
_FILE_LIST_URL_FALLBACK = f"{_FTP_BASE}/oa_bulk/oa_comm/xml/oa_comm_use_file_list.csv"
_FILE_LIST_URL_FALLBACK2 = f"{_FTP_BASE}/oa_comm_use_file_list.txt"
_USER_AGENT   = "NederCorpus/0.1 (academic; Dutch biomedical corpus; PMC OA)"

# Dutch-language journal ISSN prefixes / known Dutch journals
# Used to pre-filter without downloading all ~4M article entries
_DUTCH_JOURNAL_KEYWORDS = [
    "tijdschrift",      # e.g. "Nederlands Tijdschrift voor Geneeskunde"
    "nederlands",
    "nederland",
    "dutch",
    "belgisch",
    "vlaams",
    "ned tijdschr",
    "ntv",
    "huisarts",
    "verpleeg",
]


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, retries: int = 3, timeout: int = 120) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 5.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _get_file_list(output_dir: Path) -> list[dict]:
    """
    Download and parse the PMC OA file list.

    Returns list of dicts: {path, accession, pmid, license, citation}
    Filters to entries that look like Dutch-language journals.
    """
    list_cache = output_dir / "oa_comm_file_list.txt"
    # Track the URL used so we know the delimiter (TSV vs CSV)
    url_cache_meta = output_dir / "oa_comm_file_list_url.txt"
    used_list_url = ""
    if not list_cache.exists():
        logger.info("[pmc_nl] Downloading PMC OA file list (~50MB)...")
        data = None
        for url_attempt in (_FILE_LIST_URL, _FILE_LIST_URL_FALLBACK, _FILE_LIST_URL_FALLBACK2):
            try:
                data = _fetch(url_attempt, timeout=300)
                used_list_url = url_attempt
                logger.info("[pmc_nl] File list fetched from: %s", url_attempt)
                break
            except Exception as exc:
                logger.warning("[pmc_nl] File list URL failed (%s): %s", url_attempt, exc)
        if data is None:
            logger.error(
                "[pmc_nl] Cannot download file list from any URL. "
                "Check https://ftp.ncbi.nlm.nih.gov/pub/pmc/ manually for the "
                "current file-list path and update _FILE_LIST_URL in this file."
            )
            return []
        list_cache.write_bytes(data)
        url_cache_meta.write_text(used_list_url, encoding="utf-8")
        logger.info("[pmc_nl] File list saved: %s", list_cache.name)
    else:
        used_list_url = url_cache_meta.read_text(encoding="utf-8").strip() if url_cache_meta.exists() else _FILE_LIST_URL

    # Determine delimiter: oa_file_list.txt and .txt variants are TSV; .csv is comma
    delimiter = "," if used_list_url.endswith(".csv") else "\t"

    logger.info("[pmc_nl] Parsing file list for Dutch entries (delimiter=%r)...", delimiter)
    dutch_entries: list[dict] = []
    lines = list_cache.read_text(encoding="utf-8", errors="replace").splitlines()

    # Header: File Path<sep>Article Citation<sep>Accession ID<sep>LastUpdated<sep>PMID<sep>License
    for line in lines[1:]:
        parts = line.split(delimiter)
        if len(parts) < 5:
            continue
        file_path  = parts[0].strip()
        citation   = parts[1].strip().lower()
        accession  = parts[2].strip()
        pmid       = parts[4].strip() if len(parts) > 4 else ""
        license_   = parts[5].strip() if len(parts) > 5 else ""

        # Filter: only CC0/CC-BY (no NC, no ND)
        lic_lower = license_.lower()
        if "nc" in lic_lower or "nd" in lic_lower:
            continue

        # Heuristic: check if citation mentions Dutch journal keywords
        if any(kw in citation for kw in _DUTCH_JOURNAL_KEYWORDS):
            dutch_entries.append({
                "path":      file_path,
                "citation":  citation,
                "accession": accession,
                "pmid":      pmid,
                "license":   license_,
            })

    logger.info("[pmc_nl] Found %d candidate Dutch-journal entries in file list", len(dutch_entries))
    return dutch_entries


def _extract_dutch_text_from_jats(xml_bytes: bytes) -> tuple[str, str, int | None, str]:
    """
    Extract title, abstract, year, and language from JATS XML.

    Returns (title, abstract_text, year, lang_code).
    Returns ("", "", None, "") if not Dutch or no useful text.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return "", "", None, ""

    # Check language
    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang", "")
    if not lang:
        # Try article element
        art = root.find(".//article")
        if art is not None:
            lang = art.get("{http://www.w3.org/XML/1998/namespace}lang", "")
    # Accept nl, nld, dutch, or empty (we pre-filtered by journal name)
    if lang and lang not in ("nl", "nld", "dutch", ""):
        # Skip clearly non-Dutch articles
        if lang.startswith("en") or lang.startswith("de") or lang.startswith("fr"):
            return "", "", None, lang

    # Extract title
    title_el = root.find(".//article-title")
    title = " ".join(title_el.itertext()).strip() if title_el is not None else ""

    # Extract abstract
    abstract_parts = []
    for abs_el in root.findall(".//abstract"):
        text = " ".join(abs_el.itertext()).strip()
        if text:
            abstract_parts.append(text)
    abstract = " ".join(abstract_parts).strip()

    # Extract body text (first 5000 words max to avoid giant articles)
    body_parts = []
    body_el = root.find(".//body")
    if body_el is not None:
        for para in body_el.iter("p"):
            text = " ".join(para.itertext()).strip()
            if text:
                body_parts.append(text)
    body_text = " ".join(body_parts)
    # Truncate to ~30K chars to avoid enormous articles
    if len(body_text) > 30_000:
        body_text = body_text[:30_000]

    # Year
    year_el = root.find(".//pub-date/year")
    if year_el is None:
        year_el = root.find(".//year")
    pub_year = None
    if year_el is not None and year_el.text:
        try:
            pub_year = int(year_el.text.strip())
        except ValueError:
            pass

    full_text = "\n\n".join(filter(None, [title, abstract, body_text]))
    return title, full_text, pub_year, lang


def download(
    output_dir: Path,
    max_articles: int = 2000,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch-language PMC open-access articles.

    Phase 1: Download the PMC OA file list and filter for Dutch journals.
    Phase 2: Download individual article tarballs and extract JATS XML.

    Only CC-BY / CC0 articles (no NC, no ND) are downloaded.

    Args:
        max_articles: Maximum articles to download (default 2000).
        dry_run:      Log only, do not fetch.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[pmc_nl] [dry-run] would download Dutch PMC OA articles from FTP")
        return

    dutch_entries = _get_file_list(output_dir)
    if not dutch_entries:
        logger.warning("[pmc_nl] No Dutch entries found — check file list or keywords")
        return

    articles_dir = output_dir / "articles"
    articles_dir.mkdir(exist_ok=True)

    downloaded = 0
    for entry in dutch_entries[:max_articles]:
        accession = entry["accession"]
        file_path = entry["path"]  # e.g. "oa_comm/xml/00/00/PMC123456.xml.gz" or similar
        dest_xml = articles_dir / f"{accession}.xml"

        if dest_xml.exists():
            downloaded += 1
            continue

        # Construct download URL
        url = f"{_FTP_BASE}/{file_path}"
        logger.info("[pmc_nl] Downloading %s...", accession)
        try:
            data = _fetch(url, timeout=60)
        except Exception as exc:
            logger.warning("[pmc_nl] Failed %s: %s", accession, exc)
            continue

        # Handle gzip compression
        if file_path.endswith(".gz"):
            import gzip
            try:
                data = gzip.decompress(data)
            except Exception:
                pass

        # Quick language check before saving
        _, full_text, _, lang = _extract_dutch_text_from_jats(data)
        if not full_text or len(full_text) < 200:
            logger.debug("[pmc_nl] %s: no usable Dutch text (lang=%s)", accession, lang)
            continue

        dest_xml.write_bytes(data)
        _log(output_dir, url)
        downloaded += 1
        time.sleep(0.3)

    logger.info("[pmc_nl] Done. %d articles downloaded.", downloaded)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Dutch PMC open-access articles (CC-BY)"
    )
    parser.add_argument("--output",       required=True, type=Path)
    parser.add_argument("--max-articles", type=int, default=2000)
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()
    download(args.output, max_articles=args.max_articles, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
