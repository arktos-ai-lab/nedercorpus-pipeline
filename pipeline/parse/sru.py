"""
Parse SRU/CQL XML responses produced by the acquisition stage.

Handles three source families that all use the same SRU wrapper but
have different inner record schemas:

  rechtspraak  — ECLI court decisions (Atom feed + ECLI XML)
  wetten       — Dutch legislation in BWB/OVERHEIDsPublicaties format
  parliamentary — Kamerstukken / Handelingen / Staatsblad

In all cases we do best-effort text extraction: walk every text node
inside <srw:recordData>, discard boilerplate tags, collapse whitespace.
Metadata is extracted from well-known fields where present.
"""
from __future__ import annotations

import datetime
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

# SRU namespaces
NS_SRW = "http://www.loc.gov/zing/srw/"
NS_ATOM = "http://www.w3.org/2005/Atom"

# Tags whose text content we skip entirely (boilerplate / binary / metadata noise)
_SKIP_TAGS = {
    "identifier", "link", "category", "updated", "published",
    "rights", "generator", "icon", "logo", "base",
}


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _iter_text(element: ET.Element, skip_tags: set[str] | None = None) -> Iterator[str]:
    """Yield all non-empty text nodes from an element tree, skipping skip_tags."""
    skip = skip_tags or _SKIP_TAGS
    tag_local = element.tag.split("}")[-1].lower() if "}" in element.tag else element.tag.lower()
    if tag_local in skip:
        return
    if element.text and element.text.strip():
        yield element.text.strip()
    for child in element:
        yield from _iter_text(child, skip)
    if element.tail and element.tail.strip():
        yield element.tail.strip()


def _extract_text(element: ET.Element) -> str:
    parts = list(_iter_text(element))
    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_text(element: ET.Element, *xpaths: str) -> str | None:
    for xpath in xpaths:
        el = element.find(xpath)
        if el is not None and el.text:
            return el.text.strip()
    return None


def _parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    m = re.search(r"\b(\d{4})\b", date_str)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# SRU record extractor
# ---------------------------------------------------------------------------

def _records_from_sru_xml(xml_bytes: bytes) -> Iterator[ET.Element]:
    """Yield each <recordData> element from an SRU SearchRetrieve response.

    Handles both SRU 1.1 (http://www.loc.gov/zing/srw/) and SRU 2.0
    (http://docs.oasis-open.org/ns/search-ws/sruResponse) by matching on
    local tag names only, ignoring namespace prefixes.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("XML parse error: %s", exc)
        return
    for record in root.iter():
        if record.tag.split("}")[-1] == "record":
            for child in record:
                if child.tag.split("}")[-1] == "recordData":
                    yield child


# ---------------------------------------------------------------------------
# Rechtspraak (ECLI court decisions — individual XML files)
# ---------------------------------------------------------------------------

# Namespaces used in ECLI decision XML files
_RS_NS  = "http://www.rechtspraak.nl/schema/rechtspraak-1.0"
_DC_NS  = "http://purl.org/dc/elements/1.1/"
_DCT_NS = "http://purl.org/dc/terms/"

# Tags that carry full decision text in ECLI XML
_ECLI_TEXT_TAGS = {"inhoud", "uitspraak", "conclusie", "para", "parablock",
                   "section", "title", "nr", "emphasis", "bridgehead"}
# Tags to skip in ECLI XML (noise / metadata blocks)
_ECLI_SKIP_TAGS = {"meta", "identifier", "link", "category", "rights",
                   "zaaknummer", "hasVersion", "replaces", "isReplacedBy"}


def _iter_ecli_text(element: ET.Element) -> Iterator[str]:
    """Walk an ECLI XML element tree, yielding text from content-bearing nodes."""
    local = element.tag.split("}")[-1].lower() if "}" in element.tag else element.tag.lower()
    if local in _ECLI_SKIP_TAGS:
        return
    if element.text and element.text.strip():
        yield element.text.strip()
    for child in element:
        yield from _iter_ecli_text(child)
    if element.tail and element.tail.strip():
        yield element.tail.strip()


def _extract_ecli_text(root: ET.Element) -> str:
    """Extract full decision text from an ECLI XML root element."""
    # Prefer <rs:inhoud> / <inhoud> sections when present
    inhoud_parts: list[str] = []
    for el in root.iter():
        local = el.tag.split("}")[-1].lower() if "}" in el.tag else el.tag.lower()
        if local == "inhoud":
            parts = list(_iter_ecli_text(el))
            if parts:
                inhoud_parts.append("\n".join(parts))

    if inhoud_parts:
        text = "\n\n".join(inhoud_parts)
    else:
        # Fallback: walk the full document
        text = "\n".join(_iter_ecli_text(root))

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _meta_from_ecli_xml(root: ET.Element) -> dict:
    """Extract Dublin Core metadata from an ECLI XML root."""
    meta: dict = {}
    for ns in (_DC_NS, _DCT_NS):
        for field_name in ("identifier", "date", "publisher", "subject", "title",
                           "zaaknummer", "coverage"):
            if field_name in meta:
                continue
            val = _find_text(root, f".//{{{ns}}}{field_name}")
            if val:
                meta[field_name] = val
    # Also check the Rechtspraak-specific namespace
    for field_name in ("zaaknummer", "inhoudsindicatie"):
        if field_name not in meta:
            val = _find_text(root, f".//{{{_RS_NS}}}{field_name}")
            if val:
                meta[field_name] = val
    return meta


def parse_ecli_xml_file(xml_file: Path) -> Document | None:
    """
    Parse a single ECLI decision XML file downloaded by the acquirer.

    The file is expected to be the full-text XML returned by:
      data.rechtspraak.nl/uitspraken/content?id=ECLI:NL:...
    """
    try:
        xml_bytes = xml_file.read_bytes()
        root = ET.fromstring(xml_bytes)
    except (OSError, ET.ParseError) as exc:
        logger.warning("Cannot parse %s: %s", xml_file.name, exc)
        return None

    text = _extract_ecli_text(root)
    if len(text) < 50:
        return None

    meta_dict = _meta_from_ecli_xml(root)
    ecli = meta_dict.get("identifier") or xml_file.stem.replace("_", ":", 4)

    doc_id = make_id("rechtspraak", ecli)
    meta = DocumentMeta(
        id=doc_id,
        source="rechtspraak",
        source_url=f"https://uitspraken.rechtspraak.nl/#!/details?id={ecli}",
        license="PD",
        publication_year=_parse_year(meta_dict.get("date")),
        country="NL",
        domain="legal",
        language_variant="NL",
        acquisition_date=datetime.date.today().isoformat(),
    )
    return Document(text=text, meta=meta)


def parse_rechtspraak_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Rechtspraak data from raw_dir.

    Prefers individual ECLI XML files in raw_dir/decisions/ (Phase 2 output
    of the revised acquirer).  Falls back to extracting metadata summaries
    from the Atom feed pages if no decisions/ directory exists yet.
    """
    decisions_dir = raw_dir / "decisions"

    if decisions_dir.exists():
        xml_files = sorted(decisions_dir.glob("ECLI_*.xml"))
        logger.info("[rechtspraak] Parsing %d individual decision XMLs", len(xml_files))
        for xml_file in xml_files:
            doc = parse_ecli_xml_file(xml_file)
            if doc:
                yield doc
        return

    # --- fallback: Atom feed pages contain brief summaries only ---
    logger.warning(
        "[rechtspraak] No decisions/ directory found in %s. "
        "Run the acquirer with full two-phase download to get decision text. "
        "Falling back to feed-page metadata summaries.", raw_dir
    )
    acq_date = datetime.date.today().isoformat()
    for xml_file in sorted(raw_dir.glob("feed_page_*.xml")):
        logger.info("Parsing feed page (summary only): %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
            root = ET.fromstring(xml_bytes)
        except (OSError, ET.ParseError) as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        ns = f"{{{NS_ATOM}}}"
        for entry in root.iter(f"{ns}entry"):
            # Extract the brief summary text from the feed entry
            summary_el = entry.find(f"{ns}summary")
            title_el   = entry.find(f"{ns}title")
            id_el      = entry.find(f"{ns}id")
            updated_el = entry.find(f"{ns}updated")

            ecli = id_el.text.strip() if id_el is not None and id_el.text else ""
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            summary = summary_el.text.strip() if summary_el is not None and summary_el.text else ""

            text = "\n".join(t for t in [title, summary] if t)
            if not text or len(text) < 10:
                continue

            doc_id = make_id("rechtspraak", ecli or xml_file.stem)
            meta = DocumentMeta(
                id=doc_id,
                source="rechtspraak",
                source_url=f"https://uitspraken.rechtspraak.nl/#!/details?id={ecli}",
                license="PD",
                publication_year=_parse_year(
                    updated_el.text if updated_el is not None else None
                ),
                country="NL",
                domain="legal",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Raad van State / CRvB / CBB (Dutch administrative courts)
# ---------------------------------------------------------------------------

def parse_raad_van_state_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch administrative court decisions downloaded by raad_van_state.py.

    Reads individual ECLI XML files from raw_dir/decisions/ — the same format
    as rechtspraak.nl decisions. Sets source="raad_van_state" (not "rechtspraak").
    """
    decisions_dir = raw_dir / "decisions"
    if not decisions_dir.exists():
        logger.warning("[raad_van_state] No decisions/ directory in %s", raw_dir)
        return

    xml_files = sorted(decisions_dir.glob("ECLI_*.xml"))
    logger.info("[raad_van_state] Parsing %d ECLI XMLs", len(xml_files))
    for xml_file in xml_files:
        doc = parse_ecli_xml_file(xml_file)
        if doc is None:
            continue
        # Override source (parse_ecli_xml_file sets "rechtspraak")
        ecli = xml_file.stem.replace("_", ":", 4)
        meta = DocumentMeta(
            id=make_id("raad_van_state", ecli),
            source="raad_van_state",
            source_url=doc.meta.source_url,
            license="PD",
            publication_year=doc.meta.publication_year,
            country="NL",
            domain="legal",
            language_variant="NL",
            acquisition_date=doc.meta.acquisition_date,
        )
        yield Document(text=doc.text, meta=meta)


# ---------------------------------------------------------------------------
# Wetten (Dutch legislation)
# ---------------------------------------------------------------------------

def _parse_wetten_record(data: ET.Element) -> tuple[str, dict]:
    meta: dict = {}

    title = _find_text(data,
        ".//{http://purl.org/dc/elements/1.1/}title",
        ".//{http://purl.org/dc/terms/}title",
    )
    meta["title"] = title

    date = _find_text(data,
        ".//{http://purl.org/dc/elements/1.1/}date",
        ".//{http://purl.org/dc/terms/}date",
        ".//{http://purl.org/dc/terms/}modified",
    )
    meta["date"] = date

    identifier = _find_text(data,
        ".//{http://purl.org/dc/elements/1.1/}identifier",
        ".//{http://purl.org/dc/terms/}identifier",
    )
    meta["identifier"] = identifier

    text = _extract_text(data)
    return text, meta


def parse_wetten_files(raw_dir: Path) -> Iterator[Document]:
    acq_date = datetime.date.today().isoformat()
    for xml_file in sorted(raw_dir.glob("wetten_batch_*.xml")):
        logger.info("Parsing %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
            text, record_meta = _parse_wetten_record(data)
            if not text or len(text) < 50:
                continue

            raw_id = record_meta.get("identifier") or f"{xml_file.stem}_{i}"
            doc_id = make_id("wetten", raw_id)

            meta = DocumentMeta(
                id=doc_id,
                source="wetten",
                source_url=record_meta.get("identifier") or "https://wetten.nl",
                license="PD",
                publication_year=_parse_year(record_meta.get("date")),
                country="NL",
                domain="legal",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Parliamentary (kamerstukken / handelingen)
# ---------------------------------------------------------------------------

_PARLIAMENTARY_DOMAIN_MAP = {
    "kamerstuk":  "parliamentary",
    "handelingen": "parliamentary",
    "kamervragen": "parliamentary",
    "staatsblad": "legal",
}


def _parse_parliamentary_record(data: ET.Element, area: str) -> tuple[str, dict]:
    meta: dict = {}
    title = _find_text(data,
        ".//{http://purl.org/dc/elements/1.1/}title",
        ".//{http://purl.org/dc/terms/}title",
    )
    meta["title"] = title
    date = _find_text(data,
        ".//{http://purl.org/dc/elements/1.1/}date",
        ".//{http://purl.org/dc/terms/}date",
    )
    meta["date"] = date
    identifier = _find_text(data,
        ".//{http://purl.org/dc/elements/1.1/}identifier",
        ".//{http://purl.org/dc/terms/}identifier",
    )
    meta["identifier"] = identifier
    text = _extract_text(data)
    return text, meta


def parse_parliamentary_files(raw_dir: Path) -> Iterator[Document]:
    acq_date = datetime.date.today().isoformat()
    # raw_dir contains subdirs per area: kamerstuk/, handelingen/, etc.
    for area_dir in sorted(raw_dir.iterdir()):
        if not area_dir.is_dir():
            continue
        area = area_dir.name
        domain = _PARLIAMENTARY_DOMAIN_MAP.get(area, "parliamentary")

        for xml_file in sorted(area_dir.glob("batch_*.xml")):
            logger.info("Parsing %s/%s", area, xml_file.name)
            try:
                xml_bytes = xml_file.read_bytes()
            except OSError as exc:
                logger.warning("Cannot read %s: %s", xml_file, exc)
                continue

            for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
                text, record_meta = _parse_parliamentary_record(data, area)
                if not text or len(text) < 50:
                    continue

                raw_id = record_meta.get("identifier") or f"{area}_{xml_file.stem}_{i}"
                doc_id = make_id("parliamentary", raw_id)

                meta = DocumentMeta(
                    id=doc_id,
                    source=f"parliamentary:{area}",
                    source_url=record_meta.get("identifier") or "https://officielebekendmakingen.nl",
                    license="PD",
                    publication_year=_parse_year(record_meta.get("date")),
                    country="NL",
                    domain=domain,
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Staatscourant (Dutch government gazette)
# ---------------------------------------------------------------------------

def parse_staatscourant_files(raw_dir: Path) -> Iterator[Document]:
    """Parse Staatscourant SRU batch XML files downloaded by staatscourant.py."""
    acq_date = datetime.date.today().isoformat()

    for xml_file in sorted(raw_dir.glob("stcrt_batch_*.xml")):
        logger.info("Parsing %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
            title = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}title",
                ".//{http://purl.org/dc/terms/}title",
            )
            date = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}date",
                ".//{http://purl.org/dc/terms/}date",
                ".//{http://purl.org/dc/terms/}modified",
            )
            identifier = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}identifier",
                ".//{http://purl.org/dc/terms/}identifier",
            )

            text = _extract_text(data)
            if not text or len(text) < 50:
                continue

            raw_id = identifier or f"{xml_file.stem}_{i}"
            doc_id = make_id("staatscourant", raw_id)

            meta = DocumentMeta(
                id=doc_id,
                source="staatscourant",
                source_url=identifier or "https://www.officielebekendmakingen.nl",
                license="PD",
                publication_year=_parse_year(date),
                country="NL",
                domain="government",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Lokale Regelgeving (CVDR — Centrale Voorziening Decentrale Regelgeving)
# ---------------------------------------------------------------------------

def parse_lokale_regelgeving_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse CVDR local/provincial/waterboard regulations.

    Handles two formats:
      1. cvdr_records.jsonl — written by the updated acquirer (2026-03-24+)
         Each line: {identifier, title, creator, modified, xml_url, text}
      2. cvdr_batch_*.xml  — legacy SRU batch XML files (old acquirer)
    """
    import json as _json

    acq_date = datetime.date.today().isoformat()

    # --- Format 1: JSONL (new acquirer) ---
    # Acquirer saves to {raw_dir}/{source_name}/cvdr_records.jsonl; also try raw_dir directly.
    jsonl_path = raw_dir / "cvdr_records.jsonl"
    if not jsonl_path.exists():
        jsonl_path = raw_dir / raw_dir.name / "cvdr_records.jsonl"
    if jsonl_path.exists():
        logger.info("[lokale_regelgeving] Parsing %s", jsonl_path.name)
        for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = _json.loads(line)
            except _json.JSONDecodeError:
                continue

            text = rec.get("text", "").strip()
            if len(text) < 50:
                continue

            identifier = rec.get("identifier", "")
            modified   = rec.get("modified", "")
            xml_url    = rec.get("xml_url", "") or f"https://lokaleregelgeving.overheid.nl/{identifier}"

            meta = DocumentMeta(
                id=make_id("lokale_regelgeving", identifier),
                source="lokale_regelgeving",
                source_url=xml_url,
                license="PD",
                publication_year=_parse_year(modified),
                country="NL",
                domain="legal",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
        return

    # --- Format 2: legacy XML batch files ---
    for xml_file in sorted(raw_dir.glob("cvdr_batch_*.xml")):
        logger.info("Parsing %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
            identifier = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}identifier",
                ".//{http://purl.org/dc/terms/}identifier",
            )
            date = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}date",
                ".//{http://purl.org/dc/terms/}date",
                ".//{http://purl.org/dc/terms/}modified",
            )
            text = _extract_text(data)
            if not text or len(text) < 50:
                continue

            raw_id = identifier or f"{xml_file.stem}_{i}"
            doc_id = make_id("lokale_regelgeving", raw_id)

            meta = DocumentMeta(
                id=doc_id,
                source="lokale_regelgeving",
                source_url=identifier or "https://lokaleregelgeving.overheid.nl",
                license="PD",
                publication_year=_parse_year(date),
                country="NL",
                domain="legal",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Officiële Bekendmakingen (Gemeenteblad, Provincieblad, Waterschapsblad, BGR)
# ---------------------------------------------------------------------------

_OB_AREA_META = {
    "gemeenteblad":     ("government", "NL"),
    "provincieblad":    ("government", "NL"),
    "waterschapsblad":  ("government", "NL"),
    "bgr":              ("government", "NL"),
}


def parse_officiele_bekendmakingen_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse official local gazette SRU batch files (Gemeenteblad, Provincieblad,
    Waterschapsblad, Blad Gemeenschappelijke Regelingen).

    raw_dir contains one subdir per gazette type, each with batch_*.xml files.
    """
    acq_date = datetime.date.today().isoformat()

    for area_dir in sorted(raw_dir.iterdir()):
        if not area_dir.is_dir():
            continue
        area = area_dir.name
        domain, country = _OB_AREA_META.get(area, ("government", "NL"))

        for xml_file in sorted(area_dir.glob("batch_*.xml")):
            logger.info("Parsing %s/%s", area, xml_file.name)
            try:
                xml_bytes = xml_file.read_bytes()
            except OSError as exc:
                logger.warning("Cannot read %s: %s", xml_file, exc)
                continue

            for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
                identifier = _find_text(data,
                    ".//{http://purl.org/dc/elements/1.1/}identifier",
                    ".//{http://purl.org/dc/terms/}identifier",
                )
                date = _find_text(data,
                    ".//{http://purl.org/dc/elements/1.1/}date",
                    ".//{http://purl.org/dc/terms/}date",
                )
                text = _extract_text(data)
                if not text or len(text) < 50:
                    continue

                raw_id = identifier or f"{area}_{xml_file.stem}_{i}"
                doc_id = make_id("officiele_bekendmakingen", raw_id)

                meta = DocumentMeta(
                    id=doc_id,
                    source=f"officiele_bekendmakingen:{area}",
                    source_url=identifier or "https://www.officielebekendmakingen.nl",
                    license="PD",
                    publication_year=_parse_year(date),
                    country=country,
                    domain=domain,
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Eerste Kamer — Dutch Senate plenary Handelingen (CC0, 1995-present)
# ---------------------------------------------------------------------------

def parse_eerste_kamer_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Eerste Kamer SRU batch XML files (handelingen_ek/, agenda_ek/ subdirs).
    Same format as parse_officiele_bekendmakingen_files.
    """
    acq_date = datetime.date.today().isoformat()

    for area_dir in sorted(raw_dir.iterdir()):
        if not area_dir.is_dir():
            continue
        area = area_dir.name

        for xml_file in sorted(area_dir.glob("batch_*.xml")):
            try:
                xml_bytes = xml_file.read_bytes()
            except OSError as exc:
                logger.warning("[ek] Cannot read %s: %s", xml_file, exc)
                continue

            for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
                identifier = _find_text(data,
                    ".//{http://purl.org/dc/elements/1.1/}identifier",
                    ".//{http://purl.org/dc/terms/}identifier",
                )
                date = _find_text(data,
                    ".//{http://purl.org/dc/elements/1.1/}date",
                    ".//{http://purl.org/dc/terms/}date",
                )
                text = _extract_text(data)
                if not text or len(text) < 50:
                    continue

                raw_id = identifier or f"{area}_{xml_file.stem}_{i}"
                meta = DocumentMeta(
                    id=make_id("eerste_kamer", raw_id),
                    source="eerste_kamer",
                    source_url=identifier or "https://www.eerstekamer.nl/",
                    license="CC0",
                    publication_year=_parse_year(date),
                    country="NL",
                    domain="parliamentary",
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# PUC — Publieke UitvoeringsCommunicatie (executive-agency documents, CC0)
# ---------------------------------------------------------------------------

def parse_puc_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse PUC uitvoeringsinformatie SRU batch XML files from overheid.nl.

    raw_dir contains batch_*.xml files in the same SRU/GZD format as
    officiele_bekendmakingen.  Documents include policy handbooks,
    work instructions, and agency announcements.
    License: CC0.
    """
    acq_date = datetime.date.today().isoformat()

    for xml_file in sorted(raw_dir.glob("batch_*.xml")):
        logger.info("[puc] Parsing %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("[puc] Cannot read %s: %s", xml_file, exc)
            continue

        for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
            identifier = _find_text(
                data,
                ".//{http://purl.org/dc/elements/1.1/}identifier",
                ".//{http://purl.org/dc/terms/}identifier",
            )
            date = _find_text(
                data,
                ".//{http://purl.org/dc/elements/1.1/}date",
                ".//{http://purl.org/dc/terms/}date",
            )
            text = _extract_text(data)
            if not text or len(text) < 50:
                continue

            raw_id = identifier or f"puc_{xml_file.stem}_{i}"
            doc_id = make_id("puc", raw_id)

            meta = DocumentMeta(
                id=doc_id,
                source="puc",
                source_url=identifier or "https://repository.overheid.nl",
                license="CC0",
                publication_year=_parse_year(date),
                country="NL",
                domain="government",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Tuchtrecht (disciplinary tribunal decisions)
# ---------------------------------------------------------------------------

def parse_tuchtrecht_files(raw_dir: Path) -> Iterator[Document]:
    """Parse tuchtrecht SRU batch XML files from overheid.nl."""
    acq_date = datetime.date.today().isoformat()

    for xml_file in sorted(raw_dir.glob("tucht_batch_*.xml")):
        logger.info("Parsing %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
            identifier = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}identifier",
                ".//{http://purl.org/dc/terms/}identifier",
            )
            date = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}date",
                ".//{http://purl.org/dc/terms/}date",
            )
            text = _extract_text(data)
            if not text or len(text) < 50:
                continue

            raw_id = identifier or f"{xml_file.stem}_{i}"
            doc_id = make_id("tuchtrecht", raw_id)

            meta = DocumentMeta(
                id=doc_id,
                source="tuchtrecht",
                source_url=identifier or "https://tuchtrecht.overheid.nl",
                license="PD",
                publication_year=_parse_year(date),
                country="NL",
                domain="legal",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Tractatenblad (Dutch treaty gazette)
# ---------------------------------------------------------------------------

def parse_tractatenblad_files(raw_dir: Path) -> Iterator[Document]:
    """Parse Tractatenblad SRU batch XML files from overheid.nl."""
    acq_date = datetime.date.today().isoformat()

    for xml_file in sorted(raw_dir.glob("trb_batch_*.xml")):
        logger.info("Parsing %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
            identifier = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}identifier",
                ".//{http://purl.org/dc/terms/}identifier",
            )
            date = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}date",
                ".//{http://purl.org/dc/terms/}date",
            )
            text = _extract_text(data)
            if not text or len(text) < 50:
                continue

            raw_id = identifier or f"{xml_file.stem}_{i}"
            doc_id = make_id("tractatenblad", raw_id)

            meta = DocumentMeta(
                id=doc_id,
                source="tractatenblad",
                source_url=identifier or "https://www.officielebekendmakingen.nl",
                license="PD",
                publication_year=_parse_year(date),
                country="NL",
                domain="legal",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Kamerstukken (Dutch parliamentary documents)
# ---------------------------------------------------------------------------

def parse_kamerstukken_files(raw_dir: Path) -> Iterator[Document]:
    """Parse Kamerstukken SRU batch XML files from overheid.nl.

    Handles all six document type areas (kst, ah_tk, blg, tk, ek, ah_ek)
    that the acquirer saves as ``{type}_batch_{start:08d}.xml``.
    """
    acq_date = datetime.date.today().isoformat()

    for xml_file in sorted(raw_dir.glob("*_batch_*.xml")):
        logger.info("[kamerstukken] Parsing %s", xml_file.name)
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file, exc)
            continue

        for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
            identifier = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}identifier",
                ".//{http://purl.org/dc/terms/}identifier",
            )
            date = _find_text(data,
                ".//{http://purl.org/dc/elements/1.1/}date",
                ".//{http://purl.org/dc/terms/}date",
            )
            text = _extract_text(data)
            if not text or len(text) < 50:
                continue

            raw_id = identifier or f"{xml_file.stem}_{i}"
            doc_id = make_id("kamerstukken", raw_id)

            meta = DocumentMeta(
                id=doc_id,
                source="kamerstukken",
                source_url=identifier or "https://www.officielebekendmakingen.nl",
                license="PD",
                publication_year=_parse_year(date),
                country="NL",
                domain="parliamentary",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# BES Bekendmakingen (Bonaire / Sint Eustatius / Saba)
# ---------------------------------------------------------------------------

def parse_bes_bekendmakingen_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse BES island official publications from raw_dir.

    Expects subdirs per product area (ab/, blad/) each containing:
      xml/  — individual publication XML files downloaded via SRU
      metadata.jsonl — record metadata

    The XML files follow the same overheid.nl SRU/GZD schema.
    """
    acq_date  = datetime.date.today().isoformat()
    doc_count = 0

    for area_dir in sorted(raw_dir.iterdir()):
        if not area_dir.is_dir():
            continue
        xml_dir = area_dir / "xml"
        if not xml_dir.exists():
            continue

        for xml_file in sorted(xml_dir.glob("*.xml")):
            try:
                xml_bytes = xml_file.read_bytes()
            except OSError as exc:
                logger.warning("Cannot read %s: %s", xml_file, exc)
                continue

            for i, data in enumerate(_records_from_sru_xml(xml_bytes)):
                identifier = _find_text(data,
                    ".//{http://purl.org/dc/elements/1.1/}identifier",
                    ".//{http://purl.org/dc/terms/}identifier",
                )
                date = _find_text(data,
                    ".//{http://purl.org/dc/elements/1.1/}date",
                    ".//{http://purl.org/dc/terms/}date",
                )
                text = _extract_text(data)
                if not text or len(text) < 50:
                    continue

                raw_id = identifier or f"{xml_file.stem}_{i}"
                doc_id = make_id("bes_bekendmakingen", raw_id)

                meta = DocumentMeta(
                    id=doc_id,
                    source="bes_bekendmakingen",
                    source_url=identifier or "https://www.officielebekendmakingen.nl",
                    license="PD",
                    publication_year=_parse_year(date),
                    country="NL",
                    domain="government",
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                doc_count += 1

    logger.info("[bes_bekendmakingen] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# Rechtspraak Caribisch — Caribbean court decisions
# ---------------------------------------------------------------------------

def parse_rechtspraak_caribisch_files(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Caribbean court decisions from raw_dir.

    Supports two layouts:
    - Flat:    raw_dir/decisions/*.xml       (current acquirer output)
    - By court: raw_dir/{court}/decisions/*.xml  (legacy layout)

    Uses the same ECLI XML parser as the mainland rechtspraak source.
    """
    doc_count = 0

    def _yield_from(xml_files: list[Path], label: str) -> None:
        nonlocal doc_count
        logger.info("[rechtspraak_caribisch:%s] Parsing %d decision XMLs", label, len(xml_files))
        for xml_file in xml_files:
            doc = parse_ecli_xml_file(xml_file)
            if doc is None:
                continue
            doc.meta.source           = "rechtspraak_caribisch"
            doc.meta.language_variant = "NL"
            yield doc
            doc_count += 1

    # Flat layout: raw_dir/decisions/*.xml
    flat_decisions = raw_dir / "decisions"
    if flat_decisions.exists() and any(flat_decisions.glob("*.xml")):
        yield from _yield_from(sorted(flat_decisions.glob("*.xml")), "flat")
    else:
        # Per-court layout: raw_dir/{court}/decisions/*.xml
        for court_dir in sorted(raw_dir.iterdir()):
            if not court_dir.is_dir():
                continue
            decisions_dir = court_dir / "decisions"
            if not decisions_dir.exists():
                continue
            yield from _yield_from(sorted(decisions_dir.glob("*.xml")), court_dir.name)

    logger.info("[rechtspraak_caribisch] Parsed %d documents", doc_count)
