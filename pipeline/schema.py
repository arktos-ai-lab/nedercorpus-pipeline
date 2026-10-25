"""Metadata schema for every document in the corpus."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# License types — every document must carry one of these values.
# The license is serialised into every JSONL record so provenance is
# unambiguously traceable at the document level, independent of any external
# catalogue.
#
#   PD        — Public Domain (no known copyright, or explicit PD dedication)
#   CC0       — Creative Commons Zero (explicit public-domain dedication)
#   CC-BY     — Creative Commons Attribution (any version: 1.0–4.0)
#   CC-BY-SA  — Creative Commons Attribution-ShareAlike (any version)
#   ODC-By    — Open Data Commons Attribution (web corpora: FineWeb-2, mC4)
#   unknown   — license could not be determined at parse time (use sparingly)
# ---------------------------------------------------------------------------
LicenseType = Literal["PD", "CC0", "CC-BY", "CC-BY-SA", "ODC-By", "unknown"]

# Country of origin / jurisdiction of the text (ISO 3166-1 alpha-2)
Country = Literal[
    "NL",   # Netherlands
    "BE",   # Belgium
    "EU",   # European Union (supranational)
    "SR",   # Suriname
    "AW",   # Aruba
    "CW",   # Curaçao
    "DE",   # Germany (North Frisian, Saterland Frisian)
    "ZA",   # South Africa (Afrikaans)
]

# Language variant (IETF-style tags used throughout the corpus)
LanguageVariant = Literal[
    "NL",       # Standard Dutch (Netherlands)
    "NL-BE",    # Belgian Dutch / Flemish
    "FY",       # West Frisian
    "LI",       # Limburgish
    "VLS",      # West Flemish
    "ZEA",      # Zeelandic
    "NDS-NL",   # Dutch Low Saxon
    "AF",       # Afrikaans
    "FRR",      # North Frisian
    "STQ",      # Saterland Frisian
    "PAP",      # Papiamentu/Papiamento (Curaçao/Aruba)
    "SRN",      # Sranan Tongo
    "SR",       # Surinamese Dutch (legacy — prefer NL + country=SR)
    "BE",       # Belgian Dutch (legacy — prefer NL-BE)
]

Domain = Literal[
    "legal", "parliamentary", "encyclopedic", "literary", "web",
    "educational", "academic", "speech", "cultural", "financial",
    "technical", "software", "religious", "news", "historical",
]


@dataclass
class ProvenanceEntry:
    """An atomic unit of provenance/evidence for a claim or transformation."""
    entry_type: str                     # "statistical_anchor", "linguistic_rule", "tos_snapshot"
    evidence_pointer: str               # DOI, TableID, RuleID (e.g. "GTRP:H001"), or URL
    method: str                         # "deterministic", "rule_based", "llm_refinement"
    text_segment: str | None = None      # The snippet this entry validates
    timestamp: str = ""                 # ISO 8601
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "entry_type": self.entry_type,
            "evidence_pointer": self.evidence_pointer,
            "method": self.method,
            "text_segment": self.text_segment,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProvenanceEntry":
        return cls(**d)


@dataclass
class DocumentMeta:
    id: str
    source: str                          # e.g. "rechtspraak", "wetten", "eurlex"
    source_url: str                      # canonical URL or dump reference
    license: LicenseType                 # ALWAYS set — serialised into every record
    publication_year: int | None
    country: Country | None              # None only when genuinely indeterminate
    domain: Domain
    language_variant: LanguageVariant
    historical_flag: bool = False
    normalized_version: bool = False
    acquisition_date: str = ""           # ISO 8601 date string
    synthetic: bool = False              # True for LLM-generated/transformed documents
    synthetic_method: str | None = None  # e.g. "af_to_nl", "hist_normalize"
    # ---------------------------------------------------------------------------
    # The Ledger: A machine-readable audit trail for executable provenance.
    # ---------------------------------------------------------------------------
    ledger: list[ProvenanceEntry] = field(default_factory=list)

    # ---------------------------------------------------------------------------
    # Ethical metadata — for transparency and downstream filtering.
    # ---------------------------------------------------------------------------
    language_period: str | None = None
    content_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "source_url": self.source_url,
            "license": self.license,
            "publication_year": self.publication_year,
            "country": self.country,
            "domain": self.domain,
            "language_variant": self.language_variant,
            "historical_flag": self.historical_flag,
            "normalized_version": self.normalized_version,
            "acquisition_date": self.acquisition_date,
            "synthetic": self.synthetic,
            "synthetic_method": self.synthetic_method,
            "ledger": [e.to_dict() for e in self.ledger],
            "language_period": self.language_period,
            "content_warnings": self.content_warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentMeta":
        # Handle the ledger nesting
        ledger_data = d.pop("ledger", [])
        ledger = [ProvenanceEntry.from_dict(e) for e in ledger_data]
        return cls(ledger=ledger, **d)
