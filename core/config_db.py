"""
EXTTO - ConfigDB: gestione di extto_config.db

Contiene due tabelle:
  settings      - chiave/valore per tutte le impostazioni di extto.conf
  movies_config - configurazione film (ex movies.txt)

Separato da extto_series.db per tenere config e dati operativi distinti.
Migrazione automatica da file .conf/.txt al primo avvio.
"""

import json
import os
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional

# Path relativo alla directory del package core/
_HERE    = os.path.dirname(os.path.abspath(__file__))
_BASE    = os.path.dirname(_HERE)          # directory principale extto
CONFIG_DB_FILE  = os.path.join(_BASE, 'extto_config.db')
_CONFIG_FILE    = os.path.join(_BASE, 'extto.conf')
_SERIES_FILE    = os.path.join(_BASE, 'series.txt')
_MOVIES_FILE    = os.path.join(_BASE, 'movies.txt')
_LEGACY_FILE    = os.path.join(_BASE, 'series_config.txt')

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS movies_config (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    year     TEXT NOT NULL DEFAULT '',
    quality  TEXT NOT NULL DEFAULT 'any',
    language TEXT NOT NULL DEFAULT 'ita',
    enabled  INTEGER NOT NULL DEFAULT 1,
    subtitle TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS translations (
    lang  TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (lang, key)
);

CREATE TABLE IF NOT EXISTS torrent_limits (
    info_hash  TEXT PRIMARY KEY,
    dl_bytes   INTEGER NOT NULL DEFAULT -1,
    ul_bytes   INTEGER NOT NULL DEFAULT -1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Lingue predefinite: nome nativo usato nel selettore UI
# Chiavi in ISO 639-2 (3 lettere) — stesso formato usato da EXTTO_LANGUAGES in app.js.
# Le chiavi a 2 lettere sono mantenute come alias per retrocompatibilità
# (vecchi valori salvati nel DB prima della v40.1).
# ---------------------------------------------------------------------------
LANGUAGE_NATIVE_NAMES = {
    # ISO 639-2 (3 lettere) — formato canonico
    'ita': 'Italiano',
    'eng': 'English',
    'deu': 'Deutsch',
    'fra': 'Français',
    'spa': 'Español',
    'por': 'Português',
    'jpn': '日本語',
    'chi': '中文',
    'kor': '한국어',
    'rus': 'Русский',
    'ara': 'العربية',
    'nld': 'Nederlands',
    'pol': 'Polski',
    'tur': 'Türkçe',
    'swe': 'Svenska',
    'nor': 'Norsk',
    'dan': 'Dansk',
    'fin': 'Suomi',
    'hun': 'Magyar',
    'cze': 'Čeština',
    'ron': 'Română',
    'ukr': 'Українська',
    # ISO 639-1 (2 lettere) — alias retrocompatibilità
    'it': 'Italiano',
    'en': 'English',
    'de': 'Deutsch',
    'fr': 'Français',
    'es': 'Español',
    'pt': 'Português',
    'ja': '日本語',
    'zh': '中文',
    'ko': '한국어',
    'ru': 'Русский',
    'ar': 'العربية',
    'nl': 'Nederlands',
    'pl': 'Polski',
    'tr': 'Türkçe',
    'sv': 'Svenska',
    'no': 'Norsk',
    'da': 'Dansk',
    'fi': 'Suomi',
    'hu': 'Magyar',
    'cs': 'Čeština',
    'ro': 'Română',
    'uk': 'Українська',
}

# Cartella dove cercare/salvare i file YAML di scambio
_LANGUAGES_DIR = os.path.join(_BASE, 'languages')

# ---------------------------------------------------------------------------
# Connessione
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONFIG_DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _SCHEMA.strip().split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

def get_all_settings() -> Dict[str, Any]:
    """Restituisce tutte le impostazioni come dict {key: value}."""
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            result = {}
            for r in rows:
                # Le liste sono serializzate come JSON array
                try:
                    parsed = json.loads(r['value'])
                    result[r['key']] = parsed
                except (json.JSONDecodeError, TypeError):
                    result[r['key']] = r['value']
            return result
        finally:
            conn.close()


def get_setting(key: str, default: Any = '') -> Any:
    """Legge una singola impostazione."""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                return default
            try:
                return json.loads(row['value'])
            except (json.JSONDecodeError, TypeError):
                return row['value']
        finally:
            conn.close()


def set_setting(key: str, value: Any) -> None:
    """Salva una singola impostazione (upsert)."""
    with _lock:
        conn = _get_conn()
        try:
            # Liste e dict vengono serializzati come JSON
            if isinstance(value, (list, dict)):
                serialized = json.dumps(value, ensure_ascii=False)
            else:
                serialized = str(value)
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, serialized)
            )
            conn.commit()
        finally:
            conn.close()


def set_settings_bulk(data: Dict[str, Any]) -> None:
    """Salva molte impostazioni in un'unica transazione."""
    with _lock:
        conn = _get_conn()
        try:
            for key, value in data.items():
                if isinstance(value, (list, dict)):
                    serialized = json.dumps(value, ensure_ascii=False)
                else:
                    serialized = str(value)
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, serialized)
                )
            conn.commit()
        finally:
            conn.close()


def delete_setting(key: str) -> None:
    """Rimuove una impostazione."""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM settings WHERE key=?", (key,))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# MOVIES CONFIG
# ---------------------------------------------------------------------------

def get_movies_config() -> List[Dict]:
    """Restituisce la lista film configurati."""
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT id, name, year, quality, language, enabled, subtitle "
                "FROM movies_config ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def save_movies_config(movies: List[Dict]) -> None:
    """Sostituisce tutta la lista film (delete + insert)."""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM movies_config")
            for m in movies:
                conn.execute(
                    "INSERT INTO movies_config(name,year,quality,language,enabled,subtitle) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        m.get('name', ''),
                        str(m.get('year', '') or ''),
                        m.get('quality', m.get('qual', 'any')),
                        m.get('language', m.get('lang', 'ita')),
                        1 if m.get('enabled', True) else 0,
                        m.get('subtitle', ''),
                    )
                )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# MIGRAZIONE DAI FILE
# ---------------------------------------------------------------------------

def needs_migration() -> bool:
    """
    True se il DB config è vuoto (nessuna impostazione) ma esiste
    almeno uno dei file di configurazione originali.
    """
    has_files = (
        os.path.exists(_CONFIG_FILE) or
        os.path.exists(_SERIES_FILE) or
        os.path.exists(_MOVIES_FILE) or
        os.path.exists(_LEGACY_FILE)
    )
    if not has_files:
        return False
    with _lock:
        conn = _get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM settings"
            ).fetchone()[0]
            return count == 0
        finally:
            conn.close()


def migrate_from_files() -> Dict[str, Any]:
    """
    Legge extto.conf + series.txt + movies.txt e li importa nel DB.
    Restituisce un report:
      {
        'settings_imported': int,
        'series_imported': int,
        'movies_imported': int,
        'errors': [str],
        'files_found': [str],
      }
    """
    report = {
        'settings_imported': 0,
        'series_imported': 0,
        'movies_imported': 0,
        'errors': [],
        'files_found': [],
    }

    settings_data: Dict[str, Any] = {}
    series_data: List[Dict] = []
    movies_data: List[Dict] = []

    # ── 1. Legge extto.conf ──────────────────────────────────────────────────
    conf_path = None
    if os.path.exists(_CONFIG_FILE):
        conf_path = _CONFIG_FILE
        report['files_found'].append(_CONFIG_FILE)
    elif os.path.exists(_LEGACY_FILE):
        conf_path = _LEGACY_FILE
        report['files_found'].append(_LEGACY_FILE)

    if conf_path:
        try:
            _parse_conf_file(conf_path, settings_data, series_data)
        except Exception as e:
            report['errors'].append(f"Errore lettura {conf_path}: {e}")

    # ── 2. Legge series.txt ──────────────────────────────────────────────────
    if os.path.exists(_SERIES_FILE):
        report['files_found'].append(_SERIES_FILE)
        try:
            with open(_SERIES_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '|' in line:
                        s = _parse_series_line(line)
                        if s:
                            series_data.append(s)
        except Exception as e:
            report['errors'].append(f"Errore lettura series.txt: {e}")

    # ── 3. Legge movies.txt ──────────────────────────────────────────────────
    if os.path.exists(_MOVIES_FILE):
        report['files_found'].append(_MOVIES_FILE)
        try:
            with open(_MOVIES_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '|' in line:
                        m = _parse_movie_line(line)
                        if m:
                            movies_data.append(m)
        except Exception as e:
            report['errors'].append(f"Errore lettura movies.txt: {e}")

    # ── 4. Salva nel DB ──────────────────────────────────────────────────────
    try:
        set_settings_bulk(settings_data)
        report['settings_imported'] = len(settings_data)
    except Exception as e:
        report['errors'].append(f"Errore salvataggio settings: {e}")

    try:
        # Le serie vanno anche nel DB operativo (tabella series)
        # ma quello è compito di database.py — qui salviamo solo il count
        report['series_imported'] = len(series_data)
        # Salviamo le serie come setting speciale per il passaggio a database.py
        set_setting('_migrated_series', series_data)
    except Exception as e:
        report['errors'].append(f"Errore salvataggio serie: {e}")

    try:
        save_movies_config(movies_data)
        report['movies_imported'] = len(movies_data)
    except Exception as e:
        report['errors'].append(f"Errore salvataggio film: {e}")

    return report


def rename_files_to_old() -> Dict[str, bool]:
    """
    Rinomina i file di configurazione in .old.
    Restituisce {filename: success}.
    """
    results = {}
    for src in [_CONFIG_FILE, _SERIES_FILE, _MOVIES_FILE, _LEGACY_FILE]:
        if os.path.exists(src):
            dst = src + '.old'
            try:
                os.rename(src, dst)
                results[os.path.basename(src)] = True
            except Exception as e:
                results[os.path.basename(src)] = False
    return results


def get_migration_status() -> Dict[str, Any]:
    """
    Stato della migrazione per la UI:
      - files_present: lista file ancora presenti
      - db_populated: True se il DB ha già dati
      - needs_migration: True se serve importare
    """
    files_present = [
        f for f in [_CONFIG_FILE, _SERIES_FILE, _MOVIES_FILE]
        if os.path.exists(f)
    ]
    with _lock:
        conn = _get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM settings"
            ).fetchone()[0]
            db_populated = count > 0
        finally:
            conn.close()

    return {
        'files_present': [os.path.basename(f) for f in files_present],
        'db_populated': db_populated,
        'needs_migration': bool(files_present) and not db_populated,
        'can_rename': bool(files_present) and db_populated,
    }


# ---------------------------------------------------------------------------
# I18N — Traduzioni interfaccia
# ---------------------------------------------------------------------------

# Mappa ISO 639-1 (2 lettere) → ISO 639-2 (3 lettere) per normalizzazione.
# Usata per retrocompatibilità con valori salvati prima della v40.1.
_LANG2_TO_LANG3 = {
    'it': 'ita', 'en': 'eng', 'de': 'deu', 'fr': 'fra', 'es': 'spa',
    'pt': 'por', 'ja': 'jpn', 'zh': 'chi', 'ko': 'kor', 'ru': 'rus',
    'ar': 'ara', 'nl': 'nld', 'pl': 'pol', 'tr': 'tur', 'sv': 'swe',
    'no': 'nor', 'da': 'dan', 'fi': 'fin', 'hu': 'hun', 'cs': 'cze',
    'ro': 'ron', 'uk': 'ukr',
}
# Inverso: codice 3 lettere → 2 lettere (per cercare file YAML con nome breve)
_LANG3_TO_LANG2 = {v: k for k, v in _LANG2_TO_LANG3.items()}


def _normalize_lang_code(code: str) -> str:
    """Converte un codice lingua in ISO 639-2 (3 lettere).
    Se il codice è già a 3 lettere lo restituisce invariato.
    Se è a 2 lettere (vecchio formato) lo converte.
    Default: 'ita'.
    """
    c = (code or '').strip().lower()
    if len(c) == 3:
        return c
    return _LANG2_TO_LANG3.get(c, 'ita')

def get_ui_language() -> str:
    """Restituisce la lingua UI attiva in formato ISO 639-2 (3 lettere).
    Normalizza automaticamente i vecchi valori a 2 lettere salvati nel DB.
    Default: 'ita'.
    """
    raw = get_setting('ui_language', 'ita')
    return _normalize_lang_code(raw)


def set_ui_language(lang: str) -> None:
    """Imposta la lingua UI attiva. Salva sempre in formato ISO 639-2 (3 lettere)."""
    set_setting('ui_language', _normalize_lang_code(lang.strip().lower()))


def get_languages() -> List[Dict]:
    """
    Restituisce le lingue disponibili nel DB.
    Aggiunge sempre 'ita' (lingua master) anche se non ha righe translations.
    Formato: [{'code': 'ita', 'name': 'Italiano', 'count': 42}, ...]
    """
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT lang, COUNT(*) as cnt FROM translations GROUP BY lang"
            ).fetchall()
            result = {}
            for r in rows:
                # Normalizza il codice a 3 lettere (retrocompatibilità)
                code = _normalize_lang_code(r['lang'])
                if code not in result:
                    result[code] = {
                        'code': code,
                        'name': LANGUAGE_NATIVE_NAMES.get(code, code.upper()),
                        'count': 0,
                    }
                result[code]['count'] += r['cnt']
            # 'ita' è sempre presente come lingua master
            if 'ita' not in result:
                result['ita'] = {
                    'code': 'ita',
                    'name': 'Italiano',
                    'count': 0,
                }
            return sorted(result.values(), key=lambda x: (x['code'] != 'ita', x['code']))
        finally:
            conn.close()


def get_translation(lang: str) -> Dict[str, str]:
    """
    Restituisce tutte le stringhe tradotte per la lingua indicata.
    Formato: {'chiave': 'valore tradotto', ...}
    """
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT key, value FROM translations WHERE lang=?", (lang,)
            ).fetchall()
            return {r['key']: r['value'] for r in rows}
        finally:
            conn.close()


def set_translation_bulk(lang: str, strings: Dict[str, str]) -> int:
    """
    Salva/aggiorna molte stringhe per una lingua in una transazione.
    Restituisce il numero di righe salvate.
    """
    lang = lang.strip().lower()
    with _lock:
        conn = _get_conn()
        try:
            count = 0
            for key, value in strings.items():
                if not key or not isinstance(key, str):
                    continue
                conn.execute(
                    "INSERT INTO translations(lang,key,value) VALUES(?,?,?) "
                    "ON CONFLICT(lang,key) DO UPDATE SET value=excluded.value",
                    (lang, key.strip(), str(value))
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()


def delete_translation_lang(lang: str) -> int:
    """Elimina tutte le stringhe di una lingua. Restituisce righe eliminate."""
    lang = _normalize_lang_code(lang.strip().lower())
    if lang in ('it', 'ita'):
        raise ValueError("Non puoi eliminare la lingua master (Italiano)")
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute("DELETE FROM translations WHERE lang=?", (lang,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def get_available_yaml_files() -> List[str]:
    """
    Restituisce i codici lingua trovati come file YAML nella cartella languages/,
    normalizzati a ISO 639-2 (3 lettere). Esempio: 'en.yaml' → 'eng'.
    """
    if not os.path.isdir(_LANGUAGES_DIR):
        return []
    result = []
    for fname in sorted(os.listdir(_LANGUAGES_DIR)):
        if fname.endswith('.yaml') or fname.endswith('.yml'):
            raw_code = fname.rsplit('.', 1)[0].lower()
            code = _normalize_lang_code(raw_code)
            if code not in result:
                result.append(code)
    return result


def import_yaml_file(lang: str) -> Dict[str, Any]:
    """
    Importa un file YAML da languages/<lang>.yaml nel DB.
    Il file viene cercato col nome originale (es. 'en.yaml') ma le stringhe
    vengono salvate nel DB con il codice normalizzato a 3 lettere (es. 'eng').
    Restituisce {'imported': int, 'lang': str, 'error': str|None}
    """
    # Codice normalizzato per il DB (sempre 3 lettere)
    lang_norm = _normalize_lang_code(lang)
    # Cerca il file con tutte le varianti del nome: eng, en, e il valore originale
    # Supporta sia 'en.yaml' che 'eng.yaml'
    lang2 = _LANG3_TO_LANG2.get(lang_norm, '')   # eng → en
    search_names = list(dict.fromkeys(filter(None, [lang, lang_norm, lang2])))  # dedup
    path = None
    for name in search_names:
        for ext in ('yaml', 'yml'):
            candidate = os.path.join(_LANGUAGES_DIR, f"{name}.{ext}")
            if os.path.exists(candidate):
                path = candidate
                break
        if path:
            break
    if not path:
        return {'imported': 0, 'lang': lang_norm, 'error': f"File languages/{lang}.yaml non trovato"}

    try:
        import yaml
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return {'imported': 0, 'lang': lang_norm, 'error': "Il file YAML non contiene un dizionario chiave:valore"}
        # Salva sempre con codice normalizzato (3 lettere)
        count = set_translation_bulk(lang_norm, data)
        return {'imported': count, 'lang': lang_norm, 'error': None}
    except ImportError:
        return {'imported': 0, 'lang': lang_norm, 'error': "PyYAML non installato: pip install pyyaml"}
    except Exception as e:
        return {'imported': 0, 'lang': lang_norm, 'error': str(e)}


def export_yaml_file(lang: str) -> Dict[str, Any]:
    """
    Esporta le stringhe di una lingua in languages/<lang>.yaml.
    Restituisce {'exported': int, 'path': str, 'error': str|None}
    """
    strings = get_translation(lang)
    if not strings:
        return {'exported': 0, 'path': '', 'error': f"Nessuna stringa per la lingua '{lang}'"}

    try:
        import yaml
        os.makedirs(_LANGUAGES_DIR, exist_ok=True)
        path = os.path.join(_LANGUAGES_DIR, f"{lang}.yaml")
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(strings, f, allow_unicode=True, default_flow_style=False, sort_keys=True, width=10000, default_style="'")
        return {'exported': len(strings), 'path': path, 'error': None}
    except ImportError:
        return {'exported': 0, 'path': '', 'error': "PyYAML non installato: pip install pyyaml"}
    except Exception as e:
        return {'exported': 0, 'path': '', 'error': str(e)}


# ---------------------------------------------------------------------------
# PARSER INTERNI (usati solo dalla migrazione)
# ---------------------------------------------------------------------------

def _parse_conf_file(path: str, settings: dict, series: list) -> None:
    """Parsa extto.conf e popola settings dict e series list."""
    is_legacy = (path == _LEGACY_FILE)
    # Liste multi-valore: accumulano più righe
    list_keys = {'url', 'blacklist', 'wantedlist', 'archive_cred',
                 'archive_root', 'custom_score'}

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('@') and '=' in line:
                raw_key, _, raw_val = line[1:].partition('=')
                key = raw_key.strip()
                val = raw_val.split('#')[0].strip()
                if not val:
                    continue
                if key in list_keys:
                    existing = settings.get(key, [])
                    if not isinstance(existing, list):
                        existing = [existing] if existing else []
                    existing.append(val)
                    settings[key] = existing
                else:
                    settings[key] = val
                continue

            # File legacy: può contenere righe serie
            if is_legacy and '|' in line:
                s = _parse_series_line(line)
                if s:
                    series.append(s)


def _parse_series_line(line: str) -> Optional[Dict]:
    """Parsa una riga di series.txt."""
    parts = [p.strip() for p in line.split('|')]
    if len(parts) < 5:
        return None

    item: Dict[str, Any] = {
        'name':            parts[0],
        'seasons':         parts[1],
        'quality':         parts[2],
        'language':        parts[3],
        'enabled':         parts[4].lower() in ('yes', 'true', '1'),
        'archive_path':    '',
        'timeframe':       0,
        'aliases':         [],
        'ignored_seasons': [],
        'tmdb_id':         '',
        'subtitle':        '',
    }
    for ex in parts[5:]:
        ex = ex.strip()
        if not ex:
            continue
        if ex.startswith('alias='):
            item['aliases'] = [a.strip() for a in ex[6:].split(',') if a.strip()]
        elif ex.startswith('ignored:'):
            item['ignored_seasons'] = [
                int(x) for x in ex[8:].split(',') if x.strip().isdigit()
            ]
        elif ex.startswith('tmdb='):
            item['tmdb_id'] = ex[5:].strip()
        elif ex.startswith('subtitle='):
            item['subtitle'] = ex[9:].strip()
        elif 'timeframe:' in ex:
            m = re.search(r'timeframe:(\d+)h', ex)
            if m:
                item['timeframe'] = int(m.group(1))
        elif not item['archive_path']:
            item['archive_path'] = ex
    return item


def _parse_movie_line(line: str) -> Optional[Dict]:
    """Parsa una riga di movies.txt."""
    parts = [p.strip() for p in line.split('|')]
    if len(parts) < 3:
        return None
    return {
        'name':     parts[0],
        'year':     parts[1] if len(parts) > 1 else '',
        'quality':  parts[2] if len(parts) > 2 else 'any',
        'language': parts[3] if len(parts) > 3 else 'ita',
        'enabled':  parts[4].lower() in ('yes', 'true', '1') if len(parts) > 4 else True,
        'subtitle': parts[5].strip() if len(parts) > 5 else '',
    }


# ---------------------------------------------------------------------------
# TORRENT LIMITS — Limiti di banda individuali per torrent
#
# Migrazione da torrent_limits.json: i dati sono ora in extto_config.db
# (tabella torrent_limits). La chiave è l'info_hash del torrent.
# Valori in byte/s; -1 = nessun limite.
# ---------------------------------------------------------------------------

def get_torrent_limit(info_hash: str) -> Optional[Dict]:
    """Restituisce {info_hash, dl_bytes, ul_bytes} per un torrent, o None se non esiste."""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT info_hash, dl_bytes, ul_bytes FROM torrent_limits WHERE info_hash=?",
                (info_hash.lower(),)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_all_torrent_limits() -> Dict[str, Dict]:
    """Restituisce tutti i limiti come {info_hash: {dl_bytes, ul_bytes}}.
    Stesso formato del vecchio torrent_limits.json per compatibilità con il codice esistente."""
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT info_hash, dl_bytes, ul_bytes FROM torrent_limits"
            ).fetchall()
            return {r['info_hash']: {'dl_bytes': r['dl_bytes'], 'ul_bytes': r['ul_bytes']} for r in rows}
        finally:
            conn.close()


def set_torrent_limit(info_hash: str, dl_bytes: int = -1, ul_bytes: int = -1) -> None:
    """Salva o aggiorna i limiti di banda per un torrent (upsert).
    Valori in byte/s; -1 = nessun limite."""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO torrent_limits(info_hash, dl_bytes, ul_bytes, updated_at)
                   VALUES(?, ?, ?, datetime('now'))
                   ON CONFLICT(info_hash) DO UPDATE SET
                       dl_bytes   = excluded.dl_bytes,
                       ul_bytes   = excluded.ul_bytes,
                       updated_at = datetime('now')
                """,
                (info_hash.lower(), int(dl_bytes), int(ul_bytes))
            )
            conn.commit()
        finally:
            conn.close()


def delete_torrent_limit(info_hash: str) -> bool:
    """Rimuove i limiti per un torrent. Ritorna True se esisteva."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                "DELETE FROM torrent_limits WHERE info_hash=?",
                (info_hash.lower(),)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def migrate_torrent_limits_from_json(json_path: str) -> int:
    """Importa torrent_limits.json nel DB. Da eseguire una volta sola.
    Ritorna il numero di record importati (0 se il file è vuoto o assente)."""
    if not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not data:
            return 0
        count = 0
        for info_hash, val in data.items():
            if not isinstance(val, dict):
                continue
            set_torrent_limit(
                info_hash=info_hash,
                dl_bytes=int(val.get('dl_bytes', -1)),
                ul_bytes=int(val.get('ul_bytes', -1)),
            )
            count += 1
        return count
    except Exception:
        return 0
