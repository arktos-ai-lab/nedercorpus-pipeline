"""Near-duplicate detection using MinHash + LSH."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


def _shingles(text: str, k: int = 5) -> set[str]:
    """Return the set of k-character shingles from text."""
    text = re.sub(r"\s+", " ", text.lower())
    return {text[i : i + k] for i in range(len(text) - k + 1)}


def _hash_shingle(shingle: str, seed: int) -> int:
    digest = hashlib.md5(f"{seed}:{shingle}".encode()).digest()
    return int.from_bytes(digest[:4], "little")


@dataclass
class MinHashSignature:
    doc_id: str
    signature: list[int]


def compute_signature(doc_id: str, text: str, num_hashes: int = 128) -> MinHashSignature:
    """Compute a MinHash signature for a document."""
    shingles = _shingles(text)
    sig = [min(_hash_shingle(s, seed) for s in shingles) if shingles else 0
           for seed in range(num_hashes)]
    return MinHashSignature(doc_id=doc_id, signature=sig)


def jaccard_estimate(a: MinHashSignature, b: MinHashSignature) -> float:
    """Estimate Jaccard similarity from two MinHash signatures."""
    matches = sum(x == y for x, y in zip(a.signature, b.signature))
    return matches / len(a.signature)


@dataclass
class DedupIndex:
    """Simple in-memory deduplication index."""
    threshold: float = 0.95
    _signatures: list[MinHashSignature] = field(default_factory=list)
    _seen_ids: set[str] = field(default_factory=set)

    def is_duplicate(self, sig: MinHashSignature) -> bool:
        for existing in self._signatures:
            if jaccard_estimate(sig, existing) >= self.threshold:
                return True
        return False

    def add(self, sig: MinHashSignature) -> bool:
        """Add a document. Returns True if it was kept, False if it was a duplicate."""
        if self.is_duplicate(sig):
            return False
        self._signatures.append(sig)
        self._seen_ids.add(sig.doc_id)
        return True
