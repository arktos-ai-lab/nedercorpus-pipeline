"""Tests for the plain_text parse module."""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import (
    _strip_gutenberg_boilerplate,
    _parse_europarl_session,
    _ep_year_from_filename,
    parse_gutenberg_dir,
    parse_europarl_dir,
    parse_delpher_dir,
    parse_rijksoverheid_dir,
    _codex_text,
    parse_codex_dir,
    _vlpar_text,
    _vlpar_year,
    parse_vlpar_dir,
    _jrc_text,
    _jrc_year,
    parse_jrc_dir,
    parse_dbnl_dir,
    parse_common_voice_dir,
)


# ---------------------------------------------------------------------------
# Gutenberg helpers
# ---------------------------------------------------------------------------

def test_strip_boilerplate_removes_header_footer():
    raw = (
        "Some header text.\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK MAX HAVELAAR ***\n"
        "Dit is de eigenlijke tekst van het boek. Multatuli schreef dit.\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK MAX HAVELAAR ***\n"
        "Some footer text."
    )
    result = _strip_gutenberg_boilerplate(raw)
    assert "eigenlijke tekst" in result
    assert "header text" not in result
    assert "footer text" not in result


def test_strip_boilerplate_no_markers_returns_full():
    raw = "Gewone tekst zonder markers."
    assert _strip_gutenberg_boilerplate(raw) == raw.strip()


def test_parse_gutenberg_dir_yields_document():
    book_text = (
        "*** START OF THE PROJECT GUTENBERG EBOOK DE STILLE KRACHT ***\n"
        "Dit is een fragment van een Nederlandse roman. Het verhaal speelt "
        "zich af in Nederlands-Indië en gaat over de stille kracht der natuur. "
        "Louis Couperus beschreef de koloniale samenleving met groot psychologisch "
        "inzicht. De roman geldt als een van de hoogtepunten van de Nederlandse "
        "literatuur uit het fin de siècle. De sfeer van het boek is zwaar en "
        "dromerig, doordrongen van het mysterieuze Oost-Indische landschap.\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK DE STILLE KRACHT ***\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "1234.txt").write_text(book_text, encoding="utf-8")
        (p / "1234.txt.url").write_text(
            "https://www.gutenberg.org/ebooks/1234", encoding="utf-8"
        )
        docs = list(parse_gutenberg_dir(p))
    assert len(docs) == 1
    assert docs[0].meta.source == "gutenberg"
    assert docs[0].meta.license == "PD"
    assert docs[0].meta.domain == "literature"
    assert "stille kracht" in docs[0].text
    assert docs[0].meta.source_url == "https://www.gutenberg.org/ebooks/1234"


def test_parse_gutenberg_dir_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "99.txt").write_text("Te kort.", encoding="utf-8")
        docs = list(parse_gutenberg_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Europarl helpers
# ---------------------------------------------------------------------------

def test_parse_europarl_session_strips_tags():
    raw = "<CHAPTER ID=1>\n<P ID=2>\nDe voorzitter opent de vergadering.\n</P>\n</CHAPTER>"
    result = _parse_europarl_session(raw)
    assert "voorzitter opent" in result
    assert "<CHAPTER" not in result
    assert "<P" not in result


def test_ep_year_from_filename():
    assert _ep_year_from_filename("ep-96-04-15.txt") == 1996
    assert _ep_year_from_filename("ep-11-12-14.txt") == 2011
    assert _ep_year_from_filename("ep-00-01-17.txt") == 2000
    assert _ep_year_from_filename("unknown.txt") is None


def test_parse_europarl_dir_sessions_subdir():
    session_text = (
        "<CHAPTER ID=1>\n<P ID=1>\n"
        "De Europese Commissie bespreekt de begroting voor het volgende jaar. "
        "De lidstaten moeten hun bijdragen verhogen om de doelstellingen te halen.\n"
        "</P>\n</CHAPTER>"
    )
    with tempfile.TemporaryDirectory() as tmp:
        sessions = Path(tmp) / "sessions"
        sessions.mkdir()
        (sessions / "ep-99-03-10.txt").write_text(session_text, encoding="utf-8")
        docs = list(parse_europarl_dir(Path(tmp)))
    assert len(docs) == 1
    assert docs[0].meta.source == "europarl"
    assert docs[0].meta.license == "PD"
    assert docs[0].meta.publication_year == 1999
    assert "begroting" in docs[0].text


def test_parse_europarl_dir_flat():
    """Also works when session files are directly in raw_dir (no sessions/)."""
    session_text = (
        "<CHAPTER ID=1>\n<P ID=1>\n"
        "Het Europees Parlement stemt over de nieuwe richtlijn betreffende "
        "de bescherming van persoonsgegevens in de digitale samenleving.\n"
        "</P>\n</CHAPTER>"
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "ep-02-09-24.txt").write_text(session_text, encoding="utf-8")
        docs = list(parse_europarl_dir(Path(tmp)))
    assert len(docs) == 1
    assert docs[0].meta.publication_year == 2002


# ---------------------------------------------------------------------------
# Delpher
# ---------------------------------------------------------------------------

def test_parse_delpher_dir_yields_document():
    article = (
        "De stoomboot vertrok gisteren uit de haven van Rotterdam met bestemming "
        "Batavia. Aan boord bevonden zich verscheidene passagiers en een lading "
        "koloniale waren. Het weder was gunstig."
    )
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        f = p / "ddd_010864687_mpeg21_a0024.txt"
        f.write_text(article, encoding="utf-8")
        f.with_name(f.name + ".url").write_text(
            "https://resolver.kb.nl/resolve?urn=ddd:010864687:mpeg21:a0024&role=article",
            encoding="utf-8",
        )
        docs = list(parse_delpher_dir(p))
    assert len(docs) == 1
    assert docs[0].meta.source == "delpher"
    assert docs[0].meta.license == "CC0"
    assert docs[0].meta.domain == "news"
    assert "Rotterdam" in docs[0].text


def test_parse_delpher_dir_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "short.txt").write_text("Te kort.", encoding="utf-8")
        docs = list(parse_delpher_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Rijksoverheid
# ---------------------------------------------------------------------------

_RO_DOC = {
    "id": "persbericht-2024-12345",
    "title": "Kabinet presenteert nieuwe klimaatmaatregelen",
    "introductie": "Het kabinet heeft vandaag een pakket aan klimaatmaatregelen bekendgemaakt.",
    "body": "<p>De maatregelen richten zich op de reductie van CO2-uitstoot in de industrie.</p>",
    "available": "2024-03-15",
    "canonical": "https://www.rijksoverheid.nl/actueel/nieuws/2024/03/15/klimaatmaatregelen",
}


def test_parse_rijksoverheid_dir_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "persberichten"
        p.mkdir()
        f = p / "persbericht-2024-12345.json"
        f.write_text(json.dumps(_RO_DOC), encoding="utf-8")
        f.with_name(f.name + ".url").write_text(_RO_DOC["canonical"], encoding="utf-8")
        docs = list(parse_rijksoverheid_dir(Path(tmp)))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "rijksoverheid"
    assert d.meta.license == "CC0"
    assert d.meta.publication_year == 2024
    assert "klimaatmaatregelen" in d.text
    assert d.meta.source_url == _RO_DOC["canonical"]


def test_parse_rijksoverheid_dir_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "empty.json").write_text(json.dumps({"title": "x"}), encoding="utf-8")
        docs = list(parse_rijksoverheid_dir(p))
    assert docs == []


def test_parse_rijksoverheid_dir_strips_html_in_body():
    doc = {
        "title": "Besluit over subsidieregeling",
        "body": "<p>De <strong>subsidieregeling</strong> geldt voor ondernemers die investeren "
                "in duurzame energie. De aanvraagperiode loopt tot 31 december 2024.</p>"
                "<p>Meer informatie via het RVO-loket.</p>",
        "available": "2024-01-10",
    }
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "subsidie.json").write_text(json.dumps(doc), encoding="utf-8")
        docs = list(parse_rijksoverheid_dir(Path(tmp)))
    assert len(docs) == 1
    assert "<p>" not in docs[0].text
    assert "subsidieregeling" in docs[0].text


# ---------------------------------------------------------------------------
# Vlaamse Codex
# ---------------------------------------------------------------------------

_CODEX_DOC = {
    "Document": {
        "Titel": "Decreet betreffende de organisatie van het onderwijs",
        "InWerkingTredingsDatum": "2019-09-01",
    },
    "Inhoudstafel": [
        {
            "Tekst": "<p>Artikel 1. Dit decreet regelt een gemeenschapsaangelegenheid.</p>",
            "Subartikel": [
                {
                    "Tekst": "Artikel 2. De minister bevoegd voor onderwijs legt de voorwaarden vast.",
                }
            ],
        }
    ],
    "_meta": {
        "id": 1000099,
        "canonical": "https://codex.vlaanderen.be/Zoeken/Document.aspx?DID=1000099",
        "acquisition_date": "2024-01-15",
    },
}


def test_codex_text_extracts_title_and_articles():
    text = _codex_text(_CODEX_DOC)
    assert "Decreet" in text
    assert "gemeenschapsaangelegenheid" in text
    assert "minister" in text
    assert "<p>" not in text


def test_parse_codex_dir_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        f = p / "1000099.json"
        f.write_text(json.dumps(_CODEX_DOC), encoding="utf-8")
        f.with_name(f.name + ".url").write_text(
            _CODEX_DOC["_meta"]["canonical"], encoding="utf-8"
        )
        docs = list(parse_codex_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "codex"
    assert d.meta.license == "CC0"
    assert d.meta.country == "BE"
    assert d.meta.publication_year == 2019
    assert "onderwijs" in d.text
    assert d.meta.source_url == _CODEX_DOC["_meta"]["canonical"]


def test_parse_codex_dir_skips_short():
    short_doc = {"Document": {"Titel": "x"}, "Inhoudstafel": []}
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "short.json").write_text(json.dumps(short_doc), encoding="utf-8")
        docs = list(parse_codex_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Vlaams Parlement
# ---------------------------------------------------------------------------

_VLPAR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<handelingen datum="2023-03-15">
  <journaallijnen>
    <journaallijn id="1001">
      <omschrijving>De voorzitter opent de vergadering. De commissie bespreekt het voorstel van decreet betreffende de organisatie van het hoger onderwijs in Vlaanderen.</omschrijving>
    </journaallijn>
    <journaallijn id="1002">
      <omschrijving>Minister De Wever legt de beleidsverklaring af over de Vlaamse economische strategie voor de komende legislatuur.</omschrijving>
    </journaallijn>
  </journaallijnen>
</handelingen>"""


def test_vlpar_text_extracts_omschrijving():
    text = _vlpar_text(_VLPAR_XML)
    assert "voorzitter" in text
    assert "hoger onderwijs" in text
    assert "beleidsverklaring" in text


def test_vlpar_year_from_datum_attribute():
    assert _vlpar_year(_VLPAR_XML) == 2023


def test_parse_vlpar_dir_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        f = p / "1791486.xml"
        f.write_bytes(_VLPAR_XML)
        f.with_name(f.name + ".url").write_text(
            "https://www.vlaamsparlement.be/nl/vergadering/1791486", encoding="utf-8"
        )
        docs = list(parse_vlpar_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "vlpar"
    assert d.meta.license == "CC-BY"
    assert d.meta.country == "BE"
    assert d.meta.publication_year == 2023
    assert "onderwijs" in d.text


def test_parse_vlpar_dir_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "empty.xml").write_bytes(b"<handelingen/>")
        docs = list(parse_vlpar_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# JRC-Acquis (TEI XML)
# ---------------------------------------------------------------------------

_JRC_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI>
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>Richtlijn 2003/87/EG betreffende een systeem voor de handel in broeikasgasemissierechten</title>
      </titleStmt>
      <publicationStmt>
        <date>2003-10-13</date>
      </publicationStmt>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <p>Het Europees Parlement en de Raad van de Europese Unie hebben deze richtlijn vastgesteld.</p>
        <p>Gelet op het Verdrag tot oprichting van de Europese Gemeenschap, inzonderheid op artikel 175, lid 1.</p>
        <p>Overwegende dat een systeem voor de handel in emissierechten de Gemeenschap en de lidstaten in staat zal stellen de doelstellingen te bereiken.</p>
      </div>
    </body>
  </text>
</TEI>"""


def test_jrc_text_extracts_title_and_paragraphs():
    text = _jrc_text(_JRC_XML)
    assert "broeikasgasemissierechten" in text
    assert "Europees Parlement" in text
    assert "emissierechten" in text


def test_jrc_year_from_header():
    assert _jrc_year(_JRC_XML) == 2003


def test_parse_jrc_dir_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        docs_dir = p / "docs"
        docs_dir.mkdir()
        (docs_dir / "jrc2003L0087-nl.xml").write_bytes(_JRC_XML)
        docs = list(parse_jrc_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "jrc"
    assert d.meta.license == "CC-BY"
    assert d.meta.country == "EU"
    assert d.meta.publication_year == 2003
    assert "broeikasgasemissierechten" in d.text


def test_parse_jrc_dir_flat_fallback():
    """Also works when XML files are directly in raw_dir (no docs/ subdir)."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "jrc2003L0087-nl.xml").write_bytes(_JRC_XML)
        docs = list(parse_jrc_dir(Path(tmp)))
    assert len(docs) == 1


def test_parse_jrc_dir_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "empty.xml").write_bytes(b"<TEI><text><body><p>Te kort.</p></body></text></TEI>")
        docs = list(parse_jrc_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# DBNL
# ---------------------------------------------------------------------------

_DBNL_TEXT = (
    "Dit is een fragment uit de roman Max Havelaar van Multatuli, oorspronkelijk "
    "gepubliceerd in het jaar 1860. De verteller beschrijft de toestanden in "
    "Nederlands-Indië met groot literair meesterschap en sociale betrokkenheid. "
    "Het boek geldt als een van de hoogtepunten van de Nederlandse literatuur en "
    "heeft bijgedragen aan bewustwording over koloniale misstanden in de negentiende eeuw. "
    "Multatuli, pseudoniem van Eduard Douwes Dekker, schreef dit werk vanuit persoonlijke ervaring."
)


def test_parse_dbnl_dir_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()
        (texts_dir / "mult001maxh01_01.txt").write_text(_DBNL_TEXT, encoding="utf-8")
        docs = list(parse_dbnl_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "dbnl"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.domain == "literature"
    assert "Multatuli" in d.text
    assert "dbnl.org" in d.meta.source_url


def test_parse_dbnl_dir_flat_fallback():
    """Works when .txt files are directly in raw_dir (no texts/ subdir)."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "mult001maxh01_01.txt").write_text(_DBNL_TEXT, encoding="utf-8")
        docs = list(parse_dbnl_dir(Path(tmp)))
    assert len(docs) == 1


def test_parse_dbnl_dir_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        texts = Path(tmp) / "texts"
        texts.mkdir()
        (texts / "short.txt").write_text("Te kort.", encoding="utf-8")
        docs = list(parse_dbnl_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Common Voice
# ---------------------------------------------------------------------------

def _make_cv_tsv(sentences: list[str], locale: str = "nl") -> str:
    lines = ["sentence\tlocale"]
    for s in sentences:
        lines.append(f"{s}\t{locale}")
    return "\n".join(lines)


def test_parse_common_voice_dir_yields_batched_documents():
    sentences = [
        f"Dit is testzin nummer {i} voor het Common Voice corpus." for i in range(150)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        locale_dir = p / "nl"
        locale_dir.mkdir()
        (locale_dir / "sentences_0000.tsv").write_text(
            _make_cv_tsv(sentences), encoding="utf-8"
        )
        docs = list(parse_common_voice_dir(p))
    # 150 sentences / 100 per batch = 2 documents
    assert len(docs) == 2
    d = docs[0]
    assert d.meta.source == "common_voice"
    assert d.meta.license == "CC0"
    assert d.meta.country == "NL"
    assert d.meta.domain == "spoken"
    assert "testzin" in d.text


def test_parse_common_voice_dir_flemish_locale():
    sentences = [
        f"Dit is een Vlaamse zin nummer {i} voor het corpus." for i in range(50)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        locale_dir = p / "nl_BE"
        locale_dir.mkdir()
        (locale_dir / "sentences_0000.tsv").write_text(
            _make_cv_tsv(sentences, locale="nl-BE"), encoding="utf-8"
        )
        docs = list(parse_common_voice_dir(p))
    assert len(docs) == 1
    assert docs[0].meta.country == "BE"
    assert docs[0].meta.language_variant == "nl-BE"


def test_parse_common_voice_dir_skips_empty():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        locale_dir = p / "nl"
        locale_dir.mkdir()
        (locale_dir / "sentences_0000.tsv").write_text("sentence\tlocale\n", encoding="utf-8")
        docs = list(parse_common_voice_dir(p))
    assert docs == []
