"""
Download Dutch/Frisian/Afrikaans lexicographical data from Wikidata Lexemes.

Wikidata Lexemes (https://www.wikidata.org/wiki/Wikidata:Lexicographical_data)
are a separate namespace from Wikidata entities.  Each Lexeme represents a
word/lemma with: language, part-of-speech, forms (inflections), and senses
(definitions with usage examples).

This is DISTINCT from wikidata_nl.py which harvests entity descriptions.
Lexemes provide dictionary-like text: definitions, usage examples, forms.

License: CC0 1.0 Universal
SPARQL endpoint: QLEVER (primary) / official Wikidata endpoint (fallback)

Languages harvested:
  - Q7411  = Dutch (nl)
  - Q8641  = West Frisian (fy)
  - Q14196 = Afrikaans (af)
  - Q56004 = Limburgish (li)

Usage:
  python -m pipeline acquire --sources wikidata_lexemes
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

SOURCE  = "wikidata_lexemes"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "encyclopedic"

_QLEVER_ENDPOINT  = "https://qlever.cs.uni-freiburg.de/api/wikidata"
_SPARQL_ENDPOINT  = "https://query.wikidata.org/sparql"
_USER_AGENT       = "NederCorpus/0.1 (lexicographical data harvest; contact: corpus@example.nl)"

_BATCH_SIZE       = 2_000
_ITEMS_PER_FILE   = 20_000
_MAX_RETRIES      = 4

# Wikidata language QIDs to harvest
_LANG_CONFIGS: list[dict] = [
    {"qid": "Q7411",  "code": "nl",  "variant": "NL",  "country": "NL"},
    {"qid": "Q8641",  "code": "fy",  "variant": "FY",  "country": "NL"},
    {"qid": "Q14196", "code": "af",  "variant": "AF",  "country": "ZA"},
    {"qid": "Q56004", "code": "li",  "variant": "LI",  "country": "NL"},
]

# SPARQL query: lexemes with lemmas + optional glosses per language.
# Notes:
#   - Use full URI <http://www.w3.org/ns/lemon/ontolex#LexicalEntry> for type assertion
#     (PREFIX shorthand `a ontolex:LexicalEntry` returns 0 on Wikidata SPARQL endpoint)
#   - wikibase:language wd:QID returns 0 — rely on LANG() filter on lemma literal instead
# {{lang_code}} and {{offset}} are substituted at runtime.
_QLEVER_QUERY = """\
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX ontolex: <http://www.w3.org/ns/lemon/ontolex#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?lexeme ?lemma ?pos ?gloss WHERE {{
  ?lexeme a <http://www.w3.org/ns/lemon/ontolex#LexicalEntry> ;
          <http://wikiba.se/ontology#lemma> ?lemma .
  FILTER(LANG(?lemma) = "{lang_code}")
  OPTIONAL {{ ?lexeme wikibase:lexicalCategory ?pos . }}
  OPTIONAL {{ ?lexeme ontolex:sense ?sense . ?sense skos:definition ?gloss . FILTER(LANG(?gloss) = "{lang_code}") }}
}}
LIMIT {limit}
OFFSET {offset}
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


def _sparql_get(query: str, endpoint: str, timeout: int = 60) -> dict:
    """Execute a SPARQL SELECT query via GET and return the JSON result."""
    params = urllib.parse.urlencode({"query": query, "format": "json"})
    url = f"{endpoint}?{params}"
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/sparql-results+json",
    }
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = 20.0 * (attempt + 1)
            logger.warning("[wikidata_lexemes] Retry %d: %s (wait %.0fs)", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _harvest_language(lang_cfg: dict, output_dir: Path) -> int:
    """Harvest all lexemes for one language config. Returns total entries written."""
    lang_qid  = lang_cfg["qid"]
    lang_code = lang_cfg["code"]
    variant   = lang_cfg["variant"]

    total     = 0
    file_num  = 0
    offset    = 0
    done      = False
    endpoint  = _QLEVER_ENDPOINT  # may switch to fallback after first batch

    while not done:
        query = _QLEVER_QUERY.format(
            lang_qid=lang_qid,
            lang_code=lang_code,
            limit=_BATCH_SIZE,
            offset=offset,
        )
        logger.info("[wikidata_lexemes] %s OFFSET=%d", lang_code, offset)

        try:
            result = _sparql_get(query, endpoint, timeout=120)
        except Exception as exc:
            if endpoint == _QLEVER_ENDPOINT:
                logger.warning("[wikidata_lexemes] QLEVER failed: %s — trying Wikidata", exc)
                endpoint = _SPARQL_ENDPOINT
                try:
                    result = _sparql_get(query, endpoint, timeout=120)
                except Exception as exc2:
                    logger.error("[wikidata_lexemes] Wikidata fallback failed: %s — aborting %s", exc2, lang_code)
                    break
            else:
                logger.error("[wikidata_lexemes] Endpoint failed at OFFSET %d: %s — aborting %s", offset, exc, lang_code)
                break

        bindings = result.get("results", {}).get("bindings", [])
        if not bindings and offset == 0 and endpoint == _QLEVER_ENDPOINT:
            # QLEVER returned empty silently — fall back to Wikidata
            logger.info("[wikidata_lexemes] QLEVER returned 0 at OFFSET=0, switching to Wikidata SPARQL")
            endpoint = _SPARQL_ENDPOINT
            try:
                result = _sparql_get(query, endpoint, timeout=120)
                bindings = result.get("results", {}).get("bindings", [])
            except Exception as exc:
                logger.error("[wikidata_lexemes] Wikidata fallback failed: %s — aborting %s", exc, lang_code)
                break
        if not bindings:
            done = True
            break

        # Determine output file
        out_file = output_dir / f"wikidata_lexemes_{lang_code}_{file_num:04d}.jsonl"
        with out_file.open("a", encoding="utf-8") as fh:
            for row in bindings:
                lexeme_id = row.get("lexeme", {}).get("value", "").split("/")[-1]
                lemma     = row.get("lemma", {}).get("value", "")
                gloss     = row.get("gloss", {}).get("value", "")
                if not lemma and not gloss:
                    continue
                rec = {
                    "lexeme_id": lexeme_id,
                    "lemma": lemma,
                    "gloss": gloss,
                    "language_qid": lang_qid,
                    "language_code": lang_code,
                    "language_variant": variant,
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1

        _log(output_dir, f"{endpoint}?query=wikidata_lexemes_{lang_code}_offset{offset}")

        if len(bindings) < _BATCH_SIZE:
            done = True
        else:
            offset += _BATCH_SIZE
            if total > 0 and total % _ITEMS_PER_FILE == 0:
                file_num += 1
            time.sleep(1.0)  # polite delay between pages

    logger.info("[wikidata_lexemes] %s: %d entries total", lang_code, total)
    return total


def download(output_dir: Path, **_kwargs) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grand_total = 0
    for lang_cfg in _LANG_CONFIGS:
        logger.info("[wikidata_lexemes] Starting language: %s (%s)",
                    lang_cfg["code"], lang_cfg["qid"])
        n = _harvest_language(lang_cfg, output_dir)
        grand_total += n
    logger.info("[wikidata_lexemes] Grand total: %d lexeme entries", grand_total)
