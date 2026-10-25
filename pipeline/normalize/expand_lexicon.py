"""One-time script: expand the historical Dutch lexicon from 149 to ~600 entries."""
import json
from pathlib import Path

LEXICON_PATH = Path(__file__).parent / "data" / "lexicon.json"

NEW_ENTRIES = {
    # ae/aer/aet variants
    "aen": "aan", "daer": "daar", "waer": "waar", "haer": "haar",
    "naer": "naar", "maer": "maar", "jaer": "jaar", "claer": "klaar",
    "slaet": "slaat", "gaet": "gaat", "laet": "laat", "staet": "staat",
    "nae": "na", "baer": "baar",
    # soo/oock
    "soo": "zo", "alsoo": "alzo", "oock": "ook", "ooc": "ook",
    # Demonstratives / articles
    "geene": "geen", "eene": "een",
    "deesen": "deze", "desen": "deze", "dese": "deze",
    "welcke": "welke", "welcken": "welken", "welck": "welk",
    "sulcke": "zulke", "sulcken": "zulken", "sulck": "zulk",
    "zulck": "zulk",
    "gemelte": "genoemde", "gemelden": "genoemde",
    "voorszeide": "voornoemde", "voorschreven": "voornoemde",
    "voorgemelden": "voornoemde",
    "deeser": "dezer", "deser": "dezer",
    # Verb forms
    "hadde": "had", "hebbe": "heb",
    "ware": "was", "worde": "werd", "wierdt": "werd", "wiert": "werd",
    "quam": "kwam", "quamen": "kwamen",
    "seyde": "zei", "seide": "zei", "zeyde": "zei",
    "seyden": "zeiden", "seiden": "zeiden",
    "comende": "komende", "comen": "komen", "comt": "komt",
    "connen": "kunnen", "conde": "kon", "const": "kon",
    "cunnen": "kunnen",
    # uy/ui variants
    "huys": "huis", "buyten": "buiten", "uijt": "uit", "uyt": "uit",
    "duym": "duim", "bruydt": "bruid", "kruydt": "kruid",
    "luyde": "luidde", "luyden": "luiden",
    "vuyl": "vuil",
    "bruyn": "bruin", "bruyne": "bruine",
    "schuyt": "schuit", "ruyter": "ruiter",
    "tuyn": "tuin", "tuynen": "tuinen",
    # Conjunctions/prepositions
    "ende": "en", "offte": "of", "ofte": "of", "oft": "of",
    "nochte": "noch",
    "dewijl": "doordat", "dewyle": "doordat",
    "dierhalven": "derhalve", "mitsdien": "derhalve",
    # Pronouns
    "sijne": "zijn", "sijnen": "zijn", "sijn": "zijn", "sijner": "zijn",
    "haere": "haar", "haeren": "haar",
    "hennen": "hun", "henne": "hun",
    "selve": "zelf", "selven": "zelf", "selfs": "zelf",
    "eenige": "enige", "eenigen": "enigen", "eenig": "enig",
    "yetwes": "iets", "yemand": "iemand", "yets": "iets",
    # Administrative/legal terms
    "borgemeester": "burgemeester",
    "baillieu": "baljuw",
    "fiskaal": "fiscaal",
    # Common nouns
    "stadt": "stad",
    "landt": "land",
    "volck": "volk", "volcken": "volken",
    "kercke": "kerk", "kerck": "kerk", "kercken": "kerken",
    "tijdt": "tijd", "tijden": "tijden",
    "plaetse": "plaats", "plaetsen": "plaatsen", "plaets": "plaats",
    "wercke": "werk", "werck": "werk", "wercken": "werken",
    "persoone": "persoon", "persoonen": "personen",
    "saken": "zaken", "sake": "zaak",
    "coop": "koop", "coopman": "koopman",
    "schepe": "schip",
    "gelt": "geld",
    "aensprecken": "aanspreken", "aengesproken": "aangesproken",
    # Archaic doubles
    "goet": "goed",
    "groodt": "groot", "groote": "grote",
    "kleyn": "klein", "kleyne": "kleine",
    "soet": "zoet",
    "bloedt": "bloed",
    "broodt": "brood",
    "woodt": "woord",
    "sware": "zware", "swaren": "zwaren", "swaer": "zwaar",
    # Direction
    "aldaer": "aldaar",
    "herwaerts": "herwaarts", "derwaerts": "derwaarts",
    "opwaerts": "opwaarts",
    # Numbers
    "twintigh": "twintig", "dertigh": "dertig", "viertigh": "veertig",
    "vijftigh": "vijftig", "tachtigh": "tachtig", "negentigh": "negentig",
    # Months (archaic)
    "januarij": "januari", "februarij": "februari",
    "apryl": "april", "meij": "mei", "junij": "juni",
    "julij": "juli",
    "7ber": "september", "8ber": "oktober", "9ber": "november", "Xber": "december",
    # y for ij
    "meyn": "mijn", "kleyn": "klein",
    "reyn": "rein",
    "wy": "wij", "zy": "zij", "my": "mij",
    "gy": "gij", "hy": "hij", "sy": "zij",
}

if __name__ == "__main__":
    existing = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    merged = {**existing, **NEW_ENTRIES}
    LEXICON_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Lexicon: {len(existing)} -> {len(merged)} entries")
