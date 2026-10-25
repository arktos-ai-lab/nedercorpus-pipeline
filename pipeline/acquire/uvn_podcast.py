"""
Download Universiteit van Nederland podcast episodes.

UvN publishes ~196 episodes under CC-BY. Their RSS feed (hosted on Omny)
provides episode titles, descriptions (show notes), audio enclosure URLs,
and optional Podcast 2.0 transcript links.

Acquisition strategy (per episode):
  1. If a <podcast:transcript> URL is present → download plain-text transcript,
     save {ep_id}.txt + {ep_id}.json. No audio needed.
  2. Otherwise → download audio enclosure (.mp3/.m4a), save {ep_id}.mp3 +
     {ep_id}.json. The `pipeline asr` stage will transcribe these to .txt.

License: CC-BY
Source:  https://www.universiteitvannederland.nl/podcasts
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "uvn_podcast"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "speech"

_RSS_URL = (
    "https://www.omnycontent.com/d/playlist/"
    "61ee9ca4-a1b2-4660-9651-b2b70035edf5/"
    "e3cbd078-9414-4691-b0f1-b2fe0110b4c7/"
    "f2f192d9-787a-4383-8c1a-b2fe0110b4d3/podcast.rss"
)

_USER_AGENT = "NederCorpus/0.1 (academic research; Dutch text corpus; CC-BY)"

# Podcast 2.0 namespace for <podcast:transcript>
_NS_PODCAST = "https://podcastindex.org/namespace/1.0"

_MIN_TEXT_LEN = 200
# Maximum audio file size to download (100 MB); skip if larger
_MAX_AUDIO_BYTES = 100 * 1024 * 1024


def _fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_streaming(url: str, dest: Path, timeout: int = 120) -> int:
    """
    Stream-download url to dest. Returns bytes written.
    Raises ValueError if Content-Length > _MAX_AUDIO_BYTES.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > _MAX_AUDIO_BYTES:
            raise ValueError(
                f"Audio file too large: {int(content_length)//1024//1024} MB"
            )
        data = resp.read()

    if len(data) > _MAX_AUDIO_BYTES:
        raise ValueError(f"Audio file too large: {len(data)//1024//1024} MB")

    dest.write_bytes(data)
    return len(data)


def _slug(title: str) -> str:
    """Convert episode title to a safe filename stem."""
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s[:80].strip("_")


def _fetch_rss(url: str) -> ET.Element:
    data = _fetch(url)
    return ET.fromstring(data.decode("utf-8", errors="replace"))


def _parse_episodes(root: ET.Element) -> list[dict]:
    """Extract episode metadata from RSS <item> elements."""
    episodes: list[dict] = []
    channel = root.find("channel")
    if channel is None:
        return episodes

    for item in channel.findall("item"):
        title_el      = item.find("title")
        pubdate_el    = item.find("pubDate")
        guid_el       = item.find("guid")
        link_el       = item.find("link")
        enclosure_el  = item.find("enclosure")

        title   = (title_el.text   or "").strip() if title_el   is not None else ""
        pubdate = (pubdate_el.text or "").strip() if pubdate_el is not None else ""
        guid    = (guid_el.text    or "").strip() if guid_el    is not None else ""
        link    = (link_el.text    or "").strip() if link_el    is not None else ""

        # Audio enclosure URL
        enclosure_url: str | None = None
        enclosure_ext: str = ".mp3"
        if enclosure_el is not None:
            enclosure_url = enclosure_el.get("url", "")
            enc_type = enclosure_el.get("type", "")
            if "mp4" in enc_type or "m4a" in enc_type or (enclosure_url and ".m4a" in enclosure_url):
                enclosure_ext = ".m4a"

        # Podcast 2.0 transcript tag
        transcript_url: str | None = None
        for transcript_el in item.findall(f"{{{_NS_PODCAST}}}transcript"):
            mime = transcript_el.get("type", "")
            if "text" in mime or "srt" in mime or "vtt" in mime:
                transcript_url = transcript_el.get("url")
                break

        # Parse year from RFC-2822 pubDate
        year: int | None = None
        if pubdate:
            m = re.search(r"\b(20\d{2})\b", pubdate)
            if m:
                year = int(m.group(1))

        ep_id = re.sub(r"[^a-z0-9_-]", "", guid.lower().replace("/", "_").replace(" ", "_"))[:80]
        if not ep_id:
            ep_id = _slug(title) or f"ep_{len(episodes):04d}"

        episodes.append({
            "ep_id":            ep_id,
            "title":            title,
            "publication_year": year,
            "source_url":       link or guid,
            "transcript_url":   transcript_url,
            "enclosure_url":    enclosure_url,
            "enclosure_ext":    enclosure_ext,
        })

    return episodes


def _fetch_transcript(url: str) -> str | None:
    """Download a Podcast 2.0 transcript and return plain text (strips SRT/VTT cues)."""
    try:
        data = _fetch(url, timeout=30)
        raw = data.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("[uvn_podcast] Transcript fetch failed %s: %s", url, exc)
        return None

    lines_out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"^\d{2}:\d{2}", line) or "-->" in line:
            continue
        if line.startswith("WEBVTT"):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line:
            lines_out.append(line)

    return " ".join(lines_out) if lines_out else None


def _build_sidecar(ep: dict, acq_date: str) -> dict:
    return {
        "source":           SOURCE,
        "ep_id":            ep["ep_id"],
        "title":            ep["title"],
        "source_url":       ep["source_url"],
        "license":          LICENSE,
        "publication_year": ep["publication_year"],
        "language_variant": "NL",
        "country":          COUNTRY,
        "domain":           DOMAIN,
        "acquisition_date": acq_date,
    }


def _save_episode(ep: dict, output_dir: Path, acq_date: str) -> bool:
    """
    Save episode to disk. Returns True if anything was saved.

    Priority:
      1. Podcast 2.0 transcript → save .txt (no audio download)
      2. Audio enclosure → save .mp3/.m4a for later ASR transcription
    """
    ep_id     = ep["ep_id"]
    json_path = output_dir / f"{ep_id}.json"
    txt_path  = output_dir / f"{ep_id}.txt"
    audio_ext = ep.get("enclosure_ext", ".mp3")
    audio_path = output_dir / f"{ep_id}{audio_ext}"

    # Already fully processed
    if json_path.exists() and (txt_path.exists() or audio_path.exists()):
        return False

    sidecar = _build_sidecar(ep, acq_date)

    # 1. Try Podcast 2.0 transcript
    if ep.get("transcript_url") and not txt_path.exists():
        text = _fetch_transcript(ep["transcript_url"])
        if text and len(text) >= _MIN_TEXT_LEN:
            full_text = ep["title"] + "\n\n" + text if ep["title"] else text
            txt_path.write_text(full_text, encoding="utf-8")
            json_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
            logger.debug("[uvn_podcast] Transcript saved: %s", ep_id)
            return True

    # 2. Download audio for ASR
    if ep.get("enclosure_url") and not audio_path.exists() and not txt_path.exists():
        try:
            nbytes = _fetch_streaming(ep["enclosure_url"], audio_path)
            json_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
            logger.debug(
                "[uvn_podcast] Audio saved: %s (%d MB)", ep_id, nbytes // 1024 // 1024
            )
            return True
        except Exception as exc:
            logger.warning("[uvn_podcast] Audio download failed for %s: %s", ep_id, exc)
            # Clean up partial file
            audio_path.unlink(missing_ok=True)

    return False


def download(
    output_dir: Path,
    dry_run: bool = False,
    max_episodes: int = 2000,
) -> None:
    """
    Download Universiteit van Nederland podcast episodes (CC-BY).

    For each episode, saves one of:
      {ep_id}.txt  + {ep_id}.json  — if a Podcast 2.0 transcript is available
      {ep_id}.mp3  + {ep_id}.json  — otherwise (audio for `pipeline asr` stage)

    Args:
        output_dir:   Directory to save episode files into.
        dry_run:      Log only, do not save.
        max_episodes: Maximum number of episodes to process.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[uvn_podcast] [dry-run] would fetch RSS: %s", _RSS_URL)
        return

    logger.info("[uvn_podcast] Fetching RSS: %s", _RSS_URL)
    try:
        root = _fetch_rss(_RSS_URL)
    except Exception as exc:
        logger.error("[uvn_podcast] Failed to fetch RSS: %s", exc)
        return

    episodes = _parse_episodes(root)
    logger.info("[uvn_podcast] Found %d episodes in feed", len(episodes))

    acq_date = datetime.date.today().isoformat()
    saved = 0

    for ep in episodes[:max_episodes]:
        try:
            if _save_episode(ep, output_dir, acq_date):
                saved += 1
                logger.info("[uvn_podcast] %d: %s", saved, ep["title"][:60].encode("ascii", "replace").decode("ascii"))
                time.sleep(0.5)
        except Exception as exc:
            logger.warning("[uvn_podcast] Error saving %s: %s", ep["ep_id"], exc)

    logger.info("[uvn_podcast] Done. %d episodes saved.", saved)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Universiteit van Nederland podcast (CC-BY)"
    )
    parser.add_argument("--output",       required=True, type=Path)
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--max-episodes", type=int, default=2000)
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run, max_episodes=args.max_episodes)


if __name__ == "__main__":
    main()
