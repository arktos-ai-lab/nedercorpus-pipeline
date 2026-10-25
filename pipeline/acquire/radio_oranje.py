"""
Download Radio Oranje WWII broadcast recordings from Wikimedia Commons.

Radio Oranje was the Dutch government-in-exile's BBC radio service broadcast
to the occupied Netherlands (1940–1945). The recordings are in the public domain
(PDM 1.0) and hosted on Wikimedia Commons in:
  Category:Radio_Broadcasts_by_Radio_Oranje

Acquisition strategy:
  Use the MediaWiki API to enumerate all files in the category (and its
  subcategories), then download audio files (.wav, .ogg, .flac).
  JSON sidecar files store title, date, and source URL for the ASR stage.

After download, the `pipeline asr` stage transcribes the audio to .txt using
faster-whisper (large-v3, language=nl).

License: PDM 1.0 (Public Domain Mark — no copyright restrictions)
Domain:  historical
Country: NL
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "radio_oranje"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "historical"

_API_BASE  = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "NederCorpus/0.1 (academic research; Radio Oranje WWII recordings; PDM)"
_AUDIO_EXTS = {".wav", ".ogg", ".flac", ".mp3"}

# Root category and known subcategories to scan
_CATEGORIES = [
    "Radio_Broadcasts_by_Radio_Oranje",
]

_MAX_AUDIO_BYTES = 200 * 1024 * 1024   # 200 MB ceiling per file


def _fetch(url: str, timeout: int = 120, retries: int = 4) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 30.0 * (attempt + 1)
                logger.info("[radio_oranje] 429 rate limit, waiting %.0fs (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Failed after {retries} retries: {url}")


def _api_get(params: dict) -> dict:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    qs = urllib.parse.urlencode(params)
    url = f"{_API_BASE}?{qs}"
    data = _fetch(url)
    return json.loads(data.decode("utf-8"))


def _get_category_files(category: str) -> list[dict]:
    """
    Return all file pages in *category* (including subcategories, one level deep).

    Each entry: {"title": str, "url": str, "name": str, "description_url": str}
    """
    files: list[dict] = []
    seen_titles: set[str] = set()

    def _scan_cat(cat: str) -> None:
        cmcontinue: str | None = None
        while True:
            params: dict = {
                "action":       "query",
                "list":         "categorymembers",
                "cmtitle":      f"Category:{cat}",
                "cmtype":       "file|subcat",
                "cmlimit":      "500",
                "cmprop":       "title|type",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue

            resp = _api_get(params)
            members = resp.get("query", {}).get("categorymembers", [])

            for m in members:
                title = m["title"]
                mtype = m.get("type", "")
                if mtype == "subcat":
                    # recurse one level into subcategories
                    sub_name = title.removeprefix("Category:")
                    _scan_cat(sub_name)
                elif title not in seen_titles:
                    seen_titles.add(title)
                    # Only keep audio files
                    ext = Path(title).suffix.lower()
                    if ext in _AUDIO_EXTS:
                        files.append({"title": title})

            cont = resp.get("continue", {})
            cmcontinue = cont.get("cmcontinue")
            if not cmcontinue:
                break
            time.sleep(0.3)

    for cat in _CATEGORIES:
        _scan_cat(cat)

    return files


def _get_file_info(titles: list[str]) -> list[dict]:
    """
    Batch-query the Commons API for direct download URLs + metadata.

    Returns list of dicts: {title, url, description_url, date, name}
    """
    results: list[dict] = []
    # API allows up to 50 titles per request
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        params = {
            "action":   "query",
            "titles":   "|".join(batch),
            "prop":     "imageinfo",
            "iiprop":   "url|size|mediatype|metadata|extmetadata",
            "iiextmetadatafilter": "DateTime|DateTimeOriginal|ObjectName|License",
        }
        resp = _api_get(params)
        raw_pages = resp.get("query", {}).get("pages", [])
        # formatversion=2 returns a list; older API returns a dict keyed by page ID
        pages = raw_pages if isinstance(raw_pages, list) else list(raw_pages.values())
        for page in pages:
            title = page.get("title", "")
            ii = page.get("imageinfo", [{}])[0]
            url = ii.get("url", "")
            desc_url = ii.get("descriptionurl", "")
            if not url:
                continue
            # Try to extract a date
            ext_meta = ii.get("extmetadata", {})
            date_str = (
                ext_meta.get("DateTimeOriginal", {}).get("value", "")
                or ext_meta.get("DateTime", {}).get("value", "")
            )
            year: int | None = None
            m = re.search(r"\b(19|20)\d{2}\b", date_str)
            if m:
                year = int(m.group(0))
            else:
                # Try from filename
                m2 = re.search(r"\b(1940|1941|1942|1943|1944|1945)\b", title)
                if m2:
                    year = int(m2.group(0))

            name = title.removeprefix("File:").strip()
            results.append({
                "title":       name,
                "url":         url,
                "description_url": desc_url,
                "year":        year,
            })
        time.sleep(0.2)

    return results


def _safe_stem(name: str) -> str:
    """Turn a Commons filename into a safe filesystem stem."""
    stem = Path(name).stem
    stem = re.sub(r"[^\w\- ]+", "_", stem)
    stem = re.sub(r"\s+", "_", stem)
    return stem[:120].strip("_")


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download Radio Oranje audio files from Wikimedia Commons.

    Saves {stem}.wav/.ogg + {stem}.json sidecar to output_dir/audio/.
    Skips files already downloaded (resume-safe).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(exist_ok=True)

    log_path = output_dir / "acquisition_log.jsonl"
    acq_date = datetime.date.today().isoformat()

    logger.info("[radio_oranje] Enumerating Wikimedia Commons category …")
    file_entries = _get_category_files("Radio_Broadcasts_by_Radio_Oranje")
    logger.info("[radio_oranje] Found %d audio files in category", len(file_entries))

    if not file_entries:
        logger.warning("[radio_oranje] No files found — check category name or API access")
        return

    if dry_run:
        logger.info("[radio_oranje] [dry-run] Would download %d files", len(file_entries))
        return

    titles = [e["title"] for e in file_entries]
    infos  = _get_file_info(titles)
    logger.info("[radio_oranje] Got download URLs for %d files", len(infos))

    saved = 0
    for info in infos:
        name  = info["title"]
        url   = info["url"]
        year  = info["year"]
        stem  = _safe_stem(name)
        ext   = Path(name).suffix.lower() or ".ogg"
        audio_path = audio_dir / f"{stem}{ext}"
        json_path  = audio_dir / f"{stem}.json"

        if audio_path.exists() and json_path.exists():
            continue

        try:
            data = _fetch(url, timeout=120)
        except Exception as exc:
            logger.warning("[radio_oranje] Download failed %s: %s", name, exc)
            time.sleep(1.0)
            continue

        if len(data) > _MAX_AUDIO_BYTES:
            logger.warning("[radio_oranje] File too large (%d MB), skipping: %s",
                           len(data) // 1024 // 1024, name)
            continue

        audio_path.write_bytes(data)

        sidecar = {
            "source":           SOURCE,
            "title":            name,
            "source_url":       info["description_url"],
            "license":          LICENSE,
            "publication_year": year or 1942,   # default to mid-war if unknown
            "language_variant": "NL",
            "country":          COUNTRY,
            "domain":           DOMAIN,
            "acquisition_date": acq_date,
        }
        json_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

        saved += 1
        logger.info("[radio_oranje] Saved %s (%d KB)", audio_path.name, len(data) // 1024)

        # Log to acquisition_log.jsonl
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"source": SOURCE, "url": url, "license": LICENSE,
                                "acquisition_date": acq_date}, ensure_ascii=False) + "\n")
        # Large WAV files: longer sleep to avoid Wikimedia CDN rate limits
        time.sleep(3.0)

    logger.info("[radio_oranje] Done. %d audio files saved to %s", saved, audio_dir)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Radio Oranje WWII recordings from Wikimedia Commons (PDM/PD)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
