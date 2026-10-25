"""Tests for full cleaning pipeline."""
from pipeline.clean.pipeline import clean, CleanConfig


def test_clean_basic():
    text = "   " + "Dit is een normale Nederlandse zin over het Nederlandse rechtssysteem. " * 3 + "   "
    result = clean(text)
    assert result is not None
    assert "Nederlandse" in result


def test_clean_strips_html():
    body = "Juridische tekst over bestuursrecht en procedures in Nederland. " * 3
    html = f"<html><body><nav>Menu</nav><p>{body}</p></body></html>"
    result = clean(html)
    assert result is not None
    assert "Menu" not in result
    assert "Juridische" in result


def test_clean_rejects_short():
    cfg = CleanConfig(min_chars=100)
    assert clean("Te kort.", cfg) is None


def test_clean_rejects_high_digit_ratio():
    # Mostly numbers — not useful text
    cfg = CleanConfig(min_chars=10)
    assert clean("12345 67890 11111 22222 33333", cfg) is None


def test_clean_preserves_newlines():
    para = "alinea over het Nederlandse bestuursrecht en de rechtspraak. " * 2
    text = f"Eerste {para}\n\nTweede {para}\n\nDerde {para}"
    result = clean(text)
    assert result is not None
    assert "\n" in result


def test_clean_collapses_excessive_newlines():
    para = "tekst over juridische procedures en rechtspraak in Nederland. " * 2
    text = f"Begin {para}\n\n\n\n\nEinde {para}"
    result = clean(text)
    assert result is not None
    assert "\n\n\n" not in result
