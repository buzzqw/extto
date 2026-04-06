"""
EXTTO - Client Transmission RPC.
"""

import base64
import requests
from ..constants import logger


class TransmissionClient:
    """Client Transmission RPC"""

    def __init__(self, cfg: dict):
        self.enabled = str(cfg.get('transmission_enabled', 'no')).lower() in ('yes', 'true', '1')
        if not self.enabled:
            return

        self.url  = cfg.get('transmission_url', 'http://localhost:9091/transmission/rpc')
        self.sess = requests.Session()

        username = cfg.get('transmission_username', '')
        password = cfg.get('transmission_password', '')
        if username:
            auth_str = base64.b64encode(f"{username}:{password}".encode()).decode()
            self.sess.headers['Authorization'] = f'Basic {auth_str}'

        self.enabled = self._test_connection()

    def _rpc_call(self, payload: dict) -> requests.Response:
        """Wrapper che gestisce automaticamente il rinnovo del Session-Id (HTTP 409)."""
        resp = self.sess.post(self.url, json=payload, timeout=8)
        if resp.status_code == 409:
            session_id = resp.headers.get('X-Transmission-Session-Id')
            if session_id:
                self.sess.headers['X-Transmission-Session-Id'] = session_id
            resp = self.sess.post(self.url, json=payload, timeout=8)
        resp.raise_for_status()
        return resp

    def _test_connection(self) -> bool:
        try:
            self._rpc_call({'method': 'session-get'})
            return True
        except Exception as e:
            logger.warning(f"⚠️ Transmission unreachable: {e}")
            return False

    def add(self, magnet: str, cfg_unused: dict) -> bool:
        if not self.enabled:
            return False
        try:
            paused_flag = str(cfg_unused.get('transmission_paused', 'no')).lower() in ('yes', 'true', '1') \
                if isinstance(cfg_unused, dict) else False

            payload = {
                'method': 'torrent-add',
                'arguments': {'filename': magnet, 'paused': paused_flag}
            }
            data = self._rpc_call(payload).json()
            return data.get('result') == 'success' or \
                   'duplicate' in (data.get('result') or '').lower()
        except Exception as e:
            logger.exception(f"❌ Error adding torrent to Transmission: {e}")
            return False
