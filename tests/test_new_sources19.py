"""
Tests for new parliamentary/speech sources:
  - staten_generaal (plain_text.parse_staten_generaal_dir)
  - voxpopuli      (plain_text.parse_voxpopuli_dir)
"""
import csv
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import (
    parse_staten_generaal_dir,
    parse_voxpopuli_dir,
)

# ---------------------------------------------------------------------------
# Staten-Generaal SRU XML fixture
# ---------------------------------------------------------------------------

_SGD_SRU_XML = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:dcx="http://krait.kb.nl/coop/tel/handbook/telterms.html">
  <srw:numberOfRecords>2</srw:numberOfRecords>
  <srw:records>
    <srw:record>
      <srw:recordData>
        <dc:identifier>http://resolver.kb.nl/resolve?urn=sgd:1850:tk:01234</dc:identifier>
        <dc:title>Handelingen Tweede Kamer 1849-1850 nr. 12</dc:title>
        <dc:description>
          De voorzitter opende de vergadering en deelde mede dat het wetsvoorstel
          betreffende de staatsbegrooting voor het dienstjaar 1850 in behandeling
          zou worden genomen. De heer Van den Berg nam het woord en betoogde
          dat de geraamde uitgaven voor defensie te hoog waren gesteld.
          Na beraadslaging werd besloten het voorstel aan te nemen met
          zevenentwintig stemmen vóór en drie stemmen tegen.
        </dc:description>
        <dc:date>1850-01-15</dc:date>
        <dc:type>TK</dc:type>
      </srw:recordData>
    </srw:record>
    <srw:record>
      <srw:recordData>
        <dc:identifier>http://resolver.kb.nl/resolve?urn=sgd:1920:ek:05678</dc:identifier>
        <dc:title>Handelingen Eerste Kamer 1919-1920 nr. 34</dc:title>
        <dc:description>
          De Eerste Kamer behandelde het wetsvoorstel inzake de uitbreiding
          van het kiesrecht voor vrouwen. De minister van Binnenlandsche Zaken
          verdedigde het standpunt der regeering. De vergadering sloot na
          uitvoerige beraadslagingen met aanneming van het voorstel.
        </dc:description>
        <dc:date>1920-06-10</dc:date>
        <dc:type>EK</dc:type>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>"""

# ---------------------------------------------------------------------------
# VoxPopuli TSV fixture
# ---------------------------------------------------------------------------

_VOXPOPULI_ROWS = [
    {"utterance_id": "vp_nl_001", "normalized_text": "De Commissie heeft een verslag uitgebracht over de hervorming van het gemeenschappelijk landbouwbeleid.", "raw_text": "de commissie heeft een verslag uitgebracht", "year": "2015", "speaker_id": "s001"},
    {"utterance_id": "vp_nl_002", "normalized_text": "Het Parlement verwelkomt de voorgestelde maatregelen ter verbetering van de luchtkwaliteit in Europese steden.", "raw_text": "het parlement verwelkomt de voorgestelde maatregelen", "year": "2016", "speaker_id": "s002"},
    {"utterance_id": "vp_nl_003", "normalized_text": "De lidstaten dienen te zorgen voor een doeltreffende omzetting van de richtlijn in nationaal recht.", "raw_text": "de lidstaten dienen te zorgen", "year": "2018", "speaker_id": "s003"},
]


def _write_voxpopuli_tsv(locale_dir: Path, locale: str, rows: list[dict]) -> Path:
    tsv_path = locale_dir / f"{locale}_sentences_0000.tsv"
    with tsv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["utterance_id", "normalized_text", "raw_text", "year", "speaker_id"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)
    return tsv_path


# ===========================================================================
# Tests: parse_staten_generaal_dir
# ===========================================================================

class TestParseStatenGeneraal:
    def test_empty_dir_returns_no_docs(self):
        with tempfile.TemporaryDirectory() as td:
            docs = list(parse_staten_generaal_dir(Path(td)))
        assert docs == []

    def test_batch_xml_yields_docs(self):
        with tempfile.TemporaryDirectory() as td:
            tk_dir = Path(td) / "tk"
            tk_dir.mkdir()
            (tk_dir / "tk_batch_00000001.xml").write_text(_SGD_SRU_XML, encoding="utf-8")
            docs = list(parse_staten_generaal_dir(Path(td)))
        assert len(docs) == 2

    def test_doc_text_contains_title_and_description(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "batch.xml").write_text(_SGD_SRU_XML, encoding="utf-8")
            docs = list(parse_staten_generaal_dir(Path(td)))
        texts = " ".join(d.text for d in docs)
        assert "staatsbegrooting" in texts
        assert "kiesrecht" in texts

    def test_metadata_fields(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "batch.xml").write_text(_SGD_SRU_XML, encoding="utf-8")
            docs = list(parse_staten_generaal_dir(Path(td)))
        for doc in docs:
            assert doc.meta.source == "staten_generaal"
            assert doc.meta.license == "CC0"
            assert doc.meta.domain == "parliamentary"
            assert doc.meta.language_variant == "NL"
            assert doc.meta.country == "NL"

    def test_publication_year_extracted(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "batch.xml").write_text(_SGD_SRU_XML, encoding="utf-8")
            docs = list(parse_staten_generaal_dir(Path(td)))
        years = {d.meta.publication_year for d in docs}
        assert 1850 in years
        assert 1920 in years

    def test_source_url_uses_identifier(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "batch.xml").write_text(_SGD_SRU_XML, encoding="utf-8")
            docs = list(parse_staten_generaal_dir(Path(td)))
        assert "resolver.kb.nl" in docs[0].meta.source_url

    def test_nested_chamber_subdirs(self):
        with tempfile.TemporaryDirectory() as td:
            for chamber in ("tk", "ek"):
                cdir = Path(td) / chamber
                cdir.mkdir()
                (cdir / f"{chamber}_batch_00000001.xml").write_text(_SGD_SRU_XML, encoding="utf-8")
            docs = list(parse_staten_generaal_dir(Path(td)))
        assert len(docs) == 4  # 2 chambers × 2 records each

    def test_malformed_xml_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bad.xml").write_text("THIS IS NOT XML <<< >>>", encoding="utf-8")
            (Path(td) / "good.xml").write_text(_SGD_SRU_XML, encoding="utf-8")
            docs = list(parse_staten_generaal_dir(Path(td)))
        assert len(docs) == 2


# ===========================================================================
# Tests: parse_voxpopuli_dir
# ===========================================================================

class TestParseVoxpopuli:
    def test_empty_dir_returns_no_docs(self):
        with tempfile.TemporaryDirectory() as td:
            docs = list(parse_voxpopuli_dir(Path(td)))
        assert docs == []

    def test_tsv_yields_docs(self):
        with tempfile.TemporaryDirectory() as td:
            nl_dir = Path(td) / "nl"
            nl_dir.mkdir()
            _write_voxpopuli_tsv(nl_dir, "nl", _VOXPOPULI_ROWS)
            docs = list(parse_voxpopuli_dir(Path(td)))
        assert len(docs) >= 1  # 3 utterances → 1 batch of 50

    def test_text_contains_utterances(self):
        with tempfile.TemporaryDirectory() as td:
            nl_dir = Path(td) / "nl"
            nl_dir.mkdir()
            _write_voxpopuli_tsv(nl_dir, "nl", _VOXPOPULI_ROWS)
            docs = list(parse_voxpopuli_dir(Path(td)))
        combined = " ".join(d.text for d in docs)
        assert "landbouwbeleid" in combined
        assert "luchtkwaliteit" in combined

    def test_metadata_nl(self):
        with tempfile.TemporaryDirectory() as td:
            nl_dir = Path(td) / "nl"
            nl_dir.mkdir()
            _write_voxpopuli_tsv(nl_dir, "nl", _VOXPOPULI_ROWS)
            docs = list(parse_voxpopuli_dir(Path(td)))
        doc = docs[0]
        assert doc.meta.source == "voxpopuli"
        assert doc.meta.license == "CC0"
        assert doc.meta.domain == "speech"
        assert doc.meta.language_variant == "NL"
        assert doc.meta.country == "NL"

    def test_frisian_locale_lang_variant(self):
        fy_rows = [dict(r) for r in _VOXPOPULI_ROWS]
        for r in fy_rows:
            r["normalized_text"] = "Wy hawwe de ried fan Europa bestjoerd mei ús rapportaazje."
        with tempfile.TemporaryDirectory() as td:
            fy_dir = Path(td) / "fy"
            fy_dir.mkdir()
            _write_voxpopuli_tsv(fy_dir, "fy", fy_rows)
            docs = list(parse_voxpopuli_dir(Path(td)))
        assert docs[0].meta.language_variant == "FY"

    def test_publication_year_uses_min_year_in_batch(self):
        with tempfile.TemporaryDirectory() as td:
            nl_dir = Path(td) / "nl"
            nl_dir.mkdir()
            _write_voxpopuli_tsv(nl_dir, "nl", _VOXPOPULI_ROWS)
            docs = list(parse_voxpopuli_dir(Path(td)))
        # min year in fixture is 2015
        assert docs[0].meta.publication_year == 2015

    def test_multiple_locales(self):
        with tempfile.TemporaryDirectory() as td:
            for locale in ("nl", "fy"):
                ldir = Path(td) / locale
                ldir.mkdir()
                _write_voxpopuli_tsv(ldir, locale, _VOXPOPULI_ROWS)
            docs = list(parse_voxpopuli_dir(Path(td)))
        assert len(docs) >= 2  # one batch per locale

    def test_multiple_shards(self):
        with tempfile.TemporaryDirectory() as td:
            nl_dir = Path(td) / "nl"
            nl_dir.mkdir()
            for i in range(3):
                (nl_dir / f"nl_sentences_{i:04d}.tsv").write_text(
                    "\t".join(["utterance_id", "normalized_text", "raw_text", "year", "speaker_id"]) + "\n" +
                    "\n".join(
                        f"id_{i}_{j}\t" + "De commissie stelt de volgende maatregelen voor.\t" +
                        "de commissie stelt\t2019\ts001"
                        for j in range(3)
                    ),
                    encoding="utf-8",
                )
            docs = list(parse_voxpopuli_dir(Path(td)))
        assert len(docs) >= 3  # 3 shards → at least 3 batches


# ===========================================================================
# Dispatch registration tests
# ===========================================================================

class TestDispatchRegistration:
    def test_staten_generaal_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "staten_generaal" in parsers

    def test_voxpopuli_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "voxpopuli" in parsers


# ===========================================================================
# Acquire module import tests
# ===========================================================================

class TestAcquireImports:
    def test_staten_generaal_import(self):
        from pipeline.acquire import staten_generaal
        assert callable(staten_generaal.download)

    def test_voxpopuli_import(self):
        from pipeline.acquire import voxpopuli
        assert callable(voxpopuli.download)
