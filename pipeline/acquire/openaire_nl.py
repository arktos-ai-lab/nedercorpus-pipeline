"""
Download Dutch academic papers and theses via OpenAIRE Search API.

OpenAIRE aggregates European open access research. This harvester
targets the Dutch-language subset, filtering for records with Dutch
language tags ("nld") and non-empty abstracts.

Search API endpoint: https://api.openaire.eu/search/publications
  ?country=NL&format=json&size=100&page=N

NOTE: The OAI-PMH endpoints (api.openaire.eu/oai_pmh and
api.openaire.eu/oai_pmh/v2/oai) are both dead (404 as of 2026-03).
This module was rewritten to use the Search REST API which returns
~3.2M NL-country publications. Dutch-language subset (~500k+ with "nld").

License: CC-BY (typical for Dutch OA papers); per-document rights
stored in the `rights` field of each saved record.

Usage:
  python -m pipeline acquire --sources openaire_nl
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

SOURCE  = "openaire_nl"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "science"

_SEARCH_API = "https://api.openaire.eu/search/publications"

_HEADERS = {
    "User-Agent": "NederCorpus/0.1 (academic; Dutch OA research data)",
    "Accept":     "application/json",
}


def _fetch_page(
    page: int,
    size: int = 100,
    timeout: int = 60,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """
    Fetch one page from the OpenAIRE search API.

    The API is hard-capped at 100 pages (10,000 records) per query.
    Use from_date/to_date (YYYY-MM-DD) to window results and access more records.
    """
    params: dict[str, object] = {
        "country": "NL",
        "format":  "json",
        "size":    size,
        "page":    page,
    }
    if from_date:
        params["fromDateAccepted"] = from_date
    if to_date:
        params["toDateAccepted"] = to_date
    url = f"{_SEARCH_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            if attempt == 2:
                raise
            logger.warning("[openaire_nl] page %d attempt %d failed: %s", page, attempt + 1, exc)
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _extract_record(raw: dict) -> dict | None:
    """
    Extract a Dutch-language record from a single OpenAIRE result item.

    Returns None if not Dutch, no usable abstract, or extraction error.
    """
    try:
        return _extract_record_inner(raw)
    except Exception:
        return None


def _extract_record_inner(raw: dict) -> dict | None:
    try:
        entity = raw["metadata"]["oaf:entity"]["oaf:result"]
    except (KeyError, TypeError):
        return None

    # Language filter — keep only "nld"
    lang_info = entity.get("language")
    if isinstance(lang_info, list):
        lang_code = next(
            (l.get("@classid", "") for l in lang_info if isinstance(l, dict)), ""
        )
    elif isinstance(lang_info, dict):
        lang_code = lang_info.get("@classid", "")
    else:
        lang_code = ""

    if lang_code not in {"nld", "dut/nld", "dut", "nl"}:
        return None

    # Abstract — required
    desc = entity.get("description")
    if isinstance(desc, list):
        abstract = next((d.get("$", "") for d in desc if isinstance(d, dict)), "")
    elif isinstance(desc, dict):
        abstract = desc.get("$", "")
    else:
        abstract = ""

    abstract = str(abstract).strip()
    if not abstract:
        return None

    # Title
    titles = entity.get("title", [])
    if isinstance(titles, dict):
        titles = [titles]
    title = ""
    for t in titles:
        if isinstance(t, dict):
            if t.get("@classid") == "main title":
                title = str(t.get("$", "")).strip()
                break
    if not title and titles:
        # Fall back to first title
        first = titles[0]
        title = str(first.get("$", "") if isinstance(first, dict) else "").strip()

    # Date
    date_info = entity.get("dateofacceptance")
    date_val = ""
    if isinstance(date_info, dict):
        date_val = date_info.get("$", "")
    elif isinstance(date_info, str):
        date_val = date_info

    pub_year: int | None = None
    if date_val:
        m = re.search(r'\b(19|20)\d{2}\b', date_val)
        if m:
            pub_year = int(m.group(0))

    # Identifier
    ids = entity.get("pid", [])
    if isinstance(ids, dict):
        ids = [ids]
    identifier = ""
    for id_item in ids:
        if isinstance(id_item, dict) and id_item.get("@classid") == "doi":
            identifier = str(id_item.get("$", ""))
            break
    if not identifier and ids:
        first_id = ids[0]
        identifier = str(first_id.get("$", "") if isinstance(first_id, dict) else "")

    # Rights / best access right
    bar = entity.get("bestaccessright")
    if isinstance(bar, dict):
        rights = bar.get("@classname", bar.get("@classid", "CC-BY"))
    else:
        rights = "CC-BY"

    return {
        "title":      title,
        "abstract":   abstract,
        "language":   lang_code,
        "date":       date_val,
        "identifier": identifier,
        "pub_year":   pub_year,
        "rights":     rights,
    }


def _log(output_dir: Path) -> None:
    entry = {
        "source":           SOURCE,
        "url":              _SEARCH_API,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    max_records: int = 50_000,
    shard_size: int = 5_000,
    dry_run: bool = False,
) -> None:
    """
    Harvest Dutch-language open-access publications from OpenAIRE Search API.

    The API is hard-capped at 100 pages (10,000 records) per query. To access
    more records, iterates year by year (year_from to year_to), yielding up to
    10,000 NL records per year. At ~6% Dutch hit rate: ~600 Dutch docs/year.

    Saves shards as openaire_nl_shard_NNNNNN.jsonl.
    Each record: {title, abstract, language, date, identifier, pub_year, rights}.

    Resume: tracks progress via a per-window marker file (openaire_nl_progress.json).
    """
    import json as _json

    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[openaire_nl] [dry-run] would harvest %s", _SEARCH_API)
        return

    # Year windows from 1995 to current year, newest-first for recency bias
    this_year = datetime.date.today().year
    years = list(range(this_year, 1994, -1))

    # Load progress
    progress_file = output_dir / "openaire_nl_progress.json"
    if progress_file.exists():
        progress = json.loads(progress_file.read_text(encoding="utf-8"))
    else:
        progress = {}  # year (str) → "done" | page_last_completed

    existing_shards = sorted(output_dir.glob("openaire_nl_shard_*.jsonl"))
    shard_idx = len(existing_shards)
    total_saved = sum(
        sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
        for p in existing_shards
    )

    logger.info(
        "[openaire_nl] Starting year-windowed harvest. Already saved: %d. "
        "Years %d–1995.",
        total_saved, this_year,
    )

    current_shard: list[str] = []
    PAGE_SIZE = 100
    MAX_PAGES = 100  # hard API cap

    def flush_shard() -> None:
        nonlocal shard_idx
        path = output_dir / f"openaire_nl_shard_{shard_idx:06d}.jsonl"
        path.write_text("\n".join(current_shard) + "\n", encoding="utf-8")
        logger.info(
            "[openaire_nl] shard %06d written (%d records)", shard_idx, len(current_shard)
        )
        current_shard.clear()
        shard_idx += 1

    def save_progress() -> None:
        progress_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

    for year in years:
        if total_saved >= max_records:
            break

        y_str = str(year)
        if progress.get(y_str) == "done":
            continue

        from_date = f"{year}-01-01"
        to_date   = f"{year}-12-31"
        start_page = int(progress.get(y_str, 0)) + 1

        try:
            probe = _fetch_page(1, size=1, from_date=from_date, to_date=to_date)
            year_total = int(
                probe.get("response", {})
                     .get("header", {})
                     .get("total", {})
                     .get("$", 0)
            )
        except Exception as exc:
            logger.warning("[openaire_nl] Year %d probe failed: %s", year, exc)
            continue

        if year_total == 0:
            progress[y_str] = "done"
            save_progress()
            continue

        logger.info(
            "[openaire_nl] Year %d: %d NL publications (starting page %d)",
            year, year_total, start_page,
        )

        year_pages = min(year_total // PAGE_SIZE + 1, MAX_PAGES)
        year_saved = 0

        for page in range(start_page, year_pages + 1):
            if total_saved >= max_records:
                break

            try:
                data = _fetch_page(page, size=PAGE_SIZE, from_date=from_date, to_date=to_date)
            except Exception as exc:
                logger.error("[openaire_nl] Year %d page %d failed: %s", year, page, exc)
                progress[y_str] = page - 1
                save_progress()
                break

            results_block = (
                data.get("response", {})
                    .get("results", {})
                    .get("result", [])
            )
            if not results_block:
                break
            if isinstance(results_block, dict):
                results_block = [results_block]

            for raw in results_block:
                rec = _extract_record(raw)
                if rec is None:
                    continue
                current_shard.append(json.dumps(rec, ensure_ascii=False))
                total_saved += 1
                year_saved  += 1

                if len(current_shard) >= shard_size:
                    flush_shard()

                if total_saved >= max_records:
                    break

            progress[y_str] = page
            time.sleep(0.3)

        else:
            # All pages for this year completed
            progress[y_str] = "done"

        save_progress()
        logger.info(
            "[openaire_nl] Year %d done: %d Dutch records (total: %d)",
            year, year_saved, total_saved,
        )
        time.sleep(0.5)

    if current_shard:
        flush_shard()

    _log(output_dir)
    logger.info("[openaire_nl] Done. %d Dutch OA records saved total.", total_saved)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Harvest Dutch OA publications from OpenAIRE Search API"
    )
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--max-records", type=int, default=100_000)
    parser.add_argument("--shard-size",  type=int, default=5_000)
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        max_records=args.max_records,
        shard_size=args.shard_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
