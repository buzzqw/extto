"""
EXTTO - Integrazione Simkl (core/simkl.py)

Funzionalita:
  1. Autenticazione PIN, adatta a un daemon headless
  2. Import manuale delle serie dalla lista Simkl
  3. Calendario personale usando il calendario CDN pubblico di Simkl
  4. Marcatura manuale di un episodio come visto (opzionale)

Le chiamate di sincronizzazione partono solo da azioni esplicite della UI.
Simkl raccomanda di non eseguire polling in background senza interazione.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SIMKL_API_BASE = "https://api.simkl.com"
SIMKL_DATA_BASE = "https://data.simkl.in"
SIMKL_APP_NAME = "extto"
SIMKL_APP_VERSION = "1"


class SimklClient:
    """Client Simkl con autenticazione PIN e token di lunga durata."""

    def __init__(self, client_id: str, access_token: str = ""):
        self.client_id = client_id
        self.access_token = access_token

    def _params(self, extra: Optional[dict] = None) -> dict:
        params = {
            "client_id": self.client_id,
            "app-name": SIMKL_APP_NAME,
            "app-version": SIMKL_APP_VERSION,
        }
        if extra:
            params.update(extra)
        return params

    def _headers(self, auth: bool = True) -> dict:
        headers = {
            "User-Agent": f"{SIMKL_APP_NAME}/{SIMKL_APP_VERSION}",
            "Content-Type": "application/json",
        }
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def is_authenticated(self) -> bool:
        return bool(self.access_token)

    # ------------------------------------------------------------------
    # PIN FLOW
    # ------------------------------------------------------------------

    def start_pin_auth(self) -> Optional[dict]:
        """Richiede un codice PIN da mostrare all'utente."""
        try:
            response = requests.get(
                f"{SIMKL_API_BASE}/oauth/pin",
                params=self._params(),
                headers=self._headers(auth=False),
                timeout=15,
            )
            if response.status_code == 200:
                return response.json()
            logger.error("[Simkl] oauth/pin: %s %s", response.status_code, response.text[:200])
        except Exception as exc:
            logger.error("[Simkl] start_pin_auth: %s", exc)
        return None

    def poll_pin_auth(self, user_code: str) -> Optional[dict]:
        """Controlla lo stato del PIN. Simkl risponde sempre con HTTP 200."""
        try:
            response = requests.get(
                f"{SIMKL_API_BASE}/oauth/pin/{user_code}",
                params=self._params(),
                headers=self._headers(auth=False),
                timeout=15,
            )
            if response.status_code == 200:
                return response.json()
            logger.warning("[Simkl] oauth/pin/%s: HTTP %s", user_code, response.status_code)
        except Exception as exc:
            logger.error("[Simkl] poll_pin_auth: %s", exc)
        return None

    # ------------------------------------------------------------------
    # REQUEST HELPERS
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None,
             auth: bool = True, base: str = SIMKL_API_BASE):
        try:
            response = requests.get(
                f"{base}{path}",
                params=self._params(params),
                headers=self._headers(auth=auth),
                timeout=20,
            )
            if response.status_code == 200:
                return response.json()
            logger.warning("[Simkl] GET %s: HTTP %s %s", path, response.status_code, response.text[:200])
        except Exception as exc:
            logger.error("[Simkl] GET %s: %s", path, exc)
        return None

    def _post(self, path: str, payload: dict):
        try:
            response = requests.post(
                f"{SIMKL_API_BASE}{path}",
                params=self._params(),
                headers=self._headers(auth=True),
                json=payload,
                timeout=20,
            )
            if response.status_code in (200, 201, 204):
                try:
                    return response.json()
                except Exception:
                    return {}
            logger.warning("[Simkl] POST %s: HTTP %s %s", path, response.status_code, response.text[:200])
        except Exception as exc:
            logger.error("[Simkl] POST %s: %s", path, exc)
        return None

    # ------------------------------------------------------------------
    # LISTE E CALENDARIO
    # ------------------------------------------------------------------

    def get_activities(self):
        """Lettura economica del watermark prima di una sincronizzazione."""
        return self._get("/sync/activities")

    def get_watchlist_shows(self, statuses: Optional[List[str]] = None,
                            include_anime: bool = False) -> List[Dict]:
        """Ritorna le serie nelle liste selezionate, deduplicate per Simkl ID."""
        self.get_activities()
        statuses = statuses or ["plantowatch", "watching"]
        result = {}
        types = ["shows"] + (["anime"] if include_anime else [])
        for media_type in types:
            for status in statuses:
                data = self._get(f"/sync/all-items/{media_type}/{status}")
                for show in (data or {}).get(media_type, []):
                    ids = show.get("ids", {}) or {}
                    key = ids.get("simkl") or ids.get("simkl_id") or show.get("title", "").lower()
                    result[str(key)] = {
                        "title": show.get("title", ""),
                        "year": show.get("year"),
                        "ids": ids,
                        "simkl_status": show.get("status", status),
                        "watched_episodes_count": show.get("watched_episodes_count", 0),
                    }
        return list(result.values())

    def get_my_calendar(self, days: int = 7, statuses: Optional[List[str]] = None,
                        include_anime: bool = False) -> List[Dict]:
        """Unisce il calendario CDN alle serie presenti nelle liste dell'utente."""
        days = max(1, min(int(days or 7), 33))
        watchlist = self.get_watchlist_shows(statuses, include_anime=include_anime)
        wanted_ids = set()
        for show in watchlist:
            ids = show.get("ids", {}) or {}
            for key in ("simkl", "simkl_id"):
                if ids.get(key) is not None:
                    wanted_ids.add(str(ids[key]))
        if not wanted_ids:
            return []

        catalogs = ["tv"] + (["anime"] if include_anime else [])
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)
        result = []
        for catalog in catalogs:
            data = self._get(
                f"/calendar/v2/{catalog}.json",
                auth=False,
                base=SIMKL_DATA_BASE,
            ) or {}
            metadata = data.get("metadata", {}) or {}
            for item in data.get("calendar", []) or []:
                simkl_id = str(item.get("simkl_id", ""))
                if simkl_id not in wanted_ids:
                    continue
                date_text = item.get("date")
                if not date_text:
                    continue
                try:
                    aired = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if not now <= aired <= cutoff:
                    continue
                show = metadata.get(simkl_id, {}) or {}
                episode = item.get("episode", {}) or {}
                ids = show.get("ids", {}) or {}
                result.append({
                    "series_title": show.get("title", ""),
                    "series_ids": ids,
                    "season": episode.get("season", 0),
                    "episode": episode.get("episode", 0),
                    "episode_title": episode.get("title", ""),
                    "first_aired": date_text,
                    "overview": "",
                    "simkl_url": show.get("url", ""),
                    "rating": ((show.get("ratings", {}) or {}).get("simkl", {}) or {}).get("rating"),
                    "in_extto": False,
                })
        return sorted(result, key=lambda item: item.get("first_aired", ""))

    # ------------------------------------------------------------------
    # WATCHED STATE (OPTIONAL)
    # ------------------------------------------------------------------

    def mark_episode_watched(self, show_title: str, tmdb_id: Optional[int],
                             season: int, episode: int) -> bool:
        show = {"title": show_title, "seasons": [{
            "number": season,
            "episodes": [{"number": episode}],
        }]}
        if tmdb_id:
            show["ids"] = {"tmdb": tmdb_id}
        return self._post("/sync/history", {"shows": [show]}) is not None


def load_simkl_client() -> Optional[SimklClient]:
    """Carica il client dalle impostazioni di extto_config.db."""
    try:
        import core.config_db as _cdb
        client_id = str(_cdb.get_setting("simkl_client_id", "") or "").strip()
        if not client_id:
            return None
        return SimklClient(
            client_id=client_id,
            access_token=str(_cdb.get_setting("simkl_access_token", "") or "").strip(),
        )
    except Exception as exc:
        logger.error("[Simkl] load_simkl_client: %s", exc)
        return None


def save_simkl_token(client: SimklClient) -> None:
    """Persiste il token Simkl in extto_config.db."""
    try:
        import core.config_db as _cdb
        _cdb.set_setting("simkl_access_token", client.access_token)
    except Exception as exc:
        logger.error("[Simkl] save_simkl_token: %s", exc)
