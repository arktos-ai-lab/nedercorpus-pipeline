"""
NederCorpus pipeline orchestrator.

Full end-to-end pipeline:

  acquire     Download raw data from all sources
  asr         Transcribe podcast audio files to .txt (faster-whisper, between acquire and parse)
  parse       Convert raw files → Document JSONL
  langfilter  Drop non-Dutch documents
  clean       Strip HTML, normalize encoding, quality filters
  dedup       MinHash LSH near-duplicate removal
  pii         Redact personal data (GDPR + EU AI Act compliant)
  normalize   Historical Dutch spelling normalization
  export      Write final Parquet shards
  stats       Print corpus statistics
  compliance  Generate GDPR / EU AI Act compliance report
  run-all     Run every stage in order

Usage:
  python -m pipeline run-all --output data/
  python -m pipeline acquire --sources wikipedia rechtspraak --output data/raw/
  python -m pipeline parse   --input data/raw/ --output data/parsed/
  python -m pipeline pii     --input data/deduped/ --output data/scrubbed/ --audit audit/
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load key=value pairs from a .env file into os.environ (stdlib only)."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:   # don't overwrite existing env vars
                os.environ[key] = value


_load_dotenv()

logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Stage: acquire
# ---------------------------------------------------------------------------

def cmd_acquire(args: argparse.Namespace) -> None:
    from pipeline.acquire import (
        wikipedia, rechtspraak, eurlex, wetten, parliamentary,
        belgian, suriname, gutenberg, europarl, delpher, rijksoverheid,
        codex, vlpar, jrc, dbnl, staatscourant, wikisource, common_voice,
        mozilla_dc,
        wikibooks, wikinews, dracor, tatoeba, emea,
        wikiquote, wikivoyage, wikiversity, wiktionary, frisian_wiki,
        statenvertaling, openraad, mls,
        opus_extra, ud_dutch, openboek, renaissance_poetry,
        limburgish_wiki, west_flemish_wiki, zeelandic_wiki, afrikaans_wiki,
        low_saxon_wiki, europeana_ner,
        north_frisian_wiki, saterland_frisian_wiki, papiamentu_wiki,
        sranantongo_wiki, brieven_als_buit, dcep,
        lokale_regelgeving, officiele_bekendmakingen, tuchtrecht, lblod,
        tractatenblad, advisory_bodies, oscar, hplt, rijksmuseum,
        openalex, wikidata_nl,
        fineweb2_nl, c5_dutch, dutch_pd, eu_science_commons, mc4_nl,
        rce, rivm, grondwettelijk_hof,
        openaire_nl, dekamer,
        # Caribbean & Belgium (Batch 1)
        bes_bekendmakingen, rechtspraak_caribisch,
        landscourant_aruba, coleccion_aruba, publicatieblad_curacao,
        senate_be, leipzig,
        # Historical & parliamentary (Batch 2)
        voc_wic_htr, parlamint_nl, nationaal_archief, etaamb_be,
        # Additional parliamentary & academic (Batch 3)
        staten_generaal, voxpopuli, pmc_nl,
        # Limburgish/Frisian/Afrikaans wiki expansions + Afrikaans corpora + BE parliament + TK open data (Batch 4)
        limburgish_wikisource, limburgish_wikibooks, limburgish_wikiquote, limburgish_wikinews,
        frisian_wikibooks, afrikaans_wikibooks, afrikaans_wikiquote,
        nchlt_af, autshumato_af, parlamint_be, tweede_kamer_opendata,
        # ToS-compliant replacements for youtube_cc (Batch 5)
        uvn_podcast, uvv_podcast, debalie_commons,
        # Dutch children's encyclopaedia (Batch 6)
        wikikids,
        # New sources (Batch 7)
        kamerstukken, multi_eurlex, teu_tenders,
        digibron, oapen_nl, naturalis,
        # New sources (Batch 8)
        open_raadsinformatie, kb_open_kranten, juportal_be, woo_documents,
        # New sources (Batch 9)
        vlpar_extra, woogle_foia, open_data_archieven,
        # GLAM + RVO + RIVM LCI (Batch 10)
        amsterdam_museum, rmo, rvo_webcontent, rivm_lci,
        # Gov research institutes + statistics (Batch 11)
        wodc, scp, cbs_longreads, slo_curriculum,
        # Historical VOC (Batch 12)
        globalise_voc,
        # EP debates, Wikidata lexemes, Delpher magazines (Batch 13)
        eupdcorp, wikidata_lexemes, delpher_magazines,
        # Policy institutes + ECHR (Batch 14)
        cpb_pbl, echr_nl,
        # Social policy (Batch 15)
        movisie,
        # Audiovisual heritage (Batch 16)
        open_beelden,
        # Administrative courts + WWII audio (Batch 17)
        raad_van_state, radio_oranje,
        # Middle Dutch manuscripts + Afrikaans corpora (Batch 18)
        martijn_trilogy, herne_manuscripts, autshumato_af_mono, nchlt_af_speech,
        # Spoken Dutch + Afrikaans NER (Batch 19)
        variants_corpus, nchlt_af_ner,
        # Uitvoeringsinformatie + open leermateriaal + Brussels Parlement (Batch 20)
        puc, wikiwijs, brussels_parlement,
        # Vuk'uzenzele Afrikaans magazine corpus (Batch 21)
        vukuzenzele_af,
        # DSFSI terminology, AdeKUS, SA gazettes, Meertens dialects (Batch 22)
        za_mafoko, adekus_journal, open_gazettes_za, meertens_dialecten,
        # WEWIS, SUNScholar, TNO, Diamond OA, FAME! Frisian, Groote Oorlog (Batch 23)
        wewis, sunscholar, tno_repository, diamond_oa_nl, fame_frisian, groote_oorlog,  # noqa: F401
        # Belgian Journal company bylaws (Batch 24)
        belgian_journal,
        # EuroVoc + BHIC HTR + SADiLaR AF Genre + wiktionaries (Batch 25)
        eurovoc, bhic_htr, af_genre, afrikaans_wiktionary, limburgish_wiktionary,
        frisian_wiktionary,
        # University repos NL + African Storybook (Batch 26)
        university_repos_nl, african_storybook,
        # Mensenrechten equality body decisions + expanded ORI (Batch 27)
        mensenrechten,
        # NL government bodies + SA Parliament Afrikaans (Batch 28)
        pmg_hansard, nationale_ombudsman, ovv, acm,
        # Large PD corpora + EK proceedings (Batch 29)
        common_corpus_dutch_pd, internet_archive_nl, eerste_kamer,
        gptnl_public,
        # UGent OAI-PMH + govza cabinet AF (Batch 32)
        govza_cabinet_af,
        # BWB consolidated legislation + tatoeba nds Low Saxon (Batch 33)
        bwb_legislation,
        # DANS Data Station Archaeology + Utrechts Archief (Batch 34)
        dans_archaeology, utrechts_archief,
        # DANS SSH + DataverseNL + Global Voices NL (Batch 34)
        dans_ssh, dataverse_nl, global_voices_nl,
        # Vlaamse Codex (Batch 35)
        vlaamse_codex,
        # Karin Spaink blog PD (Batch 36)
        spaink,
    )

    sources = args.sources
    out = Path(args.output)

    dispatch = {
        "wikipedia":     (wikipedia.download,      out / "wikipedia"),
        "rechtspraak":   (rechtspraak.download,    out / "rechtspraak"),
        "eurlex":        (eurlex.download,          out / "eurlex"),
        "wetten":        (wetten.download,          out / "wetten"),
        "parliamentary": (parliamentary.download,   out / "parliamentary"),
        "belgian":       (belgian.download,         out / "belgian"),
        "suriname":      (suriname.download,        out / "suriname"),
        "gutenberg":     (gutenberg.download,       out / "gutenberg"),
        "europarl":      (europarl.download,        out / "europarl"),
        "delpher":       (delpher.download,         out / "delpher"),
        "rijksoverheid": (rijksoverheid.download,   out / "rijksoverheid"),
        "codex":         (codex.download,           out / "codex"),
        "vlpar":         (vlpar.download,           out / "vlpar"),
        "jrc":           (jrc.download,             out / "jrc"),
        "dbnl":          (dbnl.download,            out / "dbnl"),
        "staatscourant": (staatscourant.download,   out / "staatscourant"),
        "wikisource":    (wikisource.download,      out / "wikisource"),
        "common_voice":  (common_voice.download,    out / "common_voice"),
        "mozilla_dc":    (mozilla_dc.download,      out / "mozilla_dc"),
        "wikibooks":      (wikibooks.download,      out / "wikibooks"),
        "wikinews":       (wikinews.download,       out / "wikinews"),
        "dracor":         (dracor.download,         out / "dracor"),
        "tatoeba":        (tatoeba.download,        out / "tatoeba"),
        "emea":           (emea.download,           out / "emea"),
        "wikiquote":      (wikiquote.download,      out / "wikiquote"),
        "wikivoyage":     (wikivoyage.download,     out / "wikivoyage"),
        "wikiversity":    (wikiversity.download,    out / "wikiversity"),
        "wiktionary":     (wiktionary.download,    out / "wiktionary"),
        "frisian_wiki":   (frisian_wiki.download,  out / "frisian_wiki"),
        "statenvertaling":   (statenvertaling.download,    out / "statenvertaling"),
        "openraad":          (openraad.download,           out / "openraad"),
        "mls":               (mls.download,               out / "mls"),
        "opus_extra":          (opus_extra.download,         out / "opus_extra"),
        "ud_dutch":            (ud_dutch.download,           out / "ud_dutch"),
        "openboek":            (openboek.download,           out / "openboek"),
        "renaissance_poetry":  (renaissance_poetry.download, out / "renaissance_poetry"),
        "limburgish_wiki":        (limburgish_wiki.download,       out / "limburgish_wiki"),
        "west_flemish_wiki":      (west_flemish_wiki.download,     out / "west_flemish_wiki"),
        "zeelandic_wiki":         (zeelandic_wiki.download,        out / "zeelandic_wiki"),
        "afrikaans_wiki":         (afrikaans_wiki.download,        out / "afrikaans_wiki"),
        "low_saxon_wiki":         (low_saxon_wiki.download,        out / "low_saxon_wiki"),
        "europeana_ner":          (europeana_ner.download,         out / "europeana_ner"),
        "north_frisian_wiki":     (north_frisian_wiki.download,    out / "north_frisian_wiki"),
        "saterland_frisian_wiki": (saterland_frisian_wiki.download,out / "saterland_frisian_wiki"),
        "papiamentu_wiki":        (papiamentu_wiki.download,       out / "papiamentu_wiki"),
        "sranantongo_wiki":       (sranantongo_wiki.download,      out / "sranantongo_wiki"),
        "brieven_als_buit":       (brieven_als_buit.download,      out / "brieven_als_buit"),
        "dcep":                   (dcep.download,                  out / "dcep"),
        "lokale_regelgeving":     (lokale_regelgeving.download,    out / "lokale_regelgeving"),
        "officiele_bekendmakingen": (officiele_bekendmakingen.download, out / "officiele_bekendmakingen"),
        "tuchtrecht":             (tuchtrecht.download,            out / "tuchtrecht"),
        "lblod":                  (lblod.download,                 out / "lblod"),
        "tractatenblad":          (tractatenblad.download,          out / "tractatenblad"),
        "advisory_bodies":        (advisory_bodies.download,        out / "advisory_bodies"),
        "oscar":                  (oscar.download,                  out / "oscar"),
        "hplt":                   (hplt.download,                   out / "hplt"),
        "rijksmuseum":            (rijksmuseum.download,            out / "rijksmuseum"),
        "openalex":               (openalex.download,               out / "openalex"),
        "wikidata_nl":            (wikidata_nl.download,            out / "wikidata_nl"),
        "fineweb2_nl":            (fineweb2_nl.download,            out / "fineweb2_nl"),
        "c5_dutch":               (c5_dutch.download,               out / "c5_dutch"),
        "dutch_pd":               (dutch_pd.download,               out / "dutch_pd"),
        "eu_science_commons":     (eu_science_commons.download,     out / "eu_science_commons"),
        "mc4_nl":                 (mc4_nl.download,                 out / "mc4_nl"),
        "rce":                    (rce.download,                    out / "rce"),
        "rivm":                   (rivm.download,                   out / "rivm"),
        "grondwettelijk_hof":     (grondwettelijk_hof.download,     out / "grondwettelijk_hof"),
        "openaire_nl":            (openaire_nl.download,            out / "openaire_nl"),
        "dekamer":                (dekamer.download,                out / "dekamer"),
        # Caribbean & Belgium (Batch 1)
        "bes_bekendmakingen":     (bes_bekendmakingen.download,     out / "bes_bekendmakingen"),
        "rechtspraak_caribisch":  (rechtspraak_caribisch.download,  out / "rechtspraak_caribisch"),
        "landscourant_aruba":     (landscourant_aruba.download,     out / "landscourant_aruba"),
        "coleccion_aruba":        (coleccion_aruba.download,        out / "coleccion_aruba"),
        "publicatieblad_curacao": (publicatieblad_curacao.download, out / "publicatieblad_curacao"),
        "senate_be":              (senate_be.download,              out / "senate_be"),
        "leipzig":                (leipzig.download,                out / "leipzig"),
        # Historical & parliamentary (Batch 2)
        "voc_wic_htr":            (voc_wic_htr.download,            out / "voc_wic_htr"),
        "parlamint_nl":           (parlamint_nl.download,           out / "parlamint_nl"),
        "nationaal_archief":      (nationaal_archief.download,      out / "nationaal_archief"),
        "etaamb_be":              (etaamb_be.download,              out / "etaamb_be"),
        # Additional parliamentary & academic (Batch 3)
        "staten_generaal":        (staten_generaal.download,        out / "staten_generaal"),
        "voxpopuli":              (voxpopuli.download,              out / "voxpopuli"),
        "pmc_nl":                 (pmc_nl.download,                 out / "pmc_nl"),
        # Limburgish/Frisian/Afrikaans wiki expansions + Afrikaans corpora + BE parliament + TK open data (Batch 4)
        "limburgish_wikisource":  (limburgish_wikisource.download,  out / "limburgish_wikisource"),
        "limburgish_wikibooks":   (limburgish_wikibooks.download,   out / "limburgish_wikibooks"),
        "limburgish_wikiquote":   (limburgish_wikiquote.download,   out / "limburgish_wikiquote"),
        "limburgish_wikinews":    (limburgish_wikinews.download,    out / "limburgish_wikinews"),
        "frisian_wikibooks":      (frisian_wikibooks.download,      out / "frisian_wikibooks"),
        "afrikaans_wikibooks":    (afrikaans_wikibooks.download,    out / "afrikaans_wikibooks"),
        "afrikaans_wikiquote":    (afrikaans_wikiquote.download,    out / "afrikaans_wikiquote"),
        "nchlt_af":               (nchlt_af.download,               out / "nchlt_af"),
        "autshumato_af":          (autshumato_af.download,          out / "autshumato_af"),
        "parlamint_be":           (parlamint_be.download,           out / "parlamint_be"),
        "tweede_kamer_opendata":  (tweede_kamer_opendata.download,  out / "tweede_kamer_opendata"),
        # ToS-compliant replacements for youtube_cc (Batch 5)
        "uvn_podcast":            (uvn_podcast.download,            out / "uvn_podcast"),
        "uvv_podcast":            (uvv_podcast.download,            out / "uvv_podcast"),
        "debalie_commons":        (debalie_commons.download,        out / "debalie_commons"),
        # Dutch children's encyclopaedia (Batch 6)
        "wikikids":               (wikikids.download,               out / "wikikids"),
        # New sources (Batch 7)
        "kamerstukken":           (kamerstukken.download,           out / "kamerstukken"),
        "multi_eurlex":           (multi_eurlex.download,           out / "multi_eurlex"),
        "teu_tenders":            (teu_tenders.download,            out / "teu_tenders"),
        "digibron":               (digibron.download,               out / "digibron"),
        "oapen_nl":               (oapen_nl.download,               out / "oapen_nl"),
        "naturalis":              (naturalis.download,              out / "naturalis"),
        # New sources (Batch 8)
        "open_raadsinformatie":   (open_raadsinformatie.download,   out / "open_raadsinformatie"),
        "kb_open_kranten":        (kb_open_kranten.download,        out / "kb_open_kranten"),
        "juportal_be":            (juportal_be.download,            out / "juportal_be"),
        "woo_documents":          (woo_documents.download,          out / "woo_documents"),
        # New sources (Batch 9)
        "vlpar_extra":            (vlpar_extra.download,            out / "vlpar_extra"),
        "woogle_foia":            (woogle_foia.download,            out / "woogle_foia"),
        "open_data_archieven":    (open_data_archieven.download,    out / "open_data_archieven"),
        # GLAM + RVO + RIVM LCI (Batch 10)
        "amsterdam_museum":       (amsterdam_museum.download,       out / "amsterdam_museum"),
        "rmo":                    (rmo.download,                    out / "rmo"),
        "rvo_webcontent":         (rvo_webcontent.download,         out / "rvo_webcontent"),
        "rivm_lci":               (rivm_lci.download,               out / "rivm_lci"),
        # Gov research institutes + statistics (Batch 11)
        "wodc":                   (wodc.download,                   out / "wodc"),
        "scp":                    (scp.download,                    out / "scp"),
        "cbs_longreads":          (cbs_longreads.download,          out / "cbs_longreads"),
        "slo_curriculum":         (slo_curriculum.download,         out / "slo_curriculum"),
        # Historical VOC (Batch 12)
        "globalise_voc":          (globalise_voc.download,          out / "globalise_voc"),
        # EP debates, Wikidata lexemes, Delpher magazines (Batch 13)
        "eupdcorp":               (eupdcorp.download,               out / "eupdcorp"),
        "wikidata_lexemes":       (wikidata_lexemes.download,       out / "wikidata_lexemes"),
        "delpher_magazines":      (delpher_magazines.download,      out / "delpher_magazines"),
        # Policy institutes + ECHR (Batch 14)
        "cpb_pbl":                (cpb_pbl.download,                out / "cpb_pbl"),
        "echr_nl":                (echr_nl.download,                out / "echr_nl"),
        # Social policy (Batch 15)
        "movisie":                (movisie.download,                out / "movisie"),
        # Audiovisual heritage (Batch 16)
        "open_beelden":           (open_beelden.download,           out / "open_beelden"),
        # Administrative courts + WWII audio (Batch 17)
        "raad_van_state":         (raad_van_state.download,         out / "raad_van_state"),
        "radio_oranje":           (radio_oranje.download,           out / "radio_oranje"),
        # Batch 18 — Middle Dutch manuscripts + Afrikaans corpora
        "martijn_trilogy":        (martijn_trilogy.download,        out / "martijn_trilogy"),
        "herne_manuscripts":      (herne_manuscripts.download,      out / "herne_manuscripts"),
        "autshumato_af_mono":     (autshumato_af_mono.download,     out / "autshumato_af_mono"),
        "nchlt_af_speech":        (nchlt_af_speech.download,        out / "nchlt_af_speech"),
        # Batch 19 — Spoken Dutch + Afrikaans NER
        "variants_corpus":        (variants_corpus.download,        out / "variants_corpus"),
        "nchlt_af_ner":           (nchlt_af_ner.download,           out / "nchlt_af_ner"),
        # Batch 20 — Uitvoeringsinformatie + open leermateriaal + Brussels Parlement
        "puc":                    (puc.download,                    out / "puc"),
        "wikiwijs":               (wikiwijs.download,               out / "wikiwijs"),
        "brussels_parlement":     (brussels_parlement.download,     out / "brussels_parlement"),
        # Batch 21 — Vuk'uzenzele Afrikaans magazine corpus
        "vukuzenzele_af":         (vukuzenzele_af.download,         out / "vukuzenzele_af"),
        # Batch 22 — DSFSI terminology, AdeKUS, SA gazettes, Meertens dialects
        "za_mafoko":              (za_mafoko.download,              out / "za_mafoko"),
        "adekus_journal":         (adekus_journal.download,         out / "adekus_journal"),
        "open_gazettes_za":       (open_gazettes_za.download,       out / "open_gazettes_za"),
        "meertens_dialecten":     (meertens_dialecten.download,     out / "meertens_dialecten"),
        # Batch 23 — WEWIS, SUNScholar, TNO, Diamond OA, FAME! Frisian, Groote Oorlog
        "wewis":                  (wewis.download,                  out / "wewis"),
        "sunscholar":             (sunscholar.download,             out / "sunscholar"),
        "tno_repository":         (tno_repository.download,         out / "tno_repository"),
        "diamond_oa_nl":          (diamond_oa_nl.download,          out / "diamond_oa_nl"),
        "fame_frisian":           (fame_frisian.download,           out / "fame_frisian"),
        "groote_oorlog":          (groote_oorlog.download,          out / "groote_oorlog"),
        "belgian_journal":        (belgian_journal.download,        out / "belgian_journal"),
        # Batch 25
        "eurovoc":                (eurovoc.download,                out / "eurovoc"),
        "bhic_htr":               (bhic_htr.download,              out / "bhic_htr"),
        "af_genre":               (af_genre.download,              out / "af_genre"),
        "afrikaans_wiktionary":   (afrikaans_wiktionary.download,  out / "afrikaans_wiktionary"),
        "limburgish_wiktionary":  (limburgish_wiktionary.download, out / "limburgish_wiktionary"),
        "frisian_wiktionary":     (frisian_wiktionary.download,    out / "frisian_wiktionary"),
        # University repos NL + African Storybook (Batch 26)
        "university_repos_nl":    (university_repos_nl.download,   out / "university_repos_nl"),
        "african_storybook":      (african_storybook.download,     out / "african_storybook"),
        # Mensenrechten equality body decisions (Batch 27)
        "mensenrechten":          (mensenrechten.download,         out / "mensenrechten"),
        # NL government bodies + SA Parliament Afrikaans (Batch 28)
        "pmg_hansard":            (pmg_hansard.download,           out / "pmg_hansard"),
        "nationale_ombudsman":    (nationale_ombudsman.download,   out / "nationale_ombudsman"),
        "ovv":                    (ovv.download,                   out / "ovv"),
        "acm":                    (acm.download,                   out / "acm"),
        # Large PD corpora + EK proceedings (Batch 29)
        "common_corpus_dutch_pd": (common_corpus_dutch_pd.download, out / "common_corpus_dutch_pd"),
        "internet_archive_nl":    (internet_archive_nl.download,    out / "internet_archive_nl"),
        "eerste_kamer":           (eerste_kamer.download,           out / "eerste_kamer"),
        # GPT-NL Public Corpus Dutch subsets (Batch 30)
        "gptnl_public":           (gptnl_public.download,           out / "gptnl_public"),
        # UGent OAI-PMH + govza cabinet AF (Batch 32)
        "govza_cabinet_af":       (govza_cabinet_af.download,       out / "govza_cabinet_af"),
        # BWB consolidated legislation (Batch 33)
        "bwb_legislation":        (bwb_legislation.download,        out / "bwb_legislation"),
        # DANS Data Station Archaeology + Utrechts Archief (Batch 34)
        "dans_archaeology":       (dans_archaeology.download,       out / "dans_archaeology"),
        "utrechts_archief":       (utrechts_archief.download,       out / "utrechts_archief"),
        # DANS SSH + DataverseNL + Global Voices NL (Batch 34)
        "dans_ssh":               (dans_ssh.download,               out / "dans_ssh"),
        "dataverse_nl":           (dataverse_nl.download,           out / "dataverse_nl"),
        "global_voices_nl":       (global_voices_nl.download,       out / "global_voices_nl"),
        # Vlaamse Codex (Batch 35)
        "vlaamse_codex":          (vlaamse_codex.download,          out / "vlaamse_codex"),
        # Karin Spaink blog PD (Batch 36)
        "spaink":                 (spaink.download,                 out / "spaink"),
    }

    selected = list(dispatch.keys()) if sources == ["all"] else sources
    for name in selected:
        if name not in dispatch:
            logger.error("Unknown source: %s. Valid: %s", name, list(dispatch))
            sys.exit(1)

    parallel = getattr(args, "parallel", 1)

    def _run_source(name: str) -> None:
        fn, dest = dispatch[name]
        logger.info("=== Acquiring: %s -> %s ===", name, dest)
        if name == "common_voice":
            cv_locale   = getattr(args, "cv_locale", "nl")
            cv_api_key  = getattr(args, "cv_api_key", None)
            hf_token    = getattr(args, "hf_token", None)
            hf_dataset  = getattr(args, "hf_dataset", common_voice.DEFAULT_HF_DATASET)
            dl_url      = getattr(args, "cv_download_url", None)
            locales     = ["nl", "nl-BE"] if cv_locale == "both" else [cv_locale]
            for loc in locales:
                fn(dest, dry_run=args.dry_run,
                   locale=loc, cv_api_key=cv_api_key,
                   download_url=dl_url, hf_token=hf_token,
                   hf_dataset=hf_dataset)
        elif name == "mozilla_dc":
            cv_api_key = getattr(args, "cv_api_key", None)
            fn(dest, dry_run=args.dry_run, cv_api_key=cv_api_key)
        else:
            fn(dest, dry_run=args.dry_run)
        logger.info("=== Done: %s ===", name)

    if parallel <= 1:
        for name in selected:
            _run_source(name)
    else:
        logger.info("Acquiring %d sources with %d parallel workers", len(selected), parallel)
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(_run_source, name): name for name in selected}
            for fut in as_completed(futures):
                name = futures[fut]
                exc = fut.exception()
                if exc:
                    logger.error("=== FAILED: %s — %s ===", name, exc)


# ---------------------------------------------------------------------------
# Stage: asr  (transcribe podcast audio → .txt, runs between acquire and parse)
# ---------------------------------------------------------------------------

def cmd_asr(args: argparse.Namespace) -> None:
    from pipeline.asr import run_asr
    counts = run_asr(
        raw_dir=Path(args.input),
        sources=args.sources if args.sources else None,
        model_size=args.model_size,
        device=args.device,
        compute_type=args.compute_type,
    )
    total = sum(counts.values())
    logger.info("ASR complete. %d files transcribed across %d sources.", total, len(counts))


# ---------------------------------------------------------------------------
# Stage: parse
# ---------------------------------------------------------------------------

def cmd_parse(args: argparse.Namespace) -> None:
    from pipeline.parse.dispatch import dispatch
    counts = dispatch(
        raw_dir=Path(args.input),
        parsed_dir=Path(args.output),
        sources=args.sources if args.sources else None,
    )
    total = sum(counts.values())
    logger.info("Parse complete. %d documents across %d sources.", total, len(counts))


# ---------------------------------------------------------------------------
# Stage: langfilter
# ---------------------------------------------------------------------------

def cmd_langfilter(args: argparse.Namespace) -> None:
    from pipeline.langfilter import filter_document, LangFilterConfig
    from pipeline.document import read_jsonl, write_jsonl

    in_dir  = Path(args.input)
    out_dir = Path(args.output)
    cfg = LangFilterConfig()

    total_in = total_out = 0

    for jsonl_file in sorted(in_dir.rglob("*.jsonl")):
        rel  = jsonl_file.relative_to(in_dir)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and dest.stat().st_mtime >= jsonl_file.stat().st_mtime:
            logger.info("Skipping %s (up to date)", rel)
            continue

        def dutch_docs():
            nonlocal total_in, total_out
            for doc in read_jsonl(jsonl_file):
                total_in += 1
                if filter_document(doc, cfg):
                    total_out += 1
                    yield doc

        written = write_jsonl(dutch_docs(), dest)
        logger.info("Langfilter %s -> %d docs", rel, written)

    logger.info(
        "Langfilter done. In: %d, Out: %d (dropped: %d)",
        total_in, total_out, total_in - total_out,
    )


# ---------------------------------------------------------------------------
# Stage: clean
# ---------------------------------------------------------------------------

def cmd_clean(args: argparse.Namespace) -> None:
    from pipeline.clean.pipeline import clean, CleanConfig
    from pipeline.document import read_jsonl, write_jsonl, Document

    in_dir  = Path(args.input)
    out_dir = Path(args.output)
    cfg = CleanConfig()
    total_in = total_out = 0

    for jsonl_file in sorted(in_dir.rglob("*.jsonl")):
        rel  = jsonl_file.relative_to(in_dir)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and dest.stat().st_mtime >= jsonl_file.stat().st_mtime:
            logger.info("Skipping %s (up to date)", rel)
            continue

        def cleaned_docs():
            nonlocal total_in, total_out
            for doc in read_jsonl(jsonl_file):
                total_in += 1
                cleaned = clean(doc.text, cfg)
                if cleaned is not None:
                    total_out += 1
                    yield Document(text=cleaned, meta=doc.meta, original_text=doc.original_text)

        written = write_jsonl(cleaned_docs(), dest)
        logger.info("Cleaned %s -> %d docs", rel, written)

    logger.info("Clean done. In: %d, Out: %d (dropped: %d)", total_in, total_out, total_in - total_out)


# ---------------------------------------------------------------------------
# Stage: dedup
# ---------------------------------------------------------------------------

def cmd_dedup(args: argparse.Namespace) -> None:
    import time
    from pipeline.dedup.lsh import LSHIndex, minhash_signature
    from pipeline.document import read_jsonl, write_jsonl

    in_dir  = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    index = LSHIndex(num_hashes=128, bands=16, rows=8, threshold=args.threshold)
    total_in = total_out = 0

    all_files = sorted(in_dir.rglob("*.jsonl"))
    total_files = len(all_files)

    # Split into already-done and pending.
    # A file is "done" if its deduped output exists and is non-empty.
    done_files:    list[tuple[Path, Path, Path]] = []  # (src, rel, dest)
    pending_files: list[tuple[Path, Path]]       = []  # (src, rel)
    for jsonl_file in all_files:
        rel  = jsonl_file.relative_to(in_dir)
        dest = out_dir / rel
        if dest.exists() and dest.stat().st_size > 0:
            done_files.append((jsonl_file, rel, dest))
        else:
            pending_files.append((jsonl_file, rel))

    # Rebuild LSH index from already-written outputs so cross-source dedup
    # remains correct when we continue.
    if done_files:
        logger.info(
            "[dedup] Resuming: %d/%d files done, %d pending. Rebuilding index...",
            len(done_files), total_files, len(pending_files),
        )
        rebuild_start = time.perf_counter()
        reindex_n = 0
        for _, rel, dest in done_files:
            file_reindex = 0
            for doc in read_jsonl(dest):
                sig = minhash_signature(doc.text, index.num_hashes)
                index.force_add(doc.id, sig)
                reindex_n += 1
                file_reindex += 1
            logger.info("[dedup] re-indexed %-45s  %6d docs  (index: %d)", rel, file_reindex, index.size)
        logger.info(
            "[dedup] Index rebuilt: %d docs in %.1fs",
            reindex_n, time.perf_counter() - rebuild_start,
        )
    else:
        logger.info("[dedup] Starting fresh: %d files", total_files)

    files_done_count = len(done_files)
    stage_start = time.perf_counter()

    for loop_idx, (jsonl_file, rel) in enumerate(pending_files):
        file_num = files_done_count + loop_idx + 1  # 1-based position in full list
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        file_start = time.perf_counter()
        file_in = file_out = 0

        def _deduped_docs(src=jsonl_file, r=rel):
            nonlocal total_in, total_out, file_in, file_out
            for doc_num, doc in enumerate(read_jsonl(src), 1):
                total_in += 1
                file_in  += 1
                if index.add(doc.id, doc.text):
                    total_out += 1
                    file_out  += 1
                    yield doc
                if doc_num % 10_000 == 0:
                    elapsed = time.perf_counter() - file_start
                    rate    = doc_num / elapsed if elapsed else 0
                    logger.info(
                        "[dedup]   %s | %7d in | %7d kept (%.1f%%) | %6.0f docs/s | index: %d",
                        r, doc_num, file_out, 100 * file_out / doc_num, rate, index.size,
                    )

        write_jsonl(_deduped_docs(), dest)

        elapsed  = time.perf_counter() - file_start
        rate     = file_in / elapsed if elapsed else 0
        kept_pct = 100 * file_out / file_in if file_in else 0

        # ETA: based on average time per pending file processed so far
        pending_done     = loop_idx + 1
        pending_left     = len(pending_files) - pending_done
        stage_elapsed    = time.perf_counter() - stage_start
        avg_secs_pending = stage_elapsed / pending_done
        eta_secs         = int(avg_secs_pending * pending_left)
        if eta_secs >= 3600:
            eta_str = f"{eta_secs // 3600}h {(eta_secs % 3600) // 60}m"
        elif eta_secs >= 60:
            eta_str = f"{eta_secs // 60}m {eta_secs % 60}s"
        else:
            eta_str = f"{eta_secs}s"

        logger.info(
            "[dedup] %3d/%d | %-45s | in: %7d  kept: %7d (%.1f%%) | %6.0f docs/s | %.1fs | index: %d | ETA: %s",
            file_num, total_files, rel,
            file_in, file_out, kept_pct,
            rate, elapsed, index.size, eta_str,
        )

    total_elapsed = time.perf_counter() - stage_start
    resume_note = f" (resumed; {len(done_files)} files pre-existing)" if done_files else ""
    logger.info(
        "[dedup] Complete%s | %d files | session in: %d  kept: %d  removed: %d (%.1f%%) | %.2fh",
        resume_note, total_files, total_in, total_out,
        total_in - total_out,
        100 * (total_in - total_out) / total_in if total_in else 0,
        total_elapsed / 3600,
    )


# ---------------------------------------------------------------------------
# Stage: pii
# ---------------------------------------------------------------------------

def cmd_pii(args: argparse.Namespace) -> None:
    from pipeline.pii.scrubber import scrub, ScrubConfig
    from pipeline.pii.audit import PIIAuditLog
    from pipeline.document import read_jsonl, write_jsonl, Document

    in_dir    = Path(args.input)
    out_dir   = Path(args.output)
    audit_dir = Path(args.audit)

    cfg = ScrubConfig(
        use_regex=True,
        use_ner=not args.no_ner,
    )

    total_in = total_out = total_redactions = 0

    with PIIAuditLog(audit_dir) as audit:
        for jsonl_file in sorted(in_dir.rglob("*.jsonl")):
            rel  = jsonl_file.relative_to(in_dir)
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)

            if dest.exists() and dest.stat().st_mtime >= jsonl_file.stat().st_mtime:
                logger.info("Skipping %s (up to date)", rel)
                continue

            def scrubbed_docs():
                nonlocal total_in, total_out, total_redactions
                for doc in read_jsonl(jsonl_file):
                    total_in += 1
                    scrubbed_text, record = scrub(doc.text, doc_id=doc.id, config=cfg)
                    total_redactions += record.count
                    audit.record(record, source=doc.meta.source)
                    total_out += 1
                    yield Document(text=scrubbed_text, meta=doc.meta, original_text=None)

            written = write_jsonl(scrubbed_docs(), dest)
            logger.info("PII scrubbed %s -> %d docs", rel, written)

    logger.info(
        "PII scrub done. Docs: %d, Total redactions: %d",
        total_out, total_redactions,
    )


# ---------------------------------------------------------------------------
# Stage: synth
# ---------------------------------------------------------------------------

def cmd_synth(args: argparse.Namespace) -> None:
    from pipeline.synth import run_synth
    total = run_synth(
        langfiltered_dir=Path(args.langfiltered),
        output_dir=Path(args.output),
        method=args.method,
        llm_backend=args.llm_backend,
        claude_model=args.claude_model,
        ratio=args.ratio,
        noise_levels=args.noise_levels if hasattr(args, "noise_levels") else None,
        dry_run=args.dry_run,
        languages=getattr(args, "wd_languages", ["NL", "AF", "FY", "LI"]),
        limit=getattr(args, "wd_limit", 50_000),
    )
    logger.info("[synth] %d synthetic documents generated", total)


# ---------------------------------------------------------------------------
# Stage: normalize
# ---------------------------------------------------------------------------

def cmd_normalize(args: argparse.Namespace) -> None:
    from pipeline.normalize.pipeline import normalize_document, NormalizeConfig
    from pipeline.document import read_jsonl, write_jsonl

    in_dir  = Path(args.input)
    out_dir = Path(args.output)
    cfg = NormalizeConfig(
        apply_rules=True,
        apply_lexicon=True,
        lexicon_path=Path(args.lexicon) if args.lexicon else None,
        use_llm=args.use_llm,
        llm_backend=args.llm_backend,
        claude_model=args.claude_model,
    )

    for jsonl_file in sorted(in_dir.rglob("*.jsonl")):
        rel  = jsonl_file.relative_to(in_dir)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and dest.stat().st_mtime >= jsonl_file.stat().st_mtime:
            logger.info("Skipping %s (up to date)", rel)
            continue

        def normalized_docs():
            for doc in read_jsonl(jsonl_file):
                yield normalize_document(doc, cfg)

        written = write_jsonl(normalized_docs(), dest)
        logger.info("Normalized %s -> %d docs", rel, written)


# ---------------------------------------------------------------------------
# Stage: export
# ---------------------------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> None:
    from pipeline.export import export_from_dir
    total = export_from_dir(
        input_dir=Path(args.input),
        output_dir=Path(args.output),
        shard_size=args.shard_size,
    )
    logger.info("Export complete: %d documents", total)


# ---------------------------------------------------------------------------
# Stage: stats
# ---------------------------------------------------------------------------

def cmd_stats(args: argparse.Namespace) -> None:
    from pipeline.stats import compute_stats
    stats = compute_stats(Path(args.input))
    print(stats.report())


# ---------------------------------------------------------------------------
# Stage: compliance
# ---------------------------------------------------------------------------

def cmd_compliance(args: argparse.Namespace) -> None:
    from pipeline.compliance import generate
    report_path = generate(
        corpus_dir=Path(args.corpus),
        audit_dir=Path(args.audit),
        output_dir=Path(args.output),
    )
    logger.info("Compliance report written: %s", report_path)


# ---------------------------------------------------------------------------
# Stage: run-all
# ---------------------------------------------------------------------------

def cmd_run_all(args: argparse.Namespace) -> None:
    """Run the full pipeline end-to-end."""
    base = Path(args.output)

    # Full stage sequence with default paths
    stages = [
        ("acquire",    argparse.Namespace(sources=args.sources, output=str(base / "raw"),        dry_run=args.dry_run)),
        ("asr",        argparse.Namespace(input=str(base / "raw"),        sources=["uvn_podcast", "uvv_podcast", "radio_oranje"], model_size="large-v3", device="auto", compute_type="auto")),
        ("parse",      argparse.Namespace(input=str(base / "raw"),        output=str(base / "parsed"),    sources=None)),
        ("langfilter", argparse.Namespace(input=str(base / "parsed"),     output=str(base / "langfiltered"))),
        ("clean",      argparse.Namespace(input=str(base / "langfiltered"), output=str(base / "cleaned"))),
        ("dedup",      argparse.Namespace(input=str(base / "cleaned"),    output=str(base / "deduped"),   threshold=0.85)),
        ("pii",        argparse.Namespace(input=str(base / "deduped"),    output=str(base / "scrubbed"),  audit=str(base / "audit"), no_ner=args.no_ner)),
        ("normalize",  argparse.Namespace(input=str(base / "scrubbed"),   output=str(base / "normalized"), lexicon=None, use_llm=args.use_llm, llm_backend=args.llm_backend, claude_model=args.claude_model)),
        ("export",     argparse.Namespace(input=str(base / "normalized"), output=str(base / "final"),     shard_size=50_000)),
        ("stats",      argparse.Namespace(input=str(base / "final"))),
        ("compliance", argparse.Namespace(corpus=str(base / "final"),     audit=str(base / "audit"),      output=str(base / "compliance"))),
    ]

    handlers = {
        "acquire": cmd_acquire, "asr": cmd_asr, "parse": cmd_parse,
        "langfilter": cmd_langfilter, "clean": cmd_clean, "dedup": cmd_dedup,
        "pii": cmd_pii, "synth": cmd_synth, "normalize": cmd_normalize,
        "export": cmd_export, "stats": cmd_stats, "compliance": cmd_compliance,
    }

    for stage_name, stage_args in stages:
        logger.info("\n%s\n=== STAGE: %-12s ===\n%s", "=" * 44, stage_name.upper(), "=" * 44)
        handlers[stage_name](stage_args)

    logger.info("Pipeline complete.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _write_build_manifest(data_dir: Path) -> None:
    """Write data/build_manifest.json for replication traceability."""
    import datetime as _dt
    import importlib.metadata
    import json
    import platform
    import subprocess

    manifest: dict = {
        "created_at":     _dt.datetime.now().isoformat(),
        "python_version": sys.version,
        "python_path":    sys.executable,
        "platform":       platform.platform(),
        "os":             platform.system(),
        "os_release":     platform.release(),
        "machine":        platform.machine(),
        "processor":      platform.processor(),
        "libraries":      {},
    }

    for pkg in [
        "langdetect", "spacy", "pyarrow", "pdfplumber",
        "faster-whisper", "anthropic", "langcodes",
        "lxml", "requests",
    ]:
        try:
            manifest["libraries"][pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            manifest["libraries"][pkg] = None

    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        manifest["git_commit"] = res.stdout.strip() if res.returncode == 0 else None
        res2 = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, check=False,
        )
        manifest["git_tag"] = res2.stdout.strip() if res2.returncode == 0 else None
    except FileNotFoundError:
        manifest["git_commit"] = None
        manifest["git_tag"] = None

    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / "build_manifest.json"

    # Append run history rather than overwrite — keeps all build runs
    history: list = []
    if out.exists():
        try:
            history = json.loads(out.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = [history]
        except Exception:
            history = []
    history.append(manifest)
    out.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Build manifest updated: %s (%d run(s) recorded)", out, len(history))


def main() -> None:
    import datetime as _dt
    _log_dir = Path("logs")
    _log_dir.mkdir(exist_ok=True)
    _ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file = _log_dir / f"pipeline_{_ts}.log"

    # Console + file logging simultaneously
    _fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
    _root = logging.getLogger()
    _root.setLevel(logging.INFO)

    _ch = logging.StreamHandler(sys.stdout)
    _ch.setFormatter(_fmt)
    _root.addHandler(_ch)

    _fh = logging.FileHandler(_log_file, encoding="utf-8")
    _fh.setFormatter(_fmt)
    _root.addHandler(_fh)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("datasets").setLevel(logging.WARNING)

    logger.info("NederCorpus pipeline starting — log: %s", _log_file)

    _write_build_manifest(Path("data"))

    parser = argparse.ArgumentParser(
        prog="python -m pipeline",
        description="NederCorpus — end-to-end Dutch corpus pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _SOURCES = [
        "all",
        "wikipedia", "rechtspraak", "eurlex", "wetten", "parliamentary",
        "belgian", "suriname",
        "gutenberg", "europarl", "delpher", "rijksoverheid",
        "codex", "vlpar", "jrc",
        "dbnl", "staatscourant", "wikisource", "common_voice",
        "mozilla_dc",
        "wikibooks", "wikinews", "dracor", "tatoeba", "emea",
        "wikiquote", "wikivoyage", "wikiversity", "wiktionary", "frisian_wiki",
        "statenvertaling", "openraad", "mls",
        "opus_extra", "ud_dutch", "openboek", "renaissance_poetry",
        "limburgish_wiki", "west_flemish_wiki", "zeelandic_wiki",
        "afrikaans_wiki", "low_saxon_wiki", "europeana_ner",
        "north_frisian_wiki", "saterland_frisian_wiki", "papiamentu_wiki",
        "sranantongo_wiki", "brieven_als_buit", "dcep",
        "lokale_regelgeving", "officiele_bekendmakingen", "tuchtrecht", "lblod",
        "tractatenblad", "advisory_bodies", "oscar", "hplt", "rijksmuseum",
        "openalex", "wikidata_nl",
        "fineweb2_nl", "c5_dutch", "dutch_pd", "eu_science_commons", "mc4_nl",
        "rce", "rivm", "grondwettelijk_hof",
        "openaire_nl", "dekamer",
        # Caribbean & Belgium (Batch 1)
        "bes_bekendmakingen", "rechtspraak_caribisch",
        "landscourant_aruba", "coleccion_aruba", "publicatieblad_curacao",
        "senate_be", "leipzig",
        # Historical & parliamentary (Batch 2)
        "voc_wic_htr", "parlamint_nl", "nationaal_archief", "etaamb_be",
        # Additional parliamentary & academic (Batch 3)
        "staten_generaal", "voxpopuli", "pmc_nl",
        # Limburgish/Frisian/Afrikaans wiki expansions + Afrikaans corpora + BE parliament + TK open data (Batch 4)
        "limburgish_wikisource", "limburgish_wikibooks", "limburgish_wikiquote", "limburgish_wikinews",
        "frisian_wikibooks", "afrikaans_wikibooks", "afrikaans_wikiquote",
        "nchlt_af", "autshumato_af", "parlamint_be", "tweede_kamer_opendata",
        # ToS-compliant replacements for youtube_cc (Batch 5)
        "uvn_podcast", "uvv_podcast", "debalie_commons",
        # Dutch children's encyclopaedia (Batch 6)
        "wikikids",
        # New sources (Batch 7)
        "kamerstukken", "multi_eurlex", "teu_tenders",
        "digibron", "oapen_nl", "naturalis",
        # New sources (Batch 8)
        "open_raadsinformatie", "kb_open_kranten", "juportal_be", "woo_documents",
        # New sources (Batch 9)
        "vlpar_extra", "woogle_foia", "open_data_archieven",
        # GLAM + RVO + RIVM LCI (Batch 10)
        "amsterdam_museum", "rmo", "rvo_webcontent", "rivm_lci",
        # Gov research institutes + statistics (Batch 11)
        "wodc", "scp", "cbs_longreads", "slo_curriculum",
        # Historical VOC (Batch 12)
        "globalise_voc",
        # EP debates, Wikidata lexemes, Delpher magazines (Batch 13)
        "eupdcorp", "wikidata_lexemes", "delpher_magazines",
        # Policy institutes + ECHR (Batch 14)
        "cpb_pbl", "echr_nl",
        # Social policy (Batch 15)
        "movisie",
        # Audiovisual heritage (Batch 16)
        "open_beelden",
        # Administrative courts + WWII audio (Batch 17)
        "raad_van_state", "radio_oranje",
        # Middle Dutch manuscripts + Afrikaans corpora (Batch 18)
        "martijn_trilogy", "herne_manuscripts", "autshumato_af_mono", "nchlt_af_speech",
        # Spoken Dutch + Afrikaans NER (Batch 19)
        "variants_corpus", "nchlt_af_ner",
        # Uitvoeringsinformatie + open leermateriaal + Brussels Parlement (Batch 20)
        "puc", "wikiwijs", "brussels_parlement",
        # Vuk'uzenzele Afrikaans magazine corpus (Batch 21)
        "vukuzenzele_af",
        # DSFSI terminology, AdeKUS journal, SA gazettes, Meertens dialects (Batch 22)
        "za_mafoko", "adekus_journal", "open_gazettes_za", "meertens_dialecten",
        # WEWIS, SUNScholar, TNO, Diamond OA, FAME! Frisian, Groote Oorlog (Batch 23)
        "wewis", "sunscholar", "tno_repository", "diamond_oa_nl", "fame_frisian", "groote_oorlog",
        # Belgian Journal company bylaws (Batch 24)
        "belgian_journal",
        # EuroVoc + BHIC HTR + SADiLaR AF Genre + wiktionaries (Batch 25)
        "eurovoc", "bhic_htr", "af_genre", "afrikaans_wiktionary", "limburgish_wiktionary",
        "frisian_wiktionary",
        # University repos NL + African Storybook (Batch 26)
        "university_repos_nl", "african_storybook",
        # Mensenrechten equality body decisions (Batch 27)
        "mensenrechten",
        # NL government bodies + SA Parliament Afrikaans (Batch 28)
        "pmg_hansard", "nationale_ombudsman", "ovv", "acm",
        # Large PD corpora + EK proceedings (Batch 29)
        "common_corpus_dutch_pd", "internet_archive_nl", "eerste_kamer",
        # GPT-NL Public Corpus Dutch subsets (Batch 30)
        "gptnl_public",
        # UGent OAI-PMH + govza cabinet AF (Batch 32)
        "govza_cabinet_af",
        # BWB consolidated legislation + tatoeba nds Low Saxon (Batch 33)
        "bwb_legislation",
        # DANS Data Station Archaeology + Utrechts Archief (Batch 34)
        "dans_archaeology",
        "utrechts_archief",
        # DANS SSH + DataverseNL + Global Voices NL (Batch 34)
        "dans_ssh",
        "dataverse_nl",
        "global_voices_nl",
        # Vlaamse Codex — Flemish legislation NL-BE (Batch 35)
        "vlaamse_codex",
        # Karin Spaink blog PD (Batch 36)
        "spaink",
    ]

    # acquire
    p = sub.add_parser("acquire")
    p.add_argument("--sources",     nargs="+", default=["all"], choices=_SOURCES)
    p.add_argument("--output",      default="data/raw")
    p.add_argument("--dry-run",     action="store_true")
    p.add_argument("--parallel",    type=int, default=4,
                   help="Number of sources to acquire concurrently (default: 4)")
    p.add_argument("--cv-api-key",        default=None,
                   help="Mozilla Data Collective API key for Common Voice (or set CV_API_KEY env var)")
    p.add_argument("--hf-token",         default=None,
                   help="HuggingFace read token for Common Voice (or set HF_TOKEN env var)")
    p.add_argument("--hf-dataset",       default="mozilla-foundation/common_voice_17_0",
                   help="HuggingFace dataset ID for Common Voice")
    p.add_argument("--cv-locale",        default="both", choices=["nl", "nl-BE", "both"],
                   help="Common Voice locale(s) to download (default: both)")
    p.add_argument("--cv-download-url",  default=None,
                   help="Signed Mozilla S3 URL — copy from browser after clicking Download "
                        "on commonvoice.mozilla.org/en/datasets")

    # asr  (transcribe podcast audio, runs between acquire and parse)
    p = sub.add_parser("asr", help="Transcribe podcast audio files to .txt using faster-whisper")
    p.add_argument("--input",        default="data/raw",
                   help="Raw data directory (contains source subdirs with .mp3/.m4a files)")
    p.add_argument("--sources",      nargs="+", default=["uvn_podcast", "uvv_podcast", "radio_oranje"],
                   help="Source names to transcribe (default: uvn_podcast uvv_podcast radio_oranje)")
    p.add_argument("--model-size",   default="large-v3",
                   choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                   help="Whisper model size (default: large-v3)")
    p.add_argument("--device",       default="auto", choices=["auto", "cuda", "cpu"],
                   help="Inference device (default: auto — CUDA if available)")
    p.add_argument("--compute-type", default="auto",
                   choices=["auto", "float16", "int8", "float32"],
                   help="CTranslate2 compute type (default: auto)")

    # parse
    p = sub.add_parser("parse")
    p.add_argument("--input",   default="data/raw")
    p.add_argument("--output",  default="data/parsed")
    p.add_argument("--sources", nargs="+", default=None)

    # langfilter
    p = sub.add_parser("langfilter")
    p.add_argument("--input",  default="data/parsed")
    p.add_argument("--output", default="data/langfiltered")

    # clean
    p = sub.add_parser("clean")
    p.add_argument("--input",  default="data/langfiltered")
    p.add_argument("--output", default="data/cleaned")

    # dedup
    p = sub.add_parser("dedup")
    p.add_argument("--input",     default="data/cleaned")
    p.add_argument("--output",    default="data/deduped")
    p.add_argument("--threshold", type=float, default=0.85)

    # pii
    p = sub.add_parser("pii", help="Scrub PII (GDPR + EU AI Act)")
    p.add_argument("--input",   default="data/deduped")
    p.add_argument("--output",  default="data/scrubbed")
    p.add_argument("--audit",   default="audit")
    p.add_argument("--no-ner",  action="store_true", help="Disable spaCy NER (regex only)")

    # synth
    p = sub.add_parser("synth", help="Generate synthetic data (AF/PAP/SRN→NL, NL noise, Wikidata expand)")
    p.add_argument("--langfiltered", default="data/langfiltered",
                   help="Input: langfiltered JSONL directory")
    p.add_argument("--output",       default="data/synth")
    p.add_argument("--method",       default="af_to_nl",
                   choices=["af_to_nl", "pap_to_nl", "srn_to_nl", "nl_noise", "wikidata_expand", "legal_templates", "all"],
                   help="Synthesis method")
    p.add_argument("--llm-backend",  default="local", choices=["local", "claude"],
                   help="LLM backend: 'local' (Qwen via localhost:8000) or 'claude' (Anthropic API)")
    p.add_argument("--claude-model", default="claude-haiku-4-5-20251001",
                   choices=["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"])
    p.add_argument("--ratio",        type=int, default=20,
                   help="NL:source ratio cap per method (default: 20)")
    p.add_argument("--noise-levels", nargs="+", default=None,
                   choices=["light", "heavy", "ultra"],
                   help="Noise levels for nl_noise method (default: all three)")
    p.add_argument("--wd-languages", nargs="+", default=["NL", "AF", "FY", "LI"],
                   metavar="LANG",
                   help="Language variants for wikidata_expand (default: NL AF FY LI)")
    p.add_argument("--wd-limit",     type=int, default=50_000,
                   help="Max entities per language for wikidata_expand (default: 50000)")
    p.add_argument("--dry-run",      action="store_true")

    # normalize
    p = sub.add_parser("normalize")
    p.add_argument("--input",        default="data/scrubbed")
    p.add_argument("--output",       default="data/normalized")
    p.add_argument("--lexicon",      default=None)
    p.add_argument("--use-llm",      action="store_true",
                   help="Use LLM for linguistic normalization of historical Dutch")
    p.add_argument("--llm-backend",  default="local", choices=["local", "claude"],
                   help="LLM backend: 'local' (Qwen via localhost:8000) or 'claude' (Anthropic API)")
    p.add_argument("--claude-model", default="claude-sonnet-4-6",
                   choices=["claude-sonnet-4-6", "claude-opus-4-6"],
                   help="Claude model (only used when --llm-backend claude)")

    # export
    p = sub.add_parser("export")
    p.add_argument("--input",      default="data/normalized")
    p.add_argument("--output",     default="data/final")
    p.add_argument("--shard-size", type=int, default=50_000)

    # stats
    p = sub.add_parser("stats")
    p.add_argument("--input", default="data/final")

    # compliance
    p = sub.add_parser("compliance")
    p.add_argument("--corpus", default="data/final")
    p.add_argument("--audit",  default="audit")
    p.add_argument("--output", default="compliance")

    # run-all
    p = sub.add_parser("run-all", help="Run every stage end-to-end")
    p.add_argument("--output",       default="data")
    p.add_argument("--sources",      nargs="+", default=["all"])
    p.add_argument("--dry-run",      action="store_true")
    p.add_argument("--no-ner",       action="store_true")
    p.add_argument("--use-llm",      action="store_true",
                   help="Use LLM for normalize stage (local Qwen by default)")
    p.add_argument("--llm-backend",  default="local", choices=["local", "claude"])
    p.add_argument("--claude-model", default="claude-sonnet-4-6",
                   choices=["claude-sonnet-4-6", "claude-opus-4-6"])
    p.add_argument("--cv-api-key",    default=None,
                   help="Mozilla Data Collective API key (or set CV_API_KEY env var)")
    p.add_argument("--hf-token",     default=None)
    p.add_argument("--hf-dataset",   default="mozilla-foundation/common_voice_17_0")
    p.add_argument("--cv-locale",    default="both", choices=["nl", "nl-BE", "both"])
    p.add_argument("--cv-download-url", default=None,
                   help="Signed Mozilla S3 URL for Common Voice download")

    args = parser.parse_args()
    {
        "acquire":    cmd_acquire,
        "asr":        cmd_asr,
        "parse":      cmd_parse,
        "langfilter": cmd_langfilter,
        "clean":      cmd_clean,
        "dedup":      cmd_dedup,
        "pii":        cmd_pii,
        "synth":      cmd_synth,
        "normalize":  cmd_normalize,
        "export":     cmd_export,
        "stats":      cmd_stats,
        "compliance": cmd_compliance,
        "run-all":    cmd_run_all,
    }[args.command](args)


if __name__ == "__main__":
    main()
