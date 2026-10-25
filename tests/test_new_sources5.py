"""
Tests for: North Frisian, Saterland Frisian, Papiamentu, Sranan Tongo Wikipedia
parsers, and Brieven als Buit parser.
"""
import bz2
import tempfile
from pathlib import Path

from pipeline.parse.wiki_dump import (
    parse_north_frisian_wiki_dir,
    parse_saterland_frisian_wiki_dir,
    parse_papiamentu_wiki_dir,
    parse_sranantongo_wiki_dir,
)
from pipeline.parse.plain_text import parse_brieven_als_buit_dir


def _make_wiki_dump(title: str, text: str) -> bytes:
    xml = f"""<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
  <page>
    <title>{title}</title>
    <ns>0</ns>
    <id>7</id>
    <revision>
      <id>101</id>
      <timestamp>2023-09-01T00:00:00Z</timestamp>
      <text>{text}</text>
    </revision>
  </page>
</mediawiki>""".encode("utf-8")
    return bz2.compress(xml)


_LONG = (
    "Dit artikel giet oer de skiednis fan dizze regio en har kultuer. "
    "De befolking hat har eigen taal en tradysjes dy't al ieuwen besteane. "
    "De ekonomy is farieare mei lânbou, yndustry en toerisme as haadsektoaren. "
    "Elk jier wurde kulturele eveneminten hâlden foar de lokale mienskip."
)


# ---------------------------------------------------------------------------
# North Frisian Wikipedia
# ---------------------------------------------------------------------------

def test_parse_north_frisian_wiki_yields_document():
    dump = _make_wiki_dump("Sölring", _LONG)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "frrwiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_north_frisian_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "north_frisian_wiki"
    assert d.meta.license == "CC-BY-SA"
    assert d.meta.language_variant == "FRR"
    assert d.meta.country == "DE"
    assert "frr.wikipedia.org" in d.meta.source_url


def test_parse_north_frisian_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_north_frisian_wiki_dir(Path(tmp))) == []


# ---------------------------------------------------------------------------
# Saterland Frisian Wikipedia
# ---------------------------------------------------------------------------

def test_parse_saterland_frisian_wiki_yields_document():
    dump = _make_wiki_dump("Seelterlound", _LONG)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "stqwiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_saterland_frisian_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "saterland_frisian_wiki"
    assert d.meta.language_variant == "STQ"
    assert d.meta.country == "DE"
    assert "stq.wikipedia.org" in d.meta.source_url


def test_parse_saterland_frisian_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_saterland_frisian_wiki_dir(Path(tmp))) == []


# ---------------------------------------------------------------------------
# Papiamentu Wikipedia
# ---------------------------------------------------------------------------

def test_parse_papiamentu_wiki_yields_document():
    dump = _make_wiki_dump("Kòrsou", _LONG)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "papwiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_papiamentu_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "papiamentu_wiki"
    assert d.meta.language_variant == "PAP"
    assert d.meta.country == "NL"
    assert "pap.wikipedia.org" in d.meta.source_url


def test_parse_papiamentu_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_papiamentu_wiki_dir(Path(tmp))) == []


# ---------------------------------------------------------------------------
# Sranan Tongo Wikipedia
# ---------------------------------------------------------------------------

def test_parse_sranantongo_wiki_yields_document():
    dump = _make_wiki_dump("Sranan Kondre", _LONG)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "srnwiki-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_sranantongo_wiki_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "sranantongo_wiki"
    assert d.meta.language_variant == "SRN"
    assert d.meta.country == "SR"
    assert "srn.wikipedia.org" in d.meta.source_url


def test_parse_sranantongo_wiki_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_sranantongo_wiki_dir(Path(tmp))) == []


# ---------------------------------------------------------------------------
# Brieven als Buit — plain text letters
# ---------------------------------------------------------------------------

_LETTER_TEXT = (
    "Lieve vrouw, ick schrijve u desen brief uyt de Stadt Amsterdam "
    "den 15 Martii 1702. Ick hope dat ghij ende de kinderen in goede "
    "gesondtheyt sijn. Wij sijn gisteren aengecomen met het schip de "
    "Eendracht ende hebben een goede reys gehadt. Het weer was goed "
    "ende wij hebben geen storm gehadt. Ick verhope u haest te sien. "
    "Uwen lieven man, Jan Pietersen."
)


def test_parse_brieven_als_buit_plain_text():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        letters = p / "letters"
        letters.mkdir()
        (letters / "brief_001.txt").write_text(_LETTER_TEXT * 3, encoding="utf-8")
        docs = list(parse_brieven_als_buit_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "brieven_als_buit"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "historical"
    assert d.meta.country == "NL"
    assert d.meta.language_variant == "NL"
    assert "Amsterdam" in d.text or "lieve" in d.text.lower()


def test_parse_brieven_als_buit_year_extraction():
    text_with_year = "Schreef den 14 April 1703. " + _LETTER_TEXT * 3
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        letters = p / "letters"
        letters.mkdir()
        (letters / "brief_002.txt").write_text(text_with_year, encoding="utf-8")
        docs = list(parse_brieven_als_buit_dir(p))
    assert len(docs) == 1
    assert docs[0].meta.publication_year == 1703


def test_parse_brieven_als_buit_tei_xml():
    tei = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc><titleStmt><title>Brief Jan aan Marie</title></titleStmt></fileDesc>
    <profileDesc><creation><date when="1698-03-15"/></creation></profileDesc>
  </teiHeader>
  <text><body><div>
    <p>Lieve Marie, ick schrijve u desen brief uyt Batavia.</p>
    <p>Ick hope dat ghij in goede gesondtheyt sijt ende de kinderen oock.</p>
    <p>Het leven hier is swaar maer ick verhope haest thuis te comen.</p>
    <p>Uwen man, Pieter de Vries, den 15 Maart 1698.</p>
  </div></body></text>
</TEI>"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        letters = p / "letters"
        letters.mkdir()
        (letters / "brief_batavia.xml").write_bytes(tei)
        docs = list(parse_brieven_als_buit_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "brieven_als_buit"
    assert d.meta.publication_year == 1698
    assert "Batavia" in d.text or "Marie" in d.text


def test_parse_brieven_als_buit_tsv():
    tsv = "id\tdate\ttext\n1\t1705\tLieve vrouw ick schrijve u desen brief uyt Amsterdam den eersten Januarij.\n"
    tsv = tsv * 5  # repeat to exceed min length
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        letters = p / "letters"
        letters.mkdir()
        (letters / "brieven.tsv").write_text(tsv, encoding="utf-8")
        docs = list(parse_brieven_als_buit_dir(p))
    assert len(docs) >= 1
    assert docs[0].meta.source == "brieven_als_buit"


def test_parse_brieven_als_buit_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        letters = p / "letters"
        letters.mkdir()
        (letters / "tiny.txt").write_text("Kort.", encoding="utf-8")
        docs = list(parse_brieven_als_buit_dir(p))
    assert docs == []


def test_parse_brieven_als_buit_no_letters_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_brieven_als_buit_dir(Path(tmp)))
    assert docs == []
