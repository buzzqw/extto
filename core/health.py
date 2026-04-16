import os
import psutil
import shutil
import socket
import time
from datetime import datetime, timezone
from typing import Dict, List
import requests
import urllib3
import warnings
from .constants import logger

class HealthMonitor:
    def __init__(self, cfg):
        self.cfg = cfg

    def get_full_report(self) -> Dict:
        """Ritorna un report completo della salute del sistema."""
        return {
            'disk': self.get_disk_status(),
            'indexers': self.get_indexer_status(),
            'system': self.get_system_stats(),
            'folders': self.get_folder_permissions(),
            'services': self.get_service_status(),
            'logs': self.get_recent_errors(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

    def get_service_status(self) -> List[Dict]:
        """Controlla i processi attivi invece di systemd (Compatibile con Docker/Windows/Linux)."""
        services = {
            'EXTTO Engine': 'extto3.py',
            'Jackett': 'jackett',
            'Prowlarr': 'prowlarr'
        }
        results = []
        try:
            # Crea una lista di tutti i processi in esecuzione
            running_cmds = []
            for p in psutil.process_iter(['name', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline']) if p.info['cmdline'] else p.info['name']
                    if cmd:
                        running_cmds.append(cmd.lower())
                except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                    pass  # processo terminato o senza permessi — normale
            
            for label, keyword in services.items():
                is_active = any(keyword.lower() in cmd for cmd in running_cmds)
                
                # Se è Jackett o Prowlarr e non è in locale (Docker separato), lo diamo per buono ("Esterno")
                if not is_active:
                    if label == 'Jackett' and getattr(self.cfg, 'jackett_url', None):
                        status_text = 'Esterno / Docker'
                        is_active = True 
                    elif label == 'Prowlarr' and getattr(self.cfg, 'prowlarr_url', None):
                        status_text = 'Esterno / Docker'
                        is_active = True
                    else:
                        status_text = 'Non in esecuzione'
                else:
                    status_text = 'Attivo (Processo Locale)'

                results.append({
                    'name': label,
                    'status': status_text,
                    'ok': is_active
                })
        except Exception as e:
            logger.debug(f'health get_service_status: {e}')
            
        return results

    def get_recent_errors(self) -> List[str]:
        """Estrae le ultime 10 righe di errore dal file log."""
        log_file = 'extto.log'
        if not os.path.exists(log_file):
            return []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                errors = [l.strip() for l in lines if 'ERROR' in l or 'CRITICAL' in l]
                return errors[-10:] # Ultime 10
        except:
            return []

    def get_disk_status(self) -> List[Dict]:
        """Controlla lo spazio disco estraendo i path in modo dinamico e sicuro."""
        paths_to_check = {'Sistema (Locale)': os.path.abspath('.')}
        
        # Aggiungiamo i NAS dinamicamente
        ar = getattr(self.cfg, 'archive_root', None)
        if ar:
            if isinstance(ar, list):
                for i, p in enumerate(ar):
                    if p and isinstance(p, str): paths_to_check[f'Archivio NAS {i+1}'] = p
            elif isinstance(ar, str):
                paths_to_check['Archivio NAS'] = ar
                
        trash = getattr(self.cfg, 'trash_path', None)
        if trash and isinstance(trash, str):
            paths_to_check['Cestino (Trash)'] = trash

        results = []
        for label, path in paths_to_check.items():
            if not path or not os.path.exists(path):
                continue
            try:
                usage = shutil.disk_usage(path)
                free_gb = usage.free / (1024**3)
                total_gb = usage.total / (1024**3)
                percent = (usage.used / usage.total * 100) if usage.total > 0 else 0
                results.append({
                    'label': label,
                    'path': path,
                    'total_gb': round(total_gb, 2),
                    'free_gb': round(free_gb, 2),
                    'percent': round(percent, 1),
                    'status': 'warning' if percent > 90 else 'ok'
                })
            except Exception as e:
                logger.debug(f'health disk check {path}: {e}')
        return results

    def get_indexer_status(self) -> List[Dict]:
        """Controlla se gli indexer rispondono (Timeout aumentato a 15s)."""
        indexers = []
        
        # Disabilita i warning per certificati SSL non validi se si usano IP locali
        warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
        
        # Prowlarr
        if getattr(self.cfg, 'prowlarr_url', None) and getattr(self.cfg, 'prowlarr_api', None):
            try:
                url = f"{self.cfg.prowlarr_url.rstrip('/')}/api/v1/system/status?apikey={self.cfg.prowlarr_api}"
                resp = requests.get(url, timeout=15, verify=False)
                status = 'ok' if resp.status_code == 200 else 'error'
                indexers.append({'name': 'Prowlarr', 'status': status, 'url': self.cfg.prowlarr_url})
            except:
                indexers.append({'name': 'Prowlarr', 'status': 'offline', 'url': self.cfg.prowlarr_url})

        # Jackett
        if getattr(self.cfg, 'jackett_url', None) and getattr(self.cfg, 'jackett_api', None):
            try:
                url = f"{self.cfg.jackett_url.rstrip('/')}/api/v2.0/indexers/all/results?apikey={self.cfg.jackett_api}&t=search&q=test"
                resp = requests.get(url, timeout=15, verify=False)
                status = 'ok' if resp.status_code == 200 else 'error'
                indexers.append({'name': 'Jackett', 'status': status, 'url': self.cfg.jackett_url})
            except:
                indexers.append({'name': 'Jackett', 'status': 'offline', 'url': self.cfg.jackett_url})
                
        return indexers

    def get_system_stats(self) -> Dict:
        """Statistiche CPU, RAM e Network."""
        return {
            'cpu_percent': psutil.cpu_percent(),
            'ram_percent': psutil.virtual_memory().percent,
            'hostname': socket.gethostname(),
            'uptime_seconds': int(time.time() - psutil.boot_time()) if hasattr(psutil, 'boot_time') else 0
        }

    def get_folder_permissions(self) -> List[Dict]:
        """Verifica i permessi di scrittura provando fisicamente a creare e rimuovere un file."""
        folders = set()
        folders.add(os.path.abspath('.'))
        
        trash = getattr(self.cfg, 'trash_path', None)
        if trash and isinstance(trash, str):
            folders.add(trash)
            
        ar = getattr(self.cfg, 'archive_root', None)
        if ar:
            if isinstance(ar, list):
                for p in ar:
                    if p and isinstance(p, str): folders.add(p)
            elif isinstance(ar, str):
                folders.add(ar)
                
        results = []
        for f in sorted(list(folders)):
            # Se la cartella non esiste, creiamola prima di testarla (comodo per il trash)
            try:
                if not os.path.exists(f):
                    os.makedirs(f, exist_ok=True)
            except Exception as e:
                logger.debug(f'health makedirs {f}: {e}')
                
            if not os.path.exists(f):
                continue
                
            # Test di scrittura REALE e INFALLIBILE
            is_writable = False
            test_file = os.path.join(f, '.extto_write_test')
            try:
                with open(test_file, 'w') as tf:
                    tf.write('ok')
                os.remove(test_file)
                is_writable = True
            except Exception:
                is_writable = False

            results.append({
                'path': f,
                'writable': is_writable,
                'readable': os.access(f, os.R_OK)
            })
        return results
