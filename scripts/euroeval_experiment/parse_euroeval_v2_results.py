"""
parse_euroeval_v2_results.py: collect EuroEval Dutch scores for the v2
(shuffled, C5-relabelled) NL licence-tier rerun and emit paste-ready LaTeX.

Inputs (per condition, written by run_vast_eval_v2.py):
  experiment_results/euroeval_v2/_raw/<cond>/euroeval_benchmark_results.jsonl
      primary source: one JSON record per benchmark (EuroEval 17.7.0 schema,
      scores under evaluation_results[].score_details.score)
  experiment_results/euroeval_v2/<cond>.log
      fallback and cross-check: the "Results:\n{...}" dicts in the log

Outputs (experiment_results/euroeval_v2/):
  parsed_results.json   all metrics per condition and dataset, plus source
  results_table.md      one markdown table per dataset
  latex_rows.tex        rows for tab:euroeval and tab:euroevalapp

A condition whose log lacks "Completed 9 benchmarks" or whose results are
incomplete is reported as missing; its LaTeX row is a placeholder. Reruns are
cheap (CPU only, seconds), so the script simply recomputes everything.

Usage:  python scripts/euroeval_experiment/parse_euroeval_v2_results.py
"""
import json
import math
import re
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[2] / "experiment_results" / "euroeval_v2"
CONDITIONS = ["v2_tier1_nl", "v2_tier2_nl", "v2_tier3_nl"]
N_BENCHMARKS = 9

# dataset id in the JSONL -> display name used in the logs and the v1 table
DATASET_NAMES = {
    "dbrd": "DBRD",
    "scala-nl": "ScaLA-nl",
    "conll-nl": "CoNLL-nl",
    "squad-nl": "SQuAD-nl",
    "wiki-lingua-nl": "WikiLingua-nl",
    "mmlu-nl": "MMLU-nl",
    "hellaswag-nl": "HellaSwag-nl",
    "duidelijke-taal": "Duidelijke Taal",
    "zebra-puzzles-easy-nl": "ZebraPuzzlesEasy-nl",
}

# (dataset, metric) columns of the two paper tables, in paper order
MAIN_COLS = [("DBRD", "test_macro_f1", 1), ("DBRD", "test_mcc", 1), ("SQuAD-nl", "test_f1", 2)]
APP_COLS = [
    ("HellaSwag-nl", "test_accuracy", 1),
    ("MMLU-nl", "test_accuracy", 1),
    ("ScaLA-nl", "test_macro_f1", 1),
    ("CoNLL-nl", "test_micro_f1", 1),
    ("ZebraPuzzlesEasy-nl", "test_cell_wise_accuracy", 1),
    ("WikiLingua-nl", "test_chr_f3pp", 1),
    ("Duidelijke Taal", "test_sari", 1),
]


def canon_name(raw: str) -> str:
    """Map a JSONL dataset id or a log dataset phrase to the display name."""
    if raw in DATASET_NAMES:
        return DATASET_NAMES[raw]
    for name in DATASET_NAMES.values():
        if raw.strip().endswith(name):
            return name
    return raw.strip()


def parse_jsonl(path: Path) -> dict:
    results = {}
    if not path.exists():
        return results
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        ds = rec["eval_library"]["additional_details"]["dataset"]
        metrics = {}
        for er in rec.get("evaluation_results", []):
            metrics[er["evaluation_name"]] = er["score_details"]["score"]
        results[canon_name(ds)] = metrics
    return results


LOG_PATTERN = re.compile(
    r"Finished benchmarking .*? dataset ([^\n]+?)\.\s*\n"
    r".*?Results:\n(\{.*?\})\n",
    re.DOTALL,
)


def parse_log(path: Path) -> tuple[dict, bool]:
    if not path.exists():
        return {}, False
    text = path.read_text(encoding="utf-8", errors="replace")
    complete = f"Completed {N_BENCHMARKS} benchmarks" in text
    results = {}
    for m in LOG_PATTERN.finditer(text):
        try:
            # EuroEval prints bare nan/inf for undefined standard errors; this
            # is our own log output, so a restricted eval is acceptable here.
            d = eval(m.group(2), {"__builtins__": {}, "nan": float("nan"), "inf": float("inf")})
        except (ValueError, SyntaxError, NameError):
            continue
        results[canon_name(m.group(1))] = d.get("total", {})
    return results, complete


def fmt(v, nd):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:.{nd}f}"


def latex_row(label: str, row: dict | None, cols) -> str:
    if row is None:
        cells = ["\\textit{TBD}"] * len(cols)
    else:
        cells = [fmt(row.get(ds, {}).get(metric), nd) for ds, metric, nd in cols]
    return f"{label} & " + " & ".join(cells) + " \\\\"


def main() -> int:
    parsed, problems = {}, []
    for cond in CONDITIONS:
        jl = parse_jsonl(RESULTS_DIR / "_raw" / cond / "euroeval_benchmark_results.jsonl")
        lg, complete = parse_log(RESULTS_DIR / f"{cond}.log")
        primary, source = (jl, "jsonl") if jl else (lg, "log")
        if not primary:
            print(f"[MISSING] {cond}: no results yet")
            problems.append(f"{cond}: missing")
            continue
        if not complete or len(primary) < N_BENCHMARKS:
            problems.append(f"{cond}: {len(primary)} datasets, log complete={complete}")
        # cross-check jsonl against the log where both exist
        for ds, metrics in jl.items():
            for k, v in metrics.items():
                lv = lg.get(ds, {}).get(k)
                if lv is not None and not (math.isnan(v) and math.isnan(lv)) and abs(lv - v) > 1e-6:
                    problems.append(f"{cond} {ds} {k}: jsonl {v} vs log {lv}")
        parsed[cond] = {"source": source, "log_complete": complete, "results": primary}
        print(f"{cond}: {len(primary)} datasets from {source}, log complete={complete}")

    (RESULTS_DIR / "parsed_results.json").write_text(
        json.dumps(parsed, indent=2, default=str), encoding="utf-8")

    datasets = sorted({ds for c in parsed.values() for ds in c["results"]})
    md = ["## EuroEval Dutch results, v2 NL tier rerun (shuffled splits, single seed)", ""]
    for ds in datasets:
        keys = sorted({k for c in parsed.values() for k in c["results"].get(ds, {})
                       if not k.endswith("_se") and "num_failed" not in k})
        md += [f"### {ds}", "", "| Condition | " + " | ".join(keys) + " |",
               "|---|" + "---:|" * len(keys)]
        for cond in CONDITIONS:
            row = parsed.get(cond, {}).get("results", {}).get(ds, {})
            md.append(f"| {cond} | " + " | ".join(fmt(row.get(k), 4) for k in keys) + " |")
        md.append("")
    (RESULTS_DIR / "results_table.md").write_text("\n".join(md), encoding="utf-8")

    tex = ["% tab:euroeval rows (DBRD macro-F1, DBRD MCC, SQuAD-nl F1)"]
    for cond in CONDITIONS:
        label = cond.replace("v2_", "").replace("_", "\\_") + " (v2)"
        r = parsed.get(cond, {}).get("results")
        tex.append(latex_row(label, r, MAIN_COLS))
    tex.append("% tab:euroevalapp rows (HSwag, MMLU, ScaLA, CoNLL, Zebra, WikiL., DTaal)")
    for cond in CONDITIONS:
        label = cond.replace("v2_", "").replace("_", "\\_") + " (v2)"
        r = parsed.get(cond, {}).get("results")
        tex.append(latex_row(label, r, APP_COLS))
    (RESULTS_DIR / "latex_rows.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")
    print("\n".join(tex))

    summary = foldin_summary(parsed)
    (RESULTS_DIR / "foldin_numbers.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary)

    if problems:
        print("\nPROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    return 0


V1_PARSED = RESULTS_DIR.parent / "euroeval_vast" / "parsed_results.json"
IDENTICAL_DATA_F1_GAP = 18.6   # v1 tier2_nl vs tier3_nl, identical training data


def foldin_summary(parsed: dict) -> str:
    """Numbers for the sentences of paper/euroeval_v2_foldin.md (v1 + v2 pooled)."""
    v1 = json.loads(V1_PARSED.read_text(encoding="utf-8"))["conditions"] if V1_PARSED.exists() else {}
    v2 = {c: parsed[c]["results"] for c in CONDITIONS if c in parsed}
    pooled = {**v1, **v2}
    n = len(pooled)

    def get(r, ds, m):
        return r.get(ds, {}).get(m)

    out = [f"conditions with results: {n} (v1 {len(v1)}, v2 {len(v2)}; paper needs 11)"]
    f1 = {c: get(r, "DBRD", "test_macro_f1") for c, r in v2.items()}
    mcc = {c: get(r, "DBRD", "test_mcc") for c, r in v2.items()}
    sq = {c: get(r, "SQuAD-nl", "test_f1") for c, r in v2.items()}
    out.append("v2 DBRD F1:  " + ", ".join(f"{c} {fmt(v, 1)}" for c, v in f1.items()))
    out.append("v2 DBRD MCC: " + ", ".join(f"{c} {fmt(v, 1)}" for c, v in mcc.items()))
    out.append("v2 SQuAD F1: " + ", ".join(f"{c} {fmt(v, 2)}" for c, v in sq.items()))
    vals = [v for v in f1.values() if v is not None]
    if len(vals) > 1:
        spread = max(vals) - min(vals)
        out.append(f"v2 DBRD F1 spread {spread:.1f} vs identical-data gap {IDENTICAL_DATA_F1_GAP}: "
                   + ("KEEP 'within the variation' sentence" if spread < IDENTICAL_DATA_F1_GAP
                      else "spread exceeds the gap: rewrite, do not call it run-to-run variation"))
    tier_mcc = [get(r, "DBRD", "test_mcc") for c, r in pooled.items() if "gptnl" not in c]
    tier_mcc = [v for v in tier_mcc if v is not None]
    if tier_mcc:
        out.append(f"max tier DBRD MCC (v1+v2): {max(tier_mcc):.1f}  (gptnl_1b 31.5)")
    tier_sq = [get(r, "SQuAD-nl", "test_f1") for c, r in pooled.items() if "gptnl" not in c]
    tier_sq = [v for v in tier_sq if v is not None]
    if tier_sq:
        out.append(f"max tier SQuAD-nl F1 (v1+v2): {max(tier_sq):.2f}  (gptnl_1b 3.36)")
    acc = [get(r, ds, "test_accuracy") for r in pooled.values() for ds in ("HellaSwag-nl", "MMLU-nl")]
    acc = [v for v in acc if v is not None]
    if acc:
        out.append(f"HellaSwag/MMLU accuracy range: {min(acc):.1f} to {max(acc):.1f}")
    def is_val(r, ds, m, target, nd):
        v = get(r, ds, m)
        return v is not None and round(v, nd) == target

    scala = sum(1 for r in pooled.values()
                if is_val(r, "ScaLA-nl", "test_macro_f1", 33.6, 1) and is_val(r, "ScaLA-nl", "test_mcc", 0.0, 6))
    conll = sum(1 for r in pooled.values() if is_val(r, "CoNLL-nl", "test_micro_f1", 0.0, 6))
    zebra = sum(1 for r in pooled.values() if is_val(r, "ZebraPuzzlesEasy-nl", "test_cell_wise_accuracy", 0.0, 6))
    out.append(f"ScaLA majority class (33.6, MCC 0): {scala} of {n}")
    out.append(f"CoNLL-nl 0.0 F1: {conll} of {n}")
    out.append(f"Zebra 0.0: {zebra} of {n}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
