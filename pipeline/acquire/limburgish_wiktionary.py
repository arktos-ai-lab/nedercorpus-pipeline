"""Download Limburgish (li) Wiktionary dump (18MB). License: CC-BY-SA. Domain: lexical."""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/liwiktionary/latest/liwiktionary-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("limburgish_wiktionary", DUMP_URL, "liwiktionary-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
