"""
EXTTO - Costanti globali, configurazione base e utility helpers.
"""

import re
import os
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from urllib.parse import urlparse, parse_qsl, quote

# --- PORTE E FILE ---
PORT             = 8889
CONFIG_FILE      = "extto.conf"      # impostazioni motore, client, notifiche, URL, blacklist
SERIES_FILE      = "series.txt"      # elenco serie TV monitorate
MOVIES_FILE      = "movies.txt"
_BASE_DIR        = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_FILE          = os.path.join(_BASE_DIR, "extto_series.db")
ARCHIVE_FILE     = os.path.join(_BASE_DIR, "extto_archive.db")
CACHE_FILE       = os.path.join(_BASE_DIR, "corsaro_cache.json")
LOG_FILE         = os.path.join(_BASE_DIR, "extto.log")
XML_FILE         = os.path.join(_BASE_DIR, "extto_magnet_feed.xml")
FEED_BUFFER_FILE = os.path.join(_BASE_DIR, "extto_feed_buffer.json")
STATE_DIR        = os.path.join(_BASE_DIR, "extto_torrents_state")
REFRESH          = 7200
MAX_PAGES        = 3

# Registro globale credenziali archivio (popolato da Config)
ARCHIVE_CREDENTIALS: List[Dict[str, str]] = []

# --- LOGGER ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('extto')
# Silenzia i logger rumorosi di librerie terze (urllib3, requests, ecc.)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
logger.propagate = False  # evita duplicazione verso root logger

def set_log_level(level_name: str):
    """Imposta il livello di log globale per console e file."""
    lv = level_name.upper()
    mapping = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'WARN': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    new_lv = mapping.get(lv, logging.INFO)
    logger.setLevel(new_lv)
    for h in logger.handlers:
        h.setLevel(new_lv)
    # Aggiorna anche il logger root per sicurezza su alcune configurazioni
    logging.getLogger().setLevel(new_lv)

def get_log_level() -> str:
    """Ritorna il livello di log attuale come stringa."""
    lv = logger.getEffectiveLevel()
    for name, val in logging._levelToName.items():
        if val == lv: return name.lower()
    return "info"

# --- MAGNET SANITIZER ---
MAGNET_SAFE_DN = "._-+()[]!'\" "

def sanitize_magnet(magnet: str, fallback_title: str | None = None) -> str | None:
    try:
        if not magnet or not isinstance(magnet, str):
            return None
        magnet = magnet.strip()
        if not magnet.startswith('magnet:?'):
            return None
        parsed = urlparse(magnet)
        q = dict(parse_qsl(parsed.query, keep_blank_values=False))
        xt = q.get('xt', '')
        m = re.search(r'btih:([a-fA-F0-9]{40}|[A-Z2-7]{32})$', xt)
        if not m:
            return None
        h = m.group(1)
        if len(h) == 40:
            h = h.lower()
        xt = f"urn:btih:{h}"

        dn = q.get('dn') or (fallback_title or '')
        dn_enc = quote(dn, safe=MAGNET_SAFE_DN) if dn else None

        tr = q.get('tr')
        if isinstance(tr, list):
            trackers = sorted(set(tr))
        elif isinstance(tr, str):
            trackers = [tr]
        else:
            trackers = []

        parts = [f"xt={xt}"]
        if dn_enc:
            parts.append(f"dn={dn_enc}")
        for t in trackers:
            parts.append(f"tr={quote(t, safe='/:?&=._-')}")
        return 'magnet:?' + "&".join(parts)
    except Exception:
        return None

# --- ED2K SANITIZER / HELPERS ★ NUOVO v45 ---

def sanitize_ed2k(link: str) -> str | None:
    """Valida e normalizza un link ed2k://.

    Formato atteso: ed2k://|file|nome|dimensione|hash_md4|/
    Ritorna il link normalizzato o None se invalido.
    """
    if not link or not isinstance(link, str):
        return None
    link = link.strip()
    if not link.startswith('ed2k://'):
        return None
    # Verifica struttura minima: |file|nome|size(digits)|hash MD4 (32 hex)|
    if re.search(r'ed2k://\|file\|[^|]+\|\d+\|[0-9a-fA-F]{32}\|', link):
        return link
    return None

def _extract_ed2k_hash(uri: str) -> str | None:
    """Estrae l'hash MD4 (32 hex) da un link ed2k://.

    Ritorna l'hash in lowercase o None se il link non è valido.
    Usato per il controllo duplicati in extto3.py (come _extract_btih per i magnet).
    """
    if not uri or not uri.startswith('ed2k://'):
        return None
    m = re.search(r'ed2k://\|file\|[^|]+\|\d+\|([0-9a-fA-F]{32})\|', uri)
    return m.group(1).lower() if m else None

# --- DATE PARSER ---
REL_UNITS = {
    'minuto': ('minutes', 1), 'minuti': ('minutes', 1), 'min': ('minutes', 1),
    'ora': ('hours', 1), 'ore': ('hours', 1), 'h': ('hours', 1),
    'giorno': ('days', 1), 'giorni': ('days', 1),
    'settimana': ('days', 7), 'settimane': ('days', 7)
}

def parse_date_any(text: str) -> Optional[datetime]:
    if not text:
        return None
    txt = text.strip().lower()
    now = datetime.now(timezone.utc)
    if 'oggi' in txt:
        return now
    if 'ieri' in txt:
        return now - timedelta(days=1)
    m = re.search(r'(\d+)\s*(minuti|minuto|min|ore|ora|h|giorni|giorno|settimane|settimana)\s*fa', txt)
    if m:
        qty = int(m.group(1))
        unit = m.group(2)
        kind, mult = REL_UNITS.get(unit, ('minutes', 1))
        delta = timedelta(**{kind: qty * mult})
        return now - delta
    m = re.search(r'(20\d{2})[\-/](\d{1,2})[\-/](\d{1,2})', txt)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d)
        except Exception:
            return None
    m = re.search(r'(\d{1,2})[\-/](\d{1,2})[\-/](20\d{2})', txt)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d)
        except Exception:
            return None
    return None

def _extract_date_from_element(el) -> Optional[datetime]:
    try:
        t = el.get_text(" ", strip=True)
        if t:
            t = t[:800]
        return parse_date_any(t)
    except Exception:
        return None

# --- FEED BUFFER HELPERS ---
def _extract_btih(magnet: str) -> str | None:
    try:
        if not magnet:
            return None
        m = re.search(r"btih:([a-fA-F0-9]{40}|[A-Z2-7]{32})", magnet, re.I)
        if not m:
            return None
        return m.group(1).lower()
    except Exception:
        return None

def _load_feed_buffer() -> List[Dict]:
    try:
        if not os.path.exists(FEED_BUFFER_FILE):
            return []
        with open(FEED_BUFFER_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                out = []
                for it in data:
                    if not isinstance(it, dict):
                        continue
                    if 'title' in it and 'magnet' in it:
                        out.append({
                            'title': it.get('title', ''),
                            'magnet': it.get('magnet', ''),
                            'source': it.get('source', 'feed'),
                            'added_at': it.get('added_at', datetime.now(timezone.utc).isoformat())
                        })
                return out
            return []
    except Exception:
        return []

def _save_feed_buffer(items: List[Dict]):
    try:
        with open(FEED_BUFFER_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"⚠️  Unable to save feed buffer: {e}")
