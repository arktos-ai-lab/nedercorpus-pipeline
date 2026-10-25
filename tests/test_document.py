"""Tests for Document model and JSONL I/O."""
import tempfile
from pathlib import Path

from pipeline.document import Document, write_jsonl, read_jsonl, make_id
from pipeline.schema import DocumentMeta


def _make_doc(text: str = "Dit is een test.", source: str = "test") -> Document:
    meta = DocumentMeta(
        id=make_id(source),
        source=source,
        source_url="http://example.com",
        license="PD",
        publication_year=2023,
        country="NL",
        domain="legal",
        language_variant="NL",
    )
    return Document(text=text, meta=meta)


def test_roundtrip_jsonl():
    docs = [_make_doc(f"Document nummer {i}.") for i in range(5)]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.jsonl"
        written = write_jsonl(iter(docs), path)
        assert written == 5
        restored = list(read_jsonl(path))
    assert len(restored) == 5
    for orig, rest in zip(docs, restored):
        assert orig.text == rest.text
        assert orig.meta.source == rest.meta.source
        assert orig.meta.license == rest.meta.license


def test_document_with_original():
    doc = _make_doc("Moderne tekst.")
    doc.original_text = "Oud tekſt."
    d = doc.to_dict()
    assert d["original_text"] == "Oud tekſt."
    restored = Document.from_dict(d)
    assert restored.original_text == "Oud tekſt."


def test_make_id_with_raw():
    assert make_id("rechtspraak", "ECLI:NL:HR:2020:1234") == "rechtspraak:ECLI:NL:HR:2020:1234"


def test_make_id_generates_uuid():
    id1 = make_id("test")
    id2 = make_id("test")
    assert id1.startswith("test:")
    assert id1 != id2
