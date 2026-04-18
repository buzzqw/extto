"""
EXTTO - Client libtorrent embedded (python-libtorrent / libtorrent-rasterbar).
"""

import os
import re
import time
import threading
from datetime import datetime
from typing import Optional

from ..constants import logger, STATE_DIR, DB_FILE
from ..models import stats


class LibtorrentClient:
    """
    Client torrent embedded basato su python-libtorrent (libtorrent-rasterbar).
    Richiede: pip install libtorrent

    Parametri configurabili via series_config.txt (@libtorrent_*):
      enabled, dir, port_min, port_max, dl_limit, ul_limit,
      connections_limit, upload_slots, seed_ratio, seed_time,
      dht, pex, lsd, upnp, natpmp, encryption, proxy_type,
      proxy_host, proxy_port, proxy_username, proxy_password,
      extra_trackers, announce_to_all, stop_at_ratio,
      temp_dir,
      ramdisk_enabled, ramdisk_dir, ramdisk_threshold_gb, ramdisk_margin_gb,
      sched_enabled, sched_start, sched_end,
      sched_dl_limit, sched_ul_limit, paused
    """

    _session      = None
    _lt           = None   # modulo libtorrent, impostato da _ensure_session
    _session_lock = threading.Lock()
    _alert_thread = None
    _running      = False
    _cfg_snapshot: dict = {}
    STATE_DIR      = STATE_DIR

    _ENC_MAP = {
        '0': 0, '1': 1, '2': 2,
        'disabled': 0, 'enabled': 1, 'forced': 2,
        'no': 0, 'prefer': 1, 'yes': 1,
    }
    _PROXY_MAP = {
        'none': 0, 'socks4': 1, 'socks5': 2, 'socks5_pw': 3,
        'http': 4, 'http_pw': 5, 'i2p': 6,
    }

    def __init__(self, cfg: dict):
        self.cfg     = cfg or {}
        self.enabled = str(self.cfg.get('libtorrent_enabled', 'no')).lower() in ('yes', 'true', '1')
        if not self.enabled:
            return
        self.save_path = self.cfg.get('libtorrent_dir', '/downloads').strip()
        self._ensure_session(self.cfg)
        self._apply_settings(self.cfg)

    # ------------------------------------------------------------------
    # DIRECTORY HELPERS (3-tier: ramdisk / temp / final)
    #
    # Parametri configurabili:
    #   libtorrent_ramdisk_enabled      yes/no  — interruttore master
    #   libtorrent_ramdisk_dir          path    — mountpoint del tmpfs (es. /mnt/ramdisk)
    #   libtorrent_ramdisk_threshold_gb float   — dimensione max di un singolo torrent
    #                                             ammesso sul RAM disk (es. 3.5)
    #   libtorrent_ramdisk_margin_gb    float   — spazio minimo da lasciare libero
    #                                             sul RAM disk dopo il download (es. 0.5)
    #
    # Il RAM disk deve essere creato e montato dall'utente (richiede root).
    # Esempio /etc/fstab:
    #   tmpfs  /mnt/ramdisk  tmpfs  defaults,size=4G,mode=0755  0  0
    #
    # Flusso:
    #   add()            → punta sempre a ramdisk_dir (dim. ancora ignota)
    #   metadata_recv    → ora la dimensione è nota: se non ci sta → move a temp_dir
    #   torrent_finished → logica esistente: move verso libtorrent_dir / archive_path
    #   check_seeding    → dopo il seeding svuota sia ramdisk_dir che temp_dir
    # ------------------------------------------------------------------

    @classmethod
    def _ramdisk_enabled(cls, cfg: dict) -> bool:
        """True se il RAM disk è abilitato E la directory esiste sul filesystem."""
        if str(cfg.get('libtorrent_ramdisk_enabled', 'no')).lower() not in ('yes', 'true', '1'):
            return False
        rd = cfg.get('libtorrent_ramdisk_dir', '').strip()
        return bool(rd and os.path.isdir(rd))

    @classmethod
    def _resolve_initial_save_path(cls, cfg: dict) -> str:
        """
        Determina la directory di download iniziale (logica a 3 livelli):
          1. ramdisk_dir  — se abilitato e montato (dimensione ancora ignota,
                            la verifica avviene in metadata_received_alert)
          2. temp_dir     — disco standard, per file grandi o ramdisk disabilitato
          3. libtorrent_dir — destinazione finale (ultimo resort)
        """
        if cls._ramdisk_enabled(cfg):
            rd = cfg.get('libtorrent_ramdisk_dir', '').strip()
            logger.info(f"🐏 Download target: RAM disk → {rd}")
            return rd

        temp_dir  = cfg.get('libtorrent_temp_dir',  '').strip()
        final_dir = cfg.get('libtorrent_dir', '/downloads').strip()

        if temp_dir and os.path.isdir(temp_dir):
            logger.info(f"💾 Download target: temp_dir → {temp_dir}")
            return temp_dir
        logger.info(f"💾 Download target: final_dir → {final_dir}")
        return final_dir

    @classmethod
    def _check_ramdisk_capacity(cls, cfg: dict, total_size: int) -> tuple[bool, str]:
        """
        Verifica se un torrent di total_size byte può stare nel RAM disk.
        Ritorna (fits: bool, reason: str).

        Criteri — entrambi devono essere soddisfatti:
          1. total_size ≤ libtorrent_ramdisk_threshold_gb
          2. spazio libero sul ramdisk ≥ total_size + libtorrent_ramdisk_margin_gb

        Il controllo "spazio libero" riflette direttamente il tmpfs: se il disk
        è da 4 GB e ci sono già 1 GB occupati, free = 3 GB. Nessun calcolo
        sulla RAM di sistema — è compito dell'utente dimensionare il tmpfs.
        """
        import shutil

        ramdisk_dir   = cfg.get('libtorrent_ramdisk_dir', '').strip()
        threshold_gb  = float(cfg.get('libtorrent_ramdisk_threshold_gb', 3.5))
        margin_gb     = float(cfg.get('libtorrent_ramdisk_margin_gb',    0.5))
        threshold_b   = int(threshold_gb * 1024 ** 3)
        margin_b      = int(margin_gb    * 1024 ** 3)

        # Criterio 1: dimensione massima per singolo torrent
        if total_size > threshold_b:
            return False, (
                f"file troppo grande per il RAM disk: "
                f"{total_size / 1024**3:.2f} GB > soglia {threshold_gb} GB"
            )

        # Criterio 2: spazio libero residuo
        try:
            free = shutil.disk_usage(ramdisk_dir).free
        except Exception as e:
            return False, f"impossibile leggere spazio libero su '{ramdisk_dir}': {e}"

        required = total_size + margin_b
        if free < required:
            return False, (
                f"spazio insufficiente sul RAM disk "
                f"(libero: {free / 1024**3:.2f} GB, "
                f"richiesto: {required / 1024**3:.2f} GB = "
                f"file {total_size / 1024**3:.2f} GB + margine {margin_gb} GB)"
            )

        return True, "ok"

    @classmethod
    def _set_preallocate(cls, enabled: bool):
        """
        Imposta pre_allocate_storage a livello di sessione.
        Usato per disabilitare temporaneamente la prealloca quando si aggiunge
        un torrent destinato al RAM disk (evita di riempire la RAM inutilmente).
        """
        if not cls.session_available():
            return
        try:
            cls._session.apply_settings({'pre_allocate_storage': bool(enabled)})
        except Exception as e:
            logger.debug(f"_set_preallocate({enabled}): {e}")

    # ------------------------------------------------------------------
    # SESSION LIFECYCLE
    # ------------------------------------------------------------------

    @classmethod
    def _load_state(cls):
        if not os.path.exists(cls.STATE_DIR):
            try:
                os.makedirs(cls.STATE_DIR)
            except Exception:
                pass
            return
        if cls._lt is None or cls._session is None:
            logger.warning("⚠️ _load_state: sessione non ancora inizializzata, skip")
            return
        logger.info(f"📂 Loading state from: {cls.STATE_DIR}")
        lt      = cls._lt
        session = cls._session
        count   = 0
        for filename in os.listdir(cls.STATE_DIR):
            if filename.endswith('.fastresume'):
                try:
                    path = os.path.join(cls.STATE_DIR, filename)
                    with open(path, 'rb') as f:
                        data = f.read()
                    try:
                        params = lt.read_resume_data(data)
                        # Se esiste il .torrent salvato, caricalo nei params —
                        # così libtorrent ha la mappa dei pezzi e non resta in "Attesa Metadati"
                        ih = filename.replace('.fastresume', '')
                        torrent_path = os.path.join(cls.STATE_DIR, f"{ih}.torrent")
                        if os.path.exists(torrent_path):
                            try:
                                with open(torrent_path, 'rb') as tf:
                                    ti = lt.torrent_info(lt.bdecode(tf.read()))
                                params.ti = ti
                            except Exception as te:
                                logger.debug(f"load .torrent {ih[:8]}: {te}")
                        session.add_torrent(params)
                        count += 1
                    except Exception as e:
                        logger.warning(f"⚠️ Corrupted resume data for {filename}: {e}")
                except Exception as e:
                    logger.error(f"❌ Error reading state {filename}: {e}")
        if count > 0:
            logger.info(f"✅ Restored {count} torrent.")
        # Riapplica i limiti di banda individuali salvati nel DB
        try:
            import core.config_db as _cdb
            saved_limits = _cdb.get_all_torrent_limits()
            if saved_limits:
                restored_limits = 0
                for ih, lim in saved_limits.items():
                    h = cls._find(ih)
                    if h:
                        h.set_download_limit(int(lim.get('dl_bytes', -1)))
                        h.set_upload_limit(int(lim.get('ul_bytes', -1)))
                        restored_limits += 1
                if restored_limits:
                    logger.info(f"🚦 Restored {restored_limits} torrent speed limit(s) from DB.")
        except Exception as _le:
            logger.debug(f"_load_state: restore limits: {_le}")

    @classmethod
    def _save_ui_state(cls):
        """Salva snapshot dello stato UI di ogni torrent nel DB (tabella torrent_meta)."""
        if not cls.session_available():
            return
        try:
            result = []
            s_db = cls._get_seed_limits_db()
            if not isinstance(s_db, dict): s_db = {}
            for h in cls._session.get_torrents():
                try:
                    s = h.status()
                    ih = str(h.info_hash())
                    prog = float(getattr(s, 'progress', 0))
                    is_paused = getattr(s, 'paused', False)
                    raw_state = str(s.state).split('.')[-1] if '.' in str(s.state) else str(s.state)
                    # Salta stati transitori — non ha senso salvarli come "ultimo stato noto"
                    if raw_state in ('checking_resume_data', 'downloading_metadata', 'checking_files', 'allocating'):
                        continue
                    # is_seeding: sta attivamente uploadando (raw_state='seeding')
                    # is_finished: ha tutti i pezzi ma non sta uploadando (raw_state='finished')
                    # NON unire i due: finished non è seeding
                    is_seeding  = getattr(s, 'is_seeding',  False) or raw_state == 'seeding'
                    is_finished = getattr(s, 'is_finished', False) or raw_state == 'finished'
                    is_done     = is_seeding or is_finished
                    is_auto_managed = getattr(s, 'auto_managed', False)
                    dl_rate = getattr(s, 'download_payload_rate', getattr(s, 'download_rate', 0))
                    ul_rate = getattr(s, 'upload_payload_rate', getattr(s, 'upload_rate', 0))
                    err_msg = ""
                    if getattr(s, 'error', None):
                        err_msg = s.error.message() if hasattr(s.error, 'message') else str(s.error)
                        if err_msg.lower() == 'no error': err_msg = ""
                    if err_msg:
                        ui_state = "Errore"
                    elif is_paused:
                        if is_seeding or is_finished:
                            # finished/seeding + paused: ratio raggiunto o pausa manuale
                            ui_state = "In Coda (Seeding)" if is_auto_managed else "Seeding (Completato)"
                        else:
                            ui_state = "In Coda (DL)" if is_auto_managed else "In Pausa"
                    elif is_seeding:
                        ui_state = "In Seeding" if ul_rate > 0 else "Seeding (Fermo)"
                    elif is_finished:
                        # finished = download completo, nessun peer attivo al momento
                        # NON è Terminato — è disponibile per il seeding, solo fermo
                        ui_state = "Seeding (Fermo)"
                    else:
                        ui_state = "In Scarico" if dl_rate > 0 else "In Scarico (Fermo)"
                    snapshot[ih] = {
                        'state':      ui_state,
                        'progress':   prog,
                        'paused':     is_paused,
                        'total_size': int(getattr(s, 'total_wanted', 0)),
                        'downloaded': int(getattr(s, 'total_wanted_done', 0)),
                        'name':       str(getattr(s, 'name', '') or h.name() or ''),
                    }
                except Exception:
                    pass
            if snapshot:
                cls._save_ui_state_to_db(snapshot)
        except Exception as e:
            logger.debug(f"_save_ui_state: {e}")

    @classmethod
    def _save_ui_state_to_db(cls, snapshot: dict) -> None:
        """Scrive lo snapshot nel DB SQLite (extto_series.db, tabella torrent_meta)."""
        try:
            import sqlite3 as _sq, time as _t
            db_path = DB_FILE
            now = int(_t.time())
            with _sq.connect(db_path, timeout=5) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS torrent_meta (
                        hash TEXT PRIMARY KEY, tag TEXT DEFAULT '',
                        ui_state TEXT DEFAULT '', progress REAL DEFAULT 0,
                        paused INTEGER DEFAULT 0, total_size INTEGER DEFAULT 0,
                        downloaded INTEGER DEFAULT 0, name TEXT DEFAULT '',
                        updated_at INTEGER DEFAULT 0
                    )
                """)
                # Scrive solo record con stato non vuoto — evita di sovrascrivere
                # uno snapshot valido con una riga vuota da uno stato transitorio
                valid = [(ih, v['state'], v['progress'], int(v['paused']),
                          v['total_size'], v['downloaded'], v['name'], now)
                         for ih, v in snapshot.items() if v.get('state')]
                if valid:
                    conn.executemany("""
                        INSERT INTO torrent_meta
                            (hash, ui_state, progress, paused, total_size, downloaded, name, updated_at)
                        VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(hash) DO UPDATE SET
                            ui_state=excluded.ui_state, progress=excluded.progress,
                            paused=excluded.paused, total_size=excluded.total_size,
                            downloaded=excluded.downloaded, name=excluded.name,
                            updated_at=excluded.updated_at
                    """, valid)
        except Exception as e:
            logger.debug(f"_save_ui_state_to_db: {e}")

    @classmethod
    def _load_ui_state(cls) -> dict:
        """Carica lo snapshot UI dal DB. Ritorna {hash: {state, progress, ...}}."""
        try:
            import sqlite3 as _sq
            db_path = DB_FILE
            with _sq.connect(db_path, timeout=5) as conn:
                rows = conn.execute(
                    "SELECT hash, ui_state, progress, paused, total_size, downloaded, name FROM torrent_meta"
                ).fetchall()
            return {
                r[0]: {'state': r[1], 'progress': r[2], 'paused': bool(r[3]),
                       'total_size': r[4], 'downloaded': r[5], 'name': r[6]}
                for r in rows
            }
        except Exception:
            return {}

    @classmethod
    def trigger_save_resume_data(cls):
        cls._save_ui_state()
        if not cls.session_available():
            return
        for h in cls._session.get_torrents():
            if h.is_valid():
                h.save_resume_data()

    @classmethod
    def _ensure_session(cls, cfg: dict):
        with cls._session_lock:
            if cfg:
                cls._cfg_snapshot = dict(cfg)
            if cls._session is not None:
                return
            try:
                import libtorrent as lt
                cls._lt = lt
                if not os.path.exists(cls.STATE_DIR):
                    os.makedirs(cls.STATE_DIR)
                s = lt.session()
                cls._session = s
                cls._running = True
                try:
                    mask = (lt.alert.category_t.status_notification |
                            lt.alert.category_t.storage_notification |
                            lt.alert.category_t.error_notification)
                    s.apply_settings({'alert_mask': mask})
                    logger.info(f'🔔 alert_mask set: {mask}')
                except Exception as e:
                    logger.warning(f'⚠️ alert_mask not set: {e}')
                cls._load_state()
                cls._alert_thread = threading.Thread(
                    target=cls._process_alerts, daemon=True, name='lt-alerts'
                )
                cls._alert_thread.start()
                logger.info('✅ libtorrent session started (state restored)')
            except ImportError:
                logger.error('❌ python-libtorrent not installed.')
                raise
            except Exception as e:
                logger.error(f'❌ Error starting libtorrent session: {e}')
                raise

    @classmethod
    def _apply_settings(cls, cfg: dict):
        if not cls.session_available():
            return
        lt = cls._lt
        s  = cls._session

        def _bool(k, default='yes'):
            return str(cfg.get(k, default)).lower() in ('yes', 'true', '1')

        lt_ver       = tuple(int(x) for x in getattr(lt, 'version', '1.0.0').split('.')[:2])
        use_dict_api = lt_ver >= (2, 0)

        _dl_kb      = int(cfg.get('libtorrent_dl_limit', 0))
        _ul_kb      = int(cfg.get('libtorrent_ul_limit', 0))
        dl_limit    = _dl_kb * 1024 if _dl_kb > 0 else 0   # KB/s → B/s (0 = nessun limite)
        ul_limit    = _ul_kb * 1024 if _ul_kb > 0 else 0   # KB/s → B/s (0 = nessun limite)
        logger.info(f"🚦 Limiti velocità globali → DL: {_dl_kb} KB/s ({dl_limit} B/s), UL: {_ul_kb} KB/s ({ul_limit} B/s)")
        conn_limit  = int(cfg.get('libtorrent_connections_limit', 200))
        ul_slots    = int(cfg.get('libtorrent_upload_slots', 4))
        seed_ratio  = float(cfg.get('libtorrent_seed_ratio', 0))
        seed_time   = int(cfg.get('libtorrent_seed_time', 0))
        ann_all     = _bool('libtorrent_announce_to_all', 'no')
        enc_str     = str(cfg.get('libtorrent_encryption', '1'))
        enc_val     = cls._ENC_MAP.get(enc_str.lower(), 1)
        p_min       = int(cfg.get('libtorrent_port_min', 6881))
        p_max       = int(cfg.get('libtorrent_port_max', 6891))
        # Coda torrent attivi
        active_dl   = int(cfg.get('libtorrent_active_downloads', 3))
        active_ul   = int(cfg.get('libtorrent_active_seeds', 3))
        active_tot  = int(cfg.get('libtorrent_active_limit', 5))
        # Soglia torrent lenti (KB/s → B/s)
        slow_dl     = int(cfg.get('libtorrent_slow_dl_threshold', 2)) * 1024
        slow_ul     = int(cfg.get('libtorrent_slow_ul_threshold', 2)) * 1024
        # Prealloca spazio disco
        preallocate = _bool('libtorrent_preallocate', 'no')
        
        # --- FIX: Lettura Interfaccia VPN (Killswitch) ---
        iface = str(cfg.get('libtorrent_interface', '')).strip()
        if iface and iface.lower() != 'auto':
            listen_str = f"{iface}:{p_min}"
            out_iface  = iface
        else:
            listen_str = f"0.0.0.0:{p_min}"
            out_iface  = ""
        # -------------------------------------------------

        if use_dict_api:
            pack = {
                'download_rate_limit':      int(dl_limit),
                'upload_rate_limit':        int(ul_limit),
                'connections_limit':        int(conn_limit),
                'unchoke_slots_limit':      int(ul_slots),
                'num_want':                 int(50),
                'announce_to_all_tiers':    bool(ann_all),
                'announce_to_all_trackers': bool(ann_all),
                'listen_interfaces':        listen_str,       # Aggiornato per VPN
                'outgoing_interfaces':      out_iface,        # Forza il traffico in uscita
                'out_enc_policy':           int(enc_val),
                'in_enc_policy':            int(enc_val),
                'allowed_enc_level':        int(3),
                'enable_dht':               bool(_bool('libtorrent_dht')),
                'enable_lsd':               bool(_bool('libtorrent_lsd')),
                'enable_upnp':              bool(_bool('libtorrent_upnp')),
                'enable_natpmp':            bool(_bool('libtorrent_natpmp')),
                # Coda torrent attivi
                'active_downloads':         int(active_dl),
                'active_seeds':             int(active_ul),
                'active_limit':             int(active_tot),
                # Soglia torrent lenti
                'inactive_down_rate':       int(slow_dl),
                'inactive_up_rate':         int(slow_ul),
                # Prealloca spazio disco
                'pre_allocate_storage':     bool(preallocate),
                'rename_files_on_settings_change': True,
            }
            # Limiti seeding globali — presenti anche nel ramo 1.x
            # share_ratio_limit: ratio upload/download minimo prima di fermare il seeding
            # seed_time_limit: minuti minimi di seeding (0 = disabilitato)
            if seed_ratio > 0:
                pack['share_ratio_limit'] = float(seed_ratio)
            if seed_time > 0:
                pack['seed_time_limit'] = int(seed_time * 60)
            proxy_type = str(cfg.get('libtorrent_proxy_type', 'none')).lower()
            if proxy_type != 'none':
                pack['proxy_type']     = cls._PROXY_MAP.get(proxy_type, 0)
                pack['proxy_hostname'] = cfg.get('libtorrent_proxy_host', '')
                pack['proxy_port']     = int(cfg.get('libtorrent_proxy_port', 1080))
                pack['proxy_username'] = cfg.get('libtorrent_proxy_username', '')
                pack['proxy_password'] = cfg.get('libtorrent_proxy_password', '')
            try:
                s.apply_settings(pack)
                logger.debug('⚙️  apply_settings 2.x OK')
            except Exception:
                for k, v in pack.items():
                    try:
                        s.apply_settings({k: v})
                    except Exception as e2:
                        logger.debug(f'  skip {k}={v!r}: {e2}')
        else:
            try:
                ss = s.get_settings()
                ss['download_rate_limit']      = dl_limit
                ss['upload_rate_limit']        = ul_limit
                ss['connections_limit']        = conn_limit
                ss['unchoke_slots_limit']      = ul_slots
                ss['num_want']                 = 50
                ss['announce_to_all_tiers']    = ann_all
                ss['announce_to_all_trackers'] = ann_all
                ss['active_downloads']         = active_dl
                ss['active_seeds']             = active_ul
                ss['active_limit']             = active_tot
                ss['inactive_down_rate']       = slow_dl
                ss['inactive_up_rate']         = slow_ul
                ss['pre_allocate_storage']     = preallocate
                ss['rename_files_on_settings_change'] = True
                if seed_ratio > 0:
                    ss['share_ratio_limit'] = seed_ratio
                if seed_time > 0:
                    ss['seed_time_limit'] = seed_time * 60
                s.apply_settings(ss)
            except Exception as e:
                logger.warning(f'⚠️  apply_settings 1.x: {e}')
            try:
                # Applica l'interfaccia su libtorrent 1.x
                if iface and iface.lower() != 'auto':
                    s.listen_on(p_min, p_max, iface)
                else:
                    s.listen_on(p_min, p_max)
            except Exception:
                pass
            for flag, start, stop in [
                ('libtorrent_dht',    s.start_dht,    s.stop_dht),
                ('libtorrent_lsd',    s.start_lsd,    s.stop_lsd),
                ('libtorrent_upnp',   s.start_upnp,   s.stop_upnp),
                ('libtorrent_natpmp', s.start_natpmp, s.stop_natpmp),
            ]:
                try:
                    (start if _bool(flag) else stop)()
                except Exception:
                    pass
            try:
                ep = lt.pe_settings()
                ep.out_enc_policy = enc_val
                ep.in_enc_policy  = enc_val
                s.set_pe_settings(ep)
            except Exception:
                pass
            proxy_type = str(cfg.get('libtorrent_proxy_type', 'none')).lower()
            if proxy_type != 'none':
                try:
                    ps          = lt.proxy_settings()
                    ps.type     = cls._PROXY_MAP.get(proxy_type, 0)
                    ps.hostname = cfg.get('libtorrent_proxy_host', '')
                    ps.port     = int(cfg.get('libtorrent_proxy_port', 1080))
                    ps.username = cfg.get('libtorrent_proxy_username', '')
                    ps.password = cfg.get('libtorrent_proxy_password', '')
                    s.set_proxy(ps)
                except Exception:
                    pass

        cls._cfg_snapshot = dict(cfg)
        logger.debug(f'⚙️  libtorrent {lt.version} settings applied (Interfaccia: {out_iface or "Globale"})')
        
    @classmethod
    def _load_full_cfg(cls) -> dict:
        """Carica la configurazione completa dal DB (config_db). Fallback a _cfg_snapshot."""
        try:
            import core.config_db as _cdb
            return _cdb.get_all_settings()
        except Exception as _e:
            logger.debug(f"_load_full_cfg: {_e}")
            return dict(cls._cfg_snapshot)

    @classmethod
    def _trigger_renamer(cls, h, save_path):
        """Innesca la rinomina ufficiale solo se il flag globale è attivo."""
        full_cfg = cls._load_full_cfg()

        # Controlla se la rinomina è attiva
        if str(full_cfg.get('rename_episodes', 'no')).lower() not in ('yes', 'true', '1'):
            logger.debug(f"rename_episodes non attivo — skip rename per '{h.name()}'")
            return
            
        try:
            from ..renamer import rename_completed_torrent, rename_completed_movie
            _db = None
            try:
                from ..database import Database
                _db = Database()
            except Exception:
                pass
            
            # Tenta prima con la logica Serie TV
            is_renamed = rename_completed_torrent(
                torrent_name = h.name() or '',
                save_path    = save_path,
                cfg          = full_cfg,  # <--- ORA E' CORRETTO!
                db           = _db
            )
            
            # Se fallisce, tenta come Film
            if not is_renamed:
                rename_completed_movie(
                    torrent_name = h.name() or '',
                    save_path    = save_path,
                    cfg          = full_cfg   # <--- ORA E' CORRETTO!
                )
        except Exception as _re:
            logger.warning(f"⚠️ Rename failed: {_re}")   
            
    @classmethod
    def _trigger_folder_scan(cls, h):
        """Innesca la scansione della cartella su NAS tramite API in background."""
        try:
            full_cfg = cls._load_full_cfg()
            from ..models import Parser
            ep_info = Parser.parse_series_episode(h.name() or '')
            if not ep_info: return
            
            series_name = ep_info['name']
            from ..database import Database
            db = Database()
            c = db.conn.cursor()
            c.execute("SELECT id FROM series WHERE name LIKE ?", (f"%{series_name}%",))
            row = c.fetchone()
            if not row: return
            
            s_id = row['id']
            port = full_cfg.get('web_port', 5000)
            
            import threading
            import requests
            def _bg_scan():
                try:
                    logger.info(f"🔄 Starting automatic NAS scan for '{series_name}' post-download...")
                    requests.post(f"http://127.0.0.1:{port}/api/series/{s_id}/scan-archive", timeout=15)
                except Exception: pass
                
            # Lo avvia in un thread separato per non bloccare i download attivi
            threading.Thread(target=_bg_scan, daemon=True).start()
        except Exception:
            pass        

    # ------------------------------------------------------------------
    # SEASON PACK HANDLER
    # ------------------------------------------------------------------

    _VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv', '.webm'}

    @classmethod
    def _handle_season_pack(cls, h, curr_save: str, nas_path: str, full_cfg: dict) -> int:
        """
        Post-processing completo per un season pack libtorrent.

        Per ogni file video trovato nella cartella del pack:
          1. Lo copia in nas_path (se non già presente o se lo score è migliore)
          2. Chiama discard_if_inferior() -> se esiste già un file con score maggiore,
             manda il file appena copiato in trash
          3. Chiama cleanup_old_episode() -> manda in trash i file precedenti con
             score inferiore
          4. Chiama rename_completed_torrent() sul singolo file -> TMDB rename

        Il pack originale in libtorrent_dir NON viene toccato (rimane per il seeding).
        Ritorna il numero di file copiati con successo.
        """
        import shutil as _shutil

        if not nas_path or not os.path.isdir(nas_path):
            logger.warning(f"\u26a0\ufe0f  Season pack: invalid archive_path or NAS not mounted: '{nas_path}'")
            return 0

        pack_root = os.path.join(curr_save, h.name() or '')
        if os.path.isdir(pack_root):
            pack_dir = pack_root
        else:
            pack_dir = curr_save

        cleanup_enabled = str(full_cfg.get('cleanup_upgrades', 'no')).lower() in ('yes', 'true', '1')
        trash_path      = full_cfg.get('trash_path', '').strip()
        min_score_diff  = int(full_cfg.get('cleanup_min_score_diff', 0))
        cleanup_action  = full_cfg.get('cleanup_action', 'move').strip().lower()

        copied     = 0
        skipped    = 0

        # Raccolta episodi per notifica riassuntiva
        # entry: {'ep_label': 'S02E01', 'final_name': '...', 'status': 'new'|'upgrade'|'skipped'|'inferior'}
        ep_log = []

        try:
            from ..models   import Parser
            from ..renamer  import rename_completed_torrent
        except Exception as _ie:
            logger.warning(f"\u26a0\ufe0f  Season pack: import failed ({_ie}), proceeding without rename")
            Parser                   = None
            rename_completed_torrent = None

        try:
            cleaner_mod = None
            if cleanup_enabled and trash_path:
                try:
                    from .. import cleaner as cleaner_mod
                except Exception:
                    pass
        except Exception:
            cleaner_mod = None

        for root, dirs, files in os.walk(pack_dir):
            depth = len(os.path.normpath(root).split(os.sep)) - len(os.path.normpath(pack_dir).split(os.sep))
            if depth > 3:
                dirs.clear()
                continue
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in cls._VIDEO_EXTS:
                    continue

                src = os.path.join(root, fname)
                dst = os.path.join(nas_path, fname)

                # Determina ep_label e score prima della copia
                ep_label  = fname
                ep_info   = None
                new_score = 0
                if Parser:
                    try:
                        ep_info   = Parser.parse_series_episode(fname)
                        new_score = ep_info['quality'].score() if ep_info else Parser.parse_quality(fname).score()
                        if ep_info:
                            ep_label = f"S{ep_info['season']:02d}E{ep_info['episode']:02d}"
                    except Exception:
                        pass

                # --- 1. Copia sul NAS ---
                if os.path.exists(dst):
                    logger.debug(f"   \U0001f4e6 pack skip (already present): '{fname}'")
                    skipped += 1
                    ep_log.append({'ep_label': ep_label, 'final_name': fname, 'status': 'skipped'})
                    continue
                try:
                    dst_tmp = dst + '.extto_tmp'
                    _shutil.copy2(src, dst_tmp)
                    src_size = os.path.getsize(src)
                    dst_size = os.path.getsize(dst_tmp)
                    if src_size != dst_size:
                        os.unlink(dst_tmp)
                        logger.error(f"   \u274c pack corrupted copy '{fname}': {src_size}B -> {dst_size}B, file removed")
                        continue
                    os.replace(dst_tmp, dst)
                    logger.info(f"   \U0001f4cb pack copied: '{fname}' -> '{nas_path}' ({src_size//1024//1024} MB)")
                    copied += 1
                except Exception as ce:
                    try:
                        if os.path.exists(dst + '.extto_tmp'):
                            os.unlink(dst + '.extto_tmp')
                    except Exception:
                        pass
                    logger.error(f"   \u274c pack copy failed '{fname}': {ce}")
                    continue

                # --- 2. discard_if_inferior ---
                if cleaner_mod and ep_info and trash_path:
                    try:
                        discarded = cleaner_mod.discard_if_inferior(
                            series_name    = ep_info.get('name', ''),
                            season         = ep_info.get('season', 0),
                            episode        = ep_info.get('episode', 0),
                            new_score      = new_score,
                            new_fname      = fname,
                            save_path      = nas_path,
                            trash_path     = trash_path,
                            min_score_diff = min_score_diff,
                        )
                        if discarded:
                            logger.info(f"   \U0001f5d1\ufe0f  pack: '{fname}' inferior to existing -> trash")
                            ep_log.append({'ep_label': ep_label, 'final_name': fname, 'status': 'inferior'})
                            continue
                    except Exception as e:
                        logger.warning(f"   \u26a0\ufe0f  discard_if_inferior: {e}")

                # --- 3. Rename ---
                new_fname_after_rename = fname
                if rename_completed_torrent:
                    try:
                        _db_inst = None
                        try:
                            from ..database import Database
                            _db_inst = Database()
                        except Exception:
                            pass
                        renamed = rename_completed_torrent(
                            torrent_name = fname,
                            save_path    = nas_path,
                            cfg          = full_cfg,
                            db           = _db_inst,
                        )
                        if renamed:
                            logger.info(f"   \u270f\ufe0f  pack rename: '{fname}'")
                            if Parser and ep_info:
                                for _fn in (os.listdir(nas_path) if os.path.isdir(nas_path) else []):
                                    if os.path.splitext(_fn)[1].lower() in cls._VIDEO_EXTS and _fn != fname:
                                        _fp = Parser.parse_series_episode(_fn)
                                        if _fp and _fp.get('season') == ep_info.get('season') \
                                                and _fp.get('episode') == ep_info.get('episode'):
                                            new_fname_after_rename = _fn
                                            break
                    except Exception as re_e:
                        logger.warning(f"   \u26a0\ufe0f  pack rename '{fname}': {re_e}")

                # --- 4. cleanup_old_episode ---
                ep_status = 'new'
                if cleaner_mod and ep_info and trash_path:
                    try:
                        removed = cleaner_mod.cleanup_old_episode(
                            series_name    = ep_info.get('name', ''),
                            season         = ep_info.get('season', 0),
                            episode        = ep_info.get('episode', 0),
                            new_score      = new_score,
                            new_title      = fname,
                            archive_path   = nas_path,
                            trash_path     = trash_path,
                            min_score_diff = min_score_diff,
                            new_fname      = new_fname_after_rename,
                        )
                        if removed > 0:
                            ep_status = 'upgrade'
                    except Exception as e:
                        logger.warning(f"   \u26a0\ufe0f  cleanup_old_episode: {e}")

                ep_log.append({'ep_label': ep_label, 'final_name': new_fname_after_rename, 'status': ep_status})

        if copied > 0:
            logger.info(f"\u2705 Season pack: {copied} episodes copied to NAS (skipped: {skipped})")
        elif skipped > 0:
            logger.info(f"\u2139\ufe0f  Season pack: all episodes already present on NAS")
        else:
            logger.warning(f"\u26a0\ufe0f  Season pack: no video files found in '{pack_dir}'")

        # --- NOTIFICA RIASSUNTIVA ---
        try:
            from ..notifier import Notifier as _Notif
            import core.config_db as _cdb_n
            _pp_cfg = _cdb_n.get_all_settings()
            _notif  = _Notif(_pp_cfg)

            s        = h.status()
            size_gb  = s.total_wanted_done / (1024**3)
            time_sec = s.active_time or 1
            m, sec   = divmod(int(time_sec), 60)
            speed    = (s.total_wanted_done / time_sec) / (1024**2)

            n_new      = sum(1 for e in ep_log if e['status'] == 'new')
            n_upgrade  = sum(1 for e in ep_log if e['status'] == 'upgrade')
            n_skipped  = sum(1 for e in ep_log if e['status'] == 'skipped')
            n_inferior = sum(1 for e in ep_log if e['status'] == 'inferior')

            status_icon = {'new': '\u2705', 'upgrade': '\u2b06\ufe0f', 'skipped': '\u23ed\ufe0f', 'inferior': '\U0001f5d1\ufe0f'}

            visible = ep_log if len(ep_log) <= 8 else ep_log[:5]
            ep_lines = []
            for e in visible:
                icon = status_icon.get(e['status'], '•')
                display = os.path.splitext(e['final_name'])[0] if e['final_name'] else e['ep_label']
                ep_lines.append(f"{icon} <code>{display}</code>")
            if len(ep_log) > 8:
                ep_lines.append(f"<i>... {_notif.t('e altri')} {len(ep_log) - 5}</i>")

            # Header serie: ricava dal nome del primo episodio o dal nome torrent
            series_label = h.name() or 'Series Pack'
            for e in ep_log:
                if e['ep_label'] and e['ep_label'].startswith('S') and len(e['ep_label']) >= 3:
                    series_label = f"{series_label} S{e['ep_label'][1:3]}"
                    break

            summary_parts = []
            if n_new:      summary_parts.append(f"\u2705 {n_new} {_notif.t('nuovi')}")
            if n_upgrade:  summary_parts.append(f"\u2b06\ufe0f {n_upgrade} {_notif.t('upgrade')}")
            if n_skipped:  summary_parts.append(f"\u23ed\ufe0f {n_skipped} {_notif.t('già presenti')}")
            if n_inferior: summary_parts.append(f"\U0001f5d1\ufe0f {n_inferior} {_notif.t('inferiori scartati')}")

            msg = (
                f"\U0001f4e6 <b>{_notif.t('Season Pack Archiviato')}</b>\n"
                f"\U0001f3ac <b>{series_label}</b>\n\n"
                f"\U0001f4be {size_gb:.2f} GB  \u23f1\ufe0f {m}m {sec}s  \U0001f680 {speed:.1f} MB/s\n\n"
                f"{chr(32).join(summary_parts)}\n\n"
                f"\U0001f4cb <b>{_notif.t('Episodi')}:</b>\n" + "\n".join(ep_lines) + "\n\n"
                f"\U0001f4c2 <code>{nas_path}</code>"
            )
            _notif._send_telegram(msg)
            _notif._send_email(f"EXTTO: {_notif.t('Season Pack Archiviato')} - {series_label}", msg)
        except Exception as _ne:
            logger.warning(f"\u26a0\ufe0f  Season pack notification error: {_ne}")

        return copied

    @classmethod
    def _process_alerts(cls):
        SAVE_INTERVAL    = 30
        SCHED_INTERVAL   = 60
        FILTER_INTERVAL  = 3600   # controlla ogni ora se aggiornare
        last_auto_save   = time.time()
        last_sched_check = time.time()
        last_filter_chk  = time.time()

        while cls._running:
            try:
                now = time.time()
                if now - last_auto_save > SAVE_INTERVAL:
                    cls.trigger_save_resume_data()
                    last_auto_save = now
                if now - last_sched_check > SCHED_INTERVAL:
                    cls.check_speed_schedule()
                    cls.check_seeding_limits()  # <--- AGGIUNTO QUI! Controlla il ratio ogni 60 secondi
                    last_sched_check = now
                if now - last_filter_chk > FILTER_INTERVAL:
                    cls._maybe_autoupdate_ipfilter()
                    last_filter_chk = now

                if cls._session:
                    cls._session.post_torrent_updates()
                    alerts = cls._session.pop_alerts()

                    for a in alerts:
                        a_type = str(type(a))

                        if 'save_resume_data_alert' in a_type:
                            if not os.path.exists(cls.STATE_DIR):
                                try:
                                    os.makedirs(cls.STATE_DIR)
                                except Exception:
                                    pass
                            params = getattr(a, 'params', None) or getattr(a, 'resume_data', None)
                            if params:
                                try:
                                    data = cls._lt.write_resume_data(params)
                                    if isinstance(data, dict):
                                        data = cls._lt.bencode(data)
                                    ih          = str(params.info_hash) if hasattr(params, 'info_hash') else str(a.handle.info_hash())
                                    final_path  = os.path.join(cls.STATE_DIR, f"{ih}.fastresume")
                                    temp_path   = final_path + ".tmp"
                                    with open(temp_path, 'wb') as f:
                                        f.write(data)
                                        f.flush()
                                        os.fsync(f.fileno())
                                    os.replace(temp_path, final_path)
                                except Exception as e:
                                    logger.error(f"❌ Atomic save error {ih}: {e}")

                        elif 'save_resume_data_failed_alert' in a_type:
                            pass

                        elif 'metadata_received_alert' in a_type:
                            # Salva il .torrent file quando libtorrent scarica i metadati dal tracker.
                            # Senza questo, al riavvio pieces=[] e il torrent resta in "Attesa Metadati".
                            try:
                                _mh = a.handle
                                _ti = _mh.torrent_file() if hasattr(_mh, 'torrent_file') else None
                                if _ti:
                                    _ih = str(_mh.info_hash())
                                    _tp = os.path.join(cls.STATE_DIR, f"{_ih}.torrent")
                                    _ce = cls._lt.create_torrent(_ti)
                                    _td = cls._lt.bencode(_ce.generate())
                                    _tmp = _tp + '.tmp'
                                    with open(_tmp, 'wb') as _f:
                                        _f.write(_td)
                                        _f.flush(); os.fsync(_f.fileno())
                                    os.replace(_tmp, _tp)
                                    logger.info(f"💾 Torrent metadata saved: '{_mh.name()}'")

                                    # ---> LOGICA 3-TIER: ramdisk / temp_dir / final_dir <---
                                    # Ora che conosciamo la dimensione reale, decidiamo dove scaricare.
                                    try:
                                        _cfg        = cls._cfg_snapshot
                                        _ramdisk    = _cfg.get('libtorrent_ramdisk_dir', '').strip()
                                        _temp_dir   = _cfg.get('libtorrent_temp_dir',    '').strip()
                                        _curr_save  = os.path.normpath(os.path.abspath(_mh.status().save_path))
                                        _total_size = _ti.total_size()

                                        # Il torrent è attualmente sul RAM disk?
                                        _on_ramdisk = (
                                            cls._ramdisk_enabled(_cfg) and
                                            _ramdisk and
                                            _curr_save.startswith(os.path.normpath(os.path.abspath(_ramdisk)))
                                        )

                                        if _on_ramdisk:
                                            _fits, _reason = cls._check_ramdisk_capacity(_cfg, _total_size)
                                            if _fits:
                                                logger.info(
                                                    f"🐏 RAM disk OK per '{_mh.name()}' "
                                                    f"({_total_size / 1024**3:.2f} GB) → {_ramdisk}"
                                                )
                                            else:
                                                # Non ci sta: fallback su temp_dir o final_dir
                                                _fallback = _temp_dir if (_temp_dir and os.path.isdir(_temp_dir)) \
                                                            else _cfg.get('libtorrent_dir', '/downloads').strip()
                                                logger.warning(
                                                    f"🔄 RAM disk fallback per '{_mh.name()}': {_reason} "
                                                    f"→ moving to: {_fallback}"
                                                )
                                                _mh.move_storage(_fallback)
                                    except Exception as _f_err:
                                        logger.warning(f"⚠️ Errore logica 3-tier ramdisk: {_f_err}")
                                    # ---> FINE LOGICA 3-TIER <---

                                    # Forza subito un save_resume_data con i metadati ora disponibili
                                    _mh.save_resume_data()
                            except Exception as _me:
                                logger.debug(f"metadata_received save: {_me}")

                        elif 'torrent_finished_alert' in a_type:
                            h   = a.handle
                            h.save_resume_data()

                            # Carica config completa dal DB (include tmdb_api_key, rename_episodes, ecc.)
                            full_cfg = cls._load_full_cfg()

                            # --- Notifica completamento ---
                            try:
                                s = h.status()
                                from ..notifier import Notifier
                                notif = Notifier(full_cfg)
                                # Determina se è una serie TV per sopprimere il messaggio intermedio
                                # (notify_post_processing invierà il messaggio finale completo)
                                from ..models import Parser as _Parser
                                _is_series = bool(_Parser.parse_series_episode(h.name() or ''))
                                notif.notify_torrent_complete(
                                    torrent_name     = h.name() or 'Sconosciuto',
                                    total_bytes      = s.total_wanted_done,
                                    active_time_secs = s.active_time,
                                    is_series        = _is_series,
                                )
                            except Exception as e:
                                logger.warning(f"⚠️ Completion notification error: {e}")

                            cfg          = full_cfg
                            global_dir   = cfg.get('libtorrent_dir', '/downloads').strip()
                            target_dir   = global_dir
                            move_enabled = str(cfg.get('move_episodes', 'yes')).lower() in ('yes', 'true', '1')

                            # --- 1. Cerca archive_path e determina se è un season pack ---
                            nas_path = None
                            is_pack  = False
                            try:
                                if move_enabled:
                                    from ..config import Config
                                    from ..models import Parser
                                    live_cfg = Config()
                                    ep_info  = Parser.parse_series_episode(h.name() or '')
                                    if ep_info:
                                        # is_pack=True copre sia season pack interi (episode==0, es: "Alfa S01")
                                        # che pack parziali con range (episode>0, es: "Alfa S01E01-05")
                                        is_pack = bool(ep_info.get('is_pack'))
                                        match   = live_cfg.find_series_match(ep_info['name'], ep_info['season'])
                                        if match and match.get('archive_path'):
                                            _np = match['archive_path'].strip()
                                            if os.path.exists(_np):
                                                nas_path   = _np
                                                target_dir = _np
                                                logger.info(
                                                    f"📁 NAS folder found for series: {target_dir}"
                                                    + (" [SEASON PACK]" if is_pack else "")
                                                )
                            except Exception as e:
                                logger.warning(f"⚠️ NAS folder lookup error: {e}")

                            logger.info(f"🏁 Torrent complete: '{h.name()}'")

                            # --- 2. Post-processing: pack vs singolo episodio ---
                            try:
                                curr_save   = os.path.normpath(os.path.abspath(h.status().save_path))
                                target_norm = os.path.normpath(os.path.abspath(target_dir))

                                if move_enabled and nas_path and is_pack:
                                    # ── SEASON PACK ──────────────────────────────────────
                                    # Copia episodi sul NAS, applica cleaner + rename per ognuno.
                                    # Il pack originale rimane in libtorrent_dir per il seeding.
                                    logger.info(f"📦 Season pack: starting post-processing → '{nas_path}'")
                                    cls._handle_season_pack(h, curr_save, nas_path, full_cfg)
                                    # Scan archivio aggiornato (thread background)
                                    cls._trigger_folder_scan(h)

                                elif move_enabled and curr_save != target_norm:
                                    # ── EPISODIO SINGOLO con NAS diverso ─────────────────
                                    # move_storage sposta tutto; rename+cleaner scattano su
                                    # storage_moved_alert dopo che libtorrent conferma lo spostamento.
                                    logger.info(f"🚚 Moving: '{curr_save}' → '{target_norm}'")
                                    # Marca il torrent come "NAS move in corso" PRIMA di chiamare
                                    # move_storage: check_seeding_limits legge questo set e salta
                                    # il secondo move verso libtorrent_dir evitando duplicazioni.
                                    if not hasattr(cls, '_nas_move_pending'):
                                        cls._nas_move_pending = set()
                                    cls._nas_move_pending.add(str(h.info_hash()))
                                    h.move_storage(target_dir)

                                else:
                                    # ── NESSUNO SPOSTAMENTO ──────────────────────────────
                                    # Già nella dir giusta, o move disabilitato.
                                    if not move_enabled and curr_save != target_norm:
                                        logger.info(f"🛑 Moving disabled. File in: '{curr_save}'")
                                    # Passa il path diretto al file se possibile, così
                                    # rename_completed_torrent non fa listdir sull'intera
                                    # cartella e get_media_tags riceve il path esatto.
                                    _torrent_file = os.path.join(curr_save, h.name()) if h.name() else curr_save
                                    _rename_path  = _torrent_file if os.path.isfile(_torrent_file) else curr_save
                                    cls._trigger_renamer(h, _rename_path)
                                    cls._trigger_folder_scan(h)
                                    # ── NOTIFICA POST-PROCESSING (ramo no-move) ───────────
                                    try:
                                        _s2 = h.status()
                                        try:
                                            import core.config_db as _cdb4
                                            _pp_cfg2 = _cdb4.get_all_settings()
                                        except Exception:
                                            _pp_cfg2 = dict(full_cfg)
                                        from ..notifier import Notifier as _Notif2
                                        _notif2 = _Notif2(_pp_cfg2)
                                        # Cerca il file rinominato nella directory
                                        _renamed_to2 = None
                                        try:
                                            import time as _time3
                                            _video_exts3 = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv', '.webm'}
                                            _now3 = _time3.time()
                                            _RECENT_SECS3 = 300
                                            _scan_dir = curr_save if os.path.isdir(curr_save) else os.path.dirname(curr_save)
                                            _candidates2 = []
                                            for _fn2 in os.listdir(_scan_dir):
                                                if os.path.splitext(_fn2)[1].lower() not in _video_exts3:
                                                    continue
                                                _fp2 = os.path.join(_scan_dir, _fn2)
                                                try:
                                                    _mtime2 = os.path.getmtime(_fp2)
                                                except Exception:
                                                    continue
                                                if (_now3 - _mtime2) <= _RECENT_SECS3:
                                                    _candidates2.append((_mtime2, _fn2))
                                            if _candidates2:
                                                _candidates2.sort(key=lambda x: x[0], reverse=True)
                                                _renamed_to2 = _candidates2[0][1]
                                        except Exception:
                                            pass
                                        _notif2.notify_post_processing(
                                            title_name   = h.name() or 'Sconosciuto',
                                            size_bytes   = _s2.total_wanted_done,
                                            time_sec     = _s2.active_time,
                                            action_log   = [],
                                            is_series    = True,
                                            is_processed = True,
                                            final_path   = curr_save,
                                            renamed_to   = _renamed_to2,
                                        )
                                    except Exception as _ne2:
                                        logger.warning(f"⚠️ Post-processing notification error (no-move): {_ne2}")
                                    # --- AUTO-REMOVE POST-RENAME (no-move) ---
                                    try:
                                        _ar_cfg2 = cls._cfg_snapshot or {}
                                        _do_ar2 = str(_ar_cfg2.get('auto_remove_completed', 'no')).lower() in ('yes', 'true', '1')
                                        if _do_ar2:
                                            _ih_ar2 = str(h.info_hash())
                                            cls.remove_torrent(_ih_ar2, delete_files=False)
                                            logger.info(f"🗑️ Auto-removed (post-rename, no-move): '{h.name() or _ih_ar2}'")
                                    except Exception as _are2:
                                        logger.debug(f"auto_remove post-rename no-move: {_are2}")

                            except Exception as e:
                                logger.error(f"❌ Post-processing error '{h.name()}': {e}")

                        elif 'storage_moved_alert' in a_type:
                            h = a.handle
                            try:
                                new_path = h.status().save_path
                                logger.info(f"✅ Storage successfully moved to NAS: '{h.name()}' -> {new_path}")
                                h.save_resume_data()
                                # Rimuovi dal set "move in corso": il move è completato,
                                # check_seeding_limits può ora usare il livello 2 (archive_path)
                                # se per qualsiasi motivo il flag fosse ancora presente.
                                try:
                                    getattr(cls, '_nas_move_pending', set()).discard(str(h.info_hash()))
                                except Exception:
                                    pass
                                # --- 3. RINOMINA DOPO LO SPOSTAMENTO ---
                                # Se il torrent è un singolo file, passa il path diretto al file
                                # così rename_completed_torrent non fa listdir sull'intera cartella NAS
                                torrent_file = os.path.join(new_path, h.name()) if h.name() else new_path
                                rename_save_path = torrent_file if os.path.isfile(torrent_file) else new_path
                                cls._trigger_renamer(h, rename_save_path)
                                cls._trigger_folder_scan(h)
                                # --- 4. NOTIFICA POST-PROCESSING ---
                                try:
                                    s = h.status()
                                    try:
                                        import core.config_db as _cdb3
                                        _pp_cfg = _cdb3.get_all_settings()
                                    except Exception:
                                        _pp_cfg = dict(cls._cfg_snapshot)
                                    from ..notifier import Notifier as _Notif
                                    _notif = _Notif(_pp_cfg)
                                    # Cerca il file rinominato nel nuovo path.
                                    # NON si basa sul conteggio dei file (fallisce su cartelle NAS
                                    # già popolate con episodi precedenti): usa la data di modifica
                                    # per identificare il file appena scritto (max 5 minuti fa).
                                    _renamed_to = None
                                    try:
                                        import os as _os2
                                        import time as _time2
                                        _video_exts2 = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv', '.webm'}
                                        _now2 = _time2.time()
                                        _RECENT_SECS = 300  # 5 minuti
                                        if _os2.path.isfile(new_path):
                                            # new_path è già il file diretto
                                            _renamed_to = _os2.path.basename(new_path)
                                        elif _os2.path.isdir(new_path):
                                            # Cerca il video toccato più di recente entro la finestra
                                            _candidates = []
                                            for _fn in _os2.listdir(new_path):
                                                if _os2.path.splitext(_fn)[1].lower() not in _video_exts2:
                                                    continue
                                                _fp = _os2.path.join(new_path, _fn)
                                                try:
                                                    _mtime = _os2.path.getmtime(_fp)
                                                except Exception:
                                                    continue
                                                if (_now2 - _mtime) <= _RECENT_SECS:
                                                    _candidates.append((_mtime, _fn))
                                            if _candidates:
                                                # Prende il più recente in caso di più candidati
                                                _candidates.sort(key=lambda x: x[0], reverse=True)
                                                _renamed_to = _candidates[0][1]
                                    except Exception:
                                        pass
                                    _notif.notify_post_processing(
                                        title_name   = h.name() or 'Sconosciuto',
                                        size_bytes   = s.total_wanted_done,
                                        time_sec     = s.active_time,
                                        action_log   = [],
                                        is_series    = True,
                                        is_processed = True,
                                        final_path   = new_path,
                                        renamed_to   = _renamed_to,
                                    )
                                except Exception as _ne:
                                    logger.warning(f"⚠️ Post-processing notification error: {_ne}")
                                # --- 5. AUTO-REMOVE POST-RENAME ---
                                # Se auto_remove_completed=yes, rimuove il torrent dalla sessione
                                # (senza cancellare i file) così al prossimo avvio non fa recheck
                                # inutile su file rinominati/spostati.
                                try:
                                    _ar_cfg = cls._cfg_snapshot or {}
                                    _do_ar = str(_ar_cfg.get('auto_remove_completed', 'no')).lower() in ('yes', 'true', '1')
                                    if _do_ar:
                                        _ih_ar = str(h.info_hash())
                                        cls.remove_torrent(_ih_ar, delete_files=False)
                                        logger.info(f"🗑️ Auto-removed (post-rename, NAS move): '{h.name() or _ih_ar}'")
                                except Exception as _are:
                                    logger.debug(f"auto_remove post-rename: {_are}")
                            except Exception as e:
                                logger.error(f"❌ Post-move error: {e}")

                        elif 'torrent_checked_alert' in a_type:
                            h = a.handle
                            try:
                                s = h.status()
                                pct = round(s.progress * 100, 1)
                                logger.info(f"🖥️ Integrity check complete for '{h.name()}': {pct}%")
                                
                                ih = str(h.info_hash())
                                queue = getattr(cls, '_recheck_pause_queue', set())
                                if ih in queue:
                                    queue.remove(ih)
                                    try:
                                        # Gli togliamo l'automazione, così il motore non lo riavvia da solo!
                                        h.unset_flags(cls._lt.torrent_flags.auto_managed)
                                    except AttributeError:
                                        h.auto_managed(False)
                                    h.pause()
                                    logger.info(f"⏸️ Torrent '{h.name()}' paused automatically.")
                            except Exception as e:
                                logger.warning(f"⚠️ Post-recheck error: {e}")

                    cls._session.wait_for_alert(1000)
            except Exception as e:
                logger.error(f"Error in alerts loop: {e}")
            time.sleep(0.5)

    @classmethod
    def shutdown(cls):
        logger.info("Shutting down libtorrent: saving state...")
        cls._save_ui_state()  # snapshot UI pre-shutdown
        cls._running = False
        if cls._session:
            try:
                torrents = [h for h in cls._session.get_torrents() if h.is_valid()]
                n = len(torrents)
                for h in torrents:
                    h.save_resume_data()
                # Aspetta che tutti i save_resume_data_alert arrivino (max 10s)
                saved = 0
                deadline = time.time() + 10
                while saved < n and time.time() < deadline:
                    alerts = cls._session.pop_alerts()
                    for a in alerts:
                        a_type = str(type(a))
                        if 'save_resume_data_alert' in a_type:
                            params = getattr(a, 'params', None) or getattr(a, 'resume_data', None)
                            if params:
                                try:
                                    data = cls._lt.write_resume_data(params)
                                    if isinstance(data, dict):
                                        data = cls._lt.bencode(data)
                                    ih = str(params.info_hash) if hasattr(params, 'info_hash') else str(a.handle.info_hash())
                                    fp = os.path.join(cls.STATE_DIR, f"{ih}.fastresume")
                                    tmp = fp + ".tmp"
                                    with open(tmp, 'wb') as f:
                                        f.write(data)
                                        f.flush()
                                        os.fsync(f.fileno())
                                    os.replace(tmp, fp)
                                except Exception as e:
                                    logger.warning(f"⚠️ shutdown save_resume: {e}")
                            saved += 1
                        elif 'save_resume_data_failed_alert' in a_type:
                            saved += 1
                    time.sleep(0.05)
                logger.info(f"✅ Shutdown: {saved}/{n} resume data salvati")
                cls._session.pause()
            except Exception as e:
                logger.warning(f"⚠️ Shutdown error: {e}")

    @classmethod
    def session_available(cls) -> bool:
        return cls._session is not None

    # ------------------------------------------------------------------
    # ADD
    # ------------------------------------------------------------------

    def add(self, magnet: str, cfg: dict) -> bool:
        if not self.enabled or not self.session_available():
            return False
        try:
            lt     = self.__class__._lt
            s      = self.__class__._session
            params = lt.parse_magnet_uri(magnet)

            # Arricchisce self.cfg con tutti i parametri dal DB (inclusi ramdisk_*)
            # che potrebbero non essere stati passati al costruttore.
            _full = self.__class__._load_full_cfg()
            if _full:
                self.cfg = {**_full, **self.cfg}   # self.cfg ha priorità su eventuali override

            final_path = self.save_path
            temp_path  = self.cfg.get('libtorrent_temp_dir', '').strip()
            params.save_path = self.__class__._resolve_initial_save_path(self.cfg)

            # Se il torrent va sul RAM disk, disabilita temporaneamente la prealloca:
            # preallocare in RAM prima di sapere la dimensione reale (i metadati arrivano
            # dopo) è controproducente e potrebbe riempire il RAM disk subito.
            # La prealloca viene ripristinata al valore config subito dopo l'add.
            _going_to_ramdisk = self.__class__._ramdisk_enabled(self.cfg) and \
                params.save_path == self.cfg.get('libtorrent_ramdisk_dir', '').strip()
            _global_preallocate = str(self.cfg.get('libtorrent_preallocate', 'no')).lower() in ('yes', 'true', '1')
            if _going_to_ramdisk and _global_preallocate:
                logger.debug("🐏 RAM disk target: disabilito prealloca temporaneamente")
                self.__class__._set_preallocate(False)
            # SMART 1: Controllo Spazio Disco
            # Skippato se il target è il RAM disk: min_free_space_gb è pensato per
            # il disco rigido, non per tmpfs. Il controllo capacità ramdisk avviene
            # in metadata_received_alert tramite _check_ramdisk_capacity().
            min_space_gb = float(self.cfg.get('min_free_space_gb', 0))
            if min_space_gb > 0 and not _going_to_ramdisk:
                import shutil
                try:
                    check_path = params.save_path if os.path.exists(params.save_path) else '/'
                    free_space = shutil.disk_usage(check_path).free
                    if free_space < (min_space_gb * 1024**3):
                        logger.error(f"🚫 Insufficient space! Liberi: {free_space/(1024**3):.2f} GB (Min: {min_space_gb} GB). Torrent discarded.")
                        return False
                except Exception as e:
                    logger.warning(f"⚠️ Disk space check error: {e}")

            dl_limit = int(self.cfg.get('libtorrent_dl_limit', 0))
            ul_limit = int(self.cfg.get('libtorrent_ul_limit', 0))
            # params.download_limit e upload_limit vogliono B/s, non KB/s
            if dl_limit > 0:
                params.download_limit = dl_limit * 1024
            if ul_limit > 0:
                params.upload_limit = ul_limit * 1024

            extra = self.cfg.get('libtorrent_extra_trackers', '')
            if extra:
                tr_list = [t.strip() for t in extra.split('|') if t.strip()]
                try:
                    params.trackers = list(params.trackers or []) + tr_list
                except Exception:
                    pass

            h      = s.add_torrent(params)

            # Ripristina la prealloca al valore globale dopo l'add
            if _going_to_ramdisk and _global_preallocate:
                self.__class__._set_preallocate(True)
                logger.debug("🐏 Prealloca ripristinata al valore globale")
            if str(self.cfg.get('libtorrent_sequential', 'no')).lower() in ('yes', 'true', '1'):
                try:
                    h.set_sequential_download(True)
                except Exception:
                    pass

            paused = str(self.cfg.get('libtorrent_paused', 'no')).lower() in ('yes', 'true', '1')
            h.save_resume_data()
            if paused:
                h.auto_managed(False)
                h.pause()
            else:
                h.auto_managed(True)
                h.resume()
            return True
        except Exception as e:
            logger.error(f'❌ libtorrent add: {e}')
            stats.errors += 1
            return False

    # ------------------------------------------------------------------
    # LISTING & STATS
    # ------------------------------------------------------------------

    @classmethod
    def list_torrents(cls) -> list:
        if not cls.session_available():
            return []
        try:
            result = []
            # 1. Carica il file dei limiti una sola volta per sicurezza
            s_db = cls._get_seed_limits_db()
            if not isinstance(s_db, dict): 
                s_db = {}
                
            for h in cls._session.get_torrents():
                try:
                    s = h.status()
                    
                    base_ul = getattr(s, 'all_time_upload', getattr(s, 'total_upload', 0))
                    protocol_ul = getattr(s, 'total_upload', 0) - getattr(s, 'total_payload_upload', getattr(s, 'total_upload', 0))
                    storico_ul = base_ul + max(0, protocol_ul)
                    
                    # Estrazione sicura dell'errore
                    err_msg = ""
                    if getattr(s, 'error', None):
                        err_msg = s.error.message() if hasattr(s.error, 'message') else str(s.error)
                        if err_msg.lower() == 'no error': err_msg = ""

                    # ---------------------------------------------------------
                    # 🚀 LOGICA QBITTORRENT: TRADUZIONE STATI AVANZATA
                    # ---------------------------------------------------------
                    raw_state = str(s.state).split('.')[-1] if '.' in str(s.state) else str(s.state)
                    
                    is_paused = getattr(s, 'paused', False)
                    is_auto_managed = getattr(s, 'auto_managed', False)
                    is_seeding  = getattr(s, 'is_seeding',  False) or raw_state == 'seeding'
                    is_finished = getattr(s, 'is_finished', False) or raw_state == 'finished'
                    is_done     = is_seeding or is_finished
                    
                    # Le velocità "payload" escludono il traffico di protocollo (ping tracker)
                    dl_rate = getattr(s, 'download_payload_rate', getattr(s, 'download_rate', 0))
                    ul_rate = getattr(s, 'upload_payload_rate', getattr(s, 'upload_rate', 0))
                    
                    if err_msg:
                        ui_state = "Errore"
                    elif raw_state == "checking_resume_data":
                        ui_state = "Controllo Dati"
                    elif raw_state == "allocating":
                        ui_state = "Allocazione Spazio"
                    elif raw_state == "downloading_metadata":
                        ui_state = "Attesa Metadati"
                    elif raw_state == "checking_files":
                        ui_state = "Controllo (100%)" if is_done else "Controllo File"
                    elif is_paused:
                        if is_seeding or is_finished:
                            ui_state = "In Coda (Seeding)" if is_auto_managed else "Seeding (Completato)"
                        else:
                            ui_state = "In Coda (DL)" if is_auto_managed else "In Pausa"
                    else:
                        if is_seeding:
                            ui_state = "In Seeding" if ul_rate > 0 else "Seeding (Fermo)"
                        elif is_finished:
                            ui_state = "Seeding (Fermo)"
                        else:
                            ui_state = "In Scarico" if dl_rate > 0 else "In Scarico (Fermo)"
                            
                    # Aggiunta badge Forzato [F] se l'utente ha tolto la gestione automatica
                    if not is_paused and not is_auto_managed and "Fermo" not in ui_state and "Completato" not in ui_state:
                        ui_state = f"{ui_state} [F]"
                    # Fallback stato UI: se siamo in una fase transitoria post-riavvio
                    # (Attesa Metadati, Controllo Dati) usiamo lo snapshot salvato prima dello shutdown
                    _transient = raw_state in ('checking_resume_data', 'downloading_metadata')
                    if _transient:
                        # Ricarica la cache se è None o se non ha ancora dati validi
                        _saved = getattr(cls, '_ui_state_cache', None)
                        _cache_valid = _saved and any(v.get('state') for v in _saved.values())
                        if not _cache_valid:
                            cls._ui_state_cache = cls._load_ui_state()
                            _saved = cls._ui_state_cache
                        _ih = str(h.info_hash())
                        if _ih in _saved:
                            _snap = _saved[_ih]
                            _fallback_state = _snap.get('state', '')
                            if _fallback_state:  # usa il fallback solo se non è vuoto
                                ui_state = _fallback_state
                    # ---------------------------------------------------------

                    save_p = getattr(s, 'save_path', '')
                    n = getattr(s, 'name', '')
                    physical_file = False
                    
                    # FIX: Libtorrent crea il file vuoto appena parte il download.
                    # Segnaliamo il file come "Fisicamente Presente" SOLO se il progresso 
                    # è al 100%, altrimenti la WebUI impazzisce e forza la barra al massimo!
                    if save_p and n and float(getattr(s, 'progress', 0)) >= 1.0:
                        physical_file = os.path.exists(os.path.join(save_p, n))
                    
                    # 2. Estrazione a prova di crash dei limiti di questo specifico torrent
                    my_lim = s_db.get(str(h.info_hash()))
                    if not isinstance(my_lim, dict): 
                        my_lim = {}
                    infinito = (my_lim.get('ratio', -1) == 0) or (my_lim.get('days', -1) == 0)
                    
                    result.append({
                        'hash':           str(h.info_hash()),
                        'name':           str(n) if n else '(metadata...)',
                        'state':          ui_state,
                        'progress':       float(getattr(s, 'progress', 0)),
                        'dl_rate':        int(getattr(s, 'download_rate', 0)),
                        'ul_rate':        int(getattr(s, 'upload_rate', 0)),
                        'total_size':     int(getattr(s, 'total_wanted', 0)),
                        'downloaded':     int(getattr(s, 'total_wanted_done', 0)),
                        'uploaded':       int(storico_ul),
                        'ratio':          float(round(storico_ul / max(1, getattr(s, 'total_wanted_done', 1)), 2)),
                        'num_peers':      int(getattr(s, 'num_peers', 0)),
                        'num_seeds':      int(getattr(s, 'num_seeds', 0)),
                        'paused':         is_paused,
                        'error':          str(err_msg),
                        'save_path':      str(save_p),
                        'eta':            int(cls._calc_eta(s)),
                        'added_time':     int(getattr(s, 'added_time', 0)),
                        'completed_time': int(getattr(s, 'completed_time', 0)),
                        'active_time':    int(getattr(s, 'active_time', 0)),
                        'seeding_time':   int(getattr(s, 'seeding_time', 0)),
                        'tracker':        str(getattr(s, 'current_tracker', '')),
                        'physical_file_found': physical_file,
                        'is_infinite':    infinito
                    })
                except Exception:
                    continue # Se un torrent crasha, salta solo quello e non distrugge l'intera lista
                    
            return result
        except Exception as e:
            logger.error(f'❌ libtorrent list_torrents: {e}')
            return []


    @classmethod
    def get_torrent_details(cls, info_hash: str) -> dict:
        """Estrae i dettagli avanzati (File, Trackers, Info) in stile qBittorrent"""
        h = cls._find(info_hash)
        if not h:
            return {'success': False, 'error': 'Torrent non trovato'}
        try:
            s = h.status()
            
            ti = None
            if h.has_metadata():
                try: ti = h.torrent_file()
                except AttributeError: ti = h.get_torrent_info()
            
            # --- 1. Estrazione Trackers (Protetto dal bug dizionario) ---
            trackers = []
            try:
                for tr in h.trackers():
                    if isinstance(tr, dict):
                        trackers.append({
                            'url': tr.get('url', ''),
                            'msg': tr.get('message', tr.get('msg', '')),
                            'tier': tr.get('tier', 0)
                        })
                    else:
                        trackers.append({
                            'url': getattr(tr, 'url', str(tr)),
                            'msg': getattr(tr, 'message', ''),
                            'tier': getattr(tr, 'tier', 0)
                        })
            except Exception: pass
                
            # --- 2. Estrazione File e Percentuali (Protetto) ---
            files = []
            if ti:
                try:
                    try: fp = h.file_progress()
                    except Exception: fp = h.file_progress(0)
                    
                    fs = ti.files()
                    for i in range(ti.num_files()):
                        size = fs.file_size(i)
                        prog_bytes = fp[i] if i < len(fp) else 0
                        files.append({
                            'path': fs.file_path(i),
                            'size': size,
                            'progress': (prog_bytes / max(1, size) * 100) if size > 0 else 100
                        })
                except Exception: pass
                    
            # --- 3. Statistiche Globali (Protetto) ---
            base_ul = getattr(s, 'all_time_upload', getattr(s, 'total_upload', 0))
            protocol_ul = getattr(s, 'total_upload', 0) - getattr(s, 'total_payload_upload', getattr(s, 'total_upload', 0))
            storico_ul = base_ul + max(0, protocol_ul)
            downloaded = getattr(s, 'total_wanted_done', getattr(s, 'total_done', 1))
            ratio = round(storico_ul / max(1, downloaded), 2)
            
            state_str = str(s.state).split('.')[-1] if '.' in str(s.state) else str(s.state)
            
            return {
                'success': True,
                'hash': info_hash,
                'name': getattr(s, 'name', info_hash) or info_hash,
                'save_path': getattr(s, 'save_path', ''),
                'state': state_str,
                'total_size': getattr(s, 'total_wanted', 0),
                'downloaded': downloaded,
                'uploaded': storico_ul,
                'ratio': ratio,
                'dl_rate': getattr(s, 'download_rate', 0),
                'ul_rate': getattr(s, 'upload_rate', 0),
                'active_time': getattr(s, 'active_time', 0),
                'seeding_time': getattr(s, 'seeding_time', 0),
                'seeds': getattr(s, 'num_seeds', 0),
                'peers': getattr(s, 'num_peers', 0),
                'total_seeds': getattr(s, 'list_seeds', 0),
                'total_peers': getattr(s, 'list_peers', 0),
                'pieces': ti.num_pieces() if ti else getattr(s, 'num_pieces', 0),
                'piece_size': ti.piece_length() if ti else 0,
                'trackers': trackers,
                'files': files,
                'dl_limit': h.download_limit() if hasattr(h, 'download_limit') else -1,
                'ul_limit': h.upload_limit()   if hasattr(h, 'upload_limit')   else -1,
                'seed_ratio': cls._get_seed_limits_db().get(info_hash, {}).get('ratio', -1),
                'seed_days': cls._get_seed_limits_db().get(info_hash, {}).get('days', -1),
            }
        except Exception as e:
            logger.error(f"Critical error reading torrent details: {e}")
            return {'success': False, 'error': str(e)}

    @classmethod
    def _calc_eta(cls, s) -> int:
        try:
            remaining = s.total_wanted - s.total_wanted_done
            if s.download_rate > 0 and remaining > 0:
                return int(remaining / s.download_rate)
        except Exception:
            pass
        return -1

    @classmethod
    def global_stats(cls) -> dict:
        try:
            import libtorrent  # noqa: F401
            lt_installed = True
        except ImportError:
            lt_installed = False

        if not cls.session_available():
            return {
                'dl_rate': 0, 'ul_rate': 0, 'num_torrents': 0,
                'active': 0, 'paused': 0,
                'lt_installed': lt_installed,
                'is_listening': False, 'listen_port': 0,
                'upnp_active': False,
            }
        try:
            s        = cls._session
            torrents = s.get_torrents()
            total_dl = sum(h.status().download_rate for h in torrents)
            total_ul = sum(h.status().upload_rate   for h in torrents)
            try:
                listen_port = s.listen_port()
                # is_listening: porta > 0 OPPURE s.is_listening() se disponibile
                # Evita falso 'offline' transitorio durante riapplicazione impostazioni
                if listen_port > 0:
                    is_listening = True
                elif hasattr(s, 'is_listening'):
                    is_listening = bool(s.is_listening())
                else:
                    is_listening = False
            except Exception:
                listen_port, is_listening = 0, False
            cfg         = cls._cfg_snapshot
            upnp_active = (cfg.get('libtorrent_upnp', 'yes') == 'yes' and is_listening)
            return {
                'dl_rate':      total_dl,
                'ul_rate':      total_ul,
                'num_torrents': len(torrents),
                'active':       sum(1 for h in torrents if not h.status().paused),
                'paused':       sum(1 for h in torrents if     h.status().paused),
                'lt_installed': lt_installed,
                'is_listening': is_listening,
                'listen_port':  listen_port,
                'upnp_active':  upnp_active,
            }
        except Exception:
            return {
                'dl_rate': 0, 'ul_rate': 0, 'num_torrents': 0,
                'active': 0, 'paused': 0,
                'lt_installed': lt_installed,
                'is_listening': False, 'listen_port': 0,
                'upnp_active': False,
            }

    # ------------------------------------------------------------------
    # TORRENT CONTROL
    # ------------------------------------------------------------------

    @classmethod
    def pause_torrent(cls, info_hash: str) -> bool:
        h = cls._find(info_hash)
        if h:
            try:
                # FIX: Disattiva la gestione automatica così libtorrent non lo riavvia da solo
                h.unset_flags(cls._lt.torrent_flags.auto_managed)
            except AttributeError:
                h.auto_managed(False)
            h.pause()
            logger.info(f"⏸️ Torrent manually paused (auto_managed OFF): {h.status().name}")
            return True
        return False

    @classmethod
    def resume_torrent(cls, info_hash: str) -> bool:
        h = cls._find(info_hash)
        if h:
            try:
                # FIX: Riattiva la gestione automatica per permettere al motore di gestirlo
                h.set_flags(cls._lt.torrent_flags.auto_managed)
            except AttributeError:
                h.auto_managed(True)
            h.resume()
            logger.info(f"▶️ Torrent restarted (auto_managed ON): {h.status().name}")
            return True
        return False

    @classmethod
    def recheck_torrent(cls, info_hash: str) -> bool:
        h = cls._find(info_hash)
        if h:
            try:
                s = h.status()
                was_paused = s.paused
                
                # Inizializza la memoria segreta del backend se non esiste
                if not hasattr(cls, '_recheck_pause_queue'):
                    cls._recheck_pause_queue = set()
                    
                logger.info(f"🔧 Request Force Recheck per: {s.name} (was paused: {was_paused})")
                
                if was_paused:
                    # Si appunta l'hash per rimetterlo in pausa alla fine
                    cls._recheck_pause_queue.add(str(h.info_hash()))
                    try:
                        h.unset_flags(cls._lt.torrent_flags.auto_managed)
                    except AttributeError:
                        h.auto_managed(False)
                    h.resume()

                h.force_recheck()
                return True
            except Exception as e:
                logger.error(f"❌ Torrent recheck error: {e}")
        return False
        
    @classmethod
    def remove_torrent(cls, info_hash: str, delete_files: bool = False) -> bool:
        h = cls._find(info_hash)
        if h:
            ih = str(h.info_hash())
            # Salva il nome prima della rimozione (l'handle potrebbe non essere valido dopo)
            torrent_name = h.name() or ih
            try:
                try:
                    flags = cls._lt.options_t.delete_files if delete_files else 0
                except AttributeError:
                    flags = 1 if delete_files else 0
                cls._session.remove_torrent(h, flags)
            except Exception as e:
                logger.error(f"Session removal error: {e}")
                return False
            try:
                resume_path = os.path.join(cls.STATE_DIR, f"{ih}.fastresume")
                if os.path.exists(resume_path):
                    os.remove(resume_path)
                torrent_path = os.path.join(cls.STATE_DIR, f"{ih}.torrent")
                if os.path.exists(torrent_path):
                    os.remove(torrent_path)
                action = "rimosso (con file)" if delete_files else "rimosso"
                logger.info(f"🗑️ Torrent {action}: '{torrent_name}'")
                # Rimuove il limite dal DB (se presente)
                try:
                    import core.config_db as _cdb
                    _cdb.delete_torrent_limit(ih)
                except Exception as _le:
                    logger.debug(f"remove_torrent: DB limit cleanup failed: {_le}")
            except Exception as e:
                logger.warning(f"⚠️ Unable to remove .fastresume file: {e}")
            return True
        return False

    @classmethod
    def apply_speed_limits(cls, dl_bytes: int, ul_bytes: int) -> bool:
        """Applica i limiti DL/UL globali di sessione direttamente, senza rileggere il config.
        Propaga anche a tutti i torrent attivi: il limite per-torrent (se 0 = illimitato)
        sovrascrive quello di sessione in libtorrent, quindi va azzerato esplicitamente.
        """
        s = cls._session
        if s is None:
            return False
        dl_kb = dl_bytes // 1024
        ul_kb = ul_bytes // 1024
        logger.info(f"🚦 Applying speed limits → DL: {dl_kb} KB/s, UL: {ul_kb} KB/s")
        # Aggiorna _cfg_snapshot in KB/s così check_speed_schedule non sovrascrive
        # il limite appena impostato con il valore vecchio dello snapshot
        if cls._cfg_snapshot is not None:
            cls._cfg_snapshot['libtorrent_dl_limit'] = str(dl_kb)
            cls._cfg_snapshot['libtorrent_ul_limit'] = str(ul_kb)
        try:
            s.apply_settings({'download_rate_limit': dl_bytes, 'upload_rate_limit': ul_bytes})
        except Exception:
            try:
                ss = s.get_settings()
                ss['download_rate_limit'] = dl_bytes
                ss['upload_rate_limit']   = ul_bytes
                s.apply_settings(ss)
            except Exception as e:
                logger.warning(f"apply_speed_limits fallback failed: {e}")
                return False
        # Propaga a tutti i torrent attivi: se il torrent ha limite per-torrent = 0
        # (illimitato), sovrascrive il limite di sessione — va resettato a -1
        # (= usa limite di sessione) affinché il globale abbia effetto.
        try:
            for h in s.get_torrents():
                try:
                    cur_dl = h.download_limit() if hasattr(h, 'download_limit') else 0
                    cur_ul = h.upload_limit()   if hasattr(h, 'upload_limit')   else 0
                    # 0 = illimitato per-torrent → sovrascrive la sessione, va resettato
                    if cur_dl == 0:
                        h.set_download_limit(-1)
                    if cur_ul == 0:
                        h.set_upload_limit(-1)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"apply_speed_limits: torrent propagation error: {e}")
        return True
        
    @classmethod
    def _get_seed_limits_db(cls):
        p = os.path.join(cls.STATE_DIR, 'seed_limits.json')
        try:
            import json
            if os.path.exists(p):
                with open(p, 'r') as f: return json.load(f)
        except Exception: pass
        return {}

    @classmethod
    def _save_seed_limits_db(cls, db_dict):
        p = os.path.join(cls.STATE_DIR, 'seed_limits.json')
        try:
            import json
            with open(p, 'w') as f: json.dump(db_dict, f)
        except Exception: pass    

    @classmethod
    def set_torrent_limits(cls, info_hash: str, dl_limit: int, ul_limit: int, seed_ratio: float = -1.0, seed_days: float = -1.0) -> bool:
        h = cls._find(info_hash)
        if h:
            h.set_download_limit(dl_limit)
            h.set_upload_limit(ul_limit)
            try:
                import core.config_db as _cdb
                _cdb.set_torrent_limit(info_hash, dl_bytes=dl_limit, ul_bytes=ul_limit)
            except Exception as _le:
                logger.debug(f"set_torrent_limits: DB save failed: {_le}")
                
            # Salva le regole di seeding specifiche per questo torrent
            s_db = cls._get_seed_limits_db()
            if seed_ratio < 0 and seed_days < 0:
                s_db.pop(info_hash, None)
            else:
                if info_hash not in s_db: s_db[info_hash] = {}
                s_db[info_hash]['ratio'] = seed_ratio
                s_db[info_hash]['days'] = seed_days
            cls._save_seed_limits_db(s_db)
            
            return True
        return False

    @classmethod
    def get_peers(cls, info_hash: str) -> dict:
        h = cls._find(info_hash)
        if not h:
            return {'success': False, 'peers': []}
        try:
            peers = []
            for p in h.get_peer_info():
                # flags utili
                flags = []
                try:
                    if getattr(p, 'seed', False):            flags.append('S')
                    if getattr(p, 'optimistic_unchoke', False): flags.append('O')
                    if getattr(p, 'snubbed', False):         flags.append('K')
                    if getattr(p, 'upload_only', False):     flags.append('U')
                    if getattr(p, 'endgame_mode', False):    flags.append('E')
                except Exception:
                    pass
                try:
                    raw_ip = p.ip[0] if isinstance(p.ip, tuple) else p.ip
                    ip_str = raw_ip.decode('utf-8') if isinstance(raw_ip, bytes) else str(raw_ip)
                except Exception:
                    ip_str = '?'
                try:
                    raw_client = getattr(p, 'client', '') or b''
                    client_str = raw_client.decode('utf-8', errors='replace') if isinstance(raw_client, bytes) else str(raw_client)
                    client_str = client_str.strip() or '?'
                except Exception:
                    client_str = '?'
                peers.append({
                    'ip':       ip_str,
                    'client':   client_str,
                    'dl_speed': int(getattr(p, 'down_speed', 0) or 0),
                    'ul_speed': int(getattr(p, 'up_speed',   0) or 0),
                    'progress': round(float(getattr(p, 'progress', 0) or 0) * 100, 1),
                    'flags':    ' '.join(flags),
                    'source':   int(getattr(p, 'source', 0) or 0),
                })
            # Ordina per velocità DL decrescente
            peers.sort(key=lambda x: x['dl_speed'], reverse=True)
            return {'success': True, 'peers': peers}
        except Exception as e:
            logger.error(f'get_peers error: {e}')
            return {'success': False, 'peers': [], 'error': str(e)}

    @classmethod
    def add_magnet(cls, magnet: str, save_path: str = '') -> bool:
        if not cls.session_available():
            logger.warning('add_magnet: session not available')
            return False
        try:
            lt        = cls._lt
            s         = cls._session
            # Usa _load_full_cfg per avere TUTTI i parametri dal DB (inclusi
            # libtorrent_ramdisk_* che non transitano per _apply_settings).
            # Aggiorna anche _cfg_snapshot così i controlli successivi li vedono.
            cfg = cls._load_full_cfg()
            if cfg:
                cls._cfg_snapshot.update(cfg)
            else:
                cfg = cls._cfg_snapshot
            final_dir = cfg.get('libtorrent_dir', '/downloads').strip()
            temp_dir  = cfg.get('libtorrent_temp_dir', '').strip()
            target    = save_path.strip()

            # Usa _resolve_initial_save_path (logica ramdisk) se:
            # - nessun save_path esplicito, oppure
            # - il save_path passato coincide con final_dir o temp_dir
            #   (cioè il chiamante non ha una destinazione specifica diversa da quella di default)
            _norm = os.path.normpath
            _is_default = (
                not target or
                _norm(target) == _norm(final_dir) or
                (temp_dir and _norm(target) == _norm(temp_dir))
            )
            if _is_default:
                target = cls._resolve_initial_save_path(cfg)
            else:
                logger.debug(f"add_magnet: save_path esplicito non-default → {target}")

            params           = lt.parse_magnet_uri(magnet)
            params.save_path = target

            # Disabilita prealloca temporaneamente se il target è il RAM disk
            _going_to_ramdisk = cls._ramdisk_enabled(cfg) and \
                target == cfg.get('libtorrent_ramdisk_dir', '').strip()
            _global_preallocate = str(cfg.get('libtorrent_preallocate', 'no')).lower() in ('yes', 'true', '1')
            if _going_to_ramdisk and _global_preallocate:
                logger.debug("🐏 RAM disk target: disabilito prealloca temporaneamente")
                cls._set_preallocate(False)
            # SMART 1: Controllo Spazio Disco
            # Skippato se il target è il RAM disk: min_free_space_gb è pensato per
            # il disco rigido, non per tmpfs. Il controllo capacità ramdisk avviene
            # in metadata_received_alert tramite _check_ramdisk_capacity().
            min_space_gb = float(cfg.get('min_free_space_gb', 0))
            if min_space_gb > 0 and not _going_to_ramdisk:
                try:
                    import shutil
                    check_path = target if os.path.exists(target) else '/'
                    free_space = shutil.disk_usage(check_path).free
                    if free_space < (min_space_gb * 1024**3):
                        logger.error(f"🚫 Insufficient disk space! Free: {free_space/(1024**3):.2f} GB. Manual download cancelled.")
                        return False
                except Exception:
                    pass

            extra = cfg.get('libtorrent_extra_trackers', '')
            if extra:
                extra_tr = [t.strip() for t in extra.split('|') if t.strip()]
                try:
                    params.trackers = list(params.trackers or []) + extra_tr
                except Exception:
                    pass

            h      = s.add_torrent(params)
            h.save_resume_data()

            # Ripristina la prealloca al valore globale dopo l'add
            if _going_to_ramdisk and _global_preallocate:
                cls._set_preallocate(True)
                logger.debug("🐏 Prealloca ripristinata al valore globale")
            if str(cfg.get('libtorrent_sequential', 'no')).lower() in ('yes', 'true', '1'):
                try:
                    h.set_sequential_download(True)
                    logger.info("▶️ Sequential download ENABLED for streaming")
                except Exception:
                    pass

            paused = str(cfg.get('libtorrent_paused', 'no')).lower() in ('yes', 'true', '1')
            if paused:
                try:
                    h.unset_flags(lt.torrent_flags.auto_managed)
                except AttributeError:
                    h.auto_managed(False)
                h.pause()
                logger.info(f'✅ Magnet added (paused): {magnet[:40]}... → {target}')
            else:
                try:
                    h.set_flags(lt.torrent_flags.auto_managed)
                except AttributeError:
                    h.auto_managed(True)
                h.resume()
                logger.info(f'✅ Magnet added: {magnet[:40]}... → {target}')

            try:
                cls._save_to_archive(magnet)
            except Exception as e:
                logger.warning(f'⚠️  Archive save failed: {e}')
            return True
        except Exception as e:
            logger.error(f'❌ libtorrent add_magnet (API): {e}')
            return False

    @classmethod
    def _save_to_archive(cls, magnet: str):
        from urllib.parse import parse_qs, urlparse
        if not magnet.startswith('magnet:?'):
            return
        try:
            parsed = urlparse(magnet)
            params = parse_qs(parsed.query)
            name   = params.get('dn', [f"Manuale_{int(time.time())}"])[0]
            from ..database import ArchiveDB
            db_arch = ArchiveDB()
            db_arch.add_item(name=name, magnet=magnet, source='Aggiunta Manuale')
            logger.info(f"📝 Manual torrent registered in archive: {name}")
        except Exception as e:
            logger.warning(f"⚠️  Unable to register in archive: {e}")

    @classmethod
    def _find(cls, info_hash: str):
        if not cls.session_available():
            return None
        ih = info_hash.lower()
        for h in cls._session.get_torrents():
            if str(h.info_hash()).lower() == ih:
                return h
        return None

    # ------------------------------------------------------------------
    # SPEED SCHEDULE & SEEDING WATCHDOG
    # ------------------------------------------------------------------

    @classmethod
    def check_speed_schedule(cls):
        cfg = cls._cfg_snapshot
        if not cls.session_available() or not cfg:
            return
        if str(cfg.get('libtorrent_sched_enabled', 'no')).lower() not in ('yes', 'true', '1'):
            return
        try:
            from datetime import datetime, time as dtime
            now     = datetime.now()
            now_t   = now.time().replace(second=0, microsecond=0)
            weekday = now.weekday()   # 0=lun … 6=dom

            # Giorni attivi: stringa "0,1,2,3,4" → set di int; vuoto = tutti
            days_str = str(cfg.get('libtorrent_sched_days', '')).strip()
            if days_str:
                active_days = set(int(d.strip()) for d in days_str.split(',') if d.strip().isdigit())
            else:
                active_days = set(range(7))

            day_ok = weekday in active_days

            def parse_t(s, default):
                try:
                    h, m = s.strip().split(':')
                    return dtime(int(h), int(m))
                except Exception:
                    return default

            t_start = parse_t(cfg.get('libtorrent_sched_start', '23:00'), dtime(23, 0))
            t_end   = parse_t(cfg.get('libtorrent_sched_end',   '08:00'), dtime(8,  0))

            if t_start <= t_end:
                in_time = t_start <= now_t < t_end
            else:
                in_time = now_t >= t_start or now_t < t_end

            in_schedule = day_ok and in_time

            sched_dl = int(cfg.get('libtorrent_sched_dl_limit', 0)) * 1024
            sched_ul = int(cfg.get('libtorrent_sched_ul_limit', 0)) * 1024
            base_dl  = int(cfg.get('libtorrent_dl_limit', 0)) * 1024
            base_ul  = int(cfg.get('libtorrent_ul_limit', 0)) * 1024
            dl = sched_dl if in_schedule else base_dl
            ul = sched_ul if in_schedule else base_ul

            s = cls._session
            try:
                s.apply_settings({'download_rate_limit': int(dl), 'upload_rate_limit': int(ul)})
            except Exception:
                ss = s.get_settings()
                ss['download_rate_limit'] = dl
                ss['upload_rate_limit']   = ul
                s.apply_settings(ss)

            prev = getattr(cls, '_sched_state', None)
            if prev != in_schedule:
                cls._sched_state = in_schedule
                day_names = ['Lun','Mar','Mer','Gio','Ven','Sab','Dom']
                days_label = ','.join(day_names[d] for d in sorted(active_days)) if days_str else 'tutti'
                if in_schedule:
                    logger.info(f'🕐 Scheduler ACTIVE ({t_start.strftime("%H:%M")}–{t_end.strftime("%H:%M")} days:{days_label}): DL={dl//1024}KB/s UL={ul//1024}KB/s')
                else:
                    logger.info(f'🕐 Scheduler INACTIVE (day:{day_names[weekday]} time:{now_t.strftime("%H:%M")}): DL={dl//1024}KB/s UL={ul//1024}KB/s (base limits)')
        except Exception as e:
            logger.error(f'❌ Speed scheduler error: {e}')

    @classmethod
    def check_seeding_limits(cls):
        cfg = cls._cfg_snapshot
        if not cls.session_available() or not cfg:
            return

        stop_global = str(cfg.get('libtorrent_stop_at_ratio', 'no')).lower() in ('yes', 'true', '1')
        
        try:
            global_ratio = float(cfg.get('libtorrent_seed_ratio', 0))
            # Supporta sia la vecchia config in minuti che la nuova in giorni
            global_days  = float(cfg.get('libtorrent_seed_time_days', 0))
            global_mins  = int(cfg.get('libtorrent_seed_time', 0))
            global_time  = int(global_days * 86400) if global_days > 0 else (global_mins * 60)
        except Exception:
            return

        s_db = cls._get_seed_limits_db()

        for h in cls._session.get_torrents():
            try:
                s = h.status()
                if s.paused or not s.is_seeding:
                    continue
                
                # --- CALCOLO REGOLE PER IL SINGOLO TORRENT ---
                my_limits = s_db.get(str(h.info_hash()), {})
                my_ratio = my_limits.get('ratio', -1)
                my_days = my_limits.get('days', -1)
                
                target_ratio = my_ratio if my_ratio >= 0 else global_ratio
                target_time = int(my_days * 86400) if my_days >= 0 else global_time
                
                # Se ha regole specifiche ed entrambe sono 0, significa "Seeding Illimitato" (salta il blocco)
                if my_ratio == 0 or my_days == 0:
                    continue
                    
                # Se non ha regole specifiche e le impostazioni globali dicono di NON fermare, salta
                if not stop_global and my_ratio < 0 and my_days < 0:
                    continue
                # -----------------------------------------------

                done     = s.total_wanted_done
                base_ul  = getattr(s, 'all_time_upload', s.total_upload)
                protocol_ul = s.total_upload - getattr(s, 'total_payload_upload', s.total_upload)
                upl      = base_ul + max(0, protocol_ul)
                
                ratio_ok = target_ratio > 0 and done > 0 and (upl / done) >= target_ratio
                time_ok  = target_time  > 0 and s.seeding_time >= target_time
                
                if ratio_ok or time_ok:
                    # Spegne l'auto-managed così libtorrent non lo riavvia da solo
                    try:
                        h.unset_flags(cls._lt.torrent_flags.auto_managed)
                    except AttributeError:
                        h.auto_managed(False)

                    h.pause()
                    logger.info(f'🔵 Seeding limit reached, torrent permanently paused: {s.name}')

                    # ... Codice di spostamento nel NAS (rimane invariato) ...
                    try:
                        ramdisk_dir = cfg.get('libtorrent_ramdisk_dir', '').strip()
                        temp_dir    = cfg.get('libtorrent_temp_dir',    '').strip()
                        final_dir   = cfg.get('libtorrent_dir',         '').strip()
                        _tmp_candidates = [d for d in (ramdisk_dir, temp_dir) if d and d != final_dir]
                        if _tmp_candidates and final_dir:
                            curr_save = os.path.normpath(os.path.abspath(s.save_path))
                            _in_tmp = any(curr_save.startswith(os.path.normpath(os.path.abspath(d))) for d in _tmp_candidates)
                            if _in_tmp:
                                ih_str = str(h.info_hash())
                                _pending = getattr(cls, '_nas_move_pending', set())
                                if ih_str in _pending:
                                    logger.debug(f'📦 Post-seeding: NAS move already in progress for {s.name}, skip libtorrent_dir move')
                                else:
                                    _already_on_nas = False
                                    try:
                                        from ..models import Parser as _P_sl
                                        from ..config import Config as _C_sl
                                        _ep_sl = _P_sl.parse_series_episode(s.name or '')
                                        if _ep_sl:
                                            _m_sl = _C_sl().find_series_match(_ep_sl['name'], _ep_sl['season'])
                                            if _m_sl and _m_sl.get('archive_path'):
                                                _nas_sl = os.path.normpath(os.path.abspath(_m_sl['archive_path'].strip()))
                                                if curr_save.startswith(_nas_sl): _already_on_nas = True
                                    except Exception: pass
                                    if not _already_on_nas:
                                        h.move_storage(final_dir)
                                        logger.info(f'📦 Post-seeding: moving from temp → libtorrent_dir: {s.name}')
                    except Exception as _te:
                        logger.debug(f'⚠️ temp_dir cleanup after seeding failed for {s.name}: {_te}')
            except Exception:
                pass

    # ------------------------------------------------------------------
    # IP FILTER
    # ------------------------------------------------------------------

    _ipfilter_rules_count: int = 0
    _ipfilter_last_updated: float = 0.0
    _ipfilter_last_url: str = ""
    _IPFILTER_CACHE = os.path.join(STATE_DIR, 'ipfilter.cache') if STATE_DIR else '/tmp/ipfilter.cache'

    @classmethod
    def load_ipfilter_from_file(cls, path: str) -> int:
        """Carica un file ipfilter (formato P2P/dat) nella sessione. Ritorna numero di regole."""
        if not cls.session_available():
            return 0
        lt = cls._lt
        try:
            f_obj = lt.ip_filter()
            count = 0
            
            # Nelle versioni più recenti/vecchie di libtorrent il flag blocked potrebbe essere assente, il fallback sicuro è 1
            blocked_flag = getattr(lt.ip_filter, 'blocked', 1)
            first_err = None

            with open(path, 'r', errors='ignore') as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    # Formato P2P: "Descrizione_o_Nome:1.2.3.4-5.6.7.8"
                    if ':' in line:
                        parts = line.rsplit(':', 1)
                        range_part = parts[-1].strip()
                    else:
                        range_part = line.strip()
                        
                    if '-' in range_part:
                        start_ip, end_ip = range_part.split('-', 1)
                        try:
                            f_obj.add_rule(start_ip.strip(), end_ip.strip(), blocked_flag)
                            count += 1
                        except Exception as e:
                            if not first_err:
                                first_err = str(e)
            
            # Logghiamo eventuali anomalie silenziose
            if count == 0 and first_err:
                logger.warning(f"⚠️ IP parsing failed. First error detected: {first_err}")

            cls._session.set_ip_filter(f_obj)
            cls._ipfilter_rules_count = count
            cls._ipfilter_last_updated = time.time()
            logger.info(f'🛡️ IP filter loaded: {count} rules')
            return count
        except Exception as e:
            logger.error(f'❌ IP filter load error: {e}')
            return 0

    @classmethod
    def update_ipfilter_from_url(cls, url: str) -> dict:
        """
        Scarica una blocklist da URL (supporta .gz, .zip, .p2p, .dat, .txt).
        Ritorna {'ok': bool, 'rules_count': int, 'error': str}.
        """
        import urllib.request
        import gzip
        import zipfile
        import tempfile
        import io
        import ssl

        try:
            logger.info(f'🛡️ Download blocklist: {url}')
            
            # 1. Ignoriamo gli errori dei certificati SSL (spesso bloccanti su Linux/NAS)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            # 2. Fingiamo di essere un browser per evitare blocchi anti-bot
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                raw = resp.read()

            # Decompressione automatica
            data = None
            url_lower = url.lower().split('?')[0]

            if url_lower.endswith('.gz') or raw[:2] == b'\x1f\x8b':
                data = gzip.decompress(raw).decode('utf-8', errors='ignore')
            elif url_lower.endswith('.zip') or raw[:4] == b'PK\x03\x04':
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for name in zf.namelist():
                        if any(name.lower().endswith(ext) for ext in ('.p2p', '.dat', '.txt', '.list')):
                            data = zf.read(name).decode('utf-8', errors='ignore')
                            break
                    if data is None and zf.namelist():
                        data = zf.read(zf.namelist()[0]).decode('utf-8', errors='ignore')
            else:
                data = raw.decode('utf-8', errors='ignore')

            if not data:
                return {'ok': False, 'rules_count': 0, 'error': 'Dati vuoti o formato non riconosciuto'}
                
            # Protezione Paywall/HTML
            if '<html' in data[:200].lower() or '<body' in data[:200].lower():
                logger.warning("⚠️ The URL returned a web page (HTML).")
                return {'ok': False, 'rules_count': 0, 'error': 'Il sito ha restituito una pagina web al posto della lista.'}

            # Salva cache locale
            cache_path = cls._IPFILTER_CACHE
            try:
                with open(cache_path, 'w', encoding='utf-8') as cf:
                    cf.write(data)
            except Exception as e:
                pass

            # Carica in libtorrent tramite file temporaneo
            with tempfile.NamedTemporaryFile(mode='w', suffix='.p2p', delete=False, encoding='utf-8') as tf:
                tf.write(data)
                tf_path = tf.name

            try:
                count = cls.load_ipfilter_from_file(tf_path)
                # Salviamo l'URL solo se ha effettivamente caricato qualcosa
                if count > 0:
                    cls._ipfilter_last_url = url
            finally:
                try:
                    os.unlink(tf_path)
                except Exception:
                    pass

            return {'ok': True, 'rules_count': count}
        except Exception as e:
            logger.error(f'❌ update_ipfilter_from_url: {e}')
            return {'ok': False, 'rules_count': 0, 'error': str(e)}

    @classmethod
    def ipfilter_status(cls) -> dict:
        return {
            'active':       cls._ipfilter_rules_count > 0,
            'rules_count':  cls._ipfilter_rules_count,
            'last_updated': cls._ipfilter_last_updated or None,
            'url':          getattr(cls, '_ipfilter_last_url', '')
        }
        
    @classmethod
    def _maybe_autoupdate_ipfilter(cls):
        """Chiamato periodicamente dall'alert loop: aggiorna blocklist ogni 24h se configurato."""
        cfg = cls._cfg_snapshot
        if str(cfg.get('libtorrent_ipfilter_autoupdate', 'no')).lower() != 'yes':
            return
        url = str(cfg.get('libtorrent_ipfilter_url', '')).strip()
        if not url:
            return
        last = cls._ipfilter_last_updated
        if last and (time.time() - last) < 86400:
            return
        logger.info('🛡️ Auto-updating IP filter...')
        cls.update_ipfilter_from_url(url)
