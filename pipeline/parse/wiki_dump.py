"""
Stream and parse the Dutch Wikipedia XML dump.

The dump is a bz2-compressed XML file. We stream it without loading it
entirely into memory using iterparse, then strip wikitext markup.

Skips:
  - Redirects
  - Non-article namespaces (Talk, User, Template, etc.) — namespace 0 only
  - Stubs shorter than min_chars

Dependency: standard library only (no mwparserfromhell needed).
"""
from __future__ import annotations

import bz2
import datetime
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

MW_NS = "http://www.mediawiki.org/xml/export-0.11/"


# ---------------------------------------------------------------------------
# Wikitext stripping
# ---------------------------------------------------------------------------

def _strip_wikitext(text: str) -> str:
    """Convert wikitext markup to plain text (best-effort)."""

    # Remove <ref>...</ref> and <ref ... /> citation tags
    text = re.sub(r"<ref[^>]*/?>.*?</ref>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/?>", " ", text)

    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # Remove tables {{| ... |}} and wikitables {| ... |}
    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.DOTALL)

    # Remove deeply nested templates {{...}} iteratively (up to 5 passes)
    for _ in range(5):
        prev = text
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
        if text == prev:
            break

    # Remove [[File:...]] and [[Image:...]] / [[Bestand:...]]
    text = re.sub(
        r"\[\[(?:File|Image|Bestand|Afbeelding|Media)[^\]]*\]\]",
        " ", text, flags=re.IGNORECASE
    )

    # Convert [[link|display text]] → display text
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)

    # Remove external links [url text] → text
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\]", " ", text)

    # Remove section headings markup (keep text)
    text = re.sub(r"={2,6}\s*(.+?)\s*={2,6}", r"\1", text)

    # Remove bold/italic markup
    text = re.sub(r"'{2,3}", "", text)

    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # Remove list/indent markers at line start
    text = re.sub(r"^[*#:;]+\s*", "", text, flags=re.MULTILINE)

    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _is_redirect(text: str) -> bool:
    return text.strip().lower().startswith("#doorverwijzing") or \
           text.strip().lower().startswith("#redirect")


# ---------------------------------------------------------------------------
# Dump parser
# ---------------------------------------------------------------------------

def parse_dump(dump_path: Path, min_chars: int = 200) -> Iterator[Document]:
    """
    Stream documents from a bz2-compressed Wikipedia XML dump.

    Args:
        dump_path: Path to the .xml.bz2 dump file.
        min_chars: Minimum character count for a document to be kept.
    """
    if not dump_path.exists():
        logger.error("Dump not found: %s", dump_path)
        return

    acq_date = datetime.date.today().isoformat()
    tag_page   = f"{{{MW_NS}}}page"
    tag_ns     = f"{{{MW_NS}}}ns"
    tag_title  = f"{{{MW_NS}}}title"
    tag_id     = f"{{{MW_NS}}}id"
    tag_text   = f"{{{MW_NS}}}text"
    tag_ts     = f"{{{MW_NS}}}timestamp"

    logger.info("Streaming Wikipedia dump: %s", dump_path)
    count = 0

    opener = bz2.open if dump_path.suffix == ".bz2" else open
    with opener(dump_path, "rb") as fh:
        for event, elem in ET.iterparse(fh, events=("end",)):
            if elem.tag != tag_page:
                continue

            # Only main namespace (0)
            ns_el = elem.find(tag_ns)
            if ns_el is None or ns_el.text != "0":
                elem.clear()
                continue

            title_el = elem.find(tag_title)
            id_el    = elem.find(tag_id)
            rev      = elem.find(f".//{{{MW_NS}}}revision")
            text_el  = rev.find(tag_text) if rev is not None else None
            ts_el    = rev.find(tag_ts)   if rev is not None else None

            raw_text = text_el.text if text_el is not None and text_el.text else ""

            if _is_redirect(raw_text):
                elem.clear()
                continue

            plain = _strip_wikitext(raw_text)
            if len(plain) < min_chars:
                elem.clear()
                continue

            title = title_el.text if title_el is not None else ""
            page_id = id_el.text if id_el is not None else ""
            ts = ts_el.text if ts_el is not None else ""
            year = int(ts[:4]) if ts and len(ts) >= 4 and ts[:4].isdigit() else None

            doc_id = make_id("wikipedia", page_id or title)
            meta = DocumentMeta(
                id=doc_id,
                source="wikipedia",
                source_url=f"https://nl.wikipedia.org/wiki/{title.replace(' ', '_')}",
                license="CC-BY-SA",
                publication_year=year,
                country="NL",
                domain="encyclopedic",
                language_variant="NL",
                acquisition_date=acq_date,
            )
            yield Document(text=plain, meta=meta)
            count += 1
            if count % 10_000 == 0:
                logger.info("Parsed %d Wikipedia articles", count)

            elem.clear()

    logger.info("Wikipedia parse complete: %d articles", count)


def parse_dir(raw_dir: Path) -> Iterator[Document]:
    """Find the dump file in raw_dir and parse it."""
    for pattern in ("*.xml.bz2", "*.xml"):
        for f in raw_dir.glob(pattern):
            yield from parse_dump(f)
            return
    logger.warning("No Wikipedia dump found in %s", raw_dir)


def _parse_wiki_variant(
    raw_dir: Path,
    source: str,
    license_: str,
    domain: str,
    base_url: str,
) -> Iterator[Document]:
    """Generic re-labelling wrapper for any Wikimedia dump."""
    acq_date = datetime.date.today().isoformat()

    dump_path: Path | None = None
    for pattern in ("*.xml.bz2", "*.xml"):
        for f in sorted(raw_dir.glob(pattern)):
            dump_path = f
            break
        if dump_path:
            break

    if not dump_path:
        logger.warning("No %s dump found in %s", source, raw_dir)
        return

    logger.info("Streaming %s dump: %s", source, dump_path)

    for doc in parse_dump(dump_path, min_chars=200):
        title_slug = doc.meta.source_url.split("/wiki/")[-1]
        new_meta = DocumentMeta(
            id=doc.meta.id.replace("wikipedia:", f"{source}:", 1),
            source=source,
            source_url=f"{base_url}{title_slug}",
            license=license_,
            publication_year=doc.meta.publication_year,
            country="NL",
            domain=domain,
            language_variant="NL",
            acquisition_date=acq_date,
        )
        yield Document(text=doc.text, meta=new_meta)


def parse_wikisource_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse a Dutch Wikisource XML dump (CC-BY-SA, domain=literature)."""
    yield from _parse_wiki_variant(
        raw_dir,
        source="wikisource",
        license_="CC-BY-SA",
        domain="literature",
        base_url="https://nl.wikisource.org/wiki/",
    )


def parse_wikibooks_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse a Dutch Wikibooks XML dump (CC-BY-SA, domain=educational)."""
    yield from _parse_wiki_variant(
        raw_dir,
        source="wikibooks",
        license_="CC-BY-SA",
        domain="educational",
        base_url="https://nl.wikibooks.org/wiki/",
    )


def parse_wikinews_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse a Dutch Wikinews XML dump (CC-BY, domain=news)."""
    yield from _parse_wiki_variant(
        raw_dir,
        source="wikinews",
        license_="CC-BY",
        domain="news",
        base_url="https://nl.wikinews.org/wiki/",
    )


def parse_wikiquote_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse a Dutch Wikiquote XML dump (CC-BY-SA, domain=quotations)."""
    yield from _parse_wiki_variant(
        raw_dir,
        source="wikiquote",
        license_="CC-BY-SA",
        domain="quotations",
        base_url="https://nl.wikiquote.org/wiki/",
    )


def parse_wikivoyage_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse a Dutch Wikivoyage XML dump (CC-BY-SA, domain=travel)."""
    yield from _parse_wiki_variant(
        raw_dir,
        source="wikivoyage",
        license_="CC-BY-SA",
        domain="travel",
        base_url="https://nl.wikivoyage.org/wiki/",
    )


def parse_wikiversity_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse a Dutch Wikiversity XML dump (CC-BY-SA, domain=educational)."""
    yield from _parse_wiki_variant(
        raw_dir,
        source="wikiversity",
        license_="CC-BY-SA",
        domain="educational",
        base_url="https://nl.wikiversity.org/wiki/",
    )


def parse_wiktionary_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse a Dutch Wiktionary XML dump (CC-BY-SA, domain=lexical)."""
    yield from _parse_wiki_variant(
        raw_dir,
        source="wiktionary",
        license_="CC-BY-SA",
        domain="lexical",
        base_url="https://nl.wiktionary.org/wiki/",
    )


def parse_frisian_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Frisian Wikipedia XML dump (CC-BY-SA, language=fy-NL)."""
    acq_date = datetime.date.today().isoformat()

    dump_path: Path | None = None
    for pattern in ("*.xml.bz2", "*.xml"):
        for f in sorted(raw_dir.glob(pattern)):
            dump_path = f
            break
        if dump_path:
            break

    if not dump_path:
        logger.warning("No Frisian Wikipedia dump found in %s", raw_dir)
        return

    logger.info("Streaming Frisian Wikipedia dump: %s", dump_path)

    for doc in parse_dump(dump_path, min_chars=200):
        title_slug = doc.meta.source_url.split("/wiki/")[-1]
        new_meta = DocumentMeta(
            id=doc.meta.id.replace("wikipedia:", "frisian_wiki:", 1),
            source="frisian_wiki",
            source_url=f"https://fy.wikipedia.org/wiki/{title_slug}",
            license="CC-BY-SA",
            publication_year=doc.meta.publication_year,
            country="NL",
            domain="encyclopedic",
            language_variant="FY",
            acquisition_date=acq_date,
        )
        yield Document(text=doc.text, meta=new_meta)


def _parse_dialect_wiki(
    raw_dir: Path,
    source: str,
    wiki_prefix: str,
    base_url: str,
    language_variant: str,
    country: str,
) -> Iterator[Document]:
    """Parse a dialect or closely-related-language Wikipedia dump."""
    acq_date = datetime.date.today().isoformat()

    dump_path: Path | None = None
    for pattern in ("*.xml.bz2", "*.xml"):
        for f in sorted(raw_dir.glob(pattern)):
            dump_path = f
            break
        if dump_path:
            break

    if not dump_path:
        logger.warning("No %s dump found in %s", source, raw_dir)
        return

    logger.info("Streaming %s dump: %s", source, dump_path)

    for doc in parse_dump(dump_path, min_chars=200):
        title_slug = doc.meta.source_url.split("/wiki/")[-1]
        new_meta = DocumentMeta(
            id=doc.meta.id.replace("wikipedia:", f"{source}:", 1),
            source=source,
            source_url=f"{base_url}{title_slug}",
            license="CC-BY-SA",
            publication_year=doc.meta.publication_year,
            country=country,
            domain="encyclopedic",
            language_variant=language_variant,
            acquisition_date=acq_date,
        )
        yield Document(text=doc.text, meta=new_meta)


def parse_limburgish_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Limburgish (li) Wikipedia dump (CC-BY-SA)."""
    yield from _parse_dialect_wiki(
        raw_dir, "limburgish_wiki", "liwiki",
        "https://li.wikipedia.org/wiki/",
        language_variant="LI", country="NL",
    )


def parse_west_flemish_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the West Flemish (vls) Wikipedia dump (CC-BY-SA)."""
    yield from _parse_dialect_wiki(
        raw_dir, "west_flemish_wiki", "vlswiki",
        "https://vls.wikipedia.org/wiki/",
        language_variant="VLS", country="BE",
    )


def parse_zeelandic_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Zeelandic (zea) Wikipedia dump (CC-BY-SA)."""
    yield from _parse_dialect_wiki(
        raw_dir, "zeelandic_wiki", "zeawiki",
        "https://zea.wikipedia.org/wiki/",
        language_variant="ZEA", country="NL",
    )


def parse_afrikaans_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Afrikaans (af) Wikipedia dump (CC-BY-SA)."""
    yield from _parse_dialect_wiki(
        raw_dir, "afrikaans_wiki", "afwiki",
        "https://af.wikipedia.org/wiki/",
        language_variant="AF", country="ZA",
    )


def parse_low_saxon_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Dutch Low Saxon (nds-nl) Wikipedia dump (CC-BY-SA).

    Covers Gronings, Drents, Twents, Stellingwerfs, Achterhoeks, Veluws, Sallands.
    """
    yield from _parse_dialect_wiki(
        raw_dir, "low_saxon_wiki", "nds_nlwiki",
        "https://nds-nl.wikipedia.org/wiki/",
        language_variant="NDS-NL", country="NL",
    )


def parse_north_frisian_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the North Frisian (frr) Wikipedia dump (CC-BY-SA).

    North Frisian is spoken in Schleswig-Holstein, Germany (~10,000 speakers).
    """
    yield from _parse_dialect_wiki(
        raw_dir, "north_frisian_wiki", "frrwiki",
        "https://frr.wikipedia.org/wiki/",
        language_variant="FRR", country="DE",
    )


def parse_saterland_frisian_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Saterland Frisian (stq) Wikipedia dump (CC-BY-SA).

    Saterland Frisian (Seeltersk) is spoken in Lower Saxony, Germany (~2,000 speakers).
    """
    yield from _parse_dialect_wiki(
        raw_dir, "saterland_frisian_wiki", "stqwiki",
        "https://stq.wikipedia.org/wiki/",
        language_variant="STQ", country="DE",
    )


def parse_papiamentu_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Papiamentu (pap) Wikipedia dump (CC-BY-SA).

    Papiamentu is a Dutch-influenced creole of Aruba, Curaçao, and Bonaire
    (Kingdom of the Netherlands, Dutch Caribbean).
    """
    yield from _parse_dialect_wiki(
        raw_dir, "papiamentu_wiki", "papwiki",
        "https://pap.wikipedia.org/wiki/",
        language_variant="PAP", country="NL",
    )


def parse_sranantongo_wiki_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Sranan Tongo (srn) Wikipedia dump (CC-BY-SA).

    Sranan Tongo is a Dutch/English-influenced creole of Suriname, a former
    Dutch colony. It is the most widely spoken language in Suriname.
    """
    yield from _parse_dialect_wiki(
        raw_dir, "sranantongo_wiki", "srnwiki",
        "https://srn.wikipedia.org/wiki/",
        language_variant="SRN", country="SR",
    )


def parse_afrikaans_wiktionary_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Afrikaans Wiktionary dump (6MB, CC-BY-SA).

    Afrikaans Wiktionary contains lexical definitions, usage examples,
    and etymologies written in Afrikaans — a distinct text register from
    the encyclopedic content of afrikaans_wiki.
    """
    yield from _parse_dialect_wiki(
        raw_dir, "afrikaans_wiktionary", "afwiktionary",
        "https://af.wiktionary.org/wiki/",
        language_variant="AF", country="ZA",
    )


def parse_limburgish_wiktionary_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Limburgish Wiktionary dump (18MB, CC-BY-SA).

    At 18MB, the Limburgish Wiktionary is larger than the Limburgish
    Wikipedia itself, making it the single largest source of Limburgish text.
    Contains definitions and usage in the Limburgish language.
    """
    yield from _parse_dialect_wiki(
        raw_dir, "limburgish_wiktionary", "liwiktionary",
        "https://li.wiktionary.org/wiki/",
        language_variant="LI", country="NL",
    )


def parse_frisian_wiktionary_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the West Frisian Wiktionary dump (CC-BY-SA).

    Frisian Wiktionary contains lexical definitions and usage examples
    in West Frisian — distinct register from the encyclopedic frisian_wiki.
    """
    yield from _parse_dialect_wiki(
        raw_dir, "frisian_wiktionary", "fywiktionary",
        "https://fy.wiktionary.org/wiki/",
        language_variant="FY", country="NL",
    )


def _parse_dialect_wiki_variant(
    raw_dir: Path,
    source: str,
    base_url: str,
    language_variant: str,
    country: str,
    license_: str,
    domain: str,
) -> Iterator[Document]:
    """Parse a dialect/language wiki dump with configurable domain and license."""
    acq_date = datetime.date.today().isoformat()

    dump_path: Path | None = None
    for pattern in ("*.xml.bz2", "*.xml"):
        for f in sorted(raw_dir.glob(pattern)):
            dump_path = f
            break
        if dump_path:
            break

    if not dump_path:
        logger.warning("No %s dump found in %s", source, raw_dir)
        return

    logger.info("Streaming %s dump: %s", source, dump_path)

    for doc in parse_dump(dump_path, min_chars=200):
        title_slug = doc.meta.source_url.split("/wiki/")[-1]
        new_meta = DocumentMeta(
            id=doc.meta.id.replace("wikipedia:", f"{source}:", 1),
            source=source,
            source_url=f"{base_url}{title_slug}",
            license=license_,
            publication_year=doc.meta.publication_year,
            country=country,
            domain=domain,
            language_variant=language_variant,
            acquisition_date=acq_date,
        )
        yield Document(text=doc.text, meta=new_meta)


# ---------------------------------------------------------------------------
# Limburgish Wikimedia sister projects
# ---------------------------------------------------------------------------

def parse_limburgish_wikisource_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Limburgish (li) Wikisource dump (CC-BY-SA, domain=literature)."""
    yield from _parse_dialect_wiki_variant(
        raw_dir, "limburgish_wikisource",
        "https://li.wikisource.org/wiki/",
        language_variant="LI", country="NL",
        license_="CC-BY-SA", domain="literature",
    )


def parse_limburgish_wikibooks_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Limburgish (li) Wikibooks dump (CC-BY-SA, domain=educational)."""
    yield from _parse_dialect_wiki_variant(
        raw_dir, "limburgish_wikibooks",
        "https://li.wikibooks.org/wiki/",
        language_variant="LI", country="NL",
        license_="CC-BY-SA", domain="educational",
    )


def parse_limburgish_wikiquote_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Limburgish (li) Wikiquote dump (CC-BY-SA, domain=quotations)."""
    yield from _parse_dialect_wiki_variant(
        raw_dir, "limburgish_wikiquote",
        "https://li.wikiquote.org/wiki/",
        language_variant="LI", country="NL",
        license_="CC-BY-SA", domain="quotations",
    )


def parse_limburgish_wikinews_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Limburgish (li) Wikinews dump (CC-BY, domain=news).

    Wikinews uses CC-BY (not CC-BY-SA) as its license.
    """
    yield from _parse_dialect_wiki_variant(
        raw_dir, "limburgish_wikinews",
        "https://li.wikinews.org/wiki/",
        language_variant="LI", country="NL",
        license_="CC-BY", domain="news",
    )


# ---------------------------------------------------------------------------
# West Frisian Wikimedia sister projects
# ---------------------------------------------------------------------------

def parse_frisian_wikibooks_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the West Frisian (fy) Wikibooks dump (CC-BY-SA, domain=educational)."""
    yield from _parse_dialect_wiki_variant(
        raw_dir, "frisian_wikibooks",
        "https://fy.wikibooks.org/wiki/",
        language_variant="FY", country="NL",
        license_="CC-BY-SA", domain="educational",
    )


# ---------------------------------------------------------------------------
# Afrikaans Wikimedia sister projects
# ---------------------------------------------------------------------------

def parse_afrikaans_wikibooks_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Afrikaans (af) Wikibooks dump (CC-BY-SA, domain=educational).

    Afrikaans is a daughter language of Dutch descended from 17th-century Cape Dutch.
    """
    yield from _parse_dialect_wiki_variant(
        raw_dir, "afrikaans_wikibooks",
        "https://af.wikibooks.org/wiki/",
        language_variant="AF", country="ZA",
        license_="CC-BY-SA", domain="educational",
    )


def parse_afrikaans_wikiquote_dir(raw_dir: Path) -> Iterator[Document]:
    """Parse the Afrikaans (af) Wikiquote dump (CC-BY-SA, domain=quotations)."""
    yield from _parse_dialect_wiki_variant(
        raw_dir, "afrikaans_wikiquote",
        "https://af.wikiquote.org/wiki/",
        language_variant="AF", country="ZA",
        license_="CC-BY-SA", domain="quotations",
    )
