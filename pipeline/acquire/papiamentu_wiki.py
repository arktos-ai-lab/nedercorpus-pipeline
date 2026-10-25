"""Download Papiamentu (pap) Wikipedia dump. License: CC-BY-SA.

Papiamentu is a Dutch-influenced creole language spoken in the ABC islands
(Aruba, Curaçao, Bonaire) — all part of the Kingdom of the Netherlands.
It blends Dutch, Portuguese, Spanish, and West African languages.
"""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/papwiki/latest/papwiki-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("papiamentu_wiki", DUMP_URL, "papwiki-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
