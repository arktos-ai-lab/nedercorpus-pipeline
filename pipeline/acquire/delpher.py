"""
Download historical Dutch newspaper articles from Delpher (KB National Library).

Delpher is the digitised-text platform of the Koninklijke Bibliotheek (KB).
The Delpher Open Krantenarchief covers Dutch newspapers from the 17th to 20th
centuries and is freely available for research reuse.

Two-phase acquisition:
  Phase 1: OAI-PMH enumeration (services.kb.nl/mdo/oai, set=DDD)
           → article identifiers + metadata per year range
  Phase 2: ALTO OCR text via resolver.kb.nl per page
           (the article-level role=ocr endpoint was retired; use page-level ALTO)

ALTO XML: resolver.kb.nl/resolve?urn={urn}:p001:alto
  Returns ALTO XML with <String CONTENT="word"/> elements per line of OCR.

License: CC0 / Public Domain
  The KB explicitly places the Delpher Open Krantenarchief under CC0.
  Source: https://www.kb.nl/en/research-find/datasets/delpher-newspapers
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

SOURCE  = "delpher"
LICENSE = "CC0"
COUNTRY = "NL"
DOMAIN  = "news"

_OAI_ENDPOINT = "https://services.kb.nl/mdo/oai"
_RESOLVER     = "https://resolver.kb.nl/resolve"
_USER_AGENT   = "NederCorpus/0.1 (academic research; KB CC0 open data)"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 3.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.0fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Phase 1: OAI-PMH enumeration
# ---------------------------------------------------------------------------

def _oai_url(resumption_token: str | None) -> str:
    # NOTE: OAI-PMH from/until filters on metadata modification date, NOT publication year.
    # The DDD newspaper records were digitised recently; filtering on 1800-1879 yields 0.
    # Publication-year filtering is done at parse time via the URN/article metadata.
    if resumption_token:
        params = {"verb": "ListRecords", "resumptionToken": resumption_token}
    else:
        params = {"verb": "ListRecords", "metadataPrefix": "didl", "set": "DDDarchief"}
    return f"{_OAI_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _parse_oai_response(xml_bytes: bytes) -> tuple[list[str], str | None]:
    """
    Parse an OAI-PMH ListRecords response.

    Returns (list_of_article_urns, resumption_token_or_None).
    Note: DDD records appear as 'deleted' in OAI-PMH metadata but the ALTO
    resolver still serves content — we process all records regardless of status.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("[delpher] OAI XML parse error: %s", exc)
        return [], None

    # OAI-PMH namespace
    oai_ns = "http://www.openarchives.org/OAI/2.0/"

    urns: list[str] = []
    for record_el in root.iter(f"{{{oai_ns}}}record"):
        header = record_el.find(f"{{{oai_ns}}}header")
        # Include all records — deleted status is a metadata flag, content is still accessible

        # Extract identifier from header
        ident_el = header.find(f"{{{oai_ns}}}identifier") if header is not None else None
        if ident_el is None or not ident_el.text:
            continue

        raw = ident_el.text.strip()
        # Identifier format: "DDD:ddd:010381561:mpeg21:a0001"
        # or just "ddd:010381561:mpeg21:a0001" — normalise to the ddd:... part
        if raw.upper().startswith("DDD:"):
            raw = raw[4:]
        # We want the page URN: ddd:{id}:mpeg21:p001
        # Drop the article suffix (:aXXXX) and add :p001
        if ":mpeg21:" in raw:
            base = raw.split(":mpeg21:")[0] + ":mpeg21"
            urns.append(base)
        elif raw.startswith("ddd:"):
            urns.append(raw)

    # Resumption token
    token_el = root.find(f".//{{{oai_ns}}}resumptionToken")
    token = token_el.text.strip() if token_el is not None and token_el.text else None

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique = [u for u in urns if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]
    return unique, token


# ---------------------------------------------------------------------------
# Phase 2: ALTO text extraction
# ---------------------------------------------------------------------------

def _fetch_alto_text(urn: str) -> str | None:
    """
    Fetch page-1 OCR text for a DDD article via ALTO XML.

    URN pattern: ddd:{id}:mpeg21  →  resolver?urn=ddd:{id}:mpeg21:p001:alto
    Extracts all <String CONTENT="..."/> values in document order.
    """
    alto_urn = f"{urn}:p001:alto"
    url = f"{_RESOLVER}?urn={urllib.parse.quote(alto_urn)}"
    try:
        raw = _fetch(url, timeout=30)
    except Exception as exc:
        logger.debug("[delpher] ALTO fetch failed for %s: %s", urn, exc)
        return None

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None

    words: list[str] = []
    for el in root.iter():
        if el.tag.endswith("}String") or el.tag == "String":
            content = el.get("CONTENT", "").strip()
            if content:
                words.append(content)

    text = " ".join(words).strip()
    return text if len(text) >= 50 else None


# ---------------------------------------------------------------------------
# Main download
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    max_articles: int = 50_000,
    dry_run: bool = False,
) -> None:
    """
    Download Dutch newspaper articles from the Delpher Open Krantenarchief.

    Phase 1: OAI-PMH enumeration (set=DDD) — all DDD records without date filter.
    Phase 2: ALTO OCR text via resolver.kb.nl/resolve?urn=...&p001:alto.

    Articles saved as {safe_urn}.txt with a .url sidecar.
    Covers CC0 Dutch newspapers (17th–20th century).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[delpher] [dry-run] would enumerate DDD via OAI-PMH")
        return

    total_fetched = 0
    resumption_token: str | None = None
    page = 0

    logger.info("[delpher] Enumerating DDD via OAI-PMH (max %d)", max_articles)

    while total_fetched < max_articles:
        url = _oai_url(resumption_token)
        logger.info("[delpher] OAI page %d — %s", page, url[:100])

        try:
            xml_bytes = _fetch(url, timeout=60)
        except Exception as exc:
            logger.warning("[delpher] OAI fetch failed: %s", exc)
            break

        urns, resumption_token = _parse_oai_response(xml_bytes)
        if not urns and not resumption_token:
            logger.info("[delpher] No more OAI records.")
            break

        logger.info("[delpher] OAI page %d: %d URNs", page, len(urns))

        for urn in urns:
            if total_fetched >= max_articles:
                break

            safe_name = urn.replace(":", "_").replace("/", "_")
            dest = output_dir / f"{safe_name}.txt"
            if dest.exists():
                total_fetched += 1
                continue

            text = _fetch_alto_text(urn)
            time.sleep(0.3)

            if not text:
                continue

            dest.write_text(text, encoding="utf-8")
            resolver_url = f"{_RESOLVER}?urn={urllib.parse.quote(urn)}"
            dest.with_name(dest.name + ".url").write_text(resolver_url, encoding="utf-8")
            _log(output_dir, resolver_url)
            total_fetched += 1

            if total_fetched % 500 == 0:
                logger.info("[delpher] %d articles downloaded", total_fetched)

        if not resumption_token:
            break
        page += 1
        time.sleep(2.0)  # respect KB rate limits

    logger.info("[delpher] Done. %d articles downloaded.", total_fetched)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Delpher historical Dutch newspaper articles (CC0)"
    )
    parser.add_argument("--output",       required=True, type=Path)
    parser.add_argument("--max-articles", type=int, default=50_000)
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()
    download(
        args.output,
        max_articles=args.max_articles,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
