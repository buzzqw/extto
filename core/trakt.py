"""
EXTTO - Integrazione Trakt.tv  (core/trakt.py)

Tre funzionalità:
  1. Auth via OAuth2 Device Flow (headless, NAS-friendly)
  2. Watchlist sync: importa serie dalla watchlist Trakt
  3. Calendar: prossimi episodi dalle serie seguite (opzionale: scrobbling)

Token persistiti in extto_config.db via config_db.set_settings_bulk().
Chiavi usate:
    trakt_client_id, trakt_client_secret
    trakt_access_token, trakt_refresh_token, trakt_token_expires (unix ts int)
    trakt_watchlist_sync (bool), trakt_scrobble_enabled (bool)
    trakt_calendar_days (int), trakt_import_quality (str), trakt_import_language (str)
"""

import time
import logging
from typing import Optional, List, Dict

import requests

logger = logging.getLogger(__name__)

TRAKT_API_BASE = "https://api.trakt.tv"
TRAKT_API_V    = "2"


class TraktClient:
    """Client Trakt.tv con OAuth Device Flow e auto-refresh del token."""

    def __init__(self, client_id: str, client_secret: str,
                 access_token: str = "", refresh_token: str = "",
                 token_expires: int = 0):
        self.client_id     = client_id
        self.client_secret = client_secret
        self.access_token  = access_token
        self.refresh_token = refresh_token
        self.token_expires = token_expires  # unix timestamp

    # ------------------------------------------------------------------
    # HEADERS
    # ------------------------------------------------------------------

    def _headers(self, auth: bool = True) -> dict:
        h = {
            "Content-Type":      "application/json",
            "trakt-api-key":     self.client_id,
            "trakt-api-version": TRAKT_API_V,
        }
        if auth and self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    # ------------------------------------------------------------------
    # TOKEN
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        return bool(self.access_token)

    def token_needs_refresh(self) -> bool:
        """True se il token scade entro 7 giorni o è già scaduto."""
        if not self.token_expires:
            return False
        return time.time() > (self.token_expires - 7 * 86400)

    def refresh_access_token(self) -> bool:
        if not self.refresh_token:
            return False
        try:
            resp = requests.post(
                f"{TRAKT_API_BASE}/oauth/token",
                json={
                    "refresh_token": self.refresh_token,
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri":  "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type":    "refresh_token",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.access_token  = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                self.token_expires = int(time.time()) + int(data.get("expires_in", 7776000))
                logger.info("[Trakt] Token rinnovato con successo.")
                return True
            logger.warning(f"[Trakt] refresh fallito: {resp.status_code} {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Trakt] refresh_access_token: {e}")
            return False

    def revoke_token(self) -> bool:
        if not self.access_token:
            return True
        try:
            resp = requests.post(
                f"{TRAKT_API_BASE}/oauth/revoke",
                json={
                    "token":         self.access_token,
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                self.access_token  = ""
                self.refresh_token = ""
                self.token_expires = 0
                logger.info("[Trakt] Token revocato.")
                return True
            return False
        except Exception as e:
            logger.error(f"[Trakt] revoke_token: {e}")
            return False

    # ------------------------------------------------------------------
    # DEVICE FLOW
    # ------------------------------------------------------------------

    def start_device_auth(self) -> Optional[dict]:
        """Avvia Device Flow.
        Ritorna: {user_code, verification_url, device_code, expires_in, interval}
        """
        try:
            resp = requests.post(
                f"{TRAKT_API_BASE}/oauth/device/code",
                json={"client_id": self.client_id},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"[Trakt] device/code: {resp.status_code} {resp.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"[Trakt] start_device_auth: {e}")
            return None

    # ------------------------------------------------------------------
    # REQUEST HELPERS
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict = None) -> Optional[any]:
        """GET autenticato. Gestisce auto-refresh del token."""
        if self.token_needs_refresh():
            self.refresh_access_token()
        try:
            resp = requests.get(
                f"{TRAKT_API_BASE}{path}",
                headers=self._headers(auth=True),
                params=params or {},
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                if self.refresh_access_token():
                    resp2 = requests.get(
                        f"{TRAKT_API_BASE}{path}",
                        headers=self._headers(auth=True),
                        params=params or {},
                        timeout=20,
                    )
                    if resp2.status_code == 200:
                        return resp2.json()
            logger.warning(f"[Trakt] GET {path}: {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"[Trakt] _get {path}: {e}")
            return None

    def _post(self, path: str, payload: dict) -> Optional[any]:
        """POST autenticato."""
        if self.token_needs_refresh():
            self.refresh_access_token()
        try:
            resp = requests.post(
                f"{TRAKT_API_BASE}{path}",
                headers=self._headers(auth=True),
                json=payload,
                timeout=20,
            )
            if resp.status_code in (200, 201, 204):
                try:
                    return resp.json()
                except Exception:
                    return {}
            logger.warning(f"[Trakt] POST {path}: {resp.status_code} {resp.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"[Trakt] _post {path}: {e}")
            return None

    # ------------------------------------------------------------------
    # WATCHLIST
    # ------------------------------------------------------------------

    def get_watchlist_shows(self) -> List[Dict]:
        """Ritorna le serie nella watchlist dell'utente."""
        data = self._get("/sync/watchlist/shows", params={"extended": "full"})
        if not data:
            return []
        result = []
        for item in data:
            show = item.get("show", {})
            result.append({
                "title":     show.get("title", ""),
                "year":      show.get("year"),
                "ids":       show.get("ids", {}),
                "listed_at": item.get("listed_at", ""),
            })
        return result

    # ------------------------------------------------------------------
    # CALENDAR
    # ------------------------------------------------------------------

    def get_my_calendar(self, start_date: str = None, days: int = 7) -> List[Dict]:
        """Episodi in uscita per le serie seguite, da start_date per N giorni."""
        if not start_date:
            from datetime import date
            start_date = date.today().isoformat()
        data = self._get(f"/calendars/my/shows/{start_date}/{days}")
        if not data:
            return []
        result = []
        for day_block in data:
            first_aired = day_block.get("first_aired", "")
            for ep_block in day_block.get("episodes", []):
                show    = ep_block.get("show", {})
                episode = ep_block.get("episode", {})
                result.append({
                    "series_title":  show.get("title", ""),
                    "series_ids":    show.get("ids", {}),
                    "season":        episode.get("season", 0),
                    "episode":       episode.get("number", 0),
                    "episode_title": episode.get("title", ""),
                    "first_aired":   first_aired,
                    "overview":      episode.get("overview", ""),
                })
        return result

    # ------------------------------------------------------------------
    # SCROBBLING (opzionale)
    # ------------------------------------------------------------------

    def scrobble_episode(self, show_title: str, tmdb_id: Optional[int],
                         season: int, episode: int,
                         progress: float = 100.0) -> bool:
        """Segna un episodio come visto su Trakt."""
        show_payload: dict = {"title": show_title}
        if tmdb_id:
            show_payload["ids"] = {"tmdb": tmdb_id}
        result = self._post("/scrobble/stop", {
            "show":     show_payload,
            "episode":  {"season": season, "number": episode},
            "progress": progress,
        })
        if result is not None:
            logger.info(f"[Trakt] ✅ Scrobble: {show_title} S{season:02d}E{episode:02d}")
            return True
        return False


# ------------------------------------------------------------------
# FACTORY — carica/salva client da config_db
# ------------------------------------------------------------------

def load_trakt_client() -> Optional[TraktClient]:
    """Carica un TraktClient dalle impostazioni in extto_config.db.
    Ritorna None se client_id non è configurato.
    """
    try:
        import core.config_db as _cdb
        client_id     = str(_cdb.get_setting("trakt_client_id",     "") or "").strip()
        client_secret = str(_cdb.get_setting("trakt_client_secret", "") or "").strip()
        if not client_id:
            return None
        return TraktClient(
            client_id=client_id,
            client_secret=client_secret,
            access_token  = str(_cdb.get_setting("trakt_access_token",  "") or "").strip(),
            refresh_token = str(_cdb.get_setting("trakt_refresh_token", "") or "").strip(),
            token_expires = int(_cdb.get_setting("trakt_token_expires", 0) or 0),
        )
    except Exception as e:
        logger.error(f"[Trakt] load_trakt_client: {e}")
        return None


def save_trakt_tokens(client: TraktClient):
    """Persiste i token aggiornati in extto_config.db."""
    try:
        import core.config_db as _cdb
        _cdb.set_settings_bulk({
            "trakt_access_token":  client.access_token,
            "trakt_refresh_token": client.refresh_token,
            "trakt_token_expires": client.token_expires,
        })
    except Exception as e:
        logger.error(f"[Trakt] save_trakt_tokens: {e}")
