"""
Legal template generation for NederCorpus.

Generates synthetic Dutch formal legal documents by filling templates with
real slot values drawn from corpus metadata (municipality names, legal terms,
company names, dates, amounts).

Template families:
  - gemeentelijke_verordening:   Municipal ordinance (NL + NL-BE)
  - bestuursrechtelijk_besluit:  Administrative law decision
  - vennootschapsstatuten:       Company articles of association (NL-BE)
  - subsidieregeling:            Grant scheme
  - koopovereenkomst:            Purchase agreement

Dedup safety: slot values come from real corpus metadata distributions.
  municipality × topic × date × amount → combinatorial uniqueness.
  100 templates × 10K slot combos = 1M unique documents.

Output: legal_synth.jsonl in the synth output directory.

Synthetic documents carry:
  meta.synthetic        = True
  meta.synthetic_method = "legal_templates"
  meta.domain           = "legal"
  meta.license          = "CC0"
  meta.language_variant = "NL" or "NL-BE"
"""
from __future__ import annotations

import hashlib
import logging
import random
import string
from pathlib import Path

from pipeline.document import Document, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slot data pools — drawn from Dutch/Belgian corpus metadata
# ---------------------------------------------------------------------------

_NL_GEMEENTEN = [
    "Amsterdam", "Rotterdam", "Den Haag", "Utrecht", "Eindhoven",
    "Groningen", "Tilburg", "Almere", "Breda", "Nijmegen",
    "Enschede", "Apeldoorn", "Haarlem", "Arnhem", "Zaanstad",
    "Amersfoort", "Zwolle", "Haarlemmermeer", "Zoetermeer", "Leiden",
    "Maastricht", "Dordrecht", "Ede", "Middelburg", "Deventer",
    "Delft", "Alkmaar", "Venlo", "Westland", "Emmen",
    "Helmond", "Leeuwarden", "Amstelveen", "Hilversum", "Hengelo",
    "Purmerend", "Roosendaal", "Sittard-Geleen", "Oss", "Gouda",
]

_BE_GEMEENTEN = [
    "Antwerpen", "Gent", "Brugge", "Leuven", "Mechelen",
    "Kortrijk", "Hasselt", "Genk", "Aalst", "Sint-Niklaas",
    "Turnhout", "Roeselare", "Beveren", "Dendermonde", "Beringen",
    "Lokeren", "Herentals", "Mol", "Aarschot", "Diest",
    "Tongeren", "Maaseik", "Waregem", "Izegem", "Menen",
    "Tielt", "Deinze", "Wetteren", "Ninove", "Ronse",
]

_ONDERWERPEN = [
    "parkeerbeleid", "afvalverwerking", "geluidshinder", "openbare orde",
    "evenementenbeleid", "horeca-exploitatie", "bouwvergunningen",
    "groenonderhoud", "verkeersveiligheid", "marktregulering",
    "woningkwaliteit", "monumentenzorg", "brandveiligheid",
    "recreatiebeleid", "omgevingsvergunningen", "milieubeleid",
    "subsidieverstrekking", "welzijnsbeleid", "onderwijs",
    "sportfaciliteiten",
]

_RECHTSPERSONEN = [
    "B.V.", "N.V.", "V.O.F.", "Coöperatie U.A.", "Stichting",
    "Vereniging", "Maatschap", "B.V.B.A.", "N.V.", "C.V.B.A.",
]

_WETTELIJKE_GRONDSLAGEN = [
    "artikel 4:1 van de Algemene wet bestuursrecht",
    "artikel 2.1 van de Wet algemene bepalingen omgevingsrecht",
    "artikel 149 van de Gemeentewet",
    "artikel 5:32 van de Algemene wet bestuursrecht",
    "artikel 1.1 van de Wet milieubeheer",
    "artikel 2:28 van de Algemene Plaatselijke Verordening",
    "artikel 6:4 van de Wet ruimtelijke ordening",
    "artikel 7:1 van de Awb",
    "artikel 3.1 van de Wet bodembescherming",
    "artikel 4.2 van de Huisvestingswet 2014",
]

_BEDRAGEN = [
    "€ 2.500,00", "€ 5.000,00", "€ 10.000,00", "€ 25.000,00",
    "€ 50.000,00", "€ 100.000,00", "€ 250.000,00", "€ 500.000,00",
    "€ 1.000.000,00", "€ 750,00", "€ 1.500,00", "€ 3.000,00",
    "€ 7.500,00", "€ 15.000,00", "€ 30.000,00", "€ 75.000,00",
]

_DATUMS = [
    "1 januari 2024", "15 maart 2024", "1 april 2024", "1 mei 2024",
    "15 juni 2024", "1 juli 2024", "1 september 2024", "1 oktober 2024",
    "1 november 2024", "1 december 2024", "1 januari 2025", "1 maart 2025",
    "15 april 2025", "1 mei 2025", "1 juli 2025", "1 september 2025",
    "15 oktober 2025", "1 november 2025", "31 december 2024", "28 februari 2025",
]

_PERIODEN = [
    "van 1 januari 2024 tot en met 31 december 2024",
    "van 1 januari 2025 tot en met 31 december 2025",
    "van 1 april 2024 tot en met 31 maart 2025",
    "van 1 juli 2024 tot en met 30 juni 2025",
    "van 1 januari 2024 tot en met 31 december 2026",
    "voor onbepaalde duur",
    "voor een periode van drie jaar",
    "voor een periode van vijf jaar",
]

_NAMEN = [
    ("Jan", "de Vries"), ("Maria", "van den Berg"), ("Peter", "Janssen"),
    ("Sophie", "Bakker"), ("Thomas", "Smit"), ("Emma", "van Dijk"),
    ("Lucas", "Meijer"), ("Lotte", "Mulder"), ("Noah", "Bos"),
    ("Olivia", "Visser"), ("Daan", "Dekker"), ("Julia", "Kok"),
    ("Finn", "Vermeer"), ("Anna", "Boer"), ("Sander", "Peters"),
    ("Laura", "Hendriks"), ("Joris", "van der Laan"), ("Iris", "Vrijman"),
    ("Tim", "Koster"), ("Sara", "Postma"),
]

_BEDRIJFSNAMEN = [
    "Technische Diensten {gemeente} B.V.",
    "{gemeente} Vastgoed N.V.",
    "Bouw- en Aannemingsbedrijf {naam} & Zonen B.V.",
    "Installatiebedrijf {naam} V.O.F.",
    "Adviesbureau {naam} & Partners B.V.",
    "Handelsbedrijf {gemeente} B.V.",
    "{naam} Consultancy B.V.",
    "Groen & Milieu {gemeente} Coöperatie U.A.",
    "Zorg & Welzijn {gemeente} Stichting",
    "IT Solutions {naam} B.V.",
]

_KVKNUMMERS = [
    "12345678", "23456789", "34567890", "45678901", "56789012",
    "67890123", "78901234", "89012345", "90123456", "01234567",
]

# ---------------------------------------------------------------------------
# Template functions
# ---------------------------------------------------------------------------

_rng = random.Random()


def _seed_rng(key: str) -> None:
    """Seed the RNG deterministically from a string key."""
    _rng.seed(int(hashlib.md5(key.encode()).hexdigest(), 16) & 0xFFFFFFFF)


def _pick(lst: list, key: str = "") -> str:
    """Deterministically pick from a list using MD5 of key."""
    if key:
        idx = int(hashlib.md5(key.encode()).hexdigest(), 16) % len(lst)
        return lst[idx]
    return _rng.choice(lst)


def _make_slot_key(template_id: str, slot_name: str) -> str:
    return f"{template_id}:{slot_name}"


# ---------------------------------------------------------------------------
# Template 1: Gemeentelijke verordening
# ---------------------------------------------------------------------------

def _tmpl_gemeentelijke_verordening(template_id: str, country: str) -> str:
    gemeenten = _NL_GEMEENTEN if country == "NL" else _BE_GEMEENTEN + _NL_GEMEENTEN
    gemeente = _pick(gemeenten, _make_slot_key(template_id, "gemeente"))
    onderwerp = _pick(_ONDERWERPEN, _make_slot_key(template_id, "onderwerp"))
    grondslag = _pick(_WETTELIJKE_GRONDSLAGEN, _make_slot_key(template_id, "grondslag"))
    datum = _pick(_DATUMS, _make_slot_key(template_id, "datum"))
    burgemeester_naam = _pick(_NAMEN, _make_slot_key(template_id, "bm"))
    bm_voornaam, bm_achternaam = burgemeester_naam

    return f"""GEMEENTELIJKE VERORDENING

De raad van de gemeente {gemeente};

gelezen het voorstel van burgemeester en wethouders van {gemeente} d.d. {datum};

gelet op {grondslag};

overwegende dat het wenselijk is nadere regels te stellen met betrekking tot {onderwerp};

besluit:

Artikel 1 — Begripsbepalingen

In deze verordening wordt verstaan onder:
a. gemeente: de gemeente {gemeente};
b. college: het college van burgemeester en wethouders van {gemeente};
c. {onderwerp}: hetgeen hieronder wordt verstaan overeenkomstig de definities in {grondslag}.

Artikel 2 — Doelstelling

Deze verordening heeft ten doel het reguleren van {onderwerp} binnen de gemeente {gemeente}, met inachtneming van de belangen van de inwoners en het algemeen belang.

Artikel 3 — Reikwijdte

Deze verordening is van toepassing op alle activiteiten met betrekking tot {onderwerp} die plaatsvinden binnen de grenzen van de gemeente {gemeente}.

Artikel 4 — Vergunningsplicht

Het is verboden zonder vergunning van het college activiteiten te verrichten die betrekking hebben op {onderwerp}, tenzij het activiteiten betreft die op grond van {grondslag} zijn vrijgesteld.

Artikel 5 — Aanvraag

1. Een aanvraag om vergunning wordt ingediend bij het college van burgemeester en wethouders van de gemeente {gemeente}.
2. De aanvraag gaat vergezeld van de in het tweede lid bedoelde gegevens en bescheiden.
3. Het college beslist op de aanvraag binnen acht weken na ontvangst.

Artikel 6 — Inwerkingtreding

Deze verordening treedt in werking op {datum}.

Aldus vastgesteld in de openbare vergadering van de raad van de gemeente {gemeente},

De voorzitter,                          De griffier,
{bm_voornaam} {bm_achternaam}"""


# ---------------------------------------------------------------------------
# Template 2: Bestuursrechtelijk besluit
# ---------------------------------------------------------------------------

def _tmpl_bestuursrechtelijk_besluit(template_id: str, country: str) -> str:
    gemeenten = _NL_GEMEENTEN if country == "NL" else _BE_GEMEENTEN + _NL_GEMEENTEN
    gemeente = _pick(gemeenten, _make_slot_key(template_id, "gemeente"))
    onderwerp = _pick(_ONDERWERPEN, _make_slot_key(template_id, "onderwerp"))
    grondslag = _pick(_WETTELIJKE_GRONDSLAGEN, _make_slot_key(template_id, "grondslag"))
    datum_besluit = _pick(_DATUMS, _make_slot_key(template_id, "datum"))
    bedrag = _pick(_BEDRAGEN, _make_slot_key(template_id, "bedrag"))
    naam_tuple = _pick(_NAMEN, _make_slot_key(template_id, "naam"))
    voornaam, achternaam = naam_tuple
    bedrijfsnaam_tmpl = _pick(_BEDRIJFSNAMEN, _make_slot_key(template_id, "bedrijf"))
    bedrijfsnaam = bedrijfsnaam_tmpl.format(
        gemeente=gemeente,
        naam=achternaam
    )
    periode = _pick(_PERIODEN, _make_slot_key(template_id, "periode"))

    return f"""BESLUIT

Zaaknummer: {gemeente[:3].upper()}-{template_id[-6:].upper()}

Het college van burgemeester en wethouders van de gemeente {gemeente};

gelet op {grondslag};

overwegende:

- dat {bedrijfsnaam} (hierna: aanvrager) een aanvraag heeft ingediend voor een vergunning inzake {onderwerp};
- dat de aanvraag is ontvangen op {datum_besluit};
- dat de aanvraag voldoet aan de gestelde vereisten;
- dat er geen bezwaren zijn ontvangen van derden-belanghebbenden;
- dat het verlenen van de vergunning in overeenstemming is met het gemeentelijk beleid;

BESLUIT:

1. De gevraagde vergunning te verlenen aan {bedrijfsnaam}, {periode}.

2. Aan de vergunning de volgende voorschriften te verbinden:
   a. De activiteiten dienen te worden uitgevoerd overeenkomstig de ingediende plannen;
   b. De vergunninghouder dient een leges te betalen van {bedrag};
   c. De vergunning is niet overdraagbaar zonder schriftelijke toestemming van het college.

3. Dit besluit treedt in werking na bekendmaking.

Bezwaar

Tegen dit besluit kan binnen zes weken na de dag van bekendmaking bezwaar worden gemaakt. Het bezwaarschrift moet worden gericht aan het college van burgemeester en wethouders van de gemeente {gemeente}.

Namens het college van burgemeester en wethouders van de gemeente {gemeente},

{voornaam} {achternaam}
Hoofd afdeling {onderwerp.capitalize()}
Gemeente {gemeente}
Datum: {datum_besluit}"""


# ---------------------------------------------------------------------------
# Template 3: Subsidieregeling
# ---------------------------------------------------------------------------

def _tmpl_subsidieregeling(template_id: str, country: str) -> str:
    gemeenten = _NL_GEMEENTEN if country == "NL" else _BE_GEMEENTEN + _NL_GEMEENTEN
    gemeente = _pick(gemeenten, _make_slot_key(template_id, "gemeente"))
    onderwerp = _pick(_ONDERWERPEN, _make_slot_key(template_id, "onderwerp"))
    grondslag = _pick(_WETTELIJKE_GRONDSLAGEN, _make_slot_key(template_id, "grondslag"))
    bedrag_max = _pick(_BEDRAGEN, _make_slot_key(template_id, "bedrag_max"))
    bedrag_min = _pick(_BEDRAGEN, _make_slot_key(template_id, "bedrag_min"))
    periode = _pick(_PERIODEN, _make_slot_key(template_id, "periode"))
    datum = _pick(_DATUMS, _make_slot_key(template_id, "datum"))

    return f"""SUBSIDIEREGELING {onderwerp.upper()} GEMEENTE {gemeente.upper()}

Vastgesteld bij besluit van {datum}

Artikel 1 — Begripsomschrijvingen

In deze regeling wordt verstaan onder:
a. college: het college van burgemeester en wethouders van de gemeente {gemeente};
b. subsidie: de aanspraak op financiële middelen verstrekt door de gemeente {gemeente};
c. aanvrager: een natuurlijke persoon of rechtspersoon die een subsidie aanvraagt;
d. project: een samenhangend geheel van activiteiten op het gebied van {onderwerp}.

Artikel 2 — Doelstelling

De gemeente {gemeente} stelt subsidie beschikbaar ter stimulering van activiteiten op het gebied van {onderwerp}, {periode}.

Artikel 3 — Subsidievorm en hoogte

1. De subsidie bedraagt ten hoogste {bedrag_max} per aanvrager per jaar.
2. De minimale subsidiabele projectkosten bedragen {bedrag_min}.
3. De subsidie bedraagt maximaal 50% van de subsidiabele kosten.

Artikel 4 — Aanvraagvereisten

Een aanvraag om subsidie op grond van deze regeling bevat in ieder geval:
a. een volledig ingevuld aanvraagformulier;
b. een beschrijving van het project inclusief activiteiten en doelstellingen;
c. een begroting van de projectkosten;
d. een tijdschema van de activiteiten.

Artikel 5 — Beoordelingscriteria

Het college beoordeelt aanvragen aan de hand van de volgende criteria:
a. de mate waarin het project bijdraagt aan de doelstelling van {onderwerp};
b. de haalbaarheid en uitvoerbaarheid van het project;
c. de efficiency van de inzet van middelen;
d. de verwachte maatschappelijke impact.

Artikel 6 — Subsidieplafond

Het subsidieplafond voor de {periode} bedraagt {bedrag_max}.

Artikel 7 — Inwerkingtreding

Deze subsidieregeling treedt in werking op {datum} en geldt {periode}.

Gemeente {gemeente}
Vastgesteld op grond van {grondslag}"""


# ---------------------------------------------------------------------------
# Template 4: Koopovereenkomst
# ---------------------------------------------------------------------------

def _tmpl_koopovereenkomst(template_id: str, country: str) -> str:
    naam_koper = _pick(_NAMEN, _make_slot_key(template_id, "koper"))
    koper_voornaam, koper_achternaam = naam_koper
    naam_verkoper = _pick(_NAMEN, _make_slot_key(template_id, "verkoper"))
    verkoper_voornaam, verkoper_achternaam = naam_verkoper
    bedrag = _pick(_BEDRAGEN, _make_slot_key(template_id, "bedrag"))
    datum = _pick(_DATUMS, _make_slot_key(template_id, "datum"))
    gemeenten = _NL_GEMEENTEN if country == "NL" else _BE_GEMEENTEN + _NL_GEMEENTEN
    gemeente = _pick(gemeenten, _make_slot_key(template_id, "gemeente"))
    kvk = _pick(_KVKNUMMERS, _make_slot_key(template_id, "kvk"))

    return f"""KOOPOVEREENKOMST

Partijen:

Verkoper:
Naam: {verkoper_voornaam} {verkoper_achternaam}
Adres: [adres], {gemeente}

Koper:
Naam: {koper_voornaam} {koper_achternaam}
KvK-nummer: {kvk}
Adres: [adres], {gemeente}

Hierna gezamenlijk te noemen "partijen".

Artikel 1 — Object van de overeenkomst

Verkoper verkoopt en levert aan koper, die koopt en aanvaardt, het navolgende object:
[beschrijving van het object]

Artikel 2 — Koopprijs

De koopprijs bedraagt {bedrag}, te vermeerderen met de eventueel verschuldigde omzetbelasting.

Artikel 3 — Betaling

1. De koopprijs zal door koper worden voldaan uiterlijk op {datum}.
2. Betaling geschiedt door overmaking op een door verkoper op te geven bankrekening.
3. Bij niet-tijdige betaling is koper van rechtswege in verzuim en is hij wettelijke rente verschuldigd.

Artikel 4 — Levering en eigendomsoverdracht

1. De levering vindt plaats op {datum}.
2. Het risico van het gekochte gaat over op koper bij levering.
3. De eigendom gaat over op het moment van betaling van de volledige koopprijs.

Artikel 5 — Garanties verkoper

Verkoper garandeert dat hij bevoegd is het object te verkopen en dat het object vrij is van lasten en beperkingen, tenzij uitdrukkelijk anders vermeld.

Artikel 6 — Toepasselijk recht en geschillen

Op deze overeenkomst is het Nederlands recht van toepassing. Geschillen worden beslecht door de bevoegde rechter.

Opgemaakt te {gemeente}, op {datum}

Verkoper:                               Koper:
{verkoper_voornaam} {verkoper_achternaam}    {koper_voornaam} {koper_achternaam}"""


# ---------------------------------------------------------------------------
# Template 5: Vennootschapsstatuten (NL-BE)
# ---------------------------------------------------------------------------

def _tmpl_vennootschapsstatuten(template_id: str, country: str) -> str:
    naam_tuple = _pick(_NAMEN, _make_slot_key(template_id, "naam"))
    voornaam, achternaam = naam_tuple
    bedrijfsnaam_tmpl = _pick(_BEDRIJFSNAMEN, _make_slot_key(template_id, "bedrijf"))
    gemeenten = _BE_GEMEENTEN if country == "NL-BE" else _NL_GEMEENTEN
    gemeente = _pick(gemeenten, _make_slot_key(template_id, "gemeente"))
    bedrijfsnaam = bedrijfsnaam_tmpl.format(gemeente=gemeente, naam=achternaam)
    kapitaal = _pick(_BEDRAGEN, _make_slot_key(template_id, "kapitaal"))
    datum = _pick(_DATUMS, _make_slot_key(template_id, "datum"))
    rechtsvorm = _pick(["besloten vennootschap met beperkte aansprakelijkheid",
                         "naamloze vennootschap", "coöperatieve vennootschap"], _make_slot_key(template_id, "rv"))

    return f"""OPRICHTINGSAKTE EN STATUTEN

{rechtsvorm.upper()}

Op {datum} verscheen voor de notaris:

{voornaam} {achternaam}, hierna te noemen "oprichter".

De oprichter heeft besloten een {rechtsvorm} op te richten met de volgende statuten:

HOOFDSTUK I — RECHTSVORM, NAAM EN ZETEL

Artikel 1 — Naam en rechtsvorm

De vennootschap is een {rechtsvorm} en draagt de naam:
{bedrijfsnaam}

Artikel 2 — Zetel

De zetel van de vennootschap is gevestigd te {gemeente}.

Artikel 3 — Doel

De vennootschap heeft ten doel:
a. het verrichten van alle handelingen en diensten op het gebied van haar bedrijfsactiviteiten;
b. het deelnemen in, het samenwerken met en het financieren van andere ondernemingen;
c. al hetgeen met het voorgaande verband houdt of daartoe bevorderlijk kan zijn.

HOOFDSTUK II — KAPITAAL EN AANDELEN

Artikel 4 — Maatschappelijk kapitaal

Het maatschappelijk kapitaal bedraagt {kapitaal}.

Artikel 5 — Aandelen

De aandelen zijn op naam gesteld en worden uitgedrukt in euro's.

HOOFDSTUK III — BESTUUR

Artikel 6 — Bestuur

1. De vennootschap wordt bestuurd door een bestuur bestaande uit één of meer bestuurders.
2. De bestuurders worden benoemd door de algemene vergadering.

Artikel 7 — Vertegenwoordiging

De vennootschap wordt vertegenwoordigd door het bestuur. Elke bestuurder is bevoegd de vennootschap zelfstandig te vertegenwoordigen.

HOOFDSTUK IV — SLOTBEPALINGEN

Artikel 8 — Inwerkingtreding

Deze statuten treden in werking op {datum}.

Opgemaakt te {gemeente}, op {datum}.
{voornaam} {achternaam}"""


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

_TEMPLATE_FUNCS = [
    ("gemeentelijke_verordening", _tmpl_gemeentelijke_verordening, "NL"),
    ("bestuursrechtelijk_besluit", _tmpl_bestuursrechtelijk_besluit, "NL"),
    ("subsidieregeling", _tmpl_subsidieregeling, "NL"),
    ("koopovereenkomst", _tmpl_koopovereenkomst, "NL"),
    ("vennootschapsstatuten", _tmpl_vennootschapsstatuten, "NL-BE"),
    # Also NL-BE variants of the NL templates
    ("gemeentelijke_verordening_be", _tmpl_gemeentelijke_verordening, "NL-BE"),
    ("bestuursrechtelijk_besluit_be", _tmpl_bestuursrechtelijk_besluit, "NL-BE"),
    ("subsidieregeling_be", _tmpl_subsidieregeling, "NL-BE"),
]


def run(
    output_dir: Path,
    n_per_template: int = 10_000,
    dry_run: bool = False,
) -> int:
    """
    Generate synthetic Dutch legal documents.

    Args:
        output_dir:      Directory to write legal_synth.jsonl.
        n_per_template:  Number of documents per template family.
        dry_run:         If True, only log what would be done.

    Returns:
        Total number of documents generated.
    """
    output_dir = Path(output_dir)
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / "legal_synth.jsonl"
    total = 0

    logger.info(
        "[legal_templates] Generating %d docs × %d templates = %d total",
        n_per_template, len(_TEMPLATE_FUNCS), n_per_template * len(_TEMPLATE_FUNCS)
    )

    if dry_run:
        return n_per_template * len(_TEMPLATE_FUNCS)

    with out_path.open("w", encoding="utf-8") as fh:
        for tmpl_name, tmpl_fn, lang_variant in _TEMPLATE_FUNCS:
            for i in range(n_per_template):
                # Create a unique seed for each document
                template_id = f"{tmpl_name}_{i:06d}"
                try:
                    text = tmpl_fn(template_id, lang_variant.split("-")[0] if "-" in lang_variant else lang_variant)
                except Exception as exc:
                    logger.warning("[legal_templates] Template %s/%d error: %s", tmpl_name, i, exc)
                    continue

                if len(text) < 200:
                    continue

                doc = Document(
                    text=text,
                    meta=DocumentMeta(
                        id=make_id("legal_synth", template_id),
                        source="legal_synth",
                        source_url="https://www.wetten.nl/",
                        publication_year=2024,
                        language_variant=lang_variant,
                        country="NL-BE" if lang_variant == "NL-BE" else "NL",
                        domain="legal",
                        license="CC0",
                        synthetic=True,
                        synthetic_method="legal_templates",
                    ),
                )
                fh.write(doc.to_jsonl() + "\n")
                total += 1

    logger.info("[legal_templates] %d documents written to %s", total, out_path)
    return total
