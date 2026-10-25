"""
Download Papiamento texts from the Biblioteca Nacional Aruba collection
on Internet Archive.

The Biblioteca Nacional Aruba (BNA) hosts Aruba's largest digital collection
on Internet Archive with 18,800+ items including books, newspapers, periodicals,
and government documents in Papiamento and Dutch.

Important linguistic distinction:
  Papiamento (Aruba variant, ISO 639-3: pap, territory: AW)
    Uses etymology-based spelling: words end in -o (e.g. "trabao", "bida")
  Papiamentu (Curaçao/Bonaire variant, ISO 639-3: pap, territory: CW/BQ)
    Uses phonology-based spelling: words end in -u (e.g. "trabou", "bida")

This module targets the ARUBA variant (Papiamento -o spelling).
Existing papiamentu_wiki.py covers the Curaçao Wikipedia variant.

Internet Archive collection: bibliotecanacionalaruba
  URL: https://archive.org/details/bibliotecanacionalaruba

License filtering: Only CC-licensed items are downloaded.
  Accepted: CC0, CC-BY, CC-BY-SA (any version)
  Rejected: CC-BY-NC, CC-BY-ND, CC-BY-NC-ND, InC (in copyright)

Language variant: PAP (Papiamento — Aruba variant)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE    = "coleccion_aruba"
COUNTRY   = "AW"
DOMAIN    = "encyclopedic"

ARCHIVE_BASE  = "https://archive.org"
ARCHIVE_API   = "https://archive.org/advancedsearch.php"
COLLECTION    = "bibliotecanacionalaruba"

# CC licenses we accept (no NC, no ND)
_ACCEPTED_LICENSES = {
    "cc0",
    "publicdomain",
    "cc-by",
    "cc-by-sa",
    # Versioned forms
    "cc-by-4.0", "cc-by-3.0", "cc-by-2.5", "cc-by-2.0", "cc-by-1.0",
    "cc-by-sa-4.0", "cc-by-sa-3.0", "cc-by-sa-2.5", "cc-by-sa-2.0",
}

HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic research; Internet Archive open data)",
}


def _license_ok(license_url: str | None) -> tuple[bool, str]:
    """Return (accepted, normalized_license_code)."""
    if not license_url:
        return False, ""
    url = license_url.lower()

    if "publicdomain" in url or "/zero/" in url or "cc0" in url:
        return True, "CC0"
    if "creativecommons" in url:
        # Extract license type from URL path
        # e.g. https://creativecommons.org/licenses/by/4.0/
        #      https://creativecommons.org/licenses/by-sa/3.0/
        import re
        m = re.search(r'/licenses?/([a-z\-]+)/([0-9.]+)', url)
        if m:
            kind    = m.group(1)  # e.g. "by", "by-sa", "by-nc"
            version = m.group(2)
            # Reject NC, ND
            if "nc" in kind or "nd" in kind:
                return False, f"CC-{kind.upper()}-{version}"
            norm = f"CC-{kind.upper()}-{version}"
            return True, norm
        # Fallback for non-versioned
        if "/by/" in url:
            return True, "CC-BY"
        if "/by-sa/" in url:
            return True, "CC-BY-SA"
    return False, ""


def _log(output_dir: Path, url: str, license_: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          license_,
        "country":          COUNTRY,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _ia_search(query: str, page: int, rows: int = 200) -> dict:
    """Query Internet Archive advanced search API."""
    parts = [
        f"q={urllib.parse.quote(query)}",
        "fl[]=identifier",
        "fl[]=title",
        "fl[]=year",
        "fl[]=language",
        "fl[]=licenseurl",
        "fl[]=subject",
        "fl[]=description",
        f"rows={rows}",
        f"page={page + 1}",
        "output=json",
    ]
    url = f"{ARCHIVE_API}?" + "&".join(parts)
    data = _fetch(url, timeout=30)
    return json.loads(data)


def _download_text(identifier: str, dest_dir: Path) -> Path | None:
    """
    Download plain text (OCR) for an Internet Archive item.
    Tries multiple known text file naming patterns.
    """
    dest = dest_dir / f"{identifier}.txt"
    if dest.exists():
        return dest

    url_patterns = [
        f"{ARCHIVE_BASE}/download/{identifier}/{identifier}_djvu.txt",
        f"{ARCHIVE_BASE}/download/{identifier}/{identifier}_text.txt",
        f"{ARCHIVE_BASE}/download/{identifier}/{identifier}.txt",
    ]

    for url in url_patterns:
        try:
            data = _fetch(url, timeout=60)
            if len(data) > 100:  # non-empty
                dest.write_bytes(data)
                dest.with_suffix(".url").write_text(url, encoding="utf-8")
                return dest
        except Exception:
            continue

    return None


def download(
    output_dir: Path,
    max_items: int = 10_000,
    languages: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """
    Download CC-licensed texts from the Biblioteca Nacional Aruba collection.

    languages: IA language codes to filter. Default: ['Papiamento', 'Papiamentu',
               'pap', 'Dutch', 'nl']. Pass [] to skip language filter.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = output_dir / "texts"
    texts_dir.mkdir(exist_ok=True)
    meta_path  = output_dir / "metadata.jsonl"

    # Language filter
    if languages is None:
        languages = ["Papiamento", "Papiamentu", "pap", "Dutch", "nl", "Nederlands"]

    lang_clause = ""
    if languages:
        lang_parts = [f'language:"{lg}"' for lg in languages]
        lang_clause = " OR ".join(lang_parts)
        lang_clause = f" AND ({lang_clause})"

    query = f"collection:{COLLECTION}{lang_clause}"
    logger.info("[coleccion_aruba] Search query: %s", query)

    page     = 0
    fetched  = 0
    accepted = 0
    rejected = 0

    while fetched < max_items:
        if dry_run:
            logger.info("[dry-run] skipping download")
            return

        try:
            result = _ia_search(query, page)
        except Exception as exc:
            logger.error("IA search failed: %s", exc)
            break

        docs = result.get("response", {}).get("docs", [])
        if not docs:
            logger.info("[coleccion_aruba] No more items at page %d", page)
            break

        for item in docs:
            identifier  = item.get("identifier", "")
            license_url = item.get("licenseurl", "")
            title       = item.get("title", "")

            if not identifier:
                continue

            ok, license_norm = _license_ok(license_url)
            if not ok:
                logger.debug("Skipping %s (license: %s)", identifier, license_url)
                rejected += 1
                continue

            meta = {
                "identifier":   identifier,
                "title":        title,
                "year":         item.get("year", ""),
                "language":     item.get("language", ""),
                "license":      license_norm,
                "license_url":  license_url,
                "subject":      item.get("subject", ""),
                "description":  item.get("description", ""),
                "source":       SOURCE,
                "country":      COUNTRY,
            }

            # Save metadata
            with meta_path.open("a", encoding="utf-8") as mf:
                mf.write(json.dumps(meta, ensure_ascii=False) + "\n")

            # Download text
            text_path = _download_text(identifier, texts_dir)
            if text_path:
                _log(output_dir, f"{ARCHIVE_BASE}/details/{identifier}", license_norm)
                logger.info("  [%s] %s — %s (%s)",
                            license_norm, identifier, title[:60], text_path.name)
                accepted += 1
            else:
                logger.debug("  No text available for %s", identifier)

            time.sleep(0.4)

        fetched += len(docs)
        page    += 1
        logger.info("[coleccion_aruba] page=%d fetched=%d accepted=%d rejected=%d",
                    page, fetched, accepted, rejected)
        time.sleep(1.0)

    logger.info("[coleccion_aruba] Done. %d accepted, %d rejected.", accepted, rejected)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Papiamento/Dutch texts from Biblioteca Nacional Aruba (Internet Archive)"
    )
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-items",  type=int, default=10_000)
    parser.add_argument("--languages",  nargs="+", default=None,
                        help="IA language field values to include (default: Papiamento/Dutch)")
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(args.output, max_items=args.max_items, languages=args.languages, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
