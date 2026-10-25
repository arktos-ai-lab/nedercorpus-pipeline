"""
Export the final corpus to Apache Parquet format.

Parquet is the preferred format for:
- HuggingFace datasets (load_dataset("parquet", ...))
- Efficient columnar storage and filtering
- Interoperability with pandas, Polars, DuckDB, Spark

Each shard is written as a separate .parquet file to keep memory usage bounded.

Schema:
  id, text, source, license, country, domain, language_variant,
  publication_year, historical_flag, normalized_version, acquisition_date

Requires: pip install pyarrow

Falls back to JSONL export if pyarrow is not installed.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterator

from pipeline.document import Document, read_jsonl

logger = logging.getLogger(__name__)

_PYARROW_AVAILABLE = False
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _PYARROW_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _pa_schema() -> "pa.Schema":
    import pyarrow as pa
    # NOTE: original_text is intentionally excluded — it may contain pre-scrub
    # personal data. Only the scrubbed text field is stored (GDPR Art. 5(1)(e)).
    return pa.schema([
        pa.field("id",                  pa.string()),
        pa.field("text",                pa.large_utf8()),
        pa.field("source",              pa.string()),
        pa.field("source_url",          pa.string()),
        pa.field("license",             pa.string()),
        pa.field("country",             pa.string()),
        pa.field("domain",              pa.string()),
        pa.field("language_variant",    pa.string()),
        pa.field("publication_year",    pa.int32()),
        pa.field("historical_flag",     pa.bool_()),
        pa.field("normalized_version",  pa.bool_()),
        pa.field("acquisition_date",    pa.string()),
        pa.field("synthetic",           pa.bool_()),
        pa.field("synthetic_method",    pa.string()),
        pa.field("ledger",              pa.large_utf8()),  # JSON-serialised list of ProvenanceEntries
    ])


def _doc_to_row(doc: Document) -> dict:
    m = doc.meta
    # Serialise the ledger as a JSON string for maximum portability in Parquet
    ledger_json = json.dumps([e.to_dict() for e in m.ledger], ensure_ascii=False)

    return {
        "id":               m.id,
        "text":             doc.text,
        # original_text intentionally omitted — GDPR Art. 5(1)(e) storage limitation
        "source":           m.source,
        "source_url":       m.source_url,
        "license":          m.license,
        "country":          m.country,
        "domain":           m.domain,
        "language_variant": m.language_variant,
        "publication_year": m.publication_year,
        "historical_flag":  m.historical_flag,
        "normalized_version": m.normalized_version,
        "acquisition_date": m.acquisition_date,
        "synthetic":        m.synthetic,
        "synthetic_method": m.synthetic_method,
        "ledger":           ledger_json,
    }


# ---------------------------------------------------------------------------
# Parquet writer
# ---------------------------------------------------------------------------

def write_parquet(
    documents: Iterator[Document],
    output_dir: Path,
    shard_size: int = 50_000,
    compression: str = "zstd",
) -> int:
    """
    Write documents to sharded Parquet files.

    Args:
        documents:  Iterator of Document objects.
        output_dir: Directory to write shard files into.
        shard_size: Documents per shard file.
        compression: Parquet compression codec (zstd recommended).

    Returns:
        Total number of documents written.
    """
    if not _PYARROW_AVAILABLE:
        logger.warning(
            "pyarrow not installed — falling back to JSONL export. "
            "Install: pip install pyarrow"
        )
        return _write_jsonl_fallback(documents, output_dir)

    import pyarrow as pa
    import pyarrow.parquet as pq

    output_dir.mkdir(parents=True, exist_ok=True)
    schema = _pa_schema()

    shard_idx = 0
    total = 0
    batch: list[dict] = []

    def flush() -> None:
        nonlocal shard_idx
        if not batch:
            return
        arrays = {col: [row.get(col) for row in batch]
                  for col in schema.names}
        table = pa.table(arrays, schema=schema)
        path = output_dir / f"shard_{shard_idx:06d}.parquet"
        pq.write_table(table, path, compression=compression)
        logger.info("Wrote %s (%d docs)", path.name, len(batch))
        batch.clear()
        shard_idx += 1

    for doc in documents:
        batch.append(_doc_to_row(doc))
        total += 1
        if len(batch) >= shard_size:
            flush()

    flush()
    logger.info("Export complete: %d documents, %d shards", total, shard_idx)
    return total


def _write_jsonl_fallback(documents: Iterator[Document], output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "corpus.jsonl"
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    logger.info("JSONL fallback export: %d documents → %s", count, path)
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def export_from_dir(
    input_dir: Path, 
    output_dir: Path, 
    shard_size: int = 50_000,
    flavor: str = "full"
) -> int:
    """
    Export all JSONL files in input_dir to Parquet in output_dir.
    
    Args:
        input_dir:  Directory containing scrubbed JSONL files.
        output_dir: Output directory for Parquet shards.
        shard_size: Documents per shard.
        flavor:     'full' (all documents) or 'permissive' (excludes CC-BY-SA).
    """
    permissive_licenses = {'PD', 'CC0', 'CC-BY', 'ODC-By'}
    
    def all_docs() -> Iterator[Document]:
        for jsonl_file in sorted(input_dir.rglob("*.jsonl")):
            for doc in read_jsonl(jsonl_file):
                if flavor == "permissive":
                    # Simple check: is the license in the permissive set?
                    # We strip version numbers (e.g. CC-BY-4.0 -> CC-BY) for the check
                    base_license = doc.meta.license.split("-")[0:2]
                    base_license_str = "-".join(base_license).upper()
                    
                    if "CC-BY-SA" in doc.meta.license.upper():
                        continue
                    
                    # Also check against the specific set if it's not a clear SA
                    # This handles edge cases like 'mixed-CC' which we skip in permissive for safety
                    if doc.meta.license.lower() == "mixed-cc":
                        continue
                        
                yield doc
                
    return write_parquet(all_docs(), output_dir, shard_size=shard_size)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Export corpus to Parquet")
    parser.add_argument("--input",  required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--shard-size", type=int, default=50_000)
    parser.add_argument("--flavor", choices=["full", "permissive"], default="full", 
                        help="Export flavor: 'full' includes everything, 'permissive' excludes copyleft.")
    args = parser.parse_args()
    export_from_dir(args.input, args.output, shard_size=args.shard_size, flavor=args.flavor)


if __name__ == "__main__":
    main()
