"""Regelbasert kategorisering av varenavn (uten AI).

Lengste nøkkelord som finnes i navnet vinner, så «kokosmelk» blir hermetikk og
«melk» blir meieri. Hvert nøkkelord kan ha egen holdbarhet (dager); ellers brukes
kategoriens standard.
"""
import difflib
import re

DEFAULT_SHELF = {
    "Frukt og grønt": 7, "Brød og bakst": 4, "Kjøtt": 3, "Fisk og sjømat": 2, "Pålegg": 10,
    "Meieri og egg": 10, "Frysevarer": 120, "Tørrvarer": 365, "Hermetikk og sauser": 365,
    "Snacks og godteri": 120, "Drikke": 120, "Barn": 365, "Husholdning": 999, "Hygiene": 999,
    "Annet": 30,
}

# nøkkelord: holdbarhet (None = kategoriens standard)
KEYWORDS = {
    "Frukt og grønt": {
        "eple": 21, "epler": 21, "banan": 5, "bananer": 5, "appelsin": 14, "klementin": 14, "mandarin": 14,
        "sitron": 21, "lime": 21, "pære": 7, "pærer": 7, "drue": 7, "druer": 7, "jordbær": 3, "bringebær": 3,
        "blåbær": 5, "melon": 7, "vannmelon": 7, "ananas": 5, "mango": 5, "kiwi": 10, "avokado": 4,
        "fersken": 5, "nektarin": 5, "plomme": 5, "granateple": 14, "frukt": 7, "bær": 4,
        "agurk": 7, "tomat": 7, "tomater": 7, "cherrytomat": 7, "paprika": 10, "salat": 5, "isbergsalat": 7,
        "ruccola": 4, "spinat": 4, "babyspinat": 4, "brokkoli": 7, "blomkål": 7, "kål": 21, "hodekål": 30,
        "rødkål": 30, "rosenkål": 10, "gulrot": 21, "gulrøtter": 21, "potet": 30, "poteter": 30,
        "søtpotet": 21, "løk": 30, "rødløk": 30, "vårløk": 7, "purre": 14, "hvitløk": 60, "ingefær": 21,
        "sopp": 5, "sjampinjong": 5, "squash": 10, "aubergine": 7, "selleri": 14, "stangselleri": 14,
        "kålrot": 30, "pastinakk": 21, "rødbet": 21, "rødbeter": 21, "mais": 5, "sukkererter": 5,
        "aspargesbønner": 5, "asparges": 4, "reddik": 7, "chili": 14, "urter": 5, "basilikum": 5,
        "persille": 7, "koriander": 5, "dill": 5, "gressløk": 5, "grønnsaker": 7, "grønnsak": 7,
        "rotgrønnsaker": 21, "salatmiks": 4, "pak choi": 5, "fennikel": 10, "edamame": 5,
    },
    "Brød og bakst": {
        "brød": 4, "grovbrød": 4, "loff": 3, "rundstykke": 2, "rundstykker": 2, "baguette": 2,
        "ciabatta": 2, "lefse": 14, "lefser": 14, "tortilla": 20, "tortillalefse": 20, "tortillalefser": 20,
        "wraps": 20, "pitabrød": 14, "nanbrød": 7, "knekkebrød": 180, "flatbrød": 180, "boller": 3,
        "bolle": 3, "kanelbolle": 3, "skolebolle": 2, "kake": 4, "rugbrød": 7, "polarbrød": 14,
        "pølsebrød": 7, "hamburgerbrød": 7, "pizzabunn": 14, "croissant": 3, "vafler": 3,
    },
    "Kjøtt": {
        "kjøttdeig": 3, "karbonadedeig": 3, "kyllingkjøttdeig": 3, "kylling": 3, "kyllingfilet": 3,
        "kyllinglår": 3, "kyllingvinger": 3, "hel kylling": 3, "kalkun": 3, "svin": 3, "svinekjøtt": 3,
        "koteletter": 3, "kotelett": 3, "ribbe": 4, "nakkekoteletter": 3, "biff": 3, "entrecote": 4,
        "indrefilet": 4, "ytrefilet": 4, "storfe": 3, "lam": 3, "lammekjøtt": 3, "fårikål": 3, "elg": 3,
        "reinsdyr": 3, "kjøttkaker": 4, "kjøttboller": 4, "medisterkaker": 4, "pølse": 10, "pølser": 10,
        "wienerpølser": 10, "grillpølser": 10, "bacon": 10, "kjøtt": 3, "strimlet": 3, "gryterett": 3,
        "pulled pork": 4, "hamburger": 3, "burgere": 3, "svinefilet": 3, "flintsteik": 3, "kebab": 3,
    },
    "Fisk og sjømat": {
        "laks": 2, "laksefilet": 2, "ørret": 2, "torsk": 2, "torskefilet": 2, "sei": 2, "seifilet": 2,
        "hyse": 2, "hysefilet": 2, "makrell": 2, "sild": 7, "fisk": 2, "fiskefilet": 2, "reker": 2,
        "scampi": 3, "krabbe": 2, "blåskjell": 2, "skalldyr": 2, "fiskekaker": 4, "fiskeboller": 120,
        "fiskepudding": 7, "fiskegrateng": 4, "røkelaks": 10, "røkt laks": 10, "gravlaks": 10,
        "tunfisk": 365, "kaviar": 60, "fiskepinner": 120, "steinbit": 2, "kveite": 2, "uer": 2,
    },
    "Pålegg": {
        "skinke": 7, "kokt skinke": 7, "salami": 21, "servelat": 10, "leverpostei": 14, "postei": 14,
        "makrell i tomat": 365, "syltetøy": 120, "jordbærsyltetøy": 120, "nugatti": 180, "sjokoladepålegg": 180,
        "peanøttsmør": 180, "honning": 365, "brunost": 60, "gudbrandsdalsost": 60, "prim": 30,
        "smøreost": 21, "kyllingpålegg": 7, "kalkunpålegg": 7, "hamburgerrygg": 7, "pålegg": 10,
        "rullepølse": 10, "fårepølse": 30, "majones": 120, "remulade": 120, "hummus": 7, "mikado": 180,
    },
    "Meieri og egg": {
        "melk": 8, "lettmelk": 8, "helmelk": 8, "skummet": 8, "ekstra lett": 8, "laktosefri": 10,
        "havremelk": 30, "kefir": 14, "kulturmelk": 14, "biola": 14, "yoghurt": 14, "gresk yoghurt": 14,
        "skyr": 14, "fløte": 10, "matfløte": 10, "kremfløte": 10, "rømme": 14, "lettrømme": 14,
        "crème fraîche": 14, "creme fraiche": 14, "smør": 60, "meierismør": 60, "margarin": 60, "bremykt": 60,
        "ost": 30, "gulost": 30, "norvegia": 30, "jarlsberg": 30, "revet ost": 21, "mozzarella": 10,
        "parmesan": 60, "fetaost": 21, "feta": 21, "cottage cheese": 10, "kesam": 10, "kremost": 21,
        "philadelphia": 21, "snøfrisk": 21, "egg": 30, "eggehvite": 10, "pudding": 10, "dessert": 10,
        "sjokolademelk": 14, "o'boy": 365,
    },
    "Frysevarer": {
        "frossen": 120, "frosne": 120, "frys": 120, "frosen": 120, "pizza": 120, "grandiosa": 120,
        "frossenpizza": 120, "pommes frites": 120, "frityrpotet": 120, "wok-grønnsaker": 120,
        "frosne bær": 180, "frosne grønnsaker": 180, "erter": 180, "lasagne": 4, "nuggets": 120,
        "kyllingnuggets": 120, "vårruller": 120, "dumplings": 120, "is": 120, "iskrem": 120, "isbåter": 120,
        "sorbet": 120, "frossenfisk": 120,
    },
    "Tørrvarer": {
        "pasta": 365, "spagetti": 365, "spaghetti": 365, "penne": 365, "makaroni": 365, "tagliatelle": 365,
        "fusilli": 365, "lasagneplater": 365, "nudler": 365, "risnudler": 365, "ris": 365, "jasminris": 365,
        "basmatiris": 365, "fullkornsris": 365, "risotto": 365, "couscous": 365, "bulgur": 365, "quinoa": 365,
        "havregryn": 365, "havre": 365, "müsli": 180, "musli": 180, "granola": 180, "frokostblanding": 180,
        "cornflakes": 180, "mel": 365, "hvetemel": 365, "sammalt": 365, "sukker": 999, "melis": 999,
        "bakepulver": 365, "natron": 999, "gjær": 30, "tørrgjær": 365, "kakao": 365, "vaniljesukker": 365,
        "linser": 365, "kikerter": 365, "bønner": 365, "salt": 999, "pepper": 365, "krydder": 365,
        "tacokrydder": 365, "taco spice": 365, "kanel": 365, "paprikapulver": 365, "karri": 365, "curry": 365,
        "buljong": 365, "kraft": 365, "olje": 365, "olivenolje": 365, "rapsolje": 365, "eddik": 999,
        "potetmos": 365, "pannekakemiks": 365, "nøtter": 180, "mandler": 180, "rosiner": 180, "frø": 180,
        "solsikkekjerner": 180, "chiafrø": 365, "knekkebrødmiks": 365, "kaffe": 120, "te": 365,
    },
    "Hermetikk og sauser": {
        "hermetikk": 365, "hakkede tomater": 365, "tomatpuré": 365, "tomatpure": 365, "passata": 365,
        "kokosmelk": 365, "kidneybønner": 365, "sorte bønner": 365, "mais på boks": 365, "tacosaus": 365,
        "salsa": 120, "pesto": 30, "ketchup": 180, "sennep": 180, "soyasaus": 365, "soya": 365,
        "sweet chili": 365, "currypaste": 180, "curry paste": 180, "kormasaus": 365, "saus": 120,
        "pastasaus": 365, "dressing": 60, "aioli": 30, "grillsaus": 180, "brun saus": 365, "sjysaus": 365,
        "hoisin": 365, "oliven": 60, "syltede": 180, "agurksalat": 30, "rødbeter på glass": 180,
    },
    "Snacks og godteri": {
        "chips": 120, "potetgull": 120, "cheez doodles": 120, "popcorn": 120, "nøttemiks": 120,
        "sjokolade": 180, "melkesjokolade": 180, "smash": 180, "godteri": 180, "smågodt": 180, "kjeks": 180,
        "small": 120, "snacks": 120, "bar": 120, "proteinbar": 120, "rawbar": 120, "lakris": 180, "vingummi": 180, "drops": 365, "twist": 180, "kvikk lunsj": 180,
        "tyggegummi": 365, "seigmenn": 180, "pastiller": 365, "nachos": 120, "tortillachips": 120,
    },
    "Drikke": {
        "juice": 10, "appelsinjuice": 10, "eplejuice": 10, "brus": 180, "cola": 180, "pepsi": 180,
        "solo": 180, "farris": 180, "mineralvann": 180, "vann": 365, "saft": 180, "iste": 180, "smoothie": 7,
        "energidrikk": 180, "øl": 180, "vin": 365, "alkoholfri": 180, "kakaomelk": 14,
    },
    "Barn": {
        "bleie": 999, "bleier": 999, "våtservietter": 999, "barnemat": 180, "grøt": 365, "barnegrøt": 365,
        "smoothie barn": 30, "morsmelkerstatning": 365, "smokk": 999, "klemmepose": 180,
    },
    "Husholdning": {
        "oppvaskmiddel": 999, "oppvasktabletter": 999, "maskinoppvask": 999, "zalo": 999, "vaskemiddel": 999,
        "tøyvask": 999, "skyllemiddel": 999, "dopapir": 999, "toalettpapir": 999, "tørkerull": 999,
        "kjøkkenrull": 999, "servietter": 999, "søppelpose": 999, "søppelposer": 999, "bæreposer": 999,
        "aluminiumsfolie": 999, "folie": 999, "plastfolie": 999, "bakepapir": 999, "fryseposer": 999,
        "matboks": 999, "svamp": 999, "stålull": 999, "stålskrubb": 999, "oppvaskbørste": 999, "klut": 999,
        "rengjøring": 999, "allrengjøring": 999, "klor": 999, "lyspære": 999, "batterier": 999,
        "telys": 999, "stearinlys": 999, "lys": 999, "kaffefilter": 999,
    },
    "Hygiene": {
        "tannkrem": 999, "tannbørste": 999, "sjampo": 999, "shampoo": 999, "balsam": 999, "såpe": 999,
        "håndsåpe": 999, "dusjsåpe": 999, "deodorant": 999, "deo": 999, "bomullspads": 999, "q-tips": 999,
        "bind": 999, "tamponger": 999, "barberhøvel": 999, "solkrem": 999, "krem": 999, "bodylotion": 999,
        "plaster": 999, "paracet": 999, "ibux": 999, "tanntråd": 999, "munnskyll": 999,
    },
}

# Ord som overstyrer til frysevarer uansett resten av navnet
FROZEN = re.compile(r"\b(frossen|frosne|frosset|fryse|frys)\w*", re.I)

_INDEX = sorted(
    ((kw, cat, shelf) for cat, kws in KEYWORDS.items() for kw, shelf in kws.items()),
    key=lambda x: -len(x[0]),
)


def _norm(s):
    s = (s or "").lower().replace("é", "e").replace("è", "e")
    return re.sub(r"\s+", " ", s).strip()


def categorize(name):
    """Returnerer (kategori, holdbarhet_dager, nøkkelord) for et varenavn."""
    n = _norm(name)
    if not n:
        return "Annet", DEFAULT_SHELF["Annet"], None
    if FROZEN.search(n) and "fryseposer" not in n:
        return "Frysevarer", 120, "frossen"
    words = set(re.findall(r"[a-zæøå'\-]+", n))
    for kw, cat, shelf in _INDEX:
        k = _norm(kw)
        if " " in k:
            hit = k in n
        elif len(k) <= 3:
            hit = k in words  # korte ord («is», «te», «ost») må være hele ord
        else:
            hit = k in n  # sammensatte ord: «grovbrød», «kyllingfilet», «laksefilet»
        if hit:
            return cat, shelf or DEFAULT_SHELF[cat], kw
    return "Annet", DEFAULT_SHELF["Annet"], None


def pretty_name(name):
    n = re.sub(r"\s+", " ", (name or "").strip())
    return n[:1].upper() + n[1:]


def closest(name, candidates, cutoff=0.84):
    """Nærmeste eksisterende varenavn (tåler små skrivefeil)."""
    n = _norm(name)
    lower = {_norm(c): c for c in candidates}
    if n in lower:
        return lower[n]
    # entall/flertall og bestemt form: «banan» ↔ «bananer», «tomat» ↔ «tomatene»
    for k, orig in lower.items():
        short, long_ = (n, k) if len(n) <= len(k) else (k, n)
        if len(short) >= 4 and long_.startswith(short) and len(long_) - len(short) <= 3:
            return orig
    m = difflib.get_close_matches(n, list(lower), n=1, cutoff=cutoff)
    return lower[m[0]] if m else None
