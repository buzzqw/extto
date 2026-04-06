"""
EXTTO - Configurazione.

Legge da extto_config.db (via core/config_db.py).
Migrazione automatica da extto.conf/series.txt/movies.txt al primo avvio.
I file originali vengono mantenuti finché l'utente non li rinomina in .old
dalla pagina Configurazione della web UI.
"""

import json
import os
import re
from typing import List, Dict, Optional
from .constants import CONFIG_FILE, SERIES_FILE, MOVIES_FILE, ARCHIVE_CREDENTIALS, logger
from .models import Parser, Quality
from . import config_db as _cdb


class Config:
    def __init__(self):
        self.urls    = []
        self.series  = []
        self.movies  = []
        self.qbt     = {}

        # Notifications
        self.notify_telegram      = False
        self.telegram_bot_token   = ''
        self.telegram_chat_id     = ''
        self.notify_email         = False
        self.email_smtp           = 'smtp.gmail.com:587'
        self.email_from           = ''
        self.email_to             = ''
        self.email_password       = ''

        # Gap Filling
        self.jackett_url              = ''
        self.jackett_api              = ''
        self.prowlarr_url             = ''
        self.prowlarr_api             = ''
        self.jackett_save_to_archive  = True   # se False, i risultati Jackett non vengono scritti in archive.db
        self.prowlarr_save_to_archive = True   # se False, i risultati Prowlarr non vengono scritti in archive.db
        self.backup_dir               = ''
        self.backup_retention         = 5
        self.backup_schedule          = 'manual'
        self.gap_filling         = True
        self.tmdb_api_key        = ''
        self.tmdb_cache_days     = 7
        self.rename_episodes  = False
        self.rename_format    = 'base'
        self.rename_template  = ''
        # Loop interval
        self.refresh_interval = 7200  # Default 2 ore
        self.custom_scores    = {}  
        
        # Torrent cleanup settings
        self.auto_remove_completed = False

        # Age-based scraping limits
        self.max_age_days                  = 0
        self.stop_on_old_page_threshold    = 0.8

        # Feed rolling
        self.feed_max_items = 1000

        # Archive cleanup
        self.archive_cleanup_enabled = False
        self.archive_max_age_days    = 365
        self.archive_keep_min        = 10000

        # Trash / Cleaner (Nuovo)
        self.cleanup_upgrades        = False
        self.trash_path              = ''
        self.archive_root            = '' # Radice per individuazione automatica serie
        self.cleanup_min_score_diff  = 0
        self.cleanup_action          = 'move'  # move o delete

        # Move episodes / misc
        self.move_episodes           = False
        self.web_port                = 5000
        self.comics_check_interval   = 604800  # 7 giorni
        self.min_free_space_gb       = 10.0

        self._load()

    # ------------------------------------------------------------------
    # INTERNAL
    # ------------------------------------------------------------------

    def _parse_bool(self, val):
        return str(val).lower() in ('yes', 'true', '1')

    def _load(self):
        """Carica la configurazione da extto_config.db.

        Al primo avvio (DB vuoto + file presenti), esegue la migrazione
        automatica da extto.conf / series.txt / movies.txt.
        """
        # Reset liste
        self.urls   = []
        self.series = []
        self.movies = []

        # Reset blacklist/wantedlist del Parser
        from .models import Parser
        Parser.BLACKLIST  = Parser.DEFAULT_BLACKLIST.copy()
        Parser.WANTEDLIST = []

        # ── Migrazione automatica al primo avvio ─────────────────────────────
        if _cdb.needs_migration():
            logger.info("[Config] Migrazione automatica da file a DB...")
            report = _cdb.migrate_from_files()
            logger.info(
                f"[Config] Migrazione completata: "
                f"{report['settings_imported']} impostazioni, "
                f"{report['series_imported']} serie, "
                f"{report['movies_imported']} film"
            )
            if report['errors']:
                for e in report['errors']:
                    logger.warning(f"[Config] Migration: {e}")

        # ── Legge tutte le impostazioni dal DB ───────────────────────────────
        raw = _cdb.get_all_settings()

        # ── URL sorgenti ─────────────────────────────────────────────────────
        urls_raw = raw.get('url', [])
        if isinstance(urls_raw, list):
            self.urls = urls_raw
        elif urls_raw:
            self.urls = [urls_raw]

        # ── Blacklist / Wantedlist ────────────────────────────────────────────
        for item in (raw.get('blacklist', []) if isinstance(raw.get('blacklist'), list) else ([raw['blacklist']] if raw.get('blacklist') else [])):
            if item.lower() not in Parser.BLACKLIST:
                Parser.BLACKLIST.append(item.lower())
        for item in (raw.get('wantedlist', []) if isinstance(raw.get('wantedlist'), list) else ([raw['wantedlist']] if raw.get('wantedlist') else [])):
            if item.lower() not in Parser.WANTEDLIST:
                Parser.WANTEDLIST.append(item.lower())

        # ── Custom scores ─────────────────────────────────────────────────────
        self.custom_scores = {}
        for item in (raw.get('custom_score', []) if isinstance(raw.get('custom_score'), list) else ([raw['custom_score']] if raw.get('custom_score') else [])):
            try:
                parola, punti = str(item).split(':', 1)
                self.custom_scores[parola.strip().lower()] = int(punti.strip())
            except Exception:
                pass

        # ── Archive credentials ───────────────────────────────────────────────
        self.archive_credentials = []
        for item in (raw.get('archive_cred', []) if isinstance(raw.get('archive_cred'), list) else ([raw['archive_cred']] if raw.get('archive_cred') else [])):
            try:
                pref, usr, pwd = [p.strip() for p in str(item).split('|', 2)]
                if pref and usr:
                    self.archive_credentials.append({'prefix': pref, 'user': usr, 'pass': pwd})
            except Exception:
                pass
        try:
            global ARCHIVE_CREDENTIALS
            ARCHIVE_CREDENTIALS[:] = list(self.archive_credentials)
        except Exception:
            pass

        # ── Client torrent (qbt dict) ─────────────────────────────────────────
        self.qbt = {}
        for k, v in raw.items():
            if k.startswith(('qbittorrent_', 'transmission_', 'aria2_', 'libtorrent_', 'amule_')):
                self.qbt[k] = str(v)

        # ── Notifiche ─────────────────────────────────────────────────────────
        self.notify_telegram    = self._parse_bool(raw.get('notify_telegram', 'no'))
        self.telegram_bot_token = str(raw.get('telegram_bot_token', ''))
        self.telegram_chat_id   = str(raw.get('telegram_chat_id', ''))
        self.notify_email       = self._parse_bool(raw.get('notify_email', 'no'))
        self.email_smtp         = str(raw.get('email_smtp', 'smtp.gmail.com:587'))
        self.email_from         = str(raw.get('email_from', ''))
        self.email_to           = str(raw.get('email_to', ''))
        self.email_password     = str(raw.get('email_password', ''))

        # ── Gap filling / Jackett / Prowlarr ──────────────────────────────────
        self.jackett_url              = str(raw.get('jackett_url', ''))
        self.jackett_api              = str(raw.get('jackett_api', ''))
        self.prowlarr_url             = str(raw.get('prowlarr_url', ''))
        self.prowlarr_api             = str(raw.get('prowlarr_api', ''))
        self.jackett_save_to_archive  = self._parse_bool(raw.get('jackett_save_to_archive', 'yes'))
        self.prowlarr_save_to_archive = self._parse_bool(raw.get('prowlarr_save_to_archive', 'yes'))
        self.gap_filling              = self._parse_bool(raw.get('gap_filling', 'yes'))

        # ── TMDB ─────────────────────────────────────────────────────────────
        self.tmdb_api_key  = str(raw.get('tmdb_api_key', ''))
        self.tmdb_language = str(raw.get('tmdb_language', 'it-IT')).strip()
        try:
            self.tmdb_cache_days = int(raw.get('tmdb_cache_days', 7))
        except (ValueError, TypeError):
            self.tmdb_cache_days = 7

        # ── Rinomina ──────────────────────────────────────────────────────────
        self.rename_episodes = self._parse_bool(raw.get('rename_episodes', 'no'))
        self.rename_format   = str(raw.get('rename_format', 'base')).strip().lower()
        self.rename_template = str(raw.get('rename_template',
            '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}][{Lingue}]')).strip()

        # ── Intervallo ciclo ──────────────────────────────────────────────────
        try:
            self.refresh_interval = int(raw.get('refresh_interval', 7200))
        except (ValueError, TypeError):
            self.refresh_interval = 7200

        # ── Filtri età ────────────────────────────────────────────────────────
        try:
            self.max_age_days = int(str(raw.get('max_age_days', 0)).split('#')[0].strip())
        except Exception:
            self.max_age_days = 0
        try:
            v = float(str(raw.get('stop_on_old_page_threshold', 0.8)).split('#')[0].strip())
            self.stop_on_old_page_threshold = v if 0.0 <= v <= 1.0 else 0.8
        except Exception:
            self.stop_on_old_page_threshold = 0.8

        # ── Feed ──────────────────────────────────────────────────────────────
        try:
            self.feed_max_items = int(str(raw.get('feed_max_items', 1000)).split('#')[0].strip())
            if self.feed_max_items <= 0:
                self.feed_max_items = 1000
        except Exception:
            self.feed_max_items = 1000

        # ── Archive cleanup ───────────────────────────────────────────────────
        self.archive_cleanup_enabled = self._parse_bool(raw.get('archive_cleanup_enabled', 'no'))
        try:
            self.archive_max_age_days = int(str(raw.get('archive_max_age_days', 365)).split('#')[0].strip())
            if self.archive_max_age_days <= 0:
                self.archive_max_age_days = 365
        except Exception:
            self.archive_max_age_days = 365
        try:
            self.archive_keep_min = int(str(raw.get('archive_keep_min', 10000)).split('#')[0].strip())
            if self.archive_keep_min < 1000:
                self.archive_keep_min = 10000
        except Exception:
            self.archive_keep_min = 10000

        # ── Trash / Cleaner ───────────────────────────────────────────────────
        self.cleanup_upgrades       = self._parse_bool(raw.get('cleanup_upgrades', 'no'))
        self.trash_path             = str(raw.get('trash_path', '')).split('#')[0].strip()
        self.archive_root           = str(raw.get('archive_root', '')).split('#')[0].strip()
        try:
            self.cleanup_min_score_diff = int(str(raw.get('cleanup_min_score_diff', 0)).split('#')[0].strip())
        except Exception:
            self.cleanup_min_score_diff = 0
        self.cleanup_action = str(raw.get('cleanup_action', 'move')).split('#')[0].strip().lower()
        if self.cleanup_action not in ('move', 'delete'):
            self.cleanup_action = 'move'

        # ── Varie ─────────────────────────────────────────────────────────────
        self.auto_remove_completed = self._parse_bool(raw.get('auto_remove_completed', 'no'))
        self.move_episodes         = self._parse_bool(raw.get('move_episodes', 'no'))
        try:
            self.web_port = int(str(raw.get('web_port', 5000)).split('#')[0].strip())
        except Exception:
            self.web_port = 5000
        try:
            self.comics_check_interval = int(str(raw.get('comics_check_interval', 604800)).split('#')[0].strip())
        except Exception:
            self.comics_check_interval = 604800
        try:
            self.min_free_space_gb = float(str(raw.get('min_free_space_gb', 10)).split('#')[0].strip())
        except Exception:
            self.min_free_space_gb = 10.0

        # ── Backup ────────────────────────────────────────────────────────────
        self.backup_dir       = str(raw.get('backup_dir', ''))
        self.backup_retention = int(raw.get('backup_retention', 5) or 5)
        self.backup_schedule  = str(raw.get('backup_schedule', 'manual'))

        # ── Scoring overrides ─────────────────────────────────────────────────
        for k, v in raw.items():
            if not k.startswith('score_'):
                continue
            key = k[len('score_'):].lower()
            val = str(v).split('#')[0].strip()
            try:
                if key.startswith('bonus_'):
                    b_name = 'BONUS_' + key[len('bonus_'):].upper()
                    if hasattr(Quality, b_name):
                        setattr(Quality, b_name, int(val))
                elif key.startswith('res_'):
                    Quality.RES_PREF[key[len('res_'):]] = int(val)
                elif key.startswith('source_'):
                    Quality.SOURCE_PREF[key[len('source_'):]] = int(val)
                elif key.startswith('codec_'):
                    Quality.CODEC_PREF[key[len('codec_'):]] = int(val)
                elif key.startswith('audio_'):
                    Quality.AUDIO_PREF[key[len('audio_'):]] = int(val)
                elif key.startswith('group_'):
                    Quality.GROUP_PREF[key[len('group_'):]] = int(val)
            except Exception:
                pass

        # ── Debug controls ────────────────────────────────────────────────────
        self.debug_duplicates       = self._parse_bool(raw.get('debug_duplicates', 'yes'))
        self.debug_blacklisted      = self._parse_bool(raw.get('debug_blacklisted', 'yes'))
        self.debug_quality_rejected = self._parse_bool(raw.get('debug_quality_rejected', 'yes'))
        self.debug_size_rejected    = self._parse_bool(raw.get('debug_size_rejected', 'yes'))
        try:
            self.debug_max_items = int(str(raw.get('debug_max_items', 0)))
        except Exception:
            self.debug_max_items = 0

        # ── Serie TV (dal DB operativo via migrazione o sync_configs) ─────────
        # Le serie sono in database.py (tabella series estesa).
        # Config le carica da lì tramite _load_series_from_db().
        self._load_series_from_db()

        # ── Film (da movies_config in extto_config.db) ────────────────────────
        self._load_movies_from_db()


    def _load_series_from_db(self):
        """Carica self.series dalla tabella series del DB operativo.

        Esegue prima l'automigrazione delle colonne nuove (seasons, language,
        ecc.) nel caso in cui Database() non sia ancora stato istanziato.
        """
        try:
            import sqlite3 as _sq
            from .constants import DB_FILE
            conn = _sq.connect(DB_FILE)
            conn.row_factory = _sq.Row

            # Automigrazione colonne: le aggiunge se non esistono ancora.
            # Stessa lista che c'è in Database._ensure_schema() — idempotente.
            for col_sql in [
                "ALTER TABLE series ADD COLUMN is_completed BOOLEAN DEFAULT 0",
                "ALTER TABLE series ADD COLUMN is_ended BOOLEAN DEFAULT 0",
                "ALTER TABLE series ADD COLUMN aliases TEXT DEFAULT ''",
                "ALTER TABLE series ADD COLUMN seasons TEXT DEFAULT '1+'",
                "ALTER TABLE series ADD COLUMN language TEXT DEFAULT 'ita'",
                "ALTER TABLE series ADD COLUMN enabled INTEGER DEFAULT 1",
                "ALTER TABLE series ADD COLUMN archive_path TEXT DEFAULT ''",
                "ALTER TABLE series ADD COLUMN timeframe INTEGER DEFAULT 0",
                "ALTER TABLE series ADD COLUMN ignored_seasons TEXT DEFAULT '[]'",
                "ALTER TABLE series ADD COLUMN tmdb_id TEXT DEFAULT ''",
                "ALTER TABLE series ADD COLUMN subtitle TEXT DEFAULT ''",
                "ALTER TABLE series ADD COLUMN season_subfolders INTEGER DEFAULT 0",
            ]:
                try:
                    conn.execute(col_sql)
                    conn.commit()
                except Exception:
                    pass  # colonna già esistente — normale

            rows = conn.execute(
                """SELECT name, seasons, language, enabled, archive_path,
                          timeframe, aliases, ignored_seasons, tmdb_id,
                          subtitle, quality_requirement, season_subfolders
                   FROM series ORDER BY name"""
            ).fetchall()
            conn.close()
            self.series = []
            for r in rows:
                try:
                    aliases = json.loads(r['aliases'] or '[]') if r['aliases'] else []
                except Exception:
                    aliases = []
                try:
                    ignored = json.loads(r['ignored_seasons'] or '[]') if r['ignored_seasons'] else []
                except Exception:
                    ignored = []
                enabled = bool(r['enabled']) if r['enabled'] is not None else True
                if not enabled:
                    continue
                self.series.append({
                    'name':              r['name'],
                    'seasons':           r['seasons'] or '1+',
                    'qual':              r['quality_requirement'] or 'any',
                    'lang':              r['language'] or 'ita',
                    'enabled':           enabled,
                    'archive_path':      r['archive_path'] or '',
                    'timeframe':         int(r['timeframe'] or 0),
                    'aliases':           aliases,
                    'ignored_seasons':   ignored,
                    'tmdb_id':           r['tmdb_id'] or '',
                    'subtitle':          r['subtitle'] or '',
                    'season_subfolders': bool(r['season_subfolders']) if r['season_subfolders'] is not None else False,
                })

            # Sanity check: se TUTTE le serie hanno seasons='1+' ma _migrated_series
            # ne ha alcune con valori diversi, significa che il DB non è stato
            # aggiornato correttamente dopo la migrazione. Usa _migrated_series
            # come fonte di verità per il campo seasons e aggiorna il DB.
            if self.series:
                all_default = all(s['seasons'] == '1+' for s in self.series)
                if all_default:
                    migrated = _cdb.get_setting('_migrated_series', [])
                    if migrated and isinstance(migrated, list):
                        has_non_default = any(
                            s.get('seasons', '1+') != '1+' for s in migrated
                        )
                        if has_non_default:
                            logger.warning(
                                "[Config] Rilevato DB con seasons=1+ per tutte le serie "
                                "ma _migrated_series ha valori diversi — correzione automatica..."
                            )
                            # Costruisce mappa name→seasons da _migrated_series
                            seasons_map = {
                                s['name']: s.get('seasons', '1+')
                                for s in migrated if isinstance(s, dict)
                            }
                            # Aggiorna in memoria e nel DB
                            try:
                                import sqlite3 as _sq2
                                from .constants import DB_FILE
                                conn2 = _sq2.connect(DB_FILE)
                                fixed = 0
                                for s in self.series:
                                    correct = seasons_map.get(s['name'], s['seasons'])
                                    if correct != s['seasons']:
                                        s['seasons'] = correct
                                        conn2.execute(
                                            "UPDATE series SET seasons=? WHERE LOWER(name)=LOWER(?)",
                                            (correct, s['name'])
                                        )
                                        fixed += 1
                                conn2.commit()
                                conn2.close()
                                if fixed:
                                    logger.info(f"[Config] Auto-corrected {fixed} series with invalid seasons field")
                            except Exception as fix_e:
                                logger.warning(f"[Config] Auto-correction of seasons failed: {fix_e}")
        except Exception as e:
            logger.warning(f"[Config] _load_series_from_db: {e}")
            # Fallback: prova a caricare dal setting di migrazione
            migrated = _cdb.get_setting('_migrated_series', [])
            if migrated and isinstance(migrated, list):
                self.series = [
                    s for s in migrated if s.get('enabled', True)
                ]

    def _load_movies_from_db(self):
        """Carica self.movies da movies_config in extto_config.db."""
        try:
            movies_raw = _cdb.get_movies_config()
            self.movies = [
                {
                    'name': m['name'],
                    'year': m['year'],
                    'qual': m['quality'],
                    'lang': m['language'],
                }
                for m in movies_raw
                if m.get('enabled', 1)
            ]
        except Exception as e:
            logger.warning(f"[Config] _load_movies_from_db: {e}")

    def _parse_series_line(self, line: str):
        """Parsa una singola riga serie TV e la aggiunge a self.series."""
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 5:
            return
        timeframe    = 0
        archive_path = ''
        aliases      = []
        ignored      = []
        for extra in parts[5:]:
            if not extra:
                continue
            if extra.startswith('alias='):
                raw_aliases = extra[len('alias='):]
                aliases = [a.strip() for a in raw_aliases.split(',') if a.strip()]
            elif 'timeframe:' in extra:
                tf_match = re.search(r'timeframe:(\d+)h', extra)
                if tf_match:
                    try:
                        timeframe = int(tf_match.group(1))
                    except Exception:
                        timeframe = 0
            elif extra.startswith('ignored:'):
                ign_str = extra.split(':', 1)[1]
                ignored = [int(x.strip()) for x in ign_str.split(',') if x.strip().isdigit()]
            elif not archive_path:
                archive_path = extra

        self.series.append({
            'name':         parts[0],
            'seasons':      parts[1],
            'qual':         parts[2],
            'lang':         parts[3],
            'enabled':      self._parse_bool(parts[4]),
            'timeframe':    timeframe,
            'archive_path': archive_path,
            'aliases':      aliases,
            'ignored_seasons': ignored,
        })

    # ------------------------------------------------------------------
    # PUBLIC HELPERS
    # ------------------------------------------------------------------

    def find_series_match(self, name: str, season: int):
        from .models import normalize_series_name, _series_name_matches
        norm_ep = normalize_series_name(name)
        for s in self.series:
            if not s['enabled']:
                continue
            # Controlla il nome principale + tutti gli alias
            candidates = [s['name']] + s.get('aliases', [])
            matched = any(_series_name_matches(normalize_series_name(c), norm_ep)
                          for c in candidates)
            if not matched:
                continue
            seasons = s['seasons']
            if seasons == '*':
                return s
            elif '+' in seasons:
                min_s = int(seasons.replace('+', ''))
                if season >= min_s:
                    return s
            elif '-' in seasons:
                start, end = map(int, seasons.split('-'))
                if start <= season <= end:
                    return s
            elif ',' in seasons:
                allowed = [int(x) for x in seasons.split(',')]
                if season in allowed:
                    return s
            elif seasons.isdigit() and season == int(seasons):
                return s
        return None

    def find_movie_match(self, name: str, year: int):
        """Cerca un film nella config con riconoscimento intelligente."""
        for m in self.movies:
            cfg_name = m['name'].strip()
            # Normalizzazione: trasforma punti e trattini in spazi
            title_norm = re.sub(r'[._\-]', ' ', name.lower())
            name_norm  = re.sub(r'[._\-]', ' ', cfg_name.lower())
            words = name_norm.split()
            if not words: continue

            # FIX: Usa \b (singolo) per il confine di parola reale
            if not all(re.search(rf'\b{re.escape(w)}\b', title_norm) for w in words):
                continue

            cfg_year = str(m.get('year', '') or '').strip()
            if not cfg_year: return m
            
            try:
                wanted = int(cfg_year)
                # FIX: Usa \d (singolo) per trovare le cifre dell'anno
                found_years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', name)
                if not found_years: continue
                title_years = [int(y) for y in found_years]
                if any(abs(ty - wanted) <= 1 for ty in title_years):
                    return m
            except (ValueError, TypeError):
                return m
        return None

    # --- Quality requirement helpers ---

    @staticmethod
    def _min_res_from_qual_req(qual_req: str) -> int:
        ranks = {'unknown': 0, '360p': 1, '480p': 2, '576p': 3, '720p': 4, '1080p': 5, '2160p': 6}
        if not qual_req: return 0
        q = qual_req.strip().lower()
        if q in ('any', '*', 'tutti', 'all'): return 0
        
        m_lt = re.match(r'<(\d+p)', q)
        if m_lt: return 0 
        
        m = re.match(r'(\d+p)-(\d+p)', q)
        if m: return ranks.get(m.group(1), 0)
        
        m2 = re.search(r'(\d+p)', q)
        if m2: return ranks.get(m2.group(1), 0)
        
        return 0

    @staticmethod
    def _max_res_from_qual_req(qual_req: str) -> int:
        ranks = {'unknown': 0, '360p': 1, '480p': 2, '576p': 3, '720p': 4, '1080p': 5, '2160p': 6}
        if not qual_req: return 99
        q = qual_req.strip().lower()
        if q in ('any', '*', 'tutti', 'all'): return 99
        
        m_lt = re.match(r'<(\d+p)', q)
        if m_lt: return ranks.get(m_lt.group(1), 99)
        
        m = re.match(r'(\d+p)-(\d+p)', q)
        if m: return ranks.get(m.group(2), 99)
        
        return 99

    @staticmethod
    def _res_rank_from_title(title: str) -> int:
        q = Parser.parse_quality(title)
        return Parser.get_res_rank(q.resolution)

    @staticmethod
    def _lang_ok(title: str, req_lang: str) -> bool:
        """Verifica se la lingua del titolo corrisponde a quella richiesta.

        req_lang è un codice ISO 639-2 (3 lettere, es. 'ita', 'deu') oppure
        ISO 639-1 (2 lettere, es. 'it', 'de') — entrambi accettati.
        Gestisce anche codici compositi tipo 'ita,eng' (accetta se almeno uno matcha).
        """
        if not req_lang:
            return True

        # Gestione compositi: 'ita,eng' → True se almeno una lingua matcha
        if ',' in req_lang:
            return any(
                Config._lang_ok(title, part.strip())
                for part in req_lang.split(',')
                if part.strip()
            )

        q = Parser.parse_quality(title)
        req = req_lang.strip().lower()

        # Normalizzazione: accetta sia 2 che 3 lettere
        _L2_TO_L3 = {
            'it': 'ita', 'en': 'eng', 'de': 'deu', 'fr': 'fra', 'es': 'spa',
            'pt': 'por', 'ja': 'jpn', 'zh': 'chi', 'ko': 'kor', 'ru': 'rus',
            'ar': 'ara', 'nl': 'nld', 'pl': 'pol', 'tr': 'tur', 'sv': 'swe',
            'no': 'nor', 'da': 'dan', 'fi': 'fin', 'hu': 'hun', 'cs': 'cze',
            'ro': 'ron', 'uk': 'ukr',
        }
        # Normalizza a 3 lettere se possibile
        req3 = _L2_TO_L3.get(req, req)

        # Italiano — usa il campo is_ita già calcolato da parse_quality
        if req3 in ('ita', 'italiano', 'it-it'):
            return q.is_ita

        t = (title or '').lower()
        t = re.sub(r'[._\-\(\)\[\]]', ' ', t)

        # Mapping completo: per ogni lingua, elenco di pattern da cercare nel titolo.
        # Copre: sigla ISO 639-2, sigla ISO 639-1, nome in lingua originale,
        # eventuali abbreviazioni usate dai gruppi torrent.
        _LANG_PATTERNS: Dict[str, List[str]] = {
            'eng': [r'\beng\b', r'\benglish\b', r'\ben\b', r'\bvo\b', r'\boriginal\b'],
            'deu': [r'\bdeu\b', r'\bde\b', r'\bgerman\b', r'\bdeutsch\b', r'\bger\b'],
            'fra': [r'\bfra\b', r'\bfr\b', r'\bfrench\b', r'\bfrançais\b', r'\bfrancais\b', r'\bvff\b', r'\bvf\b'],
            'spa': [r'\bspa\b', r'\bes\b', r'\bspanish\b', r'\bespañol\b', r'\bespanol\b', r'\bcast\b', r'\bcastellano\b'],
            'por': [r'\bpor\b', r'\bpt\b', r'\bportuguese\b', r'\bportuguês\b', r'\bportugues\b'],
            'jpn': [r'\bjpn\b', r'\bja\b', r'\bjapanese\b', r'\bjap\b', r'\bjp\b'],
            'chi': [r'\bchi\b', r'\bzho\b', r'\bzh\b', r'\bchinese\b', r'\bmandarin\b', r'\bchs\b', r'\bcht\b'],
            'kor': [r'\bkor\b', r'\bko\b', r'\bkorean\b'],
            'rus': [r'\brus\b', r'\bru\b', r'\brussian\b'],
            'ara': [r'\bara\b', r'\bar\b', r'\barabic\b'],
            'nld': [r'\bnld\b', r'\bnl\b', r'\bdutch\b', r'\bnederlands\b'],
            'pol': [r'\bpol\b', r'\bpl\b', r'\bpolish\b', r'\bpolski\b'],
            'tur': [r'\btur\b', r'\btr\b', r'\bturkish\b', r'\btürkçe\b', r'\bturkce\b'],
            'swe': [r'\bswe\b', r'\bsv\b', r'\bswedish\b', r'\bsvenska\b'],
            'nor': [r'\bnor\b', r'\bno\b', r'\bnorwegian\b', r'\bnorsk\b'],
            'dan': [r'\bdan\b', r'\bda\b', r'\bdanish\b', r'\bdansk\b'],
            'fin': [r'\bfin\b', r'\bfi\b', r'\bfinnish\b', r'\bsuomi\b'],
            'hun': [r'\bhun\b', r'\bhu\b', r'\bhungarian\b', r'\bmagyar\b'],
            'cze': [r'\bcze\b', r'\bces\b', r'\bcs\b', r'\bczech\b', r'\bčeština\b', r'\bcestina\b'],
            'ron': [r'\bron\b', r'\brum\b', r'\bro\b', r'\bromanian\b', r'\bromână\b', r'\bromana\b'],
            'ukr': [r'\bukr\b', r'\buk\b', r'\bukrainian\b', r'\bукраїнська\b'],
        }

        patterns = _LANG_PATTERNS.get(req3)
        if patterns:
            return any(re.search(p, t) for p in patterns)

        # Fallback generico per lingue non in mappa: cerca la sigla come parola intera
        return any(re.search(rf'\b{re.escape(p)}\b', t) for p in [req3, req])

    @staticmethod
    def _sub_score(title: str, sub_req: str) -> int:
        """Calcola il bonus sottotitoli: +200 per ogni lingua trovata nel titolo.
        sub_req può essere una sigla singola ('ita') o lista separata da virgola ('ita,eng').
        Cerca marcatori REALI di sottotitolo nel titolo, NON la lingua audio.

        Pattern riconosciuti:
          sub.ita  subita  sub-ita  sub_ita  subs.ita  subforced.ita
          ita.sub  [ita]sub  sub[ita]  subs.it.en (lista post-sub)
        NON matcha: "ENG.sub.ita" come sub in lingua eng (ENG è audio, sub.ita è il sub).

        Ritorna 0 se sub_req è vuoto/none/any/*.
        """
        if not sub_req or sub_req.strip().lower() in ('', 'none', 'any', '*'):
            return 0
        t = (title or '').lower()

        _L3_L2 = {
            'ita': 'it', 'eng': 'en', 'fra': 'fr', 'deu': 'de', 'spa': 'es',
            'jpn': 'ja', 'por': 'pt', 'chi': 'zh', 'kor': 'ko', 'rus': 'ru',
        }
        _L2_L3 = {v: k for k, v in _L3_L2.items()}
        _ALL_CODES = set(_L3_L2) | set(_L2_L3)

        # Estrai tutte le sigle che compaiono in liste post-sub (es: "subs.it.en.fr")
        sub_list_langs: set = set()
        for _m in re.finditer(r'\bsub[s]?[.\s\-_]((?:[a-z]{2,3}[.\s\-_]?)+)', t):
            for code in re.findall(r'[a-z]{2,3}', _m.group(1)):
                if code in _ALL_CODES:
                    sub_list_langs.add(code)
                    if code in _L3_L2:   sub_list_langs.add(_L3_L2[code])
                    elif code in _L2_L3: sub_list_langs.add(_L2_L3[code])

        def _has_sub(code: str) -> bool:
            code = code.strip().lower()
            variants: set = {code}
            if code in _L3_L2:   variants.add(_L3_L2[code])
            elif code in _L2_L3: variants.add(_L2_L3[code])

            if variants & sub_list_langs:
                return True

            for v in variants:
                # "sub/subs/subforced" + separatore opzionale (anche punto) + sigla
                if re.search(rf'\bsub(?:s|forced|sforced)?[.\s\-_]?{re.escape(v)}\b', t):
                    return True
                # sigla + sep + "sub" — solo se NON segue subito un'altra sigla lingua
                # (distingue "ita.sub" da "eng.sub.ita" dove eng è audio)
                _m2 = re.search(
                    rf'\b{re.escape(v)}[.\s\-_]sub(?:s|forced|sforced)?(?:[.\s\-_]([a-z]{{2,3}}))?(?:[.\s\-_]|$)', t
                )
                if _m2:
                    after = _m2.group(1)
                    if not (after and after in _ALL_CODES):
                        return True
                # [ita]sub o sub[ita]
                if re.search(rf'(?:sub|subs|forced)[.\s\-_]*[\[\(]{re.escape(v)}[\]\)]', t):
                    return True
                if re.search(rf'[\[\(]{re.escape(v)}[\]\)][.\s\-_]*(?:sub|subs|forced)', t):
                    return True
            return False

        bonus = 0
        for lang in sub_req.lower().split(','):
            lang = lang.strip()
            if lang and _has_sub(lang):
                bonus += 200
        return bonus

    def get_custom_score(self, title: str) -> int:
        """Calcola i punti bonus/malus cercando le parole nel titolo."""
        bonus = 0
        t_lower = (title or '').lower()
        for parola, punti in getattr(self, 'custom_scores', {}).items():
            if parola in t_lower:
                bonus += punti
        return bonus
