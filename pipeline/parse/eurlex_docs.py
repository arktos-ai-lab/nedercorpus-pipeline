"""
Fetch full Dutch-language document bodies from EUR-Lex CELLAR API.

The acquisition stage (eurlex.py) retrieves SPARQL metadata that includes
work URIs. This module reads those results and fetches the actual text
for each work via the CELLAR content API.

CELLAR HTML endpoint:
  https://publications.europa.eu/resource/cellar/{uuid}
  with Accept: text/html and language parameter NLD

Rate-limited to 1 req/s by default. EUR-Lex content is CC-BY.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Iterator

from pipeline.clean.html import strip_html
from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

CELLAR_BASE = "https://publications.europa.eu/resource/cellar"
HEADERS = {
    "User-Agent": "NederCorpus/0.1 (research; +https://github.com/nedercorpus)",
    "Accept-Language": "nl",
}


def _cellar_url(work_uri: str) -> str | None:
    """Extract CELLAR UUID from a work URI and build the HTML fetch URL."""
    m = re.search(r"cellar/([0-9a-f\-]{36})", work_uri)
    if not m:
        return None
    uuid = m.group(1)
    params = urllib.parse.urlencode({"language": "NLD", "format": "fmx4"})
    return f"{CELLAR_BASE}/{uuid}?{params}"


def _fetch_text(url: str, timeout: int = 30) -> str | None:
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = "utf-8"
            ct = resp.headers.get_content_charset()
            if ct:
                charset = ct
            return raw.decode(charset, errors="replace")
    except Exception as exc:
        logger.debug("Fetch failed %s: %s", url, exc)
        return None


def _parse_sparql_results(json_bytes: bytes) -> list[dict]:
    """Parse SPARQL JSON results into list of {work, title} dicts."""
    try:
        data = json.loads(json_bytes)
    except json.JSONDecodeError:
        return []
    bindings = data.get("results", {}).get("bindings", [])
    out = []
    for b in bindings:
        work  = b.get("work",  {}).get("value", "")
        title = b.get("title", {}).get("value", "")
        if work:
            out.append({"work": work, "title": title})
    return out


def _read_cached_html(html_dir: Path, uuid: str) -> str | None:
    """Read a pre-downloaded HTML file from html/{uuid}.html."""
    dest = html_dir / f"{uuid}.html"
    if not dest.exists() or dest.stat().st_size < 100:
        return None
    try:
        raw = dest.read_bytes()
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def parse_dir(
    raw_dir: Path,
    delay: float = 1.0,
    max_docs: int | None = None,
) -> Iterator[Document]:
    """
    Parse EUR-Lex documents from locally cached HTML bodies.

    Reads SPARQL metadata JSON files from raw_dir, looks up each document's
    pre-downloaded HTML in raw_dir/html/{uuid}.html (written by the acquire
    stage's download_bodies()), and yields Document objects.

    Falls back to live CELLAR fetching for any UUID not yet on disk so that
    the parser remains functional during incremental acquisition runs.
    """
    acq_date = datetime.date.today().isoformat()
    html_dir = raw_dir / "html"
    html_dir.mkdir(exist_ok=True)
    fetched = 0

    for json_file in sorted(raw_dir.glob("eurlex_*.json")):
        logger.info("Loading EUR-Lex metadata: %s", json_file.name)
        works = _parse_sparql_results(json_file.read_bytes())
        logger.info("  %d works found", len(works))

        for entry in works:
            if max_docs is not None and fetched >= max_docs:
                return

            work_uri = entry["work"]
            url = _cellar_url(work_uri)
            if not url:
                logger.debug("Cannot build CELLAR URL for %s", work_uri)
                continue

            # Extract UUID for disk cache lookup
            uuid_m = re.search(r"cellar/([0-9a-f\-]{36})", work_uri)
            uuid = uuid_m.group(1) if uuid_m else None

            # Prefer cached HTML; fall back to live fetch
            html = _read_cached_html(html_dir, uuid) if uuid else None
            if html is None:
                html = _fetch_text(url)
                if html is None:
                    continue
                # Cache for next run
                if uuid:
                    try:
                        (html_dir / f"{uuid}.html").write_bytes(html.encode("utf-8", errors="replace"))
                    except OSError:
                        pass
                time.sleep(delay)

            text = strip_html(html)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 100:
                continue

            doc_id = make_id("eurlex", work_uri)
            year_m = re.search(r"\b(19|20)\d{2}\b", work_uri)
            year = int(year_m.group(0)) if year_m else None

            meta = DocumentMeta(
                id=doc_id,
                source="eurlex",
                source_url=work_uri,
                license="CC-BY",
                publication_year=year,
                country="EU",
                domain="legal",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            fetched += 1
