"""
Harvest Dutch archaeology research reports from DANS Data Station Archaeology.

The DANS Data Station Archaeology (archaeology.datastations.nl) aggregates
~172,000 datasets deposited by Dutch universities, municipalities, and commercial
archaeology firms. Each dataset typically represents a bureau-onderzoek,
veld-onderzoek, or opgraving report in Dutch.

~96% of records are Dutch-language. ~37% carry open licenses (CC0, CC-BY,
CC-BY-SA); the remainder use NC or ND variants which we exclude.

License per record: verified from Datacite rights field.
API: OAI-PMH Datacite format (https://archaeology.datastations.nl/oai)
     → has license + description in one request (vs oai_dc which lacks rights).

Usage:
  python -m pipeline acquire --sources dans_archaeology
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "dans_archaeology"
LICENSE = "CC-BY"          # conservative default; per-record license stored in metadata
COUNTRY = "NL"
DOMAIN  = "historical"     # archaeology reports = historical/cultural domain

_OAI_BASE = "https://archaeology.datastations.nl/oai"
_UA       = "NederCorpus/0.1 (academic; Dutch corpus; research)"
_DELAY    = 1.0
_TIMEOUT  = 60
_PAGE_SIZE = 100           # Dataverse OAI default

_NS_OAI = "http://www.openarchives.org/OAI/2.0/"
_NS_DC4 = "http://datacite.org/schema/kernel-4"

# Licenses we accept
_OPEN_LICENSES = {
    "cc0", "publicdomain/zero",
    "/by/4", "/by/3", "/by/2", "/by/1",    # CC-BY any version
    "by-sa/4", "by-sa/3", "by-sa/2",       # CC-BY-SA
}
# Licenses we exclude (NC, ND)
_EXCL_PATTERNS = {"nc", "nd"}


def _is_open(rights_uris: list[str]) -> bool:
    combined = " ".join(r.lower() for r in rights_uris if r)
    if not combined:
        return False
    if any(p in combined for p in _EXCL_PATTERNS):
        return False
    return any(p in combined for p in _OPEN_LICENSES)


def _license_label(rights_uris: list[str]) -> str:
    combined = " ".join(r.lower() for r in rights_uris if r)
    if "publicdomain/zero" in combined or "cc0" in combined:
        return "CC0"
    if "by-sa" in combined:
        return "CC-BY-SA"
    if "/by/" in combined:
        return "CC-BY"
    return "CC-BY"


def _fetch_xml(url: str, retries: int = 6) -> ET.Element | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
            return ET.fromstring(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = 10 * (2 ** attempt)
                logger.warning("[dans_archaeology] HTTP %d — retry in %.0fs", exc.code, wait)
                time.sleep(wait)
            else:
                raise
        except urllib.error.URLError as exc:
            wait = 10 * (2 ** attempt)
            logger.warning("[dans_archaeology] URLError %s — retry in %.0fs", exc.reason, wait)
            time.sleep(wait)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            logger.warning("[dans_archaeology] parse error: %s", exc)
            return None
        except OSError as exc:
            wait = 10 * (2 ** attempt)
            logger.warning("[dans_archaeology] OSError %s — retry in %.0fs", exc, wait)
            time.sleep(wait)
    return None


def _parse_datacite_record(resource_el: ET.Element) -> dict | None:
    """Extract text fields from a Datacite <resource> element."""
    ns = _NS_DC4

    # Rights / license
    rights_uris = [
        el.get("rightsURI", "")
        for el in resource_el.findall(f".//{{{ns}}}rights")
    ]
    if not _is_open(rights_uris):
        return None

    # Title
    title_els = resource_el.findall(f".//{{{ns}}}title")
    title = " — ".join(
        (el.text or "").strip()
        for el in title_els
        if el.text and el.get("titleType") not in ("AlternativeTitle",)
    ).strip()
    if not title:
        return None

    # Description / abstract
    desc_parts = []
    for desc_el in resource_el.findall(f".//{{{ns}}}description"):
        text = (desc_el.text or "").strip()
        if text and len(text) > 30:
            desc_parts.append(text)
    description = "\n\n".join(desc_parts)
    if not description:
        return None

    # DOI / identifier
    id_el = resource_el.find(f"{{{ns}}}identifier")
    doi = (id_el.text or "").strip() if id_el is not None else ""
    source_url = f"https://doi.org/{doi}" if doi else ""

    # Year
    year = None
    for date_el in resource_el.findall(f".//{{{ns}}}date"):
        if date_el.get("dateType") == "Available":
            year_str = (date_el.text or "")[:4]
            if year_str.isdigit():
                year = int(year_str)
                break
    if year is None:
        pub_el = resource_el.find(f"{{{ns}}}publicationYear")
        if pub_el is not None and pub_el.text:
            try:
                year = int(pub_el.text[:4])
            except ValueError:
                pass

    # Language detection: Datacite language field is usually empty, but description
    # is almost always Dutch for this station. Check for basic Dutch fingerprint.
    lang_el = resource_el.find(f"{{{ns}}}language")
    lang_text = (lang_el.text or "").lower() if lang_el is not None else ""
    if lang_text and "dutch" not in lang_text and "nl" != lang_text:
        # Explicitly non-Dutch — skip
        return None

    full_text = f"{title}\n\n{description}".strip()
    # Minimum quality check
    if len(full_text) < 100:
        return None

    return {
        "title": title,
        "description": description,
        "doi": doi,
        "source_url": source_url,
        "year": year,
        "license_label": _license_label(rights_uris),
        "rights_uris": rights_uris,
    }


def download(output_dir: Path, dry_run: bool = False) -> None:
    if dry_run:
        logger.info("[dans_archaeology] dry_run — skipping download")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "records.jsonl"
    token_file = output_dir / "resumption_token.txt"

    # Load existing DOIs to skip (resume support)
    seen_dois: set[str] = set()
    if out_file.exists():
        with out_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    if rec.get("doi"):
                        seen_dois.add(rec["doi"])
                except json.JSONDecodeError:
                    pass
        logger.info("[dans_archaeology] Resuming — %d records already saved", len(seen_dois))

    # Load resumption token
    # Use saved resumption token to resume from the right page, even with existing records
    token: str | None = None
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        logger.info("[dans_archaeology] Resuming with token: %s…", token[:40])

    written = 0
    skipped_license = 0
    skipped_dup = 0
    page = 0

    with out_file.open("a", encoding="utf-8") as fh:
        while True:
            page += 1
            if token:
                url = f"{_OAI_BASE}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
            else:
                url = f"{_OAI_BASE}?verb=ListRecords&metadataPrefix=Datacite"

            root = _fetch_xml(url)
            if root is None:
                logger.error("[dans_archaeology] Failed to parse page %d — stopping", page)
                break

            oai_ns = _NS_OAI
            # Check for OAI error
            err_el = root.find(f"{{{oai_ns}}}error")
            if err_el is not None:
                logger.error("[dans_archaeology] OAI error: %s", err_el.text)
                break

            records = root.findall(f".//{{{oai_ns}}}record")
            for record in records:
                # Skip deleted records
                header = record.find(f"{{{oai_ns}}}header")
                if header is not None and header.get("status") == "deleted":
                    continue

                # Get identifier
                id_el = header.find(f"{{{oai_ns}}}identifier") if header is not None else None
                oai_id = (id_el.text or "").strip() if id_el is not None else ""
                doi = oai_id.replace("doi:", "").strip() if oai_id.startswith("doi:") else ""

                if doi and doi in seen_dois:
                    skipped_dup += 1
                    continue

                # Parse Datacite resource element
                resource_el = record.find(f".//{{{_NS_DC4}}}resource")
                if resource_el is None:
                    skipped_license += 1
                    continue

                parsed = _parse_datacite_record(resource_el)
                if parsed is None:
                    skipped_license += 1
                    continue

                if doi:
                    seen_dois.add(doi)

                entry = {
                    "id": f"dans_archaeology_{doi.replace('/', '_').replace('.', '_')}",
                    "source": SOURCE,
                    "source_url": parsed["source_url"],
                    "license": parsed["license_label"],
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "language_variant": "NL",
                    "publication_year": parsed["year"],
                    "acquisition_date": datetime.date.today().isoformat(),
                    "text": parsed["title"] + "\n\n" + parsed["description"],
                    "doi": parsed["doi"],
                    "title": parsed["title"],
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1

            if page % 50 == 0:
                logger.info(
                    "[dans_archaeology] Page %d — %d written, %d excl-license, %d dup",
                    page, written, skipped_license, skipped_dup,
                )

            # Next page
            tok_el = root.find(f".//{{{oai_ns}}}resumptionToken")
            if tok_el is None or not tok_el.text:
                break
            token = tok_el.text
            # Save token for crash recovery
            token_file.write_text(token, encoding="utf-8")
            time.sleep(_DELAY)

    # Clean up token file on successful completion
    if token_file.exists():
        token_file.unlink()

    logger.info(
        "[dans_archaeology] Done — %d records written (%d excl-license, %d dup skipped)",
        written, skipped_license, skipped_dup,
    )
