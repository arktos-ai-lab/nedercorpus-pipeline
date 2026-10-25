"""Fetch per-page license metadata (url, license_abbr, disagreement) for C5 nld/afr/fry
parquet files, reading only those columns. One output file per input file (written to
.tmp then renamed); finished files are skipped on restart."""
import os, sys, json
from pathlib import Path
import pyarrow.parquet as pq
from huggingface_hub import HfApi, HfFileSystem

REPO = "BramVanroy/CommonCrawl-CreativeCommons"
out_dir = Path(sys.argv[1]); out_dir.mkdir(parents=True, exist_ok=True)
fs = HfFileSystem()
files = [f for f in HfApi().list_repo_files(REPO, repo_type="dataset")
         if f.endswith(".parquet") and any(f"/{l}/" in f for l in ("nld", "afr", "fry"))]
COLS = ["url", "license_abbr", "license_disagreement", "license_parse_error"]
def work(f):
    dest = out_dir / (f.replace("/", "__") + ".jsonl")
    if dest.exists():
        return
    pf = pq.ParquetFile(fs.open(f"datasets/{REPO}/{f}", "rb"))
    cols = [c for c in COLS if c in pf.schema_arrow.names]
    t = pf.read(columns=cols)
    tmp = dest.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        data = {c: t.column(c).to_pylist() for c in cols}
        for i in range(t.num_rows):
            fh.write(json.dumps({c: data[c][i] for c in cols}) + "\n")
        fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, dest)
    print(f, t.num_rows, flush=True)

from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(8) as ex:
    list(ex.map(work, sorted(files)))
print("DONE", flush=True)
