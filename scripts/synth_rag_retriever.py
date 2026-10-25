"""
Build and query a TF-IDF retrieval index from PD/CC-BY corpus text per language variant.
Only PD and CC-BY sources are used — no CC-BY-SA (no Wikipedia).

Usage:
    from scripts.synth_rag_retriever import VariantRAG
    rag = VariantRAG("PAP")
    rag.build()
    passages = rag.retrieve("overheid Aruba wetgeving", k=3)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

CLEANED = Path("F:/NederCorpus/data/cleaned")

# PD/CC-BY sources per variant — NO CC-BY-SA
# Key: language_variant code, Value: list of (jsonl_filename, max_docs)
RAG_SOURCES: dict[str, list[tuple[str, int]]] = {
    "NL": [
        ("woogle_foia.jsonl",           5000),
        ("parlamint_nl.jsonl",           5000),
        ("kamerstukken.jsonl",           5000),
        ("vlaamse_codex.jsonl",          2000),
        ("global_voices_nl.jsonl",       2000),
        ("rce.jsonl",                    2000),
    ],
    "NL-BE": [
        ("parlamint_be.jsonl",           5000),
        ("vlpar_extra.jsonl",            3000),
        ("vlaamse_codex.jsonl",          3000),
    ],
    "AF": [
        ("autshumato_af.jsonl",          5000),
        ("autshumato_af_mono.jsonl",     2000),
        ("govza_cabinet_af.jsonl",       3000),
        ("nchlt_af.jsonl",               3000),
        ("university_repos_nl.jsonl",    2000),   # has AF docs
    ],
    "FY": [
        ("wikidata_expand_fy.jsonl",   10000),  # CC0 — our own synthetic FY; entity names + base sentences
        # frisian_wiki / frisian_wikibooks / frisian_wiktionary all CC-BY-SA — excluded
        # leipzig_fy: 100 sentences only — too small to index
    ],
    "LI": [
        ("limburgish_wikinews.jsonl",   2516),
        # limburgish_wiktionary is CC-BY-SA — excluded
    ],
    "PAP": [
        ("publicatieblad_curacao.jsonl", 5000),
        ("landscourant_aruba.jsonl",      137),
        ("coleccion_aruba.jsonl",         136),
    ],
    "ZEA": [],   # No meaningful PD/CC-BY ZEA text — use glossary only
    "VLS": [],   # No meaningful PD/CC-BY VLS text — use glossary only
    "NDS-NL": [
        ("low_saxon_wiki.jsonl",         2000),  # CC-BY-SA — skip, leaving empty
    ],
    "FRR": [],
    "STQ": [],
    "SRN": [
        ("sranantongo_wiki.jsonl",        500),   # CC-BY-SA — skip
    ],
}

# Override: remove CC-BY-SA entries (belt-and-suspenders check)
_CBYSA_EXCLUDED = {
    "frisian_wiktionary.jsonl",
    "frisian_wikibooks.jsonl",
    "low_saxon_wiki.jsonl",
    "sranantongo_wiki.jsonl",
    "limburgish_wiktionary.jsonl",
}

def _safe_sources(variant: str) -> list[tuple[str, int]]:
    return [
        (fname, cap)
        for fname, cap in RAG_SOURCES.get(variant, [])
        if fname not in _CBYSA_EXCLUDED
    ]


def _load_texts(variant: str) -> Iterator[str]:
    """Yield cleaned text from all PD/CC-BY sources for the variant."""
    for fname, cap in _safe_sources(variant):
        path = CLEANED / fname
        if not path.exists():
            print(f"  [rag] {fname}: not found, skipping")
            continue
        count = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if count >= cap:
                    break
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                meta = rec.get("meta", {})
                # Double-check license
                lic = meta.get("license", "")
                if "SA" in lic.upper():
                    continue
                text = rec.get("text", "").strip()
                if len(text) > 50:
                    yield text
                    count += 1
        print(f"  [rag] {fname}: loaded {count} docs")


class VariantRAG:
    """TF-IDF retrieval index for a single language variant."""

    def __init__(self, variant: str, max_docs: int = 20_000, chunk_size: int = 300):
        self.variant = variant
        self.max_docs = max_docs
        self.chunk_size = chunk_size  # words per chunk
        self._chunks: list[str] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None

    def _chunk_text(self, text: str) -> list[str]:
        words = text.split()
        return [
            " ".join(words[i: i + self.chunk_size])
            for i in range(0, len(words), self.chunk_size)
            if len(words[i: i + self.chunk_size]) > 20
        ]

    def build(self) -> int:
        """Load corpus, chunk, fit TF-IDF. Returns number of chunks indexed."""
        print(f"[rag] Building index for {self.variant}...")
        all_chunks: list[str] = []
        for text in _load_texts(self.variant):
            all_chunks.extend(self._chunk_text(text))
            if len(all_chunks) >= self.max_docs:
                break

        if not all_chunks:
            print(f"[rag] WARNING: no PD/CC-BY text found for {self.variant}")
            self._chunks = []
            return 0

        self._chunks = all_chunks[: self.max_docs]
        self._vectorizer = TfidfVectorizer(
            max_features=30_000,
            ngram_range=(1, 2),
            sublinear_tf=True,
            min_df=2,
        )
        self._matrix = self._vectorizer.fit_transform(self._chunks)
        print(f"[rag] {self.variant}: indexed {len(self._chunks)} chunks")
        return len(self._chunks)

    def retrieve(self, query: str, k: int = 3) -> list[str]:
        """Return top-k passages most similar to query."""
        if self._vectorizer is None or not self._chunks:
            return []
        q_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(q_vec, self._matrix).flatten()
        top_idx = np.argsort(scores)[::-1][:k]
        return [self._chunks[i] for i in top_idx if scores[i] > 0.01]

    @property
    def has_content(self) -> bool:
        return bool(self._chunks)
