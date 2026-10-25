"""
Parse stage dispatcher.

Walks data/raw/, routes each source subdirectory to the correct parser,
and writes Document JSONL to data/parsed/.

Source → parser mapping:
  wikipedia/      → wiki_dump.parse_dir
  rechtspraak/    → sru.parse_rechtspraak_files
  wetten/         → sru.parse_wetten_files
  parliamentary/  → sru.parse_parliamentary_files
  eurlex/         → eurlex_docs.parse_dir
  belgian/        → html_page.parse_belgian_dir
  suriname/       → html_page.parse_suriname_dir
  gutenberg/      → plain_text.parse_gutenberg_dir
  europarl/       → plain_text.parse_europarl_dir
  delpher/        → plain_text.parse_delpher_dir
  rijksoverheid/  → plain_text.parse_rijksoverheid_dir
  codex/          → plain_text.parse_codex_dir
  vlpar/          → plain_text.parse_vlpar_dir
  jrc/            → plain_text.parse_jrc_dir
  dbnl/           → plain_text.parse_dbnl_dir
  staatscourant/  → sru.parse_staatscourant_files
  wikisource/     → wiki_dump.parse_wikisource_dir
  common_voice/   → plain_text.parse_common_voice_dir
  mozilla_dc/     → plain_text.parse_mozilla_dc_dir
  wikibooks/      → wiki_dump.parse_wikibooks_dir
  wikinews/       → wiki_dump.parse_wikinews_dir
  dracor/         → plain_text.parse_dracor_dir
  tatoeba/        → plain_text.parse_tatoeba_dir
  emea/           → plain_text.parse_emea_dir
  wikiquote/      → wiki_dump.parse_wikiquote_dir
  wikivoyage/     → wiki_dump.parse_wikivoyage_dir
  wikiversity/    → wiki_dump.parse_wikiversity_dir
  wiktionary/     → wiki_dump.parse_wiktionary_dir
  frisian_wiki/   → wiki_dump.parse_frisian_wiki_dir
  statenvertaling/→ plain_text.parse_statenvertaling_dir
  openraad/       → plain_text.parse_openraad_dir
  mls/            → plain_text.parse_mls_dir
  opus_extra/        → plain_text.parse_opus_extra_dir
  ud_dutch/          → plain_text.parse_ud_dutch_dir
  openboek/          → plain_text.parse_openboek_dir
  renaissance_poetry/ → plain_text.parse_renaissance_poetry_dir
  limburgish_wiki/   → wiki_dump.parse_limburgish_wiki_dir
  west_flemish_wiki/ → wiki_dump.parse_west_flemish_wiki_dir
  zeelandic_wiki/    → wiki_dump.parse_zeelandic_wiki_dir
  afrikaans_wiki/    → wiki_dump.parse_afrikaans_wiki_dir
  low_saxon_wiki/    → wiki_dump.parse_low_saxon_wiki_dir
  europeana_ner/        → plain_text.parse_europeana_ner_dir
  north_frisian_wiki/   → wiki_dump.parse_north_frisian_wiki_dir
  saterland_frisian_wiki/ → wiki_dump.parse_saterland_frisian_wiki_dir
  papiamentu_wiki/      → wiki_dump.parse_papiamentu_wiki_dir
  sranantongo_wiki/     → wiki_dump.parse_sranantongo_wiki_dir
  brieven_als_buit/     → plain_text.parse_brieven_als_buit_dir
  dcep/                 → plain_text.parse_dcep_dir
  lokale_regelgeving/   → sru.parse_lokale_regelgeving_files
  officiele_bekendmakingen/ → sru.parse_officiele_bekendmakingen_files
  tuchtrecht/           → sru.parse_tuchtrecht_files
  lblod/                → plain_text.parse_lblod_dir
  tractatenblad/        → sru.parse_tractatenblad_files
  advisory_bodies/      → plain_text.parse_advisory_bodies_dir
  oscar/                → plain_text.parse_oscar_dir
  hplt/                 → plain_text.parse_hplt_dir
  rijksmuseum/          → plain_text.parse_rijksmuseum_dir
  openalex/             → plain_text.parse_openalex_dir
  wikidata_nl/          → plain_text.parse_wikidata_nl_dir
  fineweb2_nl/          → plain_text.parse_fineweb2_nl_dir
  c5_dutch/             → plain_text.parse_c5_dutch_dir
  dutch_pd/             → plain_text.parse_dutch_pd_dir
  eu_science_commons/   → plain_text.parse_eu_science_commons_dir
  mc4_nl/               → plain_text.parse_mc4_nl_dir
  rce/                  → plain_text.parse_rce_dir
  rivm/                 → plain_text.parse_rivm_dir
  grondwettelijk_hof/   → plain_text.parse_grondwettelijk_hof_dir
  openaire_nl/          → plain_text.parse_openaire_nl_dir
  dekamer/              → plain_text.parse_dekamer_dir
  bes_bekendmakingen/       → sru.parse_bes_bekendmakingen_files
  rechtspraak_caribisch/    → sru.parse_rechtspraak_caribisch_files
  leipzig/                  → plain_text.parse_leipzig_dir
  landscourant_aruba/       → plain_text.parse_landscourant_aruba_dir
  coleccion_aruba/          → plain_text.parse_coleccion_aruba_dir
  senate_be/                → plain_text.parse_senate_be_dir
  publicatieblad_curacao/   → plain_text.parse_publicatieblad_curacao_dir
  voc_wic_htr/              → plain_text.parse_voc_wic_htr_dir
  parlamint_nl/             → plain_text.parse_parlamint_nl_dir
  nationaal_archief/        → plain_text.parse_nationaal_archief_dir
  etaamb_be/                → plain_text.parse_etaamb_be_dir
  staten_generaal/          → plain_text.parse_staten_generaal_dir
  voxpopuli/                → plain_text.parse_voxpopuli_dir
  pmc_nl/                   → plain_text.parse_pmc_nl_dir
  limburgish_wikisource/    → wiki_dump.parse_limburgish_wikisource_dir
  limburgish_wikibooks/     → wiki_dump.parse_limburgish_wikibooks_dir
  limburgish_wikiquote/     → wiki_dump.parse_limburgish_wikiquote_dir
  limburgish_wikinews/      → wiki_dump.parse_limburgish_wikinews_dir
  frisian_wikibooks/        → wiki_dump.parse_frisian_wikibooks_dir
  afrikaans_wikibooks/      → wiki_dump.parse_afrikaans_wikibooks_dir
  afrikaans_wikiquote/      → wiki_dump.parse_afrikaans_wikiquote_dir
  nchlt_af/                 → plain_text.parse_nchlt_af_dir
  autshumato_af/            → plain_text.parse_autshumato_af_dir
  parlamint_be/             → plain_text.parse_parlamint_be_dir
  tweede_kamer_opendata/    → plain_text.parse_tweede_kamer_opendata_dir
  uvn_podcast/              → plain_text.parse_uvn_podcast_dir
  uvv_podcast/              → plain_text.parse_uvv_podcast_dir
  debalie_commons/          → plain_text.parse_debalie_commons_dir
  kamerstukken/             → sru.parse_kamerstukken_files
  multi_eurlex/             → plain_text.parse_multi_eurlex_dir
  teu_tenders/              → plain_text.parse_teu_tenders_dir
  digibron/                 → plain_text.parse_digibron_dir
  oapen_nl/                 → plain_text.parse_oapen_nl_dir
  naturalis/                → plain_text.parse_naturalis_dir
  open_raadsinformatie/     → plain_text.parse_open_raadsinformatie_dir
  kb_open_kranten/          → plain_text.parse_kb_open_kranten_dir
  juportal_be/              → plain_text.parse_juportal_be_dir
  mensenrechten/            → plain_text.parse_mensenrechten_dir
  woo_documents/            → plain_text.parse_woo_documents_dir
  vlpar_extra/              → plain_text.parse_vlpar_extra_dir
  # New sources (Batch 9)
  woogle_foia/              → plain_text.parse_woogle_foia_dir
  open_data_archieven/      → plain_text.parse_open_data_archieven_dir
  # Audiovisual heritage (Batch 16)
  open_beelden/             → plain_text.parse_open_beelden_dir
  # Administrative courts + WWII audio (Batch 17)
  raad_van_state/           → sru.parse_raad_van_state_files
  radio_oranje/             → plain_text.parse_radio_oranje_dir
  martijn_trilogy/          → plain_text.parse_martijn_trilogy_dir
  herne_manuscripts/        → plain_text.parse_herne_manuscripts_dir
  autshumato_af_mono/       → plain_text.parse_autshumato_af_mono_dir
  nchlt_af_speech/          → plain_text.parse_nchlt_af_speech_dir
  # Spoken Dutch + Afrikaans NER (Batch 19)
  variants_corpus/          → plain_text.parse_variants_corpus_dir
  nchlt_af_ner/             → plain_text.parse_nchlt_af_ner_dir
  # Uitvoeringsinformatie + open leermateriaal + Brussels Parlement (Batch 20)
  puc/                      → plain_text.parse_puc_dir
  wikiwijs/                 → plain_text.parse_wikiwijs_dir
  brussels_parlement/       → plain_text.parse_brussels_parlement_dir
  # Vuk'uzenzele Afrikaans magazine corpus (Batch 21)
  vukuzenzele_af/           → plain_text.parse_vukuzenzele_af_dir
  # University repos NL + African Storybook (Batch 26)
  university_repos_nl/      → plain_text.parse_university_repos_nl_dir
  african_storybook/        → plain_text.parse_african_storybook_dir
  govza_cabinet_af/         → plain_text.parse_govza_cabinet_af_dir
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pipeline.document import write_jsonl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_PARSERS: dict[str, object] = {}  # populated lazily to avoid import errors


def _get_parsers() -> dict:
    from pipeline.parse import sru, wiki_dump, eurlex_docs, html_page, plain_text
    return {
        "wikipedia":       wiki_dump.parse_dir,
        "rechtspraak":     sru.parse_rechtspraak_files,
        "raad_van_state":  sru.parse_raad_van_state_files,
        "wetten":          sru.parse_wetten_files,
        "parliamentary": sru.parse_parliamentary_files,
        "eurlex":        eurlex_docs.parse_dir,
        "belgian":       html_page.parse_belgian_dir,
        "suriname":      html_page.parse_suriname_dir,
        "gutenberg":     plain_text.parse_gutenberg_dir,
        "europarl":      plain_text.parse_europarl_dir,
        "delpher":       plain_text.parse_delpher_dir,
        "rijksoverheid": plain_text.parse_rijksoverheid_dir,
        "codex":         plain_text.parse_codex_dir,
        "vlpar":         plain_text.parse_vlpar_dir,
        "jrc":           plain_text.parse_jrc_dir,
        "dbnl":          plain_text.parse_dbnl_dir,
        "staatscourant": sru.parse_staatscourant_files,
        "wikisource":    wiki_dump.parse_wikisource_dir,
        "common_voice":  plain_text.parse_common_voice_dir,
        "mozilla_dc":    plain_text.parse_mozilla_dc_dir,
        "wikibooks":      wiki_dump.parse_wikibooks_dir,
        "wikinews":       wiki_dump.parse_wikinews_dir,
        "dracor":         plain_text.parse_dracor_dir,
        "tatoeba":        plain_text.parse_tatoeba_dir,
        "emea":           plain_text.parse_emea_dir,
        "wikiquote":      wiki_dump.parse_wikiquote_dir,
        "wikivoyage":     wiki_dump.parse_wikivoyage_dir,
        "wikiversity":    wiki_dump.parse_wikiversity_dir,
        "wiktionary":     wiki_dump.parse_wiktionary_dir,
        "frisian_wiki":   wiki_dump.parse_frisian_wiki_dir,
        "statenvertaling":     plain_text.parse_statenvertaling_dir,
        "openraad":            plain_text.parse_openraad_dir,
        "mls":                 plain_text.parse_mls_dir,
        "opus_extra":          plain_text.parse_opus_extra_dir,
        "ud_dutch":            plain_text.parse_ud_dutch_dir,
        "openboek":            plain_text.parse_openboek_dir,
        "renaissance_poetry":  plain_text.parse_renaissance_poetry_dir,
        "limburgish_wiki":     wiki_dump.parse_limburgish_wiki_dir,
        "west_flemish_wiki":   wiki_dump.parse_west_flemish_wiki_dir,
        "zeelandic_wiki":      wiki_dump.parse_zeelandic_wiki_dir,
        "afrikaans_wiki":      wiki_dump.parse_afrikaans_wiki_dir,
        "low_saxon_wiki":      wiki_dump.parse_low_saxon_wiki_dir,
        "europeana_ner":           plain_text.parse_europeana_ner_dir,
        "north_frisian_wiki":      wiki_dump.parse_north_frisian_wiki_dir,
        "saterland_frisian_wiki":  wiki_dump.parse_saterland_frisian_wiki_dir,
        "papiamentu_wiki":         wiki_dump.parse_papiamentu_wiki_dir,
        "sranantongo_wiki":        wiki_dump.parse_sranantongo_wiki_dir,
        "brieven_als_buit":        plain_text.parse_brieven_als_buit_dir,
        "dcep":                    plain_text.parse_dcep_dir,
        "lokale_regelgeving":      sru.parse_lokale_regelgeving_files,
        "officiele_bekendmakingen": sru.parse_officiele_bekendmakingen_files,
        "tuchtrecht":              sru.parse_tuchtrecht_files,
        "lblod":                   plain_text.parse_lblod_dir,
        "tractatenblad":           sru.parse_tractatenblad_files,
        "advisory_bodies":         plain_text.parse_advisory_bodies_dir,
        "oscar":                   plain_text.parse_oscar_dir,
        "hplt":                    plain_text.parse_hplt_dir,
        "rijksmuseum":             plain_text.parse_rijksmuseum_dir,
        "openalex":                plain_text.parse_openalex_dir,
        "wikidata_nl":             plain_text.parse_wikidata_nl_dir,
        "fineweb2_nl":             plain_text.parse_fineweb2_nl_dir,
        "c5_dutch":                plain_text.parse_c5_dutch_dir,
        "dutch_pd":                plain_text.parse_dutch_pd_dir,
        "eu_science_commons":      plain_text.parse_eu_science_commons_dir,
        "mc4_nl":                  plain_text.parse_mc4_nl_dir,
        "rce":                     plain_text.parse_rce_dir,
        "rivm":                    plain_text.parse_rivm_dir,
        "grondwettelijk_hof":      plain_text.parse_grondwettelijk_hof_dir,
        "openaire_nl":             plain_text.parse_openaire_nl_dir,
        "dekamer":                 plain_text.parse_dekamer_dir,
        # Caribbean & Belgium additions
        "bes_bekendmakingen":       sru.parse_bes_bekendmakingen_files,
        "rechtspraak_caribisch":    sru.parse_rechtspraak_caribisch_files,
        "leipzig":                  plain_text.parse_leipzig_dir,
        "landscourant_aruba":       plain_text.parse_landscourant_aruba_dir,
        "coleccion_aruba":          plain_text.parse_coleccion_aruba_dir,
        "senate_be":                plain_text.parse_senate_be_dir,
        "publicatieblad_curacao":   plain_text.parse_publicatieblad_curacao_dir,
        # Historical & parliamentary additions
        "voc_wic_htr":              plain_text.parse_voc_wic_htr_dir,
        "parlamint_nl":             plain_text.parse_parlamint_nl_dir,
        "nationaal_archief":        plain_text.parse_nationaal_archief_dir,
        "etaamb_be":                plain_text.parse_etaamb_be_dir,
        "staten_generaal":          plain_text.parse_staten_generaal_dir,
        "voxpopuli":                plain_text.parse_voxpopuli_dir,
        "pmc_nl":                   plain_text.parse_pmc_nl_dir,
        # Limburgish/Frisian/Afrikaans wiki expansions (Batch 4)
        "limburgish_wikisource":    wiki_dump.parse_limburgish_wikisource_dir,
        "limburgish_wikibooks":     wiki_dump.parse_limburgish_wikibooks_dir,
        "limburgish_wikiquote":     wiki_dump.parse_limburgish_wikiquote_dir,
        "limburgish_wikinews":      wiki_dump.parse_limburgish_wikinews_dir,
        "frisian_wikibooks":        wiki_dump.parse_frisian_wikibooks_dir,
        "afrikaans_wikibooks":      wiki_dump.parse_afrikaans_wikibooks_dir,
        "afrikaans_wikiquote":      wiki_dump.parse_afrikaans_wikiquote_dir,
        # Afrikaans corpora, Belgian parliament, TK open data (Batch 4)
        "nchlt_af":                 plain_text.parse_nchlt_af_dir,
        "autshumato_af":            plain_text.parse_autshumato_af_dir,
        "parlamint_be":             plain_text.parse_parlamint_be_dir,
        "tweede_kamer_opendata":    plain_text.parse_tweede_kamer_opendata_dir,
        # ToS-compliant replacements for youtube_cc (Batch 5)
        "uvn_podcast":              plain_text.parse_uvn_podcast_dir,
        "uvv_podcast":              plain_text.parse_uvv_podcast_dir,
        "debalie_commons":          plain_text.parse_debalie_commons_dir,
        # Dutch children's encyclopaedia (Batch 6)
        "wikikids":                 plain_text.parse_wikikids_dir,
        # New sources (Batch 7)
        "kamerstukken":             sru.parse_kamerstukken_files,
        "multi_eurlex":             plain_text.parse_multi_eurlex_dir,
        "teu_tenders":              plain_text.parse_teu_tenders_dir,
        "digibron":                 plain_text.parse_digibron_dir,
        "oapen_nl":                 plain_text.parse_oapen_nl_dir,
        "naturalis":                plain_text.parse_naturalis_dir,
        # Municipal council docs, historical newspapers, Belgian courts, Woo (Batch 8)
        "open_raadsinformatie":     plain_text.parse_open_raadsinformatie_dir,
        "kb_open_kranten":          plain_text.parse_kb_open_kranten_dir,
        "juportal_be":              plain_text.parse_juportal_be_dir,
        "mensenrechten":            plain_text.parse_mensenrechten_dir,
        "woo_documents":            plain_text.parse_woo_documents_dir,
        # New sources (Batch 9)
        "vlpar_extra":              plain_text.parse_vlpar_extra_dir,
        "woogle_foia":              plain_text.parse_woogle_foia_dir,
        "open_data_archieven":      plain_text.parse_open_data_archieven_dir,
        # GLAM + RVO + RIVM LCI (Batch 10)
        "amsterdam_museum":         plain_text.parse_amsterdam_museum_dir,
        "rmo":                      plain_text.parse_rmo_dir,
        "rvo_webcontent":           plain_text.parse_rvo_webcontent_dir,
        "rivm_lci":                 plain_text.parse_rivm_lci_dir,
        # Gov research institutes + statistics (Batch 11)
        "wodc":                     plain_text.parse_wodc_dir,
        "scp":                      plain_text.parse_scp_dir,
        "cbs_longreads":            plain_text.parse_cbs_longreads_dir,
        "slo_curriculum":           plain_text.parse_slo_curriculum_dir,
        # Historical VOC (Batch 12)
        "globalise_voc":            plain_text.parse_globalise_voc_dir,
        # EP debates, Wikidata lexemes, Delpher magazines (Batch 13)
        "eupdcorp":                 plain_text.parse_eupdcorp_dir,
        "wikidata_lexemes":         plain_text.parse_wikidata_lexemes_dir,
        "delpher_magazines":        plain_text.parse_delpher_magazines_dir,
        # Policy institutes + ECHR (Batch 14)
        "cpb_pbl":                  plain_text.parse_cpb_pbl_dir,
        "echr_nl":                  plain_text.parse_echr_nl_dir,
        # Social policy (Batch 15)
        "movisie":                  plain_text.parse_movisie_dir,
        # Audiovisual heritage (Batch 16)
        "open_beelden":             plain_text.parse_open_beelden_dir,
        # Administrative courts + WWII audio (Batch 17)
        "raad_van_state":           sru.parse_raad_van_state_files,
        "radio_oranje":             plain_text.parse_radio_oranje_dir,
        "martijn_trilogy":          plain_text.parse_martijn_trilogy_dir,
        "herne_manuscripts":        plain_text.parse_herne_manuscripts_dir,
        "autshumato_af_mono":       plain_text.parse_autshumato_af_mono_dir,
        "nchlt_af_speech":          plain_text.parse_nchlt_af_speech_dir,
        # Spoken Dutch + Afrikaans NER (Batch 19)
        "variants_corpus":          plain_text.parse_variants_corpus_dir,
        "nchlt_af_ner":             plain_text.parse_nchlt_af_ner_dir,
        # Uitvoeringsinformatie + open leermateriaal + Brussels Parlement (Batch 20)
        "puc":                      plain_text.parse_puc_dir,
        "wikiwijs":                 plain_text.parse_wikiwijs_dir,
        "brussels_parlement":       plain_text.parse_brussels_parlement_dir,
        # Vuk'uzenzele Afrikaans magazine corpus (Batch 21)
        "vukuzenzele_af":           plain_text.parse_vukuzenzele_af_dir,
        # DSFSI terminology, AdeKUS, SA gazettes, Meertens dialects (Batch 22)
        "za_mafoko":                plain_text.parse_za_mafoko_dir,
        "adekus_journal":           plain_text.parse_adekus_journal_dir,
        "open_gazettes_za":         plain_text.parse_open_gazettes_za_dir,
        "meertens_dialecten":       plain_text.parse_meertens_dialecten_dir,
        # WEWIS, SUNScholar, TNO, Diamond OA, FAME! Frisian, Groote Oorlog (Batch 23)
        "wewis":                    plain_text.parse_wewis_dir,
        "sunscholar":               plain_text.parse_sunscholar_dir,
        "tno_repository":           plain_text.parse_tno_repository_dir,
        "diamond_oa_nl":            plain_text.parse_diamond_oa_nl_dir,
        "fame_frisian":             plain_text.parse_fame_frisian_dir,
        "groote_oorlog":            plain_text.parse_groote_oorlog_dir,
        # Belgian Journal company bylaws (CC0, Batch 24)
        "belgian_journal":          plain_text.parse_belgian_journal_dir,
        # EU / historical / Afrikaans additions (Batch 25)
        "eurovoc":                  plain_text.parse_eurovoc_dir,
        "bhic_htr":                 plain_text.parse_bhic_htr_dir,
        "af_genre":                 plain_text.parse_af_genre_dir,
        # Missing wiktionaries (Batch 25)
        "afrikaans_wiktionary":     wiki_dump.parse_afrikaans_wiktionary_dir,
        "limburgish_wiktionary":    wiki_dump.parse_limburgish_wiktionary_dir,
        "frisian_wiktionary":       wiki_dump.parse_frisian_wiktionary_dir,
        # University repos + African Storybook (Batch 26)
        "university_repos_nl":      plain_text.parse_university_repos_nl_dir,
        "african_storybook":        plain_text.parse_african_storybook_dir,
        # Batch 28 — NL government bodies + SA Parliament AF
        "pmg_hansard":              plain_text.parse_pmg_hansard_dir,
        "nationale_ombudsman":      plain_text.parse_nationale_ombudsman_dir,
        "ovv":                      plain_text.parse_ovv_dir,
        "acm":                      plain_text.parse_acm_dir,
        # Batch 29 — Large PD corpora + EK proceedings
        "common_corpus_dutch_pd":   plain_text.parse_common_corpus_dutch_pd_dir,
        "internet_archive_nl":      plain_text.parse_internet_archive_nl_dir,
        "eerste_kamer":             sru.parse_eerste_kamer_files,
        "gptnl_public":             plain_text.parse_gptnl_public_dir,
        # Batch 32 — UGent OAI-PMH, govza cabinet AF, tatoeba AF expansion
        "govza_cabinet_af":         plain_text.parse_govza_cabinet_af_dir,
        # Batch 33 — BWB consolidated legislation (PD), tatoeba nds Low Saxon
        "bwb_legislation":          plain_text.parse_bwb_legislation_dir,
        # Batch 34 — DANS Data Station Archaeology + Utrechts Archief
        "dans_archaeology":         plain_text.parse_dans_archaeology_dir,
        "utrechts_archief":         plain_text.parse_utrechts_archief_dir,
        # Batch 34 — DANS SSH + DataverseNL + Global Voices NL
        "dans_ssh":                 plain_text.parse_dans_ssh_dir,
        "dataverse_nl":             plain_text.parse_dataverse_nl_dir,
        "global_voices_nl":         plain_text.parse_global_voices_nl_dir,
        # Batch 35 — Vlaamse Codex Flemish legislation NL-BE
        "vlaamse_codex":            plain_text.parse_vlaamse_codex_dir,
        # Karin Spaink blog — PD by author statement 2026-05-08
        "spaink":                   plain_text.parse_spaink_dir,
    }


def dispatch(
    raw_dir: Path,
    parsed_dir: Path,
    sources: list[str] | None = None,
) -> dict[str, int]:
    """
    Parse all source subdirectories under raw_dir.

    Args:
        raw_dir:    Root raw data directory (contains subdirs per source).
        parsed_dir: Output directory; one .jsonl file per source.
        sources:    Optional list of source names to process; None = all.

    Returns:
        dict mapping source name → documents written.
    """
    parsers = _get_parsers()
    counts: dict[str, int] = {}

    for source_dir in sorted(raw_dir.iterdir()):
        if not source_dir.is_dir():
            continue
        source_name = source_dir.name
        if sources and source_name not in sources:
            continue
        if source_name not in parsers:
            logger.warning("No parser for source '%s' — skipping", source_name)
            continue

        parser_fn = parsers[source_name]
        out_file = parsed_dir / f"{source_name}.jsonl"
        out_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info("=== Parsing %s -> %s ===", source_name, out_file.name)
        try:
            written = write_jsonl(parser_fn(source_dir), out_file)
        except Exception as exc:
            logger.error("Parse failed for %s: %s", source_name, exc, exc_info=True)
            written = 0

        counts[source_name] = written
        logger.info("  %s: %d documents written", source_name, written)

    total = sum(counts.values())
    logger.info("Parse complete. Total documents: %d", total)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Parse raw acquired files into JSONL Documents")
    parser.add_argument("--input",   required=True, type=Path, help="data/raw/")
    parser.add_argument("--output",  required=True, type=Path, help="data/parsed/")
    parser.add_argument("--sources", nargs="+", default=None)
    args = parser.parse_args()
    dispatch(args.input, args.output, sources=args.sources)


if __name__ == "__main__":
    main()
