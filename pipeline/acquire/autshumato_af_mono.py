"""
Download the Autshumato Monolingual Afrikaans Corpus from SADiLaR.

The Autshumato project (CTexT, North-West University) developed monolingual
text corpora for South African official languages for machine translation.
This corpus is a large collection of Afrikaans text (UTF-8, one segment per line)
from South African government and general texts.

Distinct from `autshumato_af` (which is the AF-NL parallel corpus). This is
the full monolingual Afrikaans corpus — substantially larger than the AF side
of the parallel corpus.

License: CC BY 4.0
  https://repo.sadilar.org/items/f199a579-c74c-405d-b087-5b5bf70b5a99
Handle: https://hdl.handle.net/20.500.12185/580

Size: ~49.7 MB ZIP
Domain: government
Language: Afrikaans (AF)
Country: ZA
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import ssl
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "autshumato_af_mono"
LICENSE = "CC-BY"
COUNTRY = "ZA"
DOMAIN  = "government"

# DSpace REST API for handle 20.500.12185/580
_ITEM_UUID  = "f199a579-c74c-405d-b087-5b5bf70b5a99"
_HANDLE     = "20.500.12185/580"
_ZIP_NAME   = "autshumato_af_mono.zip"
# Direct bitstream content URL resolved via DSpace REST API (item UUID → bundle → bitstream)
_BITSTREAM_UUID = "6ff98516-b7b4-48e2-80f1-fb2419d8e941"
_DOWNLOAD_URL   = f"https://repo.sadilar.org/server/api/core/bitstreams/{_BITSTREAM_UUID}/content"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_download_url() -> str | None:
    """Resolve the ZIP download URL via DSpace REST API."""
    import json as _json
    # Step 1: resolve handle to item UUID
    handle_url = f"https://repo.sadilar.org/server/api/core/items/search/findByHandle?handle={_HANDLE}"
    req = urllib.request.Request(
        handle_url,
        headers={"Accept": "application/json",
                 "User-Agent": "NederCorpus/0.1 (academic research; public data)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        item_href = data.get("_links", {}).get("self", {}).get("href", "")
    except Exception as exc:
        logger.warning("[autshumato_af_mono] Handle resolve failed: %s", exc)
        return None

    if not item_href:
        return None

    # Step 2: get bundles
    bundles_url = item_href.rstrip("/") + "/bundles"
    try:
        req2 = urllib.request.Request(
            bundles_url,
            headers={"Accept": "application/json",
                     "User-Agent": "NederCorpus/0.1"},
        )
        with urllib.request.urlopen(req2, timeout=30, context=_SSL_CTX) as resp:
            bundles = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("[autshumato_af_mono] Bundle fetch failed: %s", exc)
        return None

    for bundle in bundles.get("_embedded", {}).get("bundles", []):
        if bundle.get("name") == "ORIGINAL":
            bitstreams_href = bundle.get("_links", {}).get("bitstreams", {}).get("href", "")
            if bitstreams_href:
                try:
                    req3 = urllib.request.Request(
                        bitstreams_href,
                        headers={"Accept": "application/json", "User-Agent": "NederCorpus/0.1"},
                    )
                    with urllib.request.urlopen(req3, timeout=30, context=_SSL_CTX) as resp:
                        bits = _json.loads(resp.read().decode("utf-8"))
                except Exception:
                    continue
                for bit in bits.get("_embedded", {}).get("bitstreams", []):
                    if bit.get("name", "").endswith(".zip"):
                        return bit["_links"]["content"]["href"]
    return None


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path  = output_dir / _ZIP_NAME
    txt_dir   = output_dir / "text"

    if txt_dir.exists() and any(txt_dir.glob("*.txt")):
        logger.info("[autshumato_af_mono] Already extracted — skipping")
        return

    if not zip_path.exists():
        url = _DOWNLOAD_URL  # pre-resolved bitstream URL

        if dry_run:
            logger.info("[autshumato_af_mono] DRY RUN — would download %s", url)
            return

        logger.info("[autshumato_af_mono] Downloading from %s …", url)
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "NederCorpus/0.1 (academic research; public data)"},
            )
            with urllib.request.urlopen(req, timeout=300, context=_SSL_CTX) as resp:
                with zip_path.open("wb") as f:
                    while chunk := resp.read(1 << 20):
                        f.write(chunk)
            logger.info("[autshumato_af_mono] Downloaded %d MB", zip_path.stat().st_size >> 20)
        except Exception as exc:
            logger.error("[autshumato_af_mono] Download failed: %s", exc)
            zip_path.unlink(missing_ok=True)
            return

    # Extract text file(s) from ZIP
    txt_dir.mkdir(exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.endswith(".txt") and not member.startswith("__MACOSX"):
                fname = Path(member).name
                dest  = txt_dir / fname
                if not dest.exists():
                    dest.write_bytes(zf.read(member))
                    extracted += 1

    logger.info("[autshumato_af_mono] Extracted %d text files", extracted)
    _log(output_dir, f"https://hdl.handle.net/{_HANDLE}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Autshumato Monolingual Afrikaans (SADiLaR)")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
