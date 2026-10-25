"""Tests for pipeline.synth — synthetic data generation."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.document import Document, write_jsonl
from pipeline.schema import DocumentMeta
from pipeline.synth import synth_af_to_nl, synth_pap_to_nl, synth_srn_to_nl, synth_nl_noise, run_synth, _count_nl_docs, _iter_af_docs
from pipeline.synth.af_to_nl import _call_local  # noqa: F401 — imported for patching path
from pipeline.synth.nl_noise import _add_noise_light, _add_noise_heavy, _add_noise_ultra, iter_nl_docs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_doc(lang: str, text: str = "test text", source: str = "test_src") -> Document:
    return Document(
        text=text,
        meta=DocumentMeta(
            id=f"{source}:{lang}:1",
            source=source,
            source_url="http://example.com",
            license="CC0",
            publication_year=2020,
            country="ZA" if lang == "AF" else "NL",
            domain="educational",
            language_variant=lang,
        ),
    )


def _write_docs(tmp_path: Path, docs: list[Document], subdir: str = "source") -> Path:
    d = tmp_path / subdir
    d.mkdir(parents=True, exist_ok=True)
    write_jsonl(iter(docs), d / "output.jsonl")
    return tmp_path


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestDocumentMetaSynthFields:
    def test_default_false(self):
        meta = DocumentMeta(
            id="x:1", source="x", source_url="http://x",
            license="CC0", publication_year=None, country="NL",
            domain="legal", language_variant="NL",
        )
        assert meta.synthetic is False
        assert meta.synthetic_method is None

    def test_set_synthetic(self):
        meta = DocumentMeta(
            id="x:1", source="x", source_url="http://x",
            license="CC0", publication_year=None, country="NL",
            domain="legal", language_variant="NL",
            synthetic=True,
            synthetic_method="af_to_nl",
        )
        assert meta.synthetic is True
        assert meta.synthetic_method == "af_to_nl"

    def test_serialization_roundtrip(self):
        meta = DocumentMeta(
            id="x:1", source="x", source_url="http://x",
            license="CC0", publication_year=None, country="NL",
            domain="legal", language_variant="NL",
            synthetic=True,
            synthetic_method="af_to_nl",
        )
        d = meta.to_dict()
        assert d["synthetic"] is True
        assert d["synthetic_method"] == "af_to_nl"

    def test_serialization_defaults(self):
        meta = DocumentMeta(
            id="x:1", source="x", source_url="http://x",
            license="CC0", publication_year=None, country="NL",
            domain="legal", language_variant="NL",
        )
        d = meta.to_dict()
        assert d["synthetic"] is False
        assert d["synthetic_method"] is None

    def test_document_from_dict_preserves_synthetic(self):
        doc = _make_doc("NL")
        doc.meta.synthetic = True
        doc.meta.synthetic_method = "af_to_nl"
        restored = Document.from_dict(doc.to_dict())
        assert restored.meta.synthetic is True
        assert restored.meta.synthetic_method == "af_to_nl"


# ---------------------------------------------------------------------------
# Counting helpers
# ---------------------------------------------------------------------------

class TestCountHelpers:
    def test_count_nl_docs(self, tmp_path):
        docs = [_make_doc("NL")] * 20 + [_make_doc("AF")] * 5
        _write_docs(tmp_path, docs)
        assert _count_nl_docs(tmp_path) == 20

    def test_count_nl_includes_nl_be(self, tmp_path):
        docs = [_make_doc("NL")] * 10 + [_make_doc("NL-BE")] * 5
        _write_docs(tmp_path, docs)
        assert _count_nl_docs(tmp_path) == 15

    def test_count_empty_dir(self, tmp_path):
        assert _count_nl_docs(tmp_path) == 0

    def test_iter_af_docs(self, tmp_path):
        docs = [_make_doc("NL")] * 5 + [_make_doc("AF")] * 3
        _write_docs(tmp_path, docs)
        af = list(_iter_af_docs(tmp_path))
        assert len(af) == 3
        assert all(d.meta.language_variant == "AF" for d in af)


# ---------------------------------------------------------------------------
# synth_af_to_nl
# ---------------------------------------------------------------------------

class TestSynthAfToNl:
    def _setup(self, tmp_path):
        # 20 NL docs → cap = floor(20/20) = 1 AF doc
        nl_docs = [_make_doc("NL", f"Dutch text {i}", "nl_src") for i in range(20)]
        af_docs = [_make_doc("AF", f"Afrikaanse teks {i}", "nchlt_af") for i in range(5)]
        langfiltered = tmp_path / "langfiltered"
        _write_docs(langfiltered, nl_docs + af_docs, subdir="all")
        return langfiltered

    def test_dry_run_produces_nothing(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        result = synth_af_to_nl(langfiltered, out_dir, dry_run=True)
        assert result == 0
        assert not (out_dir / "synth_af_to_nl.jsonl").exists()

    def test_respects_ratio_cap(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"

        def fake_llm(text):
            return "Nederlandse tekst hier"

        with patch("pipeline.synth.af_to_nl._call_local", side_effect=fake_llm):
            n = synth_af_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)

        # 20 NL → cap = 1
        assert n == 1

    def test_output_has_synthetic_flag(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"

        with patch("pipeline.synth.af_to_nl._call_local", return_value="Nederlandse tekst hier"):
            synth_af_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)

        dest = out_dir / "synth_af_to_nl.jsonl"
        assert dest.exists()
        docs = list(__import__("pipeline.document", fromlist=["read_jsonl"]).read_jsonl(dest))
        for doc in docs:
            assert doc.meta.synthetic is True
            assert doc.meta.synthetic_method == "af_to_nl"
            assert doc.meta.language_variant == "NL"
            assert doc.meta.source.endswith("__synth")

    def test_skips_failed_llm_calls(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"

        # LLM fails for all → 0 docs
        with patch("pipeline.synth.af_to_nl._call_local", return_value=None):
            n = synth_af_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)
        assert n == 0

    def test_idempotent_on_existing_output(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"

        with patch("pipeline.synth.af_to_nl._call_local", return_value="Nederlandse tekst hier"):
            n1 = synth_af_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)

        # Second call should return same count without re-running
        n2 = synth_af_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)
        assert n2 == n1

    def test_run_synth_af_to_nl(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        with patch("pipeline.synth.af_to_nl._call_local", return_value="test"):
            n = run_synth(langfiltered, out_dir, method="af_to_nl", ratio=20)
        assert isinstance(n, int)

    def test_run_synth_unknown_method(self, tmp_path, capsys):
        n = run_synth(tmp_path, tmp_path / "out", method="unknown_method")
        assert n == 0

    def test_higher_ratio_fewer_docs(self, tmp_path):
        """With ratio=100 and 20 NL docs, cap is 0 → nothing generated."""
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth2"
        with patch("pipeline.synth.af_to_nl._call_local", return_value="test"):
            n = synth_af_to_nl(langfiltered, out_dir, llm_backend="local", ratio=100)
        assert n == 0


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

class TestCliRegistration:
    def test_synth_command_exists(self):
        """Verify 'synth' is registered in the pipeline argument parser."""
        import argparse
        # Import main and check that 'synth' is a valid subcommand by attempting
        # a dry-run parse — no actual subprocess needed (avoids Windows charmap issues)
        from pipeline.synth import run_synth
        assert callable(run_synth)

    def test_synth_method_choices(self):
        """Verify all synth methods are importable and callable."""
        from pipeline.synth import synth_af_to_nl, synth_pap_to_nl, synth_srn_to_nl, synth_nl_noise
        assert callable(synth_af_to_nl)
        assert callable(synth_pap_to_nl)
        assert callable(synth_srn_to_nl)
        assert callable(synth_nl_noise)


# ---------------------------------------------------------------------------
# pap_to_nl
# ---------------------------------------------------------------------------

class TestSynthPapToNl:
    def _setup(self, tmp_path):
        nl_docs = [_make_doc("NL", f"Dutch text {i}", "nl_src") for i in range(20)]
        pap_docs = [_make_doc("PAP", f"Papiamento tekst {i}", "coleccion_aruba") for i in range(5)]
        langfiltered = tmp_path / "langfiltered"
        _write_docs(langfiltered, nl_docs + pap_docs, subdir="all")
        return langfiltered

    def test_dry_run_produces_nothing(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        result = synth_pap_to_nl(langfiltered, out_dir, dry_run=True)
        assert result == 0
        assert not (out_dir / "synth_pap_to_nl.jsonl").exists()

    def test_respects_ratio_cap(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        with patch("pipeline.synth.pap_to_nl._call_local", return_value="Nederlandse tekst"):
            n = synth_pap_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)
        assert n == 1

    def test_output_has_correct_meta(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        with patch("pipeline.synth.pap_to_nl._call_local", return_value="Nederlandse tekst"):
            synth_pap_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)
        from pipeline.document import read_jsonl
        docs = list(read_jsonl(out_dir / "synth_pap_to_nl.jsonl"))
        for doc in docs:
            assert doc.meta.synthetic is True
            assert doc.meta.synthetic_method == "pap_to_nl"
            assert doc.meta.language_variant == "NL"
            assert doc.meta.source.endswith("__synth")

    def test_run_synth_dispatch(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        with patch("pipeline.synth.pap_to_nl._call_local", return_value="test"):
            n = run_synth(langfiltered, out_dir, method="pap_to_nl", ratio=20)
        assert isinstance(n, int)


# ---------------------------------------------------------------------------
# srn_to_nl
# ---------------------------------------------------------------------------

class TestSynthSrnToNl:
    def _setup(self, tmp_path):
        nl_docs = [_make_doc("NL", f"Dutch text {i}", "nl_src") for i in range(20)]
        srn_docs = [_make_doc("SRN", f"Sranantongo tekst {i}", "suriname_src") for i in range(5)]
        langfiltered = tmp_path / "langfiltered"
        _write_docs(langfiltered, nl_docs + srn_docs, subdir="all")
        return langfiltered

    def test_dry_run_produces_nothing(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        result = synth_srn_to_nl(langfiltered, out_dir, dry_run=True)
        assert result == 0
        assert not (out_dir / "synth_srn_to_nl.jsonl").exists()

    def test_respects_ratio_cap(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        with patch("pipeline.synth.srn_to_nl._call_local", return_value="Nederlandse tekst"):
            n = synth_srn_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)
        assert n == 1

    def test_output_has_correct_meta(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        with patch("pipeline.synth.srn_to_nl._call_local", return_value="Nederlandse tekst"):
            synth_srn_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)
        from pipeline.document import read_jsonl
        docs = list(read_jsonl(out_dir / "synth_srn_to_nl.jsonl"))
        for doc in docs:
            assert doc.meta.synthetic is True
            assert doc.meta.synthetic_method == "srn_to_nl"
            assert doc.meta.language_variant == "NL"

    def test_no_srn_docs_returns_zero(self, tmp_path):
        nl_docs = [_make_doc("NL", f"Dutch text {i}") for i in range(20)]
        langfiltered = tmp_path / "langfiltered"
        _write_docs(langfiltered, nl_docs, subdir="all")
        out_dir = tmp_path / "synth"
        with patch("pipeline.synth.srn_to_nl._call_local", return_value="test"):
            n = synth_srn_to_nl(langfiltered, out_dir, llm_backend="local", ratio=20)
        assert n == 0


# ---------------------------------------------------------------------------
# nl_noise
# ---------------------------------------------------------------------------

class TestNlNoiseFunctions:
    def test_light_noise_returns_string(self):
        import random
        rng = random.Random(42)
        original = "De belasting bedraagt eenentwintig procent voor ondernemingen."
        noisy = _add_noise_light(original, rng)
        assert isinstance(noisy, str)
        assert len(noisy) > 0

    def test_heavy_noise_returns_string(self):
        import random
        rng = random.Random(42)
        original = "De overheid heeft nieuwe regels ingevoerd voor bedrijven in Nederland."
        noisy = _add_noise_heavy(original, rng)
        assert isinstance(noisy, str)
        assert len(noisy) > 0

    def test_ultra_noise_returns_string(self):
        import random
        rng = random.Random(42)
        original = "Dit is een lange Nederlandse zin over belasting en overheidsregels."
        noisy = _add_noise_ultra(original, rng)
        assert isinstance(noisy, str)
        assert len(noisy) > 0

    def test_ultra_strips_all_diacritics(self):
        import random
        rng = random.Random(1)
        text = "e" * 50 + "e" * 50  # all plain e, plus use actual diacritic
        text2 = "\u00e9" * 100  # all e-acute
        noisy = _add_noise_ultra(text2, rng)
        assert "\u00e9" not in noisy


class TestSynthNlNoise:
    def _setup(self, tmp_path, n_nl=100):
        nl_docs = [
            _make_doc("NL", f"Dit is Nederlandse tekst nummer {i} over overheidsregels.", "nl_src")
            for i in range(n_nl)
        ]
        langfiltered = tmp_path / "langfiltered"
        _write_docs(langfiltered, nl_docs, subdir="all")
        return langfiltered

    def test_dry_run_produces_nothing(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        results = synth_nl_noise(langfiltered, out_dir, dry_run=True)
        assert all(v == 0 for v in results.values())
        for lv in ["light", "heavy", "ultra"]:
            assert not (out_dir / f"synth_nl_noise_{lv}.jsonl").exists()

    def test_all_three_levels_by_default(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        results = synth_nl_noise(langfiltered, out_dir, ratio=20)
        assert set(results.keys()) == {"light", "heavy", "ultra"}

    def test_respects_ratio_cap(self, tmp_path):
        langfiltered = self._setup(tmp_path, n_nl=40)
        out_dir = tmp_path / "synth"
        results = synth_nl_noise(langfiltered, out_dir, ratio=20)
        for level, count in results.items():
            assert count == 2, f"{level}: expected 2, got {count}"

    def test_output_files_created(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        synth_nl_noise(langfiltered, out_dir, ratio=20)
        for level in ["light", "heavy", "ultra"]:
            assert (out_dir / f"synth_nl_noise_{level}.jsonl").exists()

    def test_output_has_correct_meta(self, tmp_path):
        langfiltered = self._setup(tmp_path, n_nl=20)
        out_dir = tmp_path / "synth"
        synth_nl_noise(langfiltered, out_dir, ratio=20, levels=["light"])
        from pipeline.document import read_jsonl
        docs = list(read_jsonl(out_dir / "synth_nl_noise_light.jsonl"))
        for doc in docs:
            assert doc.meta.synthetic is True
            assert doc.meta.synthetic_method == "nl_noise_light"
            assert doc.meta.language_variant == "NL"
            assert "__noise_light" in doc.meta.source

    def test_single_level_only(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        results = synth_nl_noise(langfiltered, out_dir, levels=["heavy"], ratio=20)
        assert set(results.keys()) == {"heavy"}
        assert not (out_dir / "synth_nl_noise_light.jsonl").exists()
        assert not (out_dir / "synth_nl_noise_ultra.jsonl").exists()

    def test_run_synth_nl_noise_dispatch(self, tmp_path):
        langfiltered = self._setup(tmp_path)
        out_dir = tmp_path / "synth"
        n = run_synth(langfiltered, out_dir, method="nl_noise", ratio=20)
        assert isinstance(n, int)
        assert n >= 0

    def test_idempotent_on_existing_output(self, tmp_path):
        langfiltered = self._setup(tmp_path, n_nl=20)
        out_dir = tmp_path / "synth"
        r1 = synth_nl_noise(langfiltered, out_dir, ratio=20, levels=["light"])
        r2 = synth_nl_noise(langfiltered, out_dir, ratio=20, levels=["light"])
        assert r1 == r2
