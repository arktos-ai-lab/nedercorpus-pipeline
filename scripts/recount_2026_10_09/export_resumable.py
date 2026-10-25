"""Re-export data/normalized to Parquet (50,000-doc shards), resumable per shard.

Same row schema and document order as pipeline.export.export_from_dir. Shards are
written to <out>/shard_NNNNNN.parquet via a .tmp file + rename; on restart, completed
shards are skipped (their documents are re-read but not re-written).

Usage: python export_resumable.py F:/NederCorpus/data/normalized F:/NederCorpus/data/final_v2
"""
import os, sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.document import read_jsonl            # noqa: E402
from pipeline.export import _doc_to_row, _pa_schema  # noqa: E402
import pyarrow as pa
import pyarrow.parquet as pq

src, out = Path(sys.argv[1]), Path(sys.argv[2])
SHARD = 50_000
out.mkdir(parents=True, exist_ok=True)
schema = _pa_schema()
done = {p.name for p in out.glob("shard_*.parquet")}
batch, idx, total = [], 0, 0


def flush():
    global idx
    name = f"shard_{idx:06d}.parquet"
    if name not in done:
        table = pa.table({c: [r.get(c) for r in batch] for c in schema.names}, schema=schema)
        tmp = out / (name + ".tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, out / name)
        print(f"wrote {name} ({len(batch)})", flush=True)
    batch.clear()
    idx += 1


for f in sorted(src.rglob("*.jsonl")):
    for doc in read_jsonl(f):
        if f"shard_{idx:06d}.parquet" in done:
            batch.append(None)          # placeholder; shard already on disk
        else:
            batch.append(_doc_to_row(doc))
        total += 1
        if len(batch) >= SHARD:
            flush()
if batch:
    flush()
(out / "_EXPORT_DONE.json").write_text(json.dumps({"documents": total, "shards": idx}))
print(f"DONE {total} documents, {idx} shards", flush=True)
