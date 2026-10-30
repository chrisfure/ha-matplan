"""Prompter og kall mot Home Assistant sin AI-oppgave (Claude)."""
import json

import db
import ha
from parse import extract_json

CATEGORIES = [
    "Frukt og grønt", "Meieri og egg", "Kjøtt", "Fisk og sjømat", "Brød og bakst",
    "Tørrvarer", "Hermetikk og sauser", "Frysevarer", "Pålegg", "Snacks og godteri",
    "Drikke", "Barn", "Husholdning", "Hygiene", "Annet",
]


def ask_json(instructions, attachments=None, task_name="Matplan"):
    entity = db.get_setting("ai_entity") or None
    raw = ha.ai_task(instructions, entity_id=entity, attachments=attachments, task_name=task_name)
    return extract_json(raw)


RECEIPT_RULES = """Returner KUN JSON, ingen forklaring, på formen:
{"butikk":"Kiwi","dato":"YYYY-MM-DD","total":123.45,"varer":[{"navn":"varenavn slik det står","antall":1,"enhet":"stk","pris":12.90}]}

Regler:
- butikk: kjedenavnet (Kiwi, Coop Extra, Coop Mega, Coop Prix, Rema 1000, Meny, Spar, Joker, Bunnpris, Oda, Extra ...), uten filialnavn.
- Ta med alle varelinjer. Hopp over pant, bæreposer, rabattlinjer, summer, betaling og bonus.
- «2 x 19,90» betyr antall 2, og pris skal være linjesummen (39,80).
- Varer solgt på vekt (f.eks. 0,452 kg) får antall 0.452 og enhet "kg".
- pris er det som faktisk ble betalt for linjen, som tall med punktum.
- Ukjent dato: null. Ukjent total: null.
- Hvis dette ikke er en kvittering for varekjøp: {"varer":[]}"""


def parse_receipt_image(attachments):
    prompt = "Les kvitteringen i vedlegget fra en norsk dagligvarebutikk.\n\n" + RECEIPT_RULES
    return ask_json(prompt, attachments=attachments, task_name="Matplan: les kvittering")


def parse_receipt_text(text):
    prompt = (
        "Les denne kvitteringen fra en norsk dagligvarebutikk.\n\n"
        + RECEIPT_RULES
        + "\n\nKvitteringstekst:\n\"\"\"\n"
        + text[:15000]
        + "\n\"\"\""
    )
    return ask_json(prompt, task_name="Matplan: les kvittering")


def normalize(raw_names, existing):
    listing = [{"i": i, "v": n} for i, n in enumerate(raw_names)]
    existing_txt = ", ".join(existing) if existing else "(ingen ennå)"
    prompt = f"""Du gjør om varenavn fra norske dagligvarekvitteringer til korte, generelle produktnavn. Navnene brukes til å finne kjøpsmønstre og planlegge middager.

Eksisterende produktnavn (bruk NØYAKTIG samme navn hvis varen er samme type produkt):
{existing_txt}

Regler:
- n: kort, generelt norsk navn med stor forbokstav, uten merke, størrelse og pakning. Eksempler: «TINE LETTMELK 1L» og «Tine Lettmelk 1% fett, 1,75 l» → «Lettmelk». «Gilde Kjøttboller Italiensk krydder» → «Kjøttboller». «Prima Lavpris Seifilet 400 g» → «Seifilet». Behold forskjeller som betyr noe for innkjøp (Lettmelk/Helmelk, Gresk yoghurt/Yoghurt naturell, Kjøttdeig/Kyllingkjøttdeig).
- k: kategori, én av: {", ".join(CATEGORIES)}.
- h: typisk antall dager varen holder seg hjemme etter kjøp (f.eks. fersk fisk 2, kjøttdeig 3, melk 8, brød 4, bananer 5, gulrot 21, ost 30, pasta 365, frysevarer 120).
- m: 1 hvis det er mat eller drikke som kan inngå i måltider, ellers 0 (husholdning, hygiene, snacks kan være 1 eller 0 etter skjønn).
- Er linjen ikke en vare (pant, pose, rabatt, frakt, levering), sett n til "".

Varer:
{json.dumps(listing, ensure_ascii=False)}

Svar KUN med en JSON-liste med ett element per vare:
[{{"i":0,"n":"Lettmelk","k":"Meieri og egg","h":8,"m":1}}]"""
    return ask_json(prompt, task_name="Matplan: normaliser varer")


MENU_RULES = """Slik bygger du menyen:
- Hverdager (mandag–torsdag): enkel, rask og «kjedelig» hverdagsmat som barn spiser – f.eks. fiskekaker, fiskegrateng, kjøttkaker, kjøttboller, pasta med kjøttsaus, kyllinggryte, suppe, omelett, fiskepinner med grønnsaker. Helst maks 30 minutter.
- Fredag og lørdag: typisk kosemat/helgemat – f.eks. taco, hjemmelaget pizza, hamburgere, grillmat, wok, lasagne.
- Søndag: en litt finere søndagsmiddag med god tid – f.eks. ovnsbakt kylling, steik, gryte, fisk i ovn, kjøttkaker i brun saus med potetmos.
- Dårlig tid en dag (trening, møter, sene hjemkomster) går foran: velg da noe raskt eller noe som kan lages på forhånd, også i helgen.

Næring (barn i vekst – følg de norske kostrådene):
- Fisk minst to ganger i uka, gjerne én gang fet fisk (laks, makrell, ørret).
- Grønnsaker til hver middag, gjerne flere farger. Belgfrukter og vegetar av og til.
- Fullkorn der det passer (fullkornspasta, brun ris, grovt brød/lefser).
- Begrens bearbeidet kjøtt (pølser, bacon, ferdigretter) og mye salt; heller rent kjøtt, kylling og fisk.
- Helgemat kan være kos, men gjør den gjerne litt sunnere (mye grønt i tacoen, hjemmelaget pizza med grønnsaker).
- "type": én av fisk, kjøtt, kylling, vegetar."""


def _context_block(ctx):
    return f"""Husstand: {ctx['household'] or 'ikke oppgitt – anta en familie med to voksne og barn'}
Vanlig middagstid: {ctx['dinner_time']}
Preferanser og allergier: {ctx['preferences'] or 'ingen oppgitt'}
Ønske denne gangen: {ctx['wish'] or 'ingen'}

Kalender:
{ctx['calendar']}

Det vi trolig har hjemme (anslått ut fra kvitteringer; * = bør brukes snart):
{ctx['pantry'] or '(ingen data ennå)'}

Allerede planlagt i middagskalenderen (skal IKKE byttes ut):
{ctx['planned'] or 'ingenting'}

Middager vi har hatt de siste to ukene (unngå å gjenta disse): {ctx['recent'] or 'ingen registrert'}
Middager vi spiser oftest (antall ganger siste år): {ctx['frequent'] or 'ingen data ennå'}
Middager familien liker ekstra godt: {ctx['liked'] or 'ingen registrert'}
Middager familien ikke liker: {ctx['disliked'] or 'ingen registrert'}"""


SUGGESTION_SHAPE = '{"tittel":"...","type":"fisk|kjøtt|kylling|vegetar","planlagt":false,"min":20,"bruker":["..."],"mangler":["..."],"beskrivelse":"én–to setninger om retten og tilbehør (grønnsaker)"}'


def dinner_plan(ctx):
    n = ctx["per_day"]
    prompt = f"""Du er en praktisk middagsplanlegger for en norsk barnefamilie. Lag middagsforslag for dagene under.

{_context_block(ctx)}

Slik bruker du kalenderen:
- Finn ut hvem som er hjemme til middag hver dag: reiser, jobb eller vakter som overlapper middagstid, overnatting borte osv. Kalendernavnet sier som regel hvem avtalen gjelder.
- Finn ut hvor god tid det er: aktiviteter rett før eller rundt middagstid (trening, turn, fotball, møter, «kommer sent») betyr dårlig tid.
- Hvis det er uklart, anta at alle er hjemme og normal tid.

{MENU_RULES}

Regler:
- Dager som allerede er planlagt: returner KUN den planlagte retten som eneste forslag, med "planlagt": true, og fyll ut "type", "bruker" og "mangler" for den. Er planen «Middag borte» eller lignende, sett "hjemme": 0 og "forslag": [].
- Andre dager: omtrent halvparten av forslagene bør være kjente favoritter fra listen over det vi spiser oftest (når de passer dagen). Resten kan være nye retter i samme stil.
- Se hele perioden under ett: varier typene, og sørg for fisk minst to ganger per uke når perioden er lang nok (regn med planlagte dager).
- "tittel" skal være kort, som i familiens kalender (f.eks. «Taco», «Currylaks», «Kikertkorma», «Kyllinggryte»). Detaljer hører hjemme i "beskrivelse".
- Bruk helst det vi har, spesielt varer merket *.
- Salt, pepper, olje, smør, mel, sukker og vanlige krydder kan antas å finnes.
- {n} forslag per dag, av ulik type.
- "bruker": varenavn fra listen over det vi har. "mangler": konkrete varer som må kjøpes, med vanlige norske varenavn.

Svar KUN med JSON, ingen forklaring:
{{"dager":[{{"dato":"YYYY-MM-DD","hjemme":4,"hvem":"alle","tid":"kort|normal|god","begrunnelse":"kort setning, f.eks. «Turn 17–18:30, én voksen på jobbreise»","forslag":[{SUGGESTION_SHAPE}]}}]}}"""
    return ask_json(prompt, task_name="Matplan: middagsforslag")


def replace_one(ctx, dato, ukedag, avoid, day_info):
    prompt = f"""Du er en praktisk middagsplanlegger for en norsk barnefamilie. Foreslå ÉN ny middag for {ukedag} {dato}.

{_context_block(ctx)}

Om dagen: {day_info or 'ingen spesielle hensyn'}

{MENU_RULES}

Regler:
- Ikke foreslå noen av disse (allerede vurdert): {", ".join(avoid) or 'ingen'}.
- Bruk helst det vi har, spesielt varer merket *. Salt, pepper, olje, smør, mel og vanlige krydder finnes.
- "tittel" kort, som i familiens kalender. "bruker": varenavn fra listen over det vi har. "mangler": konkrete varer som må kjøpes.

Svar KUN med JSON for ett forslag, ingen forklaring:
{SUGGESTION_SHAPE}"""
    return ask_json(prompt, task_name="Matplan: nytt forslag")


def recipe(title, description, people, household, preferences, pantry):
    prompt = f"""Skriv en enkel, familievennlig oppskrift på norsk for middagen «{title}».
Om retten: {description or 'ingen beskrivelse'}
Antall som spiser: {people or 4} ({household or 'familie med to voksne og barn'})
Preferanser og allergier: {preferences or 'ingen oppgitt'}
Dette har vi trolig hjemme, bruk gjerne det: {pantry or 'ukjent'}

Krav:
- Næringsrik middag for barn i vekst: med grønnsaker (gjerne flere farger), fullkorn der det passer, lite salt og lite bearbeidet kjøtt.
- Enkle, tydelige steg som en travel forelder kan følge. Norske mål (g, dl, ss, ts, stk).
- Mengder tilpasset antall personer.
- Maks 8 steg.

Svar KUN med JSON, ingen forklaring:
{{"porsjoner":4,"tid":30,"ingredienser":["400 g laksefilet","3 dl fullkornsris"],"steg":["Sett ovnen på 200 °C.","…"],"tilbehor":"f.eks. agurk og gulrotstaver","tips":"kort tips, f.eks. hvordan få barna med eller lage dobbel porsjon til matpakke"}}"""
    return ask_json(prompt, task_name="Matplan: oppskrift")
