"""
Parse dialect Wiktionary entries to build a Dutch → dialect vocabulary map.
Matches Wikidata triple content words against the map to produce
vocabulary hints that are SPECIFIC to what will appear in the generated text.

Wiktionary entry format (from limburgish_wiktionary.jsonl):
    dialect_term (Nederlands: dutch_term1, dutch_term2)

Strategy:
  1. Parse all wiktionary entries to build {nl_lemma: [dialect_forms]} dict
  2. Given a set of content words from Wikidata triples, look them up
  3. Return only the matches — terms that will plausibly appear in the output

Usage:
    from scripts.synth_dialect_mapper import DialectMapper
    mapper = DialectMapper("LI")
    mapper.build()
    hints = mapper.map_triple_terms(["persoon", "Nederland", "politicus", "geboren"])
    # Returns: {"persoon": "persoan", "Nederland": "Nederlaand", ...}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CLEANED = Path("F:/NederCorpus/data/cleaned")

# Which wiktionary files to use per variant
# We extract vocabulary from these to build NL→dialect maps
# Note: SA license doesn't prevent USE as a vocabulary reference;
# the generated text is our own and not a derivative of the dictionary.
WIKTIONARY_SOURCES: dict[str, list[str]] = {
    "LI":  ["limburgish_wiktionary.jsonl"],
    "FY":  ["frisian_wiktionary.jsonl"],
    "VLS": ["west_flemish_wiki.jsonl"],    # parse dialect terms from wiki text
    "ZEA": ["zeelandic_wiki.jsonl"],
    "AF":  [],                              # AF is close enough to NL; no wiktionary needed
    "PAP": [],                              # PAP government text used for RAG instead
    "NL":  [],
    "NL-BE": [],
}

# Regex: "dialect_term (Nederlands: translation1, translation2)"
_NL_PATTERN = re.compile(
    r"^([^\s(]{2,40})\s+\([Nn]ederlands:\s*([^)]{2,100})\)",
    re.MULTILINE
)

# Also handle: "dialect_term (nl: translation)" or inline definitions
_NL_PATTERN2 = re.compile(
    r"\b([a-záéíóúàèëïöüÿ][a-záéíóúàèëïöüÿ\-]{1,30})\s*\(Nederlands:\s*([^)]{2,80})\)",
    re.IGNORECASE
)


def _clean_term(t: str) -> str:
    return t.strip().lower().strip(".,;:\"'")


def _parse_wiktionary_text(text: str) -> list[tuple[str, str]]:
    """
    Extract (dialect_form, dutch_equivalent) pairs from a wiktionary page text.
    Returns list of (dialect, nl_equivalent) tuples.
    """
    pairs = []
    for m in _NL_PATTERN.finditer(text):
        dialect_form = _clean_term(m.group(1))
        nl_translations = [_clean_term(t) for t in m.group(2).split(",")]
        for nl in nl_translations:
            if nl and len(nl) > 1:
                pairs.append((dialect_form, nl))
    for m in _NL_PATTERN2.finditer(text):
        dialect_form = _clean_term(m.group(1))
        nl_translations = [_clean_term(t) for t in m.group(2).split(",")]
        for nl in nl_translations:
            if nl and len(nl) > 1:
                pairs.append((dialect_form, nl))
    return pairs


class DialectMapper:
    """
    Maps Dutch content words from Wikidata triples to their dialect equivalents.
    Only returns mappings for words that actually appear in the triple content,
    ensuring injected vocabulary is relevant to the text being generated.
    """

    def __init__(self, variant: str):
        self.variant = variant
        # nl_lemma → list of dialect forms (first is preferred standard form)
        self._nl_to_dialect: dict[str, list[str]] = {}
        self._built = False

    def build(self) -> int:
        """Parse wiktionary files and build the mapping. Returns entry count."""
        sources = WIKTIONARY_SOURCES.get(self.variant, [])
        if not sources:
            self._built = True
            return 0

        total_pairs = 0
        for fname in sources:
            path = CLEANED / fname
            if not path.exists():
                print(f"  [dialect] {fname}: not found")
                continue
            count = 0
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = rec.get("text", "")
                    if not text:
                        continue
                    pairs = _parse_wiktionary_text(text)
                    for dialect_form, nl_term in pairs:
                        if nl_term not in self._nl_to_dialect:
                            self._nl_to_dialect[nl_term] = []
                        if dialect_form not in self._nl_to_dialect[nl_term]:
                            self._nl_to_dialect[nl_term].append(dialect_form)
                    count += len(pairs)
            total_pairs += count
            print(f"  [dialect] {fname}: {count} (dialect, NL) pairs extracted")

        self._built = True
        print(f"  [dialect] {self.variant}: {len(self._nl_to_dialect)} unique NL lemmas mapped")
        return len(self._nl_to_dialect)

    def map_triple_terms(self, nl_terms: list[str]) -> dict[str, str]:
        """
        Given a list of Dutch content words from Wikidata triples,
        return a dict of {nl_term: preferred_dialect_form} for those
        that have a known dialect equivalent.

        Only returns mappings for the provided terms — never random vocabulary.
        """
        if not self._built:
            self.build()

        result = {}
        for term in nl_terms:
            term_lower = term.lower().strip()
            if term_lower in self._nl_to_dialect:
                # Use first (most common) dialect form
                result[term_lower] = self._nl_to_dialect[term_lower][0]
            # Also try singular/plural variants (naive: strip trailing 's' or 'en')
            for stripped in [term_lower.rstrip("s"), term_lower.rstrip("en")]:
                if stripped and stripped != term_lower and stripped in self._nl_to_dialect:
                    result[term_lower] = self._nl_to_dialect[stripped][0]
                    break

        return result

    def format_vocabulary_hint(self, nl_terms: list[str]) -> str:
        """
        Format vocabulary hints for LLM prompt injection.
        Returns empty string if no matches found.
        """
        mapping = self.map_triple_terms(nl_terms)
        if not mapping:
            return ""
        lines = [f"  {nl} → {dialect}" for nl, dialect in sorted(mapping.items())]
        return (
            f"Gebruik de volgende {self.variant}-dialectvormen waar passend:\n"
            + "\n".join(lines)
        )

    @property
    def has_mappings(self) -> bool:
        return bool(self._nl_to_dialect)
