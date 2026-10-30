# Huskelappen

Huskelappen leser ukebrevene og PDF-ene fra barnehagen. Den finner det dere må huske, for eksempel turdager, ting som skal med, foreldresamtaler, stengte dager og frister.

- **Du godkjenner alt først.** Hver søndag kl. 18 (kan endres) får du et varsel hvis det ligger forslag og venter. Kommer det noe som gjelder før neste gjennomgang, varsles du med en gang.
- **Riktig kalender.** Det som gjelder ett barn, havner i det barnets kalender. Gjelder det flere, havner det i kalenderen til hvert av dem.
- **Veggskjermen.** Fra kl. 19 kvelden før til kl. 09 vises en tekst øverst på dashbordet om hva som skjer i barnehagen. Teksten vises bare når det er noe å huske.
- **Kveldsvarsel.** Kl. 19 får telefonen du velger (bare den) et varsel om hva som skjer i morgen.

Andre barns samtaler, personalmøter og vanlige aktiviteter som samling og utelek tas ikke med.

## Oppsett

### 1. Installer
Huskelappen ligger i samme repo som Matplan. Gå til **Innstillinger → Apper → Appbutikk → ⋮ → Se etter oppdateringer**. Da dukker Huskelappen opp. Installer og start den.

### 2. E-post (Gmail)
Gå til **Innstillinger → Apper → Huskelappen → Konfigurasjon**:
- Slå på **imap_enabled**.
- **imap_user**: Gmail-adressen din.
- **imap_password**: et app-passord. Du kan bruke det samme som til Matplan, eller lage et nytt på myaccount.google.com/apppasswords.
- **imap_sender**: barnehagens domene eller adresse, for eksempel `barnehagen.no`. Bare e-post herfra leses. Feltet er påkrevd.
- **imap_folder**: `INBOX`. Arkiverer du barnehagepost, lager du heller en Gmail-etikett med filter og skriver navnet på den her.

Lagre og start appen på nytt. Første gang leses de siste 14 dagene. Etter det sjekkes e-posten hver halvtime.

### 3. Innstillinger i appen
Åpne **Huskelappen** i sidemenyen og gå til **Innstillinger**:
- **Barna:** trykk «+ Legg til barn» for hvert barn. Fyll inn navn, hvordan barnehagen skriver navnet (for eksempel «Ola AB» hvis flere heter Ola), avdeling og grupper/klubber, og hvilken kalender det skal inn i. Ingen e-post leses før minst ett barn er lagt inn.
- **Telefon som varsles:** velg telefonen og trykk «Send testvarsel».
- Juster eventuelt tidspunktene.

### 4. Kortet på veggskjermen
Gå til fanen **Skjermen**, kopier kortet og legg det **øverst** på dashbordet ditt. Rediger dashbordet, velg ⋮ → Rå konfigurasjon, eller legg til et manuelt kort:

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
- **Uka:** forslag som venter, gruppert per dag. Du kan godkjenne, endre (dato, klokkeslett, hva som skal med, hvilket barn) eller avvise. «Godkjenn alle» legger alt inn på én gang.
- **Legg til noe selv:** for beskjeder som kom på papir eller i garderoben.
- **Last opp ukeplan (PDF):** under Innstillinger. Fint for å teste, eller hvis et skriv kom på en annen måte enn e-post.

## Slik leses skrivene
E-postteksten og PDF-ene sendes til Claude gjennom Home Assistant sin AI-oppgave, den samme som Matplan bruker. PDF-ene sendes både som tekst og som bilder av sidene, så Claude også ser symboler og oppsett, for eksempel et symbol som markerer turdag. Claude får vite hvilke grupper barna går i, så det som gjelder én klubb bare havner hos barna i den klubben. Datoene hentes fra ukeplanens kolonner, ikke fra overskrifter (de kan være feil).

Ingenting legges i kalenderen før du har godkjent det.

## Sensorer
- `sensor.huskelappen_barnehagen`: kort tekst som tilstand. Hele teksten ligger i egenskapen `tekst`, og antall forslag som venter ligger i `venter_godkjenning`.
- `binary_sensor.huskelappen_vis`: `on` når teksten skal vises på skjermen.
