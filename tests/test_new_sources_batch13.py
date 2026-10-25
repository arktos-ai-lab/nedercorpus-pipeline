"""
Tests for Batch 13 new sources:
  - eupdcorp         (EP plenary debates 1999–2024, Zenodo 15056399, CC-BY)
  - wikidata_lexemes (Dutch/Frisian/Afrikaans/Limburgish lexemes, CC0)
  - delpher_magazines (pre-1880 Dutch periodicals, PD)

Also tests:
  - DocumentMeta.content_warnings and language_period fields (schema.py)
  - content_warnings on historical colonial sources (voc_wic_htr, globalise_voc)
"""
import json
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Schema — new ethical metadata fields
# ---------------------------------------------------------------------------

class TestDocumentMetaEthicalFields:
    def test_content_warnings_default_empty(self):
        from pipeline.schema import DocumentMeta
        meta = DocumentMeta(
            id="test_001",
            source="test",
            source_url="http://example.com",
            license="CC0",
            publication_year=2020,
            country="NL",
            domain="legal",
            language_variant="NL",
        )
        assert meta.content_warnings == []
        assert meta.language_period is None

    def test_content_warnings_colonial(self):
        from pipeline.schema import DocumentMeta
        meta = DocumentMeta(
            id="test_002",
            source="voc_wic_htr",
            source_url="https://zenodo.org/records/11209325",
            license="CC-BY",
            publication_year=1680,
            country="NL",
            domain="historical",
            language_variant="NL",
            language_period="early_modern",
            content_warnings=["colonial_terminology", "archaic_racial_language"],
        )
        assert "colonial_terminology" in meta.content_warnings
        assert "archaic_racial_language" in meta.content_warnings
        assert meta.language_period == "early_modern"

    def test_to_dict_includes_ethical_fields(self):
        from pipeline.schema import DocumentMeta
        meta = DocumentMeta(
            id="test_003",
            source="test",
            source_url="http://example.com",
            license="PD",
            publication_year=1850,
            country="NL",
            domain="historical",
            language_variant="NL",
            language_period="19th_century",
            content_warnings=["archaic_racial_language"],
        )
        d = meta.to_dict()
        assert d["language_period"] == "19th_century"
        assert "archaic_racial_language" in d["content_warnings"]

    def test_language_period_values(self):
        from pipeline.schema import DocumentMeta
        for period in ("early_modern", "19th_century", "contemporary", None):
            meta = DocumentMeta(
                id=f"test_period_{period}",
                source="test",
                source_url="http://example.com",
                license="CC0",
                publication_year=None,
                country="NL",
                domain="historical",
                language_variant="NL",
                language_period=period,
            )
            assert meta.language_period == period


# ---------------------------------------------------------------------------
# Acquirer import tests
# ---------------------------------------------------------------------------

class TestAcquireImports:
    def test_eupdcorp_import(self):
        from pipeline.acquire import eupdcorp
        assert callable(eupdcorp.download)

    def test_wikidata_lexemes_import(self):
        from pipeline.acquire import wikidata_lexemes
        assert callable(wikidata_lexemes.download)

    def test_delpher_magazines_import(self):
        from pipeline.acquire import delpher_magazines
        assert callable(delpher_magazines.download)


# ---------------------------------------------------------------------------
# Parser tests — eupdcorp
# ---------------------------------------------------------------------------

class TestParseEupdcorp:
    def _make_raw(self, tmp: Path, speeches: list[dict]) -> Path:
        raw = tmp / "eupdcorp"
        raw.mkdir()
        out = raw / "eupdcorp_nl_speeches.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for s in speeches:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
        return raw

    def test_basic_parse(self):
        from pipeline.parse.plain_text import parse_eupdcorp_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"speech_id": "1", "speaker": "J. de Vries", "date": "2003-04-15",
                 "term": "5", "text": "Mijnheer de Voorzitter, ik wil graag het woord nemen."},
                {"speech_id": "2", "speaker": "A. Jansen", "date": "2003-04-16",
                 "term": "5", "text": "Ik steun de resolutie volledig."},
            ])
            docs = list(parse_eupdcorp_dir(raw))
            assert len(docs) >= 1
            all_text = " ".join(d.text for d in docs)
            assert "Voorzitter" in all_text

    def test_metadata(self):
        from pipeline.parse.plain_text import parse_eupdcorp_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"speech_id": "99", "speaker": "T. Kok", "date": "2010-01-01",
                 "term": "7", "text": "Het Europees Parlement debatteert vandaag."},
            ])
            docs = list(parse_eupdcorp_dir(raw))
            assert len(docs) >= 1
            meta = docs[0].meta
            assert meta.source == "eupdcorp"
            assert meta.license == "CC-BY"
            assert meta.country == "EU"
            assert meta.domain == "parliamentary"
            assert meta.language_variant == "NL"

    def test_empty_speeches_skipped(self):
        from pipeline.parse.plain_text import parse_eupdcorp_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"speech_id": "1", "text": ""},
                {"speech_id": "2", "text": "   "},
                {"speech_id": "3", "text": "Goedemiddag, geachte collega's."},
            ])
            docs = list(parse_eupdcorp_dir(raw))
            all_text = " ".join(d.text for d in docs)
            assert "Goedemiddag" in all_text

    def test_missing_file_returns_empty(self):
        from pipeline.parse.plain_text import parse_eupdcorp_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "eupdcorp"
            raw.mkdir()
            docs = list(parse_eupdcorp_dir(raw))
            assert docs == []

    def test_speaker_and_date_in_text(self):
        from pipeline.parse.plain_text import parse_eupdcorp_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"speech_id": "5", "speaker": "Femke Halsema", "date": "2015-06-01",
                 "text": "Democratie vereist transparantie."},
            ])
            docs = list(parse_eupdcorp_dir(raw))
            all_text = " ".join(d.text for d in docs)
            assert "Femke Halsema" in all_text or "Democratie" in all_text


# ---------------------------------------------------------------------------
# Parser tests — wikidata_lexemes
# ---------------------------------------------------------------------------

class TestParseWikidataLexemes:
    def _make_raw(self, tmp: Path, lang: str, entries: list[dict]) -> Path:
        raw = tmp / "wikidata_lexemes"
        raw.mkdir()
        out = raw / f"wikidata_lexemes_{lang}_0000.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        return raw

    def test_basic_parse_nl(self):
        from pipeline.parse.plain_text import parse_wikidata_lexemes_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), "nl", [
                {"lexeme_id": "L123", "lemma": "fiets", "gloss": "een tweewielig rijwiel",
                 "language_code": "nl", "language_variant": "NL"},
                {"lexeme_id": "L124", "lemma": "huis", "gloss": "een gebouw om in te wonen",
                 "language_code": "nl", "language_variant": "NL"},
            ])
            docs = list(parse_wikidata_lexemes_dir(raw))
            assert len(docs) >= 1
            all_text = " ".join(d.text for d in docs)
            assert "fiets" in all_text
            assert "huis" in all_text

    def test_metadata_nl(self):
        from pipeline.parse.plain_text import parse_wikidata_lexemes_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), "nl", [
                {"lexeme_id": "L1", "lemma": "boom", "gloss": "een groot gewas met een stam",
                 "language_code": "nl", "language_variant": "NL"},
            ])
            docs = list(parse_wikidata_lexemes_dir(raw))
            assert len(docs) >= 1
            meta = docs[0].meta
            assert meta.source == "wikidata_lexemes"
            assert meta.license == "CC0"
            assert meta.language_variant == "NL"
            assert meta.country == "NL"

    def test_metadata_af(self):
        from pipeline.parse.plain_text import parse_wikidata_lexemes_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "wikidata_lexemes"
            raw.mkdir()
            out = raw / "wikidata_lexemes_af_0000.jsonl"
            with out.open("w", encoding="utf-8") as fh:
                for i in range(5):
                    fh.write(json.dumps({
                        "lexeme_id": f"L{i}", "lemma": f"woord{i}",
                        "gloss": f"definisie {i}", "language_code": "af",
                    }, ensure_ascii=False) + "\n")
            docs = list(parse_wikidata_lexemes_dir(raw))
            assert len(docs) >= 1
            assert docs[0].meta.language_variant == "AF"
            assert docs[0].meta.country == "ZA"

    def test_empty_entries_skipped(self):
        from pipeline.parse.plain_text import parse_wikidata_lexemes_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), "nl", [
                {"lexeme_id": "L1", "lemma": "", "gloss": ""},
                {"lexeme_id": "L2", "lemma": "tafel", "gloss": "een meubel met een plat blad"},
            ])
            docs = list(parse_wikidata_lexemes_dir(raw))
            all_text = " ".join(d.text for d in docs)
            assert "tafel" in all_text

    def test_missing_dir_returns_empty(self):
        from pipeline.parse.plain_text import parse_wikidata_lexemes_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "wikidata_lexemes"
            raw.mkdir()
            docs = list(parse_wikidata_lexemes_dir(raw))
            assert docs == []

    def test_lemma_gloss_formatted(self):
        from pipeline.parse.plain_text import parse_wikidata_lexemes_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), "nl", [
                {"lexeme_id": "L99", "lemma": "water", "gloss": "een vloeistof (H2O)",
                 "language_code": "nl"},
            ])
            docs = list(parse_wikidata_lexemes_dir(raw))
            all_text = " ".join(d.text for d in docs)
            assert "water: een vloeistof" in all_text


# ---------------------------------------------------------------------------
# Parser tests — delpher_magazines
# ---------------------------------------------------------------------------

class TestParseDelpherMagazines:
    def _make_raw(self, tmp: Path, records: list[dict]) -> Path:
        raw = tmp / "delpher_magazines"
        raw.mkdir()
        out = raw / "delpher_magazines_records.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return raw

    def test_basic_parse(self):
        from pipeline.parse.plain_text import parse_delpher_magazines_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"identifier": "oai:kb.nl:DDD:001", "title": "De Gids", "year": 1850,
                 "date": "1850-03", "text": "Een artikel over de Nederlandse samenleving."},
            ])
            docs = list(parse_delpher_magazines_dir(raw))
            assert len(docs) == 1
            assert "Gids" in docs[0].text or "Nederlandse" in docs[0].text

    def test_metadata(self):
        from pipeline.parse.plain_text import parse_delpher_magazines_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"identifier": "oai:kb.nl:DDD:002", "title": "Spectator", "year": 1780,
                 "date": "1780", "text": "Een beschouwing over de Verlichting."},
            ])
            docs = list(parse_delpher_magazines_dir(raw))
            assert len(docs) == 1
            meta = docs[0].meta
            assert meta.source == "delpher_magazines"
            assert meta.license == "PD"
            assert meta.country == "NL"
            assert meta.domain == "news"
            assert meta.historical_flag is True
            assert meta.publication_year == 1780

    def test_language_period_early_modern(self):
        from pipeline.parse.plain_text import parse_delpher_magazines_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"identifier": "oai:kb.nl:DDD:003", "year": 1750,
                 "text": "Oud tijdschrift tekst over de achttiende eeuw."},
            ])
            docs = list(parse_delpher_magazines_dir(raw))
            assert docs[0].meta.language_period == "early_modern"

    def test_language_period_19th_century(self):
        from pipeline.parse.plain_text import parse_delpher_magazines_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"identifier": "oai:kb.nl:DDD:004", "year": 1860,
                 "text": "Negentiende-eeuwse tekst over maatschappij."},
            ])
            docs = list(parse_delpher_magazines_dir(raw))
            assert docs[0].meta.language_period == "19th_century"

    def test_short_text_skipped(self):
        from pipeline.parse.plain_text import parse_delpher_magazines_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"identifier": "oai:kb.nl:DDD:005", "year": 1820, "text": "Te kort."},
                {"identifier": "oai:kb.nl:DDD:006", "year": 1830,
                 "text": "Dit is een langere tekst die wel voldoende is."},
            ])
            docs = list(parse_delpher_magazines_dir(raw))
            assert len(docs) == 1
            assert "langere" in docs[0].text

    def test_missing_file_returns_empty(self):
        from pipeline.parse.plain_text import parse_delpher_magazines_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "delpher_magazines"
            raw.mkdir()
            docs = list(parse_delpher_magazines_dir(raw))
            assert docs == []

    def test_title_prepended_to_text(self):
        from pipeline.parse.plain_text import parse_delpher_magazines_dir
        with tempfile.TemporaryDirectory() as tmp:
            raw = self._make_raw(Path(tmp), [
                {"identifier": "oai:kb.nl:DDD:007", "title": "De Nederlander",
                 "year": 1845, "text": "Artikeltekst zonder de titel erin verwerkt."},
            ])
            docs = list(parse_delpher_magazines_dir(raw))
            assert "De Nederlander" in docs[0].text


# ---------------------------------------------------------------------------
# Dispatch registration
# ---------------------------------------------------------------------------

class TestDispatchRegistration:
    def test_eupdcorp_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "eupdcorp" in parsers

    def test_wikidata_lexemes_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "wikidata_lexemes" in parsers

    def test_delpher_magazines_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "delpher_magazines" in parsers
