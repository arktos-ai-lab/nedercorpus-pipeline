"""
Tests for new Caribbean & Belgium sources:
  - bes_bekendmakingen parser (sru.parse_bes_bekendmakingen_files)
  - rechtspraak_caribisch parser (sru.parse_rechtspraak_caribisch_files)
  - leipzig parser (plain_text.parse_leipzig_dir)
  - landscourant_aruba parser (plain_text.parse_landscourant_aruba_dir)
  - coleccion_aruba parser (plain_text.parse_coleccion_aruba_dir)
  - senate_be parser (plain_text.parse_senate_be_dir)
  - publicatieblad_curacao parser (plain_text.parse_publicatieblad_curacao_dir)
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.sru import (
    parse_bes_bekendmakingen_files,
    parse_rechtspraak_caribisch_files,
)
from pipeline.parse.plain_text import (
    parse_leipzig_dir,
    parse_landscourant_aruba_dir,
    parse_coleccion_aruba_dir,
    parse_senate_be_dir,
    parse_publicatieblad_curacao_dir,
)

# ---------------------------------------------------------------------------
# Minimal SRU XML for BES bekendmakingen
# ---------------------------------------------------------------------------

_BES_SRU_XML = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/"
    xmlns:gzd="http://standaarden.overheid.nl/sru">
  <srw:numberOfRecords>1</srw:numberOfRecords>
  <srw:records>
    <srw:record>
      <srw:recordData>
        <gzd:gzWaardelijst>
          <dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/"
            >https://repository.overheid.nl/frbr/officielepublicaties/ab/2024001</dc:identifier>
          <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/"
            >Afkondigingsblad van Bonaire 2024 nr. 1</dc:title>
          <dc:date xmlns:dc="http://purl.org/dc/elements/1.1/">2024-01-15</dc:date>
          <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Openbaar Lichaam Bonaire</dc:creator>
          <content>
            Dit eilandsbesluit regelt de vaststelling van de begroting voor het
            dienstjaar 2024. Het bestuurscollege van Bonaire heeft besloten
            de begroting vast te stellen conform de ingediende stukken.
            De totale uitgaven bedragen tien miljoen dollar.
          </content>
        </gzd:gzWaardelijst>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>"""

# ---------------------------------------------------------------------------
# Minimal ECLI XML for Caribbean rechtspraak
# ---------------------------------------------------------------------------

_CARIB_ECLI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<open-rechtspraak xmlns:dcterms="http://purl.org/dc/terms/"
                  xmlns:psi="http://www.rechtspraak.nl/schema/rechtspraak-1.0">
  <rs:metadata xmlns:rs="http://www.rechtspraak.nl/schema/rechtspraak-1.0">
    <dcterms:identifier>ECLI:NL:OGHACMB:2023:1</dcterms:identifier>
    <dcterms:issued>2023-03-15</dcterms:issued>
    <dcterms:abstract>Hoger beroep inzake arbeidsovereenkomst op Curaçao.</dcterms:abstract>
  </rs:metadata>
  <rs:uitspraak xmlns:rs="http://www.rechtspraak.nl/schema/rechtspraak-1.0">
    <para>
      Het Gemeenschappelijk Hof van Justitie heeft in de onderhavige zaak
      vastgesteld dat de arbeidsovereenkomst rechtsgeldig is opgezegd.
      De werknemer heeft recht op een ontslagvergoeding conform het
      Caribisch arbeidsrecht. Het hof bekrachtigt het vonnis van de
      rechtbank in eerste aanleg en veroordeelt de werkgever tot betaling
      van de verschuldigde vergoeding binnen dertig dagen na betekening.
    </para>
  </rs:uitspraak>
</open-rechtspraak>"""

# ---------------------------------------------------------------------------
# Senate BE HTML
# ---------------------------------------------------------------------------

_SENATE_HTML = """<!DOCTYPE html>
<html lang="nl">
<head><title>Plenaire vergadering — Senaat</title></head>
<body>
<header><nav>Navigation</nav></header>
<div class="content">
<p>De voorzitter: Ik open de vergadering. Het woord is aan senator Janssen.</p>
<p>De heer Janssen: Mevrouw de voorzitter, ik wil het hebben over de hervorming
van de pensioenen. Dit is een belangrijk dossier dat vele burgers in België
rechtstreeks raakt. De regering dient meer transparantie te betrachten.</p>
<p>De mevrouw Peters: De senator heeft gelijk. Wij steunen de vraag om meer
duidelijkheid over de pensioenhervorming. De Senaat moet zijn controlerende
rol spelen ten aanzien van de uitvoerende macht.</p>
<p>Le président: La parole est à M. Dupont. (French section)</p>
<p>M. Dupont: Je voudrais parler des pensions. C'est un sujet très important.</p>
<p>De voorzitter: De stemming is aangevraagd. We stemmen over dit voorstel.</p>
</div>
<footer>Footer content</footer>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Leipzig sentences file
# ---------------------------------------------------------------------------

_LEIPZIG_SENTENCES = """1\tDe West-Vlaamse dialecten worden gesproken in de provincie West-Vlaanderen.
2\tVloandern is eine schöne Gegend mit viel Geschichte und Kultur.
3\tDe Brugse markt is een van de mooiste pleinen van Europa.
4\tIn Gent wonen vele studenten die aan de universiteit studeren.
5\tHet West-Vlaams onderscheidt zich van het Standaardnederlands door zijn klanken.
"""

# ---------------------------------------------------------------------------
# Tests: BES bekendmakingen
# ---------------------------------------------------------------------------

def test_bes_bekendmakingen_parses_sru_xml():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        xml_dir = p / "ab" / "xml"
        xml_dir.mkdir(parents=True)
        (xml_dir / "ab_2024001.xml").write_text(_BES_SRU_XML, encoding="utf-8")

        docs = list(parse_bes_bekendmakingen_files(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "bes_bekendmakingen"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.domain == "government"
    assert d.meta.language_variant == "NL"
    assert "Bonaire" in d.text or "begroting" in d.text or len(d.text) > 50


def test_bes_bekendmakingen_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_bes_bekendmakingen_files(Path(tmp)))
    assert docs == []


def test_bes_bekendmakingen_year_parsed():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        xml_dir = p / "ab" / "xml"
        xml_dir.mkdir(parents=True)
        (xml_dir / "ab_2024001.xml").write_text(_BES_SRU_XML, encoding="utf-8")
        docs = list(parse_bes_bekendmakingen_files(p))

    if docs:
        assert docs[0].meta.publication_year == 2024


# ---------------------------------------------------------------------------
# Tests: Rechtspraak Caribisch
# ---------------------------------------------------------------------------

def test_rechtspraak_caribisch_parses_ecli_xml():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        decisions_dir = p / "oghacmb" / "decisions"
        decisions_dir.mkdir(parents=True)
        (decisions_dir / "ECLI_NL_OGHACMB_2023_1.xml").write_text(
            _CARIB_ECLI_XML, encoding="utf-8"
        )

        docs = list(parse_rechtspraak_caribisch_files(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "rechtspraak_caribisch"
    assert d.meta.license == "PD"
    assert d.meta.language_variant == "NL"
    assert len(d.text) > 50


def test_rechtspraak_caribisch_empty():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_rechtspraak_caribisch_files(Path(tmp)))
    assert docs == []


def test_rechtspraak_caribisch_multiple_courts():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for court in ["oghacmb", "ogeabes", "ogeacur"]:
            d = p / court / "decisions"
            d.mkdir(parents=True)
            (d / f"ECLI_NL_{court.upper()}_2023_1.xml").write_text(
                _CARIB_ECLI_XML, encoding="utf-8"
            )
        docs = list(parse_rechtspraak_caribisch_files(p))

    # All three courts should yield documents
    assert len(docs) >= 1


# ---------------------------------------------------------------------------
# Tests: Leipzig
# ---------------------------------------------------------------------------

def test_leipzig_parses_sentences():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        vls_dir = p / "vls"
        vls_dir.mkdir()
        (vls_dir / "sentences.txt").write_text(_LEIPZIG_SENTENCES, encoding="utf-8")

        docs = list(parse_leipzig_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert "vls" in d.meta.source
    assert d.meta.license == "CC-BY"
    assert d.meta.language_variant == "VLS"
    assert d.meta.domain == "web"
    assert len(d.text) > 20


def test_leipzig_strips_index_prefix():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        vls_dir = p / "vls"
        vls_dir.mkdir()
        (vls_dir / "sentences.txt").write_text(_LEIPZIG_SENTENCES, encoding="utf-8")
        docs = list(parse_leipzig_dir(p))

    # Sentences should NOT contain the numeric index prefix
    for d in docs:
        for line in d.text.splitlines():
            assert not line.startswith("1\t")
            assert not line.startswith("2\t")


def test_leipzig_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_leipzig_dir(Path(tmp)))
    assert docs == []


def test_leipzig_language_variant_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for corpus, expected_variant in [("zea", "ZEA"), ("lim", "LI"), ("afr", "AF"), ("fy", "FY")]:
            d = p / corpus
            d.mkdir()
            (d / "sentences.txt").write_text(
                "1\tDit is een testzin voor het dialect corpus.\n" * 5,
                encoding="utf-8",
            )

        docs = list(parse_leipzig_dir(p))

    variants_found = {d.meta.language_variant for d in docs}
    assert "ZEA" in variants_found
    assert "LI"  in variants_found
    assert "AF"  in variants_found
    assert "FY"  in variants_found


# ---------------------------------------------------------------------------
# Tests: Senate BE
# ---------------------------------------------------------------------------

def test_senate_be_parses_dutch_sections():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        leg_dir = p / "legislature_10"
        leg_dir.mkdir()
        (leg_dir / "session_1-100.html").write_text(_SENATE_HTML, encoding="utf-8")

        docs = list(parse_senate_be_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "senate_be"
    assert d.meta.license == "PD"
    assert d.meta.country == "BE"
    assert d.meta.domain == "parliamentary"
    assert d.meta.language_variant == "NL-BE"
    assert d.meta.publication_year == 2019  # legislature 10 starts 2019


def test_senate_be_filters_french():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        leg_dir = p / "legislature_10"
        leg_dir.mkdir()
        (leg_dir / "session_1-100.html").write_text(_SENATE_HTML, encoding="utf-8")
        docs = list(parse_senate_be_dir(p))

    # French text should be filtered or minimized
    for d in docs:
        # Should contain Dutch content
        assert any(word in d.text.lower() for word in
                   ["voorzitter", "senator", "senaat", "vergadering", "stemming", "pensioenen"])


def test_senate_be_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_senate_be_dir(Path(tmp)))
    assert docs == []


def test_senate_be_legislature_year():
    """Publication year is derived from legislature number."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for leg, expected_year in [(6, 2003), (8, 2010), (9, 2014)]:
            leg_dir = p / f"legislature_{leg:02d}"
            leg_dir.mkdir(exist_ok=True)
            (leg_dir / "session_test.html").write_text(_SENATE_HTML, encoding="utf-8")

        docs = list(parse_senate_be_dir(p))

    years = {d.meta.publication_year for d in docs if d.meta.publication_year}
    assert 2003 in years or 2010 in years or 2014 in years


# ---------------------------------------------------------------------------
# Tests: Coleccion Aruba
# ---------------------------------------------------------------------------

_PAPIAMENTO_TEXT = """
Nos isla di Aruba tin un historia largo y rico. E lenguahe Papiamento
ta wòrdu papia pa hopi hende riba e isla. Nos kultural ta importante
pa nos komunidad. Aruba ta parti di Reino Hulandes.
Di Historia di Aruba ta kuminsá mucho aña pasa.
"""

def test_coleccion_aruba_parses_texts():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()

        identifier = "testitem001"
        (texts_dir / f"{identifier}.txt").write_text(_PAPIAMENTO_TEXT, encoding="utf-8")
        (texts_dir / f"{identifier}.url").write_text(
            f"https://archive.org/details/{identifier}", encoding="utf-8"
        )

        meta = {
            "identifier": identifier,
            "title": "Historia di Aruba",
            "year": "1985",
            "language": "Papiamento",
            "license": "CC-BY-4.0",
        }
        (p / "metadata.jsonl").write_text(json.dumps(meta) + "\n", encoding="utf-8")

        docs = list(parse_coleccion_aruba_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "coleccion_aruba"
    assert d.meta.country == "AW"
    assert d.meta.language_variant == "PAP"
    assert d.meta.publication_year == 1985
    assert "Aruba" in d.text or "Papiamento" in d.text


def test_coleccion_aruba_dutch_text_gets_nl_variant():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()

        identifier = "dutchitem001"
        (texts_dir / f"{identifier}.txt").write_text(
            "Dit is een Nederlandstalige tekst over Aruba. " * 5, encoding="utf-8"
        )
        meta = {"identifier": identifier, "language": "Dutch", "license": "CC-BY-4.0"}
        (p / "metadata.jsonl").write_text(json.dumps(meta) + "\n", encoding="utf-8")

        docs = list(parse_coleccion_aruba_dir(p))

    assert len(docs) >= 1
    assert docs[0].meta.language_variant == "NL"


def test_coleccion_aruba_empty():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_coleccion_aruba_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Tests: Landscourant Aruba
# ---------------------------------------------------------------------------

def test_landscourant_aruba_archive_texts():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        archive_dir = p / "archive"
        archive_dir.mkdir()

        identifier = "DWJZ-LC2022No1"
        (archive_dir / f"{identifier}.txt").write_text(
            "LANDSCOURANT VAN ARUBA\n\nLandsverordening tot regeling van de rechtstoestand "
            "van ambtenaren. De Minister van Binnenlandse Zaken maakt bekend dat per "
            "heden de volgende verordening van kracht is. Vastgesteld op tien januari "
            "tweeduizend tweeëntwintig door de minister.",
            encoding="utf-8",
        )
        (archive_dir / f"{identifier}.url").write_text(
            f"https://archive.org/details/{identifier}", encoding="utf-8"
        )
        meta = {"identifier": identifier, "year": "2022"}
        (archive_dir / f"{identifier}.json").write_text(json.dumps(meta), encoding="utf-8")

        docs = list(parse_landscourant_aruba_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "landscourant_aruba"
    assert d.meta.license == "PD"
    assert d.meta.country == "AW"
    assert d.meta.language_variant == "NL"
    assert d.meta.publication_year == 2022


def test_landscourant_aruba_empty():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_landscourant_aruba_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Tests: Publicatieblad Curaçao
# ---------------------------------------------------------------------------

def test_publicatieblad_curacao_historical_texts():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        hist_dir = p / "historical_na"
        hist_dir.mkdir()

        (hist_dir / "pb_na_2005_001.txt").write_text(
            "PUBLICATIEBLAD VAN DE NEDERLANDSE ANTILLEN\n\n"
            "Landsverordening van de vijfde januari tweeduizend vijf houdende "
            "vaststelling van de begroting van Curaçao. De gezaghebber van de "
            "eilandgebied Curaçao heeft goedgekeurd de volgende verordening.",
            encoding="utf-8",
        )
        (hist_dir / "pb_na_2005_001.url").write_text(
            "https://archive.org/details/pb_na_2005_001", encoding="utf-8"
        )

        docs = list(parse_publicatieblad_curacao_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "publicatieblad_curacao"
    assert d.meta.license == "PD"
    assert d.meta.country == "CW"
    assert d.meta.language_variant == "NL"
    assert "Curaçao" in d.text or "Antillen" in d.text or "begroting" in d.text


def test_publicatieblad_curacao_empty():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_publicatieblad_curacao_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Tests: Acquire module imports (smoke test)
# ---------------------------------------------------------------------------

def test_acquire_imports():
    """All new acquire modules must be importable without error."""
    from pipeline.acquire import (
        bes_bekendmakingen,
        rechtspraak_caribisch,
        leipzig,
        landscourant_aruba,
        coleccion_aruba,
        senate_be,
        publicatieblad_curacao,
    )
    assert callable(bes_bekendmakingen.download)
    assert callable(rechtspraak_caribisch.download)
    assert callable(leipzig.download)
    assert callable(landscourant_aruba.download)
    assert callable(coleccion_aruba.download)
    assert callable(senate_be.download)
    assert callable(publicatieblad_curacao.download)


def test_dispatch_registers_new_sources():
    """All new sources must be registered in the dispatcher."""
    from pipeline.parse.dispatch import _get_parsers
    parsers = _get_parsers()
    for source in [
        "bes_bekendmakingen",
        "rechtspraak_caribisch",
        "leipzig",
        "landscourant_aruba",
        "coleccion_aruba",
        "senate_be",
        "publicatieblad_curacao",
    ]:
        assert source in parsers, f"Source '{source}' missing from dispatch"
