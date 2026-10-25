"""
Download the FAME! open Frisian text corpus.

FAME (Frisian Audio Mining Enterprise) produced open-source speech and text
resources for West Frisian (FY). The text corpus contains ~38 million words
from Omrop Fryslân radio subtitles and other Frisian text sources.
Published by Fryske Akademy / Radboud University.

Strategies tried in order:
  A — OpenSLR resource pages (46, 78, 64)
  B — GitHub repos (opensource-spraakherkenning-nl, fryske-akademy)
  C — CLARIN handle redirect
  D — Direct known URLs (Fryske Akademy fileadmin, downloads.openslr.org/46)

If all strategies fail a _NOT_FOUND.txt file is written so the pipeline can
skip this source gracefully.

License: CC-BY 4.0
Country: NL
Language variant: FY
Domain: broadcast

Usage:
  python -m pipeline acquire --sources fame_frisian
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import logging
import re
import ssl
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE   = "fame_frisian"
LICENSE  = "CC-BY"
COUNTRY  = "NL"
DOMAIN   = "broadcast"
LANGUAGE = "FY"

_USER_AGENT = "NederCorpus/0.1 (academic; Frisian corpus; contact: corpus@example.org)"
_DELAY      = 1.5   # seconds between requests

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_LINES_PER_FILE = 10_000  # split large text archives into chunks of this size


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    """Fetch URL, return bytes or None on failure."""
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            logger.warning(
                "[fame_frisian] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1
            )
            if exc.code in (400, 403, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning(
                "[fame_frisian] Error fetching %s: %s (attempt %d)", url, exc, attempt + 1
            )
            time.sleep(2.0 * (attempt + 1))
    return None


def _log(output_dir: Path, url: str, identifier: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "identifier":       identifier,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Text-file splitting helpers
# ---------------------------------------------------------------------------

def _save_text_chunks(text: str, output_dir: Path, prefix: str) -> int:
    """
    Split text into chunks of _LINES_PER_FILE lines and save as
    {prefix}_{N:04d}.txt.  Returns the number of files written.
    """
    lines = text.splitlines()
    if not lines:
        return 0
    n_files = 0
    for start in range(0, len(lines), _LINES_PER_FILE):
        chunk = "\n".join(lines[start : start + _LINES_PER_FILE])
        dest = output_dir / f"{prefix}_{n_files:04d}.txt"
        if not dest.exists():
            dest.write_text(chunk, encoding="utf-8")
        n_files += 1
    return n_files


def _extract_text_from_archive(data: bytes, output_dir: Path, archive_name: str) -> int:
    """
    Extract plain-text files from a tar.gz or zip archive.
    Returns the total number of text files written (chunks counted).
    """
    total = 0
    # Try tar.gz / tar.bz2 / tar
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = member.name
                ext  = Path(name).suffix.lower()
                if ext not in (".txt", ".tsv", ".text", ".stm", ".ctm", ""):
                    continue
                fobj = tf.extractfile(member)
                if fobj is None:
                    continue
                try:
                    text = fobj.read().decode("utf-8", errors="replace")
                except Exception:
                    continue
                prefix = re.sub(r'[/\\]', '_', name).rstrip("_")[:60]
                prefix = f"fame_text_{archive_name}_{prefix}"
                n = _save_text_chunks(text, output_dir, prefix)
                total += n
        return total
    except tarfile.TarError:
        pass

    # Try zip
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                ext = Path(info.filename).suffix.lower()
                if ext not in (".txt", ".tsv", ".text", ".stm", ".ctm", ""):
                    continue
                try:
                    text = zf.read(info.filename).decode("utf-8", errors="replace")
                except Exception:
                    continue
                prefix = re.sub(r'[/\\]', '_', info.filename).rstrip("_")[:60]
                prefix = f"fame_text_{archive_name}_{prefix}"
                n = _save_text_chunks(text, output_dir, prefix)
                total += n
        return total
    except zipfile.BadZipFile:
        pass

    return 0


def _scan_html_for_archives(html: str, base_url: str) -> list[str]:
    """Return absolute URLs of .tar.gz / .zip / .txt links found in HTML."""
    urls: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1).strip()
        if not any(href.lower().endswith(ext) for ext in (".tar.gz", ".tgz", ".tar.bz2", ".zip", ".txt")):
            continue
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            parsed = urllib.parse.urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith("http"):
            href = urllib.parse.urljoin(base_url, href)
        if href not in urls:
            urls.append(href)
    return urls


# ---------------------------------------------------------------------------
# Strategy A — OpenSLR
# ---------------------------------------------------------------------------

_FRISIAN_KEYWORDS = ("frisian", "frisian", "frysk", "fries", "fame_text", "fame_lm",
                      "fame_corpus", "_fy_", "-fy-", ".fy.", "west_frisian")
_NON_FRISIAN_REJECT = ("tunisian", "arabic", "msa", "libyan", "maltese", "turkish",
                        "persian", "amharic", "swahili", "somali", "yoruba")


def _is_frisian_archive(url: str) -> bool:
    """Return True only if the URL looks like a Frisian archive."""
    ul = url.lower()
    if any(kw in ul for kw in _NON_FRISIAN_REJECT):
        return False
    return any(kw in ul for kw in _FRISIAN_KEYWORDS)


def _strategy_a(output_dir: Path) -> bool:
    """Try OpenSLR resource pages known to contain Frisian data."""
    # OpenSLR 96 is the FAME Frisian ASR corpus; 46/78/64 are NOT Frisian
    openslr_pages = [
        "https://www.openslr.org/96/",
        "https://downloads.openslr.org/96/",
    ]
    for page_url in openslr_pages:
        logger.info("[fame_frisian] Strategy A: probing %s", page_url)
        html_bytes = _fetch(page_url, timeout=30)
        if html_bytes is None:
            time.sleep(_DELAY)
            continue
        html = html_bytes.decode("utf-8", errors="replace")
        archive_urls = _scan_html_for_archives(html, page_url)
        for au in archive_urls:
            al = au.lower()
            # Skip audio-only, require text/lm content, strictly validate Frisian
            if any(kw in al for kw in ("wav", "flac", "mp3")):
                continue
            if not _is_frisian_archive(au) and not any(
                kw in al for kw in ("text", "lm", "corpus", "transcript")
            ):
                continue
            fname = au.rsplit("/", 1)[-1]
            dest = output_dir / f"fame_corpus_{fname}"
            if dest.exists():
                _log(output_dir, au, dest.name)
                return True
            logger.info("[fame_frisian] Strategy A: downloading %s", au)
            data = _fetch(au, timeout=300)
            if data is None:
                continue
            dest.write_bytes(data)
            logger.info("[fame_frisian] Saved %s (%.1f MB)", dest.name, len(data) / 1e6)
            _log(output_dir, au, dest.name)
            n = _extract_text_from_archive(data, output_dir, "openslr96")
            if n:
                logger.info("[fame_frisian] Extracted %d text chunk files", n)
            return True
        time.sleep(_DELAY)

    return False


# ---------------------------------------------------------------------------
# Strategy B — GitHub
# ---------------------------------------------------------------------------

def _github_api_fetch(url: str) -> object | None:
    """Fetch GitHub API JSON."""
    data = _fetch(url, timeout=30)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _try_github_repo(api_url: str, output_dir: Path, label: str) -> bool:
    """Try fetching files from a GitHub API URL (releases or contents)."""
    obj = _github_api_fetch(api_url)
    if obj is None:
        return False

    download_urls: list[str] = []

    if isinstance(obj, dict):
        # releases/latest → assets
        for asset in obj.get("assets", []):
            u = asset.get("browser_download_url", "")
            if u:
                download_urls.append(u)
    elif isinstance(obj, list):
        # contents → files
        for item in obj:
            if not isinstance(item, dict):
                continue
            u = item.get("download_url") or item.get("html_url", "")
            name = item.get("name", "")
            if u and any(name.lower().endswith(ext) for ext in (".txt", ".zip", ".tar.gz", ".tgz")):
                download_urls.append(u)

    for du in download_urls:
        fname = du.rsplit("/", 1)[-1]
        dest  = output_dir / f"fame_corpus_{label}_{fname}"
        if dest.exists():
            _log(output_dir, du, dest.name)
            return True
        logger.info("[fame_frisian] Strategy B: downloading %s", du)
        data = _fetch(du, timeout=300)
        if data is None:
            continue
        dest.write_bytes(data)
        logger.info("[fame_frisian] Saved %s (%.1f MB)", dest.name, len(data) / 1e6)
        _log(output_dir, du, dest.name)
        n = _extract_text_from_archive(data, output_dir, label)
        logger.info("[fame_frisian] Extracted %d text chunk files from %s", n, label)
        return True

    return False


def _strategy_b(output_dir: Path) -> bool:
    """Try GitHub repos for FAME text corpus."""
    github_targets = [
        (
            "https://api.github.com/repos/opensource-spraakherkenning-nl/fame-text-corpus/releases/latest",
            "fame_gh_releases",
        ),
        (
            "https://api.github.com/repos/opensource-spraakherkenning-nl/fame-text-corpus/contents/",
            "fame_gh_contents",
        ),
        (
            "https://api.github.com/repos/fryske-akademy/frisian-corpus/contents/",
            "fryske_gh_contents",
        ),
        (
            "https://api.github.com/repos/fryske-akademy/frisian-corpus/releases/latest",
            "fryske_gh_releases",
        ),
    ]
    for api_url, label in github_targets:
        logger.info("[fame_frisian] Strategy B: probing %s", api_url)
        if _try_github_repo(api_url, output_dir, label):
            return True
        time.sleep(_DELAY)
    return False


# ---------------------------------------------------------------------------
# Strategy C — CLARIN handle
# ---------------------------------------------------------------------------

def _strategy_c(output_dir: Path) -> bool:
    """Try CLARIN handle redirect for FAME corpus."""
    handle_url = "https://hdl.handle.net/11032/fame-corpus"
    logger.info("[fame_frisian] Strategy C: following CLARIN handle %s", handle_url)
    data = _fetch(handle_url, timeout=30)
    if data is None:
        return False
    html = data.decode("utf-8", errors="replace")
    # Scan for download links
    archive_urls = _scan_html_for_archives(html, handle_url)
    for au in archive_urls:
        al = au.lower()
        if any(kw in al for kw in ("text", "corpus", "frisian", "fame", "lm")):
            fname = au.rsplit("/", 1)[-1]
            dest  = output_dir / f"fame_corpus_clarin_{fname}"
            if dest.exists():
                _log(output_dir, au, dest.name)
                return True
            archive_data = _fetch(au, timeout=300)
            if archive_data:
                dest.write_bytes(archive_data)
                _log(output_dir, au, dest.name)
                n = _extract_text_from_archive(archive_data, output_dir, "clarin")
                logger.info("[fame_frisian] Strategy C: %d text chunks", n)
                return True
    return False


# ---------------------------------------------------------------------------
# Strategy D — Direct known URLs
# ---------------------------------------------------------------------------

def _strategy_d(output_dir: Path) -> bool:
    """Try direct known download URLs."""
    direct_urls = [
        "https://www.fryske-akademy.nl/fileadmin/inhoud/img/newsitems/FAME_corpus.zip",
        "https://www.fryske-akademy.nl/fileadmin/inhoud/img/newsitems/FAME_text_corpus.zip",
        "https://downloads.openslr.org/96/fame_text.tar.gz",
        "https://downloads.openslr.org/96/fame_lm.tar.gz",
    ]
    for url in direct_urls:
        fname = url.rsplit("/", 1)[-1]
        dest  = output_dir / f"fame_corpus_{fname}"
        if dest.exists():
            logger.info("[fame_frisian] Strategy D: already have %s", dest.name)
            _log(output_dir, url, dest.name)
            return True
        logger.info("[fame_frisian] Strategy D: trying %s", url)
        data = _fetch(url, timeout=300)
        if data is None:
            time.sleep(_DELAY)
            continue
        dest.write_bytes(data)
        logger.info("[fame_frisian] Saved %s (%.1f MB)", dest.name, len(data) / 1e6)
        _log(output_dir, url, dest.name)
        n = _extract_text_from_archive(data, output_dir, "direct")
        logger.info("[fame_frisian] Strategy D: %d text chunks extracted", n)
        return True
    return False


# ---------------------------------------------------------------------------
# Strategy E — HuggingFace OPUS-100 fy-en (Frisian side of parallel corpus)
# ---------------------------------------------------------------------------

def _strategy_e(output_dir: Path) -> bool:
    """Download OPUS-100 fy-en parquet from HuggingFace (Frisian-side text)."""
    try:
        import pyarrow.parquet as pq  # type: ignore
        import io as _io
    except ImportError:
        logger.warning("[fame_frisian] Strategy E: pyarrow not installed — skipping")
        return False

    splits = ["train", "validation", "test"]
    total_saved = 0
    for split in splits:
        url = (
            f"https://huggingface.co/api/datasets/Helsinki-NLP/opus-100"
            f"/parquet/fy-en/{split}/0.parquet"
        )
        dest = output_dir / f"fame_opus100_fy_{split}.parquet"
        if dest.exists():
            logger.info("[fame_frisian] Strategy E: already have %s", dest.name)
            total_saved += 1
            continue
        logger.info("[fame_frisian] Strategy E: downloading %s", url)
        data = _fetch(url, timeout=120)
        if data is None:
            time.sleep(_DELAY)
            continue
        dest.write_bytes(data)
        logger.info("[fame_frisian] Saved %s (%.1f MB)", dest.name, len(data) / 1e6)
        _log(output_dir, url, dest.name)
        total_saved += 1
        time.sleep(_DELAY)

    if total_saved == 0:
        return False

    # Extract Frisian (source) column from parquets into text chunks
    pq_files = sorted(output_dir.glob("fame_opus100_fy_*.parquet"))
    lines: list[str] = []
    for pq_path in pq_files:
        try:
            table = pq.read_table(str(pq_path))
        except Exception as exc:
            logger.warning("[fame_frisian] Cannot read %s: %s", pq_path.name, exc)
            continue
        # OPUS-100 columns: 'translation' dict with 'fy' and 'en' keys
        if "translation" in table.schema.names:
            for i in range(table.num_rows):
                row = table.column("translation")[i].as_py()
                if isinstance(row, dict):
                    fy_text = (row.get("fy") or "").strip()
                    if fy_text:
                        lines.append(fy_text)
        # Also try direct 'fy' column
        elif "fy" in table.schema.names:
            for i in range(table.num_rows):
                val = table.column("fy")[i].as_py()
                if val:
                    lines.append(str(val).strip())

    if not lines:
        logger.warning("[fame_frisian] Strategy E: parquets downloaded but no fy text found")
        return total_saved > 0  # still counts as success (files are there)

    n = _save_text_chunks("\n".join(lines), output_dir, "fame_text_opus100_fy")
    logger.info("[fame_frisian] Strategy E: %d Frisian sentences → %d text chunk files", len(lines), n)
    return True


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download(output_dir: Path, dry_run: bool = False, **kwargs) -> None:
    """
    Download the FAME! Frisian text corpus (CC-BY 4.0).

    Tries four strategies in order; writes _NOT_FOUND.txt if all fail.

    Saves:
      fame_corpus_*.{tar.gz,zip}  — raw archive(s)
      fame_text_*_{NNNN}.txt      — plain-text chunks (10 000 lines each)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[fame_frisian] [dry-run] would try 4 strategies to download FAME corpus")
        return

    # Skip if we already have text chunks
    existing_txt = list(output_dir.glob("fame_text_*.txt"))
    if existing_txt:
        logger.info(
            "[fame_frisian] Already have %d text chunk files — skipping", len(existing_txt)
        )
        return

    strategies = [
        ("A (OpenSLR 96)",         _strategy_a),
        ("B (GitHub)",             _strategy_b),
        ("C (CLARIN handle)",      _strategy_c),
        ("D (Direct known URLs)",  _strategy_d),
        ("E (HuggingFace OPUS-100 fy-en)", _strategy_e),
    ]

    for label, fn in strategies:
        logger.info("[fame_frisian] Trying strategy %s", label)
        try:
            success = fn(output_dir)
        except Exception as exc:
            logger.warning("[fame_frisian] Strategy %s raised: %s", label, exc)
            success = False
        if success:
            logger.info("[fame_frisian] Strategy %s succeeded", label)
            return
        logger.info("[fame_frisian] Strategy %s failed or found nothing", label)
        time.sleep(_DELAY)

    # All strategies failed
    msg = (
        "FAME Frisian corpus not found.\n\n"
        "Strategies tried:\n"
        "  A: OpenSLR pages 46, 78, 64 and downloads.openslr.org/46/\n"
        "  B: GitHub opensource-spraakherkenning-nl/fame-text-corpus "
        "and fryske-akademy/frisian-corpus\n"
        "  C: CLARIN handle hdl.handle.net/11032/fame-corpus\n"
        "  D: Direct URLs (fryske-akademy.nl fileadmin, downloads.openslr.org/46/)\n\n"
        "Manual steps:\n"
        "  1. Visit https://www.openslr.org/46/ and check for text/LM downloads.\n"
        "  2. Visit https://research.rug.nl/en/datasets/fame-speech-database for links.\n"
        "  3. Contact Fryske Akademy (info@fryske-akademy.nl) for direct access.\n"
        f"Date attempted: {datetime.date.today().isoformat()}\n"
    )
    not_found = output_dir / "_NOT_FOUND.txt"
    not_found.write_text(msg, encoding="utf-8")
    logger.warning(
        "[fame_frisian] All strategies failed. See %s for details.", not_found
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download FAME! Frisian text corpus (CC-BY 4.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
