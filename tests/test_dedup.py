"""Tests for deduplication."""
from pipeline.dedup.minhash import compute_signature, jaccard_estimate, DedupIndex


def test_identical_documents():
    text = "Dit is een testzin voor deduplicatie."
    s1 = compute_signature("doc1", text)
    s2 = compute_signature("doc2", text)
    assert jaccard_estimate(s1, s2) > 0.95


def test_different_documents():
    s1 = compute_signature("doc1", "De kat zit op de mat.")
    s2 = compute_signature("doc2", "Juridische teksten over het Europees recht.")
    assert jaccard_estimate(s1, s2) < 0.5


def test_dedup_index_removes_duplicate():
    idx = DedupIndex(threshold=0.95)
    text = "Dit is een testzin voor deduplicatie."
    s1 = compute_signature("doc1", text)
    s2 = compute_signature("doc2", text)
    assert idx.add(s1) is True
    assert idx.add(s2) is False   # duplicate — should be rejected


def test_dedup_index_keeps_unique():
    idx = DedupIndex(threshold=0.95)
    s1 = compute_signature("doc1", "De kat zit op de mat.")
    s2 = compute_signature("doc2", "Volledig andere tekst over Europees recht.")
    assert idx.add(s1) is True
    assert idx.add(s2) is True
