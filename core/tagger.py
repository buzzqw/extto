"""
EXTTO - Tagging automatico torrent su qBittorrent.

Assegna il tag "Serie TV" agli episodi scaricati e "Film" ai film.
Funziona solo con qBittorrent (unico client con API tag nativa).
Libtorrent e Transmission non hanno un sistema di tag — vengono ignorati silenziosamente.

Utilizzo:
    from core.tagger import Tagger
    tagger = Tagger(qbt_client)       # passare l'istanza QbtClient attiva
    tagger.ensure_tags()              # creare i tag se non esistono (una volta al boot)
    tagger.tag_torrent(magnet, "Serie TV")
    tagger.tag_torrent(magnet, "Film")
"""

import re
import time
import os
import json
import tempfile
from typing import Optional
from .constants import logger
from .utils import safe_load_json, safe_save_json

# Percorso del file tag UI — stesso della directory di lavoro di extto
_TAGS_FILE = "torrent_tags.json"

def _save_ui_tag(info_hash: str, tag: str) -> None:
    """Scrive {info_hash: tag} in torrent_tags.json (file letto dalla Web UI).
    Chiamata dopo ogni tagging riuscito — no-op silenziosa in caso di errore."""
    if not info_hash or not tag:
        return
    try:
        tags = safe_load_json(_TAGS_FILE)
        tags[info_hash.lower()] = str(tag).strip()
        safe_save_json(_TAGS_FILE, tags)
    except Exception as e:
        logger.debug(f"_save_ui_tag: {e}")

TAG_SERIES = "Serie TV"
TAG_FILM   = "Film"
ALL_TAGS   = [TAG_SERIES, TAG_FILM]

_HASH_RE = re.compile(r'btih:([a-fA-F0-9]{40})', re.I)


def _extract_hash(magnet: str) -> Optional[str]:
    """Estrae l'info-hash da un magnet link."""
    m = _HASH_RE.search(magnet or '')
    return m.group(1).lower() if m else None


class Tagger:
    """
    Gestisce il tagging automatico su qBittorrent.
    Se il client non è qBittorrent (o non è abilitato), tutti i metodi
    sono no-op sicuri — non sollevano eccezioni.
    """

    def __init__(self, client):
        """
        client: istanza di QbtClient (o qualsiasi altro client).
        Il tagger verifica se il client ha l'attributo `sess` e `url`
        (propri di QbtClient) per determinare se il tagging è supportato.
        """
        self._supported = (
            client is not None
            and getattr(client, 'enabled', False)
            and hasattr(client, 'sess')
            and hasattr(client, 'url')
        )
        if self._supported:
            self._sess = client.sess
            self._url  = client.url.rstrip('/')

    # ------------------------------------------------------------------

    def ensure_tags(self) -> bool:
        """
        Crea i tag "Serie TV" e "Film" su qBittorrent se non esistono già.
        Chiamare una volta al boot del motore.
        Ritorna True se tutto OK, False in caso di errore.
        """
        if not self._supported:
            return True   # no-op su altri client
        try:
            resp = self._sess.get(f"{self._url}/api/v2/torrents/tags", timeout=5)
            existing = set(t.strip().lower() for t in (resp.json() or []))
            for tag in ALL_TAGS:
                if tag.lower() not in existing:
                    self._sess.post(
                        f"{self._url}/api/v2/torrents/createTag",
                        data={'tags': tag},
                        timeout=5
                    )
                    logger.info(f"🏷️  Tag creato su qBittorrent: '{tag}'")
                else:
                    logger.debug(f"🏷️  Tag already present on qBittorrent: '{tag}'")
            return True
        except Exception as e:
            logger.warning(f"⚠️  Tagger.ensure_tags: {e}")
            return False

    def tag_torrent(self, magnet: str, tag: str, retries: int = 8, delay: float = 1.5) -> bool:
        """
        Assegna un tag a un torrent identificato dal magnet link.
        Ritenta fino a `retries` volte con `delay` secondi tra un tentativo e l'altro,
        perché qBittorrent potrebbe non aver ancora indicizzato il torrent appena aggiunto.
        Ritorna True se il tag è stato applicato, False in caso di fallimento.
        """
        if not self._supported:
            return True   # no-op su altri client

        ih = _extract_hash(magnet)
        if not ih:
            logger.warning(f"⚠️  Tagger: unable to extract hash from magnet")
            return False

        for attempt in range(retries):
            try:
                # Verifica che il torrent esista già nella sessione qBit
                info_resp = self._sess.get(
                    f"{self._url}/api/v2/torrents/info",
                    params={'hashes': ih},
                    timeout=5
                )
                torrents = info_resp.json() if info_resp.ok else []
                if torrents:
                    self._sess.post(
                        f"{self._url}/api/v2/torrents/addTags",
                        data={'hashes': ih, 'tags': tag},
                        timeout=5
                    )
                    logger.info(f"🏷️  Tag '{tag}' applicato: {ih[:8]}…")
                    _save_ui_tag(ih, tag)
                    return True
            except Exception as e:
                logger.debug(f"   Tagger attempt {attempt+1}: {e}")

            if attempt < retries - 1:
                time.sleep(delay)

        logger.warning(f"⚠️  Tagger: timeout applicazione tag '{tag}' per {ih[:8]}…")
        return False
