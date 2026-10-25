"""
Download Staten-Generaal Digitaal (Dutch parliamentary proceedings 1814–1995).

The Koninklijke Bibliotheek (KB) digitized the complete official records of
the Dutch States-General (Eerste Kamer + Tweede Kamer + Verenigde Vergadering)
from 1814 to 1995 under CC0 1.0 Universal.

180+ years of Dutch parliamentary language — unique historical register.

WORKING endpoint (confirmed 2026-03-24):
  OAI-PMH: https://services.kb.nl/mdo/oai
  Set:     sgd:document
  Prefix:  dcx (richer metadata) or oai_dc

  The jsru.kb.nl SRU endpoint is DEAD ("Collection SGD does not exist").
  repository.overheid.nl/sru also does not have this collection.
  The OAI-PMH endpoint is the only working bulk access route.

Two-phase acquisition (same pattern as delpher.py):
  Phase 1: OAI-PMH ListRecords (set=sgd:document) → identifiers + metadata
  Phase 2: resolver.kb.nl/resolve?urn={urn}&role=xml → full text XML

License: CC0 1.0 Universal
  https://www.opencultuurdata.nl/wiki/koninklijke-bibliotheek-staten-generaal-digitaal/

Size: ~454,000 parliamentary documents (Tweede Kamer + Eerste Kamer + joint sessions).

Usage:
  python -m pipeline acquire --sources staten_generaal
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "staten_generaal"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "parliamentary"

_OAI_BASE   = "https://services.kb.nl/mdo/oai"
_OAI_SET    = "sgd:document"
_OAI_PREFIX = "dcx"            # KB Dublin Core eXtended; richer than oai_dc
_RESOLVER   = "https://resolver.kb.nl/resolve"
_USER_AGENT = "NederCorpus/0.1 (academic research; CC0 Staten-Generaal Digitaal)"
_MAX_RETRIES = 3

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_YEAR_RE = re.compile(r"(\d{4})")


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60) -> bytes:
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = 5.0 * (attempt + 1)
            logger.warning("[staten_generaal] Retry %d: %s (wait %.0fs)", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _oai_url(resumption_token: str | None) -> str:
    if resumption_token:
        params = {"verb": "ListRecords", "resumptionToken": resumption_token}
    else:
        params = {
            "verb":           "ListRecords",
            "set":            _OAI_SET,
            "metadataPrefix": _OAI_PREFIX,
        }
    return _OAI_BASE + "?" + urllib.parse.urlencode(params)


def _extract_oai_records(xml_bytes: bytes) -> tuple[list[dict], str | None]:
    """
    Parse one OAI-PMH ListRecords page.
    Returns (records, next_resumption_token).
    Each record: {identifier, title, date, urn}.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.error("[staten_generaal] XML parse error: %s", exc)
        return [], None

    # Check for OAI error
    err = root.find(".//{http://www.openarchives.org/OAI/2.0/}error")
    if err is not None:
        logger.error("[staten_generaal] OAI error %s: %s", err.get("code"), err.text)
        return [], None

    records = []
    for rec in root.findall(".//{http://www.openarchives.org/OAI/2.0/}record"):
        # Skip deleted
        header = rec.find("{http://www.openarchives.org/OAI/2.0/}header")
        if header is not None and header.get("status") == "deleted":
            continue

        id_el = rec.find(".//{http://www.openarchives.org/OAI/2.0/}identifier")
        identifier = id_el.text.strip() if id_el is not None and id_el.text else ""

        # Try dcx:title, then dc:title
        title = ""
        for ns in (
            "{http://krait.kb.nl/coop/tel/handbook/telterms.html}",
            "{http://purl.org/dc/elements/1.1/}",
        ):
            ttl_el = rec.find(f".//{ns}title")
            if ttl_el is not None and ttl_el.text:
                title = ttl_el.text.strip()
                break

        date = ""
        for ns in (
            "{http://krait.kb.nl/coop/tel/handbook/telterms.html}",
            "{http://purl.org/dc/elements/1.1/}",
            "{http://purl.org/dc/terms/}",
        ):
            dt_el = rec.find(f".//{ns}date")
            if dt_el is not None and dt_el.text:
                date = dt_el.text.strip()
                break

        if identifier:
            records.append({
                "identifier": identifier,
                "title":      title,
                "date":       date,
            })

    # Resumption token
    token_el = root.find(".//{http://www.openarchives.org/OAI/2.0/}resumptionToken")
    token = None
    if token_el is not None and token_el.text and token_el.text.strip():
        token = token_el.text.strip()

    return records, token


def _fetch_full_text(urn: str) -> str:
    """
    Fetch full text of an SGD document via the KB resolver.
    URN format: sgd:mpeg21:{id}  or  sgd:{id}
    Resolver URL: resolver.kb.nl/resolve?urn={urn}&role=xml
    Returns extracted plain text, or "" on failure.
    """
    url = f"{_RESOLVER}?urn={urllib.parse.quote(urn)}&role=xml"
    try:
        data = _fetch(url, timeout=30)
    except Exception as exc:
        logger.debug("[staten_generaal] Resolver failed for %s: %s", urn, exc)
        return ""

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ""

    # SGD XML: text is in <p>, <al>, or plain text nodes under the root
    parts: list[str] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag in ("p", "al", "tekst", "alinea") and el.text and el.text.strip():
            parts.append(el.text.strip())
        elif el.text and el.text.strip() and not parts:
            # Catch plain text documents
            parts.append(el.text.strip())

    if not parts:
        # Last resort: all text nodes joined
        text = " ".join(
            t.strip() for t in root.itertext()
            if t and t.strip()
        )
        return re.sub(r"\s+", " ", text).strip()

    return "\n".join(parts)


def download(
    output_dir: Path,
    max_records: int = 500_000,
    fetch_text: bool = True,
    dry_run: bool = False,
) -> None:
    """
    Harvest Staten-Generaal Digitaal via KB OAI-PMH (set=sgd:document).

    Phase 1: OAI-PMH ListRecords → identifiers + metadata
    Phase 2: resolver.kb.nl/resolve?urn={urn}&role=xml → full text

    Saves records to sgd_records.jsonl.
    Resumption token saved to sgd_resumption_token.txt.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[staten_generaal] [dry-run] would harvest OAI-PMH set=sgd:document")
        return

    out_file    = output_dir / "sgd_records.jsonl"
    token_file  = output_dir / "sgd_resumption_token.txt"

    # Load seen identifiers
    seen: set[str] = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                seen.add(json.loads(line).get("identifier", ""))
            except json.JSONDecodeError:
                pass
    logger.info("[staten_generaal] %d records already collected", len(seen))

    # Load resumption token
    token: str | None = None
    if token_file.exists():
        saved = token_file.read_text(encoding="utf-8").strip()
        token = saved if saved else None

    total_written = 0

    while total_written < max_records:
        url = _oai_url(token)
        logger.info("[staten_generaal] OAI-PMH request (token=%s)", token[:20] if token else "None")

        try:
            data = _fetch(url, timeout=90)
        except Exception as exc:
            logger.error("[staten_generaal] OAI-PMH fetch failed: %s", exc)
            break

        page_records, token = _extract_oai_records(data)

        if not page_records and token is None:
            logger.info("[staten_generaal] Harvest complete")
            break

        logger.info("[staten_generaal] Page: %d records", len(page_records))

        for meta in page_records:
            ident = meta["identifier"]
            if ident in seen:
                continue

            # Extract URN from identifier for resolver
            # KB OAI identifiers: "oai:kb.nl:{collection}:{id}" or similar
            urn = ident.replace("oai:kb.nl:", "").replace("oai:", "")

            text = ""
            if fetch_text and urn:
                text = _fetch_full_text(urn)
                time.sleep(0.05)  # polite delay per document

            year = None
            m = _YEAR_RE.search(meta.get("date", ""))
            if m:
                year = int(m.group(1))

            rec = {
                "identifier": ident,
                "title":      meta.get("title", ""),
                "date":       meta.get("date", ""),
                "year":       year,
                "text":       text or meta.get("title", ""),
            }
            seen.add(ident)
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _log(output_dir, f"{_RESOLVER}?urn={urllib.parse.quote(urn)}&role=xml")
            total_written += 1

        if token:
            token_file.write_text(token, encoding="utf-8")
        else:
            token_file.write_text("", encoding="utf-8")
            logger.info("[staten_generaal] OAI-PMH enumeration complete")
            break

        if total_written % 5000 == 0:
            logger.info("[staten_generaal] %d records written so far", total_written)

        time.sleep(1.0)  # polite delay between pages

    logger.info("[staten_generaal] Done. %d records written.", total_written)
