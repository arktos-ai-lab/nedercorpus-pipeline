"""
Download Dutch-language EU legislation from EUR-Lex (reusable with attribution).

Two phases:
  1. query_sparql()     — enumerate work URIs via SPARQL, save paginated JSON files.
  2. download_bodies()  — fetch full HTML for each work from the CELLAR API and
                          save to html/{uuid}.html.  Skips already-downloaded files,
                          so it is safe to interrupt and resume.

The parse stage (eurlex_docs.py) reads from the local html/ cache — it never
makes live HTTP requests, which makes parsing fast, reproducible, and offline.

License: CC-BY (EUR-Lex reuse policy)
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

EURLEX_SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
SOURCE  = "eurlex"
LICENSE = "CC-BY"
COUNTRY = "EU"
DOMAIN  = "legal"

# Document types to query — regulations, directives, decisions, and EU case law.
# cdm:judgment: Court of Justice (CJEU) and General Court judgments (CC BY 4.0
# per EUR-Lex legal notice for editorial/case-law content).
_DOC_TYPES = [
    "cdm:regulation",
    "cdm:directive",
    "cdm:decision",
    "cdm:recommendation",
    "cdm:judgment",
]

_QUERY_TEMPLATE = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?work WHERE {{
  ?work a {doc_type} .
}}
ORDER BY ?work
LIMIT {limit}
OFFSET {offset}
"""
# NOTE: The cdm:work_has_expression / cdm:expression_uses_language language filter
# returned 0 results as of 2026-03 — the CDM ontology no longer populates those
# expression-level triples in the SPARQL endpoint.  We now retrieve all works of
# each document type and rely on CELLAR content-negotiation (?language=NLD) in the
# parse stage to obtain the Dutch text (eurlex_docs.py).


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


def _sparql_fetch(query: str, timeout: int = 60) -> bytes | None:
    from urllib.parse import urlencode
    params = urlencode({"query": query, "format": "application/sparql-results+json"})
    url = f"{EURLEX_SPARQL}?{params}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/sparql-results+json",
                 "User-Agent": "NederCorpus/0.1 (research; public data)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("SPARQL request failed: %s", exc)
        return None


def _count_bindings(json_bytes: bytes) -> int:
    try:
        data = json.loads(json_bytes)
        return len(data.get("results", {}).get("bindings", []))
    except Exception:
        return 0


def query_sparql(
    output_dir: Path,
    page_size: int = 500,
    max_results: int = 500_000,
    dry_run: bool = False,
) -> None:
    """
    Paginate through EUR-Lex SPARQL for each document type and save results.

    Each page is saved as eurlex_{doc_type}_{offset:08d}.json.
    The parse stage reads these files and fetches full-text from CELLAR.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    for doc_type in _DOC_TYPES:
        short = doc_type.split(":")[-1]   # "regulation", "directive", etc.
        offset = 0
        type_total = 0

        logger.info("[eurlex] Querying %s (max %d)", doc_type, max_results)

        while type_total < max_results:
            query = _QUERY_TEMPLATE.format(
                doc_type=doc_type,
                limit=page_size,
                offset=offset,
            )
            logger.info("  %s offset=%d", short, offset)

            if dry_run:
                logger.info("  [dry-run] skipping")
                break

            data = _sparql_fetch(query)
            if data is None:
                logger.warning("  SPARQL failed at offset %d — stopping.", offset)
                break

            n = _count_bindings(data)
            if n == 0:
                logger.info("  No more results for %s.", short)
                break

            dest = output_dir / f"eurlex_{short}_{offset:08d}.json"
            dest.write_bytes(data)
            _log_entry(output_dir, EURLEX_SPARQL)
            logger.info("  Saved %s (%d works)", dest.name, n)

            type_total += n
            offset += page_size

            if n < page_size:
                # Last (partial) page
                break

            time.sleep(1.0)   # be polite to the SPARQL endpoint

        logger.info("[eurlex] %s total: %d works", short, type_total)


_CELLAR_BASE = "https://publications.europa.eu/resource/cellar"
_CELLAR_HEADERS = {
    "User-Agent": "NederCorpus/0.1 (research; public data)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "nl",
}


def _cellar_url(work_uri: str) -> str | None:
    """Build a CELLAR HTML fetch URL from a work URI."""
    m = re.search(r"cellar/([0-9a-f\-]{36})", work_uri)
    if not m:
        return None
    uuid = m.group(1)
    params = urllib.parse.urlencode({"language": "NLD", "format": "fmx4"})
    return f"{_CELLAR_BASE}/{uuid}?{params}", m.group(1)


def _cellar_url_and_uuid(work_uri: str) -> tuple[str, str] | tuple[None, None]:
    m = re.search(r"cellar/([0-9a-f\-]{36})", work_uri)
    if not m:
        return None, None
    uuid = m.group(1)
    params = urllib.parse.urlencode({"language": "NLD", "format": "fmx4"})
    return f"{_CELLAR_BASE}/{uuid}?{params}", uuid


def _read_sparql_uris(json_file: Path) -> list[str]:
    """Return work URI strings from a SPARQL result JSON file."""
    try:
        data = json.loads(json_file.read_bytes())
        return [
            b.get("work", {}).get("value", "")
            for b in data.get("results", {}).get("bindings", [])
            if b.get("work", {}).get("value")
        ]
    except Exception:
        return []


def download_bodies(
    output_dir: Path,
    delay: float = 1.0,
    dry_run: bool = False,
) -> None:
    """
    Pre-download HTML document bodies from the CELLAR API to disk.

    Reads all eurlex_*.json metadata files in output_dir, derives each
    document's CELLAR UUID, and saves the HTML to output_dir/html/{uuid}.html.

    Already-downloaded files (non-empty) are skipped — safe to interrupt/resume.
    """
    html_dir = output_dir / "html"
    html_dir.mkdir(exist_ok=True)

    total = skipped = fetched = failed = 0

    for json_file in sorted(output_dir.glob("eurlex_*.json")):
        uris = _read_sparql_uris(json_file)
        for work_uri in uris:
            total += 1
            url, uuid = _cellar_url_and_uuid(work_uri)
            if not url:
                continue
            dest = html_dir / f"{uuid}.html"
            if dest.exists() and dest.stat().st_size > 100:
                skipped += 1
                continue
            if dry_run:
                logger.info("[dry-run] would fetch %s", uuid)
                continue

            req = urllib.request.Request(url, headers=_CELLAR_HEADERS)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                dest.write_bytes(raw)
                fetched += 1
                if fetched % 100 == 0:
                    logger.info(
                        "[eurlex] bodies: %d fetched, %d skipped, %d failed / %d total",
                        fetched, skipped, failed, total,
                    )
            except Exception as exc:
                logger.debug("CELLAR fetch failed %s: %s", uuid, exc)
                failed += 1

            time.sleep(delay)

    logger.info(
        "[eurlex] download_bodies done: %d fetched, %d skipped, %d failed, %d total",
        fetched, skipped, failed, total,
    )


def download(
    output_dir: Path,
    max_results: int = 500_000,
    dry_run: bool = False,
    bodies: bool = True,
) -> None:
    query_sparql(output_dir, max_results=max_results, dry_run=dry_run)
    if bodies:
        download_bodies(output_dir, dry_run=dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download EUR-Lex Dutch legislation (SPARQL + CELLAR bodies)"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--max-results", type=int, default=500_000,
                        help="Max results per document type (default 500000)")
    parser.add_argument("--no-bodies",   action="store_true",
                        help="Only run SPARQL enumeration, skip body download")
    parser.add_argument("--bodies-only", action="store_true",
                        help="Skip SPARQL, only download bodies for already-fetched metadata")
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    if args.bodies_only:
        download_bodies(args.output, dry_run=args.dry_run)
    else:
        download(args.output, max_results=args.max_results,
                 dry_run=args.dry_run, bodies=not args.no_bodies)


if __name__ == "__main__":
    main()
