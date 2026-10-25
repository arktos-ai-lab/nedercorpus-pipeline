"""Tests for cleaning utilities."""
from pipeline.clean.html import strip_html
from pipeline.clean.encoding import to_utf8, normalize_unicode


def test_strip_html_basic():
    html = "<html><body><p>Hallo wereld</p><script>alert(1)</script></body></html>"
    assert "Hallo wereld" in strip_html(html)
    assert "alert" not in strip_html(html)


def test_strip_html_nav():
    html = "<nav>Menu item</nav><p>Content</p>"
    result = strip_html(html)
    assert "Content" in result
    assert "Menu item" not in result


def test_to_utf8_utf8():
    raw = "Hé".encode("utf-8")
    assert to_utf8(raw) == "Hé"


def test_to_utf8_latin1():
    raw = "Hé".encode("iso-8859-1")
    assert to_utf8(raw, hint="iso-8859-1") == "Hé"


def test_normalize_unicode_nfc():
    # é as NFD (e + combining acute) should become NFC
    nfd = "e\u0301"
    result = normalize_unicode(nfd)
    assert result == "é"
