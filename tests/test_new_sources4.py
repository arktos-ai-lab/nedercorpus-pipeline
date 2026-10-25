"""
Tests for dialect Wikipedia parsers (Limburgish, West Flemish, Zeelandic,
Afrikaans, Low Saxon) and Europeana NER parser.
"""
import bz2
import tempfile
from pathlib import Path

from pipeline.parse.wiki_dump import (
    parse_limburgish_wiki_dir,
    parse_west_flemish_wiki_dir,
    parse_zeelandic_wiki_dir,
    parse_afrikaans_wiki_dir,
    parse_low_saxon_wiki_dir,
)
from pipeline.parse.plain_text import parse_europeana_ner_dir


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_wiki_dump(title: str, text: str) -> bytes:
    xml = f"""<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
  <page>
    <title>{title}</title>
    <ns>0</ns>
    <id>42</id>
    <revision>
      <id>1001</id>
      <timestamp>2023-06-15T12:00:00Z</timestamp>
      <text>{text}</text>
    </revision>
  </page>
</mediawiki>""".encode("utf-8")
    return bz2.compress(xml)


_LONG_TEXT = (
    "Dit artikel beschrijft de geschiedenis en cultuur van de regio. "
    "De regio heeft een rijke traditie van lokale dialecten en gebruiken. "
    "Veel inwoners spreken naast de standaardtaal ook hun eigen dialect. "
    "De economie van het gebied is gevarieerd met landbouw, industrie en toerisme. "
    "De lokale gemeenschappen houden jaarlijkse festivals en culturele evenementen. "
    "Het landschap bestaat uit rivieren, heuvels en uitgestrekte velden."
)


# ---------------------------------------------------------------------------
# Limburgish Wikipedia
# ---------------------------------------------------------------------------

def test_parse_limburgish_wiki_yields_document():
    dump = _make_wiki_dump("Mestreech", _LONG_TEXT)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "liwiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_limburgish_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "limburgish_wiki"
    assert d.meta.license == "CC-BY-SA"
    assert d.meta.language_variant == "LI"
    assert d.meta.country == "NL"
    assert d.meta.domain == "encyclopedic"
    assert "li.wikipedia.org" in d.meta.source_url


def test_parse_limburgish_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_limburgish_wiki_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# West Flemish Wikipedia
# ---------------------------------------------------------------------------

def test_parse_west_flemish_wiki_yields_document():
    dump = _make_wiki_dump("Brugge", _LONG_TEXT)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "vlswiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_west_flemish_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "west_flemish_wiki"
    assert d.meta.language_variant == "VLS"
    assert d.meta.country == "BE"
    assert "vls.wikipedia.org" in d.meta.source_url


def test_parse_west_flemish_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_west_flemish_wiki_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Zeelandic Wikipedia
# ---------------------------------------------------------------------------

def test_parse_zeelandic_wiki_yields_document():
    dump = _make_wiki_dump("Middelburg", _LONG_TEXT)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "zeawiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_zeelandic_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "zeelandic_wiki"
    assert d.meta.language_variant == "ZEA"
    assert d.meta.country == "NL"
    assert "zea.wikipedia.org" in d.meta.source_url


def test_parse_zeelandic_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_zeelandic_wiki_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Afrikaans Wikipedia
# ---------------------------------------------------------------------------

def test_parse_afrikaans_wiki_yields_document():
    dump = _make_wiki_dump("Kaapstad", _LONG_TEXT)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "afwiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_afrikaans_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "afrikaans_wiki"
    assert d.meta.language_variant == "AF"
    assert d.meta.country == "ZA"
    assert "af.wikipedia.org" in d.meta.source_url


def test_parse_afrikaans_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_afrikaans_wiki_dir(Path(tmp)))
    assert docs == []


def test_parse_afrikaans_wiki_skips_redirect():
    dump = _make_wiki_dump("Verwysing", "#redirect [[Kaapstad]]")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "afwiki.xml.bz2").write_bytes(dump)
        docs = list(parse_afrikaans_wiki_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Low Saxon (nds-nl) Wikipedia
# ---------------------------------------------------------------------------

def test_parse_low_saxon_wiki_yields_document():
    dump = _make_wiki_dump("Grunnen", _LONG_TEXT)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nds_nlwiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_low_saxon_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "low_saxon_wiki"
    assert d.meta.language_variant == "NDS-NL"
    assert d.meta.country == "NL"
    assert "nds-nl.wikipedia.org" in d.meta.source_url


def test_parse_low_saxon_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_low_saxon_wiki_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Europeana Newspapers NER
# ---------------------------------------------------------------------------

def _make_bio_file(n_sentences: int = 50) -> bytes:
    """Create a synthetic BIO-tagged Dutch newspaper file."""
    lines = []
    words = [
        "De", "Nederlandsche", "Regeering", "heeft", "besloten", "dat",
        "de", "wet", "zal", "worden", "ingevoerd", ".",
    ]
    for i in range(n_sentences):
        for j, word in enumerate(words):
            tag = "B-ORG" if j == 1 else ("I-ORG" if j == 2 else "O")
            lines.append(f"{word}\t{tag}")
        lines.append("")  # sentence boundary
    return "\n".join(lines).encode("utf-8")


def test_parse_europeana_ner_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        bio_dir = p / "bio"
        bio_dir.mkdir()
        (bio_dir / "enp_NL.kb.bio").write_bytes(_make_bio_file(300))
        docs = list(parse_europeana_ner_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "europeana_ner"
    assert d.meta.license == "CC0"
    assert d.meta.domain == "news"
    assert d.meta.country == "NL"
    assert "EuropeanaNewspapers" in d.meta.source_url


def test_parse_europeana_ner_extracts_tokens():
    bio_content = b"Het\tO\nVaderland\tB-ORG\nmeldt\tO\nnieuws\tO\n.\tO\n\n"
    bio_content *= 250  # enough for min length
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        bio_dir = p / "bio"
        bio_dir.mkdir()
        (bio_dir / "enp_NL.kb.bio").write_bytes(bio_content)
        docs = list(parse_europeana_ner_dir(p))
    assert len(docs) >= 1
    # Tokens should be joined (NE tags stripped)
    text = " ".join(d.text for d in docs)
    assert "Vaderland" in text
    assert "B-ORG" not in text


def test_parse_europeana_ner_no_bio_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_europeana_ner_dir(Path(tmp)))
    assert docs == []


def test_parse_europeana_ner_only_nl_files():
    """Only enp_NL.*.bio files should be parsed; German/French files ignored."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        bio_dir = p / "bio"
        bio_dir.mkdir()
        (bio_dir / "enp_NL.kb.bio").write_bytes(_make_bio_file(300))
        (bio_dir / "enp_DE.lft.bio").write_bytes(_make_bio_file(300))
        (bio_dir / "enp_FR.bnf.bio").write_bytes(_make_bio_file(300))
        docs = list(parse_europeana_ner_dir(p))
    # All docs should be from the NL file only
    assert all(d.meta.source == "europeana_ner" for d in docs)
    # Only 1 NL file processed — not 3
    sources_ids = {d.meta.id for d in docs}
    assert all("enp_NL.kb" in doc_id for doc_id in sources_ids)
