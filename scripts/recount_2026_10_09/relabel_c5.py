"""Relabel C5 documents in the normalized build from C5's own per-page `license_abbr`.

Author decision 2026-10-10: relabel, drop only NC/ND pages and pages that cannot be
matched to an admissible license; keep C5 Wikipedia pages (no extra dedup).

Inputs : data/normalized/c5_dutch.jsonl (build), c5_lic/*.jsonl (url -> license_abbr,
         fetched from BramVanroy/CommonCrawl-CreativeCommons by c5_licenses.py)
Outputs: data/normalized/c5_dutch.jsonl (replaced atomically), original backed up to
         data/backup_2026-10-10/normalized/c5_dutch.jsonl, summary JSON next to this script.
Resumable: the work unit is the whole file; the summary JSON marks completion, and the
backup copy is verified before the original is replaced.
"""
import collections, glob, json, os, shutil, sys
from pathlib import Path

ROOT = Path("F:/NederCorpus")
SRC = ROOT / "data/normalized/c5_dutch.jsonl"
BAK = ROOT / "data/backup_2026-10-10/normalized/c5_dutch.jsonl"
LIC_DIR = ROOT / "scripts/recount_2026_10_09/c5_lic"
SUMMARY = Path(__file__).with_name("relabel_c5_summary.json")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.acquire.c5_dutch import license_from_c5_abbr  # noqa: E402

if SUMMARY.exists():
    print("already done:", SUMMARY); sys.exit(0)

# 1. backup (verify size)
BAK.parent.mkdir(parents=True, exist_ok=True)
if not BAK.exists() or BAK.stat().st_size != SRC.stat().st_size:
    shutil.copy2(SRC, BAK)
assert BAK.stat().st_size == SRC.stat().st_size

# 2. url -> set of abbr values across all C5 dumps
abbrs = collections.defaultdict(set)
disagree = set()
for f in glob.glob(str(LIC_DIR / "*.jsonl")):
    for line in open(f, encoding="utf-8"):
        r = json.loads(line)
        abbrs[r["url"]].add(r.get("license_abbr"))
        if r.get("license_disagreement"):
            disagree.add(r["url"])

RANK = {"PD": 0, "CC0": 1, "CC-BY": 2, "CC-BY-SA": 3}   # most restrictive wins
stats = collections.Counter()
by_abbr = collections.Counter()
new_license = collections.Counter()
by_var_lic = collections.Counter()
tok_by_license = collections.Counter()
dropped_tok = 0
tmp = SRC.with_suffix(".jsonl.tmp")
SIDE = ROOT / "data/normalized_sidecar/c5_license_provenance.jsonl"
SIDE.parent.mkdir(parents=True, exist_ok=True)
with open(BAK, encoding="utf-8") as fin, open(tmp, "w", encoding="utf-8") as fout, \
        open(SIDE, "w", encoding="utf-8") as side:
    for line in fin:
        if not line.strip():
            continue
        o = json.loads(line)
        m = o["meta"]
        stats["in"] += 1
        vals = abbrs.get(m.get("source_url"))
        ntok = len(o["text"].split())
        if not vals:
            stats["drop_unmatched"] += 1; dropped_tok += ntok; continue
        if len(vals) > 1:
            stats["url_multiple_abbr"] += 1
        mapped = [license_from_c5_abbr(v) for v in vals]
        for v in vals:
            by_abbr[v] += 1 if len(vals) == 1 else 0
        if len(vals) > 1:
            by_abbr["|".join(sorted(map(str, vals)))] += 1
        if any(x is None for x in mapped):
            bad = [v for v, x in zip(vals, mapped) if x is None]
            if any(b and ("nc" in b or "nd" in b) for b in bad):
                stats["drop_nc_nd"] += 1
            else:
                stats["drop_unknown_license"] += 1      # cc-unknown, certification, missing
            dropped_tok += ntok
            continue
        lic = max(mapped, key=RANK.get)
        m["license"] = lic
        # Provenance goes to a sidecar: DocumentMeta rejects unknown keys, and
        # pipeline.document.read_jsonl silently skips such records.
        dis = m.get("source_url") in disagree
        side.write(json.dumps({"id": m["id"], "source_url": m.get("source_url"), "license": lic,
                               "c5_license_abbr": "|".join(sorted(map(str, vals))),
                               "c5_license_disagreement": dis}) + "\n")
        stats["kept"] += 1
        stats["kept_disagreement"] += int(dis)
        new_license[lic] += 1
        tok_by_license[lic] += ntok
        by_var_lic[f"{m.get('language_variant')}|{lic}"] += 1
        fout.write(json.dumps(o, ensure_ascii=False) + "\n")
    fout.flush(); os.fsync(fout.fileno())
os.replace(tmp, SRC)
summary = {"stats": stats, "by_abbr": by_abbr, "new_license_docs": new_license,
           "new_license_tokens": tok_by_license, "by_variant_license": by_var_lic,
           "dropped_tokens": dropped_tok, "backup": str(BAK)}
SUMMARY.write_text(json.dumps(summary, indent=1, default=dict), encoding="utf-8")
print(json.dumps(summary, indent=1, default=dict))
