"""Build shuffled NL license-tier splits from the relabelled build (2026-10-10 rerun).

Differences from prepare_tier_splits.py (the 2026-07 splits):
  * documents are sampled AND written in a seeded random order, so a training run that
    stops at its token budget sees a random sample, not an alphabetical source prefix;
  * each split is capped at TARGET_WS whitespace tokens (enough to fill the 1.1B GPT-2
    token training buffer of train_tier.py), instead of writing every eligible doc;
  * NL scope only (NL + NL-BE), three cumulative tiers:
      tier1_nl = PD + CC0, tier2_nl = + CC-BY, tier3_nl = + CC-BY-SA.

Resumable (repo CLAUDE.md): pass 1 writes one index file per input file (skipped if
present); pass 2 writes each split via 64 seeded shuffle buckets to <split>.jsonl.tmp
and renames it when complete; finished splits are skipped.
"""
import json, os, random, sys
from pathlib import Path
import numpy as np

NORM_DIR = Path("F:/NederCorpus/data/normalized")
OUT_DIR = Path("F:/NederCorpus/scripts/euroeval_experiment/tier_splits_v2")
IDX_DIR = OUT_DIR / "_index"
SEED = 42
TARGET_WS = 1_500_000_000
N_BUCKETS = 64
TIER_OF = {"PD": 1, "CC0": 1, "CC-BY": 2, "CC-BY-3.0": 2, "CC-BY-SA": 3}
SPLITS = {"tier1_nl": 1, "tier2_nl": 2, "tier3_nl": 3}

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def index_files(files):
    IDX_DIR.mkdir(parents=True, exist_ok=True)
    for fi, path in enumerate(files):
        dest = IDX_DIR / f"{path.stem}.npz"
        if dest.exists():
            continue
        lines, toks, tiers = [], [], []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for li, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = o.get("meta") or {}
                lang = (m.get("language_variant") or "").strip().upper()
                tier = TIER_OF.get((m.get("license") or "").strip(), 0)
                n = len((o.get("text") or "").split())
                if lang in ("NL", "NL-BE") and tier and n >= 10:
                    lines.append(li); toks.append(n); tiers.append(tier)
        tmp = dest.with_name(dest.stem + ".tmp.npz")
        np.savez(tmp, line=np.array(lines, dtype=np.int64), tok=np.array(toks, dtype=np.int64),
                 tier=np.array(tiers, dtype=np.int8))
        os.replace(tmp, dest)
        print(f"  indexed [{fi+1}/{len(files)}] {path.name}: {len(lines):,} NL docs", flush=True)


def build_split(name, max_tier, files):
    out = OUT_DIR / f"{name}.jsonl"
    meta_path = OUT_DIR / f"{name}.meta.json"
    if out.exists() and meta_path.exists():
        print(f"  {name}: exists, skipping"); return
    # eligible (file_idx, line, tok)
    elig = []
    for fi, path in enumerate(files):
        z = np.load(IDX_DIR / f"{path.stem}.npz")
        sel = z["tier"] <= max_tier
        for li, n in zip(z["line"][sel], z["tok"][sel]):
            elig.append((fi, int(li), int(n)))
    rng = random.Random(f"{SEED}-{name}")
    rng.shuffle(elig)
    chosen, acc = [], 0
    for e in elig:
        chosen.append(e); acc += e[2]
        if acc >= TARGET_WS:
            break
    print(f"  {name}: eligible {len(elig):,} docs, chose {len(chosen):,} docs / {acc/1e9:.2f}B ws tokens", flush=True)
    bucket_of = {}
    for i, (fi, li, _) in enumerate(chosen):
        bucket_of[(fi, li)] = rng.randrange(N_BUCKETS)
    bdir = OUT_DIR / f"_buckets_{name}"
    bdir.mkdir(exist_ok=True)
    bfh = [open(bdir / f"b{b:02d}.jsonl", "w", encoding="utf-8") for b in range(N_BUCKETS)]
    by_file = {}
    for (fi, li), b in bucket_of.items():
        by_file.setdefault(fi, {})[li] = b
    lic_count, src_count = {}, {}
    for fi in sorted(by_file):
        wanted = by_file[fi]
        with open(files[fi], encoding="utf-8", errors="replace") as fh:
            for li, line in enumerate(fh):
                b = wanted.get(li)
                if b is not None:
                    bfh[b].write(line if line.endswith("\n") else line + "\n")
    for f in bfh:
        f.close()
    tmp = out.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fout:
        for b in range(N_BUCKETS):
            p = bdir / f"b{b:02d}.jsonl"
            # split on "\n" only: str.splitlines() also splits on U+2028/U+0085,
            # which json.dumps(ensure_ascii=False) leaves raw inside strings
            lines = [x + "\n" for x in p.read_text(encoding="utf-8").split("\n") if x]
            rng.shuffle(lines)
            for line in lines:
                m = json.loads(line).get("meta") or {}
                lic_count[m.get("license")] = lic_count.get(m.get("license"), 0) + 1
                src_count[m.get("source")] = src_count.get(m.get("source"), 0) + 1
                fout.write(line)
        fout.flush(); os.fsync(fout.fileno())
    os.replace(tmp, out)
    for b in range(N_BUCKETS):
        (bdir / f"b{b:02d}.jsonl").unlink()
    bdir.rmdir()
    top = sorted(src_count.items(), key=lambda x: -x[1])[:10]
    meta = {"split": name, "max_tier": max_tier, "seed": SEED, "target_ws_tokens": TARGET_WS,
            "eligible_docs": len(elig), "docs": len(chosen), "ws_tokens": acc,
            "licenses": lic_count, "top_sources": top}
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"  {name}: written {out} {json.dumps(meta)[:400]}", flush=True)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(NORM_DIR.glob("*.jsonl"))
    print(f"Pass 1: indexing {len(files)} files", flush=True)
    index_files(files)
    print("Pass 2: building splits", flush=True)
    for name, t in SPLITS.items():
        build_split(name, t, files)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
