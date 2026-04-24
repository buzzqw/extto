"""
EXTTO - Persistenza: Database (episodi/film), ArchiveDB, SmartCache.
"""

import os
import re
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .constants import (
    DB_FILE, ARCHIVE_FILE, CACHE_FILE, ARCHIVE_CREDENTIALS, logger
)
from .models import stats, Parser, Quality


# ---------------------------------------------------------------------------
# DATABASE principale
# ---------------------------------------------------------------------------

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _ensure_schema(self):
        c = self.conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                quality_requirement TEXT
            )
        ''')
        # Automigrazione: aggiungi colonne se non esistono
        for col_sql in [
            "ALTER TABLE series ADD COLUMN is_completed BOOLEAN DEFAULT 0",
            "ALTER TABLE series ADD COLUMN is_ended BOOLEAN DEFAULT 0",
            "ALTER TABLE series ADD COLUMN aliases TEXT DEFAULT ''",
            # Colonne aggiunte v39: migrazione da series.txt a DB
            "ALTER TABLE series ADD COLUMN seasons TEXT DEFAULT '1+'",
            "ALTER TABLE series ADD COLUMN language TEXT DEFAULT 'ita'",
            "ALTER TABLE series ADD COLUMN enabled INTEGER DEFAULT 1",
            "ALTER TABLE series ADD COLUMN archive_path TEXT DEFAULT ''",
            "ALTER TABLE series ADD COLUMN timeframe INTEGER DEFAULT 0",
            "ALTER TABLE series ADD COLUMN ignored_seasons TEXT DEFAULT '[]'",
            "ALTER TABLE series ADD COLUMN tmdb_id TEXT DEFAULT ''",
            "ALTER TABLE series ADD COLUMN subtitle TEXT DEFAULT ''",
        ]:
            try: 
                c.execute(col_sql)
                self.conn.commit()
            except Exception as e:
                # Silenzia l'errore "duplicate column name" perché è il comportamento previsto
                if "duplicate column name" not in str(e).lower():
                    logger.debug(f"Database setup error: {e}")

        c.execute('''
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER,
                season INTEGER,
                episode INTEGER,
                title TEXT,
                quality_score INTEGER,
                is_repack INTEGER,
                magnet_hash TEXT UNIQUE,
                magnet_link TEXT,
                downloaded_at TEXT,
                archive_path TEXT,
                size_bytes INTEGER DEFAULT 0,
                UNIQUE(series_id, season, episode)
            )
        ''')
        try: c.execute("ALTER TABLE episodes ADD COLUMN archive_path TEXT"); self.conn.commit()
        except: pass
        try: c.execute("ALTER TABLE episodes ADD COLUMN size_bytes INTEGER DEFAULT 0"); self.conn.commit()
        except: pass
        try: c.execute("ALTER TABLE episodes ADD COLUMN original_title TEXT"); self.conn.commit()
        except: pass

        # Feed matches: release che hanno passato il check lingua ma non altri criteri,
        # oppure release scaricate. Si mantengono i top-5 per quality_score per episodio.
        c.execute('''
            CREATE TABLE IF NOT EXISTS episode_feed_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                title TEXT NOT NULL,
                quality_score INTEGER NOT NULL DEFAULT 0,
                fail_reason TEXT,
                magnet TEXT,
                found_at TEXT NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_efm_ep ON episode_feed_matches(series_id, season, episode, quality_score DESC)')

        c.execute('''
            CREATE TABLE IF NOT EXISTS episode_discards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                reason TEXT NOT NULL,
                at TEXT NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_episode_discards_ep ON episode_discards(series_id, season, episode, at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_episode_discards_at ON episode_discards(at)')

        c.execute('''
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                year INTEGER,
                title TEXT,
                quality_score INTEGER,
                magnet_hash TEXT UNIQUE,
                magnet_link TEXT,
                downloaded_at TEXT,
                size_bytes INTEGER DEFAULT 0
            )
        ''')
        try: c.execute("ALTER TABLE movies ADD COLUMN size_bytes INTEGER DEFAULT 0"); self.conn.commit()
        except: pass
        try: c.execute("ALTER TABLE movies ADD COLUMN removed_at TEXT DEFAULT NULL"); self.conn.commit()
        except: pass
        try: c.execute("ALTER TABLE pending_downloads ADD COLUMN downloaded_at TEXT"); self.conn.commit()
        except: pass

        c.execute('''
            CREATE TABLE IF NOT EXISTS movie_discards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                year INTEGER NOT NULL,
                reason TEXT NOT NULL,
                at TEXT NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_movie_discards ON movie_discards(name, year, at DESC)')

        # Ricrea la tabella movie_feed_matches se manca la colonna movie_name
        # (versioni precedenti usavano movie_id int — drop e ricrea)
        try:
            c.execute("SELECT movie_name FROM movie_feed_matches LIMIT 1")
        except Exception:
            c.execute("DROP TABLE IF EXISTS movie_feed_matches")
            self.conn.commit()

        c.execute('''
            CREATE TABLE IF NOT EXISTS movie_feed_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_name TEXT NOT NULL,
                title TEXT NOT NULL,
                quality_score INTEGER NOT NULL DEFAULT 0,
                lang_bonus INTEGER NOT NULL DEFAULT 0,
                fail_reason TEXT,
                magnet TEXT,
                found_at TEXT NOT NULL
            )
        ''')
        try:
            c.execute('CREATE INDEX IF NOT EXISTS idx_mfm ON movie_feed_matches(movie_name, quality_score DESC)')
        except Exception:
            pass

        c.execute('''
            CREATE TABLE IF NOT EXISTS pending_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER,
                season INTEGER,
                episode INTEGER,
                best_title TEXT,
                best_quality_score INTEGER,
                best_magnet TEXT,
                first_seen_at TEXT,
                timeframe_hours INTEGER,
                status TEXT DEFAULT 'pending',
                downloaded_at TEXT,
                UNIQUE(series_id, season, episode)
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS cycle_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                payload TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS episode_archive_presence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER,
                season INTEGER,
                episode INTEGER,
                best_quality_score INTEGER,
                at TEXT,
                UNIQUE(series_id, season, episode)
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS series_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                tvdb_id INTEGER,
                season INTEGER NOT NULL,
                expected_episodes INTEGER NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(series_id, season)
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_series_metadata ON series_metadata(series_id, season)')

        # Indici su downloaded_at: usati da get_consumption_stats (SUM con WHERE su date)
        # Senza di questi, su 100k+ episodi ogni query di stats è un full scan.
        c.execute('CREATE INDEX IF NOT EXISTS idx_episodes_dl_at ON episodes(downloaded_at)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_movies_dl_at ON movies(downloaded_at)')
        # Indice su series_id per episodes: accelera find_gaps, get_series_seasons, get_series_stats
        c.execute('CREATE INDEX IF NOT EXISTS idx_episodes_series ON episodes(series_id, season, episode)')
        # Indice su archive_path: usato da _best_quality_in_path
        c.execute('CREATE INDEX IF NOT EXISTS idx_episodes_archive_path ON episodes(archive_path)')
        # Indici aggiuntivi: pending_downloads e magnet_hash
        c.execute('CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_downloads(status, series_id, season, episode)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_episodes_magnet ON episodes(magnet_hash)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_movies_magnet ON movies(magnet_hash)')

        self.conn.commit()

    # ------------------------------------------------------------------
    # CONFIG SYNC
    # ------------------------------------------------------------------

    def sync_configs(self, series_list, movies_list):
        """Sincronizza la configurazione serie dal DB config al DB operativo.
        Fa upsert di tutti i campi — inserisce se non esiste, aggiorna se esiste.
        """
        import json as _json
        c = self.conn.cursor()
        for s in series_list:
            aliases_json  = _json.dumps(s.get('aliases', []), ensure_ascii=False)
            ignored_json  = _json.dumps(s.get('ignored_seasons', []), ensure_ascii=False)
            c.execute(
                """INSERT INTO series
                       (name, quality_requirement, seasons, language, enabled,
                        archive_path, timeframe, aliases, ignored_seasons, tmdb_id, subtitle)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                       quality_requirement = excluded.quality_requirement,
                       seasons             = excluded.seasons,
                       language            = excluded.language,
                       enabled             = excluded.enabled,
                       archive_path        = excluded.archive_path,
                       timeframe           = excluded.timeframe,
                       aliases             = excluded.aliases,
                       ignored_seasons     = excluded.ignored_seasons,
                       tmdb_id             = excluded.tmdb_id,
                       subtitle            = excluded.subtitle
                """,
                (
                    s['name'],
                    s.get('qual', s.get('quality', 'any')),
                    s.get('seasons', '1+'),
                    s.get('lang', s.get('language', 'ita')),
                    1 if s.get('enabled', True) else 0,
                    s.get('archive_path', ''),
                    int(s.get('timeframe', 0) or 0),
                    aliases_json,
                    ignored_json,
                    str(s.get('tmdb_id', '') or ''),
                    str(s.get('subtitle', '') or ''),
                )
            )
        self.conn.commit()

    # ------------------------------------------------------------------
    # DISCARD LOGGING
    # ------------------------------------------------------------------

    def undo_episode_send(self, series_id: int, season: int, episode: int, magnet: str) -> bool:
        """Annulla l'inserimento/aggiornamento fatto da check_series quando il send al client fallisce.

        - Se l'episodio è stato appena inserito (magnet_hash corrisponde, downloaded_at < 10s fa):
          lo cancella completamente.
        - Se era un upgrade (esisteva già un record precedente con hash diverso):
          ripristina downloaded_at=NULL e magnet_link=NULL per renderlo "da scaricare".
        Restituisce True se ha effettuato un'operazione, False altrimenti.
        """
        import re as _re
        from datetime import datetime, timezone, timedelta
        h = _re.search(r'btih:([a-fA-F0-9]{40})', magnet, _re.I)
        if not h:
            return False
        hash_val = h.group(1).lower()
        try:
            c = self.conn.cursor()
            c.execute(
                "SELECT id, magnet_hash, downloaded_at FROM episodes "
                "WHERE series_id=? AND season=? AND episode=? AND magnet_hash=?",
                (series_id, season, episode, hash_val)
            )
            row = c.fetchone()
            if not row:
                return False
            # Controlla se è stato inserito/aggiornato negli ultimi 10 secondi
            try:
                ts = datetime.fromisoformat(row['downloaded_at'].replace('Z', '+00:00'))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - ts).total_seconds()
            except Exception:
                age = 9999
            if age > 10:
                return False  # troppo vecchio, non toccare
            # Cancella il record (era un inserimento new o un upgrade che ora va rimosso)
            c.execute("DELETE FROM episodes WHERE id=?", (row['id'],))
            self.conn.commit()
            logger.info(f"↩️  undo_episode_send: removed ghost episode "
                        f"S{season:02d}E{episode:02d} (series_id={series_id})")
            return True
        except Exception as e:
            logger.warning(f"undo_episode_send error: {e}")
            return False

    def record_episode_discard(self, series_id: int, season: int, episode: int, reason: str):
        try:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO episode_discards (series_id, season, episode, reason, at) VALUES (?, ?, ?, ?, ?)",
                (series_id, season, episode, reason, datetime.now(timezone.utc).isoformat())
            )
            self.conn.commit()
        except Exception as e:
            logger.debug(f"record_episode_discard: {e}")

    def record_movie_feed_match(self, movie_name: str, title: str,
                               quality_score: int, lang_bonus: int,
                               fail_reason, magnet: str):
        """Registra un candidato nel feed film (top-5 per effective_score).
        Usa movie_name come chiave — funziona anche per film non ancora scaricati.
        Mantiene max 5 slot: 1 fisso per 'downloaded', 4 rimpiazzabili per score.
        """
        try:
            c = self.conn.cursor()
            effective = quality_score + lang_bonus
            found_at = datetime.now(timezone.utc).isoformat()

            # Conta slot esistenti
            c.execute("SELECT COUNT(*) FROM movie_feed_matches WHERE movie_name=?", (movie_name,))
            count = c.fetchone()[0]

            if fail_reason == 'downloaded':
                # Slot fisso: aggiorna o inserisce il record downloaded
                c.execute("SELECT id FROM movie_feed_matches WHERE movie_name=? AND fail_reason='downloaded'", (movie_name,))
                row = c.fetchone()
                if row:
                    c.execute("UPDATE movie_feed_matches SET title=?, quality_score=?, lang_bonus=?, magnet=?, found_at=? WHERE id=?",
                              (title, quality_score, lang_bonus, magnet, found_at, row['id']))
                else:
                    c.execute("INSERT INTO movie_feed_matches (movie_name, title, quality_score, lang_bonus, fail_reason, magnet, found_at) VALUES (?,?,?,?,?,?,?)",
                              (movie_name, title, quality_score, lang_bonus, fail_reason, magnet, found_at))
            else:
                if count < 5:
                    c.execute("INSERT INTO movie_feed_matches (movie_name, title, quality_score, lang_bonus, fail_reason, magnet, found_at) VALUES (?,?,?,?,?,?,?)",
                              (movie_name, title, quality_score, lang_bonus, fail_reason, magnet, found_at))
                else:
                    # Rimpiazza il peggiore tra i non-downloaded se il nuovo è migliore
                    c.execute("SELECT id, quality_score+lang_bonus as eff FROM movie_feed_matches WHERE movie_name=? AND fail_reason!='downloaded' ORDER BY eff ASC LIMIT 1", (movie_name,))
                    worst = c.fetchone()
                    if worst and effective > worst['eff']:
                        c.execute("UPDATE movie_feed_matches SET title=?, quality_score=?, lang_bonus=?, fail_reason=?, magnet=?, found_at=? WHERE id=?",
                                  (title, quality_score, lang_bonus, fail_reason, magnet, found_at, worst['id']))
            self.conn.commit()
        except Exception as e:
            logger.warning(f"record_movie_feed_match error: {e}")

    def get_movie_feed_matches(self, movie_name: str):
        """Ritorna i feed matches per un film (per nome), ordinati per effective_score DESC."""
        try:
            c = self.conn.cursor()
            c.execute("""
                SELECT title, quality_score, lang_bonus, fail_reason, magnet, found_at,
                       (quality_score + lang_bonus) as effective_score
                FROM movie_feed_matches
                WHERE movie_name=?
                ORDER BY
                    CASE WHEN fail_reason='downloaded' THEN 1 ELSE 0 END DESC,
                    effective_score DESC
                LIMIT 5
            """, (movie_name,))
            return [dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.warning(f"get_movie_feed_matches error: {e}")
            return []

    def has_movie_feed_matches(self, movie_name: str):
        """True se esistono feed matches per il film (per nome)."""
        try:
            c = self.conn.cursor()
            c.execute("SELECT 1 FROM movie_feed_matches WHERE movie_name=? LIMIT 1", (movie_name,))
            return c.fetchone() is not None
        except Exception as e:
            logger.warning(f"has_movie_feed_matches error: {e}")
            return False

    def record_movie_discard(self, name: str, year: int, reason: str):
        try:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO movie_discards (name, year, reason, at) VALUES (?, ?, ?, ?)",
                (name, year, reason, datetime.now(timezone.utc).isoformat())
            )
            self.conn.commit()
        except Exception as e:
            logger.debug(f"record_movie_discard: {e}")

    # ------------------------------------------------------------------
    # FEED MATCHES
    # ------------------------------------------------------------------

    def record_feed_match(self, series_id: int, season: int, episode: int,
                          title: str, quality_score: int,
                          fail_reason: Optional[str], magnet: str):
        """Registra un match nei feed (lingua OK) per un episodio.
        Si mantengono i top-5 per quality_score:
        - Il record con fail_reason='downloaded' (se presente) occupa sempre uno slot fisso.
        - I restanti 4 slot sono i migliori per score tra tutti gli altri.
        Se un nuovo match ha score peggiore del minimo nei 4 slot parziali, viene ignorato.
        """
        try:
            c = self.conn.cursor()
            now = datetime.now(timezone.utc).isoformat()

            # Upsert: se esiste già un record 'downloaded' per questo episodio,
            # aggiornalo (potrebbe avere score migliore in caso di upgrade).
            if fail_reason == 'downloaded':
                c.execute('''
                    INSERT INTO episode_feed_matches
                        (series_id, season, episode, title, quality_score, fail_reason, magnet, found_at)
                    VALUES (?, ?, ?, ?, ?, 'downloaded', ?, ?)
                    ON CONFLICT DO NOTHING
                ''', (series_id, season, episode, title, quality_score, magnet, now))
                # Se già esiste un 'downloaded', aggiorna titolo/score/magnet solo se migliore
                c.execute('''
                    UPDATE episode_feed_matches
                    SET title=?, quality_score=?, magnet=?, found_at=?
                    WHERE series_id=? AND season=? AND episode=? AND fail_reason='downloaded'
                      AND quality_score < ?
                ''', (title, quality_score, magnet, now,
                      series_id, season, episode, quality_score))
                # Inserisci se non c'era nessun 'downloaded'
                c.execute('''
                    INSERT OR IGNORE INTO episode_feed_matches
                        (series_id, season, episode, title, quality_score, fail_reason, magnet, found_at)
                    SELECT ?, ?, ?, ?, ?, 'downloaded', ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM episode_feed_matches
                        WHERE series_id=? AND season=? AND episode=? AND fail_reason='downloaded'
                    )
                ''', (series_id, season, episode, title, quality_score, magnet, now,
                      series_id, season, episode))
                self.conn.commit()
                return

            # Match parziale: controlla quanti slot parziali sono già occupati
            c.execute('''
                SELECT id, quality_score FROM episode_feed_matches
                WHERE series_id=? AND season=? AND episode=? AND (fail_reason IS NULL OR fail_reason != 'downloaded')
                ORDER BY quality_score DESC
            ''', (series_id, season, episode))
            partials = c.fetchall()

            MAX_PARTIALS = 4

            if len(partials) < MAX_PARTIALS:
                # Slot libero: inserisci direttamente
                c.execute('''
                    INSERT INTO episode_feed_matches
                        (series_id, season, episode, title, quality_score, fail_reason, magnet, found_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (series_id, season, episode, title, quality_score, fail_reason, magnet, now))
            else:
                # Tutti e 4 i slot parziali occupati: rimpiazza il peggiore se il nuovo è migliore
                worst = partials[-1]  # ordinati DESC, l'ultimo è il peggiore
                if quality_score > worst['quality_score']:
                    c.execute('DELETE FROM episode_feed_matches WHERE id=?', (worst['id'],))
                    c.execute('''
                        INSERT INTO episode_feed_matches
                            (series_id, season, episode, title, quality_score, fail_reason, magnet, found_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (series_id, season, episode, title, quality_score, fail_reason, magnet, now))
                # else: score troppo basso, non vale la pena salvarlo

            self.conn.commit()
        except Exception as e:
            logger.debug(f"record_feed_match: {e}")

    def get_feed_matches(self, series_id: int, season: int, episode: int) -> List[Dict]:
        """Restituisce i top-5 match per un episodio ordinati:
        1) il record 'downloaded' per primo (se presente)
        2) poi gli altri per score DESC
        """
        try:
            c = self.conn.cursor()
            c.execute('''
                SELECT title, quality_score, fail_reason, magnet, found_at
                FROM episode_feed_matches
                WHERE series_id=? AND season=? AND episode=?
                ORDER BY
                    CASE WHEN fail_reason='downloaded' THEN 0 ELSE 1 END,
                    quality_score DESC
                LIMIT 5
            ''', (series_id, season, episode))
            return [dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.debug(f"get_feed_matches: {e}")
            return []

    def has_feed_matches(self, series_id: int, season: int, episode: int) -> bool:
        """True se esistono feed matches per l'episodio (usato per stato 'In feed')."""
        try:
            c = self.conn.cursor()
            c.execute(
                'SELECT 1 FROM episode_feed_matches WHERE series_id=? AND season=? AND episode=? LIMIT 1',
                (series_id, season, episode)
            )
            return c.fetchone() is not None
        except Exception as e:
            logger.debug(f"has_feed_matches: {e}")
            return False

    # ------------------------------------------------------------------
    # ATOMIC TRANSITIONS
    # ------------------------------------------------------------------

    def begin_downloading(self, pending_id: int) -> bool:
        c = self.conn.cursor()
        c.execute(
            "UPDATE pending_downloads SET status='downloading' WHERE id=? AND status='pending'",
            (pending_id,)
        )
        self.conn.commit()
        return c.rowcount == 1

    def reset_stale_downloading(self) -> int:
        """Al riavvio, rimette in 'pending' i download rimasti bloccati in stato 'downloading'.
        Questi torrent non sono mai stati completati (mark_downloaded li segna 'downloaded'),
        quindi al riavvio possono essere ritentati normalmente."""
        c = self.conn.cursor()
        c.execute(
            "UPDATE pending_downloads SET status='pending' WHERE status='downloading'"
        )
        self.conn.commit()
        return c.rowcount

    # ------------------------------------------------------------------
    # SERIES CHECK
    # ------------------------------------------------------------------

    def check_series(self, ep: dict, magnet: str, quality_req: str):
        from .config import Config  # imported here to avoid circular at module level
        from .models import normalize_series_name, _series_name_matches

        h = re.search(r'btih:([a-fA-F0-9]{40})', magnet, re.I)
        if not h:
            return False, "No hash"
        hash_val = h.group(1).lower()

        c = self.conn.cursor()
        # Matching robusto: normalizza il nome estratto e confronta con
        # tutti i nomi nel DB usando _series_name_matches (anti-ambiguità).
        norm_ep_name = normalize_series_name(ep['name'])
        c.execute("SELECT id, name, aliases FROM series")
        matched_row = None
        for row in c.fetchall():
            if _series_name_matches(normalize_series_name(row['name']), norm_ep_name):
                matched_row = row
                break
            # Fallback: controlla gli aliases
            try:
                aliases = json.loads(row['aliases'] or '[]')
            except Exception:
                aliases = []
            if any(_series_name_matches(normalize_series_name(a), norm_ep_name) for a in aliases):
                matched_row = row
                break
        if not matched_row:
            return False, "No series"
        series_id = matched_row['id']

        # Duplicate hash
        c.execute("SELECT id FROM episodes WHERE magnet_hash = ?", (hash_val,))
        if c.fetchone():
            stats.duplicates.append(f"{ep['name']} S{ep['season']:02d}E{ep['episode']:02d}")
            self.record_episode_discard(series_id, ep['season'], ep['episode'], 'duplicate_hash')
            return False, "Duplicate hash"

        # --- PROTEZIONE DOWNLOAD ATTIVI ---
        try:
            from .clients.libtorrent import LibtorrentClient
            active = LibtorrentClient.list_torrents()
            for t in active:
                # 1. Stesso hash
                if t.get('hash', '').lower() == hash_val:
                    return False, "Hash already active in client"
                
                # 2. Stesso episodio (evita download concorrenti dello stesso Exx)
                from .models import Parser, normalize_series_name, _series_name_matches
                t_name = t.get('name', '')
                t_ep = Parser.parse_series_episode(t_name)
                if t_ep and t_ep['season'] == ep['season'] and t_ep['episode'] == ep['episode']:
                    if _series_name_matches(normalize_series_name(t_ep['name']), normalize_series_name(ep['name'])):
                        # Se quello attivo ha score >=, blocca. Altrimenti permetti upgrade? 
                        # In realtà libtorrent gestisce male due torrent diversi per lo stesso file fisico simultaneamente.
                        return False, "Same episode already downloading"
        except Exception as e:
            logger.warning(f"protezione download attivi: {e}")

        new_score    = ep['quality'].score()
        archive_path = ep.get('archive_path', '') if isinstance(ep, dict) else ''

        # Punto 5: Se archive_path non è configurato per la serie, prova a trovare
        # automaticamente la cartella tramite @archive_root / nome_serie
        if not archive_path:
            try:
                from .config import Config
                cfg_obj = Config()
                archive_roots = cfg_obj.qbt.get('archive_root', [])
                if isinstance(archive_roots, str):
                    archive_roots = [archive_roots]
                from .models import normalize_series_name, _series_name_matches
                norm_series = normalize_series_name(ep.get('name', ''))
                for root in archive_roots:
                    if not root or not os.path.isdir(root):
                        continue
                    for entry in os.listdir(root):
                        if _series_name_matches(normalize_series_name(entry), norm_series):
                            candidate = os.path.join(root, entry)
                            if os.path.isdir(candidate):
                                archive_path = candidate
                                logger.debug(f"[ARCHIVE-CHECK] Auto-detected path: {archive_path}")
                                break
                    if archive_path:
                        break
            except Exception as e:
                logger.debug(f"archive_root auto-detect: {e}")
        try:
            if archive_path:
                logger.debug(f"[ARCHIVE-CHECK] Series='{ep.get('name')}' S{int(ep.get('season',0)):02d}E{int(ep.get('episode',0)):02d} path={archive_path}")
        except Exception:
            pass

        try:
            existing_path_score = self._best_quality_in_path(
                ep['name'], ep['season'], ep['episode'], archive_path
            ) if archive_path else None
        except Exception as e:
            logger.debug(f"_best_quality_in_path: {e}")
            existing_path_score = None

        if existing_path_score is not None:
            logger.debug(f"[ARCHIVE-CHECK] best_in_path={existing_path_score} vs new_score={new_score}")
        if existing_path_score is not None and existing_path_score >= new_score:
            stats.duplicates.append(f"{ep['name']} S{ep['season']:02d}E{ep['episode']:02d}")
            self.record_episode_discard(series_id, ep['season'], ep['episode'],
                                        "in archivio con qualita' uguale o superiore")
            
            # --- START INTELLIGENZA ARCHIVIO ---
            is_new_discovery = False
            try:
                c2 = self.conn.cursor()
                # Controlla se sapevamo già di avere questo file (e con che punteggio)
                c2.execute('SELECT best_quality_score FROM episode_archive_presence WHERE series_id=? AND season=? AND episode=?', (series_id, ep['season'], ep['episode']))
                row = c2.fetchone()
                
                # Se è la prima volta che lo vediamo, o se l'utente lo ha sostituito a mano con uno migliore!
                if not row or int(existing_path_score) > int(row[0] or 0):
                    is_new_discovery = True

                # Aggiorna la memoria del database
                c2.execute('''
                    INSERT INTO episode_archive_presence (series_id, season, episode, best_quality_score, at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(series_id,season,episode) DO UPDATE
                        SET best_quality_score=excluded.best_quality_score, at=excluded.at
                ''', (series_id, ep['season'], ep['episode'],
                      int(existing_path_score or 0), datetime.now(timezone.utc).isoformat()))
                self.conn.commit()
            except Exception as e:
                logger.warning(f"episode_archive_presence update: {e}")
            
            # Stampa nel log SOLO se è una novità assoluta o un upgrade manuale
            if is_new_discovery:
                logger.info(f"🎉 [New in Archive] Found '{ep['name']}' S{ep['season']:02d}E{ep['episode']:02d} on disk (Score: {existing_path_score}). Database aligned!")
            else:
                logger.debug(f"[ARCHIVE-CHECK] SKIP: '{ep['name']}' S{ep['season']:02d}E{ep['episode']:02d} already known (Score: {existing_path_score} >= {new_score})")
            # --- END INTELLIGENZA ARCHIVIO ---

            return False, "In archivio con qualita' uguale o superiore"

        # DB CHECK
        # --- MIGLIORAMENTO SEASON PACK ---
        # Se è un pack che rappresenta l'intera stagione (E00), verifichiamo se 
        # abbiamo già episodi di quella stagione con qualità superiore.
        if ep.get('is_pack') and ep.get('episode') == 0:
            c.execute("SELECT MAX(quality_score) as max_s, COUNT(*) as cnt FROM episodes WHERE series_id = ? AND season = ? AND episode > 0",
                     (series_id, ep['season']))
            db_row = c.fetchone()
            db_max = db_row['max_s'] if db_row else None
            db_cnt = db_row['cnt'] if db_row else 0
            
            # Quanti episodi ci aspettiamo?
            expected = self.get_expected_episodes(series_id, ep['season'])
            # FIX: Protezione contro il None se TMDB non conosce il numero di episodi
            is_complete = (expected is not None) and (expected > 0) and (db_cnt >= expected)
            
            # Se la stagione è completa e la qualità esistente è migliore o uguale, scarta.
            # Se NON è completa, permettiamo il download del pack per riempire i buchi,
            # a meno che la qualità del pack non sia davvero pessima (ma è già filtrata a monte).
            if is_complete and db_max and db_max >= new_score:
                stats.duplicates.append(f"{ep['name']} S{ep['season']:02d} Pack (inferiore a episodi esistenti)")
                self.record_episode_discard(series_id, ep['season'], 0, 'existing_season_better')
                logger.info(f"[SEASON-PACK] SKIP: season is complete and pack {new_score} is not an upgrade (DB max: {db_max})")
                return False, "Existing season better"
            
            if not is_complete:
                safe_exp = expected if (expected is not None and expected > 0) else '?'
                logger.info(f"[SEASON-PACK] ACCEPT: incomplete season ({db_cnt}/{safe_exp}), downloading pack to fill gaps")

        c.execute(
            "SELECT quality_score FROM episodes WHERE series_id = ? AND season = ? AND episode = ?",
            (series_id, ep['season'], ep['episode'])
        )
        row = c.fetchone()

        if row:
            if new_score > row['quality_score']:
                c.execute("""UPDATE episodes SET quality_score=?, is_repack=?,
                            magnet_hash=?, magnet_link=?, title=?, downloaded_at=?, archive_path=?
                            WHERE series_id=? AND season=? AND episode=?""",
                         (new_score, ep['quality'].is_repack, hash_val, magnet, ep['title'],
                          datetime.now(timezone.utc).isoformat(), archive_path, series_id, ep['season'], ep['episode']))
                self.conn.commit()
                stats.series_matched.append(f"{ep['name']} S{ep['season']:02d}E{ep['episode']:02d} (upgrade)")
                # ── CLEANUP UPGRADE ────────────────────────────────────────────
                # Se cleanup_upgrades è abilitato, cerca e sposta in trash i file
                # obsoleti (stessa puntata, score inferiore) nell'archive_path.
                _archive_path = ep.get('archive_path', '')
                _is_pack      = ep.get('is_pack', False)
                _ep_range     = ep.get('episode_range', [])
                if _archive_path:
                    try:
                        from .config import Config as _Cfg
                        _cfg_obj    = _Cfg()
                        _cleanup_on = str(_cfg_obj.qbt.get('cleanup_upgrades', 'no')).lower() in ('yes', 'true', '1')
                        _trash      = str(_cfg_obj.qbt.get('trash_path', '')).strip()
                        _min_diff   = int(_cfg_obj.qbt.get('cleanup_min_score_diff', 0) or 0)
                        _action     = str(_cfg_obj.qbt.get('cleanup_action', 'move')).strip().lower()
                        if _cleanup_on and (_trash or _action == 'delete'):
                            from .cleaner import cleanup_old_episode
                            # Per season pack: cicla su tutti gli episodi del range.
                            # Per episodio singolo: usa solo ep['episode'] (comportamento originale).
                            _episodes_to_clean = _ep_range if (_is_pack and _ep_range) else [ep['episode']]
                            _total_removed = 0
                            for _ep_num in _episodes_to_clean:
                                _removed = cleanup_old_episode(
                                    series_name   = ep['name'],
                                    season        = ep['season'],
                                    episode       = _ep_num,
                                    new_score     = new_score,
                                    new_title     = ep.get('title', ''),
                                    archive_path  = _archive_path,
                                    trash_path    = _trash,
                                    min_score_diff = _min_diff,
                                    action        = _action,
                                )
                                _total_removed += _removed
                            if _total_removed > 0:
                                _label = f"Season pack cleanup ({len(_episodes_to_clean)} ep)" if (_is_pack and _ep_range) else "Upgrade cleanup"
                                _verb = "eliminati" if _action == 'delete' else "spostati in trash"
                                logger.info(f"🗑️  {_label}: {_total_removed} obsolete file(s) {_verb}")
                        elif _cleanup_on and not _trash and _action == 'move':
                            logger.warning("⚠️  cleanup_upgrades=yes ma @trash_path non configurato — skip cleanup")
                    except Exception as _ce:
                        logger.warning(f"⚠️  cleanup upgrade error: {_ce}")
                # ──────────────────────────────────────────────────────────────
                return True, "Upgrade"
            else:
                stats.duplicates.append(f"{ep['name']} S{ep['season']:02d}E{ep['episode']:02d}")
                self.record_episode_discard(series_id, ep['season'], ep['episode'], 'existing_better')
                return False, "Existing better"
        else:
            c.execute("""INSERT INTO episodes
                        (series_id, season, episode, title, original_title, quality_score, is_repack, magnet_hash, magnet_link, downloaded_at, archive_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (series_id, ep['season'], ep['episode'], ep['title'], ep['title'], new_score,
                      ep['quality'].is_repack, hash_val, magnet, datetime.now(timezone.utc).isoformat(), archive_path))
            self.conn.commit()
            stats.series_matched.append(f"{ep['name']} S{ep['season']:02d}E{ep['episode']:02d}")
            # ── CLEANUP SEASON PACK (branch New) ──────────────────────────────
            # Se è un season pack, pulisce i file singoli inferiori per TUTTI
            # gli episodi del range, non solo per il primo.
            _is_pack    = ep.get('is_pack', False)
            _ep_range   = ep.get('episode_range', [])
            _archive_path = ep.get('archive_path', '')
            if _is_pack and _ep_range and _archive_path:
                try:
                    from .config import Config as _Cfg
                    _cfg_obj   = _Cfg()
                    _cleanup_on = str(_cfg_obj.qbt.get('cleanup_upgrades', 'no')).lower() in ('yes', 'true', '1')
                    _trash      = str(_cfg_obj.qbt.get('trash_path', '')).strip()
                    _min_diff   = int(_cfg_obj.qbt.get('cleanup_min_score_diff', 0) or 0)
                    _action     = str(_cfg_obj.qbt.get('cleanup_action', 'move')).strip().lower()
                    if _cleanup_on and (_trash or _action == 'delete'):
                        from .cleaner import cleanup_old_episode
                        _total_removed = 0
                        for _ep_num in _ep_range:
                            _removed = cleanup_old_episode(
                                series_name   = ep['name'],
                                season        = ep['season'],
                                episode       = _ep_num,
                                new_score     = new_score,
                                new_title     = ep.get('title', ''),
                                archive_path  = _archive_path,
                                trash_path    = _trash,
                                min_score_diff = _min_diff,
                                action        = _action,
                            )
                            _total_removed += _removed
                        if _total_removed > 0:
                            _verb = "eliminati" if _action == 'delete' else "spostati in trash"
                            logger.info(f"🗑️  Season pack cleanup: {_total_removed} obsolete file(s) {_verb} "
                                        f"({len(_ep_range)} episodi del range)")
                    elif _cleanup_on and not _trash and _action == 'move':
                        logger.warning("⚠️  cleanup_upgrades=yes ma @trash_path non configurato — skip cleanup pack")
                except Exception as _ce:
                    logger.warning(f"⚠️  cleanup season pack error: {_ce}")
            # ──────────────────────────────────────────────────────────────────
            return True, "New"

    def _best_quality_in_path(self, series_name: str, season: int, episode: int,
                               archive_path: str) -> Optional[int]:
        if not archive_path:
            return None
        ap = archive_path.strip()
        try:
            files = []
            if ap.lower().startswith(('http://', 'https://', 'ftp://')):
                logger.debug(f"[ARCHIVE-CHECK] Scansione remoto: {ap}")
                try:
                    req_url = ap
                    auth    = None
                    for cred in ARCHIVE_CREDENTIALS:
                        pref = cred.get('prefix', '')
                        if pref and ap.startswith(pref):
                            if ap.startswith(('http://', 'https://')):
                                auth = (cred.get('user', ''), cred.get('pass', ''))
                            elif ap.startswith('ftp://') and cred.get('user'):
                                pu = urlparse(ap)
                                if not pu.username:
                                    up = f"{cred.get('user','')}{':{}'.format(cred.get('pass','')) if cred.get('pass') else ''}@"
                                    req_url = f"{pu.scheme}://{up}{pu.hostname}{pu.path or ''}"
                            break
                    resp = requests.get(req_url, timeout=10, auth=auth) if auth else requests.get(req_url, timeout=10)
                    if resp.status_code == 200 and resp.text:
                        soup = BeautifulSoup(resp.text, 'html.parser')
                        for a in soup.find_all('a'):
                            href = (a.get('href') or '').strip()
                            text = (a.text or '').strip()
                            name = href or text
                            if name:
                                files.append(name)
                except Exception:
                    return None
            else:
                base = ap
                if ap.lower().startswith('smb://'):
                    try:
                        pu    = urlparse(ap)
                        p     = (pu.path or '/').lstrip('/')
                        parts = p.split('/', 1)
                        share = parts[0] if parts else ''
                        sub   = ('/' + parts[1]) if len(parts) > 1 else ''
                        host  = pu.hostname or ''
                        uid   = os.getuid() if hasattr(os, 'getuid') else 1000
                        candidates = [
                            f"/run/user/{uid}/gvfs/smb-share:server={host},share={share}{sub}",
                            f"/mnt/{share}{sub}",
                            f"/media/{share}{sub}",
                            f"/srv/{host}/{share}{sub}",
                        ]
                        for cand in candidates:
                            if os.path.exists(cand):
                                logger.debug(f"[ARCHIVE-CHECK] SMB normalizzato: {ap} -> {cand}")
                                base = cand
                                break
                    except Exception:
                        pass
                logger.debug(f"[ARCHIVE-CHECK] Scansione filesystem: {base}")
                if not os.path.exists(base):
                    logger.warning(f"[ARCHIVE-CHECK] ⚠️ Path non esistente: {base}")
                    return None
                try:
                    base_parts = os.path.normpath(base).split(os.sep)
                    for root, _, filenames in os.walk(base):
                        depth = len(os.path.normpath(root).split(os.sep)) - len(base_parts)
                        if depth > 3:
                            continue
                        for fn in filenames:
                            files.append(fn)
                except Exception:
                    return None

            if not files:
                logger.debug(f"[ARCHIVE-CHECK] No files found in {ap}")
                return None

            best    = None
            matched = 0
            for fname in files:
                ep_parsed = Parser.parse_series_episode(fname)
                if not ep_parsed:
                    continue
                if ep_parsed.get('season') != int(season) or ep_parsed.get('episode') != int(episode):
                    continue
                f_name = ep_parsed.get('name', '')
                try:
                    from .models import normalize_series_name, _series_name_matches
                    if not _series_name_matches(normalize_series_name(series_name),
                                                normalize_series_name(f_name)):
                        continue
                except Exception:
                    continue
                matched += 1
                q     = ep_parsed.get('quality') or Parser.parse_quality(fname)
                score = q.score() if hasattr(q, 'score') else 0
                if best is None or score > best:
                    best = score
            if matched > 0:
                logger.debug(f"[ARCHIVE-CHECK] ✅ Found {matched} files for '{series_name}' S{int(season):02d}E{int(episode):02d} on disk (Score: {best})")
            else:
                logger.debug(f"[ARCHIVE-CHECK] No match in archive for '{series_name}' (best_score=None)")
            return best
        except Exception:
            return None

    # ------------------------------------------------------------------
    # MOVIE CHECK
    # ------------------------------------------------------------------

    def check_movie(self, mov: dict, magnet: str, quality_req: str):
        from .config import Config

        h = re.search(r'btih:([a-fA-F0-9]{40})', magnet, re.I)
        if not h:
            return False, "No hash"
        hash_val = h.group(1).lower()

        c = self.conn.cursor()
        c.execute("SELECT id FROM movies WHERE magnet_hash = ?", (hash_val,))
        if c.fetchone():
            stats.duplicates.append(f"{mov['config_name']} ({mov['year']})")
            self.record_movie_discard(mov['config_name'], mov['year'], 'duplicate_hash')
            return False, "Duplicate hash"

        min_rank  = Config._min_res_from_qual_req(quality_req)
        max_rank  = Config._max_res_from_qual_req(quality_req)
        this_rank = Config._res_rank_from_title(mov['title'])
        if this_rank < min_rank:
            stats.quality_rejected.append(f"{mov['title'][:60]}... [below minimum]")
            self.record_movie_discard(mov['config_name'], mov['year'], 'below_quality')
            return False, "Below minimum quality"
        if this_rank > max_rank:
            stats.quality_rejected.append(f"{mov['title'][:60]}... [above maximum]")
            self.record_movie_discard(mov['config_name'], mov['year'], 'above_quality')
            return False, "Above maximum quality"

        c.execute("SELECT quality_score FROM movies WHERE name = ? AND year = ?",
                  (mov['config_name'], mov['year']))
        row       = c.fetchone()
        new_score = mov['quality'].score()

        if row:
            if new_score > row['quality_score']:
                c.execute("""UPDATE movies SET quality_score=?, magnet_hash=?,
                            magnet_link=?, title=?, downloaded_at=?
                            WHERE name=? AND year=?""",
                         (new_score, hash_val, magnet, mov['title'],
                          datetime.now(timezone.utc).isoformat(), mov['config_name'], mov['year']))
                self.conn.commit()
                stats.movies_matched.append(f"{mov['config_name']} ({mov['year']}) [upgrade]")
                # ── CLEANUP UPGRADE FILM ───────────────────────────────────────
                _arch = mov.get('archive_path', '')
                if _arch:
                    try:
                        from .config import Config as _Cfg
                        _cfg_obj = _Cfg()
                        _cleanup_on = str(_cfg_obj.qbt.get('cleanup_upgrades', 'no')).lower() in ('yes', 'true', '1')
                        _trash      = str(_cfg_obj.qbt.get('trash_path', '')).strip()
                        _min_diff   = int(_cfg_obj.qbt.get('cleanup_min_score_diff', 0) or 0)
                        if _cleanup_on and _trash:
                            from .cleaner import cleanup_old_movie
                            _removed = cleanup_old_movie(
                                movie_name   = mov['config_name'],
                                movie_year   = mov.get('year', 0),
                                new_score    = new_score,
                                new_title    = mov.get('title', ''),
                                archive_path = _arch,
                                trash_path   = _trash,
                                min_score_diff = _min_diff,
                            )
                            if _removed > 0:
                                logger.info(f"🗑️  Upgrade cleanup movie: {_removed} obsolete file(s) moved to trash")
                    except Exception as _ce:
                        logger.warning(f"⚠️  cleanup upgrade movie error: {_ce}")
                # ──────────────────────────────────────────────────────────────
                return True, "Upgrade"
            else:
                stats.quality_rejected.append(
                    f"{mov['config_name']} ({mov['year']}) [existing better: {row['quality_score']} vs {new_score}]"
                )
                self.record_movie_discard(mov['config_name'], mov['year'], 'existing_better')
                return False, "Existing better"
        else:
            c.execute("""INSERT INTO movies (name, year, title, quality_score, magnet_hash, magnet_link, downloaded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (mov['config_name'], mov['year'], mov['title'], new_score,
                      hash_val, magnet, datetime.now(timezone.utc).isoformat()))
            self.conn.commit()
            stats.movies_matched.append(f"{mov['config_name']} ({mov['year']})")
            return True, "New"

    # ------------------------------------------------------------------
    # TIMEFRAME
    # ------------------------------------------------------------------

    def add_pending(self, series_id, season, episode, title, score, magnet, timeframe):
        c = self.conn.cursor()
        c.execute(
            "SELECT best_quality_score FROM pending_downloads WHERE series_id=? AND season=? AND episode=?",
            (series_id, season, episode)
        )
        row = c.fetchone()
        if row:
            if score > row[0]:
                c.execute("""UPDATE pending_downloads SET best_title=?, best_quality_score=?, best_magnet=?
                            WHERE series_id=? AND season=? AND episode=?""",
                         (title, score, magnet, series_id, season, episode))
                self.conn.commit()
                return 'updated'
            return 'skipped'
        else:
            c.execute("""INSERT INTO pending_downloads
                        (series_id, season, episode, best_title, best_quality_score, best_magnet,
                         first_seen_at, timeframe_hours)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (series_id, season, episode, title, score, magnet,
                      datetime.now(timezone.utc).isoformat(), timeframe))
            self.conn.commit()
            return 'added'

    def get_ready_downloads(self):
        c = self.conn.cursor()
        c.execute("""
            SELECT p.id, p.series_id, p.season, p.episode, p.best_magnet,
                   p.best_quality_score, p.best_title, s.name as series_name
            FROM pending_downloads p
            JOIN series s ON p.series_id = s.id
            WHERE p.status = 'pending' AND
                  datetime(p.first_seen_at, '+' || p.timeframe_hours || ' hours') <= datetime('now')
        """)
        return [dict(row) for row in c.fetchall()]

    def mark_downloaded(self, pending_id: int):
        c = self.conn.cursor()
        c.execute("UPDATE pending_downloads SET status='downloaded', downloaded_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).isoformat(), pending_id))
        self.conn.commit()

    # ------------------------------------------------------------------
    # TVDB METADATA
    # ------------------------------------------------------------------

    def get_tvdb_id(self, series_id: int) -> Optional[int]:
        """Restituisce il tvdb_id salvato per questa serie, se presente."""
        c = self.conn.cursor()
        c.execute("SELECT tvdb_id FROM series_metadata WHERE series_id=? LIMIT 1", (series_id,))
        row = c.fetchone()
        return row['tvdb_id'] if row else None

    def is_tvdb_cache_fresh(self, series_id: int, max_age_days: int = 7) -> bool:
        """True se i metadati TVDB per questa serie sono stati aggiornati
        entro max_age_days giorni."""
        c = self.conn.cursor()
        c.execute("SELECT fetched_at FROM series_metadata WHERE series_id=? LIMIT 1", (series_id,))
        row = c.fetchone()
        if not row:
            return False
        try:
            fetched = datetime.fromisoformat(row['fetched_at'].replace('Z', '+00:00'))
            return (datetime.now(timezone.utc) - fetched) < timedelta(days=max_age_days)
        except Exception:
            return False

    def upsert_series_metadata(self, series_id: int, tmdb_id: int, season_counts: dict):
        """Salva/aggiorna il numero di episodi attesi per stagione.
        season_counts = {1: 10, 2: 13, 3: 8, ...}
        """
        now = datetime.now(timezone.utc).isoformat()
        c   = self.conn.cursor()
        for season, count in season_counts.items():
            c.execute('''
                INSERT INTO series_metadata (series_id, tvdb_id, season, expected_episodes, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(series_id, season) DO UPDATE SET
                    tvdb_id=excluded.tvdb_id,
                    expected_episodes=excluded.expected_episodes,
                    fetched_at=excluded.fetched_at
            ''', (series_id, tmdb_id, season, count, now))
        self.conn.commit()

    def get_expected_episodes(self, series_id: int, season: int) -> Optional[int]:
        """Restituisce il numero di episodi attesi per una stagione, o None
        se non disponibile."""
        c = self.conn.cursor()
        c.execute(
            "SELECT expected_episodes FROM series_metadata WHERE series_id=? AND season=?",
            (series_id, season)
        )
        row = c.fetchone()
        return row['expected_episodes'] if row else None

    # ------------------------------------------------------------------
    # GAP FILLING
    # ------------------------------------------------------------------

    def find_gaps(self, series_id: int, season: int) -> List[int]:
        """Trova episodi mancanti in una stagione, partendo sempre da E01.

        Se TMDB ha il conteggio episodi attesi, l'intervallo è 1..expected.
        Altrimenti l'intervallo è 1..max(posseduti) — cerca tutto dall'inizio
        fino all'ultimo episodio noto. Se la stagione è completamente assente
        dal DB, restituisce [] — sarà il loop in extto3 a gestire le stagioni
        non ancora mai scaricate cercando da E01 senza limite superiore.
        """
        c = self.conn.cursor()
        c.execute(
            "SELECT episode FROM episodes WHERE series_id=? AND season=? ORDER BY episode",
            (series_id, season)
        )
        have = set(r[0] for r in c.fetchall())

        expected = self.get_expected_episodes(series_id, season)
        if expected:
            # TMDB conosce il totale: cerca tutti gli episodi mancanti 1..expected
            return sorted(set(range(1, expected + 1)) - have)
        else:
            # Senza TMDB: cerca da E01 fino all'ultimo episodio posseduto
            if not have:
                return []
            return sorted(set(range(1, max(have) + 1)) - have)

    def get_series_stats(self) -> dict:
        """Ritorna statistiche per serie: episodi scaricati, ultimo episodio, is_ended, is_completed."""
        try:
            c = self.conn.cursor()
            c.execute("""
                SELECT s.id, s.name, s.is_completed, s.is_ended,
                       COUNT(e.id) AS ep_count,
                       MAX(e.season) AS last_season,
                       MAX(e.episode) AS last_episode,
                       MAX(e.downloaded_at) AS last_dl
                FROM series s
                LEFT JOIN episodes e ON e.series_id = s.id
                GROUP BY s.id
            """)
            result = {}
            for row in c.fetchall():
                result[row['name'].lower()] = {
                    'id':           row['id'],
                    'name':         row['name'],
                    'ep_count':     row['ep_count'] or 0,
                    'last_season':  row['last_season'],
                    'last_episode': row['last_episode'],
                    'last_dl':      row['last_dl'],
                    'is_completed': bool(row['is_completed']),
                    'is_ended':     bool(row['is_ended']),
                }
            return result
        except Exception as e:
            import logging; logging.getLogger(__name__).error(f"get_series_stats: {e}")
            return {}


    def get_series_seasons(self, series_id: int):
        c = self.conn.cursor()
        c.execute(
            "SELECT DISTINCT season FROM episodes WHERE series_id=? ORDER BY season DESC",
            (series_id,)
        )
        return [r[0] for r in c.fetchall()]

    def get_all_missing_episodes(self) -> List[Dict]:
        """Restituisce una lista di tutti gli episodi mancanti per tutte le serie abilitate."""
        c = self.conn.cursor()
        # 1. Recupera tutte le serie attive
        c.execute("SELECT id, name FROM series WHERE enabled=1")
        series = c.fetchall()
        
        all_missing = []
        for s in series:
            sid, name = s['id'], s['name']
            
            # 2. Recupera stagioni attese da metadata
            c.execute("SELECT season, expected_episodes FROM series_metadata WHERE series_id=? ORDER BY season ASC", (sid,))
            metadata = c.fetchall()
            
            # 3. Recupera episodi già posseduti
            c.execute("SELECT season, episode FROM episodes WHERE series_id=?", (sid,))
            posseduti = {}
            for row in c.fetchall():
                posseduti.setdefault(row['season'], set()).add(row['episode'])
                
            for m in metadata:
                season = m['season']
                expected = m['expected_episodes']
                have = posseduti.get(season, set())
                
                # Cerca buchi
                for ep in range(1, expected + 1):
                    if ep not in have:
                        all_missing.append({
                            'series_id': sid,
                            'series_name': name,
                            'season': season,
                            'episode': ep
                        })
        return all_missing

    # ------------------------------------------------------------------
    # METRICS HISTORY
    # ------------------------------------------------------------------

    def save_cycle_history(self, payload: dict):
        try:
            c = self.conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            c.execute("INSERT INTO cycle_history (ts, payload) VALUES (?, ?)",
                     (now_iso, json.dumps(payload)))
            
            # --- AUTO-PULIZIA: Previene l'esplosione del DB ---
            # 1. Mantieni solo gli ultimi 100 cicli per i grafici della dashboard
            c.execute("""
                DELETE FROM cycle_history 
                WHERE id NOT IN (SELECT id FROM cycle_history ORDER BY id DESC LIMIT 100)
            """)
            
            # 2. Elimina i log degli "scarti" più vecchi di 7 giorni
            cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            c.execute("DELETE FROM episode_discards WHERE at < ?", (cutoff_7d,))
            c.execute("DELETE FROM movie_discards WHERE at < ?", (cutoff_7d,))
            
            self.conn.commit()
        except Exception:
            pass

    def get_cycle_history(self, limit: int = 10) -> list[dict]:
        c = self.conn.cursor()
        c.execute("SELECT ts, payload FROM cycle_history ORDER BY id DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        out  = []
        for r in rows:
            try:
                p = json.loads(r['payload'])
            except Exception:
                p = {}
            p['ts'] = r['ts']
            out.append(p)
        return out

    # --- STATISTICHE DI CONSUMO ---
    def get_consumption_stats(self) -> Dict:
        """Ritorna statistiche unificate (Serie + Film + Fumetti)."""
        c = self.conn.cursor()
        total_b = 0
        b_30d = 0
        
        # 1. Somma Serie TV e Film
        c.execute("SELECT SUM(size_bytes) FROM episodes")
        total_b += (c.fetchone()[0] or 0)
        c.execute("SELECT SUM(size_bytes) FROM movies")
        total_b += (c.fetchone()[0] or 0)
        
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        c.execute("SELECT SUM(size_bytes) FROM episodes WHERE downloaded_at > ?", (cutoff,))
        b_30d += (c.fetchone()[0] or 0)
        c.execute("SELECT SUM(size_bytes) FROM movies WHERE downloaded_at > ?", (cutoff,))
        b_30d += (c.fetchone()[0] or 0)

        # 2. Somma Fumetti (collegandosi al secondo database)
        try:
            import sqlite3
            db_c_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'comics.db')
            if os.path.exists(db_c_path):
                with sqlite3.connect(db_c_path) as conn_c:
                    cc = conn_c.cursor()
                    cc.execute("SELECT SUM(size_bytes) FROM comics_history")
                    total_b += (cc.fetchone()[0] or 0)
                    cc.execute("SELECT SUM(size_bytes) FROM comics_weekly")
                    total_b += (cc.fetchone()[0] or 0)
                    cc.execute("SELECT SUM(size_bytes) FROM comics_history WHERE sent_at > ?", (cutoff,))
                    b_30d += (cc.fetchone()[0] or 0)
                    cc.execute("SELECT SUM(size_bytes) FROM comics_weekly WHERE sent_at > ?", (cutoff,))
                    b_30d += (cc.fetchone()[0] or 0)
        except Exception: pass

        return {
            'total_gb': round(total_b / (1024**3), 2),
            'last_30_days_gb': round(b_30d / (1024**3), 2)
        }


# ---------------------------------------------------------------------------
# ARCHIVE DB
# ---------------------------------------------------------------------------

class ArchiveDB:
    def __init__(self):
        self.conn = sqlite3.connect(ARCHIVE_FILE)
        self.conn.row_factory = sqlite3.Row
        # PRAGMA bilanciati per NAS (~75k record, 60 MB file):
        # - cache_size: 16 MB — buona hit-rate FTS senza sprechi
        # - mmap_size: 32 MB — copre metà file per letture sequenziali veloci
        #   senza mappare tutto il file (era 256 MB, troppo per un NAS)
        # - temp_store=MEMORY: ORDER BY e GROUP BY veloci (uso temporaneo, non permanente)
        self.conn.execute("PRAGMA cache_size=-16384")   # 16 MB
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA mmap_size=33554432")  # 32 MB
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        c = self.conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT UNIQUE,
                magnet TEXT,
                source TEXT,
                added_at TEXT
            )
        ''')
        # --- FIX FASE 2: Indice per velocizzare l'ordinamento cronologico su 50k+ record ---
        c.execute('CREATE INDEX IF NOT EXISTS idx_archive_added ON archive(added_at DESC)')
        # FTS5: indice full-text su title per ricerche rapide anche su archivi grandi.
        # content='archive' + content_rowid='id' mantiene la FTS sincronizzata
        # automaticamente con la tabella principale tramite i trigger qui sotto.
        c.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts
            USING fts5(title, content=archive, content_rowid=id)
        ''')
        # Trigger per mantenere FTS allineata alle INSERT/UPDATE/DELETE sulla tabella
        c.execute('''
            CREATE TRIGGER IF NOT EXISTS archive_fts_ai
            AFTER INSERT ON archive BEGIN
                INSERT INTO archive_fts(rowid, title) VALUES (new.id, new.title);
            END
        ''')
        c.execute('''
            CREATE TRIGGER IF NOT EXISTS archive_fts_ad
            AFTER DELETE ON archive BEGIN
                INSERT INTO archive_fts(archive_fts, rowid, title)
                VALUES ('delete', old.id, old.title);
            END
        ''')
        c.execute('''
            CREATE TRIGGER IF NOT EXISTS archive_fts_au
            AFTER UPDATE ON archive BEGIN
                INSERT INTO archive_fts(archive_fts, rowid, title)
                VALUES ('delete', old.id, old.title);
                INSERT INTO archive_fts(rowid, title) VALUES (new.id, new.title);
            END
        ''')
        self.conn.commit()
        # Prima esecuzione: popola la FTS se contiene già righe ma la FTS è vuota
        try:
            c.execute("SELECT COUNT(*) FROM archive_fts")
            fts_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM archive")
            tbl_count = c.fetchone()[0]
            if tbl_count > 0 and fts_count == 0:
                logger.info(f"[ArchiveDB] First FTS run: indexing {tbl_count} records...")
                c.execute("INSERT INTO archive_fts(rowid, title) SELECT id, title FROM archive")
                self.conn.commit()
                logger.info("[ArchiveDB] FTS indexing complete.")
        except Exception as _fts_err:
            logger.warning(f"[ArchiveDB] FTS init warning: {_fts_err}")

    def save_batch(self, items: List[Dict]):
        c = self.conn.cursor()
        for item in items:
            c.execute(
                "INSERT OR IGNORE INTO archive (title, magnet, source, added_at) VALUES (?, ?, ?, ?)",
                (item['title'], item['magnet'], item['source'], datetime.now(timezone.utc).isoformat())
            )
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def add_item(self, name: str, magnet: str, size: str = '', source: str = 'manual', category: str = ''):
        try:
            c = self.conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO archive (title, magnet, source, added_at) VALUES (?, ?, ?, ?)",
                (name, magnet, source, datetime.now(timezone.utc).isoformat())
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"❌ Archive write error: {e}")

    def search(self, query: str) -> List[Dict]:
        """Cerca nell'archivio per parole chiave (AND implicito, case-insensitive).
        Ogni parola della query deve comparire nel titolo (LIKE %parola%).
        Coerente con search_archive() di extto_web.py: trova titoli torrent
        con parole extra (anno, qualità, release group, ecc.)."""
        c = self.conn.cursor()
        words = [w for w in query.split() if w]
        if not words:
            return []
        try:
            conditions = " AND ".join(["title LIKE ?" for _ in words])
            params = [f"%{w}%" for w in words]
            c.execute(
                f"SELECT title, magnet, source FROM archive WHERE {conditions} LIMIT 200",
                params
            )
        except Exception:
            c.execute("SELECT title, magnet, source FROM archive WHERE title LIKE ? LIMIT 200",
                      (f"%{query}%",))
        return [{'title': r['title'], 'magnet': r['magnet'], 'source': r['source']}
                for r in c.fetchall()]

    def get_recent(self, limit: int = 1000) -> List[Dict]:
        c = self.conn.cursor()
        c.execute("""
            SELECT title, magnet, source, added_at
            FROM archive
            ORDER BY added_at DESC
            LIMIT ?
        """, (limit,))
        return [{'title': r['title'], 'magnet': r['magnet'],
                 'source': r['source'], 'added_at': r['added_at']}
                for r in c.fetchall()]

    def get_stats(self) -> dict:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM archive")
        total = c.fetchone()[0]
        if total == 0:
            return {'total': 0, 'oldest': None, 'newest': None, 'age_days': 0, 'size_mb': 0}
        c.execute("SELECT MIN(added_at), MAX(added_at) FROM archive")
        oldest, newest = c.fetchone()
        try:
            size_mb = os.path.getsize(ARCHIVE_FILE) / (1024 * 1024)
        except Exception:
            size_mb = 0
        age_days = 0
        if oldest:
            try:
                oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
                age_days  = (datetime.now(timezone.utc) - oldest_dt).days
            except Exception:
                pass
        return {
            'total': total, 'oldest': oldest, 'newest': newest,
            'age_days': age_days, 'size_mb': round(size_mb, 2)
        }

    def cleanup_old(self, max_age_days: int = 365, keep_min: int = 10000) -> dict:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM archive")
        total_before = c.fetchone()[0]
        if total_before == 0:
            return {'deleted': 0, 'kept': 0, 'total_before': 0}
        cutoff     = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff_iso = cutoff.isoformat()
        c.execute("SELECT COUNT(*) FROM archive WHERE added_at < ?", (cutoff_iso,))
        would_delete = c.fetchone()[0]
        would_keep   = total_before - would_delete
        if would_keep < keep_min and total_before > keep_min:
            c.execute("SELECT added_at FROM archive ORDER BY added_at DESC LIMIT 1 OFFSET ?", (keep_min,))
            row = c.fetchone()
            if row:
                cutoff_iso   = row[0]
                c.execute("SELECT COUNT(*) FROM archive WHERE added_at < ?", (cutoff_iso,))
                would_delete = c.fetchone()[0]
        deleted = 0
        if would_delete > 0:
            c.execute("DELETE FROM archive WHERE added_at < ?", (cutoff_iso,))
            deleted = c.rowcount
            self.conn.commit()
            # VACUUM asincrono: su 1M+ record il VACUUM sincrono blocca per secondi.
            # Lo eseguiamo in un thread daemon separato così cleanup_old ritorna subito.
            import threading as _th
            def _bg_vacuum(db_path=ARCHIVE_FILE):
                try:
                    import sqlite3 as _sq
                    vc = _sq.connect(db_path)
                    vc.execute("VACUUM")
                    vc.close()
                    logger.info("[ArchiveDB] VACUUM completed in background")
                except Exception as _ve:
                    logger.debug(f"[ArchiveDB] VACUUM background: {_ve}")
            _th.Thread(target=_bg_vacuum, daemon=True, name='archive-vacuum').start()
        c.execute("SELECT COUNT(*) FROM archive")
        total_after = c.fetchone()[0]
        return {'deleted': deleted, 'kept': total_after, 'total_before': total_before}


# ---------------------------------------------------------------------------
# SMART CACHE
# ---------------------------------------------------------------------------

class SmartCache:
    def __init__(self, file: str = CACHE_FILE):
        self.file = file
        self.data = {}
        self.load()

    def load(self):
        if os.path.exists(self.file):
            try:
                with open(self.file, 'r') as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def save(self):
        import tempfile
        dir_path = os.path.dirname(os.path.abspath(self.file)) or '.'
        fd, tmp = tempfile.mkstemp(dir=dir_path, prefix='.smartcache_', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, val: str):
        self.data[key] = val
