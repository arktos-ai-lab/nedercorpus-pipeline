"""
aggregate_results.py — Collect euroeval_results/{condition}/summary.txt files
(written by run_euroeval.sh) into one comparison table across all trained
conditions in the license-tier / data-abundance ablation.

EuroEval's exact stdout format varies by version, so this parses defensively:
any line matching "<label>: <number>" or "<label> = <number>" is captured as
a (condition, task_or_label) -> value pair. Lines that don't parse are kept
verbatim in an appendix section so nothing is silently dropped.

Usage:
  python aggregate_results.py --results_dir euroeval_results --out results_tiers.md
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

METRIC_RE = re.compile(r"^\s*([A-Za-z0-9_\-\.]+)\s*[:=]\s*(-?\d+\.?\d*)\s*$")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--results_dir", default="euroeval_results", type=Path)
    p.add_argument("--out",         default="results_tiers.md", type=Path)
    return p.parse_args()


def main():
    args = parse_args()

    if not args.results_dir.exists():
        raise SystemExit(f"ERROR: {args.results_dir} not found. "
                          f"Run run_all_tiers.sh first.")

    conditions = sorted(d.name for d in args.results_dir.iterdir() if d.is_dir())
    if not conditions:
        raise SystemExit(f"ERROR: no condition subdirectories under {args.results_dir}")

    metrics: dict[str, dict[str, str]] = defaultdict(dict)  # condition -> label -> value
    unparsed: dict[str, list[str]] = defaultdict(list)
    all_labels: set[str] = set()

    for cond in conditions:
        summary = args.results_dir / cond / "summary.txt"
        if not summary.exists():
            unparsed[cond].append("(no summary.txt found)")
            continue
        for line in summary.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            m = METRIC_RE.match(line)
            if m:
                label, value = m.group(1), m.group(2)
                metrics[cond][label] = value
                all_labels.add(label)
            else:
                unparsed[cond].append(line)

    lines = [
        "## License-tier / data-abundance ablation — EuroEval results",
        "",
        f"Conditions evaluated: {', '.join(conditions)}",
        "",
        "Tier1 = CC0+PD only. Tier2 = +CC-BY. Tier3 = +CC-BY-SA (full corpus license policy). "
        "`_nl` = NL/NL-BE only. `_all` = all 12 varieties. `gptnl_1b`/`gptnl_8b` = "
        "token-budget-matched splits at GPT-NL's reported Dutch token exposure "
        "(all licenses, all varieties).",
        "",
    ]

    if all_labels:
        sorted_labels = sorted(all_labels)
        lines.append("| Condition | " + " | ".join(sorted_labels) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(sorted_labels)) + "|")
        for cond in conditions:
            row = [metrics[cond].get(l, "—") for l in sorted_labels]
            lines.append(f"| {cond} | " + " | ".join(row) + " |")
        lines.append("")
    else:
        lines.append("No metrics could be auto-parsed from summary.txt files — "
                      "see raw output below per condition.")
        lines.append("")

    any_unparsed = any(unparsed.values())
    if any_unparsed:
        lines.append("### Unparsed lines (verify manually — parser may have missed a format)")
        lines.append("")
        for cond in conditions:
            if unparsed[cond]:
                lines.append(f"**{cond}**")
                lines.append("```")
                lines.extend(unparsed[cond][:40])
                lines.append("```")
        lines.append("")

    lines.append(
        "**Reading this table:** Tier2/Tier3 deltas over Tier1 isolate the cost of "
        "restricting to fully-permissive licenses (no ShareAlike). `_all` vs `_nl` deltas "
        "isolate the cost/benefit of multi-variety training on Standard-Dutch task "
        "performance specifically (EuroEval Dutch tasks are NL-only benchmarks, so this "
        "measures whether variety inclusion *costs* anything on the dominant-language "
        "eval, not whether it helps minority varieties — that claim is made by the "
        "separate perplexity experiment, not this one)."
    )

    out_text = "\n".join(lines)
    args.out.write_text(out_text, encoding="utf-8")
    print(f"Written -> {args.out}")
    print()
    print(out_text)


if __name__ == "__main__":
    main()
