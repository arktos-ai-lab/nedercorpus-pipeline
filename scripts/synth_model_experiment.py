"""
Per-variant model selection experiment for NederCorpus synthetic generation.

For each variant × model × method: generate synthetic articles from Wikidata
triples, score against real Wikipedia articles using BERTScore (XLM-R backbone).

Methods:
  baseline  - Wikidata triples → grouped template sentences → LLM paragraph
  rag       - baseline + PD/CC-BY corpus passages retrieved by TF-IDF
  dict      - baseline + dialect vocabulary hints from wiktionary

Models tested (MIT or Apache 2.0 only):
  Run one model at a time via --model flag. Start the target model in vLLM first:
    vllm serve <model_id> --port 8000

Usage:
    python scripts/synth_model_experiment.py --variant LI --model qwen2.5-14b --method dict
    python scripts/synth_model_experiment.py --variant PAP --model phi-4-14b --method rag
    python scripts/synth_model_experiment.py --all-variants --model qwen2.5-7b --method baseline

Results saved to: experiments/synth_results.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "F:/NederCorpus")

from scripts.synth_rag_retriever import VariantRAG
from scripts.synth_dialect_mapper import DialectMapper

# ---------------------------------------------------------------------------
# Model registry — MIT and Apache 2.0 only
# ---------------------------------------------------------------------------

MODELS: dict[str, dict] = {
    # MIT licensed
    "phi-4-14b":        {"hf": "microsoft/Phi-4",                        "license": "MIT",        "params": "14B"},
    "phi-4-mini-3.8b":  {"hf": "microsoft/Phi-4-mini-instruct",          "license": "MIT",        "params": "3.8B"},
    "phi-3.5-mini":     {"hf": "microsoft/Phi-3.5-mini-instruct",        "license": "MIT",        "params": "3.8B"},
    # Apache 2.0 licensed
    "qwen2.5-3b":       {"hf": "Qwen/Qwen2.5-3B-Instruct",               "license": "Apache-2.0", "params": "3B"},
    "qwen2.5-7b":       {"hf": "Qwen/Qwen2.5-7B-Instruct",               "license": "Apache-2.0", "params": "7B"},
    "qwen2.5-14b":      {"hf": "Qwen/Qwen2.5-14B-Instruct",              "license": "Apache-2.0", "params": "14B"},
    "qwen2.5-32b":      {"hf": "Qwen/Qwen2.5-32B-Instruct",              "license": "Apache-2.0", "params": "32B"},
    "qwen2.5-72b":      {"hf": "Qwen/Qwen2.5-72B-Instruct",              "license": "Apache-2.0", "params": "72B"},
    "mistral-7b-v0.3":  {"hf": "mistralai/Mistral-7B-Instruct-v0.3",     "license": "Apache-2.0", "params": "7B"},
    "mistral-nemo-12b": {"hf": "mistralai/Mistral-Nemo-Instruct-2407",   "license": "Apache-2.0", "params": "12B"},
    "olmo2-7b":         {"hf": "allenai/OLMo-2-1124-7B-Instruct",        "license": "Apache-2.0", "params": "7B"},
    "olmo2-13b":        {"hf": "allenai/OLMo-2-1124-13B-Instruct",       "license": "Apache-2.0", "params": "13B"},
}

VARIANTS = ["NL", "NL-BE", "AF", "FY", "LI", "PAP", "ZEA", "VLS"]

CLEANED = Path("F:/NederCorpus/data/cleaned")
RESULTS_DIR = Path("F:/NederCorpus/experiments")
RESULTS_CSV = RESULTS_DIR / "synth_results.csv"

VLLM_BASE = "http://localhost:8000/v1"

# ---------------------------------------------------------------------------
# Wikipedia reference article fetching (for BERTScore evaluation only)
# Lang variant → Wikipedia language code
# ---------------------------------------------------------------------------

WIKI_LANG: dict[str, str] = {
    "NL": "nl", "NL-BE": "nl", "AF": "af", "FY": "fy",
    "LI": "li", "PAP": "pap", "ZEA": "zea", "VLS": "vls",
    "NDS-NL": "nds_nl", "FRR": "frr", "STQ": "stq", "SRN": "srn",
}

def _fetch_wikipedia_intro(qid: str, lang: str) -> str | None:
    """Fetch the first paragraph of a Wikipedia article for a Wikidata QID."""
    # Step 1: get Wikipedia page title from Wikidata sitelinks
    url = (
        "https://www.wikidata.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "wbgetentities",
            "ids": qid,
            "props": "sitelinks",
            "sitefilter": f"{lang}wiki",
            "format": "json",
        })
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        sitelinks = data.get("entities", {}).get(qid, {}).get("sitelinks", {})
        if f"{lang}wiki" not in sitelinks:
            return None
        title = sitelinks[f"{lang}wiki"]["title"]
    except Exception:
        return None

    # Step 2: fetch intro paragraph via Wikipedia API
    wp_url = (
        f"https://{lang}.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "query",
            "titles": title,
            "prop": "extracts",
            "exintro": "1",
            "explaintext": "1",
            "redirects": "1",
            "format": "json",
        })
    )
    try:
        with urllib.request.urlopen(wp_url, timeout=15) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            extract = page.get("extract", "").strip()
            if extract and len(extract) > 50:
                # Return first 500 chars only (intro)
                return extract[:500]
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Load sample entities from existing wikidata_expand files
# ---------------------------------------------------------------------------

def _load_entities(variant: str, n: int = 50) -> list[dict]:
    """Load up to n entities from the wikidata_expand JSONL for this variant."""
    fname = f"wikidata_expand_{variant.lower()}.jsonl"
    path = CLEANED / fname
    if not path.exists():
        print(f"  [entities] {fname} not found")
        return []
    entities = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta = rec.get("meta", {})
            source_url = meta.get("source_url", "")
            qid = source_url.split("/")[-1] if source_url else ""
            if not qid.startswith("Q"):
                continue
            entities.append({
                "qid": qid,
                "text": rec.get("text", ""),   # existing template output
                "source_url": source_url,
            })
            if len(entities) >= n:
                break
    return entities


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_VARIANT_NAMES = {
    "NL": "Nederlands", "NL-BE": "Belgisch-Nederlands (Vlaams)",
    "AF": "Afrikaans", "FY": "Fries (West-Fries)",
    "LI": "Limburgs", "PAP": "Papiamentu/Papiaments",
    "ZEA": "Zeeuws", "VLS": "West-Vlaams",
    "NDS-NL": "Nedersaksisch", "FRR": "Noord-Fries",
    "STQ": "Saterlands Fries", "SRN": "Sranantongo",
}


def build_baseline_prompt(entity_text: str, variant: str) -> str:
    lang_name = _VARIANT_NAMES.get(variant, variant)
    return (
        f"Schrijf een korte encyclopedische alinea (3-5 zinnen) in het {lang_name} "
        f"over het volgende onderwerp. Gebruik alleen de gegeven feiten. "
        f"Verzin geen extra informatie.\n\n"
        f"Feiten:\n{entity_text}\n\n"
        f"Alinea:"
    )


def build_rag_prompt(entity_text: str, variant: str, passages: list[str]) -> str:
    lang_name = _VARIANT_NAMES.get(variant, variant)
    context = "\n---\n".join(passages[:3]) if passages else ""
    ctx_block = f"\nAchtergrondtekst ter referentie:\n{context}\n" if context else ""
    return (
        f"Schrijf een korte encyclopedische alinea (3-5 zinnen) in het {lang_name} "
        f"over het volgende onderwerp. Gebruik alleen de gegeven feiten. "
        f"Verzin geen extra informatie.{ctx_block}\n"
        f"Feiten:\n{entity_text}\n\n"
        f"Alinea:"
    )


def build_dict_prompt(entity_text: str, variant: str, vocab_hint: str) -> str:
    lang_name = _VARIANT_NAMES.get(variant, variant)
    vocab_block = f"\n{vocab_hint}\n" if vocab_hint else ""
    return (
        f"Schrijf een korte encyclopedische alinea (3-5 zinnen) in het {lang_name} "
        f"over het volgende onderwerp. Gebruik alleen de gegeven feiten. "
        f"Verzin geen extra informatie.{vocab_block}\n"
        f"Feiten:\n{entity_text}\n\n"
        f"Alinea:"
    )


# ---------------------------------------------------------------------------
# vLLM inference
# ---------------------------------------------------------------------------

def _call_vllm(prompt: str, model_key: str, max_tokens: int = 300,
               temperature: float = 0.3) -> str | None:
    """Call the running vLLM server (OpenAI-compatible completions endpoint)."""
    model_id = MODELS[model_key]["hf"]
    payload = json.dumps({
        "model": model_id,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stop": ["\n\n\n", "Feiten:", "---"],
    }).encode()
    req = urllib.request.Request(
        f"{VLLM_BASE}/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        return data["choices"][0]["text"].strip()
    except Exception as e:
        print(f"    [vllm] error: {e}")
        return None


# ---------------------------------------------------------------------------
# Evaluation: BERTScore + chrF++
# ---------------------------------------------------------------------------

def _bertscore_batch(hypotheses: list[str], references: list[str],
                     lang: str = "nl") -> list[float]:
    """
    BERTScore F1 (XLM-RoBERTa-large backbone, language-agnostic).
    Falls back to token overlap F1 if bert-score is not installed.
    """
    try:
        from bert_score import score as bert_score_fn
        P, R, F = bert_score_fn(
            hypotheses, references,
            model_type="xlm-roberta-large",
            lang=lang,
            verbose=False,
            batch_size=8,
        )
        return F.tolist()
    except ImportError:
        print("  [score] bert-score not installed, using token overlap fallback")
        results = []
        for hyp, ref in zip(hypotheses, references):
            h_toks = set(hyp.lower().split())
            r_toks = set(ref.lower().split())
            if not r_toks:
                results.append(0.0)
                continue
            precision = len(h_toks & r_toks) / len(h_toks) if h_toks else 0
            recall = len(h_toks & r_toks) / len(r_toks)
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            results.append(round(f1, 4))
        return results


def _chrf_score(hypothesis: str, reference: str) -> float:
    """
    chrF++ sentence score (word_order=2, sacrebleu reference implementation).
    Returns a 0–100 score. Falls back to 0.0 if sacrebleu is not installed.
    """
    try:
        from sacrebleu.metrics import CHRF
        return round(CHRF(word_order=2).sentence_score(hypothesis, [reference]).score, 2)
    except ImportError:
        return 0.0


def _bleu_score(hypothesis: str, reference: str) -> float:
    """
    Sentence-BLEU via sacrebleu. Returns a 0–100 score.
    Falls back to 0.0 if sacrebleu is not installed.
    """
    try:
        from sacrebleu.metrics import BLEU
        return round(BLEU(effective_order=True).sentence_score(hypothesis, [reference]).score, 2)
    except ImportError:
        return 0.0


# Param string → approximate int for size comparisons
def _param_int(params: str) -> int:
    params = params.upper().replace("B", "")
    try:
        return int(float(params))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Main experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    variant: str,
    model_key: str,
    method: str,
    n_entities: int = 50,
    rag: VariantRAG | None = None,
    mapper: DialectMapper | None = None,
) -> list[dict]:
    """
    Run one (variant, model, method) combination.
    Returns list of result dicts ready for CSV writing.
    """
    print(f"\n=== {variant} / {model_key} / {method} ===")
    entities = _load_entities(variant, n=n_entities)
    if not entities:
        print(f"  No entities found for {variant}")
        return []

    wiki_lang = WIKI_LANG.get(variant, "nl")
    results = []

    # For low-resource variants with glossary injection, large models may not benefit —
    # their internal representations already cover the vocabulary. Run anyway so we can
    # measure and report the effect direction per model size.
    if method == "dict" and variant in ("FY", "ZEA", "VLS", "LI"):
        param_size = _param_int(MODELS[model_key]["params"])
        if param_size > 14:
            print(f"  [note] {variant} dict method with {model_key} ({MODELS[model_key]['params']}): "
                  f"glossary injection effect direction may reverse for large models on low-resource variants. "
                  f"Running to measure.")

    # Extract content words for dialect mapping (from entity texts)
    def _content_words(text: str) -> list[str]:
        words = re.findall(r"[a-záéíóúàèëïöüÿA-Z][a-záéíóúàèëïöüÿA-Z\-]{2,}", text)
        return list(set(w.lower() for w in words))

    for i, entity in enumerate(entities):
        qid = entity["qid"]
        template_text = entity["text"]

        # Fetch Wikipedia reference for evaluation
        ref_text = _fetch_wikipedia_intro(qid, wiki_lang)
        if not ref_text:
            print(f"  [{i+1}/{len(entities)}] {qid}: no Wikipedia article in {wiki_lang}, skipping")
            continue

        # Build prompt based on method
        if method == "baseline":
            prompt = build_baseline_prompt(template_text, variant)
        elif method == "rag":
            passages = rag.retrieve(template_text, k=3) if rag and rag.has_content else []
            prompt = build_rag_prompt(template_text, variant, passages)
        elif method == "dict":
            content_words = _content_words(template_text)
            vocab_hint = mapper.format_vocabulary_hint(content_words) if mapper else ""
            prompt = build_dict_prompt(template_text, variant, vocab_hint)
        else:
            raise ValueError(f"Unknown method: {method}")

        # Generate
        generated = _call_vllm(prompt, model_key)
        if not generated:
            print(f"  [{i+1}/{len(entities)}] {qid}: generation failed")
            continue

        # Score — all three metrics from one pass
        scores = _bertscore_batch([generated], [ref_text], lang=wiki_lang)
        bs_f1 = scores[0] if scores else 0.0
        chrf = _chrf_score(generated, ref_text)
        bleu = _bleu_score(generated, ref_text)

        result = {
            "variant": variant,
            "model": model_key,
            "model_params": MODELS[model_key]["params"],
            "model_license": MODELS[model_key]["license"],
            "method": method,
            "qid": qid,
            "bertscore_f1": round(bs_f1, 4),
            "chrf_pp": chrf,
            "bleu": bleu,
            "generated_len": len(generated.split()),
            "ref_len": len(ref_text.split()),
            "has_wiki_ref": True,
        }
        results.append(result)
        print(f"  [{i+1}/{len(entities)}] {qid}: "
              f"BERTScore={bs_f1:.3f}  chrF++={chrf:.1f}  BLEU={bleu:.1f}  "
              f"len={len(generated.split())}")
        time.sleep(0.2)  # polite

    return results


def _write_results(results: list[dict], append: bool = True) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "variant", "model", "model_params", "model_license", "method",
        "qid", "bertscore_f1", "chrf_pp", "bleu", "generated_len", "ref_len", "has_wiki_ref",
    ]
    mode = "a" if append and RESULTS_CSV.exists() else "w"
    with open(RESULTS_CSV, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if mode == "w":
            writer.writeheader()
        writer.writerows(results)
    print(f"\nResults written to {RESULTS_CSV} ({len(results)} rows)")


def _print_summary(results: list[dict]) -> None:
    if not results:
        print("No results.")
        return
    scores = [r["bertscore_f1"] for r in results]
    print(f"\nSummary: n={len(scores)}  mean={sum(scores)/len(scores):.3f}  "
          f"min={min(scores):.3f}  max={max(scores):.3f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(description="Synthetic generation model experiment")
    p.add_argument("--variant", choices=VARIANTS, help="Language variant")
    p.add_argument("--all-variants", action="store_true", help="Run all variants")
    p.add_argument("--model", choices=list(MODELS.keys()), required=True)
    p.add_argument("--method", choices=["baseline", "rag", "dict", "all"],
                   default="baseline")
    p.add_argument("--n", type=int, default=50, help="Entities per variant")
    p.add_argument("--list-models", action="store_true")
    return p.parse_args()


def main():
    args = _parse_args()

    if args.list_models:
        print(f"{'Key':<20} {'HF model ID':<55} {'License':<12} {'Size'}")
        print("-" * 100)
        for key, info in MODELS.items():
            print(f"{key:<20} {info['hf']:<55} {info['license']:<12} {info['params']}")
        return

    variants_to_run = VARIANTS if args.all_variants else [args.variant]
    if not variants_to_run[0]:
        print("Specify --variant or --all-variants")
        return

    methods = ["baseline", "rag", "dict"] if args.method == "all" else [args.method]

    # Pre-build RAG indexes and dialect mappers (once per variant)
    rag_indexes: dict[str, VariantRAG] = {}
    dialect_mappers: dict[str, DialectMapper] = {}

    for variant in variants_to_run:
        if "rag" in methods:
            rag = VariantRAG(variant)
            rag.build()
            rag_indexes[variant] = rag
        if "dict" in methods:
            mapper = DialectMapper(variant)
            mapper.build()
            dialect_mappers[variant] = mapper

    all_results = []
    for variant in variants_to_run:
        for method in methods:
            results = run_experiment(
                variant=variant,
                model_key=args.model,
                method=method,
                n_entities=args.n,
                rag=rag_indexes.get(variant),
                mapper=dialect_mappers.get(variant),
            )
            all_results.extend(results)
            _write_results(results, append=True)

    _print_summary(all_results)


if __name__ == "__main__":
    main()
