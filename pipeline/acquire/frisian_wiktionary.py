"""Download West Frisian (fy) Wiktionary dump. License: CC-BY-SA. Domain: lexical."""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/fywiktionary/latest/fywiktionary-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("frisian_wiktionary", DUMP_URL, "fywiktionary-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
