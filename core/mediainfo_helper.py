"""
EXTTO - Estrazione tag tecnici dai file video tramite pymediainfo.

Fornisce la funzione get_media_tags(filepath) che restituisce un dict con:
  resolution  : 'HDTV-1080p', 'WEBDL-2160p', ecc.
  hdr         : 'HDR10', 'HDR10Plus', 'DV', 'DV HDR10', 'HLG' oppure None
  video_codec : 'h265', 'h264', 'AV1', ecc.
  audio_codec : 'EAC3', 'EAC3 Atmos', 'TrueHD', 'DTS-MA', ecc.
  channels    : '5.1', '7.1', 'Stereo', ecc.
  languages   : lista ISO 639-1 ['it', 'en'] delle tracce audio presenti

Richiede: pymediainfo (pip install pymediainfo) + libreria libmediainfo sul sistema.
Se pymediainfo non è installato o il file non è leggibile, restituisce un dict vuoto
senza sollevare eccezioni — il rename degrada silenziosamente al formato base.
"""

from __future__ import annotations
import os
from typing import Optional
from .constants import logger


def get_media_tags(filepath: str, retries: int = 3, retry_delay: float = 2.0) -> dict:
    """
    Analizza un file video e restituisce i tag tecnici per la rinomina.
    Ritenta fino a `retries` volte se il file risulta inaccessibile o vuoto
    (può succedere se chiamato mentre libtorrent sta ancora scrivendo/spostando).
    Non solleva eccezioni: in caso di errore ritorna {}.
    """
    if not filepath or not os.path.isfile(filepath):
        return {}
    try:
        from pymediainfo import MediaInfo
    except ImportError:
        logger.debug("pymediainfo not installed — technical tags unavailable")
        return {}

    import time as _time

    for attempt in range(1, retries + 1):
        try:
            # Controlla stabilità dimensione solo se il file è recente (< 10s):
            # evita sleep inutili su file già stabili sul NAS post-move.
            _mtime_age = _time.time() - os.path.getmtime(filepath)
            if _mtime_age < 10:
                size1 = os.path.getsize(filepath)
                _time.sleep(0.3)
                size2 = os.path.getsize(filepath)
                if size1 != size2:
                    logger.debug(f"mediainfo: file still being written '{filepath}' (attempt {attempt}/{retries}), waiting...")
                    _time.sleep(retry_delay)
                    continue

            mi = MediaInfo.parse(filepath)
        except Exception as e:
            logger.warning(f"⚠️ mediainfo parse error '{filepath}' (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                _time.sleep(retry_delay)
            continue

        video_tracks = [t for t in mi.tracks if t.track_type == 'Video']
        audio_tracks = [t for t in mi.tracks if t.track_type == 'Audio']

        # Se nessuna traccia trovata, riprova (file non ancora leggibile completamente)
        if not video_tracks and attempt < retries:
            logger.debug(f"mediainfo: no video tracks in '{filepath}' (attempt {attempt}/{retries}), retrying...")
            _time.sleep(retry_delay)
            continue

        result: dict = {}

        # ── Video ────────────────────────────────────────────────────────────
        if video_tracks:
            vt = video_tracks[0]
            result['resolution'] = _resolution_tag(vt)
            hdr = _hdr_tag(vt)
            if hdr:
                result['hdr'] = hdr
            vc = _video_codec_tag(vt)
            if vc:
                result['video_codec'] = vc

        # ── Audio: preferisce la traccia con Default=Yes, fallback alla prima ──
        if audio_tracks:
            # pymediainfo restituisce default come stringa 'Yes'/'No' o bool
            def _is_default(t):
                d = getattr(t, 'default', None)
                if d is None:
                    return False
                return str(d).lower() in ('yes', 'true', '1')

            at = next((t for t in audio_tracks if _is_default(t)), audio_tracks[0])
            ac = _audio_codec_tag(at)
            if ac:
                result['audio_codec'] = ac
            ch = _channels_tag(at)
            if ch:
                result['channels'] = ch

        # ── Lingue audio (tutte le tracce) ───────────────────────────────────
        langs = []
        for at in audio_tracks:
            lang = _normalize_lang(getattr(at, 'language', None))
            if lang and lang not in langs:
                langs.append(lang)
        if langs:
            result['languages'] = langs

        if result:
            return result

    logger.warning(f"⚠️ mediainfo: failed to extract tags from '{filepath}' after {retries} attempts — rename will use base format")
    return {}


# ── Helpers interni ───────────────────────────────────────────────────────

def _resolution_tag(t) -> Optional[str]:
    height = getattr(t, 'height', None)
    width  = getattr(t, 'width', None)
    scan   = (getattr(t, 'scan_type', '') or '').lower()
    suffix = 'i' if scan == 'interlaced' else 'p'
    
    if height is None and width is None:
        return None
        
    h = int(height) if height else 0
    w = int(width) if width else 0

    if w >= 3800 or h >= 2000: return f'2160{suffix}'
    if w >= 1900 or h >= 1000: return f'1080{suffix}'
    if w >= 1200 or h >= 700:  return f'720{suffix}'
    if h >= 540:               return f'576{suffix}'
    return f'480{suffix}'


def _hdr_tag(t) -> Optional[str]:
    # pymediainfo espone l'HDR in campi diversi a seconda della versione:
    # versioni recenti usano hdr_format_string (stringa completa) o hdr_format (solo nome)
    hdr_fmt    = (getattr(t, 'hdr_format', '')              or '').strip()
    hdr_str    = (getattr(t, 'hdr_format_string', '')       or '').strip()
    hdr_compat = (getattr(t, 'hdr_format_compatibility', '') or '').strip()
    transfer   = (getattr(t, 'transfer_characteristics', '') or '').strip()

    # Unifica tutto in un'unica stringa per i check
    hdr_all = f"{hdr_fmt} {hdr_str} {hdr_compat}".strip()

    if not hdr_all and not transfer:
        return None

    dv     = 'Dolby Vision' in hdr_all
    hdr10p = 'HDR10+' in hdr_all or 'HDR10 Plus' in hdr_all
    hdr10  = 'HDR10' in hdr_all
    hlg    = 'HLG' in transfer or 'HLG' in hdr_all
    pq     = 'PQ' in transfer  # HDR generico senza profilo esplicito (fallback)

    if dv and hdr10:   return 'DV HDR10'
    if dv:             return 'DV'
    if hdr10p:         return 'HDR10Plus'
    if hdr10:          return 'HDR10'
    if hlg:            return 'HLG'
    if pq:             return 'HDR'
    return None


def _video_codec_tag(t) -> Optional[str]:
    fmt     = (getattr(t, 'format', '') or '').lower()
    profile = (getattr(t, 'format_profile', '') or '').lower()
    codec   = (getattr(t, 'codec_id', '') or '').lower()

    if fmt == 'hevc' or 'hevc' in codec: return 'h265'
    if fmt == 'avc'  or 'avc'  in codec: return 'h264'
    if fmt == 'av1'  or 'av01' in codec: return 'AV1'
    if 'xvid' in fmt or 'xvid' in codec: return 'XviD'
    if 'divx' in fmt:                    return 'DivX'
    if 'vc-1' in fmt or 'vc1' in fmt:   return 'VC-1'
    if fmt:
        return fmt.upper()
    return None


def _audio_codec_tag(t) -> Optional[str]:
    fmt     = (getattr(t, 'format', '') or '').lower()
    comm    = (getattr(t, 'commercial_name', '') or '').lower()
    profile = (getattr(t, 'format_profile', '') or '').lower()
    codec   = (getattr(t, 'codec_id', '') or '').lower()

    atmos = 'atmos' in profile or 'atmos' in comm or 'joc' in fmt

    if 'truehd' in comm or 'truehd' in fmt:
        return 'TrueHD Atmos' if atmos else 'TrueHD'
    if 'e-ac-3' in fmt or 'eac-3' in fmt or 'eac3' in codec or 'dolby digital plus' in comm:
        return 'EAC3 Atmos' if atmos else 'EAC3'
    if 'ac-3' in fmt or 'ac3' in codec or ('dolby digital' in comm and 'plus' not in comm):
        return 'AC3'
    if 'dts' in fmt or 'dts' in codec:
        if 'ma' in profile or 'master' in profile: return 'DTS-MA'
        if 'x' in profile and 'x:' not in profile: return 'DTS-X'
        return 'DTS'
    if 'aac' in fmt or 'aac' in codec:  return 'AAC'
    if 'flac' in fmt:                   return 'FLAC'
    if 'opus' in fmt:                   return 'Opus'
    if 'mp3' in fmt or 'mpeg' in fmt:   return 'MP3'
    if 'pcm' in fmt:                    return 'PCM'
    if fmt:
        return fmt.upper()
    return None


def _channels_tag(t) -> Optional[str]:
    ch = getattr(t, 'channel_s', None)
    if ch is None:
        return None
    try:
        ch = int(ch)
    except (ValueError, TypeError):
        return None
    if ch == 1: return 'Mono'
    if ch == 2: return 'Stereo'
    if ch == 6: return '5.1'
    if ch == 8: return '7.1'
    return f'{ch}ch'


_LANG_MAP = {
    'italian': 'IT', 'italiano': 'IT', 'it': 'IT', 'ita': 'IT',
    'english': 'EN', 'inglese': 'EN',  'en': 'EN', 'eng': 'EN',
    'french':  'FR', 'francese': 'FR', 'fr': 'FR', 'fra': 'FR',
    'spanish': 'ES', 'spagnolo': 'ES', 'es': 'ES', 'spa': 'ES',
    'german':  'DE', 'tedesco':  'DE', 'de': 'DE', 'deu': 'DE',
    'portuguese': 'PT', 'pt': 'PT', 'por': 'PT',
    'russian': 'RU', 'ru': 'RU', 'rus': 'RU',
    'japanese': 'JA', 'ja': 'JA', 'jpn': 'JA',
    'chinese':  'ZH', 'zh': 'ZH', 'zho': 'ZH',
    'arabic':   'AR', 'ar': 'AR', 'ara': 'AR',
}

def _normalize_lang(lang: Optional[str]) -> Optional[str]:
    if not lang:
        return None
    return _LANG_MAP.get(lang.lower().strip())


def mediainfo_available() -> bool:
    """Verifica che pymediainfo sia installato E che la libreria C libmediainfo
    sia raggiungibile a runtime (can_parse() == True)."""
    try:
        from pymediainfo import MediaInfo
        return bool(MediaInfo.can_parse())
    except Exception:
        return False
