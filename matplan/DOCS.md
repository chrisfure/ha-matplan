# Matplan

Matplan samler kvitteringer fra Oda, Kiwi, Coop og andre butikker. Ut fra dem lærer appen hva dere vanligvis kjøper og hvor ofte. Den foreslår handleliste og middager basert på det dere trolig har hjemme og hva som står i kalenderen.

Appen dukker opp som **Matplan** i sidemenyen, og alle brukere i Home Assistant kan bruke den, også i mobilappen.

## Førstegangsoppsett

1. Åpne Matplan og gå til **Mer → Innstillinger**. Under **Mer → Bruksanvisning** finner du en kort oversikt over hele appen.
2. Velg **handleliste** (to-do-listen varer skal legges i) og **kalendere**.
3. Skriv kort hvem som bor i huset og hvilken kalender som hører til hvem.
4. La **AI-modell** stå på «Standard», eller velg Claude-entiteten.

## Kvitteringer

- **Bilde eller PDF:** Fanen *Kvitteringer* → «Ta bilde» eller «Last opp». Fungerer fint med skjermbilder fra Trumf-appen (Kiwi) og Coop-appen.
- **Søk og sortering:** Søk etter en vare, for eksempel «melk», eller en butikk. Da vises alle kvitteringer som inneholder søket, med varelinjene som traff og hva dere har brukt på dem. Du kan filtrere på butikk og sortere på dato (nyeste eller eldste først), butikk eller beløp. Listen er gruppert per måned eller per butikk, med summer.
- **E-post:** Se under. Oda-kvitteringer leses direkte uten AI. Andre kvitteringer, også videresendte skjermbilder, tolkes av Claude.

## Kvitteringer fra e-post (Gmail)

1. Lag et **app-passord** for Gmail: Google-konto → Sikkerhet → Totrinnsbekreftelse må være på → «App-passord».
2. Lag en Gmail-etikett som heter **Matplan**.
3. Lag et filter i Gmail med `from:hei@oda.com subject:kvittering` → *Bruk etiketten Matplan*. Huk av «Bruk filteret også på samsvarende samtaler», så kommer historikken med.
4. Lag et filter til med `to:dittnavn+matplan@gmail.com` → *Bruk etiketten Matplan*. Kvitteringer og skjermbilder som du eller andre videresender til `dittnavn+matplan@gmail.com`, havner da i Matplan. Har du domene hos Cloudflare, kan du i stedet lage `kvittering@dittdomene` med Email Routing og videresende til denne adressen.
5. I Home Assistant går du til **Innstillinger → Apper → Matplan → Konfigurasjon**. Slå på e-posthenting, og fyll inn e-postadresse og app-passord. Trykk «Vis ubrukte valgfrie» hvis feltene ikke vises. Lagre og start appen på nytt.

Første henting går gjennom historikken (365 dager som standard), og det kan ta noen minutter. Etter det sjekkes e-posten hvert 15. minutt.

## Oda-handlekurv (valgfritt)

1. I Home Assistant går du til **Innstillinger → Apper → Matplan → Konfigurasjon** og trykker «Vis ubrukte valgfrie».
2. Fyll inn **Oda – e-post** og **Oda – passord**. Lagre og start appen på nytt.

🔒 **Matplan bestiller aldri.** Oda-delen kan bare logge inn, lese (søk, handlekurv, tidligere ordrer) og *legge til* varer i handlekurven. Alle andre kall er sperret i koden (`oda.py`): bestilling, betaling, leveringstid, fjerning og tømming av kurven. Bestillingen fullfører du selv i Oda-appen.

Oda har ikke noe offisielt, åpent API. Matplan bruker de samme kallene som Oda-nettsiden, beskrevet i [mcp-oda](https://github.com/gbbirkisson/mcp-oda). Endrer Oda nettsiden sin, kan funksjonen slutte å virke til den blir oppdatert.

## Priser og prissammenligning (valgfritt)

- **Kilo-, liter- og stykkpris:** Pakningsstørrelsen leses fra varenavnet på kvitteringen, uten AI («Bacon 150 g», «Lettmelk 1,75 l», «Yoghurt 4x125g», «Egg 12 stk»). Hver vare sammenlignes i den enheten som passer flest av kjøpene: kr/kg, kr/l, kr/stk eller per pakning når størrelsen ikke står. Da blir 400 g bacon for 79 kr (197 kr/kg) og 150 g for 59 kr (393 kr/kg) sammenlignbare. Oda og Kassalapp viser også kilo- og literpris.
- **Fra kvitteringene (Lager):** snittpris (i varens enhet) siste 3 måneder, hva dere har brukt siste 3 måneder, billigste og dyreste kjøp (butikk og dato), en liten prisgraf og ▲/▼ når siste pris avviker mer enn 5 % fra snittet.
- **Dagens Oda-pris (Handle):** krever Oda-innlogging. Viser nåpris og om varen er på tilbud, med vanlig pris og tilbudstekst.
- **⚖️ Sammenlign priser (Kassalapp):** viser hva handlelista koster hos Kiwi, Rema 1000, Coop, Meny og flere, med billigste kjede markert. 🏷 betyr at prisen er mer enn 10 % under vanlig nivå siste 30 dager (trolig tilbud). Er det feil vare, trykker du «Bytt» og velger riktig, så husker appen valget.
  - Lag en gratis bruker og API-nøkkel på **kassal.app/api**, og legg nøkkelen inn som **Kassalapp – API-nøkkel** i app-konfigurasjonen.
  - Prisene kommer fra kjedenes nettbutikker og kan avvike fra prisen i din butikk. Medlemstilbud (Trumf, Coop) er ikke med.

## Økonomi

Øverst på **Kvitteringer** står snittet per måned og hva dere har brukt denne måneden, med et anslag for hele måneden. Hver måned i lista har ▲/▼ mot snittet. Trykk **Økonomi** øverst (på PC i menyen) for mer:

- **Snitt per måned, uke og handel.** Snittet regnes bare av hele måneder (de siste 6). Måneden som pågår, den første måneden med kvitteringer og måneder med uvanlig få kvitteringer holdes utenfor, så snittet ikke blir for lavt.
- **Mat og annet:** husholdning og hygiene regnes som «annet», ut fra varens kategori.
- **Månedsbudsjett:** sett et beløp, så ser dere hva som er igjen, og om måneden ligger an til å gå over.
- **Graf** over de siste 12 månedene, **per butikk** (snitt per måned, antall handler og per handel), **hva pengene går til** (kategorier), **mest penger på** og varer som har **blitt dyrere**.
- **💡 Mulig sparing:** varer der dere tidligere har betalt minst 12 % mindre per kg, liter eller stk, med butikk og pakningsstørrelse, og omtrent hvor mye det utgjør i måneden.
- **Sensor i Home Assistant:** Matplan oppdaterer `sensor.matplan_dagligvarer_denne_maned` hvert kvarter med hva som er brukt denne måneden. Snitt, anslag, budsjett og hva som er igjen ligger som egenskaper. Kortet du kan lime inn i dashbordet, står nederst på Økonomi-siden.

Alt regnes ut i appen uten AI. Tallene blir ikke bedre enn kvitteringene, så handler uten kvittering mangler.

## Beredskapslager

Trykk **Beredskap** øverst i fanen Lager (på PC i menyen). Lista følger rådene fra DSB om egenberedskap: å klare seg selv i **7 døgn** uten strøm, vann, internett og butikker. Mengdene regnes ut for husstanden (standard 2 voksne og 3 barn), og kan endres med **Endre**.

- **Vann:** 20 liter per person, altså 100 liter for fem personer. Appen viser hvor mange døgn vannet dere har, rekker.
- **Mat:** mat som tåler romtemperatur, med mengder per person (barn omtrent 70 %). Appen regner ut omtrent hvor mange døgn maten rekker, ut fra kalorier (ca. 2200 kcal per voksen og 1600 per barn per døgn).
- **Resten av DSBs liste:** varme, kokeapparat, lys, DAB-radio, batterier, powerbank, medisiner, førstehjelp, jodtabletter (barn og voksne under 40), hygiene og nødtoalett, kontanter, betalingskort og telefonliste på papir.
- **Tips i tillegg** (merket «tips»): manuell boksåpner, vannrensetabletter, aktiviteter for barna uten strøm, kjølebag, brannslukker, halvfull tank og mer. Egne ting kan legges til nederst.
- **Best før:** legg inn dato på ting som går ut. Appen varsler to måneder før, så dere kan bruke det i hverdagen og kjøpe nytt.
- **Egen mengde:** trykk på en ting for å endre hvor mye dere vil ha, hvis dere vil ha mer eller mindre enn anbefalt. Raden viser da «Vil ha» i stedet for «Trenger», og du kan gå tilbake til anbefalt mengde.
- **Ikke aktuelt:** ting som ikke passer, for eksempel ved hvis dere ikke har peis, tas ut av regnestykket.
- **🔄 Rullerende lager:** ting dere bruker i hverdagen (havregryn, pasta og ris, hermetikk, knekkebrød, UHT-melk, dopapir, våtservietter, bleier, batterier m.m.) kobles automatisk til varene i lageret ut fra navnet, og merkes 🛟 i Lager. Bruk det eldste først og fyll på med det dere kjøper – da fornyes beredskapen hele tiden, og ingenting går ut på dato.
  - Best før anslås ut fra siste kjøp og typisk holdbarhet, og fornyes ved hvert kjøp. Har en koblet vare ikke vært kjøpt på lenge, får den merket «ikke kjøpt på lenge».
  - Kjøp siden dere telte sist vises som «+ fra kvitteringer», så det er ett trykk å legge dem til.
  - På **Handle** står det «Også beredskap – bruk eldste først» ved slike varer, og ting som mangler i beredskapen foreslås (ikke forhåndsvalgt).
  - Koblingen kan endres ved å trykke på tingen i beredskapslista.
- **🛟 Beredskapsmiddag annenhver uke:** når middagsplanen lages, og det har gått to uker siden sist, får Claude en liste over matvarene i beredskapen som bør brukes først (utløpt eller utløper snart, ikke fornyet på lenge, eller eldst). Ett av forslagene, helst en hverdag, bruker da noe derfra (for eksempel pasta med makrell eller chili med bønner) og merkes 🛟. Når dere trykker «Vi lagde denne», velger dere hva og hvor mye som ble brukt. Det trekkes fra beredskapen, og det samme legges på handlelista merket «(beredskap)».
- **Handleliste:** «🛒 Legg det som mangler på handlelista» legger valgte ting på handlelista, merket «(beredskap)».

Les mer på [sikkerhverdag.no](https://www.sikkerhverdag.no/din-beredskap/).

## Ultraprosessert mat

Varer som trolig er ultraprosessert, får merket **UP** i Lager og på Handle. Bearbeidet kjøtt («Bearb. kjøtt») og mye sukker merkes også. Trykk på varen for å se hvorfor, og et **bedre alternativ**, for eksempel vann eller farris i stedet for brus, havregryn i stedet for søte frokostblandinger, naturell yoghurt med bær i stedet for smaksatt, grovt brød i stedet for loff, og fisk i ovnen i stedet for fiskepinner.

- **Grunnlag:** merkingen bygger på NOVA-systemet, som brukes internasjonalt for ultraprosessert mat. Alternativene følger de norske kostrådene (Helsedirektoratet 2024, basert på de nordiske næringsstoffanbefalingene NNR 2023) og WHO: mer grønnsaker, frukt, bær, belgfrukter, fisk og grove kornvarer; mindre bearbeidet kjøtt, sukker og salt; vann som tørstedrikk; velg Nøkkelhull-merket. Kostrådene bruker ikke selve begrepet «ultraprosessert», og WHO arbeider med en egen retningslinje. Der NOVA og kostrådene spriker (for eksempel myk margarin og fiskeboller), følger appen kostrådene og markerer ikke varen.
- **Uten AI:** vurderingen gjøres ut fra varenavnet. Er merket feil, trykker du *Ikke ultraprosessert* eller *Merk som ultraprosessert* på varen.
- **Økonomi → Helse** viser hvor stor del av matpengene de siste tre månedene som går til ultraprosessert mat, og de største postene med alternativ.

## PC og mobil

Appen tilpasser seg skjermen. På PC kommer menyen i sidekanten. Middagsdagene vises i et rutenett, og forslag og handleliste står side om side. Lageret kan vises som en tabell (☰ → Liste) som sorteres ved å klikke på kolonnetitlene, og kvitteringene åpnes i et eget felt ved siden av listen.

## Butikkvarsel og handleliste på dashbordet

- **Butikkvarsel:** Under **Mer → Innstillinger → Butikkvarsel** lager Matplan automasjonen «Matplan – butikkvarsel» i Home Assistant. Den varsler på telefonen når noen har vært i en av butikkene i 2 minutter, det står noe på handlelista og klokka er innenfor tidsrommet du velger. Trykk på varselet for å åpne handlelista.
  - Legg inn butikkene dere bruker, med navn og koordinater (fra Google Maps: høyreklikk → koordinater). Radius kan endres per butikk.
  - Hver person kobles til sin telefon (HA-appen). Velg om bare den som er i butikken skal få varsel, eller begge.
  - Automasjonen bruker GPS-posisjonen til personene. Du trenger ikke å lage soner.
  - Den **opprettes avslått**, og ingen får varsel før du trykker «Slå på butikkvarsel».
- **Dashbordkort:** Samme side viser kortet du limer inn i dashbordet (type `todo-list`). Handlelista ligger også alltid under **Gjøremål** i sidemenyen i HA.

## Middagskalenderen

Matplan bruker kalenderen som heter **Middag** i Home Assistant. Den kan også velges under Mer → Innstillinger.

- **Historikk:** Tidligere middager i kalenderen hentes inn hver 6. time og vises under «Mest spist siste år». Claude bruker dem som utgangspunkt for forslagene, og omtrent halvparten blir kjente favoritter. «Middag borte» og «… hos Julia» og lignende telles ikke med.
- **Planlagte middager:** Står det allerede noe i kalenderen for en dag, foreslår appen ikke noe annet den dagen. Den viser hva som trengs til den planlagte retten, og varene kan legges rett på handlelista.
- **Velg = skriv inn:** Trykker du «📅 Velg» på et forslag, legges det inn i Middag-kalenderen som heldagsavtale med kort navn, for eksempel «Taco». Vil du bytte etterpå, endrer eller sletter du avtalen i kalenderen selv. Skrivingen kan slås av under Mer → Innstillinger.
- **Tommel opp/ned** gjelder retten, ikke én bestemt dag, og påvirker forslagene framover.

## Gjester

Trykk **👥 Gjester** over «Lag middagsforslag», velg antall voksne og barn og hvilke dager de kommer. Claude teller gjestene med, velger retter som passer flere (barnevennlig hvis det kommer barn, gjerne litt finere i helga) og regner med større mengder i det som må kjøpes. Dagen får merket «👥 +3 gjester», og oppskriften lages for riktig antall.

## Menyen og oppskrifter

- **Hverdag og helg:** Mandag–torsdag foreslås enkel, rask hverdagsmat. Fredag og lørdag foreslås kosemat, og søndag en litt finere søndagsmiddag. Dager med dårlig tid går foran.
- **Næring:** Forslagene følger de norske kostrådene, med fisk minst to ganger i uka, grønnsaker til hver middag, fullkorn der det passer og lite bearbeidet kjøtt. Hvert forslag er merket 🐟 fisk, 🥩 kjøtt, 🐔 kylling eller 🥦 vegetar, og øverst ser du fordelingen for det som er valgt og planlagt.
- **Trykk på et forslag** for å se detaljene. Der kan du hente oppskrift, redigere (tittel, beskrivelse, type, det som mangler, ingredienser og fremgangsmåte), be om et nytt forslag, legge det som mangler på handlelista eller registrere at dere lagde det.
- **🔄 Nytt forslag** bytter ut bare det ene forslaget.
- **✏️ Egen middag** legger inn noe dere har lyst på selv, og du kan hente oppskrift på det også.
- **Oppskrift i kalenderen:** Når du velger en middag, hentes oppskriften først, og den skrives inn i beskrivelsen på kalenderavtalen.
- **Oppskriftene huskes per rett.** Neste gang dere har taco, brukes samme oppskrift. Har du redigert den, brukes alltid din versjon. Trykk på en tidligere middag under «Middager vi har hatt» for å se eller lage oppskriften på den.

Finner ikke appen kalenderen, må den være slått på i Google Calendar-integrasjonen i Home Assistant: Innstillinger → Enheter og tjenester → Google Calendar → entiteter → «Middag».

## Slik tenker appen

- **Lærer av dere:** hver gang dere trykker *Har* eller *Tomt*, eller krysser av hva som ble brukt i en middag, husker appen om varen gikk tom tidligere eller senere enn den trodde. Etter noen rettelser justeres anslaget for akkurat den varen (vises på varekortet som «🧠 Lært av rettelser»). Jo flere kvitteringer og rettelser, jo sjeldnere må dere rette.
- **Mengde teller:** kjøpte dere mer enn vanlig sist (f.eks. tre pakker i stedet for én), regnes varen å vare tilsvarende lenger.
- **«Har» varer ikke evig:** trykker dere *Har*, regner appen med at det dere hadde, varer omtrent en halv periode til. Deretter går anslaget tilbake til vanlig, så varen ikke blir stående som «har» for alltid.
- **Rytme** («ukentlig», «annenhver uke», «månedlig», «sjelden») vises når en vare er kjøpt minst 3 ganger siste år. Den regnes ut fra den største av to tall: median avstand mellom de siste kjøpene, og snittet fra første kjøp til i dag. Da blir ikke en vare som saft «ukentlig» bare fordi den ble kjøpt to ganger samme uke, og tiden siden siste kjøp teller med. Er varen ikke kjøpt på lenge (mer enn 2,5 ganger vanlig avstand), står det «sjelden».
- **Lager** er et anslag: en vare regnes som «har» til den trolig er brukt opp, ut fra holdbarhet og hvor ofte dere kjøper den. Trykk *Har* eller *Tomt* når anslaget er feil. Det gjør også middagsforslagene bedre.
  - **Status** (standard): øverst står det som haster – *Bruk snart* (trykk *Brukt* når det er spist) og faste varer som *går tom snart* (*Har mer* / *Tomt*). Under ligger *Har hjemme*, *Tomt – faste varer*, *Av og til* og *Kjøpt én gang*, som kan åpnes og lukkes.
  - **☰-menyen** bytter til *Sted i huset* (kjøleskap, fryser, frukt og brød, skap, annet – med en stripe som viser omtrent hvor mye som er igjen) eller *Liste*, der du velger sortering over lista og snur rekkefølgen med pila. Stedet følger varens kategori.
  - Priser, rytme og prisgraf ligger på varekortet når du trykker på en vare.
  - Tallene øverst (hjemme, bruk snart, går tom, tomt) fungerer også som snarveier til filtrene.
  - Filtre: har trolig, bruk snart, går tom snart, tomt, faste varer (kjøpt minst 3 ganger siste halvår) og alle. Du kan også velge kategori.
  - **Per kvartal:** hver vare viser hvor mange ganger den er kjøpt de siste 3 månedene, for eksempel «3× per kvartal». Det gjør det lett å sammenligne varer.
  - Sortering: kategori i butikkrekkefølge, mest kjøpt (per kvartal), oftest kjøpt, snart tomt, sist kjøpt, mest penger brukt siste halvår og navn.
  - Trykk på en vare for å se hvor ofte den kjøpes, vanlig pris og hva dere har brukt på den siste halvår. Der kan du også endre navn, kategori og holdbarhet.
  - **+ Legg til varer:** Skriv én eller flere varer, adskilt med komma. De kategoriseres automatisk uten AI, ut fra en norsk ordliste og varene dere har fra før. Skrivefeil og entall/flertall kobles til kjente varer («lettmlk» → Lettmelk, «banan» → Bananer). Du ser kategorien før du lagrer og kan endre den, og appen husker endringen. «Nettopp kjøpt» teller med i kjøpsmønsteret. Skru det av for ting dere har fra før.
  - **Autofullføring:** Mens du skriver, vises resten av ordet i grått, og forslag fra deres egne varer og appens ordliste dukker opp under feltet. Godta med trykk på den grå teksten, Enter, komma (går rett videre til neste vare) eller trykk på et forslag. Det fungerer også i fritekstfeltet på handlelista.
- **Handleliste:** varer dere har kjøpt minst 3 ganger siste halvår, og som etter vanlig rytme snart går tom, foreslås. «Dager til neste handel» under Mer → Innstillinger styrer hvor langt fram appen ser.
  - Vis i **butikkrekkefølge**, gruppert etter kategori, eller som **mest akutt** (tomt nå, går tom i dag, i morgen …).
  - Knappen viser anslått pris for det du har valgt.
  - Varene legges inn i handlelista i Home Assistant i butikkrekkefølge.
  - **Står allerede på lista:** varer som ligger på handlelista i Home Assistant, vises ikke som forslag, heller ikke blant vanlige varer. Appen kjenner igjen varen selv om den står litt annerledes på lista («Lettmelk 1,75 l», «bananer»). Fjernes varen fra lista, kommer forslaget tilbake. Varer som er huket av, regnes som kjøpt.
  - **Fjerne ting:** ✕ ved en vare under «På handlelista nå» fjerner den helt fra handlelista i Home Assistant, slik at den heller ikke tas med til Oda. «Tøm lista» fjerner alt. ✕ ved et forslag gir to valg: «Ikke nå» skjuler det til varen etter vanlig rytme går tom igjen, og «Aldri foreslå» skjuler det for godt (kan angres under Lager). I Oda-vinduet kan du trykke «🗑 Fjern fra lista» eller «Hopp over».
  - **🛒 Legg i Oda-handlekurven:** Sender valgte varer og det som står på handlelista til handlekurven hos Oda. Matplan velger produktet dere pleier å kjøpe, ut fra Oda-kvitteringer og tidligere Oda-ordrer. «Sjekk» betyr at varen er gjettet, og med «Bytt» kan du søke og velge en annen. Valgene huskes til neste gang.
- **Middag:** Claude får kalenderen for de neste dagene, det dere trolig har (varer som bør brukes snart prioriteres), nylige middager og det familien liker og ikke liker. «Vi lagde denne» registrerer middagen, markerer brukte varer som tomme og lar dere gi tommel opp eller ned.
- **Varenavn** slås sammen automatisk («TINE LETTMELK 1L» og «Tine Lettmelk 1,75 l» blir *Lettmelk*). Ble noe feil, trykker du på varen under *Lager* og gir den nytt navn. Skriver du navnet på en annen vare, slås de sammen.

## Feilsøking

- **«AI-oppgaven returnerte ikke noe svar» eller avkuttet JSON:** Øk «Maksimalt antall tokens» i Anthropic-integrasjonen til minst 4000.
- **Bilder blir ikke lest:** AI-oppgaven må støtte vedlegg (Anthropic-integrasjonen gjør det). Bildene lagres i `/media/matplan`.
- **Loggen** finner du under Innstillinger → Apper → Matplan → Logg.
