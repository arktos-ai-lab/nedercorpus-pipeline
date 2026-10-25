"""
Download Dutch audiovisual heritage metadata from Open Beelden (openbeelden.nl).

Open Beelden is a platform of the Netherlands Institute for Sound and Vision
(Nederlands Instituut voor Beeld en Geluid) that publishes Dutch historical
audiovisual material under open licenses.  The metadata — Dutch-language titles,
descriptions, and shot-by-shot abstracts — is released under the
Creative Commons Public Domain Mark 1.0 (PDM 1.0).

OAI-PMH endpoint: https://www.openbeelden.nl/feeds/oai/
Metadata format: oai_dc

License: Public Domain (Creative Commons PDM 1.0)
  https://creativecommons.org/publicdomain/mark/1.0/
Domain: audiovisual_heritage
Country: NL

~11,000 Dutch-language records covering historical footage from Sound and Vision,
VPRO, Eye Film Institute, and other Dutch cultural institutions.

Usage:
  python -m pipeline acquire --sources open_beelden
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

SOURCE  = "open_beelden"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "audiovisual_heritage"

_OAI_ENDPOINT = "https://www.openbeelden.nl/feeds/oai/"
_USER_AGENT   = "NederCorpus/0.1 (academic; open heritage data; PDM 1.0)"

# OAI-PMH XML namespaces
_NS = {
    "oai":    "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc":     "http://purl.org/dc/elements/1.1/",
}


def _fetch_oai(params: dict, retries: int = 3, timeout: int = 30) -> str:
    """Fetch an OAI-PMH response, return raw XML string."""
    url = f"{_OAI_ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[open_beelden] OAI-PMH request failed: %s", exc)
                return ""
            time.sleep(3.0 * (attempt + 1))
    return ""


def _parse_records(xml_text: str) -> tuple[list[dict], str | None]:
    """
    Parse OAI-PMH ListRecords response.

    Returns (records, resumption_token).
    records: list of dicts with id, title, description, subjects, rights, date.
    resumption_token: next page token, or None if done.
    """
    records: list[dict] = []
    if not xml_text:
        return records, None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("[open_beelden] XML parse error: %s", exc)
        return records, None

    ns = _NS
    for record_el in root.findall(".//oai:record", ns):
        header = record_el.find("oai:header", ns)
        if header is None:
            continue
        status = header.get("status", "")
        if status == "deleted":
            continue

        identifier_el = header.find("oai:identifier", ns)
        if identifier_el is None or not identifier_el.text:
            continue
        record_id = identifier_el.text.strip()

        # Date
        datestamp_el = header.find("oai:datestamp", ns)
        datestamp = datestamp_el.text.strip() if datestamp_el is not None and datestamp_el.text else ""

        # DC metadata
        dc_el = record_el.find(".//oai_dc:dc", ns)
        if dc_el is None:
            continue

        title = ""
        title_el = dc_el.find("dc:title", ns)
        if title_el is not None and title_el.text:
            title = title_el.text.strip()

        # Only keep Dutch-language records
        lang_el = dc_el.find("dc:language", ns)
        lang = lang_el.text.strip() if lang_el is not None and lang_el.text else ""
        if lang and lang not in {"nl", "dut", "nld"}:
            continue

        description = ""
        desc_el = dc_el.find("dc:description", ns)
        if desc_el is not None and desc_el.text:
            description = desc_el.text.strip()

        subjects = []
        for subj_el in dc_el.findall("dc:subject", ns):
            if subj_el.text and subj_el.text.strip():
                subjects.append(subj_el.text.strip())

        rights = ""
        rights_el = dc_el.find("dc:rights", ns)
        if rights_el is not None and rights_el.text:
            rights = rights_el.text.strip()

        date = ""
        date_el = dc_el.find("dc:date", ns)
        if date_el is not None and date_el.text:
            date = date_el.text.strip()

        coverage = []
        for cov_el in dc_el.findall("dc:coverage", ns):
            if cov_el.text and cov_el.text.strip():
                coverage.append(cov_el.text.strip())

        if not title and not description:
            continue

        records.append({
            "id":          record_id,
            "title":       title,
            "description": description,
            "subjects":    subjects,
            "coverage":    coverage,
            "rights":      rights,
            "date":        date,
        })

    # Resumption token
    token_el = root.find(".//oai:resumptionToken", ns)
    token: str | None = None
    if token_el is not None and token_el.text and token_el.text.strip():
        token = token_el.text.strip()

    return records, token


def _log(output_dir: Path) -> None:
    entry = {
        "source":           SOURCE,
        "url":              _OAI_ENDPOINT,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Harvest Dutch audiovisual metadata from Open Beelden via OAI-PMH.

    Saves all records to open_beelden_records.jsonl.
    Supports resume: skips if output file already exists and is non-empty.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "open_beelden_records.jsonl"

    if out_file.exists() and out_file.stat().st_size > 0:
        logger.info("[open_beelden] Already acquired: %s — skipping.", out_file)
        return

    if dry_run:
        logger.info("[open_beelden] [dry-run] Would harvest %s", _OAI_ENDPOINT)
        return

    _log(output_dir)
    logger.info("[open_beelden] Harvesting OAI-PMH from %s", _OAI_ENDPOINT)

    total = 0
    params: dict = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}

    with out_file.open("w", encoding="utf-8") as fout:
        while True:
            xml_text = _fetch_oai(params)
            if not xml_text:
                logger.warning("[open_beelden] Empty response, stopping.")
                break

            records, token = _parse_records(xml_text)
            for rec in records:
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1

            logger.info(
                "[open_beelden] Page done: +%d records (total: %d), token: %s",
                len(records), total, bool(token),
            )

            if not token:
                break

            params = {"verb": "ListRecords", "resumptionToken": token}
            time.sleep(1.5)  # Polite rate limiting

    logger.info("[open_beelden] Done. %d Dutch records saved.", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Open Beelden Dutch AV heritage metadata (PDM 1.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
