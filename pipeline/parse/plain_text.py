"""
Parse plain-text, JSON, and XML files from multiple acquirers.

parse_gutenberg_dir    — .txt files; strips Gutenberg header/footer boilerplate
parse_europarl_dir     — Europarl session .txt files with <CHAPTER>/<P> markup
parse_delpher_dir      — .txt files; raw OCR articles (one per file)
parse_rijksoverheid_dir — .json files; extracts title + body HTML → plain text
parse_codex_dir        — .json files from Vlaamse Codex VolledigDocument API
parse_vlpar_dir        — .xml files from Vlaams Parlement handelingen API
parse_jrc_dir          — TEI XML files from JRC-Acquis corpus
parse_mozilla_dc_dir   — TSV shards from Mozilla Data Collective speaker datasets
parse_dracor_dir       — TEI XML plays from Dutch Drama Corpus (DutchDraCor)
parse_tatoeba_dir      — TSV sentences from Tatoeba (nld)
parse_emea_dir         — Plain-text sentences from EMEA corpus (OPUS)
parse_opus_extra_dir   — OPUS monolingual corpora (EUconst, KDE4, Ubuntu, GNOME, PHP, ECB)
parse_ud_dutch_dir     — UD Dutch treebank sentences (Alpino, LassySmall)
parse_openboek_dir     — Dutch literary texts from OpenBoek (CC-BY 4.0)
parse_renaissance_poetry_dir — Dutch Renaissance poetry corpus (CC0)
parse_europeana_ner_dir      — Europeana Newspapers Dutch NER corpus (CC0, historical)
parse_brieven_als_buit_dir   — Early Modern Dutch letters, Brieven als Buit (CC-BY)
parse_dcep_dir               — Digital Corpus of the European Parliament (EU open re-use ≈ CC-BY)
parse_advisory_bodies_dir    — WRR, CPB, PBL, Rekenkamer, SCP, Gezondheidsraad, Onderwijsraad, Raad voor Cultuur, ROB (CC-BY / PD / CC0)
parse_oscar_dir              — OSCAR Dutch Common Crawl subset (CC0)
parse_hplt_dir               — HPLT Dutch Internet Archive corpus (CC0)
parse_rijksmuseum_dir        — Rijksmuseum collection metadata (CC0)
parse_openalex_dir           — OpenAlex Dutch scientific paper abstracts (CC0)
parse_voc_wic_htr_dir        — VOC/WIC HTR Early Modern Dutch transcriptions (CC BY 4.0)
parse_parlamint_nl_dir       — ParlaMint-NL Tweede Kamer TEI XML (CC BY 4.0)
parse_nationaal_archief_dir  — Nationaal Archief OAI-PMH finding aids (CC0)
parse_etaamb_be_dir          — Belgian Gazette etaamb.openjustice.be Dutch texts (PD)
parse_staten_generaal_dir    — Staten-Generaal Digitaal KB SRU proceedings 1814-1995 (CC0)
parse_voxpopuli_dir          — VoxPopuli European Parliament speech transcripts CC0
parse_pmc_nl_dir             — PubMed Central Dutch open-access articles (CC-BY)
parse_nchlt_af_dir           — NCHLT Afrikaans government text corpus (CC BY 2.5 ZA)
parse_autshumato_af_dir      — Autshumato English-Afrikaans parallel corpus Afrikaans side (CC BY 4.0)
parse_parlamint_be_dir       — ParlaMint-BE Belgian Chamber TEI XML Dutch utterances (CC BY 4.0)
parse_tweede_kamer_opendata_dir — Tweede Kamer SyncFeed Atom parliamentary text (CC0)
parse_multi_eurlex_dir         — MultiEURLEX EU legal docs nl/af/fy (CC-BY-4.0)
parse_teu_tenders_dir          — TED EU procurement tenders (CC0)
parse_digibron_dir             — Digibron pre-1954 Dutch Protestant periodicals (PD)
parse_oapen_nl_dir             — OAPEN Dutch open-access academic books (CC-BY)
parse_naturalis_dir            — Naturalis Biodiversity Center publications (CC-BY)
parse_open_raadsinformatie_dir — ORI municipal council documents (PD)
parse_kb_open_kranten_dir      — KB Open Kranten historical newspapers ALTO XML (CC-BY 4.0)
parse_juportal_be_dir          — JuPortal Belgian court decisions in Dutch (PD)
parse_woo_documents_dir        — Woo open government request documents (PD)
parse_vlpar_extra_dir          — .xml jln files from Vlaams Parlement journaallijn API (CC-BY)
parse_woogle_foia_dir          — Woogle FOIA (Woo/Wob) Dutch government documents (CC BY 4.0)
parse_open_data_archieven_dir  — Open Data Archieven EAD finding aids via OAI-PMH (CC0)
parse_variants_corpus_dir      — VariaNTS spoken Dutch corpus transcriptions (CC BY 4.0, Zenodo)
parse_nchlt_af_ner_dir         — NCHLT Afrikaans NER annotated corpus text (CC BY 2.5 ZA, SADiLaR)
parse_puc_dir                  — PUC uitvoeringsinformatie PDFs (CC0, puc.overheid.nl)
parse_wikiwijs_dir             — Wikiwijs open educational resources (CC0/CC-BY/CC-BY-SA, kennisnet.nl)
parse_brussels_parlement_dir   — Brussels Parliament plenaire session reports (PD, NL-BE)
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from pipeline.clean.html import strip_html
from pipeline.document import Document, make_id
from pipeline.parse.html_page import _read_url_sidecar
from pipeline.schema import DocumentMeta, ProvenanceEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# License normalizer
# Ensures every document carries a canonical LicenseType value regardless of
# how the source metadata spells the license string.
# ---------------------------------------------------------------------------
_LICENSE_NORM: dict[str, str] = {
    "cc0":            "CC0",
    "cc-0":           "CC0",
    "cc0-1.0":        "CC0",
    "cc by":          "CC-BY",
    "cc-by":          "CC-BY",
    "cc by 4.0":      "CC-BY",
    "cc by 3.0":      "CC-BY",
    "cc-by-4.0":      "CC-BY",
    "cc-by-3.0":      "CC-BY",
    "cc by sa":       "CC-BY-SA",
    "cc-by-sa":       "CC-BY-SA",
    "cc by-sa 4.0":   "CC-BY-SA",
    "cc-by-sa-4.0":   "CC-BY-SA",
    "cc by-sa 3.0":   "CC-BY-SA",
    "pd":             "PD",
    "public domain":  "PD",
    "odc-by":         "ODC-By",
    "odc by":         "ODC-By",
}


def _norm_license(raw: str) -> str:
    """Canonicalize a license string to a LicenseType value."""
    return _LICENSE_NORM.get(raw.strip().lower(), raw.strip())

# ---------------------------------------------------------------------------
# Gutenberg
# ---------------------------------------------------------------------------

# Markers that delimit the actual book text inside a Gutenberg .txt file
_PG_START = re.compile(
    r"\*{3}\s*START OF (?:THIS |THE )?PROJECT GUTENBERG.*?\*{3}", re.IGNORECASE
)
_PG_END = re.compile(
    r"\*{3}\s*END OF (?:THIS |THE )?PROJECT GUTENBERG.*?\*{3}", re.IGNORECASE
)


def _strip_gutenberg_boilerplate(raw: str) -> str:
    """Return the text between Gutenberg start/end markers, or the full text."""
    start_m = _PG_START.search(raw)
    end_m   = _PG_END.search(raw)
    if start_m and end_m and start_m.end() < end_m.start():
        return raw[start_m.end(): end_m.start()].strip()
    if start_m:
        return raw[start_m.end():].strip()
    return raw.strip()


def parse_gutenberg_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Gutenberg plain-text book files, one Document per book."""
    acq_date = datetime.date.today().isoformat()

    for txt_file in sorted(raw_dir.glob("*.txt")):
        try:
            raw = txt_file.read_bytes().decode("utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", txt_file.name, exc)
            continue

        text = _strip_gutenberg_boilerplate(raw)
        text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
        if len(text) < 200:
            continue

        pg_id      = txt_file.stem          # numeric Gutenberg ID
        source_url = _read_url_sidecar(txt_file) or f"https://www.gutenberg.org/ebooks/{pg_id}"
        doc_id     = make_id("gutenberg", pg_id)

        # Rough publication year from URL or filename (not always present)
        year_m = re.search(r"\b(1[5-9]\d{2}|20[012]\d)\b", source_url)
        year   = int(year_m.group(0)) if year_m else None

        meta = DocumentMeta(
            id=doc_id,
            source="gutenberg",
            source_url=source_url,
            license="PD",
            publication_year=year,
            country="NL",
            domain="literature",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Europarl
# ---------------------------------------------------------------------------

_EP_TAG = re.compile(r"<[^>]+>")


def _parse_europarl_session(raw: str) -> str:
    """Strip Europarl XML-like tags and return clean text."""
    text = _EP_TAG.sub(" ", raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ep_year_from_filename(name: str) -> int | None:
    """ep-96-04-15.txt → 1996, ep-11-12-14.txt → 2011."""
    m = re.search(r"ep-(\d{2})-", name)
    if not m:
        return None
    yy = int(m.group(1))
    # Europarl v7 covers 1996–2011; two-digit years in that range
    return 1900 + yy if yy >= 96 else 2000 + yy


def parse_europarl_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Europarl session files.  One Document per session file."""
    acq_date = datetime.date.today().isoformat()
    sessions_dir = raw_dir / "sessions"
    search_dir   = sessions_dir if sessions_dir.exists() else raw_dir

    for txt_file in sorted(search_dir.glob("*.txt")):
        try:
            raw = txt_file.read_bytes().decode("utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", txt_file.name, exc)
            continue

        text = _parse_europarl_session(raw)
        if len(text) < 100:
            continue

        year   = _ep_year_from_filename(txt_file.name)
        doc_id = make_id("europarl", txt_file.stem)
        meta   = DocumentMeta(
            id=doc_id,
            source="europarl",
            source_url="https://www.statmt.org/europarl/",
            license="PD",
            publication_year=year,
            country="EU",
            domain="parliamentary",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Delpher
# ---------------------------------------------------------------------------

def parse_delpher_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Delpher OCR article text files, one Document per article."""
    acq_date = datetime.date.today().isoformat()

    for txt_file in sorted(raw_dir.glob("*.txt")):
        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", txt_file.name, exc)
            continue

        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 80:
            continue

        source_url = _read_url_sidecar(txt_file) or str(txt_file)
        doc_id     = make_id("delpher", txt_file.stem)

        # Year often embedded in URN (e.g. ddd:010864687 from 19xx articles)
        year_m = re.search(r"\b(1[6-9]\d{2})\b", source_url)
        year   = int(year_m.group(0)) if year_m else None

        meta = DocumentMeta(
            id=doc_id,
            source="delpher",
            source_url=source_url,
            license="CC0",
            publication_year=year,
            country="NL",
            domain="news",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Rijksoverheid
# ---------------------------------------------------------------------------

def _rijksoverheid_text(doc: dict) -> str:
    """Extract plain text from a Rijksoverheid JSON document."""
    parts: list[str] = []
    title = doc.get("title") or doc.get("onderwerp") or ""
    if title:
        parts.append(title)

    intro = doc.get("introductie") or doc.get("samenvatting") or ""
    if intro:
        parts.append(strip_html(intro) if "<" in intro else intro)

    body = doc.get("body") or doc.get("content") or doc.get("tekst") or ""
    if body:
        parts.append(strip_html(body) if "<" in body else body)

    text = "\n\n".join(p.strip() for p in parts if p.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_rijksoverheid_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Rijksoverheid JSON documents from all category subdirs."""
    acq_date = datetime.date.today().isoformat()

    for json_file in sorted(raw_dir.rglob("*.json")):
        # Skip acquisition log
        if json_file.name == "acquisition_log.jsonl":
            continue
        try:
            doc = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cannot parse %s: %s", json_file.name, exc)
            continue

        text = _rijksoverheid_text(doc)
        if len(text) < 100:
            continue

        source_url = _read_url_sidecar(json_file) or \
                     doc.get("canonical") or doc.get("url") or str(json_file)
        doc_id     = make_id("rijksoverheid", json_file.stem)

        date_str = doc.get("available") or doc.get("date") or doc.get("updated") or ""
        year_m   = re.search(r"\b(20\d{2})\b", date_str)
        year     = int(year_m.group(0)) if year_m else None

        meta = DocumentMeta(
            id=doc_id,
            source="rijksoverheid",
            source_url=source_url,
            license="CC0",
            publication_year=year,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Vlaamse Codex
# ---------------------------------------------------------------------------

def _codex_text(doc: dict) -> str:
    """Extract plain text from a Vlaamse Codex document JSON.

    New format (2026-04): doc has a top-level 'text' field with pre-extracted
    article text from the WetgevingArtikelVersie API.
    Legacy format: doc has 'Document'/'Inhoudstafel' structure (VolledigDocument).
    """
    # New format: text pre-extracted at acquire time
    if "text" in doc:
        title = (doc.get("opschrift") or "").strip()
        body = (doc.get("text") or "").strip()
        text = (title + "\n\n" + body).strip() if title else body
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    # Legacy VolledigDocument format (fallback)
    parts: list[str] = []
    info = doc.get("Document") or {}
    title = (info.get("Titel") or "").strip()
    if title:
        parts.append(title)

    def _walk(node):
        if isinstance(node, dict):
            tekst = (node.get("Tekst") or "").strip()
            if tekst:
                tekst = re.sub(r"<[^>]+>", " ", tekst)
                tekst = re.sub(r"\s{2,}", " ", tekst).strip()
                if tekst:
                    parts.append(tekst)
            for key in ("Items", "Subartikel", "Paragraaf", "Lid", "Alinea", "Onderdeel"):
                child = node.get(key)
                if isinstance(child, list):
                    for item in child:
                        _walk(item)
                elif isinstance(child, dict):
                    _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(doc.get("Inhoudstafel", []))
    text = "\n\n".join(p for p in parts if p)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_codex_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Vlaamse Codex JSON documents, one Document per .json file."""
    acq_date = datetime.date.today().isoformat()

    for json_file in sorted(raw_dir.rglob("*.json")):
        if json_file.name == "acquisition_log.jsonl":
            continue
        try:
            doc = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cannot parse %s: %s", json_file.name, exc)
            continue

        text = _codex_text(doc)
        if len(text) < 100:
            continue

        # Support both new format (top-level fields) and legacy (_meta block)
        meta_block = doc.get("_meta") or {}
        source_url = (
            _read_url_sidecar(json_file)
            or doc.get("canonical")
            or meta_block.get("canonical")
            or str(json_file)
        )
        doc_id_raw = doc.get("id") or meta_block.get("id") or json_file.stem
        doc_id = make_id("codex", str(doc_id_raw))

        date_str = (
            doc.get("datum")
            or (doc.get("Document") or {}).get("InWerkingTredingsDatum")
            or (doc.get("Document") or {}).get("PublicatieDatum")
            or meta_block.get("acquisition_date")
            or ""
        )
        year_m = re.search(r"\b(20\d{2}|19\d{2})\b", date_str)
        year = int(year_m.group(0)) if year_m else None

        meta = DocumentMeta(
            id=doc_id,
            source="codex",
            source_url=source_url,
            license="CC0",
            publication_year=year,
            country="BE",
            domain="legislation",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Vlaams Parlement
# ---------------------------------------------------------------------------

def _vlpar_text(xml_bytes: bytes) -> str:
    """
    Extract plain text from Vlaams Parlement handelingen XML.

    Structure: <handelingen><journaallijnen><journaallijn id="...">
      <omschrijving><![CDATA[HTML...]]></omschrijving>
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug("Vlaams Parlement XML parse error: %s", exc)
        return ""

    parts: list[str] = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local.lower() in ("omschrijving", "tekst", "inhoud", "title", "titel"):
            raw = (el.text or "").strip()
            if raw:
                # Strip HTML tags (omschrijving contains CDATA HTML)
                cleaned = re.sub(r"<[^>]+>", " ", raw)
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
                if cleaned:
                    parts.append(cleaned)

    text = "\n\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _vlpar_year(xml_bytes: bytes) -> int | None:
    """Extract publication year from handelingen XML date attributes or content."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    for attr in ("datum", "date", "jaar", "year"):
        val = root.get(attr)
        if val:
            m = re.search(r"\b(19|20)\d{2}\b", val)
            if m:
                return int(m.group(0))
    # Try first date-like string in text
    text = ET.tostring(root, encoding="unicode")[:2000]
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group(0)) if m else None


def parse_vlpar_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Vlaams Parlement handelingen XML files, one Document per meeting."""
    acq_date = datetime.date.today().isoformat()

    for xml_file in sorted(raw_dir.glob("*.xml")):
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file.name, exc)
            continue

        text = _vlpar_text(xml_bytes)
        if len(text) < 100:
            continue

        source_url = (
            _read_url_sidecar(xml_file)
            or f"https://www.vlaamsparlement.be/nl/vergadering/{xml_file.stem}"
        )
        doc_id = make_id("vlpar", xml_file.stem)
        year = _vlpar_year(xml_bytes)

        meta = DocumentMeta(
            id=doc_id,
            source="vlpar",
            source_url=source_url,
            license="CC-BY",
            publication_year=year,
            country="BE",
            domain="parliamentary",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# JRC-Acquis (TEI XML)
# ---------------------------------------------------------------------------

def _jrc_text(xml_bytes: bytes) -> str:
    """
    Extract plain text from a JRC-Acquis TEI XML document.

    Structure:
      <TEI><teiHeader>...<title>...</title>...</teiHeader>
           <text><body><div><p>...</p></div></body></text></TEI>
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug("JRC TEI XML parse error: %s", exc)
        return ""

    parts: list[str] = []

    # Title from teiHeader (first title element)
    for title_el in root.iter():
        local = title_el.tag.split("}")[-1] if "}" in title_el.tag else title_el.tag
        if local == "title":
            t = (title_el.text or "").strip()
            if t:
                parts.append(t)
            break

    # Body paragraphs
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "p":
            para_text = "".join(el.itertext()).strip()
            if para_text:
                parts.append(para_text)

    text = "\n\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _jrc_year(xml_bytes: bytes) -> int | None:
    """Extract publication year from JRC TEI header."""
    snippet = xml_bytes[:3000].decode("utf-8", errors="replace")
    m = re.search(r"\b(19|20)\d{2}\b", snippet)
    return int(m.group(0)) if m else None


def _jrc_doc_url(xml_bytes: bytes, filename: str) -> str:
    """Try to extract EUR-Lex URL or CELEX number from JRC TEI header."""
    snippet = xml_bytes[:3000].decode("utf-8", errors="replace")
    m = re.search(r"\b(\d{1,2}[A-Z]{1,2}\d{4}[A-Z]\d{4,})\b", snippet)
    if m:
        return f"https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:{m.group(1)}"
    return f"https://wt-public.emm4u.eu/Acquis/JRC-Acquis.3.0/corpus/{filename}"


def parse_jrc_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse JRC-Acquis TEI XML files from the docs/ subdirectory."""
    acq_date = datetime.date.today().isoformat()

    docs_dir = raw_dir / "docs"
    search_dir = docs_dir if docs_dir.exists() else raw_dir

    for xml_file in sorted(search_dir.glob("*.xml")):
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", xml_file.name, exc)
            continue

        text = _jrc_text(xml_bytes)
        if len(text) < 100:
            continue

        source_url = _read_url_sidecar(xml_file) or _jrc_doc_url(xml_bytes, xml_file.name)
        doc_id = make_id("jrc", xml_file.stem)
        year = _jrc_year(xml_bytes)

        meta = DocumentMeta(
            id=doc_id,
            source="jrc",
            source_url=source_url,
            license="CC-BY",
            publication_year=year,
            country="EU",
            domain="legislation",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# DBNL (Digitale Bibliotheek voor de Nederlandse Letteren)
# ---------------------------------------------------------------------------

def _dbnl_year(text: str, filename: str) -> int | None:
    """
    Guess publication year for a DBNL text.

    DBNL filenames encode the work ID (e.g. 'mult001cour01_01.txt');
    the year is not in the filename.  We scan the first 500 chars
    for a plausible year (1200-1950 for historical literary works).
    """
    snippet = text[:500]
    for m in re.finditer(r"\b(1[2-9]\d{2}|20[01]\d)\b", snippet):
        y = int(m.group(0))
        if 1200 <= y <= 1950:
            return y
    return None


def parse_dbnl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse DBNL public-domain Dutch literary texts.

    Reads .txt files extracted from the DBNL bulk ZIP (txt_pd.zip)
    in raw_dir/texts/.  Falls back to raw_dir if no texts/ subdir exists.
    """
    acq_date = datetime.date.today().isoformat()

    texts_dir = raw_dir / "texts"
    search_dir = texts_dir if texts_dir.exists() else raw_dir

    for txt_file in sorted(search_dir.glob("*.txt")):
        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", txt_file.name, exc)
            continue

        text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
        if len(text) < 200:
            continue

        doc_id = make_id("dbnl", txt_file.stem)
        # DBNL work URLs follow the pattern /tekst/{id}/{id}.php
        # The stem encodes the DBNL ID (e.g. mult001cour01_01)
        dbnl_id = re.sub(r"_\d+$", "", txt_file.stem)  # strip shard suffix if any
        source_url = f"https://www.dbnl.org/tekst/{dbnl_id}/{dbnl_id}.php"
        year = _dbnl_year(text, txt_file.name)

        meta = DocumentMeta(
            id=doc_id,
            source="dbnl",
            source_url=source_url,
            license="PD",
            publication_year=year,
            country="NL",
            domain="literature",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Common Voice (sentence transcripts)
# ---------------------------------------------------------------------------

# Sentences per document — batch to ensure docs are long enough for quality filters
_CV_BATCH = 100


def parse_common_voice_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Common Voice sentence transcript shards.

    Reads sentences_NNNN.tsv files from locale subdirectories
    (nl/ and nl_BE/ inside raw_dir).  Batches sentences into documents
    of _CV_BATCH sentences each to produce meaningful-length documents.
    """
    import csv as _csv

    acq_date = datetime.date.today().isoformat()

    # Look for locale subdirs (nl, nl_BE) or flat TSV files directly
    tsv_files: list[Path] = []
    for locale_dir in sorted(raw_dir.iterdir()):
        if locale_dir.is_dir():
            tsv_files.extend(sorted(locale_dir.glob("sentences_*.tsv")))
        elif locale_dir.suffix == ".tsv" and locale_dir.name.startswith("sentences_"):
            tsv_files.append(locale_dir)

    for tsv_file in tsv_files:
        # Derive locale from parent dir name
        locale_raw = tsv_file.parent.name if tsv_file.parent != raw_dir else "nl"
        locale = locale_raw.replace("_", "-")  # nl_BE → nl-BE
        country = "BE" if "BE" in locale.upper() else "NL"
        lang_variant = locale if "-" in locale else "NL"

        try:
            content = tsv_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", tsv_file.name, exc)
            continue

        reader = _csv.DictReader(
            __import__("io").StringIO(content), delimiter="\t"
        )
        sentences: list[str] = []
        batch_num = 0

        def _flush(batch: list[str], num: int) -> Document | None:
            if not batch:
                return None
            text = "\n".join(batch)
            if len(text) < 100:
                return None
            doc_id = make_id(f"common_voice_{locale}", f"{tsv_file.stem}_{num}")
            meta = DocumentMeta(
                id=doc_id,
                source="common_voice",
                source_url="https://commonvoice.mozilla.org",
                license="CC0",
                publication_year=None,
                country=country,
                domain="spoken",
                language_variant=lang_variant,
                acquisition_date=acq_date,
            )
            return Document(text=text, meta=meta)

        for row in reader:
            s = (row.get("sentence") or "").strip()
            if not s:
                continue
            sentences.append(s)
            if len(sentences) >= _CV_BATCH:
                doc = _flush(sentences, batch_num)
                if doc:
                    yield doc
                sentences = []
                batch_num += 1

        # Yield remaining
        doc = _flush(sentences, batch_num)
        if doc:
            yield doc


# ---------------------------------------------------------------------------
# Mozilla Data Collective speaker datasets (Nathalie, Flemishguy, Ronnie, Pim)
# ---------------------------------------------------------------------------

_MDC_LOCALE_MAP = {
    "flemishguy":        "NL-BE",
    "cv_frisian_spont":  "FY",
    "cv_frisian_script": "FY",
}


def parse_mozilla_dc_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Mozilla Data Collective single-speaker transcript shards.

    Reads sentences_NNNN.tsv files from dataset subdirectories
    (nathalie/, flemishguy/, ronnie/, pim/ inside raw_dir).
    Batches _CV_BATCH sentences into one Document.
    """
    import csv as _csv
    import io as _io

    acq_date = datetime.date.today().isoformat()

    for dataset_dir in sorted(raw_dir.iterdir()):
        if not dataset_dir.is_dir():
            continue
        dataset_name = dataset_dir.name
        locale = _MDC_LOCALE_MAP.get(dataset_name, "nl")
        country = "BE" if "BE" in locale.upper() else "NL"
        lang_variant = locale if "-" in locale else "NL"

        for tsv_file in sorted(dataset_dir.glob("sentences_*.tsv")):
            try:
                content = tsv_file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning("Cannot read %s: %s", tsv_file.name, exc)
                continue

            reader = _csv.DictReader(_io.StringIO(content), delimiter="\t")
            sentences: list[str] = []
            batch_num = 0

            def _flush_mdc(batch: list[str], num: int) -> Document | None:
                if not batch:
                    return None
                text = "\n".join(batch)
                if len(text) < 100:
                    return None
                doc_id = make_id(f"mozilla_dc_{dataset_name}", f"{tsv_file.stem}_{num}")
                meta = DocumentMeta(
                    id=doc_id,
                    source="mozilla_dc",
                    source_url="https://commonvoice.mozilla.org",
                    license="CC0",
                    publication_year=None,
                    country=country,
                    domain="spoken",
                    language_variant=lang_variant,
                    acquisition_date=acq_date,
                )
                return Document(text=text, meta=meta)

            for row in reader:
                s = (row.get("sentence") or row.get("text") or "").strip()
                if not s:
                    continue
                sentences.append(s)
                if len(sentences) >= _CV_BATCH:
                    doc = _flush_mdc(sentences, batch_num)
                    if doc:
                        yield doc
                    sentences = []
                    batch_num += 1

            doc = _flush_mdc(sentences, batch_num)
            if doc:
                yield doc


# ---------------------------------------------------------------------------
# DutchDraCor — Dutch Drama Corpus (CC0, TEI XML plays 1500-1800)
# ---------------------------------------------------------------------------

_TEI_NS = "http://www.tei-c.org/ns/1.0"


def _dracor_text(tree: ET.ElementTree) -> str:
    """Extract speech and stage directions from a DraCor TEI play."""
    root = tree.getroot()
    parts: list[str] = []

    # Title from teiHeader
    title_el = root.find(f".//{{{_TEI_NS}}}titleStmt/{{{_TEI_NS}}}title")
    if title_el is not None and title_el.text:
        parts.append(title_el.text.strip())

    # All text content from <body> (speeches + stage directions)
    body = root.find(f".//{{{_TEI_NS}}}body")
    if body is not None:
        for el in body.iter():
            if el.tag in (f"{{{_TEI_NS}}}sp", f"{{{_TEI_NS}}}stage",
                          f"{{{_TEI_NS}}}p",  f"{{{_TEI_NS}}}l"):
                text = " ".join((el.text or "").split())
                if text:
                    parts.append(text)

    return "\n".join(parts)


def _dracor_year(root: ET.Element) -> int | None:
    for date_el in root.iter(f"{{{_TEI_NS}}}date"):
        for attr in ("when", "notBefore", "from"):
            val = date_el.get(attr, "")
            if val and len(val) >= 4 and val[:4].isdigit():
                return int(val[:4])
    return None


def parse_dracor_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch Drama Corpus TEI XML plays (CC0).

    Expects TEI files in raw_dir/tei/*.xml — one file per play.
    """
    acq_date = datetime.date.today().isoformat()
    tei_dir = raw_dir / "tei"

    if not tei_dir.exists():
        logger.warning("[dracor] No tei/ subdirectory in %s", raw_dir)
        return

    for xml_file in sorted(tei_dir.glob("*.xml")):
        try:
            tree = ET.parse(xml_file)
        except ET.ParseError as exc:
            logger.warning("[dracor] XML parse error in %s: %s", xml_file.name, exc)
            continue

        text = _dracor_text(tree)
        if len(text) < 200:
            continue

        root = tree.getroot()
        year = _dracor_year(root)
        play_id = xml_file.stem  # e.g. "bredero-moortje"

        meta = DocumentMeta(
            id=make_id("dracor", play_id),
            source="dracor",
            source_url=f"https://dracor.org/dutch/{play_id}",
            license="CC0",
            publication_year=year,
            country="NL",
            domain="literature",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Tatoeba Dutch sentences (CC-BY 2.0)
# ---------------------------------------------------------------------------

def parse_tatoeba_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch and Afrikaans Tatoeba sentences (CC-BY 2.0).

    Reads {lang}_sentences.tsv (id TAB lang TAB text) for nld and afr.
    Batches _CV_BATCH sentences per Document.
    """
    import csv as _csv
    import io as _io

    acq_date = datetime.date.today().isoformat()

    _lang_meta = {
        "nld": ("NL", "NL", "https://tatoeba.org/nl/sentences/search?query=&from=nld"),
        "afr": ("ZA", "AF", "https://tatoeba.org/nl/sentences/search?query=&from=afr"),
        "nds": ("NL", "NDS-NL", "https://tatoeba.org/nl/sentences/search?query=&from=nds"),
    }

    for lang, (country, lang_variant, src_url) in _lang_meta.items():
        tsv_file = raw_dir / f"{lang}_sentences.tsv"
        if not tsv_file.exists():
            logger.warning("[tatoeba] %s not found in %s", tsv_file.name, raw_dir)
            continue

        try:
            content = tsv_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("[tatoeba] Cannot read %s: %s", tsv_file, exc)
            continue

        reader = _csv.reader(_io.StringIO(content), delimiter="\t")
        sentences: list[str] = []
        batch_num = 0

        def _flush_tat(batch: list[str], num: int, _lang=lang, _country=country,
                       _variant=lang_variant, _url=src_url, _date=acq_date) -> Document | None:
            if not batch:
                return None
            text = "\n".join(batch)
            if len(text) < 100:
                return None
            return Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id("tatoeba", f"{_lang}_{num}"),
                    source="tatoeba",
                    source_url=_url,
                    license="CC-BY",
                    publication_year=None,
                    country=_country,
                    domain="general",
                    language_variant=_variant,
                    acquisition_date=_date,
                ),
            )

        for row in reader:
            if len(row) < 3:
                continue
            s = row[2].strip()
            if not s:
                continue
            sentences.append(s)
            if len(sentences) >= _CV_BATCH:
                doc = _flush_tat(sentences, batch_num)
                if doc:
                    yield doc
                sentences = []
                batch_num += 1

        doc = _flush_tat(sentences, batch_num)
        if doc:
            yield doc


# ---------------------------------------------------------------------------
# EMEA — European Medicines Agency corpus (CC-BY 4.0 via OPUS)
# ---------------------------------------------------------------------------

def parse_emea_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse EMEA Dutch monolingual corpus (CC-BY 4.0).

    Reads nl.txt — one sentence per line. Groups lines into paragraph-sized
    documents (~500 lines each) to produce meaningful-length documents.
    """
    acq_date = datetime.date.today().isoformat()
    txt_file = raw_dir / "nl.txt"

    if not txt_file.exists():
        logger.warning("[emea] nl.txt not found in %s", raw_dir)
        return

    _LINES_PER_DOC = 500
    lines: list[str] = []
    doc_num = 0

    def _flush_emea(batch: list[str], num: int) -> Document | None:
        text = "\n".join(batch).strip()
        if len(text) < 200:
            return None
        return Document(
            text=text,
            meta=DocumentMeta(
                id=make_id("emea", str(num)),
                source="emea",
                source_url="https://opus.nlpl.eu/EMEA",
                license="CC-BY",
                publication_year=None,
                country="NL",
                domain="medical",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )

    try:
        with txt_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if s:
                    lines.append(s)
                if len(lines) >= _LINES_PER_DOC:
                    doc = _flush_emea(lines, doc_num)
                    if doc:
                        yield doc
                    lines = []
                    doc_num += 1
    except OSError as exc:
        logger.warning("[emea] Cannot read %s: %s", txt_file, exc)
        return

    doc = _flush_emea(lines, doc_num)
    if doc:
        yield doc


# ---------------------------------------------------------------------------
# Statenvertaling — 1637 Dutch Bible (Public Domain, USFX XML)
# ---------------------------------------------------------------------------

def parse_statenvertaling_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse the 1637 Dutch Statenvertaling from USFX XML.

    One Document per Bible book. Extracts verse text from <v> elements.
    """
    acq_date = datetime.date.today().isoformat()

    xml_file = raw_dir / "nl-statenvertaling.xml"
    if not xml_file.exists():
        # Try any XML in the directory
        candidates = list(raw_dir.glob("*.xml"))
        if not candidates:
            logger.warning("[statenvertaling] No XML file found in %s", raw_dir)
            return
        xml_file = candidates[0]

    try:
        tree = ET.parse(xml_file)
    except ET.ParseError as exc:
        logger.warning("[statenvertaling] XML parse error: %s", exc)
        return

    root = tree.getroot()

    # USFX namespace (may or may not be present)
    ns = root.tag.rpartition("}")[0].lstrip("{") if "}" in root.tag else ""
    _t = lambda tag: f"{{{ns}}}{tag}" if ns else tag  # noqa: E731

    books = root.findall(_t("book")) or root.findall("book")

    if not books:
        # CES format (cesDoc/text/body/div/seg): one div per chapter/book, seg per verse
        if root.tag == "cesDoc":
            # Collect text per top-level div (= book) under body
            body = root.find("text/body") or root.find("body")
            if body is not None:
                for book_div in body.findall("div"):
                    segs = [el.text.strip() for el in book_div.iter("seg") if el.text and el.text.strip()]
                    if not segs:
                        continue
                    text = " ".join(segs)
                    if len(text) < 100:
                        continue
                    book_id = book_div.get("n", book_div.get("id", "book"))
                    yield Document(
                        text=text,
                        meta=DocumentMeta(
                            id=make_id("statenvertaling", book_id),
                            source="statenvertaling",
                            source_url="https://nl.wikisource.org/wiki/Bijbel_(Statenvertaling)",
                            license="PD",
                            publication_year=1637,
                            country="NL",
                            domain="religious",
                            language_variant="NL",
                            historical_flag=True,
                            acquisition_date=acq_date,
                        ),
                    )
                return
        # Flat format: collect all verse text into one document
        verses = [el.text.strip() for el in root.iter() if el.tag in (_t("v"), "v") and el.text]
        if verses:
            text = " ".join(verses)
            yield Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id("statenvertaling", "full"),
                    source="statenvertaling",
                    source_url="https://nl.wikisource.org/wiki/Bijbel_(Statenvertaling)",
                    license="PD",
                    publication_year=1637,
                    country="NL",
                    domain="religious",
                    language_variant="NL",
                    acquisition_date=acq_date,
                ),
            )
        return

    for book_el in books:
        book_id = book_el.get("id", "")
        verses: list[str] = []
        for el in book_el.iter():
            if el.tag in (_t("v"), "v") and el.text:
                verses.append(el.text.strip())
        if not verses:
            continue
        text = " ".join(verses)
        if len(text) < 100:
            continue
        yield Document(
            text=text,
            meta=DocumentMeta(
                id=make_id("statenvertaling", book_id),
                source="statenvertaling",
                source_url="https://nl.wikisource.org/wiki/Bijbel_(Statenvertaling)",
                license="PD",
                publication_year=1637,
                country="NL",
                domain="religious",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )


# ---------------------------------------------------------------------------
# OpenRaadsinformatie — Dutch municipal council documents (CC0)
# ---------------------------------------------------------------------------

def parse_openraad_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse OpenRaadsinformatie JSONL shards.

    Each shard contains records with: id, title, text, municipality, date, source_url.
    """
    acq_date = datetime.date.today().isoformat()

    for jsonl_file in sorted(raw_dir.glob("openraad_*.jsonl")):
        try:
            with jsonl_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    text = (record.get("text") or "").strip()
                    if not text or len(text) < 100:
                        continue

                    title = (record.get("title") or "").strip()
                    if title:
                        text = f"{title}\n\n{text}"

                    date_str = record.get("date") or ""
                    year: int | None = None
                    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                        year = int(date_str[:4])

                    source_url = record.get("source_url") or "https://openraadsinformatie.nl"

                    yield Document(
                        text=text,
                        meta=DocumentMeta(
                            id=make_id("openraad", record.get("id") or line[:40]),
                            source="openraad",
                            source_url=source_url,
                            license="CC0",
                            publication_year=year,
                            country="NL",
                            domain="government",
                            language_variant="NL",
                            acquisition_date=acq_date,
                        ),
                    )
        except OSError as exc:
            logger.warning("[openraad] Cannot read %s: %s", jsonl_file.name, exc)


# ---------------------------------------------------------------------------
# MLS — Multilingual LibriSpeech Dutch (CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_mls_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse MLS Dutch sentence transcript shards (CC-BY 4.0).

    Reads sentences_NNNN.tsv files — same format as Common Voice shards.
    Batches _CV_BATCH sentences per Document.
    """
    import csv as _csv
    import io as _io

    acq_date = datetime.date.today().isoformat()

    for tsv_file in sorted(raw_dir.glob("sentences_*.tsv")):
        try:
            content = tsv_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("[mls] Cannot read %s: %s", tsv_file.name, exc)
            continue

        reader = _csv.DictReader(_io.StringIO(content), delimiter="\t")
        sentences: list[str] = []
        batch_num = 0

        def _flush_mls(batch: list[str], num: int) -> Document | None:
            if not batch:
                return None
            text = "\n".join(batch)
            if len(text) < 100:
                return None
            return Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id("mls", f"{tsv_file.stem}_{num}"),
                    source="mls",
                    source_url="https://huggingface.co/datasets/facebook/multilingual_librispeech",
                    license="CC-BY",
                    publication_year=None,
                    country="NL",
                    domain="literature",
                    language_variant="NL",
                    acquisition_date=acq_date,
                ),
            )

        for row in reader:
            s = (row.get("sentence") or "").strip()
            if not s:
                continue
            sentences.append(s)
            if len(sentences) >= _CV_BATCH:
                doc = _flush_mls(sentences, batch_num)
                if doc:
                    yield doc
                sentences = []
                batch_num += 1

        doc = _flush_mls(sentences, batch_num)
        if doc:
            yield doc


# ---------------------------------------------------------------------------
# OPUS extra corpora (EUconst, KDE4, Ubuntu, GNOME, PHP, ECB)
# ---------------------------------------------------------------------------

# Per-corpus metadata overrides: (license, domain, country)
_OPUS_EXTRA_META: dict[str, tuple[str, str, str]] = {
    "euconst":       ("PD",       "legal",     "EU"),
    "kde4":          ("CC-BY-SA", "software",  "NL"),
    "ubuntu":        ("CC-BY-SA", "software",  "NL"),
    "gnome":         ("CC-BY-SA", "software",  "NL"),
    "php":           ("CC-BY",    "technical", "NL"),
    "ecb":           ("CC-BY",    "financial", "EU"),
    "global_voices": ("CC-BY",    "news",      "NL"),
    "dgt":           ("PD",       "legal",     "EU"),
    "eubookshop":    ("PD",       "legal",     "EU"),
    "europat":       ("PD",       "legal",        "EU"),
    "nllb":          ("CC-BY-SA", "multilingual", "NL"),
    "wikimatrix":    ("CC-BY-SA", "encyclopedic", "NL"),
    "ccmatrix":      ("CC0",      "web",          "NL"),
    "opensubtitles": ("CC-BY-SA", "subtitles",    "NL"),
    "qed":           ("CC-BY",    "educational",  "NL"),
    "bible_uedin":   ("PD",       "religious",    "NL"),
    "opus_books":    ("CC-BY-SA", "literature",   "NL"),
}

_OPUS_LINES_PER_DOC = 500


def parse_opus_extra_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse OPUS monolingual Dutch corpora (EUconst, KDE4, Ubuntu, GNOME, PHP, ECB).

    Each corpus is stored as {corpus}/nl.txt — one sentence per line.
    Groups _OPUS_LINES_PER_DOC lines into one Document.
    """
    acq_date = datetime.date.today().isoformat()

    for corpus_dir in sorted(raw_dir.iterdir()):
        if not corpus_dir.is_dir():
            continue
        corpus = corpus_dir.name
        txt_file = corpus_dir / "nl.txt"
        if not txt_file.exists():
            logger.warning("[opus_extra] nl.txt not found in %s", corpus_dir)
            continue

        license_, domain, country = _OPUS_EXTRA_META.get(corpus, ("CC-BY", "general", "NL"))
        source_url = f"https://opus.nlpl.eu/{corpus.upper()}"
        doc_num = 0
        lines: list[str] = []

        def _flush_opus(batch: list[str], num: int) -> Document | None:
            text = "\n".join(batch).strip()
            if len(text) < 200:
                return None
            return Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id(f"opus_{corpus}", str(num)),
                    source="opus_extra",
                    source_url=source_url,
                    license=license_,
                    publication_year=None,
                    country=country,
                    domain=domain,
                    language_variant="NL",
                    acquisition_date=acq_date,
                ),
            )

        try:
            with txt_file.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    s = line.strip()
                    if s:
                        lines.append(s)
                    if len(lines) >= _OPUS_LINES_PER_DOC:
                        doc = _flush_opus(lines, doc_num)
                        if doc:
                            yield doc
                        lines = []
                        doc_num += 1
        except OSError as exc:
            logger.warning("[opus_extra] Cannot read %s: %s", txt_file, exc)
            continue

        doc = _flush_opus(lines, doc_num)
        if doc:
            yield doc


# ---------------------------------------------------------------------------
# UD Dutch treebanks (Alpino + LassySmall, CC-BY 4.0)
# ---------------------------------------------------------------------------

_UD_SENTENCES_PER_DOC = 200


def parse_ud_dutch_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse UD Dutch treebank sentence text.

    Reads {treebank}/sentences.txt — one sentence per line (extracted from CoNLL-U).
    Batches _UD_SENTENCES_PER_DOC sentences per Document.
    """
    acq_date = datetime.date.today().isoformat()

    # Per-treebank metadata overrides: (source_url, license, country, language_variant)
    _treebank_meta: dict[str, tuple[str, str, str, str]] = {
        "alpino":               ("https://github.com/UniversalDependencies/UD_Dutch-Alpino",        "CC-BY-SA", "NL", "NL"),
        "lassy_small":          ("https://github.com/UniversalDependencies/UD_Dutch-LassySmall",     "CC-BY-SA", "NL", "NL"),
        "afrikaans_afribooms":  ("https://github.com/UniversalDependencies/UD_Afrikaans-AfriBooms",  "CC-BY-SA", "ZA", "AF"),
        "frisian_dutch_fame":   ("https://github.com/UniversalDependencies/UD_Frisian_Dutch-Fame",   "CC-BY-SA", "NL", "FY"),
        "low_saxon_lsdc":       ("https://github.com/UniversalDependencies/UD_Low_Saxon-LSDC",       "CC-BY-SA", "NL", "NDS-NL"),
    }

    for tb_dir in sorted(raw_dir.iterdir()):
        if not tb_dir.is_dir():
            continue
        treebank = tb_dir.name
        sentences_file = tb_dir / "sentences.txt"
        if not sentences_file.exists():
            continue

        source_url, license_, country, lang_variant = _treebank_meta.get(
            treebank,
            ("https://universaldependencies.org", "CC-BY-SA", "NL", "NL"),
        )
        sentences: list[str] = []
        doc_num = 0

        def _flush_ud(batch: list[str], num: int) -> Document | None:
            text = "\n".join(batch).strip()
            if len(text) < 100:
                return None
            return Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id(f"ud_{treebank}", str(num)),
                    source="ud_dutch",
                    source_url=source_url,
                    license=license_,
                    publication_year=None,
                    country=country,
                    domain="general",
                    language_variant=lang_variant,
                    acquisition_date=acq_date,
                ),
            )

        try:
            with sentences_file.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    s = line.strip()
                    if not s:
                        continue
                    sentences.append(s)
                    if len(sentences) >= _UD_SENTENCES_PER_DOC:
                        doc = _flush_ud(sentences, doc_num)
                        if doc:
                            yield doc
                        sentences = []
                        doc_num += 1
        except OSError as exc:
            logger.warning("[ud_dutch] Cannot read %s: %s", sentences_file, exc)
            continue

        doc = _flush_ud(sentences, doc_num)
        if doc:
            yield doc


# ---------------------------------------------------------------------------
# OpenBoek — Dutch literary texts (CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_openboek_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse OpenBoek Dutch literary plain-text files (CC-BY 4.0).

    Reads .txt files from raw_dir/texts/ — one Document per file.
    """
    acq_date = datetime.date.today().isoformat()
    texts_dir = raw_dir / "texts"
    search_dir = texts_dir if texts_dir.exists() else raw_dir

    for txt_file in sorted(search_dir.glob("*.txt")):
        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[openboek] Cannot read %s: %s", txt_file.name, exc)
            continue

        text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
        if len(text) < 200:
            continue

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=make_id("openboek", txt_file.stem),
                source="openboek",
                source_url="https://github.com/andreasvc/openboek",
                license="CC-BY",
                publication_year=None,
                country="NL",
                domain="literature",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )


# ---------------------------------------------------------------------------
# Dutch Renaissance Poetry Corpus (CC0, 16th-17th century)
# ---------------------------------------------------------------------------

def parse_renaissance_poetry_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch Renaissance Poetry Corpus (CC0).

    Reads text files from raw_dir/texts/ — one Document per file.
    Covers 16th-17th century Dutch poetry (alexandrines, iambic pentameters).
    """
    acq_date = datetime.date.today().isoformat()
    texts_dir = raw_dir / "texts"
    search_dir = texts_dir if texts_dir.exists() else raw_dir

    _text_exts = {".txt", ".text", ".csv", ".tsv", ".xml"}

    for text_file in sorted(f for f in search_dir.iterdir()
                             if f.is_file() and f.suffix.lower() in _text_exts):
        try:
            text = text_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[renaissance_poetry] Cannot read %s: %s", text_file.name, exc)
            continue

        text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
        if len(text) < 100:
            continue

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=make_id("renaissance_poetry", text_file.stem),
                source="renaissance_poetry",
                source_url="https://github.com/mirsdes/Dutch_Renaissance_poetry_corpus",
                license="CC0",
                publication_year=None,
                country="NL",
                domain="literature",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )


# ---------------------------------------------------------------------------
# Europeana Newspapers NER — Dutch historical newspaper text (CC0)
# ---------------------------------------------------------------------------

def parse_europeana_ner_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Europeana Newspapers Dutch NER corpus (CC0).

    Reads BIO-tagged files from raw_dir/bio/ (e.g. enp_NL.kb.bio).
    Each file is OCR text from historical Dutch newspapers with NE annotations.
    Extracts the token stream, groups into sentence-batches per Document.
    """
    acq_date = datetime.date.today().isoformat()
    bio_dir = raw_dir / "bio"
    search_dir = bio_dir if bio_dir.exists() else raw_dir

    _SENTENCES_PER_DOC = 200

    for bio_file in sorted(search_dir.glob("enp_NL.*.bio")):
        try:
            content = bio_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("[europeana_ner] Cannot read %s: %s", bio_file.name, exc)
            continue

        # Parse BIO format: "token\tTAG" lines, blank lines = sentence breaks
        sentences: list[str] = []
        current_tokens: list[str] = []
        doc_num = 0

        def _flush_eur(sents: list[str], num: int) -> Document | None:
            text = "\n".join(sents).strip()
            if len(text) < 100:
                return None
            return Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id(f"europeana_ner_{bio_file.stem}", str(num)),
                    source="europeana_ner",
                    source_url="https://github.com/EuropeanaNewspapers/ner-corpora",
                    license="CC0",
                    publication_year=None,
                    country="NL",
                    domain="news",
                    language_variant="NL",
                    acquisition_date=acq_date,
                ),
            )

        for line in content.splitlines():
            line = line.strip()
            if not line:
                # Sentence boundary
                if current_tokens:
                    sentences.append(" ".join(current_tokens))
                    current_tokens = []
                if len(sentences) >= _SENTENCES_PER_DOC:
                    doc = _flush_eur(sentences, doc_num)
                    if doc:
                        yield doc
                    sentences = []
                    doc_num += 1
            else:
                # Token is the first tab-separated field
                token = line.split("\t")[0]
                if token:
                    current_tokens.append(token)

        # Flush remaining
        if current_tokens:
            sentences.append(" ".join(current_tokens))
        doc = _flush_eur(sentences, doc_num)
        if doc:
            yield doc


# ---------------------------------------------------------------------------
# Brieven als Buit — Early Modern Dutch correspondence (CC-BY 4.0)
# ---------------------------------------------------------------------------

def _bab_text_from_xml(xml_bytes: bytes) -> tuple[str, int | None]:
    """Extract text and year from a Brieven als Buit TEI/XML letter file."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return "", None

    # Try to find year from date elements
    year: int | None = None
    for el in root.iter():
        for attr in ("when", "notBefore", "from", "notAfter"):
            val = el.get(attr, "")
            if val and len(val) >= 4 and val[:4].isdigit():
                y = int(val[:4])
                if 1600 <= y <= 1820:
                    year = y
                    break
        if year:
            break
    if not year:
        # Scan raw text for 4-digit year in expected range
        snippet = xml_bytes[:2000].decode("utf-8", errors="replace")
        for m in re.finditer(r"\b(16\d{2}|17\d{2}|180\d|181\d)\b", snippet):
            year = int(m.group(0))
            break

    # Extract all paragraph/line text
    parts: list[str] = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local in ("p", "ab", "l", "seg", "s", "div"):
            t = " ".join((el.text or "").split())
            if t:
                parts.append(t)

    # Fallback: all text nodes
    if not parts:
        text = " ".join(root.itertext()).strip()
        text = re.sub(r"\s+", " ", text)
        return text, year

    return "\n".join(parts), year


def parse_brieven_als_buit_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Brieven als Buit Early Modern Dutch letters (CC-BY 4.0).

    Reads XML/TXT/TSV files from raw_dir/letters/.
    One Document per letter. Period: ~1650–1800.
    """
    acq_date = datetime.date.today().isoformat()
    letters_dir = raw_dir / "letters"
    search_dir = letters_dir if letters_dir.exists() else raw_dir

    for f in sorted(search_dir.iterdir()):
        if not f.is_file():
            continue

        ext = f.suffix.lower()
        text = ""
        year: int | None = None

        if ext == ".xml":
            try:
                raw = f.read_bytes()
            except OSError as exc:
                logger.warning("[brieven_als_buit] Cannot read %s: %s", f.name, exc)
                continue
            text, year = _bab_text_from_xml(raw)

        elif ext in (".txt", ".text"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace").strip()
            except OSError as exc:
                logger.warning("[brieven_als_buit] Cannot read %s: %s", f.name, exc)
                continue
            m = re.search(r"\b(16\d{2}|17\d{2}|180\d|181\d)\b", text[:500])
            year = int(m.group(0)) if m else None

        elif ext in (".csv", ".tsv"):
            import csv as _csv
            import io as _io
            delim = "\t" if ext == ".tsv" else ","
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning("[brieven_als_buit] Cannot read %s: %s", f.name, exc)
                continue
            _TEXT_COLS = ("text", "inhoud", "content", "brief", "transcriptie", "body")
            reader = _csv.DictReader(_io.StringIO(content), delimiter=delim)
            parts = []
            for row in reader:
                for col in _TEXT_COLS:
                    val = row.get(col, "").strip()
                    if val:
                        parts.append(val)
                        break
            text = "\n".join(parts)
        else:
            continue

        text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
        if len(text) < 100:
            continue

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=make_id("brieven_als_buit", f.stem),
                source="brieven_als_buit",
                source_url="https://brievenalsbuit.nl",
                license="CC-BY",
                publication_year=year or 1700,
                country="NL",
                domain="historical",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )


# ---------------------------------------------------------------------------
# DCEP — Digital Corpus of the European Parliament
# ---------------------------------------------------------------------------

def parse_dcep_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse the DCEP Dutch monolingual corpus (EU open re-use ≈ CC-BY).

    DCEP covers EP documents from 2001–2012 not included in Europarl:
    press releases, committee reports, written questions, legislative docs.

    Files are plain .txt, one sentence/paragraph per line. Groups 500 lines
    per Document.
    """
    acq_date = datetime.date.today().isoformat()
    _LINES_PER_DOC = 500

    txt_files = sorted(raw_dir.rglob("*.txt"))
    if not txt_files:
        logger.warning("[dcep] No .txt files found in %s", raw_dir)
        return

    doc_num = 0
    lines: list[str] = []

    def _flush(batch: list[str], num: int) -> Document | None:
        text = "\n".join(batch).strip()
        if len(text) < 200:
            return None
        return Document(
            text=text,
            meta=DocumentMeta(
                id=make_id("dcep", str(num)),
                source="dcep",
                source_url="https://joint-research-centre.ec.europa.eu/language-technology-resources/dcep-digital-corpus-european-parliament_en",
                license="CC-BY",
                publication_year=None,
                country="EU",
                domain="parliamentary",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )

    for txt_file in txt_files:
        try:
            for raw_line in txt_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                lines.append(line)
                if len(lines) >= _LINES_PER_DOC:
                    doc = _flush(lines, doc_num)
                    if doc:
                        doc_num += 1
                        yield doc
                    lines = []
        except Exception as exc:
            logger.warning("[dcep] Could not read %s: %s", txt_file, exc)

    if lines:
        doc = _flush(lines, doc_num)
        if doc:
            yield doc

    logger.info("[dcep] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# LBLOD — Lokale Beslissingen als Linked Open Data (Flanders, CC0)
# ---------------------------------------------------------------------------

# Predicates that carry decision text in LBLOD N-Triples
_LBLOD_TEXT_PREDICATES = {
    "http://data.vlaanderen.be/ns/besluit#motivering",
    "http://www.w3.org/ns/prov#value",
    "http://purl.org/dc/terms/description",
    "http://mu.semte.ch/vocabularies/ext/besluitenlijst",
}

_LBLOD_LITERAL_RE = re.compile(
    r'^"(.*)"(?:@nl|@nl-BE|@nl-NL|@nl-[A-Z]+|^^<[^>]*>)?\s*$',
    re.DOTALL,
)
_LBLOD_MIN_CHARS = 200
_LBLOD_BATCH_CHARS = 5_000   # group small decisions into one document


def _parse_nt_literal(obj: str) -> str | None:
    """Extract text from an N-Triples object literal, return None if not a string."""
    obj = obj.strip()
    if not obj.startswith('"'):
        return None
    # Strip trailing ^^<type> or @lang
    m = re.match(r'^"(.*)"(?:@\S+|\^\^<[^>]+>)?$', obj, re.DOTALL)
    if not m:
        return None
    # Unescape N-Triples escapes
    text = m.group(1)
    text = text.replace("\n", "\n").replace("\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
    return text.strip()


def parse_lblod_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse LBLOD Flemish local government decisions (CC0).

    Supports two raw formats:
    - besluiten.jsonl  — SPARQL-sourced JSONL (current acquirer output)
    - besluiten.nt     — N-Triples dump (legacy; falls back if jsonl absent)

    Groups short snippets into ~5000-char batches.
    """
    acq_date = datetime.date.today().isoformat()

    doc_num = 0
    buffer: list[str] = []
    buffer_chars = 0

    def _flush(parts: list[str], num: int) -> Document | None:
        text = "\n\n".join(p for p in parts if p)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < _LBLOD_MIN_CHARS:
            return None
        return Document(
            text=text,
            meta=DocumentMeta(
                id=make_id("lblod", str(num)),
                source="lblod",
                source_url="https://data.lblod.info",
                license="CC0",
                publication_year=None,
                country="BE",
                domain="government",
                language_variant="NL-BE",
                acquisition_date=acq_date,
            ),
        )

    # --- JSONL path (SPARQL output) ---
    jsonl_file = raw_dir / "besluiten.jsonl"
    if jsonl_file.exists():
        import json as _json
        try:
            for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                text = rec.get("text", "").strip()
                if not text or len(text) < 20:
                    continue
                buffer.append(text)
                buffer_chars += len(text)
                if buffer_chars >= _LBLOD_BATCH_CHARS:
                    doc = _flush(buffer, doc_num)
                    if doc:
                        doc_num += 1
                        yield doc
                    buffer = []
                    buffer_chars = 0
        except Exception as exc:
            logger.error("[lblod] Error reading besluiten.jsonl: %s", exc)
        if buffer:
            doc = _flush(buffer, doc_num)
            if doc:
                yield doc
        logger.info("[lblod] Parsed %d documents from besluiten.jsonl", doc_num)
        return

    # --- N-Triples path (legacy dump) ---
    nt_file = raw_dir / "besluiten.nt"
    if not nt_file.exists():
        logger.warning("[lblod] Neither besluiten.jsonl nor besluiten.nt found in %s", raw_dir)
        return

    # Reject HTML masquerading as N-Triples
    try:
        header = nt_file.read_bytes()[:20]
        if header.lstrip().startswith(b"<!"):
            logger.warning("[lblod] besluiten.nt appears to be HTML — re-run the acquirer")
            return
    except OSError:
        return

    try:
        for line in nt_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip(" .").split(" ", 2)
            if len(parts) < 3:
                continue
            predicate = parts[1].strip("<>")
            if predicate not in _LBLOD_TEXT_PREDICATES:
                continue
            obj_str = parts[2].strip()
            text = _parse_nt_literal(obj_str)
            if not text or len(text) < 20:
                continue
            buffer.append(text)
            buffer_chars += len(text)
            if buffer_chars >= _LBLOD_BATCH_CHARS:
                doc = _flush(buffer, doc_num)
                if doc:
                    doc_num += 1
                    yield doc
                buffer = []
                buffer_chars = 0
    except Exception as exc:
        logger.error("[lblod] Error reading besluiten.nt: %s", exc)
        return

    if buffer:
        doc = _flush(buffer, doc_num)
        if doc:
            yield doc

    logger.info("[lblod] Parsed %d documents from besluiten.nt", doc_num)


# ---------------------------------------------------------------------------
# Advisory bodies: WRR, SER, CPB, PBL
# ---------------------------------------------------------------------------

_ADVISORY_META: dict[str, tuple[str, str, str]] = {
    # body_name: (license, domain, country)
    "wrr":              ("CC-BY", "government", "NL"),
    "ser":              ("CC-BY", "government", "NL"),
    "cpb":              ("PD",    "government", "NL"),
    "pbl":              ("CC-BY", "government", "NL"),
    "rekenkamer":       ("CC0",   "government", "NL"),
    "scp_nl":           ("CC0",   "government", "NL"),
    "gezondheidsraad":  ("CC0",   "government", "NL"),
    "onderwijsraad":    ("CC0",   "government", "NL"),
    "raad_voor_cultuur":("CC0",   "government", "NL"),
    "rob":              ("CC0",   "government", "NL"),
}


def parse_advisory_bodies_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse publications from Dutch advisory bodies (sitemap-scraped).

    Bodies: WRR, CPB, PBL, Rekenkamer, SCP, Gezondheidsraad, Onderwijsraad,
    Raad voor Cultuur, ROB. Each saves JSONL records to:
      raw_dir/{body}/{body}_records.jsonl
    """
    acq_date = datetime.date.today().isoformat()

    for body, (default_license, domain, country) in _ADVISORY_META.items():
        body_dir = raw_dir / body
        if not body_dir.exists():
            continue

        jsonl_file = body_dir / f"{body}_records.jsonl"
        if not jsonl_file.exists():
            continue

        count = 0
        for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = rec.get("text", "").strip()
            if len(text) < 100:
                continue

            source_url = rec.get("source_url", "")
            lic = rec.get("license", default_license)
            rec_acq = rec.get("acquisition_date") or acq_date

            year: int | None = None
            year_val = rec.get("year")
            if year_val is not None:
                try:
                    year = int(year_val)
                except (TypeError, ValueError):
                    pass

            doc_id = re.sub(r'[/:\\?*|"<>\s]', '_', source_url)[-60:] or str(count)
            source_name = f"advisory_bodies:{body}"
            meta = DocumentMeta(
                id=make_id(source_name, doc_id),
                source=source_name,
                source_url=source_url,
                license=lic,
                publication_year=year,
                country=country,
                domain=domain,
                language_variant="NL",
                acquisition_date=rec_acq,
            )
            yield Document(text=text, meta=meta)
            count += 1

        logger.info("[advisory_bodies:%s] %d documents", body, count)


# ---------------------------------------------------------------------------
# OSCAR Dutch (Common Crawl subset, CC0)
# ---------------------------------------------------------------------------

def parse_oscar_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse OSCAR Dutch JSONL shards (oscar_nl_shard_NNNNNN.jsonl).

    Each line: {"text": "...", "meta": {"url": "...", ...}}
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for shard_file in sorted(raw_dir.glob("oscar_nl_shard_*.jsonl")):
        logger.info("[oscar] Parsing %s", shard_file.name)
        try:
            lines = shard_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("[oscar] Cannot read %s: %s", shard_file, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = record.get("text", "").strip()
            if len(text) < 100:
                continue

            meta_dict = record.get("meta", {})
            source_url = meta_dict.get("url", meta_dict.get("warc_headers", {}).get("warc-target-uri", ""))
            year: int | None = None
            if source_url:
                m = re.search(r'\b(20[012]\d)\b', source_url)
                if m:
                    year = int(m.group(1))

            meta = DocumentMeta(
                id=make_id("oscar", str(doc_num)),
                source="oscar",
                source_url=source_url or "https://huggingface.co/datasets/oscar-corpus/OSCAR-2301",
                license="CC0",
                publication_year=year,
                country="NL",
                domain="web",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[oscar] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# HPLT Dutch (Internet Archive corpus, CC0)
# ---------------------------------------------------------------------------

def parse_hplt_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse HPLT Dutch JSONL shards (hplt_nl_NNNN.jsonl or hplt_nl_NNNN.jsonl.zst).

    Record format: {"id": str, "text": str, "url": str, "document_lang": str, ...}
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    shard_files = sorted(raw_dir.glob("hplt_nl_*.jsonl"))
    zst_files = sorted(raw_dir.glob("hplt_nl_*.jsonl.zst"))

    def _iter_lines(path: Path) -> Iterator[str]:
        if path.suffix == ".zst":
            try:
                import zstandard as zstd
                dctx = zstd.ZstdDecompressor()
                raw = path.read_bytes()
                text = dctx.decompress(raw, max_output_size=10 * 1024 ** 3).decode("utf-8", errors="replace")
            except ImportError:
                logger.warning("[hplt] zstandard not installed; skipping %s", path)
                return
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        yield from text.splitlines()

    for shard_file in shard_files + zst_files:
        logger.info("[hplt] Parsing %s", shard_file.name)
        try:
            lines = list(_iter_lines(shard_file))
        except OSError as exc:
            logger.warning("[hplt] Cannot read %s: %s", shard_file, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = record.get("text", "").strip()
            if len(text) < 100:
                continue

            source_url = record.get("url", "")
            year: int | None = None
            if source_url:
                m = re.search(r'\b(20[012]\d)\b', source_url)
                if m:
                    year = int(m.group(1))

            meta = DocumentMeta(
                id=make_id("hplt", str(doc_num)),
                source="hplt",
                source_url=source_url or "https://hplt-project.org",
                license="CC0",
                publication_year=year,
                country="NL",
                domain="web",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[hplt] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# Rijksmuseum collection metadata (CC0)
# ---------------------------------------------------------------------------

def _parse_rijksmuseum_nt(nt_file: Path, acq_date: str) -> Iterator[Document]:
    """
    Parse a Rijksmuseum N-Triples dump and yield Documents.

    Streams line-by-line to avoid loading the full file into memory (files are
    400-980 MB). Groups Dutch-language literals by subject URI, then yields
    one Document per subject that has meaningful Dutch text.
    """
    import re as _re

    # Match: <subject> <predicate> "value"@lang .  (lang tag optional)
    triple_re = _re.compile(
        r'^<([^>]+)>\s+<([^>]+)>\s+"((?:[^"\\]|\\.)*)"\s*(?:@([a-z]{2}(?:-[A-Za-z0-9]+)?))?\s*\.\s*$'
    )

    _TEXT_PREDICATES = {
        "http://schema.org/name",
        "http://schema.org/description",
        "http://schema.org/abstract",          # main Dutch text in library works
        "http://schema.org/creditText",
        "http://schema.org/alternativeName",
        "http://www.w3.org/2000/01/rdf-schema#label",
        "http://www.w3.org/2004/02/skos/core#prefLabel",
        "http://www.w3.org/2004/02/skos/core#altLabel",
        "http://purl.org/dc/elements/1.1/title",
        "http://purl.org/dc/elements/1.1/description",
        "http://purl.org/dc/terms/title",
        "http://purl.org/dc/terms/description",
    }
    _DATE_PREDICATES = {
        "http://schema.org/dateCreated",
        "http://purl.org/dc/elements/1.1/date",
        "http://purl.org/dc/terms/date",
        "http://purl.org/dc/terms/created",
    }

    # subject → {predicate: [values]}  — only Dutch text, kept in memory
    subjects: dict[str, dict[str, list[str]]] = {}

    try:
        fh = nt_file.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("[rijksmuseum] Cannot open %s: %s", nt_file, exc)
        return

    with fh:
        for raw_line in fh:
            m = triple_re.match(raw_line.strip())
            if not m:
                continue
            subj, pred, value, lang = m.group(1), m.group(2), m.group(3), m.group(4)

            if pred in _TEXT_PREDICATES and lang in ("nl", "nl-NL", "nl-nl"):
                value = value.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
                subjects.setdefault(subj, {}).setdefault(pred, []).append(value)
            elif pred in _DATE_PREDICATES:
                subjects.setdefault(subj, {}).setdefault("__date__", []).append(value)

    for subj, preds in subjects.items():
        parts: list[str] = []

        # Title/name first
        for pred in (
            "http://schema.org/name",
            "http://purl.org/dc/elements/1.1/title",
            "http://purl.org/dc/terms/title",
            "http://www.w3.org/2000/01/rdf-schema#label",
            "http://www.w3.org/2004/02/skos/core#prefLabel",
        ):
            vals = preds.get(pred, [])
            if vals:
                parts.append(vals[0])
                break

        # Abstract / description
        for pred in (
            "http://schema.org/abstract",
            "http://schema.org/description",
            "http://purl.org/dc/elements/1.1/description",
            "http://purl.org/dc/terms/description",
            "http://schema.org/creditText",
            "http://www.w3.org/2004/02/skos/core#altLabel",
            "http://schema.org/alternativeName",
        ):
            for val in preds.get(pred, []):
                parts.append(val)

        text = "\n\n".join(p.strip() for p in parts if p.strip())
        if len(text) < 20:
            continue

        year = None
        for date_val in preds.get("__date__", []):
            ym = _re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", date_val)
            if ym:
                year = int(ym.group(1))
                break

        meta = DocumentMeta(
            id=make_id("rijksmuseum", subj.split("/")[-1]),
            source="rijksmuseum",
            source_url=subj,
            license="CC0",
            publication_year=year,
            country="NL",
            domain="cultural_heritage",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


def parse_rijksmuseum_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Rijksmuseum collection metadata files.

    Handles two formats:
      rijksmuseum_page_*.json  — old REST API format (artObjects array)
      rijksmuseum_{name}.nt    — bulk N-Triples dump (from tar.gz archives)
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    # Old API format: rijksmuseum_page_*.json
    for page_file in sorted(raw_dir.glob("rijksmuseum_page_*.json")):
        try:
            data = json.loads(page_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[rijksmuseum] Cannot parse %s: %s", page_file, exc)
            continue

        for obj in data.get("artObjects", []):
            parts: list[str] = []

            title = obj.get("title") or obj.get("longTitle") or ""
            if title:
                parts.append(title)
            subtitle = obj.get("subTitle") or ""
            if subtitle:
                parts.append(subtitle)
            plaque = obj.get("plaqueDescriptionDutch") or ""
            if plaque:
                parts.append(plaque)
            for label in obj.get("labels", []):
                label_text = label.get("text") or ""
                if label_text:
                    parts.append(label_text)

            text = "\n\n".join(p.strip() for p in parts if p.strip())
            if len(text) < 20:
                continue

            obj_num = obj.get("objectNumber", str(doc_num))
            source_url = obj.get("links", {}).get("web", "") or f"https://www.rijksmuseum.nl/nl/collectie/{obj_num}"

            meta = DocumentMeta(
                id=make_id("rijksmuseum", obj_num),
                source="rijksmuseum",
                source_url=source_url,
                license="CC0",
                publication_year=None,
                country="NL",
                domain="cultural_heritage",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    # Bulk N-Triples dump format: rijksmuseum_*.nt
    for nt_file in sorted(raw_dir.glob("rijksmuseum_*.nt")):
        logger.info("[rijksmuseum] Parsing N-Triples: %s", nt_file.name)
        for doc in _parse_rijksmuseum_nt(nt_file, acq_date):
            yield doc
            doc_num += 1

    logger.info("[rijksmuseum] Parsed %d objects", doc_num)


# ---------------------------------------------------------------------------
# OpenAlex Dutch scientific papers (CC0)
# ---------------------------------------------------------------------------

def parse_openalex_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse OpenAlex Dutch scientific paper metadata (CC0).

    Reads openalex_page_NNNNNN.jsonl files — one JSON object per line.
    Each object has: {id, title, abstract, publication_year, doi}.
    Combines title + abstract into document text.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for jsonl_file in sorted(raw_dir.glob("openalex_page_*.jsonl")):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[openalex] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            title    = (obj.get("title")    or "").strip()
            abstract = (obj.get("abstract") or "").strip()
            text = "\n\n".join(p for p in (title, abstract) if p)
            if len(text) < 100:
                continue

            year = obj.get("publication_year")
            pub_year: int | None = int(year) if year else None
            doi = obj.get("doi") or ""
            source_url = doi if doi else obj.get("id", "")

            meta = DocumentMeta(
                id=make_id("openalex", obj.get("id", str(doc_num))),
                source="openalex",
                source_url=source_url,
                license="CC0",
                publication_year=pub_year,
                country="NL",
                domain="academic",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[openalex] Parsed %d works", doc_num)


# ---------------------------------------------------------------------------
# Wikidata Dutch entity descriptions (CC0)
# ---------------------------------------------------------------------------

def parse_wikidata_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Wikidata Dutch entity labels and descriptions (CC0).

    Reads wikidata_nl_NNNNNN.jsonl files. Batches entities into Documents
    of ~200 entities each (one label+description per line within the doc).
    This preserves individual entity granularity while keeping doc size
    reasonable for downstream dedup.
    """
    acq_date = datetime.date.today().isoformat()
    _ENTITIES_PER_DOC = 200
    doc_num = 0

    for jsonl_file in sorted(raw_dir.glob("wikidata_nl_*.jsonl")):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[wikidata_nl] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        batch: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            label = (obj.get("label") or "").strip()
            desc  = (obj.get("description") or "").strip()
            if not label or not desc:
                continue

            batch.append(f"{label}: {desc}")

            if len(batch) >= _ENTITIES_PER_DOC:
                text = "\n".join(batch)
                yield Document(
                    text=text,
                    meta=DocumentMeta(
                        id=make_id("wikidata_nl", str(doc_num)),
                        source="wikidata_nl",
                        source_url="https://www.wikidata.org",
                        license="CC0",
                        publication_year=None,
                        country="NL",
                        domain="encyclopedic",
                        language_variant="NL",
                        acquisition_date=acq_date,
                    ),
                )
                doc_num += 1
                batch = []

        # Flush remainder
        if batch:
            text = "\n".join(batch)
            if len(text) >= 100:
                yield Document(
                    text=text,
                    meta=DocumentMeta(
                        id=make_id("wikidata_nl", str(doc_num)),
                        source="wikidata_nl",
                        source_url="https://www.wikidata.org",
                        license="CC0",
                        publication_year=None,
                        country="NL",
                        domain="encyclopedic",
                        language_variant="NL",
                        acquisition_date=acq_date,
                    ),
                )
                doc_num += 1

    logger.info("[wikidata_nl] Parsed %d entity-batch documents", doc_num)


# ---------------------------------------------------------------------------
# FineWeb-2 Dutch — high-quality filtered web text (ODC-By)
# ---------------------------------------------------------------------------

def parse_fineweb2_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse FineWeb-2 Dutch shards (ODC-By).

    Reads fineweb2_nl_shard_NNNNNN.jsonl files.
    Each line: {text, url, date, id}.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for jsonl_file in sorted(raw_dir.glob("fineweb2_nl_shard_*.jsonl")):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[fineweb2_nl] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = (obj.get("text") or "").strip()
            if len(text) < 100:
                continue

            url = obj.get("url", "")
            date_str = obj.get("date", "")
            pub_year: int | None = None
            if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                pub_year = int(date_str[:4])

            meta = DocumentMeta(
                id=make_id("fineweb2_nl", obj.get("id", str(doc_num))),
                source="fineweb2_nl",
                source_url=url,
                license="ODC-By",
                publication_year=pub_year,
                country="NL",
                domain="web",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[fineweb2_nl] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# C5 Dutch — CC-licensed CommonCrawl pages (per-document CC license)
# ---------------------------------------------------------------------------

def parse_c5_dutch_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse C5 CC-licensed web pages (Dutch, Afrikaans, Frisian).

    Reads c5_{lang}_shard_NNNNNN.jsonl files (nl/af/fy).
    Also handles legacy c5_dutch_shard_NNNNNN.jsonl (old nl-only format).
    Each line: {text, url, license, date, lang?}.
    The per-document CC license is preserved in Document.meta.license.
    """
    # lang config → (language_variant, country)
    _LANG_META = {
        "nl": ("NL", "NL"),
        "af": ("AF", "ZA"),
        "fy": ("FY", "NL"),
    }

    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    # Match both new per-lang files and legacy c5_dutch_shard_* files
    files = sorted(
        list(raw_dir.glob("c5_nld_shard_*.jsonl"))
        + list(raw_dir.glob("c5_afr_shard_*.jsonl"))
        + list(raw_dir.glob("c5_fry_shard_*.jsonl"))
        + list(raw_dir.glob("c5_nl_shard_*.jsonl"))    # legacy config name
        + list(raw_dir.glob("c5_af_shard_*.jsonl"))    # legacy config name
        + list(raw_dir.glob("c5_fy_shard_*.jsonl"))    # legacy config name
        + list(raw_dir.glob("c5_dutch_shard_*.jsonl"))  # legacy nl-only format
    )

    for jsonl_file in files:
        # Detect language from filename
        name = jsonl_file.name
        if name.startswith("c5_afr_") or name.startswith("c5_af_"):
            lang_variant, country = "AF", "ZA"
        elif name.startswith("c5_fry_") or name.startswith("c5_fy_"):
            lang_variant, country = "FY", "NL"
        else:
            lang_variant, country = "NL", "NL"  # nld/nl and legacy dutch

        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[c5_dutch] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = (obj.get("text") or "").strip()
            if len(text) < 100:
                continue

            # Allow per-record lang override (from new format's `lang` field)
            rec_lang = obj.get("lang", "")
            if rec_lang in _LANG_META:
                lang_variant, country = _LANG_META[rec_lang]

            url = obj.get("url", "")
            # Raw shards written before 2026-10-10 carry a defaulted "CC-BY" that was
            # never read from C5. Trust only records that also store C5's own
            # `license_abbr`; never default a missing license.
            license_ = obj.get("license")
            if not obj.get("license_abbr") or license_ not in ("CC-BY", "CC-BY-SA", "CC0", "PD"):
                continue
            date_str = obj.get("date", "")
            pub_year: int | None = None
            if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                pub_year = int(date_str[:4])

            meta = DocumentMeta(
                id=make_id("c5_dutch", str(doc_num)),
                source="c5_dutch",
                source_url=url,
                license=license_,
                publication_year=pub_year,
                country=country,
                domain="web",
                language_variant=lang_variant,
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[c5_dutch] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# PleIAs Dutch-PD — largest open Dutch corpus (18.6B words, PD)
# ---------------------------------------------------------------------------

def parse_dutch_pd_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse PleIAs/common_corpus Dutch subset (Public Domain).

    Reads dutch_pd_records.jsonl (Parquet-based acquirer) or dutch_pd_shard_*.jsonl (legacy).
    Each line: {text, title, date, source_dataset, url, license, acquisition_date}.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    # Prefer the single records file; fall back to legacy shards
    candidates = [raw_dir / "dutch_pd_records.jsonl"] + sorted(raw_dir.glob("dutch_pd_shard_*.jsonl"))
    jsonl_files = [f for f in candidates if f.exists()]

    for jsonl_file in jsonl_files:
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[dutch_pd] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = (obj.get("text") or "").strip()
            if len(text) < 200:
                continue

            url = obj.get("url", "")
            date_str = obj.get("date", "")
            pub_year: int | None = None
            if date_str:
                for part in date_str.split("-"):
                    if part.isdigit() and 1000 <= int(part) <= 2026:
                        pub_year = int(part)
                        break

            # Infer domain from source_dataset field
            src = (obj.get("source_dataset") or "").lower()
            if any(k in src for k in ("krant", "nieuws", "newspaper", "periodical")):
                domain = "news"
            elif any(k in src for k in ("book", "boek", "literatuur", "gutenberg", "dbnl")):
                domain = "literature"
            else:
                domain = "literature"

            meta = DocumentMeta(
                id=make_id("dutch_pd", str(doc_num)),
                source="dutch_pd",
                source_url=url,
                license="PD",
                publication_year=pub_year,
                country="NL",
                domain=domain,
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[dutch_pd] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# EU Science Commons — Pclanglais/EU-Science-Commons (CC-BY, EU science)
# ---------------------------------------------------------------------------

def parse_eu_science_commons_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse EU-Science-Commons shards.

    Reads eu_science_commons_shard_NNNNNN.jsonl files.
    Each line: {text, title, url, year, source_name, license, language}.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    candidates = [raw_dir / "eu_science_commons_records.jsonl"] + sorted(raw_dir.glob("eu_science_commons_shard_*.jsonl"))
    jsonl_files = [f for f in candidates if f.exists()]

    for jsonl_file in jsonl_files:
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[eu_science_commons] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = (obj.get("text") or "").strip()
            if len(text) < 100:
                continue

            url = obj.get("url", "")
            year_str = str(obj.get("year", ""))
            pub_year: int | None = None
            if year_str.isdigit() and 1900 <= int(year_str) <= 2030:
                pub_year = int(year_str)

            license_ = obj.get("license", "CC-BY")

            meta = DocumentMeta(
                id=make_id("eu_science_commons", str(doc_num)),
                source="eu_science_commons",
                source_url=url,
                license=license_,
                publication_year=pub_year,
                country="EU",
                domain="scientific",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[eu_science_commons] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# mC4-nl-cleaned — large ODC-By Dutch web corpus (~23B words)
# ---------------------------------------------------------------------------

def parse_mc4_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse mC4-nl-cleaned shards (ODC-By).

    Reads mc4_nl_shard_NNNNNN.jsonl files.
    Each line: {text, url, timestamp}.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for jsonl_file in sorted(raw_dir.glob("mc4_nl_shard_*.jsonl")):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[mc4_nl] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = (obj.get("text") or "").strip()
            if len(text) < 100:
                continue

            url = obj.get("url", "")
            ts = obj.get("timestamp", "")
            pub_year: int | None = None
            if ts and len(ts) >= 4 and ts[:4].isdigit():
                pub_year = int(ts[:4])

            meta = DocumentMeta(
                id=make_id("mc4_nl", str(doc_num)),
                source="mc4_nl",
                source_url=url,
                license="ODC-By",
                publication_year=pub_year,
                country="NL",
                domain="web",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[mc4_nl] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# RCE — Rijksdienst voor het Cultureel Erfgoed (CC BY 4.0)
# ---------------------------------------------------------------------------

def parse_rce_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse RCE cultural heritage monument descriptions.

    Reads rce_batch_NNNNNN.jsonl files.
    Each line: {id, naam, omschrijving, acquisition_date}.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for jsonl_file in sorted(raw_dir.rglob("rce_batch_*.jsonl")):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[rce] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            naam       = (obj.get("naam") or "").strip()
            omschrijving = (obj.get("omschrijving") or "").strip()

            if not omschrijving:
                continue

            text = f"{naam}\n\n{omschrijving}" if naam else omschrijving
            if len(text) < 30:
                continue

            monument_id = obj.get("id", "")
            source_url = monument_id if monument_id.startswith("http") else ""

            meta = DocumentMeta(
                id=make_id("rce", monument_id or str(doc_num)),
                source="rce",
                source_url=source_url,
                license="CC-BY",
                publication_year=None,
                country="NL",
                domain="cultural_heritage",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[rce] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# RIVM — Dutch public health / environment publications (PD Art. 11 Aw)
# ---------------------------------------------------------------------------

def parse_rivm_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse RIVM scientific publication records harvested via OAI-PMH.

    Reads rivm_shard_NNNNNN.jsonl files.
    Each line: {title, description, language, date, identifier, pub_year}.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for jsonl_file in sorted(raw_dir.glob("rivm_shard_*.jsonl")):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[rivm] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            title       = (obj.get("title") or "").strip()
            description = (obj.get("description") or "").strip()

            parts = [p for p in [title, description] if p]
            if not parts:
                continue
            text = "\n\n".join(parts)
            if len(text) < 50:
                continue

            pub_year = obj.get("pub_year")
            identifier = obj.get("identifier", "")
            source_url = identifier if identifier.startswith("http") else ""

            meta = DocumentMeta(
                id=make_id("rivm", identifier or str(doc_num)),
                source="rivm",
                source_url=source_url,
                license="PD",
                publication_year=int(pub_year) if pub_year else None,
                country="NL",
                domain="science",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[rivm] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# Grondwettelijk Hof — Belgian Constitutional Court (PD, Art. XI.172 WER)
# ---------------------------------------------------------------------------

def parse_grondwettelijk_hof_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Belgian Constitutional Court decisions (Grondwettelijk Hof).

    Looks for ghcc_*.pdf files (production) and ghcc_*.txt files (test
    fixtures / pre-extracted text).  For each stem, the .txt sidecar takes
    priority; if absent, pdfplumber is used to extract text from the .pdf.
    Documents shorter than 100 characters are skipped.

    Filename convention: ghcc_{year}_{num:03d}.pdf  (e.g. ghcc_2022_001.pdf)
    Source URL:          https://nl.const-court.be/public/n/{year}/{year}-{num:03d}n.pdf
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    # Collect all stems present in the directory (from .pdf and/or .txt files)
    stems: dict[str, Path] = {}  # stem -> preferred path (.txt preferred over .pdf)
    for txt_file in sorted(raw_dir.glob("ghcc_*.txt")):
        stems[txt_file.stem] = txt_file
    for pdf_file in sorted(raw_dir.glob("ghcc_*.pdf")):
        if pdf_file.stem not in stems:
            stems[pdf_file.stem] = pdf_file

    for stem in sorted(stems):
        file_path = stems[stem]
        parts = stem.split("_")  # ghcc, YYYY, NNN
        if len(parts) != 3:
            logger.warning("[grondwettelijk_hof] Unexpected filename: %s", stem)
            continue

        try:
            year = int(parts[1])
            num  = int(parts[2])
        except ValueError:
            logger.warning("[grondwettelijk_hof] Cannot parse year/num from: %s", stem)
            continue

        text: str | None = None

        if file_path.suffix == ".txt":
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning("[grondwettelijk_hof] Cannot read %s: %s", file_path.name, exc)
                continue
        else:
            # PDF — try pdfplumber, fall back gracefully
            try:
                import pdfplumber as _pdfplumber
                with _pdfplumber.open(file_path) as pdf:
                    text = "\n\n".join(page.extract_text() or "" for page in pdf.pages)
            except ImportError:
                logger.warning(
                    "[grondwettelijk_hof] pdfplumber not installed; skipping %s",
                    file_path.name,
                )
                continue
            except Exception as exc:
                logger.warning(
                    "[grondwettelijk_hof] Cannot extract text from %s: %s",
                    file_path.name, exc,
                )
                continue

        if text is None or len(text) < 100:
            continue

        source_url = (
            f"https://nl.const-court.be/public/n/{year}/{year}-{num:03d}n.pdf"
        )

        meta = DocumentMeta(
            id=make_id("grondwettelijk_hof", f"{year}_{num:03d}"),
            source="grondwettelijk_hof",
            source_url=source_url,
            license="PD",
            publication_year=year,
            country="BE",
            domain="legal",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_num += 1

    logger.info("[grondwettelijk_hof] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# OpenAIRE NL — Dutch open-access academic publications (CC-BY)
# ---------------------------------------------------------------------------

def _normalize_oa_rights(rights: str) -> str | None:
    """
    Normalize an OpenAIRE rights string into a canonical license token.

    Returns None to signal that the document should be skipped (e.g. NC).
    """
    r = rights.upper()
    if "BY-NC" in r:
        return None          # Non-commercial — skip
    if "BY-SA" in r:
        return "CC-BY-SA"
    if "BY" in r:
        return "CC-BY"
    # Fallback: treat as CC-BY
    return "CC-BY"


def parse_openaire_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse OpenAIRE Dutch OA publication records harvested via OAI-PMH.

    Reads openaire_nl_shard_NNNNNN.jsonl files.
    Each line: {title, abstract, language, date, identifier, pub_year, rights}.
    Text = title + "\\n\\n" + abstract (or just title if no abstract).
    Skips records shorter than 80 characters or with NC license.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for jsonl_file in sorted(raw_dir.glob("openaire_nl_shard_*.jsonl")):
        try:
            lines = jsonl_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[openaire_nl] Cannot read %s: %s", jsonl_file.name, exc)
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            title    = (obj.get("title")    or "").strip()
            abstract = (obj.get("abstract") or "").strip()

            parts = [p for p in [title, abstract] if p]
            if not parts:
                continue
            text = "\n\n".join(parts)
            if len(text) < 80:
                continue

            raw_rights = obj.get("rights", "CC-BY")
            license_ = _normalize_oa_rights(raw_rights)
            if license_ is None:
                continue   # Skip non-commercial records

            pub_year_raw = obj.get("pub_year")
            pub_year: int | None = int(pub_year_raw) if pub_year_raw else None

            identifier = str(obj.get("identifier", "") or "")
            source_url = identifier if identifier.startswith("http") else ""

            meta = DocumentMeta(
                id=make_id("openaire_nl", identifier or str(doc_num)),
                source="openaire_nl",
                source_url=source_url,
                license=license_,
                publication_year=pub_year,
                country="NL",
                domain="science",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_num += 1

    logger.info("[openaire_nl] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# De Kamer — Belgian Chamber of Representatives plenary transcripts (PD)
# ---------------------------------------------------------------------------

# Legislature number → (start_year, end_year) for approximate publication year
_DEKAMER_LEGISLATURES = {
    49: (1995, 1999),
    50: (1999, 2003),
    51: (2003, 2007),
    52: (2007, 2010),
    53: (2010, 2014),
    54: (2014, 2019),
    55: (2019, 2024),
    56: (2024, 2030),
}

# Dutch function words with low overlap with French for language detection.
# Excluded: "de" (French preposition), "en" (French "and"), "in" (French),
#           "is" (French "is"), "al" (French "al"), "aan" (similar to French).
# Kept: words that are rare or absent in French.
_NL_FUNCTION_WORDS = frozenset(
    "het een van op dat niet te zijn voor met als er "
    "door om nog wel ook uit bij maar naar wordt werd "
    "heeft hebben worden hij zij wij zij hun".split()
)


def _dutch_ratio(text: str) -> float:
    """
    Return the ratio of Dutch function-word tokens to total tokens.

    A ratio > 0.08 is taken as sufficient evidence of Dutch text.
    Uses only words that are strongly Dutch (low overlap with French).
    """
    words = [w.strip(".,;:!?\"'()[]") for w in text.lower().split()]
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in _NL_FUNCTION_WORDS)
    return hits / len(words)


def _extract_dekamer_paragraphs(html_bytes: bytes) -> list[str]:
    """
    Extract paragraph text from a De Kamer HTML transcript.

    Uses stdlib html.parser to avoid a BeautifulSoup dependency.
    Returns a list of non-empty paragraph strings.
    """
    import html as _html_mod
    import html.parser as _html_parser

    paragraphs: list[str] = []
    current: list[str] = []
    in_p = False

    class _ParagraphExtractor(_html_parser.HTMLParser):
        def handle_starttag(self, tag, attrs):
            nonlocal in_p
            if tag == "p":
                in_p = True
                current.clear()

        def handle_endtag(self, tag):
            nonlocal in_p
            if tag == "p" and in_p:
                in_p = False
                text = " ".join(current).strip()
                text = _html_mod.unescape(text)
                # Collapse multiple spaces
                text = " ".join(text.split())
                if text:
                    paragraphs.append(text)
                current.clear()

        def handle_data(self, data):
            if in_p:
                current.append(data)

        def handle_entityref(self, name):
            if in_p:
                current.append(_html_mod.unescape(f"&{name};"))

        def handle_charref(self, name):
            if in_p:
                if name.startswith("x"):
                    current.append(chr(int(name[1:], 16)))
                else:
                    current.append(chr(int(name)))

    try:
        html_text = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html_text = html_bytes.decode("latin-1", errors="replace")

    extractor = _ParagraphExtractor()
    try:
        extractor.feed(html_text)
    except Exception as exc:
        logger.debug("[dekamer] HTML parse warning: %s", exc)

    return paragraphs


def parse_dekamer_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse De Kamer plenary session HTML transcripts (Public Domain).

    Reads dekamer_leg{leg}_ip{num}.html files.
    Extracts paragraphs and keeps only those with sufficient Dutch content
    (function-word ratio > 0.05).
    Skips documents where the joined Dutch text is shorter than 200 chars.

    DocumentMeta: source="dekamer", license="PD", country="BE",
                  domain="parliamentary", language_variant="NL-BE"
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    _leg_re = re.compile(r"dekamer_leg(\d+)_ip(\d+)\.html$")

    for html_file in sorted(raw_dir.glob("dekamer_leg*.html")):
        m = _leg_re.match(html_file.name)
        if not m:
            logger.warning("[dekamer] Unexpected filename: %s", html_file.name)
            continue

        leg = int(m.group(1))
        num = int(m.group(2))

        try:
            raw_bytes = html_file.read_bytes()
        except OSError as exc:
            logger.warning("[dekamer] Cannot read %s: %s", html_file.name, exc)
            continue

        paragraphs = _extract_dekamer_paragraphs(raw_bytes)

        # Keep only Dutch paragraphs
        dutch_paras = [p for p in paragraphs if _dutch_ratio(p) > 0.05]

        text = "\n".join(dutch_paras).strip()
        if len(text) < 200:
            continue

        # Approximate publication year from legislature start year
        leg_years = _DEKAMER_LEGISLATURES.get(leg)
        pub_year: int | None = leg_years[0] if leg_years else None

        source_url = (
            f"https://www.dekamer.be/doc/PCRI/html/{leg}/ip{num:03d}x.html"
        )

        meta = DocumentMeta(
            id=make_id("dekamer", f"leg{leg}_ip{num:03d}"),
            source="dekamer",
            source_url=source_url,
            license="PD",
            publication_year=pub_year,
            country="BE",
            domain="parliamentary",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_num += 1

    logger.info("[dekamer] Parsed %d documents", doc_num)


# ---------------------------------------------------------------------------
# Leipzig Corpora — Dutch dialect variants (CC-BY)
# ---------------------------------------------------------------------------

# Maps Leipzig corpus local_name → (language_variant, country)
_LEIPZIG_LANG_MAP: dict[str, tuple[str, str]] = {
    "vls":    ("VLS",    "BE"),
    "zea":    ("ZEA",    "NL"),
    "lim":    ("LI",     "NL"),
    "nds_nl": ("NDS-NL", "NL"),
    "afr":    ("AF",     "ZA"),
    "fy":     ("FY",     "NL"),
    "nl":     ("NL",     "NL"),
    "nl_be":  ("NL-BE",  "BE"),
}


def parse_leipzig_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Leipzig Corpora Dutch-family dialect sentences.

    Expects subdirs per corpus:
      vls/sentences.txt, zea/sentences.txt, lim/sentences.txt, etc.

    Leipzig sentence file format (tab-separated):
      <index>\t<sentence>
    or just:
      <sentence>

    Each sentence becomes one Document (short, suitable for dialect coverage).
    Groups of ≤100 consecutive sentences are batched into single documents
    to avoid excessive small documents.
    """
    acq_date  = datetime.date.today().isoformat()
    doc_count = 0
    BATCH_SIZE = 100

    for corpus_dir in sorted(raw_dir.iterdir()):
        if not corpus_dir.is_dir():
            continue

        sentences_file = corpus_dir / "sentences.txt"
        if not sentences_file.exists():
            logger.warning("[leipzig:%s] No sentences.txt found", corpus_dir.name)
            continue

        lang_variant, country = _LEIPZIG_LANG_MAP.get(corpus_dir.name.lower(), ("NL", "NL"))
        source_name  = f"leipzig_{corpus_dir.name.lower()}"

        try:
            lines = sentences_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("[leipzig:%s] Cannot read: %s", corpus_dir.name, exc)
            continue

        # Clean lines: strip index prefix if present
        sentences: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Leipzig format: "1\tsentence text" or plain text
            if "\t" in line:
                parts = line.split("\t", 1)
                if len(parts) == 2 and parts[0].isdigit():
                    line = parts[1].strip()
            if line:
                sentences.append(line)

        # Batch sentences into documents
        for batch_start in range(0, len(sentences), BATCH_SIZE):
            batch = sentences[batch_start: batch_start + BATCH_SIZE]
            text  = "\n".join(batch)
            if len(text) < 50:
                continue

            doc_id = make_id(source_name, f"{corpus_dir.name}_{batch_start:07d}")
            meta   = DocumentMeta(
                id=doc_id,
                source=source_name,
                source_url="https://wortschatz.uni-leipzig.de/en/download/",
                license="CC-BY",
                publication_year=None,
                country=country,
                domain="web",
                language_variant=lang_variant,
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

        logger.info("[leipzig:%s] Parsed %d sentences → %d documents",
                    corpus_dir.name, len(sentences), (len(sentences) + BATCH_SIZE - 1) // BATCH_SIZE)

    logger.info("[leipzig] Total documents: %d", doc_count)


# ---------------------------------------------------------------------------
# Coleccion Aruba — Papiamento texts from Biblioteca Nacional Aruba (Internet Archive)
# ---------------------------------------------------------------------------

def parse_coleccion_aruba_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Papiamento/Dutch texts from the Biblioteca Nacional Aruba collection.

    Expects:
      texts/{identifier}.txt    — OCR plain text
      texts/{identifier}.url    — source URL sidecar
      metadata.jsonl            — per-item metadata

    Language variant: PAP (Papiamento — Aruba -o spelling variant)
    License: CC0 / CC-BY / CC-BY-SA (per item, from metadata)
    """
    acq_date   = datetime.date.today().isoformat()
    doc_count  = 0
    texts_dir  = raw_dir / "texts"
    meta_path  = raw_dir / "metadata.jsonl"

    # Build identifier → metadata index
    meta_index: dict[str, dict] = {}
    if meta_path.exists():
        for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ident = rec.get("identifier", "")
                if ident:
                    meta_index[ident] = rec
            except json.JSONDecodeError:
                continue

    if not texts_dir.exists():
        logger.warning("[coleccion_aruba] No texts/ directory in %s", raw_dir)
        return

    def _ia_str(val: object, default: str = "") -> str:
        """Coerce an Internet Archive metadata value to a plain string."""
        if val is None:
            return default
        if isinstance(val, list):
            return " ".join(str(v) for v in val if v is not None)
        return str(val)

    for txt_file in sorted(texts_dir.glob("*.txt")):
        identifier = txt_file.stem

        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[coleccion_aruba] Cannot read %s: %s", txt_file.name, exc)
            continue

        if len(text) < 100:
            continue

        # Get metadata — IA metadata fields can be lists, null, or strings
        item_meta  = meta_index.get(identifier, {})

        license_raw = item_meta.get("license")
        license_    = _norm_license(_ia_str(license_raw, "CC-BY") or "CC-BY")
        title       = _ia_str(item_meta.get("title"), identifier) or identifier
        year_raw    = _ia_str(item_meta.get("year"), "")
        pub_year: int | None = None
        if year_raw:
            m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", year_raw)
            if m:
                pub_year = int(m.group(1))

        # Language detection: treat as PAP (Papiamento)
        lang_raw = item_meta.get("language", "")
        lang_key = " ".join(lang_raw) if isinstance(lang_raw, list) else str(lang_raw or "")
        lang_var = "NL" if "dutch" in lang_key.lower() or "nl" in lang_key.lower() else "PAP"

        source_url = _read_url_sidecar(txt_file) or f"https://archive.org/details/{identifier}"

        doc_id = make_id("coleccion_aruba", identifier)
        meta   = DocumentMeta(
            id=doc_id,
            source="coleccion_aruba",
            source_url=source_url,
            license=license_,
            publication_year=pub_year,
            country="AW",
            domain="encyclopedic",
            language_variant=lang_var,
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info("[coleccion_aruba] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# Landscourant Aruba — official gazette PDFs + Internet Archive texts
# ---------------------------------------------------------------------------

def parse_landscourant_aruba_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Landscourant van Aruba (official gazette).

    Expects:
      gobierno/{year}/*.pdf      — PDFs from gobierno.aw
      gobierno/{year}/*.url      — URL sidecars
      archive/*.txt              — OCR text from Internet Archive
      archive/*.json             — metadata sidecars

    Language variant: NL (Aruban Dutch — same standard Dutch in legal text)
    """
    from pipeline.parse.pdf_text import extract_text as extract_pdf

    acq_date  = datetime.date.today().isoformat()
    doc_count = 0

    # Parse gobierno.aw PDFs
    gobierno_dir = raw_dir / "gobierno"
    if gobierno_dir.exists():
        for year_dir in sorted(gobierno_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            year_str = year_dir.name
            pub_year: int | None = int(year_str) if year_str.isdigit() else None

            for pdf_file in sorted(year_dir.glob("*.pdf")):
                text = extract_pdf(pdf_file)
                if not text or len(text.strip()) < 100:
                    continue

                source_url = _read_url_sidecar(pdf_file) or str(pdf_file)
                doc_id = make_id("landscourant_aruba", pdf_file.stem)
                meta   = DocumentMeta(
                    id=doc_id,
                    source="landscourant_aruba",
                    source_url=source_url,
                    license="PD",
                    publication_year=pub_year,
                    country="AW",
                    domain="government",
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text.strip(), meta=meta)
                doc_count += 1

    # Parse Internet Archive OCR texts
    archive_dir = raw_dir / "archive"
    if archive_dir.exists():
        for txt_file in sorted(archive_dir.glob("*.txt")):
            try:
                text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if len(text) < 100:
                continue

            # Read sidecar metadata if present
            json_sidecar = txt_file.with_suffix(".json")
            pub_year = None
            if json_sidecar.exists():
                try:
                    meta_rec = json.loads(json_sidecar.read_text(encoding="utf-8"))
                    year_raw = meta_rec.get("year", "")
                    if year_raw:
                        m_yr = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(year_raw))
                        if m_yr:
                            pub_year = int(m_yr.group(1))
                except (json.JSONDecodeError, OSError):
                    pass

            source_url = _read_url_sidecar(txt_file) or f"https://archive.org/details/{txt_file.stem}"
            doc_id = make_id("landscourant_aruba", f"ia_{txt_file.stem}")
            meta   = DocumentMeta(
                id=doc_id,
                source="landscourant_aruba",
                source_url=source_url,
                license="PD",
                publication_year=pub_year,
                country="AW",
                domain="government",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

    logger.info("[landscourant_aruba] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# Senate BE — Belgian Senate Dutch proceedings
# ---------------------------------------------------------------------------

_SENATE_DUTCH_MARKERS = re.compile(
    r'\b(de\s+voorzitter|mevrouw|mijnheer|de\s+heer|de\s+senator|wij\s+vragen|'
    r'de\s+staatssecretaris|de\s+minister|stemming|vergadering)\b',
    re.IGNORECASE,
)


def _extract_senate_dutch_html(html: str) -> str:
    """
    Extract Dutch-language paragraphs from a Belgian Senate session HTML.

    The Senate publishes bilingual documents. Dutch sections are typically
    marked with language attributes or follow Dutch speaker introductions.
    We filter by Dutch vocabulary ratio.
    """
    import html as html_module

    # Strip scripts, styles, navigation
    html_clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html_clean = re.sub(r'<style[^>]*>.*?</style>', '', html_clean, flags=re.DOTALL | re.IGNORECASE)
    html_clean = re.sub(r'<nav[^>]*>.*?</nav>', '', html_clean, flags=re.DOTALL | re.IGNORECASE)
    html_clean = re.sub(r'<header[^>]*>.*?</header>', '', html_clean, flags=re.DOTALL | re.IGNORECASE)
    html_clean = re.sub(r'<footer[^>]*>.*?</footer>', '', html_clean, flags=re.DOTALL | re.IGNORECASE)

    # Extract text from <p> and <div> tags
    raw_paras: list[str] = re.findall(r'<(?:p|div)[^>]*>(.*?)</(?:p|div)>', html_clean,
                                       re.DOTALL | re.IGNORECASE)

    dutch_paras: list[str] = []
    for raw in raw_paras:
        # Strip inner tags
        text_p = re.sub(r'<[^>]+>', ' ', raw)
        text_p = html_module.unescape(text_p)
        text_p = re.sub(r'\s+', ' ', text_p).strip()

        if len(text_p) < 30:
            continue
        # Keep paragraphs with Dutch vocabulary markers
        if _dutch_ratio(text_p) > 0.05 or _SENATE_DUTCH_MARKERS.search(text_p):
            dutch_paras.append(text_p)

    return "\n".join(dutch_paras)


def parse_senate_be_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Belgian Senate Dutch-language plenary proceedings.

    Expects legislature subdirs: legislature_06/, legislature_07/, etc.
    Each containing session_*.html files + optional *.pdf files.

    Language variant: NL-BE (formal Belgian Dutch)
    """
    acq_date  = datetime.date.today().isoformat()
    doc_count = 0

    _LEG_YEARS = {6: 2003, 7: 2007, 8: 2010, 9: 2014, 10: 2019, 11: 2024}

    for leg_dir in sorted(raw_dir.iterdir()):
        if not leg_dir.is_dir():
            continue
        # Parse legislature number from directory name
        m = re.match(r'legislature_(\d+)', leg_dir.name)
        if not m:
            continue
        leg_num  = int(m.group(1))
        pub_year = _LEG_YEARS.get(leg_num)

        for html_file in sorted(leg_dir.glob("session_*.html")):
            try:
                html = html_file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning("[senate_be] Cannot read %s: %s", html_file.name, exc)
                continue

            text = _extract_senate_dutch_html(html)
            if len(text) < 200:
                continue

            source_url = _read_url_sidecar(html_file) or f"https://www.senate.be"
            session_id = html_file.stem.replace("session_", "")
            doc_id     = make_id("senate_be", f"leg{leg_num:02d}_{session_id}")

            meta = DocumentMeta(
                id=doc_id,
                source="senate_be",
                source_url=source_url,
                license="PD",
                publication_year=pub_year,
                country="BE",
                domain="parliamentary",
                language_variant="NL-BE",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

    logger.info("[senate_be] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# Publicatieblad Curaçao — official gazette (PDFs + IA historical)
# ---------------------------------------------------------------------------

def parse_publicatieblad_curacao_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Curaçao Publicatieblad (official gazette).

    Expects:
      gobiernu/{year}/*.pdf   — PDFs from gobiernu.cw
      gobiernu/{year}/*.url   — URL sidecars
      historical_na/*.txt     — OCR text from Internet Archive (Netherlands Antilles era)

    Language variant: NL (formal Dutch — Curaçao legal register)
    """
    from pipeline.parse.pdf_text import extract_text as extract_pdf

    acq_date  = datetime.date.today().isoformat()
    doc_count = 0

    # Parse gobiernu.cw PDFs
    gobiernu_dir = raw_dir / "gobiernu"
    if gobiernu_dir.exists():
        for year_dir in sorted(gobiernu_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            year_str = year_dir.name
            pub_year: int | None = int(year_str) if year_str.isdigit() else None

            for pdf_file in sorted(year_dir.glob("*.pdf")):
                text = extract_pdf(pdf_file)
                if not text or len(text.strip()) < 100:
                    continue

                source_url = _read_url_sidecar(pdf_file) or str(pdf_file)
                doc_id = make_id("publicatieblad_curacao", pdf_file.stem)
                meta   = DocumentMeta(
                    id=doc_id,
                    source="publicatieblad_curacao",
                    source_url=source_url,
                    license="PD",
                    publication_year=pub_year,
                    country="CW",
                    domain="government",
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text.strip(), meta=meta)
                doc_count += 1

    # Parse Internet Archive historical texts
    historical_dir = raw_dir / "historical_na"
    if historical_dir.exists():
        for txt_file in sorted(historical_dir.glob("*.txt")):
            try:
                text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if len(text) < 100:
                continue

            source_url = _read_url_sidecar(txt_file) or f"https://archive.org/details/{txt_file.stem}"
            doc_id = make_id("publicatieblad_curacao", f"na_{txt_file.stem}")
            meta   = DocumentMeta(
                id=doc_id,
                source="publicatieblad_curacao",
                source_url=source_url,
                license="PD",
                publication_year=None,
                country="CW",
                domain="government",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

    logger.info("[publicatieblad_curacao] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# VOC/WIC HTR — Early Modern Dutch handwritten text recognition transcriptions
# ---------------------------------------------------------------------------

def parse_voc_wic_htr_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse VOC/WIC HTR transcriptions from Zenodo.

    Handles multiple possible formats depending on what was downloaded:
    - Plain .txt files (one document per file)
    - .jsonl files (one JSON record per line: {id, text, ...})
    - Extracted ZIP contents (subdirectories with per-page .txt files)

    Source: Globalise project, Zenodo record 11209325, CC BY 4.0
    Language: Early Modern Dutch (NL, historical pre-1900)
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    def _yield_txt(path: Path, source_url: str) -> Iterator[Document]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return
        if len(text) < 100:
            return
        doc_id = make_id("voc_wic_htr", path.stem)
        meta = DocumentMeta(
            id=doc_id,
            source="voc_wic_htr",
            source_url=source_url,
            license="CC-BY",
            publication_year=None,  # 17th–19th century; file-level year unknown
            country="NL",
            domain="historical",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)

    def _yield_jsonl(path: Path) -> Iterator[Document]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = (rec.get("text") or rec.get("transcription") or "").strip()
            if len(text) < 100:
                continue
            rec_id = rec.get("id") or rec.get("page_id") or path.stem
            source_url = rec.get("url") or f"https://zenodo.org/records/11209325"
            doc_id = make_id("voc_wic_htr", str(rec_id))
            pub_year = rec.get("year") or rec.get("date_year")
            try:
                pub_year = int(pub_year) if pub_year else None
            except (ValueError, TypeError):
                pub_year = None
            meta = DocumentMeta(
                id=doc_id,
                source="voc_wic_htr",
                source_url=source_url,
                license="CC-BY",
                publication_year=pub_year,
                country="NL",
                domain="historical",
                language_variant="NL",
                acquisition_date=acq_date,
                historical_flag=True,
                language_period="early_modern",
                content_warnings=["colonial_terminology", "archaic_racial_language"],
            )
            yield Document(text=text, meta=meta)

    # Extract any ZIP archives first so rglob below picks up the contents
    import zipfile as _zipfile
    for zip_path in sorted(raw_dir.glob("*.zip")):
        extract_to = raw_dir / zip_path.stem
        if not extract_to.exists():
            logger.info("[voc_wic_htr] Extracting %s ...", zip_path.name)
            try:
                with _zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_to)
            except Exception as exc:
                logger.warning("[voc_wic_htr] Cannot extract %s: %s", zip_path.name, exc)

    # Walk the raw_dir for all supported file types
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        source_url = f"https://zenodo.org/records/11209325/files/{path.name}"

        if suffix == ".jsonl":
            for doc in _yield_jsonl(path):
                yield doc
                doc_count += 1
        elif suffix == ".txt":
            for doc in _yield_txt(path, source_url):
                yield doc
                doc_count += 1
        elif suffix == ".json":
            # Single JSON object (metadata + text)
            try:
                rec = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            text = (rec.get("text") or rec.get("transcription") or "").strip()
            if len(text) >= 100:
                doc_id = make_id("voc_wic_htr", path.stem)
                meta = DocumentMeta(
                    id=doc_id,
                    source="voc_wic_htr",
                    source_url=source_url,
                    license="CC-BY",
                    publication_year=rec.get("year"),
                    country="NL",
                    domain="historical",
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                doc_count += 1

    logger.info("[voc_wic_htr] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# ParlaMint-NL — Tweede Kamer parliamentary proceedings (TEI XML, CC BY 4.0)
# ---------------------------------------------------------------------------

# TEI namespaces used by ParlaMint
_PM_NS = {
    "tei":   "http://www.tei-c.org/ns/1.0",
    "xml":   "http://www.w3.org/XML/1998/namespace",
    "an":    "http://www.akomantoso.org/2.0",
    "pm":    "http://www.clarin.eu/parlamint",
}


def _parse_parlamint_tei(xml_path: Path, acq_date: str) -> Iterator[Document]:
    """
    Parse a single ParlaMint TEI XML file.

    Each <u> (utterance) element is one speech turn. We emit one Document
    per utterance (or per sitting, for very short sessions).
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        logger.warning("[parlamint_nl] XML error in %s: %s", xml_path.name, exc)
        return

    root = tree.getroot()

    # Extract session-level metadata from teiHeader
    header = root.find(".//tei:teiHeader", _PM_NS)
    session_date = None
    session_id = xml_path.stem

    if header is not None:
        date_el = header.find(".//tei:settingDesc//tei:date", _PM_NS)
        if date_el is None:
            date_el = header.find(".//tei:sourceDesc//tei:date", _PM_NS)
        if date_el is not None:
            date_str = date_el.get("when") or date_el.get("from") or ""
            if date_str and len(date_str) >= 4:
                try:
                    session_date = int(date_str[:4])
                except ValueError:
                    pass

    source_url = f"https://www.clarin.si/repository/xmlui/handle/11356/2047"

    # Collect all utterances
    utterances = root.findall(".//tei:u", _PM_NS)
    if not utterances:
        # Some files are metadata-only; skip
        return

    for utt_el in utterances:
        # Collect all <seg> text within the utterance
        segments = []
        for seg in utt_el.iter():
            if seg.text and seg.text.strip():
                segments.append(seg.text.strip())
            if seg.tail and seg.tail.strip():
                segments.append(seg.tail.strip())
        text = " ".join(segments).strip()
        if len(text) < 50:
            continue

        utt_id = utt_el.get("{http://www.w3.org/XML/1998/namespace}id") or utt_el.get("id") or ""
        doc_id = make_id("parlamint_nl", f"{session_id}_{utt_id}" if utt_id else session_id)
        meta = DocumentMeta(
            id=doc_id,
            source="parlamint_nl",
            source_url=source_url,
            license="CC-BY",
            publication_year=session_date,
            country="NL",
            domain="parliamentary",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


def parse_parlamint_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse ParlaMint-NL TEI XML files from raw_dir.

    Expects either:
    - raw_dir/tei/**/*.xml  (extracted archive)
    - raw_dir/**/*.xml      (flat or nested structure)

    Source: CLARIN.SI, CC BY 4.0, Tweede Kamer 2015-2022
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    # Prefer the tei/ subdir if present
    tei_dir = raw_dir / "tei"
    search_root = tei_dir if tei_dir.exists() else raw_dir

    xml_files = sorted(search_root.rglob("*.xml"))
    logger.info("[parlamint_nl] Found %d XML files in %s", len(xml_files), search_root)

    for xml_path in xml_files:
        # Skip TEI annotation layers (ana files) — focus on plain text
        if xml_path.name.endswith("-ana.xml"):
            continue
        for doc in _parse_parlamint_tei(xml_path, acq_date):
            yield doc
            doc_count += 1

    logger.info("[parlamint_nl] Parsed %d utterances", doc_count)


# ---------------------------------------------------------------------------
# Nationaal Archief — archival finding aids via OAI-PMH (CC0)
# ---------------------------------------------------------------------------

def parse_nationaal_archief_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Nationaal Archief OAI-PMH harvested finding aid metadata.

    Reads JSONL batch files written by the nationaal_archief acquirer.
    Each record: {id, title, description, date}

    Source: service.archief.nl OAI-PMH, CC0
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    batch_files = sorted(raw_dir.glob("nationaal_archief_batch_*.jsonl"))
    logger.info("[nationaal_archief] Found %d batch files", len(batch_files))

    for batch_path in batch_files:
        try:
            lines = batch_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            title       = (rec.get("title") or "").strip()
            description = (rec.get("description") or "").strip()
            rec_id      = (rec.get("id") or "").strip()
            date_str    = (rec.get("date") or "")

            # Build document text: title + description
            parts = []
            if title:
                parts.append(title)
            if description:
                parts.append(description)
            text = "\n\n".join(parts).strip()

            if len(text) < 50:
                continue

            pub_year = None
            if date_str:
                year_m = re.search(r'\b(1[5-9]\d{2}|20[012]\d)\b', date_str)
                if year_m:
                    try:
                        pub_year = int(year_m.group(1))
                    except ValueError:
                        pass

            source_url = rec_id if rec_id.startswith("http") else f"https://service.archief.nl/"
            doc_id = make_id("nationaal_archief", rec_id or f"{batch_path.stem}_{doc_count}")
            meta = DocumentMeta(
                id=doc_id,
                source="nationaal_archief",
                source_url=source_url,
                license="CC0",
                publication_year=pub_year,
                country="NL",
                domain="archival",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

    logger.info("[nationaal_archief] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# Etaamb BE — Belgian Official Gazette Dutch texts (PD)
# ---------------------------------------------------------------------------

def parse_etaamb_be_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Belgian Gazette texts harvested from etaamb.openjustice.be.

    Reads per-document JSON files stored in year subdirectories:
      raw_dir/{year}/{doc_id}.json
    Each JSON: {id, title, publication_year, text, url, acquisition_date}

    Source: etaamb.openjustice.be, Public Domain (Belgian Copyright Act Art. 8)
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    # Walk year dirs and flat files
    json_files = sorted(raw_dir.rglob("*.json"))
    logger.info("[etaamb_be] Found %d JSON files", len(json_files))

    for json_path in json_files:
        # Skip acquisition_log files
        if "acquisition_log" in json_path.name:
            continue
        try:
            rec = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue

        text = (rec.get("text") or "").strip()
        if len(text) < 200:
            continue

        title   = (rec.get("title") or "").strip()
        if title:
            text = f"{title}\n\n{text}"

        rec_id     = str(rec.get("id") or json_path.stem)
        source_url = rec.get("url") or f"https://etaamb.openjustice.be/nl/document/{rec_id}"
        pub_year   = rec.get("publication_year")
        try:
            pub_year = int(pub_year) if pub_year else None
        except (ValueError, TypeError):
            pub_year = None

        # Infer year from parent dir name if not in record
        if pub_year is None:
            try:
                pub_year = int(json_path.parent.name)
            except ValueError:
                pass

        doc_id = make_id("etaamb_be", rec_id)
        meta   = DocumentMeta(
            id=doc_id,
            source="etaamb_be",
            source_url=source_url,
            license="PD",
            publication_year=pub_year,
            country="BE",
            domain="government",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info("[etaamb_be] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# Staten-Generaal Digitaal — Dutch parliamentary proceedings 1814–1995 (CC0)
# ---------------------------------------------------------------------------

# KB DCX namespace used in SGD SRU responses
_SGD_NS = {
    "sru":    "http://docs.oasis-open.org/ns/search-ws/sruResponse",
    "srw":    "http://www.loc.gov/zing/srw/",          # legacy fallback
    "dc":     "http://purl.org/dc/elements/1.1/",
    "dcterms":"http://purl.org/dc/terms/",             # actual namespace used
    "dcx":    "http://krait.kb.nl/coop/tel/handbook/telterms.html",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "gzd":    "http://standaarden.overheid.nl/sru",
    "ovwet":  "http://standaarden.overheid.nl/wetgeving/",
}

_CHAMBER_MAP = {
    "tk": ("NL", 2020),   # Tweede Kamer
    "ek": ("NL", 2020),   # Eerste Kamer
    "vv": ("NL", 2020),   # Verenigde Vergadering
}


def _parse_sgd_batch_xml(xml_path: Path, acq_date: str) -> Iterator[Document]:
    """Parse one KB SGD SRU batch XML file, yielding Documents."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        logger.warning("[staten_generaal] XML error in %s: %s", xml_path.name, exc)
        return

    root = tree.getroot()

    # Support both sru: (new OASIS standard) and srw: (old LoC standard)
    rec_els = root.findall(".//sru:record", _SGD_NS)
    if not rec_els:
        rec_els = root.findall(".//srw:record", _SGD_NS)

    for rec_el in rec_els:
        data_el = rec_el.find("sru:recordData", _SGD_NS)
        if data_el is None:
            data_el = rec_el.find("srw:recordData", _SGD_NS)
        if data_el is None:
            continue

        # Records use dcterms: namespace (http://purl.org/dc/terms/)
        # Fall back to dc: (http://purl.org/dc/elements/1.1/) for older batches
        def _dc(tag: str) -> str:
            for ns_key in ("dcterms", "dc"):
                el = data_el.find(f".//{{{_SGD_NS[ns_key]}}}{tag}")
                if el is not None and el.text:
                    return el.text.strip()
            return ""

        def _dc_all(tag: str) -> list[str]:
            results = []
            for ns_key in ("dcterms", "dc"):
                results.extend(
                    (el.text or "").strip()
                    for el in data_el.findall(f".//{{{_SGD_NS[ns_key]}}}{tag}")
                    if el.text and el.text.strip()
                )
            return results

        identifier = _dc("identifier")
        title       = _dc("title")
        description = " ".join(_dc_all("description"))
        # dcterms uses "issued" or "modified" for date; dc uses "date"
        date_str    = _dc("issued") or _dc("date") or _dc("modified")

        # The full text is typically in dc:description; title supplements it
        parts = []
        if title:
            parts.append(title)
        if description and description != title:
            parts.append(description)
        text = "\n\n".join(parts).strip()

        if len(text) < 50:
            continue

        # Prefer vergaderjaar (actual parliamentary session year) over dcterms dates
        vergaderjaar = ""
        vj_el = data_el.find(f".//{{{_SGD_NS['ovwet']}}}vergaderjaar")
        if vj_el is not None and vj_el.text:
            vergaderjaar = vj_el.text.strip()

        pub_year = None
        # Extract year from vergaderjaar ("1937-1938" → 1937) first
        if vergaderjaar:
            year_m = re.search(r'\b(1[5-9]\d{2}|200\d|201\d|202\d)\b', vergaderjaar)
            if year_m:
                try:
                    pub_year = int(year_m.group(1))
                except ValueError:
                    pass
        # Fall back to dcterms date fields
        if pub_year is None and date_str:
            year_m = re.search(r'\b(1[89]\d{2}|200\d|201\d|202\d)\b', date_str)
            if not year_m:
                year_m = re.search(r'\b(1[5-9]\d{2})\b', date_str)
            if year_m:
                try:
                    pub_year = int(year_m.group(1))
                except ValueError:
                    pass

        source_url = identifier if identifier.startswith("http") else (
            f"https://resolver.kb.nl/resolve?urn={identifier}" if identifier else
            "https://www.kb.nl/bronnen-zoekwijzers/dataservices-en-apis/staten-generaal-digitaal"
        )
        doc_id = make_id("staten_generaal", identifier or xml_path.stem)
        meta = DocumentMeta(
            id=doc_id,
            source="staten_generaal",
            source_url=source_url,
            license="CC0",
            publication_year=pub_year,
            country="NL",
            domain="parliamentary",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


def parse_staten_generaal_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Staten-Generaal Digitaal parliamentary proceedings.

    Handles two formats:
      1. sgd_records.jsonl — written by the updated OAI-PMH acquirer (2026-03-24+)
         Each line: {identifier, title, date, year, text}
      2. Legacy XML batch files — old SRU-based acquirer (now dead)

    Source: KB services.kb.nl/mdo/oai set=sgd:document, CC0
    Coverage: Dutch parliamentary proceedings 1814–1995
    """
    acq_date  = datetime.date.today().isoformat()
    doc_count = 0

    # --- Format 1: JSONL (new OAI-PMH acquirer) ---
    jsonl_path = raw_dir / "sgd_records.jsonl"
    if jsonl_path.exists():
        logger.info("[staten_generaal] Parsing %s", jsonl_path.name)
        for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = rec.get("text", "").strip()
            if len(text) < 30:
                continue

            identifier = rec.get("identifier", "")
            year       = rec.get("year")
            src_url    = f"https://resolver.kb.nl/resolve?urn={urllib.parse.quote(identifier)}&role=xml" \
                         if identifier else "https://statengeneraaldigitaal.nl/"

            meta = DocumentMeta(
                id=make_id("staten_generaal", identifier or str(doc_count)),
                source="staten_generaal",
                source_url=src_url,
                license="CC0",
                publication_year=int(year) if year else None,
                country="NL",
                domain="parliamentary",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

        logger.info("[staten_generaal] Parsed %d documents", doc_count)
        return

    # --- Format 2: legacy XML batch files ---
    xml_files = sorted(raw_dir.rglob("*.xml"))
    logger.info("[staten_generaal] Found %d legacy XML batch files", len(xml_files))
    for xml_path in xml_files:
        for doc in _parse_sgd_batch_xml(xml_path, acq_date):
            yield doc
            doc_count += 1

    logger.info("[staten_generaal] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# VoxPopuli — European Parliament speech transcripts (CC0)
# ---------------------------------------------------------------------------

# Language variant by locale subdir name
_VOXPOPULI_LANG_MAP = {
    "nl": "NL",
    "fy": "FY",
}


def parse_voxpopuli_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse VoxPopuli Dutch/Frisian speech transcripts.

    Reads TSV files written by the voxpopuli acquirer:
      {locale}/{locale}_sentences_{shard:04d}.tsv
    Columns: utterance_id, normalized_text, raw_text, year, speaker_id

    Source: facebook/voxpopuli on HuggingFace, CC0
    Register: European Parliament plenary sessions 2009–2020
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    for locale_dir in sorted(raw_dir.iterdir()):
        if not locale_dir.is_dir():
            continue
        locale = locale_dir.name
        lang_variant = _VOXPOPULI_LANG_MAP.get(locale, "NL")

        tsv_files = sorted(locale_dir.glob(f"{locale}_sentences_*.tsv"))
        if not tsv_files:
            continue
        logger.info("[voxpopuli:%s] Found %d TSV shards", locale, len(tsv_files))

        for tsv_path in tsv_files:
            try:
                import csv as _csv
                with tsv_path.open(encoding="utf-8", newline="") as f:
                    reader = _csv.DictReader(f, delimiter="\t")
                    rows = list(reader)
            except OSError:
                continue

            # Group utterances into documents of ~50 utterances each (like tatoeba/mls)
            batch_size = 50
            for batch_start in range(0, len(rows), batch_size):
                batch = rows[batch_start: batch_start + batch_size]
                texts = []
                years = []
                for row in batch:
                    text = (row.get("normalized_text") or row.get("raw_text") or "").strip()
                    if text:
                        texts.append(text)
                    y = row.get("year", "")
                    if y and y.isdigit():
                        years.append(int(y))
                    else:
                        # Fall back to year embedded in utterance_id: "20100210-0900-..."
                        uid = row.get("utterance_id", "")
                        if uid and len(uid) >= 4 and uid[:4].isdigit():
                            years.append(int(uid[:4]))
                if not texts:
                    continue

                full_text = " ".join(texts)
                pub_year = min(years) if years else None
                first_uid = (batch[0].get("utterance_id") or "").strip() if batch else ""

                doc_id = make_id("voxpopuli", f"{locale}_{tsv_path.stem}_{batch_start}_{first_uid}")
                meta = DocumentMeta(
                    id=doc_id,
                    source="voxpopuli",
                    source_url="https://huggingface.co/datasets/facebook/voxpopuli",
                    license="CC0",
                    publication_year=pub_year,
                    country="NL",
                    domain="speech",
                    language_variant=lang_variant,
                    acquisition_date=acq_date,
                )
                yield Document(text=full_text, meta=meta)
                doc_count += 1

    logger.info("[voxpopuli] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# PMC NL — PubMed Central Dutch open-access articles (CC-BY)
# ---------------------------------------------------------------------------

# JATS XML namespaces (PMC uses a mix of namespace-aware and plain elements)
_JATS_NS: dict[str, str] = {}  # JATS uses no default namespace prefix


def _parse_pmc_jats(xml_path: Path, acq_date: str) -> Document | None:
    """
    Parse one PMC JATS XML article file.

    Extracts title + abstract + selected body paragraphs.
    Returns None if text is too short or clearly non-Dutch.
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        logger.warning("[pmc_nl] XML error in %s: %s", xml_path.name, exc)
        return None

    root = tree.getroot()

    # Language check (accept nl, nld, empty)
    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang", "")
    if not lang:
        art = root.find(".//article")
        if art is not None:
            lang = art.get("{http://www.w3.org/XML/1998/namespace}lang", "")
    if lang and lang[:2] not in ("nl", ""):
        if lang[:2] in ("en", "de", "fr", "es", "pt"):
            return None  # Clearly non-Dutch

    # Title
    title_el = root.find(".//article-title")
    title = " ".join(title_el.itertext()).strip() if title_el is not None else ""

    # Abstract
    abstract_parts = [
        " ".join(abs_el.itertext()).strip()
        for abs_el in root.findall(".//abstract")
        if " ".join(abs_el.itertext()).strip()
    ]
    abstract = " ".join(abstract_parts)

    # Body paragraphs (limited)
    body_parts = []
    body_el = root.find(".//body")
    if body_el is not None:
        for para in body_el.iter("p"):
            text = " ".join(para.itertext()).strip()
            if text:
                body_parts.append(text)
                if len(" ".join(body_parts)) > 20_000:
                    break
    body_text = " ".join(body_parts)

    full_text = "\n\n".join(filter(None, [title, abstract, body_text])).strip()
    if len(full_text) < 200:
        return None

    # Publication year
    pub_year = None
    for year_tag in (".//pub-date/year", ".//year"):
        year_el = root.find(year_tag)
        if year_el is not None and year_el.text:
            try:
                pub_year = int(year_el.text.strip())
                break
            except ValueError:
                pass

    # License from article metadata
    license_el = root.find(".//license")
    article_license = "CC-BY"
    if license_el is not None:
        href = license_el.get("{http://www.w3.org/1999/xlink}href", "")
        if "cc0" in href.lower() or "publicdomain" in href.lower():
            article_license = "CC0"
        elif "by-sa" in href.lower():
            article_license = "CC-BY-SA"
        elif "by-nc" in href.lower() or "nc" in href.lower():
            return None  # NC license — skip

    # DOI / source URL
    doi_el = root.find(".//article-id[@pub-id-type='doi']")
    doi = doi_el.text.strip() if doi_el is not None and doi_el.text else ""
    source_url = f"https://doi.org/{doi}" if doi else f"https://www.ncbi.nlm.nih.gov/pmc/"

    # PMC ID for make_id
    pmc_el = root.find(".//article-id[@pub-id-type='pmc']")
    pmc_id = pmc_el.text.strip() if pmc_el is not None and pmc_el.text else xml_path.stem

    doc_id = make_id("pmc_nl", pmc_id)
    meta = DocumentMeta(
        id=doc_id,
        source="pmc_nl",
        source_url=source_url,
        license=article_license,
        publication_year=pub_year,
        country="NL",
        domain="academic",
        language_variant="NL",
        acquisition_date=acq_date,
    )
    return Document(text=full_text, meta=meta)


def parse_pmc_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse PMC Dutch-language JATS XML articles.

    Expects: raw_dir/articles/*.xml  (downloaded by pmc_nl acquirer)

    Source: PubMed Central OA Comm Use subset, CC-BY / CC0
    Domain: biomedical / life sciences academic Dutch
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    articles_dir = raw_dir / "articles"
    search_root  = articles_dir if articles_dir.exists() else raw_dir
    xml_files    = sorted(search_root.glob("*.xml"))
    logger.info("[pmc_nl] Found %d XML files in %s", len(xml_files), search_root)

    for xml_path in xml_files:
        doc = _parse_pmc_jats(xml_path, acq_date)
        if doc is None:
            continue
        yield doc
        doc_count += 1

    logger.info("[pmc_nl] Parsed %d Dutch articles", doc_count)


# ---------------------------------------------------------------------------
# NCHLT Afrikaans — SADiLaR government text corpus (CC BY 2.5 ZA)
# ---------------------------------------------------------------------------

def parse_nchlt_af_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse NCHLT Afrikaans text corpus from SADiLaR.

    Reads .txt files extracted from the NCHLT Afrikaans ZIP.
    Each file contains Afrikaans government-domain text.

    Source: SADiLaR / NCHLT, CC BY 2.5 South Africa
    Language variant: AF (Afrikaans), Country: ZA
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    # Extract ZIP archive if present and not yet extracted
    import zipfile as _zipfile
    for zip_path in sorted(raw_dir.glob("*.zip")):
        extract_to = raw_dir / zip_path.stem
        if not extract_to.exists():
            logger.info("[nchlt_af] Extracting %s ...", zip_path.name)
            try:
                with _zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_to)
            except Exception as exc:
                logger.warning("[nchlt_af] Cannot extract %s: %s", zip_path.name, exc)

    txt_files = sorted(raw_dir.rglob("*.txt"))
    logger.info("[nchlt_af] Found %d .txt files in %s", len(txt_files), raw_dir)

    for txt_path in txt_files:
        try:
            text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[nchlt_af] Could not read %s: %s", txt_path.name, exc)
            continue

        if len(text) < 50:
            continue

        doc_id = make_id("nchlt_af", txt_path.stem)
        meta = DocumentMeta(
            id=doc_id,
            source="nchlt_af",
            source_url="https://hdl.handle.net/20.500.12185/293",
            license="CC-BY",
            publication_year=None,
            country="ZA",
            domain="government",
            language_variant="AF",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info("[nchlt_af] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# Autshumato Afrikaans — SADiLaR parallel corpus Afrikaans side (CC BY 4.0)
# ---------------------------------------------------------------------------

def parse_autshumato_af_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Autshumato English-Afrikaans parallel corpus (Afrikaans side only).

    Reads .txt and .af files extracted from the Autshumato ZIP.
    Each line is one Afrikaans sentence from South African government texts.
    Documents are batched into chunks of 50 sentences to avoid very short docs.

    Source: SADiLaR / CTexT NWU, CC BY 4.0
    Language variant: AF (Afrikaans), Country: ZA
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0
    batch_size = 50

    text_files = sorted(
        list(raw_dir.glob("*.af"))
        + list(raw_dir.glob("*.afr"))
        + list(raw_dir.glob("*.txt"))
    )
    logger.info("[autshumato_af] Found %d text files in %s", len(text_files), raw_dir)

    for txt_path in text_files:
        try:
            lines = txt_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("[autshumato_af] Could not read %s: %s", txt_path.name, exc)
            continue

        # Batch sentences into documents
        batch: list[str] = []
        batch_idx = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            batch.append(line)
            if len(batch) >= batch_size:
                text = " ".join(batch)
                if len(text) >= 50:
                    doc_id = make_id("autshumato_af", f"{txt_path.stem}_{batch_idx}")
                    meta = DocumentMeta(
                        id=doc_id,
                        source="autshumato_af",
                        source_url="https://repo.sadilar.org/handle/20.500.12185/574",
                        license="CC-BY",
                        publication_year=None,
                        country="ZA",
                        domain="government",
                        language_variant="AF",
                        acquisition_date=acq_date,
                    )
                    yield Document(text=text, meta=meta)
                    doc_count += 1
                batch = []
                batch_idx += 1

        # Flush remaining lines
        if batch:
            text = " ".join(batch)
            if len(text) >= 50:
                doc_id = make_id("autshumato_af", f"{txt_path.stem}_{batch_idx}")
                meta = DocumentMeta(
                    id=doc_id,
                    source="autshumato_af",
                    source_url="https://repo.sadilar.org/handle/20.500.12185/574",
                    license="CC-BY",
                    publication_year=None,
                    country="ZA",
                    domain="government",
                    language_variant="AF",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                doc_count += 1

    logger.info("[autshumato_af] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# ParlaMint-BE — Belgian Chamber TEI XML (CC BY 4.0)
# ---------------------------------------------------------------------------

def _parse_parlamint_be_tei(xml_path: Path, acq_date: str) -> Iterator[Document]:
    """
    Parse a single ParlaMint-BE TEI XML file.

    Each <u> (utterance) element is one speech turn. Only Dutch-language
    utterances (xml:lang="nl" or "nl-BE") are emitted.
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        logger.warning("[parlamint_be] XML error in %s: %s", xml_path.name, exc)
        return

    root = tree.getroot()

    # Extract session-level metadata from teiHeader
    header = root.find(".//tei:teiHeader", _PM_NS)
    session_date = None
    session_id = xml_path.stem

    if header is not None:
        date_el = header.find(".//tei:settingDesc//tei:date", _PM_NS)
        if date_el is None:
            date_el = header.find(".//tei:sourceDesc//tei:date", _PM_NS)
        if date_el is not None:
            date_str = date_el.get("when") or date_el.get("from") or ""
            if date_str and len(date_str) >= 4:
                try:
                    session_date = int(date_str[:4])
                except ValueError:
                    pass

    source_url = "https://www.clarin.si/repository/xmlui/handle/11356/2004"

    # Collect all utterances
    utterances = root.findall(".//tei:u", _PM_NS)
    if not utterances:
        return

    for utt_el in utterances:
        # Filter: only Dutch-language utterances
        lang_attr = utt_el.get("{http://www.w3.org/XML/1998/namespace}lang") or ""
        if lang_attr and not lang_attr.lower().startswith("nl"):
            continue

        # Collect all text within the utterance
        segments = []
        for seg in utt_el.iter():
            if seg.text and seg.text.strip():
                segments.append(seg.text.strip())
            if seg.tail and seg.tail.strip():
                segments.append(seg.tail.strip())
        text = " ".join(segments).strip()
        if len(text) < 50:
            continue

        utt_id = utt_el.get("{http://www.w3.org/XML/1998/namespace}id") or utt_el.get("id") or ""
        doc_id = make_id("parlamint_be", f"{session_id}_{utt_id}" if utt_id else session_id)
        meta = DocumentMeta(
            id=doc_id,
            source="parlamint_be",
            source_url=source_url,
            license="CC-BY",
            publication_year=session_date,
            country="BE",
            domain="parliamentary",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


def parse_parlamint_be_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse ParlaMint-BE TEI XML files from raw_dir.

    Expects either:
    - raw_dir/tei/**/*.xml  (extracted archive)
    - raw_dir/**/*.xml      (flat or nested structure)

    Skips *-ana.xml annotation layer files.
    Filters for Dutch-language utterances (xml:lang starting with "nl").

    Source: CLARIN.SI, CC BY 4.0, Belgian Chamber of Representatives 2015-2023
    Language variant: NL-BE
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    # Prefer the tei/ subdir if present
    tei_dir = raw_dir / "tei"
    search_root = tei_dir if tei_dir.exists() else raw_dir

    xml_files = sorted(search_root.rglob("*.xml"))
    logger.info("[parlamint_be] Found %d XML files in %s", len(xml_files), search_root)

    for xml_path in xml_files:
        # Skip TEI annotation layers (ana files) — same content as plain TEI
        if xml_path.name.endswith("-ana.xml"):
            continue
        for doc in _parse_parlamint_be_tei(xml_path, acq_date):
            yield doc
            doc_count += 1

    logger.info("[parlamint_be] Parsed %d utterances", doc_count)


# ---------------------------------------------------------------------------
# Tweede Kamer Open Data — SyncFeed Atom XML (CC0)
# ---------------------------------------------------------------------------

def _tk_find_el(parent: ET.Element, tag: str, atom_ns: str) -> ET.Element | None:
    """Find a child element by tag, trying with Atom namespace first then without."""
    el = parent.find(f"{{{atom_ns}}}{tag}")
    if el is not None:
        return el
    return parent.find(tag)


def _tk_find_text(entry: ET.Element, tag: str, atom_ns: str) -> str:
    """Extract text content of a named child element from an Atom entry."""
    el = _tk_find_el(entry, tag, atom_ns)
    if el is None:
        return ""
    return " ".join(el.itertext()).strip()


_TK_ATOM_NS = "http://www.w3.org/2005/Atom"


def _parse_tk_feed_xml(xml_path: Path, acq_date: str) -> Iterator["Document"]:
    """Yield Documents from a Tweede Kamer SyncFeed Atom XML file."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        logger.warning("[tweede_kamer_opendata] XML error in %s: %s", xml_path.name, exc)
        return

    root = tree.getroot()
    ns = {"a": _TK_ATOM_NS}

    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        summary_el = entry.find("a:summary", ns)
        updated_el = entry.find("a:updated", ns)
        link_el = entry.find("a:link[@rel='alternate']", ns)

        title = (title_el.text or "").strip() if title_el is not None else ""
        summary = (summary_el.text or "").strip() if summary_el is not None else ""

        parts = [p for p in (title, summary) if p]
        text = "\n".join(parts).strip()
        if len(text) < 20:
            continue

        pub_year = None
        updated = (updated_el.text or "") if updated_el is not None else ""
        m = re.match(r"(\d{4})", updated)
        if m:
            try:
                pub_year = int(m.group(1))
            except ValueError:
                pass

        href = ""
        if link_el is not None:
            href = link_el.get("href", "")
        source_url = href or "https://opendata.tweedekamer.nl/"

        entry_id_el = entry.find("a:id", ns)
        uid = (entry_id_el.text or xml_path.stem).strip() if entry_id_el is not None else xml_path.stem
        doc_id = make_id("tweede_kamer_opendata", uid)

        meta = DocumentMeta(
            id=doc_id,
            source="tweede_kamer_opendata",
            source_url=source_url,
            license="CC0",
            publication_year=pub_year,
            country="NL",
            domain="parliamentary",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)


def parse_tweede_kamer_opendata_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Tweede Kamer open data XML files.

    Handles two file types:
    - tk_feed_*.xml  — Atom SyncFeed pages; each <entry> yields a document from
                       its <title> + <summary> fields.
    - tk_doc_*.xml   — VLOS vlosCoreDocument files; full text extracted from XML.

    Source: gegevensmagazijn.tweedekamer.nl SyncFeed, CC0
    Language variant: NL, Country: NL
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0

    feed_files = sorted(raw_dir.rglob("tk_feed_*.xml"))
    doc_files  = sorted(raw_dir.rglob("tk_doc_*.xml"))

    logger.info(
        "[tweede_kamer_opendata] Found %d feed pages and %d VLOS doc files in %s",
        len(feed_files), len(doc_files), raw_dir,
    )

    for xml_path in feed_files:
        for doc in _parse_tk_feed_xml(xml_path, acq_date):
            yield doc
            doc_count += 1

    for xml_path in doc_files:
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            logger.warning("[tweede_kamer_opendata] XML error in %s: %s", xml_path.name, exc)
            continue

        root = tree.getroot()

        text = " ".join(root.itertext()).strip()
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 100:
            continue

        pub_year = None
        timestamp = root.get("Timestamp", "") or root.get("timestamp", "")
        m = re.match(r"(\d{4})", timestamp)
        if m:
            try:
                pub_year = int(m.group(1))
            except ValueError:
                pass

        uid = xml_path.stem
        doc_id = make_id("tweede_kamer_opendata", uid)
        meta = DocumentMeta(
            id=doc_id,
            source="tweede_kamer_opendata",
            source_url="https://opendata.tweedekamer.nl/",
            license="CC0",
            publication_year=pub_year,
            country="NL",
            domain="parliamentary",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info("[tweede_kamer_opendata] Parsed %d documents", doc_count)


# ---------------------------------------------------------------------------
# UvN Podcast — Universiteit van Nederland (CC-BY, NL, speech)
# ---------------------------------------------------------------------------

def parse_uvn_podcast_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Universiteit van Nederland podcast episodes (CC-BY).

    Each episode produces:
      {ep_id}.txt  — transcript or show notes (plain text)
      {ep_id}.json — metadata sidecar

    Yields one Document per episode.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for txt_file in sorted(raw_dir.glob("*.txt")):
        if txt_file.name.startswith("_"):
            continue

        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[uvn_podcast] Cannot read %s: %s", txt_file.name, exc)
            continue

        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 200:
            continue

        ep_id = txt_file.stem
        sidecar_path = txt_file.with_suffix(".json")
        sidecar: dict = {}
        if sidecar_path.exists():
            try:
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        pub_year: int | None = sidecar.get("publication_year")
        source_url: str = sidecar.get("source_url", "")
        license_: str = sidecar.get("license", "CC-BY")

        meta = DocumentMeta(
            id=make_id("uvn_podcast", ep_id),
            source="uvn_podcast",
            source_url=source_url,
            license=license_,
            publication_year=pub_year,
            country="NL",
            domain="speech",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_num += 1

    logger.info("[uvn_podcast] Parsed %d episodes", doc_num)


# ---------------------------------------------------------------------------
# UvV Podcast — Universiteit van Vlaanderen (CC-BY, NL-BE, speech)
# ---------------------------------------------------------------------------

def parse_uvv_podcast_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Universiteit van Vlaanderen podcast episodes (CC-BY, NL-BE).

    Each episode produces:
      {ep_id}.txt  — transcript or show notes (plain text)
      {ep_id}.json — metadata sidecar

    Yields one Document per episode.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for txt_file in sorted(raw_dir.glob("*.txt")):
        if txt_file.name.startswith("_"):
            continue

        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[uvv_podcast] Cannot read %s: %s", txt_file.name, exc)
            continue

        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 200:
            continue

        ep_id = txt_file.stem
        sidecar_path = txt_file.with_suffix(".json")
        sidecar: dict = {}
        if sidecar_path.exists():
            try:
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        pub_year: int | None = sidecar.get("publication_year")
        source_url: str = sidecar.get("source_url", "")
        license_: str = sidecar.get("license", "CC-BY")

        meta = DocumentMeta(
            id=make_id("uvv_podcast", ep_id),
            source="uvv_podcast",
            source_url=source_url,
            license=license_,
            publication_year=pub_year,
            country="BE",
            domain="speech",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_num += 1

    logger.info("[uvv_podcast] Parsed %d episodes", doc_num)


# ---------------------------------------------------------------------------
# Radio Oranje — WWII Dutch government-in-exile broadcasts (PDM/PD, historical)
# ---------------------------------------------------------------------------

def parse_radio_oranje_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Radio Oranje ASR transcripts (PDM 1.0, NL, historical).

    The acquirer stores audio + JSON sidecars in raw_dir/audio/.
    After `pipeline asr`, each audio file has a companion .txt transcript.

    Yields one Document per transcribed recording.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    # Transcripts are in the audio/ subdirectory
    audio_dir = raw_dir / "audio"
    search_dir = audio_dir if audio_dir.is_dir() else raw_dir

    for txt_file in sorted(search_dir.glob("*.txt")):
        if txt_file.name.startswith("_"):
            continue

        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[radio_oranje] Cannot read %s: %s", txt_file.name, exc)
            continue

        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 100:
            continue

        stem = txt_file.stem
        sidecar_path = txt_file.with_suffix(".json")
        sidecar: dict = {}
        if sidecar_path.exists():
            try:
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        pub_year: int | None = sidecar.get("publication_year")
        source_url: str = sidecar.get("source_url", "")

        meta = DocumentMeta(
            id=make_id("radio_oranje", stem),
            source="radio_oranje",
            source_url=source_url,
            license="PD",
            publication_year=pub_year,
            country="NL",
            domain="historical",
            language_variant="NL",
            historical_flag=True,
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_num += 1

    logger.info("[radio_oranje] Parsed %d transcripts", doc_num)


# ---------------------------------------------------------------------------
# De Balie Commons — Wikimedia Commons video descriptions/subtitles (CC-BY-3.0)
# ---------------------------------------------------------------------------

def parse_debalie_commons_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse De Balie video descriptions and subtitles from Wikimedia Commons (CC-BY-3.0).

    Each file produces:
      {file_slug}.txt  — subtitle text or description (plain text)
      {file_slug}.json — metadata sidecar

    Yields one Document per file.
    """
    acq_date = datetime.date.today().isoformat()
    doc_num = 0

    for txt_file in sorted(raw_dir.glob("*.txt")):
        if txt_file.name.startswith("_"):
            continue

        try:
            text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[debalie_commons] Cannot read %s: %s", txt_file.name, exc)
            continue

        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 200:
            continue

        slug = txt_file.stem
        sidecar_path = txt_file.with_suffix(".json")
        sidecar: dict = {}
        if sidecar_path.exists():
            try:
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        pub_year: int | None = sidecar.get("publication_year")
        source_url: str = sidecar.get("source_url", "")
        license_: str = sidecar.get("license", "CC-BY-3.0")

        meta = DocumentMeta(
            id=make_id("debalie_commons", slug),
            source="debalie_commons",
            source_url=source_url,
            license=license_,
            publication_year=pub_year,
            country="NL",
            domain="culture",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_num += 1

    logger.info("[debalie_commons] Parsed %d files", doc_num)


# ---------------------------------------------------------------------------
# Wikikids (wikikids.nl) — Dutch children's encyclopaedia, CC BY-SA 3.0
# ---------------------------------------------------------------------------

def parse_wikikids_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Wikikids articles downloaded by acquire/wikikids.py.

    Input: wikikids_articles.jsonl — one JSON record per line:
      {"pageid": 123, "title": "Hond", "wikitext": "...", "acquisition_date": "..."}

    Strips wikitext markup using wiki_dump._strip_wikitext, skips redirects
    and stubs shorter than 200 characters.

    Yields one Document per article. language_variant=NL, domain=encyclopedic.
    """
    import urllib.parse as _urlparse
    from pipeline.parse.wiki_dump import _strip_wikitext, _is_redirect

    acq_date = datetime.date.today().isoformat()
    jsonl_path = raw_dir / "wikikids_articles.jsonl"
    if not jsonl_path.exists():
        logger.warning("[wikikids] No wikikids_articles.jsonl found in %s", raw_dir)
        return

    total = skipped = 0
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        wikitext = rec.get("wikitext", "")
        if not wikitext or _is_redirect(wikitext):
            skipped += 1
            continue

        text = _strip_wikitext(wikitext)
        if len(text) < 200:
            skipped += 1
            continue

        title   = rec.get("title", "")
        page_id = rec.get("pageid", 0)
        date    = rec.get("acquisition_date", acq_date)

        meta = DocumentMeta(
            id=make_id("wikikids", str(page_id)),
            source="wikikids",
            source_url=f"https://wikikids.nl/{_urlparse.quote(title.replace(' ', '_'))}",
            license="CC-BY-SA",
            publication_year=None,
            country="NL",
            domain="encyclopedic",
            language_variant="NL",
            acquisition_date=date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[wikikids] Parsed %d articles (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Open Raadsinformatie (ORI) — municipal council documents, Public Domain
# ---------------------------------------------------------------------------

def parse_open_raadsinformatie_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Open Raadsinformatie (ORI) municipal council documents.

    Reads ori_documents.jsonl written by acquire/open_raadsinformatie.py.
    Each record: {id, title, text, date, municipality, url, acquisition_date}

    The acquirer may also save whatever JSON the API returns; we try to
    normalise multiple possible field names.

    Source: api.openraadsinformatie.nl, Public Domain (Art. 11 Auteurswet)
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0
    skipped = 0

    jsonl_path = raw_dir / "ori_documents.jsonl"
    if not jsonl_path.exists():
        logger.warning("[ori] No ori_documents.jsonl found in %s", raw_dir)
        return

    with jsonl_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            # Try multiple text field names in priority order
            text = ""
            for field in ("text", "description", "body", "content", "summary"):
                val = rec.get(field)
                if val and isinstance(val, str) and len(val.strip()) >= 100:
                    text = val.strip()
                    break

            if not text:
                # Combine title + description/summary
                parts = []
                for field in ("title", "description", "summary"):
                    val = rec.get(field)
                    if val and isinstance(val, str) and val.strip():
                        parts.append(val.strip())
                text = "\n\n".join(parts)

            if len(text) < 100:
                skipped += 1
                continue

            rec_id   = str(rec.get("id") or rec.get("identifier") or f"ori_{doc_count}")
            date_str = str(rec.get("date") or rec.get("datum") or rec.get("acquisition_date") or "")
            url      = str(rec.get("url") or rec.get("source_url") or "")

            pub_year = None
            year_m = re.search(r'\b(20[012]\d|199\d)\b', date_str)
            if year_m:
                try:
                    pub_year = int(year_m.group(1))
                except ValueError:
                    pass

            meta = DocumentMeta(
                id=make_id("open_raadsinformatie", rec_id),
                source="open_raadsinformatie",
                source_url=url or "https://api.openraadsinformatie.nl/v1/",
                license="PD",
                publication_year=pub_year,
                country="NL",
                domain="government",
                language_variant="NL",
                acquisition_date=date_str or acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

    logger.info("[ori] Parsed %d documents (%d skipped)", doc_count, skipped)


# ---------------------------------------------------------------------------
# KB Open Kranten — historical Dutch newspapers, CC-BY 4.0
# ---------------------------------------------------------------------------

def _parse_alto_xml(xml_path: Path) -> str:
    """
    Parse an ALTO XML file and extract the textual content.

    ALTO (Analyzed Layout and Text Object) is the standard KB format for
    OCR text. Text content lives in <String CONTENT="..."> attributes or
    in <TextLine> / <TextBlock> elements.
    """
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError as exc:
        logger.debug("[kb_kranten] XML parse error in %s: %s", xml_path.name, exc)
        return ""

    parts: list[str] = []

    # ALTO format: <String CONTENT="word"> attributes within <TextLine>
    for string_el in root.iter():
        # Handle namespaced and non-namespaced elements
        local = string_el.tag.split("}")[-1] if "}" in string_el.tag else string_el.tag
        if local == "String":
            content = string_el.get("CONTENT") or string_el.get("content") or ""
            if content.strip():
                parts.append(content.strip())
        elif local in ("TextLine", "textline"):
            # Some ALTO variants put text directly in element text
            if string_el.text and string_el.text.strip():
                parts.append(string_el.text.strip())

    if parts:
        # ALTO strings are individual words — join with spaces, then form paragraphs
        # Group into paragraphs by using the structure: TextBlock → TextLines → Strings
        return " ".join(parts)

    # Fallback: extract all text content
    all_text = " ".join(
        (el.text or "").strip()
        for el in root.iter()
        if el.text and el.text.strip()
    )
    return all_text.strip()


def _parse_didl_xml(xml_path: Path) -> dict:
    """
    Parse a DIDL metadata XML file and extract title, date, and identifier.
    Returns a dict with keys: title, date, identifier.
    """
    meta: dict = {"title": "", "date": "", "identifier": ""}
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError:
        return meta

    # DC/DCTERMS namespace elements
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        text  = (el.text or "").strip()
        if not text:
            continue
        if local in ("title", "Title"):
            meta["title"] = text
        elif local in ("date", "Date", "issued"):
            meta["date"] = text
        elif local in ("identifier", "Identifier"):
            meta["identifier"] = text

    return meta


def parse_kb_open_kranten_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse KB Open Kranten historical newspaper article XML files.

    Handles:
      articles/*_articletext.xml  — ALTO XML article text files
      articles/*.xml              — Any ALTO XML (includes non-suffixed files)
      *_batch_*.xml               — SRU metadata fallback batches
      *_sru_*_probe.xml           — SRU probe files (skipped as metadata-only)

    Source: lab.kb.nl, CC-BY 4.0
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0
    skipped = 0

    articles_dir = raw_dir / "articles"

    # --- Parse ALTO XML article files ---
    if articles_dir.exists():
        xml_files = sorted(articles_dir.glob("*.xml"))
        logger.info("[kb_kranten] Found %d XML files in articles/", len(xml_files))

        for xml_path in xml_files:
            text = _parse_alto_xml(xml_path)
            if len(text) < 100:
                skipped += 1
                continue

            # Try to find a matching DIDL metadata file
            didl_path = xml_path.with_name(xml_path.name.replace("_articletext", "_didl"))
            meta_info: dict = {"title": "", "date": "", "identifier": ""}
            if didl_path.exists():
                meta_info = _parse_didl_xml(didl_path)

            title   = meta_info.get("title") or xml_path.stem
            date_str = meta_info.get("date") or ""
            rec_id  = meta_info.get("identifier") or xml_path.stem

            pub_year = None
            year_m = re.search(r'\b(1[6-9]\d{2}|20[012]\d)\b', date_str or xml_path.name)
            if year_m:
                try:
                    pub_year = int(year_m.group(1))
                except ValueError:
                    pass

            meta = DocumentMeta(
                id=make_id("kb_open_kranten", rec_id),
                source="kb_open_kranten",
                source_url=f"https://resolver.kb.nl/resolve?urn={rec_id}" if rec_id else "https://lab.kb.nl/dataset/open-kranten",
                license="CC-BY",
                publication_year=pub_year,
                country="NL",
                domain="news",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

    # --- Parse SRU metadata fallback batches ---
    for sru_file in sorted(raw_dir.glob("kb_kranten_sru_*.xml")):
        if "probe" in sru_file.name:
            continue
        try:
            tree = ET.parse(str(sru_file))
            root = tree.getroot()
        except ET.ParseError as exc:
            logger.warning("[kb_kranten] SRU XML parse error in %s: %s", sru_file.name, exc)
            continue

        for record in root.iter():
            local = record.tag.split("}")[-1] if "}" in record.tag else record.tag
            if local not in ("record", "Record"):
                continue

            # Extract all text content from child elements
            parts: list[str] = []
            title = ""
            date_str = ""
            rec_id = ""
            for child in record.iter():
                child_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                text_val = (child.text or "").strip()
                if not text_val:
                    continue
                if child_local in ("title", "Title"):
                    title = text_val
                    parts.append(text_val)
                elif child_local in ("description", "abstract", "subject"):
                    parts.append(text_val)
                elif child_local in ("date", "Date", "issued"):
                    date_str = text_val
                elif child_local in ("identifier", "Identifier"):
                    rec_id = text_val

            text = "\n\n".join(p for p in parts if p)
            if len(text) < 50:
                skipped += 1
                continue

            pub_year = None
            year_m = re.search(r'\b(1[6-9]\d{2}|20[012]\d)\b', date_str)
            if year_m:
                try:
                    pub_year = int(year_m.group(1))
                except ValueError:
                    pass

            meta = DocumentMeta(
                id=make_id("kb_open_kranten", rec_id or f"{sru_file.stem}_{doc_count}"),
                source="kb_open_kranten",
                source_url=rec_id if rec_id.startswith("http") else "https://lab.kb.nl/dataset/open-kranten",
                license="CC-BY",
                publication_year=pub_year,
                country="NL",
                domain="news",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            doc_count += 1

    logger.info("[kb_kranten] Parsed %d documents (%d skipped)", doc_count, skipped)


# ---------------------------------------------------------------------------
# JuPortal BE — Belgian court decisions in Dutch, Public Domain
# ---------------------------------------------------------------------------

def parse_juportal_be_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Belgian court decisions downloaded from juportal.be.

    Reads juportal_decisions.jsonl written by acquire/juportal_be.py.
    Each record: {ecli, title, text, date, court, url, acquisition_date}

    Only yields Dutch-language decisions (the acquirer already filters for NL,
    but we apply a secondary check on text length).

    Source: juportal.be, Public Domain (Belgian Copyright Act Art. 8)
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0
    skipped = 0

    jsonl_path = raw_dir / "juportal_decisions.jsonl"
    if not jsonl_path.exists():
        logger.warning("[juportal] No juportal_decisions.jsonl found in %s", raw_dir)
        return

    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = str(rec.get("text") or "").strip()
        if len(text) < 100:
            skipped += 1
            continue

        ecli     = str(rec.get("ecli") or "").strip()
        title    = str(rec.get("title") or ecli).strip()
        date_str = str(rec.get("date") or rec.get("acquisition_date") or "").strip()
        court    = str(rec.get("court") or "").strip()
        url      = str(rec.get("url") or f"https://juportal.be/content/{ecli}").strip()

        pub_year = None
        # Try year from ECLI first: ECLI:BE:COURT:YEAR:number
        ecli_parts = ecli.split(":")
        if len(ecli_parts) >= 4:
            try:
                pub_year = int(ecli_parts[3])
            except (ValueError, IndexError):
                pass
        if pub_year is None and date_str:
            year_m = re.search(r'\b(1[89]\d{2}|20[012]\d)\b', date_str)
            if year_m:
                try:
                    pub_year = int(year_m.group(1))
                except ValueError:
                    pass

        meta = DocumentMeta(
            id=make_id("juportal_be", ecli or f"juportal_{doc_count}"),
            source="juportal_be",
            source_url=url,
            license="PD",
            publication_year=pub_year,
            country="BE",
            domain="legal",
            language_variant="NL-BE",
            acquisition_date=date_str or acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info("[juportal] Parsed %d decisions (%d skipped)", doc_count, skipped)


# ---------------------------------------------------------------------------
# Mensenrechten — College voor de Rechten van de Mens equality body decisions
# ---------------------------------------------------------------------------

def parse_mensenrechten_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse oordelen from College voor de Rechten van de Mens (Dutch equality body).

    Reads oordelen.jsonl written by acquire/mensenrechten.py.
    Each record: {id, title, text, date, year, number, discriminatiegrond,
                  terrein, trefwoord, url, source, license, country, domain}

    Source: oordelen.mensenrechten.nl, attribution-permissive (CC-BY compatible)
    """
    acq_date = datetime.date.today().isoformat()
    jsonl_path = raw_dir / "oordelen.jsonl"
    if not jsonl_path.exists():
        logger.warning("[mensenrechten] No oordelen.jsonl found in %s", raw_dir)
        return

    doc_count = 0
    skipped   = 0

    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = str(rec.get("text") or "").strip()
        if len(text) < 100:
            skipped += 1
            continue

        title    = str(rec.get("title") or rec.get("id") or "").strip()
        date_str = str(rec.get("date") or rec.get("acquisition_date") or "").strip()
        url      = str(rec.get("url") or "").strip()
        year     = rec.get("year")
        number   = rec.get("number")
        grond    = str(rec.get("discriminatiegrond") or "").strip()
        terrein  = str(rec.get("terrein") or "").strip()

        pub_year = None
        if year and isinstance(year, int):
            pub_year = year
        elif date_str:
            m = re.search(r"\b(\d{4})\b", date_str)
            if m:
                pub_year = int(m.group(1))

        meta_extra = {}
        if grond:
            meta_extra["discriminatiegrond"] = grond
        if terrein:
            meta_extra["terrein"] = terrein
        if number is not None:
            meta_extra["number"] = number

        meta = DocumentMeta(
            id=make_id("mensenrechten", str(rec.get("id") or str(doc_count))),
            source="mensenrechten",
            source_url=url or "https://oordelen.mensenrechten.nl/",
            license="CC-BY",
            publication_year=pub_year,
            country="NL",
            domain="legal",
            language_variant="NL",
            acquisition_date=date_str or acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info("[mensenrechten] Parsed %d oordelen (%d skipped)", doc_count, skipped)


# ---------------------------------------------------------------------------
# Woo Documents — Dutch open government request documents, Public Domain
# ---------------------------------------------------------------------------

def parse_woo_documents_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Woo (Wet open overheid) open government documents.

    Reads woo_docs.jsonl written by acquire/woo_documents.py.
    Each record: {id, title, text, date, organisation, url, acquisition_date}

    Source: api.woogle.nl / open.overheid.nl, Public Domain (Art. 11 Auteurswet)
    """
    acq_date = datetime.date.today().isoformat()
    doc_count = 0
    skipped = 0

    jsonl_path = raw_dir / "woo_docs.jsonl"
    if not jsonl_path.exists():
        logger.warning("[woo] No woo_docs.jsonl found in %s", raw_dir)
        return

    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        # Try multiple text field names
        text = ""
        for field in ("text", "body", "content", "description", "summary", "inhoud"):
            val = rec.get(field)
            if val and isinstance(val, str) and len(val.strip()) >= 100:
                text = val.strip()
                break

        if not text:
            parts = []
            for field in ("title", "description", "summary"):
                val = rec.get(field)
                if val and isinstance(val, str) and val.strip():
                    parts.append(val.strip())
            text = "\n\n".join(parts)

        if len(text) < 100:
            skipped += 1
            continue

        rec_id   = str(rec.get("id") or f"woo_{doc_count}")
        title    = str(rec.get("title") or rec.get("titel") or "").strip()
        date_str = str(rec.get("date") or rec.get("datum") or
                       rec.get("acquisition_date") or "").strip()
        url      = str(rec.get("url") or rec.get("source_url") or "").strip()

        pub_year = None
        year_m = re.search(r'\b(20[012]\d|199\d)\b', date_str)
        if year_m:
            try:
                pub_year = int(year_m.group(1))
            except ValueError:
                pass

        meta = DocumentMeta(
            id=make_id("woo_documents", rec_id),
            source="woo_documents",
            source_url=url or "https://api.woogle.nl/",
            license="PD",
            publication_year=pub_year,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=date_str or acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info("[woo] Parsed %d documents (%d skipped)", doc_count, skipped)


# ---------------------------------------------------------------------------
# Digibron — pre-1954 Dutch Protestant periodicals (PD)  [Batch 7]
# ---------------------------------------------------------------------------

def parse_digibron_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Digibron pre-1954 periodical articles from digibron_articles.jsonl.

    Each line: JSON with title/body/date/publication/url fields.
    - Strips HTML from body
    - Skips records with < 100 chars of body text after stripping
    - Parses publication year from the date field
    - source="digibron", license="PD", domain="news", language_variant="NL"
    """
    acq_date = datetime.date.today().isoformat()
    jsonl_path = raw_dir / "digibron_articles.jsonl"
    if not jsonl_path.exists():
        logger.warning("[digibron] %s not found", jsonl_path)
        return

    total = 0
    skipped = 0
    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("[digibron] Cannot read %s: %s", jsonl_path, exc)
        return

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        raw_body = rec.get("body") or rec.get("content") or rec.get("text") or ""
        text = strip_html(str(raw_body)).strip()
        if len(text) < 100:
            skipped += 1
            continue

        # Prepend title if present
        title = str(rec.get("title") or "").strip()
        if title and not text.startswith(title):
            text = f"{title}\n\n{text}"

        # Parse year from date field
        year: int | None = None
        date_str = str(rec.get("date") or "")
        if date_str:
            m = re.search(r'\b(1[5-9]\d{2}|20\d{2})\b', date_str)
            if m:
                year = int(m.group(0))

        source_url = str(rec.get("url") or "https://www.digibron.nl/")
        rec_id = str(rec.get("id") or f"line{total}")
        rec_acq = rec.get("acquisition_date") or acq_date

        meta = DocumentMeta(
            id=make_id("digibron", rec_id),
            source="digibron",
            source_url=source_url,
            license="PD",
            publication_year=year,
            country="NL",
            domain="news",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[digibron] Parsed %d articles (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# OAPEN NL — Dutch open-access academic books (CC-BY)  [Batch 7]
# ---------------------------------------------------------------------------

def parse_oapen_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse OAPEN Dutch academic books from JSON sidecars + PDF files.

    raw_dir/
      {handle_safe}.json   — metadata sidecar
      {handle_safe}.pdf    — full-text PDF (optional)

    Skips any sidecar whose licence contains NC or ND.
    Falls back to title-only document if no PDF is available.
    """
    from pipeline.parse.pdf_text import extract_text as _extract_pdf

    acq_date = datetime.date.today().isoformat()
    total = 0
    skipped = 0

    for meta_file in sorted(raw_dir.glob("*.json")):
        if meta_file.name == "acquisition_log.jsonl":
            continue
        try:
            sidecar = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[oapen_nl] Cannot read sidecar %s: %s", meta_file, exc)
            continue

        # License check — skip NC/ND
        rights = str(sidecar.get("license") or sidecar.get("rights") or "")
        r_lower = rights.lower()
        if "nc" in r_lower or "nd" in r_lower:
            skipped += 1
            continue

        # Normalise licence string
        if "/by/" in r_lower or "/by-" in r_lower:
            licence_norm = "CC-BY"
        elif "cc0" in r_lower or "cc-0" in r_lower:
            licence_norm = "CC0"
        else:
            licence_norm = rights or "CC-BY"

        # Try to extract text from PDF
        text = ""
        pdf_path = meta_file.with_suffix(".pdf")
        if pdf_path.exists():
            try:
                text = _extract_pdf(pdf_path) or ""
            except Exception as exc:
                logger.warning("[oapen_nl] PDF extraction failed %s: %s", pdf_path.name, exc)

        # Fall back to title when no PDF text
        if not text:
            title = str(sidecar.get("title") or "").strip()
            if title:
                text = title
            if len(text) < 100:
                skipped += 1
                continue

        year: int | None = sidecar.get("year")
        if year is not None:
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = None

        source_url = str(
            sidecar.get("source_url")
            or f"https://library.oapen.org/handle/{sidecar.get('handle', '')}"
        )

        meta = DocumentMeta(
            id=make_id("oapen_nl", meta_file.stem),
            source="oapen_nl",
            source_url=source_url,
            license=licence_norm,
            publication_year=year,
            country="NL",
            domain="academic",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[oapen_nl] Parsed %d books (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Naturalis — biodiversity publications (CC-BY)  [Batch 7]
# ---------------------------------------------------------------------------

def parse_naturalis_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Naturalis Biodiversity Center records from naturalis_records.jsonl.

    For each record:
    - If a matching PDF exists in pdfs/: extract text with pdf_text
    - Else: use dc:description if > 200 chars
    - Skip if resulting text < 100 chars
    - source="naturalis", license="CC-BY", domain="science", language_variant="NL"
    """
    from pipeline.parse.pdf_text import extract_text as _extract_pdf

    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "naturalis_records.jsonl"
    pdfs_dir     = raw_dir / "pdfs"

    if not records_path.exists():
        logger.warning("[naturalis] %s not found", records_path)
        return

    total = 0
    skipped = 0

    try:
        lines = records_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("[naturalis] Cannot read %s: %s", records_path, exc)
        return

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_id     = str(rec.get("record_id") or "")
        titles     = rec.get("titles") or []
        descs      = rec.get("descriptions") or []
        year       = rec.get("year")
        source_url = str(rec.get("source_url") or "https://repository.naturalis.nl/")
        rec_acq    = rec.get("acquisition_date") or acq_date

        # Compute safe filename for PDF lookup (mirrors acquirer logic)
        safe_id = re.sub(r'^[a-z]+://', '', rec_id)
        safe_id = re.sub(r'[/:\\?*|"<>]', '_', safe_id)[:120]

        text = ""

        # Try PDF first
        if pdfs_dir.exists():
            pdf_path = pdfs_dir / f"{safe_id}.pdf"
            if pdf_path.exists():
                try:
                    text = _extract_pdf(pdf_path) or ""
                except Exception as exc:
                    logger.warning("[naturalis] PDF extraction failed %s: %s", pdf_path.name, exc)

        # Fall back to description
        if not text:
            desc_text = " ".join(str(d) for d in descs if d).strip()
            if len(desc_text) > 200:
                title_text = " / ".join(str(t) for t in titles if t).strip()
                text = f"{title_text}\n\n{desc_text}" if title_text else desc_text
            elif titles:
                text = " / ".join(str(t) for t in titles if t).strip()

        if len(text) < 100:
            skipped += 1
            continue

        if year is not None:
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = None

        meta = DocumentMeta(
            id=make_id("naturalis", safe_id or str(total)),
            source="naturalis",
            source_url=source_url,
            license="CC-BY",
            publication_year=year,
            country="NL",
            domain="science",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[naturalis] Parsed %d records (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# MultiEURLEX  (EU legal documents — CC-BY-4.0)
# ---------------------------------------------------------------------------

def parse_multi_eurlex_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse MultiEURLEX EU legal documents for Dutch (nl).

    Reads either:
      - multi_eurlex_nl_*.json files (new tar.gz extraction format)
      - multi_eurlex_{lang}.parquet files (legacy format)

    Each JSON record has fields:
      - celex_id (str)
      - text (dict[lang_code, str] or str)

    Language mapping: nl → NL
    """
    acq_date = datetime.date.today().isoformat()

    _LANG_META: dict[str, tuple[str, str]] = {
        "nl": ("NL", "EU"),
        "af": ("AF", "EU"),
        "fy": ("FY", "EU"),
    }

    # Prefer new JSONL file (from tar.gz nl-extraction approach)
    nl_jsonl = raw_dir / "multi_eurlex_nl.jsonl"
    if nl_jsonl.exists() and nl_jsonl.stat().st_size > 1000:
        lang, (lang_variant, country) = "nl", _LANG_META["nl"]
        total = skipped = 0
        logger.debug("[multi_eurlex] Reading JSONL %s", nl_jsonl.name)
        for line in nl_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = str(rec.get("text") or "").strip()
            if not text:
                skipped += 1
                continue
            celex_id = str(rec.get("celex_id") or "")
            source_url = (
                f"https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:{celex_id}"
                if celex_id else
                "https://huggingface.co/datasets/coastalcph/multi_eurlex"
            )
            meta = DocumentMeta(
                id=make_id("multi_eurlex", f"nl_{celex_id}_{total}"),
                source="multi_eurlex",
                source_url=source_url,
                license="CC-BY",
                publication_year=None,
                country=country,
                domain="legal",
                language_variant=lang_variant,
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1
        logger.info("[multi_eurlex] JSONL: %d docs yielded (%d skipped)", total, skipped)
        return

    # Prefer JSON files (older tar.gz extraction format)
    json_files = list(raw_dir.glob("multi_eurlex_nl_*.json"))
    if json_files:
        lang, (lang_variant, country) = "nl", _LANG_META["nl"]
        total = skipped = 0
        for jf in sorted(json_files):
            logger.debug("[multi_eurlex] Reading JSON %s", jf.name)
            try:
                content = jf.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.warning("[multi_eurlex] Cannot read %s: %s", jf.name, exc)
                continue

            # Try JSONL first, then wrapped JSON array
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            records: list[dict] = []
            for line in lines:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
                    elif isinstance(obj, list):
                        records.extend(o for o in obj if isinstance(o, dict))
                except json.JSONDecodeError:
                    pass

            if not records:
                # Try as a single JSON array/object
                try:
                    obj = json.loads(content)
                    if isinstance(obj, list):
                        records = [r for r in obj if isinstance(r, dict)]
                    elif isinstance(obj, dict):
                        records = [obj]
                except json.JSONDecodeError:
                    pass

            for i, rec in enumerate(records):
                celex_id = str(rec.get("celex_id") or rec.get("id") or "")
                raw_text = rec.get("text") or rec.get("nl_text") or rec.get("content") or ""
                if isinstance(raw_text, dict):
                    text = (raw_text.get(lang) or "").strip()
                elif isinstance(raw_text, str):
                    text = raw_text.strip()
                else:
                    text = ""

                if not text:
                    skipped += 1
                    continue

                source_url = (
                    f"https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:{celex_id}"
                    if celex_id else
                    "https://huggingface.co/datasets/coastalcph/multi_eurlex"
                )
                meta = DocumentMeta(
                    id=make_id("multi_eurlex", f"{lang}_{celex_id}_{i}"),
                    source="multi_eurlex",
                    source_url=source_url,
                    license="CC-BY",
                    publication_year=None,
                    country=country,
                    domain="legal",
                    language_variant=lang_variant,
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                total += 1

        logger.info("[multi_eurlex] JSON format: %d docs yielded (%d skipped)", total, skipped)
        return

    # Legacy: parquet files
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        logger.warning("[multi_eurlex] pyarrow not available and no JSON files — skipping parse")
        return

    for lang, (lang_variant, country) in _LANG_META.items():
        parquet_path = raw_dir / f"multi_eurlex_{lang}.parquet"
        if not parquet_path.exists():
            logger.debug("[multi_eurlex] %s not found — skipping", parquet_path.name)
            continue

        try:
            table = pq.read_table(str(parquet_path))
        except Exception as exc:
            logger.warning("[multi_eurlex] Cannot read %s: %s", parquet_path.name, exc)
            continue

        total = skipped = 0

        for batch in table.to_batches():
            rows = batch.to_pydict()
            n_rows = len(next(iter(rows.values()), []))

            for i in range(n_rows):
                celex_id = ""
                if "celex_id" in rows:
                    celex_id = str(rows["celex_id"][i] or "")

                text = ""
                if "text" in rows:
                    raw_text = rows["text"][i]
                    if isinstance(raw_text, dict):
                        text = (raw_text.get(lang) or "").strip()
                    elif isinstance(raw_text, str):
                        text = raw_text.strip()

                if not text:
                    skipped += 1
                    continue

                source_url = (
                    f"https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:{celex_id}"
                    if celex_id else
                    "https://huggingface.co/datasets/coastalcph/multi_eurlex"
                )
                meta = DocumentMeta(
                    id=make_id("multi_eurlex", f"{lang}_{celex_id}_{i}"),
                    source="multi_eurlex",
                    source_url=source_url,
                    license="CC-BY",
                    publication_year=None,
                    country=country,
                    domain="legal",
                    language_variant=lang_variant,
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                total += 1

        logger.info("[multi_eurlex] lang=%s: %d docs yielded (%d skipped)", lang, total, skipped)


# ---------------------------------------------------------------------------
# TEDEUTenders  (EU procurement tenders — CC0)
# ---------------------------------------------------------------------------

def parse_teu_tenders_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse EU procurement tender texts from PleIAs/TEDEUTenders Parquet shards.

    Reads teu_tenders_shard_*.parquet files downloaded by acquire/teu_tenders.py.

    Columns handled (flexible schema):
      text      — tender full text
      language  — ISO language tag (e.g. 'nl', 'NL', 'nld')
      url       — source URL (optional)
      id        — document identifier (optional)

    Only rows for Germanic languages are retained:
      nl/NL/nld/NLD → NL
      af/AF/afr/AFR → AF
      fy/FY/fry/FRY → FY
      (default when language is absent or unrecognised → NL)

    Rows with fewer than 50 characters of text are skipped.
    """
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        logger.warning("[teu_tenders] pyarrow not available — skipping parse")
        return

    acq_date = datetime.date.today().isoformat()

    # Map lower-cased language tags to language_variant
    _LANG_MAP: dict[str, str] = {
        "nl":  "NL", "nld": "NL",
        "af":  "AF", "afr": "AF",
        "fy":  "FY", "fry": "FY",
    }
    # Accepted lower-cased tags (including the upper-cased variants resolved below)
    _ACCEPTED: frozenset[str] = frozenset(_LANG_MAP.keys())

    def _resolve_lang(raw_lang: str | None) -> str | None:
        """Return language_variant string or None if the language should be filtered out."""
        if not raw_lang:
            return "NL"   # no language tag → assume Dutch (dataset is mostly NL)
        normalised = raw_lang.strip().lower()
        return _LANG_MAP.get(normalised)   # None if not in the accepted set

    shard_files = sorted(raw_dir.glob("teu_tenders_shard_*.parquet"))
    if not shard_files:
        logger.warning("[teu_tenders] No shard files found in %s", raw_dir)
        return

    grand_total = grand_skipped = 0

    for shard_path in shard_files:
        try:
            table = pq.read_table(str(shard_path))
        except Exception as exc:
            logger.warning("[teu_tenders] Cannot read %s: %s", shard_path.name, exc)
            continue

        col_names = set(table.schema.names)
        total = skipped = 0

        for batch in table.to_batches():
            rows = batch.to_pydict()
            n_rows = len(next(iter(rows.values()), []))

            for i in range(n_rows):
                # --- text ---
                text = ""
                for col in ("text", "Text", "content", "body"):
                    if col in rows and rows[col][i] is not None:
                        text = str(rows[col][i]).strip()
                        break

                if len(text) < 50:
                    skipped += 1
                    continue

                # --- language ---
                raw_lang = None
                for col in ("language", "lang", "Language"):
                    if col in rows and rows[col][i] is not None:
                        raw_lang = str(rows[col][i])
                        break

                lang_variant = _resolve_lang(raw_lang)
                if lang_variant is None:
                    skipped += 1
                    continue

                # --- optional fields ---
                source_url = "https://huggingface.co/datasets/PleIAs/TEDEUTenders"
                for col in ("url", "URL", "source_url"):
                    if col in rows and rows[col][i]:
                        source_url = str(rows[col][i])
                        break

                doc_id_raw = ""
                for col in ("id", "ID", "doc_id", "notice_id"):
                    if col in rows and rows[col][i] is not None:
                        doc_id_raw = str(rows[col][i])
                        break

                meta = DocumentMeta(
                    id=make_id("teu_tenders", f"{shard_path.stem}_{i}" if not doc_id_raw else doc_id_raw),
                    source="teu_tenders",
                    source_url=source_url,
                    license="CC0",
                    publication_year=None,
                    country="EU",
                    domain="government",
                    language_variant=lang_variant,
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                total += 1

        logger.info("[teu_tenders] %s: %d docs yielded (%d skipped)", shard_path.name, total, skipped)
        grand_total   += total
        grand_skipped += skipped


# ---------------------------------------------------------------------------
# Vlaams Parlement — journaallijn debate transcripts (vlpar_extra)
# ---------------------------------------------------------------------------

def _vlpar_extra_text(xml_bytes: bytes) -> str:
    """
    Extract speaker transcript text from a /jln/{id} XML response.

    The API embeds spoken text in several possible element names.  We try
    each in priority order and fall back to scanning all elements for
    substantial text content (>100 chars).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug("[vlpar_extra] XML parse error: %s", exc)
        return ""

    # Priority element names (localname, case-insensitive)
    PRIORITY = ("sprekertekst", "tekst", "inhoud", "omschrijving", "title", "titel")

    parts: list[str] = []

    # First pass: collect from priority elements
    for el in root.iter():
        local = (el.tag.split("}")[-1] if "}" in el.tag else el.tag).lower()
        if local in PRIORITY:
            raw = (el.text or "").strip()
            if raw:
                cleaned = re.sub(r"<[^>]+>", " ", raw)   # strip embedded HTML
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
                if len(cleaned) > 20:
                    parts.append(cleaned)

    # Second pass: any element with substantial text (>100 chars) not yet captured
    if not parts:
        for el in root.iter():
            raw = (el.text or "").strip()
            if len(raw) > 100:
                cleaned = re.sub(r"<[^>]+>", " ", raw)
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
                if len(cleaned) > 100:
                    parts.append(cleaned)

    text = "\n\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _vlpar_extra_date(root: ET.Element) -> int | None:
    """Try to extract a publication year from a jln XML root element."""
    # Check common date attributes on the root
    for attr in ("datum", "date", "vergaderingDatum", "jaar", "year"):
        val = root.get(attr, "")
        if val:
            m = re.search(r"\b(19|20)\d{2}\b", val)
            if m:
                return int(m.group(0))
    # Scan child elements for date-like text
    for el in root.iter():
        local = (el.tag.split("}")[-1] if "}" in el.tag else el.tag).lower()
        if local in ("datum", "date", "vergaderingdatum", "jaar", "year"):
            val = (el.text or "").strip()
            m = re.search(r"\b(19|20)\d{2}\b", val)
            if m:
                return int(m.group(0))
    # Last resort: scan first 2 KB of raw XML
    snippet = ET.tostring(root, encoding="unicode")[:2000]
    m = re.search(r"\b(19|20)\d{2}\b", snippet)
    return int(m.group(0)) if m else None


def parse_vlpar_extra_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Vlaams Parlement journaallijn XML files downloaded by vlpar_extra acquirer.

    Each jln_*.xml file represents one debate item and may contain one or more
    speaker transcript blocks (<sprekertekst> / <tekst> / <inhoud>).
    Yields one Document per file that contains substantial text.
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for xml_file in sorted(raw_dir.glob("jln_*.xml")):
        try:
            xml_bytes = xml_file.read_bytes()
        except OSError as exc:
            logger.warning("[vlpar_extra] Cannot read %s: %s", xml_file.name, exc)
            skipped += 1
            continue

        text = _vlpar_extra_text(xml_bytes)
        if len(text) < 100:
            logger.debug("[vlpar_extra] %s: insufficient text (%d chars)", xml_file.name, len(text))
            skipped += 1
            continue

        # Extract jln ID from filename for the source URL
        jln_id_str = xml_file.stem.split("_")[1] if "_" in xml_file.stem else xml_file.stem
        source_url = (
            _read_url_sidecar(xml_file)
            or f"https://www.vlaamsparlement.be/nl/journaallijn/{jln_id_str}"
        )

        try:
            root = ET.fromstring(xml_bytes)
            year = _vlpar_extra_date(root)
        except ET.ParseError:
            year = None

        doc_id = make_id("vlpar_extra", jln_id_str)

        meta = DocumentMeta(
            id=doc_id,
            source="vlpar_extra",
            source_url=source_url,
            license="CC-BY",
            publication_year=year,
            country="BE",
            domain="parliamentary",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[vlpar_extra] %d documents yielded, %d skipped", total, skipped)


# ---------------------------------------------------------------------------
# Woogle FOIA — Dutch Government Information Act (Woo/Wob) documents (CC BY 4.0)
# ---------------------------------------------------------------------------

def parse_woogle_foia_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Woogle FOIA dataset CSV files downloaded from DANS Dataverse.

    Reads CSV files in raw_dir/woogle_foia_files/.
    Text columns tried in order: foi_bodyTextOCR, body_text, text, content, bodyText
    Metadata columns: foi_requestId / id, date_decision / date, organisation / publisher

    Source: DANS doi:10.17026/dans-zau-e3rk, CC BY 4.0, ~378M words
    """
    import csv
    import sys as _sys
    csv.field_size_limit(min(2**31 - 1, _sys.maxsize))

    acq_date  = datetime.date.today().isoformat()
    doc_count = 0
    skipped   = 0

    files_dir = raw_dir / "woogle_foia_files"
    if not files_dir.exists():
        # Fallback: CSV files directly in raw_dir
        files_dir = raw_dir

    csv_files = sorted(files_dir.glob("*.csv")) + sorted(files_dir.glob("*.csv.gz"))
    if not csv_files:
        logger.warning("[woogle_foia] No CSV files found in %s", files_dir)
        return

    logger.info("[woogle_foia] Found %d CSV file(s) (incl. gz)", len(csv_files))

    _TEXT_COLS  = ("foi_bodyTextOCR", "body_text", "text", "content", "bodyText")
    _ID_COLS    = ("foi_requestId", "foi_documentId", "id", "document_id")
    _DATE_COLS  = ("date_decision", "date_received", "date", "publication_date")
    _ORG_COLS   = ("organisation", "publisher", "bestuursorgaan", "organization")

    for csv_path in csv_files:
        logger.info("[woogle_foia] Parsing %s", csv_path.name)
        try:
            if csv_path.suffix == ".gz":
                import gzip
                import io as _io
                fh = _io.TextIOWrapper(
                    gzip.open(csv_path, "rb"), encoding="utf-8-sig", errors="replace", newline=""
                )
            else:
                fh = csv_path.open(encoding="utf-8-sig", errors="replace", newline="")
        except OSError as exc:
            logger.warning("[woogle_foia] Cannot open %s: %s", csv_path.name, exc)
            continue

        with fh:
            try:
                reader = csv.DictReader(fh)
                fieldnames = reader.fieldnames or []

                # Identify which columns are present
                text_col = next((c for c in _TEXT_COLS  if c in fieldnames), None)
                id_col   = next((c for c in _ID_COLS    if c in fieldnames), None)
                date_col = next((c for c in _DATE_COLS  if c in fieldnames), None)
                org_col  = next((c for c in _ORG_COLS   if c in fieldnames), None)

                if text_col is None:
                    logger.warning(
                        "[woogle_foia] No recognised text column in %s "
                        "(columns: %s)", csv_path.name, fieldnames[:10]
                    )
                    continue

                for row_num, row in enumerate(reader):
                    text = (row.get(text_col) or "").strip()
                    if len(text) < 200:
                        skipped += 1
                        continue

                    rec_id     = (row.get(id_col)   or "").strip() if id_col   else ""
                    date_raw   = (row.get(date_col)  or "").strip() if date_col  else ""
                    org        = (row.get(org_col)   or "").strip() if org_col   else ""

                    # Extract publication year
                    pub_year: int | None = None
                    if date_raw:
                        m = re.search(r'\b(19|20)\d{2}\b', date_raw)
                        if m:
                            try:
                                pub_year = int(m.group(0))
                            except ValueError:
                                pass

                    doc_id_raw = rec_id or f"{csv_path.stem}_{row_num}"
                    doc_id     = make_id("woogle_foia", doc_id_raw)
                    source_url = (
                        f"https://dans.datastations.nl/dataset.xhtml"
                        f"?persistentId=doi:10.17026/dans-zau-e3rk"
                    )

                    meta = DocumentMeta(
                        id=doc_id,
                        source="woogle_foia",
                        source_url=source_url,
                        license="CC-BY",
                        publication_year=pub_year,
                        country="NL",
                        domain="government",
                        language_variant="NL",
                        acquisition_date=acq_date,
                    )
                    yield Document(text=text, meta=meta)
                    doc_count += 1

            except csv.Error as exc:
                logger.warning("[woogle_foia] CSV error in %s: %s", csv_path.name, exc)
                continue

    logger.info(
        "[woogle_foia] Parsed %d documents (%d rows skipped)", doc_count, skipped
    )


# ---------------------------------------------------------------------------
# Open Data Archieven — EAD finding aids (CC0)
# ---------------------------------------------------------------------------

def parse_open_data_archieven_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Open Data Archieven OAI-PMH harvested finding aids.

    Reads archieven_records.jsonl written by the open_data_archieven acquirer.
    Each record: {id, title, description, date, institution, url}

    Source: harvest.archieven.nl OAI-PMH, CC0
    """
    acq_date  = datetime.date.today().isoformat()
    doc_count = 0
    skipped   = 0

    records_path = raw_dir / "archieven_records.jsonl"
    if not records_path.exists():
        logger.warning("[open_data_archieven] archieven_records.jsonl not found in %s", raw_dir)
        return

    logger.info("[open_data_archieven] Reading %s", records_path.name)

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        description = (rec.get("description") or "").strip()
        title       = (rec.get("title")       or "").strip()
        rec_id      = (rec.get("id")           or "").strip()
        date_raw    = (rec.get("date")         or "").strip()
        source_url  = (rec.get("url")          or "").strip()
        institution = (rec.get("institution")  or "").strip()

        # Build document text: title + description
        parts = []
        if title:
            parts.append(title)
        if description:
            parts.append(description)
        text = "\n\n".join(parts).strip()

        if len(text) < 100:
            skipped += 1
            continue

        # Extract year
        pub_year: int | None = None
        if date_raw:
            m = re.search(r'\b(1[5-9]\d{2}|20[012]\d)\b', date_raw)
            if m:
                try:
                    pub_year = int(m.group(1))
                except ValueError:
                    pass

        if not source_url:
            source_url = "https://www.archieven.nl/"

        record_license = rec.get("license") or "CC0"
        doc_id = make_id("open_data_archieven", rec_id or f"rec_{doc_count}")
        meta   = DocumentMeta(
            id=doc_id,
            source="open_data_archieven",
            source_url=source_url,
            license=record_license,
            publication_year=pub_year,
            country="NL",
            domain="cultural",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        doc_count += 1

    logger.info(
        "[open_data_archieven] Parsed %d documents (%d skipped)", doc_count, skipped
    )



# ---------------------------------------------------------------------------
# Amsterdam Museum  (collection metadata — CC0)
# ---------------------------------------------------------------------------

def parse_amsterdam_museum_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Amsterdam Museum OAI-PMH collection records from amsterdam_museum_records.jsonl.

    Each record: titles + descriptions + materials + techniques joined as document text.
    source="amsterdam_museum", license="CC0", domain="cultural", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "amsterdam_museum_records.jsonl"

    if not records_path.exists():
        logger.warning("[amsterdam_museum] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_id     = str(rec.get("record_id") or "")
        titles     = rec.get("titles") or []
        descs      = rec.get("descriptions") or []
        materials  = rec.get("materials") or []
        techniques = rec.get("techniques") or []
        year       = rec.get("year")
        source_url = str(rec.get("source_url") or "https://collectie.amsterdammuseum.nl/")
        rec_acq    = rec.get("acquisition_date") or acq_date

        parts = []
        if titles:
            parts.append(". ".join(str(t) for t in titles if t))
        if descs:
            parts.append(". ".join(str(d) for d in descs if d))
        if materials:
            parts.append("Materiaal: " + ", ".join(str(m) for m in materials if m))
        if techniques:
            parts.append("Techniek: " + ", ".join(str(t) for t in techniques if t))

        text = "\n\n".join(p for p in parts if p).strip()
        if len(text) < 50:
            skipped += 1
            continue

        if year is not None:
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = None

        safe_id = re.sub(r'[/:\\?*|"<>\s]', '_', rec_id)[:80] or str(total)
        meta = DocumentMeta(
            id=make_id("amsterdam_museum", safe_id),
            source="amsterdam_museum",
            source_url=source_url,
            license="CC0",
            publication_year=year,
            country="NL",
            domain="cultural",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[amsterdam_museum] Parsed %d records (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Rijksmuseum van Oudheden  (collection metadata — CC0)
# ---------------------------------------------------------------------------

def parse_rmo_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Rijksmuseum van Oudheden OAI-PMH collection records from rmo_records.jsonl.

    source="rmo", license="CC0", domain="cultural", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "rmo_records.jsonl"

    if not records_path.exists():
        logger.warning("[rmo] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_id     = str(rec.get("record_id") or "")
        titles     = rec.get("titles") or []
        descs      = rec.get("descriptions") or []
        materials  = rec.get("materials") or []
        techniques = rec.get("techniques") or []
        year       = rec.get("year")
        source_url = str(rec.get("source_url") or "https://www.rmo.nl/")
        rec_acq    = rec.get("acquisition_date") or acq_date

        parts = []
        if titles:
            parts.append(". ".join(str(t) for t in titles if t))
        if descs:
            parts.append(". ".join(str(d) for d in descs if d))
        if materials:
            parts.append("Materiaal: " + ", ".join(str(m) for m in materials if m))
        if techniques:
            parts.append("Techniek: " + ", ".join(str(t) for t in techniques if t))

        text = "\n\n".join(p for p in parts if p).strip()
        if len(text) < 50:
            skipped += 1
            continue

        if year is not None:
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = None

        safe_id = re.sub(r'[/:\\?*|"<>\s]', '_', rec_id)[:80] or str(total)
        meta = DocumentMeta(
            id=make_id("rmo", safe_id),
            source="rmo",
            source_url=source_url,
            license="CC0",
            publication_year=year,
            country="NL",
            domain="cultural",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[rmo] Parsed %d records (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# RVO webcontent  (government business/sustainability info — CC0/PD)
# ---------------------------------------------------------------------------

def parse_rvo_webcontent_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse RVO webcontent articles from rvo_webcontent_articles.jsonl.

    source="rvo_webcontent", license="CC0", domain="government", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "rvo_webcontent_articles.jsonl"

    if not records_path.exists():
        logger.warning("[rvo_webcontent] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        title      = str(rec.get("title") or "").strip()
        body       = str(rec.get("body") or rec.get("text") or "").strip()
        url        = str(rec.get("url") or rec.get("source_url") or "https://www.rvo.nl/")
        date_str   = str(rec.get("date") or "")
        rec_acq    = rec.get("acquisition_date") or acq_date
        rec_id     = str(rec.get("id") or re.sub(r'[^a-z0-9]', '_', url.lower())[:60])

        text = f"{title}\n\n{body}".strip() if title and body else (title or body)
        if len(text) < 100:
            skipped += 1
            continue

        pub_year: int | None = None
        m = re.search(r'\b(19|20)\d{2}\b', date_str)
        if m:
            pub_year = int(m.group(0))

        meta = DocumentMeta(
            id=make_id("rvo_webcontent", rec_id),
            source="rvo_webcontent",
            source_url=url,
            license="CC0",
            publication_year=pub_year,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[rvo_webcontent] Parsed %d articles (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# RIVM LCI-richtlijnen  (infectious disease guidelines — PD)
# ---------------------------------------------------------------------------

def parse_rivm_lci_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse RIVM LCI guidelines, draaiboeken, and factsheets from rivm_lci_guidelines.jsonl.

    source="rivm_lci", license="PD", domain="science", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "rivm_lci_guidelines.jsonl"

    if not records_path.exists():
        logger.warning("[rivm_lci] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        title        = str(rec.get("title") or "").strip()
        body         = str(rec.get("body") or "").strip()
        url          = str(rec.get("url") or "https://lci.rivm.nl/")
        date_str     = str(rec.get("date") or "")
        section_type = str(rec.get("section_type") or "richtlijn")
        rec_id       = str(rec.get("id") or re.sub(r'[^a-z0-9]', '_', url.lower())[:60])
        rec_acq      = rec.get("acquisition_date") or acq_date

        text = f"{title}\n\n{body}".strip() if title and body else (title or body)
        if len(text) < 200:
            skipped += 1
            continue

        pub_year: int | None = None
        m = re.search(r'\b(19|20)\d{2}\b', date_str)
        if m:
            pub_year = int(m.group(0))

        meta = DocumentMeta(
            id=make_id("rivm_lci", rec_id),
            source="rivm_lci",
            source_url=url,
            license="PD",
            publication_year=pub_year,
            country="NL",
            domain="science",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[rivm_lci] Parsed %d guidelines (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# WODC  (justice/criminology research reports — CC0)
# ---------------------------------------------------------------------------

def parse_wodc_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse WODC OAI-PMH records from wodc_records.jsonl.

    source="wodc", license="CC0", domain="government", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "wodc_records.jsonl"

    if not records_path.exists():
        logger.warning("[wodc] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        text = rec.get("text", "").strip()
        if len(text) < 80:
            skipped += 1
            continue

        title = rec.get("title", "")
        source_url = rec.get("source_url", "")
        year_val = rec.get("year")
        year: int | None = None
        if year_val is not None:
            try:
                year = int(year_val)
            except (TypeError, ValueError):
                pass
        rec_acq = rec.get("acquisition_date") or acq_date
        lic = rec.get("license", "CC0")

        doc_id = re.sub(r'[/:\\?*|"<>\s]', '_', source_url)[-60:] or str(total)
        meta = DocumentMeta(
            id=make_id("wodc", doc_id),
            source="wodc",
            source_url=source_url,
            license=lic,
            publication_year=year,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[wodc] Parsed %d records (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# SCP  (social/cultural policy research — CC0)
# ---------------------------------------------------------------------------

def parse_scp_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse SCP (Sociaal en Cultureel Planbureau) records from scp_records.jsonl.

    source="scp", license="CC0", domain="government", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "scp_records.jsonl"

    if not records_path.exists():
        logger.warning("[scp] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        text = rec.get("text", "").strip()
        if len(text) < 100:
            skipped += 1
            continue

        title = rec.get("title", "")
        source_url = rec.get("source_url", "")
        year_val = rec.get("year")
        year: int | None = None
        if year_val is not None:
            try:
                year = int(year_val)
            except (TypeError, ValueError):
                pass
        rec_acq = rec.get("acquisition_date") or acq_date

        doc_id = re.sub(r'[/:\\?*|"<>\s]', '_', source_url)[-60:] or str(total)
        meta = DocumentMeta(
            id=make_id("scp", doc_id),
            source="scp",
            source_url=source_url,
            license="CC0",
            publication_year=year,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=rec_acq,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[scp] Parsed %d records (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# CBS longreads  (statistics long-form articles — CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_cbs_longreads_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse CBS (Centraal Bureau voor de Statistiek) longread articles from
    cbs_longreads.jsonl.

    source="cbs_longreads", license="CC-BY", domain="government", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "cbs_longreads.jsonl"

    if not records_path.exists():
        logger.warning("[cbs_longreads] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        text = rec.get("text", "").strip()
        if len(text) < 200:
            skipped += 1
            continue

        source_url = rec.get("source_url", "")
        year_val = rec.get("year")
        year: int | None = None
        if year_val is not None:
            try:
                year = int(year_val)
            except (TypeError, ValueError):
                pass
        rec_acq = rec.get("acquisition_date") or acq_date

        # Extract ledger entries
        ledger_data = rec.get("ledger", [])
        ledger = [ProvenanceEntry.from_dict(e) for e in ledger_data]

        doc_id = re.sub(r'[/:\\?*|"<>\s]', '_', source_url)[-60:] or str(total)
        meta = DocumentMeta(
            id=make_id("cbs_longreads", doc_id),
            source="cbs_longreads",
            source_url=source_url,
            license="CC-BY",
            publication_year=year,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=rec_acq,
            ledger=ledger,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[cbs_longreads] Parsed %d articles (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# SLO Curriculum  (Dutch curriculum standards — CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_slo_curriculum_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse SLO curriculum standards from slo_curriculum.jsonl.

    source="slo_curriculum", license="CC-BY", domain="education", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "slo_curriculum.jsonl"

    if not records_path.exists():
        logger.warning("[slo_curriculum] %s not found", records_path)
        return

    total = 0
    skipped = 0

    # Batch items into documents (up to 50 per doc to avoid tiny docs)
    batch: list[str] = []
    batch_ids: list[str] = []
    batch_url = ""

    def _flush_batch() -> Iterator[Document]:
        nonlocal batch, batch_ids, batch_url
        if not batch:
            return
        text = "\n\n".join(batch)
        if len(text) < 80:
            batch = []
            batch_ids = []
            return
        doc_id = "_".join(batch_ids[:3])
        meta = DocumentMeta(
            id=make_id("slo_curriculum", doc_id),
            source="slo_curriculum",
            source_url=batch_url or "https://opendata.slo.nl/curriculum/api/v1/",
            license="CC-BY",
            publication_year=None,
            country="NL",
            domain="education",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        batch = []
        batch_ids = []

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        text = rec.get("text", "").strip()
        if len(text) < 30:
            skipped += 1
            continue

        item_id = str(rec.get("item_id", ""))
        title = rec.get("title", "")
        entry = f"{title}: {text}" if title and title not in text else text
        batch.append(entry)
        batch_ids.append(item_id or str(total))
        batch_url = rec.get("source_url", "")
        total += 1

        if len(batch) >= 50:
            yield from _flush_batch()

    yield from _flush_batch()

    logger.info("[slo_curriculum] Parsed %d items (%d skipped) into batched docs", total, skipped)


# ---------------------------------------------------------------------------
# GLOBALISE VOC  (Early Modern Dutch HTR transcriptions — CC0)
# ---------------------------------------------------------------------------

def parse_globalise_voc_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse GLOBALISE VOC inventory transcriptions from globalise_voc_records.jsonl.

    Each record = one VOC inventory (100-1000 pages of Early Modern Dutch).
    source="globalise_voc", license="CC0", domain="historical", country="NL".
    Dates roughly 1610-1796.
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "globalise_voc_records.jsonl"

    if not records_path.exists():
        logger.warning("[globalise_voc] %s not found", records_path)
        return

    total = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        text = rec.get("text", "").strip()
        if len(text) < 500:
            skipped += 1
            continue

        inv_id = str(rec.get("inventory_id", ""))
        source_url = rec.get("source_url", "")
        rec_acq = rec.get("acquisition_date") or acq_date

        # VOC records: 17th-18th century, use approximate midpoint year
        # Actual range 1610-1796, no per-inventory year available
        meta = DocumentMeta(
            id=make_id("globalise_voc", inv_id),
            source="globalise_voc",
            source_url=source_url,
            license="CC0",
            publication_year=1700,   # approximate midpoint
            country="NL",
            domain="historical",
            language_variant="NL",
            acquisition_date=rec_acq,
            historical_flag=True,
            language_period="early_modern",
            content_warnings=["colonial_terminology", "archaic_racial_language"],
        )
        yield Document(text=text, meta=meta)
        total += 1


# ---------------------------------------------------------------------------
# EUPDCorp  (European Parliament debate speeches 1999–2024 — CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_eupdcorp_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse EUPDCorp Dutch EP plenary debate speeches from eupdcorp_nl_speeches.jsonl.

    source="eupdcorp", license="CC-BY", domain="parliamentary", country="EU".
    Batches 25 speeches per Document to keep doc sizes manageable.
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "eupdcorp_nl_speeches.jsonl"

    if not records_path.exists():
        logger.warning("[eupdcorp] eupdcorp_nl_speeches.jsonl not found in %s", raw_dir)
        return

    total   = 0
    skipped = 0
    batch: list[str] = []
    batch_meta: list[dict] = []

    def _flush() -> Iterator[Document]:
        if not batch:
            return
        text = "\n\n".join(batch)
        first = batch_meta[0] if batch_meta else {}
        speech_ids = ",".join(m.get("speech_id", "") for m in batch_meta if m.get("speech_id"))
        meta = DocumentMeta(
            id=make_id("eupdcorp", speech_ids or str(total)),
            source="eupdcorp",
            source_url="https://zenodo.org/records/15056399",
            license="CC-BY",
            publication_year=int(first.get("date", "0")[:4]) if first.get("date", "")[:4].isdigit() else None,
            country="EU",
            domain="parliamentary",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = rec.get("text", "").strip()
        if len(text) < 20:
            skipped += 1
            continue

        speaker = rec.get("speaker", "")
        date    = rec.get("date", "")
        prefix  = f"[{date} — {speaker}] " if speaker or date else ""
        batch.append(prefix + text)
        batch_meta.append(rec)
        total += 1

        if len(batch) >= 25:
            yield from _flush()
            batch.clear()
            batch_meta.clear()

    yield from _flush()
    logger.info("[eupdcorp] Parsed %d speeches (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Wikidata Lexemes  (Dutch/Frisian/Afrikaans/Limburgish lexemes — CC0)
# ---------------------------------------------------------------------------

def parse_wikidata_lexemes_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Wikidata lexeme entries from wikidata_lexemes_{lang}_{N}.jsonl files.

    Each file contains lexeme entries: lemma + gloss (definition).
    Batches 100 entries per Document.
    source="wikidata_lexemes", license="CC0", domain="encyclopedic".
    """
    acq_date = datetime.date.today().isoformat()

    _VARIANT_MAP = {"nl": "NL", "fy": "FY", "af": "AF", "li": "LI"}
    _COUNTRY_MAP = {"nl": "NL", "fy": "NL", "af": "ZA", "li": "NL"}

    jsonl_files = sorted(raw_dir.glob("wikidata_lexemes_*.jsonl"))
    if not jsonl_files:
        logger.warning("[wikidata_lexemes] No wikidata_lexemes_*.jsonl found in %s", raw_dir)
        return

    for jsonl_file in jsonl_files:
        # Parse language code from filename: wikidata_lexemes_{code}_{N}.jsonl
        parts = jsonl_file.stem.split("_")
        lang_code = parts[2] if len(parts) >= 3 else "nl"
        variant   = _VARIANT_MAP.get(lang_code, "NL")
        country   = _COUNTRY_MAP.get(lang_code, "NL")

        batch: list[str] = []
        batch_ids: list[str] = []
        total = 0
        skipped = 0

        def _flush_batch() -> Iterator[Document]:
            if not batch:
                return
            text = "\n".join(batch)
            meta = DocumentMeta(
                id=make_id("wikidata_lexemes", f"{lang_code}_{batch_ids[0]}"),
                source="wikidata_lexemes",
                source_url="https://www.wikidata.org/wiki/Wikidata:Lexicographical_data",
                license="CC0",
                publication_year=None,
                country=country,
                domain="encyclopedic",
                language_variant=variant,
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)

        for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            lemma = rec.get("lemma", "").strip()
            gloss = rec.get("gloss", "").strip()
            if not lemma and not gloss:
                skipped += 1
                continue

            entry = f"{lemma}: {gloss}" if lemma and gloss else (lemma or gloss)
            batch.append(entry)
            batch_ids.append(rec.get("lexeme_id", str(total)))
            total += 1

            if len(batch) >= 100:
                yield from _flush_batch()
                batch.clear()
                batch_ids.clear()

        yield from _flush_batch()
        logger.info("[wikidata_lexemes] %s: %d entries (%d skipped)", jsonl_file.name, total, skipped)


# ---------------------------------------------------------------------------
# Delpher Magazines  (pre-1880 Dutch periodicals — PD/CC0)
# ---------------------------------------------------------------------------

def parse_delpher_magazines_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse pre-1880 Dutch magazine articles from delpher_magazines_records.jsonl.

    Each record = one article/page.  Yields one Document per article.
    source="delpher_magazines", license="PD", domain="news", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "delpher_magazines_records.jsonl"

    if not records_path.exists():
        logger.warning("[delpher_magazines] delpher_magazines_records.jsonl not found in %s", raw_dir)
        return

    total   = 0
    skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = rec.get("text", "").strip()
        if len(text) < 30:
            skipped += 1
            continue

        identifier = rec.get("identifier", "")
        year       = rec.get("year")
        title      = rec.get("title", "")

        # Prepend title if not already in text
        if title and title not in text:
            text = f"{title}\n\n{text}"

        # Assign language_period based on year
        if year and year < 1800:
            lang_period = "early_modern"
        elif year and year < 1900:
            lang_period = "19th_century"
        else:
            lang_period = None

        meta = DocumentMeta(
            id=make_id("delpher_magazines", identifier or str(total)),
            source="delpher_magazines",
            source_url=f"https://resolver.kb.nl/resolve?urn={identifier}" if identifier else
                       "https://www.kb.nl/en/research-find/datasets/delpher-magazines",
            license="PD",
            publication_year=year,
            country="NL",
            domain="news",
            language_variant="NL",
            acquisition_date=acq_date,
            historical_flag=True,
            language_period=lang_period,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[delpher_magazines] Parsed %d articles (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# CPB / PBL  (Dutch policy institute reports — CC0)
# ---------------------------------------------------------------------------

def parse_cpb_pbl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse CPB and PBL policy publication records from cpb_pbl_{key}_records.jsonl.

    source="cpb_pbl", license="CC0", domain="academic", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for jsonl_file in sorted(raw_dir.glob("cpb_pbl_*_records.jsonl")):
        institute = jsonl_file.stem.split("_")[2] if len(jsonl_file.stem.split("_")) > 2 else "cpb"
        base_url = f"https://www.{institute}.nl"

        for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            text = rec.get("text", "").strip()
            if len(text) < 50:
                skipped += 1
                continue

            pub_id = str(rec.get("pub_id", ""))
            year   = rec.get("year")
            url    = rec.get("url", "") or base_url

            meta = DocumentMeta(
                id=make_id("cpb_pbl", f"{institute}_{pub_id}"),
                source="cpb_pbl",
                source_url=url if url.startswith("http") else base_url + url,
                license="CC0",
                publication_year=int(year) if year else None,
                country="NL",
                domain="academic",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1

    if total == 0 and skipped == 0:
        logger.warning("[cpb_pbl] No cpb_pbl_*_records.jsonl found in %s", raw_dir)
    else:
        logger.info("[cpb_pbl] Parsed %d publications (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Movisie  (Dutch social-policy publications — CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_movisie_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Movisie publications from movisie_publications.jsonl.

    source="movisie", license="CC-BY", domain="policy", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    jsonl_file = raw_dir / "movisie_publications.jsonl"
    # Support nested raw dir
    if not jsonl_file.exists():
        candidates = list(raw_dir.rglob("movisie_publications.jsonl"))
        jsonl_file = candidates[0] if candidates else jsonl_file

    if not jsonl_file.exists():
        logger.warning("[movisie] movisie_publications.jsonl not found in %s", raw_dir)
        return

    for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = rec.get("text", "").strip()
        if len(text) < 100:
            skipped += 1
            continue

        url    = rec.get("url", "")
        title  = rec.get("title", "")
        date   = rec.get("date", "")
        year   = int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None

        meta = DocumentMeta(
            id=make_id("movisie", url or title),
            source="movisie",
            source_url=url,
            license="CC-BY",
            publication_year=year,
            country="NL",
            domain="policy",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[movisie] Parsed %d publications (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# ECHR NL  (European Court of Human Rights Dutch decisions — PD)
# ---------------------------------------------------------------------------

def parse_echr_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch ECHR decisions from echr_nl_decisions.jsonl.

    source="echr_nl", license="PD", domain="legal", country="EU".
    """
    acq_date = datetime.date.today().isoformat()
    records_path = raw_dir / "echr_nl_decisions.jsonl"

    if not records_path.exists():
        logger.warning("[echr_nl] echr_nl_decisions.jsonl not found in %s", raw_dir)
        return

    total = skipped = 0

    for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = rec.get("text", "").strip()
        if len(text) < 50:
            skipped += 1
            continue

        item_id  = rec.get("item_id", "")
        ecli     = rec.get("ecli", "")
        year     = rec.get("year")
        docname  = rec.get("docname", "")
        src_url  = f"https://hudoc.echr.coe.int/eng#{{{json.dumps({'itemid': [item_id]})}}}" if item_id else \
                   "https://hudoc.echr.coe.int/"

        # Prepend case name if not in text
        if docname and docname not in text:
            text = f"{docname}\n\n{text}"

        meta = DocumentMeta(
            id=make_id("echr_nl", item_id or ecli or str(total)),
            source="echr_nl",
            source_url=src_url,
            license="PD",
            publication_year=int(year) if year else None,
            country="EU",
            domain="legal",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[echr_nl] Parsed %d decisions (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Open Beelden  (Nederlands Instituut voor Beeld en Geluid — PDM 1.0)
# ---------------------------------------------------------------------------

def parse_open_beelden_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Open Beelden Dutch audiovisual heritage metadata.

    Reads open_beelden_records.jsonl.
    Each record: {id, title, description, subjects, coverage, rights, date}.

    source="open_beelden", license="PD", domain="audiovisual_heritage", country="NL".
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    jsonl_file = raw_dir / "open_beelden_records.jsonl"
    if not jsonl_file.exists():
        candidates = list(raw_dir.rglob("open_beelden_records.jsonl"))
        jsonl_file = candidates[0] if candidates else jsonl_file

    if not jsonl_file.exists():
        logger.warning("[open_beelden] open_beelden_records.jsonl not found in %s", raw_dir)
        return

    for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        title       = (rec.get("title") or "").strip()
        description = (rec.get("description") or "").strip()
        subjects    = rec.get("subjects") or []
        date        = (rec.get("date") or "").strip()
        record_id   = (rec.get("id") or "").strip()

        # Build text: title + description; skip if too short
        parts = []
        if title:
            parts.append(title)
        if description:
            parts.append(description)
        if subjects:
            parts.append("Onderwerpen: " + ", ".join(subjects))

        text = "\n\n".join(parts)
        if len(text) < 50:
            skipped += 1
            continue

        year: int | None = None
        if date:
            import re as _re
            m = _re.search(r'\b(1[89]\d{2}|20[012]\d)\b', date)
            if m:
                year = int(m.group(0))

        meta = DocumentMeta(
            id=make_id("open_beelden", record_id or title),
            source="open_beelden",
            source_url=f"https://www.openbeelden.nl/media/{record_id.split(':')[-1]}" if record_id else "",
            license="PD",
            publication_year=year,
            country="NL",
            domain="audiovisual_heritage",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[open_beelden] Parsed %d records (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Martijn Trilogy Manuscripts (Zenodo 10409449, CC-BY-SA)
# ---------------------------------------------------------------------------

def parse_martijn_trilogy_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse plain-text manuscript witnesses of the Martijn Trilogy.

    Each .txt file in plain_txt/ is one manuscript witness (17 total).
    Files contain verse lines with light markup (strophe markers, folio refs).
    We strip markup characters and yield one Document per witness.
    """
    acq_date  = datetime.date.today().isoformat()
    txt_dir   = raw_dir / "plain_txt"
    if not txt_dir.exists():
        logger.warning("[martijn_trilogy] plain_txt/ not found in %s", raw_dir)
        return

    # Strip custom markup: [fXr/v], {strophe}, <damage>, @abbreviation, etc.
    _MARKUP = re.compile(r"[\[\]{}<>@|\\]|[fF]\d+[rv]")

    total = 0
    for txt_file in sorted(txt_dir.glob("*.txt")):
        raw = txt_file.read_text(encoding="utf-8", errors="replace")
        # Strip markup, normalize whitespace
        text = _MARKUP.sub(" ", raw)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 200:
            continue

        doc_id = make_id("martijn_trilogy", txt_file.stem)
        meta   = DocumentMeta(
            id=doc_id,
            source="martijn_trilogy",
            source_url="https://zenodo.org/records/10409449",
            license="CC-BY-SA",
            publication_year=None,   # 13th-century manuscripts
            country="NL",
            domain="literary",
            language_variant="NL",
            historical_flag=True,
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[martijn_trilogy] Parsed %d manuscript witnesses", total)


# ---------------------------------------------------------------------------
# Herne Manuscripts — PAGEXML (Zenodo 10005366, CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_herne_manuscripts_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse PAGEXML transcriptions of 18 Middle Dutch manuscripts from Herne.

    Each manuscript is a directory of PAGEXML files (one per folio page).
    We extract <Unicode> text content from <TextLine> elements and concatenate
    all folios into one Document per manuscript.
    """
    import xml.etree.ElementTree as ET

    acq_date   = datetime.date.today().isoformat()
    pagexml_dir = raw_dir / "pagexml"
    if not pagexml_dir.exists():
        logger.warning("[herne_manuscripts] pagexml/ not found in %s", raw_dir)
        return

    # PAGE XML namespace (common variants)
    _NS_PATTERNS = [
        "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15",
        "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15",
        "http://schema.primaresearch.org/PAGE/gts/pagecontent/2010-03-19",
    ]

    def _extract_text(xml_path: Path) -> str:
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except ET.ParseError:
            return ""
        # Try known namespaces, then strip namespace as fallback
        lines: list[str] = []
        for ns in _NS_PATTERNS:
            for tl in root.iter(f"{{{ns}}}TextLine"):
                for te in tl.iter(f"{{{ns}}}TextEquiv"):
                    for uni in te.iter(f"{{{ns}}}Unicode"):
                        if uni.text and uni.text.strip():
                            lines.append(uni.text.strip())
                            break   # first TextEquiv per TextLine
            if lines:
                break
        if not lines:
            # Namespace-stripped fallback
            for tl in root.iter():
                if tl.tag.endswith("TextLine"):
                    for te in tl.iter():
                        if te.tag.endswith("Unicode") and te.text and te.text.strip():
                            lines.append(te.text.strip())
                            break
        return "\n".join(lines)

    total = 0
    for ms_dir in sorted(pagexml_dir.iterdir()):
        if not ms_dir.is_dir():
            continue
        folio_texts: list[str] = []
        for xml_file in sorted(ms_dir.glob("*.xml")):
            t = _extract_text(xml_file)
            if t:
                folio_texts.append(t)

        if not folio_texts:
            continue

        text = "\n\n".join(folio_texts).strip()
        if len(text) < 200:
            continue

        doc_id = make_id("herne_manuscripts", ms_dir.name)
        meta   = DocumentMeta(
            id=doc_id,
            source="herne_manuscripts",
            source_url="https://zenodo.org/records/10005366",
            license="CC-BY",
            publication_year=None,   # 14th-century manuscripts
            country="BE",
            domain="literary",
            language_variant="NL",
            historical_flag=True,
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[herne_manuscripts] Parsed %d manuscripts", total)


# ---------------------------------------------------------------------------
# Autshumato Monolingual Afrikaans Corpus (SADiLaR, CC-BY 4.0)
# ---------------------------------------------------------------------------

def parse_autshumato_af_mono_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Autshumato Monolingual Afrikaans Corpus.

    Single large UTF-8 text file, one segment per line.
    We batch 500 lines per Document to match the pipeline's document granularity.
    """
    acq_date = datetime.date.today().isoformat()
    txt_dir  = raw_dir / "text"
    if not txt_dir.exists():
        logger.warning("[autshumato_af_mono] text/ not found in %s", raw_dir)
        return

    BATCH = 500
    total = 0

    for txt_file in sorted(txt_dir.glob("*.txt")):
        lines: list[str] = []
        batch_n = 0
        for raw_line in txt_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lines.append(line)
            if len(lines) >= BATCH:
                text = "\n".join(lines)
                doc_id = make_id("autshumato_af_mono", f"{txt_file.stem}_{batch_n:06d}")
                meta   = DocumentMeta(
                    id=doc_id,
                    source="autshumato_af_mono",
                    source_url="https://hdl.handle.net/20.500.12185/580",
                    license="CC-BY",
                    publication_year=None,
                    country="ZA",
                    domain="government",
                    language_variant="AF",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                lines = []
                batch_n += 1
                total += 1

        # Remainder
        if lines:
            text = "\n".join(lines)
            if len(text) >= 100:
                doc_id = make_id("autshumato_af_mono", f"{txt_file.stem}_{batch_n:06d}")
                meta   = DocumentMeta(
                    id=doc_id,
                    source="autshumato_af_mono",
                    source_url="https://hdl.handle.net/20.500.12185/580",
                    license="CC-BY",
                    publication_year=None,
                    country="ZA",
                    domain="government",
                    language_variant="AF",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                total += 1

    logger.info("[autshumato_af_mono] Parsed %d batched documents", total)


# ---------------------------------------------------------------------------
# NCHLT Afrikaans Speech Corpus — transcription prompts (SADiLaR, CC-BY 3.0)
# ---------------------------------------------------------------------------

def parse_nchlt_af_speech_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse NCHLT Afrikaans speech transcription prompt texts.

    The acquirer extracts <orth> prompt texts from the XML transcription files
    into per-speaker .txt files under prompts/. Each speaker file becomes one
    Document (batched at 500 prompts per doc if large).
    """
    acq_date    = datetime.date.today().isoformat()
    prompts_dir = raw_dir / "prompts"
    if not prompts_dir.exists():
        logger.warning("[nchlt_af_speech] prompts/ not found in %s", raw_dir)
        return

    BATCH = 500
    total = 0

    for sub in sorted(prompts_dir.iterdir()):
        if not sub.is_dir():
            continue
        for txt_file in sorted(sub.glob("*.txt")):
            lines = [
                l.strip()
                for l in txt_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if l.strip()
            ]
            if not lines:
                continue

            for i in range(0, max(1, len(lines)), BATCH):
                chunk = lines[i : i + BATCH]
                text  = "\n".join(chunk)
                if len(text) < 50:
                    continue
                batch_id = i // BATCH
                doc_id   = make_id("nchlt_af_speech", f"{sub.name}_{txt_file.stem}_{batch_id:04d}")
                meta     = DocumentMeta(
                    id=doc_id,
                    source="nchlt_af_speech",
                    source_url="https://hdl.handle.net/20.500.12185/280",
                    license="CC-BY",
                    publication_year=None,
                    country="ZA",
                    domain="speech",
                    language_variant="AF",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
                total += 1

    logger.info("[nchlt_af_speech] Parsed %d prompt batches", total)


# ---------------------------------------------------------------------------
# VariaNTS spoken Dutch corpus  (Zenodo 3932039, CC BY 4.0)
# ---------------------------------------------------------------------------

# VariaNTS transcription files may be tab-separated (word\tphoneme\t…) or
# plain text. We extract the first column (orthographic word form) and
# reassemble into sentence-level text blocks.
_TSV_SEP = re.compile(r"\t")


def _variants_text_from_file(path: Path) -> str | None:
    """Extract orthographic text from a VariaNTS transcription file.

    Handles:
    - Plain .txt files (one sentence/word per line)
    - Tab-separated files (first column is the orthographic form)
    - CSV files (first column after header)
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    lines = raw.splitlines()
    if not lines:
        return None

    # Detect TSV/CSV by presence of tabs or commas in first non-empty line
    first = next((l for l in lines if l.strip()), "")
    if "\t" in first:
        # Tab-separated: take first column, skip header if it looks like one
        words = []
        for line in lines:
            if not line.strip():
                continue
            col = _TSV_SEP.split(line)[0].strip()
            # Skip header rows (all-alpha short strings like "word", "orth")
            if col.lower() in {"word", "orth", "orthographic", "token", "transcription", "stimulus"}:
                continue
            if col:
                words.append(col)
        return " ".join(words) if words else None

    # Plain text: join non-empty lines
    text = "\n".join(l.rstrip() for l in lines if l.strip())
    return text if text else None


def parse_variants_corpus_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse VariaNTS spoken Dutch corpus transcription files.

    The acquirer downloads the Zenodo record and extracts all non-audio files
    into a transcriptions/ subdirectory. Each file (plain text, TSV, CSV)
    yields one Document with the orthographic text content.
    """
    acq_date   = datetime.date.today().isoformat()
    texts_dir  = raw_dir / "transcriptions"
    if not texts_dir.exists():
        logger.warning("[variants_corpus] transcriptions/ not found in %s", raw_dir)
        return

    _TEXT_EXT = {".txt", ".csv", ".tsv", ".textgrid", ".cha", ".eaf", ".xml"}
    total = 0

    for path in sorted(texts_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _TEXT_EXT:
            continue

        text = _variants_text_from_file(path)
        if not text or len(text) < 30:
            continue

        doc_id = make_id("variants_corpus", path.stem)
        meta = DocumentMeta(
            id=doc_id,
            source="variants_corpus",
            source_url="https://zenodo.org/records/3932039",
            license="CC-BY",
            publication_year=2020,
            country="NL",
            domain="speech",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[variants_corpus] Parsed %d transcription files", total)


# ---------------------------------------------------------------------------
# NCHLT Afrikaans NER annotated corpus  (SADiLaR, CC BY 2.5 ZA)
# ---------------------------------------------------------------------------

# The corpus is in CoNLL-like format: one token per line, columns separated by
# tabs or spaces. The first column is the word token; subsequent columns carry
# POS and NE tags. Blank lines separate sentences.
#
# Example:
#   Die  NNP  B-ORG
#   Suid-Afrikaanse  JJ  I-ORG
#   Polisiediens  NNP  I-ORG
#   het  VBD  O
#
# We reconstruct running Afrikaans text from the token column.

_CONLL_WS = re.compile(r"[ \t]+")


def _nchlt_ner_text_from_file(path: Path) -> str | None:
    """Extract token text from a CoNLL-format NER annotation file."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    sentences: list[str] = []
    current:   list[str] = []

    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            if current:
                sentences.append(" ".join(current))
                current = []
            continue

        # Skip comment/metadata lines
        if line.startswith("#") or line.startswith("-DOCSTART-"):
            continue

        parts = _CONLL_WS.split(line)
        token = parts[0].strip()
        if token:
            current.append(token)

    if current:
        sentences.append(" ".join(current))

    text = "\n".join(sentences)
    return text if len(text) >= 30 else None


def parse_nchlt_af_ner_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse NCHLT Afrikaans NER annotated corpus, extracting plain text.

    The acquirer downloads the SADiLaR ZIP and extracts files into corpus/.
    We parse each annotation file (CoNLL-like format), strip NE/POS tags,
    and yield the reconstructed Afrikaans token sequences as Documents.
    """
    acq_date   = datetime.date.today().isoformat()
    corpus_dir = raw_dir / "corpus"
    if not corpus_dir.exists():
        logger.warning("[nchlt_af_ner] corpus/ not found in %s", raw_dir)
        return

    # CoNLL files may have various extensions; also catch .txt files
    _ANN_EXT = {".conll", ".conllu", ".tsv", ".txt", ".ann", ""}
    total = 0
    BATCH = 200  # sentences per Document to keep doc size reasonable

    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _ANN_EXT:
            continue

        text = _nchlt_ner_text_from_file(path)
        if not text or len(text) < 30:
            continue

        # Split into batches of BATCH sentences to avoid oversized documents
        sents = [s for s in text.splitlines() if s.strip()]
        for i in range(0, max(1, len(sents)), BATCH):
            chunk = "\n".join(sents[i : i + BATCH])
            if len(chunk) < 30:
                continue
            batch_id = i // BATCH
            doc_id = make_id("nchlt_af_ner", f"{path.stem}_{batch_id:04d}")
            meta = DocumentMeta(
                id=doc_id,
                source="nchlt_af_ner",
                source_url="https://hdl.handle.net/20.500.12185/392",
                license="CC-BY",
                publication_year=None,
                country="ZA",
                domain="government",
                language_variant="AF",
                acquisition_date=acq_date,
            )
            yield Document(text=chunk, meta=meta)
            total += 1

    logger.info("[nchlt_af_ner] Parsed %d batches", total)


# ---------------------------------------------------------------------------
# PUC — Publieke UitvoeringsCommunicatie (CC0, PDF-based)
# ---------------------------------------------------------------------------

def parse_puc_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse PUC uitvoeringsinformatie PDFs (CC0).

    raw_dir contains {identifier}_{versie}.pdf files downloaded from
    puc.overheid.nl.  Text is extracted via pdfminer.
    Metadata is derived from the filename: PUC_{id}_{ver}_{versie}.pdf
    """
    from pipeline.parse.pdf_text import extract_text as _pdf_text

    acq_date = datetime.date.today().isoformat()
    _fname_re = re.compile(r"^(PUC_\d+_\d+)_(\d+)\.pdf$", re.IGNORECASE)
    total = 0

    for pdf_file in sorted(raw_dir.glob("PUC_*.pdf")):
        m = _fname_re.match(pdf_file.name)
        if not m:
            continue

        identifier = m.group(1)
        versie     = m.group(2)

        text = _pdf_text(pdf_file)
        if not text or len(text) < 100:
            continue

        source_url = (
            f"https://puc.overheid.nl/PUC/Handlers/PDF.ashx"
            f"?documentIdentifier={identifier}&versie={versie}"
            f"&pad=../PDF/{identifier}_{versie}.pdf"
        )

        # Extract year from text (publication date usually in first 200 chars)
        year_m = re.search(r"\b(20\d{2}|19\d{2})\b", text[:500])
        pub_year = int(year_m.group(0)) if year_m else None

        meta = DocumentMeta(
            id=make_id("puc", f"{identifier}_{versie}"),
            source="puc",
            source_url=source_url,
            license="CC0",
            publication_year=pub_year,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[puc] Parsed %d documents", total)


# ---------------------------------------------------------------------------
# Wikiwijs — open educational resources (CC0 / CC-BY / CC-BY-SA)
# ---------------------------------------------------------------------------

_WIKIWIJS_ALLOWED_LICENSES = {"CC0", "CC-BY", "CC-BY-SA"}

# Common Dutch function words used to verify page language (wikiwijs only)
_WIKIWIJS_NL_FUNCTION_WORDS = frozenset(
    "de het een van en in is dat zijn op aan met voor niet worden"
    " hebben worden door naar ook bij maar wel meer als nog dan".split()
)


def _wikiwijs_extract_text(html_bytes: bytes) -> str:
    """
    Extract readable text from a Wikiwijs arrangement HTML page.

    Keeps: <title>, <h1>–<h4>, <p>, <li> content.
    Strips: nav, header, footer, scripts, styles, sidebar elements.
    """
    try:
        raw = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""

    # Remove script / style / nav / footer blocks entirely
    raw = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", raw,
                 flags=re.DOTALL | re.IGNORECASE)

    # Extract title
    title_m = re.search(r"<title[^>]*>([^<]{3,})</title>", raw, re.IGNORECASE)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""

    # Extract text from content tags
    content_tags = re.findall(
        r"<(?:h[1-4]|p|li)[^>]*>(.*?)</(?:h[1-4]|p|li)>",
        raw, re.DOTALL | re.IGNORECASE
    )
    parts: list[str] = []
    if title:
        parts.append(title)
    for chunk in content_tags:
        text = re.sub(r"<[^>]+>", " ", chunk)
        text = re.sub(r"&[a-z#0-9]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 20:
            parts.append(text)

    return "\n\n".join(parts)


def _wikiwijs_dutch_ratio(text: str) -> float:
    words = re.findall(r"\b[a-zA-Zàáâãäåæçèéêëìíîïðòóôõöùúûüý]{2,}\b", text.lower())
    if len(words) < 10:
        return 0.0
    nl_count = sum(1 for w in words if w in _WIKIWIJS_NL_FUNCTION_WORDS)
    return nl_count / len(words)


def parse_wikiwijs_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Wikiwijs arrangement HTML pages (CC0/CC-BY/CC-BY-SA).

    raw_dir contains:
      - manifest.jsonl  — per-arrangement metadata (url, license, title)
      - {stem}.html     — downloaded arrangement HTML
      - {stem}.json     — per-file metadata sidecar

    Only arrangements with CC0, CC-BY, or CC-BY-SA licenses are emitted.
    Skips pages with insufficient Dutch content.
    """
    acq_date = datetime.date.today().isoformat()
    total = 0

    for json_file in sorted(raw_dir.glob("*.json")):
        if json_file.name in ("acquisition_log.jsonl", "manifest.jsonl"):
            continue

        try:
            meta_doc = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        license_str = meta_doc.get("license", "")
        if license_str not in _WIKIWIJS_ALLOWED_LICENSES:
            continue

        html_file = json_file.with_suffix(".html")
        if not html_file.exists():
            continue

        try:
            html_bytes = html_file.read_bytes()
        except OSError as exc:
            logger.warning("[wikiwijs] Cannot read %s: %s", html_file.name, exc)
            continue

        text = _wikiwijs_extract_text(html_bytes)
        if len(text) < 200:
            continue

        if _wikiwijs_dutch_ratio(text) < 0.04:
            continue

        source_url = meta_doc.get("url", f"https://www.wikiwijs.nl/{html_file.stem}")
        doc_id = make_id("wikiwijs", re.sub(r"[^\w]", "_", source_url.split("wikiwijs.nl")[-1]))

        # Extract year from URL or metadata
        year_m = re.search(r"\b(20\d{2}|19\d{2})\b", source_url)
        pub_year = int(year_m.group(0)) if year_m else None

        doc_meta = DocumentMeta(
            id=doc_id,
            source="wikiwijs",
            source_url=source_url,
            license=license_str,
            publication_year=pub_year,
            country="NL",
            domain="education",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=doc_meta)
        total += 1

    logger.info("[wikiwijs] Parsed %d documents", total)


# ---------------------------------------------------------------------------
# Brussels Parliament — plenaire session reports (PD, NL-BE)
# ---------------------------------------------------------------------------

def parse_brussels_parlement_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Brussels Parliament WordPress posts (Public Domain, NL-BE).

    raw_dir/posts/ contains {post_id}.json files with WordPress post data:
    {"id": N, "title": "...", "content": "<HTML>", "link": "...", "date": "..."}

    HTML content is stripped to plain text.
    Posts with less than 200 chars of content are skipped.
    """
    acq_date = datetime.date.today().isoformat()
    posts_dir = raw_dir / "posts"
    if not posts_dir.exists():
        logger.warning("[brussels_parlement] No posts/ directory in %s", raw_dir)
        return

    total = 0
    for json_file in sorted(posts_dir.glob("*.json")):
        try:
            post = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[brussels_parlement] Cannot parse %s: %s", json_file.name, exc)
            continue

        title_raw   = post.get("title", "")
        content_raw = post.get("content", "")
        link        = post.get("link", "")
        date_str    = post.get("date", "")

        # Strip HTML
        title   = re.sub(r"<[^>]+>", " ", title_raw)
        title   = re.sub(r"&[a-z#0-9]+;", " ", title)
        title   = re.sub(r"\s+", " ", title).strip()

        content = re.sub(r"<[^>]+>", " ", content_raw)
        content = re.sub(r"&[a-z#0-9]+;", " ", content)
        content = re.sub(r"\s+", " ", content).strip()

        text = (title + "\n\n" + content).strip() if content else title
        if len(text) < 100:
            continue

        year_m   = re.search(r"\b(20\d{2}|19\d{2})\b", date_str)
        pub_year = int(year_m.group(0)) if year_m else None

        meta = DocumentMeta(
            id=make_id("brussels_parlement", str(post.get("id", json_file.stem))),
            source="brussels_parlement",
            source_url=link or f"https://www.parlement.brussels/?p={json_file.stem}",
            license="PD",
            publication_year=pub_year,
            country="BE",
            domain="parliamentary",
            language_variant="NL-BE",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[brussels_parlement] Parsed %d documents", total)


def parse_vukuzenzele_af_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Vuk'uzenzele Afrikaans articles from HuggingFace Parquet splits.

    Reads vukuzenzele_afr_{train,test,eval}.parquet files downloaded by
    acquire/vukuzenzele_af.py. Only Afrikaans rows (language_code='afr')
    are retained — all other SA language rows are stripped.

    Parquet columns: title, author, text, edition, language_code, index.
    Text field contains the full article body. Min length: 100 chars.

    License: CC BY 4.0
    """
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        logger.warning("[vukuzenzele_af] pyarrow not available — skipping parse")
        return

    parquet_files = sorted(raw_dir.glob("vukuzenzele_afr_*.parquet"))
    if not parquet_files:
        logger.warning("[vukuzenzele_af] No parquet files found in %s", raw_dir)
        return

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for pq_file in parquet_files:
        try:
            table = pq.read_table(str(pq_file))
        except Exception as exc:
            logger.warning("[vukuzenzele_af] Cannot read %s: %s", pq_file.name, exc)
            continue

        col_names = set(table.schema.names)
        for i in range(table.num_rows):
            row = {col: table.column(col)[i].as_py() for col in col_names}

            # Strict Afrikaans-only filter — skip any other SA language
            lang_code = (row.get("language_code") or "").strip().lower()
            if lang_code and lang_code != "afr":
                skipped += 1
                continue

            text_raw = row.get("text") or ""
            title_raw = row.get("title") or ""

            # Combine title + body
            if title_raw and not text_raw.startswith(title_raw):
                text = f"{title_raw}\n\n{text_raw}".strip()
            else:
                text = text_raw.strip()

            if len(text) < 100:
                skipped += 1
                continue

            # Parse publication year from edition string (e.g. "2021-03-ed1")
            edition = row.get("edition") or ""
            pub_year: int | None = None
            if edition and len(edition) >= 4 and edition[:4].isdigit():
                pub_year = int(edition[:4])

            doc_idx = row.get("index") or i
            doc_id  = make_id("vukuzenzele_af", f"{pq_file.stem}_{doc_idx}")

            meta = DocumentMeta(
                id=doc_id,
                source="vukuzenzele_af",
                source_url="https://huggingface.co/datasets/dsfsi/vukuzenzele-monolingual",
                license="CC-BY",
                publication_year=pub_year,
                country="ZA",
                domain="government",
                language_variant="AF",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1

    logger.info("[vukuzenzele_af] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# za-mafoko — DSFSI multilingual South African terminology (MIT/CC0)
# ---------------------------------------------------------------------------

def parse_za_mafoko_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse DSFSI Mafoko multilingual terminology CSVs for South African NLP.

    Reads CSV files downloaded by acquire/za_mafoko.py from GitHub
    (dsfsi/za-mavito). Each CSV contains domain-specific terminology
    (Mathematics, Finance, Elections, HIV/AIDS, Government) in multiple
    South African languages. Only Afrikaans entries are extracted.

    Columns tried (in order): 'af', 'afrikaans', 'Afrikaans', 'AF'.
    English gloss column tried: 'en', 'english', 'English', 'EN'.
    Domain is inferred from filename (e.g. 'finance_glossary.csv' → Finance).

    License: MIT / CC0
    Country: ZA, domain: reference, language_variant: AF
    """
    import csv

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    _AF_COLS = ("afr", "af", "afrikaans", "Afrikaans", "AF", "Afr")
    _EN_COLS = ("eng", "en", "english", "English", "EN")

    def _first_col(header: list[str], candidates: tuple[str, ...]) -> str | None:
        for c in candidates:
            if c in header:
                return c
        return None

    def _infer_domain(fname: str) -> str:
        fname_lower = fname.lower()
        for kw in ("finance", "financ"):
            if kw in fname_lower:
                return "Finance"
        for kw in ("math", "wiskunde"):
            if kw in fname_lower:
                return "Mathematics"
        for kw in ("election", "verkies"):
            if kw in fname_lower:
                return "Elections"
        for kw in ("hiv", "aids", "health", "gesond"):
            if kw in fname_lower:
                return "Health"
        for kw in ("government", "state", "staat"):
            if kw in fname_lower:
                return "Government"
        return "Terminology"

    csv_files = sorted(raw_dir.glob("*.csv"))
    if not csv_files:
        logger.warning("[za_mafoko] No CSV files found in %s", raw_dir)
        return

    for csv_path in csv_files:
        domain = _infer_domain(csv_path.stem)
        terms: list[str] = []
        try:
            with csv_path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                header = list(reader.fieldnames or [])
                af_col = _first_col(header, _AF_COLS)
                en_col = _first_col(header, _EN_COLS)
                if af_col is None:
                    logger.warning("[za_mafoko] No Afrikaans column in %s (headers: %s)",
                                   csv_path.name, header)
                    skipped += 1
                    continue
                for row in reader:
                    af_term = (row.get(af_col) or "").strip()
                    if not af_term:
                        continue
                    if en_col:
                        en_term = (row.get(en_col) or "").strip()
                        if en_term:
                            terms.append(f"{en_term} — {af_term}")
                        else:
                            terms.append(af_term)
                    else:
                        terms.append(af_term)
        except OSError as exc:
            logger.warning("[za_mafoko] Cannot read %s: %s", csv_path.name, exc)
            skipped += 1
            continue

        if not terms:
            skipped += 1
            continue

        # Produce one document per CSV file (glossary as structured text)
        body = f"# {domain} Terminologie (Afrikaans)\n\n" + "\n".join(terms)
        doc_id = make_id("za_mafoko", csv_path.stem)
        meta = DocumentMeta(
            id=doc_id,
            source="za_mafoko",
            source_url="https://github.com/dsfsi/za-mavito",
            license="CC0",
            publication_year=None,
            country="ZA",
            domain="reference",
            language_variant="AF",
            acquisition_date=acq_date,
        )
        yield Document(text=body, meta=meta)
        total += 1

    logger.info("[za_mafoko] Parsed %d glossary documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# AdeKUS Journal — Academic Journal of Suriname (CC-BY, OAI-PMH)
# ---------------------------------------------------------------------------

def parse_adekus_journal_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Academic Journal of Suriname articles from OAI-PMH harvest.

    Reads adekus_metadata.jsonl written by acquire/adekus_journal.py.
    For each record, uses description (abstract) as the primary text.
    If a downloaded PDF exists in pdfs/, its text is extracted instead.

    Filters to Dutch-language records. Title + abstract are combined.
    Minimum text length: 100 characters.

    License: CC-BY 4.0
    Country: SR (Suriname), domain: academic, language_variant: NL
    """
    meta_file = raw_dir / "adekus_metadata.jsonl"
    if not meta_file.exists():
        logger.warning("[adekus_journal] No adekus_metadata.jsonl in %s", raw_dir)
        return

    try:
        from pipeline.parse.pdf_text import extract_text as _pdf_text
        _has_pdf = True
    except ImportError:
        _has_pdf = False

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    _NL_LANGS = {"nl", "nld", "dut", "dutch", "nederlands"}

    for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Language filter
        lang = (rec.get("language") or "").strip().lower()
        if lang and not any(lang.startswith(nl) for nl in _NL_LANGS):
            skipped += 1
            continue

        rec_id    = rec.get("id", "")
        title     = (rec.get("title") or "").strip()
        abstract  = (rec.get("description") or "").strip()
        identifier = (rec.get("identifier") or "").strip()
        date_str  = (rec.get("date") or "")

        # Try to use PDF text if available
        text = ""
        if _has_pdf and rec_id:
            safe_id = re.sub(r"[^\w\-]", "_", rec_id)[:100]
            pdf_path = raw_dir / "pdfs" / f"{safe_id}.pdf"
            if pdf_path.exists():
                text = _pdf_text(pdf_path) or ""

        if not text:
            # Fall back to title + abstract
            parts = []
            if title:
                parts.append(title)
            if abstract and abstract != title:
                parts.append(abstract)
            text = "\n\n".join(parts)

        if len(text) < 100:
            skipped += 1
            continue

        # Parse year from date field (ISO date or year-only)
        pub_year: int | None = None
        year_m = re.search(r"\b(19|20)\d{2}\b", date_str)
        if year_m:
            pub_year = int(year_m.group(0))

        doc_id = make_id("adekus_journal", rec_id or f"idx_{total}")
        meta = DocumentMeta(
            id=doc_id,
            source="adekus_journal",
            source_url=identifier or "https://adekusjournal.uvs.edu/",
            license="CC-BY",
            publication_year=pub_year,
            country="SR",
            domain="academic",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[adekus_journal] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Open Gazettes ZA — South African government gazettes (PD, PDF-based)
# ---------------------------------------------------------------------------

def parse_open_gazettes_za_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse South African government gazette PDFs.

    Reads PDF files and gazettes_index.jsonl downloaded by
    acquire/open_gazettes_za.py from opengazettes.org.za /
    gazettes.africa.

    Gazettes are bilingual (Afrikaans + English). Text is extracted via
    pdfminer. Records with fewer than 200 characters are skipped.

    License: PD (South African government publications, Copyright Act 1978)
    Country: ZA, domain: government, language_variant: AF
    """
    try:
        from pipeline.parse.pdf_text import extract_text as _pdf_text
    except ImportError:
        logger.warning("[open_gazettes_za] pdf_text not available — skipping")
        return

    # Load index metadata for richer provenance
    index_file = raw_dir / "gazettes_index.jsonl"
    _meta_by_file: dict[str, dict] = {}
    if index_file.exists():
        for line in index_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                fname = rec.get("filename") or ""
                if fname:
                    _meta_by_file[fname] = rec
            except json.JSONDecodeError:
                continue

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for pdf_path in sorted(raw_dir.glob("gazette_*.pdf")):
        text = _pdf_text(pdf_path)
        if not text or len(text) < 200:
            skipped += 1
            continue

        idx = _meta_by_file.get(pdf_path.name, {})
        source_url = idx.get("url") or "https://opengazettes.org.za/"
        date_str   = idx.get("date") or idx.get("publication_date") or ""
        pub_year: int | None = None
        year_m = re.search(r"\b(19|20)\d{2}\b", date_str)
        if year_m:
            pub_year = int(year_m.group(0))
        if pub_year is None:
            # Try to extract year from filename e.g. gazette_2024_01_02_...pdf
            year_m2 = re.search(r"gazette_(\d{4})", pdf_path.name)
            if year_m2:
                pub_year = int(year_m2.group(1))

        doc_id = make_id("open_gazettes_za", pdf_path.stem)
        meta = DocumentMeta(
            id=doc_id,
            source="open_gazettes_za",
            source_url=source_url,
            license="PD",
            publication_year=pub_year,
            country="ZA",
            domain="government",
            language_variant="AF",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[open_gazettes_za] Parsed %d gazettes (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Meertens Dialecten — NDB soundbite transcriptions (CC-BY, Dutch dialects)
# ---------------------------------------------------------------------------

def parse_meertens_dialecten_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Meertens NDB (Nederlandse Dialectenbank) soundbite transcriptions.

    Reads soundbites.jsonl downloaded by acquire/meertens_dialecten.py.
    Each soundbite is a brief transcription of an audio fragment recorded
    from a Dutch dialect speaker (mid-20th century). Location and Kloeke
    code are used to enrich the document text.

    772 transcribed recordings available. Short transcriptions (< 30 chars)
    are skipped; longer ones are grouped by location into batches of 10
    to avoid oversized documents.

    License: CC-BY (Meertens Instituut / KNAW)
    Country: NL, domain: dialect, language_variant: NL
    """
    soundbites_file = raw_dir / "soundbites.jsonl"

    # Also scan individual .txt files
    txt_files = sorted(raw_dir.glob("ndb_*.txt"))

    if not soundbites_file.exists() and not txt_files:
        logger.warning("[meertens_dialecten] No soundbites.jsonl or ndb_*.txt in %s", raw_dir)
        return

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    # --- Process JSONL soundbites ---
    if soundbites_file.exists():
        items_by_location: dict[str, list[dict]] = {}
        for line in soundbites_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = (rec.get("text") or "").strip()
            if len(text) < 30:
                skipped += 1
                continue
            loc = (rec.get("location") or rec.get("province") or "onbekend").strip()
            items_by_location.setdefault(loc, []).append(rec)

        BATCH = 10
        for loc, items in sorted(items_by_location.items()):
            for batch_start in range(0, len(items), BATCH):
                batch = items[batch_start : batch_start + BATCH]
                parts: list[str] = []
                ledger: list[ProvenanceEntry] = []
                for item in batch:
                    text = (item.get("text") or "").strip()
                    kloeke = item.get("kloeke") or ""
                    year   = item.get("year") or ""
                    header = f"[{loc}"
                    if kloeke:
                        header += f", Kloeke {kloeke}"
                    if year:
                        header += f", {year}"
                    header += "]"
                    parts.append(f"{header}\n{text}")

                    # Aggregate ledger
                    item_ledger = item.get("ledger", [])
                    ledger.extend([ProvenanceEntry.from_dict(e) for e in item_ledger])

                body = "\n\n".join(parts)
                if len(body) < 30:
                    continue

                batch_idx  = batch_start // BATCH
                first_item = batch[0]
                pub_year_val = first_item.get("year")
                pub_year: int | None = int(str(pub_year_val)) if pub_year_val else None

                safe_loc = re.sub(r"[^\w]", "_", loc)[:40]
                doc_id   = make_id("meertens_dialecten", f"{safe_loc}_{batch_idx:04d}")
                source_url = first_item.get("url") or "https://meertens.knaw.nl/ndb/"
                meta = DocumentMeta(
                    id=doc_id,
                    source="meertens_dialecten",
                    source_url=source_url,
                    license="CC-BY",
                    publication_year=pub_year,
                    country="NL",
                    domain="dialect",
                    language_variant="NL",
                    acquisition_date=acq_date,
                    ledger=ledger,
                )
                yield Document(text=body, meta=meta)
                total += 1

    # --- Process individual .txt files (fallback) ---
    for txt_path in txt_files:
        try:
            text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("[meertens_dialecten] Cannot read %s: %s", txt_path.name, exc)
            continue
        if len(text) < 30:
            skipped += 1
            continue
        doc_id = make_id("meertens_dialecten", txt_path.stem)
        meta = DocumentMeta(
            id=doc_id,
            source="meertens_dialecten",
            source_url="https://meertens.knaw.nl/ndb/",
            license="CC-BY",
            publication_year=None,
            country="NL",
            domain="dialect",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[meertens_dialecten] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# WEWIS — Vlaamse overheid beleidsdocumenten (CC0/Open Data, NL-BE)
# ---------------------------------------------------------------------------

def parse_wewis_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse WEWIS (Departement Werk en Sociale Economie) policy documents.

    Reads wewis_metadata.jsonl + pdfs/ downloaded by acquire/wewis.py.
    Uses description/omschrijving as primary text; falls back to PDF
    extraction if a PDF was downloaded. Minimum text: 100 characters.

    License: CC0 (Vlaamse overheid open data)
    Country: NL-BE, domain: government, language_variant: NL
    """
    meta_file = raw_dir / "wewis_metadata.jsonl"
    if not meta_file.exists():
        logger.warning("[wewis] No wewis_metadata.jsonl in %s", raw_dir)
        return

    try:
        from pipeline.parse.pdf_text import extract_text as _pdf_text
        _has_pdf = True
    except ImportError:
        _has_pdf = False

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_id    = str(rec.get("id") or rec.get("naam") or rec.get("titel") or "")
        title     = (rec.get("naam") or rec.get("titel") or rec.get("title") or "").strip()
        desc      = (rec.get("omschrijving") or rec.get("beschrijving") or
                     rec.get("description") or "").strip()
        date_str  = str(rec.get("datum") or rec.get("date") or "")
        source_url = (rec.get("url") or rec.get("document_url") or rec.get("link") or
                      "https://opendata.wewis.vlaanderen.be/")

        # Try PDF first
        text = ""
        if _has_pdf and rec_id:
            safe = re.sub(r"[^\w\-]", "_", rec_id)[:80]
            pdf_path = raw_dir / "pdfs" / f"{safe}.pdf"
            if pdf_path.exists():
                text = _pdf_text(pdf_path) or ""

        if not text:
            parts = []
            if title:
                parts.append(title)
            if desc and desc != title:
                parts.append(desc)
            text = "\n\n".join(parts)

        if len(text) < 100:
            skipped += 1
            continue

        pub_year: int | None = None
        year_m = re.search(r"\b(19|20)\d{2}\b", date_str)
        if year_m:
            pub_year = int(year_m.group(0))

        meta = DocumentMeta(
            id=make_id("wewis", rec_id or f"idx_{total}"),
            source="wewis",
            source_url=str(source_url),
            license="CC0",
            publication_year=pub_year,
            country="NL-BE",
            domain="government",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[wewis] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# SUNScholar — Stellenbosch University OAI-PMH (CC-BY, Afrikaans academic)
# ---------------------------------------------------------------------------

def parse_sunscholar_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Stellenbosch University SUNScholar academic publications.

    Reads sunscholar_metadata.jsonl + optional pdfs/ from acquire/sunscholar.py.
    Primary text: title + abstract (description). Falls back to PDF if available.
    Language filter: Afrikaans (af/afr) and Dutch (nl/nld).
    Records with no declared language are langdetect-filtered (SUNScholar has many English papers).

    License: CC-BY 4.0
    Country: ZA, domain: academic, language_variant: AF or NL
    """
    meta_file = raw_dir / "sunscholar_metadata.jsonl"
    if not meta_file.exists():
        logger.warning("[sunscholar] No sunscholar_metadata.jsonl in %s", raw_dir)
        return

    try:
        from pipeline.parse.pdf_text import extract_text as _pdf_text
        _has_pdf = True
    except ImportError:
        _has_pdf = False

    # Langdetect for records with no declared language (sunscholar has many English papers)
    try:
        from langdetect import detect as _ld_detect, LangDetectException as _LDE
        _has_ld = True
    except ImportError:
        _has_ld = False

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    _AF_LANGS = {"af", "afr", "afrikaans"}
    _NL_LANGS = {"nl", "nld", "dut", "dutch"}

    for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Support both singular (old) and plural list (new acquirer) field names
        rec_id = (rec.get("record_id") or rec.get("id") or "").strip()

        titles_raw = rec.get("titles") or rec.get("title") or []
        title = (titles_raw[0] if isinstance(titles_raw, list) else titles_raw or "").strip()

        descs_raw = rec.get("descriptions") or rec.get("description") or []
        abstract = "\n".join(
            d.strip() for d in (descs_raw if isinstance(descs_raw, list) else [descs_raw])
            if isinstance(d, str) and d.strip() and d.strip() != title
        )

        langs_raw = rec.get("languages") or rec.get("language") or []
        lang = (langs_raw[0] if isinstance(langs_raw, list) and langs_raw else langs_raw or "").strip().lower()

        dates_raw = rec.get("dates") or rec.get("date") or []
        date_str = str(dates_raw[-1] if isinstance(dates_raw, list) and dates_raw else dates_raw or "")

        idents_raw = rec.get("identifiers") or rec.get("identifier") or []
        identifier = next(
            (i for i in (idents_raw if isinstance(idents_raw, list) else [idents_raw])
             if isinstance(i, str) and i.startswith("http")),
            rec.get("source_url") or "",
        )

        # Determine language variant; skip records in neither AF nor NL
        _NL_LANGS_SET = {"nl", "nld", "dut", "dutch", "nederlands"}
        is_explicit_af = lang and any(lang.startswith(a) for a in _AF_LANGS)
        is_explicit_nl = lang and (lang in _NL_LANGS_SET or lang.startswith("nl"))
        is_explicit_other = lang and not is_explicit_af and not is_explicit_nl

        if is_explicit_other:
            skipped += 1
            continue

        lang_variant = "AF" if is_explicit_af else "NL"

        # For records with no declared language, use langdetect on title+abstract
        # to avoid including English papers (SUNScholar has many English records)
        if not lang and _has_ld:
            probe = f"{title} {abstract}".strip()
            if len(probe) >= 30:
                try:
                    detected = _ld_detect(probe)
                    if detected not in ("nl", "af"):
                        skipped += 1
                        continue
                    lang_variant = "AF" if detected == "af" else "NL"
                except _LDE:
                    pass  # keep on detection failure

        text = ""
        if _has_pdf and rec_id:
            safe = re.sub(r"[^\w\-]", "_", rec_id)[:100]
            pdf_path = raw_dir / "pdfs" / f"{safe}.pdf"
            if pdf_path.exists():
                text = _pdf_text(pdf_path) or ""

        if not text:
            parts = []
            if title:
                parts.append(title)
            if abstract and abstract != title:
                parts.append(abstract)
            text = "\n\n".join(parts)

        if len(text) < 100:
            skipped += 1
            continue

        pub_year: int | None = None
        year_m = re.search(r"\b(19|20)\d{2}\b", date_str)
        if year_m:
            pub_year = int(year_m.group(0))

        meta = DocumentMeta(
            id=make_id("sunscholar", rec_id or f"idx_{total}"),
            source="sunscholar",
            source_url=identifier or "https://scholar.sun.ac.za/",
            license="CC-BY",
            publication_year=pub_year,
            country="ZA",
            domain="academic",
            language_variant=lang_variant,
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[sunscholar] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# TNO Repository — Applied science publications (CC-BY, NL)
# ---------------------------------------------------------------------------

def parse_tno_repository_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse TNO (Netherlands Organisation for Applied Scientific Research) publications.

    Reads tno_metadata.jsonl + optional pdfs/ from acquire/tno_repository.py.
    Uses title + description (abstract) as text; PDF if downloaded.

    License: CC-BY 4.0
    Country: NL, domain: academic, language_variant: NL
    """
    meta_file = raw_dir / "tno_metadata.jsonl"
    if not meta_file.exists():
        logger.warning("[tno_repository] No tno_metadata.jsonl in %s", raw_dir)
        return

    try:
        from pipeline.parse.pdf_text import extract_text as _pdf_text
        _has_pdf = True
    except ImportError:
        _has_pdf = False

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_id   = (rec.get("id") or "").strip()
        title    = (rec.get("title") or "").strip()
        abstract = (rec.get("description") or "").strip()
        date_str = str(rec.get("date") or "")
        identifier = (rec.get("identifier") or "").strip()

        text = ""
        if _has_pdf and rec_id:
            safe = re.sub(r"[^\w\-]", "_", rec_id)[:100]
            pdf_path = raw_dir / "pdfs" / f"{safe}.pdf"
            if pdf_path.exists():
                text = _pdf_text(pdf_path) or ""

        if not text:
            parts = []
            if title:
                parts.append(title)
            if abstract and abstract != title:
                parts.append(abstract)
            text = "\n\n".join(parts)

        if len(text) < 100:
            skipped += 1
            continue

        pub_year: int | None = None
        year_m = re.search(r"\b(19|20)\d{2}\b", date_str)
        if year_m:
            pub_year = int(year_m.group(0))

        meta = DocumentMeta(
            id=make_id("tno_repository", rec_id or f"idx_{total}"),
            source="tno_repository",
            source_url=identifier or "https://repository.tno.nl/",
            license="CC-BY",
            publication_year=pub_year,
            country="NL",
            domain="academic",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[tno_repository] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Diamond OA NL — Dutch Diamond Open Access journals (CC-BY/CC0, NL)
# ---------------------------------------------------------------------------

def parse_diamond_oa_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch Diamond Open Access journal articles.

    Reads diamond_oa_metadata.jsonl + optional pdfs/ from
    acquire/diamond_oa_nl.py. Articles are from Dutch OA journals
    that publish with no fees for authors or readers.

    Journals: Krisis, Tijdschrift voor Geschiedenis, Tijdschrift voor
    Arbeidsvraagstukken, Tijdschrift voor Genderstudies, Verpleegkunde.

    License: CC-BY (per journal) / CC0
    Country: NL, domain: academic, language_variant: NL
    """
    meta_file = raw_dir / "diamond_oa_metadata.jsonl"
    if not meta_file.exists():
        logger.warning("[diamond_oa_nl] No diamond_oa_metadata.jsonl in %s", raw_dir)
        return

    try:
        from pipeline.parse.pdf_text import extract_text as _pdf_text
        _has_pdf = True
    except ImportError:
        _has_pdf = False

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_id    = (rec.get("id") or "").strip()
        title     = (rec.get("title") or "").strip()
        abstract  = (rec.get("description") or "").strip()
        date_str  = str(rec.get("date") or "")
        identifier = (rec.get("identifier") or "").strip()
        rights    = (rec.get("rights") or "CC-BY").strip()

        # Map rights string to canonical license
        rights_lower = rights.lower()
        if "cc0" in rights_lower or "public domain" in rights_lower:
            license_str = "CC0"
        elif "by-sa" in rights_lower:
            license_str = "CC-BY-SA"
        else:
            license_str = "CC-BY"

        text = ""
        if _has_pdf and rec_id:
            safe = re.sub(r"[^\w\-]", "_", rec_id)[:100]
            pdf_path = raw_dir / "pdfs" / f"{safe}.pdf"
            if pdf_path.exists():
                text = _pdf_text(pdf_path) or ""

        if not text:
            parts = []
            if title:
                parts.append(title)
            if abstract and abstract != title:
                parts.append(abstract)
            text = "\n\n".join(parts)

        if len(text) < 100:
            skipped += 1
            continue

        pub_year: int | None = None
        year_m = re.search(r"\b(19|20)\d{2}\b", date_str)
        if year_m:
            pub_year = int(year_m.group(0))

        meta = DocumentMeta(
            id=make_id("diamond_oa_nl", rec_id or f"idx_{total}"),
            source="diamond_oa_nl",
            source_url=identifier or "https://www.openaccess.nl/",
            license=license_str,
            publication_year=pub_year,
            country="NL",
            domain="academic",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[diamond_oa_nl] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# FAME! Frisian — Open Frisian text corpus (CC-BY 4.0, FY)
# ---------------------------------------------------------------------------

def parse_fame_frisian_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse the FAME! open Frisian text corpus from Fryske Akademy / RU Groningen.

    Reads fame_text_*.txt files (extracted from corpus archive) downloaded by
    acquire/fame_frisian.py. Also handles .tar.gz / .zip extraction if not
    yet done. Extracts text in batches of 500 lines per Document.

    License: CC-BY 4.0
    Country: NL (Friesland), domain: broadcast, language_variant: FY
    """
    import zipfile as _zipfile
    import tarfile as _tarfile

    # Extract archives if not yet done
    for arc in list(raw_dir.glob("*.tar.gz")) + list(raw_dir.glob("*.tgz")):
        dest = raw_dir / arc.stem.replace(".tar", "")
        if not dest.exists():
            logger.info("[fame_frisian] Extracting %s ...", arc.name)
            try:
                with _tarfile.open(arc) as tf:
                    tf.extractall(raw_dir)
            except Exception as exc:
                logger.warning("[fame_frisian] Cannot extract %s: %s", arc.name, exc)

    for arc in raw_dir.glob("*.zip"):
        dest = raw_dir / arc.stem
        if not dest.exists():
            logger.info("[fame_frisian] Extracting %s ...", arc.name)
            try:
                with _zipfile.ZipFile(arc) as zf:
                    zf.extractall(raw_dir)
            except Exception as exc:
                logger.warning("[fame_frisian] Cannot extract %s: %s", arc.name, exc)

    # Check for _NOT_FOUND sentinel
    if (raw_dir / "_NOT_FOUND.txt").exists():
        logger.warning("[fame_frisian] Corpus not found (see _NOT_FOUND.txt) — skipping")
        return

    txt_files = sorted(raw_dir.rglob("*.txt"))
    txt_files = [f for f in txt_files if not f.name.startswith("_")]
    if not txt_files:
        logger.warning("[fame_frisian] No .txt files found in %s", raw_dir)
        return

    acq_date = datetime.date.today().isoformat()
    total = 0
    BATCH = 500  # lines per document

    for txt_path in txt_files:
        try:
            lines = txt_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("[fame_frisian] Cannot read %s: %s", txt_path.name, exc)
            continue

        lines = [ln for ln in lines if ln.strip()]
        for i in range(0, max(1, len(lines)), BATCH):
            chunk = "\n".join(lines[i : i + BATCH]).strip()
            if len(chunk) < 50:
                continue
            batch_idx = i // BATCH
            doc_id = make_id("fame_frisian", f"{txt_path.stem}_{batch_idx:04d}")
            meta = DocumentMeta(
                id=doc_id,
                source="fame_frisian",
                source_url="https://www.fryske-akademy.nl/",
                license="CC-BY",
                publication_year=None,
                country="NL",
                domain="broadcast",
                language_variant="FY",
                acquisition_date=acq_date,
            )
            yield Document(text=chunk, meta=meta)
            total += 1

    logger.info("[fame_frisian] Parsed %d batches", total)


# ---------------------------------------------------------------------------
# Groote Oorlog — WWI Flemish newspapers (PD, NL-BE)
# ---------------------------------------------------------------------------

def parse_groote_oorlog_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse "Nieuws van de Groote Oorlog" WWI-era Flemish newspaper collection.

    Reads groote_oorlog_metadata.jsonl + articles/ text files downloaded
    by acquire/groote_oorlog.py via meemoo/Europeana/Delpher/Krant van Toen.

    Articles are Dutch-language (NL-BE) newspapers from 1914–1918.

    License: PD (pre-1930 publications)
    Country: NL-BE, domain: news, language_variant: NL
    """
    meta_file  = raw_dir / "groote_oorlog_metadata.jsonl"
    art_dir    = raw_dir / "articles"

    # Check for _NOT_FOUND sentinel
    if (raw_dir / "_NOT_FOUND.txt").exists() and not meta_file.exists() and not (art_dir.exists() and any(art_dir.glob("*.txt"))):
        logger.warning("[groote_oorlog] No data found (see _NOT_FOUND.txt) — skipping")
        return

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    seen_ids: set[str] = set()

    # Build metadata index from JSONL
    meta_index: dict[str, dict] = {}
    if meta_file.exists():
        for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rid = str(rec.get("id") or "")
                if rid:
                    meta_index[rid] = rec
            except json.JSONDecodeError:
                continue

    def _year_from_date(date_str: str) -> int | None:
        m = re.search(r"\b(19\d{2}|18\d{2})\b", date_str)
        return int(m.group(0)) if m else None

    # --- Process article text files ---
    if art_dir.exists():
        for txt_path in sorted(art_dir.glob("*.txt")):
            try:
                text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError as exc:
                logger.warning("[groote_oorlog] Cannot read %s: %s", txt_path.name, exc)
                continue
            if len(text) < 80:
                skipped += 1
                continue

            art_id = txt_path.stem
            if art_id in seen_ids:
                continue
            seen_ids.add(art_id)

            rec = meta_index.get(art_id, {})
            source_url = rec.get("url") or "https://www.europeana.eu/"
            date_str   = str(rec.get("date") or rec.get("publication_date") or "")
            pub_year   = _year_from_date(date_str)

            meta = DocumentMeta(
                id=make_id("groote_oorlog", art_id),
                source="groote_oorlog",
                source_url=source_url,
                license="PD",
                publication_year=pub_year,
                country="NL-BE",
                domain="news",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1

    # --- Process metadata-only records (title + description as document) ---
    for rec_id, rec in meta_index.items():
        if rec_id in seen_ids:
            continue
        title = (rec.get("title") or "").strip()
        desc  = (rec.get("description") or rec.get("text") or "").strip()
        parts = []
        if title:
            parts.append(title)
        if desc and desc != title:
            parts.append(desc)
        text = "\n\n".join(parts)
        if len(text) < 80:
            skipped += 1
            continue

        seen_ids.add(rec_id)
        source_url = rec.get("url") or "https://www.europeana.eu/"
        date_str   = str(rec.get("date") or "")
        pub_year   = _year_from_date(date_str)

        meta = DocumentMeta(
            id=make_id("groote_oorlog", rec_id),
            source="groote_oorlog",
            source_url=source_url,
            license="PD",
            publication_year=pub_year,
            country="NL-BE",
            domain="news",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[groote_oorlog] Parsed %d documents (%d skipped)", total, skipped)


def parse_belgian_journal_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Belgian Journal (Belgisch Staatsblad company bylaws) JSONL records.

    Reads belgian_journal_NNN.jsonl files produced by acquire/belgian_journal.py.
    Each record: {pubid, company_name, publication_date, publication_link, text}

    Dutch records were filtered at acquire time using keyword heuristics.
    Text is the full bylaw/announcement text from the Moniteur Belge.

    License: CC0-1.0
    Country: BE, domain: legal, language_variant: NL-BE
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for jsonl_file in sorted(raw_dir.glob("belgian_journal_*.jsonl")):
        for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            text = (rec.get("text") or "").strip()
            if len(text) < 80:
                skipped += 1
                continue

            pub_date = rec.get("publication_date") or ""
            year_m = re.search(r"\b(20\d{2}|19\d{2})\b", pub_date)
            pub_year = int(year_m.group(0)) if year_m else None

            pubid = str(rec.get("pubid") or "")
            doc_id = make_id("belgian_journal", pubid if pubid else text[:40])
            source_url = rec.get("publication_link") or f"https://www.ejustice.just.fgov.be/"

            meta = DocumentMeta(
                id=doc_id,
                source="belgian_journal",
                source_url=source_url,
                license="CC0",
                publication_year=pub_year,
                country="BE",
                domain="legal",
                language_variant="NL-BE",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1

    logger.info("[belgian_journal] Parsed %d documents (%d skipped)", total, skipped)


def parse_eurovoc_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse EuroVoc EU controlled vocabulary Dutch concepts.

    Reads eurovoc_concepts.jsonl produced by acquire/eurovoc.py.
    Each concept has: id (URI), label (Dutch prefLabel), scope_note (Dutch definition).
    Alt labels are merged from eurovoc_altlabels.jsonl when present.

    Generates one document per concept: label + scope note + any alt labels.
    Documents are short but high-quality definitional Dutch prose.

    License: CC-BY 4.0 (EU Publications Office)
    Country: EU, domain: reference
    """
    acq_date = datetime.date.today().isoformat()
    concepts_path = raw_dir / "eurovoc_concepts.jsonl"
    alt_path      = raw_dir / "eurovoc_altlabels.jsonl"

    if not concepts_path.exists():
        logger.warning("[eurovoc] eurovoc_concepts.jsonl not found in %s", raw_dir)
        return

    # Build alt-label index
    alt_index: dict[str, list[str]] = {}
    if alt_path.exists():
        for line in alt_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                cid = rec.get("concept_id", "")
                lbl = rec.get("alt_label", "").strip()
                if cid and lbl:
                    alt_index.setdefault(cid, []).append(lbl)
            except json.JSONDecodeError:
                continue

    total = skipped = 0

    for line in concepts_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        concept_id = rec.get("id", "")
        label      = (rec.get("label") or "").strip()
        scope_note = (rec.get("scope_note") or "").strip()

        if not label:
            skipped += 1
            continue

        # Build document text
        alts = alt_index.get(concept_id, [])
        parts = [label]
        if alts:
            parts.append("Ook bekend als: " + "; ".join(alts) + ".")
        if scope_note:
            parts.append(scope_note)

        text = "\n".join(parts)
        if len(text) < 10:
            skipped += 1
            continue

        # Derive a short stable ID from the URI
        short_id = concept_id.rsplit("/", 1)[-1]
        doc_id   = make_id("eurovoc", short_id)

        meta = DocumentMeta(
            id=doc_id,
            source="eurovoc",
            source_url=concept_id,
            license="CC-BY",
            publication_year=None,
            country="EU",
            domain="reference",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[eurovoc] Parsed %d concepts (%d skipped)", total, skipped)


def parse_bhic_htr_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse BHIC + Dutch regional archive HTR transcriptions.

    Reads bhic_htr_*.jsonl files produced by acquire/bhic_htr.py.
    Each record contains batched page transcriptions from one Dutch
    regional archive collection (BHIC, NHA, GRA, ZA, RHCL, GA, HCO,
    HUA, Tresoar, Nationaal Archief, etc.).

    Content is 17th–20th century handwritten Dutch text (notarial deeds,
    court records, administrative documents) transcribed via HTR.

    License: CC-BY 4.0 (Zenodo 10.5281/zenodo.11209325)
    Country: NL, domain: historical
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for jsonl_file in sorted(raw_dir.glob("bhic_htr_*.jsonl")):
        for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            text = (rec.get("text") or "").strip()
            if len(text) < 50:
                skipped += 1
                continue

            rec_id  = rec.get("id") or f"{jsonl_file.stem}_{total}"
            archive = rec.get("archive") or "unknown"
            doc_id  = make_id("bhic_htr", rec_id)

            meta = DocumentMeta(
                id=doc_id,
                source="bhic_htr",
                source_url=f"https://zenodo.org/records/11209325",
                license="CC-BY",
                publication_year=None,
                country="NL",
                domain="historical",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1

    logger.info("[bhic_htr] Parsed %d documents (%d skipped)", total, skipped)


def parse_af_genre_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse SADiLaR Afrikaans Genre Classification Corpus.

    Reads af_genre_{category}.jsonl files. Six genre categories:
    fiction (drama, poetry, prose) and non-fiction (neutral, objective, subjective).
    Text is Latin-1 originally, re-encoded to UTF-8 at acquire time.

    License: CC BY 2.5 ZA
    Country: ZA, domain: literature, language_variant: AF
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for jsonl_file in sorted(raw_dir.glob("af_genre_*.jsonl")):
        for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            text = (rec.get("text") or "").strip()
            if len(text) < 30:
                skipped += 1
                continue

            rec_id = rec.get("id") or jsonl_file.stem + f"_{total}"
            genre  = rec.get("genre") or "unknown"
            doc_id = make_id("af_genre", rec_id)

            meta = DocumentMeta(
                id=doc_id,
                source="af_genre",
                source_url="https://repo.sadilar.org/handle/20.500.12185/393",
                license="CC-BY",
                publication_year=None,
                country="ZA",
                domain="literature",
                language_variant="AF",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1

    logger.info("[af_genre] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# University Repositories NL — Leiden / UvA / KU Leuven OAI-PMH (CC-BY)
# ---------------------------------------------------------------------------

def parse_university_repos_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch academic records harvested from three OAI-PMH repositories:
      Leiden University Scholarly Publications  (NL)
      UvA-DARE (University of Amsterdam)        (NL)
      KU Leuven Lirias                          (NL-BE)

    Each institution saves to its own subdir with a records.jsonl file.
    Text = title + "\\n\\n" + first description (abstract).
    Skips records with empty abstracts or fewer than 80 characters.

    License: CC-BY (per record rights field)
    Domain: academic
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    _lang_variant_map = {
        "leiden":   "NL",
        "uva":      "NL",
        "kuleuven": "NL-BE",
        "ugent":    "NL-BE",
        "nwu":      "AF",
        "up":       "AF",
        "ufs":      "AF",
    }
    _country_map = {
        "leiden":   "NL",
        "uva":      "NL",
        "kuleuven": "BE",
        "ugent":    "BE",
        "nwu":      "ZA",
        "up":       "ZA",
        "ufs":      "ZA",
    }

    def _norm_license(rights_list: list) -> str | None:
        for r in rights_list:
            r = r.upper()
            if "BY-SA" in r:
                return "CC-BY-SA"
            if "CC0" in r or "CC-0" in r:
                return "CC0"
            if "BY" in r:
                return "CC-BY"
        return None

    for inst_dir in sorted(raw_dir.iterdir()):
        if not inst_dir.is_dir():
            continue
        inst_name = inst_dir.name
        records_path = inst_dir / "records.jsonl"
        if not records_path.exists():
            continue

        inst_lang_variant = _lang_variant_map.get(inst_name, "NL")
        inst_country      = _country_map.get(inst_name, "NL")

        for line in records_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            titles = rec.get("titles") or []
            descs  = rec.get("descriptions") or []
            title  = titles[0].strip() if titles else ""
            abstract = descs[0].strip() if descs else ""

            # Build text
            if title and abstract:
                text = f"{title}\n\n{abstract}"
            elif title:
                text = title
            else:
                text = abstract

            if len(text) < 80:
                skipped += 1
                continue

            rights    = rec.get("rights") or []
            license_  = _norm_license(rights)
            if license_ is None:
                logger.debug(
                    "[university_repos_nl] skipping %s — no recognised licence in dc:rights: %r",
                    rec.get("record_id", "?"), rights,
                )
                skipped += 1
                continue
            year      = rec.get("year")
            source_url= rec.get("source_url") or ""
            rec_id    = rec.get("record_id") or f"{inst_name}_{total}"
            doc_id    = make_id(f"university_repos_nl_{inst_name}", rec_id)
            lang_variant = rec.get("lang_variant") or inst_lang_variant
            country      = rec.get("country") or inst_country

            meta = DocumentMeta(
                id=doc_id,
                source="university_repos_nl",
                source_url=source_url,
                license=license_,
                publication_year=int(year) if year else None,
                country=country,
                domain="academic",
                language_variant=lang_variant,
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1

    logger.info("[university_repos_nl] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# African Storybook Project — Afrikaans children's stories (CC-BY 4.0)
# ---------------------------------------------------------------------------

def _asp_story_text(md_content: str) -> str:
    """Extract readable story text from ASP Markdown (no YAML front matter).

    ASP asp-source format: # Title, ## page separators, footer * Key: [Val] bullets.
    """
    lines = []
    for line in md_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):  # title line — already prepended by caller
            continue
        if stripped == "##":
            continue
        if stripped.startswith("* ") and re.match(r'^\* [A-Z][a-z]+:', stripped):
            continue
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def parse_african_storybook_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Afrikaans children's stories from the African Storybook Project.

    Reads metadata.jsonl for story metadata and stories/{slug}.md for text.
    Skips stories that are too short or have no readable text.

    Source: https://github.com/global-asp/asp-source (CC-BY 4.0)
    Language variant: AF (Afrikaans), Country: ZA
    Domain: literature
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    metadata_path = raw_dir / "metadata.jsonl"
    stories_dir   = raw_dir / "stories"

    if not metadata_path.exists():
        logger.warning("[african_storybook] metadata.jsonl not found in %s", raw_dir)
        return

    for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        slug      = rec.get("slug", "")
        safe_name = rec.get("safe_name") or f"{slug}.md"
        md_path   = stories_dir / safe_name
        if not md_path.exists():
            skipped += 1
            continue

        try:
            md_content = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue

        text = _asp_story_text(md_content)
        title = rec.get("title") or slug
        if title and not text.startswith(title):
            text = f"{title}\n\n{text}"

        if len(text) < 30:
            skipped += 1
            continue

        lang    = rec.get("language", "AF")
        country = rec.get("country", "ZA")
        doc_id  = make_id("african_storybook", slug)
        meta    = DocumentMeta(
            id=doc_id,
            source="african_storybook",
            source_url=rec.get("source_url", "https://www.africanstorybook.org/"),
            license="CC-BY",
            publication_year=None,
            country=country,
            domain="literature",
            language_variant=lang,
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1

    logger.info("[african_storybook] Parsed %d documents (%d skipped)", total, skipped)


def parse_pmg_hansard_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse PMG SA Parliament Hansard/committee records (CC-BY 3.0 ZA, Afrikaans)."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    jsonl_path = raw_dir / "pmg_records.jsonl"
    if not jsonl_path.exists():
        logger.warning("[pmg_hansard] pmg_records.jsonl not found in %s", raw_dir)
        return
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        text = rec.get("text", "").strip()
        if len(text) < 200:
            skipped += 1
            continue
        doc_id = make_id("pmg_hansard", rec.get("id", ""))
        meta = DocumentMeta(
            id=doc_id,
            source="pmg_hansard",
            source_url=rec.get("url", "https://api.pmg.org.za/"),
            license="CC-BY",
            publication_year=int(rec["date"][:4]) if rec.get("date") and len(rec.get("date","")) >= 4 else None,
            country="ZA",
            domain="parliamentary",
            language_variant="AF",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1
    logger.info("[pmg_hansard] Parsed %d documents (%d skipped)", total, skipped)


def parse_nationale_ombudsman_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Dutch National Ombudsman reports (PD, Art. 11 Auteurswet)."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    jsonl_path = raw_dir / "ombudsman_reports.jsonl"
    if not jsonl_path.exists():
        logger.warning("[nationale_ombudsman] ombudsman_reports.jsonl not found in %s", raw_dir)
        return
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        text = strip_html(rec.get("text", "")).strip()
        if len(text) < 300:
            skipped += 1
            continue
        doc_id = make_id("nationale_ombudsman", rec.get("id", ""))
        meta = DocumentMeta(
            id=doc_id,
            source="nationale_ombudsman",
            source_url=rec.get("url", "https://www.nationaleombudsman.nl/"),
            license="PD",
            publication_year=None,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1
    logger.info("[nationale_ombudsman] Parsed %d documents (%d skipped)", total, skipped)


def parse_ovv_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Onderzoeksraad voor Veiligheid safety investigation reports (PD)."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    jsonl_path = raw_dir / "ovv_reports.jsonl"
    if not jsonl_path.exists():
        logger.warning("[ovv] ovv_reports.jsonl not found in %s", raw_dir)
        return
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        text = strip_html(rec.get("text", "")).strip()
        if len(text) < 300:
            skipped += 1
            continue
        doc_id = make_id("ovv", rec.get("id", ""))
        meta = DocumentMeta(
            id=doc_id,
            source="ovv",
            source_url=rec.get("url", "https://onderzoeksraad.nl/"),
            license="PD",
            publication_year=None,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1
    logger.info("[ovv] Parsed %d documents (%d skipped)", total, skipped)


def parse_acm_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse ACM (Autoriteit Consument & Markt) decisions and publications (PD/CC0)."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    jsonl_path = raw_dir / "acm_publications.jsonl"
    if not jsonl_path.exists():
        logger.warning("[acm] acm_publications.jsonl not found in %s", raw_dir)
        return
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        text = strip_html(rec.get("text", "")).strip()
        if len(text) < 300:
            skipped += 1
            continue
        doc_id = make_id("acm", rec.get("id", ""))
        meta = DocumentMeta(
            id=doc_id,
            source="acm",
            source_url=rec.get("url", "https://www.acm.nl/"),
            license="PD",
            publication_year=None,
            country="NL",
            domain="government",
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1
    logger.info("[acm] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Common Corpus Dutch PD — Rijgersberg/common_corpus_dutch_pd (HuggingFace)
# ---------------------------------------------------------------------------

def parse_common_corpus_dutch_pd_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Common Corpus Dutch PD — large PD book/periodical corpus (HuggingFace)."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    jsonl_path = raw_dir / "cc_dutch_pd_records.jsonl"
    if not jsonl_path.exists():
        logger.warning("[cc_dutch_pd] cc_dutch_pd_records.jsonl not found in %s", raw_dir)
        return
    with jsonl_path.open(encoding="utf-8", errors="replace") as _fh:
        for line in _fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            text = (rec.get("text") or "").strip()
            if len(text) < 100:
                skipped += 1
                continue
            year_val = rec.get("year")
            pub_year = int(year_val) if year_val else None
            meta = DocumentMeta(
                id=make_id("common_corpus_dutch_pd", rec.get("id") or rec.get("source_url") or str(total)),
                source="common_corpus_dutch_pd",
                source_url=rec.get("source_url") or "https://huggingface.co/datasets/Rijgersberg/common_corpus_dutch_pd",
                license="PD",
                publication_year=pub_year,
                country="NL",
                domain="literature",
                language_variant="NL",
                acquisition_date=rec.get("acquisition_date") or acq_date,
            )
            yield Document(text=text, meta=meta)
            total += 1
    logger.info("[cc_dutch_pd] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Internet Archive Dutch PD — archive.org Dutch public-domain texts
# ---------------------------------------------------------------------------

def parse_internet_archive_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Internet Archive Dutch PD book/text corpus."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    jsonl_path = raw_dir / "ia_nl_records.jsonl"
    if not jsonl_path.exists():
        logger.warning("[ia_nl] ia_nl_records.jsonl not found in %s", raw_dir)
        return
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        text = (rec.get("text") or "").strip()
        if len(text) < 200:
            skipped += 1
            continue
        pub_year = rec.get("year")
        if pub_year:
            try:
                pub_year = int(pub_year)
            except (ValueError, TypeError):
                pub_year = None
        meta = DocumentMeta(
            id=make_id("internet_archive_nl", rec.get("id") or str(total)),
            source="internet_archive_nl",
            source_url=rec.get("source_url") or "https://archive.org/",
            license="PD",
            publication_year=pub_year,
            country="NL",
            domain="literature",
            language_variant="NL",
            acquisition_date=rec.get("acquisition_date") or acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1
    logger.info("[ia_nl] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Eerste Kamer (Dutch Senate) parliamentary proceedings 1995-present
# ---------------------------------------------------------------------------

def parse_eerste_kamer_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Eerste Kamer parliamentary proceedings (SRU/overheid.nl)."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0
    jsonl_path = raw_dir / "eerste_kamer_records.jsonl"
    if not jsonl_path.exists():
        logger.warning("[ek] eerste_kamer_records.jsonl not found in %s", raw_dir)
        return
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        text = (rec.get("text") or "").strip()
        if len(text) < 100:
            skipped += 1
            continue
        pub_year = rec.get("year")
        if pub_year:
            try:
                pub_year = int(pub_year)
            except (ValueError, TypeError):
                pub_year = None
        meta = DocumentMeta(
            id=make_id("eerste_kamer", rec.get("identifier") or str(total)),
            source="eerste_kamer",
            source_url=rec.get("identifier") or "https://www.eerstekamer.nl/",
            license="CC0",
            publication_year=pub_year,
            country="NL",
            domain="parliamentary",
            language_variant="NL",
            acquisition_date=rec.get("acquisition_date") or acq_date,
        )
        yield Document(text=text, meta=meta)
        total += 1
    logger.info("[ek] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# gptnl_public — GPT-NL Public Corpus Dutch subsets (CC-BY-4.0)
# ---------------------------------------------------------------------------

def parse_gptnl_public_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse GPT-NL Public Corpus Dutch subsets (utrechts-archief, naturalis, etc.)."""
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    # Each subset lives in raw_dir/{subdir}/{subdir}.jsonl
    subset_dirs = sorted(d for d in raw_dir.iterdir() if d.is_dir())
    if not subset_dirs:
        # Also try flat JSONL files
        subset_dirs = []
        for jf in sorted(raw_dir.glob("*.jsonl")):
            if jf.name != "acquisition_log.jsonl":
                subset_dirs.append(jf.parent)
        subset_dirs = [raw_dir]

    for sub in subset_dirs:
        for jsonl_path in sorted(sub.glob("*.jsonl")):
            if jsonl_path.name in ("acquisition_log.jsonl", "done_shards.txt"):
                continue
            subset_name = sub.name if sub != raw_dir else jsonl_path.stem
            with jsonl_path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue
                    text = (rec.get("text") or "").strip()
                    if len(text) < 50:
                        skipped += 1
                        continue
                    lic = rec.get("license") or "CC-BY"
                    lic_low = lic.lower()
                    if "nc" in lic_low or "nd" in lic_low:
                        skipped += 1
                        continue
                    lang = (rec.get("language") or "nl").lower()
                    lang_variant_map = {
                        "fy": "FY", "fry": "FY", "af": "AF", "afr": "AF",
                    }
                    lang_variant = lang_variant_map.get(lang, "NL")
                    meta = DocumentMeta(
                        id=make_id("gptnl_public", rec.get("id") or str(total)),
                        source="gptnl_public",
                        source_url=rec.get("source_url") or "https://huggingface.co/datasets/GPT-NL/GPT-NL_Public_Corpus",
                        license="CC-BY" if "cc-by" in lic_low else ("CC0" if "public-domain" in lic_low or "cc0" in lic_low else "CC-BY"),
                        publication_year=None,
                        country="NL",
                        domain="mixed",
                        language_variant=lang_variant,
                        acquisition_date=rec.get("acquisition_date") or acq_date,
                    )
                    yield Document(text=text, meta=meta)
                    total += 1
    logger.info("[gptnl_public] Parsed %d documents (%d skipped)", total, skipped)


def parse_govza_cabinet_af_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Afrikaans government cabinet statements from HuggingFace Parquet files.

    Each parquet row has an 'afr' column containing a list-encoded string of
    Afrikaans sentences for one cabinet statement. We decode the list and join
    sentences into a document.

    License: CC BY 4.0  Source: dsfsi/govza-sa-cabinet-statements-sentence-aligned
    """
    try:
        import ast as _ast
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        logger.warning("[govza_cabinet_af] pyarrow not available — skipping parse")
        return

    parquet_files = sorted(raw_dir.glob("govza_cabinet_afr_*.parquet"))
    if not parquet_files:
        logger.warning("[govza_cabinet_af] No parquet files found in %s", raw_dir)
        return

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for pq_file in parquet_files:
        try:
            table = pq.read_table(str(pq_file))
        except Exception as exc:
            logger.warning("[govza_cabinet_af] Cannot read %s: %s", pq_file.name, exc)
            continue

        for i in range(table.num_rows):
            raw_val = table.column("afr")[i].as_py()
            if not raw_val:
                skipped += 1
                continue

            # The column is a string representation of a Python list of sentences
            if isinstance(raw_val, list):
                sentences = [s.strip() for s in raw_val if isinstance(s, str) and s.strip()]
            else:
                try:
                    parsed = _ast.literal_eval(str(raw_val))
                    sentences = [s.strip() for s in parsed if isinstance(s, str) and s.strip()]
                except Exception:
                    sentences = [str(raw_val).strip()]

            text = "\n".join(sentences).strip()
            if len(text) < 50:
                skipped += 1
                continue

            yield Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id("govza_cabinet_af", f"{pq_file.stem}_{i}"),
                    source="govza_cabinet_af",
                    source_url="https://huggingface.co/datasets/dsfsi/govza-sa-cabinet-statements-sentence-aligned",
                    license="CC-BY",
                    publication_year=None,
                    country="ZA",
                    domain="government",
                    language_variant="AF",
                    acquisition_date=acq_date,
                ),
            )
            total += 1

    logger.info("[govza_cabinet_af] Parsed %d documents (%d skipped)", total, skipped)


def parse_bwb_legislation_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Dutch consolidated legislation (BWB) from HuggingFace Parquet files.

    Columns: url (BWB identifier), content (XML text of law), source.
    Strips XML tags to extract plain Dutch legal text.

    License: PDDL (Public Domain)
    """
    import xml.etree.ElementTree as _ET

    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        logger.warning("[bwb_legislation] pyarrow not available — skipping parse")
        return

    parquet_files = sorted(raw_dir.glob("bwb_*.parquet"))
    if not parquet_files:
        logger.warning("[bwb_legislation] No parquet files found in %s", raw_dir)
        return

    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    def _xml_to_text(xml_str: str) -> str:
        """Extract text nodes from XML, joining with newlines."""
        try:
            root = _ET.fromstring(xml_str)
            parts = []
            for el in root.iter():
                if el.text and el.text.strip():
                    parts.append(el.text.strip())
                if el.tail and el.tail.strip():
                    parts.append(el.tail.strip())
            return "\n".join(parts)
        except _ET.ParseError:
            import re as _re
            return _re.sub(r"<[^>]+>", " ", xml_str).strip()

    for pq_file in parquet_files:
        try:
            table = pq.read_table(str(pq_file))
        except Exception as exc:
            logger.warning("[bwb_legislation] Cannot read %s: %s", pq_file.name, exc)
            continue

        for i in range(table.num_rows):
            url_val     = table.column("url")[i].as_py() or ""
            content_val = table.column("content")[i].as_py() or ""

            if not content_val:
                skipped += 1
                continue

            text = _xml_to_text(content_val) if content_val.lstrip().startswith("<") else content_val.strip()
            if len(text) < 100:
                skipped += 1
                continue

            bwb_id = url_val.split("/")[-1].replace(".xml", "") if url_val else f"{pq_file.stem}_{i}"

            yield Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id("bwb_legislation", bwb_id or f"{pq_file.stem}_{i}"),
                    source="bwb_legislation",
                    source_url=f"https://wetten.overheid.nl/{bwb_id}" if bwb_id.startswith("BWBR") else "https://wetten.overheid.nl",
                    license="PD",
                    publication_year=None,
                    country="NL",
                    domain="legal",
                    language_variant="NL",
                    acquisition_date=acq_date,
                ),
            )
            total += 1

    logger.info("[bwb_legislation] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# DANS Data Station Archaeology (CC-BY/CC0, Dutch archaeology reports)
# ---------------------------------------------------------------------------

def parse_dans_archaeology_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse DANS Data Station Archaeology records.

    Reads records.jsonl produced by acquire/dans_archaeology.py.
    Each record is a Dutch archaeology dataset with title + description text.
    License is CC-BY or CC0 (verified per-record during acquisition).
    """
    acq_date = datetime.date.today().isoformat()
    records_file = raw_dir / "records.jsonl"
    if not records_file.exists():
        logger.warning("[dans_archaeology] No records.jsonl in %s", raw_dir)
        return

    total = skipped = 0
    for line in records_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = (rec.get("text") or "").strip()
        if len(text) < 80:
            skipped += 1
            continue

        doc_id = make_id("dans_archaeology", rec.get("doi") or rec.get("id") or f"rec_{total}")
        year_raw = rec.get("publication_year")
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=doc_id,
                source="dans_archaeology",
                source_url=rec.get("source_url") or "https://archaeology.datastations.nl",
                license=rec.get("license") or "CC-BY",
                publication_year=year,
                country="NL",
                domain="historical",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )
        total += 1

    logger.info("[dans_archaeology] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Utrechts Archief — HUA finding aids / digitized document descriptions
# ---------------------------------------------------------------------------

def parse_utrechts_archief_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Het Utrechts Archief records.

    Reads records.jsonl produced by acquire/utrechts_archief.py.
    Combines records from three strategies: direct OAI-PMH, DANS deposits,
    and archieven.nl federated harvest.
    License: CC0 (HUA open data policy) or CC-BY (DANS deposits).
    """
    acq_date = datetime.date.today().isoformat()
    records_file = raw_dir / "records.jsonl"
    if not records_file.exists():
        logger.warning("[utrechts_archief] No records.jsonl in %s", raw_dir)
        return

    total = skipped = 0
    for line in records_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = (rec.get("text") or "").strip()
        if len(text) < 80:
            skipped += 1
            continue

        doc_id = make_id("utrechts_archief", rec.get("id") or rec.get("oai_id") or rec.get("doi") or f"rec_{total}")
        year_raw = rec.get("publication_year")
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=doc_id,
                source="utrechts_archief",
                source_url=rec.get("source_url") or "https://hetutrechtsarchief.nl",
                license=rec.get("license") or "CC0",
                publication_year=year,
                country="NL",
                domain="historical",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )
        total += 1

    logger.info("[utrechts_archief] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# DANS Data Station SSH — social sciences & humanities datasets
# ---------------------------------------------------------------------------

def parse_dans_ssh_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse DANS SSH records (same format as dans_archaeology)."""
    acq_date = datetime.date.today().isoformat()
    records_file = raw_dir / "records.jsonl"
    if not records_file.exists():
        logger.warning("[dans_ssh] No records.jsonl in %s", raw_dir)
        return

    total = skipped = 0
    for line in records_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = (rec.get("text") or "").strip()
        if len(text) < 80:
            skipped += 1
            continue

        doc_id = make_id("dans_ssh", rec.get("id") or rec.get("doi") or f"rec_{total}")
        year_raw = rec.get("publication_year")
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=doc_id,
                source="dans_ssh",
                source_url=rec.get("source_url") or "https://ssh.datastations.nl",
                license=rec.get("license") or "CC-BY",
                publication_year=year,
                country="NL",
                domain="academic",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )
        total += 1

    logger.info("[dans_ssh] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# DataverseNL — Dutch institutional research datasets
# ---------------------------------------------------------------------------

def parse_dataverse_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse DataverseNL records (same format as dans_archaeology)."""
    acq_date = datetime.date.today().isoformat()
    records_file = raw_dir / "records.jsonl"
    if not records_file.exists():
        logger.warning("[dataverse_nl] No records.jsonl in %s", raw_dir)
        return

    total = skipped = 0
    for line in records_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = (rec.get("text") or "").strip()
        if len(text) < 80:
            skipped += 1
            continue

        doc_id = make_id("dataverse_nl", rec.get("id") or rec.get("doi") or f"rec_{total}")
        year_raw = rec.get("publication_year")
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=doc_id,
                source="dataverse_nl",
                source_url=rec.get("source_url") or "https://dataverse.nl",
                license=rec.get("license") or "CC-BY",
                publication_year=year,
                country="NL",
                domain="academic",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )
        total += 1

    logger.info("[dataverse_nl] Parsed %d documents (%d skipped)", total, skipped)


# ---------------------------------------------------------------------------
# Global Voices NL — citizen journalism, CC-BY 3.0
# ---------------------------------------------------------------------------

def parse_global_voices_nl_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Global Voices Dutch articles (WordPress REST API output)."""
    acq_date = datetime.date.today().isoformat()
    records_file = raw_dir / "records.jsonl"
    if not records_file.exists():
        logger.warning("[global_voices_nl] No records.jsonl in %s", raw_dir)
        return

    total = skipped = 0
    for line in records_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        text = (rec.get("text") or "").strip()
        if len(text) < 80:
            skipped += 1
            continue

        doc_id = make_id("global_voices_nl", str(rec.get("id") or rec.get("wp_id") or f"rec_{total}"))
        year_raw = rec.get("publication_year")
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None

        yield Document(
            text=text,
            meta=DocumentMeta(
                id=doc_id,
                source="global_voices_nl",
                source_url=rec.get("source_url") or "https://nl.globalvoices.org",
                license="CC-BY",
                publication_year=year,
                country="NL",
                domain="news",
                language_variant="NL",
                acquisition_date=acq_date,
            ),
        )
        total += 1

    logger.info("[global_voices_nl] Parsed %d documents (%d skipped)", total, skipped)


def parse_vlaamse_codex_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse Vlaamse Codex Flemish legislation (CC-BY, OData+HTML)."""
    acq_date = datetime.date.today().isoformat()
    records_file = raw_dir / "records.jsonl"
    if not records_file.exists():
        logger.warning("[vlaamse_codex] No records.jsonl in %s", raw_dir)
        return

    total = skipped = 0
    with records_file.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            text = (rec.get("text") or "").strip()
            if len(text) < 100:
                skipped += 1
                continue

            doc_id = rec.get("id") or make_id("vlaamse_codex", str(rec.get("doc_id", total)))
            year = rec.get("publication_year")
            if year and not isinstance(year, int):
                try:
                    year = int(str(year)[:4])
                except (ValueError, TypeError):
                    year = None

            yield Document(
                text=text,
                meta=DocumentMeta(
                    id=doc_id,
                    source="vlaamse_codex",
                    source_url=rec.get("source_url") or "https://codex.vlaanderen.be",
                    license="CC-BY",
                    publication_year=year,
                    country="NL-BE",
                    domain="legal",
                    language_variant="NL-BE",
                    acquisition_date=acq_date,
                ),
            )
            total += 1

    logger.info("[vlaamse_codex] Parsed %d documents (%d skipped)", total, skipped)


def parse_spaink_dir(raw_dir: Path) -> Iterator[Document]:
    """
    Parse Karin Spaink blog posts (spaink.net, WordPress REST API).

    License: PD by author statement 2026-05-08.
    Raw data: paginated JSONL files spaink_NNNN.jsonl, one post per line.
    Each line is the raw WordPress REST API post object.
    """
    acq_date = datetime.date.today().isoformat()
    total = skipped = 0

    for jsonl_file in sorted(raw_dir.glob("spaink_*.jsonl")):
        for line in jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                post = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            # WordPress REST API fields: title.rendered, content.rendered
            title_raw   = (post.get("title")   or {}).get("rendered", "") or ""
            content_raw = (post.get("content") or {}).get("rendered", "") or ""

            title   = strip_html(title_raw).strip()
            content = strip_html(content_raw).strip()
            content = re.sub(r"\n{3,}", "\n\n", content)

            text = f"{title}\n\n{content}".strip() if title else content
            if len(text) < 100:
                skipped += 1
                continue

            post_id  = post.get("id") or post.get("slug") or f"post_{total}"
            link     = post.get("link") or "https://www.spaink.net"
            date_str = post.get("date") or ""
            year_m   = re.search(r"\b(19|20)\d{2}\b", date_str)
            year     = int(year_m.group(0)) if year_m else None

            yield Document(
                text=text,
                meta=DocumentMeta(
                    id=make_id("spaink", str(post_id)),
                    source="spaink",
                    source_url=link,
                    license="PD",
                    publication_year=year,
                    country="NL",
                    domain="journalism",
                    language_variant="NL",
                    acquisition_date=acq_date,
                ),
            )
            total += 1

    logger.info("[spaink] Parsed %d documents (%d skipped)", total, skipped)
