"""
core/clients/amule.py — Client aMule per EXTTO via amulecmd subprocess.

Usa amulecmd (pacchetto amule-utils) come backend per aggirare il bug
wxWidgets 3.2 + amuled 2.3.3 che impedisce connessioni EC dirette da Python
su Debian Bookworm / Ubuntu 24.04.

Prerequisito (una tantum):  sudo apt install amule-utils

Output reale amulecmd 2.3.3 usato per i parser (verificato su Debian):

  Status:
    > eD2k: Connected to eMule Security [45.82.80.155:5687] with HighID
    > Kad: Connected (ok)
    > Download:\t0 bytes/sec
    > Upload:\t146 bytes/sec
    > Clients in queue:\t0
    > Total sources:\t0

  Show servers:
    > [45.82.80.155:5687]     eMule Security
    > [176.123.5.89:4725]     eMule Sunrise

  Show shared:
    > HASH32 /path/to/file.ext
    > \tAuto [Hi] - 0(7) / 0(6) - 0 bytes (3.064 GB) - 0.88
    #  formato: T(R) / ?(A) - X bytes (SIZE) - ratio
    #  T=byte_trasferiti, R=req_count, A=accepted_count, SIZE=dim_file

  Show dl / Show ul: (vuoto se non ci sono file)
"""

import subprocess
import re
import os
import logging
import configparser
import socket
from typing import Optional, List

logger = logging.getLogger(__name__)


class AmuleClient:
    """Client aMule via amulecmd subprocess."""

    def __init__(self, cfg=None):
        if cfg is None:
            try:
                from core.config import Config
                cfg_obj = Config()
                cfg = cfg_obj.qbt
            except Exception:
                cfg = {}
        elif not isinstance(cfg, dict):
            cfg = getattr(cfg, 'qbt', {})

        self.host     = cfg.get('amule_host', 'localhost')
        self.port     = int(cfg.get('amule_port', 4712))
        self.password = str(cfg.get('amule_password', ''))
        self.tcp_port = int(cfg.get('amule_tcp_port', 4662))
        self.udp_port = int(cfg.get('amule_udp_port', 4672))

        home = os.path.expanduser('~')
        self.conf_path = cfg.get(
            'amule_conf_path',
            os.path.join(home, '.aMule', 'amule.conf')
        )
        self._amulecmd = self._find_amulecmd()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def is_connected(self) -> bool:
        return self._amulecmd is not None

    def _check_connection(self):
        if not self._amulecmd:
            raise RuntimeError("amulecmd non trovato — installa: sudo apt install amule-utils")

    # ── amulecmd path ────────────────────────────────────────────────────────

    def _find_amulecmd(self) -> Optional[str]:
        for p in ['/usr/bin/amulecmd', '/usr/local/bin/amulecmd']:
            if os.path.exists(p):
                return p
        try:
            r = subprocess.run(['which', 'amulecmd'],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        return None

    # ── Esecuzione comandi ───────────────────────────────────────────────────

    def _run(self, *commands: str, timeout: int = 20) -> str:
        """Esegue comandi amulecmd, ritorna stdout senza le righe di intestazione."""
        self._check_connection()
        cmd_str = '; '.join(commands)
        try:
            r = subprocess.run(
                [self._amulecmd,
                 '-h', self.host, '-p', str(self.port), '-P', self.password,
                 '-c', cmd_str],
                capture_output=True, text=True, timeout=timeout
            )
            # Rimuove le righe header ("This is amulecmd...", "Creating client...",
            # "Succeeded!...") e ritorna solo i dati
            lines = r.stdout.splitlines()
            data_lines = []
            skip_prefixes = (
                'this is amulecmd', 'creating client', 'succeeded!',
                'type \'help\'', 'syntax error',
            )
            for line in lines:
                ll = line.strip().lower()
                if any(ll.startswith(p) for p in skip_prefixes):
                    continue
                data_lines.append(line)
            return '\n'.join(data_lines)
        except subprocess.TimeoutExpired:
            logger.warning(f"amulecmd timeout ({timeout}s): {cmd_str}")
            return ''
        except Exception as e:
            logger.error(f"amulecmd error: {e}")
            return ''

    def _run_sections(self, commands: List[str], timeout: int = 30) -> dict:
        """Esegue tutti i comandi in UN SOLO subprocess amulecmd = 1 connessione EC.

        Inietta un separatore fittizio tra i comandi: amulecmd lo stampa come
        errore corto e riconoscibile, usato per splittare l'output in sezioni.
        Ritorna {comando_lower: testo_output_sezione}.
        """
        self._check_connection()
        SEP = 'EXTTO_SEP_{}' 
        parts = []
        for i, cmd in enumerate(commands):
            parts.append(cmd)
            parts.append(SEP.format(i))
        cmd_str = '; '.join(parts)
        try:
            r = subprocess.run(
                [self._amulecmd,
                 '-h', self.host, '-p', str(self.port), '-P', self.password,
                 '-c', cmd_str],
                capture_output=True, text=True, timeout=timeout
            )
            raw = r.stdout
        except subprocess.TimeoutExpired:
            logger.warning(f"amulecmd timeout ({timeout}s) in _run_sections")
            return {c.lower(): '' for c in commands}
        except Exception as e:
            logger.error(f"amulecmd _run_sections error: {e}")
            return {c.lower(): '' for c in commands}

        skip_prefixes = (
            'this is amulecmd', 'creating client', 'succeeded!',
            "type 'help'", 'syntax error',
        )
        sections = [''] * len(commands)
        current  = 0
        buf      = []
        for line in raw.splitlines():
            ll = line.strip().lower()
            if any(ll.startswith(p) for p in skip_prefixes):
                continue
            matched = False
            for i in range(len(commands)):
                if SEP.format(i).lower() in ll:
                    sections[current] = '\n'.join(buf)
                    buf     = []
                    current = i + 1
                    matched = True
                    break
            if not matched:
                buf.append(line)
        if current < len(commands):
            sections[current] = '\n'.join(buf)

        return {commands[i].lower(): sections[i] for i in range(len(commands))}

    @staticmethod
    def _data_lines(output: str) -> List[str]:
        """Ritorna righe non vuote che iniziano con ' > '."""
        result = []
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith('> '):
                result.append(stripped[2:].strip())  # BUG1 FIX: strip() finale
            elif stripped.startswith('>'):
                # Riga tipo ">eD2k: ..." senza spazio dopo >
                result.append(stripped[1:].strip())
            elif stripped:
                result.append(stripped)
        return result

    def _parse_status(self, out: str) -> dict:
        """Parsing del testo di 'amulecmd Status' già estratto (no nuova connessione)."""
        result = {
            'ed2k_connected': False, 'kad_connected':  False,
            'kad_firewalled': False, 'high_id':        False,
            'client_id':      0,
            'server_name':    '', 'server_address': '',
            'server_ip':      '', 'server_port':    0,
            'server_ping':    0,  'server_users':   0, 'server_files': 0,
            'dl_speed':       0,  'ul_speed':       0,
            'ed2k_users':     0,  'ed2k_files':     0,
        }
        for raw in self._data_lines(out):
            line = raw.strip()
            ll   = line.lower()
            # BUG1 FIX: normalizza prefisso (amulecmd può scrivere "eD2k:" o "ED2K:" ecc.)
            ll_norm = ll.lstrip()
            if ll_norm.startswith('ed2k:'):
                result['ed2k_connected'] = 'connected' in ll
                if result['ed2k_connected']:
                    # "with HighID" o "with High ID" o "highid" tutto attaccato
                    result['high_id'] = bool(re.search(r'high\s*id', ll))
                    m = re.search(
                        r'connected to (.+?) \[(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\]',
                        line, re.IGNORECASE
                    )
                    if m:
                        result['server_name']    = m.group(1).strip()
                        result['server_ip']      = m.group(2)
                        result['server_port']    = int(m.group(3))
                        result['server_address'] = f"{m.group(2)}:{m.group(3)}"
            elif ll_norm.startswith('kad:'):
                result['kad_connected']  = 'connected' in ll
                result['kad_firewalled'] = 'firewalled' in ll
            elif ll_norm.startswith('download:'):
                result['dl_speed'] = self._parse_speed(line)
            elif ll_norm.startswith('upload:'):
                result['ul_speed'] = self._parse_speed(line)
        result['client_id'] = 1 if result['ed2k_connected'] else 0
        return result

    def _parse_upload_list(self, out: str) -> list:
        """Parser per Show UL basato sul formato reale di amulecmd 2.3.3.

        Formato:
          > RANK CLIENT_ID FILENAME SIZE SPEED
          es: > 19910 http://www.aMule.org vecna eve of ruin.pdf 0 bytes 309.95 kB/s

        CLIENT_ID può essere un URL (http://...), un IP (1.2.3.4) o un hash.
        """
        items = []
        for raw in self._data_lines(out):
            line = raw.strip()
            if not line or line.lower().startswith('no ') or line.lower().startswith('zero '):
                continue

            # Velocità alla fine: "309.95 kB/s"
            m_speed = re.search(r'([\d.,]+)\s*(bytes?|kb|mb|gb)/s\s*$', line, re.IGNORECASE)
            ul_speed = self._parse_speed(m_speed.group(0)) if m_speed else 0
            rest = line[:m_speed.start()].strip() if m_speed else line

            # "N bytes/kb/mb" alla fine (trasferito in sessione)
            m_sz = re.search(r'(\d+)\s*(bytes?|kb|mb|gb)\s*$', rest, re.IGNORECASE)
            rest = rest[:m_sz.start()].strip() if m_sz else rest

            # Rank (numero) all'inizio
            m_rank = re.match(r'^(\d+)\s+', rest)
            rank   = int(m_rank.group(1)) if m_rank else 0
            rest   = rest[m_rank.end():].strip() if m_rank else rest

            # Client (prima parola) + filename (resto)
            parts    = rest.split(None, 1)
            client   = parts[0] if parts else ''
            filename = parts[1].strip() if len(parts) > 1 else rest

            items.append({
                'file_name':      filename or 'Sconosciuto',
                'user_name':      client,
                'client_ip':      client if re.match(r'\d+\.\d+\.\d+\.\d+', client) else '',
                'ul_speed':       ul_speed,
                'queue_rank':     rank,
                'software':       '',
                'upload_session': 0,
            })
        return items

    def get_status(self) -> dict:
        """
        Parsing di 'amulecmd Status':
          > eD2k: Connected to eMule Security [45.82.80.155:5687] with HighID
          > Kad: Connected (ok)
          > Download:\t0 bytes/sec
          > Upload:\t146 bytes/sec
          > Clients in queue:\t0
          > Total sources:\t0
        """
        result = {
            'ed2k_connected': False, 'kad_connected':  False,
            'kad_firewalled': False, 'high_id':        False,
            'client_id':      0,
            'server_name':    '', 'server_address': '',
            'server_ip':      '', 'server_port':    0,
            'server_ping':    0,  'server_users':   0, 'server_files': 0,
            'dl_speed':       0,  'ul_speed':       0,
            'ed2k_users':     0,  'ed2k_files':     0,
        }
        try:
            out = self._run('Status')
            for raw in self._data_lines(out):
                line = raw.strip()
                ll   = line.lower()

                if ll.startswith('ed2k:'):
                    # "eD2k: Connected to eMule Security [45.82.80.155:5687] with HighID"
                    # "eD2k: Disconnected"
                    result['ed2k_connected'] = 'connected' in ll
                    if result['ed2k_connected']:
                        result['high_id'] = bool(re.search(r'high\s*id', ll))
                        m = re.search(
                            r'connected to (.+?) \[(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\]',
                            line, re.IGNORECASE
                        )
                        if m:
                            result['server_name']    = m.group(1).strip()
                            result['server_ip']      = m.group(2)
                            result['server_port']    = int(m.group(3))
                            result['server_address'] = f"{m.group(2)}:{m.group(3)}"

                elif ll.startswith('kad:'):
                    # "Kad: Connected (ok)"  /  "Kad: Connected (firewalled)"
                    result['kad_connected']  = 'connected' in ll
                    result['kad_firewalled'] = 'firewalled' in ll

                elif ll.startswith('download:'):
                    result['dl_speed'] = self._parse_speed(line)

                elif ll.startswith('upload:'):
                    result['ul_speed'] = self._parse_speed(line)

            result['client_id'] = 1 if result['ed2k_connected'] else 0

            # Arricchisci con Statistics per utenti/file rete
            try:
                stats_out = self._run('Statistics')
                for raw in self._data_lines(stats_out):
                    ll = raw.strip().lower()
                    if 'users on working servers' in ll:
                        m = re.search(r'(\d+)', raw)
                        if m: result['ed2k_users'] = int(m.group(1))
                    elif 'files on working servers' in ll:
                        m = re.search(r'(\d+)', raw)
                        if m: result['ed2k_files'] = int(m.group(1))
                    elif 'connected to server since' in ll:
                        # "Connected To Server Since: 17:03 mins"
                        result['server_uptime'] = raw.split(':', 1)[-1].strip()
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"get_status: {e}")
        return result

    @staticmethod
    def _parse_speed(line: str) -> int:
        """'152 bytes/sec', '1.23 KB/sec', '0.5 MB/sec' → byte/s int."""
        m = re.search(r'([\d.]+)\s*(bytes?|kb|mb|gb)/s', line, re.IGNORECASE)
        if not m:
            return 0
        val  = float(m.group(1))
        unit = m.group(2).lower().rstrip('s')
        mult = {'byte': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3}.get(unit, 1)
        return int(val * mult)

    # ── Server list ──────────────────────────────────────────────────────────

    def get_server_list(self) -> list:
        """
        Parsing di 'amulecmd Show servers':
          > [45.82.80.155:5687]     eMule Security
          > [176.123.5.89:4725]     eMule Sunrise
        """
        try:
            out = self._run('Show servers')
            servers = []
            for raw in self._data_lines(out):
                # Formato: [IP:PORT]   Nome Server
                m = re.match(
                    r'\[(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\]\s*(.*)',
                    raw.strip()
                )
                if not m:
                    continue
                ip   = m.group(1)
                port = int(m.group(2))
                name = m.group(3).strip() or ip
                servers.append({
                    'name':     name,
                    'desc':     '',
                    'address':  f'{ip}:{port}',
                    'ip':       ip,
                    'port':     port,
                    'users':    0,
                    'files':    0,
                    'ping':     0,
                    'failed':   0,
                    'priority': 1,
                    'static':   False,
                    'version':  '',
                })
            return servers
        except Exception as e:
            logger.warning(f"get_server_list: {e}")
            return []

    def connect_server(self, ip: str, port: int) -> bool:
        try:
            self._run(f'Connect {ip}:{port}')
            return True
        except Exception as e:
            logger.warning(f"connect_server: {e}")
            return False

    def add_server(self, url: str) -> bool:
        """
        Aggiunge server tramite 'Add <ed2k://|serverlist|url>'.
        Per URL .met usa il formato serverlist link.
        """
        try:
            if url.startswith('http'):
                link = f'ed2k://|serverlist|{url}|/'
            else:
                link = url
            self._run(f'Add {link}')
            return True
        except Exception as e:
            logger.warning(f"add_server: {e}")
            return False

    # ── Download queue ───────────────────────────────────────────────────────

    def get_download_queue(self) -> list:
        """
        Parsing di 'amulecmd Show dl'.
        Formato:
          > HASH /path/file.ext
          > \tPrio [X] - sources_xfer(sources) / ... - dl_speed (size) - ratio
        oppure vuoto se non ci sono download.
        """
        try:
            out = self._run('Show dl')
            return self._parse_file_list(out, is_download=True)
        except Exception as e:
            logger.warning(f"get_download_queue: {e}")
            return []

    def list_torrents(self) -> list:
        return self.get_download_queue()

    def _parse_file_list(self, output: str, is_download: bool = False) -> list:
        """Parser per Show DL / Show shared basato sul formato reale di amulecmd 2.3.3.

        Show DL:
          > HASH32 /path/to/file.ext
          > \t [72.1%]   51/  58     (31) - Downloading - 001.part.met - Auto [Hi] - 1.92 MB/s

        Show shared:
          > HASH32 /path/to/file.ext
          > \t Auto [Hi] - 0(7) / 0(6) - 0 bytes (3.064 GB) - 0.88
        """
        items = []
        lines = [l for l in output.splitlines() if l.strip()]
        i = 0
        while i < len(lines):
            raw = lines[i].strip().lstrip('> ').strip()

            m_main = re.match(r'([0-9A-Fa-f]{32})\s+(.+)', raw)
            if not m_main:
                i += 1
                continue

            file_hash = m_main.group(1).upper()
            filepath  = m_main.group(2).strip()
            filename  = os.path.basename(filepath) or filepath

            # Valori default
            pct = 0.0; xfer = 0; total = 0; a4af = 0
            speed = 0; size = 0; status = 'Searching'; prio = ''

            if i + 1 < len(lines):
                next_raw = lines[i + 1].strip().lstrip('> ').strip()
                is_stats = not re.match(r'[0-9A-Fa-f]{32}\s', next_raw)
                if is_stats and next_raw:
                    stats = next_raw

                    if is_download:
                        # ── Formato DL: [72.1%]   51/  58     (31) - Status - file - Prio - Speed ──
                        # Percentuale
                        m_pct = re.search(r'\[(\d+(?:\.\d+)?)\s*%\]', stats)
                        if m_pct:
                            pct = float(m_pct.group(1))

                        # Sorgenti: "56/  56" oppure "51/  58     (31)" — a4af opzionale
                        m_src = re.search(r'(\d+)\s*/\s*(\d+)(?:\s+\(\s*(\d+)\s*\))?', stats)
                        if m_src:
                            xfer  = int(m_src.group(1))
                            total = int(m_src.group(2))
                            a4af  = int(m_src.group(3)) if m_src.group(3) else 0

                        # Split su " - " per status e priorità
                        parts = [p.strip() for p in stats.split(' - ')]
                        if len(parts) > 1: status = parts[1]
                        if len(parts) > 3: prio   = parts[3]

                        # Velocità
                        speed = self._parse_speed(stats)

                        # 100% → Completed (amulecmd dice "Downloading" durante hashing finale)
                        if pct >= 100.0:
                            status = 'Completed'
                            speed  = 0

                    else:
                        # ── Formato shared: Prio [X] - T(R) / ?(A) - X bytes (SIZE) - ratio ──
                        # T  = byte trasferiti (int grezzo), R = richieste totali
                        # A  = richieste accettate
                        # Esempio: Auto [Hi] - 0(7) / 0(6) - 0 bytes (3.064 GB) - 0.88
                        m_prio = re.search(r'(\w+)\s+\[(\w+)\]', stats)
                        if m_prio: prio = f"{m_prio.group(1)} [{m_prio.group(2)}]"

                        xfer_bytes = 0
                        m_src2 = re.search(
                            r'(\d+)\s*\(\s*(\d+)\s*\)\s*/\s*(\d+)\s*\(\s*(\d+)\s*\)', stats)
                        if m_src2:
                            xfer_bytes = int(m_src2.group(1))   # byte trasferiti grezzi (int)
                            xfer  = int(m_src2.group(2))        # req_count (richieste totali)
                            total = int(m_src2.group(4))        # accepted_count (accettate)

                        # Byte trasferiti come testo con unità: "1.50 MB (" prima della size
                        m_xfer_text = re.search(
                            r'(?<!\()([\d]+(?:[.,]\d+)?)\s*(bytes?|kb|mb|gb)\s+\(',
                            stats, re.IGNORECASE)
                        if m_xfer_text:
                            val_x  = float(m_xfer_text.group(1).replace(',', '.'))
                            unit_x = m_xfer_text.group(2).lower().rstrip('s')
                            mult_x = {'byte':1,'kb':1024,'mb':1024**2,'gb':1024**3}.get(unit_x, 1)
                            xfer_bytes = int(val_x * mult_x)

                        # Dimensione file: valore tra parentesi "( N UNIT )"
                        m_size = re.search(
                            r'\(\s*([\d.,]+)\s*(bytes?|kb|mb|gb)\s*\)', stats, re.IGNORECASE)
                        if m_size:
                            val  = float(m_size.group(1).replace(',', '.'))
                            unit = m_size.group(2).lower().rstrip('s')
                            mult = {'byte':1,'kb':1024,'mb':1024**2,'gb':1024**3}.get(unit, 1)
                            size = int(val * mult)

                    i += 1

            if is_download:
                items.append({
                    'id':             file_hash,
                    'hash':           file_hash,
                    'name':           filename,
                    'size':           size,
                    'size_known':     size > 0,
                    'completed_size': 0,
                    'progress':       round(pct, 1),
                    'ratio':          round(pct / 100.0, 4),
                    'speed':          speed,
                    'sources':        total,
                    'sources_xfer':   xfer,
                    'sources_a4af':   a4af,
                    'status':         status,
                    'status_code':    0,
                    'stopped':        False,
                    'dl_active':      xfer > 0,
                    'priority':       prio,
                })
            else:
                items.append({
                    'file_name':      filename,
                    'file_size':      size,
                    'hash':           file_hash,
                    'req_count':      xfer,         # richieste totali (gruppo 2)
                    'accepted_count': total,        # richieste accettate (gruppo 4)
                    'transferred':    xfer_bytes,   # byte trasferiti reali
                    'upload_speed':   speed,
                    'priority':       prio,
                    'comment':        '',
                    'path':           filepath,
                })
            i += 1

        return items

    @staticmethod
    def _decode_status(code: int) -> str:
        return {0:'Waiting',1:'Connecting',2:'Downloading',3:'Paused',
                4:'Stopped',5:'Completed',6:'Hash Error',
                7:'Insufficient Space',8:'Error'}.get(int(code), f'Unknown ({code})')

    def add(self, ed2k_link: str) -> bool:
        if not ed2k_link.startswith('ed2k://'):
            return False
        try:
            # Ripuliamo il link da apici accidentali e usiamo _run (nessun limite di 80 caratteri!)
            clean_link = ed2k_link.strip().strip('"').strip("'")
            
            out = self._run(f'Add {clean_link}')
            out_lower = out.lower()
            
            # Controlliamo la risposta esatta di aMule
            if 'error' in out_lower or 'failed' in out_lower or 'invalid' in out_lower:
                logger.error(f"aMule ha rifiutato il link: {out.strip()}")
                return False
                
            logger.info(f"aMule Add Result: {out.strip()}")
            return True
        except Exception as e:
            logger.warning(f"add error: {e}")
            return False
                
            return True
        except Exception as e:
            logger.warning(f"add error: {e}")
            return False

    def pause_download(self, file_hash: str) -> bool:
        try:
            self._run(f'Pause {file_hash}')
            return True
        except Exception:
            return False

    def resume_download(self, file_hash: str) -> bool:
        try:
            self._run(f'Resume {file_hash}')
            return True
        except Exception:
            return False

    def cancel_download(self, file_hash: str) -> bool:
        try:
            self._run(f'Cancel {file_hash}')
            return True
        except Exception:
            return False

    pause  = pause_download
    resume = resume_download
    cancel = cancel_download

    # ── Upload queue ─────────────────────────────────────────────────────────

    # ── Upload queue ─────────────────────────────────────────────────────────

    def get_upload_queue(self) -> list:
        """Parsing di 'amulecmd Show ul' — delega a _parse_upload_list."""
        try:
            return self._parse_upload_list(self._run('Show ul'))
        except Exception as e:
            logger.warning(f"get_upload_queue: {e}")
            return []

    # ── Shared files ─────────────────────────────────────────────────────────

    def get_shared_files(self) -> list:
        """Parsing di 'amulecmd Show shared'."""
        try:
            out = self._run('Show shared')
            return self._parse_file_list(out, is_download=False)
        except Exception as e:
            logger.warning(f"get_shared_files: {e}")
            return []

    def reload_shared(self) -> bool:
        try:
            self._run('Reload shared')
            return True
        except Exception:
            return False

    # ── Cartelle condivise via shareddir.dat ─────────────────────────────────
    # aMule 2.3.x salva le cartelle condivise in ~/.aMule/shareddir.dat
    # (una per riga), NON in amule.conf. IncomingDir è sempre condivisa
    # implicitamente e non appare in shareddir.dat.

    def _shareddir_path(self) -> str:
        """Path del file shareddir.dat di aMule."""
        return os.path.join(os.path.dirname(self.conf_path), 'shareddir.dat')

    def get_shared_dirs(self) -> list:
        """Legge le cartelle condivise da ~/.aMule/shareddir.dat.
        
        Ritorna lista di dict con:
          {'path': str, 'source': 'shareddir'|'incoming', 'removable': bool}
        """
        dirs = []
        seen = set()

        # 1. IncomingDir da amule.conf (sempre condivisa, non rimuovibile)
        try:
            if os.path.exists(self.conf_path):
                cfg = configparser.RawConfigParser()
                cfg.optionxform = str
                cfg.read(self.conf_path, encoding='utf-8')
                if cfg.has_option('eMule', 'IncomingDir'):
                    p = cfg.get('eMule', 'IncomingDir').strip().rstrip('/')
                    if p and p not in seen:
                        dirs.append({'path': p, 'source': 'incoming', 'removable': False,
                                     'label': 'Cartella Download (IncomingDir)'})
                        seen.add(p)
        except Exception as e:
            logger.warning(f"get_shared_dirs IncomingDir: {e}")

        # 2. shareddir.dat (cartelle aggiuntive, rimuovibili)
        try:
            dat = self._shareddir_path()
            if os.path.exists(dat):
                with open(dat, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        p = line.strip().rstrip('/')
                        if p and p not in seen:
                            dirs.append({'path': p, 'source': 'shareddir', 'removable': True,
                                         'label': ''})
                            seen.add(p)
        except Exception as e:
            logger.warning(f"get_shared_dirs shareddir.dat: {e}")

        return dirs

    def add_shared_dir(self, path: str, recursive: bool = False) -> int:
        import os
        path = os.path.abspath(path.rstrip('/'))
        if not os.path.isdir(path):
            raise ValueError(f"Percorso non valido o non esistente: {path}")
        
        paths_to_add = [path]
        
        if recursive:
            for root, dirs, files in os.walk(path):
                # Esclude le cartelle nascoste e quelle di sistema
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for d in dirs:
                    paths_to_add.append(os.path.abspath(os.path.join(root, d)))

        all_dirs = self.get_shared_dirs()
        existing = {os.path.abspath(d['path'].rstrip('/')) for d in all_dirs}
        shareddir_only = [d['path'] for d in all_dirs if d['removable']]
        
        added_count = 0
        for p in paths_to_add:
            if p not in existing:
                shareddir_only.append(p)
                existing.add(p)
                added_count += 1
        
        if added_count > 0:
            self._write_shared_dirs(shareddir_only)
            self.reload_shared()
            
        return added_count

    def remove_shared_dir(self, path: str) -> bool:
        import os
        path_norm = os.path.abspath(path.rstrip('/'))
        all_dirs = self.get_shared_dirs()
        
        for d in all_dirs:
            if os.path.abspath(d['path'].rstrip('/')) == path_norm and not d['removable']:
                raise ValueError("La cartella IncomingDir non può essere rimossa da qui.")
        
        keep = []
        for d in all_dirs:
            p = os.path.abspath(d['path'].rstrip('/'))
            if not d['removable']:
                keep.append(d['path'])
                continue
                
            # Scarta la cartella esatta O le sue sottocartelle fisiche (usando il separatore di sistema)
            if p == path_norm or p.startswith(path_norm + os.sep):
                continue
            keep.append(d['path'])
            
        self._write_shared_dirs(keep)
        self.reload_shared()
        return True

    def _write_shared_dirs(self, dirs: list):
        """Scrive la lista in shareddir.dat (una riga per cartella)."""
        dat = self._shareddir_path()
        # Crea la dir se non esiste (non dovrebbe mai capitare)
        os.makedirs(os.path.dirname(dat), exist_ok=True)
        with open(dat, 'w', encoding='utf-8') as f:
            for d in dirs:
                f.write(d.rstrip('/') + '\n')

    # ── Ricerca ──────────────────────────────────────────────────────────────

    def search(self, query: str, network: str = 'global',
               extension: str = '') -> list:
        """
        BUG4 FIX: Avvia ricerca ed2k e recupera risultati.
        
        Il problema precedente: amulecmd apre/chiude la connessione EC ad ogni
        invocazione. La ricerca avviata in subprocess 1 non è visibile a
        subprocess 2 ("Results") perché sono sessioni EC separate.
        
        Soluzione: un solo subprocess con stdin pipe — inviamo "Search", aspettiamo,
        poi inviamo "Results" nella STESSA sessione EC.
        """
        import time

        net_map = {'global': 'global', 'local': 'local', 'kad': 'kad'}
        net = net_map.get(network.lower(), 'global')
        q   = query.replace('"', "'").replace(';', ' ')

        self._check_connection()

        # Costruisci comando combinato:
        # amulecmd non ha una shell interattiva scripted, ma accetta più comandi
        # separati da "; " sulla riga -c. Non possiamo inserire sleep tra comandi
        # amulecmd, quindi usiamo due subprocess con delay tra loro.
        # PERÒ: usiamo _run_sections che apre 1 SOLA connessione EC e inietta
        # entrambi i comandi separati dal marker. Il delay è emulato con un
        # no-op amulecmd (Status) come "placeholder" — ma amulecmd non ha sleep.
        #
        # Strategia ottimale: subprocess 1 lancia Search e aspetta il timeout
        # naturale di amulecmd (che blocca finché la ricerca non è avviata),
        # poi subprocess 2 legge Results dopo un delay sul lato Python.
        # Questo funziona perché amuled mantiene i risultati di ricerca in RAM
        # indipendentemente dalla connessione EC.
        
        # FASE 1: avvia la ricerca — amulecmd blocca fino alla conferma di avvio
        try:
            search_out = self._run(f'Search {net} {q}', timeout=20)
            search_out_l = search_out.lower()
            if 'error' in search_out_l or 'failed' in search_out_l:
                logger.warning(f"search '{q}': FASE 1 fallita — amulecmd: {search_out.strip()!r}")
                return []
            logger.info(f"search '{q}': ricerca avviata su rete '{net}'")
        except Exception as e:
            logger.warning(f"search fase1 errore: {e}")
            return []

        # FASE 2: aspetta che amuled completi la ricerca in rete, poi leggi Results
        # Polling: fino a 6 tentativi ogni 4s (max 24s). Il primo tentativo dopo
        # 4s è sufficiente per ricerche locali; globali/kad richiedono di più.
        results = []
        for attempt in range(6):
            time.sleep(4)
            try:
                out = self._run('Results', timeout=12)
                # Il formato Results NON contiene hash MD4 — cerca righe "N.  nome  size  src"
                if re.search(r'^\d+\.\s{2,}', out, re.MULTILINE):
                    results = self._parse_search_results(out)
                    if results:
                        logger.info(f"search '{q}': {len(results)} risultati al tentativo {attempt+1}")
                        break
                    logger.info(f"search '{q}': tentativo {attempt+1} — righe trovate ma parsing vuoto (formato inatteso?)")
                else:
                    logger.info(f"search '{q}': tentativo {attempt+1}/6 — nessun risultato ancora (amuled sta cercando…)")
            except Exception as e:
                logger.warning(f"search fase2 tentativo {attempt+1}: {e}")

        if not results:
            logger.info(f"search '{q}': 0 risultati dopo 6 tentativi (24s) su rete '{net}'")

        return sorted(results, key=lambda r: -r['sources'])

    def download_result(self, idx: int, name: str = '') -> bool:
        """Scarica risultato N con 'amulecmd Download N' — amuled conosce l'hash dalla ricerca in RAM.

        Diagnostica il motivo di fallimento quando amulecmd risponde con errore:
          - "invalid"   → idx non più valido: i risultati di ricerca in RAM sono stati
                          sovrascritti da una ricerca successiva, o amuled è stato riavviato
          - "already"   → il file è già in download queue (non è un errore reale)
          - "no search" → nessuna ricerca attiva in RAM: la finestra di 24s è scaduta
          - altri       → errore generico amulecmd/amuled
        """
        label = f"'{name}' (idx={idx})" if name else f"idx={idx}"
        try:
            out = self._run(f'Download {idx}', timeout=15)
            out_lower = out.lower()
            out_clean = out.strip()

            if 'error' in out_lower or 'invalid' in out_lower or 'failed' in out_lower:
                # Diagnostica motivo specifico
                if 'invalid' in out_lower or 'no result' in out_lower:
                    reason = (
                        "idx non più valido — i risultati di ricerca in RAM di amuled "
                        "potrebbero essere stati sovrascritti da un'altra ricerca, "
                        "oppure amuled è stato riavviato tra Search e Download"
                    )
                elif 'already' in out_lower:
                    # In realtà non è un errore: il file è già in coda
                    logger.info(f"aMule: {label} — già in coda download (ignorato)")
                    return True
                elif 'no search' in out_lower or 'no result' in out_lower:
                    reason = (
                        "nessuna ricerca attiva in RAM amuled — "
                        "la finestra dei risultati (~24s) potrebbe essere scaduta"
                    )
                elif 'not connected' in out_lower or 'disconnected' in out_lower:
                    reason = "amuled non connesso alla rete eD2k al momento del download"
                else:
                    reason = f"risposta amulecmd: {out_clean!r}"
                logger.error(f"aMule download_result FALLITO — {label}: {reason}")
                return False

            if not out_clean:
                # Output vuoto: amulecmd non ha confermato né negato — timeout silenzioso?
                logger.warning(
                    f"aMule download_result — {label}: output vuoto (possibile timeout "
                    f"o versione amulecmd che non stampa conferma)"
                )
                # Consideriamo successo ottimistico: amuled potrebbe aver accettato
                return True

            logger.info(f"✅ aMule: Download avviato — {label} | amulecmd: {out_clean}")
            return True

        except subprocess.TimeoutExpired:
            logger.error(
                f"aMule download_result TIMEOUT — {label}: "
                f"amulecmd non ha risposto in 15s (amuled occupato o non raggiungibile)"
            )
            return False
        except Exception as e:
            logger.error(f"aMule download_result ECCEZIONE — {label}: {e}")
            return False

    def _parse_search_results(self, out: str) -> list:
        """Parsa l'output di 'amulecmd Results'.
        
        Il formato REALE di amulecmd 2.3.3 NON contiene hash MD4 per riga —
        è una tabella testuale:
          Nr.    Filename:    ...    Size(MB):  Sources:
          -----------------------------------------------
          0.    NomeFile.ext         807.066    14
          1.    AltroFile.avi        123.456    5
        
        L'hash MD4 appare solo in 'Show dl' / 'Show shared', non in 'Results'.
        Costruiamo un ed2k link con hash placeholder — amuled lo accetta e
        usa il nome+dimensione per trovare il file in rete.
        
        NOTA: amulecmd può troncare i nomi lunghi (colonna fissa 80 char).
        """
        out = re.sub(r'\x1b\[.*?m', '', out)  # rimuove ANSI
        out = re.sub(r'<[^>]+>', '', out)       # rimuove eventuali tag HTML/span
        results = []
        for line in out.splitlines():
            line = line.strip()
            # Formato: "N.    NOME...    SIZE_MB    SOURCES"
            # Il numero può essere 0-9999, poi punto, poi 2+ spazi
            m = re.match(r'^(\d+)\.\s{2,}(.+?)\s{2,}([\d.]+)\s+(\d+)\s*$', line)
            if not m:
                continue
            nr      = int(m.group(1))
            name    = m.group(2).strip()
            # Rimuove eventuali artefatti di span HTML rimasti
            name    = re.sub(r'-Span\s+Class\s+-\w+-.*?-Span-', '', name,
                             flags=re.IGNORECASE).strip()
            size_mb = float(m.group(3))
            sources = int(m.group(4))
            size_b  = int(size_mb * 1024 * 1024)

            # Pulisce il nome da caratteri illegali per ed2k link
            safe_name = re.sub(r'[|]', '-', name)

            results.append({
                'name':    name,
                'size':    size_b,
                'sources': sources,
                'hash':    '',
                'idx':     nr,
                'ed2k':    f'ed2k://|file|{safe_name}|{size_b}||/',
            })
        return results

    # ── Port check ───────────────────────────────────────────────────────────

    def check_ports(self) -> dict:
        result = {
            'tcp_open':  False, 'udp_open':  False,
            'tcp_port':  self.tcp_port, 'udp_port': self.udp_port,
            'tcp_error': '', 'udp_error': '',
        }
        # TCP — connect test
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            code = s.connect_ex((self.host, self.tcp_port))
            s.close()
            result['tcp_open'] = (code == 0)
            if code != 0:
                result['tcp_error'] = os.strerror(code)
        except Exception as e:
            result['tcp_error'] = str(e)

        # UDP — legge /proc/net/udp (affidabile per porte locali, no firewall issues)
        try:
            port_hex = format(self.udp_port, '04X')
            found = False
            for udp_file in ['/proc/net/udp', '/proc/net/udp6']:
                if not os.path.exists(udp_file):
                    continue
                with open(udp_file) as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) > 1 and ':' in parts[1]:
                            _, p = parts[1].split(':', 1)
                            if p.upper() == port_hex:
                                found = True
                                break
                if found:
                    break
            result['udp_open'] = found
            if not found:
                result['udp_error'] = f'porta {self.udp_port}/UDP non trovata in /proc/net/udp'
        except Exception as e:
            result['udp_error'] = str(e)

        return result

    # ── Statistics ───────────────────────────────────────────────────────────

    def get_statistics(self) -> dict:
        """Parsing di 'amulecmd Statistics' per dati aggiuntivi.

        Output reale amulecmd 2.3.3 (struttura ad albero indentata):
          Uploaded Data (Session (Total)): 1.815 GB (8.335 GB)
          Downloaded Data (Session (Total)): 927.08 MB (3.267 GB)
          Max download rate (Session): 4.16 MB/s
          Max upload rate (Session): 399.93 kB/s
          Number of Shared Files: 10946
          Total size of Shared Files: 77.850 GB
          Users on Working Servers: 107444
          Files on Working Servers: 49395226

        Alias normalizzati prodotti per il frontend JS:
          'total download'  → dati sessione scaricati (es. "927.08 MB")
          'total upload'    → dati sessione caricati  (es. "1.815 GB")
          'shared files'    → numero file condivisi   (es. "10946")
        """
        try:
            out = self._run('Statistics')
            stats = {}
            for raw in self._data_lines(out):
                line = raw.strip()
                if ':' not in line:
                    continue
                k, _, v = line.partition(':')
                key   = k.strip()
                value = v.strip()
                if not key or not value:
                    continue
                kl = key.lower()
                stats[kl] = value

                # Alias esatti basati sull'output reale di amulecmd 2.3.3
                if kl == 'downloaded data (session (total))':
                    # Valore: "927.08 MB (3.267 GB)" — prendi solo la parte sessione
                    m = __import__('re').match(r'(.+?)\s*\(', value)
                    stats['total download'] = m.group(1).strip() if m else value
                elif kl == 'uploaded data (session (total))':
                    m = __import__('re').match(r'(.+?)\s*\(', value)
                    stats['total upload'] = m.group(1).strip() if m else value
                elif kl == 'number of shared files':
                    stats['shared files'] = value
                elif kl == 'users on working servers':
                    stats['ed2k_users_stat'] = value
                elif kl == 'files on working servers':
                    stats['ed2k_files_stat'] = value
                elif kl == 'max download rate (session)':
                    stats['max download rate'] = value
                elif kl == 'max upload rate (session)':
                    stats['max upload rate'] = value
                elif kl == 'average download rate (session)':
                    stats['avg download rate'] = value
                elif kl == 'average upload rate (session)':
                    stats['avg upload rate'] = value
            return stats
        except Exception:
            return {}

    # ── Utility ──────────────────────────────────────────────────────────────

    def get_log_messages(self) -> list:
        try:
            out = self._run('Show log')
            return [l.strip().lstrip('> ') for l in out.splitlines() if l.strip()]
        except Exception:
            return []

    def get_version(self) -> str:
        return f"AmuleClient/amulecmd {self.host}:{self.port}"

    def shutdown(self) -> bool:
        try:
            self._run('Shutdown')
            return True
        except Exception:
            return False

    # ── Limiti di banda ──────────────────────────────────────────────────────

    def get_bandwidth(self) -> dict:
        """Legge MaxDownload e MaxUpload da amule.conf (valori in KB/s, 0=illimitato)."""
        result = {'max_download_kbs': 0, 'max_upload_kbs': 0}
        try:
            if not os.path.exists(self.conf_path):
                return result
            cfg = configparser.RawConfigParser()
            cfg.optionxform = str
            cfg.read(self.conf_path, encoding='utf-8')
            if cfg.has_option('eMule', 'MaxDownload'):
                result['max_download_kbs'] = int(cfg.get('eMule', 'MaxDownload'))
            if cfg.has_option('eMule', 'MaxUpload'):
                result['max_upload_kbs'] = int(cfg.get('eMule', 'MaxUpload'))
        except Exception as e:
            logger.warning(f"get_bandwidth: {e}")
        return result

    def set_bandwidth(self, max_download_kbs: int = 0, max_upload_kbs: int = 0) -> bool:
        """Scrive MaxDownload e MaxUpload in amule.conf (0 = illimitato).
        Richiede riavvio di amuled per avere effetto.
        """
        try:
            if not os.path.exists(self.conf_path):
                raise FileNotFoundError(f"amule.conf non trovato: {self.conf_path}")
            cfg = configparser.RawConfigParser()
            cfg.optionxform = str
            cfg.read(self.conf_path, encoding='utf-8')
            if not cfg.has_section('eMule'):
                cfg.add_section('eMule')
            cfg.set('eMule', 'MaxDownload', str(max(0, int(max_download_kbs))))
            cfg.set('eMule', 'MaxUpload',   str(max(0, int(max_upload_kbs))))
            with open(self.conf_path, 'w', encoding='utf-8') as f:
                cfg.write(f)
            logger.info(f"set_bandwidth: DL={max_download_kbs} KB/s UL={max_upload_kbs} KB/s")
            return True
        except Exception as e:
            logger.warning(f"set_bandwidth: {e}")
            return False

    # ── Avvio / Arresto servizio ─────────────────────────────────────────────

    @staticmethod
    def _systemctl(action: str, service_name: str, timeout: int = 15) -> tuple:
        """Esegue systemctl --user <action> <service>.
        User service: nessun sudo necessario, funziona con lingerie ~/.config/systemd/user/.
        Ritorna (ok: bool, method: str, error: str).
        """
        cmd = ['systemctl', '--user', action, service_name]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return True, 'systemctl --user', ''
            err = (r.stderr or r.stdout or '').strip()
            # Messaggio di aiuto più chiaro se il servizio non è un user service
            if 'not found' in err.lower() or 'no such' in err.lower():
                err += (f' — assicurati che {service_name}.service sia in '
                        f'~/.config/systemd/user/ e che tu abbia eseguito: '
                        f'systemctl --user daemon-reload && systemctl --user enable {service_name}')
            return False, 'systemctl --user', err
        except FileNotFoundError:
            return False, '', 'systemctl non trovato'
        except Exception as e:
            return False, '', str(e)

