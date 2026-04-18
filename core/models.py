"""
EXTTO - Modelli dati: CycleStats, Quality, Parser.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple
from .constants import logger


# ---------------------------------------------------------------------------
# NORMALIZZAZIONE NOMI SERIE
# ---------------------------------------------------------------------------

def normalize_series_name(name: str) -> str:
    """Normalizza un nome serie per confronto robusto:
    - normalizza unicode → ASCII (è→e, à→a)
    - sostituisce separatori (. _ - / \\) con spazio
    - minuscolo, rimuove punteggiatura non alfanumerica
    - rimuove 's da possessivo (Grey's → Grey)
    - coalizza acronimi: lettere singole separate da spazio (s w a t → swat)
    - rimuove 's' isolata residua da possessivo (Grey s → Grey)
    - rimuove articoli iniziali IT/EN
    - comprime spazi multipli
    NOTA: l'anno NON viene rimosso — 'Doctor Who 2005' e 'Doctor Who'
    sono serie diverse e devono matchare separatamente.
    """
    if not name:
        return ""
    # Normalizza unicode (rimuove diacritici: è→e, à→a, ü→u …)
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if not unicodedata.combining(c))
    # Rimuovi 's da possessivo PRIMA di rimuovere l'apostrofo
    # "Grey's" → "Grey", "It's" → "It"
    name = re.sub(r"'s\b", '', name)
    # Rimuovi anche 's' finale se preceduta da minuscola (possessivo senza apostrofo nei torrent)
    # ma solo per parole lunghe per non distruggere acronimi o nomi corti (es. 'alias', 'epis')
    # "Greys" -> "Grey", ma "FBI" rimane "fbi" (perché maiuscolo o corto)
    # Usiamo una regex che cerca parole che finiscono per 's'
    words = []
    for w in name.split():
        if len(w) > 3 and w.lower().endswith('s'):
            # Se la parola originale aveva l'apostrofo è già stata gestita.
            # Qui gestiamo "Greys" -> "Grey"
            words.append(w[:-1])
        else:
            words.append(w)
    name = " ".join(words)

    # Separatori → spazio
    name = re.sub(r'[._\-/\\]', ' ', name)
    # Minuscolo
    name = name.lower()
    # Rimuovi tutto ciò che non è alfanumerico o spazio
    name = re.sub(r"[^a-z0-9\s]", '', name)
    # Coalizza acronimi PRIMA della rimozione possessivo-s
    # "s w a t" → "swat",  "c s i" → "csi",  "h b o" → "hbo"
    name = re.sub(r'\b([a-z])(?: ([a-z]))+\b',
                  lambda m: m.group(0).replace(' ', ''), name)
    # Rimuovi 's' isolata residua da possessivo (Grey s Anatomy → Grey Anatomy)
    name = re.sub(r'(?<=\w) s (?=\w)', ' ', name)
    # Rimuovi articoli iniziali IT/EN
    name = re.sub(r'^(the|a|an|il|lo|la|i|gli|le|un|una)\s+', '', name.strip())
    # Comprimi spazi multipli
    return re.sub(r'\s+', ' ', name).strip()


def _series_name_matches(norm_cfg: str, norm_ep: str) -> bool:
    """Confronto tra nome config (normalizzato) e nome estratto dal torrent.

    Contratto: l'utente scrive nomi completi e corretti in series_config.txt.
    Pertanto il confronto è ESATTO dopo normalizzazione, con un'unica
    tolleranza: varianti da apostrofo-possessivo ('s finale su una parola).

    'grey anatomy'  vs 'grey anatomy'  → True   (uguale)
    'grey anatomy'  vs 'greys anatomy' → True   (possessivo residuo)
    'fbi'           vs 'fbi'           → True   (uguale)
    'fbi'           vs 'fbi international' → False  (nomi diversi)
    'ncis'          vs 'ncis los angeles'  → False  (nomi diversi)
    """
    if norm_cfg == norm_ep:
        return True

    # Tolleranza possessivo: confronto word-by-word, ogni parola può differire
    # di una sola 's' finale (Grey's → grey vs greys → grey)
    wa = norm_cfg.split()
    wb = norm_ep.split()

    # Il nome torrent può avere più token del nome config SOLO se i token
    # extra sembrano info stagione/episodio (es. "fbi s01e01") — non titoli diversi.
    # Verifichiamo che le prime N parole corrispondano, poi accettiamo solo se
    # i token extra iniziano con un pattern stagione/episodio o sono dati tecnici.
    if len(wb) > len(wa):
        extra = wb[len(wa):]
        # I token extra devono essere: stagione (s01, e01, 1x02), anno (19xx/20xx),
        # risoluzione (720p, 1080p…) o simili — NON parole di testo libero.
        import re as _re
        _episode_like = _re.compile(
            r'^(?:s\d{1,2}|e\d{1,2}|\d{1,2}x\d{1,2}|19\d{2}|20\d{2}|'
            r'\d{3,4}p|complete|hdtv|webdl|bluray|web)$', _re.I
        )
        if not _episode_like.match(extra[0]):
            return False

    if len(wa) > len(wb):
        return False

    # Prendi solo le prime N parole di wb dove N è la lunghezza di wa
    wb_sub = wb[:len(wa)]

    for x, y in zip(wa, wb_sub):
        if x == y:
            continue
        if x + 's' == y or y + 's' == x:
            continue
        return False
    return True


# --- STATISTICHE CICLO ---
class CycleStats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.scraped = {'ExtTo': 0, 'Corsaro': 0, 'Archive': 0}
        self.candidates_count = 0
        self.series_matched = []
        self.movies_matched = []
        self.quality_rejected = []
        self.blacklisted = []
        self.size_rejected = []
        self.duplicates = []
        self.errors = 0
        self.error_details = {}   # categoria -> conteggio
        self.downloads_started = 0
        self.gaps_filled = 0
    def add_error(self, category: str):
        self.errors += 1
        self.error_details[category] = self.error_details.get(category, 0) + 1

    def report(self, cfg=None):
        logger.info("=" * 60)
        logger.info("📊 CYCLE REPORT")
        logger.info("=" * 60)

        logger.info(f"🌐 Scraping: ExtTo: {self.scraped['ExtTo']} | Corsaro: {self.scraped['Corsaro']} | Archive: {self.scraped['Archive']}")
        logger.info(f"🔍 Candidates: {self.candidates_count}")
        logger.info("-" * 60)

        def print_list(label, items, icon="•", show=True, max_items=0):
            if not show:
                return
            if not items:
                logger.info(f"{icon} {label}: 0")
                return
            logger.info(f"{icon} {label}: {len(items)}")
            items_to_show = items
            if max_items > 0 and len(items) > max_items:
                items_to_show = items[:max_items]
            for item in items_to_show:
                logger.info(f"      - {item}")
            if max_items > 0 and len(items) > max_items:
                logger.info(f"      ... and {len(items) - max_items} items")

        show_dupl  = cfg.debug_duplicates        if cfg else True
        show_black = cfg.debug_blacklisted       if cfg else True
        show_qual  = cfg.debug_quality_rejected  if cfg else True
        show_size  = cfg.debug_size_rejected     if cfg else True
        max_items  = cfg.debug_max_items         if cfg else 0

        print_list("Matched Series",    self.series_matched,   "✅")
        print_list("Matched Movie",     self.movies_matched,   "🎬")
        print_list("Discarded for Quality",  self.quality_rejected, "⚖️",  show_qual,  max_items)
        print_list("Discarded for Dimension", self.size_rejected,  "🗑️",  show_size,  max_items)
        print_list("Blacklisted",       self.blacklisted,      "⛔", show_black, max_items)
        print_list("Duplicated",         self.duplicates,       "🔁", show_dupl,  max_items)

        logger.info("-" * 60)
        if self.downloads_started > 0:
            logger.info(f"🚀 DOWNLOADS STARTED: {self.downloads_started}")
        if self.gaps_filled > 0:
            logger.info(f"🔍 GAPS FILLED: {self.gaps_filled}")
        if self.downloads_started == 0 and self.gaps_filled == 0:
            logger.info("💤 No downloads in this cycle")
        if self.errors > 0:
            detail = ", ".join(f"{v} {k}" for k, v in self.error_details.items())
            logger.warning(f"⚠️  Errori: {self.errors} ({detail})")
            logger.info("=" * 60)


# Istanza globale
stats = CycleStats()


# --- QUALITY ---
# --- QUALITY ---
@dataclass
class Quality:
    resolution: str = "unknown"
    source:     str = "unknown"
    codec:      str = "unknown"
    audio:      str = "unknown"  
    is_ita:      bool = False      # True se il titolo contiene marcatori italiano (ITA/IT/Italian)
    is_repack:   bool = False
    is_proper:   bool = False
    is_real:     bool = False
    is_dv:       bool = False
    group:       str = 'unknown'

    # Questi sono i valori di base (se il file extto.conf è vuoto)
    RES_PREF    = {'2160p': 2000, '1080p': 1000, '720p': 400, '576p': 80, '480p': 0, '360p': 0, 'unknown': 0}
    CODEC_PREF  = {'h265': 200, 'x265': 200, 'hevc': 200, 'h264': 50, 'x264': 50, 'avc': 50, 'unknown': 0}
    SOURCE_PREF = {'bluray': 300, 'webdl': 200, 'webrip': 150, 'hdtv': 50, 'dvdrip': 20, 'unknown': 0}
    AUDIO_PREF  = {'truehd': 150, 'dts-hd': 120, 'dts': 100, 'ddp': 80, 'ac3': 50, '5.1': 50, 'aac': 30, 'mp3': 10, 'unknown': 0}
    GROUP_PREF  = {'mircrew': 50, 'nahom': 30, 'TheBlackKing': 30, 'BlackBit': 30, 'unknown': 0}

    BONUS_DV     = 300
    BONUS_REAL   = 100
    BONUS_PROPER = 75
    BONUS_REPACK = 50

    @classmethod
    def load_from_config(cls, settings: dict):
        """Questa funzione aggiorna i punteggi leggendoli dal tuo file extto.conf"""
        # Carica le Risoluzioni
        for k in cls.RES_PREF:
            cls.RES_PREF[k] = int(settings.get(f'score_res_{k}', cls.RES_PREF[k]))
        # Carica le Sorgenti
        for k in cls.SOURCE_PREF:
            cls.SOURCE_PREF[k] = int(settings.get(f'score_source_{k}', cls.SOURCE_PREF[k]))
        # Carica i Codec
        for k in cls.CODEC_PREF:
            cls.CODEC_PREF[k] = int(settings.get(f'score_codec_{k}', cls.CODEC_PREF[k]))
        # Carica l'Audio
        for k in cls.AUDIO_PREF:
            cls.AUDIO_PREF[k] = int(settings.get(f'score_audio_{k}', cls.AUDIO_PREF[k]))
        # Carica i Gruppi dinamicamente dal file config, forzando tutto in minuscolo
        new_groups = {'unknown': 0}
       # 1. Base di default
        for dk, dv in {'mircrew': 50, 'nahom': 30, 'theblackking': 30, 'blackbit': 30}.items():
            new_groups[dk] = dv
       # 2. Carica tutto ciò che hai inserito dall'interfaccia
        for k, v in settings.items():
            if k.startswith('score_group_'):
                clean_key = k.replace('score_group_', '').strip().lower()
                try:
                   new_groups[clean_key] = int(v)
                except ValueError:
                    pass

        cls.GROUP_PREF = new_groups
            
        # Carica i Bonus semplici
        cls.BONUS_DV = int(settings.get('score_bonus_dv', cls.BONUS_DV))
        cls.BONUS_REAL = int(settings.get('score_bonus_real', cls.BONUS_REAL))
        cls.BONUS_PROPER = int(settings.get('score_bonus_proper', cls.BONUS_PROPER))
        cls.BONUS_REPACK = int(settings.get('score_bonus_repack', cls.BONUS_REPACK))

    def score(self) -> int:
        s = 0
        s += self.RES_PREF.get(self.resolution, 0)
        s += self.SOURCE_PREF.get(self.source, 0)
        s += self.CODEC_PREF.get(self.codec, 0)
        s += self.AUDIO_PREF.get(self.audio, 0)
        
        # Confronto gruppo più robusto
        g = self.group.lower() if self.group else "unknown"
        s += self.GROUP_PREF.get(g, 0)

        if self.is_dv:     s += self.BONUS_DV
        if self.is_real:   s += self.BONUS_REAL
        if self.is_proper: s += self.BONUS_PROPER
        if self.is_repack: s += self.BONUS_REPACK
        return s

    def __str__(self):
        parts = [self.resolution, self.source, self.codec]
        if self.is_ita:             parts.append("ITA")
        if self.is_dv:  parts.append("DV")
        if self.audio != "unknown": parts.append(self.audio.upper())
        if self.group != "unknown": parts.append(self.group.upper())
        if self.is_repack:
            parts.append("REPACK")
        return "/".join([p for p in parts if p != 'unknown'])

# --- PARSER ---
class Parser:
    DEFAULT_BLACKLIST = ['cam', 'hdcam', 'ts', 'telesync', 'screener', 'sample']
    BLACKLIST  = DEFAULT_BLACKLIST.copy()
    WANTEDLIST = []

    @staticmethod
    def is_blacklisted(title: str) -> Tuple[bool, Optional[str]]:
        if not title:
            return False, None
        t_normalized = re.sub(r'[._-]', ' ', title.lower())
        for p in Parser.BLACKLIST:
            if re.search(rf'\b{re.escape(p)}\b', t_normalized):
                return True, p
        return False, None

    @staticmethod
    def is_wanted(title: str) -> bool:
        if not Parser.WANTEDLIST:
            return True
        if not title:
            return False
        t_normalized = re.sub(r'[._-]', ' ', title.lower())
        for p in Parser.WANTEDLIST:
            if not re.search(rf'\b{re.escape(p)}\b', t_normalized):
                return False
        return True

    @staticmethod
    def parse_quality(title: str) -> Quality:
        t = title.lower() if title else ""
        q = Quality()
        
        # Risoluzione
        if '2160p' in t or '4k' in t or 'uhd' in t:  q.resolution = '2160p'
        elif '1080p' in t or 'fullhd' in t:           q.resolution = '1080p'
        elif '720p'  in t or 'hd' in t:               q.resolution = '720p'
        elif '576p'  in t or 'pal' in t:              q.resolution = '576p'
        elif '480p'  in t or 'ntsc' in t:             q.resolution = '480p'

        # Sorgente
        if   'bluray' in t or 'bdrip' in t or 'brrip' in t:    q.source = 'bluray'
        elif 'web-dl' in t or 'webdl' in t or 'web' in t:      q.source = 'webdl'
        elif 'webrip' in t:                                    q.source = 'webrip'
        elif 'hdtv'   in t or 'hdtvrip' in t:                  q.source = 'hdtv'
        elif 'dvdrip' in t or 'dvd' in t:                      q.source = 'dvdrip'

        # Normalizzazioni usate nei check successivi:
        # t_norm      : sostituisce [._-] con spazio (mantiene le parentesi quadre)
        # t_norm_lang : sostituisce anche [] con spazio — usato per DV, ITA, h265
        #               perché extto rinomina i file con formato [DV HDR10][IT][h265]
        t_norm      = re.sub(r'[._-]', ' ', t)
        t_norm_lang = re.sub(r'[._ \-\[\]]', ' ', t)

        # Dolby Vision — copre sia "DV" nei torrent (separato da punti/trattini)
        # sia "[DV HDR10]" nei file rinominati da extto (parentesi quadre rimosse)
        if (' dv ' in f' {t_norm_lang} ' or 'dovi' in t_norm_lang
                or 'dolby vision' in t_norm_lang):
            q.is_dv = True

        # Codec — h265 può apparire come x265/hevc nei torrent o [h265] nei rinominati
        if   'x265' in t or 'hevc' in t or 'h.265' in t or ' h265 ' in f' {t_norm_lang} ':
            q.codec = 'h265'
        elif 'x264' in t or 'avc'  in t or 'h.264' in t or ' h264 ' in f' {t_norm_lang} ':
            q.codec = 'h264'

        # Parsing Audio
        if 'dts-hd' in t or 'dtshd' in t:      q.audio = 'dts-hd'
        elif 'dts' in t:                       q.audio = 'dts'
        elif 'ddp5.1' in t or 'ddp 5.1' in t or 'eac3' in t: q.audio = 'ddp'
        elif 'ac3' in t or 'dd5.1' in t:       q.audio = 'ac3'
        elif '5.1' in t:                       q.audio = '5.1'
        elif 'mp3' in t:                       q.audio = 'mp3'
        elif 'aac' in t:                       q.audio = 'aac'

        # Release Group (Estrazione case-insensitive)
        m_group = re.search(r'[-]([a-z0-9]+)$|\[([a-z0-9]+)\]$', t)
        if m_group:
            q.group = m_group.group(1) or m_group.group(2)
        else:
       # Fallback dinamico su TUTTI i gruppi configurati!
            for common in Quality.GROUP_PREF.keys():
                if common != 'unknown' and common in t:
                    q.group = common
                    break

        # Lingua — usa t_norm_lang (già calcolato sopra, parentesi quadre rimosse)
        # per evitare falsi positivi e per coprire il formato [IT] dei file rinominati.
        # \bita\b copre i torrent normali; \bit\b copre [IT] → " it " dopo la normalizzazione.
        #
        # IMPORTANTE: rimuove prima i tag sorgente streaming noti che contengono
        # 'it' come sigla (iT = iTunes, WEBRip, ecc.) per evitare falsi positivi.
        # Esempio: '2160p.iT.WEB-DL' → 'iT' non è italiano, è iTunes.
        # ── Rilevamento lingua italiana ─────────────────────────────────────
        # Tre livelli di ricerca, dal più sicuro al più specifico:

        # Livello 1: ITA / Italian / Italiano — non ambigui, ricerca su tutto
        _STREAMING_TAGS = (
            r'\bit\b(?=\s+web)'    # .iT.WEB-DL / .iT.WEBRip (iTunes)
            r'|\bitunes\b'           # iTunes esplicito
            r'|\bamzn\b'             # Amazon
            r'|\bdsnp\b'             # Disney+
            r'|\bnf\b(?=\s+web)'   # .NF.WEB (Netflix)
            r'|\bhmax\b'             # HBO Max
            r'|\bparamount\b'
        )
        if re.search(r'\bita\b|\bitalian\b|\bitaliano\b',
                     re.sub(_STREAMING_TAGS, ' ', t_norm_lang)):
            q.is_ita = True

        # Livello 2: formato lingua esplicito composito — IT+EN, IT|EN, [IT], [IT+EN]
        # Cerca sull'originale (t già lowercase) perché la normalizzazione rimuove + e []
        # Il "+" non appare mai nel titolo di un episodio, quindi è un indicatore sicuro
        elif re.search(r'\bit[\+\|]|\[it\]', t):
            q.is_ita = True

        # Livello 3: \bit\b solo dalla RISOLUZIONE in poi
        # Evita falsi positivi da parole nel titolo episodio ("Feel It Still", "It Chapter")
        # I tag lingua nei torrent appaiono sempre nella parte tecnica, mai nel titolo
        else:
            _res_m = re.search(
                r'\b(2160p?|1080p?|720p?|480p?|4k|uhd|bluray|web[\s\-]?dl|webrip|hdtv)\b',
                t_norm_lang
            )
            _tech = re.sub(_STREAMING_TAGS, ' ', t_norm_lang[_res_m.start():] if _res_m else '')
            if re.search(r'\bit\b', _tech) and not re.search(
                    r'\bwith\b|\bbit\b|\bsplit\b|\bedit\b|\bunit\b|\bvisit\b|'
                    r'\blimit\b|\bexit\b|\bprofit\b|\bsubmit\b|\bcommit\b|'
                    r'\bpermit\b|\badmit\b|\bomit\b|\bhit\b|\bkit\b|\bpit\b|'
                    r'\bsit\b|\bfit\b|\bwit\b|\bknit\b|\bspit\b|\bslit\b|'
                    r'\bitunes\b',
                    _tech):
                q.is_ita = True

        # Revisioni
        if 'repack' in t or 'rerip' in t: q.is_repack = True
        if 'proper' in t:                 q.is_proper = True
        if re.search(r'\breal\b', t):     q.is_real   = True
        
        return q

    @staticmethod
    def get_res_rank(resolution: str) -> int:
        ranks = {'unknown': 0, '360p': 1, '480p': 2, '576p': 3, '720p': 4, '1080p': 5, '2160p': 6}
        return ranks.get(resolution, 0)

    @staticmethod
    def parse_series_episode(title: str):
        is_bl, reason = Parser.is_blacklisted(title)
        if is_bl:
            return None
        if not Parser.is_wanted(title):
            return None

        q = Parser.parse_quality(title)

        # 1a. Range episodi: S02E01-05 oppure S02E01-E05
        match = re.search(
            r'(.+?)[ ._-]+[Ss](\d{1,2})[Ee](\d{1,2})[-–][Ee]?(\d{1,2})(?:[ ._-]|$)',
            title
        )
        if match:
            season   = int(match.group(2))
            ep_start = int(match.group(3))
            ep_end   = int(match.group(4))
            if ep_end < ep_start:
                ep_end = ep_start
            ep_range = list(range(ep_start, ep_end + 1))
            return {
                'type':         'series',
                'name':         match.group(1).replace('.', ' ').strip(),
                'season':       season,
                'episode':      ep_start,
                'episode_range': ep_range,
                'quality':      q,
                'title':        title,
                'is_pack':      True,
            }

        # 1b. Multi-episodio concatenato: S02E01E02E03
        match = re.search(r'(.+?)[ ._-]+[Ss](\d{1,2})([Ee]\d{1,2}){2,}', title)
        if match:
            season   = int(match.group(2))
            episodes = [int(e) for e in re.findall(r'[Ee](\d{1,2})', title[match.start(2):])]
            if episodes:
                return {
                    'type':         'series',
                    'name':         match.group(1).replace('.', ' ').strip(),
                    'season':       season,
                    'episode':      episodes[0],
                    'episode_range': episodes,
                    'quality':      q,
                    'title':        title,
                    'is_pack':      True,
                }

        # 1c. Standard SxxExx (episodio singolo)
        match = re.search(r'(.+?)[ ._-]+[Ss](\d{1,2})[Ee](\d{1,2})', title)
        if match:
            return {
                'type': 'series',
                'name': match.group(1).replace('.', ' ').strip(),
                'season': int(match.group(2)),
                'episode': int(match.group(3)),
                'quality': q,
                'title': title
            }

        # 2. NxNN (1x02)
        match = re.search(r'(.+?)[ ._-]+(\d{1,2})x(\d{1,4})(?:[ ._-]|$)', title)
        if match:
            return {
                'type': 'series',
                'name': match.group(1).replace('.', ' ').strip(),
                'season': int(match.group(2)),
                'episode': int(match.group(3)),
                'quality': q,
                'title': title
            }

        # 3. Date format YYYY-MM-DD
        match = re.search(r'(.+?)[ ._-]+(\d{4})[-.]((\d{1,2})[-.])(\d{1,2})(?:[ ._-]|$)', title)
        if match:
            try:
                from datetime import date
                d = date(int(match.group(2)), int(match.group(4)), int(match.group(5)))
                return {
                    'type': 'series',
                    'name': match.group(1).replace('.', ' ').strip(),
                    'season': int(match.group(2)),
                    'episode': d.timetuple().tm_yday,
                    'quality': q,
                    'title': title,
                    'date_based': True
                }
            except Exception:
                pass

        # 4. Season packs
        pack_patterns = [
            r'(.+?)[ ._-]+[Ss](\d{1,2})(?:[ ._-]|$)(?!.*[Ee]\d+)',
            r'(.+?)[ ._-]+Season[ ._-]?(\d{1,2})',
            r'(.+?)[ ._-]+Complete[ ._-]+[Ss](\d{1,2})'
        ]
        for pat in pack_patterns:
            match = re.search(pat, title, re.I)
            if match:
                if re.search(r'[Ee]\d{1,2}', title[match.end():]):
                    continue
                return {
                    'type': 'series',
                    'name': match.group(1).replace('.', ' ').strip(),
                    'season': int(match.group(2)),
                    'episode': 0,
                    'episode_range': [0],  # <--- Il fix che mancava per i pack completi!
                    'quality': q,
                    'title': title,
                    'is_pack': True
                }

        return None

    @staticmethod
    def parse_movie(title: str):
        is_bl, reason = Parser.is_blacklisted(title)
        if is_bl:
            return None
        if not Parser.is_wanted(title):
            return None
        if re.search(r'[Ss]\d{1,2}[Ee]\d{1,2}', title):
            return None
        year = 0
        ym = re.search(r'\b(19|20)\d{2}\b', title)
        if ym:
            year = int(ym.group(0))
        
        q = Parser.parse_quality(title)
        
        return {
            'type': 'movie',
            'name': title,
            'year': year,
            'quality': q,
            'title': title
        }

    @staticmethod
    def parse_size_mb(size_str: str) -> float:
        if not size_str:
            return 0
        m = re.search(r'([\d.]+)\s*([KMGT])i?B', size_str, re.I)
        if not m:
            return 0
        num, unit = float(m.group(1)), m.group(2).upper()
        units = {'K': 0.001, 'M': 1, 'G': 1024, 'T': 1024 * 1024}
        return num * units.get(unit, 1)
