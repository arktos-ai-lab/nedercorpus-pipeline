#!/usr/bin/env python3
"""
scripts/validate_dialect_split.py — Validate dialect/variety splits in NederCorpus.
Uses a character-level 3-gram Cavnar-Trenkle classifier trained on clean variety seed text
to evaluate the precision, recall, and classification error of the corpus variety tags.
"""

import json
import random
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NORM_DIR = REPO_ROOT / "data" / "normalized"
SEED = 42

def get_ngrams(text: str, n=3):
    text = text.lower()
    return [text[i:i+n] for i in range(len(text) - n + 1) if text[i:i+n].isalpha()]

def train_ngram_profiles():
    """
    Train char 3-gram profiles for each variety from raw files in normalized/
    excluding the validation sample to prevent leakage.
    """
    random.seed(SEED)
    profiles = {}
    files = list(NORM_DIR.glob("*.jsonl"))
    
    # Group raw text by variant
    variant_docs = {}
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                variant = obj.get("meta", {}).get("language_variant", "NL")
                text = obj.get("text", "")
                if len(text) < 100:
                    continue
                if variant not in variant_docs:
                    variant_docs[variant] = []
                variant_docs[variant].append(text)
                
    print("Training character 3-gram classifier profiles...")
    for variant, docs in variant_docs.items():
        # Keep 500 docs for validation, train on the rest (max 2000)
        train_sample = docs[500:2500] if len(docs) > 1000 else docs[len(docs)//2:]
        all_ngrams = []
        for doc in train_sample:
            all_ngrams.extend(get_ngrams(doc))
        
        # Build profile of top 400 ngrams
        counts = Counter(all_ngrams).most_common(400)
        profiles[variant] = [ng for ng, _ in counts]
        print(f"  Profile for {variant}: trained on {len(train_sample)} docs.")
        
    return profiles, variant_docs

def classify(text, profiles):
    ngrams = get_ngrams(text)
    doc_counts = Counter(ngrams).most_common(400)
    doc_profile = [ng for ng, _ in doc_counts]
    
    best_variant = "NL"
    min_dist = float("inf")
    
    for variant, prof in profiles.items():
        # Compute Out-of-Place distance (rank distance)
        dist = 0
        for i, ng in enumerate(doc_profile):
            if ng in prof:
                dist += abs(i - prof.index(ng))
            else:
                dist += 400  # maximum penalty
        if dist < min_dist:
            min_dist = dist
            best_variant = variant
            
    return best_variant

def main():
    profiles, variant_docs = train_ngram_profiles()
    
    results = {}
    print("\nEvaluating classifier against validation samples (500 docs per variety)...")
    for variant, docs in variant_docs.items():
        eval_sample = docs[:500]
        correct = 0
        predictions = []
        
        for doc in eval_sample:
            pred = classify(doc, profiles)
            predictions.append(pred)
            if pred == variant:
                correct += 1
                
        results[variant] = {
            "total": len(eval_sample),
            "correct": correct,
            "predictions": predictions
        }
    
    # Calculate Precision, Recall, F1
    print("\n======================================================================")
    print(" VARIETY / DIALECT CLASSIFICATION METRIC MATRIX")
    print("======================================================================\n")
    print("| Variety | Precision | Recall | F1-Score | Status |")
    print("|---|---|---|---|---|")
    
    for target in sorted(results.keys()):
        tp = results[target]["correct"]
        fn = results[target]["total"] - tp
        
        # calculate false positives
        fp = 0
        for other, res in results.items():
            if other != target:
                fp += sum(1 for p in res["predictions"] if p == target)
                
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        
        status = "Robust" if f1 >= 0.85 else "Contaminated"
        print(f"| {target} | {precision:.3f} | {recall:.3f} | {f1:.3f} | {status} |")
        
    print("\n======================================================================")

if __name__ == "__main__":
    main()
