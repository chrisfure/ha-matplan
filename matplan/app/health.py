"""Markering av ultraprosessert mat og bedre alternativer – uten AI.

Vurderingen bygger på NOVA-systemet (gruppe 4 = ultraprosessert), og alternativene
følger de norske kostrådene (Helsedirektoratet 2024, basert på NNR 2023) og WHO:
mer grønnsaker, frukt, belgfrukter, fisk og grove kornvarer; mindre bearbeidet kjøtt,
sukker og salt; vann som tørstedrikk; velg Nøkkelhull-merket.

Der NOVA og kostrådene spriker (f.eks. myk margarin og fiskeboller, som er bearbeidet,
men anbefales/godtas av kostrådene), følger vi kostrådene og markerer ikke varen.
"""
import re

import db

# (mønster, merkelapper, hvorfor, bedre alternativ)
RULES = [
    (r"pølse|wiener|frankfurter|grillpølse|kjøttpølse|chorizo",
     ["upf", "meat"], "Pølser er ultraprosessert og bearbeidet kjøtt, som kostrådene og WHO sier vi bør begrense.",
     "Kyllingfilet, kjøttdeig med lite fett eller fiskekaker med høy fiskeandel. Lag gjerne egne kjøttboller av kjøttdeig."),
    (r"bacon|salami|servelat|pepperoni|morr|spekepølse",
     ["upf", "meat"], "Bearbeidet kjøtt med mye salt – kostrådene anbefaler å begrense det.",
     "Bruk mindre, eller bytt med kyllingfilet eller magert svinekjøtt i strimler. På brødskiva: makrell, egg eller hummus."),
    (r"kokt skinke|kalkunpålegg|kyllingpålegg|hamburgerrygg|leverpostei|kjøttpålegg",
     ["meat"], "Bearbeidet kjøttpålegg – kostrådene anbefaler å velge det sjeldnere og helst magert (Nøkkelhull).",
     "Fisk på boks (makrell, sardiner, laks), egg, hummus eller rester av kylling du skjærer selv."),
    (r"nuggets|kyllingburger|chicken ?burger|kjøttkaker|kjøttboller|karbonader|hamburger(?!brød)",
     ["upf", "meat"], "Ferdige kjøttprodukter er ofte ultraprosessert, med mye salt og tilsetninger.",
     "Lag selv av kjøttdeig eller kyllingfilet – det tar kort tid og gir mindre salt."),
    (r"fiskepinner|fiskeburger|panert fisk",
     ["upf"], "Panerte fiskeprodukter er ultraprosessert, med lavere fiskeandel.",
     "Torsk, sei eller laks i ovnen med litt grovt rasp – like barnevennlig. Fisk to–tre ganger i uka er et av kostrådene."),
    (r"pizza(?!bunn|deig|mel)|grandiosa|big one|lasagne ferdig|ferdigrett|ferdigmiddag|middagsgryte|mikromat",
     ["upf"], "Ferdigretter er ultraprosessert og har ofte mye salt og mettet fett.",
     "Hjemmelaget versjon med grov bunn eller fullkornspasta og mye grønnsaker. Lag dobbel porsjon og frys."),
    (r"posesuppe|toro|nudler|mr\.? ?lee|cup noodles|risotto pose",
     ["upf"], "Pose- og instantmat er ultraprosessert og svært salt.",
     "Fullkornspasta, grove nudler eller suppe laget av grønnsaker og buljong med lite salt."),
    (r"chips|potetgull|cheez|doodles|nachos|tortillachips|popchips|ostepop|bugles|smash",
     ["upf"], "Snacks er ultraprosessert, med mye salt og fett.",
     "Usaltede nøtter, popcorn poppet selv, eller grønnsaksstaver med hummus."),
    (r"\bbrus\b|cola|pepsi|\bsolo\b|fanta|sprite|\burge\b|mozell|energidrikk|red bull|monster|\bburn\b|\biste\b|ice tea|saft|squash|sportsdrikk",
     ["upf", "sugar"], "Søte drikker er ultraprosessert. Kostrådene anbefaler vann som tørstedrikk, og WHO å begrense fritt sukker.",
     "Vann, farris eller kullsyrevann med sitron, lime eller frosne bær."),
    (r"kjeks|cookies|småkaker|muffins|wienerbrød|kanelsnurr|donut|\bkaker?\b|sjokoladekake|bløtkake|kakemiks|vafler ferdig|brownies",
     ["upf", "sugar"], "Industribakst og kjeks er ultraprosessert med mye sukker.",
     "Frukt, knekkebrød med pålegg, eller hjemmebakte grove boller og havrekjeks."),
    (r"sjokolade(?!pålegg)|smågodt|godteri|seigmenn|lakris|karamell|sjokoladeplate|kvikk lunsj|melkesjokolade|m&m|twist|non stop",
     ["upf", "sugar"], "Godteri og sjokolade er ultraprosessert med mye sukker.",
     "Frukt og bær, eller litt mørk sjokolade (70 %). Kos er helt greit av og til – i små mengder."),
    (r"\bis\b|iskrem|mjølkeis|sandwich-is|pinneis|krone-is|kroneis|lollipop",
     ["upf", "sugar"], "Iskrem er ultraprosessert med mye sukker.",
     "Frosne bær blandet med naturell yoghurt, eller hjemmelagde ispinner av moset frukt."),
    (r"frokostblanding|cornflakes|corn flakes|cheerios|coco pops|kelloggs|crunchy|granola|frosties|honey|sjokoladeflak",
     ["upf", "sugar"], "Søte frokostblandinger er ultraprosessert, med mye sukker og lite fiber.",
     "Havregryn eller usøtet müsli med bær og melk – Nøkkelhull-merket."),
    (r"yoghurt (med |jordbær|vanilje|skogsbær|bringebær|fersken|mango|sjokolade)|fruktyoghurt|go'? ?morgen|skyr (med|jordbær|vanilje|blåbær)|\bpetit|dessert|pudding|fløtepudding",
     ["upf", "sugar"], "Smaksatt yoghurt og desserter er ofte ultraprosessert med tilsatt sukker.",
     "Naturell yoghurt eller skyr med bær og litt havre eller nøtter."),
    (r"\bloff\b|kneippbrød|pølsebrød|hamburgerbrød|toastbrød|polarbrød|fine rundstykker|baguett|ciabatta",
     ["upf"], "Fint industribrød er ultraprosessert og har lite fiber.",
     "Grovt brød – se Brødskalaen og velg «Ekstra grovt» (¾–4/4) eller Nøkkelhull-merket."),
    (r"nugatti|sjokoladepålegg|sjokoladekrem|nutella",
     ["upf", "sugar"], "Søtt pålegg er ultraprosessert med mye sukker og fett.",
     "Peanøttsmør av 100 % peanøtter, banan i skiver eller syltetøy med lite sukker."),
    (r"tacokrydder|taco krydder|krydderblanding|sausmix|saus mix|bearnaise pose|brun saus pose|dressingmix|buljongterning",
     ["upf"], "Pose- og krydderblandinger er ultraprosessert og svært salte.",
     "Bland krydder selv (spisskummen, paprika, chili, hvitløk, oregano) – mye mindre salt."),
    (r"ketchup|majones|remulade|dressing|aioli|grillsaus|bbq-saus|sweet chili",
     ["upf"], "Sauser og dressinger er ofte ultraprosessert med sukker og salt.",
     "Yoghurt- eller kesamdressing med urter, salsa eller en enkel tomatsaus laget av hakkede tomater."),
    (r"vegetarburger|veggisburger|vegansk pølse|vegetarpølse|kjøttfri|planteburger|quorn|vegetarbiter|soyabiter",
     ["upf"], "Kjøtterstatninger er ofte ultraprosessert.",
     "Bønner, linser, kikerter eller tofu – kostrådene anbefaler mer belgfrukter."),
    (r"smøreost|baconost|rekeost|prim\b|kremost (med|urter)",
     ["upf"], "Smøreost er ultraprosessert og ofte salt.",
     "Hvit ost i skiver (gjerne Nøkkelhull-merket), cottage cheese eller hummus."),
]
_RULES = [(re.compile(p, re.I), tags, why, alt) for p, tags, why, alt in RULES]
TAG_LABEL = {"upf": "Ultraprosessert", "meat": "Bearbeidet kjøtt", "sugar": "Mye sukker"}


def _overrides():
    return {str(k): v for k, v in (db.get_setting("upf_override") or {}).items()}


def classify(name, pid=None, overrides=None):
    """Returnerer None eller {"upf": bool, "tags": [...], "labels": [...], "why", "alt", "manual"}."""
    ov = overrides if overrides is not None else _overrides()
    key = str(pid) if pid is not None else None
    if key and key in ov:
        if not ov[key]:
            return {"upf": False, "tags": [], "labels": [], "why": "", "alt": "", "manual": True, "cleared": True}
        hit = _match(name)
        if hit:
            hit.update(manual=True, upf=True)
            if "upf" not in hit["tags"]:
                hit["tags"] = ["upf"] + hit["tags"]
                hit["labels"] = [TAG_LABEL[t] for t in hit["tags"]]
            return hit
        return {"upf": True, "tags": ["upf"], "labels": [TAG_LABEL["upf"]], "manual": True,
                "why": "Merket som ultraprosessert av dere.",
                "alt": "Velg varer med kort ingrediensliste, gjerne Nøkkelhull-merket, eller lag selv."}
    return _match(name)


def _match(name):
    n = (name or "").lower()
    for rx, tags, why, alt in _RULES:
        if rx.search(n):
            return {"upf": "upf" in tags, "tags": list(tags), "labels": [TAG_LABEL[t] for t in tags],
                    "why": why, "alt": alt, "manual": False}
    return None


def annotate(rows, name_key="name", id_key="id"):
    """Legger på «health» for hver rad (produkter, forslag)."""
    ov = _overrides()
    for r in rows:
        h = classify(r.get(name_key), r.get(id_key), ov)
        if h and (h["tags"] or h.get("cleared")):
            r["health"] = h
    return rows


def set_override(pid, value):
    """value: True = ultraprosessert, False = ikke, None = automatisk."""
    ov = db.get_setting("upf_override") or {}
    if value is None:
        ov.pop(str(pid), None)
    else:
        ov[str(pid)] = bool(value)
    db.set_setting("upf_override", ov)
