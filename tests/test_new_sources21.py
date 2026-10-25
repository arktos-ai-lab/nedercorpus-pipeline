"""
Tests for Batch 4 new sources:
  - limburgish_wikisource, limburgish_wikibooks, limburgish_wikiquote, limburgish_wikinews
  - frisian_wikibooks, afrikaans_wikibooks, afrikaans_wikiquote
  - nchlt_af, autshumato_af, parlamint_be, tweede_kamer_opendata
"""
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Acquirer import tests
# ---------------------------------------------------------------------------

class TestAcquireImports:
    def test_limburgish_wikisource_import(self):
        from pipeline.acquire import limburgish_wikisource
        assert callable(limburgish_wikisource.download)

    def test_limburgish_wikibooks_import(self):
        from pipeline.acquire import limburgish_wikibooks
        assert callable(limburgish_wikibooks.download)

    def test_limburgish_wikiquote_import(self):
        from pipeline.acquire import limburgish_wikiquote
        assert callable(limburgish_wikiquote.download)

    def test_limburgish_wikinews_import(self):
        from pipeline.acquire import limburgish_wikinews
        assert callable(limburgish_wikinews.download)

    def test_frisian_wikibooks_import(self):
        from pipeline.acquire import frisian_wikibooks
        assert callable(frisian_wikibooks.download)

    def test_afrikaans_wikibooks_import(self):
        from pipeline.acquire import afrikaans_wikibooks
        assert callable(afrikaans_wikibooks.download)

    def test_afrikaans_wikiquote_import(self):
        from pipeline.acquire import afrikaans_wikiquote
        assert callable(afrikaans_wikiquote.download)

    def test_nchlt_af_import(self):
        from pipeline.acquire import nchlt_af
        assert callable(nchlt_af.download)

    def test_autshumato_af_import(self):
        from pipeline.acquire import autshumato_af
        assert callable(autshumato_af.download)

    def test_parlamint_be_import(self):
        from pipeline.acquire import parlamint_be
        assert callable(parlamint_be.download)

    def test_tweede_kamer_opendata_import(self):
        from pipeline.acquire import tweede_kamer_opendata
        assert callable(tweede_kamer_opendata.download)


# ---------------------------------------------------------------------------
# Acquirer dry_run tests
# ---------------------------------------------------------------------------

class TestAcquireDryRun:
    def test_limburgish_wikisource_dry_run(self, tmp_path):
        from pipeline.acquire import limburgish_wikisource
        limburgish_wikisource.download(tmp_path, dry_run=True)

    def test_limburgish_wikibooks_dry_run(self, tmp_path):
        from pipeline.acquire import limburgish_wikibooks
        limburgish_wikibooks.download(tmp_path, dry_run=True)

    def test_limburgish_wikiquote_dry_run(self, tmp_path):
        from pipeline.acquire import limburgish_wikiquote
        limburgish_wikiquote.download(tmp_path, dry_run=True)

    def test_limburgish_wikinews_dry_run(self, tmp_path):
        from pipeline.acquire import limburgish_wikinews
        limburgish_wikinews.download(tmp_path, dry_run=True)

    def test_frisian_wikibooks_dry_run(self, tmp_path):
        from pipeline.acquire import frisian_wikibooks
        frisian_wikibooks.download(tmp_path, dry_run=True)

    def test_afrikaans_wikibooks_dry_run(self, tmp_path):
        from pipeline.acquire import afrikaans_wikibooks
        afrikaans_wikibooks.download(tmp_path, dry_run=True)

    def test_afrikaans_wikiquote_dry_run(self, tmp_path):
        from pipeline.acquire import afrikaans_wikiquote
        afrikaans_wikiquote.download(tmp_path, dry_run=True)

    def test_nchlt_af_dry_run(self, tmp_path):
        from pipeline.acquire import nchlt_af
        nchlt_af.download(tmp_path, dry_run=True)

    def test_autshumato_af_dry_run(self, tmp_path):
        from pipeline.acquire import autshumato_af
        autshumato_af.download(tmp_path, dry_run=True)

    def test_parlamint_be_dry_run(self, tmp_path):
        from pipeline.acquire import parlamint_be
        parlamint_be.download(tmp_path, dry_run=True)

    def test_tweede_kamer_opendata_dry_run(self, tmp_path):
        from pipeline.acquire import tweede_kamer_opendata
        tweede_kamer_opendata.download(tmp_path, dry_run=True)


# ---------------------------------------------------------------------------
# Dispatch registration tests
# ---------------------------------------------------------------------------

class TestDispatchRegistration:
    def _parsers(self):
        from pipeline.parse.dispatch import _get_parsers
        return _get_parsers()

    def test_limburgish_wikisource_registered(self):
        assert "limburgish_wikisource" in self._parsers()

    def test_limburgish_wikibooks_registered(self):
        assert "limburgish_wikibooks" in self._parsers()

    def test_limburgish_wikiquote_registered(self):
        assert "limburgish_wikiquote" in self._parsers()

    def test_limburgish_wikinews_registered(self):
        assert "limburgish_wikinews" in self._parsers()

    def test_frisian_wikibooks_registered(self):
        assert "frisian_wikibooks" in self._parsers()

    def test_afrikaans_wikibooks_registered(self):
        assert "afrikaans_wikibooks" in self._parsers()

    def test_afrikaans_wikiquote_registered(self):
        assert "afrikaans_wikiquote" in self._parsers()

    def test_nchlt_af_registered(self):
        assert "nchlt_af" in self._parsers()

    def test_autshumato_af_registered(self):
        assert "autshumato_af" in self._parsers()

    def test_parlamint_be_registered(self):
        assert "parlamint_be" in self._parsers()

    def test_tweede_kamer_opendata_registered(self):
        assert "tweede_kamer_opendata" in self._parsers()


# ---------------------------------------------------------------------------
# Parser: parse_nchlt_af_dir
# ---------------------------------------------------------------------------

class TestParseNchltAf:
    def test_empty_dir_returns_no_docs(self, tmp_path):
        from pipeline.parse.plain_text import parse_nchlt_af_dir
        assert list(parse_nchlt_af_dir(tmp_path)) == []

    def test_txt_file_yields_doc(self, tmp_path):
        from pipeline.parse.plain_text import parse_nchlt_af_dir
        (tmp_path / "corpus.txt").write_text(
            "Die regering het aangekondig dat nuwe wette ingestel word om die ekonomie te versterk.",
            encoding="utf-8",
        )
        docs = list(parse_nchlt_af_dir(tmp_path))
        assert len(docs) == 1

    def test_metadata(self, tmp_path):
        from pipeline.parse.plain_text import parse_nchlt_af_dir
        (tmp_path / "corpus.txt").write_text(
            "Die regering het aangekondig dat nuwe wette ingestel word om die ekonomie te versterk.",
            encoding="utf-8",
        )
        doc = list(parse_nchlt_af_dir(tmp_path))[0]
        assert doc.meta.source == "nchlt_af"
        assert doc.meta.license == "CC-BY"
        assert doc.meta.country == "ZA"
        assert doc.meta.domain == "government"
        assert doc.meta.language_variant == "AF"

    def test_short_text_skipped(self, tmp_path):
        from pipeline.parse.plain_text import parse_nchlt_af_dir
        (tmp_path / "short.txt").write_text("kort", encoding="utf-8")
        assert list(parse_nchlt_af_dir(tmp_path)) == []

    def test_multiple_files(self, tmp_path):
        from pipeline.parse.plain_text import parse_nchlt_af_dir
        for i in range(3):
            (tmp_path / f"file{i}.txt").write_text(
                "Die Suid-Afrikaanse grondwet waarborg basiese regte vir alle burgers van die land.",
                encoding="utf-8",
            )
        docs = list(parse_nchlt_af_dir(tmp_path))
        assert len(docs) == 3


# ---------------------------------------------------------------------------
# Parser: parse_autshumato_af_dir
# ---------------------------------------------------------------------------

class TestParseAutshumatoAf:
    def test_empty_dir_returns_no_docs(self, tmp_path):
        from pipeline.parse.plain_text import parse_autshumato_af_dir
        assert list(parse_autshumato_af_dir(tmp_path)) == []

    def test_txt_file_yields_doc(self, tmp_path):
        from pipeline.parse.plain_text import parse_autshumato_af_dir
        lines = "\n".join(
            ["Die Minister van Finansies het 'n nuwe begroting aangekondig vir die volgende fiskale jaar."] * 60
        )
        (tmp_path / "corpus.txt").write_text(lines, encoding="utf-8")
        docs = list(parse_autshumato_af_dir(tmp_path))
        assert len(docs) >= 1

    def test_af_extension_file(self, tmp_path):
        from pipeline.parse.plain_text import parse_autshumato_af_dir
        lines = "\n".join(
            ["Afrikaans sin vir toets doeleindes van die parallelle korpus."] * 60
        )
        (tmp_path / "corpus.af").write_text(lines, encoding="utf-8")
        docs = list(parse_autshumato_af_dir(tmp_path))
        assert len(docs) >= 1

    def test_metadata(self, tmp_path):
        from pipeline.parse.plain_text import parse_autshumato_af_dir
        lines = "\n".join(
            ["Die Suid-Afrikaanse grondwet waarborg regte."] * 60
        )
        (tmp_path / "corpus.af").write_text(lines, encoding="utf-8")
        doc = list(parse_autshumato_af_dir(tmp_path))[0]
        assert doc.meta.source == "autshumato_af"
        assert doc.meta.license == "CC-BY"
        assert doc.meta.country == "ZA"
        assert doc.meta.domain == "government"
        assert doc.meta.language_variant == "AF"


# ---------------------------------------------------------------------------
# Parser: parse_parlamint_be_dir
# ---------------------------------------------------------------------------

_PARLAMINT_BE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<teiCorpus xmlns="http://www.tei-c.org/ns/1.0"
           xmlns:xml="http://www.w3.org/XML/1998/namespace">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>ParlaMint-BE test</title></titleStmt>
    </fileDesc>
    <profileDesc>
      <settingDesc>
        <setting><date when="2020-03-15"/></setting>
      </settingDesc>
    </profileDesc>
  </teiHeader>
  <TEI>
    <text>
      <body>
        <div>
          <u xml:id="utt1" xml:lang="nl">
            <seg>De minister heeft de begroting ingediend voor het volgende begrotingsjaar.</seg>
          </u>
          <u xml:id="utt2" xml:lang="fr">
            <seg>Le ministre a présenté le budget pour l'exercice prochain.</seg>
          </u>
          <u xml:id="utt3" xml:lang="nl-BE">
            <seg>Wij steunen dit voorstel voor de versterking van de sociale zekerheid in ons land.</seg>
          </u>
        </div>
      </body>
    </text>
  </TEI>
</teiCorpus>"""

_PARLAMINT_BE_ANA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<teiCorpus xmlns="http://www.tei-c.org/ns/1.0">
  <TEI><text><body><div>
    <u xml:lang="nl"><seg>Dit bestand wordt overgeslagen.</seg></u>
  </div></body></text></TEI>
</teiCorpus>"""


class TestParseParlaMintBe:
    def test_empty_dir_returns_no_docs(self, tmp_path):
        from pipeline.parse.plain_text import parse_parlamint_be_dir
        assert list(parse_parlamint_be_dir(tmp_path)) == []

    def test_dutch_utterances_only(self, tmp_path):
        from pipeline.parse.plain_text import parse_parlamint_be_dir
        tei_dir = tmp_path / "tei"
        tei_dir.mkdir()
        (tei_dir / "ParlaMint-BE_2020-03-15.xml").write_text(
            _PARLAMINT_BE_XML, encoding="utf-8"
        )
        docs = list(parse_parlamint_be_dir(tmp_path))
        # Only the 2 Dutch utterances (nl, nl-BE) should be returned, not the French one
        assert len(docs) == 2

    def test_ana_xml_skipped(self, tmp_path):
        from pipeline.parse.plain_text import parse_parlamint_be_dir
        tei_dir = tmp_path / "tei"
        tei_dir.mkdir()
        (tei_dir / "ParlaMint-BE_2020-03-15.xml").write_text(
            _PARLAMINT_BE_XML, encoding="utf-8"
        )
        (tei_dir / "ParlaMint-BE_2020-03-15-ana.xml").write_text(
            _PARLAMINT_BE_ANA_XML, encoding="utf-8"
        )
        docs = list(parse_parlamint_be_dir(tmp_path))
        # ana file should be skipped; only 2 Dutch docs from the main file
        assert len(docs) == 2

    def test_metadata(self, tmp_path):
        from pipeline.parse.plain_text import parse_parlamint_be_dir
        tei_dir = tmp_path / "tei"
        tei_dir.mkdir()
        (tei_dir / "ParlaMint-BE_2020-03-15.xml").write_text(
            _PARLAMINT_BE_XML, encoding="utf-8"
        )
        docs = list(parse_parlamint_be_dir(tmp_path))
        doc = docs[0]
        assert doc.meta.source == "parlamint_be"
        assert doc.meta.license == "CC-BY"
        assert doc.meta.country == "BE"
        assert doc.meta.domain == "parliamentary"
        assert doc.meta.language_variant == "NL-BE"
        assert doc.meta.publication_year == 2020

    def test_flat_dir_also_works(self, tmp_path):
        from pipeline.parse.plain_text import parse_parlamint_be_dir
        (tmp_path / "ParlaMint-BE_2020-03-15.xml").write_text(
            _PARLAMINT_BE_XML, encoding="utf-8"
        )
        docs = list(parse_parlamint_be_dir(tmp_path))
        assert len(docs) == 2


# ---------------------------------------------------------------------------
# Parser: parse_tweede_kamer_opendata_dir
# ---------------------------------------------------------------------------

_TK_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Tweede Kamer SyncFeed</title>
  <updated>2023-06-01T12:00:00Z</updated>
  <entry>
    <id>https://gegevensmagazijn.tweedekamer.nl/SyncFeed/2.0/Entiteiten/12345</id>
    <title>Plenaire vergadering 31 mei 2023</title>
    <summary>De Kamer heeft gedebatteerd over het klimaatbeleid en de energietransitie.</summary>
    <updated>2023-05-31T14:00:00Z</updated>
    <link rel="alternate" href="https://www.tweedekamer.nl/vergadering/12345"/>
  </entry>
  <entry>
    <id>https://gegevensmagazijn.tweedekamer.nl/SyncFeed/2.0/Entiteiten/67890</id>
    <title>Kamervraag over woningmarkt</title>
    <summary>Vraag van het lid Van Dam over de stijgende huurprijzen in de grote steden.</summary>
    <updated>2023-05-30T10:00:00Z</updated>
    <link rel="alternate" href="https://www.tweedekamer.nl/kamerstuk/67890"/>
  </entry>
</feed>"""


class TestParseTweedeKamerOpendata:
    def test_empty_dir_returns_no_docs(self, tmp_path):
        from pipeline.parse.plain_text import parse_tweede_kamer_opendata_dir
        assert list(parse_tweede_kamer_opendata_dir(tmp_path)) == []

    def test_feed_xml_yields_docs(self, tmp_path):
        from pipeline.parse.plain_text import parse_tweede_kamer_opendata_dir
        (tmp_path / "tk_feed_000000.xml").write_text(_TK_FEED_XML, encoding="utf-8")
        docs = list(parse_tweede_kamer_opendata_dir(tmp_path))
        assert len(docs) == 2

    def test_text_contains_title_and_summary(self, tmp_path):
        from pipeline.parse.plain_text import parse_tweede_kamer_opendata_dir
        (tmp_path / "tk_feed_000000.xml").write_text(_TK_FEED_XML, encoding="utf-8")
        docs = list(parse_tweede_kamer_opendata_dir(tmp_path))
        texts = " ".join(d.text for d in docs)
        assert "Plenaire vergadering" in texts
        assert "klimaatbeleid" in texts
        assert "woningmarkt" in texts

    def test_metadata(self, tmp_path):
        from pipeline.parse.plain_text import parse_tweede_kamer_opendata_dir
        (tmp_path / "tk_feed_000000.xml").write_text(_TK_FEED_XML, encoding="utf-8")
        docs = list(parse_tweede_kamer_opendata_dir(tmp_path))
        doc = docs[0]
        assert doc.meta.source == "tweede_kamer_opendata"
        assert doc.meta.license == "CC0"
        assert doc.meta.country == "NL"
        assert doc.meta.domain == "parliamentary"
        assert doc.meta.language_variant == "NL"

    def test_publication_year_extracted(self, tmp_path):
        from pipeline.parse.plain_text import parse_tweede_kamer_opendata_dir
        (tmp_path / "tk_feed_000000.xml").write_text(_TK_FEED_XML, encoding="utf-8")
        docs = list(parse_tweede_kamer_opendata_dir(tmp_path))
        assert docs[0].meta.publication_year == 2023

    def test_malformed_xml_skipped(self, tmp_path):
        from pipeline.parse.plain_text import parse_tweede_kamer_opendata_dir
        (tmp_path / "tk_feed_000000.xml").write_text("NOT XML <<< >>>", encoding="utf-8")
        (tmp_path / "tk_feed_000001.xml").write_text(_TK_FEED_XML, encoding="utf-8")
        docs = list(parse_tweede_kamer_opendata_dir(tmp_path))
        assert len(docs) == 2


# ---------------------------------------------------------------------------
# Wiki dump parser imports
# ---------------------------------------------------------------------------

class TestWikiDumpParsers:
    def test_limburgish_wikisource_parser_exists(self):
        from pipeline.parse.wiki_dump import parse_limburgish_wikisource_dir
        assert callable(parse_limburgish_wikisource_dir)

    def test_limburgish_wikibooks_parser_exists(self):
        from pipeline.parse.wiki_dump import parse_limburgish_wikibooks_dir
        assert callable(parse_limburgish_wikibooks_dir)

    def test_limburgish_wikiquote_parser_exists(self):
        from pipeline.parse.wiki_dump import parse_limburgish_wikiquote_dir
        assert callable(parse_limburgish_wikiquote_dir)

    def test_limburgish_wikinews_parser_exists(self):
        from pipeline.parse.wiki_dump import parse_limburgish_wikinews_dir
        assert callable(parse_limburgish_wikinews_dir)

    def test_frisian_wikibooks_parser_exists(self):
        from pipeline.parse.wiki_dump import parse_frisian_wikibooks_dir
        assert callable(parse_frisian_wikibooks_dir)

    def test_afrikaans_wikibooks_parser_exists(self):
        from pipeline.parse.wiki_dump import parse_afrikaans_wikibooks_dir
        assert callable(parse_afrikaans_wikibooks_dir)

    def test_afrikaans_wikiquote_parser_exists(self):
        from pipeline.parse.wiki_dump import parse_afrikaans_wikiquote_dir
        assert callable(parse_afrikaans_wikiquote_dir)

    def test_empty_dir_returns_no_docs(self, tmp_path):
        from pipeline.parse.wiki_dump import parse_limburgish_wikisource_dir
        docs = list(parse_limburgish_wikisource_dir(tmp_path))
        assert docs == []
