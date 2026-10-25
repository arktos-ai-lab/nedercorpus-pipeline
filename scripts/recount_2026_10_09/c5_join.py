"""Join NederCorpus c5_dutch docs (normalized build) to C5 per-page license metadata by URL."""
import json, glob, collections, sys
import orjson
lic = {}
for f in glob.glob(sys.argv[1] + "/*.jsonl"):
    for l in open(f, encoding="utf-8"):
        r = json.loads(l)
        lic[r["url"]] = (r.get("license_abbr"), bool(r.get("license_disagreement")), bool(r.get("license_parse_error")))
abbr = collections.Counter(); abbr_tok = collections.Counter(); dis = 0; unmatched = 0; n = 0
by_var = collections.defaultdict(collections.Counter)
for l in open("F:/NederCorpus/data/normalized/c5_dutch.jsonl", "rb"):
    o = orjson.loads(l); m = o["meta"]; n += 1
    r = lic.get(m.get("source_url"))
    if r is None:
        unmatched += 1; abbr["UNMATCHED"] += 1; continue
    abbr[r[0]] += 1; abbr_tok[r[0]] += len(o["text"].split()); by_var[m.get("language_variant")][r[0]] += 1
    dis += r[1]
print(json.dumps({"n": n, "lic_rows": len(lic), "unmatched": unmatched, "disagreement": dis,
                  "by_abbr": abbr, "tok_by_abbr": abbr_tok, "by_variant": by_var}, indent=1, default=dict))
