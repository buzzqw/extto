"""
EXTTO - Client TMDB v3 API.

Recupera il numero di episodi attesi per stagione e li salva nella tabella
`series_metadata` del DB principale (extto_series.db).

La cache ha durata configurabile (default 7 giorni) per evitare chiamate
API ad ogni ciclo.

Flusso (molto più semplice di TVDB):
  1. GET /3/search/tv?query=nome  → tmdb_id
  2. GET /3/tv/{tmdb_id}          → seasons[] con episode_count per stagione
                                    (tutto in una sola chiamata, nessuna paginazione)
  3. db.upsert_series_metadata()  → salva nel DB con timestamp
  4. db.find_gaps()               → gap filling completo

API key gratuita: https://www.themoviedb.org/settings/api
"""

import requests
from typing import Optional, List, Dict
from .constants import logger


TMDB_BASE = "https://api.themoviedb.org/3"


class TMDBClient:
    def __init__(self, api_key: str, cache_days: int = 7, language: str = 'it-IT'):
        self.api_key    = api_key
        self.cache_days = cache_days
        self.language   = language
        self.sess       = requests.Session()
        # L'autenticazione TMDB v3 è solo un parametro api_key per request,
        # non serve token JWT né refresh — molto più semplice di TVDB.
        self.sess.headers.update({'Accept': 'application/json'})

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        """Esegue una GET autenticata verso TMDB. Ritorna il JSON o None."""
        url = f"{TMDB_BASE}{path}"
        p   = {'api_key': self.api_key, **(params or {})}
        try:
            resp = self.sess.get(url, params=p, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.warning(f"⚠️ TMDB HTTP {resp.status_code} per {path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"⚠️ TMDB error for {path}: {e}")
            return None

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------

    def resolve_series_id(self, name: str) -> Optional[int]:
        try:
            data = self._get('/search/tv', {'query': name, 'language': self.language})
            results = (data or {}).get('results', [])
            if not results:
                # Ritenta senza lingua per serie con titolo solo in inglese
                data    = self._get('/search/tv', {'query': name})
                results = (data or {}).get('results', [])
            if not results:
                logger.warning(f"⚠️ TMDB: no results for '{name}'")
                return None
            tmdb_id = results[0]['id']
            found   = results[0].get('name') or results[0].get('original_name', '')
            logger.debug(f"   TMDB: '{name}' → id={tmdb_id} ({found})")
            return tmdb_id
        except Exception as e:
            # Cattura eccezioni non gestite (es. mock dei test) e restituisce None
            logger.debug(f"   TMDB resolve_series_id eccezione gestita: {e}")
            return None

    # ------------------------------------------------------------------
    # SEASON COUNTS
    # ------------------------------------------------------------------

    def fetch_season_counts(self, tmdb_id: int) -> dict:
        data = self._get(f'/tv/{tmdb_id}', {'language': self.language})
        if not data:
            return {}

        counts = {}
        for season in data.get('seasons', []):
            s_num  = season.get('season_number', 0)
            ep_cnt = season.get('episode_count', 0)
            if s_num > 0 and ep_cnt > 0:    # escludi stagione 0 (speciali)
                counts[s_num] = ep_cnt

        if counts:
            logger.debug(f"   TMDB id={tmdb_id}: {dict(sorted(counts.items()))}")
        else:
            logger.warning(f"⚠️ TMDB: no seasons found for id={tmdb_id}")
        return counts
        
    # ------------------------------------------------------------------
    # DETAILS FOR EXTTO VIEW
    # ------------------------------------------------------------------

    def fetch_series_details(self, tmdb_id: int) -> Optional[dict]:
        """Recupera tutti i dettagli estesi di una serie (poster, trama, stagioni)."""
        data = self._get(f'/tv/{tmdb_id}', {'language': self.language})
        
        # Fallback intelligente: se manca la trama in italiano, la prende in inglese
        if data and not data.get('overview') and self.language != 'en-US':
            fallback_data = self._get(f'/tv/{tmdb_id}', {'language': 'en-US'})
            if fallback_data:
                data['overview'] = fallback_data.get('overview', '')
                
        return data    

    # ------------------------------------------------------------------
    # HIGH-LEVEL: aggiorna metadati per una serie
    # ------------------------------------------------------------------

    def update_series_metadata(self, db, series_id: int, series_name: str) -> bool:
        """Pipeline completa per una serie:
        1. Controlla se la cache è ancora fresca → skip se valida
        2. Cerca su TMDB → ottieni tmdb_id
        3. Fetch stagioni → salva nel DB
        Ritorna True se ha fatto chiamate API, False se era in cache.

        Interfaccia identica a TVDBClient per compatibilità con extto3.py.
        """
        if db.is_tvdb_cache_fresh(series_id, self.cache_days):
            return False  # cache valida, niente da fare

        logger.debug(f"🌐 TMDB: aggiorno metadati per '{series_name}'")

        # Riutilizziamo la colonna tvdb_id nel DB per salvare il tmdb_id
        tmdb_id = db.get_tvdb_id(series_id)
        if not tmdb_id:
            tmdb_id = self.resolve_series_id(series_name)
            if not tmdb_id:
                logger.warning(f"⚠️ TMDB: '{series_name}' not found, skipping")
                return True

        counts = self.fetch_season_counts(tmdb_id)
        if counts:
            db.upsert_series_metadata(series_id, tmdb_id, counts)
        return True


    # ------------------------------------------------------------------
    # CALENDAR / UPCOMING
    # ------------------------------------------------------------------

    def fetch_upcoming_episodes(self, tmdb_id: int) -> List[Dict]:
        """Recupera i prossimi episodi in uscita per una serie."""
        data = self._get(f'/tv/{tmdb_id}', {'language': self.language})
        if not data:
            return []
            
        upcoming = []
        
        # 1. Controlla 'next_episode_to_air'
        next_ep = data.get('next_episode_to_air')
        if next_ep:
            upcoming.append({
                'season': next_ep.get('season_number'),
                'episode': next_ep.get('episode_number'),
                'air_date': next_ep.get('air_date'),
                'name': next_ep.get('name')
            })
            
        # 2. Se l'utente vuole di più, potremmo guardare l'ultima stagione
        # ma TMDB solitamente mette solo il prossimo immediato in 'next_episode_to_air'.
        # Per avere un calendario vero servirebbe iterare su tutte le serie.
        
        return upcoming

    
    # ------------------------------------------------------------------
    # EPISODE TITLE (per rename)
    # ------------------------------------------------------------------

    def fetch_episode_title(self, tmdb_id: int, season: int, episode: int) -> Optional[str]:
        if episode == 0:
            logger.debug(f"   TMDB: skip fetch_episode_title per S{season:02d}E00 (non supportato da TMDB)")
            return None

        # Prova prima la lingua configurata, se fallisce riprova in inglese
        langs = [self.language]
        if self.language != 'en-US':
            langs.append('en-US')
            
        for lang in langs:
            data = self._get(
                f'/tv/{tmdb_id}/season/{season}/episode/{episode}',
                {'language': lang}
            )
            if data:
                title = (data.get('name') or '').strip()
                if title:
                    logger.debug(f"   TMDB ep title [{lang}]: S{season:02d}E{episode:02d} → '{title}'")
                    return title
        return None

    def get_tmdb_id_for_series(self, db, series_name: str) -> Optional[int]:
        """Cerca il tmdb_id nel DB prima (evita chiamata API), poi su TMDB.
        Usato dal rename helper che non ha series_id disponibile direttamente.
        """
        try:
            from .models import normalize_series_name, _series_name_matches
            norm = normalize_series_name(series_name)
            c = db.conn.cursor()
            c.execute("SELECT id, name FROM series")
            for row in c.fetchall():
                if _series_name_matches(normalize_series_name(row['name']), norm):
                    tmdb_id = db.get_tvdb_id(row['id'])
                    if tmdb_id:
                        return tmdb_id
                    # trovata la serie ma tmdb_id non ancora in cache → cerca su TMDB
                    tmdb_id = self.resolve_series_id(series_name)
                    if tmdb_id:
                        counts = self.fetch_season_counts(tmdb_id)
                        db.upsert_series_metadata(row['id'], tmdb_id, counts)
                    return tmdb_id
        except Exception as e:
            logger.debug(f"   TMDB get_tmdb_id_for_series: {e}")
        # Fallback: cerca direttamente su TMDB senza DB
        return self.resolve_series_id(series_name)
