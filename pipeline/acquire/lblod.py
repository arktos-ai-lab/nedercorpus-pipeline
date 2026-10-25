"""
Download Flemish local government decisions (LBLOD —
Lokale Beslissingen als Linked Open Data).

LBLOD publishes structured open data about decisions made by Flemish
municipalities, OCMWs, provinces, and other local authorities.
Decisions include meeting agendas, voted items, and decision text.

License: CC0 1.0 (Vlaamse Overheid open data)

Data access: SPARQL endpoint at https://data.lblod.info/sparql
The besluit:motivering predicate holds the full decision text.

Previous dump-file approach (besluiten.nt.gz) has been retired — the
dump URLs now redirect to an Ember SPA instead of the N-Triples file.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "lblod"
LICENSE = "CC0"

_SPARQL_ENDPOINT = "https://data.lblod.info/sparql"
_USER_AGENT = "NederCorpus/0.1 (academic research; LBLOD CC0 open data)"
_PAGE_SIZE  = 1000
_DELAY      = 1.0   # seconds between SPARQL requests

_QUERY_TEMPLATE = """\
PREFIX besluit: <http://data.vlaanderen.be/ns/besluit#>
PREFIX prov:    <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?subject ?text
WHERE {{
  {{
    ?subject besluit:motivering ?text .
    FILTER(LANG(?text) IN ("nl", "nl-BE", ""))
  }} UNION {{
    ?subject prov:value ?text .
    FILTER(LANG(?text) IN ("nl", "nl-BE", ""))
  }} UNION {{
    ?subject dcterms:description ?text .
    FILTER(LANG(?text) IN ("nl", "nl-BE", ""))
  }}
  FILTER(STRLEN(STR(?text)) > 100)
}}
LIMIT {limit} OFFSET {offset}
"""


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _sparql_page(offset: int) -> list[dict]:
    query = _QUERY_TEMPLATE.format(limit=_PAGE_SIZE, offset=offset)
    params = urllib.parse.urlencode({
        "query": query,
        "format": "application/sparql-results+json",
    })
    url = f"{_SPARQL_ENDPOINT}?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/sparql-results+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data.get("results", {}).get("bindings", [])
    except Exception as exc:
        logger.warning("[lblod] SPARQL page offset=%d failed: %s", offset, exc)
        return []


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download LBLOD decision texts via SPARQL endpoint."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[lblod] [dry-run] skipping")
        return

    out_file = output_dir / "besluiten.jsonl"

    # Load already-seen subjects for dedup / resume
    seen: set[str] = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                seen.add(json.loads(line).get("subject", ""))
            except json.JSONDecodeError:
                pass
    logger.info("[lblod] %d records already collected", len(seen))

    total_written = 0
    offset = 0

    while True:
        logger.info("[lblod] Querying SPARQL offset=%d …", offset)
        bindings = _sparql_page(offset)
        if not bindings:
            logger.info("[lblod] No more results at offset=%d — done", offset)
            break

        with out_file.open("a", encoding="utf-8") as fh:
            for b in bindings:
                subject = b.get("subject", {}).get("value", "")
                text    = b.get("text",    {}).get("value", "").strip()
                if not text or len(text) < 50 or subject in seen:
                    continue
                seen.add(subject)
                fh.write(json.dumps({"subject": subject, "text": text},
                                     ensure_ascii=False) + "\n")
                total_written += 1

        logger.info("[lblod] offset=%d: %d total written", offset, total_written)
        if len(bindings) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
        time.sleep(_DELAY)

    _log(output_dir, _SPARQL_ENDPOINT)
    logger.info("[lblod] Done. %d decision texts saved.", total_written)
