"""
Corpus statistics reporting.

Counts documents, tokens, and character volume per source/domain/license.
'Tokens' here is a whitespace-split approximation (fast, no tokenizer needed).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.document import read_jsonl


@dataclass
class CorpusStats:
    total_docs: int = 0
    total_tokens: int = 0
    total_chars: int = 0
    by_source: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_domain: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_license: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_country: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, source: str, domain: str, license_: str, country: str, text: str) -> None:
        tokens = len(text.split())
        self.total_docs += 1
        self.total_tokens += tokens
        self.total_chars += len(text)
        self.by_source[source] += tokens
        self.by_domain[domain] += tokens
        self.by_license[license_] += tokens
        self.by_country[country] += tokens

    def report(self) -> str:
        lines = [
            "=" * 56,
            f"  Documents : {self.total_docs:>12,}",
            f"  Tokens    : {self.total_tokens:>12,}",
            f"  Characters: {self.total_chars:>12,}",
            "",
            "  By source (tokens):",
        ]
        for k, v in sorted(self.by_source.items(), key=lambda x: -x[1]):
            lines.append(f"    {k:<30} {v:>12,}")
        lines += ["", "  By domain (tokens):"]
        for k, v in sorted(self.by_domain.items(), key=lambda x: -x[1]):
            lines.append(f"    {k:<30} {v:>12,}")
        lines += ["", "  By license (tokens):"]
        for k, v in sorted(self.by_license.items(), key=lambda x: -x[1]):
            lines.append(f"    {k:<30} {v:>12,}")
        lines += ["", "  By country (tokens):"]
        for k, v in sorted(self.by_country.items(), key=lambda x: -x[1]):
            lines.append(f"    {k:<30} {v:>12,}")
        lines.append("=" * 56)
        return "\n".join(lines)


def compute_stats(input_dir: Path) -> CorpusStats:
    """Compute stats over all .jsonl files in input_dir (recursive)."""
    stats = CorpusStats()
    for jsonl_file in sorted(input_dir.rglob("*.jsonl")):
        for doc in read_jsonl(jsonl_file):
            stats.add(
                source=doc.meta.source,
                domain=doc.meta.domain,
                license_=doc.meta.license,
                country=doc.meta.country,
                text=doc.text,
            )
    return stats


def main() -> None:
    import argparse
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Print corpus statistics")
    parser.add_argument("--input", required=True, type=Path, help="Directory of .jsonl files")
    args = parser.parse_args()
    stats = compute_stats(args.input)
    print(stats.report())


if __name__ == "__main__":
    main()
