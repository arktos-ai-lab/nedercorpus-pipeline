"""Download Limburgish (li) Wikinews dump. License: CC-BY. Domain: news."""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/liwikinews/latest/liwikinews-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("limburgish_wikinews", DUMP_URL, "liwikinews-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY", dry_run=dry_run)
