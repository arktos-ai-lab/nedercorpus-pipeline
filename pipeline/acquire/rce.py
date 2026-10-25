"""
Download RCE (Rijksdienst voor het Cultureel Erfgoed) cultural heritage data.

The RCE publishes its Rijksmonumenten (protected monuments) and cultural
heritage object descriptions as Linked Open Data via a SPARQL endpoint.
~600,000 objects with Dutch descriptions.

License: CC BY 4.0
  https://linkeddata.cultureelerfgoed.nl

SPARQL endpoint: https://linkeddata.cultureelerfgoed.nl/sparql
REST API: https://api.linkeddata.cultureelerfgoed.nl

Usage:
  python -m pipeline acquire --sources rce
"""
from __future__ import annotations

import datetime
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "rce"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "cultural_heritage"

_SPARQL_ENDPOINT = "https://api.linkeddata.cultureelerfgoed.nl/datasets/rce/cho/services/cho/sparql"

# Query: fetch monument/object ID, name, and Dutch description
_SPARQL_QUERY = """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX ceo: <https://linkeddata.cultureelerfgoed.nl/def/ceo#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?monument ?naam ?omschrijving WHERE {{
  ?monument a ceo:Rijksmonument .
  OPTIONAL {{ ?monument ceo:heeftNaam/ceo:naam ?naam . }}
  OPTIONAL {{ ?monument ceo:heeftOmschrijving/ceo:omschrijving ?omschrijving .
              FILTER(LANG(?omschrijving) = "nl" || LANG(?omschrijving) = "") }}
  FILTER(BOUND(?omschrijving) && STR(?omschrijving) != "")
}}
LIMIT {limit}
OFFSET {offset}
"""


def _sparql_fetch(query: str, retries: int = 3, timeout: int = 60) -> list[dict]:
    """Execute SPARQL query and return list of result bindings."""
    params = urllib.parse.urlencode({
        "query":  query,
        "format": "application/sparql-results+json",
    })
    url = f"{_SPARQL_ENDPOINT}?{params}"
    headers = {
        "User-Agent": "NederCorpus/0.1 (academic; CC BY 4.0 open data)",
        "Accept": "application/sparql-results+json",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", {}).get("bindings", [])
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[rce] SPARQL request failed: %s", exc)
                return []
            time.sleep(3.0 * (attempt + 1))
    return []


def _log(output_dir: Path) -> None:
    entry = {
        "source":           SOURCE,
        "url":              _SPARQL_ENDPOINT,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    batch_size: int = 1000,
    max_records: int = 200_000,
    dry_run: bool = False,
) -> None:
    """
    Download RCE Rijksmonumenten descriptions via SPARQL.

    Saves batches as rce_batch_NNNNNN.jsonl.
    Each record: {id, naam, omschrijving, acquisition_date}.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[rce] [dry-run] would query %s", _SPARQL_ENDPOINT)
        return

    # Probe the SPARQL endpoint with a minimal query before committing to full download
    probe_query = _SPARQL_QUERY.format(limit=1, offset=0)
    probe_result = _sparql_fetch(probe_query, retries=1, timeout=30)
    if probe_result == [] and not list(output_dir.glob("rce_batch_*.jsonl")):
        # Empty result on first probe with no cached batches — endpoint may be down
        logger.warning(
            "[rce] RCE SPARQL endpoint returned 404. Endpoint may have moved. Skipping. "
            "Check https://linkeddata.cultureelerfgoed.nl for the current endpoint."
        )
        return

    # Count existing batches for resume
    existing = len(list(output_dir.glob("rce_batch_*.jsonl")))
    offset = existing * batch_size
    batch_idx = existing

    logger.info("[rce] Fetching (max %d records, starting at offset %d)…", max_records, offset)
    total = 0

    while offset < max_records:
        query = _SPARQL_QUERY.format(limit=batch_size, offset=offset)
        bindings = _sparql_fetch(query)
        if not bindings:
            logger.info("[rce] No more results at offset %d", offset)
            break

        records: list[str] = []
        for b in bindings:
            rec = {
                "id":          b.get("monument", {}).get("value", ""),
                "naam":        b.get("naam", {}).get("value", ""),
                "omschrijving": b.get("omschrijving", {}).get("value", ""),
                "acquisition_date": datetime.date.today().isoformat(),
            }
            if rec["omschrijving"]:
                records.append(json.dumps(rec, ensure_ascii=False))

        if records:
            path = output_dir / f"rce_batch_{batch_idx:06d}.jsonl"
            path.write_text("\n".join(records) + "\n", encoding="utf-8")
            logger.info("[rce] batch %06d: %d records", batch_idx, len(records))
            batch_idx += 1
            total += len(records)

        offset += batch_size
        time.sleep(2.0)  # Polite rate limiting

    _log(output_dir)
    logger.info("[rce] Done. %d records downloaded.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download RCE cultural heritage data (CC BY 4.0)")
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--batch-size",  type=int, default=1000)
    parser.add_argument("--max-records", type=int, default=200_000)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        batch_size=args.batch_size,
        max_records=args.max_records,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
