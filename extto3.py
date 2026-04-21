#!/usr/bin/env python3
"""
EXTTO v29.0 - Entry point principale.

Il codice è ora organizzato nel package core/:
  core/constants.py   - costanti, logger, sanitize_magnet, date helpers
  core/models.py      - CycleStats, Quality, Parser, stats
  core/config.py      - Config
  core/notifier.py    - Notifier
  core/database.py    - Database, ArchiveDB, SmartCache
  core/engine.py      - Engine, rescore_archive
  core/clients/       - QbtClient, TransmissionClient, Aria2Client, LibtorrentClient
"""

import http.server
import json
import logging
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import core.config_db as config_db
from datetime import datetime, timezone
from typing import Tuple, Dict, Any
from urllib.parse import urlparse, parse_qsl

from core import (
    PORT, REFRESH, logger,
    sanitize_magnet, _extract_btih,
    Config, Notifier,
    Database, Engine,
    Parser, stats,
    QbtClient, TransmissionClient, Aria2Client, LibtorrentClient,
    rescore_archive,
)
from core.clients.amule import AmuleClient
from core.models import Quality
from core.tagger import Tagger, TAG_SERIES, TAG_FILM

def _extract_ed2k_hash(uri: str) -> str:
    """Estrae l'hash MD4 da un link ed2k://|file|nome|dim|HASH|/"""
    try:
        parts = uri.split('|')
        # formato: ed2k://|file|nome|dimensione|hash|/
        if len(parts) >= 5:
            return parts[4].lower().strip()
    except Exception:
        pass
    return ''
TAG_COMIC = 'Fumetto'  # Tag auto-assegnato ai download fumetti/weekly pack
from core.utils import safe_load_json, safe_save_json

LIMITS_FILE = 'torrent_limits.json'
TAGS_FILE = 'torrent_tags.json'

def _load_limits() -> dict:
    return safe_load_json(LIMITS_FILE)

def _save_limit(info_hash: str, dl_kbps: int, ul_kbps: int) -> None:
    limits = _load_limits()
    if dl_kbps == 0 and ul_kbps == 0:
        limits.pop(info_hash, None)
    else:
        limits[info_hash] = {'dl_kbps': dl_kbps, 'ul_kbps': ul_kbps}
    safe_save_json(LIMITS_FILE, limits)

def _remove_limit(info_hash: str) -> None:
    _save_limit(info_hash, 0, 0)

# --- NUOVA GESTIONE v44 VIA DATABASE ---

def _apply_saved_limits() -> None:
    """Carica i limiti di banda dal Database e li applica a libtorrent."""
    try:
        limits = config_db.get_all_torrent_limits()
        if not limits:
            return
        for ih, v in limits.items():
            dl_bytes = int(v.get('dl_bytes', -1))
            ul_bytes = int(v.get('ul_bytes', -1))
            LibtorrentClient.set_torrent_limits(ih, dl_bytes, ul_bytes)
    except Exception as e:
        logger.debug(f"Error applying saved limits from DB: {e}")

def _ui_tag(magnet: str, tag: str) -> None:
    """Scrive il tag nella tabella torrent_meta del database operativo."""
    m = re.search(r'btih:([a-fA-F0-9]{40})', magnet or '', re.I)
    if not m:
        return
    ih = m.group(1).lower()
    try:
        import sqlite3
        from core.constants import DB_FILE
        # Scrive direttamente nel DB dove extto_web si aspetta di trovare i tag
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            conn.execute("""
                INSERT INTO torrent_meta (hash, tag, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(hash) DO UPDATE SET tag=excluded.tag, updated_at=excluded.updated_at
            """, (ih, str(tag).strip()))
    except Exception as e:
        logger.debug(f"Error saving UI tag to DB: {e}")

def _convert_limits_to_bytes(qbt_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Passa il dizionario invariato: _apply_settings e check_speed_schedule
    convertono già internamente i limiti da KB/s a B/s con * 1024.
    La vecchia conversione qui causava una doppia moltiplicazione che rendeva
    il valore talmente grande da essere ignorato/clampato a 0 da libtorrent.
    """
    return dict(qbt_dict)

# ---------------------------------------------------------------------------
# WEB SERVER
# ---------------------------------------------------------------------------

server_thread = None

class ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

def web_task():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        # ------------------------------------------------------------------
        # GET
        # ------------------------------------------------------------------
        def do_GET(self):
            try:
                if self.path.startswith('/api/last_cycle'):
                    payload = {
                        'scraped':                stats.scraped,
                        'candidates':             stats.candidates_count,
                        'series_matched_count':   len(stats.series_matched),
                        'movies_matched_count':   len(stats.movies_matched),
                        'quality_rejected_count': len(stats.quality_rejected),
                        'blacklisted_count':      len(stats.blacklisted),
                        'size_rejected_count':    len(stats.size_rejected),
                        'duplicates_count':       len(stats.duplicates),
                        'downloads_started':      stats.downloads_started,
                        'gaps_filled':            stats.gaps_filled,
                        'errors':                 stats.errors,
                        'generated_at':           datetime.now(timezone.utc).isoformat(),
                    }
                    self._json_response(payload)
                    return

                if self.path.startswith('/api/last_cycles'):
                    try:
                        q     = urlparse(self.path).query
                        args  = dict(parse_qsl(q))
                        limit = int(args.get('limit', '10'))
                    except Exception:
                        limit = 10  # default se il parametro non è un intero
                    with Database() as _db: hist = _db.get_cycle_history(limit)
                    self._json_response({'history': hist})
                    return

                if self.path.startswith('/api/rescore_archive'):
                    cfg    = Config()
                    with Engine() as eng, Database() as db:
                        result = rescore_archive(cfg, eng, db)
                    self._json_response({'rescore': result})
                    return

                if self.path.startswith('/api/run_now'):
                    from urllib.parse import urlparse as _up, parse_qs as _pqs
                    _qs     = _pqs(_up(self.path).query)
                    _domain = (_qs.get('domain', ['all'])[0]).lower()  # all|series|movies|comics
                    _DOMAIN_MAP = {
                        'all':    '/tmp/extto_run_now',
                        'series': '/tmp/extto_run_series',
                        'movies': '/tmp/extto_run_movies',
                        'comics': '/tmp/extto_run_comics',
                    }
                    _tf = _DOMAIN_MAP.get(_domain, '/tmp/extto_run_now')
                    try:
                        open(_tf, 'w').close()
                        logger.info(f"⏭️  Run-now requested via API: domain='{_domain}'")
                        self._json_response({'ok': True, 'domain': _domain, 'trigger': _tf})
                    except Exception as _e:
                        self._json_response({'ok': False, 'error': str(_e)}, 500)
                    return

                if self.path.startswith('/api/torrents/stats'):
                    data = LibtorrentClient.global_stats()
                    data['available'] = LibtorrentClient.session_available()
                    # Aggiunge la versione di libtorrent per la Web UI
                    try:
                        import libtorrent as lt
                        data['lt_version'] = lt.version
                    except ImportError:
                        data['lt_version'] = 'Sconosciuta'

                    # Se libtorrent non è in ascolto ma aria2 è attivo e risponde,
                    # segna is_listening=True per evitare il badge "offline" in UI.
                    if not data.get('is_listening'):
                        try:
                            from core.config import Config as _CfgA2
                            _cfg_a2 = _CfgA2()
                            _a2_enabled = str(_cfg_a2.qbt.get('aria2_enabled', 'no')).lower() in ('yes', 'true', '1')
                            if _a2_enabled:
                                from core.clients.aria2 import Aria2Client as _A2C
                                _a2 = _A2C(_cfg_a2.qbt)
                                if _a2.rpc_url:
                                    import requests as _req
                                    _pong = _req.post(_a2.rpc_url, json={
                                        'jsonrpc': '2.0', 'id': 'ping', 'method': 'aria2.getVersion',
                                        'params': [f'token:{_a2.rpc_secret}'] if _a2.rpc_secret else []
                                    }, timeout=2)
                                    if _pong.status_code == 200 and 'result' in _pong.json():
                                        ver = _pong.json()['result'].get('version', '?')
                                        data['is_listening'] = True
                                        data['available']    = True   # sblocca toolbar UI
                                        data['listen_port']  = 6800
                                        data['lt_version']   = f'aria2c {ver}'
                                        # Velocità aggregate da aria2.getGlobalStat
                                        try:
                                            _gs = _req.post(_a2.rpc_url, json={
                                                'jsonrpc': '2.0', 'id': 'gstat',
                                                'method': 'aria2.getGlobalStat',
                                                'params': [f'token:{_a2.rpc_secret}'] if _a2.rpc_secret else []
                                            }, timeout=2).json().get('result', {})
                                            data['dl_rate']  = int(_gs.get('downloadSpeed', 0))
                                            data['ul_rate']  = int(_gs.get('uploadSpeed', 0))
                                            data['active']   = int(_gs.get('numActive', 0))
                                            data['paused']   = int(_gs.get('numWaiting', 0))
                                        except Exception:
                                            pass
                        except Exception:
                            pass

                    self._json_response(data)
                    return

                # --- AMULE: lista download ★ v45 ---
                if self.path.startswith('/api/amule/downloads'):
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            with AmuleClient(_cfg_am.qbt) as _am:
                                self._json_response({'downloads': _am.get_download_queue()})
                        else:
                            self._json_response({'downloads': []})
                    except Exception as e:
                        self._json_response({'downloads': [], 'error': str(e)}, 500)
                    return

                # --- AMULE: file condivisi e cartelle ★ v50 ---
                if self.path == '/api/amule/shared/dirs':
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            dirs = _am.get_shared_dirs()
                        self._json_response({'dirs': dirs})
                    except Exception as e:
                        self._json_response({'dirs': [], 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/port-check':
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            result = _am.check_ports()
                        self._json_response(result)
                    except Exception as e:
                        self._json_response({'error': str(e)}, 500)
                    return

                if self.path.startswith('/api/amule/shared'):
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            with AmuleClient(_cfg_am.qbt) as _am:
                                self._json_response({'shared': _am.get_shared_files()})
                        else:
                            self._json_response({'shared': []})
                    except Exception as e:
                        self._json_response({'shared': [], 'error': str(e)}, 500)
                    return

                # --- AMULE: upload attivi ★ v45 ---
                if self.path.startswith('/api/amule/uploads'):
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            with AmuleClient(_cfg_am.qbt) as _am:
                                self._json_response({'uploads': _am.get_upload_queue()})
                        else:
                            self._json_response({'uploads': []})
                    except Exception as e:
                        self._json_response({'uploads': [], 'error': str(e)}, 500)
                    return

                # --- AMULE: stato connessione ed2k/Kad ★ v45 ---
                if self.path.startswith('/api/amule/status'):
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            with AmuleClient(_cfg_am.qbt) as _am:
                                self._json_response(_am.get_status())
                        else:
                            self._json_response({'error': 'aMule non abilitato'}, 400)
                    except Exception as e:
                        self._json_response({'error': str(e)}, 500)
                    return

                # --- AMULE: all-in-one consolidato (status+dl+ul) ★ v50 ---
                if self.path == '/api/amule/all':
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() not in ('yes', 'true', '1'):
                            self._json_response({'error': 'aMule non abilitato'}, 400)
                            return
                        with AmuleClient(_cfg_am.qbt) as _am:
                            status    = _am.get_status()
                            downloads = _am.get_download_queue()
                            uploads   = _am.get_upload_queue()
                        self._json_response({
                            'status':    status,
                            'downloads': downloads,
                            'uploads':   uploads,
                        })
                    except Exception as e:
                        self._json_response({'error': str(e)}, 500)
                    return

                # --- AMULE: lista server ed2k ★ v45 ---
                if self.path.startswith('/api/amule/servers'):
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            with AmuleClient(_cfg_am.qbt) as _am:
                                self._json_response({'servers': _am.get_server_list()})
                        else:
                            self._json_response({'error': 'aMule non abilitato'}, 400)
                    except Exception as e:
                        self._json_response({'error': str(e)}, 500)
                    return

                if self.path == '/api/torrents' or self.path == '/api/torrents/':
                    torrents = LibtorrentClient.list_torrents()
                    # --- INIEZIONE DOWNLOAD HTTP FUMETTI ---
                    try:
                        from core.comics import ACTIVE_HTTP_DOWNLOADS
                        if ACTIVE_HTTP_DOWNLOADS:
                            torrents.extend(list(ACTIVE_HTTP_DOWNLOADS.values()))
                    except Exception as e:
                        logger.debug(f"list_torrents HTTP downloads: {e}")
                        pass
                    # ---------------------------------------
                    # --- INIEZIONE ARIA2 TRASPARENTE ---
                    try:
                        from core.config import Config
                        cfg_a2 = Config()
                        if str(cfg_a2.qbt.get('aria2_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            from core.clients.aria2 import Aria2Client
                            torrents.extend(Aria2Client(cfg_a2.qbt).list_torrents())
                    except Exception as e:
                        logger.debug(f"list_torrents Aria2: {e}")
                        pass
                    # ---------------------------------------
                    # --- INIEZIONE AMULE TRASPARENTE ★ v45 ---
                    try:
                        from core.config import Config as _CfgAm
                        _cfg_am = _CfgAm()
                        if str(_cfg_am.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            with AmuleClient(_cfg_am.qbt) as _am:
                                torrents.extend(_am.list_torrents())
                    except Exception as e:
                        logger.debug(f"list_torrents aMule: {e}")
                        pass
                    # ------------------------------------------
                    self._json_response({'torrents': torrents})
                    return

                if self.path.startswith('/metrics'):
                    lines = [
                        '# HELP extto_scraped_total Items scraped per source in current cycle',
                        '# TYPE extto_scraped_total gauge',
                    ]
                    for src, val in stats.scraped.items():
                        lines.append(f'extto_scraped_total{{source="{src}"}} {val}')
                    lines += [
                        '# HELP extto_cycle_candidates_total Candidates seen in current cycle',
                        '# TYPE extto_cycle_candidates_total gauge',
                        f'extto_cycle_candidates_total {stats.candidates_count}',
                        '# HELP extto_downloads_started_total Downloads started in current cycle',
                        '# TYPE extto_downloads_started_total gauge',
                        f'extto_downloads_started_total {stats.downloads_started}',
                        '# HELP extto_gaps_filled_total Gaps filled in current cycle',
                        '# TYPE extto_gaps_filled_total gauge',
                        f'extto_gaps_filled_total {stats.gaps_filled}',
                        '# HELP extto_rejected_total Items rejected by reason in current cycle',
                        '# TYPE extto_rejected_total gauge',
                        f'extto_rejected_total{{reason="below_quality"}} {len(stats.quality_rejected)}',
                        f'extto_rejected_total{{reason="size_too_small"}} {len(stats.size_rejected)}',
                        f'extto_rejected_total{{reason="duplicate"}} {len(stats.duplicates)}',
                        f'extto_rejected_total{{reason="blacklisted"}} {len(stats.blacklisted)}',
                        '# HELP extto_errors_total Errors during current cycle',
                        '# TYPE extto_errors_total gauge',
                        f'extto_errors_total {stats.errors}',
                        '# HELP extto_blacklisted_total Blacklisted items in current cycle',
                        '# TYPE extto_blacklisted_total gauge',
                        f'extto_blacklisted_total {len(stats.blacklisted)}',
                        '# HELP extto_quality_rejected_total Quality-rejected items in current cycle',
                        '# TYPE extto_quality_rejected_total gauge',
                        f'extto_quality_rejected_total {len(stats.quality_rejected)}',
                    ]
                    body = ('\n'.join(lines) + '\n').encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                return http.server.SimpleHTTPRequestHandler.do_GET(self)
            except Exception as e:
                logger.debug(f"Web GET error: {e}")
                try:
                    self._json_response({'error': str(e)}, 500)
                except Exception:
                    pass

        # ------------------------------------------------------------------
        # OPTIONS / POST helpers
        # ------------------------------------------------------------------
        def _read_json_body(self):
            length = int(self.headers.get('Content-Length', 0))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode('utf-8'))

        def _cors_headers(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors_headers()
            self.end_headers()

        def _json_response(self, data: dict, status: int = 200):
            body = json.dumps(data).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        # ------------------------------------------------------------------
        # POST
        # ------------------------------------------------------------------
        def do_POST(self):
            try:
                if self.path == '/api/torrents/add':
                    payload   = self._read_json_body()
                    magnet    = payload.get('magnet', '').strip()
                    save_path = payload.get('save_path', '').strip()
                    if not magnet:
                        self._json_response({'ok': False, 'error': 'magnet mancante'}, 400)
                        return
                    ok = LibtorrentClient.add_magnet(magnet, save_path)
                    self._json_response({'ok': ok})
                    return

                if self.path == '/api/torrents/add_torrent_file':
                    import base64 as _b64
                    payload   = self._read_json_body()
                    file_data = payload.get('data', '').strip()
                    if not file_data:
                        self._json_response({'ok': False, 'error': 'data mancante'}, 400)
                        return
                    try:
                        torrent_bytes = _b64.b64decode(file_data)
                        lt = LibtorrentClient._lt
                        s  = LibtorrentClient._session
                        if lt is None or s is None:
                            self._json_response({'ok': False, 'error': 'sessione non disponibile'}, 503)
                            return
                        cfg       = LibtorrentClient._cfg_snapshot
                        final_dir = cfg.get('libtorrent_dir', '/downloads').strip()
                        temp_dir  = cfg.get('libtorrent_temp_dir', '').strip()
                        
                        target = payload.get('save_path', '').strip()
                        _norm = os.path.normpath
                        _is_default = (
                            not target or
                            _norm(target) == _norm(final_dir) or
                            (temp_dir and _norm(target) == _norm(temp_dir))
                        )
                        
                        if _is_default:
                            # Usa la logica intelligente (RAM Disk -> Temp -> Final)
                            save_path = LibtorrentClient._resolve_initial_save_path(cfg)
                            
                            # Disabilita prealloca temporaneamente se il target è il RAM disk
                            _going_to_ramdisk = LibtorrentClient._ramdisk_enabled(cfg) and save_path == cfg.get('libtorrent_ramdisk_dir', '').strip()
                            _global_preallocate = str(cfg.get('libtorrent_preallocate', 'no')).lower() in ('yes', 'true', '1')
                            if _going_to_ramdisk and _global_preallocate:
                                LibtorrentClient._set_preallocate(False)
                        else:
                            save_path = target
                            _going_to_ramdisk = False
                            _global_preallocate = False
                        
                        ti        = lt.torrent_info(lt.bdecode(torrent_bytes))
                        params    = lt.add_torrent_params()
                        params.ti = ti
                        params.save_path = save_path
                        s.add_torrent(params)
                        
                        # Ripristina la prealloca se era stata disabilitata
                        if _going_to_ramdisk and _global_preallocate:
                            LibtorrentClient._set_preallocate(True)

                        self._json_response({'ok': True})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return
                    
                if self.path == '/api/torrents/add_http':
                    payload = self._read_json_body()
                    try:
                        from core.comics import download_comic_file_bg
                        ok = download_comic_file_bg(payload.get('url', ''), payload.get('target_dir', ''), payload.get('title', ''))
                        self._json_response({'ok': ok})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)})
                    return    

                if self.path == '/api/torrents/pause':
                    payload = self._read_json_body()
                    hash_val = payload.get('hash', '').strip()
                    # --- ROUTING ARIA2 ---
                    if len(hash_val) == 16:
                        from core.config import Config
                        from core.clients.aria2 import Aria2Client
                        ok = Aria2Client(Config().qbt).pause_torrent(hash_val)
                    else:
                        ok = LibtorrentClient.pause_torrent(hash_val)
                    self._json_response({'ok': ok})
                    return

                if self.path == '/api/torrents/resume':
                    payload = self._read_json_body()
                    hash_val = payload.get('hash', '').strip()
                    # --- ROUTING ARIA2 ---
                    if len(hash_val) == 16:
                        from core.config import Config
                        from core.clients.aria2 import Aria2Client
                        ok = Aria2Client(Config().qbt).resume_torrent(hash_val)
                    else:
                        ok = LibtorrentClient.resume_torrent(hash_val)
                    self._json_response({'ok': ok})
                    return

                if self.path == '/api/torrents/recheck':
                    payload = self._read_json_body()
                    ok = LibtorrentClient.recheck_torrent(payload.get('hash', '').strip())
                    self._json_response({'ok': ok})
                    return

                if self.path == '/api/torrents/remove_completed':
                    # Rimuove tutti i torrent con progress=100 (seeding) o stato 'finished' senza cancellare i file
                    removed = []
                    
                    # ---> NUOVO: PULIZIA DOWNLOAD HTTP <---
                    try:
                        from core.comics import ACTIVE_HTTP_DOWNLOADS
                        to_delete = []
                        for k, v in ACTIVE_HTTP_DOWNLOADS.items():
                            if v.get('progress', 0) >= 1.0 or v.get('state') == 'Errore':
                                to_delete.append(k)
                                removed.append(v.get('name'))
                        for k in to_delete:
                            del ACTIVE_HTTP_DOWNLOADS[k]
                    except Exception: pass
                    # --------------------------------------

                    # --- PULIZIA ARIA2 TRASPARENTE ---
                    try:
                        from core.config import Config
                        cfg_a2 = Config()
                        if str(cfg_a2.qbt.get('aria2_enabled', 'no')).lower() in ('yes', 'true', '1'):
                            from core.clients.aria2 import Aria2Client
                            a2 = Aria2Client(cfg_a2.qbt)
                            for t in a2.list_torrents():
                                if t.get('progress', 0) >= 1.0 or t.get('state') in ('finished', 'rimosso', 'error'):
                                    a2.remove_torrent(t['hash'])
                                    removed.append(t['name'])
                    except Exception: pass
                    # ---------------------------------

                    for t in LibtorrentClient.list_torrents():
                        st = str(t.get('state', '')).lower()
                        pr = float(t.get('progress', 0))
                        is_paused = bool(t.get('paused', False))
                        
                        physical_found = t.get('physical_file_found', False)
                        is_finished_state = st in ('finished', 'finished_t', 'completato', 'finito', 'salvato')
                        can_remove = (pr >= 1.0 or is_finished_state or physical_found)
                        
                        if can_remove:
                        # ---> INIZIO PROTEZIONE SEED INFINITO <---
	                        try:
	                            t_details = LibtorrentClient.get_torrent_details(t['hash'])
	                            if t_details:
	                                my_ratio = t_details.get('seed_ratio', -1)
	                                my_days = t_details.get('seed_days', -1)
	                                if my_ratio == 0 or my_days == 0:
	                                    continue # Il torrent è infinito, salta la pulizia!
	                        except Exception:
	                            pass
                        # ---> FINE PROTEZIONE SEED INFINITO <---

                        if is_paused and not (is_finished_state or physical_found):
                            continue

                        # ---> SALVA DIMENSIONE PRIMA DI PULIRE <---
                            try:
                                total_b = t.get('total_size', 0)
                                t_hash = t.get('hash', '')
                                if total_b > 0 and t_hash:
                                    c_sz = db.conn.cursor()
                                    c_sz.execute("UPDATE episodes SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (total_b, t_hash))
                                    c_sz.execute("UPDATE movies SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (total_b, t_hash))
                                    db.conn.commit()
                            except Exception: pass
                            # ------------------------------------------

                            LibtorrentClient.remove_torrent(t['hash'], delete_files=False)
                            removed.append(t['name'])

                    # ---> PULIZIA FILE .torrent ORFANI DA extto_torrents_state/ <---
                    # Rimuove .torrent senza .fastresume corrispondente (orfani)
                    try:
                        from core.constants import STATE_DIR as _STATE_DIR
                        if _STATE_DIR and os.path.isdir(_STATE_DIR):
                            active_hashes = {str(t2.get('hash', '')).lower() for t2 in LibtorrentClient.list_torrents()}
                            for _fname in os.listdir(_STATE_DIR):
                                if not _fname.endswith('.torrent'):
                                    continue
                                _ih = _fname[:-8].lower()
                                if _ih in active_hashes:
                                    continue
                                _fr = os.path.join(_STATE_DIR, f"{_ih}.fastresume")
                                if not os.path.exists(_fr):
                                    try:
                                        os.remove(os.path.join(_STATE_DIR, _fname))
                                        logger.debug(f"Rimosso .torrent orfano: {_fname}")
                                    except Exception as _oe:
                                        logger.debug(f"Errore rimozione .torrent orfano {_fname}: {_oe}")
                    except Exception as _se:
                        logger.debug(f"Pulizia .torrent orfani: {_se}")
                    # ---------------------------------------------------------------

                    self._json_response({'success': True, 'removed': removed, 'count': len(removed)})
                    return

                if self.path == '/api/torrents/remove':
                    payload = self._read_json_body()
                    hash_val = payload.get('hash', '').strip()
                    
                    # ---> NUOVO: RIMOZIONE SINGOLA HTTP <---
                    if hash_val.startswith('http_'):
                        try:
                            from core.comics import ACTIVE_HTTP_DOWNLOADS
                            if hash_val in ACTIVE_HTTP_DOWNLOADS:
                                del ACTIVE_HTTP_DOWNLOADS[hash_val]
                                self._json_response({'ok': True})
                                return
                        except Exception: pass
                    # ---------------------------------------
                    
                    # --- ROUTING ARIA2 RIMOZIONE SINGOLA ---
                    elif len(hash_val) == 16:
                        try:
                            from core.config import Config
                            from core.clients.aria2 import Aria2Client
                            ok = Aria2Client(Config().qbt).remove_torrent(hash_val, bool(payload.get('delete_files', False)))
                            self._json_response({'ok': ok})
                            return
                        except Exception: pass
                    # ---------------------------------------

                    # ---> SALVA DIMENSIONE PRIMA DI RIMUOVERE SINGOLARMENTE <---
                    try:
                        t_details = LibtorrentClient.get_torrent_details(hash_val)
                        if t_details and t_details.get('total_size', 0) > 0:
                            c_sz = db.conn.cursor()
                            c_sz.execute("UPDATE episodes SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (t_details['total_size'], hash_val))
                            c_sz.execute("UPDATE movies SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (t_details['total_size'], hash_val))
                            db.conn.commit()
                    except Exception: pass
                    # -----------------------------------------------------------

                    ok = LibtorrentClient.remove_torrent(
                        hash_val,
                        bool(payload.get('delete_files', False))
                    )
                    if ok:
                        _config_db.delete_torrent_limit(hash_val)
                    self._json_response({'ok': ok})
                    return
                    
                if self.path == '/api/torrents/details':
                    payload = self._read_json_body()
                    info_hash = payload.get('hash', '').strip()
                    # --- DETTAGLI FITTIZI PER ARIA2 ---
                    if len(info_hash) == 16:
                        self._json_response({'hash': info_hash, 'name': 'Download Aria2', 'state': 'Aria2', 'progress': 0, 'total_size': 0})
                        return
                    data = LibtorrentClient.get_torrent_details(info_hash)
                    self._json_response(data)
                    return    

                if self.path == '/api/torrents/ipfilter_update':
                    payload = self._read_json_body()
                    url     = payload.get('url', '').strip()
                    if not url:
                        self._json_response({'ok': False, 'error': 'URL mancante'}, 400)
                        return
                    result = LibtorrentClient.update_ipfilter_from_url(url)
                    self._json_response(result)
                    return

                if self.path == '/api/torrents/ipfilter_status':
                    self._json_response(LibtorrentClient.ipfilter_status())
                    return

                if self.path == '/api/torrents/apply_settings':
                    payload = self._read_json_body()
                    # Se i limiti arrivano direttamente nel payload, applica senza rileggere il DB
                    if 'dl_kbps' in payload or 'ul_kbps' in payload:
                        dl_bytes = int(payload.get('dl_kbps', 0)) * 1024
                        ul_bytes = int(payload.get('ul_kbps', 0)) * 1024
                        LibtorrentClient.apply_speed_limits(dl_bytes, ul_bytes)
                        self._json_response({'ok': True})
                        return
                    # Altrimenti rilegge tutto dal DB (comportamento originale)
                    cfg_live = Config()
                    qbt_bytes = _convert_limits_to_bytes(cfg_live.qbt)
                    if str(qbt_bytes.get('libtorrent_enabled', 'no')).lower() in ('yes', 'true', '1'):
                        LibtorrentClient._ensure_session(qbt_bytes)
                    LibtorrentClient._apply_settings(qbt_bytes)
                    LibtorrentClient.check_seeding_limits()
                    self._json_response({'ok': True})
                    return

                if self.path == '/api/torrents/set_limits':
                    payload   = self._read_json_body()
                    info_hash = payload.get('hash', '').strip()
                    dl_kbps = int(payload.get('dl_kbps', 0))
                    ul_kbps = int(payload.get('ul_kbps', 0))
                    seed_ratio = float(payload.get('seed_ratio', -1))
                    seed_days  = float(payload.get('seed_days', -1))
                    
                    dl_bytes = dl_kbps * 1024 if dl_kbps > 0 else -1
                    ul_bytes = ul_kbps * 1024 if ul_kbps > 0 else -1
                    
                    ok = LibtorrentClient.set_torrent_limits(info_hash, dl_bytes, ul_bytes, seed_ratio, seed_days)
                    self._json_response({'ok': ok})
                    return

                if self.path == '/api/torrents/peers':
                    payload   = self._read_json_body()
                    info_hash = payload.get('hash', '').strip()
                    data = LibtorrentClient.get_peers(info_hash)
                    self._json_response(data)
                    return

                # --- AMULE POST ENDPOINTS ★ v45 ---

                if self.path == '/api/amule/add':
                    payload = self._read_json_body()
                    link = payload.get('link', '').strip()
                    if not link.startswith('ed2k://'):
                        self._json_response({'ok': False, 'error': 'Link non ed2k'}, 400)
                        return
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            ok = _am.add(link)
                        self._json_response({'ok': ok})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/server/connect':
                    payload = self._read_json_body()
                    ip   = payload.get('ip', '').strip()
                    port = int(payload.get('port', 0))
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            ok = _am.connect_server(ip, port)
                        self._json_response({'ok': ok})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/server/update':
                    payload = self._read_json_body()
                    url = payload.get('url', '').strip()
                    if not url:
                        self._json_response({'ok': False, 'error': 'URL mancante'}, 400)
                        return
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            # Passa l'URL direttamente ad aMule — amuled lo scarica da solo
                            ok = _am.add_server(url)
                        self._json_response({'ok': ok, 'message': f'Richiesta inviata ad aMule: {url}'})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/search':
                    payload = self._read_json_body()
                    query   = payload.get('query', '').strip()
                    network = payload.get('network', 'global')
                    ext     = payload.get('extension', '')
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            results = _am.search(query, network, ext)
                        self._json_response({'results': results, 'count': len(results)})
                    except Exception as e:
                        self._json_response({'results': [], 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/pause':
                    payload = self._read_json_body()
                    file_hash = payload.get('hash', '').strip()
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            ok = _am.pause_download(file_hash)
                        self._json_response({'ok': ok})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/resume':
                    payload = self._read_json_body()
                    file_hash = payload.get('hash', '').strip()
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            ok = _am.resume_download(file_hash)
                        self._json_response({'ok': ok})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/cancel':
                    payload = self._read_json_body()
                    file_hash = payload.get('hash', '').strip()
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            ok = _am.cancel_download(file_hash)
                        self._json_response({'ok': ok})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/shared/reload':
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            ok = _am.reload_shared()
                        self._json_response({'ok': ok})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/shared/add':
                    payload = self._read_json_body()
                    path = payload.get('path', '').strip()
                    if not path:
                        self._json_response({'ok': False, 'error': 'Percorso mancante'}, 400)
                        return
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            _am.add_shared_dir(path)
                        self._json_response({'ok': True, 'message': f'Cartella aggiunta: {path}'})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/shared/remove':
                    payload = self._read_json_body()
                    path = payload.get('path', '').strip()
                    if not path:
                        self._json_response({'ok': False, 'error': 'Percorso mancante'}, 400)
                        return
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            _am.remove_shared_dir(path)
                        self._json_response({'ok': True})
                    except Exception as e:
                        self._json_response({'ok': False, 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/shared/dirs':
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            dirs = _am.get_shared_dirs()
                        self._json_response({'dirs': dirs})
                    except Exception as e:
                        self._json_response({'dirs': [], 'error': str(e)}, 500)
                    return

                if self.path == '/api/amule/port-check':
                    try:
                        from core.config import Config as _Cfg
                        _cfg_am = _Cfg()
                        with AmuleClient(_cfg_am.qbt) as _am:
                            result = _am.check_ports()
                        self._json_response(result)
                    except Exception as e:
                        self._json_response({'error': str(e)}, 500)
                    return

                # ----------------------------------

                self._json_response({'error': 'Not found'}, 404)
            except Exception as e:
                try:
                    self._json_response({'error': str(e)}, 500)
                except Exception:
                    pass

    try:
        # Legge la porta dal config, se fallisce usa il salvagente PORT di constants
        from core.config import Config
        try:
            cfg_engine = Config()
            active_port = int(cfg_engine.qbt.get('engine_port', PORT))
        except Exception as e:
            logger.debug(f"engine_port read: {e}")
            active_port = PORT

        for _ in range(5):
            try:
                with ReusableServer(("", active_port), Quiet) as d:
                    d.serve_forever()
                break
            except OSError:
                time.sleep(5)
    except Exception as e:
        logger.error(f"❌ Server: {e}")


def start_server_watchdog():
    global server_thread
    if server_thread is None or not server_thread.is_alive():
        logger.info(f"🔧 Web Server ({PORT})")
        server_thread = threading.Thread(target=web_task, daemon=True)
        server_thread.start()


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    # --- INIZIO MODIFICA: SALVATAGGIO RATIO ALLO SPEGNIMENTO ---
    import signal
    import sys
    import time
    def spegnimento_sicuro(sig, frame):
        logger.info("🛑 Restart requested. Saving upload state...")
        try:
            LibtorrentClient.shutdown()
            time.sleep(3) # Diamo 3 secondi al disco per scrivere i file in modo sicuro
        except Exception as e:
            logger.warning(f"LibtorrentClient.shutdown: {e}")
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, spegnimento_sicuro)
    signal.signal(signal.SIGINT, spegnimento_sicuro)
    # --- FINE MODIFICA ---

    # --- NUOVO: CATTURA ERRORI PER IL RIASSUNTO FINALE ---
    import logging
    class ErrorCaptureHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.captured_errors = []

        def emit(self, record):
            msg = record.getMessage()
            # Cattura gli ERROR e i WARNING che contengono parole chiave critiche
            if record.levelno >= logging.ERROR or (record.levelno == logging.WARNING and any(x in msg.lower() for x in ['error', 'fallit', 'timeout'])):
                t_str = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
                self.captured_errors.append(f"[{t_str}] {msg}")

    error_catcher = ErrorCaptureHandler()
    logger.addHandler(error_catcher)
    # -----------------------------------------------------

    logger.info("🚀 EXTTO v29.0 Started")

    # Avvia extto_web.py in background
    try:
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extto_web.py')
        if os.path.exists(script_path):
            subprocess.Popen([sys.executable, script_path], cwd=os.path.dirname(script_path))
            logger.info("🌐 Web UI started in background")
        else:
            logger.warning("⚠️  extto_web.py not found, web interface not started.")
    except Exception as e:
        logger.error(f"❌ Web UI startup error: {e}")

    if '--check-listser' in sys.argv:
        logger.info("Listener check disabled on request. No operations performed.")
        return

    eng = Engine()
    db  = Database()

    # Recupera torrent bloccati in stato 'downloading' da un ciclo/riavvio precedente
    stale = db.reset_stale_downloading()
    if stale:
        logger.info(f"♻️  Recovered {stale} downloads stuck in 'downloading' state → moved back to pending")

    # Applica log_level da extto.conf (se configurato)
    try:
        from core.constants import set_log_level as _set_ll
        _ll_val = str(Config().qbt.get('log_level', '') or '').strip().lower()
        if _ll_val:
            _set_ll(_ll_val)
            logger.info(f"🔧 Log level: {_ll_val.upper()}")
    except Exception as e:
        logger.debug(f"set_log_level boot: {e}")
        pass

    # Pre-inizializzazione libtorrent
    try:
        _early_cfg        = Config()
        _lt_enabled_flag  = str(_early_cfg.qbt.get('libtorrent_enabled', 'no')).lower() in ('yes', 'true', '1')
        if _lt_enabled_flag:
            LibtorrentClient(_convert_limits_to_bytes(_early_cfg.qbt))
            logger.info("✅ LibtorrentClient pre-initialized at startup")
            _apply_saved_limits()
            
            # --- Caricamento Blocklist all'avvio (in background) ---
            ipfilter_url = _early_cfg.qbt.get('libtorrent_ipfilter_url', '').strip()
            if ipfilter_url:
                logger.info("🛡️ IP blocklist download started in background...")
                def _load_blocklist():
                    res = LibtorrentClient.update_ipfilter_from_url(ipfilter_url)
                    if res.get('ok'):
                        rules = res.get('rules_count') or 0
                        logger.info(f"✅ Blocklist applied: {rules:,} rules loaded.")
                    else:
                        logger.error(f"❌ Blocklist load error at startup: {res.get('error')}")
                threading.Thread(target=_load_blocklist, daemon=True).start()
            # -------------------------------------------------------
            
    except Exception as _e:
        logger.error(f"❌ LibtorrentClient pre-init error: {_e}")

    TRIGGER_FILE        = '/tmp/extto_run_now'
    TRIGGER_SERIES      = '/tmp/extto_run_series'
    TRIGGER_MOVIES      = '/tmp/extto_run_movies'
    TRIGGER_COMICS      = '/tmp/extto_run_comics'
    last_deep_gap_fill  = 0  # <--- Inizializza il timer a 0 così parte subito al primo ciclo
    last_comics_check   = 0  # <--- Fumetti: 0 = controlla subito al primo ciclo
    last_completion_check = 0  # <--- AGGIUNGI QUESTA
    last_disk_alert     = 0  # <--- Timer Spazio Disco
    last_load_alert     = 0  # <--- Timer Sovraccarico CPU
    _first_cycle        = True  # Flag primo ciclo: forza fumetti subito

    # --- Registro notifiche post-processing persistente su DB ---
    # Sopravvive ai riavvii: se la notifica è stata inviata non viene mai ripetuta.
    def _pp_is_notified(hash_str: str) -> bool:
        """Controlla se il torrent hash è già stato notificato."""
        try:
            import sqlite3 as _sq3
            from core.constants import DB_FILE as _DBF
            with _sq3.connect(_DBF, timeout=5) as _c:
                try:
                    _c.execute("ALTER TABLE torrent_meta ADD COLUMN pp_notified INTEGER DEFAULT 0")
                except Exception:
                    pass  # Colonna già esistente
                row = _c.execute(
                    "SELECT pp_notified FROM torrent_meta WHERE hash=?", (hash_str,)
                ).fetchone()
                return bool(row and row[0])
        except Exception:
            return False

    def _pp_mark_notified(hash_str: str) -> None:
        """Segna il torrent hash come già notificato nel DB."""
        try:
            import sqlite3 as _sq3
            from core.constants import DB_FILE as _DBF
            with _sq3.connect(_DBF, timeout=5) as _c:
                _c.execute("""
                    INSERT INTO torrent_meta (hash, pp_notified, updated_at)
                    VALUES (?, 1, strftime('%s','now'))
                    ON CONFLICT(hash) DO UPDATE SET
                        pp_notified=1, updated_at=excluded.updated_at
                """, (hash_str,))
        except Exception as _e:
            logger.debug(f"_pp_mark_notified: {_e}")

    def _pp_get_no_rename(hash_str: str) -> bool:
        """Restituisce True se il torrent ha il flag no_rename nel DB."""
        if not hash_str:
            return False
        try:
            import sqlite3 as _sq3
            from core.constants import DB_FILE as _DBF
            with _sq3.connect(_DBF, timeout=5) as _c:
                try:
                    _c.execute("ALTER TABLE torrent_meta ADD COLUMN no_rename INTEGER DEFAULT 0")
                except Exception:
                    pass  # Colonna già esistente
                row = _c.execute(
                    "SELECT no_rename FROM torrent_meta WHERE hash=?", (hash_str.lower(),)
                ).fetchone()
                return bool(row and row[0])
        except Exception:
            return False

    while True:
        error_catcher.captured_errors.clear() # Svuota la memoria errori del ciclo precedente
        start_server_watchdog()
        stats.reset()
        logger.info(f"\n🔄 CYCLE {datetime.now().strftime('%H:%M')}")

        run_now_triggered    = os.path.exists(TRIGGER_FILE)
        run_series_triggered = os.path.exists(TRIGGER_SERIES)
        run_movies_triggered = os.path.exists(TRIGGER_MOVIES)
        run_comics_triggered = os.path.exists(TRIGGER_COMICS)
        # run_now forza anche tutti i sotto-domini
        if run_now_triggered:
            run_series_triggered = run_movies_triggered = run_comics_triggered = True

        # Primo ciclo: forza fumetti subito (non aspettare il timer settimanale)
        if _first_cycle:
            run_comics_triggered = True
            _first_cycle = False

        # Log modalità ciclo
        if run_now_triggered:
            logger.info("🔄 Mode: FULL CYCLE (run_now)")
        elif run_series_triggered and not run_movies_triggered and not run_comics_triggered:
            logger.info("📺 Mode: TV SERIES only")
        elif run_movies_triggered and not run_series_triggered and not run_comics_triggered:
            logger.info("🎬 Mode: MOVIES only")
        elif run_comics_triggered and not run_series_triggered and not run_movies_triggered:
            logger.info("📚 Mode: COMICS only")
        # Cicli dominio-specifici: un solo trigger attivo → salta tutto il resto
        comics_only_cycle = (run_comics_triggered and
                             not run_now_triggered and
                             not run_series_triggered and
                             not run_movies_triggered)
        series_only_cycle = (run_series_triggered and
                             not run_now_triggered and
                             not run_comics_triggered and
                             not run_movies_triggered)
        movies_only_cycle = (run_movies_triggered and
                             not run_now_triggered and
                             not run_series_triggered and
                             not run_comics_triggered)
        cfg               = Config()

        if LibtorrentClient.session_available():
            LibtorrentClient._cfg_snapshot = _convert_limits_to_bytes(cfg.qbt)
            LibtorrentClient.check_speed_schedule()

        if not cfg.urls:
            logger.warning("⚠️  No URLs configured")
            time.sleep(60)
            continue

        db.sync_configs(cfg.series, cfg.movies)

        eng.age_filter = {
            'days':      max(0, int(getattr(cfg, 'max_age_days', 0) or 0)),
            'threshold': float(getattr(cfg, 'stop_on_old_page_threshold', 0.8) or 0.8)
        }
        if eng.age_filter['days'] > 0:
            logger.info(f"⏱️  Age filter active: last {eng.age_filter['days']} days "
                        f"(stop pagina a {int(eng.age_filter['threshold']*100)}%)")

        # Client selection
        qbt          = QbtClient(cfg.qbt)
        transmission = TransmissionClient(cfg.qbt)
        aria2        = Aria2Client(cfg.qbt)

        # Tagger — funziona solo con qBittorrent; no-op su altri client
        tagger = Tagger(qbt)
        tagger.ensure_tags()

        libtorrent_enabled_flag = str(cfg.qbt.get('libtorrent_enabled', 'no')).lower() in ('yes', 'true', '1')
        libt = None
        if libtorrent_enabled_flag:
            try:
                # Use LibtorrentClient directly as it is already imported at module level
                libt = LibtorrentClient(_convert_limits_to_bytes(cfg.qbt))
            except Exception as e:
                logger.error(f"❌ LibtorrentClient init fallita: {e}")

        aria2_enabled_flag = str(cfg.qbt.get('aria2_enabled', 'no')).lower() in ('yes', 'true', '1')

        # --- aMule client (gestisce link ed2k://) ★ v45 ---
        amule_enabled_flag = str(cfg.qbt.get('amule_enabled', 'no')).lower() in ('yes', 'true', '1')
        amule_cl = None
        if amule_enabled_flag:
            try:
                amule_cl = AmuleClient(cfg.qbt)
                logger.info("✅ aMule (ed2k) configurato")
            except Exception as _e:
                logger.error(f"❌ AmuleClient init fallita: {_e}")
        # ---------------------------------------------------

        # --- MODIFICA: Aria2 ha ora la priorità massima se abilitato ---
        if aria2_enabled_flag and aria2.enabled:
            client, client_name = aria2, "aria2c"
            logger.info("✅ Using aria2c (top priority)")
        elif libt and libt.enabled:
            client, client_name = libt, "libtorrent"
            logger.info("✅ Using libtorrent (embedded)")
        elif qbt.enabled:
            client, client_name = qbt, "qBittorrent"
            logger.info("✅ Using qBittorrent")
        elif transmission.enabled:
            client, client_name = transmission, "Transmission"
            logger.info("✅ Using Transmission")
        else:
            logger.error("❌ No torrent client enabled! (libtorrent/qBittorrent/Transmission/aria2)")
            time.sleep(60)
            continue

        notifier = Notifier({
            'notify_telegram':   cfg.notify_telegram,
            'telegram_bot_token': cfg.telegram_bot_token,
            'telegram_chat_id':   cfg.telegram_chat_id,
            'notify_email':       cfg.notify_email,
            'email_smtp':         cfg.email_smtp,
            'email_from':         cfg.email_from,
            'email_to':           cfg.email_to,
            'email_password':     cfg.email_password,
        })

        def _send_with_fallback(uri: str, meta: dict = None) -> Tuple[bool, str]:
            # --- ROUTING ED2K → aMule ★ v45 ---
            if uri and uri.startswith('ed2k://') and amule_cl:
                try:
                    if amule_cl.add(uri):
                        return True, 'amule'
                    else:
                        logger.warning("⚠️  aMule add() fallito per link ed2k")
                        return False, ''
                except Exception as _e:
                    logger.warning(f"⚠️  aMule send error: {_e}")
                    return False, ''
            # --- ROUTING NORMALE (magnet/torrent) ---
            # Inietta meta (notifier, db, cfg_dict) per il post-processing di Aria2Client
            _meta = None
            if meta is not None:
                _meta = dict(meta)
                _meta.setdefault('notifier', notifier)
                _meta.setdefault('db', db)
                _meta.setdefault('cfg_dict', {k: getattr(cfg, k, '') for k in
                    ['rename_episodes', 'rename_format', 'rename_template',
                     'tmdb_api_key', 'tmdb_language', 'tmdb_cache_days',
                     'move_episodes', 'cleanup_upgrades', 'trash_path', 'cleanup_action']})
            try:
                _add_meta = _meta if isinstance(client, Aria2Client) else None
                if client.add(uri, cfg.qbt, _add_meta) if isinstance(client, Aria2Client) else client.add(uri, cfg.qbt):
                    return True, client_name
                else:
                    logger.warning(f"⚠️  {client_name} add() failed, trying aria2c fallback")
            except Exception as e:
                logger.warning(f"⚠️  {client_name} send error: {e}. Trying aria2c fallback")

            # Fallback aria2c: SEMPRE in caso di errore, ignorando i bottoni dell'interfaccia
            if not isinstance(client, Aria2Client):
                try:
                    if aria2.add(uri, cfg.qbt, _meta):
                        return True, 'aria2c'
                except Exception as e:
                    logger.warning(f"⚠️  aria2c fallback error: {e}")
            return False, ''

        # Archive cleanup
        if cfg.archive_cleanup_enabled:
            try:
                arch_stats = eng.archive.get_stats()
                should_clean = (arch_stats['total'] > 500000 or
                                arch_stats['age_days'] > cfg.archive_max_age_days * 2)
                if should_clean:
                    result = eng.archive.cleanup_old(cfg.archive_max_age_days, cfg.archive_keep_min)
                    if result['deleted'] > 0:
                        logger.info(f"🧹 Archive cleaned: -{result['deleted']} items, {result['kept']} remaining")
            except Exception as e:
                logger.warning(f"⚠️  Archive cleanup error: {e}")

        # Scraping
        from core.constants import MAX_PAGES as _MP
        if comics_only_cycle:
            # Ciclo fumetti-only: salta scraping RSS e processing serie/film
            logger.info("📚 Comics-only cycle: skipping scraping, going directly to comics")
            live = []
        elif series_only_cycle:
            logger.info("📺 Series-only cycle: RSS scraping for TV series")
            live = eng.scrape_all(cfg.urls)
        elif movies_only_cycle:
            logger.info("🎬 Movies-only cycle: RSS scraping for movies")
            live = eng.scrape_all(cfg.urls)
        elif run_now_triggered:
            saved_age_filter = getattr(eng, 'age_filter', None)
            saved_max_pages  = _MP
            try:
                import core.constants as _cc
                eng.age_filter  = None
                _cc.MAX_PAGES   = 9999
                live = eng.scrape_all(cfg.urls)
            finally:
                eng.age_filter = saved_age_filter
                _cc.MAX_PAGES  = saved_max_pages
        else:
            live = eng.scrape_all(cfg.urls)

        # Feed XML
        archive_all = eng.archive.get_recent(cfg.feed_max_items)
        try:
            eng.generate_xml(archive_all)
            logger.info(f"📰 XML feed generated: {len(archive_all)} items from archive")
        except Exception as e:
            logger.warning(f"⚠️  Feed generation error: {e}")

        # Candidates
        archive_matched       = eng.search_archive_for_config(cfg)
        # candidates = live + archive_matched
        # stats.candidates_count = len(candidates)

        # 0. Get active torrents from client to avoid double downloads
        active_hashes = set()
        try:
            active_torrents = LibtorrentClient.list_torrents()
            for t in active_torrents:
                active_hashes.add(t.get('hash', '').lower())
        except Exception as e:
            logger.debug(f"active_hashes libtorrent: {e}")
            pass

        candidates = []
        for item in (live + archive_matched):
            uri = item.get('magnet', '')
            if uri.startswith('ed2k://'):
                h = _extract_ed2k_hash(uri)
            else:
                h = _extract_btih(uri)
            if h and h.lower() in active_hashes:
                continue
            candidates.append(item)
        
        stats.candidates_count = len(candidates)

        # Best-in-cycle collection
        best_by_ep = {}
        best_movies = {}

        # Helper: risolve series_id con matching robusto (gestisce "9-1-1" vs "9 1 1" ecc.)
        from core.models import normalize_series_name as _norm_name, _series_name_matches as _name_match
        _series_cache = None  # caricato lazy una volta sola per ciclo

        def _resolve_series_id(ep_name: str) -> int | None:
            nonlocal _series_cache
            if _series_cache is None:
                _series_cache = db.conn.execute('SELECT id, name FROM series').fetchall()
            norm = _norm_name(ep_name)
            for row in _series_cache:
                if _name_match(_norm_name(row['name']), norm):
                    return row['id']
            return None

        for item in candidates:
            ep = Parser.parse_series_episode(item['title'])
            if ep:
                match = cfg.find_series_match(ep['name'], ep['season'])
                if match:
                    timeframe = match.get('timeframe', 0)
                    min_rank  = cfg._min_res_from_qual_req(match.get('qual', ''))
                    max_rank  = cfg._max_res_from_qual_req(match.get('qual', ''))
                    this_rank = cfg._res_rank_from_title(item['title'])

                    if this_rank < min_rank:
                        stats.quality_rejected.append(f"{item['title'][:60]}...")
                        try:
                            c = db.conn.cursor()
                            _r_id = _resolve_series_id(ep['name'])
                            if _r_id:
                                db.record_episode_discard(_r_id, ep['season'], ep['episode'], 'below_quality')
                                db.record_feed_match(_r_id, ep['season'], ep['episode'],
                                                     item['title'], ep['quality'].score(),
                                                     'below_quality', item.get('magnet', ''))
                        except Exception as e:
                            logger.debug(f"record_feed_match below_quality: {e}")
                            pass
                        continue
                    if this_rank > max_rank:
                        stats.quality_rejected.append(f"{item['title'][:60]}... [above max]")
                        try:
                            c = db.conn.cursor()
                            _r_id = _resolve_series_id(ep['name'])
                            if _r_id:
                                db.record_episode_discard(_r_id, ep['season'], ep['episode'], 'above_quality')
                                db.record_feed_match(_r_id, ep['season'], ep['episode'],
                                                     item['title'], ep['quality'].score(),
                                                     'above_quality', item.get('magnet', ''))
                        except Exception as e:
                            logger.debug(f"record_feed_match above_quality: {e}")
                            pass
                        continue
                    lang_req = match.get('language', match.get('lang', 'ita'))
                    if not cfg._lang_ok(item['title'], lang_req):
                        stats.quality_rejected.append(f"{item['title'][:60]}... [lang]")
                        try:
                            c = db.conn.cursor()
                            _r_id = _resolve_series_id(ep['name'])
                            if _r_id:
                                db.record_episode_discard(_r_id, ep['season'], ep['episode'], 'lang_mismatch')
                        except Exception as e:
                            logger.debug(f"record_episode_discard lang_mismatch: {e}")
                            pass
                        continue

                    if timeframe > 0:
                        c = db.conn.cursor()
                        row_id = _resolve_series_id(ep['name'])
                        if row_id:
                            action = db.add_pending(row_id, ep['season'], ep['episode'],
                                                    ep['title'], ep['quality'].score(),
                                                    item['magnet'], timeframe)
                            if action == 'added':
                                logger.info(f"⏱️  PENDING: {ep['name']} S{ep['season']:02d}E{ep['episode']:02d} (wait {timeframe}h)")
                    else:
                        c = db.conn.cursor()
                        row_id = _resolve_series_id(ep['name'])
                        if not row_id:
                            continue
                        series_id    = row_id
                        key          = (series_id, ep['season'], ep['episode'])
                        score        = ep['quality'].score() + cfg.get_custom_score(item['title'])
                        ep_range_set = set(ep.get('episode_range') or [ep['episode']])
                        is_pack_item = ep.get('is_pack', False)

                        # --- Deduplicazione pack sovrapposti ---
                        # Se il candidato è un pack (episode_range), verifichiamo se esiste già
                        # un altro pack nella stessa stagione che lo contiene o che esso contiene.
                        if is_pack_item and len(ep_range_set) > 1:
                            dominated = False  # questo candidato è sottoinsieme di uno già presente
                            to_remove = []     # chiavi da rimuovere perché sono sottoinsiemi del candidato

                            for existing_key, existing_cand in best_by_ep.items():
                                # Confronta solo pack della stessa serie e stagione
                                if existing_key[0] != series_id or existing_key[1] != ep['season']:
                                    continue
                                existing_range = set(
                                    existing_cand.get('episode_range') or [existing_cand['episode']]
                                )
                                if len(existing_range) <= 1:
                                    continue  # l'esistente è un episodio singolo, non un pack

                                # Caso A: candidato è sottoinsieme dell'esistente
                                # (es. E10-18 arriva dopo E01-18 già presente)
                                if ep_range_set.issubset(existing_range):
                                    if score > existing_cand['score']:
                                        # Stesso contenuto ma qualità superiore: sostituisci
                                        # solo se il range è identico, altrimenti scarta
                                        if ep_range_set == existing_range:
                                            to_remove.append(existing_key)
                                        else:
                                            logger.debug(
                                                f"⏭️  Pack scartato (sottoinsieme): "
                                                f"'{ep['title'][:55]}' ⊂ '{existing_cand['title'][:55]}'"
                                            )
                                            dominated = True
                                    else:
                                        logger.debug(
                                            f"⏭️  Pack scartato (sottoinsieme): "
                                            f"'{ep['title'][:55]}' ⊂ '{existing_cand['title'][:55]}'"
                                        )
                                        dominated = True
                                    break

                                # Caso B: candidato contiene l'esistente
                                # (es. E01-18 arriva dopo E10-18 già presente)
                                if existing_range.issubset(ep_range_set):
                                    logger.debug(
                                        f"⏭️  Pack esistente rimosso (sottoinsieme del nuovo): "
                                        f"'{existing_cand['title'][:55]}' ⊂ '{ep['title'][:55]}'"
                                    )
                                    to_remove.append(existing_key)


                            for k in to_remove:
                                best_by_ep.pop(k, None)

                            if dominated:
                                continue  # scarta il candidato corrente

                        prev = best_by_ep.get(key)
                        if (prev is None) or (score > prev['score']):
                            best_by_ep[key] = {
                                'series_name':   ep['name'],
                                'season':        ep['season'],
                                'episode':       ep['episode'],
                                'episode_range': list(ep_range_set),
                                'title':         ep['title'],
                                'magnet':        item['magnet'],
                                'quality':       ep['quality'],
                                'score':         score,
                            }
                continue

# MOVIE - LOGICA BEST-IN-CYCLE (Individua il migliore della run)
            mov = Parser.parse_movie(item['title'])
            if mov:
                match = cfg.find_movie_match(mov['name'], mov['year'])
                if match:
                    lang_req = match.get('lang', match.get('language', 'ita'))
                    lang_ok  = not lang_req or cfg._lang_ok(item['title'], lang_req)
                    lang_bonus = 0

                    sub_req = match.get('subtitle', '')
                    sub_bonus = cfg._sub_score(item['title'], sub_req) if sub_req else 0

                    mov['config_name'] = match['name']
                    safe_magnet = sanitize_magnet(item['magnet'], item['title']) or item['magnet']

                    base_score    = mov['quality'].score() + cfg.get_custom_score(item['title'])
                    effective_score = base_score + lang_bonus + sub_bonus

                    # Registra nel feed (tutti i candidati, con e senza lingua)
                    try:
                        _fail = None if lang_ok else 'lang_mismatch'
                        db.record_movie_feed_match(match['name'], item['title'],
                                                   base_score, lang_bonus + sub_bonus, _fail, safe_magnet)
                    except Exception as e:
                        logger.debug(f"record_movie_feed_match: {e}")
                        pass

                    if not lang_ok:
                        stats.quality_rejected.append(f"{item['title'][:60]}... [lang film]")
                        continue

                    # Salva solo se è il migliore con lingua ok
                    key = match['name']
                    if key not in best_movies or effective_score > best_movies[key]['score']:
                        best_movies[key] = {
                            'mov': mov,
                            'magnet': safe_magnet,
                            'match': match,
                            'score': effective_score,
                            'title': item['title']
                        }
                continue

                
        if not comics_only_cycle and not movies_only_cycle:
            # 1. Best-in-cycle dispatch (serie TV)
            if best_by_ep:
                for key, cand in best_by_ep.items():
                    _m      = cfg.find_series_match(cand['series_name'], cand['season'])
                    ep_dict = {
                        'type':         'series',
                        'name':         cand['series_name'],
                        'season':       cand['season'],
                        'episode':      cand['episode'],
                        'episode_range': cand.get('episode_range', []),
                        'is_pack':      bool(cand.get('episode_range') and len(cand.get('episode_range', [])) > 1),
                        'quality':      cand['quality'],
                        'title':        cand['title'],
                        'archive_path': (_m.get('archive_path', '') if _m else ''),
                    }
                    safe_magnet = sanitize_magnet(cand['magnet'], cand['title']) or cand['magnet']
                    dl, msg     = db.check_series(ep_dict, safe_magnet, '')
                    if dl:
                        _a2_meta_series = {
                            'series_name':  cand['series_name'],
                            'season':       cand['season'],
                            'episode':      cand['episode'],
                            'archive_path': (_m.get('archive_path', '') if _m else ''),
                            'tmdb_id':      (_m.get('tmdb_id') if _m else None),
                            'title':        cand['title'],
                        }
                        ok_send, used_cl = _send_with_fallback(safe_magnet, _a2_meta_series)
                        if not ok_send:
                            _sid = key[0]
                            db.undo_episode_send(_sid, cand['season'], cand['episode'], safe_magnet)
                            logger.warning(f"⚠️  Send failed (best-in-cycle): {cand['series_name']} S{cand['season']:02d}E{cand['episode']:02d} — record DB rimosso")
                        if ok_send:
                            stats.downloads_started += 1
                            detail = 'upgrade' if (msg or '').lower().startswith('upgrade') else 'new'
                            logger.info(f"✅ DOWNLOAD [{used_cl}] (best-in-cycle, {detail}): "
                                        f"{cand['series_name']} S{cand['season']:02d}E{cand['episode']:02d}")
                            notifier.notify_download(cand['series_name'], cand['season'], cand['episode'],
                                                     cand['title'], cand['score'],
                                                     'best-in-cycle-upgrade' if detail == 'upgrade' else 'best-in-cycle-new')
                            tagger.tag_torrent(safe_magnet, TAG_SERIES)
                            _ui_tag(safe_magnet, TAG_SERIES)
                            # Registra il match scaricato nel feed_matches
                            try:
                                _sid = key[0]
                                db.record_feed_match(_sid, cand['season'], cand['episode'],
                                                     cand['title'], int(cand['score']),
                                                     'downloaded', safe_magnet)
                            except Exception as e:
                                logger.debug(f"record_feed_match downloaded: {e}")
                                pass
                        
        if not comics_only_cycle and not series_only_cycle:
            # 1b. Invio Film "Best-in-cycle"
            if best_movies:
                for name, cand in best_movies.items():
                    dl, msg = db.check_movie(cand['mov'], cand['magnet'], cand['match']['qual'])
                    if dl:
                        ok_send, used_cl = _send_with_fallback(cand['magnet'])
                        if ok_send:
                            stats.downloads_started += 1
                            logger.info(f"✅ MOVIE [{used_cl}] (best-in-cycle): {name} (Score: {cand['score']})")
                            notifier.notify_movie(name, cand['mov']['year'], cand['title'], cand['score'])
                            tagger.tag_torrent(cand['magnet'], TAG_FILM)
                            _ui_tag(cand['magnet'], TAG_FILM)
                            # Registra il download nel feed film
                            try:
                                db.record_movie_feed_match(name, cand['title'],
                                                           cand['score'], 0, 'downloaded', cand['magnet'])
                            except Exception as e:
                                logger.debug(f"record_movie_feed_match downloaded: {e}")
                                pass

        if not comics_only_cycle:
            # 2. Timeframe: download pronti (elabora pending da cicli precedenti)
            for r in db.get_ready_downloads():
                if not db.begin_downloading(r['id']):
                    continue
                safe_magnet = sanitize_magnet(r['best_magnet'], r['best_title']) or r['best_magnet']
                _m          = cfg.find_series_match(r['series_name'], r['season'])
                ep_dict     = {
                    'type':         'series',
                    'name':         r['series_name'],
                    'season':       r['season'],
                    'episode':      r['episode'],
                    'quality':      Parser.parse_quality(r['best_title'] or ''),
                    'title':        r['best_title'],
                    'archive_path': (_m.get('archive_path', '') if _m else ''),
                }
                dl_ok, msg = db.check_series(ep_dict, safe_magnet, '')
                if not dl_ok:
                    logger.info(f"⏭️  Skipping timeframe: {r['series_name']} S{r['season']:02d}E{r['episode']:02d} → {msg}")
                    continue
                _a2_meta_timeframe = {
                    'series_name':  r['series_name'],
                    'season':       r['season'],
                    'episode':      r['episode'],
                    'archive_path': (_m.get('archive_path', '') if _m else ''),
                    'tmdb_id':      (_m.get('tmdb_id') if _m else None),
                    'title':        r['best_title'],
                }
                ok_send, used_cl = _send_with_fallback(safe_magnet, _a2_meta_timeframe)
                if ok_send:
                    db.mark_downloaded(r['id'])
                    stats.downloads_started += 1
                    detail = 'upgrade' if (msg or '').lower().startswith('upgrade') else 'new'
                    logger.info(f"✅ TIMEFRAME [{used_cl}] ({detail}): {r['series_name']} "
                                f"S{r['season']:02d}E{r['episode']:02d} (score:{r['best_quality_score']})")
                    notifier.notify_download(r['series_name'], r['season'], r['episode'],
                                             r['best_title'], r['best_quality_score'],
                                             'timeframe-upgrade' if detail == 'upgrade' else 'timeframe-new')
                    tagger.tag_torrent(safe_magnet, TAG_SERIES)
                    _ui_tag(safe_magnet, TAG_SERIES)

        if not comics_only_cycle and not series_only_cycle and not movies_only_cycle:
            # 3. Scansione archivio locale (solo se run-now completo)
            if run_now_triggered:
                _arch_series_scanned    = 0
                _arch_episodes_upserted = 0
                try:
                    logger.info("🔎 ARCHIVE SCAN (Recheck All): start")
                    for s in cfg.series:
                        apath = s.get('archive_path', '')
                        if not apath or not os.path.isdir(apath):
                            continue
                        c = db.conn.cursor()
                        series_id = _resolve_series_id(s['name'])
                        if not series_id:
                            continue
                        from collections import defaultdict
                        found_map = defaultdict(int)
                        for root_dir, _, filenames in os.walk(apath):
                            for fn in filenames:
                                ep_p = Parser.parse_series_episode(fn)
                                if not ep_p:
                                    continue
                                sea, epi = int(ep_p.get('season') or 0), int(ep_p.get('episode') or 0)
                                if sea <= 0 or epi <= 0:
                                    continue
                                q  = ep_p.get('quality') or Parser.parse_quality(fn)
                                sc = q.score() if hasattr(q, 'score') else 0
                                if sc > found_map[(sea, epi)]:
                                    found_map[(sea, epi)] = sc
                        for (season, epnum), best in found_map.items():
                            now_iso = datetime.now(timezone.utc).isoformat()
                            c.execute("SELECT id, quality_score FROM episodes WHERE series_id=? AND season=? AND episode=?",
                                      (series_id, season, epnum))
                            row_ep = c.fetchone()
                            if row_ep:
                                if (row_ep['quality_score'] or 0) < int(best):
                                    c.execute("UPDATE episodes SET quality_score=?, downloaded_at=? WHERE id=?",
                                              (int(best), now_iso, row_ep['id']))
                            else:
                                c.execute("INSERT INTO episodes (series_id, season, episode, title, quality_score, is_repack, downloaded_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
                                          (series_id, season, epnum,
                                           f"{s['name']} S{season:02d}E{epnum:02d}", int(best), now_iso))
                            c.execute("INSERT INTO episode_archive_presence (series_id, season, episode, best_quality_score, at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(series_id,season,episode) DO UPDATE SET best_quality_score=excluded.best_quality_score, at=excluded.at",
                                      (series_id, season, epnum, int(best), now_iso))
                            _arch_episodes_upserted += 1
                        _arch_series_scanned += 1
                    db.conn.commit()
                except Exception as e:
                    logger.warning(f"⚠️ Archive scan error: {e}")
                finally:
                    logger.info(f"✅ ARCHIVE SCAN completed: series={_arch_series_scanned}, episodes={_arch_episodes_upserted}")

        if not comics_only_cycle and not movies_only_cycle:
            # 4. Gap filling (serie TV)
            if cfg.gap_filling:
                # Controlla se sono passate 6 ore (21600 secondi)
                is_deep_gap_run = (time.time() - last_deep_gap_fill) >= 21600
                if is_deep_gap_run:
                    logger.info("🕵️ DEEP GAP FILLING: Targeted live Jackett search activated for this cycle (max 5 per series).")
                    last_deep_gap_fill = time.time()
                else:
                    logger.info("🔍 GAP FILLING (Local Archive Only)...")

                for serie_cfg in cfg.series:
                    if not serie_cfg['enabled']:
                        continue
                    c = db.conn.cursor()
                    series_id = _resolve_series_id(serie_cfg['name'])
                    if not series_id:
                        continue
                    seasons_cfg = serie_cfg.get('seasons', '1+')

                    # Ricava le stagioni da controllare dalla configurazione della serie
                    # (es. "2+" → [2,3,4,...], "1-3" → [1,2,3], "1,3" → [1,3], "2" → [2])
                    # Per stagioni aperte (N+, *) usiamo come limite superiore il max
                    # presente in DB + 1 stagione oltre (per trovare stagioni nuove)
                    db_seasons = db.get_series_seasons(series_id)
                    db_max_season = max(db_seasons) if db_seasons else 0

                    seasons_to_check = []  # inizializzato sempre
                    min_s = max_s = None

                    if seasons_cfg == '*':
                        min_s  = 1
                        max_s  = max(db_max_season, 1)
                    elif '+' in str(seasons_cfg):
                        min_s  = int(str(seasons_cfg).replace('+', ''))
                        max_s  = max(db_max_season, min_s)
                    elif '-' in str(seasons_cfg):
                        parts  = str(seasons_cfg).split('-')
                        min_s, max_s = int(parts[0]), int(parts[1])
                    elif ',' in str(seasons_cfg):
                        seasons_to_check = [int(x) for x in str(seasons_cfg).split(',')]
                    else:
                        try:
                            min_s = max_s = int(seasons_cfg)
                        except Exception:
                            pass  # seasons_cfg non è un intero singolo, gestito nel ramo except
                            min_s = max_s = 1

                    if min_s is not None:
                        seasons_to_check = list(range(min_s, max_s + 1))

                    for season in seasons_to_check:
                        # Episodi già posseduti in DB per questa stagione
                        c2 = db.conn.cursor()
                        c2.execute(
                            "SELECT episode FROM episodes WHERE series_id=? AND season=? ORDER BY episode",
                            (series_id, season)
                        )
                        have = set(r[0] for r in c2.fetchall())

                        # Limite superiore: TMDB se disponibile, altrimenti max posseduto
                        expected = db.get_expected_episodes(series_id, season)
                        if expected:
                            ep_range = range(1, expected + 1)
                        elif have:
                            ep_range = range(1, max(have) + 1)
                        else:
                            # Stagione completamente assente: cerca solo E01 come sonda
                            ep_range = range(1, 2)

                        # 0. Sincronizzazione hash attivi prima del gap filling
                        active_hashes = set()
                        try:
                            active_torrents = LibtorrentClient.list_torrents()
                            for t in active_torrents:
                                active_hashes.add(t.get('hash', '').lower())
                        except Exception: pass

                        gaps = sorted(set(ep_range) - have)
                        if gaps:
                            logger.info(f"   → {serie_cfg['name']} S{season:02d} gap: {gaps}")
                            live_queries_count = 0  # <--- Contatore di sicurezza anti-ban
                        
                            for ep_num in gaps:
                                # Protezione: se l'episodio è già nel client (magari aggiunto in questo ciclo) skip
                                # Ma qui abbiamo solo hash, non sappiamo Exx senza parse... 
                                # Il check_series dentro il loop gap gestirà la deduplica Exx.
                                
                                ep_str = f"S{season:02d}E{ep_num:02d}"
                                search_queries = [f"{serie_cfg['name']} {ep_str}"]
                                for alias in serie_cfg.get('aliases', []):
                                    search_queries.append(f"{alias} {ep_str}")
                                
                                results = []
                                # 1. Cerca prima nell'archivio locale (gratis e veloce)
                                for sq in search_queries:
                                    results.extend(eng.archive.search(sq))

                                if results:
                                    logger.debug(f"      📂 Archive: {len(results)} candidates for {ep_str}")
                                else:
                                    logger.debug(f"      📂 Archive: no results for {ep_str}")

                                # 2. DEEP SEARCH MIRATA: interroga tutti gli indexer configurati
                                if not results and is_deep_gap_run and live_queries_count < 5:
                                    from core.engine import Engine as _Eng
                                    _active_indexers = _Eng._get_indexers(cfg)
                                    if not _active_indexers:
                                        logger.debug(f"      ⚠️ Deep gap: no active indexer, skipping Jackett for {ep_str}")
                                    else:
                                        lang_req = serie_cfg.get('language', serie_cfg.get('lang', 'ita'))

                                        # Usiamo SOLO il nome della serie (es. "The Pitt ita"), senza S02E03!
                                        base_q = serie_cfg['name']
                                        if lang_req and lang_req not in ('custom', 'none', 'any', '*'):
                                            base_q += f" {lang_req}"
                                        sub_req = serie_cfg.get('subtitle', '')
                                        if sub_req and sub_req not in ('none', 'any', '*'):
                                            from core.engine import _subtitle_query_terms as _sqt
                                            for _term in _sqt(sub_req):
                                                base_q += f" {_term}"

                                        logger.info(f"      📡 Jackett TV-Search: '{base_q}' (Season: {season}, Episode: {ep_num})")

                                        # Recupera tvdb_id dalla cache per ricerche più precise
                                        _tvdb_id = db.get_tvdb_id(series_id)

                                        # Passiamo season, ep e (se disponibile) tvdb_id
                                        j_res = eng._jackett_search(
                                            base_q,
                                            {},   # config ignorato, indexer letti da Config()
                                            season=season,
                                            ep=ep_num,
                                            tvdb_id=_tvdb_id
                                        )

                                        if j_res:
                                            if cfg.jackett_save_to_archive or cfg.prowlarr_save_to_archive:
                                                to_arch = [r for r in j_res if not (
                                                    ('Jackett' in r.get('source','') and not cfg.jackett_save_to_archive) or
                                                    ('Prowlarr' in r.get('source','') and not cfg.prowlarr_save_to_archive)
                                                )]
                                                if to_arch:
                                                    eng.archive.save_batch(to_arch)
                                            results.extend(j_res)

                                        # --- Fallback aMule/eD2k se Jackett è vuoto ---
                                        # Nota: usa download_result(idx) direttamente perché i risultati
                                        # di amulecmd Results non hanno hash MD4 — il link ed2k placeholder
                                        # non è usabile da amuled.add() né da libtorrent.
                                        elif amule_cl and str(
                                            config_db.get_setting('gap_fill_ed2k', 'no')
                                        ).lower() in ('yes', 'true', '1'):
                                            _ed2k_q = f"{serie_cfg['name']} {ep_str}"
                                            logger.info(f"      🫏 Jackett vuoto → fallback eD2k: '{_ed2k_q}'")
                                            amule_res = amule_cl.search(_ed2k_q, network='global')
                                            if not amule_res:
                                                logger.info(f"      🫏 eD2k: nessun risultato per '{_ed2k_q}'")
                                            else:
                                                logger.info(f"      🫏 eD2k: {len(amule_res)} risultati per '{_ed2k_q}'")
                                                _ed2k_series_id = None
                                                try:
                                                    _ed2k_series_id = _resolve_series_id(serie_cfg['name'])
                                                except Exception:
                                                    pass
                                                _lang_req_ed2k = serie_cfg.get('language', serie_cfg.get('lang', 'ita'))
                                                _qual_req_ed2k = serie_cfg.get('qual', 'any')
                                                _best_am       = None
                                                _best_am_score = -1

                                                for a_item in amule_res:
                                                    _a_title = a_item['name']
                                                    _a_score = 0
                                                    _a_fail  = None

                                                    # Verifica lingua
                                                    if not cfg._lang_ok(_a_title, _lang_req_ed2k):
                                                        _a_fail = 'lingua'
                                                        logger.info(f"        eD2k scarto [lingua≠{_lang_req_ed2k}]: '{_a_title}'")
                                                    else:
                                                        # Verifica episodio: deve matchare SxxExx
                                                        _a_ep = Parser.parse_series_episode(_a_title)
                                                        if not _a_ep or _a_ep['season'] != season or _a_ep['episode'] != ep_num:
                                                            _a_fail = 'episodio non corrispondente'
                                                            _parsed_ep = f"S{_a_ep['season']:02d}E{_a_ep['episode']:02d}" if _a_ep else "non parsato"
                                                            logger.info(f"        eD2k scarto [ep: cercavo {ep_str}, trovato {_parsed_ep}]: '{_a_title}'")
                                                        else:
                                                            _a_score   = _a_ep['quality'].score() if hasattr(_a_ep.get('quality'), 'score') else 0
                                                            # Verifica qualità con lo stesso metodo del pipeline normale
                                                            _min_rank  = cfg._min_res_from_qual_req(_qual_req_ed2k)
                                                            _max_rank  = cfg._max_res_from_qual_req(_qual_req_ed2k)
                                                            _this_rank = cfg._res_rank_from_title(_a_title)
                                                            if _this_rank < _min_rank:
                                                                _a_fail = f'qualità insufficiente (rank {_this_rank} < {_min_rank})'
                                                                logger.info(f"        eD2k scarto [qualità bassa rank {_this_rank}<{_min_rank}]: '{_a_title}'")
                                                            elif _this_rank > _max_rank:
                                                                _a_fail = f'qualità oltre limite (rank {_this_rank} > {_max_rank})'
                                                                logger.info(f"        eD2k scarto [qualità alta rank {_this_rank}>{_max_rank}]: '{_a_title}'")
                                                            else:
                                                                logger.info(f"        eD2k candidato OK: '{_a_title}' "
                                                                            f"score={_a_score} sorgenti={a_item['sources']}")

                                                    # Registra nel feed della serie (visibile nell'UI)
                                                    if _ed2k_series_id:
                                                        try:
                                                            db.record_feed_match(
                                                                _ed2k_series_id, season, ep_num,
                                                                f"[eD2k] {_a_title}", int(_a_score),
                                                                _a_fail,        # None = ok, str = motivo scarto
                                                                a_item['ed2k']  # link placeholder (loggato, non usato per add)
                                                            )
                                                        except Exception as _fe:
                                                            logger.debug(f"record_feed_match eD2k: {_fe}")

                                                    # Tieni il migliore per score (a parità: più sorgenti)
                                                    if _a_fail is None:
                                                        if _a_score > _best_am_score or (
                                                            _a_score == _best_am_score and
                                                            _best_am and a_item['sources'] > _best_am['sources']
                                                        ):
                                                            _best_am_score = _a_score
                                                            _best_am       = a_item

                                                if _best_am:
                                                    logger.info(f"      🫏 eD2k migliore: '{_best_am['name']}' "
                                                                f"score={_best_am_score} sorgenti={_best_am['sources']} "
                                                                f"→ Download {_best_am['idx']}")
                                                    if amule_cl.download_result(_best_am['idx'], name=_best_am['name']):
                                                        stats.gaps_filled += 1
                                                        stats.downloads_started += 1
                                                        logger.info(f"   ✅ GAP FILLED [amule/eD2k]: "
                                                                    f"{serie_cfg['name']} {ep_str} "
                                                                    f"(score={_best_am_score})")
                                                        notifier.notify_gap_filled(serie_cfg['name'], season, ep_num)
                                                        # Aggiorna il feed match come 'downloaded'
                                                        if _ed2k_series_id:
                                                            try:
                                                                db.record_feed_match(
                                                                    _ed2k_series_id, season, ep_num,
                                                                    f"[eD2k] {_best_am['name']}",
                                                                    int(_best_am_score),
                                                                    'downloaded',
                                                                    _best_am['ed2k']
                                                                )
                                                            except Exception:
                                                                pass
                                                    else:
                                                        logger.warning(
                                                            f"      ⚠️  eD2k Download FALLITO — "
                                                            f"{serie_cfg['name']} {ep_str} | "
                                                            f"file: '{_best_am['name']}' | "
                                                            f"idx={_best_am['idx']} sorgenti={_best_am['sources']} "
                                                            f"(vedi log aMule sopra per il motivo)"
                                                        )
                                                else:
                                                    logger.info(f"      🫏 eD2k: nessun candidato valido "
                                                                f"(lingua={_lang_req_ed2k} qualità={_qual_req_ed2k}) "
                                                                f"tra {len(amule_res)} risultati")
                                        # -------------------------------------------------

                                        live_queries_count += 1
                                        time.sleep(2)
                            
                                best_ep_cand = None
                                best_ep_score = -1
                                _gap_series_id = None
                                try:
                                    _gap_series_id = _resolve_series_id(serie_cfg['name'])
                                except Exception as e:
                                    logger.debug(f"_resolve_series_id: {e}")
                                    pass

                                for item in results:
                                    lang_req = serie_cfg.get('language', serie_cfg.get('lang', 'ita'))
                                    if not cfg._lang_ok(item['title'], lang_req):
                                        continue
                                
                                    ep_p = Parser.parse_series_episode(item['title'])
                                    if ep_p and ep_p['season'] == season and ep_p['episode'] == ep_num:
                                        match = cfg.find_series_match(ep_p['name'], ep_p['season'])
                                        if not match or match['name'] != serie_cfg['name']:
                                            continue

                                        safe_magnet = sanitize_magnet(item['magnet'], item['title']) or item['magnet']
                                        ep_p['archive_path'] = serie_cfg.get('archive_path', '')
                                        dl_ok, msg = db.check_series(ep_p, safe_magnet, serie_cfg['qual'])
                                        _score = ep_p['quality'].score() if hasattr(ep_p['quality'], 'score') else 0

                                        # Registra il match parziale (lingua OK, qualità non matchava o era già ok)
                                        if _gap_series_id:
                                            _reason = None if dl_ok else (msg or 'partial')
                                            try:
                                                db.record_feed_match(_gap_series_id, season, ep_num,
                                                                     item['title'], int(_score),
                                                                     _reason, safe_magnet)
                                            except Exception as e:
                                                logger.debug(f"record_feed_match gap: {e}")
                                                pass

                                        if dl_ok:
                                            if _score > best_ep_score:
                                                best_ep_score = _score
                                                # Identifica l'uploader o usa Archivio come fallback
                                                source = item.get('uploader') or "Archivio"
                                                best_ep_cand = (ep_p, safe_magnet, source)
                                            
                                if best_ep_cand:
                                    ep_p, safe_magnet, source = best_ep_cand
                                    ok_send, used_cl = _send_with_fallback(safe_magnet)
                                    if not ok_send and _gap_series_id:
                                        db.undo_episode_send(_gap_series_id, season, ep_num, safe_magnet)
                                        logger.warning(f"⚠️  Send failed (gap-fill): {serie_cfg['name']} S{season:02d}E{ep_num:02d} — DB record removed")
                                    if ok_send:
                                        stats.gaps_filled += 1
                                        stats.downloads_started += 1
                                        logger.info(f"   ✅ GAP FILLED [{used_cl}] via {source}: {serie_cfg['name']} {ep_str} (Score: {best_ep_score})")
                                        notifier.notify_gap_filled(serie_cfg['name'], season, ep_num)
                                        tagger.tag_torrent(safe_magnet, TAG_SERIES)
                                        _ui_tag(safe_magnet, TAG_SERIES)
                                        # Registra il match come 'downloaded'
                                        if _gap_series_id:
                                            try:
                                                db.record_feed_match(_gap_series_id, season, ep_num,
                                                                     ep_p['title'], int(best_ep_score),
                                                                     'downloaded', safe_magnet)
                                            except Exception as e:
                                                logger.debug(f"record_feed_match gap downloaded: {e}")
                                                pass

        if not comics_only_cycle and not series_only_cycle:
            # 4b. Ricerca Retroattiva Film in Archivio
            logger.info("🎬 SEARCHING MOVIES IN ARCHIVE...")
            for mov_cfg in cfg.movies:
                if not mov_cfg.get('enabled', True):
                    continue
                
                c = db.conn.cursor()
                c.execute("SELECT id FROM movies WHERE name=? AND magnet_link IS NOT NULL", (mov_cfg['name'],))
                if c.fetchone():
                    continue
                
                search_str = mov_cfg['name']
                results = eng.archive.search(search_str)
            
                best_movie_cand = None
                best_movie_score = -1
            
                for item in results:
                    lang_req = mov_cfg.get('language', mov_cfg.get('lang', 'ita'))
                    if not cfg._lang_ok(item['title'], lang_req):
                        continue
                    
                    mov_p = Parser.parse_movie(item['title'])
                    if not mov_p:
                        continue
                    
                    match = cfg.find_movie_match(mov_p['name'], mov_p['year'])
                    if match and match['name'] == mov_cfg['name']:
                        mov_p['config_name'] = match['name']
                        safe_magnet = sanitize_magnet(item['magnet'], item['title']) or item['magnet']
                    
                        dl_ok, msg = db.check_movie(mov_p, safe_magnet, match.get('qual', match.get('quality', '')))
                        if dl_ok:
                            score = mov_p['quality'].score() if hasattr(mov_p['quality'], 'score') else 0
                            if score > best_movie_score:
                                best_movie_score = score
                                source = item.get('uploader') or "Archivio"
                                best_movie_cand = (mov_p, safe_magnet, match, source, item['title'])
                            
                if best_movie_cand:
                    mov_p, safe_magnet, match, source, raw_title = best_movie_cand
                    ok_send, used_cl = _send_with_fallback(safe_magnet)
                    if ok_send:
                        stats.downloads_started += 1
                        logger.info(f"   🚀 MOVIE FOUND [{used_cl}] via {source}: {match['name']} (Score: {best_movie_score})")
                        notifier.notify_movie(match['name'], mov_p['year'], raw_title, best_movie_score)
                        tagger.tag_torrent(safe_magnet, TAG_FILM)
                        _ui_tag(safe_magnet, TAG_FILM)
                else:
                    if results:
                        sample = results[0]['title'][:50]
                        logger.info(f"   ℹ️ Filters not met for '{mov_cfg['name']}'. (Sample found: {sample}...)")


        # 5. Report & pulizia trigger
        db.save_cycle_history({
            'scraped':        stats.scraped,
            'candidates':     stats.candidates_count,
            'series_matched': len(stats.series_matched),
            'movies_matched': len(stats.movies_matched),
            'downloads':      stats.downloads_started,
            'gaps':           stats.gaps_filled,
            'errors':         stats.errors,
        })
        
        # --- SALVATAGGIO DIMENSIONE FILE COMPLETATI NEL DB ---
        try:
            if LibtorrentClient.session_available():
                c_sz = db.conn.cursor()
                updated_any = False
                for t in LibtorrentClient.list_torrents():
                    if t.get('progress', 0) >= 1.0 or t.get('state', '') in ('finished', 'finished_t', 'seeding', 'salvato'):
                        t_hash = t.get('hash', '')
                        total_bytes = t.get('total_size', 0)
                        t_name = t.get('name', 'Sconosciuto') # <--- ECCO LA VARIABILE CHE MANCAVA!
                        
                        if t_hash and total_bytes > 0:
                            # 1. Prova ad aggiornare l'episodio
                            c_sz.execute("UPDATE episodes SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (total_bytes, t_hash))
                            ep_updated = c_sz.rowcount
                            
                            # 2. Prova ad aggiornare il film
                            c_sz.execute("UPDATE movies SET size_bytes=? WHERE magnet_hash=? AND size_bytes=0", (total_bytes, t_hash))  
                            mov_updated = c_sz.rowcount
                            
                            # 3. Se è un download MANUALE o un PACK (non ha aggiornato nulla sopra)
                            if ep_updated == 0 and mov_updated == 0:
                                now_iso = datetime.now(timezone.utc).isoformat()
                                c_sz.execute("INSERT OR IGNORE INTO episodes (series_id, season, episode, title, size_bytes, downloaded_at, magnet_hash) VALUES (0, 0, 0, ?, ?, ?, ?)",
                                            (t_name, total_bytes, now_iso, t_hash))
                            
                            updated_any = True
                if updated_any:
                    db.conn.commit()
        except Exception as e:
            logger.error(f"Error updating size_bytes: {e}")
        
        # --- POST-PROCESSING E PULIZIA AUTOMATICA TORRENT COMPLETATI ---
        try:
            if LibtorrentClient.session_available():
                import shutil
                from core.renamer import _build_filename, _VIDEO_EXTS
                from core.cleaner import _handle_duplicate
                from core.tmdb import TMDBClient

                api_key = getattr(cfg, 'tmdb_api_key', '').strip()
                do_move = str(getattr(cfg, 'move_episodes', 'no')).lower() in ('yes', 'true', '1')
                do_rename = str(getattr(cfg, 'rename_episodes', 'no')).lower() in ('yes', 'true', '1')
                do_cleanup = str(getattr(cfg, 'cleanup_upgrades', 'no')).lower() in ('yes', 'true', '1')
                trash_path = str(getattr(cfg, 'trash_path', '')).strip()
                cleanup_action = str(getattr(cfg, 'cleanup_action', 'move')).lower()
                rename_fmt = str(getattr(cfg, 'rename_format', 'base')).lower()
                rename_template = str(getattr(cfg, 'rename_template', '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}]'))

                for t in LibtorrentClient.list_torrents():
                    if t.get('progress', 0) >= 1.0:
                        t_hash = t['hash']

                        # ANTI-FALSO-COMPLETAMENTO: salta torrent con metadata non ancora risolto.
                        # Libtorrent riporta progress=1.0 subito dopo l'aggiunta di un magnet
                        # prima che il metadata sia scaricato (total_size=0, active_time~0).
                        # Attendiamo almeno 10s di attività e una dimensione nota prima di processare.
                        _t_size   = t.get('total_size', 0)
                        _t_active = t.get('active_time', 0)
                        if _t_size == 0 or _t_active < 10:
                            logger.debug(f"[post-processing] skip torrent non maturo: {t.get('name','')} size={_t_size} active={_t_active}s")
                            continue

                        # ANTI-DUPLICATI: salta torrent già notificati (controllo persistente su DB)
                        if _pp_is_notified(t_hash):
                            continue

                        t_name = t['name']
                        t_path = os.path.join(t['save_path'], t_name)
                        
                        # ANTI-DOPPIONE: Se la cartella non c'è più, controlla se il file
                        # è già arrivato sul NAS (libtorrent lo sposta autonomamente).
                        if not os.path.exists(t_path):
                            if do_rename and api_key and not _pp_get_no_rename(t_hash):
                                ep = Parser.parse_series_episode(t_name)
                                match = cfg.find_series_match(ep['name'], ep['season']) if ep else None
                                if match:
                                    dest_dir = match.get('archive_path', '')
                                    if dest_dir and os.path.isdir(dest_dir):
                                        from core.renamer import rename_completed_torrent
                                        cfg_dict = {k: getattr(cfg, k, '') for k in
                                                    ['rename_episodes','rename_format','rename_template',
                                                     'tmdb_api_key','tmdb_language','tmdb_cache_days']}
                                        rename_completed_torrent(t_name, dest_dir, cfg_dict, db)
                            elif do_rename and _pp_get_no_rename(t_hash):
                                logger.info(f"⏭️ [no_rename] Skip rename per '{t_name}' (flag impostato)")
                            if getattr(cfg, 'auto_remove_completed', False):
                                LibtorrentClient.remove_torrent(t_hash, delete_files=False)
                            continue
                        
                        action_log = []
                        is_processed = False
                        is_series = False

                        # 1. Recupero Statistiche (Da passare al Notifier)
                        size_bytes = t.get('total_size', 0)
                        time_sec = t.get('active_time', t.get('time_active', 1))

                        # 2. Processamento
                        ep = Parser.parse_series_episode(t_name)
                        if ep:
                            match = cfg.find_series_match(ep['name'], ep['season'])
                            if match:
                                is_series = True
                                series_name_disp = f"{match['name']} S{ep['season']:02d}E{ep['episode']:02d}"
                                dest_dir = match.get('archive_path', '')

                                # Season pack (episode=0): già gestito da libtorrent._handle_season_pack()
                                # che copia e rinomina i singoli episodi. Qui saltiamo per evitare
                                # di trattarlo come file singolo.
                                if ep.get('is_pack') or ep.get('episode') == 0:
                                    logger.debug(f"[post-processing] skip season pack E00: {t_name}")
                                    if getattr(cfg, 'auto_remove_completed', False):
                                        LibtorrentClient.remove_torrent(t_hash, delete_files=False)
                                    continue

                                if dest_dir and do_move and os.path.exists(t_path):
                                    video_files = []
                                    if os.path.isdir(t_path):
                                        for root, _, files in os.walk(t_path):
                                            for f in files:
                                                if os.path.splitext(f)[1].lower() in _VIDEO_EXTS and 'sample' not in f.lower():
                                                    video_files.append(os.path.join(root, f))
                                    else:
                                        if os.path.splitext(t_path)[1].lower() in _VIDEO_EXTS:
                                            video_files.append(t_path)

                                    if video_files:
                                        video_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
                                        src_file = video_files[0]
                                        _, ext = os.path.splitext(src_file)
                                        final_name = os.path.basename(src_file)

                                        renamed_to = None   # nome file rinominato (solo basename)
                                        _skip_rename = _pp_get_no_rename(t_hash)
                                        if _skip_rename:
                                            logger.info(f"⏭️ [no_rename] Skip rename+TMDB per '{t_name}' (flag impostato)")
                                        if do_rename and api_key and not _skip_rename:
                                            try:
                                                tmdb = TMDBClient(api_key, cache_days=7)
                                                t_id = match.get('tmdb_id') or tmdb.resolve_series_id(match['name'])
                                                # Per season pack (E00) TMDB non ha titolo — usa "Season Pack" come fallback
                                                if ep['episode'] == 0:
                                                    ep_title = 'Season Pack'
                                                else:
                                                    ep_title = tmdb.fetch_episode_title(t_id, ep['season'], ep['episode']) if t_id else None
                                                final_name = _build_filename(match['name'], ep['season'], ep['episode'], ep_title, ext, fmt=rename_fmt, template_str=rename_template)
                                                renamed_to = final_name
                                                action_log.append(f"✏️ <b>Rinominato in:</b>\n<code>{final_name}</code>")
                                            except Exception as e:
                                                logger.warning(f"build_filename post-processing: {e}")
                                                action_log.append("⚠️ Rinomina fallita (uso nome originale)")

                                        use_season_subdir = match.get('season_subfolders', False)
                                        if use_season_subdir:
                                            target_dir = os.path.join(dest_dir, f"Stagione {ep['season']}")
                                            os.makedirs(target_dir, exist_ok=True)
                                        else:
                                            target_dir = dest_dir
                                        dst_file = os.path.join(target_dir, final_name)

                                        if not os.path.exists(dst_file):
                                            shutil.move(src_file, dst_file)
                                            # "Spostato in" rimosso: ridondante con "Archiviato in" che mostra già il path completo
                                            is_processed = True

                                            if do_cleanup:
                                                cleaned_count = 0
                                                for root, _, files in os.walk(dest_dir):
                                                    for f in files:
                                                        if f == final_name: continue
                                                        if os.path.splitext(f)[1].lower() not in _VIDEO_EXTS: continue
                                                        
                                                        m_old = re.search(r'(?i)[Ss]0*(\d{1,2})[._\-\s]*[Ee]0*(\d{1,3})', f)
                                                        if m_old and int(m_old.group(1)) == ep['season'] and int(m_old.group(2)) == ep['episode']:
                                                            old_fpath = os.path.join(root, f)
                                                            if _handle_duplicate(old_fpath, trash_path, action=cleanup_action, reason="Upgrade completato"):
                                                                cleaned_count += 1
                                                if cleaned_count > 0:
                                                    action_log.append(f"🧹 <b>Pulizia:</b>\nEliminati/Spostati {cleaned_count} file obsoleti")

                                            # Rimuovi l'eventuale cartella vuota lasciata dal torrent
                                            if os.path.isdir(t_path) and not os.listdir(t_path):
                                                shutil.rmtree(t_path, ignore_errors=True)

                                            # --- INIZIO FIX ANTI-AMNESIA (FASE 1) ---
                                            try:
                                                
                                                t_q = Parser.parse_quality(t_name)
                                                t_score = (t_q.score() if hasattr(t_q, 'score') else 0) + getattr(cfg, 'get_custom_score', lambda x: 0)(t_name)
                                                
                                                c_db = db.conn.cursor()
                                                c_db.execute("SELECT id FROM series WHERE name=?", (match['name'],))
                                                s_row = c_db.fetchone()
                                                if s_row:
                                                    s_id = s_row['id']
                                                    now_iso = datetime.now(timezone.utc).isoformat()
                                                    c_db.execute("SELECT id, quality_score FROM episodes WHERE series_id=? AND season=? AND episode=?", (s_id, ep['season'], ep['episode']))
                                                    row_ep = c_db.fetchone()
                                                    if row_ep:
                                                        # Aggiorna se il punteggio reale della release è superiore a quanto in memoria
                                                        if (row_ep['quality_score'] or 0) < t_score:
                                                            c_db.execute("UPDATE episodes SET quality_score=?, downloaded_at=?, magnet_hash=?, title=? WHERE id=?", (t_score, now_iso, t_hash, t_name, row_ep['id']))
                                                    else:
                                                        c_db.execute("INSERT INTO episodes (series_id, season, episode, title, quality_score, downloaded_at, magnet_hash) VALUES (?, ?, ?, ?, ?, ?, ?)", (s_id, ep['season'], ep['episode'], t_name, t_score, now_iso, t_hash))
                                                    db.conn.commit()
                                            except Exception as db_err:
                                                logger.debug(f"Error saving anti-amnesia score: {db_err}")
                                            # --- FINE FIX ---

                        # 2b. FILM / DOWNLOAD MANUALE con tag_dir_rules
                        # Se il torrent non è una serie (ep è None) ma ha un tag
                        # associato che ha una final_dir configurata, sposta il file lì.
                        # NOTA: libtorrent può aver già spostato il file dalla temp_dir
                        # alla libtorrent_dir globale prima che arrivassimo qui,
                        # quindi t_path potrebbe non esistere. In quel caso cerchiamo
                        # il file nella libtorrent_dir.
                        _lt_global_dir = getattr(cfg, 'libtorrent_dir', '').strip()
                        if not os.path.exists(t_path) and _lt_global_dir:
                            _candidate = os.path.join(_lt_global_dir, t_name)
                            if os.path.exists(_candidate):
                                t_path = _candidate
                        if not ep and not is_processed and os.path.exists(t_path):
                            try:
                                import sqlite3
                                from core.constants import DB_FILE as _DBF
                                # 1. Leggi il tag del torrent dal DB
                                _t_tag = ''
                                with sqlite3.connect(_DBF, timeout=5) as _tc:
                                    _row = _tc.execute(
                                        "SELECT tag FROM torrent_meta WHERE hash=?",
                                        (t_hash.lower(),)
                                    ).fetchone()
                                    if _row:
                                        _t_tag = (_row[0] or '').strip()

                                if _t_tag:
                                    # 2. Cerca la final_dir nella regola corrispondente
                                    _rules = config_db.get_setting('tag_dir_rules', [])
                                    _final_dir = ''
                                    for _r in (_rules if isinstance(_rules, list) else []):
                                        if isinstance(_r, dict) and _r.get('tag', '').strip().lower() == _t_tag.lower():
                                            _final_dir = _r.get('final_dir', '').strip()
                                            break

                                    if _final_dir and os.path.isdir(_final_dir):
                                        # 3. Raccogli file video
                                        _video_files = []
                                        if os.path.isdir(t_path):
                                            for _root, _, _files in os.walk(t_path):
                                                for _f in _files:
                                                    if os.path.splitext(_f)[1].lower() in _VIDEO_EXTS and 'sample' not in _f.lower():
                                                        _video_files.append(os.path.join(_root, _f))
                                        elif os.path.splitext(t_path)[1].lower() in _VIDEO_EXTS:
                                            _video_files.append(t_path)

                                        if _video_files:
                                            _video_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
                                            _src = _video_files[0]
                                            _dst = os.path.join(_final_dir, os.path.basename(_src))
                                            if not os.path.exists(_dst):
                                                shutil.move(_src, _dst)
                                                is_processed = True
                                                action_log.append(f"📁 <b>Archiviato in:</b>\n<code>{_dst}</code>")
                                                logger.info(f"📦 [tag_dir] '{t_name}' → '{_dst}' (tag: {_t_tag})")
                                                # Rimuovi cartella vuota
                                                if os.path.isdir(t_path) and not os.listdir(t_path):
                                                    shutil.rmtree(t_path, ignore_errors=True)
                                            else:
                                                logger.info(f"[tag_dir] skip: '{_dst}' esiste già")
                                    elif _final_dir:
                                        logger.warning(f"[tag_dir] final_dir '{_final_dir}' per tag '{_t_tag}' non esiste o non è una directory")
                            except Exception as _tde:
                                logger.error(f"[tag_dir] errore post-processing tag_dir_rules: {_tde}")

                        # 3. DELEGA LA NOTIFICA A NOTIFIER.PY
                        if is_processed or is_series:
                            title_for_notify = series_name_disp if is_series else t_name
                            if hasattr(notifier, 'notify_post_processing'):
                                _final_path  = dst_file if is_processed and 'dst_file' in dir() else None
                                _renamed_to  = renamed_to if is_processed and 'renamed_to' in dir() else None
                                notifier.notify_post_processing(title_for_notify, size_bytes, time_sec, action_log, is_series, is_processed, final_path=_final_path, renamed_to=_renamed_to)
                            logger.info(f"📦 Post-processing completed for: {title_for_notify}")
                        else:
                            if hasattr(notifier, 'notify_post_processing'):
                                notifier.notify_post_processing(t_name, size_bytes, time_sec, action_log, is_series, is_processed)
                            logger.info(f"📦 Post-processing completed for: {t_name}")

                        # Marca l'hash come già notificato nel DB (persiste ai riavvii)
                        # Per torrent non-serie: marca solo se il file è stato effettivamente
                        # spostato, altrimenti al prossimo ciclo può ritentare.
                        if is_series or is_processed:
                            _pp_mark_notified(t_hash)

                        # 4. Pulizia coda client
                        if getattr(cfg, 'auto_remove_completed', False):
                            LibtorrentClient.remove_torrent(t_hash, delete_files=False)

        except Exception as e:
            logger.error(f"❌ Automatic post-processing error: {e}")
        
        # --- NUOVO: CONTROLLO SERIE COMPLETATE E NOTIFICA TELEGRAM (Solo 1 volta al giorno) ---
        # 86400 secondi = 24 ore. Esegue al primo avvio e poi una volta al giorno.
        if (time.time() - last_completion_check) >= 86400:
            last_completion_check = time.time()
            logger.info("🔍 Starting daily series completion check...")
            try:
                c_comp = db.conn.cursor()
                try: c_comp.execute("ALTER TABLE series ADD COLUMN is_completed BOOLEAN DEFAULT 0"); db.conn.commit()
                except: pass

                from core.tmdb import TMDBClient
                from core.models import normalize_series_name, _series_name_matches
                
                api_key = getattr(cfg, 'tmdb_api_key', '').strip()
                
                if api_key:
                    tmdb_client = TMDBClient(api_key, cache_days=7)
                    for s_cfg in cfg.series:
                        if not s_cfg.get('enabled', True): continue
                        
                        s_name = s_cfg['name']
                        _comp_id = _resolve_series_id(s_name)
                        if not _comp_id: continue
                        
                        s_id = _comp_id
                        
                        # Legge l'ID pinnato dal file di configurazione, altrimenti lo cerca e salva
                        tmdb_id = s_cfg.get('tmdb_id') or tmdb_client.get_tmdb_id_for_series(db, s_name) or tmdb_client.resolve_series_id(s_name)
                        if not tmdb_id: continue
                        
                        details = tmdb_client.fetch_series_details(tmdb_id)
                        if not details: continue
                        
                        # Salva poster su disco se non già presente
                        try:
                            import os as _os
                            _posters_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'static', 'posters')
                            _poster_file = _os.path.join(_posters_dir, f's_{s_id}.jpg')
                            if not _os.path.exists(_poster_file):
                                _poster_path = details.get('poster_path', '')
                                if _poster_path:
                                    import requests as _req
                                    _resp = _req.get(f'https://image.tmdb.org/t/p/w300{_poster_path}', timeout=10)
                                    if _resp.status_code == 200:
                                        _os.makedirs(_posters_dir, exist_ok=True)
                                        with open(_poster_file, 'wb') as _f:
                                            _f.write(_resp.content)
                        except Exception as _pe:
                            logger.debug(f"poster extto3: {_pe}")
                        
                        # 🛡️ SELF-HEALING 1: Controllo anti-corruzione per vecchi ID TVDB
                        tmdb_name = details.get('name') or details.get('original_name') or ''
                        if not _series_name_matches(normalize_series_name(s_name), normalize_series_name(tmdb_name)):
                            tmdb_id = tmdb_client.resolve_series_id(s_name)
                            if not tmdb_id: continue
                            details = tmdb_client.fetch_series_details(tmdb_id)
                            if not details: continue
                            db.upsert_series_metadata(s_id, tmdb_id, {})
                            
                        is_tmdb_ended = details.get('status', '') in ['Ended', 'Canceled']
                        
                        # 🛡️ SELF-HEALING 2: Ripristina serie falsamente completate
                        _comp_row = db.conn.execute('SELECT is_completed FROM series WHERE id=?', (s_id,)).fetchone()
                        already_completed = bool(_comp_row and _comp_row['is_completed'])
                        
                        if already_completed:
                            if not is_tmdb_ended:
                                c_comp.execute('UPDATE series SET is_completed=0, is_ended=0 WHERE id=?', (s_id,))
                                db.conn.commit()
                                logger.info(f"🔄 Series '{s_name}' reopened (TMDB status changed).")
                            continue
                            
                        if is_tmdb_ended:
                            c_comp.execute('UPDATE series SET is_ended=1 WHERE id=?', (s_id,))
                            db.conn.commit()
                        else:
                            continue
                        
                        tmdb_seasons = {s['season_number']: s['episode_count'] for s in details.get('seasons', []) if s['season_number'] > 0}
                        if not tmdb_seasons: continue
                        
                        seasons_req = str(s_cfg.get('seasons', '1+'))
                        ignored = s_cfg.get('ignored_seasons', [])
                        tmdb_max = max(tmdb_seasons.keys())
                        
                        seasons_to_check = []
                        min_s, max_s = None, None
                        if seasons_req == '*': min_s, max_s = 1, tmdb_max
                        elif '+' in seasons_req: min_s, max_s = int(seasons_req.replace('+', '')), tmdb_max
                        elif '-' in seasons_req: min_s, max_s = map(int, seasons_req.split('-'))
                        elif ',' in seasons_req: seasons_to_check = [int(x) for x in seasons_req.split(',')]
                        else: min_s = max_s = int(seasons_req) if seasons_req.isdigit() else 1
                        
                        if min_s is not None: seasons_to_check = list(range(min_s, max_s + 1))
                        valid_seasons = [s for s in seasons_to_check if s not in ignored and s in tmdb_seasons]
                        if not valid_seasons: continue
                        
                        missing_any = False
                        for s_num in valid_seasons:
                            exp_count = tmdb_seasons[s_num]
                            if exp_count <= 0: continue
                            
                            c_comp.execute('''
                                SELECT COUNT(DISTINCT episode) FROM episodes 
                                WHERE series_id=? AND downloaded_at IS NOT NULL 
                                AND season=? AND episode BETWEEN 1 AND ?
                            ''', (s_id, s_num, exp_count))
                            
                            down_count = c_comp.fetchone()[0]
                            if down_count < exp_count:
                                missing_any = True
                                break
                                
                        if not missing_any and valid_seasons:
                            c_comp.execute('UPDATE series SET is_completed=1 WHERE id=?', (s_id,))
                            db.conn.commit()
                            logger.info(f"🎊 SERIES 100% COMPLETE: {s_name}!")
                            notifier.notify_series_complete(s_name)
            except Exception as e:
                logger.warning(f"⚠️ Series completion check error: {e}")
        # -------------------------------------------------------------
        
        stats.report(cfg)

        # 5. Ciclo Fumetti (getcomics.org)
        comics_interval = int(getattr(cfg, 'comics_check_interval', 604800))  # default: 7 giorni
        _comics_due = comics_interval > 0 and (time.time() - last_comics_check) >= comics_interval
        if _comics_due or run_comics_triggered:
            if _comics_due:
                last_comics_check = time.time()
            logger.info("📚 Starting comics cycle (getcomics.org)...")
            try:
                from core.comics import run_comics_cycle, ComicsDB as _ComicsDB
                import os as _os

                def _comics_send(magnet: str, save_path: str = '') -> bool:
                    """Invia magnet al client attivo nel ciclo corrente.
                    Prova in ordine: client principale (qbt/transmission/aria2),
                    poi libtorrent embedded come fallback.
                    Assegna automaticamente il tag 'Fumetto' al torrent.
                    """
                    sent = False
                    try:
                        # Client principale già selezionato nel ciclo (qBittorrent, Transmission, Aria2)
                        if client.add(magnet, cfg.qbt):
                            sent = True
                    except Exception as _ce:
                        logger.warning(f"⚠️ Comics send error (main client): {_ce}")
                    if not sent:
                        try:
                            # Fallback: libtorrent embedded
                            from core.clients.libtorrent import LibtorrentClient
                            if LibtorrentClient.session_available():
                                sent = LibtorrentClient.add_magnet(magnet, save_path)
                        except Exception as _le:
                            logger.warning(f"⚠️ Comics send error (libtorrent): {_le}")
                    if sent:
                        # Auto-tag: assegna 'Fumetto' sia al client torrent sia alla UI
                        try:
                            tagger.tag_torrent(magnet, TAG_COMIC)
                        except Exception as _te:
                            logger.debug(f"Comics tag_torrent: {_te}")
                        try:
                            _ui_tag(magnet, TAG_COMIC)
                        except Exception as _ute:
                            logger.debug(f"Comics _ui_tag: {_ute}")
                    return sent

                result = run_comics_cycle(
                    send_magnet_fn = _comics_send,
                    logger_fn      = logger.info,
                    notify_fn      = notifier.notify_comic if hasattr(notifier, 'notify_comic') else None
                )
                if result['sent'] > 0:
                    logger.info(f"📚 Comics: {result['sent']} new downloads started")
                if result['errors']:
                    for err in result['errors'][:5]:
                        logger.warning(f"⚠️ Comics: {err}")
            except ImportError:
                logger.warning("⚠️ comics.py module not found, comics cycle skipped")
            except Exception as _comics_err:
                logger.warning(f"⚠️ Comics cycle error: {_comics_err}")

        # 5b. Backup Automatico (Gestione Interna)
        backup_schedule = str(getattr(cfg, 'backup_schedule', 'manual')).lower()
        if backup_schedule in ['daily', 'weekly']:
            backup_interval = 86400 if backup_schedule == 'daily' else 604800
            try:
                import glob
                backup_dir = str(getattr(cfg, 'backup_dir', 'backups')).strip()
                existing_backups = sorted(glob.glob(os.path.join(backup_dir, 'extto-backup-*.zip')), reverse=True)
                last_bkp_time = os.path.getmtime(existing_backups[0]) if existing_backups else 0
            except Exception as e:
                logger.debug(f"backup last_time: {e}")
                last_bkp_time = 0
                
            if (time.time() - last_bkp_time) >= backup_interval:
                logger.info(f"🗂️ Automatic backup trigger detected ({backup_schedule}). Starting in background...")
                def _run_bg_backup():
                    try:
                        import requests
                        _web_port = int(getattr(cfg, 'web_port', 5000))
                        
                        # 1. SICUREZZA: Crea prima il file ZIP di Backup (sia locale che Cloud)
                        requests.post(f'http://127.0.0.1:{_web_port}/api/backup/run', timeout=600)
                        
                        # 2. PULIZIA: Lancia il VACUUM su entrambi i database per recuperare i Megabyte
                        logger.info("🧹 Starting automatic database optimization (VACUUM and ANALYZE)...")
                        requests.post(f'http://127.0.0.1:{_web_port}/api/db/action', json={'action': 'VACUUM', 'target': 'both'}, timeout=300)
                        
                        # 3. VELOCITÀ: Lancia ANALYZE per ricalcolare gli indici di ricerca
                        requests.post(f'http://127.0.0.1:{_web_port}/api/db/action', json={'action': 'ANALYZE', 'target': 'both'}, timeout=120)
                        
                    except Exception as e:
                        logger.warning(f"⚠️ Backup and Maintenance routine error: {e}")
                
                threading.Thread(target=_run_bg_backup, daemon=True).start()

        # 5c. Pulizia Automatica Cestino
        trash_retention_raw = str(getattr(cfg, 'trash_retention_days', '')).strip()
        if trash_retention_raw:
            try:
                trash_days = int(trash_retention_raw)
                trash_path = str(getattr(cfg, 'trash_path', '')).strip()
                if trash_days > 0 and trash_path and os.path.exists(trash_path):
                    cutoff = time.time() - trash_days * 86400
                    deleted = 0
                    freed = 0
                    for fname in os.listdir(trash_path):
                        fpath = os.path.join(trash_path, fname)
                        if not os.path.isfile(fpath):
                            continue
                        try:
                            if os.path.getmtime(fpath) < cutoff:
                                freed += os.path.getsize(fpath)
                                os.remove(fpath)
                                deleted += 1
                        except Exception as e:
                            logger.debug(f"trash cleanup remove: {e}")
                            pass
                    if deleted > 0:
                        logger.info(f"🗑️ Trash cleanup: removed {deleted} files ({round(freed/1024/1024,1)} MB freed, older than {trash_days} days)")
            except (ValueError, Exception):
                pass

        # --- NUOVO: STAMPA IL DETTAGLIO ERRORI A FINE CICLO ---
        if error_catcher.captured_errors:
            logger.info("📝 --- Cycle Error Details ---")
            unique_errs = list(dict.fromkeys(error_catcher.captured_errors)) # Rimuove i doppioni per non intasare
            for err in unique_errs[:15]:
                logger.info(f"   ↳ {err}")
            if len(unique_errs) > 15:
                logger.info(f"   ↳ ... and {len(unique_errs)-15} more issues detected.")
        # ------------------------------------------------------

        # Pulisce tutti i trigger file usati in questo ciclo
        for _tf in ('/tmp/extto_run_now', '/tmp/extto_run_series',
                    '/tmp/extto_run_movies', '/tmp/extto_run_comics'):
            if os.path.exists(_tf):
                try:
                    os.remove(_tf)
                except Exception as e:
                    logger.debug(f"trigger file remove: {e}")
                    pass
        
        # --- NUOVO: CONTROLLO SALUTE E NOTIFICHE (Disco e Sovraccarico) ---
        try:
            import psutil
            import shutil
            
            # 1. Controllo Spazio Disco (nella cartella dei download)
            dl_dir = cfg.qbt.get('libtorrent_dir', '/downloads')
            if os.path.exists(dl_dir):
                total, used, free = shutil.disk_usage(dl_dir)
                free_gb = free / (1024**3)
                # Legge la soglia dalle tue impostazioni avanzate (default: 10 GB)
                min_gb = float(getattr(cfg, 'min_free_space_gb', 10) or 10)
                
                if free_gb <= min_gb:
                    # Invia la notifica max 1 volta ogni 24 ore per non spammare
                    if (time.time() - last_disk_alert) > 86400:
                        logger.warning(f"⚠️ Low disk space on {dl_dir}: {free_gb:.1f} GB remaining.")
                        notifier.notify_system_event('warning', f"⚠️ *Spazio Disco in Esaurimento!*\n\nLa cartella principale ha solo *{free_gb:.1f} GB* liberi (Soglia: {min_gb} GB). Libera spazio per evitare il blocco dei download.")
                        last_disk_alert = time.time()
                else:
                    # Se l'utente ha fatto pulizia, riarmiamo l'allarme
                    last_disk_alert = 0

            # 2. Controllo Sovraccarico Sistema (Load Average a 5 minuti)
            if hasattr(os, 'getloadavg'):
                load1, load5, load15 = os.getloadavg()
                cores = psutil.cpu_count() or 1
                
                # Se il carico medio degli ultimi 5 minuti supera il 95% della capacità totale dei core
                if load5 > (cores * 0.95):
                    # Invia la notifica max 1 volta ogni 6 ore
                    if (time.time() - last_load_alert) > 21600:
                        ram_pct = psutil.virtual_memory().percent
                        logger.warning(f"🔥 System under heavy load. Load5: {load5:.1f} (Cores: {cores}). RAM: {ram_pct}%")
                        notifier.notify_system_event('warning', f"🔥 *Sovraccarico Sistema Rilevato!*\n\nIl server è sotto sforzo da diversi minuti.\n• *Carico CPU (5m):* {load5:.2f} / {cores} core\n• *Uso RAM:* {ram_pct}%")
                        last_load_alert = time.time()
                else:
                    last_load_alert = 0
                    
        except Exception as e:
            logger.debug(f"System health check error: {e}")
        # ------------------------------------------------------------------
        
        
        # 6. Smart sleep + Sotto-ciclo RSS (Ogni 30 minuti)
        try:
            raw_ref = int(getattr(cfg, 'refresh_interval', 120))
            # Retrocompatibilità: Se il valore è > 1000, l'utente ce l'ha ancora in secondi
            current_refresh = raw_ref * 60 if raw_ref < 1000 else raw_ref
        except Exception as e:
            logger.debug(f"current_refresh parse: {e}")
            current_refresh = 7200
            
        logger.info(f"💤 Waiting for next Full Scan ({current_refresh/60:.0f}min)...")
        start_sleep = time.time()
        last_rss_check = time.time()
        RSS_INTERVAL = 1800 # 30 minuti in secondi
        
        while (time.time() - start_sleep) < current_refresh:
            if os.path.exists(TRIGGER_FILE) or os.path.exists(TRIGGER_SERIES) or \
               os.path.exists(TRIGGER_MOVIES) or os.path.exists(TRIGGER_COMICS):
                logger.info("⏭️  Trigger detected: starting immediate cycle!")
                break
            start_server_watchdog()
            
            # --- START SOTTO-CICLO RSS ---
            if (time.time() - last_rss_check) >= RSS_INTERVAL:
                last_rss_check = time.time()
                # Ricarica config solo se il ciclo principale è terminato > 5 min fa
                # (evita doppia lettura DB se RSS scatta subito dopo il ciclo)
                _cfg_age = time.time() - getattr(cfg, '_loaded_at', 0)
                cfg_live = cfg if _cfg_age < 300 else Config()
                from core.engine import Engine as _Eng
                if _Eng._get_indexers(cfg_live):   # FIX-D: attivo se almeno un indexer configurato
                    logger.info("⚡ Starting RSS Scan (Jackett/Prowlarr Fast Check)...")
                    try:
                        rss_items = eng.get_jackett_rss({})  # FIX-D: config ignorato, usa Config()
                        if rss_items:
                            logger.info(f"   ↳ {len(rss_items)} new releases analyzed.")
                            # Salva in archive.db solo se abilitato per la sorgente
                            _rss_to_arch = [r for r in rss_items if not (
                                ('Jackett' in r.get('source','') and not cfg_live.jackett_save_to_archive) or
                                ('Prowlarr' in r.get('source','') and not cfg_live.prowlarr_save_to_archive)
                            )]
                            if _rss_to_arch:
                                eng.archive.save_batch(_rss_to_arch)
                            
                            # Valutazione diretta delle release RSS (molto più veloce e risolve il bug dei timeframe=0)
                            fast_s_up = 0
                            fast_m_up = 0
                            
                            for item in rss_items:
                                # 1. SERIE TV
                                ep = Parser.parse_series_episode(item['title'])
                                if ep:
                                    match = cfg_live.find_series_match(ep['name'], ep['season'])
                                    if match and cfg_live._lang_ok(item['title'], match.get('language', match.get('lang', 'ita'))):
                                        min_r = cfg_live._min_res_from_qual_req(match.get('quality', match.get('qual', '')))
                                        max_r = cfg_live._max_res_from_qual_req(match.get('quality', match.get('qual', '')))
                                        this_r = cfg_live._res_rank_from_title(item['title'])
                                        
                                        if min_r <= this_r <= max_r:
                                            ep_dict = {
                                                'type': 'series', 'name': match['name'], 'season': ep['season'],
                                                'episode': ep['episode'], 'episode_range': ep.get('episode_range', []),
                                                'is_pack': bool(ep.get('episode_range') and len(ep.get('episode_range', [])) > 1),
                                                'quality': ep['quality'], 'title': ep['title'],
                                                'archive_path': match.get('archive_path', '')
                                            }
                                            safe_mag = sanitize_magnet(item['magnet'], item['title']) or item['magnet']
                                            
                                            # Check Series gestisce l'inserimento, l'upgrade o i Season Pack
                                            dl, msg = db.check_series(ep_dict, safe_mag, '')
                                            if dl:
                                                ok_send, used_cl = _send_with_fallback(safe_mag)
                                                if not ok_send:
                                                    _rss_sid = _resolve_series_id(match['name'])
                                                    if _rss_sid:
                                                        db.undo_episode_send(_rss_sid, ep['season'], ep['episode'], safe_mag)
                                                    logger.warning(f"⚠️  Send failed (fast-rss): {match['name']} S{ep['season']:02d}E{ep['episode']:02d} — DB record removed")
                                                if ok_send:
                                                    fast_s_up += 1
                                                    ep_label = f"S{ep['season']:02d}E{ep['episode']:02d}" if not ep_dict['is_pack'] else f"S{ep['season']:02d} PACK"
                                                    logger.info(f"   ✅ DOWNLOAD FAST-RSS [{used_cl}]: {match['name']} {ep_label}")
                                                    notifier.notify_download(match['name'], ep['season'], ep['episode'], item['title'], ep['quality'].score() + cfg_live.get_custom_score(item['title']), "rss-fast")
                                                    tagger.tag_torrent(safe_mag, TAG_SERIES)
                                                    _ui_tag(safe_mag, TAG_SERIES)
                                continue
                                
                                # 2. FILM
                                mov = Parser.parse_movie(item['title'])
                                if mov:
                                    match = cfg_live.find_movie_match(mov['name'], mov['year'])
                                    if match and cfg_live._lang_ok(item['title'], match.get('language', match.get('lang', 'ita'))):
                                        mov['config_name'] = match['name']
                                        safe_mag = sanitize_magnet(item['magnet'], item['title']) or item['magnet']
                                        dl, msg = db.check_movie(mov, safe_mag, match.get('quality', match.get('qual', '')))
                                        if dl:
                                            ok_send, used_cl = _send_with_fallback(safe_mag)
                                            if ok_send:
                                                fast_m_up += 1
                                                score = mov['quality'].score() + cfg_live.get_custom_score(item['title'])
                                                logger.info(f"   🚀 MOVIE FAST-RSS [{used_cl}]: {match['name']} (Score: {score})")
                                                notifier.notify_movie(match['name'], mov['year'], item['title'], score)
                                                tagger.tag_torrent(safe_mag, TAG_FILM)
                                                _ui_tag(safe_mag, TAG_FILM)
                                                
                            if fast_s_up > 0 or fast_m_up > 0:
                                logger.info(f"   🎉 New downloads sent from RSS! Series: {fast_s_up}, Movies: {fast_m_up}")
                                
                            # Processa comunque in coda eventuali Timeframe scaduti naturalmente
                            for r in db.get_ready_downloads():
                                if db.begin_downloading(r['id']):
                                    s_mag = sanitize_magnet(r['best_magnet'], r['best_title']) or r['best_magnet']
                                    ok_send, used_cl = _send_with_fallback(s_mag)
                                    if ok_send:
                                        db.mark_downloaded(r['id'])
                                        logger.info(f"   ✅ DOWNLOAD TIMEFRAME EXPIRED: {r['series_name']} S{r['season']:02d}E{r['episode']:02d}")
                                        notifier.notify_download(r['series_name'], r['season'], r['episode'], r['best_title'], r['best_quality_score'], "timeframe")
                    except Exception as e:
                        logger.warning(f"   ⚠️  RSS Scan error: {e}")
            # --- END SOTTO-CICLO RSS ---

            time.sleep(5)

if __name__ == "__main__":
    # --- 1. NOTIFICA DI AVVIO ---
    try:
        from core import Config, Notifier, logger
        cfg = Config()
        notifier = Notifier({
            'notify_telegram':    cfg.notify_telegram,
            'telegram_bot_token': cfg.telegram_bot_token,
            'telegram_chat_id':   cfg.telegram_chat_id,
            'notify_email':       cfg.notify_email,
            'email_smtp':         cfg.email_smtp,
            'email_from':         cfg.email_from,
            'email_to':           cfg.email_to,
            'email_password':     cfg.email_password,
        })
        notifier.notify_system_event('startup', 'Il servizio EXTTO è partito ed è operativo in background.')
    except Exception as e:
        logger.warning(f"notify startup: {e}")
        pass # Ignora se config o rete non sono ancora pronti
        
    # --- 2. ESECUZIONE MOTORE E CATTURA CRASH ---
    try:
        main()
    except KeyboardInterrupt:
        try:
            from core import logger
            logger.info("🛑 EXTTO stopped by user.")
        except: pass
        print("\nClean exit.") # <--- MODIFICATO QUI
    except Exception as fatal_error:
        import traceback
        error_details = traceback.format_exc()
        try:
            from core import logger
            logger.critical(f"❌ FATAL SYSTEM CRASH:\n{error_details}")
        except: pass
        
        # Prova a inviare la notifica di morte un istante prima di chiudersi
        try:
            from core import Config, Notifier
            cfg = Config()
            notifier = Notifier({
                'notify_telegram':    cfg.notify_telegram,
                'telegram_bot_token': cfg.telegram_bot_token,
                'telegram_chat_id':   cfg.telegram_chat_id,
                'notify_email':       cfg.notify_email,
                'email_smtp':         cfg.email_smtp,
                'email_from':         cfg.email_from,
                'email_to':           cfg.email_to,
                'email_password':     cfg.email_password,
            })
            short_error = str(error_details)[-1500:] # Taglia se troppo lungo per Telegram
            notifier.notify_system_event('crash', f"Eccezione non gestita:\n\n{short_error}")
        except Exception as e:
            logger.debug(f"notify crash: {e}")
            pass
        
        raise # Rilancia l'errore a systemd per innescare il riavvio automatico
