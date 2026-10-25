"""
Compliance report generator.

Produces a structured report covering:
  - GDPR Article 30 (records of processing activities)
  - EU AI Act Article 10 (data governance for training data)
  - EU AI Act Article 13 (transparency)

The report is written to compliance/report.json and compliance/report.md
for human review.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from pipeline.pii.audit import load_summary as load_pii_summary
from pipeline.stats import compute_stats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static compliance metadata (update when pipeline changes)
# ---------------------------------------------------------------------------

PROCESSING_PURPOSE = (
    "Construction of a large-scale, legally reproducible Dutch-language text corpus "
    "for natural language processing research and training of language models."
)

LEGAL_BASIS_GDPR = (
    "Primary basis: Article 6(1)(f) GDPR — legitimate interests (driestapstoets: "
    "(1) scientific research and open NLP infrastructure is a gerechtvaardigd belang; "
    "(2) incidental personal data in officially published documents is noodzakelijk to include "
    "since no text-only alternative exists for Rechtspraak and parliamentary sources; "
    "(3) data subjects are parties to publicly published records, De Rechtspraak applies its "
    "own pseudonymisation before publication, and NederCorpus applies a second PII redaction "
    "layer before distribution). "
    "Alternative basis: Article 6(1)(e) GDPR — public interest task, for parliamentary and "
    "official government publication sources. "
    "For special categories of data (Art. 9 GDPR): Art. 9(2)(j) AVG jo. Art. 44 UAVG — "
    "wetenschappelijk onderzoek; passende waarborgen: two-layer PII redaction, SHA-256 audit "
    "chain, original text discarded, Art. 17 erasure mechanism available. "
    "Rechtspraak.nl documents are sourced from the official data.rechtspraak.nl open-data "
    "endpoint (bulk XML API), not by website scraping; this resolves any Art. 7(5) "
    "Databankenwet exposure. "
    "Full driestapstoets and Art. 14(5)(b) public notice: see LEGAL_BASIS.md in project root."
)

DATA_MINIMIZATION_MEASURES = [
    "Only publicly available, officially published government documents are collected.",
    "Rechtspraak.nl documents are sourced from the officially anonymized publication channel.",
    "A multi-layer PII scrubber (regex + optional NER) removes residual personal identifiers.",
    "BSN numbers are validated via the elfproef before redaction to avoid false positives.",
    "Dates of birth are only redacted in contextual proximity to birth-indicating keywords.",
    "Postcodes are only redacted when appearing in address-like context.",
    "Original (pre-scrub) text is discarded; only redacted text is stored.",
    "No cookies, session tokens, or user-tracking data are collected.",
]

SUBJECT_RIGHTS_MECHANISM = (
    "Individuals who believe their personal data has not been fully anonymized may submit "
    "a removal request referencing the source document identifier. The document will be "
    "removed from the corpus within 30 days via the GDPR Art. 17 erasure mechanism "
    "(pipeline/pii/erasure.py). Requests can be filed via the project issue tracker at "
    "https://github.com/[org]/NederCorpus/issues with label 'gdpr-erasure-request'."
)

EUAIACT_DATA_GOVERNANCE = {
    "article_10_2_a": "Source selection limited to public domain, CC0, CC-BY, and CC-BY-SA licensed materials. "
                      "Design choice: exclude newspaper archives, commercial databases, and all-rights-reserved content.",
    "article_10_2_b": "Data collection via official government APIs, SRU endpoints, and official dumps. "
                      "No unauthorized scraping. All acquisition logged with URL, license, and timestamp.",
    "article_10_2_c": "Processing pipeline: parse → language filter → clean → deduplicate → PII scrub → normalize → export. "
                      "Each stage is reproducible and logged.",
    "article_10_2_d": "Assumptions: officially published government and institutional documents are "
                      "representative of formal written Dutch. Literary texts (Gutenberg) and historical "
                      "newspapers (Delpher) extend coverage to informal and archaic registers. "
                      "Web-crawled data (OSCAR, Common Crawl) is explicitly excluded due to uncontrolled "
                      "copyright provenance and elevated PII risk.",
    "article_10_2_e": "Scale estimated at >10B tokens from legal, parliamentary, literary, historical-press, "
                      "and encyclopedic sources. "
                      "Identified gaps: spoken language, dialectal variation, contemporary informal registers.",
    "article_10_2_f": "Known biases: overrepresentation of formal/legal register; underrepresentation of "
                      "Surinamese Dutch, Belgian Dutch dialectal variety, and informal speech. "
                      "Corpus balance strategy documented in README §9. "
                      "Historical sources (Delpher pre-1879, Gutenberg pre-1923) contain archaic orthography "
                      "mitigated by the normalize stage.",
    "article_10_2_g": "Data gaps: historical text coverage pre-1800 is sparse; Surinamese and Belgian "
                      "government digital archives have incomplete coverage for pre-2000 material.",
    "article_10_3":   (
        "Data governance: license matrix maintained in sources/license_matrix.csv. "
        "PII audit log (JSONL with SHA-256 integrity chain) written per pipeline run. "
        "Deduplication prevents data leakage between train/test splits. "
        "NER model used: reported in pii_summary.json (field: ner_model_name)."
    ),
    "article_10_5":   (
        "Special categories processed: (Art. 9) BSN (national ID), dates of birth, "
        "health/medical data (diagnoses, disability status, psychiatric records), "
        "and (Art. 10) criminal conviction and offence data. All are redacted before "
        "corpus export. Special-category redaction events are logged separately in "
        "the PII audit trail with has_special_category_data=true."
    ),
}


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

def generate(
    corpus_dir: Path,
    audit_dir: Path,
    output_dir: Path,
) -> Path:
    """
    Generate the compliance report.

    Args:
        corpus_dir: Directory containing final JSONL corpus files (for stats).
        audit_dir:  Directory containing PII audit logs.
        output_dir: Where to write report.json and report.md.

    Returns:
        Path to the written report.json.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Corpus statistics
    try:
        stats = compute_stats(corpus_dir)
        stats_dict = {
            "total_documents": stats.total_docs,
            "total_tokens": stats.total_tokens,
            "total_characters": stats.total_chars,
            "by_source": dict(stats.by_source),
            "by_domain": dict(stats.by_domain),
            "by_license": dict(stats.by_license),
            "by_country": dict(stats.by_country),
        }
    except Exception as exc:
        logger.warning("Could not compute corpus stats: %s", exc)
        stats_dict = {}

    # PII audit summary
    pii_summary = load_pii_summary(audit_dir)

    # Include NER model version in report if available
    try:
        from pipeline.pii.ner import get_model_name
        ner_model = get_model_name() or "not loaded"
    except Exception:
        ner_model = "unavailable"

    report = {
        "report_generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "project": "NederCorpus",
        "version": "0.1.0",

        "gdpr": {
            "processing_purpose": PROCESSING_PURPOSE,
            "legal_basis": LEGAL_BASIS_GDPR,
            "data_minimization_measures": DATA_MINIMIZATION_MEASURES,
            "subject_rights_mechanism": SUBJECT_RIGHTS_MECHANISM,
            "article_30_1_e_third_country_transfers": (
                "Surinamese government sources (staatsblad, DNA, rechtspraak SR) are "
                "collected from servers in Suriname, a third country without an EU "
                "adequacy decision (GDPR Art. 45). Transfer basis: Art. 49(1)(g) — "
                "transfer necessary for important reasons of public interest (linguistic "
                "and cultural heritage preservation). The data consists exclusively of "
                "officially published public-domain government documents."
            ),
            "pii_audit_summary": pii_summary,
            "ner_model": ner_model,
        },

        "eu_ai_act": {
            "article_10_data_governance": EUAIACT_DATA_GOVERNANCE,
        },

        "corpus_statistics": stats_dict,

        "data_sources": _load_license_matrix(),
    }

    json_path = output_dir / "report.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Compliance report: %s", json_path)

    md_path = output_dir / "report.md"
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    logger.info("Compliance report (MD): %s", md_path)

    return json_path


def _load_license_matrix() -> list[dict]:
    import csv
    matrix_path = Path(__file__).parent.parent / "sources" / "license_matrix.csv"
    if not matrix_path.exists():
        return []
    with matrix_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _render_markdown(report: dict) -> str:
    g = report["gdpr"]
    ai = report["eu_ai_act"]["article_10_data_governance"]
    stats = report.get("corpus_statistics", {})
    pii = g.get("pii_audit_summary", {})

    lines = [
        "# NederCorpus — Compliance Report",
        f"\nGenerated: {report['report_generated']}",
        "\n---\n",
        "## GDPR Compliance\n",
        f"**Processing purpose:** {g['processing_purpose']}\n",
        f"**Legal basis:** {g['legal_basis']}\n",
        "**Data minimization measures:**\n",
    ]
    for m in g["data_minimization_measures"]:
        lines.append(f"- {m}")
    lines += [
        f"\n**Subject rights mechanism:** {g['subject_rights_mechanism']}\n",
        "### PII Audit Summary\n",
        f"- Documents with redactions: {pii.get('docs_with_redactions', 'N/A')}",
        f"- Total redactions made: {pii.get('total_redactions', 'N/A')}",
        f"- Documents with special-category data (Art. 9): {pii.get('docs_with_special_category_data', 'N/A')}",
        "\n---\n",
        "## EU AI Act Compliance (Article 10 — Data Governance)\n",
    ]
    for key, val in ai.items():
        article = key.replace("_", " ").upper()
        lines.append(f"**{article}:** {val}\n")
    lines += [
        "\n---\n",
        "## Corpus Statistics\n",
        f"- Total documents: {stats.get('total_documents', 'N/A'):,}" if isinstance(stats.get('total_documents'), int) else f"- Total documents: {stats.get('total_documents', 'N/A')}",
        f"- Total tokens: {stats.get('total_tokens', 'N/A'):,}" if isinstance(stats.get('total_tokens'), int) else f"- Total tokens: {stats.get('total_tokens', 'N/A')}",
        "\n### By License\n",
    ]
    for lic, tok in sorted(stats.get("by_license", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {lic}: {tok:,} tokens")
    lines += [
        "\n### By Domain\n",
    ]
    for dom, tok in sorted(stats.get("by_domain", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {dom}: {tok:,} tokens")
    lines.append("\n---\n*Report auto-generated by pipeline/compliance.py*\n")
    return "\n".join(lines)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate compliance report")
    parser.add_argument("--corpus",  required=True, type=Path)
    parser.add_argument("--audit",   required=True, type=Path)
    parser.add_argument("--output",  default="compliance", type=Path)
    args = parser.parse_args()
    generate(args.corpus, args.audit, args.output)


if __name__ == "__main__":
    main()
