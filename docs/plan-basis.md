# Apollo terug naar de basis — plan v2

Vervangt `apollo-basis-plan.md` en `fase1-2-concreet.md`. Die twee beschreven de
situatie van vóór de visuele groepen; deze versie is nagelopen tegen de echte code
op 2026-09-28 (testsuite: 673 passed, 8 skipped).

## De kern

> Ik gooi mijn documenten in Apollo. Apollo vertelt me in gewone taal wat bij
> elkaar hoort, wat nog actueel is en wat oud is — en ik beslis. Mijn bestanden
> verplaats ik nooit per ongeluk, en er verdwijnt nooit iets.

Twee zinnen daarin zijn hard: **ik beslis** en **er verdwijnt nooit iets**.
Alles hieronder volgt daaruit. De gebruiker kent geen embeddings, vector-DB,
knowledge graph, pipeline of agent; die blijven onder de motorkap.

## Stand van zaken

### Wat er al staat (hergebruiken, niet opnieuw bouwen)

| Onderdeel | Bewijs | Status |
| --- | --- | --- |
| Visuele groepen (model) | `models.py` — `GROUP_SOURCES`, `GROUP_LAYOUTS`, `ARCHIVE_CATEGORY`, `Group`, `GroupPlacement` | klaar |
| Visuele groepen (service) | `services/placement.py` — groepen, plaatsingen, `groups_of_document`, `get_or_create_archive` | klaar |
| Visuele groepen (API) | `api/routes_groups.py` — 9 endpoints; de module schrijft geen enkel bestand | klaar |
| Visuele groepen (UI) | `components/GroupsBoard.tsx` + `App.tsx` sectie `groups`, drag tussen groepen | klaar |
| Migratie | `migrations/versions/0009_visual_groups_and_signals.py` | klaar |
| Tests groepen | `tests/test_placement.py` (25), `tests/test_groups_api.py` (16), `scripts/smoke-groups-board.mjs` | klaar |
| Signalen: woordenschat | `models.py` — `PULSE_SIGNALS`, `PULSE_SIGNAL_REFS` | klaar |
| Signalen: validatie | `services/signals.py` — `parse_signals()` weigert onbekende namen, traversals en verzonnen paden | klaar |
| Signalen: kolommen | `PulseItem.signals`, `PulseItem.signal_refs` | klaar |
| Signalen: tests | `tests/test_signals.py` (20) | klaar, maar zie hieronder |
| Fysiek verplaatsen (motor) | `proposals.plan_move(..., allow_missing_dir=True)`, al gebruikt door `routes_inventory.py` | klaar |
| Map-vocabulaire (architectuurrepo) | `services/inventory.py` — `TARGET_STRUCTURE`, `LOW_CONFIDENCE_THRESHOLD` | klaar |
| Inbox: opslag | `services/storage.py` — `store_upload`, `list_inbox`, `ensure_inbox`; `APOLLO_STORAGE_ROOT` + `effective_allowed_workspace_roots` in `config.py` | klaar (Fase 1) |
| Inbox: API | `api/routes_inbox.py` — `GET /inbox`, `POST /inbox/upload`; `get_or_create_storage_repo` in `api/deps.py` | klaar (Fase 1) |
| Inbox: UI | `components/InboxDropzone.tsx`, Inbox als eerste sectie in `NavigationColumn`, Inbox-kolom in `FolderContentsColumn` | klaar (Fase 1) |
| Intake ≠ documentatie | `repositories.is_storage` (migratie `0010`) + filter in `get_documentation_repository` | klaar (Fase 1) |
| Tests inbox | `tests/test_storage.py` + `tests/test_inbox_api.py` (37) | klaar |
| Analyse: motor | `services/delphi.py` — `analyse`, `record_signals`, `signals_for_document/group`, `dismiss_signal` | klaar (Fase 2) |
| Analyse: API | `api/routes_signals.py` — `/delphi/analyze`, `/signals`, `/signals/count`, `/signals/{id}/dismiss` | klaar (Fase 2) |
| Analyse: bewaarplaats | `DocSignal` (migratie `0011`) met verplichte `why` en status `new/confirmed/dismissed` | klaar (Fase 2) |
| Analyse: UI | `Analyseren`-knop met teller, rapportbalk, signaalbalk boven het document | klaar (Fase 2) |
| Tests analyse | `tests/test_delphi.py` (14), `tests/test_signals_api.py` (24), `tests/test_signals.py` (28) | klaar |
| Groepen uit de analyse | `services/delphi_grouping.py` — `propose_clusters` (tweede pass over de bevindingen), `propose_groups` (schrijft `source="ai"`, `placed_by="ai"`) | klaar (Fase 3) |
| Tests groepvoorstel | `tests/test_delphi_grouping.py` (27), plus 6 end-to-endtests in `tests/test_signals_api.py` | klaar |
| Leiders-veto is niet te omzeilen | `GroupPlacementRequest` heeft geen `placed_by` meer; `routes_groups.py` schrijft altijd `"user"` | klaar (Fase 3) |

### Wat ontbreekt

| Gat | Bewijs |
| --- | --- |
| Upload en Inbox | **geregeld in Fase 1** — zie hierboven |
| Alleen `Inbox/` bestaat fysiek | `Projecten/`, `Administratie/`, `Referentie/` en `Archief/` komen in Fase 4; een lege map per categorie is een bewering die de lezer niet heeft gedaan |
| De signaal-motor wordt nooit aangeroepen | **geregeld in Fase 2** — `services/delphi.py` roept het model zelf, via het bestaande budget en de bestaande woordenschat |
| Signalen zijn onzichtbaar in de API | **geregeld in Fase 2** — `/signals` per document, per groep, plus `/signals/count` voor de teller |
| Geen reden per signaal | **geregeld in Fase 2** — `why` is verplicht; zonder reden wordt de bevinding bij de grens weggegooid |
| Geen dismiss-status | **geregeld in Fase 2** — `doc_signals.status`; een weggezette bevinding blijft weggezet na een nieuwe pass |
| Nog geen groepen uit de analyse | **geregeld in Fase 3** — `services/delphi_grouping.py`; één knop doet signalen én groepen |
| Archiveren doet nog niets met een map | Fase 4: het signaal is er, de actie niet — nog bewust |
| Delphi maakt geen groepen | `create_group(..., source="ai")` komt alleen in tests voor |
| De sidebar kent geen groepen of signalen | `client.groupsOfDocument()` bestaat maar wordt door geen enkel component gebruikt |
| Geen app-beheerde opslagroot | **geregeld in Fase 1**: `effective_storage_root` en `effective_allowed_workspace_roots` in `config.py`. Een lege operatorlijst blijft leeg, want in development betekent leeg "alles toegestaan" en er zou anders één map overblijven |
| Bevriezen is niet gedaan | `NavigationColumn.tsx` toont nog 10 secties, inclusief repos, inventory en pulse |

### Waar de oude plannen van uitgingen die niet meer klopten

1. **Twee definities van "gearchiveerd"**: `Group.is_archive` (werkt) naast een
   `archived`-kolom op `PulseItem` die door niets werd geschreven. Die kolom is
   verwijderd; het archief is groepslidmaatschap.
2. **Twee map-vocabulaires**: het oude plan noemde `Inbox/Projecten/Administratie/
   Referentie/Archief`, de code heeft `foundation/architecture/...`. Het tweede
   beschrijft een architectuurrepo; het eerste de eigen verzameling van de
   gebruiker. Ze blijven gescheiden en staan elk in één constante.
3. **De signaalnamen verschilden**: het oude plan noemde zeven deels Nederlandse
   namen, de code heeft vijf Engelse (`outdated`, `duplicate`, `new`, `update`,
   `conflict`). De code is de bron; de app is Engelstalig.
4. **De hele groep- en signaallaag stond ongecommit.** Dat is de mechanische
   oorzaak van "ik raak de draad kwijt": duizenden regels werk in de working tree,
   niets in de geschiedenis.

## Fase 0 — Vastzetten (halve dag)

**Status: klaar (2026-09-28).** `3c66257` → `ed43486`, zeven commits, `PulseItem.archived`
verwijderd, plan in `docs/plan-basis.md`. Suite 598 passed / 8 skipped, `npm run build`
schoon, gepusht naar `main`.

Doel: een punt waarop terugvallen mogelijk is. Niets bouwen op een ongecommitte
staat.

- `archived` verwijderen van `PulseItem` (model, migratie 0009, docstring van
  `placement.py`), zodat er één definitie van gearchiveerd is.
- Zes commits, gescheiden op onderwerp: groepen en het model, de signaal-
  woordenschat, links, repositories en git, chat, en de frontend als één
  checkpoint. De frontend is één commit omdat `app.css` en `App.tsx` honderden
  verweven hunks hebben die niet per feature te splitsen zijn zonder
  tussenstanden te verzinnen die nooit bestaan hebben.
- Dit plan in `docs/plan-basis.md`, README bijgewerkt.

**Klaar als:** de volledige testsuite groen is, `npm run build` schoon is en
`git status` geen werk meer verbergt.

## Fase 1 — Erin krijgen: drag & drop → Inbox (1–2 dagen)

**Status: klaar (2026-09-28).** `services/storage.py`, `api/routes_inbox.py`,
`InboxDropzone.tsx`, migratie `0010`, 37 nieuwe tests.

Doel: tien bestanden van het bureaublad slepen en ze in Apollo kunnen lezen.

- `services/storage.py`: `storage_root()` en `store_upload(workspace_id,
  filename, bytes)`. Naam schonen, de-dupliceren (`verslag-2.pdf`), atomair
  schrijven (tmp + replace), grenzen `DOC_SUFFIXES` en `MAX_READ_BYTES` uit
  `services/documents.py`. Geen `unlink`, geen `rmdir`.
- `config.py`: `apollo_storage_root` plus `effective_storage_root` (zelfde
  patroon als `effective_source_checkout_root`), en
  `effective_allowed_workspace_roots` die de opslagroot toevoegt. Zonder dat
  laatste weigert Apollo in productie de map die het zelf net aanmaakte.
- `api/deps.py`: `get_or_create_storage_repo(db, workspace_id)`.
- `api/routes_inbox.py`: `POST /workspaces/{id}/inbox/upload` (multipart) en
  `GET /workspaces/{id}/inbox`. De upload doorloopt `read_document()` als
  leesbaarheidspoort. 400 bij verkeerd type of te groot, 409 zonder storage-repo.
- Frontend: `api.uploadInbox()` / `api.listInbox()` in `client.ts` (de enige plek
  met URLs), `components/InboxDropzone.tsx` dat `dataTransfer.files` leest (niet
  `application/x-apollo-document`, dat is de interne sleep). Inbox is de
  default-sectie; de dropzone is de lege staat van de documentenkolom.
- Submappen: nu alleen `Inbox/` en `Archief/`; `Projecten/`, `Administratie/` en
  `Referentie/` pas in Fase 4.

**Klaar als:** tien gemengde bestanden (md/txt/pdf/docx) droppen, ze fysiek in
`Inbox/` staan, in de lijst verschijnen, openen in het leesvenster, en niets
verplaatst of verwijderd is. Tests: happy path, verkeerd type, te groot, traversal
in de bestandsnaam, naamconflict.

**Wat er anders ging dan gepland, en waarom:**

1. **De Inbox is een eigen repository, niet de geregistreerde map.** Het plan
   legde de opslag in de bestaande docs-repository. Dat zou schrijven in een map
   die van de lezer is, terwijl de regel is dat die map alleen via voorstellen
   wordt geschreven. Gevolg: twee documentation-repositories per workspace, dus
   een manier nodig om ze te onderscheiden. Migratie `0010` voegt
   `repositories.is_storage` toe en `get_documentation_repository` slaat de
   intake over — anders draaide een pulse- of inventory-run over de lege Inbox en
   rapporteerde "niets gevonden" over het verkeerde.
2. **Opslaan maakt de repository aan, opvragen niet.** `GET /inbox` op een
   workspace die nog niets heeft geeft `repository_id: null` en een lege lijst,
   en legt niets op schijf. Een workspace die alleen bekeken wordt hoort geen map
   achter te laten.
3. **Een onleesbaar bestand wordt bewaard en gemeld, niet geweigerd.** De bytes
   zijn aangekomen; weggooien laat de lezer zonder bestand én zonder reden
   achter. De response zegt `readable: false` met de reden erbij.
4. **Alleen `Inbox/` wordt aangemaakt.** `Projecten/`, `Administratie/`,
   `Referentie/` en `Archief/` blijven Fase 4.
5. **De opdrachtregel is per bestand, niet per map.** Twaalf documenten samen
   gedropt slagen of falen niet als één: elf opgeslagen en één geweigerd is een
   beter resultaat dan niets, en de lezer moet zien welke welke was.
6. **De Inbox is de eerste sectie, en de app start daar.** Een sessie begint met
   iets toevoegen, niet met een lijst objecten doorlopen.

## Fase 2 — Delphi zegt wat het ziet (2–3 dagen)

**Status: klaar (2026-09-28).** `services/delphi.py`, `api/routes_signals.py`,
migratie `0011`, signaalbalk en `Analyseren`-knop, 57 nieuwe en herziene tests.
Echte proef met drie documenten in de Inbox: drie bevindingen met reden en
verwijzing, teller 3 → 2 na één verberging, bestanden byte-identiek.

Doel: één knop **Analyseren** → per document 0..n signalen in gewone taal, elk
met een reden en een aanklikbare verwijzing. Nog géén groepen.

**Wat er anders ging dan gepland, en waarom:**

1. **Eigen motor in plaats van de pulse-scans aanroepen.** Het plan wilde
   `run_pulse()` en `run_inventory()` hergebruiken. De pulse-scan is
   hash-gedreven, schrijft `PulseItem`-rijen en kent een eigen
   suggest/apply-modus; de inventory gebruikt de architectuur-mapwoorden
   (`foundation/`, `architecture/`), niet de mapwoorden van iemands eigen
   verzameling. `services/delphi.py` gebruikt wél het contextbudget, de
   structurele representatie en de woordenschat, maar vraagt in één keer om
   signalen — en dat is ook de enige manier om het Later te kunnen uitbreiden met
   groepvoorstellen in Fase 3.
2. **De bevindingen leven in een eigen tabel** (`doc_signals`, migratie 0011),
   niet in de JSON-kolommen van `pulse_items`. Een signaal moet een `why` hebben
   die niet leeg is, een status die een beslissing van de lezer vastlegt, en op
   document én groep opvraagbaar zijn. Dat is een CHECK-constraint en een join
   waard, geen JSON-lijst.
3. **De teller heeft een eigen eindpunt** (`/signals/count`). De lijst weigert
   terecht een hele workspace, maar een badge die "0" zegt terwijl er drie open
   staan, is precies de leugen die dit product moet vermijden.
4. **De analyse valt terug op de inbox.** Een workspace met alleen gedropt
   werk heeft geen documentation-repository, en de knop zou daar weigeren — dus
   de meest voorkomende situatie in de basisworkflow zou falen op stap één.
5. **Eén bevinding die niet meer gevonden wordt, blijft staan.** Weggooien zou
   het ononderscheidbaar maken van een bevinding die nooit gemaakt is.

**Let op bij de volgende migratie:** `init_db()` roept `create_all` aan op
SQLite (`app/db.py:219`), dus het starten van de dev-server maakt een ontbrekende
tabel aan *zonder* de alembic-stempel. Daarna lukt `alembic upgrade head` niet
meer met "table already exists". De oplossing is `alembic stamp head` — het
schema is dan immers al correct. Zelfde waarheid als in
`tests/test_migrations.py::test_existing_create_all_database_is_adopted_by_stamping`.
Een testrun zelf raakt `apollo_dev.db` niet: gecontroleerd, byte-identiek
voor en na.

**Klaar als:** met een key heeft elk nieuw document 0..n signalen met reden en
werkende link; zonder key is de knop uitgeschakeld met uitleg; dismiss verbergt en
verwijdert niets; de bestandshashes vóór en na de analyse gelijk zijn.

- Signaalvorm uitbreiden naar `{kind, reference, why}`: `why` is verplicht en mag
  niet leeg zijn, zodat een claim zonder reden de UI niet haalt.
- Opslag in een eigen tabel `doc_signals` (`workspace_id`, `repository_id`,
  `file_path`, `kind`, `reference`, `why`, `confidence`, `status`
  (`new`/`confirmed`/`dismissed`), `run_id`, `created_at`), migratie 0010. Reden:
  een signaal over een document zonder `PulseItem` heeft nu geen plek, dismiss
  heeft geen kolom, en "signalen bij deze groep" is dan een join met
  `group_placements` in plaats van JSON.
- Motor aansluiten, niet nabouwen: `signals_prompt_instruction()` in de
  systeemprompt van `services/pulse.py` en het antwoord door `parse_signals`.
  Rol `background` blijft; incrementeel op content-hash blijft.
- `api/routes_signals.py`: `POST /workspaces/{id}/delphi/analyze`,
  `GET /workspaces/{id}/signals?path=…`, `GET …/signals?group_id=…` (voor Fase 5),
  `POST …/signals/{id}/dismiss`. Zonder LLM: 503 met de bestaande melding en een
  uitgeschakelde knop met uitleg.
- UI: "Analyseren"-knop bij de documentenlijst met een badge van open signalen; een
  signaalbalk boven het leesvenster: *"Looks older than planning-2026.pdf —
  [Open] [Propose archive] [Dismiss]"*.

**Klaar als:** met een key heeft elk nieuw document 0..n signalen met reden en
werkende link; zonder key is de knop uitgeschakeld met uitleg; dismiss verbergt en
verwijdert niets; de bestandshashes vóór en na de analyse gelijk zijn.

## Fase 3 — Delphi stelt groepen voor (1–2 dagen) — **afgerond**

- Nieuwe `services/delphi_grouping.py`: `propose_clusters` (vraagt het model om te
  groeperen) en `propose_groups` (schrijft `create_group(source="ai")` en
  `place_document(placed_by="ai")`). Eén knop doet signalen **en** groepsvoorstel;
  de gebruiker vroeg om één ding, niet om twee schermen.
- De mens wint bij een re-scan: een document met `placed_by="user"` wordt nooit
  teruggezet, en een AI-groep die leegloopt blijft bestaan als leeg in plaats van
  stilzwijgend opgeruimd.
- `GroupsBoard` blijft ongewijzigd: slepen is klaar en schrijft alleen naar de
  database.

**Afwijking van de oorspronkelijke tekst, met reden.** De clusters komen uit de
*bevindingen* van de leespass, niet uit de bestaande pulse-connections. Pulse
verloopt in batches en levert bovendien pas iets op nadat er een scan is
gedraaid, dus een cluster dat over twee leesbatches heen ligt zou daar onzichtbaar
blijven. De clustering krijgt daarom een eigen, kleine pass: de bevindingen (paar
honderd tokens voor de hele collectie) plus de volledige padenlijst. Het model
leest de documenten geen tweede keer, dus het kan geen structuur verzinnen die de
bewijzen niet dragen. De pass mag falen zonder de bevindingen mee te nemen.

**Klaar als:** analyseren op tien bestanden twee tot vier groepen oplevert met
gewone namen en elk lid met een reden; één foute groep met één drag gecorrigeerd
is en gecorrigeerd blijft na opnieuw analyseren.

## Fase 4 — Fysiek & archief (1–2 dagen)

- De eerste toewijzing aan een groep levert een **voorstel** via
  `plan_move(..., allow_missing_dir=True)`, geaccepteerd in het bestaande
  voorstellenscherm. Daarna is de map stabiel en zijn latere groepsveranderingen
  visueel.
- Archiveren is naar de `Archief`-groep plus een voorstel om fysiek naar
  `Archief/` te gaan: één woord, één constante (`ARCHIVE_CATEGORY`).
- Geen delete-endpoint en geen `unlink` in nieuwe code.

**Klaar als:** archiveren bewaart, de verplaatsing als voorstel verschijnt, weigeren
alles ongemoeid laat, en alleen de aangeboden move in `git status` staat.

## Fase 5 — Contextsidebar (1 dag)

- Bij een document: "Hoort bij" (via het bestaande maar ongebruikte
  `groupsOfDocument`), signalen met reden, relaties, en acties (naar archief,
  verbergen, chat).
- Bij een groep: leden, open signalen, "Delphi stelde dit voor".
- Chat wordt een secundaire actie in plaats van de default tab.

**Klaar als:** een document selecteren groep en signalen toont zonder extra klik,
en een groep openen leden en signalen toont.

## Fase 6 — Opruimen (halve dag)

- `repos`, `inventory`, `pulse`, `decisions` en `questions` naar "Meer" of achter
  een instelling; embeddings, vector en sources blijven backend-only (verbergen,
  niet slopen).
- README: de basisworkflow (Inbox → Analyseren → Groepen → Slepen → Archief) als
  eerste sectie.
- `npm run smoke:basis`: drop → analyse op de mock-provider → groep → drag →
  archief.

## Harde regels

1. Apollo verwijdert nooit documenten. Geen `unlink`, geen `rmdir`, geen
   delete-endpoint voor documenten.
2. Visueel slepen is uitsluitend een databasewijziging.
3. Elk schrijven aan een bestaand bestand loopt via plan → accept, nooit
   automatisch committen.
4. Elk pad via `safe_path()`, en een door de app beheerde root staat expliciet in
   de allow-list — anders weigert Apollo zijn eigen map.
5. Eén woord per begrip: "Archief" is de groep én de map, en er is één definitie
   van gearchiveerd.
6. Signalen zijn voorstellen, geen beslissingen. Elk signaal heeft een reden en
   een controleerbare verwijzing.
7. Een groep is een beeld, geen schijfwijziging — en een groep die de lezer heeft
   geordend, is van hem. Geen enkele pass zet een document terug dat de lezer
   zelf heeft geplaatst, en de API kan dit niet omzeilen.
8. Code en UI in het Engels.
9. Elke fase eindigt met een groene testrun en een commit.

## Nu expliciet niet doen

Embeddings en vector search, chat-modes, source-clone en sync, knowledge graph,
auto-apply, geplande scans, code-analyse. Die staan al in de backend en blijven
daar staan; ze komen niet in de weg van de basisworkflow.

## Eerstvolgende stap

Fase 4 — Fysiek & archief. De eerste toewijzing aan een groep levert een
voorstel op (`plan_move(..., allow_missing_dir=True)`) in het bestaande
voorstellenscherm; archiveren is naar de `Archief`-groep plus zo'n voorstel.
Tot die tijd is de documentatie op schijf statisch en de groepen lopen er
bewust nog los naast — dat is de scheiding die Fase 4 nu gaat sluiten.


