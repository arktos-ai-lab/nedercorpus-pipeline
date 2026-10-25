"""
Download WODC (Wetenschappelijk Onderzoek- en Documentatiecentrum) publications.

WODC is the research and documentation centre of the Dutch Ministry of Justice.
Publications are hosted on repository.wodc.nl (TU Delft DSpace instance).

License: CC0 1.0 Universal
Repository: https://repository.wodc.nl/
OAI-PMH: https://repository.wodc.nl/oai/request
~2,000+ Dutch justice/criminology/policy research reports
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "wodc"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "government"

_OAI_BASE  = "https://repository.wodc.nl/oai/request"
_USER_AGENT = "NederCorpus/0.1 (academic research; open government data)"
_BATCH_SIZE = 200


def _fetch(url: str, retries: int = 3, timeout: int = 30) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()
    out_file = output_dir / "wodc_records.jsonl"

    # Skip if already acquired
    if out_file.exists() and out_file.stat().st_size > 0:
        logger.info("[wodc] Already acquired: %s — skipping.", out_file)
        return

    if dry_run:
        logger.info("[wodc] [dry-run] Would harvest OAI-PMH from %s", _OAI_BASE)
        return

    logger.info("[wodc] Harvesting OAI-PMH from %s", _OAI_BASE)
    _log(output_dir, _OAI_BASE)

    total = 0
    resumption_token: str | None = None
    first = True

    with out_file.open("w", encoding="utf-8") as fout:
        while True:
            if first:
                url = (
                    f"{_OAI_BASE}?verb=ListRecords"
                    f"&metadataPrefix=oai_dc"
                    f"&set=col_10068_30"   # WODC reports collection
                )
                first = False
            elif resumption_token:
                encoded = urllib.parse.quote(resumption_token, safe="")
                url = f"{_OAI_BASE}?verb=ListRecords&resumptionToken={encoded}"
            else:
                break

            try:
                data = _fetch(url)
            except Exception as exc:
                logger.warning("[wodc] OAI fetch failed: %s", exc)
                break

            try:
                root = ET.fromstring(data)
            except ET.ParseError as exc:
                logger.warning("[wodc] XML parse error: %s", exc)
                break

            ns = {
                "oai": "http://www.openarchives.org/OAI/2.0/",
                "dc":  "http://purl.org/dc/elements/1.1/",
                "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
            }

            for record in root.findall(".//oai:record", ns):
                header = record.find("oai:header", ns)
                if header is not None:
                    status = header.get("status", "")
                    if status == "deleted":
                        continue

                metadata = record.find(".//oai_dc:dc", ns)
                if metadata is None:
                    continue

                def _vals(tag: str) -> list[str]:
                    return [
                        e.text.strip()
                        for e in metadata.findall(f"dc:{tag}", ns)
                        if e.text and e.text.strip()
                    ]

                titles = _vals("title")
                # Skip non-Dutch (description language check)
                descriptions = _vals("description")
                subjects = _vals("subject")
                creators = _vals("creator")
                dates = _vals("date")
                identifiers = _vals("identifier")
                languages = _vals("language")

                # Filter: keep only Dutch-language records
                lang_ok = not languages or any(
                    lg.lower().startswith(("nl", "dut", "ned"))
                    for lg in languages
                )
                if not lang_ok:
                    continue

                # Combine text fields
                text_parts = titles + descriptions + subjects
                text = "\n".join(p for p in text_parts if p)
                if len(text) < 50:
                    continue

                # Extract year
                year: int | None = None
                for d in dates:
                    import re
                    m = re.search(r'\b(19|20)\d{2}\b', d)
                    if m:
                        year = int(m.group(0))
                        break

                # Source URL: prefer handle.net or repository URL
                source_url = next(
                    (i for i in identifiers if "http" in i.lower()),
                    identifiers[0] if identifiers else _OAI_BASE,
                )

                record_obj = {
                    "title": titles[0] if titles else "",
                    "text": text,
                    "creators": creators,
                    "year": year,
                    "source_url": source_url,
                    "license": LICENSE,
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "language": "nl",
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(record_obj, ensure_ascii=False) + "\n")
                total += 1

            # Check resumption token
            rt_el = root.find(".//oai:resumptionToken", ns)
            if rt_el is not None and rt_el.text and rt_el.text.strip():
                resumption_token = rt_el.text.strip()
                logger.info("[wodc] %d records so far, continuing...", total)
            else:
                break

            time.sleep(1.0)

    # If set-based harvest got 0, fall back to full harvest
    if total == 0:
        logger.info("[wodc] Set harvest got 0 records, trying full harvest...")
        resumption_token = None
        first = True

        with out_file.open("w", encoding="utf-8") as fout:
            while True:
                if first:
                    url = (
                        f"{_OAI_BASE}?verb=ListRecords"
                        f"&metadataPrefix=oai_dc"
                    )
                    first = False
                elif resumption_token:
                    encoded = urllib.parse.quote(resumption_token, safe="")
                    url = f"{_OAI_BASE}?verb=ListRecords&resumptionToken={encoded}"
                else:
                    break

                try:
                    data = _fetch(url)
                except Exception as exc:
                    logger.warning("[wodc] Full harvest fetch failed: %s", exc)
                    break

                try:
                    root = ET.fromstring(data)
                except ET.ParseError as exc:
                    logger.warning("[wodc] XML parse error: %s", exc)
                    break

                ns = {
                    "oai": "http://www.openarchives.org/OAI/2.0/",
                    "dc":  "http://purl.org/dc/elements/1.1/",
                    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
                }

                for record in root.findall(".//oai:record", ns):
                    header = record.find("oai:header", ns)
                    if header is not None:
                        status = header.get("status", "")
                        if status == "deleted":
                            continue

                    metadata = record.find(".//oai_dc:dc", ns)
                    if metadata is None:
                        continue

                    def _vals2(tag: str) -> list[str]:
                        return [
                            e.text.strip()
                            for e in metadata.findall(f"dc:{tag}", ns)
                            if e.text and e.text.strip()
                        ]

                    titles = _vals2("title")
                    descriptions = _vals2("description")
                    subjects = _vals2("subject")
                    creators = _vals2("creator")
                    dates = _vals2("date")
                    identifiers = _vals2("identifier")
                    languages = _vals2("language")

                    lang_ok = not languages or any(
                        lg.lower().startswith(("nl", "dut", "ned"))
                        for lg in languages
                    )
                    if not lang_ok:
                        continue

                    text_parts = titles + descriptions + subjects
                    text = "\n".join(p for p in text_parts if p)
                    if len(text) < 50:
                        continue

                    year = None
                    for d in dates:
                        import re
                        m = re.search(r'\b(19|20)\d{2}\b', d)
                        if m:
                            year = int(m.group(0))
                            break

                    source_url = next(
                        (i for i in identifiers if "http" in i.lower()),
                        identifiers[0] if identifiers else _OAI_BASE,
                    )

                    record_obj = {
                        "title": titles[0] if titles else "",
                        "text": text,
                        "creators": creators,
                        "year": year,
                        "source_url": source_url,
                        "license": LICENSE,
                        "country": COUNTRY,
                        "domain": DOMAIN,
                        "language": "nl",
                        "acquisition_date": acq_date,
                    }
                    fout.write(json.dumps(record_obj, ensure_ascii=False) + "\n")
                    total += 1

                rt_el = root.find(".//oai:resumptionToken", ns)
                if rt_el is not None and rt_el.text and rt_el.text.strip():
                    resumption_token = rt_el.text.strip()
                    logger.info("[wodc] %d records so far...", total)
                else:
                    break

                time.sleep(1.0)

    logger.info("[wodc] Done. %d Dutch records saved to %s", total, out_file)
