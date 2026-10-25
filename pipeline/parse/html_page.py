"""
Parse HTML pages saved by the Belgian and Surinamese acquirers.

Extracts:
- Page title from <title> or <h1>
- Main content text (strips nav, header, footer, sidebar, script, style)
- Publication year from <time> tags or URL patterns
- Language from <html lang="...">

"""
from __future__ import annotations

import datetime
import logging
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator

from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

# Tags to skip entirely (including children)
_SKIP_TAGS = frozenset({
    "script", "style", "nav", "header", "footer",
    "aside", "figure", "figcaption", "noscript",
    "form", "input", "button", "select", "option",
    "meta", "link", "head",
})

# Block-level tags that should produce a newline in output
_BLOCK_TAGS = frozenset({
    "p", "div", "article", "section", "main", "h1", "h2", "h3",
    "h4", "h5", "h6", "li", "td", "th", "blockquote", "pre", "br",
})


class _ContentExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []
        self._title: str = ""
        self._in_title = False
        self._lang: str = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag_lower = tag.lower()
        # title lives inside <head> which is a skip tag — handle before depth check
        if tag_lower == "title":
            self._in_title = True
            return
        if tag_lower in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag_lower == "html":
            attr_dict = dict(attrs)
            self._lang = attr_dict.get("lang", "")
        if tag_lower in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag_lower == "title":
            self._in_title = False
        if tag_lower in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        # Check _in_title first — title lives inside <head> which is a skip tag
        if self._in_title:
            self._title += data
            return
        if self._skip_depth:
            return
        if data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def extract_html(html: str) -> tuple[str, str, str]:
    """
    Extract (text, title, lang) from an HTML string.
    """
    extractor = _ContentExtractor()
    try:
        extractor.feed(html)
    except Exception as exc:
        logger.debug("HTML parse error: %s", exc)
    return extractor.get_text(), extractor._title.strip(), extractor._lang


def _infer_year(path: Path, html: str) -> int | None:
    # Try <time datetime="..."> first
    m = re.search(r'<time[^>]+datetime=["\'](\d{4})', html)
    if m:
        return int(m.group(1))
    # Try URL path components
    m = re.search(r'\b(20[012]\d|199\d)\b', str(path))
    if m:
        return int(m.group(1))
    return None


def _read_url_sidecar(file_path: Path) -> str | None:
    """Return the URL stored in a .url sidecar file, or None if absent."""
    sidecar = file_path.with_name(file_path.name + ".url")
    if sidecar.exists():
        try:
            return sidecar.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return None


def parse_html_file(
    html_file: Path,
    source: str,
    license_: str,
    country: str,
    domain: str,
    language_variant: str,
) -> Document | None:
    """Parse a single HTML file and return a Document, or None if too short."""
    try:
        raw = html_file.read_bytes()
    except OSError:
        return None

    # Detect encoding from meta charset or default to utf-8
    enc_m = re.search(rb'charset=["\']?([a-zA-Z0-9\-]+)', raw[:2000])
    encoding = enc_m.group(1).decode("ascii", errors="ignore") if enc_m else "utf-8"
    try:
        html = raw.decode(encoding, errors="replace")
    except (LookupError, ValueError):
        html = raw.decode("utf-8", errors="replace")

    text, title, lang = extract_html(html)
    if len(text) < 100:
        return None

    year = _infer_year(html_file, html)
    doc_id = make_id(source, html_file.stem)
    source_url = _read_url_sidecar(html_file) or str(html_file)

    meta = DocumentMeta(
        id=doc_id,
        source=source,
        source_url=source_url,
        license=license_,
        publication_year=year,
        country=country,
        domain=domain,
        language_variant=language_variant,
        acquisition_date=datetime.date.today().isoformat(),
    )
    return Document(text=text, meta=meta)


# ---------------------------------------------------------------------------
# Directory parsers for each HTML-producing acquirer
# ---------------------------------------------------------------------------

def _extract_dutch_paragraphs(text: str) -> str:
    """
    From bilingual (Dutch/French) text extracted from a Belgian Staatsblad PDF,
    return only Dutch-language paragraphs.

    Uses langdetect if available; falls back to a Dutch function-word heuristic.
    """
    try:
        from langdetect import detect as _detect
        _have_langdetect = True
    except ImportError:
        _have_langdetect = False

    def _is_dutch(para: str) -> bool:
        if len(para) < 30:
            return False
        if _have_langdetect:
            try:
                return _detect(para) == "nl"
            except Exception:
                pass
        # Heuristic: Dutch function words
        dutch_words = {"de", "het", "van", "een", "in", "op", "en", "te", "dat",
                       "die", "met", "zijn", "als", "aan", "er", "om", "ook",
                       "niet", "bij", "voor", "over", "worden", "wordt", "werd"}
        words = set(re.findall(r'\b[a-z]{2,}\b', para.lower()))
        return len(words & dutch_words) >= 3

    paras = re.split(r'\n{2,}', text)
    dutch = [p.strip() for p in paras if _is_dutch(p.strip())]
    return "\n\n".join(dutch)


def parse_belgian_dir(raw_dir: Path) -> Iterator[Document]:
    from pipeline.parse.pdf_text import extract_text as _extract_pdf

    acq_date = datetime.date.today().isoformat()

    for subdir_name, source, domain in [
        ("staatsblad", "belgisch_staatsblad", "legal"),
        ("justel",     "justel",              "legal"),
    ]:
        subdir = raw_dir / subdir_name
        # Fallback: if the direct path is missing or empty, check one level
        # deeper (data/raw/belgian/belgian/staatsblad/). This happens when the
        # acquirer was run with --output data/raw/belgian/ instead of data/raw/.
        if not subdir.exists() or not any(subdir.rglob("*.pdf")) and not any(subdir.rglob("*.html")):
            alt = raw_dir / raw_dir.name / subdir_name
            if alt.exists():
                subdir = alt
        if not subdir.exists():
            continue

        # HTML files (legacy ELI-based downloads)
        for html_file in sorted(subdir.rglob("*.html")):
            doc = parse_html_file(
                html_file,
                source=source,
                license_="PD",
                country="BE",
                domain=domain,
                language_variant="NL-BE",
            )
            if doc:
                yield doc

        # PDF files (mopdf daily issue downloads)
        for pdf_file in sorted(subdir.rglob("*.pdf")):
            raw_text = _extract_pdf(pdf_file)
            if not raw_text:
                continue
            text = _extract_dutch_paragraphs(raw_text)
            if len(text) < 200:
                continue
            year_m = re.search(r'\b(20\d{2})\b', pdf_file.stem)
            year   = int(year_m.group(1)) if year_m else None
            doc_id = make_id(source, pdf_file.stem)
            url    = _read_url_sidecar(pdf_file) or str(pdf_file)
            meta   = DocumentMeta(
                id=doc_id,
                source=source,
                source_url=url,
                license="PD",
                publication_year=year,
                country="BE",
                domain=domain,
                language_variant="NL-BE",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)


def parse_suriname_dir(raw_dir: Path) -> Iterator[Document]:
    import json as _json
    from pipeline.parse.pdf_text import extract_text as extract_pdf

    # (source_name, domain, country) — language_variant is always "NL" for
    # Surinamese Dutch (SR is the ISO country code, not a language code)
    source_map = {
        "staatsblad":       ("suriname_staatsblad",  "legal",         "SR"),
        "dna":              ("suriname_dna",          "parliamentary", "SR"),
        "rechtspraak_sr":   ("suriname_rechtspraak",  "legal",         "SR"),
        "national_archives_sr": ("suriname_archives", "historical",    "SR"),
    }

    for subdir_name, (source, domain, country) in source_map.items():
        subdir = raw_dir / subdir_name
        if not subdir.exists():
            continue

        acq_date = datetime.date.today().isoformat()

        # HTML files
        for html_file in sorted(subdir.rglob("*.html")):
            doc = parse_html_file(
                html_file,
                source=source,
                license_="PD",
                country=country,
                domain=domain,
                language_variant="NL",
            )
            if doc:
                yield doc

        # PDF files
        for pdf_file in sorted(subdir.rglob("*.pdf")):
            text = extract_pdf(pdf_file)
            if not text:
                continue
            year_m = re.search(r'\b(19|20)\d{2}\b', str(pdf_file))
            year = int(year_m.group(0)) if year_m else None
            doc_id = make_id(source, pdf_file.stem)
            pdf_source_url = _read_url_sidecar(pdf_file) or str(pdf_file)
            meta = DocumentMeta(
                id=doc_id,
                source=source,
                source_url=pdf_source_url,
                license="PD",
                publication_year=year,
                country=country,
                domain=domain,
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=text, meta=meta)

        # JSON files (Openarchieven national_archives_sr)
        for json_file in sorted(subdir.rglob("*.json")):
            try:
                data = _json.loads(json_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[suriname] Cannot read %s: %s", json_file, exc)
                continue
            records = data.get("records", data.get("results", []))
            if not isinstance(records, list):
                continue
            for rec in records:
                # Extract text from description/title/content fields
                parts = [
                    rec.get("title", ""),
                    rec.get("description", ""),
                    rec.get("content", ""),
                    rec.get("transcription", ""),
                ]
                text = "\n\n".join(p for p in parts if p and isinstance(p, str)).strip()
                if len(text) < 50:
                    continue
                raw_id = str(rec.get("id", json_file.stem))
                source_url = rec.get("url", rec.get("permalink", "https://www.openarchieven.nl"))
                year_str = str(rec.get("date", rec.get("year", "")))
                year_m = re.search(r'\b(1[5-9]\d{2}|20\d{2})\b', year_str)
                year = int(year_m.group(0)) if year_m else None
                meta = DocumentMeta(
                    id=make_id(source, raw_id),
                    source=source,
                    source_url=source_url,
                    license="CC0",  # Openarchieven is CC0
                    publication_year=year,
                    country=country,
                    domain=domain,
                    language_variant="NL",
                    acquisition_date=acq_date,
                )
                yield Document(text=text, meta=meta)
