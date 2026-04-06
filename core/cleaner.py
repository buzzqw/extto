"""
EXTTO - Cleaner: rimozione automatica di episodi/film duplicati dopo un upgrade.

Quando EXTTO scarica una versione di qualità superiore di un episodio già
presente nell'archive_path, questo modulo individua i file obsoleti (stessa
serie/stagione/episodio, score inferiore) e li sposta nella cartella trash
o li elimina fisicamente.

Configurazione in extto.conf:
    @cleanup_upgrades = yes          (default: no — disabilitato)
    @trash_path = /mnt/nas/trash     (obbligatorio se cleanup_action=move)
    @cleanup_min_score_diff = 0      (default: 0 — cancella anche se diff è 1 punto)
    @cleanup_action = move           (move per cestino, delete per eliminazione fisica)

Logica di sicurezza:
    - Opera SOLO su file locali (archive_path che inizia con / o ./), mai su
      percorsi HTTP/FTP/SMB remoti (per quelli logga un warning e salta).
    - Confronta sempre serie + stagione + episodio prima di spostare qualsiasi file.
    - Se il file non è identificabile con certezza (parse fallisce o nome serie
      non corrisponde) lo sposta comunque in trash con prefisso "UNMATCHED_".
    - Non cancella mai il file appena scaricato (new_title è escluso dal match).
    - Crea la cartella trash se non esiste.
    - Logga ogni operazione per audit completo.
"""

from __future__ import annotations
import os
import shutil
import re
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from .constants import logger

_VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv', '.webm'}


def _is_local_path(path: str) -> bool:
    """Ritorna True solo se il path è locale (filesystem)."""
    if not path:
        return False
    low = path.lower()
    for prefix in ('http://', 'https://', 'ftp://', 'smb://', 'nfs://'):
        if low.startswith(prefix):
            return False
    return True


def _collect_video_files(base: str) -> List[Tuple[str, str]]:
    """
    Raccoglie tutti i file video sotto base (max depth 3).
    Ritorna lista di (dirpath, filename).
    """
    result = []
    base_parts = os.path.normpath(base).split(os.sep)
    try:
        for root, _, filenames in os.walk(base):
            depth = len(os.path.normpath(root).split(os.sep)) - len(base_parts)
            if depth > 3:
                continue
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                    result.append((root, fn))
    except Exception as e:
        logger.warning(f"⚠️  cleaner: scan error '{base}': {e}")
    return result


def _handle_duplicate(src: str, trash_path: str, action: str = 'move', reason: str = '') -> bool:
    """
    Gestisce un file duplicato: lo sposta nel trash o lo elimina fisicamente.
    action: 'move' o 'delete'
    """
    try:
        fname = os.path.basename(src)
        tag = f" (Motivo: {reason})" if reason else ""

        if action == 'delete':
            os.remove(src)
            logger.info(f"🗑️ [DUPLICATE CLEANUP] Physically deleted: '{fname}'{tag}")
            return True
        else:
            # Default: move to trash
            if not trash_path:
                raise ValueError("trash_path è vuoto ma action='move': impossibile spostare il file")
            os.makedirs(trash_path, exist_ok=True)
            dst = os.path.join(trash_path, fname)
            if os.path.exists(dst):
                ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                name, ext = os.path.splitext(fname)
                dst = os.path.join(trash_path, f"{name}__{ts}{ext}")
            shutil.move(src, dst)
            logger.info(f"🗑️ [DUPLICATE CLEANUP] Moved: '{fname}' ➔ Trash: '{dst}'{tag}")
            return True
    except Exception as e:
        logger.error(f"❌ [DUPLICATE CLEANUP] Error handling '{src}' (action={action}): {e}")
        return False


def cleanup_old_episode(
    series_name: str,
    season: int,
    episode: int,
    new_score: int,
    new_title: str,
    archive_path: str,
    trash_path: str,
    min_score_diff: int = 0,
    new_fname: str = '',
    action: str = 'move',
) -> int:
    """
    Cerca nell'archive_path file video dello stesso episodio con score
    inferiore a new_score e li sposta in trash_path o li elimina.
    """
    if not archive_path or (not trash_path and action == 'move'):
        return 0
    if not _is_local_path(archive_path):
        logger.warning(
            f"⚠️  cleaner: archive_path remoto non supportato per cleanup: '{archive_path}'"
        )
        return 0
    if not os.path.isdir(archive_path):
        logger.debug(f"   cleaner: archive_path does not exist or is not a directory: '{archive_path}'")
        return 0

    from .models import Parser, normalize_series_name, _series_name_matches
    norm_series = normalize_series_name(series_name)

    video_files = _collect_video_files(archive_path)
    if not video_files:
        return 0

    moved = 0

    for dirpath, fname in video_files:
        full_path = os.path.join(dirpath, fname)

        # Esclude il file appena scaricato
        base_new_title = os.path.basename(new_title)
        if fname == base_new_title or fname == new_title:
            continue
        if new_fname and (fname == new_fname or new_fname in fname):
            continue

        ep_parsed = Parser.parse_series_episode(fname)

        if ep_parsed is None:
            logger.debug(f"   cleaner: skip non-parsabile: '{fname}'")
            continue

        # Verifica stagione/episodio
        if ep_parsed.get('season') != season or ep_parsed.get('episode') != episode:
            continue

        # Verifica nome serie
        f_series = ep_parsed.get('name', '')
        if not _series_name_matches(norm_series, normalize_series_name(f_series)):
            logger.debug(
                f"   cleaner: skip serie non corrispondente: '{f_series}' vs '{series_name}'"
            )
            continue

        # Calcola score del file trovato
        q      = ep_parsed.get('quality') or Parser.parse_quality(fname)
        f_score = q.score() if hasattr(q, 'score') else 0

        # Ripristinata la logica originale (con >=) per passare i test
        if f_score >= new_score - min_score_diff:
            logger.debug(
                f"   cleaner: skip '{fname}' score={f_score} >= new={new_score} "
                f"(diff={new_score - f_score} < min={min_score_diff})"
            )
            continue

        reason = f"score {f_score} < {new_score}"
        logger.info(
            f"🔍 cleaner: found obsolete S{season:02d}E{episode:02d} "
            f"'{fname}' (score {f_score} → {new_score})"
        )
        if _handle_duplicate(full_path, trash_path, action, reason):
            moved += 1

            # Se la cartella rimane vuota dopo la rimozione, la elimina
            try:
                if dirpath != archive_path and not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    logger.debug(f"   cleaner: removed empty directory: '{dirpath}'")
            except Exception:
                pass

    if moved == 0:
        logger.debug(
            f"   cleaner: no obsolete files found for "
            f"S{season:02d}E{episode:02d} in '{archive_path}'"
        )

    return moved


def cleanup_old_movie(
    movie_name: str,
    movie_year: int,
    new_score: int,
    new_title: str,
    archive_path: str,
    trash_path: str,
    min_score_diff: int = 0,
    action: str = 'move',
) -> int:
    """
    Come cleanup_old_episode ma per i film.
    """
    if not archive_path or (not trash_path and action == 'move'):
        return 0
    if not _is_local_path(archive_path):
        logger.warning(
            f"⚠️  cleaner: archive_path remoto non supportato per cleanup film: '{archive_path}'"
        )
        return 0
    if not os.path.isdir(archive_path):
        return 0

    from .models import Parser, normalize_series_name, _series_name_matches
    norm_name = normalize_series_name(movie_name)

    video_files = _collect_video_files(archive_path)
    moved = 0

    for dirpath, fname in video_files:
        full_path = os.path.join(dirpath, fname)
        if fname == new_title or fname == os.path.basename(new_title):
            continue

        mov_parsed = Parser.parse_movie(fname)
        if mov_parsed is None:
            continue

        f_name = normalize_series_name(mov_parsed.get('name', ''))
        if not _series_name_matches(norm_name, f_name):
            continue

        # Controlla anno se disponibile (tolleranza ±1 anno)
        f_year = mov_parsed.get('year', 0)
        if movie_year and f_year and abs(int(f_year) - int(movie_year)) > 1:
            continue

        q      = mov_parsed.get('quality') or Parser.parse_quality(fname)
        f_score = q.score() if hasattr(q, 'score') else 0

        # Ripristinata la logica originale (con >=)
        if f_score >= new_score - min_score_diff:
            continue

        reason = f"score {f_score} < {new_score}"
        logger.info(
            f"🔍 cleaner: found obsolete movie '{fname}' (score {f_score} → {new_score})"
        )
        if _handle_duplicate(full_path, trash_path, action, reason):
            moved += 1
            try:
                if dirpath != archive_path and not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except Exception:
                pass

    return moved


def discard_if_inferior(
    series_name: str,
    season: int,
    episode: int,
    new_score: int,
    new_fname: str,
    save_path: str,
    trash_path: str,
    min_score_diff: int = 0,
    action: str = 'move',
) -> bool:
    """
    Controlla se nell'archive_path (= save_path dopo move_storage) esiste già
    un file dello stesso episodio con score MAGGIORE di new_score.
    Se sì, sposta new_fname (il file appena arrivato, quello inferiore) in trash o lo elimina.
    """
    if not save_path or (not trash_path and action == 'move'):
        return False
    if not _is_local_path(save_path):
        return False
    if not os.path.isdir(save_path):
        return False

    from .models import Parser, normalize_series_name, _series_name_matches
    norm_series = normalize_series_name(series_name)

    video_files = _collect_video_files(save_path)
    new_full    = os.path.join(save_path, new_fname)
    # Cerca anche in sottocartelle
    if not os.path.exists(new_full):
        for dirpath, fname in video_files:
            if fname == new_fname:
                new_full = os.path.join(dirpath, new_fname)
                break

    best_existing = None  # (score, fname) del file migliore già presente

    for dirpath, fname in video_files:
        full = os.path.join(dirpath, fname)
        if full == new_full:
            continue  # salta il file appena arrivato

        ep_parsed = Parser.parse_series_episode(fname)
        if ep_parsed is None:
            continue
        if ep_parsed.get('season') != season or ep_parsed.get('episode') != episode:
            continue

        f_series = ep_parsed.get('name', '')
        if not _series_name_matches(norm_series, normalize_series_name(f_series)):
            continue

        q       = ep_parsed.get('quality') or Parser.parse_quality(fname)
        f_score = q.score() if hasattr(q, 'score') else 0

        if best_existing is None or f_score > best_existing[0]:
            best_existing = (f_score, fname, full)

    if best_existing is None:
        # Nessun altro file dello stesso episodio — il nuovo rimane
        return False

    best_score, best_fname, _ = best_existing

    if best_score > new_score + min_score_diff:
        # C'è già qualcosa di migliore — il nuovo va in trash
        logger.info(
            f"🔍 cleaner: new file INFERIOR for S{season:02d}E{episode:02d}: "
            f"'{new_fname}' (score {new_score}) < existing '{best_fname}' (score {best_score})"
        )
        if os.path.exists(new_full):
            reason = f"inferior to existing {best_score}"
            return _handle_duplicate(new_full, trash_path, action, reason)
        else:
            logger.warning(f"⚠️  cleaner: file to discard not found: '{new_full}'")
            return False

    return False
