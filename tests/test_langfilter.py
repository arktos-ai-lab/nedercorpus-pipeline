"""Tests for language filter."""
from pipeline.langfilter import is_dutch, filter_document, LangFilterConfig
from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta


def _doc(text: str, country: str = "NL") -> Document:
    meta = DocumentMeta(
        id=make_id("test"),
        source="test",
        source_url="",
        license="PD",
        publication_year=2023,
        country=country,
        domain="legal",
        language_variant="NL",
    )
    return Document(text=text, meta=meta)


def test_dutch_text_accepted():
    text = "De rechtbank heeft geoordeeld dat het beroep gegrond is. " * 5
    assert is_dutch(text) is True


def test_suriname_always_passes():
    """SR documents skip language detection (trust source metadata)."""
    doc = _doc("This is English text but from Suriname.", country="SR")
    assert filter_document(doc) is True


def test_belgium_always_passes():
    """BE documents skip language detection."""
    doc = _doc("Ce texte est en français.", country="BE")
    assert filter_document(doc) is True


def test_very_short_text_passes_on_error():
    cfg = LangFilterConfig(min_chars_for_detection=100, pass_on_error=True)
    assert is_dutch("Kort.", cfg) is True


def test_very_short_text_fails_on_strict():
    import pipeline.langfilter as lf
    if not lf._LANGDETECT_AVAILABLE:
        return  # without langdetect the function always returns True
    cfg = LangFilterConfig(min_chars_for_detection=100, pass_on_error=False)
    assert is_dutch("Kort.", cfg) is False


def test_no_langdetect_passes_all():
    """If langdetect is not installed, everything passes through."""
    import pipeline.langfilter as lf
    original = lf._LANGDETECT_AVAILABLE
    try:
        lf._LANGDETECT_AVAILABLE = False
        assert is_dutch("Completely irrelevant text in any language.") is True
    finally:
        lf._LANGDETECT_AVAILABLE = original
