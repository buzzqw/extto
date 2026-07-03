"""
EXTTO - Client rqbit (HTTP API) con Watchdog e Post-Processing Integrati.

rqbit e' un demone BitTorrent scritto in Rust, pilotabile solo via HTTP API
(nessun binding Python, nessun webhook/evento). Il Watchdog qui sotto fa polling
via HTTP ogni 5 secondi e, al completamento, richiama le stesse routine di
post-processing gia' usate da LibtorrentClient (rename TMDB, season pack,
notifica, anti-duplicati) invece di duplicarle come fa core/clients/aria2.py:
_trigger_renamer/_trigger_folder_scan/_trigger_mediaserver_refresh/_handle_season_pack
sono classmethod "handle-agnostiche" (accettano una stringa al posto di un vero
torrent handle) e vengono richiamate direttamente da qui.

Persistenza: a differenza di aria2.py, lo stato "torrent in attesa di
post-processing" e "inizio seeding" viene scritto su disco (STATE_DIR) cosi'
un riavvio di EXTTO non perde il download appena completato ne' l'orario di
inizio seeding usato per i seed_time limits. Le info di match (serie/stagione/
episodio) NON servono in memoria: vengono sempre ricalcolate dal nome del
torrent al momento del post-processing, esattamente come fa LibtorrentClient.
"""

import os
import shutil
import threading
import time
from ..constants import logger, STATE_DIR
from ..utils import safe_load_json, safe_save_json


class RqbitClient:
    """Client rqbit via HTTP API, con watchdog di post-processing.

    Parametri configurabili via series_config (@rqbit_*):
      enabled, http_addr, dir, dl_limit, ul_limit, seed_ratio, seed_time
    """

    # Singleton watchdog: un solo thread per processo
    _watchdog_thread = None
    _watchdog_lock   = threading.Lock()
    _instance        = None   # istanza maestra che possiede gli hash pending

    # Stato condiviso tra istanze (create ad ogni ciclo come Aria2Client)
    _known_hashes  = set()   # tutti gli hash mai visti (per il routing pause/resume/remove)
    _hashes_lock   = threading.Lock()
    _pending       = set()   # info_hash non ancora post-processati (persistito su disco)
    _finish_time   = {}      # info_hash -> timestamp inizio seeding, per seed_time (persistito su disco)
    _state_loaded  = False   # True dopo il primo caricamento da disco nel processo corrente

    _PENDING_FILE = os.path.join(STATE_DIR, 'rqbit_pending.json')
    _SEED_START_FILE = os.path.join(STATE_DIR, 'rqbit_seed_start.json')
    _MAGNET_FILE  = os.path.join(STATE_DIR, 'rqbit_magnets.json')  # info_hash -> {uri, output_folder}
    _magnets      = {}
    _magnets_lock = threading.Lock()

    # Cache per num_seeds/num_peers: rinfrescata al massimo ogni 10s per hash
    # (rqbit non offre un conteggio seed/peer aggregato: va ricavato con una
    # chiamata separata a /peer_stats, che qui evitiamo di rifare ad ogni poll).
    _peer_stats_cache = {}   # info_hash -> (timestamp, num_peers)
    _peer_stats_lock  = threading.Lock()
    _PEER_STATS_TTL   = 10

    def __init__(self, cfg: dict):
        self.cfg       = cfg or {}
        self.enabled   = str(self.cfg.get('rqbit_enabled', 'no')).lower() in ('yes', 'true', '1')
        self.http_addr = self.cfg.get('rqbit_http_addr', '127.0.0.1:3030').strip()
        self.base_url  = f"http://{self.http_addr}"
        self.dir       = self.cfg.get('rqbit_dir', '').strip()
        self._running  = False

        if self.enabled:
            with RqbitClient._watchdog_lock:
                self._load_persisted_state()
                _alive = any(
                    t.name == 'rqbit-watchdog' and t.is_alive()
                    for t in threading.enumerate()
                )
                if not _alive:
                    RqbitClient._instance = self
                    self._running = True
                    t = threading.Thread(
                        target=self._watchdog_loop,
                        daemon=True,
                        name='rqbit-watchdog'
                    )
                    t.start()
                    RqbitClient._watchdog_thread = t
                    logger.info(f"rqbit Watchdog avviato ({len(RqbitClient._pending)} download in sospeso ripresi da disco).")
                elif RqbitClient._instance is None:
                    RqbitClient._instance = self

    # ------------------------------------------------------------------
    # Persistenza pending/seed-start (sopravvive al riavvio di EXTTO)
    # ------------------------------------------------------------------
    @classmethod
    def _load_persisted_state(cls):
        if cls._state_loaded:
            return
        cls._state_loaded = True
        try:
            pending = safe_load_json(cls._PENDING_FILE, default=[])
            with cls._hashes_lock:
                cls._pending.update(h.lower() for h in pending if h)
                cls._known_hashes.update(cls._pending)
        except Exception as e:
            logger.debug(f"rqbit _load_persisted_state pending: {e}")
        try:
            seed_start = safe_load_json(cls._SEED_START_FILE, default={})
            if isinstance(seed_start, dict):
                with cls._hashes_lock:
                    cls._finish_time.update({k.lower(): float(v) for k, v in seed_start.items()})
        except Exception as e:
            logger.debug(f"rqbit _load_persisted_state seed_start: {e}")
        try:
            magnets = safe_load_json(cls._MAGNET_FILE, default={})
            if isinstance(magnets, dict):
                with cls._magnets_lock:
                    cls._magnets.update({k.lower(): v for k, v in magnets.items()})
        except Exception as e:
            logger.debug(f"rqbit _load_persisted_state magnets: {e}")

    @classmethod
    def _persist_pending(cls):
        try:
            with cls._hashes_lock:
                data = list(cls._pending)
            safe_save_json(cls._PENDING_FILE, data)
        except Exception as e:
            logger.debug(f"rqbit _persist_pending: {e}")

    @classmethod
    def _persist_magnets(cls):
        try:
            with cls._magnets_lock:
                data = dict(cls._magnets)
            safe_save_json(cls._MAGNET_FILE, data)
        except Exception as e:
            logger.debug(f"rqbit _persist_magnets: {e}")

    @classmethod
    def _persist_seed_start(cls):
        try:
            with cls._hashes_lock:
                data = dict(cls._finish_time)
            safe_save_json(cls._SEED_START_FILE, data)
        except Exception as e:
            logger.debug(f"rqbit _persist_seed_start: {e}")

    # ------------------------------------------------------------------
    # Helper HTTP
    # ------------------------------------------------------------------
    def _get(self, path: str, timeout: float = 5) -> dict:
        import requests
        resp = requests.get(f"{self.base_url}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data=None, timeout: float = 8, content_type: str = 'text/plain') -> dict:
        import requests
        resp = requests.post(f"{self.base_url}{path}", data=data,
                              headers={'Content-Type': content_type}, timeout=timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {}

    @classmethod
    def is_known(cls, info_hash: str) -> bool:
        with cls._hashes_lock:
            return (info_hash or '').lower() in cls._known_hashes

    @classmethod
    def _remember(cls, info_hash: str):
        with cls._hashes_lock:
            cls._known_hashes.add((info_hash or '').lower())

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------
    def add(self, uri: str, cfg_unused: dict, meta: dict = None) -> bool:
        """Aggiunge un magnet/torrent da URI (magnet: o http/https). meta e'
        opzionale e usato solo per il messaggio di log iniziale: il
        post-processing ricalcola sempre serie/stagione/episodio dal nome del
        torrent, quindi non serve persisterlo."""
        master = RqbitClient._instance
        if master is not None and master is not self and master._running:
            return master.add(uri, cfg_unused, meta)
        if not self.enabled:
            return False
        entry = {'kind': 'magnet', 'uri': uri}
        title = (meta or {}).get('title', uri[:60])
        return self._add_common("/torrents?paused=false", uri, 'text/plain', entry, title)

    def add_torrent_bytes(self, torrent_bytes: bytes, filename: str = '') -> bool:
        """Aggiunge un file .torrent (bytes grezzi) — usato dall'upload manuale."""
        if not self.enabled:
            return False
        import base64
        entry = {'kind': 'torrent', 'data_b64': base64.b64encode(torrent_bytes).decode('ascii')}
        return self._add_common("/torrents?paused=false", torrent_bytes,
                                 'application/x-bittorrent', entry, filename or 'file.torrent')

    def _add_common(self, path: str, data, content_type: str, entry: dict, log_label: str) -> bool:
        try:
            j = self._post(path, data=data, content_type=content_type)
            info_hash = ((j.get('details') or {}).get('info_hash') or '').lower()
            if not info_hash:
                return False
            self._remember(info_hash)
            with RqbitClient._hashes_lock:
                RqbitClient._pending.add(info_hash)
            RqbitClient._persist_pending()
            # Ricorda come riaggiungere questo torrent (magnet o bytes) + output_folder:
            # serve per recheck/restart (stessa robustezza di LibtorrentClient.get_magnet_uri).
            entry['output_folder'] = j.get('output_folder', '')
            entry.setdefault('added_at', time.time())
            with RqbitClient._magnets_lock:
                RqbitClient._magnets[info_hash] = entry
            RqbitClient._persist_magnets()
            name = (j.get('details') or {}).get('name', log_label)
            logger.info(f"+ rqbit: Aggiunto {info_hash[:12]} — {name}")
            return True
        except Exception as e:
            logger.error(f"rqbit add error: {e}")
            return False

    # ------------------------------------------------------------------
    # API torrent management (list/pause/resume/remove)
    # ------------------------------------------------------------------
    def _peer_count(self, info_hash: str) -> int:
        """Numero di peer connessi (rqbit non distingue seed/leech in modo
        affidabile via API, quindi lo stesso valore viene usato per entrambi)."""
        now = time.time()
        with RqbitClient._peer_stats_lock:
            cached = RqbitClient._peer_stats_cache.get(info_hash)
            if cached and (now - cached[0]) < RqbitClient._PEER_STATS_TTL:
                return cached[1]
        try:
            j = self._get(f"/torrents/{info_hash}/peer_stats", timeout=3)
            count = len(j.get('peers') or {})
        except Exception:
            count = 0
        with RqbitClient._peer_stats_lock:
            RqbitClient._peer_stats_cache[info_hash] = (now, count)
        return count

    @staticmethod
    def _seed_limit_entry(info_hash: str) -> dict:
        from .libtorrent import LibtorrentClient
        return LibtorrentClient._get_seed_limits_db().get(info_hash.lower(), {})

    def _added_at(self, info_hash: str) -> float:
        with RqbitClient._magnets_lock:
            entry = RqbitClient._magnets.get(info_hash)
        return float(entry.get('added_at', 0)) if entry else 0.0

    @staticmethod
    def _ui_state(is_error: bool, finished: bool, is_paused: bool, dl_rate: float, ul_rate: float) -> str:
        """Stessa vocabolario italiano usato da LibtorrentClient.list_torrents()
        — _torrentStateBadge() nel frontend colora/traduce in base a queste
        esatte sottostringhe ('scarico', 'seeding', 'fermo', 'pausa', 'errore',
        'completato'), quindi stati in inglese non verrebbero riconosciuti."""
        if is_error:
            return "Errore"
        if finished:
            if is_paused:
                return "Seeding (Completato)"
            return "In Seeding" if ul_rate > 0 else "Seeding (Fermo)"
        if is_paused:
            return "In Pausa"
        return "In Scarico" if dl_rate > 0 else "In Scarico (Fermo)"

    def list_torrents(self) -> list:
        if not self.enabled:
            return []
        try:
            j = self._get("/torrents")
            out = []
            for t in j.get('torrents', []):
                info_hash = (t.get('info_hash') or '').lower()
                if not info_hash:
                    continue
                self._remember(info_hash)
                try:
                    st = self._get(f"/torrents/{info_hash}/stats/v1")
                except Exception as e:
                    logger.debug(f"rqbit stats/v1 {info_hash[:12]}: {e}")
                    continue

                total    = int(st.get('total_bytes', 0))
                done     = int(st.get('progress_bytes', 0))
                uploaded = int(st.get('uploaded_bytes', 0))
                finished = bool(st.get('finished', False))
                state    = str(st.get('state', ''))
                live     = st.get('live') or {}
                dl_speed = float((live.get('download_speed') or {}).get('mbps', 0)) * 1024 * 1024
                ul_speed = float((live.get('upload_speed') or {}).get('mbps', 0)) * 1024 * 1024
                remaining_secs = int(((live.get('time_remaining') or {}).get('duration') or {}).get('secs', 0))

                progress = (done / total) if total > 0 else 0.0
                ratio    = (uploaded / total) if total > 0 else 0.0
                is_error = bool(st.get('error'))
                is_paused = (state == 'paused')
                peers = self._peer_count(info_hash)
                ui_state = self._ui_state(is_error, finished, is_paused, dl_speed, ul_speed)

                my_lim = self._seed_limit_entry(info_hash)
                is_infinite = (my_lim.get('ratio', -1) == 0) or (my_lim.get('days', -1) == 0)

                added_at = self._added_at(info_hash)
                with RqbitClient._hashes_lock:
                    seed_started = RqbitClient._finish_time.get(info_hash)
                now = time.time()
                seeding_time = int(now - seed_started) if seed_started else 0
                active_time  = int(now - added_at) if added_at else 0

                out.append({
                    'hash':               info_hash,
                    'name':               t.get('name', ''),
                    'state':              ui_state,
                    'progress':           progress,
                    'total_size':         total,
                    'downloaded':         done,
                    '_dlBytes':           done,
                    '_ulBytes':           uploaded,
                    'dl_rate':            dl_speed,
                    'ul_rate':            ul_speed,
                    'eta':                remaining_secs,
                    'ratio':              ratio,
                    'num_seeds':          peers,
                    'num_peers':          peers,
                    'paused':             is_paused,
                    'error':              is_error,
                    'save_path':          t.get('output_folder', self.dir),
                    'physical_file_found': False,
                    'is_infinite':        is_infinite,
                    'added_time':         int(added_at),
                    'completed_time':     int(seed_started) if (finished and seed_started) else 0,
                    'active_time':        active_time,
                    'seeding_time':       seeding_time,
                })
            return out
        except Exception as e:
            logger.debug(f"rqbit list_torrents: {e}")
            return []

    def get_torrent_details(self, info_hash: str) -> dict:
        """Dettagli avanzati (File, Trackers, Info) — stesso schema di
        LibtorrentClient.get_torrent_details() per parità nel pannello Scarico."""
        info_hash = (info_hash or '').lower().strip()
        try:
            details = self._get(f"/torrents/{info_hash}")
            d = details.get('details', {}) or details
            name          = d.get('name', info_hash)
            output_folder = details.get('output_folder', self.dir)
            files_raw     = d.get('files', [])
            total_pieces  = int(d.get('total_pieces', 0))

            st = self._get(f"/torrents/{info_hash}/stats/v1")
            total     = int(st.get('total_bytes', 0))
            done      = int(st.get('progress_bytes', 0))
            uploaded  = int(st.get('uploaded_bytes', 0))
            raw_state = str(st.get('state', ''))
            finished  = bool(st.get('finished', False))
            is_error  = bool(st.get('error'))
            is_paused = (raw_state == 'paused')
            live     = st.get('live') or {}
            dl_rate  = int(float((live.get('download_speed') or {}).get('mbps', 0)) * 1024 * 1024)
            ul_rate  = int(float((live.get('upload_speed') or {}).get('mbps', 0)) * 1024 * 1024)
            ratio    = round(uploaded / max(1, done), 2)
            state    = self._ui_state(is_error, finished, is_paused, dl_rate, ul_rate)

            file_progress = st.get('file_progress') or []
            files = []
            for i, f in enumerate(files_raw):
                fsize = int(f.get('length', 0))
                fdone = int(file_progress[i]) if i < len(file_progress) else 0
                files.append({
                    'path':     f.get('name', ''),
                    'size':     fsize,
                    'progress': round((fdone / fsize * 100), 1) if fsize > 0 else 100,
                })

            # rqbit non espone un endpoint tracker: ricava gli 'tr=' dal magnet
            # originale (se il torrent è stato aggiunto da EXTTO come magnet).
            trackers = []
            with RqbitClient._magnets_lock:
                entry = RqbitClient._magnets.get(info_hash, {})
            uri = entry.get('uri', '') if entry.get('kind') != 'torrent' else ''
            if uri:
                import urllib.parse as _up
                try:
                    qs = _up.parse_qs(_up.urlsplit(uri).query)
                    for tr_url in qs.get('tr', []):
                        trackers.append({'url': tr_url, 'msg': '', 'tier': 0})
                except Exception:
                    pass

            _slimits = self._seed_limit_entry(info_hash)
            added_at = self._added_at(info_hash)
            with RqbitClient._hashes_lock:
                seed_started = RqbitClient._finish_time.get(info_hash)
            now = time.time()
            peers = self._peer_count(info_hash)

            # v2 puro usa infohash a 64 char hex; altrimenti v1 (o hybrid, non
            # distinguibile via questa API — stessa euristica di fallback usata
            # da LibtorrentClient quando i metadata v1/v2 non sono disponibili).
            torrent_type = 'v2' if len(info_hash) == 64 else 'v1'

            return {
                'success':       True,
                'hash':          info_hash,
                'torrent_type':  torrent_type,
                'name':          name,
                'save_path':     output_folder,
                'state':         state,
                'total_size':    total,
                'downloaded':    done,
                'uploaded':      uploaded,
                'ratio':         ratio,
                'dl_rate':       dl_rate,
                'ul_rate':       ul_rate,
                'active_time':   int(now - added_at) if added_at else 0,
                'seeding_time':  int(now - seed_started) if seed_started else 0,
                'seeds':         peers,
                'peers':         peers,
                'total_seeds':   peers,
                'total_peers':   peers,
                'pieces':        total_pieces,
                'piece_size':    int(total / total_pieces) if total_pieces > 0 else 0,
                'trackers':      trackers,
                'files':         files,
                'dl_limit':      -1,   # rqbit non supporta limiti banda per-torrent via API
                'ul_limit':      -1,
                'seed_ratio':    _slimits.get('ratio', -1),
                'seed_days':     _slimits.get('days', -1),
            }
        except Exception as e:
            logger.error(f"rqbit get_torrent_details error: {e}")
            return {'success': False, 'error': str(e)}

    def get_peers(self, info_hash: str) -> dict:
        """Peer connessi — rqbit non espone client/velocità istantanea per
        peer via API, solo contatori cumulativi: quei campi restano a 0."""
        info_hash = (info_hash or '').lower().strip()
        try:
            j = self._get(f"/torrents/{info_hash}/peer_stats")
            peers = []
            for addr, p in (j.get('peers') or {}).items():
                counters = p.get('counters', {})
                peers.append({
                    'ip':       addr,
                    'client':   p.get('conn_kind', '?'),
                    'dl_speed': 0,
                    'ul_speed': 0,
                    'progress': 0,
                    'flags':    'S' if counters.get('fetched_chunks', 0) > 0 else '',
                    'source':   0,
                })
            peers.sort(key=lambda x: x['ip'])
            return {'success': True, 'peers': peers}
        except Exception as e:
            logger.debug(f"rqbit get_peers error: {e}")
            return {'success': False, 'peers': [], 'error': str(e)}

    def pause_torrent(self, info_hash: str) -> bool:
        try:
            self._post(f"/torrents/{info_hash}/pause")
            return True
        except Exception as e:
            logger.debug(f"rqbit pause: {e}")
            return False

    def resume_torrent(self, info_hash: str) -> bool:
        try:
            self._post(f"/torrents/{info_hash}/start")
            return True
        except Exception as e:
            logger.debug(f"rqbit resume: {e}")
            return False

    def remove_torrent(self, info_hash: str, delete_files: bool = False) -> bool:
        try:
            action = 'delete' if delete_files else 'forget'
            self._post(f"/torrents/{info_hash}/{action}")
            changed = False
            with RqbitClient._hashes_lock:
                if info_hash in RqbitClient._pending:
                    RqbitClient._pending.discard(info_hash)
                    changed = True
                if RqbitClient._finish_time.pop(info_hash, None) is not None:
                    RqbitClient._persist_seed_start()
            if changed:
                RqbitClient._persist_pending()
            if delete_files:
                with RqbitClient._magnets_lock:
                    RqbitClient._magnets.pop(info_hash, None)
                RqbitClient._persist_magnets()
            return True
        except Exception as e:
            logger.debug(f"rqbit remove: {e}")
            return False

    @staticmethod
    def _readd_payload(entry: dict):
        """Ricostruisce (data, content_type) per riaggiungere un torrent già
        noto, sia che fosse un magnet/URL sia un file .torrent caricato."""
        if entry.get('kind') == 'torrent' and entry.get('data_b64'):
            import base64
            return base64.b64decode(entry['data_b64']), 'application/x-bittorrent'
        return entry.get('uri', ''), 'text/plain'

    def recheck_torrent(self, info_hash: str) -> bool:
        """Riverifica i pezzi già scaricati sul disco (equivalente al 'recheck'
        di libtorrent): rimuove il torrent da rqbit SENZA cancellare i file,
        poi lo riaggiunge sullo stesso output_folder — rqbit ricontrolla i
        pezzi già presenti prima di riprendere il download."""
        info_hash = (info_hash or '').lower().strip()
        with RqbitClient._magnets_lock:
            entry = RqbitClient._magnets.get(info_hash)
        if not entry or not (entry.get('uri') or entry.get('data_b64')):
            logger.warning(f"rqbit recheck: nessuna sorgente salvata per {info_hash[:12]}, impossibile riverificare")
            return False
        try:
            self._post(f"/torrents/{info_hash}/forget")
            time.sleep(0.3)
            out_folder = entry.get('output_folder', '')
            path = "/torrents?paused=false"
            if out_folder:
                import urllib.parse as _up
                path += f"&output_folder={_up.quote(out_folder)}"
            data, content_type = self._readd_payload(entry)
            j = self._post(path, data=data, content_type=content_type)
            new_hash = ((j.get('details') or {}).get('info_hash') or '').lower()
            if new_hash:
                self._remember(new_hash)
                with RqbitClient._hashes_lock:
                    RqbitClient._pending.add(new_hash)
                RqbitClient._persist_pending()
                logger.info(f"rqbit recheck: torrent {new_hash[:12]} riaggiunto su '{out_folder}' per riverifica pezzi")
                return True
            return False
        except Exception as e:
            logger.error(f"rqbit recheck error: {e}")
            return False

    def restart_torrent(self, info_hash: str) -> bool:
        """Riavvia un torrent da zero: cancella file+torrent da rqbit e lo
        riaggiunge pulito (stesso comportamento di /api/torrents/restart per
        libtorrent)."""
        info_hash = (info_hash or '').lower().strip()
        with RqbitClient._magnets_lock:
            entry = RqbitClient._magnets.get(info_hash)
        if not entry or not (entry.get('uri') or entry.get('data_b64')):
            logger.warning(f"rqbit restart: nessuna sorgente salvata per {info_hash[:12]}, impossibile riavviare")
            return False
        try:
            self._post(f"/torrents/{info_hash}/delete")
            with RqbitClient._hashes_lock:
                RqbitClient._pending.discard(info_hash)
                RqbitClient._finish_time.pop(info_hash, None)
            RqbitClient._persist_pending()
            RqbitClient._persist_seed_start()
            time.sleep(0.3)
            data, content_type = self._readd_payload(entry)
            j = self._post("/torrents?paused=false", data=data, content_type=content_type)
            new_hash = ((j.get('details') or {}).get('info_hash') or '').lower()
            if new_hash:
                self._remember(new_hash)
                with RqbitClient._hashes_lock:
                    RqbitClient._pending.add(new_hash)
                RqbitClient._persist_pending()
                with RqbitClient._magnets_lock:
                    RqbitClient._magnets[new_hash] = {**entry, 'output_folder': j.get('output_folder', '')}
                RqbitClient._persist_magnets()
                logger.info(f"rqbit restart: torrent {new_hash[:12]} riavviato da zero")
                return True
            return False
        except Exception as e:
            logger.error(f"rqbit restart error: {e}")
            return False

    # ------------------------------------------------------------------
    # Seed limits per-torrent (riusa lo stesso seed_limits.json di libtorrent:
    # stesso schema {info_hash: {'ratio':..,'days':..}}, -1 = usa il default
    # globale, 0 = seed infinito). Chiamato da extto3.py /api/torrents/set_limits.
    # ------------------------------------------------------------------
    @classmethod
    def set_seed_limits(cls, info_hash: str, seed_ratio: float = -1.0, seed_days: float = -1.0) -> bool:
        from .libtorrent import LibtorrentClient
        info_hash = (info_hash or '').lower().strip()
        if not info_hash:
            return False
        try:
            s_db = LibtorrentClient._get_seed_limits_db()
            s_db = {k.lower(): v for k, v in s_db.items()}
            if seed_ratio < 0 and seed_days < 0:
                pass  # nessun valore esplicito: non toccare regole esistenti
            else:
                s_db.setdefault(info_hash, {})
                s_db[info_hash]['ratio'] = seed_ratio
                s_db[info_hash]['days']  = seed_days
            LibtorrentClient._save_seed_limits_db(s_db)
            return True
        except Exception as e:
            logger.debug(f"rqbit set_seed_limits: {e}")
            return False

    # =========================================================================
    # WATCHDOG
    # =========================================================================
    def _watchdog_loop(self):
        while self._running:
            try:
                with RqbitClient._hashes_lock:
                    pending_snapshot = set(RqbitClient._pending)

                for info_hash in pending_snapshot:
                    try:
                        st = self._get(f"/torrents/{info_hash}/stats/v1")
                        if bool(st.get('finished', False)):
                            with RqbitClient._hashes_lock:
                                RqbitClient._pending.discard(info_hash)
                            RqbitClient._persist_pending()
                            self._trigger_post_processing(info_hash)
                    except Exception as e:
                        logger.debug(f"rqbit watchdog {info_hash[:12]}: {e}")

                self._check_seeding_limits()

            except Exception as e:
                logger.debug(f"rqbit watchdog loop: {e}")

            time.sleep(5)

    def _check_seeding_limits(self):
        """Applica seed_ratio/seed_time: prima l'override per-torrent
        (seed_limits.json condiviso con libtorrent), poi il default globale
        rqbit_seed_ratio/rqbit_seed_time (rqbit non li supporta nativamente)."""
        try:
            from .libtorrent import LibtorrentClient
            global_ratio = float(self.cfg.get('rqbit_seed_ratio', 0) or 0)
            global_mins  = float(self.cfg.get('rqbit_seed_time', 0) or 0)
            seed_limits_db = LibtorrentClient._get_seed_limits_db()

            for t in self.list_torrents():
                info_hash = t['hash']
                if 'seeding' not in t['state'].lower() or not t.get('total_size'):
                    continue

                now = time.time()
                with RqbitClient._hashes_lock:
                    is_new = info_hash not in RqbitClient._finish_time
                    started = RqbitClient._finish_time.setdefault(info_hash, now)
                if is_new:
                    RqbitClient._persist_seed_start()
                seeding_secs = now - started

                override = seed_limits_db.get(info_hash, {})
                o_ratio = override.get('ratio', -1)
                o_days  = override.get('days', -1)

                # Seed infinito esplicito per questo torrent: mai rimuovere
                if o_ratio == 0 or o_days == 0:
                    continue

                target_ratio = o_ratio if o_ratio >= 0 else global_ratio
                target_time  = (o_days * 86400) if o_days >= 0 else (global_mins * 60)

                ratio_ok = target_ratio > 0 and t.get('ratio', 0) >= target_ratio
                time_ok  = target_time > 0 and seeding_secs >= target_time

                if not (target_ratio > 0 or target_time > 0):
                    continue
                if ratio_ok or time_ok:
                    logger.info(f"rqbit: seed limit raggiunto per {t.get('name', info_hash[:12])} — stop seeding")
                    self.remove_torrent(info_hash, delete_files=False)
        except Exception as e:
            logger.debug(f"rqbit check_seeding_limits: {e}")

    # =========================================================================
    # POST-PROCESSING — richiama la pipeline condivisa di LibtorrentClient
    # =========================================================================
    def _resolve_archive_target(self, torrent_name: str, cfg_dict: dict):
        """Determina (nas_path, is_pack) per un torrent completato, stessa logica
        usata da LibtorrentClient._process_alerts per il torrent_finished_alert."""
        nas_path = None
        is_pack  = False
        try:
            move_enabled = str(cfg_dict.get('move_episodes', 'yes')).lower() in ('yes', 'true', '1')
            if not move_enabled:
                return None, False
            from ..models import Parser
            from ..config import Config
            ep_info = Parser.parse_series_episode(torrent_name or '')
            if ep_info:
                is_pack = bool(ep_info.get('is_pack'))
                match = Config().find_series_match(ep_info['name'], ep_info['season'])
                if match and match.get('archive_path'):
                    _np = match['archive_path'].strip()
                    if os.path.exists(_np):
                        nas_path = _np
        except Exception as e:
            logger.debug(f"rqbit _resolve_archive_target: {e}")
        return nas_path, is_pack

    def _trigger_post_processing(self, info_hash: str):
        """Chiamato dal watchdog (torrent appena finito) o al riavvio per un
        hash rimasto pending su disco. Non dipende da nessuno stato in memoria:
        rilegge tutto (config, nome torrent, match serie) da fonti persistenti."""
        try:
            from .libtorrent import LibtorrentClient
            full_cfg = LibtorrentClient._load_full_cfg()

            details = self._get(f"/torrents/{info_hash}")
            d = details.get('details', {}) or details
            t_name = d.get('name', '')
            output_folder = details.get('output_folder', self.dir)

            if not t_name:
                logger.warning("rqbit post-processing: nome torrent non determinabile, skip.")
                return

            logger.info(f"rqbit post-processing: '{t_name}' in '{output_folder}'")

            do_move   = str(full_cfg.get('move_episodes', 'no')).lower() in ('yes', 'true', '1')
            do_rename = str(full_cfg.get('rename_episodes', 'no')).lower() in ('yes', 'true', '1')
            if not (do_move or do_rename):
                logger.info("rqbit post-processing: rinomina e spostamento disabilitati, skip.")
                return

            nas_path, is_pack = self._resolve_archive_target(t_name, full_cfg)

            try:
                st = self._get(f"/torrents/{info_hash}/stats/v1")
                total_bytes = int(st.get('total_bytes', 0))
            except Exception:
                total_bytes = 0
            with RqbitClient._hashes_lock:
                started = RqbitClient._finish_time.get(info_hash, time.time())
            elapsed = max(1, int(time.time() - started)) if started else 1

            if is_pack and nas_path:
                # Season pack: copia+rename+cleanup+notifica gestiti internamente,
                # l'originale resta in output_folder per il seeding.
                LibtorrentClient._handle_season_pack(
                    None, os.path.dirname(output_folder.rstrip('/')), nas_path, full_cfg,
                    _torrent_name=t_name, _size_bytes=total_bytes, _active_time_secs=elapsed
                )
                LibtorrentClient._trigger_folder_scan(t_name)
                LibtorrentClient._trigger_mediaserver_refresh(t_name, nas_path)
                return

            # Episodio singolo o film
            target_save_path = output_folder
            is_processed = False

            if do_move and nas_path and os.path.normpath(os.path.abspath(nas_path)) != \
                    os.path.normpath(os.path.abspath(output_folder)):
                try:
                    from ..config import Config as _Cfg
                    from ..models import Parser as _Parser
                    ep_info = _Parser.parse_series_episode(t_name)
                    use_season_subdir = False
                    if ep_info:
                        _match = _Cfg().find_series_match(ep_info['name'], ep_info['season'])
                        use_season_subdir = (_match or {}).get('season_subfolders', False)
                    dest_dir = os.path.join(nas_path, f"Stagione {ep_info['season']}") \
                        if (use_season_subdir and ep_info) else nas_path
                    os.makedirs(dest_dir, exist_ok=True)

                    if os.path.isdir(output_folder):
                        for fname in os.listdir(output_folder):
                            src = os.path.join(output_folder, fname)
                            dst = os.path.join(dest_dir, fname)
                            if not os.path.exists(dst):
                                shutil.move(src, dst)
                        if not os.listdir(output_folder):
                            os.rmdir(output_folder)
                    elif os.path.isfile(output_folder):
                        dst = os.path.join(dest_dir, os.path.basename(output_folder))
                        if not os.path.exists(dst):
                            shutil.move(output_folder, dst)

                    target_save_path = dest_dir
                    is_processed = True
                except Exception as e:
                    logger.warning(f"rqbit post-proc: spostamento fallito: {e}")

            new_path = LibtorrentClient._trigger_renamer(t_name, target_save_path)
            LibtorrentClient._trigger_folder_scan(t_name)
            final_path = new_path or target_save_path
            LibtorrentClient._trigger_mediaserver_refresh(
                os.path.basename(final_path) if new_path else t_name, final_path
            )

            # Notifica: ricostruita sempre da zero da full_cfg (mai da un
            # riferimento vivo passato all'add(), che non sopravvive a un
            # riavvio) — stesso pattern usato da _handle_season_pack.
            try:
                from ..notifier import Notifier
                notifier = Notifier(full_cfg)
                from ..models import Parser
                ep_info = Parser.parse_series_episode(t_name)
                if ep_info:
                    title_disp = f"{ep_info['name']} S{ep_info['season']:02d}E{ep_info['episode']:02d}"
                    notifier.notify_post_processing(
                        title_disp, total_bytes, elapsed, [], True,
                        is_processed or bool(new_path),
                        final_path=final_path if (is_processed or new_path) else None,
                        renamed_to=os.path.basename(new_path) if new_path else None,
                    )
                else:
                    notifier.notify_torrent_complete(
                        torrent_name=t_name, total_bytes=total_bytes,
                        active_time_secs=elapsed, is_movie=True,
                    )
            except Exception as _ne:
                logger.warning(f"rqbit post-proc notifica: {_ne}")

            if str(full_cfg.get('auto_remove_completed', 'no')).lower() in ('yes', 'true', '1'):
                self.remove_torrent(info_hash, delete_files=False)
                logger.info(f"rqbit: auto-rimosso dopo post-processing '{t_name}'")

        except Exception as e:
            logger.warning(f"rqbit post-processing errore: {e}", exc_info=True)
