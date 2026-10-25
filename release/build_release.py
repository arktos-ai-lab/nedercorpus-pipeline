#!/usr/bin/env python3
"""
build_release.py: re-shard the NederCorpus final export into the Hugging Face release layout.

    python release/build_release.py                    # plan + build (resumable)
    python release/build_release.py --finalize-only    # rebuild stats/checksums only

Input  : F:/NederCorpus/data/final/shard_*.parquet   (9,298,724 docs, C5-relabelled build,
         _EXPORT_DONE.json; read only)
Output : F:/NederCorpus/release_build/
           data/<licence>/<variety>/part-NNNNN.parquet   licence in {pd, cc0, cc-by, cc-by-sa}
           _plan/<input>.npz + _plan_progress.jsonl       phase A checkpoints (one per input)
           _units.json                                    output-shard plan (derived, atomic)
           _manifest.jsonl                                phase B checkpoints (one per output)
           SHA256SUMS, release_stats.json

Phase A reads each input shard once and records, per (licence, variety) partition, the row
indices and text byte sizes (one .npz per input, written atomically, then a flushed+fsynced
progress line). Phase B cuts each partition's ordered row stream into output shards of about
--target-text-bytes of raw UTF-8 text (about 350-450 MB of zstd Parquet for Dutch prose) and
writes each shard to a temp name, fsyncs, renames, hashes, then appends a flushed+fsynced
manifest line. A restart skips finished inputs and finished shards.

Columns are the export's columns minus `ledger` (empty in all 9,298,724 rows), plus
`license_spdx`, `lang_iso639_3` and `license_evidence_url` (from sources/license_matrix.csv
where the source has a row). Nothing is filtered out: the export already excludes the C5
NC/ND/unknown pages and `legal_synth`. CPU only.
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
PAPER = HERE.parent
DEFAULT_IN = Path("F:/NederCorpus/data/final")
DEFAULT_OUT = Path("F:/NederCorpus/release_build")
EXPECTED_DOCS = 9_298_724

LIC_DIR = {"PD": "pd", "CC0": "cc0", "CC-BY": "cc-by", "CC-BY-3.0": "cc-by", "CC-BY-SA": "cc-by-sa"}
# The build labels CC-BY / CC-BY-SA without a version; we do not invent one.
LIC_SPDX = {"PD": "LicenseRef-PublicDomain", "CC0": "CC0-1.0", "CC-BY-3.0": "CC-BY-3.0",
            "CC-BY": "LicenseRef-CC-BY-unversioned", "CC-BY-SA": "LicenseRef-CC-BY-SA-unversioned"}
ISO = {"NL": "nld", "NL-BE": "nld", "AF": "afr", "FY": "fry", "fy-NL": "fry", "LI": "lim",
       "NDS-NL": "nds", "VLS": "vls", "ZEA": "zea", "PAP": "pap", "SRN": "srn", "FRR": "frr",
       "STQ": "stq"}
DROP_COLS = {"ledger"}
# Source-level licence corrections applied at release time (checked against the sources'
# own licence statements on 2026-10-15; see sources/license_matrix.csv). The export in
# data/final still carries the older labels for these sources; the pipeline parsers now
# emit the corrected label.
SOURCE_LICENSE_OVERRIDE = {"jrc": "CC-BY", "vlpar": "CC-BY"}


def relabel(lic, src):
    """Return the licence labels with SOURCE_LICENSE_OVERRIDE applied (numpy or list in, same out)."""
    if isinstance(lic, np.ndarray):
        lic = lic.copy()
        for s, l in SOURCE_LICENSE_OVERRIDE.items():
            lic[src == s] = l
        return lic
    return [SOURCE_LICENSE_OVERRIDE.get(s, l) for l, s in zip(lic, src)]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def append_durable(path: Path, text: str):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path):
    out = []
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # torn last line after a crash; that unit is redone
    return out


# ---------------------------------------------------------------------------
# Phase A: per-input partition index
# ---------------------------------------------------------------------------
def plan_input(path: str, plan_dir: str):
    t = pq.read_table(path, columns=["license", "language_variant", "source", "text"])
    lic = relabel(t["license"].to_numpy(zero_copy_only=False), t["source"].to_numpy(zero_copy_only=False))
    var = t["language_variant"].to_numpy(zero_copy_only=False)
    nbytes = pc.binary_length(pc.cast(t["text"], pa.large_binary())).to_numpy(zero_copy_only=False)
    del t
    keys, arrays, summary = [], {}, {}
    for j, (l, v) in enumerate(sorted(set(zip(lic, var)))):
        if l not in LIC_DIR:
            raise ValueError(f"{path}: unexpected licence label {l!r}")
        idx = np.nonzero((lic == l) & (var == v))[0].astype(np.int64)
        key = f"{l}|{v}"
        keys.append(key)
        arrays[f"i{j}"] = idx
        arrays[f"b{j}"] = nbytes[idx].astype(np.int64)
        summary[key] = {"rows": int(idx.size), "text_bytes": int(arrays[f"b{j}"].sum())}
    name = Path(path).stem
    final = Path(plan_dir) / f"{name}.npz"
    tmp = Path(plan_dir) / f"{name}.tmp.npz"
    np.savez(tmp, keys=np.array(keys, dtype=object), **arrays)
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    return {"input": Path(path).name, "rows": int(lic.size), "parts": summary}


def load_plan(plan_dir: Path, name: str):
    z = np.load(plan_dir / f"{Path(name).stem}.npz", allow_pickle=True)
    keys = list(z["keys"])
    return {k: (z[f"i{j}"], z[f"b{j}"]) for j, k in enumerate(keys)}


# ---------------------------------------------------------------------------
# Output-shard plan (deterministic from phase A)
# ---------------------------------------------------------------------------
def make_units(inputs, plan_dir: Path, target: int):
    streams = defaultdict(list)  # key -> [(input, idx, bytes)]
    for name in inputs:
        for key, (idx, b) in load_plan(plan_dir, name).items():
            streams[(LIC_DIR[key.split("|")[0]], key.split("|")[1])].append((name, key, idx, b))
    units = []
    for (ldir, var) in sorted(streams):
        part, cur, k = defaultdict(list), 0, 0

        def emit():
            nonlocal part, cur, k
            units.append({"unit": f"{ldir}/{var}/{k:05d}", "path": f"data/{ldir}/{var}/part-{k:05d}.parquet",
                          "take": {n: sorted(set(int(i) for i in v)) for n, v in part.items()},
                          "text_bytes": cur})
            part, cur, k = defaultdict(list), 0, k + 1

        for name, key, idx, b in streams[(ldir, var)]:
            for i, nb in zip(idx.tolist(), b.tolist()):
                if part and cur + nb > target:
                    emit()
                part[name].append(i)
                cur += nb
        if part:
            emit()
    return units


# ---------------------------------------------------------------------------
# Phase B worker
# ---------------------------------------------------------------------------
def build_unit(unit, in_dir: str, out_dir: str, evidence: dict):
    in_dir, out_dir = Path(in_dir), Path(out_dir)
    final = out_dir / unit["path"]
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(final.name + ".tmp")
    writer, rows, counts = None, 0, Counter()
    for name in sorted(unit["take"]):
        pf = pq.ParquetFile(in_dir / name)
        cols = [c for c in pf.schema_arrow.names if c not in DROP_COLS]
        t = pf.read(columns=cols).take(pa.array(unit["take"][name], type=pa.int64()))
        src = t["source"].to_pylist()
        lic = relabel(t["license"].to_pylist(), src)
        if lic != t["license"].to_pylist():
            t = t.set_column(t.schema.get_field_index("license"), "license", pa.array(lic, pa.string()))
        var = t["language_variant"].to_pylist()
        t = t.append_column("license_spdx", pa.array([LIC_SPDX[x] for x in lic], pa.string()))
        t = t.append_column("lang_iso639_3", pa.array([ISO.get(v) for v in var], pa.string()))
        t = t.append_column("license_evidence_url", pa.array([evidence.get(s) for s in src], pa.string()))
        for s, l, v in zip(src, lic, var):
            counts[(s, l, v)] += 1
        if writer is None:
            writer = pq.ParquetWriter(tmp, t.schema, compression="zstd", compression_level=6)
        elif t.schema != writer.schema:
            t = t.cast(writer.schema)
        writer.write_table(t, row_group_size=5_000)
        rows += t.num_rows
        del t
    writer.close()
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    return {"unit": unit["unit"], "outputs": [{"path": unit["path"], "rows": rows,
            "bytes": final.stat().st_size, "sha256": _sha256(final)}],
            "counts": [[*k, v] for k, v in sorted(counts.items())],
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}


def load_evidence():
    ev = {}
    with open(PAPER / "sources" / "license_matrix.csv", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("url"):
                ev[r["source"]] = r["url"]
    return ev


def done_units(out: Path):
    done = {}
    for r in read_jsonl(out / "_manifest.jsonl"):
        if all((out / o["path"]).exists() and (out / o["path"]).stat().st_size == o["bytes"]
               for o in r["outputs"]):
            done[r["unit"]] = r
    return done


def finalize(out: Path, units, plan_rows: int):
    done = done_units(out)
    shards = [o for r in done.values() for o in r["outputs"]]
    agg = Counter()
    for r in done.values():
        for s, l, v, n in r["counts"]:
            agg[(s, l, v)] += n
    by_lic, by_var, by_src = Counter(), Counter(), Counter()
    for (s, l, v), n in agg.items():
        by_lic[l] += n
        by_var[v] += n
        by_src[s] += n
    with open(out / "SHA256SUMS.tmp", "w", encoding="utf-8", newline="\n") as fh:
        for o in sorted(shards, key=lambda o: o["path"]):
            fh.write(f"{o['sha256']}  {o['path']}\n")
    os.replace(out / "SHA256SUMS.tmp", out / "SHA256SUMS")
    stats = {"units_total": len(units), "units_done": len(done),
             "units_missing": [u["unit"] for u in units if u["unit"] not in done],
             "input_rows": plan_rows, "expected_docs_paper": EXPECTED_DOCS,
             "parquet_files": len(shards), "parquet_rows": sum(o["rows"] for o in shards),
             "parquet_bytes": sum(o["bytes"] for o in shards),
             "max_shard_bytes": max((o["bytes"] for o in shards), default=0),
             "docs_by_license": dict(by_lic), "docs_by_variety": dict(by_var),
             "docs_by_source": dict(sorted(by_src.items()))}
    (out / "release_stats.json.tmp").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    os.replace(out / "release_stats.json.tmp", out / "release_stats.json")
    print(json.dumps({k: v for k, v in stats.items() if k != "docs_by_source"}, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--target-text-bytes", type=int, default=1_250_000_000)
    ap.add_argument("--finalize-only", action="store_true")
    args = ap.parse_args()

    inputs = sorted(Path(p).name for p in glob.glob(str(args.input / "shard_*.parquet")))
    marker = json.loads((args.input / "_EXPORT_DONE.json").read_text())
    if marker.get("documents") != EXPECTED_DOCS:
        print(f"WARNING: export marker says {marker.get('documents')} docs, paper says {EXPECTED_DOCS}")
    plan_dir = args.out / "_plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    progress = args.out / "_plan_progress.jsonl"

    # Phase A
    planned = {r["input"]: r for r in read_jsonl(progress) if (plan_dir / f"{Path(r['input']).stem}.npz").exists()}
    todo = [n for n in inputs if n not in planned]
    print(f"phase A: {len(inputs)} inputs, {len(planned)} planned, {len(todo)} to scan", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(plan_input, str(args.input / n), str(plan_dir)): n for n in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            append_durable(progress, json.dumps(r) + "\n")
            planned[r["input"]] = r
            print(f"  A [{i}/{len(todo)}] {r['input']} {r['rows']} rows", flush=True)
    plan_rows = sum(planned[n]["rows"] for n in inputs)
    print(f"phase A done: {plan_rows} rows", flush=True)

    # Units
    units_path = args.out / "_units.json"
    if units_path.exists():
        saved = json.loads(units_path.read_text(encoding="utf-8"))
        if saved["target_text_bytes"] != args.target_text_bytes or saved["inputs"] != inputs:
            sys.exit("Existing _units.json was made with other inputs or target; use a fresh --out.")
        units = saved["units"]
    else:
        units = make_units(inputs, plan_dir, args.target_text_bytes)
        tmp = units_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"target_text_bytes": args.target_text_bytes, "inputs": inputs,
                                   "units": units}), encoding="utf-8")
        os.replace(tmp, units_path)
    print(f"{len(units)} output shards planned", flush=True)

    # Phase B
    if not args.finalize_only:
        evidence = load_evidence()
        done = done_units(args.out)
        todo = [u for u in units if u["unit"] not in done]
        # Big shards first so the long ones are not left for the end.
        todo.sort(key=lambda u: -u["text_bytes"])
        print(f"phase B: {len(done)} done, {len(todo)} to build", flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(build_unit, u, str(args.input), str(args.out), evidence): u for u in todo}
            for i, f in enumerate(as_completed(futs), 1):
                u = futs[f]
                try:
                    r = f.result()
                except Exception as e:
                    print(f"  B [{i}/{len(todo)}] FAILED {u['unit']}: {e!r}", flush=True)
                    continue
                append_durable(args.out / "SHA256SUMS.partial",
                               "".join(f"{o['sha256']}  {o['path']}\n" for o in r["outputs"]))
                append_durable(args.out / "_manifest.jsonl", json.dumps(r) + "\n")
                o = r["outputs"][0]
                print(f"  B [{i}/{len(todo)}] {u['unit']}: {o['rows']} rows, {o['bytes'] / 1e6:.0f} MB", flush=True)
    finalize(args.out, units, plan_rows)


if __name__ == "__main__":
    main()
