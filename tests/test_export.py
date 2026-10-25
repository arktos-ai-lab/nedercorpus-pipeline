"""Tests for Parquet export (and JSONL fallback)."""
import tempfile
from pathlib import Path

from pipeline.document import Document, write_jsonl, make_id
from pipeline.schema import DocumentMeta
from pipeline.export import export_from_dir, _write_jsonl_fallback


def _make_docs(n: int = 5) -> list[Document]:
    docs = []
    for i in range(n):
        meta = DocumentMeta(
            id=make_id("test", str(i)),
            source="test",
            source_url="https://example.com",
            license="PD",
            publication_year=2020 + i,
            country="NL",
            domain="legal",
            language_variant="NL",
        )
        docs.append(Document(
            text=f"Dit is testdocument nummer {i} over het Nederlandse rechtsstelsel.",
            meta=meta,
        ))
    return docs


def test_jsonl_fallback_export():
    docs = _make_docs(3)
    with tempfile.TemporaryDirectory() as tmp:
        # Write input JSONL
        in_dir = Path(tmp) / "input"
        in_dir.mkdir()
        write_jsonl(iter(docs), in_dir / "test.jsonl")

        # Export
        out_dir = Path(tmp) / "output"
        total = export_from_dir(in_dir, out_dir)

    assert total == 3


def test_parquet_export_if_available():
    try:
        import pyarrow
    except ImportError:
        return  # skip if pyarrow not installed

    docs = _make_docs(10)
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = Path(tmp) / "input"
        in_dir.mkdir()
        write_jsonl(iter(docs), in_dir / "test.jsonl")

        out_dir = Path(tmp) / "output"
        total = export_from_dir(in_dir, out_dir, shard_size=4)

        assert total == 10
        parquet_files = list(out_dir.glob("*.parquet"))
        assert len(parquet_files) == 3  # ceil(10/4) = 3 shards


def test_parquet_schema_fields():
    """All required fields present in Parquet output."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return

    docs = _make_docs(2)
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = Path(tmp) / "input"
        in_dir.mkdir()
        write_jsonl(iter(docs), in_dir / "test.jsonl")
        out_dir = Path(tmp) / "output"
        export_from_dir(in_dir, out_dir)

        pf = list(out_dir.glob("*.parquet"))[0]
        schema = pq.read_schema(str(pf))
        required = {"id", "text", "source", "license", "country", "domain"}
        assert required.issubset(set(schema.names))
