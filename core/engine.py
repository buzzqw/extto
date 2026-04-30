"""
EXTTO - Engine: scraping, ricerca archivio, generazione feed XML.
"""

import re
import time
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import List, Dict
from urllib.parse import urlparse, urljoin, unquote, quote

import requests
from bs4 import BeautifulSoup

from .constants import (
    MAX_PAGES, XML_FILE, PORT,
    sanitize_magnet, _extract_date_from_element,
    _load_feed_buffer, _save_feed_buffer, _extract_btih,
    logger
)
from .models import stats, Parser
from .database import ArchiveDB, SmartCache, Database
from .constants import CACHE_FILE

# Mappa ISO 639-2 → ISO 639-1 per subtitle queries
_LANG3_TO_LANG2 = {
    'ita': 'it', 'eng': 'en', 'fra': 'fr', 'deu': 'de', 'spa': 'es',
    'jpn': 'ja', 'por': 'pt', 'chi': 'zh', 'zho': 'zh', 'kor': 'ko',
    'rus': 'ru', 'ara': 'ar', 'tur': 'tr', 'pol': 'pl', 'nld': 'nl',
    'swe': 'sv', 'nor': 'no', 'fin': 'fi', 'ces': 'cs', 'hun': 'hu',
}
_LANG2_TO_LANG3 = {v: k for k, v in _LANG3_TO_LANG2.items()}

def _subtitle_query_terms(raw: str) -> list:
    """Data una sigla (o lista separata da virgola), restituisce tutti i termini
    da aggiungere alla query Jackett: sia la forma a 2 che a 3 lettere.
    Es: 'ita' → ['sub ita', 'sub it']
        'ita,eng' → ['sub ita', 'sub it', 'sub eng', 'sub en']
        'it' → ['sub it', 'sub ita']
    """
    terms = []
    for code in raw.lower().split(','):
        code = code.strip()
        if not code:
            continue
        terms.append(f'sub {code}')
        # Aggiunge anche la variante complementare
        if code in _LANG3_TO_LANG2:
            terms.append(f'sub {_LANG3_TO_LANG2[code]}')
        elif code in _LANG2_TO_LANG3:
            terms.append(f'sub {_LANG2_TO_LANG3[code]}')
    return terms


def _get_current_season(series_name: str) -> int | None:
    """
    Determina la stagione da usare nella ricerca ampia Jackett, in ordine di priorità:

    1. Stagione con episodi in stato 'downloading' / 'pending' / 'queued' (download attivi)

    2. Stagione degli ultimi 3 episodi scaricati (per numero stagione/episodio, non per data):
       - Se quegli episodi sono gli ULTIMI della stagione secondo TMDB → cerca stagione+1
         (TMDB usato SOLO per sapere se la stagione corrente è conclusa, non per trovare gap)
       - Altrimenti → cerca la stagione corrente (ci sono ancora episodi da aspettare)
       I buchi interni (es. E05 mancante tra E04 e E06) vengono ignorati qui e lasciati
       al gap filling mirato che li cerca episodio per episodio.

    3. Fallback → stagione più alta nel DB (errore TMDB o DB vuoto)

    Ritorna None solo se il DB è completamente vuoto per questa serie → query generica.
    """
    try:
        db = Database()

        # Recupera series_id dalla tabella series (episodes non ha series_name)
        sid_row = db.conn.execute(
            "SELECT id FROM series WHERE LOWER(name) = LOWER(?)",
            (series_name,)
        ).fetchone()
        if not sid_row:
            return None
        series_id = int(sid_row[0])

        # Ultimi 3 episodi per numero stagione/episodio (ordine logico, non temporale)
        last3 = db.conn.execute(
            """SELECT season, episode FROM episodes
               WHERE series_id = ?
               ORDER BY season DESC, episode DESC LIMIT 3""",
            (series_id,)
        ).fetchall()
        if not last3:
            return None  # Nessun episodio in DB → query generica

        current_season = int(last3[0][0])
        max_episode    = int(last3[0][1])

        # Controlla se siamo alla fine della stagione corrente usando TMDB.
        # TMDB usato SOLO per sapere quanti episodi ha questa stagione specifica —
        # non per trovare gap né per decidere quante stagioni esistono.
        # Se TMDB fallisce → restiamo sulla stagione corrente (safe).
        try:
            expected = db.get_expected_episodes(series_id, current_season)
            if expected and max_episode >= expected:
                return current_season + 1  # Stagione completa → cerca la prossima
        except Exception as e:
            logger.debug(f"next_season check: {e}")

        return current_season

    except Exception as e:
        logger.debug(f"_next_season: {e}")
        return None


class Engine:
    def __init__(self):
        try:
            import cloudscraper
            self.sess = cloudscraper.create_scraper(
                browser={'browser': 'firefox', 'platform': 'linux', 'mobile': False}
            )
        except ImportError:
            self.sess = requests.Session()
            self.sess.headers.update({
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            })
        self.archive  = ArchiveDB()
        self.cache    = SmartCache(CACHE_FILE)
        self.age_filter = {'days': 0, 'threshold': 0.8}

    def close(self):
        try:
            if hasattr(self, 'archive') and hasattr(self.archive, 'conn'):
                self.archive.conn.close()
        except Exception as e:
            logger.debug(f"archive conn close: {e}")
        try:
            if hasattr(self, 'sess'):
                self.sess.close()
        except Exception as e:
            logger.debug(f"sess close: {e}")

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # MANUAL SEARCH
    # ------------------------------------------------------------------

    def perform_manual_search(self, query: str, config_urls: List[str]) -> List[Dict]:
        results = []
        clean_q = quote(query)

        # Ricerca Archivio Locale
        arch_res = self.archive.search(query)
        for r in arch_res:
            r['source'] = 'Archivio DB'
            r['score']  = Parser.parse_quality(r['title']).score()
            results.append(r)

        # Ricerca Live — usa max_pages=1 localmente, senza toccare la costante globale
        # (evita race condition in ambienti multi-thread come Flask/Waitress)
        search_urls = []
        for u in config_urls:
            if 'ext.to' in u or 'extto' in u:
                parsed = urlparse(u)
                base   = f"{parsed.scheme}://{parsed.netloc}/search/{clean_q}/"
                search_urls.append(base)
            elif 'ilcorsaronero' in u or 'corsaro' in u:
                parsed = urlparse(u)
                base   = f"{parsed.scheme}://{parsed.netloc}/search/{clean_q}"
                search_urls.append(base)

        for url in search_urls:
            try:
                if 'ext.to' in url or 'extto' in url:
                    items = list(self._extto(url, max_pages=1))
                    for i in items:
                        i['source'] = 'ExtTo Live'
                    results.extend(items)
                elif 'corsaro' in url:
                    items = list(self._corsaro(url, max_pages=1))
                    for i in items:
                        i['source'] = 'Corsaro Live'
                    results.extend(items)
                elif 'get-posts/user:' in url:
                    items = list(self._tgx_user(url))
                    results.extend(items)
            except Exception as e:
                logger.error(f"Search error {url}: {e}")

        # --- INTEGRAZIONE INDEXER (Jackett + Prowlarr) ---
        try:
            from .config import Config
            cfg_obj = Config()
            if self._get_indexers(cfg_obj):
                j_results = self._jackett_search(query, {})
                results.extend(j_results)
        except Exception as e:
            logger.error(f"Manual indexer search error: {e}")

        # --- MOTORI DI RICERCA WEB (BT4G, SolidTorrents, ...) ---
        try:
            ws_results = self._web_search_all(query)
            results.extend(ws_results)
        except Exception as e:
            logger.error(f"Web search engines error: {e}")

        unique = {}
        for r in results:
            if r['magnet'] not in unique:
                if 'score' not in r:
                    r['score'] = Parser.parse_quality(r['title']).score()
                unique[r['magnet']] = r

        final_list = list(unique.values())
        final_list.sort(key=lambda x: x['score'], reverse=True)
        return final_list

    # ------------------------------------------------------------------
    # FEED
    # ------------------------------------------------------------------

    def update_feed_and_generate(self, live_items: List[Dict], max_items: int = 1000):
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            buf     = _load_feed_buffer()

            def _key_for(item: Dict) -> str:
                btih = _extract_btih(item.get('magnet', ''))
                if btih:
                    return f"btih:{btih}"
                return f"title:{(item.get('title') or '').strip().lower()}"

            merged: Dict[str, Dict] = {}
            for it in buf:
                k = _key_for(it)
                if not k:
                    continue
                merged[k] = {
                    'title':    it.get('title', ''),
                    'magnet':   it.get('magnet', ''),
                    'source':   it.get('source', 'feed'),
                    'added_at': it.get('added_at', now_iso)
                }

            new_count = 0
            for it in (live_items or []):
                k      = _key_for(it)
                if not k:
                    continue
                is_new = k not in merged
                merged[k] = {
                    'title':    it.get('title', ''),
                    'magnet':   it.get('magnet', ''),
                    'source':   it.get('source', 'live'),
                    'added_at': now_iso
                }
                if is_new:
                    new_count += 1

            all_items = list(merged.values())

            def _parse_dt(s):
                try:
                    return datetime.fromisoformat(s.replace('Z', '+00:00'))
                except Exception:
                    return datetime.now(timezone.utc)  # data malformata, usa ora corrente

            all_items.sort(key=lambda x: _parse_dt(x.get('added_at', now_iso)), reverse=True)
            limited = all_items[: max(1, int(max_items or 1000))]
            _save_feed_buffer(limited)

            to_xml = [{'title': it['title'], 'magnet': it['magnet']} for it in limited]
            self.generate_xml(to_xml)
            logger.info(f"📰 Feed updated: +{new_count} new, total buffer {len(limited)}/{max_items}")
        except Exception as e:
            logger.error(f"❌ Error updating rolling feed: {e}")

    # ------------------------------------------------------------------
    # SCRAPING
    # ------------------------------------------------------------------

    def scrape_all(self, urls, domain: str = 'all'):
        """domain: 'all' | 'series' | 'movies' | 'comics' — skippa le query
        Jackett/Prowlarr non pertinenti al dominio richiesto."""
        items = []
        
        # 1. Scraping Fonti Tradizionali (ExtTo / Corsaro)
        for idx, url in enumerate(urls):
            logger.debug(f"🔎 [{idx+1}/{len(urls)}]: {url[:60]}...")
            try:
                if 'ext.to' in url or 'extto' in url:
                    g = list(self._extto(url))
                    stats.scraped['ExtTo'] += len(g)
                    items.extend(g)
                    logger.info(f"🔎 ExtTo [{idx+1}/{len(urls)}]: {len(g)} items — {url[:55]}...")
                elif 'corsaro' in url:
                    g = list(self._corsaro(url))
                    stats.scraped['Corsaro'] += len(g)
                    items.extend(g)
                    logger.info(f"🔎 Corsaro [{idx+1}/{len(urls)}]: {len(g)} items — {url[:55]}...")
                elif 'get-posts/user:' in url:
                    g = list(self._tgx_user(url))
                    stats.scraped['TGx'] = stats.scraped.get('TGx', 0) + len(g)
                    items.extend(g)
                    logger.info(f"🔎 TGx [{idx+1}/{len(urls)}]: {len(g)} items — {url[:55]}...")
                else:
                    g = list(self._generic_rss(url))
                    src_label = self._rss_source_label(url)
                    stats.scraped[src_label] = stats.scraped.get(src_label, 0) + len(g)
                    items.extend(g)
                    logger.info(f"🔎 RSS [{idx+1}/{len(urls)}]: {len(g)} items — {url[:55]}...")
            except Exception as e:
                logger.error(f"❌ Scraping error {url}: {e}")
                stats.add_error("scraping")

        # 2. Scraping Intelligente via Jackett + Prowlarr
        try:
            from .config import Config
            cfg_obj = Config()
            indexers = self._get_indexers(cfg_obj)

            if indexers:
                label_str = ' + '.join(ix['label'] for ix in indexers)
                logger.debug(f"🔎 Querying indexer: {label_str}...")
                # FIX-F: inizializza contatori per ogni indexer attivo
                for ix in indexers:
                    stats.scraped[ix['label']] = 0

                series_list = getattr(cfg_obj, 'series', [])
                movies_list = getattr(cfg_obj, 'movies', [])

                for s in (series_list if domain != 'movies' else []):
                    enabled = s.get('enabled', False) if isinstance(s, dict) else getattr(s, 'enabled', False)
                    name    = s.get('name', '')       if isinstance(s, dict) else getattr(s, 'name', '')
                    lang    = (s.get('language') or s.get('lang') or '') if isinstance(s, dict) else getattr(s, 'language', getattr(s, 'lang', ''))
                    sub     = (s.get('subtitle') or '') if isinstance(s, dict) else getattr(s, 'subtitle', '')
                    aliases = s.get('aliases', [])    if isinstance(s, dict) else getattr(s, 'aliases', [])
                    if not (enabled and name):
                        continue

                    # Stagione corrente: prima downloading/pending, poi la più alta nel DB
                    current_season = _get_current_season(name)
                    season_param   = current_season  # passato a Torznab come &season=N

                    lang_str = str(lang).strip().lower()
                    sub_str  = str(sub).strip().lower()

                    # Costruisce la lista di nomi da cercare: nome principale + aliases
                    names_to_search = [str(name).strip()]
                    for a in (aliases or []):
                        a = str(a).strip()
                        if a and a.lower() != name.lower():
                            names_to_search.append(a)

                    ix_items = []
                    for search_name in names_to_search:
                        query = search_name
                        if current_season:
                            query = f"{query} S{current_season:02d}"
                            if search_name == names_to_search[0]:
                                logger.debug(f"   ↳ [{name}] season detected: {current_season} → query+S{current_season:02d}")
                        else:
                            if search_name == names_to_search[0]:
                                logger.debug(f"   ↳ [{name}] no season in DB, generic query")

                        if lang_str and lang_str not in ('custom', 'none', 'any', '*'):
                            query = f"{query} {lang_str}"

                        queries_to_run = []
                        if sub_str and sub_str not in ('none', 'any', '*'):
                            for term in _subtitle_query_terms(sub_str):
                                queries_to_run.append(f"{query} {term}")
                        else:
                            queries_to_run.append(query)

                        for q in queries_to_run:
                            ix_items.extend(self._jackett_search(q, {}, season=season_param))
                        if len(names_to_search) > 1:
                            time.sleep(3)

                    # FIX-F: aggiorna contatore per sorgente
                    for r in ix_items:
                        src = r.get('source', '')
                        for ix in indexers:
                            if ix['label'] in src:
                                stats.scraped[ix['label']] = stats.scraped.get(ix['label'], 0) + 1
                    items.extend(ix_items)
                    time.sleep(3)

                for m in (movies_list if domain != 'series' else []):
                    enabled = m.get('enabled', False) if isinstance(m, dict) else getattr(m, 'enabled', False)
                    name    = m.get('name', '')       if isinstance(m, dict) else getattr(m, 'name', '')
                    lang    = (m.get('language') or m.get('lang') or '') if isinstance(m, dict) else getattr(m, 'language', getattr(m, 'lang', ''))
                    sub     = (m.get('subtitle') or '') if isinstance(m, dict) else getattr(m, 'subtitle', '')
                    if not (enabled and name):
                        continue
                    query    = str(name).strip()
                    lang_str = str(lang).strip().lower()
                    if lang_str and lang_str not in ('custom', 'none', 'any', '*'):
                        query = f"{query} {lang_str}"
                    sub_str = str(sub).strip().lower()
                    queries_to_run = []
                    if sub_str and sub_str not in ('none', 'any', '*'):
                        for term in _subtitle_query_terms(sub_str):
                            queries_to_run.append(f"{query} {term}")
                    else:
                        queries_to_run.append(query)
                    ix_items = []
                    for q in queries_to_run:
                        ix_items.extend(self._jackett_search(q, {}))
                    for r in ix_items:
                        src = r.get('source', '')
                        for ix in indexers:
                            if ix['label'] in src:
                                stats.scraped[ix['label']] = stats.scraped.get(ix['label'], 0) + 1
                    items.extend(ix_items)
                    time.sleep(3)
        except Exception as e:
            logger.error(f"❌ Indexer scraping error: {e}")

        if items:
            # Filtra: salva in archive.db solo i risultati dagli indexer con flag abilitato
            try:
                from .config import Config as _Cfg
                _cfg = _Cfg()
                items_to_archive = [
                    i for i in items
                    if not (
                        ('Jackett' in i.get('source', '') and not _cfg.jackett_save_to_archive) or
                        ('Prowlarr' in i.get('source', '') and not _cfg.prowlarr_save_to_archive)
                    )
                ]
            except Exception as e:
                logger.warning(f"rescore_archive sort: {e}")
                items_to_archive = items
            from .models import Parser as _Parser
            items_to_archive = [i for i in items_to_archive
                                if not _Parser.is_content_filtered(i.get('title', ''))]
            if items_to_archive:
                self.archive.save_batch(items_to_archive)
            self.cache.save()
        return items

    def search_archive_for_config(self, config):
        found = []
        seen_magnets = set()
        for s in config.series:
            names = [s['name']] + [a for a in s.get('aliases', []) if a and a.lower() != s['name'].lower()]
            for name in names:
                for item in self.archive.search(name):
                    if item['magnet'] not in seen_magnets:
                        seen_magnets.add(item['magnet'])
                        found.append(item)
        for m in config.movies:
            found.extend(self.archive.search(m['name']))
        stats.scraped['Archive'] = len(found)
        return found

    def _flaresolverr_get(self, url: str, timeout: int = 60):
        """Chiama FlareSolverr per bypassare Cloudflare su ext.to.
        Restituisce (status_code, html_text) se riesce, None altrimenti.
        Come effetto collaterale aggiorna self.sess con i cookies e lo User-Agent
        ricevuti da FlareSolverr, così le richieste successive (pagine dettaglio)
        passano direttamente senza dover richiamare FlareSolverr."""
        from .config import Config as _Cfg
        fs_url = getattr(_Cfg(), 'flaresolverr_url', '').strip().rstrip('/')
        if not fs_url:
            return None
        try:
            resp = requests.post(
                f"{fs_url}/v1",
                json={"cmd": "request.get", "url": url, "maxTimeout": timeout * 1000},
                timeout=timeout + 10,
            )
            if resp.status_code != 200:
                logger.warning(f"⚠️ FlareSolverr HTTP {resp.status_code}")
                return None
            data = resp.json()
            if data.get('status') != 'ok':
                logger.warning(f"⚠️ FlareSolverr status: {data.get('status')} — {data.get('message','')}")
                return None
            sol = data['solution']
            # Copia cookies nella sessione requests → le detail page non richiedono FlareSolverr
            for c in sol.get('cookies', []):
                self.sess.cookies.set(
                    c['name'], c['value'],
                    domain=c.get('domain', '').lstrip('.')
                )
            ua = sol.get('userAgent', '')
            if ua:
                self.sess.headers['User-Agent'] = ua
            return sol.get('status', 200), sol.get('response', '')
        except Exception as e:
            logger.warning(f"⚠️ FlareSolverr error: {e}")
            return None

    def _search_bitsearch(self, query: str, limit: int = 20) -> List[Dict]:
        """Ricerca su bitsearch.to (ex solidtorrents.to) via JSON API (no Cloudflare)."""
        from urllib.parse import quote_plus as _qp
        # Prova entrambi i domini: bitsearch.to è il nome attuale, solidtorrents.to redirige
        for host in ('https://bitsearch.to', 'https://solidtorrents.to'):
            url = f"{host}/api/v1/search?q={_qp(query)}&fuv=yes&limit={limit}"
            try:
                r = self.sess.get(url, timeout=15, allow_redirects=True)
                if r.status_code != 200:
                    continue
                out = []
                for item in r.json().get('results', []):
                    h = (item.get('infohash') or '').strip().lower()
                    t = (item.get('title')    or '').strip()
                    if not h or len(h) != 40 or not t:
                        continue
                    out.append({'title': t,
                                'magnet': f"magnet:?xt=urn:btih:{h}&dn={_qp(t)}",
                                'source': 'BitSearch'})
                return out
            except Exception:
                continue
        logger.warning("⚠️ BitSearch: nessuna risposta da bitsearch.to / solidtorrents.to")
        return []

    def _search_bt4g(self, query: str, max_results: int = 8) -> List[Dict]:
        """Ricerca su bt4gprx.com (Cloudflare → FlareSolverr).

        Flusso:
        1. Ottieni la pagina risultati via FlareSolverr (imposta i cookie in self.sess).
        2. Raccogli i link alle pagine dettaglio (≤ max_results).
        3. Recupera ogni pagina dettaglio con self.sess (cookie già validi, no FlareSolverr).
        4. Estrai il magnet dalla pagina dettaglio; fallback: costruisci dall'hash nell'URL.
        """
        from urllib.parse import quote_plus as _qp, urljoin
        _BASE = 'https://bt4gprx.com'
        search_url = f"{_BASE}/search?q={_qp(query)}&p=1&order=age"

        # ── 1. Pagina risultati ────────────────────────────────────────────────
        html = None
        try:
            r = self.sess.get(search_url, timeout=15)
            if r.status_code == 200 and 'just a moment' not in r.text.lower():
                html = r.text
        except Exception:
            pass
        if not html:
            fs = self._flaresolverr_get(search_url, timeout=40)
            if fs and fs[0] == 200:
                html = fs[1]
        if not html:
            logger.warning("⚠️ BT4G: nessuna risposta dalla pagina risultati (configura FlareSolverr)")
            return []

        # ── 2. Raccogli link pagine dettaglio ─────────────────────────────────
        soup = BeautifulSoup(html, 'html.parser')
        _detail_re = re.compile(r'^/(?:detail|torrent|item)/\S+', re.I)
        detail_links = []
        seen_href = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not _detail_re.match(href):
                continue
            full = urljoin(_BASE, href)
            if full in seen_href:
                continue
            seen_href.add(full)
            title_guess = a.get_text(' ', strip=True)
            detail_links.append((full, title_guess))
            if len(detail_links) >= max_results:
                break

        if not detail_links:
            logger.warning("⚠️ BT4G: nessun link dettaglio trovato nella pagina risultati")
            return []

        # ── 3+4. Visita ogni pagina dettaglio e cerca il magnet ──────────────
        _magnet_href_re = re.compile(r'^magnet:\?xt=urn:btih:', re.I)
        _btih_url_re    = re.compile(r'[0-9a-fA-F]{40}', re.I)
        out = []

        for detail_url, title_guess in detail_links:
            try:
                dr = self.sess.get(detail_url, timeout=15)
                if dr.status_code != 200:
                    continue
                dsoup = BeautifulSoup(dr.text, 'html.parser')
                magnet = None

                # Prima scelta: <a href="magnet:?..."> diretto
                for ma in dsoup.find_all('a', href=_magnet_href_re):
                    magnet = ma['href']
                    break

                # Fallback: costruisci dall'hash nell'URL (presente in quasi tutti i DHT engine)
                if not magnet:
                    m = _btih_url_re.search(detail_url)
                    if m:
                        h = m.group(0).lower()
                        # prova a trovare un titolo migliore dalla pagina
                        title_tag = dsoup.find('h1') or dsoup.find('h2') or dsoup.find('title')
                        title_guess = (title_tag.get_text(' ', strip=True) if title_tag else title_guess) or title_guess
                        magnet = f"magnet:?xt=urn:btih:{h}&dn={_qp(title_guess[:200])}"

                if not magnet:
                    continue

                # Aggiusta il dn= con il titolo migliore ricavato dalla pagina
                if not title_guess:
                    title_tag = dsoup.find('h1') or dsoup.find('h2') or dsoup.find('title')
                    title_guess = title_tag.get_text(' ', strip=True) if title_tag else ''

                out.append({'title': title_guess or query,
                            'magnet': magnet,
                            'source': 'BT4G'})
            except Exception as e:
                logger.debug(f"BT4G detail {detail_url}: {e}")
                continue

        logger.info(f"🌐 BT4G: {len(out)} risultati per '{query}'")
        return out

    def _search_1337x(self, query: str, max_results: int = 8) -> List[Dict]:
        """Ricerca su 1337x.to e mirror (prova in ordine, diretto poi FlareSolverr).

        Flusso uguale a BT4G: FlareSolverr sulla listing page, poi self.sess per i dettagli.
        """
        from urllib.parse import quote as _q, urljoin
        MIRRORS = [
            'https://1337x.to',
            'https://1337x.st',
            'https://x1337x.ws',
            'https://x1337x.eu',
            'https://x1337x.cc',
        ]
        _CF_SIGNS = ('just a moment', 'checking your browser', 'enable javascript')

        def _is_cf(text: str) -> bool:
            lo = text.lower()
            return any(s in lo for s in _CF_SIGNS)

        # ── 1. Trova un mirror funzionante ────────────────────────────────────
        html = None
        base = None
        for mirror in MIRRORS:
            search_url = f"{mirror}/search/{_q(query, safe='')}/1/"
            # Prova diretta
            try:
                r = self.sess.get(search_url, timeout=15)
                if r.status_code == 200 and not _is_cf(r.text):
                    html = r.text
                    base = mirror
                    break
            except Exception:
                pass
            # FlareSolverr
            fs = self._flaresolverr_get(search_url, timeout=40)
            if fs and fs[0] == 200 and not _is_cf(fs[1]):
                html = fs[1]
                base = mirror
                break

        if not html or not base:
            logger.warning("⚠️ 1337x: nessun mirror raggiungibile")
            return []

        # ── 2. Estrai link pagine dettaglio dalla listing ─────────────────────
        soup = BeautifulSoup(html, 'html.parser')
        _det_re = re.compile(r'^/torrent/\d+/', re.I)
        detail_links, seen_href = [], set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not _det_re.match(href):
                continue
            full = urljoin(base, href)
            if full in seen_href:
                continue
            seen_href.add(full)
            detail_links.append((full, a.get_text(' ', strip=True)))
            if len(detail_links) >= max_results:
                break

        if not detail_links:
            logger.warning(f"⚠️ 1337x ({base}): nessun risultato")
            return []

        # ── 3. Visita ogni dettaglio e preleva il magnet ──────────────────────
        _mag_re = re.compile(r'^magnet:\?xt=urn:btih:', re.I)
        out = []
        for detail_url, title_guess in detail_links:
            try:
                dr = self.sess.get(detail_url, timeout=15)
                if dr.status_code != 200:
                    continue
                dsoup = BeautifulSoup(dr.text, 'html.parser')
                magnet = None
                for ma in dsoup.find_all('a', href=_mag_re):
                    magnet = ma['href']
                    break
                if not magnet:
                    continue
                h1 = dsoup.find('h1')
                title = (h1.get_text(strip=True) if h1 else title_guess) or title_guess
                out.append({'title': title, 'magnet': magnet, 'source': '1337x'})
            except Exception as e:
                logger.debug(f"1337x detail {detail_url}: {e}")

        logger.info(f"🌐 1337x: {len(out)} risultati per '{query}' (mirror: {base})")
        return out

    def _web_search_all(self, query: str) -> List[Dict]:
        """Interroga tutti i motori di ricerca web abilitati nella config."""
        from .config import Config
        engines = getattr(Config(), 'websearch_engines', []) or []
        if not engines:
            return []
        results = []
        if 'bitsearch' in engines:
            results.extend(self._search_bitsearch(query))
        if 'bt4g' in engines:
            results.extend(self._search_bt4g(query))
        if '1337x' in engines:
            results.extend(self._search_1337x(query))
        # Deduplicazione per BTIH hash
        seen, unique = set(), []
        for r in results:
            h = _extract_btih(r.get('magnet', ''))
            if h and h not in seen:
                seen.add(h)
                unique.append(r)
        return unique

    def _sanity_check(self, size_str):
        if not size_str:
            return True
        mb = Parser.parse_size_mb(size_str)
        if mb > 0 and mb < 50:
            return False
        return True

    def _extto(self, url, max_pages: int = None):
        # --- ESTRAE L'UTENTE DALL'URL ---
        tag = "ExtTo"
        m_user = re.search(r'filter=u=([^&]+)', url)
        if m_user:
            tag = f"ExtTo - {unquote(m_user.group(1))}"

        parsed = urlparse(url)
        base   = f"{parsed.scheme}://{parsed.netloc}"

        pages = max_pages if max_pages is not None else MAX_PAGES
        for page in range(pages):
            try:
                p_url = f"{url}&page={page}" if page > 0 else url

                # Prova FlareSolverr (bypass Cloudflare); fallback a requests diretto
                fs = self._flaresolverr_get(p_url)
                if fs is not None:
                    status_code, html_text = fs
                    if status_code != 200:
                        logger.warning(f"⚠️ ExtTo FlareSolverr status {status_code}: {p_url[:60]}")
                        break
                    soup = BeautifulSoup(html_text, 'html.parser')
                else:
                    res = self.sess.get(p_url, timeout=10)
                    if res.status_code != 200:
                        logger.warning(f"⚠️ ExtTo HTTP {res.status_code}: {p_url[:60]}")
                        break
                    soup = BeautifulSoup(res.content, 'html.parser')

                # Struttura nuova: <a class="torrent-title-link" href="/SLUG-ID/" ...>
                title_links = soup.find_all('a', class_='torrent-title-link')

                # Fallback struttura vecchia: magnet diretto nella listing
                if not title_links:
                    old_links = soup.find_all('a', href=re.compile(r'^magnet:\?'))
                    if not old_links:
                        break
                    for l in old_links:
                        t = unquote(re.search(r'dn=([^&]+)', l['href']).group(1)).replace('+', ' ')
                        t_display = f"{t} [{tag}]"
                        mg = sanitize_magnet(l['href'], t_display) or l['href']
                        if mg.startswith('magnet:?'):
                            if 'dn=' in mg:
                                mg = re.sub(r'dn=[^&]+', f"dn={quote(t_display)}", mg)
                            else:
                                mg += f"&dn={quote(t_display)}"
                        yield {'title': t_display, 'magnet': mg, 'source': tag}
                    time.sleep(1.5)
                    continue

                cutoff = None
                if isinstance(self.age_filter, dict) and (self.age_filter.get('days', 0) or 0) > 0:
                    cutoff = datetime.now() - timedelta(days=int(self.age_filter.get('days', 0)))
                items_on_page = 0
                old_on_page   = 0

                for a in title_links:
                    items_on_page += 1

                    # Titolo: testo del link (BS4 strip automaticamente i tag interni <span>)
                    t = a.get_text(strip=True)
                    if not t:
                        continue
                    t_display   = f"{t} [{tag}]"
                    detail_href = a.get('href', '')
                    if not detail_href:
                        continue
                    detail_url = urljoin(base, detail_href)

                    # Cache: evita visite duplicate alla pagina di dettaglio
                    cached = self.cache.get(t)
                    if cached:
                        mg = sanitize_magnet(cached, t_display) or cached
                        if mg.startswith('magnet:?'):
                            if 'dn=' in mg:
                                mg = re.sub(r'dn=[^&]+', f"dn={quote(t_display)}", mg)
                            else:
                                mg += f"&dn={quote(t_display)}"
                        yield {'title': t_display, 'magnet': mg, 'source': tag}
                        continue

                    try:
                        time.sleep(0.8)
                        sub      = self.sess.get(detail_url, timeout=10)
                        if sub.status_code != 200:
                            logger.debug(f"_extto detail HTTP {sub.status_code}: {detail_url[:60]}")
                            continue
                        sub_soup = BeautifulSoup(sub.content, 'html.parser')

                        if cutoff is not None:
                            dt = _extract_date_from_element(sub_soup)
                            if dt and dt < cutoff:
                                old_on_page += 1
                                continue

                        # Magnet via link webga.zx (struttura attuale di ext.to)
                        # <a href="https://webga.zx/show?magnet=magnet:?xt=urn:btih:...">
                        magnet = None
                        for lnk in sub_soup.find_all('a', href=True):
                            h = lnk['href']
                            if 'magnet=' in h and 'xt=urn:btih:' in h:
                                magnet = h.split('magnet=', 1)[1]
                                break

                        # Fallback: magnet diretto
                        if not magnet:
                            mag_a = sub_soup.find('a', href=re.compile(r'^magnet:\?'))
                            if mag_a:
                                magnet = mag_a['href']

                        # Fallback: hash grezzo nel testo
                        if not magnet:
                            txt = sub_soup.get_text(' ')
                            mh  = re.search(r'\b([a-fA-F0-9]{40}|[A-Z2-7]{32})\b', txt)
                            if mh:
                                magnet = f"magnet:?xt=urn:btih:{mh.group(1).lower()}&dn={quote(t_display)}"

                        if not magnet:
                            logger.debug(f"_extto: no magnet on {detail_url[:60]}")
                            continue

                        mg = sanitize_magnet(magnet, t_display) or magnet
                        if mg.startswith('magnet:?'):
                            if 'dn=' in mg:
                                mg = re.sub(r'dn=[^&]+', f"dn={quote(t_display)}", mg)
                            else:
                                mg += f"&dn={quote(t_display)}"

                        self.cache.set(t, mg)
                        yield {'title': t_display, 'magnet': mg, 'source': tag}

                    except Exception as _detail_err:
                        logger.debug(f"_extto detail error '{t[:60]}': {_detail_err}")

                if cutoff is not None and items_on_page > 0:
                    ratio = old_on_page / max(1, items_on_page)
                    if ratio >= float(self.age_filter.get('threshold', 0.8)):
                        logger.info(f"⏳ ExtTo early-stop: page {page} old (>{self.age_filter.get('days')}d) {old_on_page}/{items_on_page}")
                        break
                time.sleep(1.5)
            except Exception as _page_err:
                logger.debug(f"_extto page {page} error: {_page_err}")
                break
                
    # ------------------------------------------------------------------
    # TORZNAB UNIFICATO: Jackett + Prowlarr
    # ------------------------------------------------------------------

    @staticmethod
    def _torznab_url(base_url: str) -> str:
        """Costruisce l'endpoint Torznab corretto in base al tipo di indexer.
        
        - Jackett:  /api/v2.0/indexers/all/results/torznab/api
        - Prowlarr: /api/v1/indexer/all/results/torznab/api
        
        Il rilevamento è automatico: se l'URL contiene 'prowlarr' o la porta
        tipica 9696, usa il path Prowlarr; altrimenti usa quello Jackett.
        """
        url = base_url.rstrip('/')
        # Heuristica: porta 9696 = Prowlarr di default; altrimenti 9117 = Jackett
        is_prowlarr = ('prowlarr' in url.lower() or ':9696' in url)
        if is_prowlarr:
            return f"{url}/api/v1/indexer/all/results/torznab/api"
        else:
            return f"{url}/api/v2.0/indexers/all/results/torznab/api"

    @staticmethod
    def _get_indexers(cfg_obj) -> list:
        """Costruisce la lista di indexer attivi dalla configurazione.
        
        Supporta sia il formato legacy (jackett_url/jackett_api) sia il nuovo
        (prowlarr_url/prowlarr_api), restituendo sempre una lista omogenea:
        [{'url': ..., 'api': ..., 'label': ...}, ...]
        """
        indexers = []
        # Jackett (legacy + corrente)
        j_url = getattr(cfg_obj, 'jackett_url', '').strip()
        j_api = getattr(cfg_obj, 'jackett_api', '').strip()
        if j_url and j_api:
            indexers.append({'url': j_url, 'api': j_api, 'label': 'Jackett'})
        # Prowlarr
        p_url = getattr(cfg_obj, 'prowlarr_url', '').strip()
        p_api = getattr(cfg_obj, 'prowlarr_api', '').strip()
        if p_url and p_api:
            indexers.append({'url': p_url, 'api': p_api, 'label': 'Prowlarr'})
        return indexers

    def get_jackett_rss(self, config: dict) -> list:
        """Feed RSS globale da tutti gli indexer configurati (Jackett e/o Prowlarr).
        
        Chiamato ogni 30 minuti dal Fast RSS Scan per catturare le uscite immediata-
        mente senza aspettare il Full Scan biorario.
        """
        from .config import Config as _Cfg
        cfg_obj = _Cfg()
        indexers = self._get_indexers(cfg_obj)
        if not indexers:
            return []

        all_results = []
        for ix in indexers:
            search_url = self._torznab_url(ix['url'])
            params = {'apikey': ix['api'], 't': 'search', 'limit': 100}
            
            # --- LETTURA TIMEOUT DINAMICO ---
            try:
                j_timeout = int(getattr(cfg_obj, 'jackett_timeout', 30))
            except (ValueError, TypeError):
                j_timeout = 30
            # --------------------------------
            
            try:
                res = self.sess.get(search_url, params=params, timeout=j_timeout)
                if res.status_code == 200:
                    root = ET.fromstring(res.content)
                    for item in root.findall('./channel/item'):
                        title_el = item.find('title')
                        link_el  = item.find('link')
                        if title_el is None or link_el is None:
                            continue
                        t = title_el.text.strip()
                        l = link_el.text.strip()
                        # Prova a leggere il nome tracker dall'elemento specifico
                        for tag_name in ('jackettindexer', 'prowlarrindexer'):
                            ix_el = item.find(tag_name)
                            if ix_el is not None and ix_el.text:
                                tracker = ix_el.text.strip()
                                break
                        else:
                            tracker = ix['label']
                        tag      = f"{ix['label']} RSS - {tracker}"
                        t_disp   = f"{t} [{tag}]"
                        mg       = sanitize_magnet(l, t_disp) or l
                        if mg.startswith('magnet:?'):
                            if 'dn=' in mg:
                                mg = re.sub(r'dn=[^&]+', f"dn={quote(t_disp)}", mg)
                            else:
                                mg += f"&dn={quote(t_disp)}"
                        
                        # --- SCUDO ANTI-NoneType: Protezione per Errore RSS Scan ---
                        size_el = item.find('size')
                        if size_el is not None and size_el.text:
                            try:
                                # Converte in intero o 0 se vuoto, evitando il crash '>' not supported
                                val = int(size_el.text) if size_el.text else 0
                                if 0 < val < 50_000_000:
                                    continue # Salta i file minuscoli (NFO/Sample)
                            except (ValueError, TypeError):
                                pass
                        # -----------------------------------------------------------
                        
                        all_results.append({'title': t_disp, 'magnet': mg, 'source': tag})
                else:
                    logger.warning(f"⚠️ {ix['label']} RSS: HTTP {res.status_code}")
            except Exception as e:
                logger.warning(f"⚠️ {ix['label']} RSS error: {e}")

        return all_results

    def _jackett_search(self, query: str, config: dict, season: int = None, ep: int = None,
                        tvdb_id: int = None) -> list:
        """Ricerca Torznab su TUTTI gli indexer configurati (Jackett e/o Prowlarr).
        
        Il parametro `config` è mantenuto per compatibilità ma viene ignorato:
        la lista degli indexer viene sempre letta dalla configurazione corrente.
        Se tvdb_id è fornito viene passato come parametro per ricerche più precise.
        """
        from .config import Config as _Cfg
        cfg_obj = _Cfg()
        indexers = self._get_indexers(cfg_obj)
        if not indexers:
            return []

        all_results = []
        for ix in indexers:
            results = self._torznab_single(ix, query, season=season, ep=ep, tvdb_id=tvdb_id)
            all_results.extend(results)
        # Dedup per Hash reale (ignora i tracker aggiuntivi che sporcano la stringa)
        seen = {}
        for r in all_results:
            hash_val = _extract_btih(r['magnet'])
            key = hash_val.lower() if hash_val else r['magnet']
            if key not in seen:
                seen[key] = r
        return list(seen.values())

    def _torznab_single(self, indexer: dict, query: str, season: int = None,
                        ep: int = None, tvdb_id: int = None) -> list:
        """Esegue una singola query Torznab su un indexer specifico."""
        results    = []
        search_url = self._torznab_url(indexer['url'])
        label      = indexer['label']

        params = {
            'apikey': indexer['api'],
            't': 'tvsearch' if season is not None else 'search',
            'q': query
        }
        if season  is not None: params['season'] = season
        if ep      is not None: params['ep']     = ep
        if tvdb_id is not None and season is not None:
            params['tvdbid'] = tvdb_id
            logger.debug(f"   ↳ [{label}] tvdbid={tvdb_id} included in query")

        try:
            # --- LETTURA TIMEOUT DINAMICO ---
            from .config import Config as _Cfg
            try:
                j_timeout = int(getattr(_Cfg(), 'jackett_timeout', 30))
            except (ValueError, TypeError):
                j_timeout = 30
            # --------------------------------
            
            logger.debug(f"   ↳ [{label}] Searching: '{query}'")
            res = self.sess.get(search_url, params=params, timeout=j_timeout)

            if res.status_code != 200:
                logger.warning(f"⚠️ Error {label}: HTTP {res.status_code}")
                return results

            root = ET.fromstring(res.content)
            for item in root.findall('./channel/item'):
                title_el = item.find('title')
                link_el  = item.find('link')
                size_el  = item.find('size')
                if title_el is None or link_el is None:
                    continue
                t = title_el.text.strip()
                l = link_el.text.strip()

                # Nome tracker dall'elemento specifico del provider
                tracker = label
                for tag_name in ('jackettindexer', 'prowlarrindexer'):
                    ix_el = item.find(tag_name)
                    if ix_el is not None and ix_el.text:
                        tracker = f"{label} - {ix_el.text.strip()}"
                        break

                t_disp = f"{t} [{tracker}]"
                mg     = sanitize_magnet(l, t_disp) or l
                if mg.startswith('magnet:?'):
                    if 'dn=' in mg:
                        mg = re.sub(r'dn=[^&]+', f"dn={quote(t_disp)}", mg)
                    else:
                        mg += f"&dn={quote(t_disp)}"

                # Scarta file troppo piccoli (probabilmente sample o NFO)
                if size_el is not None and size_el.text:
                    try:
                        # Assicura che il valore sia un intero prima di fare il confronto (Scudo NoneType)
                        val = int(size_el.text) if size_el.text else 0
                        if 0 < val < 50_000_000:
                            continue
                    except ValueError:
                        pass

                results.append({'title': t_disp, 'magnet': mg, 'source': tracker})

        except Exception as e:
            logger.warning(f"⚠️ Error during call to {label}: {e}")
            stats.add_error("Jackett timeout" if "timeout" in str(e).lower() else "indexer")

        mode = params.get('t', 'search')
        season_info = f" S{params['season']:02d}" if 'season' in params else ""
        logger.info(f"      ✓ {label} [{mode}{season_info}]: {len(results)} results for '{query}'")
        return results

    # ------------------------------------------------------------------
    # RSS GENERICO (knaben, eztv, nyaa, o qualsiasi feed RSS standard)
    # ------------------------------------------------------------------

    @staticmethod
    def _rss_source_label(url: str) -> str:
        """Ricava un'etichetta leggibile dal dominio dell'URL."""
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or url
            # knaben.org → Knaben, rss.knaben.org → Knaben
            parts = host.split('.')
            # Prende il penultimo elemento (dominio senza TLD e sottodomini)
            label = parts[-2] if len(parts) >= 2 else parts[0]
            return label.capitalize()
        except Exception:
            return 'RSS'  # URL malformato, label generica

    def _generic_rss(self, url: str):
        """
        Scarica e parsa un feed RSS generico di torrent.

        Struttura attesa (compatibile con knaben, eztv, nyaa, showrss…):
          <item>
            <title>Nome.Serie.S01E01.1080p…</title>
            <link>magnet:?xt=…  oppure  https://…/file.torrent</link>
            <description>… Size: 4.5 GB …</description>   (opzionale)
            <pubDate>…</pubDate>                           (opzionale)
          </item>

        Strategia di estrazione magnet (in ordine di priorità):
          1. Tag <link> se contiene 'magnet:'
          2. Prima occorrenza di magnet: nella <description> (CDATA)
          3. Attributo href/url in <enclosure> o <torrent:magnetURI>
          4. Skip item se nessun magnet trovato

        Nota: knaben mette il magnet dentro CDATA in <link>; il parser
        xml.etree lo legge correttamente come testo del nodo.
        """
        import re as _re
        import xml.etree.ElementTree as _ET
        import requests as _req
        import warnings
        import urllib3

        source_label = self._rss_source_label(url)
        items = []

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                resp = _req.get(
                    url, timeout=20, verify=False,
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; EXTTO/1.0)'}
                )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"❌ Generic RSS: unable to download '{url}': {e}")
            return items

        try:
            root = _ET.fromstring(resp.content)
        except _ET.ParseError as e:
            logger.error(f"❌ Generic RSS: invalid XML from '{url}': {e}")
            return items

        # Namespace RSS 2.0 non ha namespace, ma alcuni feed usano atom/torrent
        _MAGNET_RE = _re.compile(r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40,}[^\s"<]*', _re.I)
        _SIZE_RE   = _re.compile(r'Size:\s*([\d.]+\s*[KMGT]i?B)', _re.I)

        channel = root.find('channel')
        if channel is None:
            # Atom feed o struttura non standard: prova root direttamente
            channel = root

        for item in channel.findall('item'):
            title_el = item.find('title')
            title = (title_el.text or '').strip() if title_el is not None else ''
            if not title:
                continue

            # --- Estrai magnet ---
            magnet = None

            # 1. <link>
            link_el = item.find('link')
            link_text = (link_el.text or '').strip() if link_el is not None else ''
            if 'magnet:' in link_text:
                m = _MAGNET_RE.search(link_text)
                if m:
                    magnet = m.group(0)

            # 2. <description> CDATA
            if not magnet:
                desc_el = item.find('description')
                desc_text = (desc_el.text or '') if desc_el is not None else ''
                m = _MAGNET_RE.search(desc_text)
                if m:
                    magnet = m.group(0)

            # 3. <enclosure> o namespace torrent:magnetURI
            if not magnet:
                enc = item.find('enclosure')
                if enc is not None:
                    for attr in ('url', 'href'):
                        v = enc.get(attr, '')
                        if 'magnet:' in v:
                            magnet = v
                            break
                # Namespace torrent (es. showrss)
                if not magnet:
                    for child in item:
                        if 'magnetURI' in child.tag or 'magnet' in child.tag.lower():
                            magnet = (child.text or '').strip() or None
                            if magnet:
                                break

            if not magnet:
                logger.debug(f"   Generic RSS: no magnet for '{title[:60]}'")
                continue

            # Sanitizza e valida il magnet
            from .constants import sanitize_magnet
            magnet = sanitize_magnet(magnet, title)
            if not magnet:
                continue

            # --- Estrai size (opzionale) ---
            size_str = ''
            desc_el = item.find('description')
            if desc_el is not None and desc_el.text:
                sm = _SIZE_RE.search(desc_el.text)
                if sm:
                    size_str = sm.group(1)

            # --- Estrai data (opzionale) ---
            pub_el = item.find('pubDate')
            pub_text = (pub_el.text or '').strip() if pub_el is not None else ''

            items.append({
                'title':   title,
                'magnet':  magnet,
                'size':    size_str,
                'date':    pub_text,
                'source':  source_label,
                'uploader': source_label,
            })

        logger.debug(f"   RSS {source_label}: {len(items)} items extracted from {url[:60]}")
        return items

    def _corsaro(self, url, max_pages: int = None):
        # --- ESTRAE L'UTENTE DALL'URL ---
        tag = "Corsaro"
        m_user = re.search(r'/user/([^/?]+)', url)
        if m_user:
            tag = f"Corsaro - {unquote(m_user.group(1))}"

        base       = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        _mp        = max_pages if max_pages is not None else MAX_PAGES
        scan_pages = min(_mp + 2, 5)
        for page in range(scan_pages):
            try:
                p_url = f"{url}{'&' if '?' in url else '?'}page={page}" if page > 0 else url
                res   = self.sess.get(p_url, timeout=10)
                if res.status_code != 200:
                    break
                soup  = BeautifulSoup(res.content, 'html.parser')
                links = soup.find_all('a', href=re.compile(r'/torrents?/\d+'))
                if not links:
                    break
                cutoff = None
                if isinstance(self.age_filter, dict) and (self.age_filter.get('days', 0) or 0) > 0:
                    cutoff = datetime.now() - timedelta(days=int(self.age_filter.get('days', 0)))
                items_on_page = 0
                old_on_page   = 0

                for a in links:
                    items_on_page += 1
                    t = a.text.strip()
                    
                    # --- CREA IL TITOLO CON SORGENTE/UTENTE ---
                    t_display = f"{t} [{tag}]"
                    
                    if self.cache.get(t):
                        mg = sanitize_magnet(self.cache.get(t), t_display) or self.cache.get(t)
                        if mg.startswith('magnet:?'):
                            if 'dn=' in mg:
                                mg = re.sub(r'dn=[^&]+', f"dn={quote(t_display)}", mg)
                            else:
                                mg += f"&dn={quote(t_display)}"
                        yield {'title': t_display, 'magnet': mg, 'source': tag}
                        continue
                        
                    try:
                        sub      = self.sess.get(urljoin(base, a['href']), timeout=5)
                        sub_soup = BeautifulSoup(sub.content, 'html.parser')
                        if cutoff is not None:
                            dt = _extract_date_from_element(sub_soup)
                            if dt and dt < cutoff:
                                old_on_page += 1
                                time.sleep(0.2)
                                continue
                        sz = sub_soup.find(string=re.compile(r'Dimensione|Size'))
                        if sz and not self._sanity_check(sz.find_parent().get_text()):
                            stats.size_rejected.append(t)
                            try:
                                ep_try = Parser.parse_series_episode(t)
                                # Usa un'unica istanza DB condivisa per evitare
                                # connection leak in loop di scraping
                                _db = Database()
                                if ep_try:
                                    c2 = _db.conn.cursor()
                                    c2.execute("SELECT id FROM series WHERE name LIKE ?",
                                               (f"%{ep_try['name']}%",))
                                    r2 = c2.fetchone()
                                    if r2:
                                        _db.record_episode_discard(r2['id'], ep_try['season'],
                                                                   ep_try['episode'], 'size_too_small')
                                else:
                                    mov_try = Parser.parse_movie(t)
                                    if mov_try and 'name' in mov_try and 'year' in mov_try:
                                        nm = mov_try.get('config_name') or mov_try['name']
                                        _db.record_movie_discard(nm, mov_try['year'], 'size_too_small')
                                _db.conn.close()
                            except Exception as _e:
                                logger.debug(f"_corsaro size-discard DB error: {_e}")
                            continue
                            
                        m = sub_soup.find('a', href=re.compile(r'^magnet:\?'))
                        if m:
                            mg = sanitize_magnet(m['href'], t_display) or m['href']
                            if mg.startswith('magnet:?'):
                                if 'dn=' in mg:
                                    mg = re.sub(r'dn=[^&]+', f"dn={quote(t_display)}", mg)
                                else:
                                    mg += f"&dn={quote(t_display)}"
                            self.cache.set(t, mg)
                            yield {'title': t_display, 'magnet': mg, 'source': tag}
                        else:
                            txt = sub_soup.get_text(" ")
                            mh  = re.search(r'\b([a-fA-F0-9]{40}|[A-Z2-7]{32})\b', txt)
                            if mh:
                                info_hash = mh.group(1)
                                built     = f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={quote(t_display)}"
                                mg        = sanitize_magnet(built, t_display) or built
                                self.cache.set(t, mg)
                                yield {'title': t_display, 'magnet': mg, 'source': tag}
                        time.sleep(1)
                    except Exception as _torrent_err:
                        logger.debug(f"_corsaro error on torrent '{t[:60]}': {_torrent_err}")
                # FIX-H: ratio check fuori dal loop su singolo torrent, dentro il loop su link
                ratio = old_on_page / max(1, items_on_page)
                if cutoff is not None and ratio >= float(self.age_filter.get('threshold', 0.8)):
                    logger.info(f"⏳ Corsaro early-stop: page {page} old (>{self.age_filter.get('days')}d) {old_on_page}/{items_on_page}")
                    break
            except Exception as _page_err:
                logger.debug(f"_corsaro page {page} error: {_page_err}")
                break

    # ------------------------------------------------------------------
    # TORRENTGALAXY — scraping pagina uploader /get-posts/user:USERNAME/
    # Funziona con tutti i domini/mirror: torrentgalaxy.one/.to/.info/.space/ecc.
    # Il riconoscimento avviene tramite 'get-posts/user:' nel path dell'URL,
    # non sul dominio — così è mirror-agnostic.
    # ------------------------------------------------------------------

    def _tgx_user(self, url: str):
        """
        Scarica e parsa la pagina uploader di TorrentGalaxy (qualsiasi mirror).

        Struttura HTML reale (verificata su torrentgalaxy.one):
          div.tgxtablerow
            └─ div.tgxtablecell > a.txlight[href="/post-detail/HASH/nome/"]
                 → titolo del torrent (testo del <b> interno)
            └─ NON c'è il magnet diretto nella listing
               → bisogna visitare /post-detail/HASH/nome/ per trovarlo

        Nella pagina di dettaglio il magnet è in un <a href="magnet:?...">
        con HTML entities (&amp; ecc.) che BeautifulSoup decodifica automaticamente.

        La SmartCache evita di rivisitare le pagine di dettaglio già note.
        """
        import re as _re
        from urllib.parse import urljoin as _urljoin, urlparse as _urlparse, quote as _quote, unquote as _unquote
        from html import unescape as _unescape

        # Etichetta: "TGx - MIRCrewRS" estratta dall'URL
        m_user = _re.search(r'/get-posts/user:([^/?]+)', url)
        username = _unquote(m_user.group(1)) if m_user else 'TGx'
        tag = f"TGx - {username}"

        parsed = _urlparse(url)
        base   = f"{parsed.scheme}://{parsed.netloc}"

        _HEADERS = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        items = []

        # --- Scarica la pagina listing ---
        try:
            resp = self.sess.get(url, timeout=15, headers=_HEADERS)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"❌ TGx listing error '{url}': {e}")
            return items

        soup = BeautifulSoup(resp.content, 'html.parser')
        rows = soup.select('div.tgxtablerow')

        if not rows:
            logger.warning(f"⚠️ TGx: nessuna riga trovata in {url[:60]} — struttura HTML cambiata?")
            return items

        logger.debug(f"   TGx {tag}: {len(rows)} righe trovate in listing")

        for row in rows:
            # --- Titolo: <a class="txlight" href="/post-detail/..."> ---
            title_tag = row.select_one('a.txlight[href*="post-detail"]')
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title:
                continue

            torrent_path = title_tag.get('href', '')
            torrent_url  = _urljoin(base, torrent_path) if torrent_path else ''
            t_display    = f"{title} [{tag}]"

            # --- Cache: evita visite duplicate alla pagina di dettaglio ---
            cached_mg = self.cache.get(title)
            if cached_mg:
                magnet = cached_mg
            else:
                if not torrent_url:
                    logger.debug(f"   TGx: nessun URL dettaglio per '{title[:60]}'")
                    continue
                try:
                    time.sleep(1)  # Cortesia verso il server
                    sub = self.sess.get(torrent_url, timeout=10, headers=_HEADERS)
                    sub.raise_for_status()
                    sub_soup = BeautifulSoup(sub.content, 'html.parser')
                    # BeautifulSoup decodifica automaticamente &amp; → & nelle href
                    mag_tag = sub_soup.find('a', href=_re.compile(r'^magnet:\?'))
                    if not mag_tag:
                        logger.debug(f"   TGx: no magnet in '{torrent_url}'")
                        continue
                    magnet = mag_tag['href']  # già decodificato da BS4
                    self.cache.set(title, magnet)
                except Exception as e:
                    logger.debug(f"   TGx detail error '{torrent_url}': {e}")
                    continue

            # --- Sanitizza e aggiorna dn= nel magnet ---
            magnet = sanitize_magnet(magnet, t_display) or magnet
            if magnet.startswith('magnet:?'):
                if 'dn=' in magnet:
                    magnet = re.sub(r'dn=[^&]+', f"dn={quote(t_display)}", magnet)
                else:
                    magnet += f"&dn={quote(t_display)}"

            items.append({'title': t_display, 'magnet': magnet, 'source': tag})

        logger.info(f"   TGx {tag}: {len(items)}/{len(rows)} items con magnet estratti")
        self.cache.save()
        return items

    def generate_xml(self, items, base_url: str = None):
        rss  = ET.Element('rss', version='2.0')
        chan = ET.SubElement(rss, 'channel')
        ET.SubElement(chan, 'title').text       = 'EXTTO Magnet Feed'
        ET.SubElement(chan, 'description').text = 'Feed automatico di magnet link da EXTTO'
        # Usa base_url se passato, altrimenti costruisce da PORT per evitare l'hardcoding
        _base = base_url or f'http://localhost:{PORT}'
        ET.SubElement(chan, 'link').text        = _base
        ET.SubElement(chan, 'lastBuildDate').text = \
            datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')

        for i in items:
            item = ET.SubElement(chan, 'item')
            ET.SubElement(item, 'title').text = i.get('title', 'Unknown')
            ET.SubElement(item, 'link').text  = i.get('magnet', '')
            desc = i.get('title', '')
            if i.get('source'):
                desc += f" [{i.get('source')}]"
            ET.SubElement(item, 'description').text = desc
            if i.get('added_at'):
                try:
                    dt       = datetime.fromisoformat(i['added_at'].replace('Z', '+00:00'))
                    pub_date = dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
                    ET.SubElement(item, 'pubDate').text = pub_date
                except Exception as e:
                    logger.debug(f"pubDate format: {e}")

        tree = ET.ElementTree(rss)
        ET.indent(tree, space='  ')
        with open(XML_FILE, 'wb') as f:
            tree.write(f, encoding='utf-8', xml_declaration=True)


# ---------------------------------------------------------------------------
# RESCORE ARCHIVE
# ---------------------------------------------------------------------------

def rescore_archive(cfg, eng: Engine, db: Database) -> dict:
    """Rianalizza l'archivio applicando i criteri correnti senza scaricare nulla."""
    updated = {'series': 0, 'movies': 0}
    for s in cfg.series:
        seen_magnets = set()
        results = []
        names = [s['name']] + [a for a in s.get('aliases', []) if a and a.lower() != s['name'].lower()]
        for name in names:
            for item in eng.archive.search(name):
                if item['magnet'] not in seen_magnets:
                    seen_magnets.add(item['magnet'])
                    results.append(item)
        lang_req = s.get('language', s.get('lang', 'ita'))
        for item in results:
            if not cfg._lang_ok(item['title'], lang_req):
                continue
            ep = Parser.parse_series_episode(item['title'])
            if not ep:
                continue
            match = cfg.find_series_match(ep['name'], ep['season'])
            if not match:
                continue
            safe_magnet = sanitize_magnet(item['magnet'], item['title']) or item['magnet']
            try:
                ep['archive_path'] = match.get('archive_path', '')
            except Exception as e:
                logger.debug(f"archive_path assign: {e}")
            dl, msg = db.check_series(ep, safe_magnet, match.get('qual', ''))
            if dl:
                updated['series'] += 1
    for m in cfg.movies:
        results = eng.archive.search(m['name'])
        lang_req = m.get('language', m.get('lang', 'ita'))
        for item in results:
            if not cfg._lang_ok(item['title'], lang_req):
                continue
            mov = Parser.parse_movie(item['title'])
            if not mov:
                continue
            mov['config_name'] = m['name']
            safe_magnet = sanitize_magnet(item['magnet'], item['title']) or item['magnet']
            dl, msg = db.check_movie(mov, safe_magnet, m.get('qual', ''))
            if dl:
                updated['movies'] += 1
    return updated
