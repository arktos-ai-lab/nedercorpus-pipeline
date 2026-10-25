"""
Download the NCHLT Afrikaans Speech Corpus from SADiLaR.

Orthographically transcribed broadband speech corpus in Afrikaans.
~56 hours of speech from 200 speakers. Developed by CSIR Meraka Institute
and North-West University as part of the NCHLT initiative.

For NederCorpus we extract only the orthographic transcription text (the
<orth> prompt text from the XML metadata files). The audio is not used.

Also downloads the NCHLT Afrikaans Auxiliary Speech Corpus (handles 280 + 290)
which provides additional transcribed prompts.

License: CC BY 3.0 Unported
  https://repo.sadilar.org/items/2bf723bc-7420-439e-a55d-833e10b0c776  (main)
  https://repo.sadilar.org/items/5414c15d-f626-49e0-b118-1d30cd07f322  (auxiliary)

Size: ~4.5 GB (main) + ~6.6 GB (auxiliary) — audio not stored, only XML prompts
Domain: speech
Language: Afrikaans (AF)
Country: ZA
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import ssl
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "nchlt_af_speech"
LICENSE = "CC-BY"   # CC BY 3.0 — use CC-BY as umbrella
COUNTRY = "ZA"
DOMAIN  = "speech"

# (item_uuid, label) — UUIDs from repo.sadilar.org item pages
_ITEMS = [
    ("2bf723bc-7420-439e-a55d-833e10b0c776", "nchlt_af_speech_main"),
    ("5414c15d-f626-49e0-b118-1d30cd07f322", "nchlt_af_speech_aux"),
]

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# Regex to extract prompt text from NCHLT XML transcription files
# <orth>dit is ʼn voorbeeld sin</orth>
_ORTH_RE = re.compile(r"<orth>([^<]+)</orth>", re.IGNORECASE)


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_zip_urls(item_uuid: str) -> list[str]:
    """Resolve SADiLaR DSpace item UUID to list of ZIP download URLs."""
    import json as _json
    urls: list[str] = []
    bundles_url = f"https://repo.sadilar.org/server/api/core/items/{item_uuid}/bundles"
    try:
        req = urllib.request.Request(
            bundles_url,
            headers={"Accept": "application/json", "User-Agent": "NederCorpus/0.1"},
        )
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("[nchlt_af_speech] Bundle fetch %s failed: %s", item_uuid, exc)
        return urls

    for bundle in data.get("_embedded", {}).get("bundles", []):
        if bundle.get("name") != "ORIGINAL":
            continue
        bits_href = bundle.get("_links", {}).get("bitstreams", {}).get("href", "")
        if not bits_href:
            continue
        try:
            req2 = urllib.request.Request(
                bits_href,
                headers={"Accept": "application/json", "User-Agent": "NederCorpus/0.1"},
            )
            with urllib.request.urlopen(req2, timeout=30, context=_SSL_CTX) as resp:
                bits = _json.loads(resp.read().decode("utf-8"))
            for bit in bits.get("_embedded", {}).get("bitstreams", []):
                if bit.get("name", "").endswith(".zip"):
                    urls.append(bit["_links"]["content"]["href"])
        except Exception as exc:
            logger.warning("[nchlt_af_speech] Bitstream fetch failed: %s", exc)
    return urls


def _extract_prompts_from_zip(zip_path: Path, out_dir: Path) -> int:
    """Extract <orth> prompt texts from XML files in the ZIP into .txt files.
    Returns number of prompt files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    speaker_prompts: dict[str, list[str]] = {}

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            # XML transcription files — skip audio and dict files
            if not member.endswith(".xml"):
                continue
            try:
                content = zf.read(member).decode("utf-8", errors="replace")
            except Exception:
                continue
            prompts = _ORTH_RE.findall(content)
            if not prompts:
                continue
            # Group by speaker directory
            parts = member.replace("\\", "/").split("/")
            speaker = parts[-3] if len(parts) >= 3 else "unknown"
            speaker_prompts.setdefault(speaker, []).extend(prompts)

    written = 0
    for speaker, prompts in speaker_prompts.items():
        unique_prompts = list(dict.fromkeys(p.strip() for p in prompts if p.strip()))
        if not unique_prompts:
            continue
        dest = out_dir / f"{speaker}.txt"
        dest.write_text("\n".join(unique_prompts), encoding="utf-8")
        written += 1

    return written


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = output_dir / "prompts"

    for item_uuid, label in _ITEMS:
        label_dir = prompts_dir / label
        if label_dir.exists() and any(label_dir.glob("*.txt")):
            logger.info("[nchlt_af_speech] %s already extracted — skipping", label)
            continue

        zip_urls = _resolve_zip_urls(item_uuid)
        if not zip_urls:
            logger.warning("[nchlt_af_speech] Could not resolve download URLs for %s", label)
            continue

        if dry_run:
            logger.info("[nchlt_af_speech] DRY RUN — would download %d ZIP(s) for %s", len(zip_urls), label)
            continue

        label_dir.mkdir(parents=True, exist_ok=True)
        for i, url in enumerate(zip_urls):
            zip_path = output_dir / f"{label}_{i}.zip"
            if not zip_path.exists():
                logger.info("[nchlt_af_speech] Downloading %s part %d/%d (~3-7 GB) …", label, i+1, len(zip_urls))
                try:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "NederCorpus/0.1 (academic research; public data)"},
                    )
                    with urllib.request.urlopen(req, timeout=600, context=_SSL_CTX) as resp:
                        with zip_path.open("wb") as f:
                            while chunk := resp.read(1 << 20):
                                f.write(chunk)
                    logger.info("[nchlt_af_speech] Downloaded %s (%d MB)", zip_path.name, zip_path.stat().st_size >> 20)
                except Exception as exc:
                    logger.error("[nchlt_af_speech] Download failed for %s part %d: %s", label, i+1, exc)
                    zip_path.unlink(missing_ok=True)
                    continue

            logger.info("[nchlt_af_speech] Extracting prompts from %s …", zip_path.name)
            n = _extract_prompts_from_zip(zip_path, label_dir)
            logger.info("[nchlt_af_speech] %s part %d: %d speaker prompt files written", label, i+1, n)
            zip_path.unlink(missing_ok=True)

        _log(output_dir, f"https://repo.sadilar.org/items/{item_uuid}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download NCHLT Afrikaans speech transcripts (SADiLaR)")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
