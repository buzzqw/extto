"""
EXTTO - Client qBittorrent.
"""

import re
import requests
from ..constants import logger
from ..models import stats


class QbtClient:
    def __init__(self, cfg: dict):
        self.enabled = str(cfg.get('qbittorrent_enabled', 'no')).lower() in ('yes', 'true', '1')
        if not self.enabled:
            return

        self.url  = cfg.get('qbittorrent_url', 'http://localhost:8080')
        self.sess = requests.Session()

        try:
            res = self.sess.post(
                f"{self.url}/api/v2/auth/login",
                data={
                    'username': cfg.get('qbittorrent_username', 'admin'),
                    'password': cfg.get('qbittorrent_password', 'adminadmin')
                },
                timeout=5
            )
            res.raise_for_status()
        except Exception as e:
            logger.exception(f"❌ qBittorrent login failed: {e}")
            self.enabled = False

    def add(self, magnet: str, cfg: dict) -> bool:
        if not self.enabled:
            return False

        cat   = cfg.get('qbittorrent_category', 'tv')
        pause = str(cfg.get('qbittorrent_paused', 'no')).lower() in ('yes', 'true', '1')

        try:
            # paused/stopped e category passati direttamente nell'unica chiamata POST.
            # L'API v2 di qBittorrent li gestisce nativamente: niente loop di attesa.
            # 'stopped' è il parametro introdotto nelle versioni più recenti (≥5.x),
            # 'paused' rimane per compatibilità con le versioni precedenti.
            form = {
                'urls':     magnet,
                'category': cat,
                'paused':   'true' if pause else 'false',
                'autoTMM':  'false',
                'stopped':  'true' if pause else 'false'
            }
            mp = {k: (None, v) for k, v in form.items()}
            self.sess.post(f"{self.url}/api/v2/torrents/add", files=mp, timeout=10)
            return True
        except Exception as e:
            logger.exception(f"❌ Error adding torrent to qBittorrent: {e}")
            stats.errors += 1
            return False
