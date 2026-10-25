"""
Download Dutch entity descriptions and labels from Wikidata (CC0).

Wikidata is a free, collaborative, multilingual knowledge base. All data
is released under CC0 1.0 Universal. Every entity can have a Dutch (nl)
label (short name) and description (one-line summary).

Uses QLEVER (Uni Freiburg) as primary endpoint — it handles large OFFSET
queries without timeout, unlike the official Wikidata SPARQL endpoint.
The official endpoint is used as fallback for small spot queries only.

Note on IRI pagination: FILTER(?item > wd:Q{n}) uses *lexicographic* IRI
comparison, so Q9 > Q10 (wrong). We use OFFSET pagination instead, which
works correctly on QLEVER.

The resulting text combines label + description into short but dense Dutch
factual sentences — a register not well covered by longer documents.

Estimated volume: ~5–8 million Dutch descriptions.

License: CC0 (https://www.wikidata.org/wiki/Wikidata:Licensing)

Usage:
  python -m pipeline acquire --sources wikidata_nl
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

SOURCE  = "wikidata_nl"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "encyclopedic"

# Primary: QLEVER (Uni Freiburg) — designed for full-scan Wikidata queries
_QLEVER_ENDPOINT = "https://qlever.cs.uni-freiburg.de/api/wikidata"
# Fallback: official Wikidata SPARQL endpoint (times out at large OFFSETs)
_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

_BATCH_SIZE = 5_000      # entities per SPARQL page
_ITEMS_PER_FILE = 50_000 # entities per output JSONL file
_MAX_RETRIES = 3

# QLEVER query: OFFSET-based, no wikibase:label service (uses direct triples).
# QLEVER handles large OFFSETs efficiently (designed for Wikidata full scans).
_QLEVER_QUERY = """\
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX schema: <http://schema.org/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?item ?label ?desc WHERE {{
  ?item schema:description ?desc .
  ?item rdfs:label ?label .
  FILTER(LANG(?desc) = "nl")
  FILTER(LANG(?label) = "nl")
}}
LIMIT {limit}
OFFSET {offset}
"""

# Official endpoint query: small batches only, no ORDER BY (avoids timeout).
_OFFICIAL_QUERY = """\
SELECT ?item ?itemLabel ?itemDescription WHERE {{
  ?item schema:description ?itemDescription .
  FILTER(LANG(?itemDescription) = "nl")
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "nl" .
    ?item rdfs:label ?itemLabel .
  }}
  FILTER(LANG(?itemLabel) = "nl")
}}
LIMIT {limit}
OFFSET {offset}
"""


def _sparql_get(endpoint: str, query: str, timeout: int = 120) -> list[dict]:
    """GET a SPARQL query (for QLEVER which redirects POST→GET). Follows redirects."""
    params = urllib.parse.urlencode({"query": query})
    url = f"{endpoint}?{params}"
    headers = {
        "User-Agent": "NederCorpus/0.1 (academic; wikidata nl batch download)",
        "Accept":     "application/sparql-results+json",
    }
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
    with opener.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("results", {}).get("bindings", [])


def _sparql_post(endpoint: str, query: str, timeout: int = 90) -> list[dict]:
    """POST a SPARQL query (for official Wikidata endpoint)."""
    body = urllib.parse.urlencode({"query": query}).encode("utf-8")
    headers = {
        "User-Agent":   "NederCorpus/0.1 (academic; wikidata nl batch download)",
        "Accept":       "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("results", {}).get("bindings", [])


def _fetch_batch(offset: int, use_qlever: bool = True) -> tuple[list[dict], bool]:
    """
    Fetch one batch of Dutch entities at the given OFFSET.
    Returns (bindings, used_qlever).
    Tries QLEVER first (primary), falls back to official endpoint.
    """
    if use_qlever:
        query = _QLEVER_QUERY.format(limit=_BATCH_SIZE, offset=offset)
        for attempt in range(_MAX_RETRIES):
            try:
                bindings = _sparql_get(_QLEVER_ENDPOINT, query, timeout=120)
                return bindings, True
            except Exception as exc:
                wait = 20 * (attempt + 1)
                logger.warning(
                    "[wikidata_nl] QLEVER failed at offset=%d (attempt %d/%d): %s — wait %ds",
                    offset, attempt + 1, _MAX_RETRIES, exc, wait,
                )
                if attempt == _MAX_RETRIES - 1:
                    logger.info("[wikidata_nl] Falling back to official endpoint...")
                    break
                time.sleep(wait)

    # Official endpoint fallback (small batches, may timeout at large offsets)
    query = _OFFICIAL_QUERY.format(limit=min(_BATCH_SIZE, 1000), offset=offset)
    for attempt in range(_MAX_RETRIES):
        try:
            bindings = _sparql_post(_SPARQL_ENDPOINT, query, timeout=90)
            return bindings, False
        except Exception as exc:
            wait = 30 * (attempt + 1)
            logger.warning(
                "[wikidata_nl] Official endpoint failed at offset=%d (attempt %d/%d): %s — wait %ds",
                offset, attempt + 1, _MAX_RETRIES, exc, wait,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(wait)

    raise RuntimeError(f"Both QLEVER and official endpoint failed at offset={offset}")


def _extract_record(b: dict, use_qlever: bool, acq_date: str) -> dict | None:
    """Extract a record from a SPARQL binding dict."""
    item_id = b.get("item", {}).get("value", "").split("/")[-1]
    if use_qlever:
        label = b.get("label", {}).get("value", "")
        desc  = b.get("desc",  {}).get("value", "")
    else:
        label = b.get("itemLabel",       {}).get("value", "")
        desc  = b.get("itemDescription", {}).get("value", "")

    if not item_id.startswith("Q") or not label or not desc:
        return None
    return {"id": item_id, "label": label, "description": desc,
            "acquisition_date": acq_date}


def download(
    output_dir: Path,
    max_entities: int = 8_000_000,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch Wikidata entity labels and descriptions.

    Uses OFFSET-based pagination on QLEVER, which handles large offsets
    without timeout. Resume is supported by counting existing records and
    continuing from that offset.

    Saves wikidata_nl_NNNNNN.jsonl files, 50K entities each.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[wikidata_nl] [dry-run] would download Wikidata NL descriptions")
        return

    # Resume: count already-downloaded entities to determine starting offset
    file_num = 0
    total = 0
    existing = sorted(output_dir.glob("wikidata_nl_*.jsonl"))
    if existing:
        last_file = existing[-1]
        file_num = int(last_file.stem.split("_")[-1])
        lines = [ln for ln in last_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        total = (len(existing) - 1) * _ITEMS_PER_FILE + len(lines)
        logger.info(
            "[wikidata_nl] Resuming from offset=%d (%d entities already downloaded)",
            total, total,
        )

    offset = total  # OFFSET = number already downloaded
    use_qlever = True
    consecutive_failures = 0

    while total < max_entities:
        logger.info("[wikidata_nl] SPARQL offset=%d (total=%d)", offset, total)
        try:
            bindings, use_qlever = _fetch_batch(offset, use_qlever)
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            logger.error("[wikidata_nl] Batch failed at offset=%d: %s", offset, exc)
            if consecutive_failures >= 3:
                logger.error(
                    "[wikidata_nl] 3 consecutive failures — stopping.\n"
                    "  Re-run to resume from offset=%d.", offset,
                )
                break
            time.sleep(60)
            continue

        if not bindings:
            logger.info("[wikidata_nl] No more results at offset=%d", offset)
            break

        # Write records
        records = [_extract_record(b, use_qlever, acq_date) for b in bindings]
        records = [r for r in records if r is not None]

        out_path = output_dir / f"wikidata_nl_{file_num:06d}.jsonl"
        current_count = 0
        if out_path.exists():
            current_count = sum(
                1 for ln in out_path.read_text(encoding="utf-8").splitlines() if ln.strip()
            )

        with out_path.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                current_count += 1
                total += 1
                if current_count >= _ITEMS_PER_FILE:
                    file_num += 1
                    out_path = output_dir / f"wikidata_nl_{file_num:06d}.jsonl"
                    current_count = 0

        offset += len(bindings)  # advance by actual rows returned (not just valid records)

        logger.info(
            "[wikidata_nl] +%d entities (total=%d, next offset=%d)",
            len(records), total, offset,
        )

        # Polite delay — QLEVER is faster but still a shared service
        time.sleep(2 if use_qlever else 8)

    # Log acquisition
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source": SOURCE, "url": _QLEVER_ENDPOINT,
            "license": LICENSE, "acquisition_date": acq_date,
            "total_entities": total,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[wikidata_nl] Done. %d entities downloaded.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Dutch Wikidata entity descriptions (CC0)"
    )
    parser.add_argument("--output",       required=True, type=Path)
    parser.add_argument("--max-entities", type=int, default=8_000_000)
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()
    download(args.output, max_entities=args.max_entities, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
