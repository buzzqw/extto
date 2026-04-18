"""
EXTTO - Client aria2 (RPC JSON e CLI fallback) con Watchdog e Post-Processing Integrati.

Il Watchdog monitora i GID aria2 via RPC ogni 5 secondi. Al completamento esegue
la stessa pipeline di extto3.py: rinomina (TMDB), spostamento su archive_path,
notifica Telegram, anti-duplicati su DB.
"""

import os
import random
import re
import shutil
import threading
import time
from ..constants import logger


class Aria2Client:
    """Supporto aria2 come client principale o fallback.
    - Se configurato `aria2_rpc_url`, usa JSON-RPC (`aria2.addUri`) e avvia il Watchdog.
    - Altrimenti prova `aria2c` via CLI (se presente nel PATH).
    """

    # Singleton watchdog: un solo thread per processo
    _watchdog_thread = None
    _watchdog_lock   = threading.Lock()
    _instance        = None   # istanza maestra che possiede i GID pendenti

    def __init__(self, cfg: dict):
        self.cfg         = cfg or {}
        self.enabled     = str(self.cfg.get('aria2_enabled', 'no')).lower() in ('yes', 'true', '1')
        self.rpc_url     = self.cfg.get('aria2_rpc_url', '').strip()
        self.rpc_secret  = self.cfg.get('aria2_rpc_secret', '').strip()
        self.dir         = self.cfg.get('aria2_dir', '').strip()
        self.aria2c_path = self.cfg.get('aria2c_path', 'aria2c').strip()

        # Se aria2_rpc_url non e' nel cfg (scritto da aria2_start dopo l'avvio),
        # lo leggiamo dal DB cosi' enabled funziona correttamente.
        if self.enabled and not self.rpc_url:
            try:
                from .. import config_db as _cdb
                self.rpc_url    = (_cdb.get_setting('aria2_rpc_url', '') or '').strip()
                self.rpc_secret = self.rpc_secret or (_cdb.get_setting('aria2_rpc_secret', '') or '').strip()
            except Exception:
                pass

        # GID e metadati: appartengono all'istanza maestra singleton
        self._pending_gids = set()
        self._gids_lock    = threading.Lock()
        self._gid_meta     = {}
        self._meta_lock    = threading.Lock()
        self._running      = False

        # Avvia il watchdog solo una volta per processo.
        # Usa threading.enumerate() per verificare se il thread esiste già
        # (funziona anche se gli attributi di classe vengono reimportati).
        if self.enabled and self.rpc_url:
            with Aria2Client._watchdog_lock:
                # Cerca un thread con questo nome già in esecuzione
                _alive = any(
                    t.name == 'aria2-watchdog' and t.is_alive()
                    for t in threading.enumerate()
                )
                if not _alive:
                    Aria2Client._instance = self
                    self._running = True
                    t = threading.Thread(
                        target=self._watchdog_loop,
                        daemon=True,
                        name='aria2-watchdog'
                    )
                    t.start()
                    Aria2Client._watchdog_thread = t
                    logger.info("Aria2 Watchdog avviato.")
                elif Aria2Client._instance is None:
                    # Thread vivo ma istanza maestra persa (reimport): recupera
                    Aria2Client._instance = self

    # ------------------------------------------------------------------
    # Helper RPC
    # ------------------------------------------------------------------
    def _rpc_params(self, *args):
        """Costruisce lista params con token opzionale in testa."""
        params = []
        if self.rpc_secret:
            params.append(f"token:{self.rpc_secret}")
        params.extend(args)
        return params

    def _rpc_post(self, method: str, *args, id_val=None) -> dict:
        import requests
        payload = {
            'jsonrpc': '2.0',
            'id':      id_val or random.randint(1, 999999),
            'method':  method,
            'params':  self._rpc_params(*args),
        }
        resp = requests.post(self.rpc_url, json=payload, timeout=5)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # RPC add
    # ------------------------------------------------------------------
    def _rpc_add(self, uri: str, meta: dict = None) -> bool:
        if not self.rpc_url:
            return False
        try:
            opts = {
                'max-connection-per-server': str(self.cfg.get('aria2_max_connection', '16')),
                'split':                     str(self.cfg.get('aria2_split', '16')),
                'max-download-limit':        f"{self.cfg.get('aria2_dl_limit', '0')}K",
                'max-upload-limit':          f"{self.cfg.get('aria2_ul_limit', '0')}K",
                'min-split-size':            '1M',
                'seed-time':                 '0',
            }
            if self.dir:
                opts['dir'] = self.dir

            j = self._rpc_post('aria2.addUri', [uri], opts)
            if 'result' in j:
                gid = j['result']
                with self._gids_lock:
                    self._pending_gids.add(gid)
                if meta:
                    with self._meta_lock:
                        self._gid_meta[gid] = meta
                logger.info(f"+ Aria2: Aggiunto GID {gid} — {(meta or {}).get('title', uri[:60])}")
                return True
            return False
        except Exception as e:
            logger.error(f"Aria2 _rpc_add error: {e}")
            return False

    # ------------------------------------------------------------------
    # CLI fallback
    # ------------------------------------------------------------------
    def _cli_add(self, uri: str) -> bool:
        try:
            import shutil as _sh
            import subprocess as _sp
            if not _sh.which(self.aria2c_path):
                logger.warning(f"Aria2: aria2c non trovato in PATH ({self.aria2c_path})")
                return False
            args = [
                self.aria2c_path,
                '--continue=true',
                '--max-connection-per-server=16',
                '--split=16',
                '--min-split-size=1M',
                '--seed-time=0',
            ]
            if self.dir:
                args += ['--dir', self.dir]
            args.append(uri)
            _sp.Popen(args, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            logger.info("Aria2: download avviato via CLI (post-processing non disponibile senza RPC)")
            return True
        except Exception as e:
            logger.error(f"Aria2 _cli_add error: {e}")
            return False

    def add(self, uri: str, cfg_unused: dict, meta: dict = None) -> bool:
        """Aggiunge un download.
        meta (opzionale): dict con le chiavi
            series_name, season, episode, archive_path,
            tmdb_id, title, notifier, db, cfg_dict
        passato da extto3 al momento dell'invio.
        Se questa istanza e' "leggera" (senza watchdog), delega alla maestra
        per garantire il tracciamento GID.
        """
        master = Aria2Client._instance
        if master is not None and master is not self and master._running:
            return master.add(uri, cfg_unused, meta)
        if self.rpc_url and self._rpc_add(uri, meta):
            return True
        return self._cli_add(uri)

    # ------------------------------------------------------------------
    # API torrent management (list/pause/resume/remove) — invariate
    # ------------------------------------------------------------------
    def list_torrents(self) -> list:
        if not self.rpc_url:
            return []
        try:
            active  = self._rpc_post('aria2.tellActive').get('result', [])
            waiting = self._rpc_post('aria2.tellWaiting', 0, 100).get('result', [])
            stopped = self._rpc_post('aria2.tellStopped', 0, 100).get('result', [])
            out = []
            for t in active + waiting + stopped:
                name = (t.get('bittorrent', {}).get('info', {}).get('name', '')
                        or os.path.basename((t.get('files') or [{}])[0].get('path', '')))
                total    = int(t.get('totalLength', 0))
                done     = int(t.get('completedLength', 0))
                uploaded = int(t.get('uploadLength', 0))
                dl_speed = int(t.get('downloadSpeed', 0))
                ul_speed = int(t.get('uploadSpeed', 0))
                progress = (done / total) if total > 0 else 0.0
                status   = t.get('status', 'unknown')   # active/waiting/paused/complete/error/removed

                # ETA in secondi (campo usato dal frontend come torr.eta)
                remaining = total - done
                eta = (remaining // dl_speed) if (dl_speed > 0 and remaining > 0) else 0

                # Ratio upload/download
                ratio = (uploaded / done) if done > 0 else 0.0

                # Mappa stato aria2 → stringa leggibile dal frontend
                _state_map = {
                    'active':   'downloading',
                    'waiting':  'queued',
                    'paused':   'paused',
                    'complete': 'seeding',
                    'error':    'error',
                    'removed':  'error',
                }

                out.append({
                    'hash':               t.get('gid', ''),
                    'name':               name,
                    'state':              _state_map.get(status, status),
                    'progress':           progress,
                    'total_size':         total,
                    'downloaded':         done,
                    '_dlBytes':           done,
                    '_ulBytes':           uploaded,
                    'dl_rate':            dl_speed,
                    'ul_rate':            ul_speed,
                    'eta':                eta,
                    'ratio':              ratio,
                    'num_seeds':          int(t.get('numSeeders', 0)),
                    'num_peers':          int(t.get('connections', 0)),
                    'paused':             status == 'paused',
                    'error':              status in ('error', 'removed'),
                    'save_path':          t.get('dir', self.dir),
                    'physical_file_found': False,
                })
            return out
        except Exception as e:
            logger.debug(f"Aria2 list_torrents: {e}")
            return []

    def pause_torrent(self, gid: str) -> bool:
        try:
            self._rpc_post('aria2.pause', gid)
            return True
        except Exception as e:
            logger.debug(f"Aria2 pause: {e}")
            return False

    def resume_torrent(self, gid: str) -> bool:
        try:
            self._rpc_post('aria2.unpause', gid)
            return True
        except Exception as e:
            logger.debug(f"Aria2 resume: {e}")
            return False

    def remove_torrent(self, gid: str, delete_files: bool = False) -> bool:
        try:
            self._rpc_post('aria2.remove', gid)
            return True
        except Exception as e:
            logger.debug(f"Aria2 remove: {e}")
            return False

    # =========================================================================
    # WATCHDOG
    # =========================================================================

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        elif n < 1024 ** 2:
            return f"{n / 1024:.1f} KB"
        elif n < 1024 ** 3:
            return f"{n / 1024 ** 2:.1f} MB"
        return f"{n / 1024 ** 3:.2f} GB"

    @staticmethod
    def _fmt_eta(remaining: int, speed: int) -> str:
        if speed <= 0 or remaining <= 0:
            return ''
        secs = remaining // speed
        if secs < 60:
            return f" (ETA {secs}s)"
        elif secs < 3600:
            return f" (ETA {secs // 60}m {secs % 60:02d}s)"
        h = secs // 3600
        m = (secs % 3600) // 60
        return f" (ETA {h}h {m:02d}m)"

    def _watchdog_loop(self):
        _last_pct: dict = {}   # gid -> ultimo % loggato (int), -1 = mai loggato

        while self._running:
            try:
                with self._gids_lock:
                    gids_snapshot = set(self._pending_gids)

                if not gids_snapshot:
                    _last_pct.clear()
                    time.sleep(5)
                    continue

                # Pulisci GID non più monitorati
                for gone in set(_last_pct) - gids_snapshot:
                    _last_pct.pop(gone, None)

                for gid in gids_snapshot:
                    try:
                        j      = self._rpc_post('aria2.tellStatus', gid, id_val='watchdog')
                        data   = j.get('result', {})
                        status = data.get('status')

                        total    = int(data.get('totalLength', 0))
                        done     = int(data.get('completedLength', 0))
                        speed    = int(data.get('downloadSpeed', 0))

                        # Nome breve per i log
                        bt_name = data.get('bittorrent', {}).get('info', {}).get('name', '')
                        if not bt_name:
                            files = data.get('files', [{}])
                            bt_name = os.path.basename(files[0].get('path', '')) if files else gid
                        label = (bt_name[:55] + '…') if len(bt_name) > 55 else bt_name

                        if status == 'complete':
                            if _last_pct.get(gid, -1) < 100:
                                size_str = self._fmt_bytes(total) if total else '?'
                                logger.info(f"Aria2 [{gid}] 100% completato — {label} ({size_str})")
                            _last_pct.pop(gid, None)
                            with self._gids_lock:
                                self._pending_gids.discard(gid)
                            with self._meta_lock:
                                meta = self._gid_meta.pop(gid, None)
                            self._trigger_post_processing(data, meta)

                        elif status in ('error', 'removed'):
                            logger.warning(f"Aria2 [{gid}] stato '{status}' — {label}")
                            _last_pct.pop(gid, None)
                            with self._gids_lock:
                                self._pending_gids.discard(gid)
                            with self._meta_lock:
                                self._gid_meta.pop(gid, None)

                        elif status in ('active', 'waiting', 'paused'):
                            if total > 0:
                                pct = int(done * 100 / total)
                                prev = _last_pct.get(gid, -1)
                                # Prima volta (prev=-1) oppure si è superato un multiplo di 5%
                                if prev < 0 or (pct // 5) > (prev // 5):
                                    speed_str = f" @ {self._fmt_bytes(speed)}/s" if speed > 0 else ""
                                    eta_str   = self._fmt_eta(total - done, speed)
                                    logger.info(
                                        f"Aria2 [{gid}] {pct:3d}%{speed_str}{eta_str} — {label}"
                                    )
                                    _last_pct[gid] = pct
                            else:
                                # Dimensione non ancora nota (metadata non risolto)
                                if gid not in _last_pct:
                                    logger.info(f"Aria2 [{gid}] in attesa metadata — {label}")
                                    _last_pct[gid] = 0

                    except Exception as e:
                        logger.debug(f"Watchdog GID {gid}: {e}")

            except Exception as e:
                logger.debug(f"Watchdog loop: {e}")

            time.sleep(5)

    # =========================================================================
    # POST-PROCESSING — stessa pipeline di extto3.py
    # =========================================================================
    def _trigger_post_processing(self, data: dict, meta: dict):
        """
        Esegue rinomina (TMDB), spostamento su archive_path e notifica.
        Segue la stessa logica del blocco post-processing di extto3.py.

        data: risposta aria2.tellStatus (con 'dir', 'files', 'bittorrent')
        meta: dict passato da extto3 al momento dell'add(), con chiavi:
              series_name, season, episode, archive_path, tmdb_id,
              title, notifier, db, cfg_dict
              (None se il download non è tracciato — es. CLI o fallback)
        """
        try:
            # --- Ricava nome e percorso dal risultato aria2 ---
            bittorrent = data.get('bittorrent', {})
            t_name = bittorrent.get('info', {}).get('name', '')
            if not t_name:
                files = data.get('files', [])
                if files and files[0].get('path'):
                    t_name = os.path.basename(files[0]['path'])
            save_dir = data.get('dir', self.dir)

            if not t_name:
                logger.warning("Aria2 post-processing: nome file non determinabile, skip.")
                return

            logger.info(f"Aria2 post-processing: '{t_name}' in '{save_dir}'")

            # --- Carica cfg se non già in meta ---
            cfg_dict = (meta or {}).get('cfg_dict') or self.cfg

            do_move   = str(cfg_dict.get('move_episodes', 'no')).lower() in ('yes', 'true', '1')
            do_rename = str(cfg_dict.get('rename_episodes', 'no')).lower() in ('yes', 'true', '1')
            api_key   = str(cfg_dict.get('tmdb_api_key', '')).strip()
            rename_fmt      = str(cfg_dict.get('rename_format', 'base')).lower()
            rename_template = str(cfg_dict.get('rename_template',
                                               '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}]'))

            if not (do_move or do_rename):
                logger.info("Aria2 post-processing: rinomina e spostamento disabilitati, skip.")
                return

            from ..renamer import _build_filename, _VIDEO_EXTS, rename_completed_torrent

            notifier = (meta or {}).get('notifier')
            db       = (meta or {}).get('db')

            # --- Recupera archive_path e metadati serie ---
            archive_path = (meta or {}).get('archive_path', '').strip()
            series_name  = (meta or {}).get('series_name', '')
            season       = (meta or {}).get('season')
            episode      = (meta or {}).get('episode')
            tmdb_id      = (meta or {}).get('tmdb_id')

            # Se meta non disponibile (fallback/CLI), prova a matchare dal nome del file
            if not series_name or not archive_path:
                try:
                    from ..models import Parser
                    from ..config import Config
                    ep = Parser.parse_series_episode(t_name)
                    if ep:
                        cfg_obj = Config()
                        match   = cfg_obj.find_series_match(ep['name'], ep['season'])
                        if match:
                            series_name  = match.get('name', ep['name'])
                            season       = ep['season']
                            episode      = ep['episode']
                            archive_path = match.get('archive_path', '')
                            tmdb_id      = match.get('tmdb_id')
                except Exception as e:
                    logger.debug(f"Aria2 post-proc: match serie fallito: {e}")

            # --- Trova il file video nella cartella di download ---
            t_path = os.path.join(save_dir, t_name)
            video_files = []
            if os.path.isdir(t_path):
                for root, _, files in os.walk(t_path):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in _VIDEO_EXTS and 'sample' not in f.lower():
                            video_files.append(os.path.join(root, f))
            elif os.path.isfile(t_path) and os.path.splitext(t_path)[1].lower() in _VIDEO_EXTS:
                video_files.append(t_path)

            if not video_files:
                logger.warning(f"Aria2 post-proc: nessun file video trovato in '{t_path}'")
                # Prova comunque rename_completed_torrent se archive_path è noto
                if archive_path and os.path.isdir(archive_path) and do_rename and api_key:
                    rename_completed_torrent(t_name, archive_path, cfg_dict, db)
                return

            action_log = []
            is_processed = False

            # --- Serie TV: spostamento + rinomina ---
            if series_name and season is not None and episode is not None and archive_path:
                if not os.path.isdir(archive_path):
                    logger.warning(f"Aria2 post-proc: archive_path non esiste: '{archive_path}'")
                    return

                video_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
                src_file = video_files[0]
                _, ext   = os.path.splitext(src_file)
                final_name = os.path.basename(src_file)
                renamed_to = None

                # Rinomina con TMDB
                if do_rename and api_key:
                    try:
                        from ..tmdb import TMDBClient
                        tmdb = TMDBClient(api_key, cache_days=7)
                        if not tmdb_id:
                            tmdb_id = tmdb.resolve_series_id(series_name)
                        ep_title = tmdb.fetch_episode_title(tmdb_id, season, episode) if tmdb_id else None
                        final_name = _build_filename(
                            series_name, season, episode, ep_title, ext,
                            fmt=rename_fmt, template_str=rename_template
                        )
                        renamed_to = final_name
                        action_log.append(f"✏️ <b>Rinominato in:</b>\n<code>{final_name}</code>")
                    except Exception as e:
                        logger.warning(f"Aria2 post-proc: _build_filename fallito: {e}")
                        action_log.append("⚠️ Rinomina fallita (uso nome originale)")

                # Sottocartella stagione
                try:
                    from ..config import Config as _Cfg
                    _match = _Cfg().find_series_match(series_name, season)
                    use_season_subdir = (_match or {}).get('season_subfolders', False)
                except Exception:
                    use_season_subdir = False

                if use_season_subdir:
                    target_dir = os.path.join(archive_path, f"Stagione {season}")
                    os.makedirs(target_dir, exist_ok=True)
                else:
                    target_dir = archive_path

                dst_file = os.path.join(target_dir, final_name)

                if do_move and not os.path.exists(dst_file):
                    shutil.move(src_file, dst_file)
                    action_log.append(f"📁 <b>Archiviato in:</b>\n<code>{dst_file}</code>")
                    is_processed = True
                    logger.info(f"Aria2 post-proc: spostato → '{dst_file}'")

                    # Rimuovi cartella vuota residua
                    if os.path.isdir(t_path) and not os.listdir(t_path):
                        shutil.rmtree(t_path, ignore_errors=True)

                    # Aggiorna DB anti-amnesia
                    if db:
                        try:
                            from ..models import Parser
                            t_q     = Parser.parse_quality(t_name)
                            t_score = t_q.score() if hasattr(t_q, 'score') else 0
                            now_iso = __import__('datetime').datetime.now(
                                __import__('datetime').timezone.utc).isoformat()
                            c_db = db.conn.cursor()
                            c_db.execute("SELECT id FROM series WHERE name=?", (series_name,))
                            s_row = c_db.fetchone()
                            if s_row:
                                s_id = s_row['id'] if isinstance(s_row, dict) else s_row[0]
                                c_db.execute(
                                    "SELECT id, quality_score FROM episodes WHERE series_id=? AND season=? AND episode=?",
                                    (s_id, season, episode)
                                )
                                row_ep = c_db.fetchone()
                                if row_ep:
                                    old_score = (row_ep['quality_score'] if isinstance(row_ep, dict) else row_ep[1]) or 0
                                    if old_score < t_score:
                                        ep_id = row_ep['id'] if isinstance(row_ep, dict) else row_ep[0]
                                        c_db.execute(
                                            "UPDATE episodes SET quality_score=?, downloaded_at=?, title=? WHERE id=?",
                                            (t_score, now_iso, t_name, ep_id)
                                        )
                                else:
                                    c_db.execute(
                                        "INSERT INTO episodes (series_id, season, episode, title, quality_score, downloaded_at) VALUES (?,?,?,?,?,?)",
                                        (s_id, season, episode, t_name, t_score, now_iso)
                                    )
                                db.conn.commit()
                        except Exception as db_err:
                            logger.debug(f"Aria2 post-proc DB update: {db_err}")

                # Notifica
                if notifier and (is_processed or series_name):
                    series_name_disp = f"{series_name} S{season:02d}E{episode:02d}"
                    size_bytes = int(data.get('totalLength', 0)) or os.path.getsize(dst_file if is_processed else src_file)
                    if hasattr(notifier, 'notify_post_processing'):
                        notifier.notify_post_processing(
                            series_name_disp, size_bytes, 0, action_log,
                            True, is_processed,
                            final_path=dst_file if is_processed else None,
                            renamed_to=renamed_to
                        )
                    logger.info(f"Aria2 post-processing completato: {series_name_disp}")

            else:
                # --- Film o sconosciuto: usa rename_completed_torrent come extto3 ---
                if archive_path and os.path.isdir(archive_path) and do_rename and api_key:
                    rename_completed_torrent(t_name, archive_path, cfg_dict, db)
                    logger.info(f"Aria2 post-proc film: rename_completed_torrent su '{t_name}'")
                else:
                    logger.info(f"Aria2 post-proc: nessun archive_path configurato per '{t_name}', file lasciato in '{save_dir}'")

        except Exception as e:
            logger.warning(f"Aria2 post-processing errore: {e}", exc_info=True)
