# Synthetic data generation for NederCorpus
from pipeline.synth.af_to_nl import run as synth_af_to_nl
from pipeline.synth.af_to_nl import count_nl_docs as _count_nl_docs
from pipeline.synth.af_to_nl import iter_af_docs as _iter_af_docs
from pipeline.synth.pap_to_nl import run as synth_pap_to_nl
from pipeline.synth.srn_to_nl import run as synth_srn_to_nl
from pipeline.synth.nl_noise import run as synth_nl_noise
from pipeline.synth.wikidata_expand import run as synth_wikidata_expand
from pipeline.synth.legal_templates import run as synth_legal_templates


def run_synth(
    langfiltered_dir,
    output_dir,
    method: str = "af_to_nl",
    llm_backend: str = "local",
    claude_model: str = "claude-haiku-4-5-20251001",
    ratio: int = 20,
    noise_levels: list | None = None,
    dry_run: bool = False,
    **kwargs,
) -> int:
    """
    Dispatch to the requested synthesis method.

    Methods:
      af_to_nl        — Afrikaans → Dutch via LLM
      pap_to_nl       — Papiamento → Dutch via LLM
      srn_to_nl       — Sranantongo → Dutch via LLM
      nl_noise        — Dutch + programmatic noise (light/heavy/ultra)
      wikidata_expand — Wikidata triples → coherent paragraphs (NL/AF/FY/LI/...)
      all             — Run all LLM translation methods + all noise levels
    """
    import logging
    logger = logging.getLogger(__name__)

    if method == "af_to_nl":
        return synth_af_to_nl(
            langfiltered_dir, output_dir,
            llm_backend=llm_backend,
            claude_model=claude_model,
            ratio=ratio,
            dry_run=dry_run,
        )
    if method == "pap_to_nl":
        return synth_pap_to_nl(
            langfiltered_dir, output_dir,
            llm_backend=llm_backend,
            claude_model=claude_model,
            ratio=ratio,
            dry_run=dry_run,
        )
    if method == "srn_to_nl":
        return synth_srn_to_nl(
            langfiltered_dir, output_dir,
            llm_backend=llm_backend,
            claude_model=claude_model,
            ratio=ratio,
            dry_run=dry_run,
        )
    if method == "nl_noise":
        counts = synth_nl_noise(
            langfiltered_dir, output_dir,
            levels=noise_levels,
            ratio=ratio,
            dry_run=dry_run,
        )
        return sum(counts.values())
    if method == "wikidata_expand":
        # languages and limit can be passed via extra kwargs; default to NL+AF+FY+LI
        wd_langs = kwargs.get("languages", ["NL", "AF", "FY", "LI"])
        wd_limit = kwargs.get("limit", 50_000)
        wd_counts = synth_wikidata_expand(
            output_dir=output_dir,
            languages=wd_langs,
            limit=wd_limit,
            dry_run=dry_run,
        )
        return sum(wd_counts.values())
    if method == "legal_templates":
        n_per = kwargs.get("n_per_template", 10_000)
        return synth_legal_templates(
            output_dir=output_dir,
            n_per_template=n_per,
            dry_run=dry_run,
        )
    if method == "all":
        total = 0
        total += synth_af_to_nl(
            langfiltered_dir, output_dir,
            llm_backend=llm_backend,
            claude_model=claude_model,
            ratio=ratio,
            dry_run=dry_run,
        )
        total += synth_pap_to_nl(
            langfiltered_dir, output_dir,
            llm_backend=llm_backend,
            claude_model=claude_model,
            ratio=ratio,
            dry_run=dry_run,
        )
        total += synth_srn_to_nl(
            langfiltered_dir, output_dir,
            llm_backend=llm_backend,
            claude_model=claude_model,
            ratio=ratio,
            dry_run=dry_run,
        )
        counts = synth_nl_noise(
            langfiltered_dir, output_dir,
            levels=noise_levels,
            ratio=ratio,
            dry_run=dry_run,
        )
        total += sum(counts.values())
        return total

    logger.error(
        "[synth] Unknown method: %s (choices: af_to_nl, pap_to_nl, srn_to_nl, nl_noise, wikidata_expand, all)",
        method,
    )
    return 0
