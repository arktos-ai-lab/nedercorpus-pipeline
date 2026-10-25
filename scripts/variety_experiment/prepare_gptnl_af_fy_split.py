"""
prepare_gptnl_af_fy_split.py — Build a third perplexity-experiment training
split: GPT-NL's actual public corpus, all languages that overlap with a
NederCorpus variety (Dutch + West Frisian — GPT-NL_Public_Corpus covers only
nl/en/de/fy/da, so those are the only two of its languages that map onto any
of NederCorpus's 12 varieties), plus NederCorpus's own documents in NL, FY,
Afrikaans, Limburgs, Zeeuws, and Papiamento/Papiamentu (configurable via
--nedercorpus_varieties; default is these six — see NEDERCORPUS_EXTRA_VARIETIES
below for which of NederCorpus's 12 varieties are NOT included by default and
why). AF/LI/ZEA/PAP have no GPT-NL equivalent at all, so that portion can
only come from NederCorpus; NL/FY are NederCorpus's own copies layered on
top of whatever GPT-NL itself already contributes for those two.

Rationale: the original nl_only vs full comparison only tests NederCorpus's
own varieties against each other. It doesn't test the sharper claim a
GPT-NL-aware reviewer will ask about directly: "if you start from GPT-NL's
*actual* training data and add NederCorpus's other varieties on top, do those
varieties improve, and does NL perplexity hold up against a real external
corpus rather than our own held-out split?" Using GPT-NL's full multi-source
mix (including youtube-commons, per explicit instruction — see point 2
below) rather than a Dutch-only filter is the more faithful reproduction of
what GPT-NL actually trained on.

One correctness guard that is non-negotiable regardless of what's included:

1. LEAKAGE. GPT-NL's public corpus configs on HuggingFace are literally named
   after sources NederCorpus also draws from: `rechtspraak`, `woogle`,
   `officiele-bekendmakingen`, `tweedekamer`, `dpc`. If we pulled these
   naively, GPT-NL "Dutch text" could be near-duplicate to documents already
   sitting in experiment_data/holdout.jsonl (our own NL/NL-BE eval set) or in
   the AF/FY holdout carved out for this exact experiment. Any document whose
   text exactly matches a holdout entry is dropped before writing the split.
   This is a hard requirement, not a nice-to-have: training on eval text
   invalidates every PPL number this experiment produces.

Note on youtube-commons: NederCorpus's own corpus excludes YouTube-sourced
content because YouTube's ToS §4.B/§4.J prohibit redistribution for ML
training regardless of the per-video CC license PleIAs documents (preprint
§2.1). That policy governs what goes into NederCorpus itself. This script
includes youtube-commons in the GPT-NL baseline by default per explicit
instruction, because the point of this condition is to faithfully reproduce
what GPT-NL actually trained on, not to launder youtube-commons into
NederCorpus — this file's output (train_gptnl_af_fy.jsonl) is experiment
input for a comparison baseline, not a NederCorpus source, and is not part
of the distributed corpus. Pass --exclude-youtube-commons to leave it out
if you want the stricter version instead.

Usage:
  pip install datasets
  python prepare_gptnl_af_fy_split.py \
      --normalized_dir data/normalized \
      --holdout experiment_data/holdout.jsonl \
      --out experiment_data/train_gptnl_af_fy.jsonl \
      --gptnl_target_tokens 1_400_000_000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SEED = 42

# All configs on GPT-NL/GPT-NL_Public_Corpus as of the July 2026 dataset card,
# INCLUDING youtube-commons by default — see module docstring.
GPTNL_CONFIGS = [
    "american-stories", "auditdienstrijk", "belgian-journal", "cc_english-pd",
    "cc_eurovoc", "cc_german-pd", "cc_github_open_source", "cc_loc-pd-books",
    "cc_openalex", "common-crawl", "dansknaw", "dpc", "european-parliament",
    "kb-open-kranten", "kb-pd-books", "kb", "multi-eurlex", "nationaal-archief",
    "naturalis", "noordhollandsarchief", "officiele-bekendmakingen",
    "openraadsinformatie-part1", "openraadsinformatie-part2", "pbl",
    "rechtspraak", "tweedekamer", "utrechts-archief", "wikidata", "wikiwijs",
    "woogle", "zeeuws-archief", "youtube-commons",
]
YOUTUBE_CONFIG = "youtube-commons"

# GPT-NL_Public_Corpus covers exactly 5 languages: nl, en, de, fy, da. Of
# those, only nl and fy correspond to a NederCorpus variety (there is no
# separate GPT-NL language code for Belgian Dutch, Afrikaans, Limburgish,
# etc.), so those are the two pulled directly from GPT-NL.
GPTNL_OVERLAP_LANGUAGES = {"nl", "fy"}

# NederCorpus-side varieties layered on top of the GPT-NL pull, per explicit
# instruction. NL and FY here are NederCorpus's OWN documents, additional to
# (not instead of) whatever nl/fy GPT-NL itself contributes above. AF, LI,
# ZEA, PAP have no GPT-NL equivalent at all and come only from here.
#
# This is 6 of NederCorpus's 12 varieties. NOT included: NL-BE, VLS,
# NDS-NL, FRR, STQ, SRN. That may be intentional (VLS/NDS-NL/FRR/STQ/SRN
# are NederCorpus's smallest varieties by document count — see preprint
# §6.3 — and NL-BE arguably overlaps with GPT-NL's plain "nl" language tag
# rather than being a distinct condition worth separating). If the omission
# of those six wasn't deliberate, add them to this set before running.
NEDERCORPUS_EXTRA_VARIETIES = {"NL", "FY", "AF", "LI", "ZEA", "PAP"}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--normalized_dir", default="data/normalized", type=Path)
    p.add_argument("--holdout", default="experiment_data/holdout.jsonl", type=Path)
    p.add_argument("--out", default="experiment_data/train_gptnl_af_fy.jsonl", type=Path)
    p.add_argument("--gptnl_target_tokens", default=1_400_000_000, type=int,
                    help="Measured token budget (nl+fy combined) to pull from "
                         "GPT-NL_Public_Corpus. Default matches the corrected "
                         "GPT-NL Dutch-slice estimate (1.4B, not the ~36B figure "
                         "used for the full public+private breakdown in the paper's "
                         "related-work table — that number describes something "
                         "different; reconcile the two before submission).")
    p.add_argument("--nedercorpus_varieties", default=sorted(NEDERCORPUS_EXTRA_VARIETIES),
                    nargs="+",
                    help="NederCorpus language_variant codes to layer on top of the "
                         "GPT-NL pull. Default: NL, FY, AF, LI, ZEA, PAP. Does NOT "
                         "include NL-BE, VLS, NDS-NL, FRR, STQ, SRN — pass them "
                         "explicitly if that omission wasn't intentional.")
    p.add_argument("--nedercorpus_max_tokens_per_variety", default=500_000_000, type=int,
                    help="Cap per NederCorpus variety (default 500M). NL alone is "
                         "~6B+ tokens of the corpus — pulling it uncapped would try "
                         "to hold most of NederCorpus in RAM at once alongside the "
                         "GPT-NL pull. AF/LI/ZEA/PAP will likely come in well under "
                         "this cap and be included in full; NL and FY will almost "
                         "certainly be capped and randomly subsampled (seed 42).")
    p.add_argument("--exclude-youtube-commons", action="store_true",
                    help="Drop the youtube-commons config. Default: INCLUDED, to "
                         "faithfully reproduce GPT-NL's actual training mix as the "
                         "external baseline for this comparison.")
    return p.parse_args()


def approx_tokens(text: str) -> int:
    return len(text.split())


def load_holdout_texts(holdout_path: Path) -> set[str]:
    """Exact-text lookup set. Cheap and sufficient here — we only need to
    catch verbatim overlap between GPT-NL's redistribution of a shared
    source and our own copy of the same document, which will match exactly
    (same upstream text, no reformatting), not near-duplicates."""
    texts: set[str] = set()
    if not holdout_path.exists():
        print(f"WARNING: {holdout_path} not found — leakage guard will be a no-op. "
              f"Run prepare_splits.py first.", flush=True)
        return texts
    with open(holdout_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("text", "")
            if t:
                texts.add(t)
    print(f"Loaded {len(texts):,} holdout texts for leakage exclusion.", flush=True)
    return texts


def pull_gptnl_overlap(target_tokens: int, holdout_texts: set[str],
                        include_youtube: bool) -> tuple[list[str], dict[str, int]]:
    """Round-robin across GPT-NL configs (streaming — never downloads a
    full config to disk), keeping records whose `language` is in
    GPTNL_OVERLAP_LANGUAGES ({'nl', 'fy'} — the only two of GPT-NL's five
    languages that map onto a NederCorpus variety), until target reached or
    configs exhausted. Round-robin (not sequential) avoids the split being
    dominated by whichever config happens to be listed first.

    Returns (collected_texts, tokens_by_language) — the per-language
    breakdown matters for the paper: this is a mixed nl+fy pull sharing one
    token budget, not two independently-sized pulls, and readers need to
    know how much of the 1.4B actually ended up being Frisian."""
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("ERROR: pip install datasets")

    configs = list(GPTNL_CONFIGS) if include_youtube else \
        [c for c in GPTNL_CONFIGS if c != YOUTUBE_CONFIG]
    rng = random.Random(SEED)
    rng.shuffle(configs)

    print(f"Pulling GPT-NL slice ({sorted(GPTNL_OVERLAP_LANGUAGES)}) from "
          f"{len(configs)} configs (youtube-commons "
          f"{'included' if include_youtube else 'EXCLUDED'}), "
          f"target {target_tokens/1e9:.2f}B tokens ...", flush=True)

    iterators = {}
    exhausted = set()
    retry_counts: dict[str, int] = {cfg: 0 for cfg in configs}
    MAX_RETRIES = 4  # a single transient reset (observed: intermittent
    # "connection forcibly closed by remote host" on this network) should
    # not permanently drop 1/32 of the pull. huggingface_hub's own internal
    # retry (the "Retrying in 1s [Retry N/5]" lines) exhausts in ~4s and
    # raises past us with its http client already torn down, so `next()` on
    # the same iterator object is doomed forever — the fix is to rebuild a
    # fresh load_dataset()/iter() for that config before giving up on it.
    collected: list[str] = []
    tokens_by_lang: dict[str, int] = {lang: 0 for lang in GPTNL_OVERLAP_LANGUAGES}
    total_tokens = 0
    total_leaked_skipped = 0
    total_other_lang_skipped = 0

    def open_iterator(cfg: str):
        ds = load_dataset("GPT-NL/GPT-NL_Public_Corpus", cfg, split="train", streaming=True)
        return iter(ds)

    for cfg in configs:
        try:
            iterators[cfg] = open_iterator(cfg)
        except Exception as e:
            print(f"  [SKIP config] {cfg}: {e}", flush=True)
            exhausted.add(cfg)

    round_num = 0
    while total_tokens < target_tokens and len(exhausted) < len(configs):
        round_num += 1
        for cfg in configs:
            if cfg in exhausted:
                continue
            if total_tokens >= target_tokens:
                break
            try:
                rec = next(iterators[cfg])
            except StopIteration:
                exhausted.add(cfg)
                continue
            except Exception as e:
                retry_counts[cfg] += 1
                if retry_counts[cfg] > MAX_RETRIES:
                    print(f"  [ERROR reading] {cfg}: {e} — {MAX_RETRIES} retries "
                          f"exhausted, marking exhausted.", flush=True)
                    exhausted.add(cfg)
                    continue
                print(f"  [ERROR reading] {cfg}: {e} — reopening iterator "
                      f"(retry {retry_counts[cfg]}/{MAX_RETRIES}) ...", flush=True)
                time.sleep(3 * retry_counts[cfg])
                try:
                    iterators[cfg] = open_iterator(cfg)
                except Exception as e2:
                    print(f"  [ERROR reopening] {cfg}: {e2}", flush=True)
                continue

            lang = (rec.get("language") or "").strip().lower()
            if lang not in GPTNL_OVERLAP_LANGUAGES:
                total_other_lang_skipped += 1
                continue
            text = rec.get("text", "")
            if not text:
                continue
            if text in holdout_texts:
                total_leaked_skipped += 1
                continue

            collected.append(text)
            n = approx_tokens(text)
            total_tokens += n
            tokens_by_lang[lang] += n

        if round_num % 50 == 0:
            print(f"  round {round_num}: {total_tokens/1e6:.0f}M tokens "
                  f"(nl={tokens_by_lang.get('nl', 0)/1e6:.0f}M, "
                  f"fy={tokens_by_lang.get('fy', 0)/1e6:.0f}M), "
                  f"{len(exhausted)}/{len(configs)} configs exhausted, "
                  f"{total_leaked_skipped} leakage-skipped, "
                  f"{total_other_lang_skipped} other-language skipped", flush=True)

    print(f"GPT-NL slice: {total_tokens/1e9:.3f}B tokens from {len(collected):,} docs "
          f"(nl={tokens_by_lang.get('nl', 0)/1e9:.3f}B, "
          f"fy={tokens_by_lang.get('fy', 0)/1e9:.3f}B). "
          f"Skipped {total_leaked_skipped:,} docs for holdout leakage, "
          f"{total_other_lang_skipped:,} records in other languages.", flush=True)
    if total_tokens < target_tokens * 0.9:
        print(f"WARNING: only reached {total_tokens/1e9:.2f}B of "
              f"{target_tokens/1e9:.2f}B target — configs exhausted early. "
              f"Report the actual figure, not the target, in the paper.", flush=True)
    return collected, tokens_by_lang


class _NcDocRef:
    __slots__ = ("file_idx", "line_no", "tokens", "variant")

    def __init__(self, file_idx: int, line_no: int, tokens: int, variant: str):
        self.file_idx = file_idx
        self.line_no = line_no
        self.tokens = tokens
        self.variant = variant


def _index_nedercorpus_varieties(files: list[Path], holdout_texts: set[str],
                                  varieties: set[str]) -> tuple[list[_NcDocRef], int]:
    """Pass 1: scan every normalized file once, keeping only a lightweight
    (file_idx, line_no, tokens, variant) reference for matching, non-holdout
    documents — never the text itself. This is the same index-then-sample
    pattern prepare_splits.py and prepare_tier_splits.py already use
    elsewhere in this repo, for the same reason: NL alone is most of the
    corpus, and holding its text in a Python list would risk OOM-killing a
    paid multi-hour GPU rental."""
    refs: list[_NcDocRef] = []
    skipped = 0
    print(f"\nIndexing {len(files)} normalized files for {sorted(varieties)} documents "
          f"(pass 1/2 — index only, no text held in memory) ...", flush=True)
    for fi, path in enumerate(files):
        if fi % 20 == 0:
            print(f"  [{fi}/{len(files)}] {path.name} ...", flush=True)
        with open(path, encoding="utf-8", errors="replace") as fh:
            for li, line in enumerate(fh):
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    rec = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                variant = ((rec.get("meta") or {}).get("language_variant") or "").upper()
                if variant not in varieties:
                    continue
                text = rec.get("text", "")
                if not text:
                    continue
                if text in holdout_texts:
                    skipped += 1
                    continue
                refs.append(_NcDocRef(fi, li, approx_tokens(text), variant))
    print(f"  Indexed {len(refs):,} candidate docs, excluded {skipped:,} holdout docs.",
          flush=True)
    return refs, skipped


def pull_nedercorpus_varieties(normalized_dir: Path, holdout_texts: set[str],
                                varieties: set[str], max_tokens_per_variety: int,
                                out_fh) -> dict[str, int]:
    """Two-pass, capped, streaming-to-`out_fh` version of the NederCorpus
    variety pull. Default variety set: NL, FY, AF, LI, ZEA, PAP — NL and FY
    are also pulled from GPT-NL directly (pull_gptnl_overlap), so this adds
    NederCorpus's own copies of those on top; AF/LI/ZEA/PAP have no GPT-NL
    equivalent and come only from here.

    Each variety is capped at `max_tokens_per_variety` (randomly subsampled,
    seed 42, if it exceeds the cap) so that including NL doesn't try to pull
    ~6B+ tokens of NederCorpus's own text into memory at once. Writes
    {"text": ...} lines directly to `out_fh` as it goes rather than
    returning a big list — text for a given document exists in memory only
    for the instant it's read from its source file and written out.

    Returns tokens_by_variety for reporting."""
    files = sorted(normalized_dir.glob("*.jsonl"))
    refs, _ = _index_nedercorpus_varieties(files, holdout_texts, varieties)

    by_variant: dict[str, list[_NcDocRef]] = {v: [] for v in varieties}
    for r in refs:
        by_variant[r.variant].append(r)

    rng = random.Random(SEED)
    selected: dict[tuple[int, int], str] = {}  # (file_idx, line_no) -> variant
    tokens_by_variety: dict[str, int] = {v: 0 for v in varieties}
    for v, v_refs in by_variant.items():
        total_v_tokens = sum(r.tokens for r in v_refs)
        if total_v_tokens <= max_tokens_per_variety:
            chosen = v_refs
        else:
            shuffled = list(v_refs)
            rng.shuffle(shuffled)
            chosen = []
            acc = 0
            for r in shuffled:
                if acc + r.tokens > max_tokens_per_variety:
                    break
                chosen.append(r)
                acc += r.tokens
            print(f"  {v}: {total_v_tokens/1e9:.2f}B tokens available, capped to "
                  f"{max_tokens_per_variety/1e6:.0f}M — random subsample "
                  f"({len(chosen):,}/{len(v_refs):,} docs).", flush=True)
        for r in chosen:
            selected[(r.file_idx, r.line_no)] = v
            tokens_by_variety[v] += r.tokens

    print(f"\nStreaming pass 2/2: writing {len(selected):,} selected docs "
          f"directly to output ...", flush=True)
    by_file: dict[int, set[int]] = {}
    for (fi, li) in selected:
        by_file.setdefault(fi, set()).add(li)

    written = 0
    for fi in sorted(by_file):
        wanted = by_file[fi]
        with open(files[fi], encoding="utf-8", errors="replace") as fh:
            for li, line in enumerate(fh):
                if li not in wanted:
                    continue
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    rec = json.loads(line_s)
                except json.JSONDecodeError:
                    continue
                text = rec.get("text", "")
                if not text:
                    continue
                out_fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
                written += 1

    breakdown = ", ".join(f"{v}={tokens_by_variety[v]/1e6:.1f}M" for v in sorted(varieties))
    print(f"NederCorpus {sorted(varieties)}: {sum(tokens_by_variety.values())/1e6:.1f}M "
          f"tokens from {written:,} docs written ({breakdown}).", flush=True)
    return tokens_by_variety, written


def main():
    args = parse_args()
    holdout_texts = load_holdout_texts(args.holdout)
    nedercorpus_varieties = {v.upper() for v in args.nedercorpus_varieties}

    missing = {"NL-BE", "VLS", "NDS-NL", "FRR", "STQ", "SRN"} - nedercorpus_varieties
    if missing:
        print(f"NOTE: {sorted(missing)} are NOT in --nedercorpus_varieties "
              f"({sorted(nedercorpus_varieties)}). Pass them explicitly with "
              f"--nedercorpus_varieties if full 12-variety coverage was intended.",
              flush=True)

    include_youtube = not args.exclude_youtube_commons
    gptnl_texts, gptnl_lang_tokens = pull_gptnl_overlap(
        args.gptnl_target_tokens, holdout_texts, include_youtube)

    # Written GPT-NL-block-then-NederCorpus-block, NOT globally shuffled at the
    # document level. This is deliberate, not an oversight: shuffling would
    # require materializing NederCorpus's (capped but still large) selection
    # as one Python list alongside GPT-NL's, which is exactly the memory risk
    # this two-pass design exists to avoid. It doesn't cost anything at
    # training time either — train_tier.py's Trainer uses the default
    # RandomSampler, which shuffles at the chunk level after tokenization, so
    # block order in the raw JSONL has no effect on what order the model
    # actually sees chunks in during training.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for text in gptnl_texts:
            fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
        gptnl_docs = len(gptnl_texts)
        del gptnl_texts  # free before the (still potentially large) NederCorpus pass

        nc_variety_tokens, nc_docs = pull_nedercorpus_varieties(
            args.normalized_dir, holdout_texts, nedercorpus_varieties,
            args.nedercorpus_max_tokens_per_variety, fh)

    gptnl_total_tokens = gptnl_lang_tokens.get("nl", 0) + gptnl_lang_tokens.get("fy", 0)
    nc_total_tokens = sum(nc_variety_tokens.values())
    total_tokens = gptnl_total_tokens + nc_total_tokens
    total_docs = gptnl_docs + nc_docs

    print(f"\nCombined split: {total_docs:,} docs, {total_tokens/1e9:.3f}B tokens")
    print(f"  GPT-NL nl:  {gptnl_lang_tokens.get('nl', 0)/1e9:.3f}B tokens")
    print(f"  GPT-NL fy:  {gptnl_lang_tokens.get('fy', 0)/1e9:.3f}B tokens "
          f"(youtube-commons {'included' if include_youtube else 'excluded'})")
    for v in sorted(nedercorpus_varieties):
        print(f"  NederCorpus {v}: {nc_variety_tokens.get(v, 0)/1e9:.3f}B tokens")
    print(f"Written -> {args.out}")

    meta = {
        "gptnl_target_tokens": args.gptnl_target_tokens,
        "gptnl_tokens_by_language": gptnl_lang_tokens,
        "gptnl_youtube_commons_included": include_youtube,
        "nedercorpus_varieties_requested": sorted(nedercorpus_varieties),
        "nedercorpus_varieties_omitted": sorted(missing),
        "nedercorpus_max_tokens_per_variety": args.nedercorpus_max_tokens_per_variety,
        "nedercorpus_tokens_by_variety": nc_variety_tokens,
        "nedercorpus_docs": nc_docs,
        "total_tokens": total_tokens,
        "total_docs": total_docs,
    }
    meta_path = args.out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Composition metadata -> {meta_path}")


if __name__ == "__main__":
    main()
