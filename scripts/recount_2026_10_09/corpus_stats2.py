"""Second resumable pass over normalized JSONL: variant x license counts, PII placeholder
counts, publication-year <=1914 counts, synthetic flag. One checkpoint line per file."""
import os, sys, json, re
from pathlib import Path
import orjson

ckpt = Path(sys.argv[1])
done = set()
if ckpt.exists():
    for line in ckpt.read_text(encoding="utf-8").splitlines():
        if line.strip():
            done.add(json.loads(line)["file"])
PH = re.compile(r"\[(BSN|IBAN|EMAIL|TELEFOON|CREDITCARD|REISDOCUMENT|ID-NUMMER|NAAM|PERSOON|ADRES)\]")
out = open(ckpt, "a", encoding="utf-8")
for p in sorted(Path("F:/NederCorpus/data/normalized").glob("*.jsonl")):
    if p.name in done:
        continue
    res = {"file": p.name, "var_lic": {}, "var_lic_tok": {}, "ph": {}, "ph_docs": 0,
           "hist_le1914": 0, "synthetic": 0, "sources": {}, "var_src": {}}
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
            text = o.get("text") or ""
            k = f"{(m.get('language_variant') or '').strip().upper()}|{(m.get('license') or '').strip()}"
            res["var_lic"][k] = res["var_lic"].get(k, 0) + 1
            res["var_lic_tok"][k] = res["var_lic_tok"].get(k, 0) + len(text.split())
            hits = PH.findall(text)
            if hits:
                res["ph_docs"] += 1
                for h in hits:
                    res["ph"][h] = res["ph"].get(h, 0) + 1
            y = m.get("publication_year")
            if isinstance(y, int) and y <= 1914:
                res["hist_le1914"] += 1
            if m.get("synthetic"):
                res["synthetic"] += 1
            s = m.get("source") or p.stem
            res["sources"][s] = res["sources"].get(s, 0) + 1
            vs = f"{(m.get('language_variant') or '').strip().upper()}|{s}"
            res["var_src"][vs] = res["var_src"].get(vs, 0) + 1
    out.write(json.dumps(res) + "\n"); out.flush(); os.fsync(out.fileno())
    print(p.name, res["ph_docs"], flush=True)
out.close()
print("DONE", flush=True)
