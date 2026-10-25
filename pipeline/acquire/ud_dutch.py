"""
Download Dutch and Afrikaans Universal Dependencies treebanks.

UD treebanks contain syntactically annotated sentences in CoNLL-U format.
We extract the raw sentence text (# text = ...) for the corpus.

Treebanks:
  alpino            — UD_Dutch-Alpino (CC-BY-SA 4.0) — news + general Dutch
                      https://github.com/UniversalDependencies/UD_Dutch-Alpino
  lassy_small       — UD_Dutch-LassySmall (CC-BY-SA 4.0) — Wikipedia-based Dutch
                      https://github.com/UniversalDependencies/UD_Dutch-LassySmall
  afrikaans_afribooms — UD_Afrikaans-AfriBooms (CC-BY-SA 4.0) — Afrikaans govt docs
                      https://github.com/UniversalDependencies/UD_Afrikaans-AfriBooms
  frisian_dutch_fame  — UD_Frisian_Dutch-Fame (CC-BY-SA 4.0) — code-switched Frisian-Dutch speech
                      https://github.com/UniversalDependencies/UD_Frisian_Dutch-Fame
  low_saxon_lsdc    — UD_Low_Saxon-LSDC (CC-BY-SA 4.0) — 8 Low Saxon dialect groups, historical texts
                      https://github.com/UniversalDependencies/UD_Low_Saxon-LSDC
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "ud_dutch"
LICENSE = "CC-BY"

TREEBANKS = {
    "alpino": (
        "https://github.com/UniversalDependencies/UD_Dutch-Alpino/archive/refs/heads/master.zip",
        "UD_Dutch-Alpino-master",
    ),
    "lassy_small": (
        "https://github.com/UniversalDependencies/UD_Dutch-LassySmall/archive/refs/heads/master.zip",
        "UD_Dutch-LassySmall-master",
    ),
    "afrikaans_afribooms": (
        "https://github.com/UniversalDependencies/UD_Afrikaans-AfriBooms/archive/refs/heads/master.zip",
        "UD_Afrikaans-AfriBooms-master",
    ),
    "frisian_dutch_fame": (
        "https://github.com/UniversalDependencies/UD_Frisian_Dutch-Fame/archive/refs/heads/master.zip",
        "UD_Frisian_Dutch-Fame-master",
    ),
    "low_saxon_lsdc": (
        "https://github.com/UniversalDependencies/UD_Low_Saxon-LSDC/archive/refs/heads/master.zip",
        "UD_Low_Saxon-LSDC-master",
    ),
}


def _log(output_dir: Path, treebank: str, url: str) -> None:
    entry = {
        "source": f"ud_dutch_{treebank}",
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _extract_sentences(conllu_bytes: bytes) -> list[str]:
    """Extract # text = ... lines from CoNLL-U data."""
    sentences = []
    for line in conllu_bytes.decode("utf-8", errors="replace").splitlines():
        if line.startswith("# text = "):
            s = line[len("# text = "):].strip()
            if s:
                sentences.append(s)
    return sentences


def download(output_dir: Path, dry_run: bool = False) -> None:
    """Download UD Dutch treebanks and extract sentence text."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[ud_dutch] [dry-run] skipping")
        return

    headers = {"User-Agent": "NederCorpus/0.1 (academic research; UD CC-BY 4.0)"}

    for treebank, (zip_url, folder_prefix) in TREEBANKS.items():
        dest_dir = output_dir / treebank
        dest_dir.mkdir(exist_ok=True)
        sentences_file = dest_dir / "sentences.txt"

        if sentences_file.exists():
            logger.info("[ud_dutch] %s already extracted — skipping", treebank)
            continue

        logger.info("[ud_dutch] Downloading %s …", treebank)
        req = urllib.request.Request(zip_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                zip_data = resp.read()
        except Exception as exc:
            logger.error("[ud_dutch] Download failed for %s: %s", treebank, exc)
            continue

        all_sentences: list[str] = []
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for name in zf.namelist():
                    if name.endswith(".conllu"):
                        all_sentences.extend(_extract_sentences(zf.read(name)))
        except Exception as exc:
            logger.error("[ud_dutch] Extraction failed for %s: %s", treebank, exc)
            continue

        sentences_file.write_text(
            "\n".join(all_sentences), encoding="utf-8"
        )
        _log(output_dir, treebank, zip_url)
        logger.info("[ud_dutch] %s: %d sentences extracted", treebank, len(all_sentences))
