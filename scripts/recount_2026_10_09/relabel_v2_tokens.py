"""Licence-tier totals of the v2 release (2026-10-15 relabel), from the per-source counts.

The 2026-10-15 rebuild relabelled jrc and vlpar from PD to CC-BY (vlaamse_codex was already
CC-BY per document). The text is unchanged, so the tier totals follow from the per-source
counts in ckpt_normalized_v2.jsonl without a rescan. Doc counts must equal
F:/NederCorpus/release_build/release_stats.json; the script stops if they do not.

    python relabel_v2_tokens.py   # writes stats_v2_relabel_summary.txt
"""
import json
from pathlib import Path

import agg

HERE = Path(__file__).resolve().parent
RELABEL = {"jrc": ("PD", "CC-BY"), "vlpar": ("PD", "CC-BY")}
STATS = Path("F:/NederCorpus/release_build/release_stats.json")

T, g = agg.agg(HERE / "ckpt_normalized_v2.jsonl")
lic = {k: dict(v) for k, v in g["by_license"].items()}
for src, (old, new) in RELABEL.items():
    v = g["by_src_lic"][f"{src}|{old}"]
    for f in ("docs", "tok"):
        lic[old][f] -= v[f]
        lic[new][f] += v[f]
# The paper's CC-BY row includes the 14 CC BY 3.0 documents.
by3 = lic.pop("CC-BY-3.0")
lic["CC-BY"] = {f: lic["CC-BY"][f] + by3[f] for f in ("docs", "tok")}

rel = json.loads(STATS.read_text(encoding="utf-8"))["docs_by_license"]
rel["CC-BY"] += rel.pop("CC-BY-3.0", 0)
assert {k: v["docs"] for k, v in lic.items()} == rel, (lic, rel)

lines = [f"TOTAL docs {T['docs']} tok {T['tok']}"]
for k in ("PD", "CC0", "CC-BY", "CC-BY-SA"):
    v = lic[k]
    lines.append(f"{k} docs={v['docs']} {100 * v['docs'] / T['docs']:.1f}% "
                 f"tok={v['tok'] / 1e9:.3f}B {100 * v['tok'] / T['tok']:.1f}%")
nsa = [lic[k] for k in ("PD", "CC0", "CC-BY")]
lines.append(f"nonSA docs {100 * sum(v['docs'] for v in nsa) / T['docs']:.1f}% "
             f"tok {100 * sum(v['tok'] for v in nsa) / T['tok']:.1f}%")
lines.append("doc counts match release_stats.json (CC-BY incl. 14 CC-BY-3.0)")
(HERE / "stats_v2_relabel_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
