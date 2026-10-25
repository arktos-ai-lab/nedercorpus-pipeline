"""
Tests for new historical & parliamentary sources:
  - voc_wic_htr  (plain_text.parse_voc_wic_htr_dir)
  - parlamint_nl (plain_text.parse_parlamint_nl_dir)
  - nationaal_archief (plain_text.parse_nationaal_archief_dir)
  - etaamb_be    (plain_text.parse_etaamb_be_dir)
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import (
    parse_voc_wic_htr_dir,
    parse_parlamint_nl_dir,
    parse_nationaal_archief_dir,
    parse_etaamb_be_dir,
)

# ---------------------------------------------------------------------------
# VOC/WIC HTR fixtures
# ---------------------------------------------------------------------------

_VOC_TXT_CONTENT = (
    "Alsoo wy den 14 dito aan 't uytkomen van de reede verbleven zyn "
    "ende den 15 dito des morgens de anker lichten konden, zoo is het "
    "schip Na den Middag ten 4 uuren onder zeyl gegaan. "
    "Den Boeckhouder heeft de papieren overgegeven aan den Schipper. "
    "Goede wind en helder weder."
)

_VOC_JSONL_CONTENT = json.dumps({
    "id": "voc_inv_1234_page_001",
    "text": (
        "Heden compareerden voor ons, Schepenen van Batavia, de "
        "navolgende Compagniesdienaren dewelke verklaarden dat zij "
        "den dienst hebben verricht als beschreven in het dagregister. "
        "Aldus gedaan in Batavia den 12 Februarij 1701."
    ),
    "year": 1701,
    "url": "https://zenodo.org/records/11209325",
}) + "\n"

# ---------------------------------------------------------------------------
# ParlaMint-NL TEI XML fixture
# ---------------------------------------------------------------------------

_PARLAMINT_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="ParlaMint-NL_2020-03-18">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>ParlaMint-NL_2020-03-18</title>
      </titleStmt>
      <sourceDesc>
        <bibl>
          <date when="2020-03-18"/>
        </bibl>
      </sourceDesc>
    </fileDesc>
    <settingDesc>
      <setting>
        <date when="2020-03-18"/>
        <name>Tweede Kamer der Staten-Generaal</name>
      </setting>
    </settingDesc>
  </teiHeader>
  <text>
    <body>
      <div type="debateSection">
        <u xml:id="ParlaMint-NL_2020-03-18.u1" who="#Rutte">
          <seg xml:id="ParlaMint-NL_2020-03-18.u1.s1">
            Voorzitter, de regering heeft besloten de scholen te sluiten
            vanwege de verspreiding van het coronavirus in Nederland.
            Dit is een ingrijpende maatregel maar noodzakelijk om verdere
            verspreiding te voorkomen. Wij vragen de bevolking om thuis te blijven.
          </seg>
        </u>
        <u xml:id="ParlaMint-NL_2020-03-18.u2" who="#Klever">
          <seg xml:id="ParlaMint-NL_2020-03-18.u2.s1">
            Dank u wel voorzitter. Ik wil de minister-president vragen
            welke maatregelen er worden genomen voor de zorg voor ouderen
            tijdens deze crisis. De kwetsbare ouderen verdienen extra aandacht.
          </seg>
        </u>
      </div>
    </body>
  </text>
</TEI>"""

# ---------------------------------------------------------------------------
# Nationaal Archief JSONL fixture
# ---------------------------------------------------------------------------

_NA_BATCH_JSONL = "\n".join([
    json.dumps({
        "id": "oai:service.archief.nl:gaf/1234",
        "title": "Inventaris van het archief van de Raad van State",
        "description": (
            "Dit archief bevat stukken van de Raad van State uit de periode 1588-1795. "
            "Het omvat resoluties, adviezen en correspondentie betreffende het bestuur "
            "van de Republiek der Zeven Verenigde Nederlanden."
        ),
        "date": "1588/1795",
    }),
    json.dumps({
        "id": "oai:service.archief.nl:gaf/5678",
        "title": "Archief van de VOC, Kamer Amsterdam",
        "description": (
            "Het archief van de Kamer Amsterdam van de VOC (1602-1799) bevat "
            "boekhouding, scheepsjournalen, brieven en instructies. "
            "Het is een van de grootste historische archieven ter wereld."
        ),
        "date": "1602/1799",
    }),
])

# ---------------------------------------------------------------------------
# Etaamb BE JSON fixture
# ---------------------------------------------------------------------------

_ETAAMB_JSON = {
    "id": "12345",
    "title": "Koninklijk besluit tot vaststelling van de personeelsformatie",
    "publication_year": 2015,
    "text": (
        "Wij Filip, Koning der Belgen, Aan allen die nu zijn en hierna wezen zullen, "
        "Onze Groet. Gelet op de wet van 22 maart 1999 betreffende de identificatieprocedure "
        "via DNA-analyse in strafzaken. Op de voordracht van Onze Minister van Justitie. "
        "Hebben Wij besloten en besluiten Wij: Artikel 1. De personeelsformatie van de "
        "rechterlijke organisatie wordt vastgesteld zoals bepaald in de bijlage bij dit besluit. "
        "Art. 2. Dit besluit treedt in werking op de dag van de bekendmaking ervan in het "
        "Belgisch Staatsblad. Gegeven te Brussel, 15 januari 2015."
    ),
    "url": "https://etaamb.openjustice.be/nl/document/12345",
    "acquisition_date": "2026-02-24",
}


# ===========================================================================
# Tests: parse_voc_wic_htr_dir
# ===========================================================================

class TestParseVocWicHtr:
    def test_empty_dir_returns_no_docs(self):
        with tempfile.TemporaryDirectory() as td:
            docs = list(parse_voc_wic_htr_dir(Path(td)))
        assert docs == []

    def test_txt_file_yields_doc(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "scan_001.txt").write_text(_VOC_TXT_CONTENT, encoding="utf-8")
            docs = list(parse_voc_wic_htr_dir(Path(td)))
        assert len(docs) == 1
        assert "Batavia" in docs[0].text or "Boeckhouder" in docs[0].text or "Alsoo" in docs[0].text

    def test_txt_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "scan_001.txt").write_text(_VOC_TXT_CONTENT, encoding="utf-8")
            docs = list(parse_voc_wic_htr_dir(Path(td)))
        assert docs[0].meta.source == "voc_wic_htr"
        assert docs[0].meta.license == "CC-BY"
        assert docs[0].meta.domain == "historical"
        assert docs[0].meta.language_variant == "NL"
        assert docs[0].meta.country == "NL"

    def test_jsonl_file_yields_doc(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "batch_001.jsonl").write_text(_VOC_JSONL_CONTENT, encoding="utf-8")
            docs = list(parse_voc_wic_htr_dir(Path(td)))
        assert len(docs) == 1
        assert "Batavia" in docs[0].text

    def test_jsonl_publication_year(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "batch_001.jsonl").write_text(_VOC_JSONL_CONTENT, encoding="utf-8")
            docs = list(parse_voc_wic_htr_dir(Path(td)))
        assert docs[0].meta.publication_year == 1701

    def test_short_txt_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "short.txt").write_text("Te kort.", encoding="utf-8")
            docs = list(parse_voc_wic_htr_dir(Path(td)))
        assert docs == []

    def test_nested_subdirs(self):
        with tempfile.TemporaryDirectory() as td:
            subdir = Path(td) / "inventory_1234"
            subdir.mkdir()
            (subdir / "page_001.txt").write_text(_VOC_TXT_CONTENT, encoding="utf-8")
            docs = list(parse_voc_wic_htr_dir(Path(td)))
        assert len(docs) == 1


# ===========================================================================
# Tests: parse_parlamint_nl_dir
# ===========================================================================

class TestParseParlaMintNl:
    def test_empty_dir_returns_no_docs(self):
        with tempfile.TemporaryDirectory() as td:
            docs = list(parse_parlamint_nl_dir(Path(td)))
        assert docs == []

    def test_tei_file_yields_utterances(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ParlaMint-NL_2020-03-18.xml").write_text(
                _PARLAMINT_TEI, encoding="utf-8"
            )
            docs = list(parse_parlamint_nl_dir(Path(td)))
        assert len(docs) == 2  # Two utterances

    def test_utterance_text(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ParlaMint-NL_2020-03-18.xml").write_text(
                _PARLAMINT_TEI, encoding="utf-8"
            )
            docs = list(parse_parlamint_nl_dir(Path(td)))
        texts = " ".join(d.text for d in docs)
        assert "coronavirus" in texts
        assert "minister-president" in texts

    def test_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ParlaMint-NL_2020-03-18.xml").write_text(
                _PARLAMINT_TEI, encoding="utf-8"
            )
            docs = list(parse_parlamint_nl_dir(Path(td)))
        for doc in docs:
            assert doc.meta.source == "parlamint_nl"
            assert doc.meta.license == "CC-BY"
            assert doc.meta.domain == "parliamentary"
            assert doc.meta.language_variant == "NL"
            assert doc.meta.country == "NL"
            assert doc.meta.publication_year == 2020

    def test_ana_files_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ParlaMint-NL_2020-03-18-ana.xml").write_text(
                _PARLAMINT_TEI, encoding="utf-8"
            )
            docs = list(parse_parlamint_nl_dir(Path(td)))
        assert docs == []

    def test_tei_subdir(self):
        with tempfile.TemporaryDirectory() as td:
            tei_dir = Path(td) / "tei"
            tei_dir.mkdir()
            (tei_dir / "session.xml").write_text(_PARLAMINT_TEI, encoding="utf-8")
            docs = list(parse_parlamint_nl_dir(Path(td)))
        assert len(docs) == 2


# ===========================================================================
# Tests: parse_nationaal_archief_dir
# ===========================================================================

class TestParseNationaalArchief:
    def test_empty_dir_returns_no_docs(self):
        with tempfile.TemporaryDirectory() as td:
            docs = list(parse_nationaal_archief_dir(Path(td)))
        assert docs == []

    def test_batch_jsonl_yields_docs(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "nationaal_archief_batch_000000.jsonl").write_text(
                _NA_BATCH_JSONL, encoding="utf-8"
            )
            docs = list(parse_nationaal_archief_dir(Path(td)))
        assert len(docs) == 2

    def test_doc_text_contains_title_and_description(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "nationaal_archief_batch_000000.jsonl").write_text(
                _NA_BATCH_JSONL, encoding="utf-8"
            )
            docs = list(parse_nationaal_archief_dir(Path(td)))
        assert "Raad van State" in docs[0].text
        assert "resoluties" in docs[0].text

    def test_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "nationaal_archief_batch_000000.jsonl").write_text(
                _NA_BATCH_JSONL, encoding="utf-8"
            )
            docs = list(parse_nationaal_archief_dir(Path(td)))
        for doc in docs:
            assert doc.meta.source == "nationaal_archief"
            assert doc.meta.license == "CC0"
            assert doc.meta.domain == "archival"
            assert doc.meta.language_variant == "NL"
            assert doc.meta.country == "NL"

    def test_publication_year_extracted_from_date(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "nationaal_archief_batch_000000.jsonl").write_text(
                _NA_BATCH_JSONL, encoding="utf-8"
            )
            docs = list(parse_nationaal_archief_dir(Path(td)))
        # "1588/1795" → extracts 1588
        assert docs[0].meta.publication_year == 1588

    def test_multiple_batches(self):
        with tempfile.TemporaryDirectory() as td:
            for i in range(3):
                (Path(td) / f"nationaal_archief_batch_{i:06d}.jsonl").write_text(
                    _NA_BATCH_JSONL, encoding="utf-8"
                )
            docs = list(parse_nationaal_archief_dir(Path(td)))
        assert len(docs) == 6


# ===========================================================================
# Tests: parse_etaamb_be_dir
# ===========================================================================

class TestParseEtaambBe:
    def test_empty_dir_returns_no_docs(self):
        with tempfile.TemporaryDirectory() as td:
            docs = list(parse_etaamb_be_dir(Path(td)))
        assert docs == []

    def test_json_file_yields_doc(self):
        with tempfile.TemporaryDirectory() as td:
            year_dir = Path(td) / "2015"
            year_dir.mkdir()
            (year_dir / "12345.json").write_text(
                json.dumps(_ETAAMB_JSON, ensure_ascii=False), encoding="utf-8"
            )
            docs = list(parse_etaamb_be_dir(Path(td)))
        assert len(docs) == 1

    def test_text_contains_title_and_body(self):
        with tempfile.TemporaryDirectory() as td:
            year_dir = Path(td) / "2015"
            year_dir.mkdir()
            (year_dir / "12345.json").write_text(
                json.dumps(_ETAAMB_JSON, ensure_ascii=False), encoding="utf-8"
            )
            docs = list(parse_etaamb_be_dir(Path(td)))
        assert "Koninklijk besluit" in docs[0].text
        assert "personeelsformatie" in docs[0].text

    def test_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            year_dir = Path(td) / "2015"
            year_dir.mkdir()
            (year_dir / "12345.json").write_text(
                json.dumps(_ETAAMB_JSON, ensure_ascii=False), encoding="utf-8"
            )
            docs = list(parse_etaamb_be_dir(Path(td)))
        doc = docs[0]
        assert doc.meta.source == "etaamb_be"
        assert doc.meta.license == "PD"
        assert doc.meta.domain == "government"
        assert doc.meta.language_variant == "NL-BE"
        assert doc.meta.country == "BE"
        assert doc.meta.publication_year == 2015

    def test_year_from_dir_name_when_missing_in_json(self):
        rec = dict(_ETAAMB_JSON)
        rec["publication_year"] = None
        with tempfile.TemporaryDirectory() as td:
            year_dir = Path(td) / "2018"
            year_dir.mkdir()
            (year_dir / "99999.json").write_text(json.dumps(rec), encoding="utf-8")
            docs = list(parse_etaamb_be_dir(Path(td)))
        assert docs[0].meta.publication_year == 2018

    def test_short_text_skipped(self):
        rec = dict(_ETAAMB_JSON)
        rec["text"] = "Te kort."
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "short.json").write_text(json.dumps(rec), encoding="utf-8")
            docs = list(parse_etaamb_be_dir(Path(td)))
        assert docs == []

    def test_acquisition_log_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            # Create an acquisition_log.jsonl that also has .json extension (edge case)
            (Path(td) / "acquisition_log.json").write_text(
                json.dumps(_ETAAMB_JSON), encoding="utf-8"
            )
            docs = list(parse_etaamb_be_dir(Path(td)))
        assert docs == []


# ===========================================================================
# Dispatch registration tests
# ===========================================================================

class TestDispatchRegistration:
    def test_voc_wic_htr_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "voc_wic_htr" in parsers

    def test_parlamint_nl_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "parlamint_nl" in parsers

    def test_nationaal_archief_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "nationaal_archief" in parsers

    def test_etaamb_be_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "etaamb_be" in parsers


# ===========================================================================
# Acquire module import tests
# ===========================================================================

class TestAcquireImports:
    def test_voc_wic_htr_import(self):
        from pipeline.acquire import voc_wic_htr
        assert callable(voc_wic_htr.download)

    def test_parlamint_nl_import(self):
        from pipeline.acquire import parlamint_nl
        assert callable(parlamint_nl.download)

    def test_nationaal_archief_import(self):
        from pipeline.acquire import nationaal_archief
        assert callable(nationaal_archief.download)

    def test_etaamb_be_import(self):
        from pipeline.acquire import etaamb_be
        assert callable(etaamb_be.download)
