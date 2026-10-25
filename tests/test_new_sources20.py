"""
Tests for PMC NL source:
  - pmc_nl (plain_text.parse_pmc_nl_dir)
"""
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_pmc_nl_dir

# ---------------------------------------------------------------------------
# JATS XML fixture — realistic Dutch biomedical article
# ---------------------------------------------------------------------------

_JATS_NL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving and Interchange DTD v1.2 20190208//EN"
  "https://jats.nlm.nih.gov/archiving/1.2/JATS-archivearticle1.dtd">
<article xml:lang="nl" article-type="research-article">
  <front>
    <article-meta>
      <article-id pub-id-type="pmc">12345678</article-id>
      <article-id pub-id-type="doi">10.1234/ntv.2020.001</article-id>
      <title-group>
        <article-title>De invloed van leefstijlinterventies op type 2 diabetes mellitus in de Nederlandse bevolking</article-title>
      </title-group>
      <pub-date pub-type="epub">
        <year>2020</year>
        <month>3</month>
        <day>15</day>
      </pub-date>
      <permissions>
        <license license-type="open-access"
          xlink:href="http://creativecommons.org/licenses/by/4.0/"
          xmlns:xlink="http://www.w3.org/1999/xlink">
          <license-p>This is an open access article under the CC BY license.</license-p>
        </license>
      </permissions>
      <abstract>
        <p>Achtergrond: Type 2 diabetes mellitus is een toenemend probleem in Nederland,
        mede als gevolg van vergrijzing en veranderde leefstijlpatronen.
        Methoden: In deze gerandomiseerde gecontroleerde studie werden 340 patiënten
        met prediabetes gedurende 12 maanden gevolgd.
        Resultaten: De interventiegroep vertoonde een significante verbetering in
        nuchtere bloedglucose (p &lt; 0,001) en gewichtsverlies.
        Conclusie: Intensieve leefstijlinterventies zijn effectief bij het voorkomen
        van type 2 diabetes bij Nederlandse patiënten met prediabetes.</p>
      </abstract>
    </article-meta>
  </front>
  <body>
    <sec>
      <title>Inleiding</title>
      <p>Type 2 diabetes mellitus treft in Nederland naar schatting 1,1 miljoen mensen.
      De ziekte wordt gekenmerkt door insulineresistentie en een progressieve daling
      van de bètacelfunctie. Vroegtijdige preventie via leefstijlaanpassingen is
      de meest effectieve strategie om de diabetesprevalentie te beperken.</p>
    </sec>
    <sec>
      <title>Methoden</title>
      <p>Patiënten werden geworven via huisartspraktijken in de regio Utrecht.
      De interventie bestond uit wekelijkse begeleiding door een diëtist en
      maandelijkse consulten bij de huisarts gedurende twaalf maanden.</p>
    </sec>
  </body>
</article>"""

_JATS_EN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<article xml:lang="en" article-type="research-article">
  <front>
    <article-meta>
      <article-id pub-id-type="pmc">99999999</article-id>
      <title-group>
        <article-title>Lifestyle interventions in type 2 diabetes</article-title>
      </title-group>
      <pub-date><year>2021</year></pub-date>
      <permissions>
        <license xlink:href="http://creativecommons.org/licenses/by/4.0/"
          xmlns:xlink="http://www.w3.org/1999/xlink"/>
      </permissions>
      <abstract><p>This is an English-language article about diabetes management.</p></abstract>
    </article-meta>
  </front>
</article>"""

_JATS_NC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<article xml:lang="nl" article-type="research-article">
  <front>
    <article-meta>
      <article-id pub-id-type="pmc">77777777</article-id>
      <title-group>
        <article-title>Een artikel met NC licentie</article-title>
      </title-group>
      <pub-date><year>2019</year></pub-date>
      <permissions>
        <license xlink:href="http://creativecommons.org/licenses/by-nc/4.0/"
          xmlns:xlink="http://www.w3.org/1999/xlink"/>
      </permissions>
      <abstract><p>Dit artikel heeft een niet-commerciële licentie en moet worden overgeslagen.</p></abstract>
    </article-meta>
  </front>
</article>"""


class TestParsePmcNl:
    def test_empty_dir_returns_no_docs(self):
        with tempfile.TemporaryDirectory() as td:
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert docs == []

    def test_dutch_jats_yields_doc(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "PMC12345678.xml").write_text(_JATS_NL_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert len(docs) == 1

    def test_text_contains_title_and_abstract(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "PMC12345678.xml").write_text(_JATS_NL_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        text = docs[0].text
        assert "diabetes" in text
        assert "leefstijl" in text
        assert "insulineresistentie" in text

    def test_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "PMC12345678.xml").write_text(_JATS_NL_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        doc = docs[0]
        assert doc.meta.source == "pmc_nl"
        assert doc.meta.license == "CC-BY"
        assert doc.meta.domain == "academic"
        assert doc.meta.language_variant == "NL"
        assert doc.meta.country == "NL"

    def test_publication_year(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "PMC12345678.xml").write_text(_JATS_NL_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert docs[0].meta.publication_year == 2020

    def test_doi_in_source_url(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "PMC12345678.xml").write_text(_JATS_NL_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert "doi.org" in docs[0].meta.source_url

    def test_english_article_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "PMC99999999.xml").write_text(_JATS_EN_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert docs == []

    def test_nc_license_article_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "PMC77777777.xml").write_text(_JATS_NC_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert docs == []

    def test_flat_dir_also_works(self):
        """Parser should also find XMLs directly in raw_dir (no articles/ subdir)."""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "PMC12345678.xml").write_text(_JATS_NL_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert len(docs) == 1

    def test_malformed_xml_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "articles"
            art_dir.mkdir()
            (art_dir / "bad.xml").write_text("NOT XML <<< >>>", encoding="utf-8")
            (art_dir / "PMC12345678.xml").write_text(_JATS_NL_XML, encoding="utf-8")
            docs = list(parse_pmc_nl_dir(Path(td)))
        assert len(docs) == 1


class TestDispatchRegistration:
    def test_pmc_nl_registered(self):
        from pipeline.parse.dispatch import _get_parsers
        parsers = _get_parsers()
        assert "pmc_nl" in parsers


class TestAcquireImport:
    def test_pmc_nl_import(self):
        from pipeline.acquire import pmc_nl
        assert callable(pmc_nl.download)
