"""Resumable whitespace-token stats over NederCorpus normalized JSONL or final parquet.

One checkpoint line per input file (flushed + fsynced); completed files are skipped
on restart. Usage: python corpus_stats.py normalized|final <checkpoint.jsonl>
"""
import os, sys, json, collections
from pathlib import Path
import orjson

mode, ckpt = sys.argv[1], Path(sys.argv[2])
done = set()
if ckpt.exists():
    for line in ckpt.read_text(encoding="utf-8").splitlines():
        if line.strip():
            done.add(json.loads(line)["file"])

def new():
    return {"docs": 0, "tok": 0, "docs_ge10": 0, "tok_ge10": 0}

def add(agg, key, n):
    d = agg.setdefault(key, new())
    d["docs"] += 1; d["tok"] += n
    if n >= 10:
        d["docs_ge10"] += 1; d["tok_ge10"] += n

if mode == "normalized":
    files = sorted(Path("F:/NederCorpus/data/normalized").glob("*.jsonl"))
else:
    files = sorted(Path("F:/NederCorpus/data/final").glob("*.parquet"))

out = open(ckpt, "a", encoding="utf-8")
for p in files:
    if p.name in done:
        continue
    res = {"file": p.name, "total": new(), "by_source": {}, "by_license": {},
           "by_variant": {}, "by_domain": {}, "by_src_lic": {}}
    def rec(src, lic, var, dom, n):
        t = res["total"]; t["docs"] += 1; t["tok"] += n
        if n >= 10: t["docs_ge10"] += 1; t["tok_ge10"] += n
        add(res["by_source"], src, n); add(res["by_license"], lic, n)
        add(res["by_variant"], var, n); add(res["by_domain"], dom, n)
        add(res["by_src_lic"], f"{src}|{lic}", n)
    if mode == "normalized":
        with open(p, "rb") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = orjson.loads(line)
                except Exception:
                    continue
                m = o.get("meta") or {}
                n = len((o.get("text") or "").split())
                rec(m.get("source") or p.stem, (m.get("license") or "").strip(),
                    (m.get("language_variant") or "").strip().upper(), m.get("domain") or "", n)
    else:
        import pyarrow.parquet as pq
        t = pq.read_table(p, columns=["text", "source", "license", "language_variant", "domain"])
        cols = [t.column(c).to_pylist() for c in ["text", "source", "license", "language_variant", "domain"]]
        for text, src, lic, var, dom in zip(*cols):
            rec(src or "", (lic or "").strip(), (var or "").strip().upper(), dom or "", len((text or "").split()))
    out.write(json.dumps(res) + "\n"); out.flush(); os.fsync(out.fileno())
    print(p.name, res["total"], flush=True)
out.close()
print("DONE", flush=True)
