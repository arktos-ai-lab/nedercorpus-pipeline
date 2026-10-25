"""
Scalable MinHash LSH deduplication using band decomposition.

Complexity: O(n) insertion and lookup (approximate), O(1) per band bucket.
For n documents, this is dramatically faster than the O(n²) pairwise approach.

Algorithm:
  1. Compute MinHash signature of k hash functions per document.
  2. Split signature into b bands of r rows each (k = b * r).
  3. For each band, hash the band's rows into a bucket.
  4. Two documents are candidate duplicates if they share ANY bucket.
  5. Verify candidates with exact Jaccard estimate.

Tuning:
  - Higher b → more candidates (higher recall, lower precision)
  - Higher r → fewer candidates (higher precision, lower recall)
  - Threshold ≈ (1/b)^(1/r)

Memory layout:
  Signatures are stored as numpy uint64 arrays (1 KB each) rather than
  Python lists of Python ints (~4 KB each after heap fragmentation).
  This 4× reduction prevents OOM during large-corpus rebuild phases.
"""
from __future__ import annotations

import hashlib
import re
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np


# ---------------------------------------------------------------------------
# Shingling
# ---------------------------------------------------------------------------

# Truncate texts longer than this to head + tail before shingling.
# Prevents unbounded (128, N) temporary arrays in minhash_signature.
_MAX_TEXT_CHARS = 100_000

# Batch size for the vectorised MinHash inner loop.
# Each batch allocates (128, BATCH) uint64 = BATCH × 1 KB.
# Keep this small to avoid triggering WSL virtual-memory fragmentation
# after a large index rebuild (~20 GB heap) even when GBs of RAM are free.
_MINHASH_BATCH = 1_000

def shingles(text: str, k: int = 5) -> list[str]:
    text = re.sub(r"\s+", " ", text.lower())
    return [text[i : i + k] for i in range(max(0, len(text) - k + 1))]


# ---------------------------------------------------------------------------
# MinHash  (vectorised via NumPy — ~100× faster than pure-Python SHA1 loop)
# ---------------------------------------------------------------------------

def minhash_signature(text: str, num_hashes: int = 128) -> np.ndarray:
    """
    Compute a MinHash signature of length num_hashes.

    Strategy:
      1. Truncate very long texts to _MAX_TEXT_CHARS (head + tail).
      2. Extract unique 5-char shingles (set → no duplicates).
      3. Hash each unique shingle with SHA-1 ONCE → uint64 base values.
      4. For each of the num_hashes seeds, mix base values with a cheap
         pairwise-independent family and take the minimum — all vectorised
         via NumPy broadcasting (no Python loop over shingles).

    Returns a numpy array of shape (num_hashes,) dtype uint64.
    Storing as ndarray instead of list[int] uses ~4× less memory.

    Complexity: O(|unique_shingles|) SHA-1 calls + O(num_hashes × |shingles|)
    NumPy integer ops — roughly 100× faster than the naive per-seed SHA-1 loop.
    """
    text = re.sub(r"\s+", " ", text.lower())
    # Truncate: keep first half + last half to preserve both ends
    if len(text) > _MAX_TEXT_CHARS:
        half = _MAX_TEXT_CHARS // 2
        text = text[:half] + text[-half:]

    unique = list({text[i : i + 5] for i in range(max(0, len(text) - 4))})
    if not unique:
        return np.zeros(num_hashes, dtype=np.uint64)

    # Pairwise-independent MinHash family: h_i(s) = (a_s + i * b_s) mod 2^64
    # Per shingle: extract two uint64 values (a, b) from SHA-1's 20 bytes.
    # For uniform (a, b), P(min_A h_i = min_B h_i) = Jaccard(A, B) exactly.
    # NumPy uint64 arithmetic wraps mod 2^64 automatically.
    n = len(unique)
    a_vals = np.empty(n, dtype=np.uint64)
    b_vals = np.empty(n, dtype=np.uint64)
    for j, s in enumerate(unique):
        digest = hashlib.sha1(s.encode()).digest()
        a_vals[j] = int.from_bytes(digest[0:8], "little")
        b_vals[j] = int.from_bytes(digest[8:16], "little")

    seeds = np.arange(num_hashes, dtype=np.uint64)  # shape: (num_hashes,)

    # Process shingles in fixed-size batches to avoid a large (128, n)
    # temporary array.  After an 8M+ doc index rebuild, the heap is heavily
    # fragmented; even a 10 MB contiguous allocation can fail.  Each batch
    # is at most (128, _MINHASH_BATCH) = ~1 MB — safe under fragmentation.
    result = np.full(num_hashes, np.iinfo(np.uint64).max, dtype=np.uint64)
    for start in range(0, n, _MINHASH_BATCH):
        end = min(start + _MINHASH_BATCH, n)
        batch_a = a_vals[start:end]
        batch_b = b_vals[start:end]
        batch_h = batch_a[np.newaxis, :] + seeds[:, np.newaxis] * batch_b[np.newaxis, :]
        np.minimum(result, batch_h.min(axis=1), out=result)
    return result


def jaccard_from_signatures(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sum(a == b)) / len(a)


# ---------------------------------------------------------------------------
# LSH Index
# ---------------------------------------------------------------------------

@dataclass
class LSHIndex:
    """
    MinHash LSH index with band decomposition.

    Default parameters target ~0.85 Jaccard threshold.
    Adjust bands/rows to tune precision/recall tradeoff.

    Signatures are stored as numpy uint64 arrays (1 KB each) to minimise
    Python heap fragmentation at scale.
    """
    num_hashes: int = 128
    bands: int = 16          # b
    rows: int = 8            # r  (num_hashes = bands * rows)
    threshold: float = 0.85

    # bucket_key -> list of doc_ids.  Only doc_ids are stored here;
    # signatures are looked up from _signatures for Jaccard verification.
    # Storing (doc_id, sig) tuples instead would waste ~9 GB of RAM at 8M
    # docs (128M tuples × 72 bytes each) because the sig is never read back
    # from _buckets — candidates() only needs the doc_id to do the lookup.
    _buckets: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # doc_id -> signature ndarray  (for final Jaccard verification)
    _signatures: dict[str, np.ndarray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.bands * self.rows == self.num_hashes, (
            f"bands ({self.bands}) * rows ({self.rows}) must equal num_hashes ({self.num_hashes})"
        )
        self._buckets = defaultdict(list)
        self._signatures = {}

    def _band_keys(self, sig: np.ndarray) -> Iterator[str]:
        for band_idx in range(self.bands):
            band = sig[band_idx * self.rows : (band_idx + 1) * self.rows]
            # Pack band values as bytes for fast, collision-resistant hashing
            band_bytes = struct.pack(f"{self.rows}Q", *band.tolist())
            band_hash = hashlib.md5(
                band_idx.to_bytes(2, "little") + band_bytes
            ).hexdigest()
            yield f"b{band_idx}:{band_hash}"

    def candidates(self, sig: np.ndarray) -> set[str]:
        """Return all doc IDs that share at least one band bucket with sig."""
        result: set[str] = set()
        for key in self._band_keys(sig):
            result.update(self._buckets.get(key, []))
        return result

    def is_duplicate(self, doc_id: str, sig: np.ndarray) -> bool:
        """Return True if any existing document has Jaccard >= threshold."""
        for cand_id in self.candidates(sig):
            cand_sig = self._signatures[cand_id]
            if jaccard_from_signatures(sig, cand_sig) >= self.threshold:
                return True
        return False

    def add(self, doc_id: str, text: str) -> bool:
        """
        Add a document. Returns True if kept, False if near-duplicate.
        Computes MinHash signature internally.
        """
        sig = minhash_signature(text, self.num_hashes)
        if self.is_duplicate(doc_id, sig):
            return False
        for key in self._band_keys(sig):
            self._buckets[key].append(doc_id)
        self._signatures[doc_id] = sig
        return True

    def add_with_signature(self, doc_id: str, sig: np.ndarray) -> bool:
        """Add a document by pre-computed signature (numpy array)."""
        if self.is_duplicate(doc_id, sig):
            return False
        for key in self._band_keys(sig):
            self._buckets[key].append(doc_id)
        self._signatures[doc_id] = sig
        return True

    def force_add(self, doc_id: str, sig: np.ndarray) -> None:
        """Add without duplicate check — for rebuilding index from already-clean data."""
        for key in self._band_keys(sig):
            self._buckets[key].append(doc_id)
        self._signatures[doc_id] = sig

    @property
    def size(self) -> int:
        return len(self._signatures)
