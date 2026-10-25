"""Tests for the historical normalization pipeline."""
from pipeline.normalize.pipeline import normalize_text, normalize_document, NormalizeConfig
from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta


def _historical_doc(text: str) -> Document:
    meta = DocumentMeta(
        id=make_id("dbnl"),
        source="dbnl",
        source_url="https://dbnl.org/test",
        license="PD",
        publication_year=1650,
        country="NL",
        domain="literary",
        language_variant="NL",
        historical_flag=True,
    )
    return Document(text=text, meta=meta)


def test_normalize_text_long_s():
    result = normalize_text("De beſchrijving van het koningrijck.")
    assert "ſ" not in result
    assert "beschrijving" in result


def test_normalize_document_preserves_original():
    doc = _historical_doc("De beſchrijving van het coningrijck.")
    normalized = normalize_document(doc)
    assert normalized.original_text == "De beſchrijving van het coningrijck."
    assert "ſ" not in normalized.text
    assert normalized.meta.normalized_version is True


def test_normalize_document_skips_modern():
    meta = DocumentMeta(
        id=make_id("wikipedia"),
        source="wikipedia",
        source_url="https://nl.wikipedia.org",
        license="CC-BY-SA",
        publication_year=2023,
        country="NL",
        domain="encyclopedic",
        language_variant="NL",
        historical_flag=False,
    )
    doc = Document(text="Moderne tekst.", meta=meta)
    result = normalize_document(doc)
    assert result.meta.normalized_version is False
    assert result.original_text is None


def test_normalize_lexicon_replacement():
    cfg = NormalizeConfig(apply_rules=False, apply_lexicon=True)
    result = normalize_text("Het sijn de saecken van het coninck.", cfg)
    assert "zijn" in result
    assert "zaken" in result
