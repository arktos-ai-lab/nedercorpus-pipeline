"""
historical_ner_density.py — Measure the NER-signal utility of NederCorpus's
1915 historical-name-redaction cutoff (pipeline/pii/scrubber.py,
_HISTORICAL_CUTOFF_YEAR = 1915) and extract the pre-1915 slice as a
standalone "pruned" subcorpus.

Rationale for the cutoff itself (not new — already implemented; this script
just measures and documents it): Elizabeth Philippina "Bets" van
Boven-Smaal, born 5 January 1915, is the Netherlands' documented oldest
living person (verified via GRG/LongeviQuest, July 2025). No document dated
1914 or earlier can name a still-living person under any actuarially
plausible reading of that record — so redacting names in such documents
serves no GDPR purpose (GDPR only protects living natural persons) while
destroying real NER training signal for no benefit. Documents from 1915
onward are conservatively still redacted, since someone born in 1915 could
in principle still be alive.

What this script does:
  1. Scans data/deduped/*.jsonl (final corpus population, pre-PII-redaction
     text) for every document with publication_year <= 1914, and writes them
     verbatim to --out_pruned as a standalone "historical NER-safe subcorpus"
     — a distinct data product from the main (redacted) corpus, for
     downstream users who specifically want unredacted historical NER
     training data.
  2. Reservoir-samples a size-matched comparison set of modern (>=1915)
     documents from the same sources.
  3. Runs spaCy PERSON-entity detection (same model as the production
     pipeline, pipeline/pii/ner.py) on both samples and reports entities per
     1,000 whitespace tokens — the density of names that a live redaction
     pass would find (and that IS found, unredacted, in the historical
     slice; and that WOULD be redacted away in the modern slice were it
     published pre-1915 instead).

Note: this only counts PERSON entities detected by spaCy NER, not the
regex-layer identifiers (BSN/IBAN/etc.) — those are checksum-validated
structured numbers, not the kind of contextual entity signal this
experiment is about.

Usage:
    # spaCy + nl_core_news_lg on Python 3.14 fails to import in this repo's
    # main environment (confirmed: `ner.is_available()` returns False under
    # Python 3.14.2 here) — run this under the Python 3.11 venv instead:
    /f/NederCorpus/.venv_euroeval/Scripts/python.exe historical_ner_density.py \
        --deduped_dir ../../data/deduped \
        --out_pruned ../../experiment_data/pruned_historical_ner_subcorpus.jsonl \
        --sample_size 500
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HISTORICAL_CUTOFF_YEAR = 1915  # must match pipeline/pii/scrubber.py


def scan_and_split(deduped_dir: Path, seed: int):
    """Single pass: collect ALL historical (<=1914) docs verbatim, and
    reservoir-sample a comparison pool of modern (>=1915) docs from the
    same sources that contributed historical docs (domain-matched)."""
    rng = random.Random(seed)
    historical: list[dict] = []
    modern_reservoir: list[dict] = []
    modern_seen = 0
    historical_sources: set[str] = set()

    files = sorted(deduped_dir.glob("*.jsonl"))
    print(f"Pass 1: scanning {len(files)} files for publication_year <= {HISTORICAL_CUTOFF_YEAR - 1} ...",
          flush=True)
    for fi, fp in enumerate(files, 1):
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    meta = d.get("meta") or {}
                    y = meta.get("publication_year")
                    if y is None or len(d.get("text", "")) < 100:
                        continue
                    if y <= HISTORICAL_CUTOFF_YEAR - 1:
                        historical.append(d)
                        historical_sources.add(fp.stem)
        except OSError:
            continue
        if fi % 40 == 0:
            print(f"  [{fi}/{len(files)}] scanned, {len(historical):,} historical docs so far ...", flush=True)

    print(f"Pass 1 done: {len(historical):,} historical docs from sources: {sorted(historical_sources)}")

    print(f"\nPass 2: reservoir-sampling modern (>= {HISTORICAL_CUTOFF_YEAR}) comparison "
          f"docs from the same sources ...", flush=True)
    n_target = len(historical)
    for fp in files:
        if fp.stem not in historical_sources:
            continue
        with open(fp, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                meta = d.get("meta") or {}
                y = meta.get("publication_year")
                if y is None or y < HISTORICAL_CUTOFF_YEAR or len(d.get("text", "")) < 100:
                    continue
                modern_seen += 1
                if len(modern_reservoir) < n_target:
                    modern_reservoir.append(d)
                else:
                    j = rng.randint(0, modern_seen - 1)
                    if j < n_target:
                        modern_reservoir[j] = d

    print(f"Pass 2 done: sampled {len(modern_reservoir):,} of {modern_seen:,} modern docs seen.")
    return historical, modern_reservoir


def load_ner():
    """Reuse the exact production NER wrapper (pipeline/pii/ner.py) so this
    experiment measures the same model the pipeline would use, not a
    different one chosen for convenience."""
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    from pipeline.pii import ner
    if not ner.is_available():
        sys.exit("ERROR: spaCy Dutch model not available in this Python "
                 "environment. Run under .venv_euroeval (Python 3.11) — "
                 "spaCy fails to import under this repo's default Python 3.14.")
    print(f"Loaded NER model: {ner.get_model_name()}")
    return ner


def person_density(ner_module, docs: list[dict], sample_size: int, seed: int):
    rng = random.Random(seed)
    sample = docs if len(docs) <= sample_size else rng.sample(docs, sample_size)
    total_tokens = 0
    total_person_entities = 0
    docs_with_person = 0
    for d in sample:
        text = d.get("text", "")
        total_tokens += len(text.split())
        ents = ner_module.find_entities(text)
        n_person = sum(1 for label, *_ in ents if label in ("PERSON", "PER"))
        total_person_entities += n_person
        if n_person:
            docs_with_person += 1
    density = (total_person_entities / total_tokens * 1000) if total_tokens else 0.0
    return {
        "n_docs_sampled": len(sample),
        "total_tokens": total_tokens,
        "total_person_entities": total_person_entities,
        "person_entities_per_1000_tokens": density,
        "pct_docs_with_at_least_one_person": docs_with_person / len(sample) if sample else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deduped_dir", type=Path, default=Path("../../data/deduped"))
    ap.add_argument("--out_pruned", type=Path, default=Path("../../experiment_data/pruned_historical_ner_subcorpus.jsonl"))
    ap.add_argument("--sample_size", type=int, default=500,
                     help="Docs per group (historical/modern) to run NER on. "
                          "The pruned dataset itself always contains ALL historical docs, "
                          "regardless of this cap.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out_results", type=Path, default=Path("historical_ner_density_results.json"))
    args = ap.parse_args()

    historical, modern = scan_and_split(args.deduped_dir, args.seed)

    args.out_pruned.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_pruned, "w", encoding="utf-8") as out_fh:
        for d in historical:
            out_fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"\nWrote pruned historical subcorpus: {args.out_pruned} ({len(historical):,} docs)")

    ner_module = load_ner()
    print(f"\nRunning NER on up to {args.sample_size} docs per group ...")
    hist_stats = person_density(ner_module, historical, args.sample_size, args.seed)
    modern_stats = person_density(ner_module, modern, args.sample_size, args.seed)

    results = {
        "historical_cutoff_year": HISTORICAL_CUTOFF_YEAR,
        "pruned_dataset_path": str(args.out_pruned),
        "pruned_dataset_total_docs": len(historical),
        "historical_group": hist_stats,
        "modern_group": modern_stats,
        "density_ratio_historical_over_modern": (
            hist_stats["person_entities_per_1000_tokens"] / modern_stats["person_entities_per_1000_tokens"]
            if modern_stats["person_entities_per_1000_tokens"] else None
        ),
    }
    args.out_results.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== Results ===")
    print(f"Historical (<= {HISTORICAL_CUTOFF_YEAR - 1}): {hist_stats}")
    print(f"Modern     (>= {HISTORICAL_CUTOFF_YEAR}):    {modern_stats}")
    print(f"\nWrote {args.out_results}")


if __name__ == "__main__":
    main()
