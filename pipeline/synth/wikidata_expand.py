"""
Wikidata triple → coherent paragraph synthetic expansion.

For each Wikidata entity that has Dutch labels, fetches all property triples
and renders them into a coherent multi-sentence paragraph in the entity's
domain register.  Extends to all NederCorpus language variants that have
Wikidata labels (NL, AF, FY, LI, ZEA, VLS, PAP).

This is a zero-LLM, fully deterministic pipeline:
  - Entity selection: SPARQL on Wikidata Query Service
  - Template selection: hash(QID) % len(templates) — reproducible
  - Output uniqueness: each paragraph tied to a unique QID; no two are
    near-identical because each entity has a unique property combination
  - MinHash-safe: 0.85 Jaccard cannot conflate paragraphs about different
    entities (property values differ everywhere)

Output: one JSONL per language variant in the synth output directory:
  wikidata_expand_nl.jsonl, wikidata_expand_af.jsonl, etc.

Synthetic documents carry:
  meta.synthetic        = True
  meta.synthetic_method = "wikidata_expand"
  meta.language_variant = "NL" | "AF" | "FY" | "LI" | ...
  meta.source           = "wikidata_expand_{lang}"
  meta.domain           = "reference"
  meta.license          = "CC0"  (Wikidata is CC0)
  meta.country          = "NL" / "ZA" / "BE" / ...

Usage (CLI via __main__.py):
  python -m pipeline synth --method wikidata_expand [--lang NL] [--limit 10000]

Or directly:
  from pipeline.synth.wikidata_expand import run
  run(output_dir=Path("data/synth"), languages=["NL","AF"], limit=5000)
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator

from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------

# Maps our language_variant codes → (Wikidata language code, country, label)
_LANG_CONFIG: dict[str, dict] = {
    "NL":  {"wd_lang": "nl",  "country": "NL", "label": "Dutch"},
    "AF":  {"wd_lang": "af",  "country": "ZA", "label": "Afrikaans"},
    "FY":  {"wd_lang": "fy",  "country": "NL", "label": "West Frisian"},
    "LI":  {"wd_lang": "li",  "country": "NL", "label": "Limburgish"},
    "ZEA": {"wd_lang": "zea", "country": "NL", "label": "Zeelandic"},
    "VLS": {"wd_lang": "vls", "country": "NL", "label": "West Flemish"},
    "PAP": {"wd_lang": "pap", "country": "AW", "label": "Papiamento"},
}

_DEFAULT_LANGUAGES = ["NL", "AF", "FY", "LI"]

# ---------------------------------------------------------------------------
# SPARQL endpoint
# ---------------------------------------------------------------------------

_WD_API_URL = "https://www.wikidata.org/w/api.php"
_BATCH_SIZE = 50   # wbgetentities supports up to 50 IDs per request
_RETRY_DELAY = 10  # seconds between retries on 429/503
_MAX_RETRIES = 5

# ---------------------------------------------------------------------------
# Wikidata property IDs we care about
# Maps property P-ID → human-readable key used in templates
# ---------------------------------------------------------------------------

_PROP_MAP = {
    "P31":   "soort",                           # instance of
    "P17":   "land",                            # country
    "P131":  "gelegen in de bestuurlijke eenheid",
    "P571":  "datum van oprichting",            # inception
    "P576":  "ontbonden op",                    # dissolved/abolished
    "P569":  "geboortedatum",                   # date of birth
    "P570":  "sterfdatum",                      # date of death
    "P19":   "geboorteplaats",                  # place of birth
    "P20":   "sterfplaats",                     # place of death
    "P106":  "beroep",                          # occupation
    "P108":  "werkgever",                       # employer
    "P101":  "vakgebied",                       # field of work
    "P136":  "genre",                           # genre
    "P407":  "taal van het werk of naam",       # language of work
    "P1082": "bevolking",                       # population
    "P2046": "oppervlakte",                     # area
    "P84":   "architect",                       # architect
    "P170":  "maker",                           # creator
    "P495":  "land van oorsprong",              # country of origin
    "P577":  "publicatiedatum",                 # publication date
    "P123":  "uitgever",                        # publisher
    "P127":  "eigenaar",                        # owned by
    "P749":  "moederbedrijf",                   # parent organization
    "P112":  "oprichter",                       # founded by
    "P159":  "hoofdkantoor locatie",            # headquarters location
    "P856":  "officiële website",               # official website
}

# Properties whose values are QIDs → need label resolution
_QID_VALUE_PROPS = {
    "P31", "P17", "P131", "P19", "P20", "P106", "P108", "P101",
    "P136", "P407", "P495", "P127", "P749", "P112", "P159", "P170",
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _api_get(params: dict, retries: int = _MAX_RETRIES) -> dict | None:
    """Call the Wikidata MediaWiki API, return parsed JSON."""
    url = _WD_API_URL + "?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "NederCorpus/0.14 (synthetic data; https://github.com/KingOfTheAce2/NederCorpus)",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503):
                wait = _RETRY_DELAY * (attempt + 1)
                logger.warning("[wikidata_expand] HTTP %d, retrying in %ds", exc.code, wait)
                time.sleep(wait)
            else:
                logger.error("[wikidata_expand] API HTTP %d: %s", exc.code, exc.reason)
                return None
        except Exception as exc:
            logger.warning("[wikidata_expand] API error (attempt %d): %s", attempt + 1, exc)
            time.sleep(3)
    return None


# ---------------------------------------------------------------------------
# Entity fetching via sequential QID iteration
# ---------------------------------------------------------------------------


def _fetch_entity_batch(qids: list[str], lang: str) -> dict[str, dict]:
    """
    Fetch label + properties for up to 50 QIDs using wbgetentities API.
    Returns {qid: {"label": str, "props": [(prop_key, value_str)]}}
    """
    data = _api_get({
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "languages": lang,
        "languagefallback": "1",
        "props": "labels|claims",
        "format": "json",
    })
    if not data or "entities" not in data:
        return {}

    result: dict[str, dict] = {}
    entities = data["entities"]

    # Collect all QID values in claims that need label resolution
    qid_values_to_resolve: set[str] = set()
    for qid, entity in entities.items():
        if entity.get("missing"):
            continue
        for prop_id, prop_claims in entity.get("claims", {}).items():
            if prop_id not in _QID_VALUE_PROPS:
                continue
            for claim in prop_claims:
                snak = claim.get("mainsnak", {})
                if snak.get("snaktype") == "value":
                    dv = snak.get("datavalue", {})
                    if dv.get("type") == "wikibase-entityid":
                        val_qid = dv["value"].get("id", "")
                        if val_qid.startswith("Q"):
                            qid_values_to_resolve.add(val_qid)

    # Resolve QID values to labels in batches of 50
    qid_labels: dict[str, str] = {}
    qid_list = list(qid_values_to_resolve)
    for i in range(0, len(qid_list), 50):
        chunk = qid_list[i:i+50]
        label_data = _api_get({
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "languages": lang,
            "languagefallback": "1",
            "props": "labels",
            "format": "json",
        })
        if label_data and "entities" in label_data:
            for lqid, lent in label_data["entities"].items():
                lbl = lent.get("labels", {})
                if lang in lbl:
                    qid_labels[lqid] = lbl[lang]["value"]
                elif lbl:
                    # fallback to first available label
                    qid_labels[lqid] = next(iter(lbl.values()))["value"]

    # Build result
    for qid, entity in entities.items():
        if entity.get("missing"):
            continue
        labels = entity.get("labels", {})
        if lang not in labels:
            continue
        label = labels[lang]["value"]
        props: list[tuple[str, str]] = []

        for prop_id, prop_key in _PROP_MAP.items():
            claims = entity.get("claims", {}).get(prop_id, [])
            # Sort by rank: preferred > normal > deprecated
            rank_order = {"preferred": 0, "normal": 1, "deprecated": 2}
            claims = sorted(claims, key=lambda c: rank_order.get(c.get("rank", "normal"), 1))
            for claim in claims[:2]:  # max 2 values per property
                if claim.get("rank") == "deprecated":
                    continue
                snak = claim.get("mainsnak", {})
                if snak.get("snaktype") != "value":
                    continue
                dv = snak.get("datavalue", {})
                dv_type = dv.get("type", "")
                if dv_type == "string":
                    val = dv["value"]
                elif dv_type == "wikibase-entityid":
                    val_qid = dv["value"].get("id", "")
                    val = qid_labels.get(val_qid, "")
                elif dv_type == "time":
                    # Extract year (or full date for recent times)
                    raw = dv["value"].get("time", "")
                    # Format: +1885-01-01T00:00:00Z, precision 9=year 11=day
                    precision = dv["value"].get("precision", 9)
                    if raw.startswith("+") or raw.startswith("-"):
                        year = raw[1:5]
                        if precision >= 10:
                            month = raw[6:8]
                            day = raw[9:11]
                            val = f"{day}-{month}-{year}" if precision == 11 else f"{month}-{year}"
                        else:
                            val = year
                    else:
                        val = raw[:10]
                elif dv_type == "quantity":
                    amount = dv["value"].get("amount", "")
                    val = amount.lstrip("+")
                elif dv_type == "monolingualtext":
                    val = dv["value"].get("text", "")
                elif dv_type == "globecoordinate":
                    continue  # skip coordinates
                else:
                    continue
                if val and val.strip():
                    props.append((prop_key, val.strip()))
                break  # only first valid claim per property

        result[qid] = {"label": label, "props": props}

    return result


def _iter_entities(wd_lang: str, limit: int, start_qid: int = 1) -> Iterator[dict]:
    """
    Yield {qid, label, props} for entities with labels in `wd_lang`.

    Iterates sequentially from Q{start_qid} upward, skipping missing entities.
    Stops after `limit` valid entities are yielded.
    """
    fetched = 0
    q_num = start_qid
    consecutive_misses = 0

    while fetched < limit:
        # Build a batch of candidate QIDs
        batch_qids = [f"Q{q_num + i}" for i in range(_BATCH_SIZE)]
        q_num += _BATCH_SIZE

        entities = _fetch_entity_batch(batch_qids, wd_lang)

        batch_found = 0
        for qid in batch_qids:
            if qid in entities:
                yield {"qid": qid, **entities[qid]}
                fetched += 1
                batch_found += 1
                if fetched >= limit:
                    break

        # Track consecutive empty batches to detect end of QID space
        if batch_found == 0:
            consecutive_misses += 1
            if consecutive_misses >= 5:
                logger.info("[wikidata_expand] 5 consecutive empty batches, stopping")
                break
        else:
            consecutive_misses = 0

        time.sleep(0.3)  # polite delay


# ---------------------------------------------------------------------------
# Paragraph templates
# ---------------------------------------------------------------------------

# Each template is a callable: (label, props_dict) → str | None
# props_dict: {prop_label_lower: value_label}
# Returns None if insufficient data for this template.


def _prop_map(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Flatten first occurrence of each prop (lowercased) into a dict."""
    out: dict[str, str] = {}
    for prop, val in pairs:
        key = prop.strip().lower()
        if key not in out and val.strip():
            out[key] = val.strip()
    return out


# Dutch templates (NL)
_TEMPLATES_NL = [
    # 0: person
    lambda lbl, p: (
        f"{lbl} (geboren {p['geboortedatum']}) is een {p.get('beroep', p.get('werkgever', 'persoon'))}"
        + (f" uit {p.get('geboorteplaats', p.get('land', 'Nederland'))}" if any(k in p for k in ("geboorteplaats", "land")) else "")
        + "."
        + (f" {lbl} werkt op het gebied van {p['vakgebied']}." if "vakgebied" in p else "")
        if "geboortedatum" in p else None
    ),
    # 1: organization / institution
    lambda lbl, p: (
        f"{lbl} is een {p.get('soort', 'organisatie')} "
        f"{'opgericht in ' + p['datum van oprichting'] if 'datum van oprichting' in p else ''}"
        + (f", gevestigd in {p.get('hoofdkantoor locatie', p.get('gelegen in de bestuurlijke eenheid', ''))}."
           if any(k in p for k in ("hoofdkantoor locatie", "gelegen in de bestuurlijke eenheid")) else ".")
        + (f" Opgericht door {p['oprichter']}." if "oprichter" in p else "")
        if any(k in p for k in ("datum van oprichting", "soort", "hoofdkantoor locatie")) else None
    ),
    # 2: geographic place
    lambda lbl, p: (
        f"{lbl} is een {p.get('soort', 'plaats')}"
        + (f" in {p['land']}" if "land" in p else "")
        + "."
        + (f" De bevolking bedraagt {p['bevolking']}." if "bevolking" in p else "")
        + (f" Het behoort tot {p['gelegen in de bestuurlijke eenheid']}."
           if "gelegen in de bestuurlijke eenheid" in p else "")
        if any(k in p for k in ("land", "bevolking", "gelegen in de bestuurlijke eenheid")) else None
    ),
    # 3: creative work (film, book, album, song)
    lambda lbl, p: (
        f"{lbl} is een {p.get('soort', 'werk')} "
        f"{'van ' + p['auteur'] if 'auteur' in p else ('van ' + p['regisseur'] if 'regisseur' in p else '')}."
        + (f" Het is gepubliceerd in {p.get('publicatiedatum', p.get('verschijningsdatum', ''))}."
           if any(k in p for k in ("publicatiedatum", "verschijningsdatum")) else "")
        + (f" De taal is {p['taal van het werk of naam']}."
           if "taal van het werk of naam" in p else "")
        if any(k in p for k in ("auteur", "regisseur", "publicatiedatum")) else None
    ),
    # 4: generic fallback
    lambda lbl, p: (
        f"{lbl} is een {p.get('soort', 'entiteit')}."
        + (" " + ". ".join(
            f"Het heeft als {k} {v}"
            for k, v in list(p.items())[:3]
            if k not in ("soort",)
        ) + "." if len(p) > 1 else "")
        if p else None
    ),
]

# Afrikaans templates (AF)
_TEMPLATES_AF = [
    lambda lbl, p: (
        f"{lbl} (gebore {p['geboortedatum']}) is 'n {p.get('beroep', 'persoon')} "
        f"uit {p.get('geboorteland', p.get('land', 'Suid-Afrika'))}."
        if "geboortedatum" in p else None
    ),
    lambda lbl, p: (
        f"{lbl} is 'n {p.get('soort', 'organisasie')} "
        f"{'gestig in ' + p['datum van oprichting'] if 'datum van oprichting' in p else ''}."
        + (f" Dit is geleë in {p['locatie']}." if "locatie" in p else "")
        if any(k in p for k in ("datum van oprichting", "locatie", "soort")) else None
    ),
    lambda lbl, p: (
        f"{lbl} is 'n {p.get('soort', 'plek')} "
        f"{'in ' + p['land'] if 'land' in p else ''}."
        + (f" Die bevolking is {p['bevolking']}." if "bevolking" in p else "")
        if any(k in p for k in ("land", "bevolking")) else None
    ),
    lambda lbl, p: (
        f"{lbl} is 'n {p.get('soort', 'werk')} "
        f"{'deur ' + p.get('auteur', p.get('regisseur', '')) if any(k in p for k in ('auteur','regisseur')) else ''}."
        if any(k in p for k in ("auteur", "regisseur", "publicatiedatum")) else None
    ),
    lambda lbl, p: (
        f"{lbl} is 'n {p.get('soort', 'entiteit')}."
        if p else None
    ),
]

# West Frisian templates (FY)
_TEMPLATES_FY = [
    lambda lbl, p: (
        f"{lbl} (berne {p['geboortedatum']}) is in {p.get('beroep', 'persoan')} "
        f"út {p.get('geboorteland', p.get('land', 'Nederlân'))}."
        if "geboortedatum" in p else None
    ),
    lambda lbl, p: (
        f"{lbl} is in {p.get('soort', 'organisaasje')} "
        f"{'oprjochte yn ' + p['datum van oprichting'] if 'datum van oprichting' in p else ''}."
        if any(k in p for k in ("datum van oprichting", "locatie", "soort")) else None
    ),
    lambda lbl, p: (
        f"{lbl} is in {p.get('soort', 'plak')} "
        f"{'yn ' + p['land'] if 'land' in p else ''}."
        if any(k in p for k in ("land", "bevolking")) else None
    ),
    lambda lbl, p: (
        f"{lbl} is in {p.get('soort', 'wurk')}."
        if p else None
    ),
    lambda lbl, p: f"{lbl} is in {p.get('soort', 'ding')}." if p else None,
]

# Limburgish templates (LI) — use Dutch as base, minimal adaptation
_TEMPLATES_LI = [
    lambda lbl, p: (
        f"{lbl} (gebaore {p['geboortedatum']}) is 'ne {p.get('beroep', 'persoon')} "
        f"oet {p.get('geboorteland', p.get('land', 'Nederland'))}."
        if "geboortedatum" in p else None
    ),
    lambda lbl, p: (
        f"{lbl} is 'ne {p.get('soort', 'organisatie')} "
        f"{'opgericht in ' + p['datum van oprichting'] if 'datum van oprichting' in p else ''}."
        if any(k in p for k in ("datum van oprichting", "locatie", "soort")) else None
    ),
    lambda lbl, p: (
        f"{lbl} is 'ne {p.get('soort', 'plaets')} "
        f"{'in ' + p['land'] if 'land' in p else ''}."
        if any(k in p for k in ("land", "bevolking")) else None
    ),
    lambda lbl, p: (
        f"{lbl} is 'n {p.get('soort', 'wèrk')}."
        if p else None
    ),
    lambda lbl, p: f"{lbl} is {p.get('soort', 'e entiteit')}." if p else None,
]

# Papiamento templates (PAP)
_TEMPLATES_PAP = [
    lambda lbl, p: (
        f"{lbl} (naci {p['geboortedatum']}) ta un {p.get('beroep', 'persona')} "
        f"for di {p.get('geboorteland', p.get('land', 'Aruba'))}."
        if "geboortedatum" in p else None
    ),
    lambda lbl, p: (
        f"{lbl} ta un {p.get('soort', 'organisashon')} "
        f"{'funda na ' + p['datum van oprichting'] if 'datum van oprichting' in p else ''}."
        if any(k in p for k in ("datum van oprichting", "locatie", "soort")) else None
    ),
    lambda lbl, p: (
        f"{lbl} ta un {p.get('soort', 'lugar')} "
        f"{'na ' + p['land'] if 'land' in p else ''}."
        if any(k in p for k in ("land", "bevolking")) else None
    ),
    lambda lbl, p: (
        f"{lbl} ta un {p.get('soort', 'obra')}."
        if p else None
    ),
    lambda lbl, p: f"{lbl} ta un {p.get('soort', 'entidad')}." if p else None,
]

_TEMPLATES_BY_LANG = {
    "NL":  _TEMPLATES_NL,
    "AF":  _TEMPLATES_AF,
    "FY":  _TEMPLATES_FY,
    "LI":  _TEMPLATES_LI,
    "ZEA": _TEMPLATES_NL,   # Zeelandic: use Dutch templates (closest)
    "VLS": _TEMPLATES_NL,   # West Flemish: use Dutch templates
    "PAP": _TEMPLATES_PAP,
}


def _select_template(qid: str, templates: list) -> int:
    """Deterministically select a template index from the QID hash."""
    h = int(hashlib.md5(qid.encode()).hexdigest(), 16)
    return h % len(templates)


def _render_paragraph(qid: str, label: str, pairs: list[tuple[str, str]], lang_code: str) -> str | None:
    """Render a paragraph for the given entity in the given language variant."""
    templates = _TEMPLATES_BY_LANG.get(lang_code, _TEMPLATES_NL)
    # Remove self-referential values (e.g., country == label)
    cleaned = [(k, v) for k, v in pairs if v.lower() != label.lower()]
    props = _prop_map(cleaned)

    # Try the deterministically selected template first, then cycle through others
    start = _select_template(qid, templates)
    for i in range(len(templates)):
        idx = (start + i) % len(templates)
        try:
            result = templates[idx](label, props)
            if result:
                # Clean up: collapse multiple spaces, fix " ." → "."
                import re as _re
                result = _re.sub(r" +\.", ".", result.strip())
                result = _re.sub(r"  +", " ", result)
                if len(result) > 20:
                    return result
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------


def run(
    output_dir: Path,
    languages: list[str] | None = None,
    limit: int = 50_000,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Generate Wikidata-grounded paragraphs for each requested language variant.

    Args:
        output_dir: Directory to write wikidata_expand_{lang}.jsonl files.
        languages:  List of language variant codes (default: NL, AF, FY, LI).
        limit:      Max entities to fetch per language.
        dry_run:    If True, only log what would be done (no writes).

    Returns:
        Dict mapping language code → number of documents generated.
    """
    output_dir = Path(output_dir)
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    langs = languages or _DEFAULT_LANGUAGES
    counts: dict[str, int] = {}

    for lang_code in langs:
        cfg = _LANG_CONFIG.get(lang_code)
        if cfg is None:
            logger.warning("[wikidata_expand] Unknown language variant: %s", lang_code)
            continue

        wd_lang = cfg["wd_lang"]
        country = cfg["country"]
        out_path = output_dir / f"wikidata_expand_{lang_code.lower()}.jsonl"

        logger.info("[wikidata_expand] Starting %s (wd_lang=%s, limit=%d)", lang_code, wd_lang, limit)

        if dry_run:
            logger.info("[wikidata_expand] DRY RUN — would write %s", out_path)
            counts[lang_code] = 0
            continue

        docs_written = 0

        out_path.parent.mkdir(parents=True, exist_ok=True)
        fh = out_path.open("a", encoding="utf-8")
        try:
            for entity in _iter_entities(wd_lang, limit):
                qid = entity["qid"]
                label = entity["label"]
                pairs = entity.get("props", [])
                para = _render_paragraph(qid, label, pairs, lang_code)
                if para is None:
                    continue
                doc = Document(
                    text=para,
                    meta=DocumentMeta(
                        id=make_id(f"wikidata_expand_{lang_code.lower()}", qid),
                        source=f"wikidata_expand_{lang_code.lower()}",
                        source_url=f"https://www.wikidata.org/wiki/{qid}",
                        publication_year=None,
                        language_variant=lang_code,
                        country=country,
                        domain="reference",
                        license="CC0",
                        synthetic=True,
                        synthetic_method="wikidata_expand",
                    ),
                )
                fh.write(doc.to_jsonl() + "\n")
                docs_written += 1
        finally:
            fh.close()

        counts[lang_code] = docs_written
        logger.info("[wikidata_expand] %s: %d documents written to %s", lang_code, docs_written, out_path)

    return counts


# ---------------------------------------------------------------------------
# Q&A generation from existing wikidata_expand JSONL
# ---------------------------------------------------------------------------

# Question templates per property key, per language variant
# Format: (question_fn, answer_key)
_QA_TEMPLATES: dict[str, dict[str, tuple]] = {
    "NL": {
        "soort":           ("Wat voor type entiteit is {label}?", "soort"),
        "land":            ("In welk land bevindt {label} zich?", "land"),
        "bevolking":       ("Hoeveel inwoners heeft {label}?", "bevolking"),
        "datum van oprichting": ("In welk jaar is {label} opgericht of gesticht?", "datum van oprichting"),
        "geboortedatum":   ("Wanneer is {label} geboren?", "geboortedatum"),
        "beroep":          ("Wat is het beroep van {label}?", "beroep"),
        "vakgebied":       ("Op welk vakgebied werkt {label}?", "vakgebied"),
        "genre":           ("Tot welk genre behoort het werk {label}?", "genre"),
        "architect":       ("Wie ontwierp {label}?", "architect"),
        "oprichter":       ("Wie heeft {label} opgericht?", "oprichter"),
        "publicatiedatum": ("Wanneer is {label} gepubliceerd?", "publicatiedatum"),
    },
    "AF": {
        "soort":           ("Watter tipe entiteit is {label}?", "soort"),
        "land":            ("In watter land is {label} geleë?", "land"),
        "bevolking":       ("Hoeveel inwoners het {label}?", "bevolking"),
        "datum van oprichting": ("In watter jaar is {label} gestig?", "datum van oprichting"),
        "geboortedatum":   ("Wanneer is {label} gebore?", "geboortedatum"),
        "beroep":          ("Wat is {label} se beroep?", "beroep"),
    },
    "FY": {
        "soort":           ("Wat foar type is {label}?", "soort"),
        "land":            ("Yn hokker lân leit {label}?", "land"),
        "bevolking":       ("Hoefolle ynwenners hat {label}?", "bevolking"),
        "geboortedatum":   ("Wannear is {label} berne?", "geboortedatum"),
        "beroep":          ("Wat is it berop fan {label}?", "beroep"),
    },
    "LI": {
        "soort":           ("Wat veur type is {label}?", "soort"),
        "land":            ("In welk land ligk {label}?", "land"),
        "bevolking":       ("Hoevöal inwoeëners het {label}?", "bevolking"),
        "geboortedatum":   ("Wanneier is {label} gebaore?", "geboortedatum"),
    },
}


def _generate_qa_pairs(
    label: str,
    pairs: list[tuple[str, str]],
    lang_code: str,
) -> list[str]:
    """Generate Q&A pairs as formatted strings for a single entity."""
    templates = _QA_TEMPLATES.get(lang_code, _QA_TEMPLATES["NL"])
    props = _prop_map(pairs)
    qa_pairs = []
    for prop_key, (question_tmpl, answer_key) in templates.items():
        if answer_key in props:
            q = question_tmpl.format(label=label)
            a = props[answer_key]
            qa_pairs.append(f"V: {q}\nA: {a}")
    return qa_pairs


def run_qa(
    expand_output_dir: Path,
    qa_output_dir: Path,
    languages: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Generate Q&A pairs from completed wikidata_expand JSONL files.

    Reads wikidata_expand_{lang}.jsonl files and produces wikidata_qa_{lang}.jsonl
    with Q&A formatted documents.

    Args:
        expand_output_dir: Directory containing wikidata_expand_*.jsonl files.
        qa_output_dir:     Output directory for Q&A documents.
        languages:         Language variant codes to process (default: all found).
        dry_run:           If True, only log counts without writing.

    Returns:
        Dict mapping language code → number of Q&A documents written.
    """
    expand_output_dir = Path(expand_output_dir)
    qa_output_dir = Path(qa_output_dir)
    if not dry_run:
        qa_output_dir.mkdir(parents=True, exist_ok=True)

    langs = languages or list(_LANG_CONFIG.keys())
    counts: dict[str, int] = {}

    for lang_code in langs:
        src = expand_output_dir / f"wikidata_expand_{lang_code.lower()}.jsonl"
        if not src.exists():
            logger.debug("[wikidata_qa] No source file for %s: %s", lang_code, src)
            continue

        cfg = _LANG_CONFIG.get(lang_code)
        if not cfg:
            continue
        country = cfg["country"]

        out_path = qa_output_dir / f"wikidata_qa_{lang_code.lower()}.jsonl"
        logger.info("[wikidata_qa] Generating Q&A for %s from %s", lang_code, src.name)

        if dry_run:
            n = sum(1 for _ in open(src, encoding="utf-8"))
            logger.info("[wikidata_qa] DRY RUN — would process %d source docs", n)
            counts[lang_code] = 0
            continue

        docs_written = 0
        with open(src, encoding="utf-8") as in_fh, \
             out_path.open("w", encoding="utf-8") as out_fh:
            for line in in_fh:
                line = line.strip()
                if not line:
                    continue
                src_doc = Document.from_jsonl(line)
                qid = src_doc.meta.source_url.split("/")[-1] if src_doc.meta.source_url else ""
                label = src_doc.text.split(" is ")[0].split(" is 'n")[0].strip()

                # Re-parse props from the source document text isn't ideal;
                # for Q&A we need the original prop pairs.
                # Here we read them from the source_url and call the API again.
                # For a faster approach, we embed props in the doc text.
                # Compromise: generate Q&A from the paragraph text using pattern matching.
                # Extract facts from the rendered paragraph:
                text = src_doc.text
                qa_pairs = []

                # Detect population
                import re as _re
                pop_m = _re.search(r"bevolking bedraagt ([0-9,]+)|bevolking is ([0-9,]+)", text)
                if pop_m:
                    pop = pop_m.group(1) or pop_m.group(2)
                    qa_pairs.append(f"V: Hoeveel inwoners heeft {label}?\nA: {pop}")

                # Detect type
                type_m = _re.search(r"is een ([^.]+?)[. ]", text)
                if not type_m:
                    type_m = _re.search(r"is 'n ([^.]+?)[. ]", text)
                if type_m:
                    entity_type = type_m.group(1).strip()
                    if len(entity_type) > 2:
                        qa_pairs.append(f"V: Wat is {label}?\nA: {entity_type.capitalize()}")

                # Detect location
                loc_m = _re.search(r"in ([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\.", text)
                if loc_m:
                    loc = loc_m.group(1)
                    qa_pairs.append(f"V: Waar bevindt {label} zich?\nA: {loc}")

                if not qa_pairs:
                    continue

                qa_text = "\n\n".join(qa_pairs)
                qa_doc = Document(
                    text=qa_text,
                    meta=DocumentMeta(
                        id=make_id(f"wikidata_qa_{lang_code.lower()}", qid or ""),
                        source=f"wikidata_qa_{lang_code.lower()}",
                        source_url=src_doc.meta.source_url or "",
                        publication_year=None,
                        language_variant=lang_code,
                        country=country,
                        domain="reference",
                        license="CC0",
                        synthetic=True,
                        synthetic_method="wikidata_qa",
                    ),
                )
                out_fh.write(qa_doc.to_jsonl() + "\n")
                docs_written += 1

        counts[lang_code] = docs_written
        logger.info("[wikidata_qa] %s: %d Q&A documents written to %s", lang_code, docs_written, out_path)

    return counts
