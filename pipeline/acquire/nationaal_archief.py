"""
Download archival finding aids (inventarissen) from the Nationaal Archief via OAI-PMH.

The Nationaal Archief (National Archives of the Netherlands) publishes its
archival descriptions under CC0 via an OAI-PMH endpoint. These are EAD
(Encoded Archival Description) XML documents describing collections and their
inventories, written in Dutch.

OAI-PMH endpoint: https://service.archief.nl/gaf/oai/!open_oai.OAIHandler
Metadata prefixes available: oai_dc, oai_ead2002
License: CC0 (https://www.nationaalarchief.nl/open-data)

Each OAI record contains a full finding aid (inventory description) for one
archival collection. These descriptions are rich Dutch prose explaining the
provenance, scope, and content of the collection.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "nationaal_archief"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "archival"

_OAI_BASE   = "https://service.archief.nl/gaf/oai/!open_oai.OAIHandler"
_META_PREFIX = "oai_dc"  # Use DC for broad coverage; EAD can be requested separately
_USER_AGENT  = "NederCorpus/0.1 (academic; CC0 open data; Dutch archival corpus)"

_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc":  "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, retries: int = 4, timeout: int = 60) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 5.0 * (attempt + 1)
            logger.warning("Retry %d: %s (wait %.0fs)", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _oai_url(verb: str, extra: dict[str, str] | None = None) -> str:
    params: dict[str, str] = {"verb": verb}
    if extra:
        params.update(extra)
    return f"{_OAI_BASE}?{urllib.parse.urlencode(params)}"


def _parse_records(xml_bytes: bytes) -> tuple[list[dict], str | None]:
    """
    Parse OAI-PMH ListRecords response.

    Returns:
        records:        list of {id, title, description, date, xml_bytes}
        resumption_token: string token for next page, or None if finished
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("XML parse error: %s", exc)
        return [], None

    records = []
    for rec_el in root.findall(".//oai:record", _NS):
        header = rec_el.find("oai:header", _NS)
        if header is None:
            continue
        status = header.get("status", "")
        if status == "deleted":
            continue

        id_el = header.find("oai:identifier", _NS)
        rec_id = id_el.text.strip() if id_el is not None and id_el.text else ""

        metadata = rec_el.find("oai:metadata", _NS)
        if metadata is None:
            continue

        # The Nationaal Archief endpoint returns rdf:RDF metadata, not oai_dc:dc.
        # Extract Dutch text from DC/DCT/RDFS/SKOS elements anywhere in the metadata.
        _DC  = "http://purl.org/dc/elements/1.1/"
        _DCT = "http://purl.org/dc/terms/"
        _RDF_NS = "http://www.w3.org/2000/01/rdf-schema#"
        _SKOS = "http://www.w3.org/2004/02/skos/core#"

        def _texts(ns_tag: str) -> list[str]:
            return [
                el.text.strip()
                for el in metadata.iter(ns_tag)
                if el.text and el.text.strip() and len(el.text.strip()) > 3
            ]

        titles = (
            _texts(f"{{{_DC}}}title") or
            _texts(f"{{{_DCT}}}title") or
            _texts(f"{{{_SKOS}}}prefLabel") or
            _texts(f"{{{_RDF_NS}}}label")
        )
        descriptions = (
            _texts(f"{{{_DCT}}}description") or
            _texts(f"{{{_DC}}}description")
        )
        dates = _texts(f"{{{_DCT}}}date") or _texts(f"{{{_DC}}}date")

        title       = titles[0] if titles else ""
        description = " ".join(descriptions)
        date_str    = dates[0] if dates else ""

        if not title and not description:
            continue

        records.append({
            "id":          rec_id,
            "title":       title,
            "description": description,
            "date":        date_str,
        })

    # Resumption token for next page
    token_el = root.find(".//oai:resumptionToken", _NS)
    token = None
    if token_el is not None and token_el.text and token_el.text.strip():
        token = token_el.text.strip()

    return records, token


def download(
    output_dir: Path,
    max_pages: int = 200,
    dry_run: bool = False,
) -> None:
    """
    Harvest archival finding aid metadata from Nationaal Archief via OAI-PMH.

    Saves records as batched JSONL files (nationaal_archief_batch_{N:06d}.jsonl).

    Args:
        max_pages: Maximum OAI-PMH pages to harvest (each page ~100 records).
        dry_run:   Log only, do not fetch.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[nationaal_archief] [dry-run] would harvest OAI-PMH from %s", _OAI_BASE)
        return

    page = 0
    token: str | None = None
    total_records = 0

    while page < max_pages:
        if token:
            url = _oai_url("ListRecords", {"resumptionToken": token})
        else:
            url = _oai_url("ListRecords", {
                "metadataPrefix": _META_PREFIX,
            })

        batch_file = output_dir / f"nationaal_archief_batch_{page:06d}.jsonl"
        if batch_file.exists():
            logger.info("[nationaal_archief] page %d already saved, advancing...", page)
            # Need to re-fetch to get next token
            try:
                xml_bytes = _fetch(url)
                _, token = _parse_records(xml_bytes)
            except Exception as exc:
                logger.warning("[nationaal_archief] Could not advance past page %d: %s", page, exc)
                break
            if not token:
                break
            page += 1
            continue

        logger.info("[nationaal_archief] Harvesting page %d...", page)
        try:
            xml_bytes = _fetch(url)
        except Exception as exc:
            logger.warning("[nationaal_archief] Fetch failed at page %d: %s", page, exc)
            break

        records, token = _parse_records(xml_bytes)
        if records:
            lines = [json.dumps(r, ensure_ascii=False) for r in records]
            batch_file.write_text("\n".join(lines), encoding="utf-8")
            total_records += len(records)
            logger.info("[nationaal_archief] page %d: %d records", page, len(records))
            _log(output_dir, url)

        if not token:
            logger.info("[nationaal_archief] No more pages (no resumption token)")
            break

        page += 1
        time.sleep(0.5)

    logger.info("[nationaal_archief] Done. %d records across %d pages.", total_records, page + 1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Harvest Nationaal Archief finding aids via OAI-PMH (CC0)"
    )
    parser.add_argument("--output",     required=True, type=Path)
    parser.add_argument("--max-pages",  type=int, default=200)
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    download(args.output, max_pages=args.max_pages, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
