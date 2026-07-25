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
import shutil
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
    # FIX: se il nome torrent è in italiano (es. "Daredevil Rinascita") e non matcha,
    # prova anche con solo il primo token (es. "Daredevil") per trovare la serie nel DB tramite alias.
    try:
        from .config import Config as _RenamerCfg
        _rcfg = _RenamerCfg()
        _match = _rcfg.find_series_match(series_name, season)
        if not _match:
            # Fallback: cerca per primo token — utile per titoli tradotti ("Daredevil Rinascita" → "Daredevil")
            _first_token = series_name.split()[0] if series_name else ''
            if _first_token and _first_token.lower() != series_name.lower():
                _match = _rcfg.find_series_match(_first_token, season)
                if _match:
                    logger.info(f"ℹ️  rename: match parziale per titolo tradotto '{series_name}' → '{_match['name']}' (via token '{_first_token}')")
        if _match:
            series_name = _match['name']
    except Exception as e:
        logger.debug(f"find_series_match rename: {e}")

    tmdb_lang   = str(cfg.get('tmdb_language', 'it-IT')).strip()
    tmdb        = TMDBClient(api_key, cache_days=int(cfg.get('tmdb_cache_days', 7)), language=tmdb_lang)
    tmdb_id     = tmdb.get_tmdb_id_for_series(db, series_name) if db else tmdb.resolve_series_id(series_name)
    # FIX: se TMDB non trova nulla con il nome corrente (può essere un titolo italiano tradotto
    # che non matcha su TMDB), riprova con il nome originale dal torrent prima di arrendersi.
    if not tmdb_id and series_name != ep['name'].replace('.', ' ').strip():
        _orig_name = ep['name'].replace('.', ' ').strip()
        logger.info(f"ℹ️  rename: TMDB fallback con nome originale torrent '{_orig_name}'")
        tmdb_id = tmdb.get_tmdb_id_for_series(db, _orig_name) if db else tmdb.resolve_series_id(_orig_name)
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
    video_files = [f for f in entries if os.path.splitext(f)[1].lower() in _VIDEO_EXTS]

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
            norm_ep_name = normalize_series_name(ep_f['name'])
            if not _series_name_matches(norm_ep_name, norm_series):
                # FIX: tolleranza titolo tradotto — "Daredevil Rinascita" vs "Daredevil Born Again"
                # Accetta se il primo token coincide (es. "daredevil" == "daredevil")
                _nw = norm_series.split()
                _ew = norm_ep_name.split()
                if _nw and _ew and _nw[0] == _ew[0]:
                    logger.debug(f"rename: match parziale (titolo tradotto) '{ep_f['name']}' ~ '{series_name}'")
                else:
                    logger.debug(f"rename: skip '{fname}' — series mismatch ('{ep_f['name']}' ≠ '{series_name}')")
                    continue
        else:
            if Parser.parse_movie(fname):
                logger.debug(f"rename: skip '{fname}' — riconosciuto come film, non episodio serie")
                continue
            if len(video_files) == 1:
                logger.info(f"ℹ️  rename: '{fname}' — nessun pattern S/E nel nome, rinomina impossibile")
            else:
                logger.debug(f"rename: skip '{fname}' — nessun pattern S/E riconoscibile nel nome file")
            continue

        tags = {}
        if rename_fmt != 'base':
            _media_path = os.path.join(save_path, fname)
            if not os.path.isfile(_media_path):
                logger.warning(f"⚠️  mediainfo: file non trovato per analisi tecnica: '{_media_path}'")
            else:
                try:
                    from .mediainfo_helper import get_media_tags
                    tags = get_media_tags(_media_path)
                    if not tags:
                        logger.warning(f"⚠️  mediainfo: nessun tag estratto da '{_media_path}' — verifica pymediainfo/libmediainfo")
                    else:
                        logger.debug(f"   mediainfo tags: {tags}")
                except Exception as e:
                    logger.warning(f"⚠️  mediainfo tags series: {e}")

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
            try:
                os.rename(src, dst)
            except OSError:
                # Fallback cross-device (es. libtorrent_dir e NAS su mount diversi)
                shutil.move(src, dst)
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

    if renamed > 0 and renamed_video_fname:
        return os.path.join(save_path, renamed_video_fname)
    return None


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

    year_tag           = f" ({official_year})" if official_year else ""
    renamed            = 0
    renamed_movie_fname = None

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
            try:
                os.rename(src, dst)
            except OSError:
                shutil.move(src, dst)
            logger.info(f"🎬 Movie Renamed: '{fname}' → '{new_name}'")
            renamed += 1
            renamed_movie_fname = new_name
        except Exception as e:
            logger.error(f"❌ rename_movie: error on '{fname}': {e}")

    if renamed > 0 and renamed_movie_fname:
        return os.path.join(save_path, renamed_movie_fname)
    return None


_TEMPLATE_PLACEHOLDER_RE = re.compile(
    r'\{(Serie|Anno|Stagione|Episodio|Titolo|Risoluzione|VideoCodec|Audio|AudioCodec|Canali|HDR|Lingue)\}'
)
# Placeholder "tag" che possono legittimamente risultare vuoti (HDR solo per
# contenuti HDR, Anno se TMDB non lo trova) — in quel caso _build_filename
# rimuove il blocco [{...}]/({...}) PER INTERO, quindi qui l'intero blocco
# (parentesi comprese) va reso opzionale, non solo il contenuto.
# Gli altri tag (Risoluzione/VideoCodec/Audio/AudioCodec/Canali/Lingue) sono
# sempre valorizzati da mediainfo su un file analizzato correttamente: se il
# loro blocco manca, il file NON è ancora nel formato giusto — vanno quindi
# tenuti obbligatori (solo il contenuto è jolly, le parentesi restano fisse).
_WRAPPED_PLACEHOLDER_RE = re.compile(r'([\[(])\{(HDR|Anno)\}([\])])')
_TEMPLATE_WILDCARDS = {
    'Anno':         r'(?:\(\d{4}\))?',
    'Titolo':       r'.+',
    'Risoluzione':  r'.*',
    'VideoCodec':   r'.*',
    'Audio':        r'.*',
    'AudioCodec':   r'.*',
    'Canali':       r'.*',
    'HDR':          r'.*',
    'Lingue':       r'.*',
}


def _pattern_regex(series_name: str, season: int, episode: int,
                    fmt: str, template_str: str = "") -> Optional['re.Pattern']:
    """Regex del pattern di rinomina EFFETTIVAMENTE configurato (base/standard/
    completo/custom), usata per verificare se un file segue GIA' quel formato
    senza dover interrogare TMDB per il titolo esatto.

    Deliberatamente più permissiva del nome esatto (il titolo/i tag possono
    variare), ma richiede la struttura letterale " - SxxExx - " subito dopo
    il nome serie sanificato — struttura che una release scene (punteggiata o
    con tag gruppo) non produce mai per caso.
    """
    s_name = re.escape(_sanitize(series_name))
    if not s_name:
        return None

    if fmt == 'custom' and template_str:
        # Prima passata: blocchi tag avvolti singolarmente tra [] o () — l'intero
        # blocco (parentesi comprese) è opzionale, perché _build_filename lo
        # rimuove del tutto quando il valore è vuoto (es. nessun tag HDR).
        wrapped_spans = []
        for m in _WRAPPED_PLACEHOLDER_RE.finditer(template_str):
            open_c, close_c = re.escape(m.group(1)), re.escape(m.group(3))
            wrapped_spans.append((m.start(), m.end(), f'(?:{open_c}.*?{close_c})?'))

        subs = dict(_TEMPLATE_WILDCARDS)
        subs['Serie']     = s_name
        subs['Stagione']  = re.escape(f"S{season:02d}")
        subs['Episodio']  = re.escape(f"E{episode:02d}")

        parts = []
        last = 0
        wi = 0
        for m in _TEMPLATE_PLACEHOLDER_RE.finditer(template_str):
            # Se questo placeholder fa parte di un blocco già gestito dalla
            # prima passata (wrapped_spans), saltalo qui: verrà emesso una
            # sola volta quando si raggiunge lo start del blocco.
            in_wrapped = wi < len(wrapped_spans) and wrapped_spans[wi][0] <= m.start() < wrapped_spans[wi][1]
            if in_wrapped:
                w_start, w_end, w_rx = wrapped_spans[wi]
                if last < w_start:
                    parts.append(re.escape(template_str[last:w_start]))
                parts.append(w_rx)
                last = w_end
                wi += 1
                continue
            parts.append(re.escape(template_str[last:m.start()]))
            parts.append(subs[m.group(1)])
            last = m.end()
        parts.append(re.escape(template_str[last:]))
        body = ''.join(parts)
    else:
        # base / standard / completo condividono lo stesso "cuore":
        # "{Serie}[ (Anno)] - SxxExx - {resto}"
        ep_str = re.escape(f"S{season:02d}E{episode:02d}")
        body = rf"{s_name}(?: \(\d{{4}}\))? - {ep_str} - .+"

    try:
        return re.compile(r'^' + body + r'\.\w+$', re.IGNORECASE)
    except re.error as e:
        logger.debug(f"_pattern_regex: regex invalida ({e})")
        return None


def _looks_renamed(fname: str, series_name: str, season: int, episode: int,
                   fmt: str = 'base', template_str: str = "") -> bool:
    pattern = _pattern_regex(series_name, season, episode, fmt, template_str)
    if pattern is None:
        return False
    return bool(pattern.match(fname))


def _extract_existing_title(fname: str, season: int, episode: int) -> Optional[str]:
    """Se il file ha già un titolo "vero" incorporato nel nome (es. da un
    rename precedente), lo estrae per poterlo riusare quando TMDB non
    risponde — evita di sovrascrivere un titolo buono con il fallback
    generico "Episodio N" solo perché TMDB ha un buco/404 per quell'episodio.
    """
    base = os.path.splitext(fname)[0]
    m = re.search(rf"S{season:02d}E{episode:02d}\s*-\s*(.+)$", base, re.IGNORECASE)
    if not m:
        return None
    title = m.group(1)
    # rimuove i tag finali tra parentesi/quadre (qualità/audio/lingua), anche
    # concatenati senza spazio, con un eventuale trattino residuo davanti
    title = re.sub(r'(\s*-)?(\s*[\[(][^\[\]()]*[\])])+\s*$', '', title).strip()
    if not title or title.lower().startswith('episodio'):
        return None
    return title


def run_lazy_rename_verify(db, cfg, limit: int = 5):
    """Verifica lazy dei nomi file già scaricati.

    Due fasi con costi molto diversi:
    - Fase 1 (economica: solo filesystem + regex, NIENTE TMDB): controlla fino
      a QUICK_SCAN_CAP episodi non ancora verificati per vedere se il nome file
      rispetta GIA' il pattern configurato. Quelli a posto vengono marcati
      verificati subito, senza consumare il budget di rinomina — altrimenti,
      con episodi vecchi già rinominati correttamente in cima alla coda (id
      ASC), il budget si esaurirebbe sempre su quelli e non si arriverebbe mai
      a controllare gli episodi realmente sospetti.
    - Fase 2 (costosa: TMDB + rename su disco): solo per gli episodi che la
      Fase 1 ha segnalato come NON conformi al pattern. Throttled a `limit`
      per ciclo, com'è giusto che sia per un'operazione che tocca file reali.
    """
    if not hasattr(cfg, 'get'):
        _get = lambda k, d=None: getattr(cfg, k, d) if hasattr(cfg, k) else d
    else:
        _get = cfg.get

    if str(_get('rename_episodes', 'no')).lower() not in ('yes', 'true', '1'):
        return {'verified': 0, 'renamed': 0, 'skipped': 0}
    api_key = _get('tmdb_api_key', '').strip()
    if not api_key:
        return {'verified': 0, 'renamed': 0, 'skipped': 0}

    rename_fmt = str(_get('rename_format', 'base')).strip().lower()
    rename_template = str(_get('rename_template', '')).strip()

    from .models import Parser, normalize_series_name, _series_name_matches

    QUICK_SCAN_CAP = 200  # tetto episodi/ciclo per il check strutturale (nessuna chiamata TMDB)

    verified = 0        # già a posto secondo il pattern configurato — nessun limite
    renamed = 0          # rinomine effettive (throttled a `limit`)
    skipped = 0          # file non trovato su disco
    quick_checked = 0
    seen_ids = set()
    needs_rename = []    # (ep_row, found_file, scan_dir) che non rispettano il pattern

    def _find_file(archive_path, s_name, s_season, s_episode):
        for sub in [archive_path,
                    os.path.join(archive_path, f"Stagione {s_season}"),
                    os.path.join(archive_path, f"Season {s_season}")]:
            if not os.path.isdir(sub):
                continue
            try:
                for fname in os.listdir(sub):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in _VIDEO_EXTS:
                        continue
                    ep_p = Parser.parse_series_episode(fname)
                    if not ep_p:
                        continue
                    if ep_p['season'] != s_season or ep_p['episode'] != s_episode:
                        continue
                    if not _series_name_matches(normalize_series_name(ep_p['name']),
                                                normalize_series_name(s_name)):
                        continue
                    return fname, sub
            except Exception:
                pass
        return None, None

    # --- Fase 1: check strutturale economico, fino a QUICK_SCAN_CAP episodi ---
    while quick_checked < QUICK_SCAN_CAP and len(needs_rename) < limit:
        batch = db.get_unverified_episodes(QUICK_SCAN_CAP * 2)
        if not batch:
            break

        fresh = [ep for ep in batch if ep['id'] not in seen_ids]
        if not fresh:
            break

        for ep_row in fresh:
            seen_ids.add(ep_row['id'])
            if quick_checked >= QUICK_SCAN_CAP or len(needs_rename) >= limit:
                break

            ep_id = ep_row['id']
            s_name = ep_row['series_name']
            s_season = ep_row['season']
            s_episode = ep_row['episode']
            archive_path = ep_row['archive_path']

            if not archive_path or not os.path.isdir(archive_path):
                skipped += 1
                continue

            found_file, scan_dir = _find_file(archive_path, s_name, s_season, s_episode)
            if not found_file:
                skipped += 1
                continue

            quick_checked += 1

            if _looks_renamed(found_file, s_name, s_season, s_episode, rename_fmt, rename_template):
                db.mark_episode_verified(ep_id)
                verified += 1
                continue

            needs_rename.append((ep_row, found_file, scan_dir))

    # --- Fase 2: rinomine vere e proprie (TMDB + disco), throttled a `limit` ---
    if needs_rename:
        from .tmdb import TMDBClient
        tmdb_lang = str(_get('tmdb_language', 'it-IT')).strip()
        tmdb_cache = int(_get('tmdb_cache_days', 7))
        tmdb = TMDBClient(api_key, cache_days=tmdb_cache, language=tmdb_lang)

        for ep_row, found_file, scan_dir in needs_rename[:limit]:
            ep_id = ep_row['id']
            s_name = ep_row['series_name']
            s_season = ep_row['season']
            s_episode = ep_row['episode']
            ep_label = f"{s_name} S{s_season:02d}E{s_episode:02d}"

            try:
                tmdb_id = ep_row.get('tmdb_id')
                if not tmdb_id:
                    tmdb_id = tmdb.get_tmdb_id_for_series(db, s_name) or tmdb.resolve_series_id(s_name)
                ep_title = tmdb.fetch_episode_title(tmdb_id, s_season, s_episode) if tmdb_id else None
                if not ep_title:
                    # TMDB non ha risposto (404/rate-limit/episodio non ancora
                    # in catalogo): se il file ha già un titolo vero, riusalo
                    # invece di sovrascriverlo col fallback generico.
                    ep_title = _extract_existing_title(found_file, s_season, s_episode) \
                               or f"Episodio {s_episode}"

                year = _get_series_year(tmdb, tmdb_id) if rename_fmt != 'base' else None

                tags = {}
                if rename_fmt != 'base':
                    try:
                        from .mediainfo_helper import get_media_tags
                        tags = get_media_tags(os.path.join(scan_dir, found_file)) or {}
                    except Exception:
                        pass

                ext = os.path.splitext(found_file)[1].lower()
                new_name = _build_filename(s_name, s_season, s_episode, ep_title, ext,
                                           fmt=rename_fmt, year=year, tags=tags,
                                           template_str=rename_template)

                src = os.path.join(scan_dir, found_file)
                dst = os.path.join(scan_dir, new_name)

                if src == dst:
                    db.mark_episode_verified(ep_id)
                    verified += 1
                    continue

                if os.path.exists(dst):
                    try:
                        q_src = Parser.parse_quality(found_file)
                        q_dst = Parser.parse_quality(new_name)
                        from .cleaner import _handle_duplicate
                        if q_src.score() > q_dst.score():
                            _handle_duplicate(dst, _get('trash_path', ''),
                                              _get('cleanup_action', 'move'),
                                              "rename verify (inferior)")
                        else:
                            _handle_duplicate(src, _get('trash_path', ''),
                                              _get('cleanup_action', 'move'),
                                              "rename verify (new inferior)")
                    except Exception as e_dst:
                        logger.debug(f"lazy_rename conflict {ep_label}: {e_dst}")
                    db.mark_episode_verified(ep_id)
                    verified += 1
                    continue

                try:
                    os.rename(src, dst)
                except OSError:
                    shutil.move(src, dst)
                logger.info(f"  ✏️  {ep_label}: '{found_file}' → '{new_name}'")
                renamed += 1
                verified += 1
                db.mark_episode_verified(ep_id)
            except Exception as e_ren:
                # Non marcare verificato: un fallimento reale (TMDB, permessi, ecc.)
                # deve essere ritentato al prossimo ciclo, non nascosto per sempre.
                logger.debug(f"lazy_rename error {ep_label}: {e_ren}")

    if quick_checked == 0:
        logger.info("🔍 Controllo rinomina: nessun episodio da verificare")
    else:
        already_ok = verified - renamed
        parts = [f"{quick_checked} episodi verificati"]
        if renamed > 0:
            parts.append(f"{renamed} rinominati")
        if already_ok > 0:
            parts.append(f"{already_ok} gia' a posto")
        logger.info("🔍 " + ", ".join(parts))

    return {'verified': verified, 'renamed': renamed, 'skipped': skipped}
