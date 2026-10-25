"""
Download Dutch-language decisions from the European Court of Human Rights (ECHR).

HUDOC — the ECHR database — contains case law from the European Court of Human
Rights and the former European Commission of Human Rights. All decisions are
public domain (official legal decisions of an international court).

Dutch-language decisions exist for cases involving Netherlands, Belgium, and
occasionally other countries where Dutch is an official language.

HUDOC search API:
  https://hudoc.echr.coe.int/app/query/results
  Parameters: query (CQL), select (fields), sort, start, length

Language filter: languageisocode:"DUT"  (Dutch ISO 639-2 code)
Document types: JUDGMENTS, DECISIONS, COMMITTEE DECISIONS, COMMUNICATED CASES

License: Public Domain
  ECHR case law is official output of an international court — no copyright.
  Terms of use explicitly allow free reproduction for non-commercial and
  commercial purposes.

Size: ~254 Dutch-language decision summaries (verified 2026-03-24).
  These are translated summaries provided by the Dutch Ministry of Foreign
  Affairs — not the original French/English judgments. Small but unique
  register (international human rights law in Dutch).

Usage:
  python -m pipeline acquire --sources echr_nl
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "echr_nl"
LICENSE = "PD"
COUNTRY = "EU"
DOMAIN  = "legal"

_HUDOC_QUERY_API = "https://hudoc.echr.coe.int/app/query/results"
_HUDOC_DOC_API   = "https://hudoc.echr.coe.int/app/conversion/docx/html/body"
_USER_AGENT      = "NederCorpus/0.1 (academic research; Dutch legal corpus)"
_PAGE_SIZE       = 500
_MAX_RETRIES     = 4

# Fields to retrieve from the search index
_SELECT_FIELDS = ",".join([
    "itemid", "docname", "applicability", "article", "conclusion",
    "decisiondate", "judgementdate", "importance", "languageisocode",
    "typedescription", "violation", "nonviolation", "ecli",
    "documentcollectionid", "scl", "kpdate", "extractedappno", "respondent",
])

# CQL query: Dutch language, published decisions only
_BASE_QUERY = (
    'contentsitename:"ECHR" AND '
    'languageisocode:"DUT" AND '
    'documentcollectionid2:"GRANDCHAMBER" OR '
    'contentsitename:"ECHR" AND languageisocode:"DUT" AND '
    'documentcollectionid2:"CHAMBER" OR '
    'contentsitename:"ECHR" AND languageisocode:"DUT" AND '
    'documentcollectionid2:"COMMITTEE" OR '
    'contentsitename:"ECHR" AND languageisocode:"DUT" AND '
    'documentcollectionid2:"DECISIONS"'
)
# Simpler query that reliably returns Dutch docs:
_SIMPLE_QUERY = 'contentsitename:"ECHR" AND languageisocode:"DUT"'


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60) -> bytes:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/html, */*",
        "Referer": "https://hudoc.echr.coe.int/",
    }
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = 10.0 * (attempt + 1)
            logger.warning("[echr_nl] Retry %d: %s (wait %.0fs)", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _search_url(start: int) -> str:
    params = urllib.parse.urlencode({
        "query":  _SIMPLE_QUERY,
        "select": _SELECT_FIELDS,
        "sort":   "judgementdate Desc",
        "start":  start,
        "length": _PAGE_SIZE,
    })
    return f"{_HUDOC_QUERY_API}?{params}"


def _fetch_document_text(item_id: str) -> str:
    """
    Fetch the full text of an ECHR decision via the docx/html/body endpoint.
    Type-8 press releases return HTTP 204 No Content — returns "" for those.
    Type 15/9/14 (full translation documents) return positional HTML.
    """
    import urllib.error
    url = f"{_HUDOC_DOC_API}?library=ECHR&id={urllib.parse.quote(item_id)}"
    try:
        raw = _fetch(url, timeout=60)
        if not raw or len(raw) < 50:
            return ""
        text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 100:
            return text
    except urllib.error.HTTPError as exc:
        if exc.code in (204, 404, 410):
            return ""
        logger.debug("[echr_nl] HTTP %d fetching body for %s", exc.code, item_id)
    except Exception:
        pass
    return ""


def download(output_dir: Path, max_docs: int = 2_000, **_kwargs) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "echr_nl_decisions.jsonl"

    seen: set[str] = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                seen.add(json.loads(line).get("item_id", ""))
            except json.JSONDecodeError:
                pass
    logger.info("[echr_nl] %d decisions already collected", len(seen))

    total_written = 0
    start = 0

    while start < max_docs:
        url = _search_url(start)
        logger.info("[echr_nl] Querying HUDOC start=%d", start)

        try:
            data = json.loads(_fetch(url, timeout=60))
        except Exception as exc:
            logger.error("[echr_nl] Search API failed at start=%d: %s", start, exc)
            break

        # HUDOC response: {"resultcount": N, "results": [...]}
        result_count = data.get("resultcount", 0)
        results = data.get("results", [])

        if start == 0:
            logger.info("[echr_nl] Total Dutch decisions available: %d", result_count)

        if not results:
            logger.info("[echr_nl] No results at start=%d — done", start)
            break

        for entry in results:
            columns = entry.get("columns", {})
            item_id  = columns.get("itemid", "")
            if not item_id or item_id in seen:
                continue

            docname      = columns.get("docname", "")
            ecli         = columns.get("ecli", "")
            dec_date     = columns.get("decisiondate") or columns.get("judgementdate") or columns.get("kpdate") or ""
            article      = columns.get("article", "")
            conclusion   = columns.get("conclusion", "")
            importance   = columns.get("importance", "")
            violation    = columns.get("violation", "")
            nonviolation = columns.get("nonviolation", "")
            scl          = columns.get("scl", "")
            respondent   = columns.get("respondent", "")
            appno        = columns.get("extractedappno", "")
            typedesc     = columns.get("typedescription", "")

            # Year from decision date
            year = None
            m = re.search(r"(\d{4})", dec_date)
            if m:
                year = int(m.group(1))

            # Fetch full text (works for type 15/9/14; type 8 returns "")
            text = _fetch_document_text(item_id)
            if not text:
                # Build text from rich metadata fields (sufficient for type-8 press releases)
                parts = [p for p in [docname, conclusion, violation, nonviolation, scl] if p]
                text = "\n".join(parts).strip()

            if len(text) < 30:
                seen.add(item_id)
                continue

            rec = {
                "item_id":      item_id,
                "docname":      docname,
                "ecli":         ecli,
                "date":         dec_date,
                "year":         year,
                "article":      article,
                "conclusion":   conclusion,
                "violation":    violation,
                "nonviolation": nonviolation,
                "respondent":   respondent,
                "appno":        appno,
                "importance":   importance,
                "typedesc":     typedesc,
                "text":         text,
            }
            seen.add(item_id)
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _log(output_dir, f"https://hudoc.echr.coe.int/#{{{json.dumps({'itemid': [item_id]})}}}")
            total_written += 1
            time.sleep(0.1)

        logger.info("[echr_nl] Written %d / %d", total_written, result_count)

        if start + _PAGE_SIZE >= result_count:
            break
        start += _PAGE_SIZE
        time.sleep(1.5)

    logger.info("[echr_nl] Done. %d Dutch ECHR decisions written.", total_written)
