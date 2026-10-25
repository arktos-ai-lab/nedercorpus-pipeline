"""Download North Frisian (frr) Wikipedia dump. License: CC-BY-SA.

North Frisian is spoken in Schleswig-Holstein, Germany. It is a distinct
Frisian variety from West Frisian (fy-NL) spoken in the Netherlands.
"""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/frrwiki/latest/frrwiki-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("north_frisian_wiki", DUMP_URL, "frrwiki-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
