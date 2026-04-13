#!/usr/bin/env python3
"""
EXTTO Web Interface v1.4 - FULL RESTORED (Original Features + Killswitch + Smart Search)
Backend Flask per la gestione via web di EXTTO
"""

from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from flask_cors import CORS
import sqlite3
import os
import json
import re
import time
import requests
import psutil
import socket
import threading  # <--- AGGIUNGI QUESTA RIGA QUI
from urllib.parse import unquote
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from threading import Thread
import queue
# ... resto degli import
try:
    # Riusa parser/quality dell'engine per coerenza di riconoscimento
    from core.models import Parser, Quality
except Exception as e:
    print(f"⚠️  import core.models fallback: {e}")
    Parser = None
    Quality = None
import subprocess
import warnings

try:
    from core.models import Parser, Quality
    from core.constants import PORT as DEFAULT_ENGINE_PORT
    import core.config_db as _cdb
except Exception as e:
    print(f"⚠️  import core.constants fallback: {e}")
    Parser = None
    Quality = None
    DEFAULT_ENGINE_PORT = 8889

def get_engine_port():
    """Legge la porta del motore dal file di configurazione, con salvagente."""
    try:
        cfg = parse_series_config()
        return int(cfg.get('settings', {}).get('engine_port', DEFAULT_ENGINE_PORT))
    except Exception as e:
        logger.debug(f"get_engine_port: {e}")
        return DEFAULT_ENGINE_PORT

app = Flask(__name__)
CORS(app)

RENAME_PROGRESS = {"status": "idle", "current": 0, "total": 0, "msg": ""}
_rename_progress_lock = threading.Lock()
_config_write_lock = threading.Lock()  # Protegge operazioni read-modify-write sulla config

# Configurazione

# --- CONFIGURAZIONE PERCORSI ASSOLUTI ---
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_FILE      = os.path.join(BASE_DIR, "extto_series.db")
ARCHIVE_FILE = os.path.join(BASE_DIR, "extto_archive.db")
CONFIG_FILE  = os.path.join(BASE_DIR, "extto.conf")
SERIES_FILE  = os.path.join(BASE_DIR, "series.txt")
MOVIES_FILE  = os.path.join(BASE_DIR, "movies.txt")
LOG_FILE     = os.path.join(BASE_DIR, "extto.log")
_LEGACY_FILE = os.path.join(BASE_DIR, "series_config.txt")
LANGUAGES_DIR = os.path.join(BASE_DIR, "languages")

def _default_lang() -> str:
    """
    Restituisce la lingua audio di default per nuove serie/film.
    Legge 'default_language' dalla config DB.
    Se non impostata (stringa vuota o assente) restituisce '' = nessun filtro lingua.
    Configurabile dalla pagina Configurazione → Avanzate.
    """
    try:
        v = str(_cdb.get_setting('default_language', '')).strip().lower()
        return v if v else 'ita'
    except Exception:
        return 'ita'

BACKUP_DIR = "backups"

# Queue per log streaming
log_queue = queue.Queue()

# ============================================================================
# DATABASE UTILITIES
# ============================================================================

import threading
import logging

# ---------------------------------------------------------------------------
# LOGGING SETUP — configura root logger + propaga a tutti i sottomoduli
# (core.comics, core.models, ecc.) così i loro logger.info/error appaiono
# ---------------------------------------------------------------------------
_log_formatter = logging.Formatter(
    '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    # stderr — visibile in terminale/journalctl/supervisord
    _sh = logging.StreamHandler()
    _sh.setFormatter(_log_formatter)
    _root_logger.addHandler(_sh)
# File handler aggiunto separatamente (sempre, anche se c'erano già handler)
_log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extto.log')
if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', '') == _log_file_path
           for h in _root_logger.handlers):
    try:
        _fh = logging.FileHandler(_log_file_path, encoding='utf-8')
        _fh.setFormatter(_log_formatter)
        _root_logger.addHandler(_fh)
    except Exception as e:
        print(f"⚠️  RotatingFileHandler setup: {e}")
        pass
# Assicura che tutti i logger figli (core.*) propaghino al root
logging.getLogger('core').setLevel(logging.DEBUG)
logging.getLogger('core').propagate = True
_root_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

class ExtToDB:
    """Gestione database EXTTO - thread-safe via threading.local()

    Ogni thread Flask ottiene la propria connessione SQLite, evitando
    la condivisione di una singola connessione tra thread multipli.
    WAL mode permette letture concorrenti senza bloccare le scritture.
    """

    _local = threading.local()

    def _get_conn(self):
        if not getattr(self._local, 'conn', None):
            if not os.path.exists(DB_FILE):
                self._local.conn = None
                return None
            # Fix Bolla Temporale: aggiunto isolation_level=None
            conn = sqlite3.connect(DB_FILE, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def _get_conn_archive(self):
        """Restituisce la connessione archivio del thread corrente."""
        if not getattr(self._local, 'conn_archive', None):
            if not os.path.exists(ARCHIVE_FILE):
                self._local.conn_archive = None
                return None
            try:
                # Fix Bolla Temporale: aggiunto isolation_level=None
                conn = sqlite3.connect(ARCHIVE_FILE, check_same_thread=False, isolation_level=None)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA synchronous=NORMAL")
                def regexp(expr, item):
                    try:
                        return 1 if re.search(expr, str(item), re.IGNORECASE) else 0
                    except Exception:
                        return 0  # regex malformata, tratta come no-match
                conn.create_function("REGEXP", 2, regexp)
                self._local.conn_archive = conn
            except Exception as e:
                logger.debug(f"conn_archive REGEXP setup: {e}")
                logger.exception("Unable to open Archive DB")
                self._local.conn_archive = None
        return self._local.conn_archive

    @property
    def conn(self):
        return self._get_conn()

    @property
    def conn_archive(self):
        return self._get_conn_archive()

    def __init__(self):
        pass  # Connessione creata al primo uso per thread

    def connect(self):
        """Mantenuto per compatibilita', non fa nulla."""
        pass
    
    def get_stats(self) -> dict:
        """Statistiche generali"""
        if not self.conn:
            return {}
        
        c = self.conn.cursor()
        stats = {}
        
        c.execute("SELECT COUNT(*) FROM series")
        stats['series'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM movies WHERE removed_at IS NULL")
        stats['movies'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM episodes")
        stats['episodes'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM movies WHERE magnet_link IS NOT NULL")
        stats['movie_downloads'] = c.fetchone()[0]
        
        stats['downloads'] = stats['episodes'] + stats['movie_downloads']
        
        # Ultima attività
        c.execute("""
            SELECT MAX(d) FROM (
                SELECT MAX(downloaded_at) as d FROM episodes
                UNION
                SELECT MAX(downloaded_at) as d FROM movies
            )
        """)
        last = c.fetchone()[0]
        stats['last_activity'] = last if last else "N/A"
        
        # Archivio
        if self.conn_archive:
            c_arch = self.conn_archive.cursor()
            c_arch.execute("SELECT COUNT(*) FROM archive")
            stats['archive_size'] = c_arch.fetchone()[0]
        else:
            stats['archive_size'] = 0
        
        return stats
    
    def get_all_series(self) -> List[dict]:
        """Lista di tutte le serie"""
        if not self.conn:
            return []
        
        c = self.conn.cursor()
        c.execute("""
            SELECT s.id, s.name, s.quality_requirement,
            COUNT(e.id) as episodes_count, 
            MAX(e.downloaded_at) as last_download,
            (SELECT season FROM episodes 
             WHERE series_id=s.id 
             ORDER BY downloaded_at DESC LIMIT 1) as last_season,
            (SELECT episode FROM episodes 
             WHERE series_id=s.id 
             ORDER BY downloaded_at DESC LIMIT 1) as last_episode
            FROM series s 
            LEFT JOIN episodes e ON s.id = e.series_id
            GROUP BY s.id 
            ORDER BY s.name COLLATE NOCASE
        """)
        return [dict(row) for row in c.fetchall()]
    
    def get_series_episodes(self, series_id: int) -> List[dict]:
        """Episodi di una serie"""
        if not self.conn:
            return []
        
        c = self.conn.cursor()
        c.execute(
            """
            SELECT e.id, e.season, e.episode, e.title, e.quality_score,
                   e.downloaded_at, e.is_repack, e.magnet_link,
                   CASE WHEN ap.series_id IS NOT NULL THEN 1 ELSE 0 END AS present_in_archive,
                   ap.best_quality_score AS archive_quality_score
            FROM episodes e
            LEFT JOIN episode_archive_presence ap
              ON ap.series_id = e.series_id
             AND ap.season = e.season
             AND ap.episode = e.episode
            WHERE e.series_id = ? AND e.episode > 0
            ORDER BY e.season DESC, e.episode DESC
            """,
            (series_id,)
        )
        return [dict(row) for row in c.fetchall()]
        
        
    
    def get_all_movies(self) -> List[dict]:
        """Lista di tutti i film scaricati/storico"""
        if not self.conn:
            return []
        
        try:
            c = self.conn.cursor()
            c.execute("""
                SELECT m.id, m.name, m.year, m.title,
                CASE WHEN m.magnet_link IS NOT NULL THEN 1 ELSE 0 END as downloaded, 
                m.downloaded_at, m.quality_score
                FROM movies m
                WHERE m.removed_at IS NULL
                ORDER BY m.downloaded_at DESC NULLS LAST, m.name COLLATE NOCASE
            """)
            return [dict(row) for row in c.fetchall()]
        except Exception as e:
            logger.error(f"Error reading movies table (old schema?): {e}")
            # Fallback in caso manchi la colonna magnet_link in vecchi DB
            try:
                c.execute("SELECT * FROM movies ORDER BY id DESC")
                return [dict(row) for row in c.fetchall()]
            except Exception as e:
                logger.debug(f"get_all_movies: {e}")
                return []
    
    def search_archive(self, query: str = "", offset: int = 0, limit: int = 50) -> Tuple[List[dict], int]:
        """Ricerca nell'archivio con supporto filtri avanzati (+/-)"""
        if not self.conn_archive:
            return [], 0
        
        c = self.conn_archive.cursor()
        conditions = []
        params = []
        
        if query:
            query = query.strip()
            
            # OPZIONE 1: Regex pura (se inizia con "rx:")
            if query.startswith("rx:"):
                conditions.append("title REGEXP ?")
                params.append(query[3:].strip())
            
            # OPZIONE 2: Ricerca "Smart" (+/- e Wildcards)
            else:
                keywords = query.split()
                for kw in keywords:
                    exclude = False
                    if kw.startswith('-') and len(kw) > 1:
                        exclude = True
                        kw = kw[1:]
                    elif kw.startswith('+') and len(kw) > 1:
                        kw = kw[1:]
                    
                    if '*' in kw or '?' in kw:
                        clean_kw = kw.replace('*', '%').replace('?', '_')
                        if exclude:
                            conditions.append("title NOT LIKE ?")
                        else:
                            conditions.append("title LIKE ?")
                        params.append(clean_kw)
                    else:
                        if exclude:
                            conditions.append("title NOT LIKE ?")
                        else:
                            conditions.append("title LIKE ?")
                        params.append(f"%{kw}%")
        
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        
        try:
            # 1. Conta totale
            count_sql = f"SELECT COUNT(*) FROM archive {where_clause}"
            c.execute(count_sql, tuple(params))
            total = c.fetchone()[0]
            
            # 2. Prendi risultati paginati
            data_sql = f"""
                SELECT id, title, magnet, added_at 
                FROM archive 
                {where_clause}
                ORDER BY added_at DESC 
                LIMIT ? OFFSET ?
            """
            query_params = tuple(params) + (limit, offset)
            
            c.execute(data_sql, query_params)
            items = [dict(row) for row in c.fetchall()]
            
            return items, total
            
        except Exception as e:
            print(f"Errore ricerca archivio: {e}")
            return [], 0
    
    def delete_episode(self, episode_id: int):
        """Elimina un episodio"""
        if not self.conn:
            return
        c = self.conn.cursor()
        c.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
        self.conn.commit()
    
    def delete_series(self, series_id: int):
        """Elimina una serie e tutti i suoi episodi"""
        if not self.conn:
            return
        c = self.conn.cursor()
        c.execute("DELETE FROM episodes WHERE series_id=?", (series_id,))
        c.execute("DELETE FROM series WHERE id=?", (series_id,))
        self.conn.commit()
    
    def delete_movie(self, movie_id: int):
        """Soft-delete: marca il film come rimosso senza cancellarlo fisicamente,
        così rimane visibile in Ultimi Download con il badge 'Rimosso dall'archivio'."""
        if not self.conn:
            return
        from datetime import datetime, timezone
        c = self.conn.cursor()
        c.execute("UPDATE movies SET removed_at=? WHERE id=?",
                  (datetime.now(timezone.utc).isoformat(), movie_id))
        self.conn.commit()

    def get_series_stats(self) -> dict:
        """Statistiche aggregate per serie: ep scaricati, ultimo episodio, is_ended, is_completed."""
        if not self.conn:
            return {}
        try:
            from core.database import Database
            with Database() as _db:
                return _db.get_series_stats()
        except Exception as e:
            logger.error(f"get_series_stats: {e}")
            return {}

    def record_feed_match(self, series_id: int, season: int, episode: int,
                          title: str, quality_score: int,
                          fail_reason, magnet: str):
        """Delega a core.database.Database.record_feed_match."""
        if not self.conn:
            return
        try:
            from core.database import Database as _CoreDB
            _CoreDB().record_feed_match(series_id, season, episode,
                                        title, quality_score, fail_reason, magnet)
        except Exception as e:
            logger.warning(f"ExtToDB.record_feed_match: {e}")

    def get_feed_matches(self, series_id: int, season: int, episode: int):
        """Delega a core.database.Database.get_feed_matches."""
        try:
            from core.database import Database as _CoreDB
            return _CoreDB().get_feed_matches(series_id, season, episode)
        except Exception as e:
            logger.warning(f"ExtToDB.get_feed_matches: {e}")
            return []

    def record_movie_feed_match(self, movie_id: int, title: str,
                                quality_score: int, lang_bonus: int,
                                fail_reason, magnet: str):
        """Delega a core.database.Database.record_movie_feed_match."""
        if not self.conn:
            return
        try:
            from core.database import Database as _CoreDB
            _CoreDB().record_movie_feed_match(movie_id, title, quality_score,
                                              lang_bonus, fail_reason, magnet)
        except Exception as e:
            logger.warning(f"ExtToDB.record_movie_feed_match: {e}")

    def get_movie_feed_matches(self, movie_name: str):
        """Delega a core.database.Database.get_movie_feed_matches."""
        try:
            from core.database import Database as _CoreDB
            return _CoreDB().get_movie_feed_matches(movie_name)
        except Exception as e:
            logger.warning(f"ExtToDB.get_movie_feed_matches: {e}")
            return []

    def has_movie_feed_matches(self, movie_name: str):
        """Delega a core.database.Database.has_movie_feed_matches."""
        try:
            from core.database import Database as _CoreDB
            return _CoreDB().has_movie_feed_matches(movie_name)
        except Exception as e:
            logger.warning(f"ExtToDB.has_movie_feed_matches: {e}")
            return False

db = ExtToDB()

# ============================================================================
# CONFIG MANAGEMENT
# ============================================================================

def parse_series_config() -> dict:
    """Legge la configurazione da extto_config.db e dalla tabella series del DB operativo.
    Mantiene l'interfaccia originale: restituisce {'settings': {...}, 'series': [...]}.
    """
    settings = _cdb.get_all_settings()
    series   = _load_series_from_db_web()
    return {'settings': settings, 'series': series}


def _load_series_from_db_web() -> list:
    """Carica le serie dalla tabella series del DB operativo."""
    import sqlite3 as _sq
    try:
        with _sq.connect(DB_FILE) as conn:
            conn.row_factory = _sq.Row
            rows = conn.execute(
                """SELECT name, quality_requirement, seasons, language, enabled,
                          archive_path, timeframe, aliases, ignored_seasons,
                          tmdb_id, subtitle, season_subfolders
                   FROM series ORDER BY name"""
            ).fetchall()
        result = []
        for r in rows:
            try:
                aliases = json.loads(r['aliases'] or '[]')
            except Exception:
                aliases = []
            try:
                ignored = json.loads(r['ignored_seasons'] or '[]')
            except Exception:
                ignored = []
            result.append({
                'name':              r['name'],
                'seasons':           r['seasons'] or '1+',
                'quality':           r['quality_requirement'] or 'any',
                'language':          r['language'] or _default_lang(),
                'enabled':           bool(r['enabled']) if r['enabled'] is not None else True,
                'archive_path':      r['archive_path'] or '',
                'timeframe':         int(r['timeframe'] or 0),
                'aliases':           aliases,
                'ignored_seasons':   ignored,
                'tmdb_id':           r['tmdb_id'] or '',
                'subtitle':          r['subtitle'] or '',
                'season_subfolders': bool(r['season_subfolders']) if r['season_subfolders'] is not None else False,
            })
        return result
    except Exception as e:
        logger.warning(f"_load_series_from_db_web: {e}")
        return []


def _save_series_to_db(series_list: list, sync_delete: bool = False) -> None:
    """Salva/aggiorna le serie nel DB operativo (tabella series).
    sync_delete=True: rimuove anche le serie non presenti nella lista.
                      Usare SOLO per operazioni di eliminazione esplicita.
    """
    import sqlite3 as _sq
    conn = _sq.connect(DB_FILE)
    conn.row_factory = _sq.Row
    try:
        for s in series_list:
            aliases_json = json.dumps(s.get('aliases', []), ensure_ascii=False)
            ignored_json = json.dumps(s.get('ignored_seasons', []), ensure_ascii=False)
            conn.execute(
                """INSERT INTO series
                       (name, quality_requirement, seasons, language, enabled,
                        archive_path, timeframe, aliases, ignored_seasons, tmdb_id, subtitle,
                        season_subfolders)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
                       subtitle            = excluded.subtitle,
                       season_subfolders   = excluded.season_subfolders
                """,
                (
                    s['name'],
                    s.get('quality', s.get('qual', 'any')),
                    s.get('seasons', '1+'),
                    s.get('language', s.get('lang', 'ita')),
                    1 if s.get('enabled', True) else 0,
                    s.get('archive_path', ''),
                    int(s.get('timeframe', 0) or 0),
                    aliases_json,
                    ignored_json,
                    str(s.get('tmdb_id', '') or ''),
                    str(s.get('subtitle', '') or ''),
                    1 if s.get('season_subfolders', False) else 0,
                )
            )
        # Rimuove le serie non più presenti (solo se sync_delete=True)
        if sync_delete and series_list:
            names_in_list = [s['name'] for s in series_list]
            placeholders  = ','.join('?' * len(names_in_list))
            conn.execute(
                f"DELETE FROM series WHERE name NOT IN ({placeholders})",
                names_in_list
            )
        conn.commit()
    finally:
        conn.close()


def _parse_series_line_into(line: str, series_list: list):
    """Parsa una riga serie e la appende a series_list."""
    parts = [p.strip() for p in line.split('|')]
    if len(parts) < 5:
        return
    item = {
        'name':     parts[0],
        'seasons':  parts[1],
        'quality':  parts[2],
        'language': parts[3],
        'enabled':  parts[4] == 'yes',
    }
    for ex in parts[5:]:
        ex = ex.strip()
        if not ex:
            continue
        if ex.startswith('timeframe:'):
            m = re.search(r'timeframe:(\d+)h', ex)
            if m:
                try:
                    item['timeframe'] = int(m.group(1))
                except Exception:
                    pass  # timeframe non numerico, ignorato
        elif ex.startswith('alias='):
            item['aliases'] = [a.strip() for a in ex.split('=', 1)[1].split(',') if a.strip()]
        elif ex.startswith('ignored:'):
            item['ignored_seasons'] = [
                int(x.strip()) for x in ex.split(':', 1)[1].split(',')
                if x.strip().isdigit()
            ]
        # AGGIUNGI QUESTE DUE RIGHE:
        elif ex.startswith('tmdb='):
            item['tmdb_id'] = ex.split('=', 1)[1].strip()
        elif ex.startswith('subtitle='):
            item['subtitle'] = ex.split('=', 1)[1].strip()
        # -------------------------
        elif 'archive_path' not in item:
            item['archive_path'] = ex
    series_list.append(item)


def save_series_config(config: dict) -> bool:
    """Salva la configurazione su extto_config.db e sulla tabella series del DB operativo.
    NON scrive più su extto.conf o series.txt (migrazione a DB completata in v40).
    """
    try:
        settings = config.get('settings', {})
        _cdb.set_settings_bulk(settings)
    except Exception as e:
        logger.error(f"save_series_config settings: {e}")
        return False
    try:
        _save_series_to_db(config.get('series', []))
    except Exception as e:
        logger.error(f"save_series_config series: {e}")
        return False
    return True


def _atomic_write(path: str, content: str) -> None:
    """Scrive content su path in modo atomico tramite file temporaneo."""
    import tempfile
    dir_path = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=dir_path, prefix='.extto_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"safe_save_config: {e}")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_extto_conf(config: dict) -> bool:
    """Scrive extto.conf con tutte le impostazioni (senza le serie)."""
    try:
        settings = config.get('settings', {})
        written_keys = set()
        lines = []

        lines.append("# " + "=" * 76)
        lines.append("# EXTTO - Configurazione")
        lines.append("# " + "=" * 76)
        lines.append("")

        groups = [
            ("CLIENT TORRENT: LIBTORRENT (EMBEDDED)", ["libtorrent_"]),
            ("CLIENT ESTERNI: QBITTORRENT, TRANSMISSION, ARIA2",
             ["qbittorrent_", "transmission_", "aria2_"]),
            ("NOTIFICHE: TELEGRAM & EMAIL", ["notify_", "telegram_", "email_"]),
            ("MOTORE: JACKETT, RICERCA E AVANZATE",
             ["jackett_", "prowlarr_", "min_free_space", "gap_filling", "max_age",
              "stop_on_", "debug_", "archive_", "rename_episodes", "rename_format", "move_episodes",
              "jackett_save_to_archive", "prowlarr_save_to_archive", "tmdb_language",
              "backup_dir", "backup_retention", "backup_schedule", "refresh_interval",
              "comics_", "web_port", "engine_port", "cleanup_upgrades", "trash_path", "cleanup_min_score_diff", "trash_retention_days"]),
              ("PUNTEGGI E QUALITÀ (SCORES)", ["score_", "auto_remove_completed"]), # <--- AGGIUNGI QUESTA RIGA
        ]

        for group_name, prefixes in groups:
            # Abbiamo rimosso 'and k in KNOWN_SETTINGS' per permettere il salvataggio
            # dinamico di opzioni nuove inviate dall'interfaccia web.
            keys_to_write = sorted(
                k for k, v in settings.items()
                if k not in written_keys
                and not isinstance(v, list)
                and any(k.startswith(p) for p in prefixes)
            )
            if keys_to_write:
                lines.append(f"# --- {group_name} ---")
                for k in keys_to_write:
                    lines.append(f"@{k} = {settings[k]}")
                lines.append("")
                written_keys.update(keys_to_write)

        # Qualsiasi altra opzione che non rientra nei prefissi precedenti finisce qui,
        # senza essere scartata, grazie alla rimozione di 'KNOWN_SETTINGS'
        leftover = sorted(
            k for k, v in settings.items()
            if k not in written_keys
            and not isinstance(v, list)
        )
        if leftover:
            lines.append("# --- ALTRE IMPOSTAZIONI ---")
            for k in leftover:
                lines.append(f"@{k} = {settings[k]}")
            lines.append("")

        lines.append("# " + "=" * 76)
        lines.append("# SORGENTI, REGOLE E CARTELLE RADICE")
        lines.append("# " + "=" * 76)

        list_keys = [
            ("archive_root", "Cartelle radice dell'archivio (opzionali)"),
            ("archive_cred", "Credenziali per montaggi remoti (PREFIX|user|pass)"),
            ("url",          "URL Sorgenti (ExtTo, Corsaro)"),
            ("blacklist",    "Parole vietate nei titoli (es: cam, ts)"),
            ("wantedlist",   "Parole obbligatorie nei titoli"),
            ("custom_score", "Punteggi Personalizzati (Parola:Punti)"),
        ]
        for key, desc in list_keys:
            vals = settings.get(key, [])
            if isinstance(vals, list) and vals:
                lines.append(f"\n# {desc}")
                for item in vals:
                    lines.append(f"@{key} = {item}")

        lines.append("")
        # NON scrive più su file — configurazione migrata a extto_config.db (v40)
        # _atomic_write(CONFIG_FILE, "\n".join(lines) + "\n")
        return True
    except Exception as e:
        logger.error(f"Error saving extto.conf: {e}")
        return False


def _save_series_list(config: dict) -> bool:
    """Scrive series.txt con il solo elenco delle serie TV."""
    try:
        lines = []
        lines.append("# " + "=" * 76)
        lines.append("# EXTTO - Serie TV monitorate")
        lines.append("# Formato: Nome | Stagioni | Qualità | Lingua | Enabled"
                     " | Path (opzionale) | timeframe:Xh | alias=... | ignored:N,M | tmdb=ID | subtitle=SIGLA")
        lines.append("# " + "=" * 76)
        lines.append("")

        for serie in sorted(config.get('series', []), key=lambda s: s.get('name', '').lower()):
            enabled      = 'yes' if serie.get('enabled', True) else 'no'
            archive_path = serie.get('archive_path', '').strip()
            timeframe    = int(serie.get('timeframe', 0) or 0)
            aliases      = serie.get('aliases', [])
            ignored      = serie.get('ignored_seasons', [])

            line = (f"{serie['name']} | {serie['seasons']} | "
                    f"{serie['quality']} | {serie['language']} | {enabled}")
            if archive_path:
                line += f" | {archive_path}"
            if timeframe > 0:
                line += f" | timeframe:{timeframe}h"
            if aliases:
                line += f" | alias={','.join(aliases)}"
            if ignored:
                line += f" | ignored:{','.join(map(str, ignored))}"
            # AGGIUNGI QUESTE DUE RIGHE:
            if serie.get('tmdb_id'):
                line += f" | tmdb={serie['tmdb_id']}"
            if serie.get('subtitle'):
                line += f" | subtitle={serie['subtitle']}"
            # -------------------------
            lines.append(line)

        lines.append("")
        # NON scrive più su file — serie migrate a extto_series.db (v40)
        # _atomic_write(SERIES_FILE, "\n".join(lines) + "\n")

        # Sync aliases nel DB
        try:
            import sqlite3 as _sqlite3
            _conn = _sqlite3.connect(DB_FILE)
            _c2 = _conn.cursor()
            for serie in config.get('series', []):
                _aliases_json = json.dumps(serie.get('aliases', []), ensure_ascii=False)
                _c2.execute(
                    "UPDATE series SET aliases=? WHERE LOWER(name)=LOWER(?)",
                    (_aliases_json, serie['name'])
                )
            _conn.commit()
        except Exception as _e:
            logger.warning(f"Sync aliases DB: {_e}")
        finally:
            try: _conn.close()
            except Exception: pass

        return True
    except Exception as e:
        logger.error(f"Error saving series.txt: {e}")
        return False


def parse_movies_config() -> List[dict]:
    """Legge i film configurati da extto_config.db."""
    raw = _cdb.get_movies_config()
    return [
        {
            'name':     m['name'],
            'year':     m['year'],
            'quality':  m['quality'],
            'language': m['language'],
            'enabled':  bool(m['enabled']),
            'subtitle': m['subtitle'],
        }
        for m in raw
    ]

def save_movies_config(movies: List[dict]) -> bool:
    """Salva i film in extto_config.db."""
    try:
        _cdb.save_movies_config(movies)
        return True
    except Exception as e:
        logger.error(f"save_movies_config: {e}")
        return False

# ============================================================================
# LOG STREAMING & UTILS
# ============================================================================

def tail_log_file():
    """Tail del file di log per streaming"""
    if not os.path.exists(LOG_FILE):
        return
    
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        # Vai alla fine
        f.seek(0, 2)
        
        while True:
            line = f.readline()
            if line:
                log_queue.put(line)
            else:
                time.sleep(0.5)

# --- MODIFICA: API per interfacce di rete (Killswitch) ---

@app.route('/api/network/interfaces')
def get_network_interfaces():
    """Rileva le interfacce di rete del sistema (Cross-Platform)"""
    interfaces = {}
    try:
        # Usa psutil che funziona su Windows, Mac e Linux!
        net_if_addrs = psutil.net_if_addrs()
        
        for iface_name, addrs in net_if_addrs.items():
            itype = 'Ethernet'
            iface_lower = iface_name.lower()
            
            # Deduzione del tipo dal nome (valido per la maggior parte dei sistemi)
            if iface_lower.startswith(('wl', 'wi-fi', 'airport')): itype = 'WiFi'
            elif iface_lower.startswith(('tun', 'wg', 'ppp', 'utun')): itype = 'VPN'
            elif iface_lower.startswith('lo'): itype = 'Loopback'
            
            ip = 'N/A'
            # Cerca l'indirizzo IPv4 (AF_INET)
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    break
            
            if ip != 'N/A' and itype != 'Loopback': # Opzionale: puoi escludere il Loopback
                interfaces[iface_name] = {'ip': ip, 'type': itype}
                
    except Exception as e:
        logger.exception("Error detecting network interfaces")
        return jsonify({'error': str(e)}), 500
    
    return jsonify({'interfaces': interfaces})

@app.route('/api/manual-search', methods=['GET'])
def manual_search():
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({'success': False, 'error': 'Query vuota'}), 400
            
        from core.engine import Engine
        from core.config import Config
        from core.models import Parser as _Parser
        _parser = Parser or _Parser
        eng = Engine()
        cfg = Config()
        
        # 1. Ricerca Live (Jackett/Scraper esterni)
        live_results = eng.perform_manual_search(query, cfg.urls)
        
        # 2. Ricerca "Smart" nell'Archivio Locale (Supporto +/-)
        archive_items, _ = db.search_archive(query, limit=100)
        archive_results = []
        for row in archive_items:
            # Calcola il quality score dal titolo (evita score=0 fisso che
            # impedisce l'inserimento in episode_feed_matches quando i 4 slot sono pieni)
            try:
                _aq = _Parser.parse_quality(row['title'])
                _ascore = _aq.score() if _aq else 0
            except Exception as e:
                logger.debug(f"parse_quality archive row: {e}")
                _ascore = 0
            archive_results.append({
                'title': row['title'],
                'magnet': row['magnet'],
                'source': 'Archivio Locale',
                'score': _ascore
            })
        
        # 3. Unione e Deduplicazione (evita che lo stesso magnet compaia due volte)
        seen_magnets = set()
        results = []
        
        for item in archive_results + live_results:
            mag = item.get('magnet')
            if mag and mag not in seen_magnets:
                seen_magnets.add(mag)
                # Diamo un'etichetta se proviene dal database
                if 'source' not in item:
                    item['source'] = item.get('uploader') or 'Archivio Locale'
                results.append(item)
        
        # --- FASE 3: VALUTAZIONE SCARTI (BLINDATA) ---
        if _parser:
            for res in results:
                try: # <--- SCUDO: Se un singolo titolo fallisce, non blocca gli altri 254!
                    reasons = []
                    t_lower = res.get('title', '').lower()
                    
                    # 1. Controllo Blacklist
                    for b in _parser.BLACKLIST:
                        if b and b in t_lower:
                            reasons.append(f"Blacklist ({b})")
                    
                    # 2. Controllo Limiti Serie TV
                    ep = _parser.parse_series_episode(res['title'])
                    if ep:
                        match = cfg.find_series_match(ep['name'], ep['season'])
                        if match:
                            qual_req = match.get('qual', match.get('quality', ''))
                            min_rank = cfg._min_res_from_qual_req(qual_req)
                            max_rank = cfg._max_res_from_qual_req(qual_req)
                            this_rank = cfg._res_rank_from_title(res['title'])
                            
                            if this_rank < min_rank: reasons.append(f"Qualità bassa (Richiesta: {qual_req})")
                            if this_rank > max_rank: reasons.append("Qualità oltre limite max")
                            
                            lang_req = match.get('lang', match.get('language', 'ita'))
                            if not cfg._lang_ok(res['title'], lang_req): 
                                reasons.append(f"Manca Lingua ({lang_req})")
                        else:
                            _dl = _default_lang()
                            if not cfg._lang_ok(res['title'], _dl): reasons.append(f"Manca Lingua ({_dl})")
                    else:
                        # 3. Controllo Limiti Film
                        mov = _parser.parse_movie(res['title'])
                        if mov:
                            match = cfg.find_movie_match(mov['name'], mov.get('year', ''))
                            if match:
                                qual_req = match.get('qual', match.get('quality', ''))
                                min_rank = cfg._min_res_from_qual_req(qual_req)
                                this_rank = cfg._res_rank_from_title(res['title'])
                                
                                if this_rank < min_rank: reasons.append(f"Qualità bassa (Richiesta: {qual_req})")
                                
                                lang_req = match.get('lang', match.get('language', 'ita'))
                                if not cfg._lang_ok(res['title'], lang_req): 
                                    reasons.append(f"Manca Lingua ({lang_req})")
                            else:
                                _dl = _default_lang()
                                if not cfg._lang_ok(res['title'], _dl): reasons.append(f"Manca Lingua ({_dl})")
                        else:
                            _dl = _default_lang()
                            if not cfg._lang_ok(res['title'], _dl): reasons.append(f"Manca Lingua ({_dl})")

                    # Applica i punti bonus/malus
                    bonus = cfg.get_custom_score(res['title']) if hasattr(cfg, 'get_custom_score') else 0
                    res['score'] = res.get('score', 0) + bonus
                    
                    res['rejections'] = reasons
                    
                except Exception as item_err:
                    # Registra l'errore del singolo titolo ma continua con gli altri
                    logger.warning(f"Parse error on title '{res.get('title')}': {item_err}")
        # ----------------------------------

        # --- SALVATAGGIO IN episode_feed_matches ---
        # Per ogni risultato che riguarda una serie TV con S/E riconoscibili,
        # registra il match nel DB in modo che compaia nei "Ultimi trovati" e nel popup 📋
        try:
            from core.models import Parser as _ParserFM, normalize_series_name, _series_name_matches
            _pfm = Parser or _ParserFM
            if not _pfm:
                logger.warning("⚠️  feed_match: Parser not available, skip")
            else:
                _c = db.conn.cursor()
                _all_series = _c.execute('SELECT id, name, aliases FROM series').fetchall()
                logger.info(f"📋 feed_match: {len(results)} results to process, {len(_all_series)} series in DB")
                _fm_saved = 0
                _fm_no_ep = 0
                _fm_no_series = 0
                _fm_no_magnet = 0
                for res in results:
                    try:
                        _title = res.get('title', '')
                        ep = _pfm.parse_series_episode(_title)
                        if not ep or not ep.get('season') or not ep.get('episode'):
                            _fm_no_ep += 1
                            logger.debug(f"   feed_match skip (no S/E): '{_title[:70]}'")
                            continue
                        _norm_ep = normalize_series_name(ep['name'])
                        _sid = None
                        for _srow in _all_series:
                            if _series_name_matches(normalize_series_name(_srow['name']), _norm_ep):
                                _sid = _srow['id']
                                break
                            try:
                                _aliases = json.loads(_srow['aliases'] or '[]')
                            except Exception:
                                _aliases = []  # aliases malformati, ignorati
                            if any(_series_name_matches(normalize_series_name(a), _norm_ep) for a in _aliases):
                                _sid = _srow['id']
                                logger.debug(f"   feed_match: alias match for '{ep['name']}' → series id={_srow['id']} ({_srow['name']})")
                                break
                        if not _sid:
                            _fm_no_series += 1
                            logger.debug(f"   feed_match skip (series not found): ep_name='{ep['name']}' norm='{_norm_ep}' | '{_title[:60]}'")
                            continue
                        _magnet = res.get('magnet', '')
                        if not _magnet:
                            _fm_no_magnet += 1
                            logger.debug(f"   feed_match skip (no magnet): '{_title[:70]}'")
                            continue
                        _score = int(res.get('score', 0))
                        _rejections = res.get('rejections', [])
                        if not _rejections:
                            _fail = None
                        elif any('Blacklist' in r for r in _rejections):
                            _fail = 'blacklisted'
                        elif any('Lingua' in r for r in _rejections):
                            _fail = 'lang_mismatch'
                        elif any('bassa' in r for r in _rejections):
                            _fail = 'below_quality'
                        elif any('limite max' in r or 'troppo alta' in r.lower() for r in _rejections):
                            _fail = 'above_quality'
                        else:
                            _fail = 'below_quality'
                        db.record_feed_match(_sid, ep['season'], ep['episode'],
                                             _title, _score, _fail, _magnet)
                        _fm_saved += 1
                        logger.debug(f"   feed_match OK: S{ep['season']:02d}E{ep['episode']:02d} score={_score} fail={_fail} | '{_title[:60]}'")
                    except Exception as _fm_err:
                        logger.warning(f"   feed_match error on '{res.get('title','')[:60]}': {_fm_err}")
                logger.info(f"📋 feed_match: saved={_fm_saved} | no_ep={_fm_no_ep} | series_not_found={_fm_no_series} | no_magnet={_fm_no_magnet}")
        except Exception as _fm_outer_err:
            logger.error(f"❌ feed_match critical error: {_fm_outer_err}")
        return jsonify({'success': True, 'results': results})
        
    except Exception as e:
        # LOG DETTAGLIATO IN CASO DI CRASH TOTALE
        import traceback
        err_details = traceback.format_exc()
        logger.error(f"❌ Critical error in manual_search:\n{err_details}")
        return jsonify({'success': False, 'error': f"Errore server. Controlla i log."}), 500

@app.route('/api/manual-search-ed2k', methods=['GET'])
def manual_search_ed2k():
    """
    Ricerca su aMule/eD2k per una query libera.
    Restituisce i risultati con idx, name, size, sources usabili da download_result.
    """
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({'success': False, 'error': 'Query vuota'}), 400

        import core.config_db as _cdb_ed2k
        _am_en = str(_cdb_ed2k.get_setting('amule_enabled', 'no')).lower() in ('yes', 'true', '1')
        if not _am_en:
            return jsonify({'success': False, 'error': 'aMule non abilitato nelle impostazioni'}), 400

        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()

        with AmuleClient(cfg) as client:
            results = client.search(query, network='global')

        if results is None:
            return jsonify({'success': False, 'error': 'Nessuna risposta da amuled (è in esecuzione?)'}), 503

        results = sorted(results, key=lambda r: r.get('sources', 0), reverse=True)

        return jsonify({'success': True, 'results': results, 'count': len(results)})

    except Exception as e:
        logger.error(f"manual_search_ed2k: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/manual-search-ed2k-download', methods=['POST'])
def manual_search_ed2k_download():
    """
    Avvia il download di un risultato eD2k tramite il suo idx.
    Body JSON: { idx: int }
    """
    try:
        data = request.json or {}
        idx  = data.get('idx')
        if idx is None:
            return jsonify({'success': False, 'error': 'idx mancante'}), 400

        import core.config_db as _cdb_ed2k
        _am_en = str(_cdb_ed2k.get_setting('amule_enabled', 'no')).lower() in ('yes', 'true', '1')
        if not _am_en:
            return jsonify({'success': False, 'error': 'aMule non abilitato'}), 400

        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()

        with AmuleClient(cfg) as client:
            ok = client.download_result(int(idx))

        if ok:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'aMule non ha accettato il download'}), 500

    except Exception as e:
        logger.error(f"manual_search_ed2k_download: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/stream')
def stream_logs():
    """Stream dei log in tempo reale via SSE"""
    def generate():
        # Invia le ultime 50 righe
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines[-50:]:
                    yield f"data: {json.dumps({'line': line})}\n\n"
        
        # Stream nuove righe
        while True:
            try:
                line = log_queue.get(timeout=1)
                yield f"data: {json.dumps({'line': line})}\n\n"
            except GeneratorExit:
                # Il client ha chiuso la connessione: termina senza errori
                return
            except BrokenPipeError:
                return
            except Exception as e:
                logger.debug(f"stream event: {e}")
                # Ping periodico per mantenere vivo lo stream
                yield f"data: {json.dumps({'ping': True})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

# Trigger file per avviare immediatamente un ciclo del motore senza systemd
TRIGGER_FILE = '/tmp/extto_run_now'
TRIGGER_DOMAIN_MAP = {
    'all':    '/tmp/extto_run_now',
    'series': '/tmp/extto_run_series',
    'movies': '/tmp/extto_run_movies',
    'comics': '/tmp/extto_run_comics',
}

@app.route('/api/run-now', methods=['POST'])
def run_now():
    """Endpoint esistente (compatibilità) — forza ciclo completo."""
    try:
        with open(TRIGGER_FILE, 'w') as f:
            f.write(str(time.time()))
        return jsonify({'success': True, 'message': 'Ciclo richiesto (trigger creato)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/run_now', methods=['GET', 'POST'])
def run_now_domain():
    """Forza un ciclo immediato limitato al dominio specificato.
    Parametro: domain = all | series | movies | comics (default: all)
    Usato dai pulsanti Ricontrolla nella dashboard.
    """
    try:
        domain = request.args.get('domain', 'all').lower()
        if domain not in TRIGGER_DOMAIN_MAP:
            domain = 'all'
        tf = TRIGGER_DOMAIN_MAP[domain]
        with open(tf, 'w') as f:
            f.write(str(time.time()))
        logger.info(f"⏭️  Run-now requested via Web UI: domain='{domain}'")
        return jsonify({'ok': True, 'domain': domain, 'trigger': tf})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/config/add_all_from_archive', methods=['POST'])
def add_all_from_archive():
    """Scansiona la radice archivio (@archive_root) e aggiunge al series_config
    tutte le serie mancanti (una riga per cartella), impostando:
    - seasons = *
    - quality = 1080p
    - language = ita
    - enabled = yes
    - archive_path = <archive_root>/<cartella>
    """
    try:
        cfg = parse_series_config()
        settings = cfg.get('settings', {})
        payload = request.get_json(silent=True) or {}
        # Supporta piu' radici: se nel payload e' specificata 'root', usala; altrimenti:
        # - se c'e' una sola @archive_root, usa quella; se piu' di una, richiedi selezione esplicita
        roots = settings.get('archive_root', []) if isinstance(settings.get('archive_root'), list) else ([settings.get('archive_root')] if settings.get('archive_root') else [])
        sel = (payload.get('root') or '').strip()
        if not sel:
            if len(roots) == 1:
                sel = roots[0]
            else:
                return jsonify({'success': False, 'error': 'Specifica la radice con il parametro root o configura una sola @archive_root'}), 400
        root = sel.strip()
        if not os.path.isdir(root):
            return jsonify({'success': False, 'error': f"Percorso non valido: {root}"}), 400

        existing_names_lower = set([s['name'].strip().lower() for s in cfg.get('series', [])])
        added = 0
        try:
            for entry in sorted(os.listdir(root)):
                full = os.path.join(root, entry)
                if not os.path.isdir(full):
                    continue
                name = entry.strip()
                if not name:
                    continue
                if name.lower() in existing_names_lower:
                    continue
                cfg['series'].append({
                    'name': name,
                    'seasons': '*',
                    'quality': '1080p',
                    'language': _default_lang(),
                    'enabled': True,
                    'archive_path': full
                })
                existing_names_lower.add(name.lower())
                added += 1
        except Exception as e:
            return jsonify({'success': False, 'error': f"Errore scansione archivio: {e}"}), 500

        if not save_series_config(cfg):
            return jsonify({'success': False, 'error': 'Salvataggio series_config fallito'}), 500
        return jsonify({'success': True, 'added': added, 'message': f"Aggiunte {added} serie dal percorso archivio"})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/series/<int:series_id>/path', methods=['POST'])
def update_series_archive_path(series_id: int):
    """Aggiorna l'archive_path per una serie in series_config (match per nome)."""
    try:
        data = request.get_json(force=True)
        new_path = (data.get('archive_path') or '').strip()
        if not new_path:
            return jsonify({'success': False, 'error': 'Percorso vuoto'}), 400
        # Prendi il nome serie dal DB
        if not db.conn:
            return jsonify({'success': False, 'error': 'DB non disponibile'}), 500
        c = db.conn.cursor()
        c.execute('SELECT name FROM series WHERE id=?', (series_id,))
        row = c.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Serie non trovata'}), 404
        series_name = row['name'] if isinstance(row, sqlite3.Row) else row[0]
        # Aggiorna in config
        cfg = parse_series_config()
        updated = False
        for s in cfg.get('series', []):
            if s.get('name', '').strip().lower() == series_name.strip().lower():
                s['archive_path'] = new_path
                updated = True
                break
        if not updated:
            return jsonify({'success': False, 'error': 'Serie non presente in series_config'}), 404
        if not save_series_config(cfg):
            return jsonify({'success': False, 'error': 'Salvataggio fallito'}), 500
        return jsonify({'success': True, 'message': 'Percorso aggiornato'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/series/<int:series_id>/update', methods=['POST'])
def update_series_fields(series_id: int):
    """Aggiorna tutti i campi configurabili di una serie direttamente nel DB.
    Evita il roundtrip GET-tutto → modifica → POST-tutto che può perdere dati.
    """
    try:
        data = request.get_json(force=True) or {}
        import sqlite3 as _sq5

        # Leggi riga corrente per merge
        conn5 = _sq5.connect(DB_FILE)
        conn5.row_factory = _sq5.Row
        row = conn5.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        if not row:
            conn5.close()
            return jsonify({'success': False, 'error': 'Serie non trovata'}), 404

        # Merge: usa i valori dal payload, fallback su quelli attuali nel DB
        def _get(key, fallback):
            return data[key] if key in data else fallback

        aliases_raw  = _get('aliases', json.loads(row['aliases'] or '[]'))
        ignored_raw  = _get('ignored_seasons', json.loads(row['ignored_seasons'] or '[]'))
        aliases_json = json.dumps(aliases_raw if isinstance(aliases_raw, list) else [], ensure_ascii=False)
        ignored_json = json.dumps(ignored_raw if isinstance(ignored_raw, list) else [], ensure_ascii=False)

        conn5.execute("""
            UPDATE series SET
                name                = ?,
                quality_requirement = ?,
                seasons             = ?,
                language            = ?,
                enabled             = ?,
                archive_path        = ?,
                timeframe           = ?,
                aliases             = ?,
                ignored_seasons     = ?,
                tmdb_id             = ?,
                subtitle            = ?,
                season_subfolders   = ?
            WHERE id = ?
        """, (
            _get('name',              row['name']),
            _get('quality',           row['quality_requirement'] or 'any'),
            _get('seasons',           row['seasons'] or '1+'),
            _get('language',          row['language'] or 'ita'),
            1 if _get('enabled',      bool(row['enabled'])) else 0,
            _get('archive_path',      row['archive_path'] or ''),
            int(_get('timeframe',     row['timeframe'] or 0) or 0),
            aliases_json,
            ignored_json,
            str(_get('tmdb_id',       row['tmdb_id'] or '') or ''),
            str(_get('subtitle',      row['subtitle'] or '') or ''),
            1 if _get('season_subfolders', bool(row['season_subfolders'] if 'season_subfolders' in row.keys() else 0)) else 0,
            series_id,
        ))
        conn5.commit()
        conn5.close()

        # Se tmdb_id è cambiato, aggiorna anche series_metadata.tvdb_id
        # (usato da get_tmdb_id_for_series e da extto-details come cache)
        new_tmdb_id = _get('tmdb_id', row['tmdb_id'] or '')
        old_tmdb_id = str(row['tmdb_id'] or '')
        if new_tmdb_id and str(new_tmdb_id) != old_tmdb_id:
            try:
                conn6 = _sq5.connect(DB_FILE)
                conn6.execute(
                    "UPDATE series_metadata SET tvdb_id=? WHERE series_id=?",
                    (int(new_tmdb_id), series_id)
                )
                conn6.commit()
                conn6.close()
                logger.info(f"Series #{series_id}: tmdb_id updated {old_tmdb_id} → {new_tmdb_id} (series_metadata)")

                # Invalida cache file TMDB se esiste (file JSON nella cache dir)
                try:
                    import glob as _glob
                    cache_dirs = [
                        os.path.join(BASE_DIR, 'core', 'tmdb_cache'),
                        os.path.join(BASE_DIR, 'tmdb_cache'),
                    ]
                    for cdir in cache_dirs:
                        for f_old in _glob.glob(os.path.join(cdir, f'{old_tmdb_id}*.json')):
                            os.remove(f_old)
                            logger.debug(f"TMDB cache removed: {f_old}")
                except Exception as ce:
                    logger.debug(f"Cache TMDB cleanup: {ce}")
            except Exception as me:
                logger.warning(f"update series_metadata tmdb_id: {me}")

        logger.info(f"Series #{series_id} updated: aliases={aliases_raw}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"update_series_fields: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/series/<int:series_id>/scan-archive', methods=['POST'])
def scan_one_series_archive(series_id: int):
    """Scansiona il percorso archivio: usa le RegEx elastiche e legge il nome delle cartelle."""
    import os
    import re as _re
    from collections import defaultdict

    try:
        if not db.conn:
            return jsonify({'success': False, 'error': 'DB non disponibile'}), 500
        c = db.conn.cursor()
        c.execute('SELECT name FROM series WHERE id=?', (series_id,))
        r = c.fetchone()
        if not r:
            return jsonify({'success': False, 'error': 'Serie non trovata'}), 404
        series_name = r['name'] if isinstance(r, sqlite3.Row) else r[0]
        
        cfg = parse_series_config()
        apath = ''
        for s in cfg.get('series', []):
            if s.get('name', '').strip().lower() == series_name.strip().lower():
                apath = s.get('archive_path', '')
                break
        if not apath:
            return jsonify({'success': False, 'error': 'Percorso archivio non impostato per la serie'}), 400
        
        base = apath
        if not os.path.isdir(base):
            log_maintenance(f"❌ Errore Scansione {series_name}: Il percorso {base} non esiste sul disco!")
            return jsonify({'success': False, 'error': f'Percorso non valido: {base}'}), 400
            
        log_maintenance(f"📂 Avvio scansione per '{series_name}' in: {base}")
        
        found = defaultdict(int)
        video_exts = {'.mkv', '.mp4', '.avi', '.ts', '.m4v'}
        file_count = 0

        for root, _, files in os.walk(base):
            # Leggiamo la Stagione dal nome della cartella genitore!
            folder_season = None
            folder_name = os.path.basename(root)
            folder_match = _re.search(r'(?i)(?:stagione|season|serie|s)\s*[._\-]*0*(\d+)', folder_name)
            if folder_match:
                folder_season = int(folder_match.group(1))

            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in video_exts or 'sample' in fn.lower():
                    continue
                    
                file_count += 1
                sea = epi = 0
                
                # METODO 1: Match Standard Universale (S01E01, 1x01, s01.e01)
                m_std = _re.search(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})|0*(\d{1,2})x0*(\d{1,3})', fn)
                if m_std:
                    if m_std.group(1): # Formato S01E01
                        sea = int(m_std.group(1))
                        epi = int(m_std.group(2))
                    else:              # Formato 1x01
                        sea = int(m_std.group(3))
                        epi = int(m_std.group(4))
                else:
                    # METODO 2: Usa la stagione della cartella
                    if folder_season is not None:
                        m_ep = _re.search(r'(?i)(?:ep|episodio|episode|e)[._\-\s]*0*(\d{1,3})|^0*(\d{1,3})(?:[\s\.\-]|$)', fn)
                        if m_ep:
                            sea = folder_season
                            epi = int(m_ep.group(1) or m_ep.group(2))
                
                if sea > 0 and epi > 0:
                    q = Parser.parse_quality(fn)
                    score = q.score() or 20000 # Score base se non riconosce nulla
                    
                    if score >= found[(sea,epi)]:
                        found[(sea,epi)] = score

        log_maintenance(f"   ↳ Esaminati {file_count} file video. Identificati {len(found)} episodi validi.")

        upserts = 0
        now_iso = datetime.now().isoformat()
        for (sea, epi), best in found.items():
            fake_title = f"{series_name} S{sea:02d}E{epi:02d}"
                
            c.execute('SELECT id, quality_score FROM episodes WHERE series_id=? AND season=? AND episode=?', (series_id, sea, epi))
            row = c.fetchone()
            if row:
                if (row['quality_score'] or 0) <= int(best):
                    c.execute('UPDATE episodes SET title=?, quality_score=?, downloaded_at=? WHERE id=?', (fake_title, int(best), now_iso, row['id']))
                    upserts += 1
            else:
                c.execute('INSERT INTO episodes (series_id, season, episode, title, quality_score, is_repack, magnet_hash, magnet_link, downloaded_at) VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, ?)', (series_id, sea, epi, fake_title, int(best), now_iso))
                upserts += 1
                
            c.execute('''
                INSERT INTO episode_archive_presence (series_id, season, episode, best_quality_score, at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(series_id,season,episode) DO UPDATE SET best_quality_score=excluded.best_quality_score, at=excluded.at
            ''', (series_id, sea, epi, int(best), now_iso))
            
        db.conn.commit()
        log_maintenance(f"✅ Scansione EXTTO Terminata: {upserts} episodi allineati nel database per {series_name}.")
        return jsonify({'success': True, 'updated': upserts, 'message': f'Trovati {upserts} episodi!'})
    except Exception as e:
        log_maintenance(f"❌ Errore critico scansione {series_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
        
# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/')
def index():
    """Home page"""
    return render_template('index.html')

@app.route('/api/license', methods=['GET'])
def get_license():
    """Restituisce il contenuto di LICENSE.txt dalla root del progetto"""
    license_path = os.path.join(BASE_DIR, 'LICENSE.txt')
    if not os.path.exists(license_path):
        return jsonify({'error': 'File LICENSE.txt non trovato'}), 404
    try:
        with open(license_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'content': content})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/rename-progress', methods=['GET'])
def get_rename_progress():
    """Restituisce lo stato di avanzamento della rinomina/preview"""
    return jsonify(RENAME_PROGRESS)    
    
@app.route('/api/log_level', methods=['GET', 'POST'])
def handle_log_level():
    """Legge o imposta il livello di log globale (debug, info, warning, error)"""
    from core.constants import get_log_level, set_log_level
    try:
        if request.method == 'GET':
            # Legge il livello attuale attivo in memoria
            return jsonify({'ok': True, 'level': get_log_level()})
        else:
            # Imposta il nuovo livello
            data = request.json or {}
            new_level = data.get('level', 'info').lower()
            
            # Applica immediatamente alla sessione corrente
            set_log_level(new_level)
            
            # Salva nel file di configurazione per renderlo permanente
            cfg = parse_series_config()
            if 'settings' not in cfg:
                cfg['settings'] = {}
            cfg['settings']['log_level'] = new_level
            save_series_config(cfg)
            
            log_maintenance(f"🔧 Livello Log modificato a: {new_level.upper()}")
            return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500    

@app.route('/api/stats')
def get_stats():
    """Statistiche generali"""
    stats = db.get_stats()
    
    # Aggiungi info consumo GB
    try:
        from core.database import Database as _CoreDB
        with _CoreDB() as _cdb:
            stats["consumption"] = _cdb.get_consumption_stats()
    except Exception as e:
        logger.warning(f"get_consumption_stats: {e}")
        stats['consumption'] = {'total_gb': 0, 'last_30_days_gb': 0, 'last_7_days_gb': 0}

    # Aggiungi info configurazione
    config = parse_series_config()
    stats['series_configured'] = len(config['series'])
    stats['series_enabled'] = sum(1 for s in config['series'] if s['enabled'])
    
    movies = parse_movies_config()
    stats['movies_configured'] = len(movies)
    stats['movies_enabled'] = sum(1 for m in movies if m['enabled'])
    
    # --- NUOVO: Calcolo Spazio Libero sul disco ---
    try:
        import shutil
        import os
        # Controlla lo spazio nella cartella di download, se non la trova usa la directory corrente
        target_path = config.get('settings', {}).get('libtorrent_dir', '/')
        if not os.path.exists(target_path):
            target_path = os.getcwd()
            
        total, used, free = shutil.disk_usage(target_path)
        stats['disk_free_gb'] = round(free / (1024 ** 3), 1)
    except Exception as e:
        stats['disk_free_gb'] = 0
        # --- NUOVO: Statistiche Fumetti ---
    try:
        from core.comics import ComicsDB
        cdb = ComicsDB(os.path.join(BASE_DIR, 'comics.db'))
        comics_list = cdb.get_comics()
        stats['comics_configured'] = len(comics_list)
        # Conta tutti gli elementi nello storico
        history = cdb.get_history(limit=10000)
        stats['comics_downloads'] = len(history)
    except Exception as e:
        logger.debug(f"comics stats: {e}")
        stats['comics_configured'] = 0
        stats['comics_downloads'] = 0
    # ----------------------------------
        
    return jsonify(stats)

@app.route('/api/health')
def get_health():
    """Report salute del sistema"""
    try:
        from core.health import HealthMonitor
        from core.config import Config
        cfg = Config()
        monitor = HealthMonitor(cfg)
        return jsonify(monitor.get_full_report())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/series')
def get_series():
    """Lista serie TV con ordinamento opzionale: ?sort=name|episodes|last&order=asc|desc"""
    series = db.get_all_series()
    sort_by = request.args.get('sort', 'name')
    order   = request.args.get('order', 'asc').lower()
    reverse = (order == 'desc')

    sort_keys = {
        'name':     lambda x: (x.get('name') or '').lower(),
        'episodes': lambda x: x.get('episodes_count') or 0,
        'last':     lambda x: x.get('last_download') or '',
    }
    key_fn = sort_keys.get(sort_by, sort_keys['name'])
    series.sort(key=key_fn, reverse=reverse)
    return jsonify(series)

@app.route('/api/series/<int:series_id>/episodes')
def get_series_episodes(series_id):
    """Episodi di una serie"""
    episodes = db.get_series_episodes(series_id)
    return jsonify(episodes)

@app.route('/api/series/<int:series_id>/feed-matches/<int:season>/<int:episode>')
def get_episode_feed_matches(series_id, season, episode):
    """Restituisce i top-5 feed matches per un episodio."""
    try:
        matches = db.get_feed_matches(series_id, season, episode)
        return jsonify(matches)
    except Exception as e:
        return jsonify([]), 200

@app.route('/api/series/<int:series_id>/last-found')
def get_series_last_found(series_id):
    """Restituisce gli ultimi N match trovati nel feed per una serie (tutti gli episodi)."""
    try:
        limit = int(request.args.get('limit', 5))
        if not db.conn:
            return jsonify([])
        c = db.conn.cursor()
        c.execute('''
            SELECT season, episode, title, quality_score, fail_reason, magnet, found_at
            FROM episode_feed_matches
            WHERE series_id = ?
            ORDER BY found_at DESC
            LIMIT ?
        ''', (series_id, limit))
        rows = [dict(r) for r in c.fetchall()]
        return jsonify(rows)
    except Exception as e:
        logger.warning(f"last-found error: {e}")
        return jsonify([]), 200
    
@app.route('/api/series/completeness', methods=['GET'])
def get_series_completeness():
    """Restituisce istantaneamente dal DB locale quali serie sono marcate come completate dal motore."""
    try:
        c = db.conn.cursor()
        # Se la colonna non esiste ancora (motore non riavviato), fallisce in modo silenzioso
        c.execute('SELECT name FROM series WHERE is_completed=1')
        results = {row['name']: True for row in c.fetchall()}
        return jsonify(results)
    except Exception as e:
        logger.debug(f"get_downloaded_series: {e}")
        return jsonify({})
    
@app.route('/api/series/stats', methods=['GET'])
def get_series_stats():
    """Statistiche aggregate per serie: ep scaricati, ultimo episodio, is_ended, is_completed."""
    try:
        return jsonify(db.get_series_stats())
    except Exception as e:
        return jsonify({'error': str(e)}), 500



def _get_own_port() -> int:
    """Porta su cui sta girando Flask (per chiamate interne)"""
    try:
        cfg = parse_series_config()
        return int(cfg.get('settings', {}).get('web_port', 5000))
    except Exception:
        return 5000


@app.route('/api/series/all-missing', methods=['GET'])
def get_all_missing_episodes():
    """Episodi mancanti: presenti nella config ma con quality_score=0 o assenti nel DB."""
    try:
        import re as _re
        import json as _j
        cfg = parse_series_config()
        c   = db.conn.cursor()
        results = []

        for serie in cfg.get('series', []):
            if not serie.get('enabled', True):
                continue
            c.execute('SELECT id, ignored_seasons FROM series WHERE name=? COLLATE NOCASE', (serie['name'],))
            row = c.fetchone()
            if not row:
                continue
            series_id = row['id']
            try:
                ignored = _j.loads(row['ignored_seasons'] or '[]')
            except Exception:
                ignored = []

            # Espande stagioni richieste (es. "1+", "1-3", "1,2,3")
            seasons_str = str(serie.get('seasons', '1+'))
            requested_seasons = set()
            for part in seasons_str.split(','):
                part = part.strip()
                if part.endswith('+'):
                    start = int(_re.sub(r'\D', '', part) or '1')
                    c.execute('SELECT MAX(season) FROM episodes WHERE series_id=?', (series_id,))
                    max_s = c.fetchone()[0] or start
                    requested_seasons.update(range(start, max(max_s, start) + 1))
                elif '-' in part:
                    a, b = part.split('-', 1)
                    requested_seasons.update(range(int(a), int(b) + 1))
                elif _re.match(r'^\d+$', part):
                    requested_seasons.add(int(part))

            requested_seasons -= set(ignored)
            if not requested_seasons:
                continue

            # Episodi presenti con quality_score > 0
            c.execute(
                'SELECT season, episode FROM episodes WHERE series_id=? AND quality_score > 0',
                (series_id,)
            )
            present = {(r['season'], r['episode']) for r in c.fetchall()}

            for season in sorted(requested_seasons):
                c.execute(
                    'SELECT MAX(episode) as max_ep FROM episodes WHERE series_id=? AND season=?',
                    (series_id, season)
                )
                max_row = c.fetchone()
                max_ep = max_row['max_ep'] if max_row and max_row['max_ep'] else 0
                for ep in range(1, max_ep + 1):
                    if (season, ep) not in present:
                        results.append({
                            'series_id':   series_id,
                            'series_name': serie['name'],
                            'season':      season,
                            'episode':     ep
                        })

        results.sort(key=lambda x: (x['series_name'].lower(), x['season'], x['episode']))
        return jsonify(results)
    except Exception as e:
        logger.error(f'all-missing: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/series/calendar', methods=['GET'])
def get_series_calendar():
    """Prossimi episodi in uscita (30 giorni) per le serie monitorate, via TMDB."""
    try:
        from core.config import Config as _CalCfg
        from core.tmdb import TMDBClient
        from datetime import datetime as _dt, timedelta as _td

        api_key = getattr(_CalCfg(), 'tmdb_api_key', '').strip()
        if not api_key:
            return jsonify([])

        tmdb     = TMDBClient(api_key)
        cfg      = parse_series_config()
        c        = db.conn.cursor()
        today    = _dt.utcnow().date()
        deadline = today + _td(days=30)
        results  = []

        for serie in cfg.get('series', []):
            if not serie.get('enabled', True):
                continue
            c.execute('SELECT id, tmdb_id FROM series WHERE name=? COLLATE NOCASE', (serie['name'],))
            row = c.fetchone()
            if not row:
                continue
            tmdb_id   = str(row['tmdb_id'] or serie.get('tmdb_id', '') or '').strip()
            series_id = row['id']
            if not tmdb_id:
                continue
            try:
                details = tmdb.fetch_series_details(tmdb_id)
                if not details:
                    continue
                next_ep = details.get('next_episode_to_air')
                if not next_ep:
                    continue
                air_str = next_ep.get('air_date', '')
                if not air_str:
                    continue
                air_date = _dt.strptime(air_str, '%Y-%m-%d').date()
                if today <= air_date <= deadline:
                    results.append({
                        'series_id':   series_id,
                        'series_name': serie['name'],
                        'season':      next_ep.get('season_number', 0),
                        'episode':     next_ep.get('episode_number', 0),
                        'name':        next_ep.get('name', ''),
                        'air_date':    air_str
                    })
            except Exception as _ce:
                logger.debug(f'calendar tmdb {tmdb_id}: {_ce}')

        results.sort(key=lambda x: x['air_date'])
        return jsonify(results)
    except Exception as e:
        logger.error(f'calendar: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/scan-all-archives', methods=['POST'])
def scan_all_archives():
    """Scansiona gli archivi NAS di tutte le serie configurate che hanno un archive_path."""
    try:
        import requests as _req
        cfg   = parse_series_config()
        c     = db.conn.cursor()
        port  = _get_own_port()
        total = 0
        errors = []

        for serie in cfg.get('series', []):
            if not serie.get('archive_path', '').strip():
                continue
            c.execute('SELECT id FROM series WHERE name=? COLLATE NOCASE', (serie['name'],))
            row = c.fetchone()
            if not row:
                continue
            sid = row['id']
            try:
                r = _req.post(
                    f'http://127.0.0.1:{port}/api/series/{sid}/scan-archive',
                    timeout=120
                )
                if r.status_code == 200:
                    total += r.json().get('updated', 0)
                else:
                    errors.append(serie['name'])
            except Exception as _se:
                errors.append(f"{serie['name']}: {_se}")

        msg = f'Scansione completa: {total} episodi allineati.'
        if errors:
            msg += f' Errori ({len(errors)}): {", ".join(errors[:5])}'
        log_maintenance(f'📂 scan-all-archives — {msg}')
        return jsonify({'success': True, 'updated': total, 'message': msg, 'errors': errors})
    except Exception as e:
        logger.error(f'scan-all-archives: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/torrents/remove_completed', methods=['POST'])
def remove_completed_torrents():
    """Rimuove FORZATAMENTE tutti i torrent al 100% (anche se in pausa) e quelli finti-completati sul NAS."""
    try:
        import requests, re, os
        from urllib.parse import unquote_plus
        from core.models import normalize_series_name, _series_name_matches

        # 1. Recupera la lista reale dal motore
        resp = requests.get(f'http://127.0.0.1:{get_engine_port()}/api/torrents', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            torrents = data.get('torrents', []) if isinstance(data, dict) else data
            if isinstance(torrents, dict): torrents = list(torrents.values())

            cfg_data = parse_series_config()
            configured_series = cfg_data.get('series', [])

            hashes_to_remove = []
            for t in torrents:
                t_hash = t.get('hash', '')
                t_name = unquote_plus(t.get('name', ''))
                t_prog = float(t.get('progress', 0))
                t_paused = t.get('paused', False)
                
                is_completed = False

                # CASO 1: È già al 100% nel client (Risolve il Weekly Pack in pausa!)
                if t_prog >= 1.0:
                    is_completed = True

                # CASO 3: Torrent "fantasma" — in attesa metadati ma già completato in passato
                # (completed_time > 0 significa che era già finito, ha perso i metadati dopo riavvio)
                elif 'metadat' in str(t.get('state', '')).lower() and int(t.get('completed_time', 0)) > 0:
                    is_completed = True

                # CASO 2: Finti-completati bloccati sullo 0%
                elif t_paused and (t_prog == 0.0 or t.get('total_size', 0) == 0):
                    m_ep = re.search(r'(?i)[.\s_-]*S(\d{1,2})[.\s_-]*E(\d{1,3})', t_name)
                    if m_ep:
                        norm_t_name = normalize_series_name(t_name[:m_ep.start()])
                        s_num, e_num = int(m_ep.group(1)), int(m_ep.group(2))
                        
                        target_path = next((s.get('archive_path', '').strip() for s in configured_series if _series_name_matches(normalize_series_name(s.get('name', '')), norm_t_name)), None)
                        if target_path and os.path.isdir(target_path):
                            found = False
                            try:
                                for root, _, files in os.walk(target_path):
                                    if root[len(target_path):].count(os.sep) > 2: continue
                                    for f in files:
                                        if not f.lower().endswith(('.mkv', '.mp4', '.avi', '.ts')): continue
                                        if re.search(rf'(?i)s0*{s_num}[._\-\s]*e0*{e_num}', f):
                                            is_completed = True
                                            found = True; break
                                    if found: break
                            except: pass
                    else:
                        # Controllo fisico per fumetti e film
                        final_dir = cfg_data.get('settings', {}).get('libtorrent_dir', '').strip()
                        if t_name and t_name != 'Tutto' and final_dir and os.path.exists(final_dir):
                            clean_tname = re.sub(r'[._-]+', ' ', t_name).strip().lower()
                            if clean_tname:
                                try:
                                    for f_name in os.listdir(final_dir):
                                        clean_fname = re.sub(r'[._-]+', ' ', f_name).strip().lower()
                                        if clean_tname in clean_fname or clean_fname in clean_tname:
                                            is_completed = True
                                            break
                                except Exception as e:
                                    logger.debug(f"is_completed scan disco: {e}")
                                    pass

                if is_completed:
                    hashes_to_remove.append(t_hash)
                    total_b = t.get('total_size', 0)
                    if total_b > 0:
                        try:
                            # 1. Fumetti
                            from core.comics import ComicsDB
                            cdb = ComicsDB()
                            cdb.conn.execute("UPDATE comics_history SET size_bytes=? WHERE title LIKE ? AND size_bytes=0", (total_b, f"%{t_name}%"))
                            cdb.conn.execute("UPDATE comics_weekly SET size_bytes=? WHERE magnet LIKE ? AND size_bytes=0", (total_b, f"%{t_hash}%"))
                            cdb.conn.commit()
                            # 2. Serie TV e Film (extto_series.db)
                            db.conn.execute("UPDATE episodes SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (total_b, t_hash))
                            db.conn.execute("UPDATE movies SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (total_b, t_hash))
                            db.conn.commit()
                        except Exception as e:
                            logger.warning(f"size_bytes update: {e}")

            # 3. Elimina fisicamente i torrent dalla lista (senza cancellare i file su disco!)
            for h in set(hashes_to_remove):
                try:
                    requests.post(f'http://127.0.0.1:{get_engine_port()}/api/torrents/remove', json={'hash': h, 'delete_files': False}, timeout=5)
                except: pass

        # 4. Esegue la pulizia standard come fallback
        resp_clean = requests.post(f'http://127.0.0.1:{get_engine_port()}/api/torrents/remove_completed', timeout=10)
        return jsonify(resp_clean.json())
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/http-downloads/remove-completed', methods=['POST'])
def remove_completed_http_downloads():
    """Rimuove da ACTIVE_HTTP_DOWNLOADS le voci in stato Terminato, Errore o simili."""
    try:
        from core.comics import ACTIVE_HTTP_DOWNLOADS
        # Stati terminali reali da comics.py:
        #   HTTP  → 'Finished'  |  Mega → 'Terminato'  |  Errori → 'Errore' o 'Errore: ...'
        terminal_states = {'terminato', 'errore', 'salvato', 'completato',
                           'finished', 'saved', 'done', 'error'}
        keys_to_remove = [
            k for k, v in list(ACTIVE_HTTP_DOWNLOADS.items())
            if str(v.get('state', '')).lower().split(':')[0].strip() in terminal_states
        ]
        for k in keys_to_remove:
            ACTIVE_HTTP_DOWNLOADS.pop(k, None)
        return jsonify({'removed': len(keys_to_remove), 'keys': keys_to_remove})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/http-downloads/remove', methods=['POST'])
def remove_single_http_download():
    """Rimuove una singola voce da ACTIVE_HTTP_DOWNLOADS dato il suo dl_id (hash fittizio)."""
    try:
        from core.comics import ACTIVE_HTTP_DOWNLOADS
        data   = request.get_json(force=True) or {}
        dl_key = data.get('hash', '').strip()
        if not dl_key:
            return jsonify({'ok': False, 'error': 'hash mancante'}), 400
        if dl_key in ACTIVE_HTTP_DOWNLOADS:
            ACTIVE_HTTP_DOWNLOADS.pop(dl_key, None)
            return jsonify({'ok': True, 'removed': dl_key})
        return jsonify({'ok': False, 'error': 'chiave non trovata'}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# BACKUP
# ──────────────────────────────────────────────────────────────────────────────
import zipfile, glob, fnmatch

def _load_backup_settings() -> dict:
    """Legge impostazioni backup da extto.conf (via parse_series_config).
    Fallback ai valori di default se non configurati.
    """
    defaults = {
        'backup_dir':       os.path.join(BASE_DIR, 'backups'),
        'retention':        5,
        'schedule':         'manual',   # manual | daily | weekly
    }
    try:
        cfg = parse_series_config()
        s = cfg.get('settings', {})
        if s.get('backup_dir'):       defaults['backup_dir']   = s['backup_dir']
        if s.get('backup_retention'): defaults['retention']    = int(s['backup_retention'])
        if s.get('backup_schedule'):  defaults['schedule']     = s['backup_schedule']
    except Exception as e:
        logger.debug(f"backup defaults: {e}")
        pass
    return defaults

@app.route('/api/backup/settings', methods=['GET', 'POST'])
def backup_settings():
    if request.method == 'GET':
        defaults = _load_backup_settings()
        # Aggiungi default per cloud e Telegram
        cfg_full = parse_series_config()
        s = cfg_full.get('settings', {})
        defaults['cloud_type']     = s.get('backup_cloud_type', 'none') # none | ftp
        defaults['cloud_host']     = s.get('backup_cloud_host', '')
        defaults['cloud_user']     = s.get('backup_cloud_user', '')
        defaults['cloud_pass']     = s.get('backup_cloud_pass', '')
        defaults['cloud_path']     = s.get('backup_cloud_path', '/')
        defaults['send_telegram']  = str(s.get('backup_send_telegram', 'false')).lower() in ('true', '1', 'yes')
        return jsonify(defaults)
    try:
        data = request.json or {}
        cfg_full = parse_series_config()
        s = cfg_full.setdefault('settings', {})
        if 'backup_dir'  in data: s['backup_dir']       = data['backup_dir'].strip()
        if 'retention'   in data: s['backup_retention'] = str(int(data['retention']))
        if 'schedule'    in data: s['backup_schedule']  = data['schedule']
        
        # Cloud settings
        if 'cloud_type' in data: s['backup_cloud_type'] = data['cloud_type']
        if 'cloud_host' in data: s['backup_cloud_host'] = data['cloud_host']
        if 'cloud_user' in data: s['backup_cloud_user'] = data['cloud_user']
        if 'cloud_pass' in data: s['backup_cloud_pass'] = data['cloud_pass']
        if 'cloud_path' in data: s['backup_cloud_path'] = data['cloud_path']
        if 'send_telegram' in data: s['backup_send_telegram'] = 'true' if data['send_telegram'] else 'false'
        
        save_series_config(cfg_full)
        import logging as _l; _l.getLogger('extto.backup').info(f"💾 Backup settings salvati nel Database")
        return jsonify({'success': True})
    except Exception as e:
        import logging as _l; _l.getLogger('extto.backup').error(f"❌ Errore salvataggio backup settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
@app.route('/api/scores/settings', methods=['GET', 'POST'])
def scores_settings():
    try:
        cfg_data = parse_series_config()
        s = cfg_data.get('settings', {})

        if request.method == 'POST':
            data = request.json or {}
            
            # 1. Salva i valori singoli (Bonus e Opzioni)
            for key in ['bonus_dv', 'bonus_real', 'bonus_proper', 'bonus_repack', 'auto_remove_completed']:
                if key in data:
                    if key == 'auto_remove_completed':
                        s[key] = 'yes' if data[key] else 'no'
                    else:
                        s[f'score_{key}'] = str(data[key])
            
            # 2. Salva le tabelle dinamiche (Risoluzione, Sorgente, Codec, Audio, Gruppi)
            for map_name in ['res_pref', 'source_pref', 'codec_pref', 'audio_pref', 'group_pref']:
                if map_name in data:
                    prefix = map_name.split('_')[0]
                    for k, v in data[map_name].items():
                        s[f'score_{prefix}_{k}'] = str(v)
                        
            save_series_config(cfg_data)
            return jsonify({'success': True})

        # LOGICA DINAMICA: Prende tutto ciò che esiste nel file
        def _extract_map(prefix):
            result = {}
            full_prefix = f"score_{prefix}_"
            for k, v in s.items():
                if k.startswith(full_prefix):
                    clean_key = k.replace(full_prefix, "").strip()
                    try: result[clean_key] = int(float(str(v).strip()))
                    except: result[clean_key] = 0
            return result

        # Se una mappa è vuota (es. primo avvio), mette dei default minimi per non lasciare i riquadri vuoti
        res_map = _extract_map('res') or {'2160p':1000, '1080p':500, '720p':250}
        src_map = _extract_map('source') or {'bluray':300, 'webdl':200}
        
        data = {
            'res_pref':    res_map,
            'source_pref': src_map,
            'codec_pref':  _extract_map('codec'),
            'audio_pref':  _extract_map('audio'),
            'group_pref':  _extract_map('group'),
            'bonus_dv': int(float(str(s.get('score_bonus_dv', 300)))),
            'bonus_real': int(float(str(s.get('score_bonus_real', 100)))),
            'bonus_proper': int(float(str(s.get('score_bonus_proper', 75)))),
            'bonus_repack': int(float(str(s.get('score_bonus_repack', 50)))),
            'auto_remove_completed': str(s.get('auto_remove_completed', 'no')).lower() in ('yes', 'true', '1')
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backup/run', methods=['POST'])
def run_backup():
    """Crea uno ZIP completo di tutto EXTTO nella cartella configurata."""
    import shutil
    import logging as _logging
    _blog = _logging.getLogger('extto.backup')
    try:
        cfg = _load_backup_settings()
        backup_dir = cfg['backup_dir'].strip()
        retention  = int(cfg.get('retention', 5))
        os.makedirs(backup_dir, exist_ok=True)

        ts       = datetime.now().strftime('%Y-%m-%d--%H-%M-%S')
        zip_name = f'extto-backup-{ts}.zip'
        zip_path = os.path.join(backup_dir, zip_name)

        EXCLUDE_PATTERNS = [
            '__pycache__', '*.pyc', '*.pyo',
            'backups/', '*.log', '.git/', '.venv/',
            'extto_torrents_state/',   # dati resume libtorrent: non portabili
        ]

        def _should_exclude(rel_path: str) -> bool:
            rel_norm = rel_path.replace(os.sep, '/')
            for pat in EXCLUDE_PATTERNS:
                if pat.endswith('/'):
                    if ('/' + pat.rstrip('/') + '/') in ('/' + rel_norm) or rel_norm.startswith(pat.rstrip('/') + '/'):
                        return True
                elif fnmatch.fnmatch(os.path.basename(rel_norm), pat):
                    return True
            return False

        file_count = 0
        total_size = 0

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for root, dirs, files in os.walk(BASE_DIR):
                # Esclude cartelle in-place
                dirs[:] = [d for d in dirs if not _should_exclude(os.path.relpath(os.path.join(root, d), BASE_DIR))]
                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel   = os.path.relpath(fpath, BASE_DIR)
                    if _should_exclude(rel):
                        continue
                    zf.write(fpath, arcname=rel)
                    file_count += 1
                    total_size += os.path.getsize(fpath)

        zip_size = os.path.getsize(zip_path)

        # --- CLOUD BACKUP (FTP) ---
        cloud_info = ""
        try:
            cfg_full = parse_series_config()
            s = cfg_full.get('settings', {})
            ctype = s.get('backup_cloud_type', 'none')
            if ctype == 'ftp':
                host = s.get('backup_cloud_host')
                user = s.get('backup_cloud_user')
                pw   = s.get('backup_cloud_pass')
                rem  = s.get('backup_cloud_path', '/')
                if host and user and pw:
                    _blog.info(f"☁️ Caricamento backup su FTP: {host}...")
                    import ftplib
                    with ftplib.FTP(host) as ftp:
                        ftp.login(user, pw)
                        # Assicurati che la cartella esista (semplificato)
                        try: ftp.cwd(rem)
                        except: pass
                        with open(zip_path, 'rb') as f:
                            ftp.storbinary(f'STOR {zip_name}', f)
                    cloud_info = f" (Caricato su FTP: {host})"
                    _blog.info(f"✅ Cloud backup completato!")
            elif ctype == 'dropbox':
                token = s.get('backup_cloud_pass') # Usiamo il campo pass per il token
                path  = s.get('backup_cloud_path', '/')
                if token:
                    _blog.info("☁️ Caricamento backup su Dropbox...")
                    with open(zip_path, 'rb') as f:
                        cloud_path = (path.rstrip('/') + '/' + zip_name).lstrip('/')
                        if not cloud_path.startswith('/'): cloud_path = '/' + cloud_path
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "Dropbox-API-Arg": json.dumps({"path": cloud_path, "mode": "overwrite"}),
                            "Content-Type": "application/octet-stream"
                        }
                        r = requests.post("https://content.dropboxapi.com/2/files/upload", headers=headers, data=f, timeout=600)
                        r.raise_for_status()
                    cloud_info = " (Caricato su Dropbox)"
                    _blog.info(f"✅ Cloud backup completato!")
        except Exception as ce:
            _blog.warning(f"⚠️ Errore Cloud Backup: {ce}")
            cloud_info = f" (Errore Cloud: {ce})"

        # Retention: tieni solo gli ultimi N backup
        existing = sorted(glob.glob(os.path.join(backup_dir, 'extto-backup-*.zip')))
        while len(existing) > retention:
            old = existing.pop(0)
            try:
                os.remove(old)
            except Exception as e:
                logger.debug(f"remove old backup: {e}")
                pass

        kept = len(glob.glob(os.path.join(backup_dir, 'extto-backup-*.zip')))
        tg_sent = False
        try:
            cfg_full = parse_series_config().get('settings', {})
            send_tg  = str(cfg_full.get('backup_send_telegram', 'false')).lower() in ('true', '1', 'yes')
            if send_tg:
                from core.notifier import Notifier as _Notifier
                _n = _Notifier(cfg_full)
                if _n.tg_enabled:
                    _n.notify_backup_complete(zip_name, round(zip_size/1024**2, 1), file_count, kept, cloud_info)
                    tg_sent = True
                else:
                    logger.debug("backup notify: Telegram abilitato nelle impostazioni backup ma token/chat_id mancanti")
        except Exception as _ne:
            logger.debug(f"backup notify: {_ne}")
        tg_str = " 📨 Telegram ✓" if tg_sent else ""
        _blog.info(f"✅ Backup completato: {zip_name} ({round(zip_size/1024**2,1)} MB, {file_count} file, {kept} backup conservati){tg_str}")
        return jsonify({
            'success':    True,
            'filename':   zip_name,
            'path':       zip_path,
            'files':      file_count,
            'source_mb':  round(total_size / 1024**2, 1),
            'zip_mb':     round(zip_size / 1024**2, 1),
            'kept':       kept,
        })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _blog.error(f"❌ Errore backup: {e}\n{tb}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backup/list', methods=['GET'])
def list_backups():
    """Lista i backup esistenti nella cartella configurata."""
    try:
        cfg  = _load_backup_settings()
        bdir = cfg['backup_dir'].strip()
        files = sorted(glob.glob(os.path.join(bdir, 'extto-backup-*.zip')), reverse=True)
        result = []
        for fp in files:
            stat = os.stat(fp)
            result.append({
                'name':     os.path.basename(fp),
                'path':     fp,
                'size_mb':  round(stat.st_size / 1024**2, 1),
                'date':     datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M'),
            })
        return jsonify({'backups': result, 'dir': bdir})
    except Exception as e:
        return jsonify({'backups': [], 'dir': '', 'error': str(e)})

@app.route('/api/backup/send-telegram', methods=['POST'])
def send_backup_telegram():
    """Invia un file ZIP di backup al gruppo Telegram configurato."""
    try:
        data = request.get_json(silent=True) or {}
        file_path = data.get('path', '').strip()
        
        if not file_path or not os.path.exists(file_path):
            return jsonify({'success': False, 'error': 'File di backup non trovato sul disco.'})

        # Telegram Bot API ha un limite rigoroso di 50 MB per i file in upload
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb > 49.9:
            return jsonify({'success': False, 'error': f'File troppo grande ({file_size_mb:.1f} MB). Il limite dei bot Telegram è 50 MB.'})

        # Leggi configurazione Telegram
        cfg = parse_series_config()
        settings = cfg.get('settings', {})
        token = str(settings.get('telegram_bot_token', '')).strip()
        chat_id = str(settings.get('telegram_chat_id', '')).strip()
        
        if not token or not chat_id:
            return jsonify({'success': False, 'error': 'Token Bot o Chat ID di Telegram non configurati nelle impostazioni.'})

        # Invia il file usando le API ufficiali di Telegram
        import requests
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        
        log_maintenance(f"📤 Avvio upload del backup {os.path.basename(file_path)} su Telegram ({file_size_mb:.1f} MB)...")
        
        with open(file_path, 'rb') as doc:
            files = {'document': (os.path.basename(file_path), doc)}
            payload = {'chat_id': chat_id, 'caption': '📦 *Backup Manuale EXTTO*\nEcco l\'archivio richiesto.', 'parse_mode': 'Markdown'}
            # Usiamo un timeout lungo perché l'upload di 40MB può richiedere tempo
            r = requests.post(url, data=payload, files=files, timeout=120)
        
        if r.status_code == 200:
            log_maintenance("✅ Backup inviato con successo su Telegram.")
            return jsonify({'success': True, 'message': 'Backup inviato su Telegram con successo!'})
        else:
            err_desc = r.json().get('description', 'Errore sconosciuto')
            log_maintenance(f"❌ Errore API Telegram durante l'upload: {err_desc}")
            return jsonify({'success': False, 'error': f'Errore API Telegram: {err_desc}'})
            
    except Exception as e:
        log_maintenance(f"❌ Errore interno upload Telegram: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/series/<int:series_id>/extto-details')
def get_extto_details(series_id):
    """Genera i dettagli completi per la vista stile EXTTO (TMDB + NAS + DB)."""
    try:
        # 1. Recupera la serie dal DB e dalla Configurazione
        c = db.conn.cursor()
        # Legge nome E tmdb_id direttamente dal DB operativo (fonte più affidabile)
        c.execute('SELECT name, tmdb_id, archive_path, ignored_seasons FROM series WHERE id=?', (series_id,))
        row = c.fetchone()
        if not row: return jsonify({'error': 'Serie non trovata'}), 404
        series_name   = row['name']
        db_tmdb_id    = str(row['tmdb_id']).strip() if row['tmdb_id'] else ''
        db_archive    = row['archive_path'] or ''
        try:
            import json as _json_ig
            db_ignored = _json_ig.loads(row['ignored_seasons'] or '[]')
        except Exception:
            db_ignored = []

        cfg = parse_series_config()
        from core.models import normalize_series_name as _nsn_cfg
        _norm_sname = _nsn_cfg(series_name)
        serie_cfg = next((s for s in cfg['series'] if _nsn_cfg(s['name']) == _norm_sname), {})
        # Priorità: DB operativo > config (il DB è aggiornato da /update, la config può essere stale)
        archive_path    = db_archive or serie_cfg.get('archive_path', '')
        ignored_seasons = db_ignored or serie_cfg.get('ignored_seasons', [])

        # 2. Richiama TMDB per la grafica (leggerissimo grazie alla cache)
        from core.config import Config as CoreConfig
        from core.tmdb import TMDBClient
        core_cfg = CoreConfig()
        api_key = getattr(core_cfg, 'tmdb_api_key', '').strip()
        
        meta = {'poster': '', 'backdrop': '', 'overview': 'Nessuna trama disponibile.', 'year': '', 'network': ''}
        tmdb_seasons = {}
        
        if api_key:
            tmdb = TMDBClient(api_key)
            # MODIFICA QUESTA RIGA: PRIMA CERCA IN CONFIG, POI NEL DB, INFINE PER NOME
            # Priorità: 1) DB operativo (series.tmdb_id) 2) config 3) series_metadata 4) ricerca per nome
            tmdb_id = db_tmdb_id or serie_cfg.get('tmdb_id') or tmdb.get_tmdb_id_for_series(db, series_name) or tmdb.resolve_series_id(series_name)
            if tmdb_id:
                meta['tmdb_id'] = tmdb_id
                details = tmdb.fetch_series_details(tmdb_id)
                if details:
                    meta['poster'] = details.get('poster_path', '')
                    meta['backdrop'] = details.get('backdrop_path', '')
                    meta['overview'] = details.get('overview', meta['overview'])
                    meta['year'] = details.get('first_air_date', '')[:4]
                    networks = details.get('networks', [])
                    meta['network'] = networks[0]['name'] if networks else ''
                    meta['status'] = details.get('status', '') # <-- Aggiunto per sapere se è 'Ended'
                    
                    for s in details.get('seasons', []):
                        if s['season_number'] > 0:
                            tmdb_seasons[s['season_number']] = s['episode_count']

        # 3. Legge il Database Locale per capire cosa abbiamo "cercato/trovato"
        episodes_db = db.get_series_episodes(series_id)
        
        # 4. Legge il NAS (se esiste) per calcolare il peso in GB e NOME dei file
        import os, re
        file_sizes = {}
        file_names = {} # <--- NUOVO: Raccoglitore nomi file
        video_exts = {'.mkv', '.mp4', '.avi', '.ts', '.m4v'} # <-- SCUDO ANTI IMMAGINI/SUB
        
        
        if archive_path and os.path.exists(archive_path):
            for root, _, files in os.walk(archive_path):
                folder_season = None
                folder_name = os.path.basename(root)
                folder_match = re.search(r'(?i)(?:stagione|season|serie|s)\s*[._\-]*0*(\d+)', folder_name)
                if folder_match:
                    folder_season = int(folder_match.group(1))

                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in video_exts or 'sample' in f.lower():
                        continue # IGNORA SUB, JPG E SAMPLE
                        
                    s_num = e_num = 0
                    m_std = re.search(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})|0*(\d{1,2})x0*(\d{1,3})', f)
                    if m_std:
                        s_num = int(m_std.group(1) or m_std.group(3))
                        e_num = int(m_std.group(2) or m_std.group(4))
                    elif folder_season is not None:
                        m_ep = re.search(r'(?i)(?:ep|episodio|episode|e)[._\-\s]*0*(\d{1,3})|^0*(\d{1,3})(?:[\s\.\-]|$)', f)
                        if m_ep:
                            s_num = folder_season
                            e_num = int(m_ep.group(1) or m_ep.group(2))
                    
                    if s_num > 0 and e_num > 0:
                        file_path = os.path.join(root, f)
                        try:
                            size = os.path.getsize(file_path)
                            # Prende il file più pesante e ne salva il nome
                            if size > file_sizes.get((s_num, e_num), 0):
                                file_sizes[(s_num, e_num)] = size
                                file_names[(s_num, e_num)] = f # <--- Salva il nome fisico
                        except: pass
        
        # 4b. Legge la cartella di download locale per lo stato "Scaricato"
        local_sizes = {}
        from core.config import Config as _CoreCfg2
        _local_cfg = _CoreCfg2()
        local_dir = getattr(_local_cfg, 'libtorrent_dir', '').strip()
        if local_dir and os.path.exists(local_dir) and local_dir != archive_path:
            for root, _, files in os.walk(local_dir):
                folder_name = os.path.basename(root)
                folder_match = re.search(r'(?i)(?:stagione|season|serie|s)\s*[._\-]*0*(\d+)', folder_name)
                folder_season = int(folder_match.group(1)) if folder_match else None
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in video_exts or 'sample' in f.lower():
                        continue
                    s_num = e_num = 0
                    m_std = re.search(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})|0*(\d{1,2})x0*(\d{1,3})', f)
                    if m_std:
                        s_num = int(m_std.group(1) or m_std.group(3))
                        e_num = int(m_std.group(2) or m_std.group(4))
                    elif folder_season is not None:
                        m_ep = re.search(r'(?i)(?:ep|episodio|episode|e)[._\-\s]*0*(\d{1,3})|^0*(\d{1,3})(?:[\s\.\-]|$)', f)
                        if m_ep:
                            s_num = folder_season
                            e_num = int(m_ep.group(1) or m_ep.group(2))
                    if s_num > 0 and e_num > 0:
                        try:
                            size = os.path.getsize(os.path.join(root, f))
                            if size > local_sizes.get((s_num, e_num), 0):
                                local_sizes[(s_num, e_num)] = size
                        except: pass
        
        # 5. Struttura i dati unendo TMDB, DB e NAS
        seasons_data = {}
        
        # Inserisce gli episodi previsti da TMDB come "Mancanti" di base
        for s_num, ep_count in tmdb_seasons.items():
            seasons_data[s_num] = {}
            for e_num in range(1, ep_count + 1):
                seasons_data[s_num][e_num] = {
                    'status': 'Mancante', 'id': None, 'title': f"Episodio {e_num}", 
                    'score': 0, 'size': file_sizes.get((s_num, e_num), 0),
                    'file_name': file_names.get((s_num, e_num), '') # <--- Inserisce il nome
                }

        # Sovrascrive con i dati reali del Database
        for ep in episodes_db:
            s_num, e_num = ep['season'], ep['episode']
            if e_num <= 0: continue # Ulteriore scudo per nascondere i Season Pack

            _fs_val = file_sizes.get((s_num, e_num), 0)
            _ls_val = local_sizes.get((s_num, e_num), 0)
            if s_num not in seasons_data: seasons_data[s_num] = {}
            if e_num not in seasons_data[s_num]:
                seasons_data[s_num][e_num] = {'size': 0, 'file_name': ''}
                
            seasons_data[s_num][e_num].update({
                'id': ep['id'],
                'status': (
                    'NAS'        if file_sizes.get((s_num, e_num), 0) > 0 else
                    'Scaricato'  if local_sizes.get((s_num, e_num), 0) > 0 else
                    'In DB'      if ep['downloaded_at'] else
                    'In DB'      if ep['magnet_link'] else
                    'Mancante'
                ),
                'title': ep['title'],
                'score': ep['quality_score'],
                'size': file_sizes.get((s_num, e_num), 0),
                'file_name': file_names.get((s_num, e_num), ''),
                'downloaded_at': ep['downloaded_at']
            })

        # Raccoglie i feed matches per episodi non ancora scaricati
        # (usato per lo stato "In feed")
        try:
            c_fm = db.conn.cursor()
            c_fm.execute('''
                SELECT DISTINCT season, episode FROM episode_feed_matches
                WHERE series_id=?
            ''', (series_id,))
            feed_match_set = {(r['season'], r['episode']) for r in c_fm.fetchall()}
        except Exception as e:
            logger.debug(f"feed_match_set: {e}")
            feed_match_set = set()

        # Imposta lo stato "In feed" per episodi Mancanti che hanno feed matches
        for s_num, eps in seasons_data.items():
            for e_num, ep_info in eps.items():
                if ep_info['status'] == 'Mancante' and (s_num, e_num) in feed_match_set:
                    ep_info['status'] = 'In feed'
                # Aggiunge flag per mostrare il pulsante feed_matches
                ep_info['has_feed_matches'] = (s_num, e_num) in feed_match_set

        # Controlla i torrent attivi nel client per marcare episodi "In scarico"
        # anche se aggiunti manualmente (non presenti nel DB di EXTTO)
        try:
            lt_resp = requests.get(f'http://127.0.0.1:{get_engine_port()}/api/torrents', timeout=3)
            if lt_resp.ok:
                _lt_data = lt_resp.json()
                # Il motore può restituire {'torrents': [...]} oppure direttamente [...]
                if isinstance(_lt_data, dict):
                    active_torrents = _lt_data.get('torrents', [])
                    if isinstance(active_torrents, dict):
                        active_torrents = list(active_torrents.values())
                elif isinstance(_lt_data, list):
                    active_torrents = _lt_data
                else:
                    active_torrents = []
                from core.models import normalize_series_name as _nsn
                norm_series = _nsn(series_name)
                for t in active_torrents:
                    t_name = t.get('name', '') or ''
                    # Salta torrent già completati
                    # NOTA: progress è 0-100 (non 0-1), state può essere 'seeding_t'/'finished_t'
                    progress = t.get('progress', 0)
                    state    = t.get('state', '')
                    if progress >= 100.0 or 'seeding' in state or 'finished' in state:
                        continue
                    # Prova a parsare S##E## dal nome del torrent
                    m_ep = re.search(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})', t_name)
                    if not m_ep:
                        continue
                    t_season  = int(m_ep.group(1))
                    t_episode = int(m_ep.group(2))
                    # Estrae la parte prima di S##E##, sostituisce punti/underscore con spazi
                    raw_prefix = re.sub(r'(?i)[Ss]\d+[Ee]\d+.*', '', t_name).strip(' ._-')
                    raw_prefix = re.sub(r'[._]', ' ', raw_prefix).strip()
                    norm_t = _nsn(raw_prefix)
                    # Usa _series_name_matches (gestisce possessivo) + fallback sottostringa
                    # per varianti con anno (es. "Brilliant Minds 2024" vs "Brilliant Minds")
                    from core.models import _series_name_matches as _snm
                    # Rimuove trattini per gestire "Monarch - Legacy" vs "Monarch Legacy"
                    norm_series_nd = norm_series.replace('-', ' ').replace('  ', ' ').strip()
                    norm_t_nd      = norm_t.replace('-', ' ').replace('  ', ' ').strip()
                    _matched = (_snm(norm_series, norm_t) or
                                _snm(norm_series_nd, norm_t_nd) or
                                norm_series_nd in norm_t_nd or
                                norm_t_nd in norm_series_nd)
                    if not _matched:
                        continue
                    # Marca l'episodio come In scarico se non è già Scaricato
                    ep_info = seasons_data.get(t_season, {}).get(t_episode)
                    if ep_info and ep_info['status'] not in ('Scaricato', 'NAS'):
                        ep_info['status'] = 'In scarico'
        except Exception as e:
            logger.debug(f"downloading state: {e}")
            pass  # Se libtorrent non risponde, non bloccare il caricamento

        # Prepara l'output formattato per il frontend
        output_seasons = []
        for s_num in sorted(seasons_data.keys(), reverse=True):
            eps = []
            total_size = 0
            downloaded_count = 0
            total_eps = len(seasons_data[s_num])
            
            for e_num in sorted(seasons_data[s_num].keys()):
                ep_info = seasons_data[s_num][e_num]
                total_size += ep_info['size']
                if ep_info['status'] in ['Scaricato', 'NAS']: downloaded_count += 1
                eps.append({'episode': e_num, **ep_info})
                
            output_seasons.append({
                'season': s_num,
                'monitored': s_num not in ignored_seasons, # <--- Aggiunto!
                'total_episodes': total_eps,
                'downloaded_episodes': downloaded_count,
                'total_size_bytes': total_size,
                'episodes': eps
            })

        # --- NUOVO: CONTROLLO COMPLETEZZA "AL VOLO" (Retroattivo) ---
        is_completed = False
        try:
            # Assicura che la colonna esista
            try: c.execute("ALTER TABLE series ADD COLUMN is_completed BOOLEAN DEFAULT 0"); db.conn.commit()
            except: pass
            
            c.execute("SELECT is_completed FROM series WHERE id=?", (series_id,))
            r_comp = c.fetchone()
            is_completed = bool(r_comp[0]) if r_comp else False
            
            # Se TMDB dice che è finita e non era segnata come completa...
            if not is_completed and meta.get('status') in ['Ended', 'Canceled']:
                # L'oggetto output_seasons ha già contato le stagioni "Monitorate" (il segnalibro acceso!)
                tot_exp = sum(s['total_episodes'] for s in output_seasons if s['monitored'])
                tot_down = sum(s['downloaded_episodes'] for s in output_seasons if s['monitored'])
                        
                if tot_exp > 0 and tot_down >= tot_exp:
                    # Promossa! Registra nel DB!
                    c.execute("UPDATE series SET is_completed=1 WHERE id=?", (series_id,))
                    db.conn.commit()
                    is_completed = True
        except Exception as e:
            logger.debug(f"is_completed libtorrent: {e}")
            pass
        # Calcola is_ended dal DB (aggiornato ogni ciclo da extto3.py)
        is_ended = False
        try:
            c.execute("SELECT is_ended FROM series WHERE id=?", (series_id,))
            r_end = c.fetchone()
            if r_end:
                is_ended = bool(r_end[0])
            # Fallback: se TMDB dice Ended/Canceled anche se il DB non è ancora aggiornato
            if not is_ended and meta.get('status') in ['Ended', 'Canceled']:
                is_ended = True
                c.execute("UPDATE series SET is_ended=1 WHERE id=?", (series_id,))
                db.conn.commit()
        except Exception as e:
            logger.debug(f"is_ended update: {e}")

        return jsonify({
            'success':      True,
            'series_name':  series_name,
            'archive_path': archive_path,
            'meta':         meta,
            'seasons':      output_seasons,
            'is_completed': is_completed,
            'is_ended':     is_ended,
            'tmdb_status':  meta.get('status', ''),
        })
    except Exception as e:
        import traceback, logging as _l
        _l.getLogger('extto.web').error(f"❌ Errore extto-details: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/series/by-name/<path:series_name>', methods=['DELETE'])
def delete_series_by_name(series_name):
    """Elimina una serie per nome — usato quando l'ID non è disponibile."""
    try:
        import sqlite3 as _sq_n
        conn_n = _sq_n.connect(DB_FILE)
        conn_n.row_factory = _sq_n.Row
        row_n = conn_n.execute(
            "SELECT id FROM series WHERE LOWER(name)=LOWER(?)", (series_name,)
        ).fetchone()
        conn_n.close()

        if row_n:
            # Ha un ID — usa il metodo standard
            db.delete_series(row_n['id'])
            logger.info(f"🗑️ Series deleted by name: '{series_name}' (id={row_n['id']})")
        else:
            logger.warning(f"delete_series_by_name: '{series_name}' not found in operational DB")

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"delete_series_by_name: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/series/<int:series_id>', methods=['DELETE'])
def delete_series(series_id):
    """Elimina una serie dal DB operativo e dalla configurazione."""
    try:
        # 1. Leggi il nome prima di eliminare (serve per pulire extto_config.db)
        import sqlite3 as _sq_del
        conn_del = _sq_del.connect(DB_FILE)
        conn_del.row_factory = _sq_del.Row
        row_del = conn_del.execute("SELECT name FROM series WHERE id=?", (series_id,)).fetchone()
        series_name = row_del['name'] if row_del else None
        conn_del.close()

        # 2. Elimina da extto_series.db (episodi + serie)
        db.delete_series(series_id)

        # 3. Elimina anche da extto_config.db (tabella interna serie config)
        if series_name:
            try:
                migrated = _cdb.get_setting('_migrated_series', [])
                if isinstance(migrated, list):
                    migrated = [s for s in migrated if s.get('name') != series_name]
                    _cdb.set_setting('_migrated_series', migrated)
            except Exception as ce:
                logger.debug(f"delete_series config_db: {ce}")

        logger.info(f"🗑️ Series deleted: '{series_name}' (id={series_id})")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"delete_series: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/series/<int:sid>/rename', methods=['POST'])
def rename_series(sid):
    """Rinomina una serie nel DB"""
    try:
        new_name = (request.json or {}).get('new_name')
        if not new_name: return jsonify({'error': 'Name empty'}), 400
        c = db.conn.cursor()
        c.execute('UPDATE series SET name=? WHERE id=?', (new_name, sid))
        db.conn.commit()
        return jsonify({'success': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/episodes/<int:episode_id>', methods=['DELETE'])
def delete_episode(episode_id):
    """Elimina un episodio"""
    try:
        db.delete_episode(episode_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.exception(f"❌ Error delete_episode {episode_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/episodes/<int:episode_id>/redownload', methods=['POST'])
def mark_episode_redownload(episode_id):
    """Marca episodio per riscarica (abbassa score)"""
    try:
        if not db.conn:
            return jsonify({'success': False, 'error': 'Database non disponibile'}), 500
        
        c = db.conn.cursor()
        # Abbassa drasticamente lo score e azzera l'hash per evitare il blocco su duplicate_hash
        c.execute(r"UPDATE episodes SET quality_score = 0, magnet_hash = NULL WHERE id=?", (episode_id,))
        db.conn.commit()
        
        return jsonify({'success': True, 'message': 'Episodio marcato per riscarica (score=0, hash azzerato)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/episodes/<int:episode_id>/ignore', methods=['POST'])
def ignore_episode(episode_id):
    """Ignora episodio (alza score per evitare upgrade)"""
    try:
        if not db.conn:
            return jsonify({'success': False, 'error': 'Database non disponibile'}), 500
        
        c = db.conn.cursor()
        c.execute("UPDATE episodes SET quality_score = quality_score + 10000 WHERE id=?", (episode_id,))
        db.conn.commit()
        
        return jsonify({'success': True, 'message': 'Episodio ignorato'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/episodes/<int:episode_id>/force-missing', methods=['POST'])
def force_missing_episode(episode_id):
    """Forza episodio come mancante: elimina la riga dal DB così da essere considerato da gap-filling/ricerche."""
    try:
        if not db.conn:
            return jsonify({'success': False, 'error': 'Database non disponibile'}), 500
        c = db.conn.cursor()
        # Recupera info per pulizia completa
        c.execute(r"SELECT series_id, season, episode FROM episodes WHERE id=?", (episode_id,))
        row = c.fetchone()
        c.execute(r"DELETE FROM episodes WHERE id=?", (episode_id,))
        # Elimina anche da episode_archive_presence così check_series non blocca il re-download
        if row:
            c.execute(
                "DELETE FROM episode_archive_presence WHERE series_id=? AND season=? AND episode=?",
                (row['series_id'], row['season'], row['episode'])
            )
        db.conn.commit()
        return jsonify({'success': True, 'message': 'Episodio forzato come mancante (rimosso dal DB)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/movies')
def get_movies():
    """Lista film"""
    movies = db.get_all_movies()
    return jsonify(movies)
    
@app.route('/api/movies/details/<path:movie_name>', methods=['GET'])
def get_radarr_details(movie_name):
    """Genera i dettagli completi per la vista stile Radarr dei Film."""
    try:
        import urllib.parse
        import requests
        movie_name = urllib.parse.unquote(movie_name)
        
        cfg = parse_movies_config()
        mov_cfg = next((m for m in cfg if m['name'].lower() == movie_name.lower()), None)
        
        c = db.conn.cursor()
        c.execute('SELECT * FROM movies WHERE name=? ORDER BY id DESC LIMIT 1', (movie_name,))
        row = c.fetchone()
        mov_db = dict(row) if row else None
        
        if not mov_cfg and not mov_db: return jsonify({'error': 'Film non trovato'}), 404
            
        year = (mov_cfg.get('year') if mov_cfg else (mov_db.get('year') if mov_db else '')) or ''
        
        from core.config import Config as CoreConfig
        core_cfg = CoreConfig()
        api_key = getattr(core_cfg, 'tmdb_api_key', '').strip()
        
        meta = {'poster': '', 'backdrop': '', 'overview': 'Nessuna trama disponibile.', 'year': year, 'tmdb_id': None, 'title': movie_name}
        
        if api_key:
            tmdb_lang = getattr(core_cfg, 'tmdb_language', 'it-IT').strip()
            search_url = f"https://api.themoviedb.org/3/search/movie?api_key={api_key}&query={urllib.parse.quote(movie_name)}&language={tmdb_lang}"
            if year: search_url += f"&year={year}"
            
            try:
                res = requests.get(search_url, timeout=5).json()
                if res.get('results'):
                    first = res['results'][0]
                    meta['tmdb_id'] = first.get('id')
                    meta['poster'] = first.get('poster_path', '')
                    meta['backdrop'] = first.get('backdrop_path', '')
                    meta['overview'] = first.get('overview', meta['overview'])
                    meta['year'] = first.get('release_date', '')[:4] if first.get('release_date') else year
                    meta['title'] = first.get('title', movie_name)
            except Exception as e:
                _l.debug(f"File check error: {e}")
        
        status = 'Scaricato' if (mov_db and mov_db.get('magnet_link')) else 'In Ricerca'
        if not mov_cfg: status = 'Solo Storico'
        elif not mov_cfg.get('enabled', True): status = 'In Pausa'

        has_feed = db.has_movie_feed_matches(movie_name)

        return jsonify({'success': True, 'name': movie_name, 'meta': meta, 'status': status,
                        'db_info': mov_db, 'cfg_info': mov_cfg,
                        'movie_name': movie_name, 'has_feed': has_feed})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500    

@app.route('/api/movies/feed-status')
def get_movies_feed_status():
    """Ritorna lista nomi film che hanno feed matches (per colorare l'icona nella lista)."""
    try:
        c = db.conn.cursor()
        c.execute("SELECT DISTINCT movie_name FROM movie_feed_matches")
        names = [row['movie_name'] for row in c.fetchall()]
        return jsonify(names)
    except Exception as e:
        return jsonify([]), 500

@app.route('/api/movies/feed-matches')
def get_movie_feed_matches_api():
    """Ritorna i feed matches per un film cercato per nome."""
    try:
        name = request.args.get('name', '').strip()
        if not name:
            return jsonify([])
        matches = db.get_movie_feed_matches(name)
        return jsonify(matches)
    except Exception as e:
        logger.exception(f"Error get_movie_feed_matches_api: {e}")
        return jsonify([]), 500

@app.route('/api/movies/<int:movie_id>', methods=['DELETE'])
def delete_movie(movie_id):
    """Elimina un film"""
    try:
        db.delete_movie(movie_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.exception(f"❌ Error delete_movie {movie_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/archive')
def get_archive():
    """Ricerca nell'archivio"""
    query = request.args.get('q', '')
    page = int(request.args.get('page', 0))
    limit = int(request.args.get('limit', 50))
    offset = page * limit
    
    items, total = db.search_archive(query, offset, limit)
    
    return jsonify({
        'items': items,
        'total': total,
        'page': page,
        'pages': (total + limit - 1) // limit
    })

@app.route('/api/archive/delete', methods=['POST'])
def archive_batch_delete():
    """Elimina una lista di ID dall'archivio."""
    try:
        ids = (request.json or {}).get('ids', [])
        if not ids:
            return jsonify({'success': False, 'error': 'Nessun ID fornito'}), 400
        if not os.path.exists(ARCHIVE_FILE):
            return jsonify({'success': False, 'error': 'Archivio non trovato'}), 404
        with sqlite3.connect(ARCHIVE_FILE) as conn:
            c = conn.cursor()
            placeholders = ','.join(['?'] * len(ids))
            c.execute(f"DELETE FROM archive WHERE id IN ({placeholders})", ids)
            deleted = c.rowcount
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/archive/batch-download', methods=['POST'])
def archive_batch_download():
    """Restituisce i magnet link per una lista di ID (il client li invia uno per uno)."""
    try:
        ids = (request.json or {}).get('ids', [])
        if not ids:
            return jsonify({'success': False, 'error': 'Nessun ID fornito'}), 400
        if not db.conn_archive:
            return jsonify({'success': False, 'error': 'Archivio non disponibile'}), 503
        c = db.conn_archive.cursor()
        placeholders = ','.join(['?'] * len(ids))
        c.execute(f"SELECT id, title, magnet FROM archive WHERE id IN ({placeholders})", ids)
        rows = [{'id': r[0], 'title': r[1], 'magnet': r[2]} for r in c.fetchall()]
        return jsonify({'success': True, 'items': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config')
def get_config():
    """Configurazione completa"""
    try:
        config = parse_series_config()
        movies = parse_movies_config()
        return jsonify({
            'settings': config['settings'],
            'series': config['series'],
            'movies': movies
        })
    except Exception as e:
        logger.error(f'get_config: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/config', methods=['POST'])
def save_full_config():
    """Salva l'intero config in una sola operazione atomica.
    
    Riceve {settings, series} e sovrascrive completamente series_config.txt.
    Le chiavi lista (url, blacklist, wantedlist, archive_root, archive_cred)
    devono essere incluse nel payload — se assenti vengono preservate dal file
    corrente per sicurezza.
    """
    try:
        data = request.json or {}
        new_settings = data.get('settings', {})
        new_series   = data.get('series',   None)

        with _config_write_lock:
            # Leggi il file corrente solo per le chiavi lista se non fornite
            current = parse_series_config()
            LIST_KEYS = {'url', 'blacklist', 'wantedlist', 'archive_root', 'archive_cred', 'custom_score'}
            for k in current['settings']:
                # Protegge le liste e i punteggi dall'essere cancellati se non presenti nel payload
                if k in LIST_KEYS or k.startswith('score_'):
                    if k not in new_settings:
                        new_settings[k] = current['settings'][k]

            config = {
                'settings': new_settings,
                'series':   new_series if new_series is not None else current['series'],
            }
            if save_series_config(config):
                return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Errore salvataggio'}), 500
    except Exception as e:
        logger.error(f"save_full_config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/config/series', methods=['POST'])
def update_series_config():
    """Aggiorna configurazione serie. sync_delete=True elimina le serie non in lista."""
    try:
        data        = request.json or {}
        new_series  = data.get('series', [])
        sync_delete = bool(data.get('sync_delete', False))

        with _config_write_lock:
            # Salva settings (invariati)
            try:
                current = parse_series_config()
                _cdb.set_settings_bulk(current.get('settings', {}))
            except Exception as e:
                logger.error(f"update_series_config settings: {e}")

            # Salva serie con sync_delete opzionale
            try:
                _save_series_to_db(new_series, sync_delete=sync_delete)
            except Exception as e:
                logger.error(f"update_series_config db: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/config/movies', methods=['POST'])
def update_movies_config():
    """Aggiorna configurazione film"""
    try:
        data = request.json or {}
        movies = data.get('movies', [])
        
        if save_movies_config(movies):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Errore salvataggio'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/config/settings', methods=['POST'])
def update_settings():
    """Aggiorna impostazioni generali."""
    try:
        data = request.json or {}
        config = parse_series_config()
        new_settings = data.get('settings', {})

        # Validazione porte: devono essere nel range valido e diverse tra loro
        for port_key, default_val in [('web_port', 5000), ('engine_port', 8889)]:
            if port_key in new_settings:
                try:
                    p = int(new_settings[port_key])
                    if not (1024 <= p <= 65535):
                        return jsonify({'success': False,
                            'error': f'{port_key}: porta {p} non valida (range 1024-65535)'}), 400
                    new_settings[port_key] = str(p)
                except (ValueError, TypeError):
                    return jsonify({'success': False,
                        'error': f'{port_key}: valore non numerico'}), 400
        if 'web_port' in new_settings and 'engine_port' in new_settings:
            if new_settings['web_port'] == new_settings['engine_port']:
                return jsonify({'success': False,
                    'error': 'web_port e engine_port non possono essere uguali'}), 400

        LIST_KEYS = {'url', 'blacklist', 'wantedlist', 'archive_root', 'archive_cred', 'custom_score'}
        # Preserva sia le liste che tutti i punteggi (punti bonus e preferenze)
        preserved = {k: v for k, v in config['settings'].items() if k in LIST_KEYS or k.startswith('score_')}
        config['settings'] = {**preserved, **new_settings} # FIX: Permette il salvataggio corretto delle liste!

        if save_series_config(config):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Errore salvataggio'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
        
# --- NUOVO: Accumulatore di traffico isolato solo per libtorrent ---
_torrent_traffic = {'dl': 0, 'ul': 0}

def _traffic_monitor():
    """Sonda il motore torrent in background per accumulare i byte esatti (escludendo il traffico web)"""
    import requests
    import time
    while True:
        try:
            res = requests.get(f'http://127.0.0.1:{get_engine_port()}/api/torrents/stats', timeout=2)
            if res.status_code == 200:
                data = res.json()
                # Aggiunge (Velocità * 2 secondi) = Byte esatti transitati per i torrent
                _torrent_traffic['dl'] += data.get('dl_rate', 0) * 2
                _torrent_traffic['ul'] += data.get('ul_rate', 0) * 2
        except Exception as e:
            logger.debug(f"torrent traffic poll: {e}")
            pass
        time.sleep(2)

# Avvia thread di supporto solo nel processo principale (evita doppio avvio con Werkzeug reloader)
if os.environ.get('WERKZEUG_RUN_MAIN') != 'false':
    Thread(target=tail_log_file, daemon=True).start()
    threading.Thread(target=_traffic_monitor, daemon=True).start()

import threading

@app.route('/api/system/stats')
def system_stats():
    """Restituisce l'utilizzo di CPU, RAM, Disco e Traffico di Rete Totale."""
    try:
        import psutil
        import os
        import time
        
        # 1. Trova i processi EXTTO (questo server web + eventuale motore extto3.py)
        current_pid = os.getpid()
        extto_pids = [current_pid]
        
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                if p.info['cmdline'] and any('extto3.py' in cmd for cmd in p.info['cmdline']):
                    extto_pids.append(p.info['pid'])
            except Exception:
                pass  # psutil: processo già terminato
        
        cpu_total = 0.0
        ram_total_pct = 0.0
        ram_total_bytes = 0
        oldest_time = time.time()
        
        # Raccogli CPU e RAM per i processi trovati
        for pid in set(extto_pids):
            try:
                proc = psutil.Process(pid)
                cpu_total += proc.cpu_percent(interval=0.05)
                ram_total_pct += proc.memory_percent()
                ram_total_bytes += proc.memory_info().rss
                if proc.create_time() < oldest_time:
                    oldest_time = proc.create_time()
            except Exception:
                pass  # psutil: processo già terminato
        
        # Calcola Uptime (Tempo di attività)
        uptime_sec = int(time.time() - oldest_time)
        days = uptime_sec // 86400
        hours = (uptime_sec % 86400) // 3600
        minutes = (uptime_sec % 3600) // 60
        uptime_str = f"{days}g {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"

        cpu_total = min(cpu_total, 100.0)
        ram_mb = ram_total_bytes / (1024 * 1024)
        disk = psutil.disk_usage(os.getcwd()).percent 
        
        # Calcolo Spazio RAM Disk (se abilitato)
        cfg = parse_series_config()
        settings = cfg.get('settings', {})
        ramdisk_enabled = str(settings.get('libtorrent_ramdisk_enabled', 'no')).lower() in ('yes', 'true', '1')
        ramdisk_dir = settings.get('libtorrent_ramdisk_dir', '').strip()
        ramdisk_pct = 0.0
        ramdisk_used_gb = 0.0
        ramdisk_total_gb = 0.0 # <--- Aggiunto il totale!

        if ramdisk_enabled and ramdisk_dir and os.path.exists(ramdisk_dir):
            try:
                rd_usage = psutil.disk_usage(ramdisk_dir)
                ramdisk_pct = rd_usage.percent
                ramdisk_used_gb = rd_usage.used / (1024**3)
                ramdisk_total_gb = rd_usage.total / (1024**3) # <--- Calcola il totale
            except Exception:
                pass

        # 3. Legge i totali isolati di libtorrent calcolati in background
        tot_dl_bytes = _torrent_traffic['dl']
        tot_ul_bytes = _torrent_traffic['ul']
        
        return jsonify({
            'success': True, 
            'cpu': round(cpu_total, 1), 
            'ram': round(ram_total_pct, 1), 
            'ram_mb': round(ram_mb, 1),
            'disk': round(disk, 1),
            'ramdisk': round(ramdisk_pct, 1),
            'ramdisk_gb': round(ramdisk_used_gb, 2),
            'ramdisk_total_gb': round(ramdisk_total_gb, 2), # <--- Lo inviamo alla grafica
            'uptime': uptime_str,
            'total_dl_bytes': tot_dl_bytes,
            'total_ul_bytes': tot_ul_bytes
        })
    except ImportError:
        return jsonify({'success': False, 'error': 'Libreria psutil mancante'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
        
@app.route('/api/fetch-url', methods=['POST'])
def fetch_url():
    """Scarica un file .torrent gestendo i redirect verso i magnet link."""
    try:
        data = request.get_json(silent=True) or {}
        url = data.get('url', '').strip()
        
        if url.startswith('magnet:'):
            return jsonify({'success': False, 'is_magnet': True, 'magnet': url})
            
        import requests, base64
        
        # --- FIX: allow_redirects=False impedisce il crash se il tracker rimanda a un magnet ---
        res = requests.get(
            url, timeout=15, verify=False, allow_redirects=False,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        
        # Se il server risponde con un redirect (301, 302...), verifichiamo dove punta
        if res.status_code in (301, 302, 303, 307, 308):
            target = res.headers.get('Location', '')
            if target.startswith('magnet:'):
                return jsonify({'success': False, 'is_magnet': True, 'magnet': target})
            # Se è un redirect HTTP normale, lo seguiamo manualmente una volta
            res = requests.get(target, timeout=15, verify=False, headers={'User-Agent': 'Mozilla/5.0'})

        if res.status_code != 200:
            return jsonify({'success': False, 'error': f'Errore Tracker (Status: {res.status_code})'})
            
        b64_data = base64.b64encode(res.content).decode('utf-8')
        filename = 'downloaded.torrent'
        if 'Content-Disposition' in res.headers:
            import re
            m = re.search(r'filename="([^"]+)"', res.headers['Content-Disposition'])
            if m: filename = m.group(1)
            
        return jsonify({'success': True, 'data': b64_data, 'filename': filename})
    except Exception as e:
        logger.error(f"URL download error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
        
@app.route('/api/test-notification', methods=['POST'])
def test_notification():
    """Invia una notifica di test a Telegram o Email in base alla configurazione salvata."""
    try:
        config = parse_series_config()
        s = config.get('settings', {})
        success_msgs = []
        
        # Test Telegram
        if str(s.get('notify_telegram', 'no')).lower() == 'yes':
            token = str(s.get('telegram_bot_token', '')).strip()
            chat_id = str(s.get('telegram_chat_id', '')).strip()
            if token and chat_id:
                import urllib.request, json
                msg = "🔔 *Test Notifica EXTTO*\nSe leggi questo messaggio, le notifiche Telegram funzionano correttamente!"
                data = json.dumps({'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}).encode()
                req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data, headers={'Content-Type': 'application/json'})
                urllib.request.urlopen(req, timeout=10)
                success_msgs.append("Telegram")
        
        # Test Email
        if str(s.get('notify_email', 'no')).lower() == 'yes':
            smtp_server = str(s.get('email_smtp', '')).strip()
            email_from = str(s.get('email_from', '')).strip()
            email_to = str(s.get('email_to', '')).strip()
            email_pass = str(s.get('email_password', '')).strip()
            if smtp_server and email_from and email_to and email_pass:
                import smtplib
                from email.mime.text import MIMEText
                msg = MIMEText("Se leggi questa email, le notifiche di EXTTO funzionano correttamente!")
                msg['Subject'] = "🔔 Test Notifica EXTTO"
                msg['From'] = email_from
                msg['To'] = email_to
                port = 587
                if ':' in smtp_server:
                    smtp_server, port_str = smtp_server.split(':')
                    port = int(port_str)
                server = smtplib.SMTP(smtp_server, port, timeout=10)
                server.starttls()
                server.login(email_from, email_pass)
                server.send_message(msg)
                server.quit()
                success_msgs.append("Email")
        
        if success_msgs:
            return jsonify({'success': True, 'message': f'Test inviato con successo via: {", ".join(success_msgs)}'})
        else:
            return jsonify({'success': False, 'error': 'Nessun metodo di notifica configurato o abilitato.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500        


@app.route('/api/config/check-ports', methods=['POST'])
def check_ports():
    """Verifica se le porte richieste sono disponibili.
    Riceve {web_port, engine_port, current_web_port, current_engine_port}.
    Restituisce {ok: bool, conflicts: [{role, port, pid, process}]}.
    """
    import socket, re, subprocess
    try:
        data        = request.json or {}
        new_web     = int(data.get('web_port', 0))
        new_engine  = int(data.get('engine_port', 0))
        curr_web    = int(data.get('current_web_port', 0))
        curr_engine = int(data.get('current_engine_port', 0))

        def port_in_use(port):
            if not port or port < 1 or port > 65535:
                return None
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(('127.0.0.1', port))
                    return None  # libera
                except OSError:
                    pass
            # Trova il processo che occupa la porta
            pid = None
            process = 'sconosciuto'
            try:
                result = subprocess.run(
                    ['ss', '-tlnp', f'sport = :{port}'],
                    capture_output=True, text=True, timeout=3
                )
                for line in result.stdout.splitlines():
                    if f':{port}' in line and 'pid=' in line:
                        m = re.search(r'pid=(\d+)', line)
                        if m:
                            pid = int(m.group(1))
                            try:
                                with open(f'/proc/{pid}/comm') as pf:
                                    process = pf.read().strip()
                            except Exception:
                                pass
                        break
            except Exception:
                pass
            return {'pid': pid, 'process': process}

        conflicts = []
        for role, new_port, curr_port in [
            ('web_port',    new_web,    curr_web),
            ('engine_port', new_engine, curr_engine),
        ]:
            if new_port and new_port != curr_port:
                result = port_in_use(new_port)
                if result is not None:
                    conflicts.append({
                        'role':    role,
                        'port':    new_port,
                        'pid':     result.get('pid'),
                        'process': result.get('process', 'sconosciuto'),
                    })

        return jsonify({'ok': len(conflicts) == 0, 'conflicts': conflicts})
    except Exception as e:
        return jsonify({'ok': True, 'conflicts': [], 'error': str(e)})


@app.route('/api/config/urls-filters', methods=['POST'])
def update_urls_filters():
    """Aggiorna URL, blacklist e wantedlist"""
    try:
        data = request.json or {}
        config = parse_series_config()
        
        # Aggiorna URL, blacklist, wantedlist
        urls = data.get('urls', [])
        blacklist = data.get('blacklist', [])
        wantedlist = data.get('wantedlist', [])
        
        # Sostituisci gli array
        config['settings']['url'] = urls
        config['settings']['blacklist'] = blacklist
        config['settings']['wantedlist'] = wantedlist
        
        if save_series_config(config):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Errore salvataggio'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logs')
def get_logs():
    """Ultime righe del log"""
    lines = request.args.get('lines', 100, type=int)
    
    if not os.path.exists(LOG_FILE):
        return jsonify({'logs': []})
    
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
        return jsonify({'logs': all_lines[-lines:]})

@app.route('/api/recent-downloads')
def get_recent_downloads():
    """Ultimi eventi: download + cicli motore misti, con contatori per le card riepilogo."""
    try:
        events = []

        # 1. EPISODI (inclusi season pack E00)
        if db.conn:
            try:
                c = db.conn.cursor()
                c.execute("""
                    SELECT e.season, e.episode, e.quality_score, e.downloaded_at, s.name as series_name
                    FROM episodes e
                    JOIN series s ON e.series_id = s.id
                    WHERE e.downloaded_at IS NOT NULL AND e.episode >= 0
                    ORDER BY e.downloaded_at DESC
                    LIMIT 15
                """)
                for row in c.fetchall():
                    if row['episode'] == 0:
                        ep_str = f"S{row['season']:02d} Pack"
                        ep_type = 'pack'
                    else:
                        ep_str = f"S{row['season']:02d}E{row['episode']:02d}" if row['season'] and row['episode'] else ''
                        ep_type = 'episode'
                    events.append({
                        'kind':  'download',
                        'type':  ep_type,
                        'title': f"{row['series_name']} — {ep_str}" if ep_str else row['series_name'],
                        'quality_score': row['quality_score'],
                        'date':  row['downloaded_at'],
                    })
            except Exception as e:
                logger.error(f"Recent episodes DB error: {e}")

        # 2. FILM
        if db.conn:
            try:
                c = db.conn.cursor()
                c.execute("""
                    SELECT name, year, quality_score, downloaded_at, removed_at
                    FROM movies
                    WHERE downloaded_at IS NOT NULL
                    ORDER BY downloaded_at DESC
                    LIMIT 10
                """)
                for row in c.fetchall():
                    year_str = f" ({row['year']})" if row['year'] else ''
                    events.append({
                        'kind':    'download',
                        'type':    'movie',
                        'title':   f"{row['name']}{year_str}",
                        'quality_score': row['quality_score'],
                        'date':    row['downloaded_at'],
                        'removed': bool(row['removed_at']),
                    })
            except Exception as e:
                logger.error(f"Recent movies DB error: {e}")

        # 3. FUMETTI (singoli + weekly pack)
        try:
            from core.comics import ComicsDB
            _cdb_c = ComicsDB(os.path.join(BASE_DIR, 'comics.db'))

            # 3a. Singoli fumetti monitorati
            for ch in _cdb_c.get_history(limit=8):
                if not ch.get('sent_at'):
                    continue
                series = ch.get('comic_title') or ''
                ep     = ch.get('title') or ''
                if series and ep and series.lower() not in ep.lower():
                    display = f"{series} — {ep}"
                elif ep:
                    display = ep
                else:
                    display = series or 'Fumetto'
                events.append({
                    'kind':          'download',
                    'type':          'comic',
                    'title':         display,
                    'quality_score': None,
                    'date':          ch['sent_at'],
                })

            # 3b. Weekly pack inviati
            import sqlite3 as _sq_w
            _comics_db = os.path.join(BASE_DIR, 'comics.db')
            if os.path.exists(_comics_db):
                _cw = _sq_w.connect(_comics_db)
                _cw.row_factory = _sq_w.Row
                _weekly = _cw.execute("""
                    SELECT pack_date, sent_at
                    FROM comics_weekly
                    WHERE sent_at IS NOT NULL
                    ORDER BY sent_at DESC
                    LIMIT 5
                """).fetchall()
                _cw.close()
                for w in _weekly:
                    events.append({
                        'kind':          'download',
                        'type':          'comic',
                        'title':         f"Weekly Pack — {w['pack_date']}",
                        'quality_score': None,
                        'date':          w['sent_at'],
                    })
        except Exception as e:
            logger.debug(f"get_comics_history: {e}")

        # 4. CICLI MOTORE (ultimi 20)
        cycles_raw = []
        try:
            from core.database import Database as _CoreDB
            with _CoreDB() as _cdb:
                cycles_raw = _cdb.get_cycle_history(limit=20)
        except Exception as e:
            logger.debug(f"get_cycle_history activity: {e}")

        total_errors = 0
        for cy in cycles_raw:
            try:
                payload = json.loads(cy['payload']) if cy.get('payload') else {}
            except Exception:
                payload = {}
            dl  = payload.get('downloads', 0) or 0
            err = payload.get('errors', 0) or 0
            sc  = payload.get('scraped', 0) or 0
            total_errors += err
            events.append({
                'kind':      'cycle',
                'downloads': dl,
                'scraped':   sc,
                'errors':    err,
                'date':      cy['ts'],
            })

        # Ordina tutto per data decrescente
        events.sort(key=lambda x: x.get('date') or '', reverse=True)

        # Contatori 7gg per le card riepilogo
        from datetime import datetime, timezone, timedelta
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        dl_7d  = sum(1 for e in events if e['kind'] == 'download' and (e.get('date') or '') >= cutoff_7d)
        cy_7d  = sum(1 for e in events if e['kind'] == 'cycle'    and (e.get('date') or '') >= cutoff_7d)
        err_7d = sum(e.get('errors', 0) for e in events if e['kind'] == 'cycle' and (e.get('date') or '') >= cutoff_7d)

        return jsonify({
            'events':   events[:30],
            'stats':    {'downloads_7d': dl_7d, 'cycles_7d': cy_7d, 'errors_7d': err_7d},
            # retrocompatibilità
            'downloads': [e for e in events if e['kind'] == 'download'][:10],
            'found': []
        })

    except Exception as e:
        logger.error(f"General recent-downloads error: {e}")
        return jsonify({'events': [], 'stats': {}, 'downloads': [], 'found': []})


@app.route('/api/last_cycle')
def get_last_cycle():
    """Restituisce info sull'ultimo ciclo per timer prossima esecuzione"""
    try:
        # --- MODIFICA: Leggiamo il timer salvato per passarlo al frontend ---
        cfg = parse_series_config()
        raw_ref = int(cfg.get('settings', {}).get('refresh_interval', 120))
        ref_sec = raw_ref * 60 if raw_ref < 1000 else raw_ref
        try:
            from core.database import Database
            with Database() as db_obj:
                cycles = db_obj.get_cycle_history(limit=1)
            
            if cycles and len(cycles) > 0:
                cycle = cycles[0]
                return jsonify({
                    'generated_at': cycle['ts'],
                    'refresh_interval': ref_sec, # Passa i secondi al frontend
                    'stats': json.loads(cycle['payload']) if cycle.get('payload') else {}
                })
        except (ImportError, Exception) as e:
            print(f"Database import error: {e}")
        
        if os.path.exists(LOG_FILE):
            mtime = os.path.getmtime(LOG_FILE)
            return jsonify({
                'generated_at': datetime.fromtimestamp(mtime).isoformat(),
                'refresh_interval': ref_sec, # Passa i secondi al frontend
                'stats': {}
            })
        
        return jsonify({'generated_at': None, 'refresh_interval': ref_sec, 'stats': {}})
        
    except Exception as e:
        print(f"Error get_last_cycle: {e}")
        return jsonify({'generated_at': None, 'error': str(e)}), 500

@app.route('/api/cycle-history')
def get_cycle_history():
    """Storico degli ultimi N cicli per il grafico nel dashboard"""
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        from core.database import Database
        with Database() as db_obj:
            cycles = db_obj.get_cycle_history(limit=limit)
        result = []
        for c in reversed(cycles):  # Cronologico (più vecchio prima)
            try:
                payload = json.loads(c['payload']) if c.get('payload') else {}
            except Exception:
                payload = {}  # payload malformato
            result.append({
                'ts': c['ts'],
                'scraped': payload.get('scraped', 0),
                'candidates': payload.get('candidates', 0),
                'downloads': payload.get('downloads', 0),
                'gaps': payload.get('gaps', 0),
                'errors': payload.get('errors', 0),
                'series_matched': payload.get('series_matched', 0),
                'movies_matched': payload.get('movies_matched', 0),
            })
        return jsonify(result)
    except (ImportError, Exception) as e:
        logger.exception("Error get_cycle_history")
        return jsonify([])


# Fan-out broadcast per notifiche SSE: ogni client ha la propria queue.
# Una singola queue.Queue() condivisa causerebbe il problema che solo UN client
# riceve ogni notifica (primo che chiama .get()). Con il fan-out tutti i client
# connessi ricevono ogni notifica.
_notify_subscribers: list = []
_notify_lock = threading.Lock()

def push_notification(type_: str, title: str, message: str, data: dict = None):
    """Broadcast notifica a tutti i client SSE connessi."""
    payload = {
        'type': type_,
        'title': title,
        'message': message,
        'data': data or {},
        'ts': datetime.now().isoformat()
    }
    with _notify_lock:
        # Rimuove client disconnessi (queue piena = client lento/disconnesso)
        dead = []
        for q in _notify_subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _notify_subscribers.remove(q)


@app.route('/api/notifications/stream')
def stream_notifications():
    """SSE stream per notifiche push in-browser — fan-out a tutti i client connessi."""
    client_q = queue.Queue(maxsize=50)  # buffer max 50 notifiche per client
    with _notify_lock:
        _notify_subscribers.append(client_q)
    def generate():
        try:
            yield "data: " + json.dumps({'type': 'connected', 'message': 'Stream notifiche attivo'}) + "\n\n"
            while True:
                try:
                    notif = client_q.get(timeout=25)
                    yield "data: " + json.dumps(notif) + "\n\n"
                except GeneratorExit:
                    return
                except BrokenPipeError:
                    return
                except Exception as e:
                    logger.debug(f"stream log: {e}")
                    # Keepalive ping ogni 25 secondi
                    yield "data: " + json.dumps({'type': 'ping'}) + "\n\n"
        finally:
            with _notify_lock:
                try:
                    _notify_subscribers.remove(client_q)
                except ValueError:
                    pass
    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/amule/config', methods=['GET'])
def amule_config_get():
    """Legge la configurazione aMule da extto_config.db.
    Se amule_conf_path è configurato, legge anche i parametri direttamente
    da amule.conf (porte, cartelle) e li include nella risposta.
    """
    try:
        import os, configparser
        s    = _cdb.get_all_settings()
        home = os.path.expanduser('~')
        default_conf = os.path.join(home, '.aMule', 'amule.conf')

        resp = {
            'amule_enabled':   s.get('amule_enabled', 'no'),
            'amule_host':      s.get('amule_host', 'localhost'),
            'amule_port':      s.get('amule_port', '4712'),
            'amule_password':  s.get('amule_password', ''),
            'amule_service':   s.get('amule_service', 'amule-daemon'),
            'amule_conf_path': s.get('amule_conf_path', default_conf),
            'gap_fill_ed2k':   s.get('gap_fill_ed2k', 'no'),
            # campi letti da amule.conf (valori live)
            'amule_tcp_port':  '',
            'amule_udp_port':  '',
            'amule_incoming':  '',
            'amule_temp':      '',
        }

        # Prova a leggere i valori live dal amule.conf di amuled
        conf_path = resp['amule_conf_path']
        if conf_path and os.path.exists(conf_path):
            try:
                cfg = configparser.RawConfigParser()
                cfg.optionxform = str
                cfg.read(conf_path, encoding='utf-8')
                if cfg.has_option('eMule', 'Port'):
                    resp['amule_tcp_port'] = cfg.get('eMule', 'Port')
                if cfg.has_option('eMule', 'UDPPort'):
                    resp['amule_udp_port'] = cfg.get('eMule', 'UDPPort')
                if cfg.has_option('eMule', 'IncomingDir'):
                    resp['amule_incoming'] = cfg.get('eMule', 'IncomingDir')
                if cfg.has_option('eMule', 'TempDir'):
                    resp['amule_temp'] = cfg.get('eMule', 'TempDir')
            except Exception as e:
                logger.warning(f"amule_config_get: lettura amule.conf: {e}")

        return jsonify(resp)
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/amule/config', methods=['POST'])
def amule_config_save():
    """Salva la configurazione aMule in extto_config.db.
    Aggiorna anche amule.conf nel path configurato, preservando tutto
    il resto della configurazione di amuled.
    BLOCCA se amuled è in esecuzione: le modifiche a amule.conf vengono
    sovrascritte da amuled al riavvio, quindi è necessario che sia fermo.
    """
    try:
        import os, subprocess
        data = request.get_json(silent=True) or {}

        # Chiavi gestite da EXTTO nel DB
        db_keys = {
            'amule_enabled', 'amule_host', 'amule_port', 'amule_password',
            'amule_service', 'amule_conf_path',
            'gap_fill_ed2k',  # non tocca amule.conf — nessun bisogno di fermare amuled
        }
        # Chiavi che vanno scritte in amule.conf (non nel DB)
        conf_keys = {
            'amule_tcp_port', 'amule_udp_port', 'amule_incoming', 'amule_temp',
        }

        to_db   = {k: str(v).strip() for k, v in data.items() if k in db_keys}
        to_conf = {k: str(v).strip() for k, v in data.items() if k in conf_keys}

        if not to_db and not to_conf:
            return jsonify({'ok': False, 'error': 'Nessun parametro valido'}), 400

        # Salva SEMPRE i parametri sul DB, così le checkbox (es. gap_fill_ed2k) vengono salvate a prescindere
        if to_db:
            _cdb.set_settings_bulk(to_db)

        s = _cdb.get_all_settings()
        conf_path = to_db.get('amule_conf_path') or s.get('amule_conf_path', '')
        if not conf_path:
            conf_path = os.path.join(os.path.expanduser('~'), '.aMule', 'amule.conf')

        # Controlla se i parametri specifici per amule.conf sono EFFETTIVAMENTE cambiati rispetto al file su disco
        needs_conf_write = False
        if os.path.exists(conf_path):
            import configparser
            cfg = configparser.RawConfigParser()
            cfg.optionxform = str
            try:
                cfg.read(conf_path, encoding='utf-8')
                curr_tcp = cfg.get('eMule', 'Port') if cfg.has_option('eMule', 'Port') else ''
                curr_udp = cfg.get('eMule', 'UDPPort') if cfg.has_option('eMule', 'UDPPort') else ''
                curr_inc = cfg.get('eMule', 'IncomingDir') if cfg.has_option('eMule', 'IncomingDir') else ''
                curr_tmp = cfg.get('eMule', 'TempDir') if cfg.has_option('eMule', 'TempDir') else ''
                curr_prt = cfg.get('ExternalConnect', 'ECPort') if cfg.has_option('ExternalConnect', 'ECPort') else ''
                
                if 'amule_tcp_port' in to_conf and to_conf['amule_tcp_port'] != curr_tcp: needs_conf_write = True
                if 'amule_udp_port' in to_conf and to_conf['amule_udp_port'] != curr_udp: needs_conf_write = True
                if 'amule_incoming' in to_conf and to_conf['amule_incoming'] != curr_inc: needs_conf_write = True
                if 'amule_temp'     in to_conf and to_conf['amule_temp']     != curr_tmp: needs_conf_write = True
                if 'amule_port'     in to_db   and to_db['amule_port']       != curr_prt: needs_conf_write = True

                if to_db.get('amule_password'):
                    import hashlib
                    pwd_hash = hashlib.md5(to_db['amule_password'].encode('utf-8')).hexdigest()
                    curr_hash = cfg.get('ExternalConnect', 'ECPassword') if cfg.has_option('ExternalConnect', 'ECPassword') else ''
                    if pwd_hash != curr_hash:
                        needs_conf_write = True
            except Exception:
                needs_conf_write = True  # Se non riusciamo a leggere, forziamo la scrittura
        else:
            if to_conf or to_db.get('amule_port') or to_db.get('amule_password'):
                needs_conf_write = True

        if needs_conf_write:
            # ── Verifica che amuled sia SPENTO prima di toccare amule.conf ──────
            service_name = (to_db.get('amule_service') or s.get('amule_service', 'amule-daemon')).strip()
            try:
                res = subprocess.run(
                    ['systemctl', 'is-active', service_name],
                    capture_output=True, text=True, timeout=5
                )
                if res.stdout.strip() == 'active':
                    return jsonify({
                        'ok': True,
                        'service_running': True,
                        'warning': (f'Impostazioni DB salvate, MA per aggiornare amule.conf '
                                  f'devi prima fermare amuled: sudo systemctl stop {service_name}')
                    })
            except Exception:
                pass  # systemctl non disponibile o servizio non trovato: procedi comunque

            try:
                _amule_write_conf(conf_path, {**to_db, **to_conf})
                log_maintenance(f"✅ amule.conf aggiornato: {conf_path}")
            except Exception as e:
                log_maintenance(f"⚠️ Errore scrittura amule.conf: {e}")
                return jsonify({'ok': True, 'warning': str(e)})

        log_maintenance("✅ Configurazione aMule salvata")
        return jsonify({'ok': True})
    except Exception as e:
        log_maintenance(f"❌ Errore salvataggio config aMule: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/status-service', methods=['GET'])
def amule_service_status():
    """Controlla lo stato del servizio amuled via systemctl (system service)."""
    try:
        import subprocess
        s = _cdb.get_all_settings()
        service_name = s.get('amule_service', 'amule-daemon').strip()
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip()
        active = (status == 'active')
        return jsonify({'active': active, 'status': status, 'service': service_name})
    except Exception as e:
        return jsonify({'active': False, 'status': 'unknown', 'error': str(e)})


@app.route('/api/amule/bandwidth', methods=['GET'])
def amule_get_bandwidth():
    """Legge i limiti di banda correnti da amule.conf."""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            bw = client.get_bandwidth()
        return jsonify({'ok': True, **bw})
    except Exception as e:
        logger.error(f"amule_get_bandwidth: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/bandwidth', methods=['POST'])
def amule_set_bandwidth():
    """Imposta MaxDownload e MaxUpload in amule.conf (KB/s, 0=illimitato).
    Blocca se amuled è in esecuzione (le modifiche verrebbero sovrascritte).
    """
    try:
        import subprocess
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}

        # Verifica che amuled sia spento
        s = _cdb.get_all_settings()
        service_name = s.get('amule_service', 'amule-daemon').strip()
        try:
            res = subprocess.run(['systemctl', 'is-active', service_name],
                                 capture_output=True, text=True, timeout=5)
            if res.stdout.strip() == 'active':
                return jsonify({
                    'ok': False, 'service_running': True,
                    'error': f'Ferma amuled prima di modificare la banda: sudo systemctl stop {service_name}'
                }), 409
        except Exception:
            pass

        dl_kbs = int(data.get('max_download_kbs', 0))
        ul_kbs = int(data.get('max_upload_kbs', 0))
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.set_bandwidth(dl_kbs, ul_kbs)
        if ok:
            log_maintenance(f"✅ aMule: bandwidth DL={dl_kbs} KB/s UL={ul_kbs} KB/s")
            return jsonify({'ok': True, 'message': 'Limiti salvati.'})
        return jsonify({'ok': False, 'error': 'Scrittura amule.conf fallita'}), 500
    except Exception as e:
        logger.error(f"amule_set_bandwidth: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


        
@app.route('/api/amule/log', methods=['GET'])
def amule_get_log():
    """Legge le ultime righe del log di aMule in modo ultra-veloce."""
    try:
        import os
        import core.config_db as _cdb
        s = _cdb.get_all_settings()
        
        conf_path = s.get('amule_conf_path', '')
        if not conf_path:
            conf_path = os.path.join(os.path.expanduser('~'), '.aMule', 'amule.conf')
            
        log_file = os.path.join(os.path.dirname(conf_path), 'logfile')
        
        if not os.path.exists(log_file):
            return jsonify({'success': False, 'error': f'File di log non trovato in {log_file}'})
            
        lines_to_read = int(request.args.get('lines', 200))
        
        # Lettura sicura e veloce dalla fine del file (evita blocchi su log giganti)
        with open(log_file, 'rb') as f:
            f.seek(0, 2) # Vai alla fine del file
            file_size = f.tell()
            chunk_size = min(file_size, lines_to_read * 250) # Leggi solo la fine (stima larga)
            f.seek(file_size - chunk_size)
            data = f.read().decode('utf-8', errors='replace')
            lines = data.splitlines()
            
        return jsonify({'success': True, 'logs': lines[-lines_to_read:]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
        
@app.route('/api/amule/status', methods=['GET'])
def amule_status():
    """Stato della rete aMule (High ID, connessioni)."""
    try:
        from core.config import Config as _Cfg
        from core.clients.amule import AmuleClient
        _cfg_am = _Cfg()
        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1'):
            try:
                with AmuleClient(_cfg_am.qbt) as _am:
                    return jsonify(_am.get_status())
            except Exception as auth_e:
                # Se aMule rifiuta momentaneamente l'autenticazione per flooding, ignora in silenzio
                return jsonify({'ed2k_connected': False, 'kad_connected': False, 'server_name': ''})
        else:
            return jsonify({'error': 'aMule non abilitato'}), 400
    except Exception as e:
        return jsonify({'ed2k_connected': False, 'kad_connected': False})
        
@app.route('/api/amule/statistics', methods=['GET'])
def amule_get_statistics():
    """Recupera le statistiche avanzate di aMule."""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            return jsonify({'success': True, 'stats': client.get_statistics()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500        
        
@app.route('/api/amule/server/add', methods=['POST'])
def amule_add_server():
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        url = data.get("url")
        if not url:
            return jsonify({"error": "URL mancante"}), 400
        
        cfg = Config()
        with AmuleClient(cfg) as client:
            if client.add_server(url):
                return jsonify({"ok": True, "message": "Richiesta inviata ad aMule"})
        return jsonify({"error": "Fallito"}), 500
    except Exception as e:
        logger.error(f"amule_add_server: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/amule/server/update', methods=['POST'])
def amule_update_server_met():
    """Invia l'URL del server.met ad aMule e lo salva in addresses.dat"""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        import os

        data = request.get_json(force=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"ok": False, "error": "URL mancante"}), 400

        # Salva l'URL nel DB di EXTTO per ricordarselo
        _cdb.set_settings_bulk({'amule_server_met_url': url})

        # Scrive l'URL in addresses.dat per aMule
        try:
            s = _cdb.get_all_settings()
            conf_path = s.get('amule_conf_path', '')
            if not conf_path:
                conf_path = os.path.join(os.path.expanduser('~'), '.aMule', 'amule.conf')
            addr_file = os.path.join(os.path.dirname(conf_path), 'addresses.dat')
            with open(addr_file, 'w', encoding='utf-8') as f:
                f.write(url + '\n')
        except Exception as e:
            logger.warning(f"Errore scrittura addresses.dat: {e}")

        cfg = Config()
        with AmuleClient(cfg) as client:
            if client.add_server(url):
                log_maintenance(f"✅ aMule: richiesta server.met inviata ({url})")
                return jsonify({"ok": True, "message": f"Richiesta inviata ad aMule: {url}"})
        return jsonify({"ok": False, "error": "aMule non ha accettato il comando"}), 500

    except Exception as e:
        logger.error(f"amule_update_server_met: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/amule/server/connect', methods=['POST'])
def amule_connect_server():
    try:
        from core.clients.amule import AmuleClient
        data = request.get_json() or {}
        ip = data.get('ip')
        port = data.get('port')
        
        with AmuleClient() as client:
            if client.connect_server(ip, port):
                return jsonify({"ok": True})
        return jsonify({"error": "Fallito"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/amule/servers', methods=['GET'])
def amule_get_servers():
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        import os, configparser

        # Legge l'URL dal DB
        s = _cdb.get_all_settings()
        met_url = s.get('amule_server_met_url', '')

        conf_path = s.get('amule_conf_path', '')
        if not conf_path:
            conf_path = os.path.join(os.path.expanduser('~'), '.aMule', 'amule.conf')
        amule_dir = os.path.dirname(conf_path)

        # BUG5 FIX: Legge met_url in ordine di priorità:
        # 1. addresses.dat (impostato dall'interfaccia web di amuled)
        # 2. amule.conf chiave Ed2kServersUrl (impostato dalla GUI aMule → Preferenze)
        # 3. amule.conf [WebServer] URL_2 (fallback)
        # 4. DB EXTTO
        addr_file = os.path.join(amule_dir, 'addresses.dat')
        try:
            if os.path.exists(addr_file):
                with open(addr_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped and stripped.startswith('http'):
                            met_url = stripped
                            break
        except Exception:
            pass

        # Fallback: leggi Ed2kServersUrl / URL_2 da amule.conf
        if not met_url:
            try:
                if os.path.exists(conf_path):
                    cfg_parser = configparser.RawConfigParser()
                    cfg_parser.optionxform = str
                    cfg_parser.read(conf_path, encoding='utf-8')
                    # Campo principale: [eMule] Ed2kServersUrl
                    for key in ('Ed2kServersUrl', 'ed2kserversurl'):
                        if cfg_parser.has_option('eMule', key):
                            v = cfg_parser.get('eMule', key).strip()
                            if v.startswith('http'):
                                met_url = v
                                break
                    # Fallback: [WebServer] URL_2 (seconda URL configurata)
                    if not met_url and cfg_parser.has_section('WebServer'):
                        for key in ('URL_2', 'URL_1'):
                            if cfg_parser.has_option('WebServer', key):
                                v = cfg_parser.get('WebServer', key).strip()
                                if v.startswith('http') and 'server.met' in v:
                                    met_url = v
                                    break
            except Exception as e:
                logger.debug(f"amule_get_servers: lettura URL da amule.conf: {e}")

        cfg = Config()
        with AmuleClient(cfg) as client:
            servers = client.get_server_list()
            # Arricchisce il server connesso con users/files/ping da status
            try:
                st = client.get_status()
                conn_addr = st.get('server_address', '')
                if conn_addr:
                    for srv in servers:
                        if srv.get('address') == conn_addr:
                            srv['users']    = st.get('server_users', 0) or st.get('ed2k_users', 0)
                            srv['files']    = st.get('server_files', 0) or st.get('ed2k_files', 0)
                            srv['ping']     = st.get('server_ping', 0)
                            srv['connected'] = True
                            break
            except Exception:
                pass
            return jsonify({'servers': servers, 'met_url': met_url})
    except Exception as e:
        logger.error(f"amule_get_servers: {e}")
        return jsonify({'servers': [], 'error': str(e)}), 500



def _amule_write_conf(conf_path: str, settings: dict) -> None:
    """Aggiorna amule.conf nel path indicato.

    Tocca SOLO le sezioni EC e porte — tutto il resto viene preservato.
    Se il file non esiste non viene creato (amuled deve averlo già inizializzato).
    """
    import os, configparser, hashlib

    if not os.path.exists(conf_path):
        raise FileNotFoundError(
            f"amule.conf non trovato in {conf_path}. "
            f"Avvia amuled almeno una volta per generarlo, poi riprova."
        )

    cfg = configparser.RawConfigParser()
    cfg.optionxform = str   # preserva maiuscole/minuscole
    cfg.read(conf_path, encoding='utf-8')

    def _ensure(section):
        if not cfg.has_section(section):
            cfg.add_section(section)

    # ── ExternalConnect ──────────────────────────────────────────────────────
    _ensure('ExternalConnect')
    cfg.set('ExternalConnect', 'AcceptExternalConnections', '1')

    ec_port = str(settings.get('amule_port', '')).strip()
    if ec_port:
        cfg.set('ExternalConnect', 'ECPort', ec_port)

    ec_pass = str(settings.get('amule_password', '')).strip()
    if ec_pass:
        # aMule salva l'MD5 della password
        pwd_hash = hashlib.md5(ec_pass.encode('utf-8')).hexdigest()
        cfg.set('ExternalConnect', 'ECPassword', pwd_hash)

    # ── eMule: porte e cartelle (solo se forniti) ─────────────────────────────
    _ensure('eMule')

    tcp = str(settings.get('amule_tcp_port', '')).strip()
    if tcp:
        cfg.set('eMule', 'Port', tcp)

    udp = str(settings.get('amule_udp_port', '')).strip()
    if udp:
        cfg.set('eMule', 'UDPPort', udp)

    incoming = str(settings.get('amule_incoming', '')).strip()
    if incoming:
        cfg.set('eMule', 'IncomingDir', incoming)

    temp = str(settings.get('amule_temp', '')).strip()
    if temp:
        cfg.set('eMule', 'TempDir', temp)

    with open(conf_path, 'w', encoding='utf-8') as f:
        cfg.write(f)


@app.route('/api/amule/downloads', methods=['GET'])
def amule_get_downloads():
    """Lista download in corso."""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            return jsonify({'downloads': client.get_download_queue()})
    except Exception as e:
        logger.error(f"amule_get_downloads: {e}")
        return jsonify({'downloads': [], 'error': str(e)}), 500


@app.route('/api/amule/recover-parts', methods=['GET'])
def amule_recover_parts():
    """Scansiona la TempDir di aMule per file .part orfani e li re-inietta via amulecmd."""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        import configparser, struct, os

        s = _cdb.get_all_settings()
        conf_path = s.get('amule_conf_path', '')
        if not conf_path:
            conf_path = os.path.join(os.path.expanduser('~'), '.aMule', 'amule.conf')

        temp_dir = ''
        try:
            cfg_parser = configparser.RawConfigParser()
            cfg_parser.optionxform = str
            cfg_parser.read(conf_path, encoding='utf-8')
            if cfg_parser.has_option('eMule', 'TempDir'):
                temp_dir = cfg_parser.get('eMule', 'TempDir').strip()
        except Exception as e:
            logger.warning(f"recover-parts: lettura TempDir: {e}")

        if not temp_dir or not os.path.isdir(temp_dir):
            return jsonify({'ok': False, 'error': f'TempDir non trovata: {temp_dir!r}'}), 400

        def parse_part_met(path):
            """Legge (hash_hex_upper, filename, filesize) da .part.met di aMule.
            Supporta versioni 0x0e, 0x0f e 0xe0.
            """
            with open(path, 'rb') as f:
                data = f.read()
            if len(data) < 23:
                raise ValueError(f"file troppo corto ({len(data)} bytes)")
            version = data[0]
            if version not in (0x0e, 0x0f, 0xe0):
                raise ValueError(f"versione met sconosciuta: 0x{version:02x}")

            if version == 0xe0:
                # [0]=ver [1:5]=date [5:21]=hash [21:23]=hashset_count
                file_hash = data[5:21].hex().upper()
                hashset_count = struct.unpack_from('<H', data, 21)[0]
                offset = 23 + hashset_count * 16
            else:
                # [0]=ver [1:17]=hash [17:19]=hashset_count
                file_hash = data[1:17].hex().upper()
                hashset_count = struct.unpack_from('<H', data, 17)[0]
                offset = 19 + hashset_count * 16

            if offset + 4 > len(data):
                raise ValueError(f"offset tag fuori range: {offset} > {len(data)}")

            tag_count = struct.unpack_from('<I', data, offset)[0]
            offset += 4

            FT_FILENAME    = 0x01
            FT_FILESIZE    = 0x02
            FT_FILESIZE_HI = 0x3a
            TYPE_STRING    = 0x02
            TYPE_UINT64    = 0x09
            TYPE_UINT32    = 0x03
            TYPE_UINT16    = 0x05
            TYPE_UINT8     = 0x08

            filename = ''
            filesize_lo = 0
            filesize_hi = 0
            filesize64  = 0

            for _ in range(min(tag_count, 300)):
                if offset >= len(data):
                    break
                tag_type = data[offset]; offset += 1
                compressed = bool(tag_type & 0x80)
                tag_type &= 0x7f

                if compressed:
                    if offset >= len(data): break
                    tag_name = data[offset]; offset += 1
                else:
                    if offset + 2 > len(data): break
                    name_len = struct.unpack_from('<H', data, offset)[0]; offset += 2
                    if offset + name_len > len(data): break
                    tag_name_b = data[offset:offset+name_len]; offset += name_len
                    tag_name = tag_name_b[0] if name_len == 1 else -1

                if tag_type == TYPE_STRING:
                    if offset + 2 > len(data): break
                    slen = struct.unpack_from('<H', data, offset)[0]; offset += 2
                    if offset + slen > len(data): break
                    val_b = data[offset:offset+slen]; offset += slen
                    if tag_name == FT_FILENAME and not filename:
                        try:    filename = val_b.decode('utf-8')
                        except: filename = val_b.decode('latin-1', errors='replace')
                elif tag_type == TYPE_UINT64:
                    if offset + 8 > len(data): break
                    val = struct.unpack_from('<Q', data, offset)[0]; offset += 8
                    if tag_name == FT_FILESIZE: filesize64 = val
                elif tag_type == TYPE_UINT32:
                    if offset + 4 > len(data): break
                    val = struct.unpack_from('<I', data, offset)[0]; offset += 4
                    if tag_name == FT_FILESIZE:    filesize_lo = val
                    elif tag_name == FT_FILESIZE_HI: filesize_hi = val
                elif tag_type == TYPE_UINT16:
                    if offset + 2 > len(data): break
                    offset += 2
                elif tag_type == TYPE_UINT8:
                    if offset + 1 > len(data): break
                    offset += 1
                else:
                    break  # tipo sconosciuto, stop sicuro

            if filesize64:
                filesize = filesize64
            elif filesize_lo or filesize_hi:
                filesize = (filesize_hi << 32) | filesize_lo
            else:
                filesize = 0

            return file_hash, filename, filesize

        # Ottieni hash già in coda per non duplicare
        cfg = Config()
        active_hashes = set()
        try:
            with AmuleClient(cfg) as client:
                for d in client.get_download_queue():
                    active_hashes.add(d.get('hash', '').upper())
        except Exception:
            pass

        recovered = []
        skipped   = []
        errors    = []

        for fname in sorted(os.listdir(temp_dir)):
            if not fname.endswith('.part.met'):
                continue
            met_path  = os.path.join(temp_dir, fname)
            part_path = met_path[:-4]  # rimuove '.met'

            if not os.path.exists(part_path):
                errors.append(f'{fname}: file .part mancante su disco')
                continue

            try:
                file_hash, filename, filesize = parse_part_met(met_path)
            except Exception as e:
                errors.append(f'{fname}: {e}')
                continue

            if file_hash in active_hashes:
                skipped.append(filename or fname)
                continue

            # Fallback nome: usa il numero del .part se il tag era assente
            if not filename:
                filename = fname.replace('.part.met', '.part')

            # Dimensione: usa quella dal .part.met; se 0, usa dimensione file .part su disco
            if filesize == 0:
                filesize = os.path.getsize(part_path)

            safe_name = re.sub(r'[|]', '-', filename)
            ed2k = f'ed2k://|file|{safe_name}|{filesize}|{file_hash}|/'

            try:
                with AmuleClient(cfg) as client:
                    ok = client.add(ed2k)
                if ok:
                    recovered.append({'name': filename, 'hash': file_hash,
                                      'size': filesize, 'ed2k': ed2k})
                    active_hashes.add(file_hash)
                    log_maintenance(f'✅ aMule recover-parts: re-iniettato {filename}')
                else:
                    errors.append(f'{filename}: amulecmd Add rifiutato')
            except Exception as e:
                errors.append(f'{filename}: {e}')

        return jsonify({
            'ok': True, 'recovered': recovered,
            'skipped': skipped, 'errors': errors, 'temp_dir': temp_dir,
        })

    except Exception as e:
        logger.error(f'amule_recover_parts: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/add', methods=['POST'])
def amule_add_link():
    """Aggiunge un link ed2k://. Estrae size/hash dal link per mostrare subito la dimensione."""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        link = data.get('link', '').strip()
        if not link.startswith('ed2k://'):
            return jsonify({'ok': False, 'error': 'Link non ed2k'}), 400
        size_from_link = 0
        hash_from_link = ''
        name_from_link = ''
        try:
            parts = link.rstrip('/').split('|')
            if len(parts) >= 5:
                name_from_link = parts[3]
                size_from_link = int(parts[4]) if parts[4].isdigit() else 0
            if len(parts) >= 6:
                hash_from_link = parts[5]
        except Exception:
            pass
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.add(link)
        return jsonify({'ok': ok, 'size': size_from_link,
                        'hash': hash_from_link, 'name': name_from_link})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/pause', methods=['POST'])
def amule_pause():
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        file_hash = data.get('hash', '').strip()
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.pause_download(file_hash)
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/resume', methods=['POST'])
def amule_resume():
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        file_hash = data.get('hash', '').strip()
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.resume_download(file_hash)
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/cancel', methods=['POST'])
def amule_cancel():
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        file_hash = data.get('hash', '').strip()
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.cancel_download(file_hash)
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/uploads', methods=['GET'])
def amule_get_uploads():
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            return jsonify({'uploads': client.get_upload_queue()})
    except Exception as e:
        return jsonify({'uploads': [], 'error': str(e)}), 500


_AMULE_SHARED_FILES_CACHE = None

@app.route('/api/amule/shared', methods=['GET'])
def amule_get_shared():
    global _AMULE_SHARED_FILES_CACHE
    try:
        force = request.args.get('force', '0') == '1'
        if not force and _AMULE_SHARED_FILES_CACHE is not None:
            return jsonify({'shared': _AMULE_SHARED_FILES_CACHE})
        
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            files = client.get_shared_files()
            _AMULE_SHARED_FILES_CACHE = files
            return jsonify({'shared': files})
    except Exception as e:
        return jsonify({'shared': [], 'error': str(e)}), 500


@app.route('/api/amule/shared/reload', methods=['POST'])
def amule_reload_shared():
    global _AMULE_SHARED_FILES_CACHE
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.reload_shared()
        _AMULE_SHARED_FILES_CACHE = None
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/shared/add', methods=['POST'])
def amule_add_shared():
    try:
        import json, os
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        path = data.get('path', '').strip()
        
        # Lettura sicura del booleano (javascript a volte manda 'true' come testo)
        rec_val = data.get('recursive', False)
        recursive = str(rec_val).lower() in ('true', '1', 'yes') if isinstance(rec_val, str) else bool(rec_val)
        
        if not path:
            return jsonify({'ok': False, 'error': 'Percorso mancante'}), 400

        path_abs = os.path.abspath(path.rstrip('/'))

        # Lettura ultra-sicura dal DB
        settings = _cdb.get_all_settings()
        raw_rec = settings.get('amule_recursive_dirs', [])
        if isinstance(raw_rec, str):
            try: rec_dirs = json.loads(raw_rec)
            except: rec_dirs = []
        else:
            rec_dirs = list(raw_rec)

        # Salva in memoria
        if recursive:
            if path_abs not in rec_dirs:
                rec_dirs.append(path_abs)
                _cdb.set_settings_bulk({'amule_recursive_dirs': json.dumps(rec_dirs)})
        
        cfg = Config()
        with AmuleClient(cfg) as client:
            added_count = client.add_shared_dir(path_abs, recursive)
            
        global _AMULE_SHARED_FILES_CACHE
        _AMULE_SHARED_FILES_CACHE = None
        
        msg = f"Aggiunta {path_abs}" if not recursive else f"Aggiunte {added_count} cartelle da {path_abs}"
        log_maintenance(f"✅ aMule: {msg}")
        return jsonify({'ok': True, 'message': msg})
    except Exception as e:
        logger.error(f"amule_add_shared: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/amule/shared/remove', methods=['POST'])
def amule_remove_shared():
    try:
        import json, os
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        path = data.get('path', '').strip()
        if not path:
            return jsonify({'ok': False, 'error': 'Percorso mancante'}), 400

        path_abs = os.path.abspath(path.rstrip('/'))

        # Lettura ultra-sicura dal DB
        settings = _cdb.get_all_settings()
        raw_rec = settings.get('amule_recursive_dirs', [])
        if isinstance(raw_rec, str):
            try: rec_dirs = json.loads(raw_rec)
            except: rec_dirs = []
        else:
            rec_dirs = list(raw_rec)
            
        if path_abs in rec_dirs:
            rec_dirs.remove(path_abs)
            _cdb.set_settings_bulk({'amule_recursive_dirs': json.dumps(rec_dirs)})

        cfg = Config()
        with AmuleClient(cfg) as client:
            client.remove_shared_dir(path_abs)
            
        global _AMULE_SHARED_FILES_CACHE
        _AMULE_SHARED_FILES_CACHE = None
        
        log_maintenance(f"✅ aMule: cartella rimossa: {path_abs}")
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"amule_remove_shared: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/amule/shared/dirs', methods=['GET'])
def amule_get_shared_dirs():
    try:
        import json, os
        from core.clients.amule import AmuleClient
        from core.config import Config

        # Lettura ultra-sicura dal DB
        settings = _cdb.get_all_settings()
        raw_rec = settings.get('amule_recursive_dirs', [])
        if isinstance(raw_rec, str):
            try: rec_dirs = json.loads(raw_rec)
            except: rec_dirs = []
        else:
            rec_dirs = list(raw_rec)

        cfg = Config()
        with AmuleClient(cfg) as client:
            raw_dirs = client.get_shared_dirs()

        final_dirs = []
        rec_abs_list = [os.path.abspath(r.rstrip('/')) for r in rec_dirs]

        for d in raw_dirs:
            p = os.path.abspath(d['path'].rstrip('/'))
            is_subfolder = False
            
            for r_dir_abs in rec_abs_list:
                if p != r_dir_abs and p.startswith(r_dir_abs + os.sep):
                    is_subfolder = True
                    break
            
            if is_subfolder:
                continue

            d['is_recursive'] = (p in rec_abs_list)
            final_dirs.append(d)

        return jsonify({'dirs': final_dirs})
    except Exception as e:
        logger.error(f"amule_get_shared_dirs: {e}")
        return jsonify({'dirs': [], 'error': str(e)}), 500


@app.route('/api/amule/search', methods=['POST'])
def amule_search():
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        query   = data.get('query', '').strip()
        network = data.get('network', 'global')
        ext     = data.get('extension', '')
        if not query:
            return jsonify({'results': [], 'error': 'Query vuota'}), 400
        cfg = Config()
        with AmuleClient(cfg) as client:
            results = client.search(query, network, ext)
        msg = None if results else 'Nessun risultato trovato (rete ed2k potrebbe non essere pronta)'
        return jsonify({'results': results, 'count': len(results), 'message': msg})
    except Exception as e:
        logger.error(f"amule_search: {e}")
        return jsonify({'results': [], 'error': str(e)}), 500


@app.route('/api/amule/search/download', methods=['POST'])
def amule_search_download():
    """Scarica il risultato N via 'amulecmd Download N'."""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        data = request.get_json(force=True) or {}
        idx  = int(data.get('idx', -1))
        if idx < 0:
            return jsonify({'ok': False, 'error': 'idx mancante'}), 400
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.download_result(idx)
        return jsonify({'ok': ok})
    except Exception as e:
        logger.error(f"amule_search_download: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/amule/port-check', methods=['GET'])
def amule_port_check():
    """Verifica se le porte TCP/UDP di aMule sono aperte."""
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            result = client.check_ports()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/amule/all', methods=['GET'])
def amule_get_all():
    """Rotta consolidata: status + downloads + uploads.
    
    NOTA: _run_sections NON funziona con amulecmd perché i marker EXTTO_SEP_N
    sono comandi sconosciuti e amulecmd stampa solo "> Syntax error" senza
    ripetere il nome del comando — il marker non appare mai nell'output.
    Usiamo 3 subprocess separati. Il costo è accettabile (3 connessioni EC
    ogni 10s) e amuled non attiva il flood-protection a questa frequenza.
    """
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        cfg = Config()
        with AmuleClient(cfg) as client:
            status    = client.get_status()
            downloads = client._parse_file_list(client._run('Show dl'), is_download=True)
            uploads   = client._parse_upload_list(client._run('Show ul'))
        return jsonify({'status': status, 'downloads': downloads, 'uploads': uploads})
    except Exception as e:
        logger.error(f"amule_get_all: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/amule/ed2k-handler', methods=['GET'])
def amule_ed2k_handler():
    """Riceve link ed2k:// da protocol handler del browser/OS.
    Chiamata da: http://localhost:PORT/api/amule/ed2k-handler?link=ed2k://...
    """
    try:
        from core.clients.amule import AmuleClient
        from core.config import Config
        link = request.args.get('link', '').strip()
        if not link or not link.startswith('ed2k://'):
            return f"Link non valido: {link}", 400
        cfg = Config()
        with AmuleClient(cfg) as client:
            ok = client.add(link)
        log_maintenance(f"✅ aMule: link ed2k aggiunto via handler: {link[:80]}")
        return (f"OK — Link aggiunto ad aMule: {link[:80]}", 200) if ok else ("Errore", 500)
    except Exception as e:
        return f"Errore: {e}", 500


@app.route('/api/amule/ed2k-install', methods=['GET'])
def amule_ed2k_install():
    """Genera script per registrare ed2k:// come protocol handler su Linux."""
    import os, getpass
    try:
        s    = _cdb.get_all_settings()
        port = s.get('flask_port', '5000')
        host = s.get('flask_host', '127.0.0.1')
        base_url = f"http://{host}:{port}"
        handler_script = f"""#!/bin/bash
# extto-ed2k-handler.sh — Invia link ed2k:// ad EXTTO/aMule
LINK="$1"
curl -sf "{base_url}/api/amule/ed2k-handler?link=$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$LINK")" \
    && notify-send "aMule" "Link ed2k aggiunto" 2>/dev/null || true
"""
        desktop_file = f"""[Desktop Entry]
Name=EXTTO ed2k Handler
Exec={os.path.expanduser('~')}/.local/bin/extto-ed2k-handler.sh %u
Type=Application
NoDisplay=true
MimeType=x-scheme-handler/ed2k;
"""
        install_cmds = f"""mkdir -p ~/.local/bin ~/.local/share/applications
cat > ~/.local/bin/extto-ed2k-handler.sh << 'SCRIPT'
{handler_script}SCRIPT
chmod +x ~/.local/bin/extto-ed2k-handler.sh
cat > ~/.local/share/applications/ed2k.desktop << 'DESKTOP'
{desktop_file}DESKTOP
update-desktop-database ~/.local/share/applications
xdg-mime default ed2k.desktop x-scheme-handler/ed2k
echo "✓ Protocol handler ed2k:// registrato. Riavvia il browser."
"""
        return jsonify({
            'success': True,
            'handler_script': handler_script,
            'desktop_file':   desktop_file,
            'install_cmds':   install_cmds,
            'base_url':       base_url,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/amule/generate-service', methods=['GET'])
def amule_generate_service():
    """Genera il contenuto del file .service systemd per amuled (user service)."""
    try:
        import os, getpass
        s = _cdb.get_all_settings()
        service_name = s.get('amule_service', 'amule-daemon').strip()
        amuled_bin   = '/usr/bin/amuled'
        content = f"""[Unit]
Description=aMule Daemon (gestito da EXTTO)
After=network.target

[Service]
Type=forking
ExecStart={amuled_bin} -f
Restart=on-failure
RestartSec=10s
TimeoutStopSec=30s

[Install]
WantedBy=default.target
"""
        install_cmd = (
            f'mkdir -p ~/.config/systemd/user/ && '
            f'cp {service_name}.service ~/.config/systemd/user/ && '
            f'systemctl --user daemon-reload && '
            f'systemctl --user enable {service_name}'
        )
        return jsonify({
            'success': True,
            'filename': f'{service_name}.service',
            'content': content,
            'install_cmd': install_cmd,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500




@app.route('/api/restart-scrape', methods=['POST'])
def restart_scrape():
    """
    Riavvio Infallibile: Chiude brutalmente il processo.
    Grazie a 'Restart=always' nel file service, systemd lo riavvierà automaticamente.
    """
    try:
        log_maintenance("🔄 Riavvio richiesto dalla Web UI. Spegnimento in corso, systemd lo riavvierà tra 10 secondi...")
        
        def _kill_system():
            import time, os, signal
            # Aspetta 1.5 secondi per permettere al server di rispondere "OK" al browser
            time.sleep(1.5) 
            
            # 1. Invia il segnale di stop al processo padre (il motore extto3.py)
            try:
                os.kill(os.getppid(), signal.SIGTERM)
            except Exception as e:
                logger.warning(f"SIGTERM to parent: {e}")
                pass
                
            # 2. Chiude se stesso (la Web UI)
            os._exit(1)
            
        import threading
        threading.Thread(target=_kill_system, daemon=True).start()
        
        return jsonify({'success': True, 'message': 'Riavvio innescato con successo.'}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/set-speed-limits', methods=['POST'])
def set_speed_limits():
    """Imposta i limiti di velocità per il client attivo (universale)."""
    try:
        data = request.get_json(silent=True) or {}
        dl_kbps = int(data.get('dl_kbps', 0))
        ul_kbps = int(data.get('ul_kbps', 0))
        
        config = parse_series_config()
        settings = config.get('settings', {})
        
        # --- 1. GESTIONE LIBTORRENT (EMBEDDED) ---
        if str(settings.get('libtorrent_enabled', 'no')).lower() in ['yes', 'true', '1']:
            # SALVATAGGIO IN KB/s NEL FILE DI CONFIGURAZIONE
            config['settings']['libtorrent_dl_limit'] = str(dl_kbps)
            config['settings']['libtorrent_ul_limit'] = str(ul_kbps)
            save_series_config(config)
            
            # Applica immediatamente alla sessione libtorrent attiva
            import requests as _req
            try:
                r = _req.post(
                    f'http://127.0.0.1:{get_engine_port()}/api/torrents/apply_settings',
                    json={'dl_kbps': dl_kbps, 'ul_kbps': ul_kbps},
                    timeout=15
                )
                if r.status_code == 200:
                    return jsonify({'success': True, 'message': 'Limiti applicati a libtorrent e salvati'})
            except Exception as e:
                logger.warning(f"set_limits libtorrent: {e}")
            return jsonify({'success': True, 'message': 'Limiti salvati nel DB. Motore non raggiungibile, riavvia per applicarli.'})

        # --- 2. GESTIONE QBITTORRENT ---
        elif str(settings.get('qbittorrent_enabled', 'no')).lower() in ['yes', 'true', '1']:
            qb_url = settings.get('qbittorrent_url', '')
            qb_user = settings.get('qbittorrent_username', '')
            qb_pass = settings.get('qbittorrent_password', '')
            try:
                import requests
                session = requests.Session()
                r = session.post(f"{qb_url}/api/v2/auth/login", data={'username': qb_user, 'password': qb_pass}, timeout=5)
                if r.text == 'Ok.':
                    # qBittorrent vuole i limiti in Bytes/s
                    dl_bytes = dl_kbps * 1024
                    ul_bytes = ul_kbps * 1024
                    session.post(f"{qb_url}/api/v2/transfer/setDownloadLimit", data={'limit': dl_bytes}, timeout=5)
                    session.post(f"{qb_url}/api/v2/transfer/setUploadLimit", data={'limit': ul_bytes}, timeout=5)
                    return jsonify({'success': True, 'message': 'Limiti inviati a qBittorrent'})
                return jsonify({'success': False, 'error': 'Login qBittorrent fallito'})
            except Exception as e:
                return jsonify({'success': False, 'error': f'Impossibile contattare qBit: {str(e)}'})

        # --- 3. GESTIONE TRANSMISSION ---
        elif str(settings.get('transmission_enabled', 'no')).lower() in ['yes', 'true', '1']:
            tr_url = settings.get('transmission_url', '')
            tr_user = settings.get('transmission_username', '')
            tr_pass = settings.get('transmission_password', '')
            try:
                import requests
                auth = (tr_user, tr_pass) if tr_user else None
                r = requests.post(tr_url, auth=auth, timeout=5)
                session_id = r.headers.get('X-Transmission-Session-Id', '')
                
                # Transmission vuole i limiti in KB/s
                payload = {
                    'method': 'session-set',
                    'arguments': {
                        'speed-limit-down': dl_kbps,
                        'speed-limit-down-enabled': dl_kbps > 0,
                        'speed-limit-up': ul_kbps,
                        'speed-limit-up-enabled': ul_kbps > 0
                    }
                }
                headers = {'X-Transmission-Session-Id': session_id}
                r = requests.post(tr_url, json=payload, headers=headers, auth=auth, timeout=5)
                if r.status_code == 409:
                    headers['X-Transmission-Session-Id'] = r.headers.get('X-Transmission-Session-Id', '')
                    r = requests.post(tr_url, json=payload, headers=headers, auth=auth, timeout=5)
                
                if r.status_code == 200:
                    return jsonify({'success': True, 'message': 'Limiti inviati a Transmission'})
                return jsonify({'success': False, 'error': 'Impossibile impostare limiti su Transmission'})
            except Exception as e:
                return jsonify({'success': False, 'error': f'Impossibile contattare Transmission: {str(e)}'})

        return jsonify({'success': False, 'error': 'Nessun client torrent supportato è abilitato.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/send-magnet', methods=['POST'])
def send_magnet():
    """Invia magnet al client torrent configurato"""
    try:
        data = request.get_json(silent=True) or {}
        magnet = data.get('magnet', '')
        save_path = data.get('save_path', '').strip()
        
        if not magnet:
            return jsonify({'success': False, 'error': 'Magnet mancante'}), 400
        
        # Leggi configurazione per sapere quale client usare
        config = parse_series_config()
        settings = config.get('settings', {})
        
        # Prova qBittorrent
        if settings.get('qbittorrent_enabled') == 'yes':
            qb_url = settings.get('qbittorrent_url', '')
            qb_user = settings.get('qbittorrent_username', '')
            qb_pass = settings.get('qbittorrent_password', '')
            try:
                login_url = f"{qb_url}/api/v2/auth/login"
                session = requests.Session()
                r = session.post(login_url, data={'username': qb_user, 'password': qb_pass}, timeout=5)
                if r.text == 'Ok.':
                    add_data = {'urls': magnet, 'paused': 'true'}
                    if save_path: add_data['savepath'] = save_path
                    r = session.post(f"{qb_url}/api/v2/torrents/add", data=add_data, timeout=5)
                    if r.text == 'Ok.':
                        push_notification('download', 'Scarico avviato', 'Torrent inviato a qBittorrent', {'magnet': magnet[:80]})
                        _save_tag_for_magnet(magnet, 'Manuale')
                        return jsonify({'success': True, 'message': 'Inviato a qBittorrent'})
            except Exception as e:
                pass
        
        # Prova Transmission
        if settings.get('transmission_enabled') == 'yes':
            tr_url = settings.get('transmission_url', '')
            tr_user = settings.get('transmission_username', '')
            tr_pass = settings.get('transmission_password', '')
            try:
                auth = (tr_user, tr_pass) if tr_user else None
                r = requests.post(tr_url, auth=auth, timeout=5)
                session_id = r.headers.get('X-Transmission-Session-Id', '')
                payload = {'method': 'torrent-add', 'arguments': {'filename': magnet, 'paused': True}}
                if save_path: payload['arguments']['download-dir'] = save_path
                headers = {'X-Transmission-Session-Id': session_id}
                r = requests.post(tr_url, json=payload, headers=headers, auth=auth, timeout=5)
                if r.status_code == 409:
                    session_id = r.headers.get('X-Transmission-Session-Id', session_id)
                    headers['X-Transmission-Session-Id'] = session_id
                    r = requests.post(tr_url, json=payload, headers=headers, auth=auth, timeout=5)
                if r.status_code == 200:
                    push_notification('download', 'Scarico avviato', 'Torrent inviato a Transmission', {'magnet': magnet[:80]})
                    _save_tag_for_magnet(magnet, 'Manuale')
                    return jsonify({'success': True, 'message': 'Inviato a Transmission'})
            except Exception as e:
                pass
        
        # Prova libtorrent (embedded)
        if settings.get('libtorrent_enabled') == 'yes':
            try:
                payload = {'magnet': magnet}
                if save_path: payload['save_path'] = save_path
                r = requests.post(
                    f'http://127.0.0.1:{get_engine_port()}/api/torrents/add',
                    json=payload,
                    timeout=10
                )
                if r.status_code == 200 and r.json().get('ok'):
                    push_notification('download', 'Scarico avviato', 'Torrent inviato a libtorrent', {'magnet': magnet[:80]})
                    _save_tag_for_magnet(magnet, 'Manuale')
                    _h = r.json().get('hash', '')
                    if _h: _save_tag_for_hash(_h, 'Manuale')
                    return jsonify({'success': True, 'message': 'Inviato a libtorrent'})
            except Exception as e:
                pass

        return jsonify({'success': False, 'error': 'Nessun client torrent configurato o disponibile'}), 500
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/upload-torrent', methods=['POST'])
def upload_torrent():
    """Upload e invio file .torrent al client"""
    try:
        data = request.json or {}
        filename = data.get('filename', 'uploaded.torrent')
        file_data = data.get('data', '')
        download_now = data.get('download_now', True)
        save_path = data.get('save_path', '').strip()
        
        if not file_data:
            return jsonify({'success': False, 'error': 'File mancante'}), 400
        
        import base64
        torrent_bytes = base64.b64decode(file_data)
        config = parse_series_config()
        settings = config.get('settings', {})
        
        if not download_now:
            return jsonify({'success': True, 'message': 'File torrent ricevuto (non inviato al client)'})
        
        # Prova qBittorrent
        if settings.get('qbittorrent_enabled') == 'yes':
            qb_url = settings.get('qbittorrent_url', '')
            qb_user = settings.get('qbittorrent_username', '')
            qb_pass = settings.get('qbittorrent_password', '')
            try:
                login_url = f"{qb_url}/api/v2/auth/login"
                session = requests.Session()
                r = session.post(login_url, data={'username': qb_user, 'password': qb_pass}, timeout=5)
                if r.text == 'Ok.':
                    add_url = f"{qb_url}/api/v2/torrents/add"
                    files = {'torrents': (filename, torrent_bytes, 'application/x-bittorrent')}
                    add_data = {'paused': 'true'}
                    if save_path: add_data['savepath'] = save_path
                    r = session.post(add_url, files=files, data=add_data, timeout=5)
                    if r.text == 'Ok.':
                        return jsonify({'success': True, 'message': 'File .torrent inviato a qBittorrent'})
            except Exception as e:
                pass
        
        # Prova Transmission
        if settings.get('transmission_enabled') == 'yes':
            tr_url = settings.get('transmission_url', '')
            tr_user = settings.get('transmission_username', '')
            tr_pass = settings.get('transmission_password', '')
            try:
                r = requests.post(tr_url, auth=(tr_user, tr_pass), timeout=5)
                session_id = r.headers.get('X-Transmission-Session-Id', '')
                torrent_b64 = base64.b64encode(torrent_bytes).decode('utf-8')
                payload = {'method': 'torrent-add', 'arguments': {'metainfo': torrent_b64, 'paused': True}}
                if save_path: payload['arguments']['download-dir'] = save_path
                headers = {'X-Transmission-Session-Id': session_id}
                r = requests.post(tr_url, json=payload, headers=headers, auth=(tr_user, tr_pass), timeout=5)
                if r.status_code == 200:
                    return jsonify({'success': True, 'message': 'File .torrent inviato a Transmission'})
            except Exception as e:
                pass
        
        # Prova libtorrent (embedded)
        if settings.get('libtorrent_enabled', 'yes') in ('yes', 'true', '1'):
            try:
                import base64 as _b64
                logger.info(f"📤 Sending file packet to local libtorrent (Port {get_engine_port()})...")
                payload = {'data': _b64.b64encode(torrent_bytes).decode('utf-8'), 'filename': filename}
                if save_path: payload['save_path'] = save_path
                r = requests.post(
                    f'http://127.0.0.1:{get_engine_port()}/api/torrents/add_torrent_file',
                    json=payload,
                    timeout=10
                )
                if r.status_code == 200 and r.json().get('ok'):
                    _lt_hash = r.json().get('hash', '')
                    if _lt_hash:
                        _save_tag_for_hash(_lt_hash, 'Manuale')
                    return jsonify({'success': True, 'message': 'File .torrent inviato a libtorrent', 'hash': _lt_hash})
                else:
                    err_msg = r.json().get('error', 'Sconosciuto') if r.status_code == 200 else r.text
                    return jsonify({'success': False, 'error': f'Errore libtorrent: {err_msg}'})
            except Exception as e:
                pass

        return jsonify({'success': False, 'error': 'Nessun client torrent configurato o disponibile'}), 500
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Proxy per torrents (timeout aumentato a 30s)

# ---------------------------------------------------------------------------
# TORRENT META (tag + stato UI) — SQLite, tabella torrent_meta in extto_series.db
# Sostituisce torrent_tags.json — migrazione automatica al primo accesso
# ---------------------------------------------------------------------------

_HASH_RE_TAG = __import__('re').compile(r'btih:([a-fA-F0-9]{40})', __import__('re').I)

def _torrent_meta_db():
    """Connessione al DB serie; crea torrent_meta se non esiste e migra torrent_tags.json."""
    import sqlite3 as _sq
    conn = _sq.connect(DB_FILE, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS torrent_meta (
            hash        TEXT PRIMARY KEY,
            tag         TEXT DEFAULT '',
            ui_state    TEXT DEFAULT '',
            progress    REAL DEFAULT 0,
            paused      INTEGER DEFAULT 0,
            total_size  INTEGER DEFAULT 0,
            downloaded  INTEGER DEFAULT 0,
            name        TEXT DEFAULT '',
            updated_at  INTEGER DEFAULT 0,
            no_rename   INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    # Migration: aggiunge no_rename se il DB è precedente
    try:
        conn.execute("ALTER TABLE torrent_meta ADD COLUMN no_rename INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass  # Colonna già esistente
    # Migrazione una-tantum da torrent_tags.json
    _migrate_tags_json(conn)
    return conn

_tags_migrated = False
def _migrate_tags_json(conn):
    """Importa torrent_tags.json nel DB se non ancora migrato."""
    global _tags_migrated
    if _tags_migrated:
        return
    _tags_migrated = True
    try:
        import json as _json
        tags_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'torrent_tags.json')
        if not os.path.exists(tags_file):
            return
        with open(tags_file, 'r', encoding='utf-8') as f:
            tags = _json.load(f)
        if not tags:
            return
        conn.executemany("""
            INSERT OR IGNORE INTO torrent_meta (hash, tag, updated_at)
            VALUES (?, ?, strftime('%s','now'))
        """, [(h.lower(), t) for h, t in tags.items() if t])
        conn.commit()
        # Rinomina il file originale come backup
        os.rename(tags_file, tags_file + '.migrated')
        logger.info(f"[torrent_meta] Migrati {len(tags)} tag da torrent_tags.json → DB")
    except Exception as e:
        logger.debug(f"_migrate_tags_json: {e}")

def _save_tag_for_hash(info_hash: str, tag: str) -> bool:
    """Salva il tag per un hash nella tabella torrent_meta."""
    if not info_hash or not tag:
        return False
    try:
        with _torrent_meta_db() as conn:
            conn.execute("""
                INSERT INTO torrent_meta (hash, tag, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(hash) DO UPDATE SET tag=excluded.tag, updated_at=excluded.updated_at
            """, (info_hash.lower(), str(tag).strip()))
        return True
    except Exception as e:
        logger.debug(f"_save_tag_for_hash: {e}")
        return False

def _save_tag_for_magnet(magnet: str, tag: str) -> bool:
    """Estrae l'hash dal magnet e chiama _save_tag_for_hash."""
    m = _HASH_RE_TAG.search(magnet or '')
    if not m:
        return False
    return _save_tag_for_hash(m.group(1).lower(), tag)

def _save_ui_state_db(snapshot: dict) -> None:
    """Salva snapshot {hash: {ui_state,progress,paused,total_size,downloaded,name}} nel DB."""
    if not snapshot:
        return
    try:
        import time as _t
        now = int(_t.time())
        with _torrent_meta_db() as conn:
            conn.executemany("""
                INSERT INTO torrent_meta (hash, ui_state, progress, paused, total_size, downloaded, name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hash) DO UPDATE SET
                    ui_state=excluded.ui_state, progress=excluded.progress,
                    paused=excluded.paused, total_size=excluded.total_size,
                    downloaded=excluded.downloaded, name=excluded.name,
                    updated_at=excluded.updated_at
            """, [
                (ih, v.get('state',''), v.get('progress',0), int(v.get('paused',0)),
                 v.get('total_size',0), v.get('downloaded',0), v.get('name',''), now)
                for ih, v in snapshot.items()
            ])
    except Exception as e:
        logger.debug(f"_save_ui_state_db: {e}")

def _load_ui_state_db() -> dict:
    """Carica tutti i record torrent_meta. Ritorna {hash: {state, progress, ...}}."""
    try:
        with _torrent_meta_db() as conn:
            rows = conn.execute(
                "SELECT hash, ui_state, progress, paused, total_size, downloaded, name FROM torrent_meta"
            ).fetchall()
        return {
            r[0]: {'state': r[1], 'progress': r[2], 'paused': bool(r[3]),
                   'total_size': r[4], 'downloaded': r[5], 'name': r[6]}
            for r in rows
        }
    except Exception as e:
        logger.debug(f"_load_ui_state_db: {e}")
        return {}


@app.route('/api/torrent-tags', methods=['GET'])
def get_torrent_tags():
    """Restituisce tutti i tag associati agli hash."""
    try:
        with _torrent_meta_db() as conn:
            rows = conn.execute("SELECT hash, tag FROM torrent_meta WHERE tag != ''").fetchall()
        return jsonify({r[0]: r[1] for r in rows})
    except Exception as e:
        logger.debug(f"get_tags: {e}")
        return jsonify({})

@app.route('/api/torrent-tags', methods=['POST'])
def set_torrent_tags():
    """Salva o rimuove tag. Riceve { "hash1": "NomeTag", "hash2": "" }."""
    try:
        data = request.json or {}
        with _torrent_meta_db() as conn:
            for h, t in data.items():
                h = h.lower()
                if t:
                    conn.execute("""
                        INSERT INTO torrent_meta (hash, tag, updated_at)
                        VALUES (?, ?, strftime('%s','now'))
                        ON CONFLICT(hash) DO UPDATE SET tag=excluded.tag, updated_at=excluded.updated_at
                    """, (h, str(t).strip()))
                else:
                    conn.execute("UPDATE torrent_meta SET tag='' WHERE hash=?", (h,))
        return jsonify({'success': True})
    except Exception as e:
        logger.warning(f"set_tags: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500




@app.route('/api/torrent-no-rename', methods=['GET'])
def get_torrent_no_rename():
    """Restituisce il dizionario {hash: no_rename} per tutti i torrent con il flag attivo."""
    try:
        with _torrent_meta_db() as conn:
            rows = conn.execute("SELECT hash, no_rename FROM torrent_meta WHERE no_rename=1").fetchall()
        return jsonify({r[0]: bool(r[1]) for r in rows})
    except Exception as e:
        logger.debug(f"get_no_rename: {e}")
        return jsonify({})

@app.route('/api/torrent-no-rename', methods=['POST'])
def set_torrent_no_rename():
    """Imposta/rimuove il flag no_rename. Riceve { \"hash1\": true/false, ... }."""
    try:
        data = request.json or {}
        with _torrent_meta_db() as conn:
            for h, val in data.items():
                h = h.lower()
                flag = 1 if val else 0
                conn.execute("""
                    INSERT INTO torrent_meta (hash, no_rename, updated_at)
                    VALUES (?, ?, strftime('%s','now'))
                    ON CONFLICT(hash) DO UPDATE SET no_rename=excluded.no_rename, updated_at=excluded.updated_at
                """, (h, flag))
        return jsonify({'success': True})
    except Exception as e:
        logger.warning(f"set_no_rename: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/maintenance/clean-trash', methods=['POST'])
def clean_trash():
    """Elimina i file nel cestino più vecchi di trash_retention_days giorni."""
    try:
        from core.config import Config as _Cfg
        cfg = _Cfg()
        trash_path = str(getattr(cfg, 'trash_path', '')).strip()
        if not trash_path:
            return jsonify({'success': False, 'error': 'Cartella cestino non configurata'})
        if not os.path.exists(trash_path):
            return jsonify({'success': False, 'error': f'Cartella cestino non trovata: {trash_path}'})

        # days può essere stringa vuota → non cancellare mai
        days_raw = str(getattr(cfg, 'trash_retention_days', '')).strip()
        if not days_raw:
            return jsonify({'success': True, 'deleted': 0, 'freed_mb': 0,
                            'message': 'Pulizia automatica disabilitata (giorni non configurati)'})
        try:
            days = int(days_raw)
            if days <= 0:
                return jsonify({'success': False, 'error': 'Il numero di giorni deve essere > 0'})
        except ValueError:
            return jsonify({'success': False, 'error': f'Valore giorni non valido: {days_raw}'})

        import time
        cutoff = time.time() - days * 86400
        deleted = 0
        freed = 0

        log_maintenance(f"🗑️ Pulizia cestino: file più vecchi di {days} giorni in {trash_path}...")
        for fname in os.listdir(trash_path):
            fpath = os.path.join(trash_path, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                mtime = os.path.getmtime(fpath)
                if mtime < cutoff:
                    size = os.path.getsize(fpath)
                    os.remove(fpath)
                    freed += size
                    deleted += 1
            except Exception as e:
                log_maintenance(f"⚠️ Errore rimozione {fname}: {e}")

        freed_mb = round(freed / 1024 / 1024, 2)
        msg = f"Rimossi {deleted} file ({freed_mb} MB liberati)"
        log_maintenance(f"✅ Pulizia cestino completata: {msg}")
        return jsonify({'success': True, 'deleted': deleted, 'freed_mb': freed_mb, 'message': msg})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def log_maintenance(msg):
    """Scrive il log usando il logger principale che gestisce la rotazione automatica."""
    try:
        from core.constants import logger as core_logger
        # Invia il messaggio al logger rotante (che lo scrive nel file log e nel terminale)
        core_logger.info(msg)
    except Exception as e:
        print(f"log_maintenance fallback: {e}")
        # Fallback di super-emergenza: scrive solo sul terminale se il logger è rotto
        line = f"{datetime.now().strftime('%H:%M:%S')} - {msg}"
        print(line, flush=True)

@app.route('/api/db/info', methods=['GET'])
def get_db_info():
    """Restituisce le dimensioni e lo stato dei database SQLite."""
    try:
        log_maintenance("ℹ️ Database status info requested (UI Update)")
        
        def get_file_info(filepath):
            if not os.path.exists(filepath): return {"size_mb": 0, "frag": 0}
            size = os.path.getsize(filepath) / (1024 * 1024)
            try:
                with sqlite3.connect(filepath) as conn:
                    c = conn.cursor()
                    c.execute("PRAGMA page_count")
                    pages = c.fetchone()[0]
                    c.execute("PRAGMA freelist_count")
                    free = c.fetchone()[0]
                    frag = round((free / pages) * 100, 1) if pages > 0 else 0
                return {"size_mb": round(size, 2), "frag": frag}
            except Exception as e:
                logger.debug(f"db_stats sqlite: {e}")
                return {"size_mb": round(size, 2), "frag": 0}

        series_info = get_file_info(DB_FILE)
        archive_info = get_file_info(ARCHIVE_FILE)
        
        archive_count = 0
        if os.path.exists(ARCHIVE_FILE):
            try:
                with sqlite3.connect(ARCHIVE_FILE) as conn:
                    archive_count = conn.execute("SELECT COUNT(*) FROM archive").fetchone()[0]
            except: pass

        return jsonify({
            'success': True,
            'series_db': series_info,
            'archive_db': archive_info,
            'archive_count': archive_count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/db/action', methods=['POST'])
def run_db_action():
    """Esegue VACUUM o ANALYZE sui database e registra nel log con dettagli."""
    try:
        data = request.json or {}
        action = data.get('action', '').upper()
        target = data.get('target', 'both')
        
        if action not in ['VACUUM', 'ANALYZE', 'REINDEX']:
            return jsonify({'success': False, 'error': 'Azione non valida'}), 400

        targets = []
        if target in ['series', 'both'] and os.path.exists(DB_FILE): targets.append(DB_FILE)
        if target in ['archive', 'both'] and os.path.exists(ARCHIVE_FILE): targets.append(ARCHIVE_FILE)

        log_maintenance(f"🛠️ Starting DB maintenance: {action} on selected files...")
        
        # Calcolo peso prima dell'operazione
        size_before = sum(os.path.getsize(p) for p in targets if os.path.exists(p))

        # Esecuzione comando su tutti i DB selezionati
        for db_path in targets:
            with sqlite3.connect(db_path) as conn:
                conn.execute(action)

        # Calcolo peso dopo l'operazione
        size_after = sum(os.path.getsize(p) for p in targets if os.path.exists(p))
        saved_mb = max(0, (size_before - size_after) / (1024 * 1024))
        before_mb = size_before / (1024 * 1024)
        after_mb = size_after / (1024 * 1024)
        
        # Formattazione messaggi dettagliati
        msg = f"{action} completato."
        if action == 'VACUUM':
            msg += f" Space freed: {saved_mb:.2f} MB (Reduced from {before_mb:.2f} MB to {after_mb:.2f} MB)."
        elif action == 'ANALYZE':
            msg += " Internal index statistics recalculated to speed up search queries."
        elif action == 'REINDEX':
            msg += " All search indexes have been rebuilt from scratch."
            
        log_maintenance(f"✅ {msg}")

        return jsonify({'success': True, 'message': msg})
        
    except Exception as e:
        log_maintenance(f"❌ Errore {action}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/db/rescore-episodes', methods=['POST'])
def rescore_episodes():
    """Ricalcola quality_score di tutti gli episodi dal titolo originale salvato nel DB.
    Strategia a cascata: original_title → feed_matches downloaded → feed_matches best
    → file fisico su disco → titolo rinominato (fallback).
    Aggiorna anche episode_archive_presence per evitare ridownload di episodi già sul NAS."""
    try:
        from core.models import Parser
        c = db.conn.cursor()
        # Recupera tutti i campi necessari, incluso series_name per la scansione disco
        try:
            rows = c.execute(
                """SELECT e.id, e.series_id, e.season, e.episode, e.title,
                          e.original_title, e.quality_score, e.archive_path,
                          s.name as series_name
                   FROM episodes e
                   JOIN series s ON e.series_id = s.id
                   WHERE e.title IS NOT NULL AND e.title != ''"""
            ).fetchall()
            has_original = True
        except Exception as e:
            logger.debug(f"upsert_series has_original: {e}")
            rows = c.execute(
                """SELECT e.id, e.series_id, e.season, e.episode, e.title,
                          e.quality_score, e.archive_path,
                          s.name as series_name
                   FROM episodes e
                   JOIN series s ON e.series_id = s.id
                   WHERE e.title IS NOT NULL AND e.title != ''"""
            ).fetchall()
            has_original = False

        # Costruisce mappa series_name → archive_path dalla config (series.txt)
        # così il disk scan funziona anche quando episodes.archive_path è vuoto
        series_path_map = {}
        # Apre core.database.Database una volta sola (ha _best_quality_in_path,
        # a differenza di ExtToDB che è la classe locale di extto_web)
        from core.database import Database as _CoreDB
        _coredb = _CoreDB()
        try:
            from core.models import normalize_series_name
            _scfg = parse_series_config()
            for _s in _scfg.get('series', []):
                _ap = (_s.get('archive_path') or '').strip()
                _sn = (_s.get('name') or '').strip()
                if _ap and _sn:
                    series_path_map[normalize_series_name(_sn)] = _ap
            logger.info(f"[RESCORE] Found {len(series_path_map)} series paths from config")
        except Exception as _me:
            logger.warning(f"[RESCORE] Error reading series config: {_me}")

        updated = 0
        skipped = 0
        no_original = 0
        archive_updated = 0
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in rows:
            try:
                # Cascata per trovare il titolo più ricco di info qualità:
                # 1) original_title  2) feed 'downloaded'  3) feed best score
                # 4) file fisico NAS  5) title rinominato (fallback)
                score_title = None

                if has_original and row['original_title']:
                    score_title = row['original_title']

                if not score_title:
                    try:
                        fm = c.execute(
                            """SELECT title FROM episode_feed_matches
                               WHERE series_id=? AND season=? AND episode=? AND fail_reason='downloaded'
                               ORDER BY quality_score DESC LIMIT 1""",
                            (row['series_id'], row['season'], row['episode'])
                        ).fetchone()
                        if fm and fm['title']:
                            score_title = fm['title']
                    except Exception:
                        pass

                if not score_title:
                    try:
                        fm = c.execute(
                            """SELECT title FROM episode_feed_matches
                               WHERE series_id=? AND season=? AND episode=?
                               ORDER BY quality_score DESC LIMIT 1""",
                            (row['series_id'], row['season'], row['episode'])
                        ).fetchone()
                        if fm and fm['title']:
                            score_title = fm['title']
                    except Exception:
                        pass

                # Opzione 4: file fisico sul NAS
                # Priorità: archive_path dell'episodio → path dalla config (series.txt)
                # NOTA: db è ExtToDB (locale), non ha _best_quality_in_path.
                # Usiamo core.database.Database direttamente.
                if not score_title:
                    try:
                        series_name = row['series_name'] or ''
                        arch_path = row['archive_path'] or ''
                        if not arch_path and series_name and series_path_map:
                            from core.models import normalize_series_name
                            arch_path = series_path_map.get(normalize_series_name(series_name), '')
                        if arch_path and series_name:
                            disk_score = _coredb._best_quality_in_path(
                                series_name, row['season'], row['episode'], arch_path
                            )
                            if disk_score and disk_score > 0:
                                new_score = disk_score
                                if new_score != row['quality_score']:
                                    c.execute('UPDATE episodes SET quality_score=? WHERE id=?', (new_score, row['id']))
                                    updated += 1
                                    try:
                                        ap = c.execute(
                                            'SELECT best_quality_score FROM episode_archive_presence WHERE series_id=? AND season=? AND episode=?',
                                            (row['series_id'], row['season'], row['episode'])
                                        ).fetchone()
                                        if ap is not None and new_score >= (ap['best_quality_score'] or 0):
                                            c.execute(
                                                'UPDATE episode_archive_presence SET best_quality_score=?, at=? WHERE series_id=? AND season=? AND episode=?',
                                                (new_score, now_iso, row['series_id'], row['season'], row['episode'])
                                            )
                                            archive_updated += 1
                                    except Exception:
                                        pass
                                else:
                                    skipped += 1
                                continue
                    except Exception as de:
                        logger.debug(f"rescore disk scan: {de}")

                if not score_title:
                    score_title = row['title']
                    no_original += 1

                new_score = Parser.parse_quality(score_title).score()
                if new_score != row['quality_score']:
                    c.execute('UPDATE episodes SET quality_score=? WHERE id=?', (new_score, row['id']))
                    updated += 1
                    try:
                        ap = c.execute(
                            'SELECT best_quality_score FROM episode_archive_presence WHERE series_id=? AND season=? AND episode=?',
                            (row['series_id'], row['season'], row['episode'])
                        ).fetchone()
                        if ap is not None and new_score >= (ap['best_quality_score'] or 0):
                            c.execute(
                                'UPDATE episode_archive_presence SET best_quality_score=?, at=? WHERE series_id=? AND season=? AND episode=?',
                                (new_score, now_iso, row['series_id'], row['season'], row['episode'])
                            )
                            archive_updated += 1
                    except Exception as ae:
                        logger.debug(f"rescore archive_presence update: {ae}")
                else:
                    skipped += 1
            except Exception as e:
                logger.debug(f"upsert_series episode: {e}")
                skipped += 1

        db.conn.commit()
        note = f' ({no_original} senza titolo originale, usato titolo rinominato)' if no_original > 0 else ''
        arch_note = f', {archive_updated} archivi allineati' if archive_updated > 0 else ''
        msg = f'Ricalcolo completato: {updated} episodi aggiornati, {skipped} invariati (su {len(rows)} totali){note}{arch_note}.'
        log_maintenance(f'🔄 {msg}')
        return jsonify({'success': True, 'message': msg, 'updated': updated, 'skipped': skipped})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/api/db/prune', methods=['POST'])
def prune_archive():
    """Elimina i record vecchi dall'archivio e registra nel log."""
    try:
        days = int((request.json or {}).get('days', 30))
        if not os.path.exists(ARCHIVE_FILE):
            return jsonify({'success': False, 'error': 'Archivio non trovato'})
            
        log_maintenance(f"🧹 Avvio pulizia Archivio (elementi più vecchi di {days} giorni)...")

        with sqlite3.connect(ARCHIVE_FILE) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM archive WHERE added_at < datetime('now', '-' || ? || ' days')", (str(days),))
            deleted = c.rowcount
        
        msg = f'Rimossi {deleted} elementi obsoleti'
        log_maintenance(f"✅ Pulizia completata: {msg}")
        
        return jsonify({'success': True, 'deleted': deleted, 'message': msg})
        
    except Exception as e:
        log_maintenance(f"❌ Errore Pulizia Archivio: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/db/prune-keyword', methods=['POST'])
def prune_archive_keyword():
    """
    Ricerca nell'archivio per parole chiave nel titolo.
    Restituisce la lista completa dei record trovati (id + title + added_at).
    Body JSON:
        keywords : str  — parole separate da virgola o spazio (OR implicito)
        limit    : int  — max risultati da restituire (default 500)
    """
    try:
        data     = request.json or {}
        raw      = str(data.get('keywords', '')).strip()
        limit    = min(int(data.get('limit', 500)), 2000)

        if not raw:
            return jsonify({'success': False, 'error': 'Nessuna keyword specificata'})

        import re as _re
        keywords = [k.strip() for k in _re.split(r'[,\s]+', raw) if len(k.strip()) >= 2]
        if not keywords:
            return jsonify({'success': False, 'error': 'Keyword troppo corte (minimo 2 caratteri)'})

        if not os.path.exists(ARCHIVE_FILE):
            return jsonify({'success': False, 'error': 'Archivio non trovato'})

        conditions = ' OR '.join(['title LIKE ?' for _ in keywords])
        params     = [f'%{k}%' for k in keywords]

        with sqlite3.connect(ARCHIVE_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(f"SELECT COUNT(*) FROM archive WHERE {conditions}", params)
            total = c.fetchone()[0]
            c.execute(
                f"SELECT id, title, added_at FROM archive WHERE {conditions} ORDER BY added_at DESC LIMIT ?",
                params + [limit]
            )
            rows = [{'id': r['id'], 'title': r['title'], 'added_at': r['added_at']} for r in c.fetchall()]

        log_maintenance(f"🔍 Keyword-search '{', '.join(keywords)}': {total} record trovati (restituiti {len(rows)})")
        return jsonify({
            'success':  True,
            'total':    total,
            'returned': len(rows),
            'keywords': keywords,
            'rows':     rows,
        })

    except Exception as e:
        log_maintenance(f"❌ Errore keyword-search: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/db/prune-by-ids', methods=['POST'])
def prune_archive_by_ids():
    """
    Elimina dall'archivio i record con gli id specificati.
    Body JSON:
        ids : list[int]  — lista di id da eliminare
    """
    try:
        data = request.json or {}
        ids  = [int(i) for i in data.get('ids', []) if str(i).isdigit()]
        if not ids:
            return jsonify({'success': False, 'error': 'Nessun id specificato'})
        if not os.path.exists(ARCHIVE_FILE):
            return jsonify({'success': False, 'error': 'Archivio non trovato'})

        placeholders = ','.join('?' * len(ids))
        with sqlite3.connect(ARCHIVE_FILE) as conn:
            c = conn.cursor()
            c.execute(f"DELETE FROM archive WHERE id IN ({placeholders})", ids)
            deleted = c.rowcount

        msg = f"Rimossi {deleted} record selezionati dall'archivio"
        log_maintenance(f"✅ Prune-by-ids: {msg}")
        return jsonify({'success': True, 'deleted': deleted, 'message': msg})

    except Exception as e:
        log_maintenance(f"❌ Errore prune-by-ids: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



PHYSICAL_FILE_CACHE = {}
CACHE_TTL = 15  # secondi — evita os.walk ripetuti ad ogni poll

_VIDEO_EXTS_PROXY = {'.mkv', '.mp4', '.avi', '.ts', '.m2ts', '.wmv'}

def _find_episode_on_disk(series_name: str, s_num: int, e_num: int,
                           archive_path: str, final_dir: str, temp_dir: str,
                           rename_fmt: str, rename_template: str) -> tuple:
    """
    Cerca un episodio SxxExx sul disco controllando ANCHE il nome della serie.
    """
    import os, re
    from core.models import normalize_series_name, _series_name_matches

    ep_re = re.compile(rf'(?i)s0*{s_num}[._\-\s]*e0*{e_num}(?!\d)')
    norm_series = normalize_series_name(series_name)

    def _scan_dir(base_dir: str, max_depth: int = 3) -> str:
        if not base_dir or not os.path.isdir(base_dir):
            return ''
        try:
            for root, dirs, files in os.walk(base_dir):
                depth = root[len(base_dir):].count(os.sep)
                if depth >= max_depth:
                    dirs[:] = []
                    continue
                for f in files:
                    if os.path.splitext(f)[1].lower() not in _VIDEO_EXTS_PROXY:
                        continue
                    if 'sample' in f.lower():
                        continue
                    rel = os.path.relpath(os.path.join(root, f), base_dir)
                    if ep_re.search(rel):
                        # Controllo blindato: il nome della serie deve essere nel file o nella cartella
                        norm_file = normalize_series_name(f)
                        norm_root = normalize_series_name(root)
                        
                        if _series_name_matches(norm_series, norm_file) or \
                           norm_series in norm_file or \
                           norm_series in norm_root:
                            return os.path.join(root, f)
        except OSError:
            pass
        return ''

    # 1. NAS
    if archive_path:
        season_dir = os.path.join(archive_path, f'Stagione {s_num}')
        for search_root in ([season_dir] if os.path.isdir(season_dir) else []) + [archive_path]:
            fpath = _scan_dir(search_root, max_depth=2)
            if fpath:
                return True, 'nas', fpath

    # 2. Cartella definitiva
    if final_dir and final_dir != archive_path:
        fpath = _scan_dir(final_dir, max_depth=3)
        if fpath:
            return True, 'final', fpath

    # 3. Cartella temporanea
    if temp_dir and temp_dir not in (final_dir, archive_path):
        fpath = _scan_dir(temp_dir, max_depth=3)
        if fpath:
            return True, 'temp', fpath

    return False, '', ''


@app.route('/api/debug/torrent_match', methods=['GET'])
def debug_torrent_match():
    """Debug endpoint: verifica perché un torrent non viene riconosciuto.
    Uso: /api/debug/torrent_match?name=Star.Trek.Starfleet.Academy.S01E10...mkv
    """
    from flask import request, jsonify
    import os, re
    from urllib.parse import unquote_plus
    from core.models import normalize_series_name, _series_name_matches

    t_name = request.args.get('name', '')
    cfg_data = parse_series_config()
    configured_series = cfg_data.get('series', [])
    settings = cfg_data.get('settings', {})
    final_dir = settings.get('libtorrent_dir', '').strip()
    temp_dir  = settings.get('libtorrent_temp_dir', '').strip()

    result = {'t_name': t_name, 'steps': []}

    m_ep = re.search(r'(?i)(?<![\w])S(\d{1,2})[.\s_-]*E(\d{1,3})(?![\d])', t_name)
    if not m_ep:
        result['steps'].append('NO SxxExx found in name')
        return jsonify(result)

    s_num, e_num = int(m_ep.group(1)), int(m_ep.group(2))
    prefix_raw   = t_name[:m_ep.start()]
    prefix_clean = re.sub(r'[._]+', ' ', prefix_raw).strip()
    norm_t_name  = normalize_series_name(prefix_clean)

    result['s_num'] = s_num
    result['e_num'] = e_num
    result['prefix_raw'] = prefix_raw
    result['prefix_clean'] = prefix_clean
    result['norm_t_name'] = norm_t_name
    result['final_dir'] = final_dir
    result['temp_dir'] = temp_dir

    series_matches = []
    for s in configured_series:
        nc = normalize_series_name(s.get('name', ''))
        m = _series_name_matches(nc, norm_t_name)
        series_matches.append({
            'name': s.get('name'),
            'norm': nc,
            'archive_path': s.get('archive_path', ''),
            'match': m
        })
    result['series_matches'] = series_matches

    matched = next((s for s in series_matches if s['match']), None)
    archive_path = matched['archive_path'] if matched else ''
    result['archive_path'] = archive_path
    result['archive_path_exists'] = os.path.isdir(archive_path) if archive_path else False
    result['final_dir_exists'] = os.path.isdir(final_dir) if final_dir else False

    found, where, found_fpath = _find_episode_on_disk(
        norm_t_name, s_num, e_num,
        archive_path, final_dir, temp_dir, 'base', ''
    )
    result['found'] = found
    result['where'] = where

    return jsonify(result)


@app.route('/api/torrents', methods=['GET'])
def proxy_torrents_base():
    """Proxy Torrents: Logica Diretta su Cartella (Bypassa il limite del DB per i Manuali)."""
    from flask import jsonify
    try:
        import requests
        resp = requests.get(f'http://127.0.0.1:{get_engine_port()}/api/torrents', timeout=3)
        if resp.status_code != 200: return resp.content, resp.status_code

        data = resp.json()
        torrents = data.get('torrents', []) if isinstance(data, dict) else data
        if isinstance(torrents, dict): torrents = list(torrents.values())

        # ---> INIZIO INIEZIONE DOWNLOAD HTTP NELLA TABELLA TORRENT <---
        try:
            from core.comics import ACTIVE_HTTP_DOWNLOADS
            if ACTIVE_HTTP_DOWNLOADS:
                torrents.extend(list(ACTIVE_HTTP_DOWNLOADS.values()))
        except Exception as e:
            logger.debug(f"list_torrents HTTP: {e}")
            pass
        # ---> FINE INIEZIONE <---

        import re, os
        from urllib.parse import unquote_plus
        from core.models import normalize_series_name, _series_name_matches

        cfg_data = parse_series_config()
        configured_series = cfg_data.get('series', [])

        # Leggi le dir di download dalla config — usate per la ricerca a 3 livelli
        settings = cfg_data.get('settings', {})
        final_dir = settings.get('libtorrent_dir', '').strip()
        temp_dir  = settings.get('libtorrent_temp_dir', '').strip()
        rename_fmt      = settings.get('rename_format', 'base').strip().lower()
        rename_template = settings.get('rename_template',
                          '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}]').strip()

        import time as _time_mod
        now_ts = _time_mod.time()

        for t in torrents:
            t_name = unquote_plus(t.get('name', ''))

            # ---> INIZIO FIX CONSUMI: Cattura il peso in tempo reale <---
            t_hash = t.get('hash', '')
            t_size = t.get('total_size', 0)
            if t_hash and t_size > 0:
                try:
                    c = db.conn.cursor()
                    c.execute("UPDATE episodes SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (t_size, t_hash))
                    ep_rows = c.rowcount
                    c.execute("UPDATE movies SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (t_size, t_hash))
                    mov_rows = c.rowcount
                    if ep_rows > 0 or mov_rows > 0:
                        db.conn.commit()
                except: pass
            # ---> FINE FIX CONSUMI <---

            # Cerca SxxExx nel nome del torrent.
            m_ep = re.search(r'(?i)(?<![\w])S(\d{1,2})[.\s_-]*E(\d{1,3})(?![\d])', t_name)
            if not m_ep:
                # SOLUZIONE STRUTTURALE FUMETTI/FILM:
                # Anche se non è una serie TV, controlliamo se il file esiste già fisicamente nella cartella
                if t_name and t_name != 'Tutto' and final_dir and os.path.exists(final_dir):
                    # Puliamo il nome del torrent da punti e underscore per un confronto sicuro
                    clean_tname = re.sub(r'[._]+', ' ', t_name).strip().lower()
                    if clean_tname:
                        for f_name in os.listdir(final_dir):
                            clean_fname = re.sub(r'[._]+', ' ', f_name).strip().lower()
                            # Se troviamo un file o cartella che combacia con il nome del torrent
                            if clean_tname in clean_fname or clean_fname in clean_tname:
                                t_prog = float(t.get('progress', 0))
                                is_finto_completato = t_prog >= 1.0 or (t_prog == 0.0 and t.get('completed_time', 0) > 0) or t.get('state') == 'error'
                                if is_finto_completato:
                                    t['physical_file_found'] = True
                                    t['progress'] = 1.0
                                    t['state'] = 'Terminato'
                                    if t.get('total_size', 0) == 0: t['total_size'] = 104857600
                                    t['completed'] = t['total_size']
                                    t['downloaded'] = t['total_size']
                                    t['eta'] = -2
                                break
            else:
                # Estrai nome serie: tutto prima di SxxExx, converti dots in spazi
                prefix_raw = t_name[:m_ep.start()]
                # "Star.Trek.Starfleet.Academy." → "Star Trek Starfleet Academy"
                prefix_clean = re.sub(r'[._]+', ' ', prefix_raw).strip()
                norm_t_name = normalize_series_name(prefix_clean)
                s_num, e_num = int(m_ep.group(1)), int(m_ep.group(2))

                # Trova la serie corrispondente in configurazione per avere archive_path.
                # Prova prima match esatto, poi match per sottostringa (fallback).
                series_cfg = next(
                    (s for s in configured_series
                     if _series_name_matches(normalize_series_name(s.get('name', '')), norm_t_name)),
                    None
                )
                # Fallback: match per sottostringa (es. "star trek starfleet academy"
                # contiene "starfleet academy" o viceversa)
                if series_cfg is None:
                    for s in configured_series:
                        nc = normalize_series_name(s.get('name', ''))
                        if nc and norm_t_name and (nc in norm_t_name or norm_t_name in nc):
                            series_cfg = s
                            break

                archive_path = series_cfg.get('archive_path', '').strip() if series_cfg else ''

                cache_key = f"{norm_t_name}|{s_num}|{e_num}"
                cached = PHYSICAL_FILE_CACHE.get(cache_key)
                if cached and (now_ts - cached[0]) < CACHE_TTL:
                    found, where = cached[1], cached[2]
                    found_fpath = cached[3] if len(cached) > 3 else ''
                else:
                    # Chiamata pulita e singola senza loop infiniti sui dischi
                    found, where, found_fpath = _find_episode_on_disk(
                        norm_t_name, s_num, e_num,
                        archive_path, final_dir, temp_dir,
                        rename_fmt, rename_template
                    )
                    PHYSICAL_FILE_CACHE[cache_key] = (now_ts, found, where, found_fpath)

                if found:
                    t_prog = float(t.get('progress', 0))
                    is_finto_completato = t_prog >= 1.0 or (t_prog == 0.0 and t.get('completed_time', 0) > 0) or t.get('state') == 'error'
                    if is_finto_completato:
                        t['physical_file_found'] = True
                        t['progress'] = 1.0
                        t['state'] = 'Terminato'
                        if t.get('total_size', 0) == 0:
                            try:
                                t['total_size'] = os.path.getsize(found_fpath) if found_fpath else 104857600
                            except OSError:
                                t['total_size'] = 104857600
                        t['completed'] = t['total_size']
                        t['downloaded'] = t['total_size']
                        if where == 'nas':
                            t['eta'] = -2
                    # where == 'final' o 'temp': pct=100, eta rimane quello reale → mostra ✓

        return jsonify(torrents)
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/torrents/set_limits', methods=['POST'])
def torrent_set_limits():
    """Inoltra i limiti DL/UL/Seeding al motore (extto3.py) che gestisce la vera sessione."""
    try:
        data = request.get_json(silent=True) or {}
        if not data.get('hash'):
            return jsonify({'ok': False, 'error': 'hash mancante'}), 400
        
        import requests
        r = requests.post(
            f'http://127.0.0.1:{get_engine_port()}/api/torrents/set_limits',
            json=data,
            timeout=10
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        logger.warning(f"torrent_set_limits proxy error: {e}")
        return jsonify({'ok': False, 'error': 'Motore non raggiungibile'}), 500


@app.route('/api/torrents/<path:subpath>', methods=['GET', 'POST', 'OPTIONS'])
def proxy_torrents(subpath):
    target_url = f'http://127.0.0.1:{get_engine_port()}/api/torrents/{subpath}'
    try:
        if request.method == 'GET':
            resp = requests.get(target_url, timeout=5)
        elif request.method == 'POST':
            resp = requests.post(
                target_url, 
                json=request.get_json(),
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
        else:
            return {'error': 'Method not allowed'}, 405
        
        return resp.content, resp.status_code, {
            'Content-Type': resp.headers.get('Content-Type', 'application/json'),
            'Access-Control-Allow-Origin': '*'
        }
    except requests.exceptions.RequestException as e:
        print(f'❌ Proxy torrent error: {e}')
        return {'error': 'Backend unavailable', 'detail': str(e)}, 503

@app.route('/api/tmdb/search', methods=['GET'])
def tmdb_search():
    """Cerca un titolo su TMDB per l'autocompletamento nella Web UI"""
    query = request.args.get('q', '').strip()
    media_type = request.args.get('type', 'tv') # 'tv' per serie, 'movie' per film

    if not query:
        return jsonify({'success': False, 'error': 'Inserisci un titolo da cercare'})

    try:
        from core.config import Config
        cfg = Config()
        api_key = getattr(cfg, 'tmdb_api_key', '').strip()
        tmdb_lang = getattr(cfg, 'tmdb_language', 'it-IT').strip()
        
        if not api_key:
            return jsonify({'success': False, 'error': 'Chiave API TMDB mancante. Inseriscila in Configurazione -> Avanzate.'})

        url = f"https://api.themoviedb.org/3/search/{media_type}"
        params = {'api_key': api_key, 'query': query, 'language': tmdb_lang}
        
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            results = []
            for item in data.get('results', [])[:5]: # Prendi i primi 5 risultati
                title = item.get('name') if media_type == 'tv' else item.get('title')
                date_str = item.get('first_air_date') if media_type == 'tv' else item.get('release_date')
                year = date_str[:4] if date_str else ''
                orig_title = item.get('original_name') or item.get('original_title')
                results.append({'id': item.get('id'), 'title': title, 'year': year, 'original_title': orig_title})
            return jsonify({'success': True, 'results': results})
        else:
            return jsonify({'success': False, 'error': f"Errore server TMDB: {res.status_code}"})
    except Exception as e:
        return jsonify({'success': False, 'error': f"Errore di rete: {str(e)}"}) 

@app.route('/api/tmdb/discover', methods=['GET'])
def tmdb_discover():
    """Scarica i film e le serie in tendenza da TMDB per la sezione Esplora"""
    media_type = request.args.get('type', 'movie') 
    category = request.args.get('category', 'trending') # <--- ORA LEGGE IL TAB CLICCATO
    
    try:
        from core.config import Config
        cfg = Config()
        api_key = getattr(cfg, 'tmdb_api_key', '').strip()
        tmdb_lang = getattr(cfg, 'tmdb_language', 'it-IT').strip()

        if not api_key:
            return jsonify({'success': False, 'error': 'API Key TMDB mancante.'})

        # --- ROUTING INTELLIGENTE BASATO SULLA CATEGORIA ---
        if category == 'top_rated':
            url = f"https://api.themoviedb.org/3/{media_type}/top_rated"
        elif category in ('now_playing', 'on_the_air'):
            url = f"https://api.themoviedb.org/3/movie/now_playing" if media_type == 'movie' else f"https://api.themoviedb.org/3/tv/on_the_air"
        elif category == 'upcoming':
            url = f"https://api.themoviedb.org/3/movie/upcoming" if media_type == 'movie' else f"https://api.themoviedb.org/3/tv/airing_today"
        elif category == 'popular':
            url = f"https://api.themoviedb.org/3/{media_type}/popular"
        else:
            # Default: Tendenze
            url = f"https://api.themoviedb.org/3/trending/{media_type}/week"
        results = []
        
        # Facciamo 3 "chiamate" per prendere le pagine 1, 2 e 3 (Totale: 60 risultati)
        for page in range(1, 4):
            params = {'api_key': api_key, 'language': tmdb_lang, 'page': page}
            res = requests.get(url, params=params, timeout=10)
            
            if res.status_code == 200:
                data = res.json()
                for item in data.get('results', []):
                    title = item.get('name') if media_type == 'tv' else item.get('title')
                    date_str = item.get('first_air_date') if media_type == 'tv' else item.get('release_date')
                    year = date_str[:4] if date_str else ''
                    results.append({
                        'id': item.get('id', ''),
                        'title': title,
                        'year': year,
                        'overview': item.get('overview', 'Nessuna trama disponibile.'),
                        'poster': item.get('poster_path', ''),
                        'vote': item.get('vote_average', 0)
                    })
            else:
                break # Se una pagina fallisce, interrompe il ciclo e restituisce quello che ha trovato

        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/series/<int:series_id>/rename-preview', methods=['GET'])
def rename_preview(series_id):
    """Genera l'anteprima dei file che verrebbero rinominati (video + file ausiliari .srt, .jpg, ecc.)."""
    try:
        from core.config import Config
        from core.tmdb import TMDBClient
        from core.renamer import _build_filename, _VIDEO_EXTS
        import os
        import re

        cfg = Config()
        api_key = getattr(cfg, 'tmdb_api_key', '').strip()
        if not api_key:
            return jsonify({'success': False, 'error': 'Chiave API TMDB mancante nelle impostazioni.'})

        c = db.conn.cursor()
        c.execute("SELECT name FROM series WHERE id=?", (series_id,))
        row = c.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Serie non trovata'})
        series_name = row['name']

        config_data = parse_series_config()
        apath = ""
        for s in config_data['series']:
            if s['name'].lower() == series_name.lower():
                apath = s.get('archive_path', '')
                break

        if not apath or not os.path.exists(apath):
            return jsonify({'success': False, 'error': 'Percorso archivio della serie non trovato sul disco.'})

        force_reprocess = request.args.get('force', '0') == '1'

        tmdb = TMDBClient(api_key, cache_days=int(getattr(cfg, 'tmdb_cache_days', 7)))
        tmdb_id = tmdb.get_tmdb_id_for_series(db, series_name)
        if not tmdb_id:
            tmdb_id = tmdb.resolve_series_id(series_name)
        if not tmdb_id:
            return jsonify({'success': False, 'error': 'Serie non identificata su TMDB.'})

        cfg_data        = Config()
        rename_fmt      = str(getattr(cfg_data, 'rename_format', 'base')).strip().lower()
        rename_template = str(getattr(cfg_data, 'rename_template', '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}][{Lingue}]')).strip()
        if rename_fmt not in ('base', 'standard', 'completo', 'custom'):
            rename_fmt = 'base'

        series_year = None
        if rename_fmt != 'base':
            try:
                td = tmdb._get(f'/tv/{tmdb_id}', {'language': tmdb.language})
                d  = (td or {}).get('first_air_date', '')
                series_year = d[:4] if d and len(d) >= 4 else None
            except Exception as e:
                logger.debug(f"series_year TMDB: {e}")
                pass

        _gmt = None
        _mediainfo_ok = False
        _mediainfo_warning = None
        if rename_fmt != 'base':
            try:
                from core.mediainfo_helper import mediainfo_available, get_media_tags as _gmt_import
                if mediainfo_available():
                    _gmt = _gmt_import
                    _mediainfo_ok = True
                else:
                    _mediainfo_warning = "mediainfo non disponibile — installa: apt install mediainfo && pip install pymediainfo"
            except ImportError as _ie:
                _mediainfo_warning = f"pymediainfo ImportError: {_ie}"
            except Exception as _me:
                _mediainfo_warning = f"mediainfo errore: {_me}"

        # Regex riutilizzata
        _re_se  = re.compile(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})|0*(\d{1,2})x0*(\d{1,3})')
        _re_ep  = re.compile(r'(?i)(?:ep|episodio|episode|e)[._\-\s]*0*(\d{1,3})|^0*(\d{1,3})(?:[\s\.\-]|$)')
        _re_fld = re.compile(r'(?i)(?:stagione|season|serie|s)\s*[._\-]*0*(\d+)')

        def _parse_se(fname, folder_season):
            """Restituisce (season, episode) oppure (0, 0)."""
            m = _re_se.search(fname)
            if m:
                return int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4))
            if folder_season is not None:
                m2 = _re_ep.search(fname)
                if m2:
                    return folder_season, int(m2.group(1) or m2.group(2))
            return 0, 0

        # --- INIZIO CONTEGGIO TOTALE ---
        total_files_to_scan = 0
        for r, d, f in os.walk(apath):
            for name in f:
                ext = os.path.splitext(name)[1].lower()
                if ext in _VIDEO_EXTS and 'sample' not in name.lower():
                    total_files_to_scan += 1
        # --- FINE CONTEGGIO TOTALE ---

        global RENAME_PROGRESS, _rename_progress_lock
        with _rename_progress_lock:
            RENAME_PROGRESS = {
                "status": "working", 
                "current": 0, 
                "total": total_files_to_scan, 
                "msg": "Inizializzazione..."
            }

        preview_list  = []
        already_ok_list = []
        current_video = 0

        for root_dir, _, files in os.walk(apath):
            folder_season = None
            fm = _re_fld.search(os.path.basename(root_dir))
            if fm:
                folder_season = int(fm.group(1))

            # --- Passo 1: Costruisci mappe separate per video singoli e per i best ---
            best_score_map = {} # (sea, epi) -> max_score
            best_base_map = {}  # (sea, epi) -> new_base del file video MIGLIORE
            old_best_video_map = {} # (sea, epi) -> old_base del file video MIGLIORE
            
            video_renames = {} # fname -> new_fname (Mappa specifica per ogni singolo file video)

            from core.renamer import _sanitize as _rn_sanitize
            from core.models import Parser
            _expected_prefix = f"{_rn_sanitize(series_name)} - S"

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _VIDEO_EXTS or 'sample' in fname.lower():
                    continue

                sea, epi = _parse_se(fname, folder_season)
                if sea <= 0 or epi <= 0:
                    continue

                current_video += 1
                with _rename_progress_lock:
                    RENAME_PROGRESS["current"] = current_video
                    RENAME_PROGRESS["msg"]     = f"Analisi: {fname[:40]}..."

                base_name = os.path.splitext(fname)[0]

                q = Parser.parse_quality(fname)
                current_score = q.score() if hasattr(q, 'score') else 0

                ep_title = tmdb.fetch_episode_title(tmdb_id, sea, epi) if tmdb_id else None
                tags = {}
                if _mediainfo_ok and _gmt:
                    try:
                        tags = _gmt(os.path.join(root_dir, fname))
                    except Exception:
                        pass

                new_video_name = _build_filename(series_name, sea, epi, ep_title, ext,
                                                 fmt=rename_fmt, year=series_year, tags=tags,
                                                 template_str=rename_template)

                # Il file è già corretto solo se il nome generato è identico a quello attuale
                if not force_reprocess and fname == new_video_name:
                    video_renames[fname] = fname
                    if (sea, epi) not in best_score_map or current_score > best_score_map.get((sea, epi), -1):
                        best_base_map[(sea, epi)] = base_name
                        old_best_video_map[(sea, epi)] = base_name
                        best_score_map[(sea, epi)] = current_score
                    already_ok_list.append(fname)
                    continue

                video_renames[fname] = new_video_name
                new_base = os.path.splitext(new_video_name)[0]

                # In caso di duplicati video per lo stesso episodio, il MIGLIORE vince (non l'ultimo!)
                if (sea, epi) not in best_score_map or current_score > best_score_map.get((sea, epi), -1):
                    best_base_map[(sea, epi)] = new_base
                    old_best_video_map[(sea, epi)] = base_name
                    best_score_map[(sea, epi)] = current_score

            # --- Passo 2: applica rename_map a TUTTI i file della cartella ---
            apath_abs = os.path.abspath(apath)
            for fname in files:
                sea, epi = _parse_se(fname, folder_season)
                if sea <= 0 or epi <= 0:
                    continue
                if (sea, epi) not in best_base_map:
                    continue

                base_name = os.path.splitext(fname)[0]
                ext       = os.path.splitext(fname)[1].lower()

                if ext in _VIDEO_EXTS and 'sample' not in fname.lower():
                    # Ogni file video ottiene la SUA rinomina corretta
                    new_fname = video_renames.get(fname, fname)
                else:
                    # I file ausiliari seguono il video MIGLIORE
                    best_new_base = best_base_map[(sea, epi)]
                    best_old_base = old_best_video_map[(sea, epi)]
                    
                    suffix = ""
                    if best_old_base and base_name.startswith(best_old_base):
                        suffix = base_name[len(best_old_base):]
                    else:
                        m_suffix = re.search(r'([.\-](?:thumb|poster|fanart|nfo|forced|[a-zA-Z]{2,3}))+$', base_name, flags=re.IGNORECASE)
                        if m_suffix:
                            suffix = m_suffix.group(0)
                            
                    new_fname = best_new_base + suffix + ext

                if fname == new_fname and not force_reprocess:
                    if fname not in already_ok_list:
                        already_ok_list.append(fname)
                    continue

                src_abs = os.path.abspath(os.path.join(root_dir, fname))
                dst_abs = os.path.abspath(os.path.join(root_dir, new_fname))

                if not src_abs.startswith(apath_abs + os.sep) or \
                   not dst_abs.startswith(apath_abs + os.sep):
                    continue

                rel_old = os.path.relpath(src_abs, apath_abs)
                rel_new = os.path.relpath(dst_abs, apath_abs)
                preview_list.append({'old': rel_old, 'new': rel_new})

        with _rename_progress_lock:
            RENAME_PROGRESS["status"] = "idle"

        return jsonify({'success': True, 'preview': preview_list, 'already_ok': already_ok_list, 'mediainfo_warning': _mediainfo_warning})
    except Exception as e:
        with _rename_progress_lock:
            RENAME_PROGRESS["status"] = "idle"
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/series/<int:series_id>/rename-execute', methods=['POST'])
def rename_execute(series_id):
    """Esegue fisicamente la rinomina sul disco (Supporta Sottocartelle e file Ausiliari)."""
    try:
        from core.config import Config
        from core.renamer import _VIDEO_EXTS
        import os
        import re

        cfg = Config()
        api_key = getattr(cfg, 'tmdb_api_key', '').strip()
        if not api_key:
            return jsonify({'success': False, 'error': 'API Key mancante'})

        c = db.conn.cursor()
        c.execute("SELECT name FROM series WHERE id=?", (series_id,))
        row = c.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Serie non trovata'})
        series_name = row['name']

        config_data = parse_series_config()
        apath = next((s.get('archive_path', '') for s in config_data['series']
                      if s['name'].lower() == series_name.lower()), "")
        if not apath or not os.path.exists(apath):
            return jsonify({'success': False, 'error': 'Percorso invalido'})

        apath_abs = os.path.abspath(apath)

        payload      = request.get_json(silent=True) or {}
        preview_data = payload.get('preview', [])
        if not preview_data:
            return jsonify({'success': False, 'error': 'Dati di anteprima mancanti. Ricarica la pagina e riprova.'})

        global RENAME_PROGRESS, _rename_progress_lock
        with _rename_progress_lock:
            RENAME_PROGRESS = {"status": "working", "current": 0, "total": len(preview_data), "msg": "Rinomina in corso..."}

        count  = 0
        _re_se = re.compile(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})|0*(\d{1,2})x0*(\d{1,3})')

        for idx, item in enumerate(preview_data):
            old_rel = item.get('old')
            new_rel = item.get('new')
            if not old_rel or not new_rel:
                continue

            src = os.path.abspath(os.path.join(apath_abs, old_rel))
            dst = os.path.abspath(os.path.join(apath_abs, new_rel))

            # Protezione path traversal: entrambi i path devono stare dentro apath
            if not src.startswith(apath_abs + os.sep) or \
               not dst.startswith(apath_abs + os.sep):
                continue

            with _rename_progress_lock:
                RENAME_PROGRESS["current"] = idx + 1
                RENAME_PROGRESS["msg"]     = f"Rinomina: {os.path.basename(src)[:40]}..."

            if os.path.exists(src):
                if src == dst:
                    continue
                    
                if os.path.exists(dst):
                    # --- GESTIONE CONFLITTO RINOMINA ---
                    try:
                        from core.cleaner import _handle_duplicate
                        from core.models import Parser
                        trash_path = str(getattr(cfg, 'trash_path', '')).strip()
                        c_action = str(getattr(cfg, 'cleanup_action', 'move')).lower()
                        
                        ext = os.path.splitext(src)[1].lower()
                        if ext in _VIDEO_EXTS:
                            q_src = Parser.parse_quality(os.path.basename(src))
                            q_dst = Parser.parse_quality(os.path.basename(dst))
                            
                            if q_src.score() > q_dst.score():
                                log_maintenance(f"🗑️ [CONFLITTO RINOMINA] Il nuovo file è migliore. Sposto l'esistente in trash: '{os.path.basename(dst)}'")
                                _handle_duplicate(dst, trash_path, action=c_action, reason="rename conflict (inferior)")
                            else:
                                log_maintenance(f"⏭️ [CONFLITTO RINOMINA] L'esistente è migliore o uguale. Scarto il nuovo: '{os.path.basename(src)}'")
                                _handle_duplicate(src, trash_path, action=c_action, reason="rename conflict (new is inferior)")
                                continue
                        else:
                            # File ausiliari (.jpg, .srt, ecc): sovrascriviamo cestinando il vecchio
                            log_maintenance(f"🗑️ [CONFLITTO RINOMINA] Sostituisco file ausiliario esistente: '{os.path.basename(dst)}'")
                            _handle_duplicate(dst, trash_path, action=c_action, reason="rename conflict (auxiliary override)")
                    except Exception as e:
                        log_maintenance(f"⚠️ Errore gestione conflitto su '{os.path.basename(src)}': {e}")
                        continue

                # A questo punto la via è libera (o dst scartato, o non c'era)
                if not os.path.exists(dst) and os.path.exists(src):
                    os.rename(src, dst)
                    log_maintenance(f"✏️ Rinomina: {os.path.basename(src)} ➔ {os.path.basename(dst)}")
                    count += 1

                    # Aggiornamento DB: se è un file video, aggiorna episodes.title
                    ext = os.path.splitext(dst)[1].lower()
                    if ext in _VIDEO_EXTS:
                        m = _re_se.search(os.path.basename(dst))
                        if m:
                            sea = int(m.group(1) or m.group(3))
                            epi = int(m.group(2) or m.group(4))
                            new_title = os.path.splitext(os.path.basename(dst))[0]
                            try:
                                c.execute(
                                    "UPDATE episodes SET title=? WHERE series_id=? AND season=? AND episode=?",
                                    (new_title, series_id, sea, epi)
                                )
                                db.conn.commit()
                            except Exception as e:
                                logger.warning(f"rename-execute db commit: {e}")

        with _rename_progress_lock:
            RENAME_PROGRESS["status"] = "idle"

        # --- PULIZIA DOPPIONI E CADAVERI (invariata) ---
        cfg_data    = Config()
        _cleanup    = str(getattr(cfg_data, 'cleanup_upgrades', 'no')).lower() in ('yes', 'true', '1')
        _trash      = str(getattr(cfg_data, 'trash_path', '')).strip()
        trash_count = 0

        if _cleanup and _trash and os.path.exists(apath):
            from core.cleaner import _handle_duplicate
            from core.models import Parser, normalize_series_name, _series_name_matches
            from collections import defaultdict
            _re_se2  = re.compile(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})|0*(\d{1,2})x0*(\d{1,3})')
            _re_ep2  = re.compile(r'(?i)(?:ep|episodio|episode|e)[._\-\s]*0*(\d{1,3})|^0*(\d{1,3})(?:[\s\.\-]|$)')
            _re_fld2 = re.compile(r'(?i)(?:stagione|season|serie|s)\s*[._\-]*0*(\d+)')

            for root_dir, _, files_in_dir in os.walk(apath):
                folder_season = None
                fm = _re_fld2.search(os.path.basename(root_dir))
                if fm:
                    folder_season = int(fm.group(1))

                ep_videos = defaultdict(list)
                ep_aux    = defaultdict(list)

                for fname in files_in_dir:
                    sea = epi = 0
                    m_std = _re_se2.search(fname)
                    if m_std:
                        sea = int(m_std.group(1) or m_std.group(3))
                        epi = int(m_std.group(2) or m_std.group(4))
                    elif folder_season is not None:
                        m_ep = _re_ep2.search(fname)
                        if m_ep:
                            sea = folder_season
                            epi = int(m_ep.group(1) or m_ep.group(2))
                    if sea > 0 and epi > 0:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in _VIDEO_EXTS and 'sample' not in fname.lower():
                            ep_videos[(sea, epi)].append(fname)
                        else:
                            ep_aux[(sea, epi)].append(fname)

                for (sea, epi), video_list in ep_videos.items():
                    best_f = video_list[0]
                    if len(video_list) > 1:
                        scored_files = []
                        for f in video_list:
                            try:
                                fpath = os.path.join(root_dir, f)
                                q = Parser.parse_quality(f)
                                name_bonus = 10 if _series_name_matches(normalize_series_name(series_name), normalize_series_name(f)) else 0
                                score = (q.score() if hasattr(q, 'score') else 0) + name_bonus
                                size  = os.path.getsize(fpath) if os.path.exists(fpath) else 0
                                scored_files.append((score, size, f))
                            except Exception as e:
                                logger.debug(f"scored_files getsize: {e}")
                                scored_files.append((0, 0, f))
                        scored_files.sort(key=lambda x: (x[0], x[1]), reverse=True)
                        best_score, best_size, best_f = scored_files[0]
                        for score, size, f in scored_files[1:]:
                            src_dup = os.path.join(root_dir, f)
                            if not os.path.exists(src_dup):
                                continue
                            reason = f"doppione di S{sea:02d}E{epi:02d} (vince '{best_f}')"
                            if _handle_duplicate(src_dup, _trash, action=str(getattr(cfg_data, 'cleanup_action', 'move')).lower(), reason=reason):
                                trash_count += 1

                    best_base = os.path.splitext(best_f)[0]
                    for aux_f in ep_aux[(sea, epi)]:
                        if not aux_f.startswith(best_base):
                            # Protezione falsi cadaveri: se il file ausiliario contiene
                            # la stessa coppia S/E del video ma ha un base diverso,
                            # potrebbe essere un .srt legittimo non rinominato (nome
                            # vecchio rimasto sul disco). Lo eliminiamo SOLO se il suo
                            # base è davvero diverso da best_base ignorando qualità/tag,
                            # cioè se non condivide neanche il prefisso S/E.
                            aux_base = os.path.splitext(aux_f)[0]
                            aux_has_se = bool(_re_se2.search(aux_base))
                            if aux_has_se:
                                # Il file ausiliario ha un suo pattern S/E esplicito:
                                # è quasi certamente un residuo di un download precedente
                                # (nome diverso dal video attuale) → cadavere legittimo
                                pass
                            else:
                                # Senza pattern S/E nel nome (es. "01 ITA.srt" in cartella
                                # stagione) non possiamo distinguere con certezza → skip
                                continue
                            aux_src = os.path.join(root_dir, aux_f)
                            if not os.path.exists(aux_src):
                                continue
                            reason = f"file orfano (non appartiene al video '{best_f}')"
                            if _handle_duplicate(aux_src, _trash, action=str(getattr(cfg_data, 'cleanup_action', 'move')).lower(), reason=reason):
                                trash_count += 1

        return jsonify({'success': True, 'renamed': count, 'removed': trash_count})
    except Exception as e:
        with _rename_progress_lock:
            RENAME_PROGRESS["status"] = "idle"
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/series/<int:series_id>/search-missing', methods=['POST'])
def search_missing_series_api(series_id):
    """Avvia la ricerca mirata degli episodi mancanti in background."""
    def _bg_search_missing():
        try:
            from core.config import Config
            from core.engine import Engine
            from core.models import Parser
            import requests

            cfg = Config()
            eng = Engine()
            _web_port = int(getattr(cfg, 'web_port', None) or 5000)
            
            # Usiamo il database globale dell'interfaccia Web (che ha get_series_episodes)
            global db

            c = db.conn.cursor()
            c.execute('SELECT name FROM series WHERE id=?', (series_id,))
            row = c.fetchone()
            if not row: return
            series_name = row['name']

            from core.models import normalize_series_name as _nsn_cfg
            serie_cfg = next((s for s in cfg.series if _nsn_cfg(s['name']) == _nsn_cfg(series_name)), None)
            if not serie_cfg: return

            log_maintenance(f"🕵️ Avviata ricerca mirata mancanti per: {series_name}")

            api_key = getattr(cfg, 'tmdb_api_key', '').strip()
            tmdb_lang = getattr(cfg, 'tmdb_language', 'it-IT').strip()

            tmdb_seasons = {}
            if api_key:
                search_url = f"https://api.themoviedb.org/3/search/tv?api_key={api_key}&query={series_name}&language={tmdb_lang}"
                search_res = requests.get(search_url, timeout=5).json()
                if search_res.get('results'):
                    tmdb_id = search_res['results'][0]['id']
                    details = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={api_key}&language={tmdb_lang}", timeout=5).json()
                    for s in details.get('seasons', []):
                        if s['season_number'] > 0:
                            tmdb_seasons[s['season_number']] = s['episode_count']

            episodes_db = db.get_series_episodes(series_id)
            downloaded = set()
            for ep in episodes_db:
                if ep['downloaded_at']:
                    downloaded.add((ep['season'], ep['episode']))

            ignored_seasons = serie_cfg.get('ignored_seasons', []) # <-- Aggiunto
            
            missing_list = []
            for s_num, ep_count in tmdb_seasons.items():
                if s_num in ignored_seasons:
                    continue # <--- SALTA COMPLETAMENTE LE STAGIONI SPENTE!
                for e_num in range(1, ep_count + 1):
                    if (s_num, e_num) not in downloaded:
                        missing_list.append((s_num, e_num))

            if not missing_list:
                log_maintenance(f"✅ Nessun episodio mancante trovato per {series_name}.")
                return

            log_maintenance(f"🔍 Trovati {len(missing_list)} episodi mancanti per {series_name}. Interrogo Jackett...")

            lang_req = serie_cfg.get('language', serie_cfg.get('lang', 'ita'))
            qual_req = serie_cfg.get('quality', serie_cfg.get('qual', ''))
            aliases  = serie_cfg.get('aliases', [])
            found_count = 0

            names_to_search = [series_name]
            for _a in (aliases or []):
                _a = str(_a).strip()
                if _a and _a.lower() != series_name.lower():
                    names_to_search.append(_a)

            for s_num, e_num in missing_list[:15]:
                ep_str = f"S{s_num:02d}E{e_num:02d}"
                log_maintenance(f"   📡 Cerco: {series_name} {ep_str}...")

                j_res = []
                for search_name in names_to_search:
                    base_q = search_name
                    if lang_req and lang_req not in ('custom', 'none', 'any', '*'):
                        base_q += f" {lang_req}"
                    j_res.extend(eng._jackett_search(base_q, {}, season=s_num, ep=e_num))
                    if len(names_to_search) > 1:
                        time.sleep(2)

                if not j_res:
                    _ed2k_en = str(_cdb.get_setting('gap_fill_ed2k', 'no')).lower() in ('yes','true','1')
                    if _ed2k_en:
                        try:
                            from core.clients.amule import AmuleClient
                            _amule_cfg = Config()
                            _am_en = str(getattr(_amule_cfg, 'qbt', {}).get('amule_enabled', 'no')).lower() in ('yes','true','1')
                            if _am_en:
                                _q = f"{series_name} {ep_str}"
                                log_maintenance(f"   🔵 Jackett: nessun risultato. Provo eD2k: '{_q}'...")
                                with AmuleClient(_amule_cfg) as _am:
                                    _am_res = _am.search(_q, network='global')
                                if _am_res:
                                    _best = max(_am_res, key=lambda r: r.get('sources', 0))
                                    log_maintenance(f"   🔵 eD2k: trovato '{_best['name']}' ({_best.get('sources',0)} sorgenti)")
                                    with AmuleClient(_amule_cfg) as _am:
                                        _ok = _am.download_result(_best['idx'])
                                    if _ok:
                                        log_maintenance(f"   ✅ eD2k: {ep_str} inviato ad aMule")
                                        found_count += 1
                                else:
                                    log_maintenance(f"   ⚠️  eD2k: nessun risultato per '{_q}'")
                        except Exception as _e:
                            log_maintenance(f"   ⚠️  eD2k fallback errore: {_e}")
                    time.sleep(2)
                    continue

                best_ep_cand = None
                best_ep_score = -1

                for item in j_res:
                    if not cfg._lang_ok(item['title'], lang_req): continue
                    ep_p = Parser.parse_series_episode(item['title'])
                    
                    if ep_p and ep_p['season'] == s_num and ep_p['episode'] == e_num:
                        match = cfg.find_series_match(ep_p['name'], ep_p['season'])
                        if not match or match['name'] != series_name: continue

                        # Controllo manuale della qualità per non dipendere dal DB del motore
                        min_rank = cfg._min_res_from_qual_req(qual_req)
                        max_rank = cfg._max_res_from_qual_req(qual_req)
                        this_rank = cfg._res_rank_from_title(item['title'])
                        _score = ep_p['quality'].score() if hasattr(ep_p['quality'], 'score') else 0
                        bonus = cfg.get_custom_score(item['title']) if hasattr(cfg, 'get_custom_score') else 0
                        _score += bonus

                        if min_rank <= this_rank <= max_rank:
                            if _score > best_ep_score:
                                best_ep_score = _score
                                best_ep_cand = item['magnet']
                            # Registra come match valido (lingua+qualità ok)
                            try:
                                db.record_feed_match(series_id, s_num, e_num,
                                                     item['title'], int(_score),
                                                     None, item['magnet'])
                            except Exception as e:
                                logger.debug(f"record_feed_match manual: {e}")
                                pass
                        else:
                            # Lingua ok ma qualità fuori range: match parziale
                            _fail = 'below_quality' if this_rank < min_rank else 'above_quality'
                            try:
                                db.record_feed_match(series_id, s_num, e_num,
                                                     item['title'], int(_score),
                                                     _fail, item['magnet'])
                            except Exception as e:
                                logger.debug(f"record_feed_match manual fail: {e}")
                                pass

                if best_ep_cand:
                    import urllib.request, json, base64, ssl
                    try:
                        # Usa jackett_timeout dalla config (default 30s)
                        _http_timeout = int((parse_series_config().get('settings') or {}).get('jackett_timeout', 30))
                        # 1. PREPARAZIONE INVIO (MAGNET vs HTTP)
                        if best_ep_cand.startswith('http'):
                            log_maintenance(f"   📥 Analisi link tracker...")
                            import requests
                            
                            res = requests.get(best_ep_cand, timeout=_http_timeout, verify=False, allow_redirects=False, headers={'User-Agent': 'Mozilla/5.0'})
                            
                            current_link = best_ep_cand
                            if res.status_code in (301, 302, 307, 308):
                                loc = res.headers.get('Location', '')
                                if loc.startswith('magnet:'):
                                    log_maintenance(f"   🧲 Redirect rilevato: è un link Magnet.")
                                    current_link = loc
                                    best_ep_cand = loc 
                                else:
                                    res = requests.get(loc, timeout=_http_timeout, verify=False, headers={'User-Agent': 'Mozilla/5.0'})

                            if not current_link.startswith('magnet:') and res.status_code == 200:
                                b64_data = base64.b64encode(res.content).decode('utf-8')
                                payload = {
                                    'filename': f"{series_name}_S{s_num:02d}E{e_num:02d}.torrent",
                                    'data': b64_data,
                                    'download_now': True
                                }
                                req_api = urllib.request.Request(
                                    f'http://127.0.0.1:{_web_port}/api/upload-torrent',
                                    data=json.dumps(payload).encode(),
                                    headers={'Content-Type': 'application/json'}
                                )
                            elif current_link.startswith('magnet:'):
                                pass
                            else:
                                raise Exception(f"Errore download file (Status: {res.status_code})")

                        if best_ep_cand.startswith('magnet:'):
                            log_maintenance(f"   🧲 Invio link Magnet al client...")
                            req_api = urllib.request.Request(
                                f'http://127.0.0.1:{_web_port}/api/send-magnet',
                                data=json.dumps({'magnet': best_ep_cand}).encode(),
                                headers={'Content-Type': 'application/json'}
                            )

                        urllib.request.urlopen(req_api, timeout=10)
                        log_maintenance(f"   ✅ Successo: {ep_str} inviato (Score: {best_ep_score})")
                        _save_tag_for_magnet(best_ep_cand, 'Serie TV')
                        found_count += 1
                        
                        now_iso = datetime.now().isoformat()
                        c = db.conn.cursor()
                        c.execute('INSERT INTO episodes (series_id, season, episode, title, quality_score, is_repack, downloaded_at) VALUES (?, ?, ?, ?, ?, 0, ?)',
                            (series_id, s_num, e_num, f"{series_name} {ep_str}", int(best_ep_score), now_iso))
                        db.conn.commit()
                        # Registra come 'downloaded' nel feed_matches
                        try:
                            db.record_feed_match(series_id, s_num, e_num,
                                                 f"{series_name} {ep_str}", int(best_ep_score),
                                                 'downloaded', best_ep_cand)
                        except Exception as e:
                            logger.debug(f"record_feed_match rescore: {e}")
                            pass
                        
                    except Exception as e:
                        log_maintenance(f"   ❌ Errore invio per {ep_str}: {str(e)}")
                else:
                    log_maintenance(f"   ❌ Nessun risultato valido per {ep_str} (qualità/lingua).")
                    _ed2k_en2 = str(_cdb.get_setting('gap_fill_ed2k', 'no')).lower() in ('yes','true','1')
                    if _ed2k_en2:
                        try:
                            from core.clients.amule import AmuleClient
                            _amule_cfg2 = Config()
                            _am_en2 = str(getattr(_amule_cfg2, 'qbt', {}).get('amule_enabled', 'no')).lower() in ('yes','true','1')
                            if _am_en2:
                                _q2 = f"{series_name} {ep_str}"
                                log_maintenance(f"   🔵 Provo fallback eD2k: '{_q2}'...")
                                with AmuleClient(_amule_cfg2) as _am2:
                                    _am_res2 = _am2.search(_q2, network='global')
                                if _am_res2:
                                    _best2 = max(_am_res2, key=lambda r: r.get('sources', 0))
                                    log_maintenance(f"   🔵 eD2k: '{_best2['name']}' ({_best2.get('sources',0)} sorgenti)")
                                    with AmuleClient(_amule_cfg2) as _am2:
                                        _ok2 = _am2.download_result(_best2['idx'])
                                    if _ok2:
                                        log_maintenance(f"   ✅ eD2k: {ep_str} inviato ad aMule")
                                        found_count += 1
                        except Exception as _e2:
                            log_maintenance(f"   ⚠️  eD2k fallback errore: {_e2}")

                time.sleep(2)

            log_maintenance(f"🏁 Ricerca mirata terminata per {series_name}. Inviati al client: {found_count}.")
        except Exception as e:
            import traceback
            err_details = traceback.format_exc()
            log_maintenance(f"❌ Errore critico in background search:\n{err_details}")

    import threading
    threading.Thread(target=_bg_search_missing, daemon=True).start()
    return jsonify({'success': True, 'message': 'Ricerca mirata avviata in background! Apri il tab Log per seguirla.'})
 
@app.route('/api/series/<int:series_id>/toggle-season', methods=['POST'])
def toggle_season(series_id):
    """Accende/spegne il monitoraggio di una specifica stagione."""
    try:
        data = request.json or {}
        season = int(data.get('season', 0))
        
        c = db.conn.cursor()
        c.execute('SELECT name FROM series WHERE id=?', (series_id,))
        series_name = c.fetchone()['name']
        
        cfg = parse_series_config()
        updated = False
        for s in cfg.get('series', []):
            if s.get('name', '').strip().lower() == series_name.strip().lower():
                ignored = s.get('ignored_seasons', [])
                if season in ignored:
                    ignored.remove(season) # La riaccende
                else:
                    ignored.append(season) # La spegne
                s['ignored_seasons'] = ignored
                updated = True
                break
        
        if updated:
            save_series_config(cfg)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Serie non trovata'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})    


@app.route('/api/rename-format-preview', methods=['GET'])
def rename_format_preview():
    from core.renamer import _build_filename, RENAME_FORMAT_LABELS
    try:
        from core.mediainfo_helper import mediainfo_available
        mi_ok = mediainfo_available()
    except ImportError:
        mi_ok = False

    template_str = request.args.get('tmpl', '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}][{HDR}][{Lingue}]')

    example_tags = {
        'resolution':  '1080p',
        'video_codec': 'h264',
        'audio_codec': 'EAC3 Atmos',
        'channels':    '5.1',
        'hdr':         'HDR10',
        'languages':   ['IT', 'EN'],
    }
    examples = {}
    for fmt in ('base', 'standard', 'completo', 'custom'):
        examples[fmt] = _build_filename(
            'Nome Serie Generica', 1, 1, 'Titolo Episodio', '.mkv',
            fmt=fmt, year='2024', tags=example_tags, template_str=template_str
        )
    return jsonify({
        'success': True,
        'mediainfo_available': mi_ok,
        'examples': examples,
        'labels': RENAME_FORMAT_LABELS,
    })


@app.route('/api/sources/health', methods=['GET'])
def sources_health():
    """Effettua un Ping/Test in tempo reale su tutte le fonti configurate (Jackett + URL).

    NOTA: verify=False è intenzionale — Jackett gira tipicamente su localhost con
    certificato self-signed; i tracker pubblici hanno spesso SSL non valido.
    Il warning viene soppresso solo localmente, non a livello di processo.
    """
    from core.config import Config
    import requests
    import time
    import urllib3

    cfg = Config()
    results = []

    # 1. Test Jackett + Prowlarr (tutti gli indexer configurati)
    from core.engine import Engine as _PingEng
    for _ix in _PingEng._get_indexers(cfg):
        _ix_url  = _ix['url'].rstrip('/')
        _ix_api  = _ix['api']
        _ix_name = _ix['label']
        # Usa l'endpoint /api corretto per tipo (Jackett vs Prowlarr)
        _test_url = _PingEng._torznab_url(_ix_url)
        start = time.time()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(_test_url, params={'apikey': _ix_api, 't': 'caps'}, timeout=10, verify=False)
            ms = int((time.time() - start) * 1000)
            if r.status_code == 200:
                results.append({'name': f'{_ix_name} (Aggregatore)', 'url': _ix_url, 'status': 'online', 'ping': ms, 'error': ''})
            else:
                results.append({'name': f'{_ix_name} (Aggregatore)', 'url': _ix_url, 'status': 'error', 'ping': ms, 'error': f'HTTP {r.status_code}'})
        except requests.exceptions.Timeout:
            results.append({'name': f'{_ix_name} (Aggregatore)', 'url': _ix_url, 'status': 'timeout', 'ping': '>10000', 'error': 'Timeout 10s'})
        except Exception as e:
            logger.debug(f"indexer check: {e}")
            results.append({'name': f'{_ix_name} (Aggregatore)', 'url': _ix_url, 'status': 'error', 'ping': '-', 'error': 'Non Raggiungibile'})

    # 2. Test URLs di Scraping
    for u in getattr(cfg, 'urls', []):
        name = "Il Corsaro Nero" if "corsaro" in u.lower() else ("ExtTo" if "extto" in u.lower() else "Sito Torrent")
        start = time.time()
        try:
            # Fake browser header per aggirare blocchi base
            # verify=False: i tracker pubblici spesso hanno SSL non valido o scaduto
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(u, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            ms = int((time.time() - start) * 1000)
            # Accettiamo 403 e 302 come "Online" perché molti tracker mettono Cloudflare o redirect
            if r.status_code in [200, 301, 302, 403]: 
                warn = f'(Risposta: HTTP {r.status_code})' if r.status_code != 200 else ''
                results.append({'name': name, 'url': u, 'status': 'online', 'ping': ms, 'error': warn})
            else:
                results.append({'name': name, 'url': u, 'status': 'error', 'ping': ms, 'error': f'HTTP {r.status_code}'})
        except requests.exceptions.Timeout:
            results.append({'name': name, 'url': u, 'status': 'timeout', 'ping': '>10000', 'error': 'Timeout 10s'})
        except Exception as e:
            results.append({'name': name, 'url': u, 'status': 'error', 'ping': '-', 'error': 'Sito Offline o Irraggiungibile'})

    return jsonify({'success': True, 'sources': results})

@app.route('/api/log', methods=['POST'])
def write_client_log():
    """Permette alla WebUI di scrivere notifiche ed eventi nel file log principale"""
    try:
        data = request.json or {}
        msg = data.get('message', '')
        if msg:
            log_maintenance(f"🖥️ [WebUI] {msg}")
        return jsonify({'success': True})
    except Exception as e:
        logger.debug(f"log_maintenance endpoint: {e}")
        return jsonify({'success': False})
    

# ============================================================================
# FUMETTI — GETCOMICS
# ============================================================================

def _comics_db():
    """Restituisce istanza ComicsDB (lazy import per non pesare all'avvio)."""
    from core.comics import ComicsDB
    return ComicsDB(os.path.join(BASE_DIR, 'comics.db'))

def _get_notifier():
    """Istanzia il Notifier con la config corrente. Ritorna None se non configurato."""
    try:
        from core.notifier import Notifier
        cfg = parse_series_config().get('settings', {})
        return Notifier(cfg)
    except Exception as e:
        logger.debug(f"get_notifier: {e}")
        return None


def _comics_scraper():
    from core.comics import GetComicsScraper
    return GetComicsScraper()

def _comics_send_magnet(magnet: str, save_path: str = '') -> bool:
    """Invia un magnet/torrent al client configurato. Usato dal ciclo fumetti."""
    try:
        config   = parse_series_config()
        settings = config.get('settings', {})
        target_path = save_path if save_path else settings.get('libtorrent_dir', '/downloads').strip()

        if settings.get('qbittorrent_enabled') == 'yes':
            qb_url  = settings.get('qbittorrent_url', '')
            qb_user = settings.get('qbittorrent_username', '')
            qb_pass = settings.get('qbittorrent_password', '')
            sess = requests.Session()
            r = sess.post(f"{qb_url}/api/v2/auth/login", data={'username': qb_user, 'password': qb_pass}, timeout=5)
            if r.text == 'Ok.':
                data = {'urls': magnet, 'paused': 'true'}
                if target_path: data['savepath'] = target_path
                r = sess.post(f"{qb_url}/api/v2/torrents/add", data=data, timeout=5)
                if r.text == 'Ok.':
                    _save_tag_for_magnet(magnet, 'Fumetti')
                    return True

        if settings.get('transmission_enabled') == 'yes':
            tr_url  = settings.get('transmission_url', '')
            tr_user = settings.get('transmission_username', '')
            tr_pass = settings.get('transmission_password', '')
            auth = (tr_user, tr_pass) if tr_user else None
            r = requests.post(tr_url, auth=auth, timeout=5)
            sid = r.headers.get('X-Transmission-Session-Id', '')
            payload = {'method': 'torrent-add', 'arguments': {'filename': magnet, 'paused': True}}
            if target_path: payload['arguments']['download-dir'] = target_path
            r = requests.post(tr_url, json=payload, headers={'X-Transmission-Session-Id': sid}, auth=auth, timeout=5)
            if r.status_code == 409:
                sid = r.headers.get('X-Transmission-Session-Id', sid)
                r = requests.post(tr_url, json=payload, headers={'X-Transmission-Session-Id': sid}, auth=auth, timeout=5)
            if r.status_code == 200:
                _save_tag_for_magnet(magnet, 'Fumetti')
                return True

        if settings.get('libtorrent_enabled') == 'yes':
            # Se è un file .torrent (es. GetComics), lo scarica prima di passarlo a Libtorrent per avere indietro l'Hash corretto da Taggare!
            if magnet.startswith('http://') or magnet.startswith('https://'):
                import base64
                try:
                    r_file = requests.get(magnet, timeout=15, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
                    if r_file.status_code == 200:
                        b64_data = base64.b64encode(r_file.content).decode('utf-8')
                        payload = {'data': b64_data, 'filename': 'comic.torrent'}
                        if target_path: payload['save_path'] = target_path
                        r = requests.post(f'http://127.0.0.1:{get_engine_port()}/api/torrents/add_torrent_file', json=payload, timeout=10)
                        if r.status_code == 200 and r.json().get('ok'):
                            _lt_h = r.json().get('hash', '')
                            if _lt_h: _save_tag_for_hash(_lt_h, 'Fumetti')
                            return True
                except Exception as e:
                    logger.error(f"Error downloading comics torrent file: {e}")
            
            # Se è un classico Magnet Link
            payload = {'magnet': magnet}
            if target_path: payload['save_path'] = target_path
            r = requests.post(f'http://127.0.0.1:{get_engine_port()}/api/torrents/add', json=payload, timeout=10)
            if r.status_code == 200 and r.json().get('ok'):
                _save_tag_for_magnet(magnet, 'Fumetti')
                _lt_h = r.json().get('hash', '')
                if _lt_h: _save_tag_for_hash(_lt_h, 'Fumetti')
                return True

    except Exception as e:
        logger.error(f"comics send_magnet error: {e}")
    return False


@app.route('/api/comics/search', methods=['GET'])
def comics_search():
    """Cerca fumetti su getcomics.org (scraping live)."""
    query = request.args.get('q', '').strip()
    page  = int(request.args.get('page', 1))
    if not query:
        return jsonify({'success': False, 'error': 'Parametro q mancante'})
    try:
        scraper = _comics_scraper()
        results = scraper.search(query, page=page)
        return jsonify({'success': True, 'results': results, 'query': query})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/weekly', methods=['GET'])
def comics_weekly():
    """
    Restituisce i link del weekly pack per una data specifica.
    Parametri: date=YYYY-MM-DD (default: data odierna)
    """
    date_str = request.args.get('date', '')
    if not date_str:
        from datetime import date as _date
        date_str = _date.today().isoformat()
    try:
        scraper = _comics_scraper()
        result  = scraper.get_weekly_links(date_str)
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/weekly/settings', methods=['GET'])
def comics_weekly_settings_get():
    """Legge le impostazioni del download automatico weekly."""
    try:
        comics_db = _comics_db()
        cfg = comics_db.get_weekly_settings()
        return jsonify({'success': True, **cfg})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/comics/weekly/settings', methods=['POST'])
def comics_weekly_settings_set():
    """Salva le impostazioni del download automatico weekly."""
    try:
        data      = request.get_json() or {}
        enabled   = bool(data.get('weekly_enabled', False))
        from_date = str(data.get('weekly_from_date', '') or '')
        comics_db = _comics_db()
        comics_db.set_weekly_settings(enabled, from_date)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/comics/weekly/list', methods=['GET'])
def comics_weekly_list():
    """Lista dei weekly pack trovati/inviati (dallo storico DB), con paginazione."""
    try:
        comics_db = _comics_db()
        limit  = int(request.args.get('limit', 20))
        page   = max(1, int(request.args.get('page', 1)))
        offset = (page - 1) * limit
        rows   = comics_db.get_weekly_packs(limit=limit, offset=offset)
        total  = comics_db.count_weekly_packs()
        return jsonify({
            'success': True,
            'packs':   rows,
            'total':   total,
            'page':    page,
            'limit':   limit,
            'pages':   max(1, (total + limit - 1) // limit),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/weekly/send', methods=['POST'])
def comics_weekly_send():
    """Invia manualmente un weekly pack al client torrent cercando fino a 7 giorni prima."""
    try:
        data     = request.get_json(silent=True) or {}
        date_str = data.get('date', '').strip()
        if not date_str:
            return jsonify({'success': False, 'error': 'data mancante'})

        scraper = _comics_scraper()
        # Abilita la ricerca flessibile!
        result  = scraper.get_weekly_links(date_str, flexible=True)
        
        if not result['found']:
            return jsonify({'success': False, 'error': f'Nessun pack trovato attorno al {date_str}'})

        magnet = result['magnets'][0] if result['magnets'] else ''
        torrent = result['torrents'][0] if result['torrents'] else ''
        link   = magnet or torrent
        actual_date = result['pack_date']

        ok = _comics_send_magnet(link)
        if ok:
            comics_db = _comics_db()
            comics_db.add_weekly(actual_date, magnet, torrent)
            comics_db.mark_weekly_sent(actual_date)
            push_notification('download', '📚 Weekly Pack Fumetti',
                              f'Pack {actual_date} inviato al client', {})
            return jsonify({'success': True, 'message': f'Weekly pack {actual_date} trovato e inviato!'})
            
        return jsonify({'success': False, 'error': 'Invio al client torrent fallito'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
        
@app.route('/api/comics/check-links', methods=['GET'])
def comics_check_links():
    """Controlla e restituisce tutti i link scaricabili di una pagina GetComics."""
    try:
        post_url = request.args.get('url', '').strip()
        if not post_url:
            return jsonify({'success': False, 'error': 'URL mancante'})
        scraper = _comics_scraper()
        links = scraper.get_links(post_url)
        magnets    = links.get('magnets',  []) or []
        torrents   = links.get('torrents', []) or []
        ddls       = links.get('ddls',     []) or []
        mega_links = links.get('mega',     []) or []
        torrent_link = (magnets + torrents + [None])[0]
        mega_link    = (mega_links + [None])[0]
        ddl_link     = (ddls + [None])[0]
        import shutil as _shutil
        return jsonify({
            'success':        True,
            'torrent_link':   torrent_link,
            'mega_link':      mega_link,
            'ddl_link':       ddl_link,
            'has_torrent':    bool(torrent_link),
            'has_mega':       bool(mega_link),
            'has_ddl':        bool(ddl_link),
            'available':      bool(torrent_link or mega_link or ddl_link),
            'megatools_ok':   bool(_shutil.which('megadl')),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/download-mega', methods=['POST'])
def comics_download_mega():
    """Avvia il download di un fumetto da Mega usando megatools (megadl)."""
    try:
        import shutil as _shutil
        if not _shutil.which('megadl'):
            return jsonify({'success': False,
                            'error': 'megatools non installato — esegui: apt install megatools'}), 503
        data     = request.get_json(silent=True) or {}
        mega_url = data.get('mega_url', '').strip()
        title    = data.get('title', 'fumetto').strip()
        logger.info(f"[MEGA] endpoint called: title={title!r} mega_url={mega_url!r}")
        if not mega_url:
            logger.warning("[MEGA] Mega URL missing in request")
            return jsonify({'success': False, 'error': 'URL Mega mancante'})
        config   = parse_series_config()
        settings = config.get('settings', {})
        target   = settings.get('libtorrent_dir', '/downloads').strip()
        logger.info(f"[MEGA] target_dir={target!r}")
        from core.comics import download_comic_mega_bg, ACTIVE_HTTP_DOWNLOADS
        logger.info(f"[MEGA] ACTIVE_HTTP_DOWNLOADS has {len(ACTIVE_HTTP_DOWNLOADS)} entries before start")
        _notifier = _get_notifier()
        ok = download_comic_mega_bg(mega_url, target, title, notifier=_notifier)
        logger.info(f"[MEGA] download_comic_mega_bg returned {ok}, ACTIVE_HTTP_DOWNLOADS now has {len(ACTIVE_HTTP_DOWNLOADS)} entries")
        if ok:
            if _notifier:
                try: _notifier.notify_comic(title, is_weekly=False, method='mega')
                except Exception: pass
            return jsonify({'success': True, 'method': 'megatools',
                            'message': f'Download Mega avviato — visibile nel Torrent Manager'})
        return jsonify({'success': False, 'error': 'Avvio download Mega fallito'})
    except Exception as e:
        logger.error(f"[MEGA] endpoint exception: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/download-direct', methods=['POST'])
def comics_download_direct():
    """Scarica un fumetto via HTTP diretto o torrent/magnet.

    Accetta:
      { "url": "<pagina getcomics>" }              → scraping automatico
      { "url": "<ddl redirect>", "title": "..." }  → DDL già noto, salta scraping
    """
    try:
        import urllib.parse as _urlparse
        data  = request.get_json(silent=True) or {}
        url   = data.get('url', '').strip()
        title = data.get('title', '').strip()
        if not url:
            return jsonify({'success': False, 'error': 'URL mancante'})

        logger.info(f"[DDL] endpoint: url={url!r} title={title!r}")

        # Se l'URL è già un redirect GetComics (/dlds/...) usalo come DDL diretto
        # senza fare scraping della pagina
        is_ddl_direct = (
            '/dlds/' in url or
            url.endswith('.cbz') or url.endswith('.cbr') or url.endswith('.zip')
        )

        magnet = torrent = ddl = ''
        if is_ddl_direct:
            ddl = url
            logger.info(f"[DDL] Direct URL, skipping scraping")
        else:
            scraper = _comics_scraper()
            links   = scraper.get_links(url)
            magnet  = links.get('magnets', [''])[0] if links.get('magnets') else ''
            torrent = links.get('torrents', [''])[0] if links.get('torrents') else ''
            ddl     = links.get('ddls',    [''])[0] if links.get('ddls')    else ''
            logger.info(f"[DDL] scraping: magnet={bool(magnet)} torrent={bool(torrent)} ddl={bool(ddl)}")

        link = magnet or torrent
        if link:
            if _comics_send_magnet(link):
                return jsonify({'success': True, 'message': 'Download torrent avviato!'})
            return jsonify({'success': False, 'error': 'Invio al client torrent fallito.'})

        elif ddl:
            config     = parse_series_config()
            settings   = config.get('settings', {})
            target_dir = settings.get('libtorrent_dir', '/downloads').strip()
            if not title:
                title = _urlparse.unquote(
                    ddl.split('/')[-1]
                    .replace('.cbz','').replace('.cbr','').replace('-',' ')
                ).title().strip() or 'fumetto'

            from core.comics import download_comic_file_bg as download_comic_http_bg
            _notifier_ddl = _get_notifier()
            ok = download_comic_http_bg(ddl, target_dir, title, notifier=_notifier_ddl)
            logger.info(f"[DDL] http_bg started={ok} title={title!r}")
            if ok:
                if _notifier_ddl:
                    try: _notifier_ddl.notify_comic(title, is_weekly=False, method='http')
                    except Exception: pass
                return jsonify({'success': True,
                                'message': 'Download avviato — visibile nel Torrent Manager'})
            return jsonify({'success': False, 'error': 'Avvio download fallito.'})

        else:
            return jsonify({'success': False, 'error': 'Nessun file scaricabile trovato.'})

    except Exception as e:
        logger.error(f"[DDL] exception: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/comics', methods=['GET'])
def comics_list():
    """Lista fumetti monitorati."""
    try:
        comics_db = _comics_db()
        comics    = comics_db.get_comics()
        # Aggiunge conteggio storia per ogni fumetto
        for c in comics:
            history = comics_db.get_history(comic_id=c['id'], limit=1000)
            c['downloads_count'] = len(history)
        return jsonify({'success': True, 'comics': comics})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics', methods=['POST'])
def comics_add():
    """Aggiunge un fumetto al monitoraggio."""
    try:
        data = request.get_json(silent=True) or {}
        required = ['title', 'tag_url', 'from_date']
        for f in required:
            if not data.get(f, '').strip():
                return jsonify({'success': False,
                                'error': f'Campo obbligatorio mancante: {f}'})
        comics_db = _comics_db()
        result = comics_db.add_comic(
            title       = data['title'].strip(),
            tag_url     = data['tag_url'].strip(),
            cover_url   = data.get('cover_url', '').strip(),
            publisher   = data.get('publisher', '').strip(),
            description = data.get('description', '').strip(),
            from_date   = data['from_date'].strip(),
            save_path   = data.get('save_path', '').strip(),
        )
        if result.get('success'):
            try:
                _n = _get_notifier()
                if _n: _n.notify_comic_monitored(data['title'].strip())
            except Exception: pass
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/<int:comic_id>', methods=['GET'])
def comics_get(comic_id):
    """Dettaglio fumetto + storico download."""
    try:
        comics_db = _comics_db()
        comic     = comics_db.get_comic(comic_id)
        if not comic:
            return jsonify({'success': False, 'error': 'Non trovato'}), 404
        history = comics_db.get_history(comic_id=comic_id, limit=50)
        return jsonify({'success': True, 'comic': comic, 'history': history})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/<int:comic_id>', methods=['PATCH'])
def comics_update(comic_id):
    """Aggiorna un fumetto (from_date, save_path, enabled, ecc.)."""
    try:
        data = request.get_json(silent=True) or {}
        comics_db = _comics_db()
        ok        = comics_db.update_comic(comic_id, **data)
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/<int:comic_id>', methods=['DELETE'])
def comics_delete(comic_id):
    """Rimuove un fumetto dal monitoraggio."""
    try:
        comics_db = _comics_db()
        ok        = comics_db.delete_comic(comic_id)
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/<int:comic_id>/history', methods=['GET'])
def comics_history(comic_id):
    """Storico download di un fumetto."""
    try:
        comics_db = _comics_db()
        history   = comics_db.get_history(comic_id=comic_id, limit=100)
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/comics/cycle', methods=['POST'])
def comics_run_cycle():
    """Forza un ciclo manuale del modulo fumetti (eseguito in background)."""
    import threading as _threading
    from core.comics import run_comics_cycle

    def _bg():
        try:
            notifier = _get_notifier()
            run_comics_cycle(
                send_magnet_fn = _comics_send_magnet,
                logger_fn      = log_maintenance,
                notify_fn      = notifier.notify_comic if notifier and hasattr(notifier, 'notify_comic') else None,
            )
        except Exception as e:
            log_maintenance(f"❌ Errore ciclo fumetti: {e}")

    _threading.Thread(target=_bg, daemon=True).start()
    return jsonify({'success': True, 'message': 'Ciclo fumetti avviato in background'})


@app.route('/api/comics/history', methods=['GET'])
def comics_history_all():
    """Storico globale degli ultimi download fumetti."""
    try:
        comics_db = _comics_db()
        history   = comics_db.get_history(limit=100)
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
        
@app.route('/api/comics/history/<int:history_id>', methods=['DELETE'])
def comics_history_delete_single(history_id):
    """Rimuove un singolo record dallo storico per permetterne il ri-scaricamento."""
    try:
        comics_db = _comics_db()
        ok = comics_db.delete_history(history_id)
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})        


@app.route('/api/comics/history/<int:history_id>/resend', methods=['POST'])
def comics_history_resend(history_id):
    """Ri-invia al client torrent il magnet di un fumetto già scaricato in passato."""
    try:
        comics_db = _comics_db()
        # Imposta row_factory per accesso per nome colonna
        import sqlite3 as _sq3
        comics_db.conn.row_factory = _sq3.Row
        # Verifica colonne disponibili nella tabella
        cols = [r[1] for r in comics_db.conn.execute("PRAGMA table_info(comics_history)").fetchall()]
        logger.info(f"comics_history columns: {cols}")
        # Costruisce query in base alle colonne esistenti
        select_cols = "id, title"
        has_magnet      = 'magnet'      in cols
        has_torrent_url = 'torrent_url' in cols
        if has_magnet:      select_cols += ", magnet"
        if has_torrent_url: select_cols += ", torrent_url"
        row = comics_db.conn.execute(
            f"SELECT {select_cols} FROM comics_history WHERE id=?", (history_id,)
        ).fetchone()
        if not row:
            return jsonify({'success': False, 'error': f'Record {history_id} non trovato'})
        title       = row['title']
        link        = ''
        if has_magnet      and row['magnet']:      link = row['magnet']
        if not link and has_torrent_url and row['torrent_url']: link = row['torrent_url']
        logger.info(f"comics resend id={history_id} title={title!r} link={link[:60] if link else 'EMPTY'!r}")
        if not link:
            return jsonify({'success': False, 'error': f'Nessun magnet/torrent salvato per "{title}". Fumetto scaricato prima che il campo venisse registrato.'})
        ok = _comics_send_magnet(link)
        logger.info(f"comics resend _comics_send_magnet result: {ok}")
        if ok:
            return jsonify({'success': True,  'message': f'"{title}" inviato al client!'})
        else:
            return jsonify({'success': False, 'error': 'Invio fallito — controlla log e configurazione client torrent'})
    except Exception as e:
        logger.error(f"comics_history_resend error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})


# ===========================================================================
# I18N — Lingue & Traduzioni
# ===========================================================================

@app.route('/api/i18n/languages', methods=['GET'])
def i18n_languages():
    """Lista lingue disponibili nel DB + file YAML disponibili in languages/."""
    try:
        langs = _cdb.get_languages()
        yaml_files = _cdb.get_available_yaml_files()
        active = _cdb.get_ui_language()
        return jsonify({
            'languages': langs,
            'yaml_files': yaml_files,
            'active': active,
        })
    except Exception as e:
        logger.warning(f"i18n_languages: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/i18n/active', methods=['GET'])
def i18n_get_active():
    """Restituisce la lingua UI attiva."""
    try:
        return jsonify({'lang': _cdb.get_ui_language()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/i18n/active', methods=['POST'])
def i18n_set_active():
    """Cambia la lingua UI attiva."""
    try:
        data = request.get_json(force=True) or {}
        lang = data.get('lang', '').strip().lower()
        if not lang:
            return jsonify({'success': False, 'error': 'Campo lang mancante'}), 400
        _cdb.set_ui_language(lang)
        return jsonify({'success': True, 'lang': lang})
    except Exception as e:
        logger.warning(f"i18n_set_active: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/i18n/<lang>', methods=['GET'])
def i18n_get_lang(lang):
    """Restituisce tutte le stringhe tradotte per una lingua."""
    try:
        lang = _cdb._normalize_lang_code(lang.lower())
        strings = _cdb.get_translation(lang)
        return jsonify({'lang': lang, 'strings': strings, 'count': len(strings)})
    except Exception as e:
        logger.warning(f"i18n_get_lang {lang}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/i18n/<lang>', methods=['POST'])
def i18n_save_lang(lang):
    """Salva/aggiorna le stringhe tradotte per una lingua."""
    try:
        lang = _cdb._normalize_lang_code(lang.lower())
        data = request.get_json(force=True) or {}
        strings = data.get('strings', {})
        if not isinstance(strings, dict):
            return jsonify({'success': False, 'error': 'Il campo strings deve essere un oggetto'}), 400
        count = _cdb.set_translation_bulk(lang, strings)
        return jsonify({'success': True, 'saved': count, 'lang': lang})
    except Exception as e:
        logger.warning(f"i18n_save_lang {lang}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/i18n/<lang>', methods=['DELETE'])
def i18n_delete_lang(lang):
    """Elimina tutte le stringhe di una lingua dal DB."""
    try:
        lang = _cdb._normalize_lang_code(lang.lower())
        count = _cdb.delete_translation_lang(lang)
        return jsonify({'success': True, 'deleted': count, 'lang': lang})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.warning(f"i18n_delete_lang {lang}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/i18n/export/<lang>', methods=['POST'])
def i18n_export_yaml(lang):
    """Esporta le stringhe di una lingua in languages/<lang>.yaml."""
    try:
        result = _cdb.export_yaml_file(lang.lower())
        if result['error']:
            return jsonify({'success': False, 'error': result['error']}), 400
        return jsonify({'success': True, 'exported': result['exported'], 'path': result['path']})
    except Exception as e:
        logger.warning(f"i18n_export_yaml {lang}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/i18n/import/<lang>', methods=['POST'])
def i18n_import_yaml(lang):
    """Importa languages/<lang>.yaml nel DB (normalizza codice a 3 lettere)."""
    try:
        result = _cdb.import_yaml_file(lang.lower())  # cerca il file col nome originale
        if result['error']:
            return jsonify({'success': False, 'error': result['error']}), 400
        return jsonify({'success': True, 'imported': result['imported'], 'lang': result['lang']})
    except Exception as e:
        logger.warning(f"i18n_import_yaml {lang}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ramdisk_check', methods=['GET'])
def ramdisk_check():
    """
    Verifica che il path indicato sia usabile da EXTTO come RAM disk.
    Controlla: esistenza, tipo di mount (tmpfs/ramfs), permessi di scrittura, spazio.
    Risposta JSON:
      ok          bool   — tutto ok, EXTTO può scrivere
      writable    bool   — EXTTO ha i permessi di scrittura
      mount_type  str    — 'tmpfs' | 'ramfs' | 'other' | 'unknown'
      is_ramdisk  bool   — True se mount_type in (tmpfs, ramfs)
      total_gb    float
      used_gb     float
      free_gb     float
      warning     str    — eventuale avviso non bloccante
      error       str    — errore bloccante (path inesistente, non scrivibile, ecc.)
    """
    import shutil, tempfile
    path = request.args.get('path', '').strip()

    if not path:
        return jsonify({'ok': False, 'error': 'Percorso non specificato'})
    if not os.path.isdir(path):
        return jsonify({'ok': False, 'error': f'Directory non trovata: {path}'})

    # --- Tipo di mount ---
    mount_type = 'unknown'
    try:
        with open('/proc/mounts', 'r') as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3:
                    mpoint = parts[1]
                    fstype = parts[2]
                    # Cerca il mountpoint più specifico che prefissa il path
                    norm_path   = os.path.normpath(path)
                    norm_mpoint = os.path.normpath(mpoint)
                    if norm_path == norm_mpoint or norm_path.startswith(norm_mpoint + os.sep):
                        mount_type = fstype
    except Exception:
        pass

    is_ramdisk = mount_type in ('tmpfs', 'ramfs')

    # --- Permessi di scrittura: tenta un file temporaneo reale ---
    writable = False
    try:
        fd, tmp_path = tempfile.mkstemp(dir=path, prefix='.extto_writetest_')
        os.close(fd)
        os.unlink(tmp_path)
        writable = True
    except Exception:
        pass

    if not writable:
        return jsonify({
            'ok': False, 'writable': False, 'is_ramdisk': is_ramdisk,
            'mount_type': mount_type,
            'error': f'EXTTO non ha i permessi di scrittura su {path}'
        })

    # --- Spazio ---
    try:
        usage   = shutil.disk_usage(path)
        total_gb = round(usage.total / 1024**3, 2)
        used_gb  = round(usage.used  / 1024**3, 2)
        free_gb  = round(usage.free  / 1024**3, 2)
    except Exception as e:
        return jsonify({'ok': False, 'writable': True, 'error': f'Errore lettura spazio: {e}'})

    # --- Avviso non bloccante se non è un fs in RAM ---
    warning = ''
    if not is_ramdisk:
        warning = (
            f"Il filesystem rilevato è '{mount_type}', non tmpfs/ramfs. "
            f"EXTTO funzionerà, ma non si tratta di un vero RAM disk."
        )

    return jsonify({
        'ok':         True,
        'writable':   True,
        'mount_type': mount_type,
        'is_ramdisk': is_ramdisk,
        'total_gb':   total_gb,
        'used_gb':    used_gb,
        'free_gb':    free_gb,
        'warning':    warning,
        'error':      '',
    })


@app.route('/api/browse_dir', methods=['GET'])
def browse_dir():
    """Elenca le sottodirectory di un percorso (per il file browser della UI)."""
    path = request.args.get('path', '/')
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return jsonify({'success': False, 'error': 'Percorso non valido', 'dirs': []})
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isdir(full) and not name.startswith('.'):
                entries.append(name)
        parent = os.path.dirname(path) if path != '/' else None
        return jsonify({'success': True, 'path': path, 'parent': parent, 'dirs': entries})
    except PermissionError:
        return jsonify({'success': False, 'error': 'Permesso negato', 'dirs': []})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'dirs': []})

# ============================================================================
# ANTI-CACHE GLOBALE PER LE API
# ============================================================================
@app.after_request
def prevent_api_caching(response):
    """Impedisce al browser di memorizzare in cache le risposte delle API."""
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response



def _sync_language_files():
    """
    Auto-sync YAML -> DB all'avvio.
    Per ogni <lang>.yaml nella cartella languages/, importa nel DB tutte le chiavi
    mancanti rispetto a cio' che e' gia' in DB.
    Basta aggiornare/sostituire il file YAML e riavviare il servizio,
    senza dover fare import manuale dalla UI.
    """
    if not os.path.isdir(LANGUAGES_DIR):
        return
    try:
        yaml_files = [f for f in os.listdir(LANGUAGES_DIR) if f.endswith('.yaml')]
        if not yaml_files:
            return
        synced = []
        for fname in yaml_files:
            lang_code = fname[:-5].lower()  # es. "eng.yaml" -> "eng"
            try:
                result = _cdb.import_yaml_file(lang_code)
                if result.get('imported', 0) > 0:
                    synced.append(f"{lang_code}(+{result['imported']})")
            except Exception as e:
                logger.warning(f"[i18n-sync] {fname}: {e}")
        if synced:
            print(f"Language files synced: {', '.join(synced)}")
        else:
            print(f"Language files up to date ({len(yaml_files)} languages)")
    except Exception as e:
        logger.warning(f"[i18n-sync] startup sync failed: {e}")


# ============================================================================
# TRAKT.TV INTEGRATION
# ============================================================================

import threading as _trakt_threading

_TRAKT_FLOW: dict = {}
_TRAKT_FLOW_LOCK  = _trakt_threading.Lock()


def _trakt_settings() -> dict:
    return {
        "client_id":        str(_cdb.get_setting("trakt_client_id",        "") or ""),
        "client_secret":    str(_cdb.get_setting("trakt_client_secret",     "") or ""),
        "access_token":     str(_cdb.get_setting("trakt_access_token",      "") or ""),
        "refresh_token":    str(_cdb.get_setting("trakt_refresh_token",     "") or ""),
        "token_expires":    int(_cdb.get_setting("trakt_token_expires",     0)  or 0),
        "watchlist_sync":   bool(_cdb.get_setting("trakt_watchlist_sync",   False)),
        "scrobble_enabled": bool(_cdb.get_setting("trakt_scrobble_enabled", False)),
        "calendar_days":    int(_cdb.get_setting("trakt_calendar_days",     7)  or 7),
        "import_quality":   str(_cdb.get_setting("trakt_import_quality",    "720p+") or "720p+"),
        "import_language":  str(_cdb.get_setting("trakt_import_language",   "ita")   or "ita"),
    }


def _trakt_load():
    from core.trakt import load_trakt_client
    return load_trakt_client()


@app.route('/api/trakt/status', methods=['GET'])
def trakt_status():
    try:
        s = _trakt_settings()
        expires_in_days = None
        if s["token_expires"]:
            delta = s["token_expires"] - time.time()
            expires_in_days = max(0, int(delta // 86400))
        return jsonify({
            "configured":       bool(s["client_id"]),
            "authenticated":    bool(s["access_token"]),
            "expires_in_days":  expires_in_days,
            "watchlist_sync":   s["watchlist_sync"],
            "scrobble_enabled": s["scrobble_enabled"],
            "calendar_days":    s["calendar_days"],
            "import_quality":   s["import_quality"],
            "import_language":  s["import_language"],
        })
    except Exception as e:
        logger.error(f"trakt_status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trakt/settings', methods=['POST'])
def trakt_save_settings():
    try:
        data = request.get_json(force=True) or {}
        bulk = {}
        for key in ("client_id", "client_secret"):
            if key in data:
                bulk[f"trakt_{key}"] = str(data[key]).strip()
        for key in ("watchlist_sync", "scrobble_enabled"):
            if key in data:
                bulk[f"trakt_{key}"] = bool(data[key])
        if "calendar_days" in data:
            bulk["trakt_calendar_days"] = int(data["calendar_days"])
        for key in ("import_quality", "import_language"):
            if key in data:
                bulk[f"trakt_{key}"] = str(data[key]).strip()
        if bulk:
            _cdb.set_settings_bulk(bulk)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"trakt_save_settings: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trakt/auth/start', methods=['POST'])
def trakt_auth_start():
    try:
        client = _trakt_load()
        if not client:
            return jsonify({"error": "client_id non configurato. Salva prima le credenziali."}), 400
        flow = client.start_device_auth()
        if not flow:
            return jsonify({"error": "Impossibile avviare il Device Flow Trakt"}), 502
        with _TRAKT_FLOW_LOCK:
            _TRAKT_FLOW.update({
                "device_code": flow["device_code"],
                "interval":    flow.get("interval", 5),
                "expires_in":  flow.get("expires_in", 600),
                "started_at":  time.time(),
            })
        return jsonify({
            "user_code":        flow["user_code"],
            "verification_url": flow["verification_url"],
            "expires_in":       flow.get("expires_in", 600),
            "interval":         flow.get("interval", 5),
        })
    except Exception as e:
        logger.error(f"trakt_auth_start: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trakt/auth/poll', methods=['POST'])
def trakt_auth_poll():
    try:
        from core.trakt import save_trakt_tokens
        with _TRAKT_FLOW_LOCK:
            state = dict(_TRAKT_FLOW)

        if not state.get("device_code"):
            return jsonify({"status": "error", "message": "Nessun Device Flow attivo. Clicca su 'Collega Trakt'."})

        elapsed = time.time() - state.get("started_at", 0)
        if elapsed >= state.get("expires_in", 600):
            return jsonify({"status": "expired", "message": "Codice scaduto. Riprova."})

        client = _trakt_load()
        if not client:
            return jsonify({"status": "error", "message": "client_id non configurato"})

        resp = requests.post(
            "https://api.trakt.tv/oauth/device/token",
            json={
                "code":          state["device_code"],
                "client_id":     client.client_id,
                "client_secret": client.client_secret,
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            client.access_token  = data["access_token"]
            client.refresh_token = data.get("refresh_token", "")
            client.token_expires = int(time.time()) + int(data.get("expires_in", 7776000))
            save_trakt_tokens(client)
            with _TRAKT_FLOW_LOCK:
                _TRAKT_FLOW.clear()
            logger.info("[Trakt] Autenticazione completata.")
            return jsonify({"status": "authorized", "message": "✅ Connesso a Trakt!"})
        elif resp.status_code == 400:
            return jsonify({"status": "pending",  "message": "In attesa di autorizzazione..."})
        elif resp.status_code == 410:
            return jsonify({"status": "expired",  "message": "Codice scaduto."})
        elif resp.status_code == 418:
            return jsonify({"status": "denied",   "message": "Autorizzazione negata."})
        else:
            return jsonify({"status": "error",    "message": f"Errore Trakt: {resp.status_code}"})

    except Exception as e:
        logger.error(f"trakt_auth_poll: {e}")
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/trakt/auth/revoke', methods=['POST'])
def trakt_auth_revoke():
    try:
        from core.trakt import save_trakt_tokens
        client = _trakt_load()
        if client and client.is_authenticated():
            client.revoke_token()
            save_trakt_tokens(client)
        _cdb.set_settings_bulk({
            "trakt_access_token":  "",
            "trakt_refresh_token": "",
            "trakt_token_expires": 0,
        })
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"trakt_auth_revoke: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trakt/watchlist', methods=['GET'])
def trakt_get_watchlist():
    try:
        client = _trakt_load()
        if not client or not client.is_authenticated():
            return jsonify({"error": "Non autenticato su Trakt"}), 401
        shows = client.get_watchlist_shows()
        c = db.conn.cursor()
        c.execute("SELECT name FROM series WHERE enabled=1")
        existing = {r[0].lower() for r in c.fetchall()}
        return jsonify([{
            "title":     show["title"],
            "year":      show["year"],
            "ids":       show["ids"],
            "listed_at": show["listed_at"],
            "in_extto":  show["title"].lower() in existing,
        } for show in shows])
    except Exception as e:
        logger.error(f"trakt_get_watchlist: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trakt/watchlist/import', methods=['POST'])
def trakt_import_watchlist():
    try:
        data          = request.get_json(force=True) or {}
        s             = _trakt_settings()
        quality       = data.get("quality",       s["import_quality"])
        language      = data.get("language",      s["import_language"])
        skip_existing = data.get("skip_existing", True)
        filter_titles = [t.lower() for t in data.get("titles", [])]

        client = _trakt_load()
        if not client or not client.is_authenticated():
            return jsonify({"error": "Non autenticato su Trakt"}), 401

        shows = client.get_watchlist_shows()
        if not shows:
            return jsonify({"imported": 0, "skipped": 0, "message": "Watchlist vuota o errore API"})

        c = db.conn.cursor()
        c.execute("SELECT name FROM series")
        existing = {r[0].lower() for r in c.fetchall()}

        imported, skipped, errors = 0, 0, []

        for show in shows:
            title = show["title"]
            tl    = title.lower()
            if filter_titles and tl not in filter_titles:
                continue
            if skip_existing and tl in existing:
                skipped += 1
                continue
            tmdb_id = str(show["ids"].get("tmdb", "") or "")
            try:
                c.execute(
                    """INSERT INTO series
                       (name, quality_requirement, seasons, language, enabled,
                        archive_path, timeframe, aliases, ignored_seasons, tmdb_id)
                       VALUES (?, ?, '1+', ?, 1, '', 0, '[]', '[]', ?)
                       ON CONFLICT(name) DO NOTHING""",
                    (title, quality, language, tmdb_id)
                )
                if c.rowcount > 0:
                    imported += 1
                    logger.info(f"[Trakt] ✅ Importata: {title} (tmdb={tmdb_id})")
                else:
                    skipped += 1
            except Exception as ie:
                errors.append(f"{title}: {ie}")
                logger.warning(f"[Trakt] import '{title}': {ie}")

        db.conn.commit()
        return jsonify({
            "imported": imported,
            "skipped":  skipped,
            "errors":   errors,
            "message":  f"{imported} serie importate, {skipped} già presenti.",
        })
    except Exception as e:
        logger.error(f"trakt_import_watchlist: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trakt/calendar', methods=['GET'])
def trakt_get_calendar():
    try:
        from datetime import date as _date
        client = _trakt_load()
        if not client or not client.is_authenticated():
            return jsonify({"error": "Non autenticato su Trakt"}), 401

        s          = _trakt_settings()
        days       = max(1, min(int(request.args.get("days", s["calendar_days"]) or 7), 31))
        start_date = request.args.get("start", _date.today().isoformat())

        episodes = client.get_my_calendar(start_date=start_date, days=days)

        c = db.conn.cursor()
        c.execute("SELECT name FROM series WHERE enabled=1")
        existing = {r[0].lower() for r in c.fetchall()}
        for ep in episodes:
            ep["in_extto"] = ep["series_title"].lower() in existing

        return jsonify({"start_date": start_date, "days": days, "episodes": episodes})
    except Exception as e:
        logger.error(f"trakt_get_calendar: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trakt/scrobble', methods=['POST'])
def trakt_scrobble():
    try:
        s = _trakt_settings()
        if not s["scrobble_enabled"]:
            return jsonify({"error": "Scrobble non abilitato nelle impostazioni Trakt"}), 403
        client = _trakt_load()
        if not client or not client.is_authenticated():
            return jsonify({"error": "Non autenticato su Trakt"}), 401

        data    = request.get_json(force=True) or {}
        name    = data.get("series_name", "")
        season  = int(data.get("season", 0))
        ep      = int(data.get("episode", 0))
        tmdb_id = int(data["tmdb_id"]) if data.get("tmdb_id") else None

        if not name or not season or not ep:
            return jsonify({"error": "series_name, season ed episode sono obbligatori"}), 400

        ok = client.scrobble_episode(name, tmdb_id, season, ep)
        if ok:
            from core.trakt import save_trakt_tokens
            save_trakt_tokens(client)
            return jsonify({"ok": True, "message": f"Segnato: {name} S{season:02d}E{ep:02d}"})
        return jsonify({"error": "Scrobble fallito (vedi log)"}), 502
    except Exception as e:
        logger.error(f"trakt_scrobble: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# END TRAKT INTEGRATION
# ============================================================================


if __name__ == '__main__':
    from waitress import serve
    import logging
    from core.config import Config

    logging.getLogger('waitress').setLevel(logging.ERROR)

    _cfg_port = Config()
    WEB_PORT = int(getattr(_cfg_port, 'web_port', None) or 5000)

    # Auto-sync dei file YAML delle traduzioni -> DB
    _sync_language_files()

    print(f"EXTTO Web Interface starting on http://localhost:{WEB_PORT}")
    serve(app, host='0.0.0.0', port=WEB_PORT, threads=4)
