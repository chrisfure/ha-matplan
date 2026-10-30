# Huskelappen

Huskelappen leser beskjeder fra barnehage, skole og aktiviteter: e-post, ukeplaner og infoskriv som PDF, og bilder av papirlapper. Den finner det dere må huske, for eksempel turdager, ting som skal med, foreldresamtaler, stengte dager og frister.

- **Du godkjenner først.** Hver søndag kl. 18 (kan endres) får du en oppsummering av uka og et varsel hvis noe venter. Varselet har en knapp, **Godkjenn alle**, så du slipper å åpne appen når alt ser riktig ut. Kommer det noe som gjelder før neste gjennomgang, varsles du med en gang.
- **Riktig kalender.** Det som gjelder ett barn, havner i det barnets kalender. Gjelder det flere, havner det i kalenderen til hvert av dem.
- **Veggskjermen.** Fra kl. 19 kvelden før til kl. 09 vises morgendagen øverst på dashbordet: beskjeder, faste ting, vær og klesråd. Søndag kveld vises også resten av uka.
- **Kveldsvarsel.** Kl. 19 kommer et varsel om hva som skjer i morgen, med vær og klær.
- **To mottakere.** Kryss av for at en til (for eksempel partner) også skal få kveldsvarsel, frister og ukesoppsummering. Godkjenning går bare til deg.

Andre barns samtaler, personalmøter og vanlige aktiviteter som samling og utelek tas ikke med.

## Oppsett

### 1. Installer
Huskelappen ligger i samme repo som Matplan. Gå til **Innstillinger → Apper → Appbutikk → ⋮ → Se etter oppdateringer**. Installer og start appen.

### 2. E-post (Gmail)
Gå til **Innstillinger → Apper → Huskelappen → Konfigurasjon**:
- Slå på **imap_enabled**.
- **imap_user**: Gmail-adressen din.
- **imap_password**: et app-passord. Du kan bruke det samme som til Matplan, eller lage et nytt på myaccount.google.com/apppasswords.
- **imap_folder**: `INBOX`. Arkiverer du slike e-poster, lager du heller en Gmail-etikett med filter og skriver navnet på den her.
- **imap_sender** (valgfri): én avsender for alle barna. Det er enklere å legge inn kilder i appen (se under).

Lagre og start appen på nytt. Første gang leses de siste 14 dagene. Etter det sjekkes e-posten hver halvtime.

### 3. Innstillinger i appen
Åpne **Huskelappen** i sidemenyen og gå til **Innstillinger**. Trykk **Lagre innstillinger** nederst når du er ferdig.

- **Barna:** ett kort per barn med navn, hvordan barnehagen eller skolen skriver navnet (for eksempel «Ola AB» hvis flere heter Ola), avdeling/klasse/grupper og kalender.
- **Kilder:** én per avsender (barnehagen, skolen, SFO, idrettslaget), med domene eller adresse og hvilke barn den gjelder. Bare e-post fra disse leses. Mange skoleapper (Vigilo, Transponder o.l.) sender bare «du har fått en melding» på e-post. Da står ikke beskjeden i e-posten, og den må tas bilde av eller legges inn selv.
- **Faste påminnelser:** ting som gjentar seg hver uke, som gymtøy på tirsdager. Claude foreslår også slike når et skriv sier «hver tirsdag». De vises på skjermen og i kveldsvarselet, men legges ikke i kalenderen. De kan settes på pause.
- **Varsler:** telefonen din, og eventuelt **Send også varsler til** med telefonen til den andre. Trykk «Send testvarsel».
- **Vær og klær:** velg værvarselet ditt i Home Assistant (for eksempel Met.no). Klesrådet bygger på temperaturen ved levering, nedbør og vind fra kl. 7 til 16.
- **Handleliste:** velg listen Matplan bruker. Noen dager før turdager og matpakkedager legges «Brød og pålegg til matpakke» på lista.
- **Læring:** forslag som ligner noe du har avvist to ganger, avvises automatisk (på som standard). Automatisk godkjenning av det du alltid godkjenner er av som standard. Alt som avgjøres automatisk står merket i lista og kan angres.

### 4. Kortet på veggskjermen
Gå til fanen **Skjermen**, kopier kortet og legg det **øverst** på dashbordet. Rediger dashbordet og velg ⋮ → Rå konfigurasjon, eller legg til et manuelt kort:

```yaml
type: conditional
conditions:
  - condition: state
    entity: binary_sensor.huskelappen_vis
    state: "on"
card:
  type: markdown
  content: "{{ state_attr('sensor.huskelappen_barnehagen', 'tekst') }}"
```

## Bruk
- **Uka:** forslag som venter, gruppert per dag. Du kan godkjenne, endre (dato, klokkeslett, ukedager, hva som skal med, hvilket barn) eller avvise. «Godkjenn alle» godkjenner alt på én gang.
- **📷 Ta bilde av lapp:** åpner kameraet på mobilen. Bildet skaleres ned og leses som et skriv. På iPhone må kameraet stå på «Mest kompatibel» (Innstillinger → Kamera → Formater) hvis du laster opp bilder fra bildebiblioteket.
- **📄 Last opp skriv:** PDF eller bilde som kom på en annen måte enn e-post.
- **Legg til noe selv:** for beskjeder som kom muntlig eller i garderoben.

## Slik leses skrivene
E-postteksten, PDF-ene og bildene sendes til Claude gjennom Home Assistant sin AI-oppgave, den samme som Matplan bruker. PDF-ene sendes både som tekst og som bilder av sidene, så Claude også ser symboler og oppsett. Claude får vite hvilke barn kilden gjelder og hvilke grupper de går i, så det som gjelder én klubb eller klasse, bare havner hos de barna. Datoene hentes fra ukeplanens kolonner, ikke fra overskrifter (de kan være feil). Noe som allerede dekkes av en fast påminnelse, foreslås ikke på nytt.

Ingenting legges i kalenderen før du har godkjent det, med mindre du slår på automatisk godkjenning.

## Sensorer
- `sensor.huskelappen_barnehagen`: kort tekst som tilstand. Hele teksten ligger i egenskapen `tekst`, og antall forslag som venter ligger i `venter_godkjenning`.
- `binary_sensor.huskelappen_vis`: `on` når teksten skal vises på skjermen.

## Knappen i varselet
Huskelappen lytter etter knappetrykk i varsler via Home Assistant sitt websocket-API. Under **Innstillinger → E-post** står det om dette er tilkoblet.
