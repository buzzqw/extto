"""
EXTTO - Rename episodi TV dopo il completamento del download.

Formati di rinomina configurabili con @rename_format:

  base       →  Serie - S01E01 - Titolo.mkv
  standard   →  Serie (Anno) - S01E01 - Titolo [Qualità][Codec Video].mkv
  completo   →  Serie (Anno) - S01E01 - Titolo [lang][Qualità][Audio Ch][HDR][Codec][LingueAudio].mkv

Esempio completo:
  Monarch - Legacy of Monsters (2023) - S01E01 - Aftermath [italian lang][HDTV-2160p][EAC3 Atmos 5.1][HDR10Plus][h265][IT+EN].mkv

Opzioni in extto.conf:
  @rename_episodes = yes          (default: no)
  @rename_format   = completo     (default: base)
"""

import os
import re
from typing import Optional
from .constants import logger

_VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv'}
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str) -> str:
    return _INVALID_CHARS.sub('', name).strip()


RENAME_FORMATS = {'base': 'base', 'standard': 'standard', 'completo': 'completo', 'custom': 'custom'}

RENAME_FORMAT_LABELS = {
    'base':     'Base  —  Serie - S01E01 - Titolo.mkv',
    'standard': 'Standard  —  Serie (Anno) - S01E01 - Titolo [Qualità][Codec].mkv',
    'completo': 'Completo  —  Serie (Anno) - S01E01 - Titolo [Qualità][Audio Ch][HDR][Codec][Lingue].mkv',
}


def _build_filename(series_name: str, season: int, episode: int,
                    title, ext: str,
                    fmt: str = 'base',
                    year=None,
                    tags=None,
                    template_str: str = "") -> str:
    tags = tags or {}
    fmt  = fmt if fmt in RENAME_FORMATS else 'base'
    s_name   = _sanitize(series_name)
    ep_str   = f"S{season:02d}E{episode:02d}"
    ep_title = f" - {_sanitize(title)}" if title else ""

    # ---- NUOVO: LOGICA DEL FORMATO LIBERO ----
    if fmt == 'custom' and template_str:
        name = template_str
        name = name.replace('{Serie}', s_name)
        name = name.replace('{Anno}', str(year) if year else "")
        name = name.replace('{Stagione}', f"S{season:02d}")
        name = name.replace('{Episodio}', f"E{episode:02d}")
        name = name.replace('{Titolo}', _sanitize(title) if title else "")
        name = name.replace('{Risoluzione}', tags.get('resolution') or "")
        name = name.replace('{VideoCodec}', tags.get('video_codec') or "")
        
        # --- MODIFICA: Unisce Codec Audio e Canali (es: AAC 5.1) ---
        audio_c = tags.get('audio_codec') or ""
        chan = tags.get('channels') or ""
        audio_full = " ".join(filter(None, [audio_c, chan]))
        
        # Supportiamo il nuovo nome {Audio} (e manteniamo il vecchio per retrocompatibilità)
        name = name.replace('{Audio}', audio_full)
        name = name.replace('{AudioCodec}', audio_full)
        name = name.replace('{Canali}', chan)
        # -----------------------------------------------------------
        
        name = name.replace('{HDR}', tags.get('hdr') or "")
        
        langs = tags.get('languages', [])
        name = name.replace('{Lingue}', '+'.join(langs) if langs else "")
        
        # Pulisce eventuali parentesi vuote (anche con spazi) o doppi spazi rimasti
        name = re.sub(r'\[\s*\]|\(\s*\)', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        # Rimuove trattini/trattini bassi rimasti a fine nome (prima dell'estensione)
        name = re.sub(r'[\s\-_]+$', '', name)
        return name + ext
    # ------------------------------------------

    if fmt == 'base':
        return f"{s_name} - {ep_str}{ep_title}{ext}"

    year_str = f" ({year})" if year else ""

    if fmt == 'standard':
        parts = []
        if tags.get('resolution'):   parts.append(tags['resolution'])
        if tags.get('video_codec'):  parts.append(tags['video_codec'])
        tag_str = ''.join(f"[{p}]" for p in parts)
        name = f"{s_name}{year_str} - {ep_str}{ep_title}"
        return (name + (" " + tag_str if tag_str else "")).rstrip() + ext

    # completo
    parts = []
    langs = tags.get('languages', [])
    if tags.get('resolution'):   parts.append(tags['resolution'])
    audio_parts = list(filter(None, [tags.get('audio_codec'), tags.get('channels')]))
    if audio_parts:              parts.append(' '.join(audio_parts))
    if tags.get('hdr'):          parts.append(tags['hdr'])
    if tags.get('video_codec'):  parts.append(tags['video_codec'])
    if len(langs) > 1:           parts.append('+'.join(langs))
    elif len(langs) == 1:        parts.append(langs[0])

    tag_str = ''.join(f"[{p}]" for p in parts)
    name = f"{s_name}{year_str} - {ep_str}{ep_title}"
    return (name + (" " + tag_str if tag_str else "")).rstrip() + ext


def _get_series_year(tmdb_client, tmdb_id):
    if not tmdb_id or not tmdb_client:
        return None
    try:
        data = tmdb_client._get(f'/tv/{tmdb_id}', {'language': tmdb_client.language})
        date = (data or {}).get('first_air_date', '')
        return date[:4] if date and len(date) >= 4 else None
    except Exception as e:
        logger.debug(f"_get_series_year: {e}")
        return None


def rename_completed_torrent(torrent_name: str, save_path: str, cfg: dict, db=None) -> bool:
    if str(cfg.get('rename_episodes', 'no')).lower() not in ('yes', 'true', '1'):
        return False
    api_key = cfg.get('tmdb_api_key', '').strip()
    if not api_key:
        logger.debug("ℹ️  rename_episodes=yes but tmdb_api_key not configured — skipping rename")
        return False

    rename_fmt = str(cfg.get('rename_format', 'base')).strip().lower()
    rename_template = str(cfg.get('rename_template', '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}][{Lingue}]')).strip()
    if rename_fmt not in RENAME_FORMATS:
        rename_fmt = 'base'

    from .models import Parser
    from .tmdb import TMDBClient

    ep = Parser.parse_series_episode(torrent_name)
    if not ep:
        return False

    series_name = ep['name']
    season      = ep['season']
    episode     = ep['episode']

    # Usa il nome configurato in series.txt se disponibile (es. "Marshals" invece di "Marshals A Yellowstone Story")
    try:
        from .config import Config as _RenamerCfg
        _rcfg = _RenamerCfg()
        _match = _rcfg.find_series_match(series_name, season)
        if _match:
            series_name = _match['name']
    except Exception as e:
        logger.debug(f"find_series_match rename: {e}")

    tmdb_lang   = str(cfg.get('tmdb_language', 'it-IT')).strip()
    tmdb        = TMDBClient(api_key, cache_days=int(cfg.get('tmdb_cache_days', 7)), language=tmdb_lang)
    tmdb_id     = tmdb.get_tmdb_id_for_series(db, series_name) if db else tmdb.resolve_series_id(series_name)
    ep_title    = tmdb.fetch_episode_title(tmdb_id, season, episode) if tmdb_id else None

    if not ep_title:
        if episode == 0:
            logger.info(f"ℹ️  rename: episode 0 (pack) — proceeding with rename without specific title")
        else:
            ep_title = f"Episodio {episode}"
            logger.info(f"ℹ️  rename: title not found on TMDB for S{season:02d}E{episode:02d} — using fallback title '{ep_title}'")

    year = _get_series_year(tmdb, tmdb_id) if rename_fmt != 'base' else None

    renamed = 0
    renamed_video_fname = None  # Tiene traccia del nome esatto dopo la rinomina

    try:
        if os.path.isfile(save_path):
            # Path diretto al file (episodio singolo spostato su NAS):
            # evita di fare listdir sull'intera cartella e rinominare episodi sbagliati
            entries   = [os.path.basename(save_path)]
            save_path = os.path.dirname(save_path)
            logger.debug(f"rename: single-file path detected, scanning only '{entries[0]}'")
        else:
            entries = os.listdir(save_path)
    except Exception as e:
        logger.error(f"❌ rename: unable to read '{save_path}': {e}")
        return False

    from .models import normalize_series_name, _series_name_matches
    norm_series = normalize_series_name(series_name)

    for fname in entries:
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _VIDEO_EXTS:
            continue
        ep_f = Parser.parse_series_episode(fname)
        if ep_f:
            # Controlla stagione/episodio E nome serie — evita di rinominare
            # file di altre serie che hanno lo stesso S/E nella stessa cartella
            if ep_f['season'] != season or ep_f['episode'] != episode:
                continue
            if not _series_name_matches(normalize_series_name(ep_f['name']), norm_series):
                logger.debug(f"rename: skip '{fname}' — series mismatch ('{ep_f['name']}' ≠ '{series_name}')")
                continue
        else:
            # File senza info S/E nel nome: usa ep del torrent come fallback (comportamento originale)
            ep_f = ep

        tags = {}
        if rename_fmt != 'base':
            try:
                from .mediainfo_helper import get_media_tags
                tags = get_media_tags(os.path.join(save_path, fname))
            except Exception as e:
                logger.debug(f"mediainfo tags series: {e}")

        new_name = _build_filename(series_name, season, episode, ep_title, ext,
                                   fmt=rename_fmt, year=year, tags=tags, template_str=rename_template)
        src = os.path.join(save_path, fname)
        dst = os.path.join(save_path, new_name)
        
        if src == dst:
            renamed += 1
            renamed_video_fname = new_name
            continue
            
        if os.path.exists(dst):
            # --- GESTIONE CONFLITTO RINOMINA ---
            # Se il file destinazione esiste, confrontiamo la qualità
            try:
                from .models import Parser
                from .cleaner import _handle_duplicate
                q_src = Parser.parse_quality(fname)
                q_dst = Parser.parse_quality(new_name)
                
                if q_src.score() > q_dst.score():
                    logger.info(f"🗑️  Rename conflict: new file is better than existing '{new_name}'. Moving existing to trash.")
                    _handle_duplicate(dst, cfg.get('trash_path', ''), cfg.get('cleanup_action', 'move'), "rename conflict (inferior)")
                else:
                    logger.info(f"⏭️  Rename conflict: '{new_name}' existing is better or equal. Skipping new file '{fname}'.")
                    _handle_duplicate(src, cfg.get('trash_path', ''), cfg.get('cleanup_action', 'move'), "rename conflict (new is inferior)")
                    continue
            except Exception as e:
                logger.warning(f"⚠️  rename conflict error: {e}")
                continue
            
        try:
            os.rename(src, dst)
            logger.info(f"✏️  Renamed: '{fname}' → '{new_name}'")
            renamed += 1
            renamed_video_fname = new_name
        except Exception as e:
            logger.error(f"❌ rename: error on '{fname}': {e}")

    # ── CLEANUP DUPLICATI ─────────────────────────────────────────────────
    _cleanup   = str(cfg.get('cleanup_upgrades', 'no')).lower() in ('yes', 'true', '1')
    _trash     = str(cfg.get('trash_path', '')).strip()
    _action    = str(cfg.get('cleanup_action', 'move')).strip().lower()
    if _cleanup and (_trash or _action == 'delete'):
        try:
            from .cleaner import cleanup_old_episode as _clean_ep, discard_if_inferior as _discard
            _min_diff  = int(cfg.get('cleanup_min_score_diff', 0) or 0)
            _new_q     = ep.get('quality') or Parser.parse_quality(torrent_name)
            _new_score = _new_q.score() if hasattr(_new_q, 'score') else 0

            # FIX CRITICO: Forniamo l'esatto nome finale calcolato poco fa, senza farlo indovinare!
            _new_fname = renamed_video_fname if renamed_video_fname else torrent_name

            # CASO B: se il nuovo è inferiore a un esistente, va in trash immediatamente
            _discarded = _discard(
                series_name    = series_name,
                season         = season,
                episode        = episode,
                new_score      = _new_score,
                new_fname      = _new_fname,
                save_path      = save_path,
                trash_path     = _trash,
                min_score_diff = _min_diff,
                action         = _action,
            )

            if not _discarded:
                # CASO A: il nuovo è migliore, rimuovi i vecchi inferiori
                _removed = _clean_ep(
                    series_name    = series_name,
                    season         = season,
                    episode        = episode,
                    new_score      = _new_score,
                    new_title      = torrent_name,
                    archive_path   = save_path,
                    trash_path     = _trash,
                    min_score_diff = _min_diff,
                    new_fname      = _new_fname,
                    action         = _action,
                )
                if _removed > 0:
                    _verb = "eliminata/e" if _action == 'delete' else "spostata/e in trash"
                    logger.info(f"🗑️  Cleanup: {_removed} obsolete version(s) {_verb}")

        except Exception as _ce:
            logger.warning(f"⚠️  cleanup post-rename error: {_ce}")
    # ──────────────────────────────────────────────────────────────────────

    return renamed > 0


def rename_completed_movie(torrent_name: str, save_path: str, cfg: dict) -> bool:
    if str(cfg.get('rename_episodes', 'no')).lower() not in ('yes', 'true', '1'):
        return False
    api_key = cfg.get('tmdb_api_key', '').strip()
    if not api_key:
        return False

    rename_fmt = str(cfg.get('rename_format', 'base')).strip().lower()
    from .models import Parser
    from .tmdb import TMDBClient

    mov = Parser.parse_movie(torrent_name)
    if not mov or not mov.get('name'):
        return False

    tmdb_lang    = str(cfg.get('tmdb_language', 'it-IT')).strip()
    tmdb         = TMDBClient(api_key, cache_days=int(cfg.get('tmdb_cache_days', 7)), language=tmdb_lang)
    tmdb_results = tmdb._get('/search/movie', {'query': mov['name'], 'year': mov.get('year'), 'language': tmdb_lang})
    if not tmdb_results or not tmdb_results.get('results'):
        return False

    best          = tmdb_results['results'][0]
    official_title = best.get('title')
    release_date   = best.get('release_date', '')
    official_year  = release_date[:4] if release_date else (mov.get('year') or '')
    if not official_title:
        return False

    year_tag = f" ({official_year})" if official_year else ""
    renamed  = 0

    try:
        entries = os.listdir(save_path)
    except Exception as e:
        logger.error(f"❌ rename_movie: unable to read '{save_path}': {e}")
        return False

    for fname in entries:
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _VIDEO_EXTS or 'sample' in fname.lower():
            continue
        tags = {}
        if rename_fmt != 'base':
            try:
                from .mediainfo_helper import get_media_tags
                tags = get_media_tags(os.path.join(save_path, fname))
            except Exception as e:
                logger.debug(f"mediainfo tags film: {e}")

        parts = []
        if rename_fmt == 'completo':
            if tags.get('resolution'):  parts.append(tags['resolution'])
            audio_str = ' '.join(filter(None, [tags.get('audio_codec'), tags.get('channels')]))
            if audio_str:               parts.append(audio_str)
            if tags.get('hdr'):         parts.append(tags['hdr'])
            if tags.get('video_codec'): parts.append(tags['video_codec'])
        elif rename_fmt == 'standard':
            if tags.get('resolution'):  parts.append(tags['resolution'])
            if tags.get('video_codec'): parts.append(tags['video_codec'])
        else:
            if mov.get('quality') and mov['quality'].resolution != 'unknown':
                parts.append(mov['quality'].resolution)

        tag_str  = ''.join(f"[{p}]" for p in parts)
        new_name = (f"{_sanitize(official_title)}{year_tag}" + (" " + tag_str if tag_str else "")).rstrip() + ext
        src = os.path.join(save_path, fname)
        dst = os.path.join(save_path, new_name)
        if src == dst:
            continue
        if os.path.exists(dst):
            try:
                from .cleaner import _handle_duplicate
                q_src = mov.get('quality') or Parser.parse_quality(fname)
                q_dst = Parser.parse_quality(new_name)
                q_src_score = q_src.score() if hasattr(q_src, 'score') else 0
                q_dst_score = q_dst.score() if hasattr(q_dst, 'score') else 0
                if q_src_score > q_dst_score:
                    logger.info(f"🗑️  Rename film conflict: new file is better, moving '{new_name}' in trash.")
                    _handle_duplicate(dst, cfg.get('trash_path', ''), cfg.get('cleanup_action', 'move'), "film rename conflict (inferior)")
                else:
                    logger.info(f"⏭️  Rename film conflict: '{new_name}' esistente è migliore o uguale. Scarto '{fname}'.")
                    _handle_duplicate(src, cfg.get('trash_path', ''), cfg.get('cleanup_action', 'move'), "film rename conflict (new is inferior)")
                    continue
            except Exception as e:
                logger.warning(f"⚠️  rename_movie conflict error: {e}")
                continue
        try:
            os.rename(src, dst)
            logger.info(f"🎬 Movie Renamed: '{fname}' → '{new_name}'")
            renamed += 1
        except Exception as e:
            logger.error(f"❌ rename_movie: error on '{fname}': {e}")

    return renamed > 0
