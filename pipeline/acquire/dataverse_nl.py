"""
Harvest Dutch research datasets from DataverseNL.

DataverseNL (dataverse.nl) is a shared Dataverse repository for Dutch
universities and research institutes that do not run their own instance.
~11,000 datasets covering social sciences, natural sciences, and humanities.

Open-license subset (CC0/CC-BY/CC-BY-SA) harvested; NC/ND excluded.

API: OAI-PMH Datacite format (https://dataverse.nl/oai)

Usage:
  python -m pipeline acquire --sources dataverse_nl
"""
from __future__ import annotations

import datetime
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "dataverse_nl"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "academic"

_OAI_BASE  = "https://dataverse.nl/oai"
_UA        = "NederCorpus/0.1 (academic; Dutch corpus; research)"
_DELAY     = 1.0
_TIMEOUT   = 60

_NS_OAI = "http://www.openarchives.org/OAI/2.0/"
_NS_DC4 = "http://datacite.org/schema/kernel-4"

_OPEN_LICENSES = {
    "cc0", "publicdomain/zero",
    "/by/4", "/by/3", "/by/2", "/by/1",
    "by-sa/4", "by-sa/3", "by-sa/2",
}
_EXCL_PATTERNS = {"nc", "nd"}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


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
    return "CC-BY"


def _fetch_xml(url: str, retries: int = 6) -> ET.Element | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
                raw = resp.read()
            return ET.fromstring(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = 10 * (2 ** attempt)
                logger.warning("[dataverse_nl] HTTP %d — retry in %.0fs", exc.code, wait)
                time.sleep(wait)
            else:
                raise
        except urllib.error.URLError as exc:
            wait = 10 * (2 ** attempt)
            logger.warning("[dataverse_nl] URLError %s — retry in %.0fs", exc.reason, wait)
            time.sleep(wait)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            logger.warning("[dataverse_nl] parse error: %s", exc)
            return None
        except OSError as exc:
            wait = 10 * (2 ** attempt)
            logger.warning("[dataverse_nl] OSError %s — retry in %.0fs", exc, wait)
            time.sleep(wait)
    return None


def _parse_record(resource_el: ET.Element) -> dict | None:
    ns = _NS_DC4
    rights_uris = [
        el.get("rightsURI", "")
        for el in resource_el.findall(f".//{{{ns}}}rights")
    ]
    if not _is_open(rights_uris):
        return None

    title_els = resource_el.findall(f".//{{{ns}}}title")
    title = " — ".join(
        (el.text or "").strip()
        for el in title_els
        if el.text and el.get("titleType") not in ("AlternativeTitle",)
    ).strip()
    if not title:
        return None

    desc_parts = []
    for desc_el in resource_el.findall(f".//{{{ns}}}description"):
        text = (desc_el.text or "").strip()
        if text and len(text) > 30:
            desc_parts.append(text)
    description = "\n\n".join(desc_parts)
    if not description:
        return None

    id_el = resource_el.find(f"{{{ns}}}identifier")
    doi = (id_el.text or "").strip() if id_el is not None else ""
    source_url = f"https://doi.org/{doi}" if doi else ""

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

    lang_el = resource_el.find(f"{{{ns}}}language")
    lang_text = (lang_el.text or "").lower() if lang_el is not None else ""
    if lang_text and "dutch" not in lang_text and "nl" != lang_text and lang_text not in ("nld", "dut"):
        return None

    full_text = f"{title}\n\n{description}".strip()
    if len(full_text) < 100:
        return None

    return {
        "title": title,
        "description": description,
        "doi": doi,
        "source_url": source_url,
        "year": year,
        "license_label": _license_label(rights_uris),
    }


def download(output_dir: Path, dry_run: bool = False) -> None:
    if dry_run:
        logger.info("[dataverse_nl] dry_run — skipping download")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "records.jsonl"

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
        logger.info("[dataverse_nl] Resuming — %d records already saved", len(seen_dois))

    token_file = output_dir / "resumption_token.txt"
    token: str | None = None
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        logger.info("[dataverse_nl] Resuming with token: %s…", token[:40])

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
                logger.error("[dataverse_nl] Failed to parse page %d — stopping", page)
                break

            err_el = root.find(f"{{{_NS_OAI}}}error")
            if err_el is not None:
                logger.error("[dataverse_nl] OAI error: %s", err_el.text)
                break

            for record in root.findall(f".//{{{_NS_OAI}}}record"):
                header = record.find(f"{{{_NS_OAI}}}header")
                if header is not None and header.get("status") == "deleted":
                    continue

                id_el = header.find(f"{{{_NS_OAI}}}identifier") if header is not None else None
                oai_id = (id_el.text or "").strip() if id_el is not None else ""
                doi = oai_id.replace("doi:", "").strip() if oai_id.startswith("doi:") else ""

                if doi and doi in seen_dois:
                    skipped_dup += 1
                    continue

                resource_el = record.find(f".//{{{_NS_DC4}}}resource")
                if resource_el is None:
                    skipped_license += 1
                    continue

                parsed = _parse_record(resource_el)
                if parsed is None:
                    skipped_license += 1
                    continue

                if doi:
                    seen_dois.add(doi)

                entry = {
                    "id": f"dataverse_nl_{doi.replace('/', '_').replace('.', '_')}",
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

            tok_el = root.find(f".//{{{_NS_OAI}}}resumptionToken")
            if tok_el is None or not tok_el.text:
                break
            token = tok_el.text
            token_file.write_text(token, encoding="utf-8")
            time.sleep(_DELAY)

    if token_file.exists():
        token_file.unlink()

    logger.info(
        "[dataverse_nl] Done — %d records written (%d excl-license, %d dup skipped)",
        written, skipped_license, skipped_dup,
    )
