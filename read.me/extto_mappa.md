**EXTTO**

Mappa Architetturale del Codice

*Documento di riferimento per analisi AI --- v38 (16 marzo 2026)*

  --------------- -------------------------------------------------------
  **Versione**    v40 --- Migrazione config da file a DB:
                  Nuovo core/config_db.py (extto_config.db),
                  Config._load() legge da DB con migrazione automatica,
                  tabella series estesa con tutti i campi di series.txt,
                  sync_configs aggiornato a upsert completo,
                  parse/save_series_config riscritta per DB,
                  endpoint API migrazione + banner UI wizard
  **Data**        17 Marzo 2026 (v40)
  **Status**      STABILE - COMPLETO
  --------------- -------------------------------------------------------

  **Versione**    v40 --- Aggiornato con: Consumo Dashboard ora
                  funzionante (fix get_consumption_stats in extto_web.py),
                  flusso rinomina episodi ottimizzato (no doppia analisi),
                  pulsante Esegui Rinomina con stato corretto,
                  size_bytes aggiornato da renamer dopo ogni download.
                  Fix ricalcolo score: cascata 5 livelli (original_title →
                  feed_matches → disk scan NAS via series.txt → title),
                  allineamento episode_archive_presence, fix ExtToDB vs
                  core.Database. Fix parse_quality: DV/h265/[IT] da titoli
                  rinominati. UI: colonna Aggiunto, larghezza 1600px,
                  sottotitolo sempre nome file fisico.

  **Scopo**       Riferimento architetturale rapido per sessioni AI
                  successive. Include tutte le modifiche recenti.

  **Autore**      Generato automaticamente da Claude (Anthropic)
  --------------- -------------------------------------------------------

**Devi trattarmi come un non programmatore. dammi sempre file completi e parla semplice**
**Assicurati ogni volta di restituire file con tutte le opzioni/comandi, senza perdita di funzionalita'**

**1. Panoramica del Sistema**

EXTTO è un sistema di automazione per il download di contenuti
multimediali tramite torrent. Monitora feed RSS di siti specializzati
(ExtTo, Il Corsaro Nero), confronta i risultati con le serie TV e i film
configurati, e scarica automaticamente i nuovi episodi usando uno dei
client torrent supportati.

**Flusso Principale (ogni ciclo \~2h)**

  ----------------------------------------------------------------------------------------
  **\#**   **Fase**                             **Dettaglio**
  -------- ------------------------------------ ------------------------------------------
  1        Config().load()                      Legge extto.conf + series.txt + movies.txt

  2        Engine.scrape_all()                  Scraping RSS da tutti gli URL configurati

  3        Engine.search_archive_for_config()   Cerca nell\'archivio locale

  4        Parser.parse_series_episode() /      Classifica ogni item
           parse_movie()                        

  5        Best-in-cycle                        Seleziona il candidato migliore per ogni
                                                episodio

  6        db.check_series()                    Verifica duplicati, upgrade qualità,
                                                archivio

  7        client.add(magnet)                   Invia al client torrent attivo

  8        Timeframe                            Gestisce attese qualità temporizzate

  9        Gap filling                          Cerca e scarica episodi mancanti

  10       TMDB                                 Aggiorna metadati stagioni, rinomina file
                                                scaricati

  11       stats.report()                       Log riassuntivo del ciclo
  ----------------------------------------------------------------------------------------

**2. Struttura dei File**

  -----------------------------------------------------------------------------
  **File**                   **Classe/Modulo**   **Responsabilità**
  -------------------------- ------------------- ------------------------------
  extto3.py                  main() + web_task() Entry point, ciclo principale,
                                                 trigger dominio-specifici

  core/constants.py          ---                 Costanti globali, logger,
                                                 set_log_level(), utility

  core/config.py             Config              Lettura extto.conf,
                                                 series.txt, movies.txt

  core/config_db.py          ---                 Gestione extto_config.db:
                                                 settings (chiave/valore) +
                                                 movies_config. Migrazione
                                                 automatica da file .conf/
                                                 .txt al primo avvio. ★ v40

  core/models.py             Parser, Quality,    Parsing titoli torrent,
                             CycleStats          calcolo qualità, statistiche.
                                                 parse_quality: fix DV/h265/
                                                 [IT] da titoli rinominati
                                                 ★ v39

  core/database.py           Database,           Persistenza SQLite (episodi,
                             ArchiveDB,          film, pending, metadata)
                             SmartCache          

  core/engine.py             Engine              Scraping RSS (ExtTo, Corsaro,
                                                 Jackett), XML feed

  core/tmdb.py               TMDBClient          Integrazione TMDB API v3
                                                 (metadati stagioni, titoli)

  core/renamer.py            ---                 Rinomina file video dopo
                                                 download (via TMDB)

  core/cleaner.py            ---                 Pulizia duplicati / upgrade
                                                 qualità (NUOVO)

  core/tagger.py             Tagger              Tagging torrent su qBittorrent
                                                 (Serie TV / Film)

  core/notifier.py           Notifier            Notifiche Telegram ed Email

  core/mediainfo_helper.py   ---                 Estrazione tag tecnici video
                                                 (risoluzione, codec, HDR)

  core/comics.py             ComicsDB,           Fumetti (getcomics.org, weekly
                             GetComicsScraper    pack)

  core/utils.py              ---                 I/O JSON thread-safe:
                                                 safe_load_json(),
                                                 safe_save_json() con lock
                                                 per file. ★ NUOVO v38

  core/\_\_init\_\_.py       ---                 Package export di tutte le
                                                 classi principali

  extto_web.py               ---                 Web UI separata
                                                 (Flask/Waitress),
                                                 sottoprocesso.
                                                 rescore_episodes: cascata
                                                 5 livelli + core.Database
                                                 per disk scan ★ v39

  backfill_size_bytes.py     ---                 Script one-shot: aggiorna
                                                 size_bytes nel DB per
                                                 episodi già scaricati.
                                                 Usa libtorrent (passo 1)
                                                 e scan disco (passo 2).
                                                 ★ NUOVO v38
  -----------------------------------------------------------------------------

**3. Moduli Core**

**3.1 constants.py --- Costanti e Utility Globali**

  ---------------------------------------------------------------------------------
  **Simbolo**                 **Tipo**      **Descrizione**
  --------------------------- ------------- ---------------------------------------
  PORT = 8889                 int           Porta del server HTTP interno

  CONFIG_FILE                 str           \"extto.conf\" --- configurazione
                                            principale

  SERIES_FILE                 str           \"series.txt\" --- elenco serie
                                            monitorate

  DB_FILE                     str           \"extto_series.db\" --- database SQLite
                                            principale

  ARCHIVE_FILE                str           \"extto_archive.db\" --- archivio
                                            torrent visti

  REFRESH = 7200              int           Intervallo ciclo in secondi (2 ore)

  logger                      Logger        Logger globale con RotatingFileHandler
                                            su extto.log

  set_log_level(level_str)    fn→bool       Imposta il livello di logging a runtime
                                            (debug/info/warning/error). Agisce su
                                            logger + tutti gli handler. ★ NUOVO

  get_log_level()             fn→str        Ritorna il livello corrente come
                                            stringa lowercase. ★ NUOVO

  sanitize_magnet(magnet)     fn→str        Normalizza/valida un magnet link;
                                            ritorna None se invalido

  parse_date_any(text)        fn→datetime   Parsifica date in vari formati italiani
                                            e ISO

  \_load_feed_buffer()        fn→list       Carica il buffer JSON dei feed recenti

  \_save_feed_buffer(items)   fn            Salva il buffer feed su file JSON

  \_extract_btih(magnet)      fn→str        Estrae info-hash da un magnet link
  ---------------------------------------------------------------------------------

**3.2 config.py --- Config**

Responsabilità: legge e valida la configurazione completa dall\'utente.

  -----------------------------------------------------------------------------------
  **Metodo**                      **Input**   **Output / Effetto**
  ------------------------------- ----------- ---------------------------------------
  \_\_init\_\_()                  ---         Chiama \_load(), popola tutti gli
                                              attributi pubblici

  \_load()                        ---         Legge extto.conf, series.txt,
                                              movies.txt; popola self.series,
                                              self.movies, self.urls, self.qbt, ecc.

  \_parse_series_line(line)       str         Parsifica una riga di series.txt → dict
                                              {name, seasons, qual, lang, enabled,
                                              archive_path, \...}

  find_series_match(name, season) str, int    Confronta nome normalizzato con
                                              self.series; ritorna il dict serie
                                              configurata o None

  find_movie_match(name, year)    str, int    Come find_series_match ma per film

  \_min_res_from_qual_req(qual)   str         Converte \"720p+\" o \"\<720p\" o
                                              \"any\" → rank minimo (es. 4). Supporta
                                              sintassi flessibile. ★ AGGIORNATO

  \_max_res_from_qual_req(qual)   str         Converte \"720p-1080p\" o \"\<720p\" o
                                              \"any\" → rank massimo. ★ AGGIORNATO

  \_res_rank_from_title(title)    str         Estrae rank risoluzione dal nome
                                              torrent

  \_lang_ok(title, req_lang)      str, str    True se la lingua richiesta è presente
                                              nel titolo

  get_custom_score(title)         str         Aggiunge score bonus se il titolo
                                              matcha \@bonus_score regole
  -----------------------------------------------------------------------------------

*Attributi pubblici principali: self.series (list\[dict\]), self.movies
(list\[dict\]), self.urls (list\[str\]), self.qbt (dict parametri
client/qualità), self.tmdb_api_key, self.notify_telegram,
self.gap_filling.*

  -----------------------------------------------------------------------
  💡 Risoluzione Sovrana (v32): Il moltiplicatore risoluzione è ora
  10.000 (era 1.000). Questo garantisce che una risoluzione superiore
  vinca sempre su qualsiasi combinazione di bonus di una risoluzione
  inferiore (es. un 4K base batte sempre un 1080p con audio DTS e bonus).

  -----------------------------------------------------------------------

  -----------------------------------------------------------------------
  💡 Caching NAS (v32): Implementata cache in extto_web.py per la
  ricerca fisica dei file sul disco (PHYSICAL_FILE_CACHE). Riduce
  carico NAS e latenza UI.

  -----------------------------------------------------------------------

**3.3 models.py --- Parser, Quality, CycleStats**

**Parser (classe statica)**

  -------------------------------------------------------------------------------
  **Metodo**                    **Input**    **Output**
  ----------------------------- ------------ ------------------------------------
  parse_series_episode(title)   str (nome    dict con: type, name, season,
                                torrent)     episode, episode_range (list se
                                             pack), is_pack (bool), quality
                                             (Quality), title --- oppure None

  parse_movie(title)            str          dict con: type, name, year, quality,
                                             title --- oppure None

  parse_quality(title)          str          Quality object con: resolution,
                                             source, codec, is_repack, is_proper,
                                             is_real

  is_blacklisted(title)         str          Tuple\[bool, str\|None\] --- (True,
                                             termine) se nella blacklist

  is_wanted(title)              str          bool --- True se tutti i termini
                                             wantedlist sono presenti
  -------------------------------------------------------------------------------

**Quality**

  -------------------------------------------------------------------------
  **Attributo/Metodo**   **Tipo**   **Descrizione**
  ---------------------- ---------- ---------------------------------------
  resolution             str        "4K", "1080p", "720p", "576p",
                                    "480p", "360p", ""

  audio                  str        "DTS-HD", "DTS", "DDP", "AC3", "MP3",
                                    "AAC", "5.1", "" ★ v32

  group                  str        "MIRCREW", "NOVRIP", "PITTY", ... ★ v32

  is_ita                 bool       True se audio italiano presente ★ v32

  is_dv                  bool       True se Dolby Vision presente ★ v33

  source                 str        "BluRay", "WEB-DL", "WEBRip",
                                    "HDTV", "CAM", ecc.

  codec                  str        "x265", "x264", "AV1", "HEVC",
                                    ecc.

  is_repack / is_proper  bool       Flag qualità speciali
  / is_real

  score()                fn→int     (rank_res × RES_MULT) + source_pref +
                                    codec_pref + audio_pref + group_pref +
                                    bonus_ita + bonus_dv + flags. (Configurabile v33)
  -------------------------------------------------------------------------

**3.4 database.py --- Database, ArchiveDB, SmartCache**

**Database (extto_series.db)**

  -----------------------------------------------------------------------------
  **Metodo**                     **Input**       **Output / Effetto**
  ------------------------------ --------------- ------------------------------
  check_series(ep, magnet,       dict, str, int, (bool, str) --- True se il
  new_score, series_id)          int             download è approvato. Verifica
                                                 duplicati, upgrade, archivio
                                                 su disco e hash/episodi già
                                                 attivi nel client. Chiama cleaner
                                                 se cleanup abilitato. ★ AGGIORNATO

  check_movie(movie, magnet,     dict, str, int, (bool, str) --- Come
  new_score, movie_id)           int             check_series ma per film

  find_gaps(series_id, season)   int, int        list\[int\] --- episodi
                                                 mancanti tra 1 e max atteso
                                                 (da TMDB)

  \_best_quality_in_path(name,   str, int, int,  int\|None --- score del file
  s, e, path)                    str             migliore trovato su disco. Ora
                                                 silenzioso con level=INFO. ★
                                                 AGGIORNATO

  upsert_series_metadata(sid,    int, int, dict  Salva metadati stagioni TMDB
  tmdb_id, counts)                               

  is_tvdb_cache_fresh(sid, days) int, int        bool --- True se la cache TMDB
                                                 è ancora valida

  save_cycle_history(payload)    dict            Salva log storico cicli

  record_episode_discard(sid, s, int, int, int,  Logga un rifiuto per debugging
  e, reason)                     str             
  -----------------------------------------------------------------------------

  -----------------------------------------------------------------------
  ★ I log \[ARCHIVE-CHECK\] di routine (scansione filesystem, nessun
  match) sono ora a livello DEBUG. Con level=INFO appaiono solo match
  trovati (✅) e path non esistenti (⚠️). ★ AGGIORNATO

  -----------------------------------------------------------------------

**3.5 engine.py --- Engine**

  --------------------------------------------------------------------------------
  **Metodo**                       **Input**       **Output / Effetto**
  -------------------------------- --------------- -------------------------------
  scrape_all(urls, cfg)            list, dict      list\[dict\] --- tutti i
                                                   torrent trovati. Una riga di
                                                   log per URL con risultato. ★
                                                   AGGIORNATO

  search_archive_for_config(cfg)   Config          Cerca nell\'ArchiveDB i
                                                   candidati per le serie
                                                   configurate

  \_extto(url)                     str             generator di dict torrent da
                                                   ExtTo

  \_corsaro(url)                   str             generator di dict torrent da Il
                                                   Corsaro Nero

  \_generic_rss(url)               str             generator di dict da qualsiasi
                                                   feed RSS standard

  \_search_indexer(query, ix, cfg) str, dict, dict list --- risultati da
                                                   Prowlarr/Jackett per la query

  \_jackett_search(query, cfg,     str, dict       list --- risultati da Jackett
  \...)                                            (legacy, mantenuto)

  update_rolling_feed(items)       list            Aggiorna il buffer JSON del
                                                   feed XML rolling
  --------------------------------------------------------------------------------

  -----------------------------------------------------------------------
  ★ Log engine ottimizzati: la riga iniziale \"Cerco\...\" è ora DEBUG,
  la riga risultato è INFO con URL + count in una sola riga. Es: \"🔎
  ExtTo \[1/3\]: 150 items --- https://extto.org/\...\". ★ AGGIORNATO

  -----------------------------------------------------------------------

**3.6 tmdb.py --- TMDBClient**

  --------------------------------------------------------------------------------
  **Metodo**                      **Input**      **Output / Effetto**
  ------------------------------- -------------- ---------------------------------
  resolve_series_id(name)         str            int\|None --- tmdb_id. Ora a
                                                 livello DEBUG (non appare con
                                                 INFO). ★ AGGIORNATO

  fetch_season_counts(tmdb_id)    int            dict{stagione: n_episodi}. Ora a
                                                 livello DEBUG. ★ AGGIORNATO

  fetch_series_details(tmdb_id)   int            dict --- dettagli estesi (poster,
                                                 trama, stagioni) per EXTTO view

  update_series_metadata(db, sid, Database, int, bool --- True se ha fatto
  name)                           str            chiamate API. Log aggiornamento a
                                                 DEBUG. ★ AGGIORNATO

  fetch_episode_title(tmdb_id, s, int, int, int  str\|None --- titolo episodio per
  e)                                             il rename

  get_tmdb_id_for_series(db,      Database, str  int\|None --- cerca prima nel DB,
  name)                                          poi su TMDB
  --------------------------------------------------------------------------------

  -----------------------------------------------------------------------
  ★ I lookup TMDB di routine (ogni serie ogni ciclo) sono ora a livello
  DEBUG. Con level=INFO appaiono solo i warning (serie non trovata,
  errori HTTP, nessuna stagione). ★ AGGIORNATO

  -----------------------------------------------------------------------

**3.7 core/clients/libtorrent.py --- LibtorrentClient ★ AGGIORNATO v31**

**Nuove opzioni `_apply_settings()` (v31)**

  -------------------------------------------------------------------------------------------
  **Gruppo**     **Chiave extto.conf**              **Chiave libtorrent**       **Default**
  -------------- ---------------------------------- --------------------------- -----------
  Coda ★         libtorrent_active_downloads        active_downloads            3
  Coda ★         libtorrent_active_seeds            active_seeds                3
  Coda ★         libtorrent_active_limit            active_limit                5
  Lenti ★        libtorrent_slow_dl_threshold (KB/s) inactive_down_rate (B/s)  2
  Lenti ★        libtorrent_slow_ul_threshold (KB/s) inactive_up_rate (B/s)    2
  File ★         libtorrent_preallocate             pre_allocate_storage        no
  File ★         libtorrent_incomplete_ext          incomplete_files_ext        no (.!extto)
  -------------------------------------------------------------------------------------------

I valori soglia lenti sono in KB/s nel conf e vengono convertiti in B/s prima di passarli a libtorrent. Tutti i parametri supportati sia in API libtorrent 2.x (dict pack) che 1.x (get_settings()).

**`_handle_season_pack()` ★ AGGIORNATO v31**

Attivazione: `is_pack = bool(ep_info.get('is_pack'))` — include sia season pack interi (episode=0) che pack parziali tipo S01E01-05 (episode=1). Non più condizione `episode == 0`.

Flusso:
1. Determina `pack_dir`: `curr_save/<h.name()>/` se esiste come dir, altrimenti `curr_save/`
2. `os.walk(pack_dir)` — trova tutti i file video ricorsivamente (max depth 3)
3. Copia **flat** ogni file in `nas_path` (mai sottocartelle)
4. Per ogni file: `discard_if_inferior()` → rename via `rename_completed_torrent()` → `cleanup_old_episode()` con `new_fname` aggiornato

Il pack originale rimane in `libtorrent_dir` per il seeding.

**`shutdown()` ★ AGGIORNATO v31**

Attende esplicitamente tutti i `save_resume_data_alert` (max 10s) prima di chiudere. Scrive ogni `.fastresume` su disco con `fsync`. Risolve il bug per cui il `save_path` aggiornato dopo `move_storage` veniva perso al riavvio del servizio.

**`set_torrent_limits(info_hash, dl_bytes, ul_bytes)` ★ NUOVO**

Imposta limiti di banda individuali per un singolo torrent via `h.set_download_limit()` / `h.set_upload_limit()`. Valori in byte/s; `-1` = nessun limite. Persistenti via `torrent_limits.json` (stesso pattern di `torrent_tags.json`): salvati al set, riapplicati all'avvio, rimossi al remove.
L'endpoint `extto3.py /api/torrents/set_limits` accetta `dl_kbps` / `ul_kbps` in KB/s e converte internamente.
`get_torrent_details()` ora restituisce anche `dl_limit` e `ul_limit` (byte/s) per mostrare i limiti attuali nell'UI.

**`get_peers(info_hash)` ★ NUOVO**

Chiama `h.get_peer_info()` e restituisce lista di peer con: `ip` (str, decodificato da bytes), `client` (str), `dl_speed` / `ul_speed` (int byte/s), `progress` (float 0-100), `flags` (S=seed O=optimistic K=snubbed U=upload-only E=endgame), `source` (int). Lista ordinata per `dl_speed` decrescente.
Endpoint: `extto3.py POST /api/torrents/peers` → proxato via `extto_web.py /api/torrents/<path:subpath>`.

**`POST /api/maintenance/clean-trash` ★ NUOVO**

Endpoint in `extto_web.py`. Legge `trash_path` e `trash_retention_days` dalla config. Elimina fisicamente i file nel cestino con `mtime < now - days*86400`. Risponde con `{deleted, freed_mb, message}`. Se `trash_retention_days` è vuoto risponde con messaggio "disabilitato" senza errore.
Job automatico identico eseguito in `extto3.py` sezione 5c ad ogni ciclo del motore.

**Grafico velocità DL/UL in tempo reale ★ NUOVO**

Nel tab Generale del dettaglio torrent: canvas HTML5 aggiornato ogni 2s con i dati del poll. Buffer circolare `_speedBuf` da 60 campioni (~2 min). Disegna due linee (DL verde, UL giallo) con area fill semi-trasparente, griglia orizzontale e label velocità corrente. Ordinamento colonne tab Peers per click su intestazione (IP, Client, DL, UL, %).
UI: tab **Peers** nel modal dettagli torrent, caricata on-demand al click (non nel poll automatico dei 2s).

**Spostamenti UI (Manutenzione)**

La sezione **Porte di Rete** (web_port / engine_port) è stata spostata dalla tab Avanzate alla vista **Manutenzione**, insieme al pulsante **Riavvia Servizio**. Generata dinamicamente da `renderNetworkPorts(settings)` chiamata da `loadConfigForMaintenance()` al caricamento della vista.

**Note API libtorrent importanti**

- `progress` è **0-100** (non 0.0-1.0)
- `state` può avere suffisso `_t`: `seeding_t`, `finished_t` — usare `in` o `'seeding' in state` per confronti robusti
- Torrent completato e messo in pausa dopo riavvio: `progress=0`, rilevare con `downloaded >= total_size`

**4. core/cleaner.py --- Pulizia Duplicati ★ NUOVO**

Nuovo modulo che gestisce lo spostamento in trash dei file video
obsoleti quando viene scaricata una versione di qualità superiore. Opera
SOLO su filesystem locale.

**Funzioni Pubbliche**

  -----------------------------------------------------------------------------------
  **Funzione**                  **Input chiave**       **Effetto / Return**
  ----------------------------- ---------------------- ------------------------------
  cleanup_old_episode(\...)     series_name, season,   int --- Cerca in archive_path
                                episode, new_score,    file dello stesso episodio con
                                new_title,             score \< new_score e li sposta
                                archive_path,          in trash. Esclude sia il file
                                trash_path,            col nome originale del torrent
                                min_score_diff=0,      (new_title) sia il file dopo
                                new_fname=\"\"         rename (new_fname).

  cleanup_old_movie(\...)       movie_name,            int --- Come
                                movie_year, new_score, cleanup_old_episode ma per
                                new_title,             film. Tolleranza ±1 anno.
                                archive_path,          
                                trash_path,            
                                min_score_diff=0       

  discard_if_inferior(\...)     series_name, season,   bool --- Caso simmetrico: se
                                episode, new_score,    esiste già un file con score
                                new_fname, save_path,  MAGGIORE, sposta new_fname in
                                trash_path,            trash. True se scartato.
                                min_score_diff=0       

  \_move_to_trash(src,          str, str, str          bool --- Sposta src in
  trash_path, reason)                                  trash_path. Gestisce
                                                       collisioni aggiungendo
                                                       timestamp. Crea trash_path se
                                                       non esiste.

  \_collect_video_files(base)   str                    list\[tuple\] --- Raccoglie
                                                       file video (mkv/mp4/avi/\...)
                                                       sotto base (max depth 3).
                                                       Ritorna (dirpath, filename).

  \_is_local_path(path)         str                    bool --- True solo se il path
                                                       è locale. Rifiuta http://,
                                                       ftp://, smb://, nfs://.
  -----------------------------------------------------------------------------------

**Scenari Gestiti**

  -----------------------------------------------------------------------------------
  **Scenario**   **Situazione**         **Azione**                **Risultato**
  -------------- ---------------------- ------------------------- -------------------
  A              Arriva 720p, sul NAS   discard_if_inferior()     4K intatto ✅
                 c\'è già il 4K         trova il 4K esistente →   
                                        sposta il 720p in trash   

  B              Arriva 4K, sul NAS ci  cleanup_old_episode()     4K resta ✅
                 sono 720p e 1080p      trova 720p e 1080p con    
                                        score \< 4K → li sposta   
                                        in trash                  

  C              Arriva 1080p, c\'è già Condizione best_score \>  Nessuna azione ✅
                 un 1080p con score     new_score +               
                 identico               min_score_diff non        
                                        soddisfatta               
  -----------------------------------------------------------------------------------

**Configurazione (extto.conf)**

  -------------------------------------------------------------------------------------
  **Parametro**              **Default**   **Descrizione**
  -------------------------- ------------- --------------------------------------------
  \@cleanup_upgrades         no            yes/no --- Abilita la pulizia automatica dei
                                           duplicati dopo upgrade.

  \@trash_path               ---           Percorso assoluto della cartella trash. Es:
                                           /mnt/nas/trash. Creata automaticamente se
                                           non esiste.

  \@cleanup_min_score_diff   0             Differenza minima di score per considerare
                                          un file obsoleto. 0 = qualsiasi upgrade. 500
                                          = solo salti significativi (es. 720p→4K,
                                          ignora variazioni h264→h265 stesso livello).

  \@cleanup_action           move          move/delete --- move sposta in trash_path,
                                          delete elimina fisicamente il duplicato. ★
                                          NUOVO

  \@trash_retention_days     (vuoto)       Numero di giorni dopo cui i file nel cestino
                                          vengono eliminati automaticamente ad ogni ciclo
                                          del motore. Vuoto = mai cancellare. ★ NUOVO
  -------------------------------------------------------------------------------------

**Dove viene chiamato**

  ---------------------------------------------------------------------------------------------
  **Caller**                           **Funzione chiamata**     **Momento**
  ------------------------------------ ------------------------- ------------------------------
  renamer.rename_completed_torrent()   discard_if_inferior() +   Dopo move_storage di
                                       cleanup_old_episode()     libtorrent: prima verifica se
                                                                 il nuovo è inferiore (Scenario
                                                                 A), poi rimuove gli inferiori
                                                                 (Scenario B). Gestisce
                                                                 automaticamente i conflitti di
                                                                 nome esistente spostando il
                                                                 peggiore in trash. ★
                                                                 AGGIORNATO

  database.check_series()              cleanup_old_episode()     Nel branch \"Upgrade\"
                                                                 (new_score \> existing_score):
                                                                 subito dopo UPDATE episodes.

  database.check_movie()               cleanup_old_movie()       Nel branch \"Upgrade\" di
                                                                 check_movie. Stesso pattern.
  ---------------------------------------------------------------------------------------------

  -----------------------------------------------------------------------
  ⚠️ cleanup_old_episode() va chiamata con new_fname (nome dopo rename)
  oltre a new_title (nome torrent). Senza new_fname, il file appena
  rinominato potrebbe essere spostato in trash da solo.

  -----------------------------------------------------------------------

**5. Trigger Dominio-Specifici e API run_now ★ NUOVO**

**5.1 Trigger File**

  --------------------------------------------------------------------------------
  **File**                **Dominio**   **Cosa attiva**
  ----------------------- ------------- ------------------------------------------
  /tmp/extto_run_now      all           Ciclo completo: scraping RSS + serie +
                                        film + fumetti + gap filling + TMDB +
                                        backup. Imposta anche
                                        run_series/movies/comics_triggered=True.

  /tmp/extto_run_series   series        Cerca nuovi episodi per tutte le serie
                                        abilitate, riempie i gap, aggiorna TMDB.
                                        Non tocca film né fumetti.

  /tmp/extto_run_movies   movies        Cerca i film configurati non ancora
                                        scaricati o upgradabili. Non tocca serie
                                        né fumetti.

  /tmp/extto_run_comics   comics        Forza il ciclo fumetti (getcomics.org)
                                        ignorando il timer settimanale.
  --------------------------------------------------------------------------------

  -----------------------------------------------------------------------
  💡 Il loop di attesa controlla ogni iterazione tutti e quattro i
  trigger file. I trigger vengono rimossi alla FINE del ciclo, quindi un
  trigger creato durante l\'esecuzione farà partire immediatamente il
  successivo.

  -----------------------------------------------------------------------

**5.2 API HTTP --- /api/run_now**

  --------------------------------------------------------------------------
  **Endpoint**                  **Metodo**   **Effetto**
  ----------------------------- ------------ -------------------------------
  /api/run_now?domain=all       GET/POST     Crea /tmp/extto_run_now → ciclo
                                             completo

  /api/run_now?domain=series    GET/POST     Crea /tmp/extto_run_series →
                                             solo serie TV

  /api/run_now?domain=movies    GET/POST     Crea /tmp/extto_run_movies →
                                             solo film

  /api/http-downloads/          POST         Rimuove da ACTIVE_HTTP_DOWNLOADS le
  remove-completed                            voci con stato Terminato/Errore/Salvato.
                                             Usato dal pulsante Pulisci. ★ NUOVO

  /api/run_now?domain=comics    GET/POST     Crea /tmp/extto_run_comics →
                                             solo fumetti
  --------------------------------------------------------------------------

*Risposta JSON: { \"ok\": true, \"domain\": \"comics\", \"trigger\":
\"/tmp/extto_run_comics\" }*

**5.3 API HTTP --- /api/log_level ★ NUOVO**

  ----------------------------------------------------------------------------
  **Endpoint**       **Metodo**   **Body /          **Effetto**
                                  Parametri**       
  ------------------ ------------ ----------------- --------------------------
  /api/log_level     GET          ---               Ritorna {\"level\":
                                                    \"info\"} --- livello
                                                    corrente

  /api/log_level     POST         {\"level\":       Applica immediatamente il
                                  \"debug\"}        livello e salva in
                                                    extto.conf come
                                                    \@log_level
  ----------------------------------------------------------------------------

**5.4 Pulsanti Ricontrolla nell\'UI (index.html)**

  ---------------------------------------------------------------------------------
  **Dove**        **Label**     **Chiama**               **Tooltip**
  --------------- ------------- ------------------------ --------------------------
  Dashboard       Ricontrolla   app.runNow(\'all\')      Ciclo completo: serie,
  (nuova barra)   Tutto                                  film, fumetti, gap
                                                         filling, TMDB. Equivale ad
                                                         aspettare il prossimo
                                                         ciclo ma subito.

  Dashboard       Ricontrolla   app.runNow(\'series\')   Cerca nuovi episodi per
                  Serie                                  tutte le serie TV
                                                         abilitate e riempie i gap.
                                                         Non tocca film né fumetti.

  Dashboard       Ricontrolla   app.runNow(\'movies\')   Cerca i film in lista non
                  Film                                   ancora scaricati. Non
                                                         tocca serie né fumetti.

  Dashboard       Ricontrolla   app.runNow(\'comics\')   Controlla getcomics.org
                  Fumetti                                ignorando il timer
                                                         settimanale. Non tocca
                                                         serie né film.

  View Serie TV   Ricontrolla   app.runNow(\'series\')   Identico al pulsante
  (header)        Serie                                  Dashboard Serie
                                                         (sostituisce
                                                         restartScrape()).

  View Film       Ricontrolla   app.runNow(\'movies\')   Nuovo --- prima non era
  (header)        Film                                   presente.

  View Fumetti /  Ricontrolla   app.runNow(\'comics\')   Sostituisce il precedente
  Monitorati      Fumetti                                \"Aggiorna Ora\" che
                                                         chiamava comicsRunCycle().
  ---------------------------------------------------------------------------------

**6. Log di Sistema ★ NUOVO**

Il livello di logging è ora configurabile a runtime dall\'interfaccia
web, senza riavvio del servizio.

**6.1 Livelli Disponibili**

  ----------------------------------------------------------------------------
  **Livello**   **Cosa appare nel log**                 **Uso consigliato**
  ------------- --------------------------------------- ----------------------
  DEBUG         Tutto: lookup TMDB, scansioni           Diagnosi problemi
                filesystem, ricerche \"Cerco:\",        specifici
                dettagli ARCHIVE-CHECK                  

  INFO          Risultati scraping (1 riga per URL),    Uso normale (default)
                match trovati in archivio, warning,     
                errori                                  

  WARNING       Solo avvisi (path non esistente, serie  Log minimale per
                non trovata, errori HTTP) e errori      produzione

  ERROR         Solo errori critici                     Monitoraggio
                                                        silenzioso
  ----------------------------------------------------------------------------

**6.2 Come si imposta**

- UI: Configurazione → Avanzate → \"Log di Sistema\" → select con 4
  livelli. Applicato immediatamente via POST /api/log_level.

- extto.conf: \@log_level = debug/info/warning/error. Letto all\'avvio
  da extto3.py.

- API diretta: POST /api/log_level con body {\"level\": \"debug\"}

**6.3 Moduli Aggiornati**

  ------------------------------------------------------------------------
  **Modulo**    **Cosa era INFO**             **Ora**
  ------------- ----------------------------- ----------------------------
  tmdb.py       \"TMDB: \'serie\' →           DEBUG --- sparisce con INFO
                id=12345\" per ogni serie ad  
                ogni ciclo                    

  tmdb.py       \"TMDB id=X: {1:13, 2:10}\"   DEBUG
                conteggio stagioni            

  tmdb.py       \"🌐 TMDB: aggiorno metadati  DEBUG
                per \'X\'\"                   

  engine.py     \"🔎 \[N/M\]: url\...\" riga  DEBUG
                inizio scraping               

  engine.py     \" ↳ N items\" riga risultato Unificata in una riga: \"🔎
                separata                      ExtTo \[1/3\]: 150 items ---
                                              url\...\"

  engine.py     \" ↳ \[Jackett\] Cerco:       DEBUG
                \'query\'\"                   

  engine.py     \"🔎 Interrogo indexer:       DEBUG
                label\...\"                   

  database.py   \"\[ARCHIVE-CHECK\] Scansione DEBUG
                filesystem: path\"            

  database.py   \"\[ARCHIVE-CHECK\] Match     DEBUG se 0, INFO ✅ se \>0
                trovati: 0, best_score=None\" 

  database.py   \"\[ARCHIVE-CHECK\]           DEBUG
                Serie=\'X\' S01E01            
                path=\...\"                   

  database.py   \"\[ARCHIVE-CHECK\] Path non  WARNING ⚠️ (sempre visibile)
                esistente: path\"             
  ------------------------------------------------------------------------

**7. Modulo Fumetti (core/comics.py)**

  -------------------------------------------------------------------------------------------
  **Classe/Funzione**                           **Responsabilità**
  --------------------------------------------- ---------------------------------------------
  ComicsDB                                      SQLite wrapper per la tabella comics (titoli
                                                monitorati, pending, weekly)

  GetComicsScraper                              Scraper per getcomics.org

  GetComicsScraper.get_links(post_url)          Estrae magnet/DDL/Mega da un post.
                                                Rileva Mega dal testo del bottone (non href)
                                                perché GetComics usa redirect /dlds/ per
                                                tutti i link. ★ AGGIORNATO

  GetComicsScraper.get_weekly_links(date_str)   Cerca il weekly pack con strategia 3 livelli:
                                                URL diretto → URL alternativo → ricerca
                                                testuale. ★ AGGIORNATO

  weekly_already_found(date_str)                Ritorna True solo se già inviato (sent_at NOT
                                                NULL) o in pending con link. Se in DB senza
                                                link, ritorna False per ritentare. ★

  download_comic_mega_bg(url, dir, title)       Scarica da Mega via megatools (megadl).
                                                Risolve redirect JS di GetComics, traccia
                                                progresso in ACTIVE_HTTP_DOWNLOADS. ★ NUOVO

  download_comic_file_bg(url, dir, title)       Scarica via HTTP diretto (DDL). Traccia
                                                progresso in ACTIVE_HTTP_DOWNLOADS.

  ACTIVE_HTTP_DOWNLOADS                         Dict globale {dl_id: {...}} con stato di
                                                tutti i download HTTP/Mega in corso o
                                                completati. Esposto dal proxy Flask in
                                                /api/torrents insieme ai torrent libtorrent.

  run_comics_cycle(send_magnet_fn)              Entry point: recupero pending → nuovi weekly
                                                → fumetti monitorati. Parte sempre all\'avvio
                                                (primo ciclo immediato). ★

  -------------------------------------------------------------------------------------------

  -----------------------------------------------------------------------
  💡 Il ciclo fumetti parte all\'avvio senza aspettare il timer
  settimanale (_first_cycle=True in extto3.py). La ricerca weekly pack
  usa 3 livelli: URL diretto, URL alternativo, ricerca testuale come
  fallback.

  💡 GetComics usa redirect JS (/dlds/...) per TUTTI i bottoni download
  (Mega, DDL, etc.). Il link reale non è nell'href ma viene risolto
  scaricando la pagina e cercando meta refresh / script location / <a>.

  💡 ACTIVE_HTTP_DOWNLOADS è il dict globale per i download HTTP/Mega.
  Il Flask proxy (/api/torrents) lo unisce ai torrent libtorrent così
  il Torrent Manager mostra tutto in una sola tabella.

  -----------------------------------------------------------------------

**8. Mappa delle Dipendenze**

**8.1 Chi chiama chi --- Flusso Principale**

  ------------------------------------------------------------------------------------------------------
  **Caller**                           **Chiama**     **Metodo/Funzione**              **Motivo**
  ------------------------------------ -------------- -------------------------------- -----------------
  extto3.main()                        Config         Config()                         Rilegge
                                                                                       configurazione
                                                                                       ogni ciclo

  extto3.main()                        Engine         scrape_all(cfg.urls)             Scraping RSS

  extto3.main()                        Engine         search_archive_for_config(cfg)   Ricerca in
                                                                                       archivio

  extto3.main()                        Parser         parse_series_episode(title)      Classificazione
                                                                                       candidati

  extto3.main()                        Config         find_series_match(name, season)  Match con serie
                                                                                       configurate

  extto3.main()                        Database       check_series(ep, magnet, qual)   Verifica e
                                                                                       inserimento
                                                                                       episodio

  extto3.main()                        client         add(magnet, cfg)                 Invio download

  extto3.main()                        Tagger         tag_torrent(magnet, tag)         Tagging
                                                                                       qBittorrent

  extto3.main()                        Notifier       notify_download(\...)            Notifica
                                                                                       Telegram/Email

  extto3.main()                        Database       find_gaps(sid, season)           Gap filling:
                                                                                       trova mancanti

  extto3.main()                        TMDBClient     update_series_metadata(db, sid,  Aggiorna cache
                                                      name)                            metadati

  extto3.main()                        Database       save_cycle_history(payload)      Log storico cicli

  extto3.main()                        CycleStats     stats.report(cfg)                Report log finale

  Engine.scrape_all()                  ArchiveDB      save_batch(items)                Salva torrent
                                                                                       nell\'archivio

  Engine.\_extto()                     SmartCache     get/set(url)                     Evita ri-scrape
                                                                                       pagine recenti

  Database.check_series()              Database       \_best_quality_in_path()         Controlla
                                                                                       archivio su disco

  Database.check_series()              cleaner        cleanup_old_episode()            Pulizia duplicati
                                                                                       dopo upgrade ★

  Database.check_movie()               cleaner        cleanup_old_movie()              Pulizia duplicati
                                                                                       film dopo upgrade
                                                                                       ★

  TMDBClient                           Database       is_tvdb_cache_fresh()            Verifica cache
                                                                                       TMDB

  TMDBClient                           Database       upsert_series_metadata()         Salva metadati
                                                                                       stagioni

  renamer.rename_completed_torrent()   Parser         parse_series_episode(fname)      Parsing nome file

  renamer.rename_completed_torrent()   TMDBClient     fetch_episode_title()            Titolo episodio
                                                                                       da TMDB

  renamer.rename_completed_torrent()   cleaner        discard_if_inferior() +          Pulizia duplicati
                                                      cleanup_old_episode()            post-rename ★

  LibtorrentClient (alert)             renamer        rename_completed_torrent()       Post-processing
                                                                                       automatico

  LibtorrentClient (alert)             Notifier       notify_torrent_complete()        Notifica
                                                                                       completamento

  extto_web.py                         constants      set_log_level() /                Endpoint
                                                      get_log_level()                  /api/log_level ★
  ------------------------------------------------------------------------------------------------------

**8.2 Dipendenze Esterne**

  -------------------------------------------------------------------------------------
  **Libreria**     **Usata in**                   **Scopo**
  ---------------- ------------------------------ -------------------------------------
  requests         Engine, TMDBClient             HTTP scraping e chiamate API

  beautifulsoup4   Engine, GetComicsScraper       Parsing HTML pagine torrent

  pymediainfo      mediainfo_helper               Analisi tecnica file video
                                                  (opzionale)

  libtorrent       core/clients/\_\_init\_\_.py   Client torrent embedded (opzionale)

  sqlite3          Database, ArchiveDB, ComicsDB  Persistenza dati (stdlib)

  Flask/Waitress   extto_web.py                   Web UI e API HTTP
  -------------------------------------------------------------------------------------

**9. Glossario Termini Interni**

  -------------------------------------------------------------------------------
  **Termine**                **Significato**
  -------------------------- ----------------------------------------------------
  episode_range              Lista di interi degli episodi contenuti in un season
                             pack (es. \[1,2,3,4,5\] per S02E01-05).

  is_pack                    Flag True quando un torrent contiene più episodi
                             (range o season pack intero, episode=0).

  best-in-cycle              Algoritmo che confronta tutti i candidati dello
                             stesso episodio nel ciclo e scarica solo il
                             migliore.

  gap filling                Processo che identifica episodi attesi ma non
                             scaricati (gap) e li cerca nell\'archivio/Jackett.

  quality_score / score()    Intero composito: (rank_risoluzione × 1000) +
                             source_bonus + codec_bonus + flags. Più alto =
                             meglio.

  pending_downloads          Download in attesa del timeframe configurato.
                             Scaricati quando ready_at ≤ ora corrente.

  tvdb_id (nel DB)           Colonna riutilizzata per salvare il tmdb_id (eredità
                             storica da quando si usava TVDB).

  series_metadata            Cache TMDB: {stagione: n_episodi_attesi}. Aggiornata
                             ogni \@tmdb_cache_days giorni.

  archive_path               Percorso locale dove risiedono già gli episodi
                             scaricati in precedenza. Usato da
                             \_best_quality_in_path().

  run_now_triggered          Flag True quando esiste /tmp/extto_run_now. Bypassa
                             filtri età e limiti pagine.

  run_series_triggered       Flag True quando esiste /tmp/extto_run_series.
                             Limita le operazioni alle sole serie TV. ★

  run_movies_triggered       Flag True quando esiste /tmp/extto_run_movies.
                             Limita le operazioni ai soli film. ★

  run_comics_triggered       Flag True quando esiste /tmp/extto_run_comics. Forza
                             il ciclo fumetti ignorando il timer. ★

  TRIGGER_FILE               /tmp/extto_run_now --- la sua presenza forza un
                             ciclo immediato senza limiti.

  TRIGGER_DOMAIN_MAP         Dict in extto_web.py: mappa dominio → path trigger
                             file. ★

  ArchiveDB                  Database separato di tutti i torrent MAI visti.
                             Funziona da \"memoria lunga\" per il gap filling.

  SmartCache                 Cache JSON in memoria per evitare di ri-scrapare URL
                             già visitati di recente.

  episode_archive_presence   Tabella DB che traccia episodi trovati su disco per
                             evitare download ridondanti.

  normalize_series_name()    Funzione che normalizza unicode, rimuove articoli,
                             gestisce possessivi per confronto robusto.

  TAG_SERIES / TAG_FILM      Tag qBittorrent \"Serie TV\" / \"Film\" applicati
                             automaticamente da Tagger.

  cleanup_upgrades           \@cleanup_upgrades in extto.conf. Se \"yes\", attiva
                             la pulizia automatica dei duplicati. ★

  trash_path                 \@trash_path: cartella dove vengono spostati i file
                             video obsoleti. Mai cancellati definitivamente. ★

  cleanup_min_score_diff     Soglia minima differenza score per attivare la
                             pulizia. 0 = qualsiasi, 500 = solo salti
                             significativi. ★

  trash_retention_days       Giorni di retention del cestino. File più vecchi
                             di N giorni vengono eliminati automaticamente
                             ad ogni ciclo extto3.py. Vuoto = mai. ★ NUOVO

  discard_if_inferior()      cleaner.py: sposta in trash il file NUOVO se ne
                             esiste già uno migliore. ★

  cleanup_old_episode()      cleaner.py: sposta in trash i file VECCHI inferiori
                             al nuovo. ★

  new_fname                  Nome del file dopo il rename (diverso dal nome
                             torrent originale). Escluso dal match per non
                             cancellare il file stesso. ★

  log_level                  \@log_level in extto.conf. Livello di logging:
                             debug/info/warning/error. Configurabile da UI. ★

  set_log_level()            constants.py: imposta il livello a runtime su
                             logger + tutti gli handler. ★

  /api/log_level             Endpoint API HTTP: GET → livello corrente, POST →
                             imposta e salva. ★

  /api/run_now?domain=X      Endpoint API HTTP: crea il trigger file
                             corrispondente al dominio X. ★

  app.runNow(domain)         Funzione JS nell\'UI: chiama /api/run_now e mostra
                             feedback via toast. ★

  app.setLogLevel(level)     Funzione JS nell\'UI: chiama /api/log_level e
                             applica immediatamente il livello. ★

  libtorrent_active_         ★ v31. Numero massimo di torrent in
  downloads/seeds/limit      download/seeding/totale attivi. Torrent lenti
                             (sotto soglia) non contano nel limite.

  libtorrent_slow_dl/ul_     ★ v31. Soglia in KB/s sotto la quale un
  threshold                  torrent è considerato "lento" e non occupa uno
                             slot attivo nella coda.

  libtorrent_preallocate     ★ v31. Riserva spazio su disco prima di
                             iniziare il download.

  libtorrent_incomplete_ext  ★ v31. Aggiunge .!extto ai file in download.
                             Rimossa automaticamente al completamento.

  is_pack (v31)              True per qualsiasi pack: season completo
                             (episode=0) O parziale (S01E01-05, episode>0).
                             Attiva sempre _handle_season_pack().
  -------------------------------------------------------------------------------

**10. Note per l\'Utilizzo da parte di AI**

  -----------------------------------------------------------------------
  📌 Leggere questa sezione prima di modificare qualsiasi file del
  progetto.

  -----------------------------------------------------------------------

**Convenzioni di Base**

- ep dict: qualsiasi dict ritornato da Parser.parse_series_episode() ---
  ha sempre: type, name, season, episode, quality, title. Se
  is_pack=True ha anche episode_range (list).

- check_series(ep, magnet, qual) → (bool, str): True = download
  approvato e registrato nel DB. Non chiamare client.add() senza aver
  chiamato check_series() prima.

- Il Database usa row_factory=sqlite3.Row: accesso per nome colonna
  (row\[\'name\'\]).

- Config si rilegge ogni ciclo: nessun riavvio necessario per modifiche
  a extto.conf.

- Lo stesso magnet può essere nel DB episodes (scaricato) e in ArchiveDB
  (visto). Sono DB separati.

- TMDBClient salva tmdb_id nella colonna tvdb_id per compatibilità
  storica.

- normalize_series_name() è la funzione critica per il matching:
  confrontare sempre con essa.

- stats è un singleton globale: reset() all\'inizio di ogni ciclo,
  report() alla fine. Per registrare errori usare sempre
  stats.add_error("categoria") — mai stats.errors += 1 direttamente.
  Il report stampa il dettaglio per categoria es. "Errori: 4 (2 Jackett timeout, 2 scraping)".

**Integrazioni Critiche ★ NUOVO**

- cleanup_old_episode() va chiamata con new_fname (nome dopo rename)
  oltre a new_title (nome torrent). Senza new_fname, il file appena
  rinominato potrebbe essere spostato in trash da solo.

- discard_if_inferior() confronta il new_score del nuovo con il MIGLIORE
  file esistente. Se esiste già un file con score maggiore, il nuovo
  (new_fname) va in trash.

- renamer.rename_completed_torrent() chiama PRIMA discard_if_inferior
  (Scenario A), poi cleanup_old_episode (Scenario B). L\'ordine è
  importante.

- I trigger dominio-specifici NON sostituiscono run_now_triggered:
  run_now forza ancora tutto e imposta anche
  run_series/movies/comics_triggered=True.

- Il ciclo fumetti viene eseguito se \_comics_due OR
  run_comics_triggered. run_comics_triggered non aggiorna
  last_comics_check, quindi il timer settimanale non viene \"consumato\"
  dal trigger manuale.

- set_log_level() agisce sul logger globale importato da constants.py.
  Tutti i moduli usano lo stesso oggetto logger, quindi il cambio è
  immediato e globale.

- Se \@log_level non è presente in extto.conf, il sistema usa INFO come
  default. Non è necessario configurarlo esplicitamente.

**Note Web UI (v40) ★**

- **Pulsante Pulisci:** rimuove sia i torrent libtorrent completati (via `/api/torrents/remove_completed`) che i download HTTP/Mega terminati/in errore (via `/api/http-downloads/remove-completed`). NON cancella file su disco.

- **Download Mega (comics):** usa `megatools` (`apt install megatools`). `ACTIVE_HTTP_DOWNLOADS` traccia il progresso. Log ogni 5% invece che ogni riga.

- **Bottone download comics (adattivo):** solo DDL → verde "⬇ Scarica"; solo Mega → rosso "M Mega"; entrambi → viola "⬇ Scarica ▾" con dropdown body-level (fixed positioning). Nessun tipo → disabilitato.

- **Rimozione bulk torrent (split button):** "🗑 Rimuovi" (mantieni file) + "▾" dropdown con opzione "Rimuovi ed Elimina" (cancella anche i file).

**Note Web UI (v31) ★**

- **Ordine navigazione:** Dashboard → Torrent → Serie TV → Film → Esplora → Fumetti → Archivio → Config → Grafici → Manutenzione → Log → Manuale

- **Toolbar torrent mobile:** `.search-input` ha `width:100%` globale — non usarla per input con larghezza fissa. Usare `form-input` + classe specifica. La regola ID `#quick-dl-limit, #quick-ul-limit` in `@media (max-width:992px)` sovrascrive le classi — deve stare a `5.5rem`.

- **ETA verde (app.js):** `isDone = pct>=100 OR state in (seeding, seeding_t, finished, finished_t) OR (paused AND downloaded>=total_size)`. Il check `downloaded>=total_size` copre il caso di torrent completati rimessi in pausa dopo riavvio servizio (dove `progress` torna 0).

- **Sort default torrent:** `_torrentSortCol: 'name'` — alfabetico.

- **Config libtorrent (index.html):** Sezioni aggiunte: "Coda Torrent Attivi" (active_downloads/seeds/limit), "Torrent Lenti" (slow_dl/ul_threshold), "File" (preallocate + incomplete_ext).

**Note Bug Fix (v38) ★**

- **Consumo Dashboard sempre 0:** `extto_web.py` ha una classe DB locale
  che NON ha `get_consumption_stats()`. La chiamata cadeva silenziosamente
  nel except. Fix: l'endpoint `/api/stats` ora apre una connessione
  temporanea a `core.database.Database` (che ce l'ha) per leggere il
  consumo. ★ CRITICO

- **size_bytes mai scritto:** La colonna esiste in `episodes` e `movies`
  ma non veniva mai popolata al download. Fix in tre punti: (1)
  `renamer.py` chiama `db.update_size_bytes()` dopo ogni `os.rename()`
  riuscito; (2) `database.py` ha il nuovo metodo `update_size_bytes()`;
  (3) il meccanismo in `extto3.py` (righe ~378, ~413, ~1463) aggiorna via
  `total_size` libtorrent al "Pulisci" o rimozione torrent — era già
  presente ma dipendeva dal consumo Dashboard non funzionante.

- **Rinomina episodi doppia analisi:** quando l'utente premeva OK al
  confirm "riprocessa tutti", `app.js` faceva una seconda chiamata a
  `rename-preview?force=1` inutile. Fix: i dati `already_ok` già
  disponibili vengono fusi in `data.preview` lato client senza nuova
  chiamata al server.

- **Pulsante "Esegui Rinomina" sempre attivo:** il pulsante nel modal
  era hardcoded senza `disabled`. Fix: parte disabilitato in `index.html`
  e viene abilitato solo quando la preview è pronta. Nuova funzione
  `closeRenameModal()` in `app.js` che resetta il pulsante alla chiusura.

**Tabella movies — nota architetturale ★**

- La tabella `movies` NON ha la colonna `archive_path` (a differenza di
  `episodes`). I film vengono aggiornati con `size_bytes` solo tramite
  `magnet_hash` al momento della rimozione del torrent dalla UI.
  Non è possibile fare backfill via disco per i film.

**Note Architetturali (v40) ★ — Migrazione Config a DB**

- **Nuovo extto_config.db:** separato da extto_series.db. Contiene
  tabella `settings` (chiave/valore, liste come JSON) e `movies_config`.
  Gestito da `core/config_db.py` con lock threading e WAL mode.

- **Migrazione automatica:** al primo avvio con file presenti e DB vuoto,
  `Config._load()` chiama `config_db.migrate_from_files()` che importa
  extto.conf + series.txt + movies.txt nel DB. La migrazione è idempotente.

- **Tabella series estesa:** aggiunte colonne seasons, language, enabled,
  archive_path, timeframe, ignored_seasons, tmdb_id, subtitle. Il
  `sync_configs()` fa ora un vero upsert di tutti i campi.

- **Config._load() invariata nell'interfaccia pubblica:** `cfg.series`,
  `cfg.movies`, `cfg.qbt`, `cfg.urls` ecc. funzionano esattamente come
  prima. Zero impatto su extto3.py.

- **parse/save_series_config in extto_web.py:** riscritte per leggere/
  scrivere sul DB. L'interfaccia dict {settings, series} è invariata.
  Tutto il codice che le chiama (80+ occorrenze) continua a funzionare.

- **Wizard migrazione UI:** banner nella pagina Configurazione con
  pulsanti "Importa nel DB" e "Rinomina in .old". Tre endpoint:
  GET /api/config/migration-status, POST /api/config/migrate,
  POST /api/config/rename-old.

- **Backup:** extto_config.db è in BASE_DIR → viene già incluso nel ZIP
  automaticamente. Nessuna modifica al sistema di backup necessaria.

- **File originali:** extto.conf/series.txt/movies.txt vengono letti
  solo durante la migrazione, poi diventano inutili. L'utente li rinomina
  in .old dalla UI quando vuole.

**Note Bug Fix (v39) ★**

- **Ricalcolo score non aggiornava episode_archive_presence:** dopo il
  ricalcolo, check_series vedeva new_score > old_score e rimandava in
  download episodi già sul NAS. Fix: rescore_episodes aggiorna anche
  episode_archive_presence in sincronia.

- **Ricalcolo usava ExtToDB invece di core.Database:** ExtToDB è la
  classe locale di extto_web.py e non ha _best_quality_in_path. La
  chiamata lanciava AttributeError silenziato dal except. Fix: istanzia
  core.database.Database una volta sola prima del loop.

- **Ricalcolo non trovava archive_path:** la colonna episodes.archive_path
  è quasi sempre vuota (popolata solo al download). Fix: costruisce una
  mappa series_name→archive_path leggendo series.txt via
  parse_series_config() prima del loop.

- **parse_quality non riconosceva titoli rinominati da extto:** [DV HDR10]
  non matchava perché re.sub(r'[._-]') lasciava le parentesi quadre.
  [h265] non matchava. [IT] non matchava (cercava solo ita). Fix in
  models.py: usa t_norm_lang (rimuove anche []) per DV, h265 e lingua.
  Aggiunto it con lista esclusioni per evitare falsi positivi inglesi.

- **Sottotitolo episodio non mostrava nome file:** la logica mostrava
  ep.title (titolo corto "The Pitt S02E01") invece del nome file fisico.
  Fix in app.js: subTitle = ep.file_name sempre se disponibile.

- **UI dettagli serie troppo stretta:** max-width da 1200px a 1600px in
  tutti i contenitori della pagina. Aggiunta colonna Aggiunto (downloaded_at)
  tra Dim. e Azioni.

**Quando caricare i file in una nuova sessione AI**

*Per sessioni di modifica, caricare sempre il file di produzione
corrente come base. I file output generati dall\'AI nelle sessioni
precedenti potrebbero mancare di modifiche integrate manualmente. File
minimi da caricare per task comuni:*

  -----------------------------------------------------------------------
  **Task**                  **File da caricare**
  ------------------------- ---------------------------------------------
  Modifiche al ciclo        extto3.py (produzione)
  principale                

  Modifiche all\'UI web     index.html + extto_web.py (produzione)

  Modifiche al log/costanti constants.py (produzione)

  Modifiche al              database.py + config.py (produzione)
  download/qualità          

  Modifiche al              models.py (produzione)
  matching/parsing/score    

  Modifiche al ricalcolo    extto_web.py (produzione)
  score / UI dettagli serie 
  / colonne episodi         

  Modifiche alla config /   core/config_db.py + core/config.py +
  migrazione file→DB        core/database.py + extto_web.py (produzione)

  Modifiche al fumetti      comics.py (produzione)

  Modifiche allo scraping   engine.py (produzione)

  Modifiche alla            renamer.py + cleaner.py (produzione)
  rinomina/pulizia          

  Modifiche al tagging /    tagger.py + utils.py (produzione)
  I/O JSON thread-safe      

  Modifiche alla UI         index.html + app.js (produzione)
  rinomina episodi          

  Modifiche al consumo /    extto_web.py + database.py (produzione)
  statistiche dashboard     
  -----------------------------------------------------------------------

**Devi trattarmi come un non programmatore. dammi sempre file completi e parla semplice**
**Assicurati ogni volta di restituire file con tutte le opzioni/comandi, senza perdita di funzionalita'**
