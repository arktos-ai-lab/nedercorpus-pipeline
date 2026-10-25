"""
Download RIVM (Rijksinstituut voor Volksgezondheid en Milieu) publications.

RIVM is the Dutch National Institute for Public Health and the Environment.
As a government institution, its publications are in the public domain under
Art. 11 of the Dutch Auteurswet.

The publications are accessible via the DSpace OAI-PMH endpoint at:
  https://rivm.openrepository.com/oai/request

License: Public Domain (Art. 11 Auteurswet)
~10,000+ Dutch-language scientific and policy reports

Usage:
  python -m pipeline acquire --sources rivm
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "rivm"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "science"

_OAI_BASE = "https://rivm.openrepository.com/oai/request"

# OAI-PMH XML namespaces
_NS = {
    "oai":  "http://www.openarchives.org/OAI/2.0/",
    "dc":   "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}


def _oai_fetch(params: dict, retries: int = 3, timeout: int = 60) -> ET.Element:
    """Fetch OAI-PMH request and return parsed XML root."""
    url = f"{_OAI_BASE}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": "NederCorpus/0.1 (academic; government PD data)"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return ET.fromstring(resp.read())
        except Exception as exc:
            if attempt == retries - 1:
                raise
            logger.warning("[rivm] Request failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _extract_dc(record_el: ET.Element) -> dict | None:
    """Extract Dublin Core fields from an OAI-PMH record element."""
    metadata = record_el.find(".//oai_dc:dc", _NS)
    if metadata is None:
        return None

    def _get(tag: str) -> str:
        el = metadata.find(f"dc:{tag}", _NS)
        return (el.text or "").strip() if el is not None else ""

    def _get_all(tag: str) -> list[str]:
        return [(el.text or "").strip() for el in metadata.findall(f"dc:{tag}", _NS) if el.text]

    title       = _get("title")
    description = _get("description")
    language    = _get("language")
    date_       = _get("date")
    identifier  = _get("identifier")

    # Skip non-Dutch records
    if language and language not in ("nl", "nld", "dut", "nl_NL"):
        return None

    # Use title + description as the document text
    parts = [p for p in [title, description] if p]
    if not parts:
        return None

    pub_year: int | None = None
    if date_:
        m = re.search(r'\b(19|20)\d{2}\b', date_)
        if m:
            pub_year = int(m.group(0))

    return {
        "title":       title,
        "description": description,
        "language":    language,
        "date":        date_,
        "identifier":  identifier,
        "pub_year":    pub_year,
    }


def _log(output_dir: Path) -> None:
    entry = {
        "source":           SOURCE,
        "url":              _OAI_BASE,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    metadata_prefix: str = "oai_dc",
    max_records: int = 50_000,
    shard_size: int = 2_000,
    dry_run: bool = False,
) -> None:
    """
    Harvest RIVM publications via OAI-PMH ListRecords.

    Saves shards as rivm_shard_NNNNNN.jsonl.
    Each record: {title, description, language, date, identifier, pub_year}.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[rivm] [dry-run] would harvest %s", _OAI_BASE)
        return

    existing = len(list(output_dir.glob("rivm_shard_*.jsonl")))
    shard_idx = existing
    records_to_skip = existing * shard_size

    logger.info("[rivm] OAI-PMH harvest (max %d records)", max_records)

    resumption_token: str | None = None
    total = 0
    skipped = 0
    current_shard: list[str] = []

    def flush_shard() -> None:
        nonlocal shard_idx
        path = output_dir / f"rivm_shard_{shard_idx:06d}.jsonl"
        path.write_text("\n".join(current_shard) + "\n", encoding="utf-8")
        logger.info("[rivm] shard %06d written (%d records)", shard_idx, len(current_shard))
        current_shard.clear()
        shard_idx += 1

    while total < max_records:
        if resumption_token:
            params = {"verb": "ListRecords", "resumptionToken": resumption_token}
        else:
            params = {
                "verb":           "ListRecords",
                "metadataPrefix": metadata_prefix,
            }

        try:
            root = _oai_fetch(params)
        except Exception as exc:
            logger.error("[rivm] OAI-PMH fetch failed: %s", exc)
            break

        list_records = root.find("oai:ListRecords", _NS)
        if list_records is None:
            error = root.find("oai:error", _NS)
            if error is not None:
                logger.warning("[rivm] OAI error: %s", error.text)
            break

        for record_el in list_records.findall("oai:record", _NS):
            if skipped < records_to_skip:
                skipped += 1
                continue

            rec = _extract_dc(record_el)
            if rec is None:
                continue

            current_shard.append(json.dumps(rec, ensure_ascii=False))
            total += 1

            if len(current_shard) >= shard_size:
                flush_shard()

            if total >= max_records:
                break

        rt_el = list_records.find("oai:resumptionToken", _NS)
        if rt_el is not None and rt_el.text:
            resumption_token = rt_el.text.strip()
        else:
            break

        time.sleep(1.0)

    if current_shard:
        flush_shard()

    _log(output_dir)
    logger.info("[rivm] Done. %d records harvested.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Harvest RIVM publications via OAI-PMH (PD)")
    parser.add_argument("--output",      required=True, type=Path)
    parser.add_argument("--max-records", type=int, default=50_000)
    parser.add_argument("--shard-size",  type=int, default=2_000)
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
