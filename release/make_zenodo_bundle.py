#!/usr/bin/env python3
"""
make_zenodo_bundle.py: assemble the NederCorpus Zenodo snapshot (no corpus text).

    python release/make_zenodo_bundle.py

Writes F:/NederCorpus/release_build/zenodo/ with documentation, provenance tables, release
statistics, the SHA-256 list of every Hugging Face Parquet shard, the redaction-log chain
heads, and a zip of the pipeline code. Each file is copied to a temp name, fsynced, renamed,
and its sha256 appended (flushed) to zenodo/SHA256SUMS, so a rerun skips finished files.
Source files are only read.
"""
import hashlib
import os
import shutil
import zipfile
from pathlib import Path

PAPER = Path(__file__).resolve().parent.parent
BUILD = Path("F:/NederCorpus/release_build")
OUT = BUILD / "zenodo"

FILES = {  # bundle name -> source path
    "README.md": PAPER / "release" / "README.md",
    "DATASHEET.md": PAPER / "DATASHEET.md",
    "LEGAL_BASIS.md": PAPER / "LEGAL_BASIS.md",
    "LICENSE": PAPER / "LICENSE-CORPUS",
    "license_breakdown.csv": PAPER / "license_breakdown.csv",
    "license_matrix.csv": PAPER / "sources" / "license_matrix.csv",
    "release_stats.json": BUILD / "release_stats.json",
    "parquet_SHA256SUMS.txt": BUILD / "SHA256SUMS",
    "redaction_log_chain_heads.json": PAPER / "release" / "redaction_log_chain_heads.json",
    "c5_relabel_summary.json": PAPER / "scripts" / "recount_2026_10_09" / "relabel_c5_summary.json",
}
CODE_DIRS = ["pipeline", "scripts", "tests", "release"]
CODE_FILES = ["pyproject.toml"]
CODE_EXT = {".py", ".sh", ".ps1", ".md", ".toml", ".txt", ".cfg", ".ini", ".yaml", ".yml"}
CODE_MAX = 2_000_000


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 22), b""):
            h.update(c)
    return h.hexdigest()


def done_names():
    """name -> recorded sha256, for files present in the bundle."""
    s = OUT / "SHA256SUMS"
    if not s.exists():
        return {}
    out = {}
    for l in s.read_text(encoding="utf-8").splitlines():
        if "  " in l:
            h, n = l.split("  ", 1)
            if (OUT / n.strip()).exists():
                out[n.strip()] = h
    return out


def rewrite_sums():
    """Final SHA256SUMS: one line per bundle file, recomputed (drops stale/duplicate lines)."""
    tmp = OUT / "SHA256SUMS.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for p in sorted(OUT.iterdir()):
            if (p.is_file() and p.name not in ("SHA256SUMS", "SHA256SUMS.tmp")
                    and not p.name.startswith(".") and not p.name.endswith(".tmp")):
                fh.write(f"{sha256(p)}  {p.name}\n")
    os.replace(tmp, OUT / "SHA256SUMS")


def record(name: str):
    with open(OUT / "SHA256SUMS", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{sha256(OUT / name)}  {name}\n")
        fh.flush()
        os.fsync(fh.fileno())


def place(tmp: Path, name: str):
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, OUT / name)
    record(name)
    print("added", name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    done = done_names()
    for name, src in FILES.items():
        if not src.exists():
            print("MISSING (skipped):", src)
            continue
        if done.get(name) == sha256(src):
            continue  # unchanged since last bundle
        tmp = OUT / (name + ".tmp")
        shutil.copyfile(src, tmp)
        place(tmp, name)
    name = "nedercorpus_code.zip"
    if name not in done:
        tmp = OUT / (name + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for d in CODE_DIRS:
                for f in sorted((PAPER / d).rglob("*")):
                    if (f.is_file() and f.suffix in CODE_EXT and "__pycache__" not in f.parts
                            and f.name != ".env" and f.stat().st_size <= CODE_MAX
                            and not (f.suffix == ".txt" and not f.name.startswith("requirements"))
                            and not ({"euroeval_results", "dda_results", "provenance"} & set(f.parts))):
                        z.write(f, f.relative_to(PAPER).as_posix())
            for f in CODE_FILES:
                if (PAPER / f).exists():
                    z.write(PAPER / f, f)
        place(tmp, name)

    rewrite_sums()


if __name__ == "__main__":
    main()
