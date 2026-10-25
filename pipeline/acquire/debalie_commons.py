"""
Download De Balie video descriptions and subtitles from Wikimedia Commons.

De Balie (Amsterdam cultural center) has uploaded ~27 video files to
Wikimedia Commons under CC-BY-3.0. This acquirer:
  1. Lists all files in Category:Videos_by_De_Balie via the MediaWiki API
  2. For each file, fetches:
     a. TimedText subtitle track ({filename}.nl.srt) if available
     b. Falls back to the file description (wikitext → plain text)
  3. Writes {file_slug}.txt + {file_slug}.json

No yt-dlp, no YouTube ToS issues. All data served by Wikimedia's public API.

License: CC-BY-3.0
Source:  https://commons.wikimedia.org/wiki/Category:Videos_by_De_Balie
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "debalie_commons"
LICENSE = "CC-BY-3.0"
COUNTRY = "NL"
DOMAIN  = "culture"

_API_BASE   = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "NederCorpus/0.1 (academic research; Dutch text corpus; Wikimedia Commons CC-BY)"
_CATEGORY   = "Category:Videos_by_De_Balie"

_MIN_TEXT_LEN = 200


def _api_get(params: dict) -> dict:
    """Call the Wikimedia API and return parsed JSON."""
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = f"{_API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _list_category_files(category: str) -> list[dict]:
    """
    Return all files in a Commons category.
    Each entry: {"title": "File:...", "pageid": int}
    """
    files: list[dict] = []
    cmcontinue: str | None = None

    while True:
        params: dict = {
            "action":  "query",
            "list":    "categorymembers",
            "cmtitle": category,
            "cmtype":  "file",
            "cmlimit": "50",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = _api_get(params)
        members = data.get("query", {}).get("categorymembers", [])
        files.extend(members)

        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")
        if not cmcontinue:
            break

    return files


def _fetch_timedtext(filename: str, lang: str = "nl") -> str | None:
    """
    Try to fetch a TimedText subtitle page for a file.

    Wikimedia TimedText pages are named:
      TimedText:{filename}.{lang}.srt
    e.g.:
      TimedText:De Balie - example.webm.nl.srt
    """
    # Strip "File:" prefix
    bare = filename.removeprefix("File:")
    tt_page = f"TimedText:{bare}.{lang}.srt"

    params = {
        "action": "parse",
        "page":   tt_page,
        "prop":   "wikitext",
    }
    try:
        data = _api_get(params)
    except Exception as exc:
        logger.debug("[debalie_commons] TimedText fetch failed for %s: %s", tt_page, exc)
        return None

    if "error" in data:
        return None

    wikitext = data.get("parse", {}).get("wikitext", "")
    if not wikitext:
        return None

    # SRT wikitext: strip cue numbers and timestamps
    lines_out: list[str] = []
    for line in wikitext.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        lines_out.append(line)

    text = " ".join(lines_out)
    return text if len(text) >= _MIN_TEXT_LEN else None


def _fetch_file_description(file_title: str) -> tuple[str, str, int | None]:
    """
    Fetch wikitext for a file page.

    Returns (description_text, source_url, pub_year).
    """
    params = {
        "action": "query",
        "titles": file_title,
        "prop":   "revisions|info",
        "rvprop": "content",
        "inprop": "url",
    }
    try:
        data = _api_get(params)
    except Exception as exc:
        logger.warning("[debalie_commons] Page fetch failed for %s: %s", file_title, exc)
        return ("", "", None)

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return ("", "", None)

    page = pages[0] if isinstance(pages, list) else list(pages.values())[0]
    source_url = page.get("fullurl", "")

    revisions = page.get("revisions", [])
    wikitext = revisions[0].get("content", "") if revisions else ""

    # Extract description from {{Information}} template |description= field.
    # The value is typically: |description={{nl|1=DUTCH TEXT}}
    # Capture everything up to the next top-level parameter (\n|) or end of template (\n}})
    desc = ""
    m = re.search(
        r"\|\s*description\s*=(.*?)(?=\n\s*\|[a-zA-Z]|\n\s*\}\})",
        wikitext,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        desc_raw = m.group(1).strip()
        # Unwrap {{nl|1=...}} / {{nl|...}} / {{en|1=...}} language templates
        lang_m = re.search(
            r"\{\{(?:nl|NL|en|EN|fr|FR)\s*\|(?:1\s*=\s*)?(.*?)\}\}",
            desc_raw,
            re.DOTALL,
        )
        if lang_m:
            desc = lang_m.group(1).strip()
        else:
            desc = desc_raw
    else:
        # Fallback: strip all markup and use first 1000 chars
        desc = re.sub(r"\{\{[^}]+\}\}", " ", wikitext)
        desc = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", desc)
        desc = re.sub(r"[=\[\]{|}]+", " ", desc)
        desc = re.sub(r" {2,}", " ", desc).strip()[:1000]

    # Final cleanup: strip any remaining wiki markup
    desc = re.sub(r"\{\{[^}]*\}\}", " ", desc)
    desc = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", desc)
    desc = re.sub(r"[=\[\]{|}]+", " ", desc)
    desc = re.sub(r"<[^>]+>", " ", desc)
    desc = re.sub(r" {2,}", " ", desc).strip()

    # Extract year
    year: int | None = None
    m2 = re.search(r"\b(20\d{2}|19\d{2})\b", wikitext)
    if m2:
        year = int(m2.group(1))

    return (desc, source_url, year)


def _file_slug(title: str) -> str:
    """Convert a File: title to a safe filename stem."""
    bare = title.removeprefix("File:")
    # Remove extension
    bare = re.sub(r"\.[a-zA-Z0-9]+$", "", bare)
    s = bare.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s[:80].strip("_")


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Download De Balie video descriptions and subtitles from Wikimedia Commons.

    For each file in Category:Videos_by_De_Balie, saves:
      {file_slug}.txt  — subtitle text or description (plain text)
      {file_slug}.json — metadata sidecar

    Args:
        output_dir: Directory to save files into.
        dry_run:    Log only, do not save.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[debalie_commons] [dry-run] would query Commons category: %s", _CATEGORY)
        return

    logger.info("[debalie_commons] Listing files in %s", _CATEGORY)
    try:
        files = _list_category_files(_CATEGORY)
    except Exception as exc:
        logger.error("[debalie_commons] Failed to list category: %s", exc)
        return

    logger.info("[debalie_commons] Found %d files", len(files))
    acq_date = datetime.date.today().isoformat()
    saved = 0

    for file_info in files:
        file_title = file_info.get("title", "")
        if not file_title.startswith("File:"):
            continue

        slug      = _file_slug(file_title)
        txt_path  = output_dir / f"{slug}.txt"
        json_path = output_dir / f"{slug}.json"

        if txt_path.exists() and json_path.exists():
            continue

        # Try Dutch subtitles first
        text = _fetch_timedtext(file_title, lang="nl")
        time.sleep(0.5)

        source_url: str = ""
        pub_year: int | None = None

        if not text:
            # Fall back to file description
            desc, source_url, pub_year = _fetch_file_description(file_title)
            time.sleep(0.5)
            if desc and len(desc) >= _MIN_TEXT_LEN:
                text = desc

        if not text:
            logger.debug("[debalie_commons] No usable text for %s", file_title)
            continue

        txt_path.write_text(text, encoding="utf-8")

        sidecar = {
            "source":           SOURCE,
            "file_title":       file_title,
            "slug":             slug,
            "source_url":       source_url,
            "license":          LICENSE,
            "publication_year": pub_year,
            "language_variant": "NL",
            "country":          COUNTRY,
            "domain":           DOMAIN,
            "acquisition_date": acq_date,
        }
        json_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
        saved += 1
        logger.info("[debalie_commons] %d: %s", saved, file_title[:60])

    logger.info("[debalie_commons] Done. %d files saved.", saved)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download De Balie videos from Wikimedia Commons (CC-BY-3.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
