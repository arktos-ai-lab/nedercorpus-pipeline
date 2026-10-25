"""Tests for HTML page parser."""
from pipeline.parse.html_page import extract_html


def test_extracts_body_text():
    html = "<html><body><p>Juridische tekst over het strafrecht.</p></body></html>"
    text, title, lang = extract_html(html)
    assert "Juridische tekst" in text


def test_skips_nav_and_script():
    html = """<html><body>
      <nav>Menu item 1</nav>
      <script>alert('hack')</script>
      <main><p>Inhoud van het document.</p></main>
    </body></html>"""
    text, _, _ = extract_html(html)
    assert "Inhoud" in text
    assert "Menu item" not in text
    assert "alert" not in text


def test_extracts_title():
    html = "<html><head><title>Wet op de omzetbelasting</title></head><body><p>Tekst.</p></body></html>"
    _, title, _ = extract_html(html)
    assert title == "Wet op de omzetbelasting"


def test_extracts_lang():
    html = '<html lang="nl"><body><p>Tekst.</p></body></html>'
    _, _, lang = extract_html(html)
    assert lang == "nl"


def test_empty_html():
    text, title, lang = extract_html("")
    assert text == ""
    assert title == ""


def test_skips_footer():
    html = """<html><body>
      <article><p>Wetsartikel 1. De belasting bedraagt twintig procent.</p></article>
      <footer>Copyright 2024</footer>
    </body></html>"""
    text, _, _ = extract_html(html)
    assert "Wetsartikel" in text
    assert "Copyright" not in text
