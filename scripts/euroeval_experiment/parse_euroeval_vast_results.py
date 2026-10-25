"""
parse_euroeval_vast_results.py — Extract EuroEval Dutch benchmark scores
from the 8 raw condition logs downloaded from the vast.ai eval run
(experiment_results/euroeval_vast/*.log) into a clean comparison table.

Each log contains DEBUG lines like:
    [DEBUG] ... Results:
    {'raw': [...], 'total': {'test_<metric>': <value>, ...}}
immediately after "Finished benchmarking ... on the <dataset name>."
This pulls the 'total' dict for each dataset out of each condition's log.
"""
import ast
import json
import re
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[2] / "experiment_results" / "euroeval_vast"
CONDITIONS = ["tier1_nl", "tier1_all", "tier2_nl", "tier2_all",
              "tier3_nl", "tier3_all", "gptnl_1b", "gptnl_8b"]


def parse_log(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    # Find each "Finished benchmarking ... on the ... dataset <NAME>." followed
    # eventually by "Results:\n{...}"
    pattern = re.compile(
        r"Finished benchmarking .*? dataset ([^\n.]+)\.\s*"
        r"(?:- [^\n]+\n)*"
        r".*?Results:\n(\{.*?\})\n",
        re.DOTALL,
    )
    results = {}
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        blob = m.group(2)
        try:
            # ast.literal_eval rejects the bare `nan` tokens EuroEval writes
            # for undefined standard errors (e.g. single-iteration runs) —
            # it only accepts literals, not names. That silently dropped
            # every single match via the bare except below. eval() with a
            # namespace supplying nan/inf handles it; safe here since this
            # is our own trusted log output, not external input.
            d = eval(blob, {"nan": float("nan"), "inf": float("inf")})
        except (ValueError, SyntaxError, NameError):
            continue
        total = d.get("total", {})
        results[name] = total
    return results


def main():
    all_results = {}
    datasets = set()
    for cond in CONDITIONS:
        path = RESULTS_DIR / f"{cond}.log"
        if not path.exists():
            print(f"[SKIP] {path} not found")
            continue
        r = parse_log(path)
        all_results[cond] = r
        datasets.update(r.keys())
        print(f"{cond}: {len(r)} datasets parsed -> {sorted(r.keys())}")

    out = {"conditions": all_results}
    out_path = RESULTS_DIR / "parsed_results.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")

    # Build a simple markdown table: primary metric per dataset per condition
    lines = ["## EuroEval Dutch benchmark results (vast.ai, real GPU, single seed)", ""]
    for ds in sorted(datasets):
        lines.append(f"### {ds}")
        lines.append("")
        # collect all metric keys seen for this dataset across conditions
        metric_keys = set()
        for cond in CONDITIONS:
            metric_keys.update(all_results.get(cond, {}).get(ds, {}).keys())
        metric_keys = sorted(k for k in metric_keys if not k.endswith("_se") and "num_failed" not in k)
        header = "| Condition | " + " | ".join(metric_keys) + " |"
        sep = "|---|" + "".join(["---:|"] * len(metric_keys))
        lines.append(header)
        lines.append(sep)
        for cond in CONDITIONS:
            row = all_results.get(cond, {}).get(ds, {})
            cells = []
            for k in metric_keys:
                v = row.get(k)
                cells.append(f"{v:.4f}" if isinstance(v, float) else str(v) if v is not None else "n/a")
            lines.append(f"| {cond} | " + " | ".join(cells) + " |")
        lines.append("")

    md_path = RESULTS_DIR / "results_table.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {md_path}")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
