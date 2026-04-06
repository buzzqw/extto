# EXTTO — Briefing per Sessioni AI

> Leggi questo documento + `extto_mappa.md` prima di qualsiasi modifica al codice.  
> Non chiedere spiegazioni già contenute qui o nella mappa: vai diretto al task.

---

## Chi sono e cosa è EXTTO

Sono **Andres**, sviluppatore del progetto EXTTO. Lavoro su Linux (Debian/Rocky/Arch) e sono esperto di Python, LaTeX e amministrazione di sistema.

EXTTO è un sistema **personale** di automazione download torrent per contenuti italiani (serie TV, film, fumetti). Non è un prodotto commerciale: è un codebase monolitico che ho sviluppato e che faccio evolvere iterativamente con il supporto di AI. Il codice è in produzione su un server Linux e scarica contenuti da feed RSS italiani (ExtTo, Il Corsaro Nero) verso un NAS locale.

**Devi trattarmi come un non programmatore. Quando le modifiche sono minime (poche righe), mostrami solo cosa cambia con PRIMA/DOPO. Dammi file completi solo quando le modifiche sono sostanziali. Parla semplice.**  
**Assicurati ogni volta di restituire file/modifiche con tutte le opzioni/comandi, senza perdita di funzionalità.**

---

## Stack tecnico

- **Linguaggio:** Python 3.11+, tutto in un package `core/`
- **DB:** SQLite (due database separati: `extto_series.db` per lo stato, `extto_archive.db` per la memoria storica)
- **Client torrent primario:** libtorrent embedded (`python-libtorrent`) — è l'unico con post-processing automatico
- **Client torrent alternativi:** qBittorrent, Transmission, aria2 — solo `add(magnet)`, nessun hook di completamento
- **Web UI:** Flask/Waitress come sottoprocesso
- **Notifiche:** Telegram + Email
- **Metadati:** TMDB API v3
- **Nessun framework esterno** per il core: solo stdlib + requests + beautifulsoup4

---

## Convenzioni operative — CRITICHE

**1. `ep` dict** — prodotto da `Parser.parse_series_episode()`. Ha sempre: `type`, `name`, `season`, `episode`, `quality`, `title`. Se `is_pack=True` ha anche `episode_range` (list di int). `is_pack=True` con qualsiasi `episode` (anche > 0) indica un pack multi-episodio o stagionale — viene sempre gestito da `_handle_season_pack()`.

**2. `check_series()` prima di `client.add()`** — sempre. `check_series()` registra nel DB, verifica duplicati, chiama il cleaner. Non si bypassa mai.

**3. Post-processing automatico solo con libtorrent** — rename, move NAS, cleaner funzionano solo nell'alert loop di `LibtorrentClient`. Con qBittorrent/Transmission/aria2 il post-processing è a carico del client esterno.

**4. `cleanup_old_episode()` vuole `new_fname`** — il nome del file dopo il rename TMDB, non solo il nome originale del torrent. Senza `new_fname`, il file appena rinominato rischia di finire in trash.

**5. Ordine cleaner:** sempre `discard_if_inferior()` (Scenario A: il nuovo è peggio?) **prima** di `cleanup_old_episode()` (Scenario B: i vecchi sono peggio?). L'ordine è semanticamente importante.

**6. `normalize_series_name()`** — usarla sempre per confrontare nomi di serie. Non confrontare stringhe grezze. La funzione già converte punti/underscore in spazi — non farlo due volte. Per il matching usare `_series_name_matches()` che gestisce anche il possessivo.

**7. `stats` è un singleton** — `reset()` a inizio ciclo, `report()` a fine ciclo. Non istanziare.

**8. Config si rilegge ogni ciclo** — nessun riavvio per modifiche a `extto.conf`. `libtorrent.py` rilegge `extto.conf` direttamente tramite filesystem walk perché opera fuori dal ciclo principale.

**9. `tvdb_id` nel DB = `tmdb_id`** — eredità storica, non rinominare la colonna.

**10. `best_by_ep` — chiave `(series_id, season, episode)`** — per i pack, `episode` è il primo del range. La deduplicazione pack sovrapposti usa `set.issubset()` sull'`episode_range` per evitare di scaricare sottoinsiemi già coperti da un pack più ampio.

**11. `progress` nell'API libtorrent è 0-100** — non 0-1. I valori di `state` possono essere `seeding_t`, `finished_t` (con suffisso `_t`) oltre a `seeding`, `finished`. Tenerne conto nei check.

**12. Season pack: `is_pack=True` è sufficiente** — La condizione per chiamare `_handle_season_pack()` è `bool(ep_info.get('is_pack'))` senza il check `episode == 0`. I pack parziali tipo `S01E01-05` hanno `episode=1` ma `is_pack=True`.

**13. `_handle_season_pack()` copia file flat** — copia sempre i singoli file video in `nas_path` senza creare sottocartelle. Il pack originale rimane in `libtorrent_dir` per il seeding. Ordine: copia → discard_if_inferior → rename → cleanup_old_episode.

**14. Shutdown libtorrent** — attende esplicitamente tutti gli alert `save_resume_data_alert` prima di chiudere (max 10s), così il `save_path` aggiornato dopo `move_storage` viene sempre persistito nel `.fastresume`.

---

## Come lavoriamo insieme

**Cosa mi aspetto da te:**

- Leggi mappa + briefing, poi lavora direttamente senza chiedermi di rispiegarti l'architettura
- Quando modifichi un file, parti **sempre dal sorgente di produzione** che ti allego — mai da output di sessioni precedenti (potrebbero mancare fix manuali intermedi)
- Se serve un file che non ho allegato, dimmelo esplicitamente prima di procedere
- Verifica sempre la sintassi Python prima di consegnarmi un file (`ast.parse`)
- Per modifiche a `extto3.py` o `libtorrent.py`: testa la logica critica con un mini-script inline se possibile
- **Modifiche minime → mostra solo PRIMA/DOPO con contesto. File completo solo se le modifiche sono tante o sparse.**

**File minimi per task comuni** (vedi anche mappa §12):

| Task | File da allegare |
|------|-----------------|
| Ciclo principale / best-in-cycle | `extto3.py` |
| Post-processing libtorrent | `clients/libtorrent.py` |
| Parsing titoli / qualità | `models.py` |
| DB / upgrade / cleaner | `database.py`, `cleaner.py` |
| Rename file | `renamer.py` |
| Web UI / API | `extto_web.py`, `index.html` |
| Scraping RSS | `engine.py` |
| Fumetti | `comics.py` |
| Stile UI | `style.css` |

---

## Stato attuale del progetto (v40)

### Modifiche sessione 3 marzo 2026 (Aggiornamento v32)

**Deduplicazione e Protezione Duplicati:**
- **Deduplicazione Hash Attivi**: `extto3.py` ora controlla gli hash già presenti nel client torrent prima di processare i candidati, evitando download doppi dello stesso magnet nello stesso ciclo o tra cicli vicini.
- **Protezione Episodi Concorrenti**: In `database.py`, la funzione `check_series` verifica se lo stesso episodio (SxxExx) è già in fase di download nel client, bloccando l'aggiunta di duplicati concorrenti.
- **Risoluzione Conflitti Rinomina**: In `renamer.py`, se il file di destinazione esiste già, il sistema ora confronta la qualità dei due file e sposta automaticamente quello inferiore nel trash, permettendo al file migliore di essere rinominato correttamente invece di fallire.

**Gestione Audio, Gruppi e Punteggi:**
- **Scoring Audio**: Il sistema ora valorizza la componente sonora. Aggiunti bonus per formati: `DTS-HD` (+150), `DTS` (+120), `DDP/E-AC3` (+100), `AC3` (+80), `5.1` (+50), `MP3/AAC` (+30).
- **Release Group**: Il `Parser` ora estrae il gruppo di rilascio (es. MIRCrew, NovRip) e assegna bonus specifici, aiutando a distinguere versioni diverse della stessa qualità.
- **Bonus ITA**: Aggiunto un bonus pesante di **+500 punti** per la presenza dell'audio italiano (`ITA` o `Italian` nel titolo).
- **Visualizzazione Qualità**: La stringa qualità ora include i tag audio, gruppo e lingua (es: `1080p/WEB/H265/ITA/DDP/MIRCREW`).

## v40 - Download Mega via megatools + fix Pulisci (13 Marzo 2026)

**Download Mega (`comics.py`):**
- `download_comic_mega_bg()` riscritta per usare `megatools` (`megadl`) via `subprocess` — rimossa dipendenza da `mega.py`
- Richiede: `apt install megatools` sul server (una tantum)
- Progresso tracciato in `ACTIVE_HTTP_DOWNLOADS` come i download HTTP: stato, percentuale, velocità, ETA visibili nel Torrent Manager
- `_resolve_mega_url()`: gestisce redirect JS di GetComics (`/dlds/...`) con 3 metodi in cascade (meta refresh → script location → link diretto)
- `_parse_megadl_progress()`: parser riscritto per il formato reale di megadl: `filename.cbz: 16,19% - 3,5 GiB (1234567 byte) of 3,5 GiB (10,1 MiB/s)` — il formato precedente era errato
- Log ridotti: da una riga per ogni % → solo ai milestone multipli di 5% (0%, 5%, 10%…100%)

**Pulsante Pulisci (`extto_web.py`, `app.js`, `index.html`):**
- Problema: il Pulisci rimuoveva solo i torrent libtorrent, ignorando `ACTIVE_HTTP_DOWNLOADS` (dove vivono i download Mega/HTTP completati)
- Nuovo endpoint Flask: `POST /api/http-downloads/remove-completed` — rimuove dal dict le voci con stato `Terminato`, `Errore`, `Salvato`
- `removeCompletedTorrents()` (app.js) ora chiama entrambi gli endpoint in sequenza e mostra nel toast quanti di ciascun tipo ha rimosso
- Tooltip aggiornato: indica che pulisce sia torrent che download HTTP/Mega

## v39 - Error Reporting Dettagliato (8 Marzo 2026)
- **`CycleStats.add_error(category)`**: aggiunto metodo in `models.py` al posto del semplice `stats.errors += 1`. Tiene un dizionario `error_details = {categoria: conteggio}` oltre al contatore totale.
- **Report errori con dettaglio**: il `report()` ora stampa `⚠️ Errori: 4 (2 Jackett timeout, 2 scraping)` invece del generico `Errori: 4`.
- **`engine.py`**: i due `stats.errors += 1` sostituiti con `stats.add_error("scraping")` (errori scraping siti) e `stats.add_error("Jackett timeout" if "timeout" in str(e).lower() else "indexer")` (errori Jackett/Prowlarr).

## v38 - Feed Film con Bonus Lingua e Sottotitoli (8 Marzo 2026)
- **Feed film** (`database.py`, `extto3.py`, `extto_web.py`, `app.js`): implementato feed "best 5" per i film analogo a quello delle serie TV, ma senza requisito lingua obbligatorio.
- **Bonus scoring film feed**: +500 se lingua soddisfatta, +400 se sottotitolo soddisfatto — garantiscono che i risultati con lingua/sub scalino sempre in cima anche se la qualità tecnica è inferiore.
- **Tabella `movie_feed_matches`**: schema aggiornato con colonna `movie_name TEXT` (versioni precedenti usavano `movie_id INT`). All'avvio verifica se la colonna esiste; se no, fa DROP e ricrea.
- **Ricerca Jackett sottotitoli**: `engine.py` ora esegue ricerche separate per ogni termine subtitle (`sub ita`, `sub it` ecc.) e unisce i risultati.

## v37 - Redesign Scoring & Grid Layout (3 Marzo 2026)
- **Redesign Scoring (4 Colonne)**: L'interfaccia di scoring ora separa chiaramente Risoluzione, Sorgente/Codec, Audio/Lingua e Bonus Extra in 4 card distinte con colori specifici.
- **Simulatore Punteggio Compatto**: Lo strumento di verifica rapida è stato spostato in fondo alla sezione e ridotto di dimensioni per non intralciare la configurazione.
- **Aggiornamento Modale TUI**: La modale `ScoreModal` nel terminale ora permette la modifica granulare di tutte le mappe (Res, Source, Codec, Audio, Groups) in un formato elenco coerente.

## v36 - Scoring Simulator & Stability (3 Marzo 2026)
-   **Scoring Additivo & Simulatore**: Redesign completo dell'interfaccia di scoring. Introdotto il **Simulatore di Punteggio** per verificare istantaneamente il valore di un torrent in base ai parametri impostati (Risoluzione, Sorgente, Codec, Audio, Lingua, DV).
-   **Rimozione Wanted/Calendar**: Rimosse le viste "Mancanti" e "Calendario" per migliorare le performance e la stabilità del sistema, in quanto ritenute troppo pesanti per l'utilizzo comune.
-   **Clarificazione NAS**: Chiarito il ruolo di `@archive_root` come radice per l'individuazione automatica delle serie.
-   **Cosmetica**: Aumentata la dimensione del font per gli esempi del formato rinomina nell'interfaccia.
-   **Fix Pulizia**: Il pulsante "Pulisci" ora rimuove correttamente anche i torrent in stato `FINISHED` o `FINISHED_T`.

## v35 - UX & Health Polish (3 Marzo 2026)
- **Fix Pulizia Torrent**: Il pulsante "Pulisci" ora rimuove correttamente anche i torrent in stato `finished` o `finished_t` (oltre a quelli al 100%).
- **Riorganizzazione UI Avanzata**: Le impostazioni avanzate sono ora raggruppate in categorie logiche (Rinomina, Pulizia, Automazione, Logging, Database) per una navigazione più intuitiva.
- **Supporto Cloud Backup Esteso**: Aggiunta la configurazione nell'interfaccia per **Google Drive** e **OneDrive** (oltre a Dropbox e FTP) nella sezione Manutenzione.
- **Pannello Salute Potenziato (v2)**: 
  - **Servizi Systemd**: Monitoraggio in tempo reale dello stato di `extto.service`, `jackett.service` e `prowlarr.service`.
  - **Log Errori**: Visualizzazione immediata delle ultime 10 righe di errore dal file `extto.log` direttamente nella UI (Web e TUI).
- **TUI Alignment**: La Tab Salute nel terminale ora mostra anche lo stato dei servizi e gli ultimi errori.
- **Episodi Mancanti (Wanted)**: Nuova vista consolidata (Web/TUI) per visualizzare tutti i buchi nelle stagioni di tutte le serie abilitate.
- **Calendario Uscite**: Integrazione date di messa in onda da TMDB per visualizzare i prossimi episodi in arrivo.
- **Backup Cloud Dropbox**: Supporto per il caricamento dei backup su Dropbox tramite App Token (inserito nel campo password).
- **Ricerca Smart UI**: Refactoring dell'interfaccia di ricerca manuale per evidenziare chiaramente i motivi di scarto (rejections) dei torrent e permettere la forzatura del download.
- **TUI Update**: Aggiunti Tab "Mancanti" e "Calendario" nel terminale per parità di funzionalità con la Web UI.

## v33 - Advanced Features & Health (3 Marzo 2026)
- **Ricerca Avanzata**: Supporto per filtri booleani `+` (incluso) e `-` (escluso). Esempio: `1080p -ita` cerca release 1080p che non contengono la parola 'ita'.
- **Dashboard Health**: Nuova sezione per monitorare in tempo reale:
  - Spazio disco (Locale, NAS Archive, Trash) con avvisi se > 90%.
  - Stato Indexer (Jackett, Prowlarr).
  - Statistiche risorse (CPU, RAM, Uptime).
  - Permessi cartelle (Read/Write check).
- **Scoring Personalizzabile**: Punteggi di qualità non più hardcoded, ma configurabili via UI e `@score_*` in `extto.conf`.
- **Pulizia Automatica**: Opzione `@auto_remove_completed` per rimuovere torrent in stato 'finished' alla fine di ogni ciclo.
- **Supporto Dolby Vision**: Riconoscimento tag `DV`, `DoVi`, `Dolby Vision` con bonus di **+300 punti** nello scoring.
- **Statistiche di Consumo**: Tracciamento dei GB scaricati (Ultimi 7 giorni, Ultimi 30 giorni, Totale Storico).
- **Auto-Backup Cloud**: Integrazione backup automatico su server **FTP**.
- **Aggiornamento TUI**: Nuova Tab "Salute" nel terminale e visualizzazione consumi nella Dashboard.
- **Data Persistence**: Aggiunta colonna `size_bytes` alle tabelle `episodes` e `movies` per il calcolo dei consumi.

## v32 - Ottimizzazione NAS & Rebranding (Marzo 2026)
- **Caching NAS**: Introdotta `PHYSICAL_FILE_CACHE` (TTL 5 min) per velocizzare l'interfaccia quando si scansiona il NAS.
- **Rebranding Totale**: Rimosso ogni riferimento residuo a "Sonarr", sostituito con "EXTTO" (log, API, UI).
- **Unificazione Parsing**: Centralizzata la logica di qualità/scoring in `core/models.py`.
- **Persistenza Percorsi**: Aggiunta colonna `archive_path` in `episodes` per evitare scansioni filesystem ripetitive.
- **Cleanup Fisico**: Nuova opzione `@cleanup_action` (`move` o `delete`) per gestire i duplicati.
- **Protezione Download**: Deduplicazione hash magnet link e blocco download concorrenti dello stesso episodio.

**Pulizia Codice:**
- Rimossi blocchi `try...except Exception: pass` silenziosi e sostituiti con log di debug per una migliore tracciabilità.
- Eliminato codice "morto" (vecchie funzioni di ricerca Jackett inutilizzate).
- Snidate dipendenze circolari tramite riorganizzazione degli import.

### Modifiche sessione 2 marzo 2026 (v31)

- **Season pack handler** (`libtorrent.py`): `_handle_season_pack()` gestisce copia episodi sul NAS + `discard_if_inferior()` + `cleanup_old_episode()` + rename TMDB per ogni singolo file
- **Deduplicazione pack sovrapposti** (`extto3.py`): in `best_by_ep`, i pack il cui `episode_range` è sottoinsieme di un pack già presente vengono scartati
- **Mappa aggiornata** a v30

### Modifiche sessioni precedenti (già in produzione)

- `cleaner.py` — pulizia duplicati/upgrade con trash_path
- Trigger dominio-specifici (`/tmp/extto_run_series`, ecc.) e API `/api/run_now?domain=X`
- Log level configurabile a runtime da UI (`/api/log_level`)
- Ottimizzazione log TMDB/engine/database

---

## Aree da non toccare senza motivo

- **Schema SQLite** — le tabelle hanno dipendenze implicite; modifiche richiedono migration esplicita
- **`normalize_series_name()`** — toccarla rompe il matching di tutte le serie
- **Interfaccia `add(magnet, cfg)`** dei client — tutti e quattro i client la espongono identica, non aggiungere parametri
- **`tvdb_id` → `tmdb_id`** — non rinominare, ci sono query sparse in tutto il codice

**Assicurati ogni volta di restituire file/modifiche con tutte le opzioni/comandi, senza perdita di funzionalità.**
