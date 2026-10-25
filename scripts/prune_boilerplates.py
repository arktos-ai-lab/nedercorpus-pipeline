#!/usr/bin/env python3
"""
scripts/prune_boilerplates.py — Heuristic Legal Boilerplate and Semantic Redundancy Filter.
Parses Rechtspraak and government documents to strip out repetitive legal boilerplate templates,
reducing semantic redundancy and improving the corpus's Vendi/diversity profile.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NORM_DIR = REPO_ROOT / "data" / "normalized"
CLEAN_DIR = REPO_ROOT / "data" / "pruned_normalized"

# Common Dutch legal boilerplate patterns
BOILERPLATE_PATTERNS = [
    re.compile(r"de voorzieningenrechter van de rechtbank.*?, rechtdoende in kort geding,", re.IGNORECASE),
    re.compile(r"gelet op het bepaalde in artikel.*? van het Wetboek van Burgerlijke Rechtsvordering,", re.IGNORECASE),
    re.compile(r"overwegende dat de formele eisen voor de indiening van het verzoekschrift zijn nageleefd,", re.IGNORECASE),
    re.compile(r"uitspraak van de meervoudige kamer voor strafzaken in de zaak tegen:", re.IGNORECASE),
    re.compile(r"dit arrest is gewezen door.*? en uitgesproken ter openbare terechtzitting,", re.IGNORECASE),
]

def clean_text(text: str) -> str:
    original_len = len(text)
    # Apply boilerplate removal
    for pattern in BOILERPLATE_PATTERNS:
        text = pattern.sub("", text)
    
    # Normalize extra whitespaces introduced by deletions
    text = re.sub(r"\s+", " ", text).strip()
    return text

def main():
    print("Starting heuristic legal boilerplate pruning ...")
    if not NORM_DIR.exists():
        print(f"Normalized directory {NORM_DIR} does not exist. Run the pipeline first.")
        return

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    files = list(NORM_DIR.glob("*.jsonl"))
    
    total_docs = 0
    total_pruned_chars = 0

    for path in files:
        out_path = CLEAN_DIR / path.name
        print(f"  Pruning boilerplates from {path.name} -> {out_path.name}")
        
        with open(path, "r", encoding="utf-8") as fin, open(out_path, "w", encoding="utf-8") as fout:
            for line in fin:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                original_text = obj.get("text", "")
                cleaned = clean_text(original_text)
                
                # If we pruned significantly or text became too short, we filter/update
                diff = len(original_text) - len(cleaned)
                if diff > 0:
                    total_pruned_chars += diff
                    obj["text"] = cleaned
                    
                total_docs += 1
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print("\n======================================================================")
    print(" BOILERPLATE PRUNING COMPLETED")
    print("======================================================================")
    print(f"  Processed documents: {total_docs:,}")
    print(f"  Total boilerplate characters removed: {total_pruned_chars:,}")
    print(f"  Pruned normalized files written to: {CLEAN_DIR}")
    print("======================================================================")

if __name__ == "__main__":
    main()
