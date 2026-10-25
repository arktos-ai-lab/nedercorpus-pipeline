"""
Download EuroVoc — the EU's multilingual controlled vocabulary / thesaurus.

EuroVoc contains ~7,000 concept descriptors organized across 21 subject fields,
covering EU legal/policy vocabulary with Dutch preferred labels, alternative
labels, and scope notes (1–3 sentence definitions).

License: CC-BY 4.0 (EU Publications Office open data policy)
SPARQL endpoint: https://publications.europa.eu/webapi/rdf/sparql
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

SOURCE  = "eurovoc"
LICENSE = "CC-BY"
COUNTRY = "EU"
DOMAIN  = "reference"

_SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
_UA     = "NederCorpus/0.1 (academic; Dutch NLP corpus; CC-BY open data)"

# Fetch all EuroVoc concepts with Dutch labels and scope notes
_QUERY = """
SELECT DISTINCT ?concept ?label ?scopeNote WHERE {{
  ?concept a <http://www.w3.org/2004/02/skos/core#Concept> .
  ?concept <http://www.w3.org/2004/02/skos/core#inScheme>
           <http://eurovoc.europa.eu/100141> .
  ?concept <http://www.w3.org/2004/02/skos/core#prefLabel> ?label .
  FILTER(LANG(?label) = "nl")
  OPTIONAL {{
    ?concept <http://www.w3.org/2004/02/skos/core#scopeNote> ?scopeNote .
    FILTER(LANG(?scopeNote) = "nl")
  }}
}}
ORDER BY ?concept
LIMIT {limit}
OFFSET {offset}
"""

# Separate query for alt labels (can be multiple per concept)
_ALT_QUERY = """
SELECT ?concept ?altLabel WHERE {{
  ?concept a <http://www.w3.org/2004/02/skos/core#Concept> .
  ?concept <http://www.w3.org/2004/02/skos/core#inScheme>
           <http://eurovoc.europa.eu/100141> .
  ?concept <http://www.w3.org/2004/02/skos/core#altLabel> ?altLabel .
  FILTER(LANG(?altLabel) = "nl")
}}
ORDER BY ?concept
LIMIT {limit}
OFFSET {offset}
"""


def _sparql(query: str, retries: int = 3, timeout: int = 60) -> list[dict]:
    params = urllib.parse.urlencode({
        "query":  query,
        "format": "json",
    })
    url = f"{_SPARQL}?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/sparql-results+json, application/json",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", {}).get("bindings", [])
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[eurovoc] SPARQL failed: %s", exc)
                return []
            wait = 5.0 * (attempt + 1)
            logger.warning("[eurovoc] Retry %d: %s (wait %.0fs)", attempt + 1, exc, wait)
            time.sleep(wait)
    return []


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download EuroVoc Dutch concepts via SPARQL.

    Saves:
      eurovoc_concepts.jsonl  — one record per EuroVoc concept with Dutch metadata
      eurovoc_altlabels.jsonl — alternate Dutch labels per concept (supplemental)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[eurovoc] [dry-run] would query SPARQL at %s", _SPARQL)
        return

    concepts_path = output_dir / "eurovoc_concepts.jsonl"
    alt_path      = output_dir / "eurovoc_altlabels.jsonl"
    acq_date      = datetime.date.today().isoformat()

    # --- Phase 1: main concepts (prefLabel + scopeNote) ---
    if concepts_path.exists():
        existing = sum(1 for _ in concepts_path.open(encoding="utf-8"))
        logger.info("[eurovoc] %d concepts already saved, skipping phase 1", existing)
        existing_ids: set[str] = set()
        for line in concepts_path.read_text(encoding="utf-8").splitlines():
            try:
                existing_ids.add(json.loads(line)["id"])
            except Exception:
                pass
    else:
        existing = 0
        existing_ids = set()

    if not existing_ids:
        logger.info("[eurovoc] Fetching concepts (prefLabel + scopeNote)…")
        batch_size = 500
        offset     = 0
        total      = 0

        with concepts_path.open("a", encoding="utf-8") as fout:
            while True:
                query    = _QUERY.format(limit=batch_size, offset=offset)
                bindings = _sparql(query)
                if not bindings:
                    logger.info("[eurovoc] No more results at offset %d", offset)
                    break

                for b in bindings:
                    concept_id = b["concept"]["value"]
                    if concept_id in existing_ids:
                        continue
                    label      = b["label"]["value"].strip()
                    scope_note = b.get("scopeNote", {}).get("value", "").strip()
                    rec = {
                        "id":         concept_id,
                        "label":      label,
                        "scope_note": scope_note,
                        "acquisition_date": acq_date,
                    }
                    fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    existing_ids.add(concept_id)
                    total += 1

                logger.info("[eurovoc] Concepts: offset %d, batch %d, total %d",
                            offset, len(bindings), total)
                offset += batch_size
                time.sleep(1.0)

        logger.info("[eurovoc] Phase 1 done: %d concepts", total)

    # --- Phase 2: alt labels ---
    if alt_path.exists():
        logger.info("[eurovoc] Alt labels already saved, skipping phase 2")
    else:
        logger.info("[eurovoc] Fetching altLabels…")
        batch_size = 1000
        offset     = 0
        alt_total  = 0

        with alt_path.open("w", encoding="utf-8") as fout:
            while True:
                query    = _ALT_QUERY.format(limit=batch_size, offset=offset)
                bindings = _sparql(query)
                if not bindings:
                    break
                for b in bindings:
                    rec = {
                        "concept_id": b["concept"]["value"],
                        "alt_label":  b["altLabel"]["value"].strip(),
                    }
                    fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    alt_total += 1
                logger.info("[eurovoc] AltLabels: offset %d, batch %d, total %d",
                            offset, len(bindings), alt_total)
                offset += batch_size
                time.sleep(1.0)

        logger.info("[eurovoc] Phase 2 done: %d alt labels", alt_total)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source":           SOURCE,
            "url":              _SPARQL,
            "license":          LICENSE,
            "acquisition_date": acq_date,
        }, ensure_ascii=False) + "\n")

    logger.info("[eurovoc] Done.")


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download EuroVoc Dutch vocabulary (CC-BY 4.0)")
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
