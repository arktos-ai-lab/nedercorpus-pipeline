"""Download Saterland Frisian (stq) Wikipedia dump. License: CC-BY-SA.

Saterland Frisian (Seeltersk) is spoken in the Saterland region of Lower Saxony,
Germany. It is the smallest living Frisian language (~2,000 speakers).
"""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/stqwiki/latest/stqwiki-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("saterland_frisian_wiki", DUMP_URL, "stqwiki-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
