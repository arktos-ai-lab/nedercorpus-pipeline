"""Download Afrikaans (af) Wikibooks dump. License: CC-BY-SA. Domain: educational.

Afrikaans is a daughter language of Dutch descended from 17th-century Cape Dutch.
"""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/afwikibooks/latest/afwikibooks-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("afrikaans_wikibooks", DUMP_URL, "afwikibooks-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
