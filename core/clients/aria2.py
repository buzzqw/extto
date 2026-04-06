"""
EXTTO - Client aria2 (RPC JSON e CLI fallback).
"""

import json
import random
import requests
from ..constants import logger


class Aria2Client:
    """Supporto aria2 come fallback.
    - Se è configurato `@aria2_rpc_url`, usa JSON-RPC (`aria2.addUri`).
    - Altrimenti prova `aria2c` via CLI (se presente nel PATH).
    """

    def __init__(self, cfg: dict):
        self.cfg        = cfg or {}
        self.enabled    = str(self.cfg.get('aria2_enabled', 'no')).lower() in ('yes', 'true', '1')
        self.rpc_url    = self.cfg.get('aria2_rpc_url', '').strip()
        self.rpc_secret = self.cfg.get('aria2_rpc_secret', '').strip()
        self.dir        = self.cfg.get('aria2_dir', '').strip()
        self.aria2c_path = self.cfg.get('aria2c_path', 'aria2c').strip()

    def _rpc_add(self, uri: str) -> bool:
        if not self.rpc_url:
            return False
        try:
            headers = {'Content-Type': 'application/json'}
            params  = []
            if self.rpc_secret:
                params.append(f"token:{self.rpc_secret}")
            params.append([uri])
            
            # --- MODIFICA: Aggiunti parametri per velocità e fine seeding ---
            opts = {
                'max-connection-per-server': '16',
                'split': '16',
                'min-split-size': '1M',
                'seed-time': '0'  # Ferma il torrent appena completato
            }
            if self.dir:
                opts['dir'] = self.dir
            params.append(opts)
            # ----------------------------------------------------------------

            payload = {
                'jsonrpc': '2.0',
                'id':      random.randint(1, 999999),
                'method':  'aria2.addUri',
                'params':  params
            }
            resp = requests.post(self.rpc_url, headers=headers,
                                 data=json.dumps(payload), timeout=5)
            if resp.status_code == 200:
                try:
                    j = resp.json()
                    return 'result' in j
                except Exception:
                    return False
            return False
        except Exception:
            return False

    def _cli_add(self, uri: str) -> bool:
        try:
            import shutil as _sh
            import subprocess as _sp
            if not _sh.which(self.aria2c_path):
                return False
            args = [
                self.aria2c_path,
                '--continue=true',
                '--max-connection-per-server=16',
                '--split=16',
                '--min-split-size=1M',
                '--seed-time=0'  # --- MODIFICA: Ferma il torrent appena completato ---
            ]
            if self.dir:
                args += ['--dir', self.dir]
            args.append(uri)
            _sp.Popen(args, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            return True
        except Exception:
            return False

    def add(self, uri: str, _cfg_unused: dict) -> bool:
        if self.rpc_url:
            if self._rpc_add(uri):
                return True
        return self._cli_add(uri)
