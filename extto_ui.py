#!/usr/bin/env python3
"""
EXTTO TUI v3
Uso: .venv/bin/python extto_ui.py [--host 127.0.0.1] [--port 5000]
Richiede: pip install textual requests
"""

import argparse
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("Manca 'requests': pip install requests")
    sys.exit(1)

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
    from textual.widgets import (
        Header, Footer, DataTable, Label, Button, Input,
        TabbedContent, TabPane, Static,
    )
    from textual.screen import ModalScreen
    from textual import work, on
    from textual.css.query import NoMatches
except ImportError:
    print("Manca 'textual': pip install textual")
    sys.exit(1)


# ─── Utility ─────────────────────────────────────────────────────────────────

QUALITY_MAP = {
    5000: "2160p HDR", 4000: "2160p", 3000: "1080p",
    2000: "720p", 1000: "DVDRip", 500: "CAM", 0: "?",
}

TORRENT_STATES = {
    "seeding": "SEED", "downloading": "DL", "paused": "PAUSA",
    "checking": "CHECK", "error": "ERR", "queued": "CODA",
    "finished": "FINE", "stalledDL": "STALL", "finished_t": "FINE_T",
    "allocating": "ALLOC", "downloading_metadata": "META",
}


def qual(score):
    if score is None:
        return "?"
    for t in sorted(QUALITY_MAP, reverse=True):
        if score >= t:
            return QUALITY_MAP[t]
    return "?"


def fmts(s):
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return s[:16] if len(s) >= 16 else s


def sz(b):
    if not b:
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f}{u}"
        b /= 1024
    return f"{b:.1f}PB"


def spd(bps):
    return "—" if not bps else sz(bps) + "/s"


def next_run(last_ts, interval_s):
    if not last_ts:
        return "?"
    try:
        dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = int(dt.timestamp() + interval_s - time.time())
        if diff <= 0:
            return "imminente"
        h, r = divmod(diff, 3600)
        return f"{h}h{r // 60:02d}m"
    except Exception:
        return "?"


def pbar(pct):
    pct = max(0, min(100, int(pct if pct > 1 else pct * 100)))
    n = pct // 5
    return f"[{'#' * n}{'.' * (20 - n)}] {pct:3d}%"


# ─── API ─────────────────────────────────────────────────────────────────────

class API:
    def __init__(self, host, port):
        self.base = f"http://{host}:{port}"
        self.url = self.base  # alias per compatibilità
        self.s = requests.Session()

    def get(self, path, timeout=10, **params):
        r = self.s.get(self.base + path, timeout=timeout, params=params or None)
        r.raise_for_status()
        return r.json()

    def delete(self, path, timeout=10):
        r = self.s.delete(self.base + path, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def post(self, path, json_data=None, timeout=15):
        r = self.s.post(self.base + path, json=json_data, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def fetch_url(self, url):
        return self.post("/api/fetch-url", {"url": url}, timeout=20)

    def upload_torrent(self, filename, data_b64):
        return self.post("/api/upload-torrent", {"filename": filename, "data": data_b64, "download_now": True}, timeout=15)

    def stats(self):                 return self.get("/api/stats")
    def syscfg(self):                return self.get("/api/system/stats")
    def last_cycle(self):            return self.get("/api/last_cycle")
    def recent(self):                return self.get("/api/recent-downloads")
    def run(self, d="all"):          return self.get("/api/run_now", domain=d)
    def series(self):                return self.get("/api/series")
    def series_stats(self):          return self.get("/api/series/stats")
    def completeness(self):          return self.get("/api/series/completeness")
    def episodes(self, sid):         return self.get(f"/api/series/{sid}/episodes")
    def del_series(self, sid, name=None):
        """Elimina serie per ID; fallback per nome se sid è 0 o None."""
        if sid and int(sid) > 0:
            return self.delete(f"/api/series/{sid}")
        elif name:
            import urllib.parse
            return self.delete(f"/api/series/by-name/{urllib.parse.quote(name, safe='')}")
        return {"success": False, "error": "ID e nome mancanti"}
    def rename_series(self, sid, n): return self.post(f"/api/series/{sid}/rename", {"new_name": n})
    def ep_redl(self, eid):          return self.post(f"/api/episodes/{eid}/redownload")
    def ep_ignore(self, eid):        return self.post(f"/api/episodes/{eid}/ignore")
    def ep_missing(self, eid):       return self.post(f"/api/episodes/{eid}/force-missing")
    def search_miss(self, sid, s):   return self.post(f"/api/series/{sid}/search-missing", {"season": s})
    def movies(self):                return self.get("/api/config").get("movies", [])
    def movies_db(self):             return self.get("/api/movies")
    def del_movie(self, mid_index):
        """Elimina un film per indice dalla config."""
        cfg = self.get("/api/config")
        lst = cfg.get("movies", [])
        if 0 <= mid_index < len(lst):
            lst.pop(mid_index)
            return self.post("/api/config/movies", {"movies": lst})
        return {"success": False}

    def del_movie_by_id(self, movie_id):
        """Elimina un film per ID dal DB operativo."""
        return self.delete(f"/api/movies/{movie_id}")
    def torrents(self):              return self.get("/api/torrents")
    def send_magnet(self, m):        return self.post("/api/send-magnet", {"magnet": m})
    def rm_completed(self):          return self.post("/api/torrents/remove_completed")
    def logs(self, n=300):           return self.get("/api/logs", lines=n)
    def set_log_level(self, lv):     return self.post("/api/log", {"level": lv})
    def config(self):                return self.get("/api/config")
    def save_settings(self, s):
        # Converte i limiti da KB/s a Bytes/s per coerenza con il salvataggio backend
        if s:
            for k in ['libtorrent_dl_limit', 'libtorrent_ul_limit', 'libtorrent_sched_dl_limit', 'libtorrent_sched_ul_limit']:
                if k in s and s[k]:
                    try:
                        v = int(s[k])
                        if v < 1000000: s[k] = str(v * 1024)
                    except: pass
        return self.post("/api/config/settings", {"settings": s})
    def archive(self, q="", page=0, limit=100):
        return self.get("/api/archive", q=q, page=page, limit=limit)
    def sources(self):               return self.get("/api/sources/health", timeout=30)
    def health(self):                return self.get("/api/health")
    def set_speed_limits(self, dl_kbps, ul_kbps):
        return self.post("/api/set-speed-limits", {"dl_kbps": dl_kbps, "ul_kbps": ul_kbps})
    def network_interfaces(self):    return self.get("/api/network/interfaces")

    def add_series(self, name, seasons='1+', quality='1080p', language='ita'):
        """Aggiunge una serie via POST diretto a /api/config/series (append-only)."""
        cfg = self.get("/api/config")
        lst = cfg.get("series", [])
        lst.append({'name': name, 'seasons': seasons, 'quality': quality,
                    'language': language, 'enabled': True, 'aliases': [],
                    'ignored_seasons': [], 'tmdb_id': '', 'subtitle': ''})
        return self.post("/api/config/series", {"series": lst})

    def add_movie(self, name):
        cfg = self.get("/api/config")
        lst = cfg.get("movies", [])
        lst.append({'name': name, 'year': '', 'quality': '1080p', 'language': 'ita', 'enabled': True})
        return self.post("/api/config/movies", {"movies": lst})

# ─── CSS ─────────────────────────────────────────────────────────────────────

CSS = """
* { scrollbar-color: #333 #0a0a0a; scrollbar-size: 1 1; }
Screen  { background: #0a0a0a; color: #c0c0c0; }
Header  { background: #001428; color: #00aaff; height: 1; }
Footer  { background: #001428; color: #555;    height: 1; }

#statusbar {
    height: 1;
    background: #001428;
    padding: 0 1;
    layout: horizontal;
}
#statusbar Label { padding: 0 2; color: #888; }
#sb-cycle { color: #00ff88; }

TabbedContent { height: 1fr; }
TabPane       { padding: 0; height: 1fr; }

.toolbar {
    height: auto;
    background: #0f0f0f;
    padding: 0 1;
    border-bottom: solid #1e1e1e;
    layout: horizontal;
    align: left middle;
}
.toolbar Button {
    min-width: 14;
    margin: 0 1;
    background: #141414;
    border: solid #2a2a2a;
    color: #999;
}
.toolbar Button:hover  { background: #222; color: #eee; }
.toolbar Button.ok     { color: #00cc66; border: solid #1a4a2a; }
.toolbar Button.warn   { color: #ffaa00; border: solid #4a3a00; }
.toolbar Button.danger { color: #ff5555; border: solid #4a1515; }
.toolbar Label         { color: #555; padding: 0 1; }

DataTable {
    height: 1fr;
    background: #0a0a0a;
    border: none;
}
DataTable > .datatable--header {
    background: #001428;
    color: #00aaff;
    text-style: bold;
}
DataTable > .datatable--cursor {
    background: #002244;
    color: #ffffff;
    text-style: bold;
}
DataTable > .datatable--even-row { background: #0c0c0c; }
DataTable > .datatable--odd-row  { background: #0a0a0a; }

/* Serie */
#series-outer  { height: 1fr; }
#series-split  { height: 1fr; layout: horizontal; }
#series-left   { width: 1fr; height: 1fr; }
#series-right  {
    width: 44;
    border-left: solid #1e1e1e;
    padding: 1 2;
    background: #0c0c0c;
    overflow-y: auto;
    height: 1fr;
}
#ep-section    { height: 7; border-top: solid #1e1e1e; }
#ep-toolbar {
    height: auto;
    background: #0f0f0f;
    padding: 0 1;
    border-bottom: solid #1e1e1e;
    layout: horizontal;
    align: left middle;
}
#ep-toolbar Button {
    min-width: 16;
    margin: 0 1;
    background: #141414;
    border: solid #2a2a2a;
    color: #999;
}
#ep-toolbar Button:hover  { background: #222; color: #eee; }
#ep-toolbar Button.warn   { color: #ffaa00; }
#ep-toolbar Button.danger { color: #ff5555; }
#ep-label { color: #555; padding: 0 1; }

.det-title { color: #00ff88; text-style: bold; margin-bottom: 1; }
.det-body  { color: #aaa; }

/* Film */
#movies-split { height: 1fr; layout: horizontal; }
#movies-left  { width: 1fr; height: 1fr; }
#movies-right {
    width: 44;
    border-left: solid #1e1e1e;
    padding: 1 2;
    background: #0c0c0c;
    overflow-y: auto;
    height: 1fr;
}

/* Log */
#log-scroll {
    height: 1fr;
    background: #050505;
    padding: 0 1;
    overflow-y: auto;
}
#log-text { color: #777; }

/* Archivio */
#arch-toolbar {
    height: auto;
    background: #0f0f0f;
    padding: 0 1;
    border-bottom: solid #1e1e1e;
    layout: horizontal;
    align: left middle;
}
#arch-toolbar Label { color: #555; padding: 0 1; }
#arch-toolbar Input {
    width: 38;
    background: #0f0f0f;
    border: solid #2a2a2a;
    color: #ccc;
}
#arch-toolbar Button {
    margin: 0 1;
    background: #141414;
    border: solid #2a2a2a;
    color: #999;
}
#arch-count { color: #444; padding: 0 2; }

/* Dashboard */
#dash-section { height: 1fr; layout: horizontal; }
#dash-left {
    width: 38;
    padding: 1 2;
    border-right: solid #1e1e1e;
    overflow-y: auto;
}
#dash-right { width: 1fr; padding: 1 1; }
.dash-hdr { color: #00aaff; text-style: bold; margin-top: 1; }
.dash-val { color: #00ff88; }

/* Toast */
#toast {
    dock: bottom;
    height: 1;
    background: #001a00;
    color: #00ff88;
    padding: 0 2;
    display: none;
}
#toast.show  { display: block; }
#toast.error { background: #1a0000; color: #ff5555; }

/* Modal */
ModalScreen { align: center middle; }
#mdl {
    background: #0d0d18;
    border: solid #223;
    padding: 2 4;
    width: 64;
    height: auto;
    max-height: 28;
}
.mtitle {
    color: #00aaff;
    text-style: bold;
    text-align: center;
    margin-bottom: 2;
}
#mdl Label  { color: #aaa; margin-bottom: 1; }
#mdl Input  {
    width: 100%;
    background: #0a0a0a;
    border: solid #223;
    color: #ccc;
    margin-bottom: 1;
}
#mdl-btns { height: 3; align: center middle; margin-top: 1; }
#mdl-btns Button { margin: 0 2; min-width: 12; }

#mdl-large {
    background: #0d0d18;
    border: solid #223;
    padding: 1 2;
    width: 80;
    height: 40;
}
.msection { color: #00ff88; text-style: bold; margin: 1 0; border-bottom: solid #1e1e1e; }
.mlabel { width: 30; color: #aaa; }
.minp { width: 15; }
.minp-short { width: 10; }
.score-row { height: 3; align: left middle; }
"""


# ─── Modals ───────────────────────────────────────────────────────────────────

class AskModal(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Annulla")]

    def __init__(self, title, label, default="", **kw):
        super().__init__(**kw)
        self._t, self._l, self._d = title, label, default

    def compose(self) -> ComposeResult:
        with Container(id="mdl"):
            yield Label("Invia Magnet o URL", classes="mtitle")
            yield Label("Link (.torrent o magnet):")
            yield Input(placeholder="magnet:?xt=... oppure http://...", id="mdl-inp")
            with Horizontal(id="mdl-btns"):
                yield Button("Conferma", id="mdl-ok",  variant="success")
                yield Button("Annulla",  id="mdl-can", variant="error")

    def on_mount(self):
        self.query_one("#mdl-inp").focus()

    @on(Button.Pressed, "#mdl-ok")
    def do_ok(self):
        v = self.query_one("#mdl-inp", Input).value.strip()
        self.dismiss(v or None)

    @on(Button.Pressed, "#mdl-can")
    def do_can(self):
        self.dismiss(None)

    @on(Input.Submitted, "#mdl-inp")
    def submitted(self):
        self.do_ok()


class ConfirmModal(ModalScreen):
    BINDINGS = [("escape", "dismiss(False)", "No")]

    def __init__(self, msg, **kw):
        super().__init__(**kw)
        self._msg = msg

    def compose(self) -> ComposeResult:
        with Container(id="mdl"):
            yield Label("Conferma", classes="mtitle")
            yield Label(self._msg)
            with Horizontal(id="mdl-btns"):
                yield Button("Si", id="mdl-yes", variant="error")
                yield Button("No", id="mdl-no",  variant="primary")

    @on(Button.Pressed, "#mdl-yes")
    def yes(self):
        self.dismiss(True)

    @on(Button.Pressed, "#mdl-no")
    def no(self):
        self.dismiss(False)


class CleanupActionModal(ModalScreen):
    """Sceglie tra MOVE (trash) e DELETE per i duplicati."""
    def compose(self) -> ComposeResult:
        with Container(id="mdl"):
            yield Label("Azione Pulizia Duplicati", classes="mtitle")
            yield Label("SPOSTA: Mette i file obsoleti nel cestino (@trash_path)")
            yield Label("ELIMINA: Cancella fisicamente il file (IRREVERSIBILE)")
            with Horizontal(id="mdl-btns"):
                yield Button("SPOSTA (trash)", id="b-move", variant="success")
                yield Button("ELIMINA (fisico)", id="b-delete", variant="error")
                yield Button("Annulla", id="b-cancel", variant="primary")

    @on(Button.Pressed, "#b-move")
    def on_move(self): self.dismiss("move")

    @on(Button.Pressed, "#b-delete")
    def on_delete(self): self.dismiss("delete")

    @on(Button.Pressed, "#b-cancel")
    def on_cancel(self): self.dismiss(None)


class ScoreModal(ModalScreen):
    """Permette di modificare i punteggi base e bonus in modo additivo."""
    def __init__(self, scores, **kw):
        super().__init__(**kw)
        self.scores = scores

    def compose(self) -> ComposeResult:
        with Container(id="mdl-large"):
            yield Label("Calcolatore e Punteggi (Scoring)", classes="mtitle")
            with ScrollableContainer(id="mdl-scroll"):
                yield Label("1. Moltiplicatore e Bonus", classes="msection")
                with Horizontal(classes="score-row"):
                    yield Label("Mult. Risoluzione:", classes="mlabel")
                    yield Input(str(self.scores.get('res_mult', 10000)), id="s-res-mult", classes="minp")
                with Horizontal(classes="score-row"):
                    yield Label("Bonus ITA:", classes="mlabel")
                    yield Input(str(self.scores.get('bonus_ita', 500)), id="s-bonus-ita", classes="minp")
                with Horizontal(classes="score-row"):
                    yield Label("Bonus Dolby Vision:", classes="mlabel")
                    yield Input(str(self.scores.get('bonus_dv', 300)), id="s-bonus-dv", classes="minp")
                with Horizontal(classes="score-row"):
                    yield Label("Bonus Real:", classes="mlabel")
                    yield Input(str(self.scores.get('bonus_real', 100)), id="s-bonus-real", classes="minp")
                with Horizontal(classes="score-row"):
                    yield Label("Bonus Proper:", classes="mlabel")
                    yield Input(str(self.scores.get('bonus_proper', 75)), id="s-bonus-proper", classes="minp")
                with Horizontal(classes="score-row"):
                    yield Label("Bonus Repack:", classes="mlabel")
                    yield Input(str(self.scores.get('bonus_repack', 50)), id="s-bonus-repack", classes="minp")

                # Aggiungiamo le mappe se presenti per rendere tutto modificabile
                if 'res_map' in self.scores:
                    yield Label("2. Rank Risoluzioni", classes="msection")
                    for k, v in sorted(self.scores['res_map'].items(), key=lambda x: x[1]):
                        with Horizontal(classes="score-row"):
                            yield Label(f"{k}:", classes="mlabel")
                            yield Input(str(v), id=f"sm-res-{k}", classes="minp-short")

                if 'source_pref' in self.scores:
                    yield Label("3. Bonus Sorgenti", classes="msection")
                    for k, v in sorted(self.scores['source_pref'].items()):
                        with Horizontal(classes="score-row"):
                            yield Label(f"{k}:", classes="mlabel")
                            yield Input(str(v), id=f"sm-src-{k}", classes="minp-short")

                if 'codec_pref' in self.scores:
                    yield Label("4. Bonus Codec", classes="msection")
                    for k, v in sorted(self.scores['codec_pref'].items()):
                        with Horizontal(classes="score-row"):
                            yield Label(f"{k}:", classes="mlabel")
                            yield Input(str(v), id=f"sm-cod-{k}", classes="minp-short")

                if 'audio_pref' in self.scores:
                    yield Label("5. Bonus Audio", classes="msection")
                    for k, v in sorted(self.scores['audio_pref'].items()):
                        with Horizontal(classes="score-row"):
                            yield Label(f"{k}:", classes="mlabel")
                            yield Input(str(v), id=f"sm-aud-{k}", classes="minp-short")

                if 'group_pref' in self.scores:
                    yield Label("6. Release Groups", classes="msection")
                    for k, v in sorted(self.scores['group_pref'].items()):
                        with Horizontal(classes="score-row"):
                            yield Label(f"{k}:", classes="mlabel")
                            yield Input(str(v), id=f"sm-grp-{k}", classes="minp-short")

                yield Label("Opzioni Pulizia", classes="msection")
                with Horizontal(classes="score-row"):
                    yield Label("Pulizia Auto Completi:", classes="mlabel")
                    yield Button("ATTIVA" if self.scores.get('auto_remove_completed') else "DISATTIVA",
                                 id="b-auto-clean", variant="success" if self.scores.get('auto_remove_completed') else "error")

            with Horizontal(id="mdl-btns"):
                yield Button("Salva",   id="mdl-ok",  variant="success")
                yield Button("Annulla", id="mdl-can", variant="error")

    @on(Button.Pressed, "#b-auto-clean")
    def toggle_clean(self, event):
        self.scores['auto_remove_completed'] = not self.scores.get('auto_remove_completed')
        event.button.label = "ATTIVA" if self.scores['auto_remove_completed'] else "DISATTIVA"
        event.button.variant = "success" if self.scores['auto_remove_completed'] else "error"

    @on(Button.Pressed, "#mdl-ok")
    def do_ok(self):
        try:
            self.scores['res_mult']    = int(self.query_one("#s-res-mult").value)
            self.scores['bonus_ita']   = int(self.query_one("#s-bonus-ita").value)
            self.scores['bonus_dv']    = int(self.query_one("#s-bonus-dv").value)
            self.scores['bonus_real']  = int(self.query_one("#s-bonus-real").value)
            self.scores['bonus_proper']= int(self.query_one("#s-bonus-proper").value)
            self.scores['bonus_repack']= int(self.query_one("#s-bonus-repack").value)

            # Update maps from inputs
            for inp in self.query(Input):
                if inp.id.startswith("sm-res-"): self.scores['res_map'][inp.id[7:]] = int(inp.value)
                if inp.id.startswith("sm-src-"): self.scores['source_pref'][inp.id[7:]] = int(inp.value)
                if inp.id.startswith("sm-cod-"): self.scores['codec_pref'][inp.id[7:]] = int(inp.value)
                if inp.id.startswith("sm-aud-"): self.scores['audio_pref'][inp.id[7:]] = int(inp.value)
                if inp.id.startswith("sm-grp-"): self.scores['group_pref'][inp.id[7:]] = int(inp.value)

            self.dismiss(self.scores)
        except Exception as e:
            self.notify_msg(f"Errore: {e}", error=True)

    @on(Button.Pressed, "#mdl-can")
    def do_can(self):
        self.dismiss(None)


class MagnetModal(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Annulla")]

    def compose(self) -> ComposeResult:
        with Container(id="mdl"):
            yield Label("Invia Magnet o URL", classes="mtitle")
            yield Label("Link (.torrent o magnet):")
            yield Input(placeholder="magnet:?xt=... oppure http://...", id="mdl-inp")
            with Horizontal(id="mdl-btns"):
                yield Button("Invia",   id="mdl-ok",  variant="success")
                yield Button("Annulla", id="mdl-can", variant="error")

    def on_mount(self):
        self.query_one("#mdl-inp").focus()

    @on(Button.Pressed, "#mdl-ok")
    def do_ok(self):
        v = self.query_one("#mdl-inp", Input).value.strip()
        self.dismiss(v or None)

    @on(Button.Pressed, "#mdl-can")
    def do_can(self):
        self.dismiss(None)

    @on(Input.Submitted, "#mdl-inp")
    def submitted(self):
        self.do_ok()


# ─── App ─────────────────────────────────────────────────────────────────────


class SpeedModal(ModalScreen):
    """Imposta limiti di velocità DL/UL al volo."""
    BINDINGS = [("escape", "dismiss(None)", "Annulla")]

    def compose(self) -> ComposeResult:
        with Container(id="mdl"):
            yield Label("Limiti Velocità (KB/s)", classes="mtitle")
            yield Label("0 = nessun limite")
            with Horizontal(classes="toolbar"):
                yield Label("Download KB/s:")
                yield Input(placeholder="0", id="spd-dl", classes="minp")
            with Horizontal(classes="toolbar"):
                yield Label("Upload KB/s:")
                yield Input(placeholder="0", id="spd-ul", classes="minp")
            with Horizontal(id="mdl-btns"):
                yield Button("Applica", id="mdl-ok",  variant="success")
                yield Button("Annulla", id="mdl-can", variant="error")

    def on_mount(self):
        self.query_one("#spd-dl").focus()

    @on(Button.Pressed, "#mdl-ok")
    def do_ok(self):
        try:
            dl = int(self.query_one("#spd-dl", Input).value.strip() or "0")
            ul = int(self.query_one("#spd-ul", Input).value.strip() or "0")
            self.dismiss((dl, ul))
        except ValueError:
            pass

    @on(Button.Pressed, "#mdl-can")
    def do_can(self):
        self.dismiss(None)


class NetworkInterfaceModal(ModalScreen):
    """Seleziona l'interfaccia di rete per libtorrent."""
    BINDINGS = [("escape", "dismiss(None)", "Annulla")]

    def __init__(self, interfaces: dict, current: str = "", **kw):
        super().__init__(**kw)
        self._ifaces   = interfaces
        self._current  = current

    def compose(self) -> ComposeResult:
        with Container(id="mdl"):
            yield Label("Interfaccia di Rete (libtorrent)", classes="mtitle")
            if not self._ifaces:
                yield Label("Nessuna interfaccia rilevata")
            else:
                for name, info in self._ifaces.items():
                    itype = info.get("type", "?")
                    ip    = info.get("ip", "?")
                    label = f"[{itype}] {name}  {ip}"
                    marker = " ◀ attuale" if name == self._current else ""
                    yield Button(label + marker, id=f"iface-{name}",
                                 variant="success" if name == self._current else "default")
            yield Label("— oppure —")
            yield Button("Nessuna (tutte le interfacce)", id="iface-ANY", variant="primary")
            with Horizontal(id="mdl-btns"):
                yield Button("Annulla", id="mdl-can", variant="error")

    @on(Button.Pressed)
    def on_any_button(self, event: Button.Pressed):
        bid = event.button.id
        if bid == "mdl-can":
            self.dismiss(None)
        elif bid == "iface-ANY":
            self.dismiss("")
        elif bid and bid.startswith("iface-"):
            self.dismiss(bid[6:])


class ExttoTUI(App):
    CSS = CSS
    TITLE = "EXTTO v30"
    BINDINGS = [
        Binding("q",  "quit",       "Esci",       priority=True),
        Binding("r",  "run_all",    "Run All"),
        Binding("s",  "run_series", "Run Serie"),
        Binding("m",  "run_movies", "Run Film"),
        Binding("c",  "run_comics", "Run Fumetti"),
        Binding("f5", "refresh",    "Aggiorna"),
    ]

    def __init__(self, api: API, **kw):
        super().__init__(**kw)
        self.api = api
        self._series   = []
        self._movies   = []
        self._movies_db = {}
        self._sel_sid  = None
        self._sel_name = None   # nome serie selezionata (per fallback delete by-name)
        self._sel_eid  = None
        self._sel_mid  = None
        self._log_auto = True

    # ── compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="statusbar"):
            yield Label("Ciclo: —",       id="sb-cycle")
            yield Label("| Prox: —",      id="sb-next")
            yield Label("| CPU:— RAM:—",  id="sb-sys")
            yield Label("| Disco:— Up:—", id="sb-disk")

        # TabPane usa id "tab-*", DataTable usa id "dt-*" — nessun conflitto
        with TabbedContent(initial="tab-dash"):

            with TabPane("Dashboard", id="tab-dash"):
                with Horizontal(id="dash-section"):
                    with Vertical(id="dash-left"):
                        yield Label("[ Ciclo ]",    classes="dash-hdr")
                        yield Static("—", id="d-cycle")
                        yield Label("[ Sistema ]",  classes="dash-hdr")
                        yield Static("—", id="d-sys")
                        yield Label("[ Database ]", classes="dash-hdr")
                        yield Static("—", id="d-db")
                    with Vertical(id="dash-right"):
                        yield Label("[ Ultimi Download ]", classes="dash-hdr")
                        yield DataTable(id="dt-dl", show_cursor=False)

            with TabPane("Serie TV", id="tab-series"):
                with Vertical(id="series-outer"):
                    with Horizontal(classes="toolbar"):
                        yield Button("Run Serie",      id="b-run-s",  classes="ok", tooltip="Forza la ricerca nuovi episodi ORA")
                        yield Button("+ Aggiungi",     id="b-add-s",  classes="ok", tooltip="Aggiungi una nuova serie")
                        yield Button("Cerca Mancanti", id="b-miss-s", classes="warn", tooltip="Cerca stagioni passate")
                        yield Button("Rinomina",       id="b-ren-s",  tooltip="Rinomina la serie selezionata")
                        yield Button("Elimina",        id="b-del-s",  classes="danger", tooltip="Elimina dal database")
                        yield Button("Aggiorna",       id="b-ref-s",  tooltip="Ricarica la lista")
                    with Horizontal(id="series-split"):
                        with Vertical(id="series-left"):
                            yield DataTable(id="dt-series", cursor_type="row")
                        with Vertical(id="series-right"):
                            yield Label("— nessuna serie —", id="det-s-title", classes="det-title")
                            yield Static("", id="det-s-body", classes="det-body")
                    with Horizontal(id="ep-toolbar"):
                        yield Label("—", id="ep-label")
                        yield Button("Riscarica",      id="b-ep-redl", classes="warn")
                        yield Button("Ignora",         id="b-ep-ign")
                        yield Button("Forza Mancante", id="b-ep-miss", classes="danger")
                    with Container(id="ep-section"):
                        yield DataTable(id="dt-eps", cursor_type="row")

            with TabPane("Film", id="tab-movies"):
                with Vertical():
                    with Horizontal(classes="toolbar"):
                        yield Button("Run Film",   id="b-run-m",  classes="ok", tooltip="Cerca i film mancanti ORA")
                        yield Button("+ Aggiungi", id="b-add-m",  classes="ok", tooltip="Aggiungi alla lista desideri")
                        yield Button("Elimina",    id="b-del-m",  classes="danger", tooltip="Rimuovi il film dalla lista")
                        yield Button("Aggiorna",   id="b-ref-m",  tooltip="Ricarica la lista")
                    with Horizontal(id="movies-split"):
                        with Vertical(id="movies-left"):
                            yield DataTable(id="dt-movies", cursor_type="row")
                        with Vertical(id="movies-right"):
                            yield Label("— nessun film —", id="det-m-title", classes="det-title")
                            yield Static("", id="det-m-body", classes="det-body")

            with TabPane("Torrent", id="tab-torrents"):
                with Vertical():
                    with Horizontal(classes="toolbar"):
                        yield Button("+ Magnet/Torrent",   id="b-add-mag",  classes="ok")
                        yield Button("Punteggi",           id="b-scores",   classes="info")
                        yield Button("Rimuovi Completati", id="b-rm-comp",  classes="warn")
                        yield Button("Azione Pulizia",     id="b-clean-act")
                        yield Button("Velocità",           id="b-speed",   classes="info")
                        yield Button("Interfaccia",        id="b-iface")
                        yield Button("Aggiorna",           id="b-ref-t")
                    yield DataTable(id="dt-torr", cursor_type="row")

            with TabPane("Log", id="tab-log"):
                with Vertical():
                    with Horizontal(classes="toolbar"):
                        yield Label("Level:")
                        yield Button("DEBUG",         id="b-ldebug")
                        yield Button("INFO",          id="b-linfo",   classes="ok")
                        yield Button("WARNING",       id="b-lwarn",   classes="warn")
                        yield Button("ERROR",         id="b-lerr",    classes="danger")
                        yield Button("Aggiorna",      id="b-ref-log")
                        yield Button("AutoScroll ON", id="b-ascroll")
                    with ScrollableContainer(id="log-scroll"):
                        yield Static("", id="log-text", markup=False)

            with TabPane("Archivio", id="tab-archive"):
                with Vertical():
                    with Horizontal(id="arch-toolbar"):
                        yield Label("Cerca:")
                        yield Input(placeholder="Matrix +1080p -ita", id="arch-inp")
                        yield Button("Cerca", id="b-arch-srch")
                        yield Label("", id="arch-count")
                    yield DataTable(id="dt-arch", cursor_type="row")

            with TabPane("Salute", id="tab-health"):
                with Vertical():
                    yield Static("Caricamento stato sistema...", id="health-txt")
                    yield DataTable(id="dt-health-disk", cursor_type="row")
                    yield DataTable(id="dt-health-idx", cursor_type="row")
                    yield DataTable(id="dt-health-serv", cursor_type="row")
                    yield DataTable(id="dt-health-err", cursor_type="row")

            with TabPane("Sorgenti", id="tab-sources"):
                with Vertical():
                    with Horizontal(classes="toolbar"):
                        yield Button("Test Sorgenti (fino a 30s)", id="b-test-src")
                        yield Label("", id="src-status")
                    yield DataTable(id="dt-src", show_cursor=False)

        yield Label("", id="toast")
        yield Footer()

    # ── on_mount ──────────────────────────────────────────────────────────────

    def on_mount(self):
        self.query_one("#dt-dl",     DataTable).add_columns("Tipo", "Titolo", "Qual", "Data")
        self.query_one("#dt-series", DataTable).add_columns("ID", "Nome", "Ep", "Qual", "Ultimo", "Stato")
        self.query_one("#dt-eps",    DataTable).add_columns("ID", "St", "Ep", "Titolo", "Qual", "Scaricato")
        self.query_one("#dt-movies", DataTable).add_columns("ID", "Titolo", "Anno", "Qual", "Lingua", "Scaricato")
        self.query_one("#dt-torr",   DataTable).add_columns("Nome", "Stato", "Progresso", "DL", "UL", "Size", "Ratio", "ETA")
        self.query_one("#dt-arch",   DataTable).add_columns("ID", "Titolo", "Aggiunto")
        self.query_one("#dt-src",    DataTable).add_columns("Nome", "URL", "Stato", "Ping")
        self.query_one("#dt-health-disk", DataTable).add_columns("Disco", "Spazio", "Stato")
        self.query_one("#dt-health-idx", DataTable).add_columns("Indice", "Stato")
        self.query_one("#dt-health-serv", DataTable).add_columns("Servizio", "Stato")
        self.query_one("#dt-health-err", DataTable).add_columns("Ultime righe di errore nel log")

        self._load_dash()
        self._load_series()
        self._load_movies()
        self._load_torrents()
        self._load_archive()
        self.set_interval(5, self._auto_refresh)
        self.set_interval(3, self._load_sys)  # <- Nuovo timer super veloce!

    # ── refresh ───────────────────────────────────────────────────────────────

    def _auto_refresh(self):
        self._load_dash()
        try:
            tab = self.query_one(TabbedContent).active
            if tab == "tab-torrents" or tab == "tab-dash":
                self._load_torrents()
            if tab == "tab-log":
                self._load_log()
        except NoMatches:
            pass

    def action_refresh(self):
        try:
            tab = self.query_one(TabbedContent).active
        except NoMatches:
            return
        {
            "tab-dash":     self._load_dash,
            "tab-series":   self._load_series,
            "tab-movies":   self._load_movies,
            "tab-torrents": self._load_torrents,
            "tab-log":      self._load_log,
            "tab-health":   self._load_health,
            "tab-archive":  lambda: self._load_archive(),
            "tab-sources":  self._load_sources,
        }.get(tab, lambda: None)()

    # ── toast ─────────────────────────────────────────────────────────────────

    def notify_msg(self, msg, duration=3.0, error=False):
        try:
            t = self.query_one("#toast", Label)
            t.update(msg)
            t.remove_class("error")
            if error:
                t.add_class("error")
            t.add_class("show")
            self.set_timer(duration, lambda: t.remove_class("show"))
        except NoMatches:
            pass

    # ── workers: Dashboard ────────────────────────────────────────────────────

    @work(thread=True)
    def _load_dash(self):
        try:
            st   = self.api.stats()
            lc   = self.api.last_cycle()
            sys_ = self.api.syscfg()
            rec  = self.api.recent()
            self.call_from_thread(self._upd_dash, st, lc, sys_, rec)
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Dashboard: {e}", 4, True)

    def _upd_dash(self, st, lc, sys_, rec):
        last = lc.get("generated_at", "")
        intv = lc.get("refresh_interval", 7200)
        nr   = next_run(last, intv)
        cpu  = sys_.get("cpu",    "?") if isinstance(sys_, dict) else "?"
        ram  = sys_.get("ram_mb", "?") if isinstance(sys_, dict) else "?"
        disk = sys_.get("disk",   "?") if isinstance(sys_, dict) else "?"
        up   = sys_.get("uptime", "?") if isinstance(sys_, dict) else "?"
        try:
            self.query_one("#sb-cycle", Label).update(f"Ciclo: {fmts(last)}")
            self.query_one("#sb-next",  Label).update(f"| Prox: {nr}")
            self.query_one("#sb-sys",   Label).update(f"| CPU:{cpu}% RAM:{ram}MB")
            self.query_one("#sb-disk",  Label).update(f"| Disco:{disk}% Up:{up}")
            self.query_one("#d-cycle", Static).update(
                f"Ultimo:     {fmts(last)}\nProssimo:   {nr}\nIntervallo: {intv // 60}min"
            )
            self.query_one("#d-sys", Static).update(
                f"CPU:    {cpu}%\nRAM:    {ram} MB\nDisco:  {disk}%\nUptime: {up}"
            )
            cons = st.get('consumption', {})
            self.query_one("#d-db", Static).update(
                f"Serie:    {st.get('series_enabled','?')}/{st.get('series_configured','?')}\n"
                f"Film:     {st.get('movies_enabled','?')}/{st.get('movies_configured','?')}\n"
                f"Fumetti:  {st.get('comics_configured','?')}\n"
                f"DL 30d:   {cons.get('last_30_days_gb','?')} GB\n"
                f"Libero:   {st.get('disk_free_gb','?')} GB"
            )
            dt = self.query_one("#dt-dl", DataTable)
            dt.clear()
            w = max(20, self.size.width - 85) # Calcola lo spazio libero
            for item in rec.get("downloads", [])[:20]:
                dt.add_row(
                    item.get("type", "?").upper(),
                    (item.get("title") or "?")[:w],
                    qual(item.get("quality_score")),
                    fmts(item.get("date")),
                )
        except NoMatches:
            pass

    # ── workers: Sistema ──────────────────────────────────────────────────────

    @work(thread=True)
    def _load_sys(self):
        try:
            sys_ = self.api.syscfg()
            self.call_from_thread(self._upd_sys, sys_)
        except Exception:
            pass

    def _upd_sys(self, sys_):
        cpu  = sys_.get("cpu",    "?") if isinstance(sys_, dict) else "?"
        ram  = sys_.get("ram_mb", "?") if isinstance(sys_, dict) else "?"
        disk = sys_.get("disk",   "?") if isinstance(sys_, dict) else "?"
        up   = sys_.get("uptime", "?") if isinstance(sys_, dict) else "?"
        try:
            self.query_one("#sb-sys",   Label).update(f"| CPU:{cpu}% RAM:{ram}MB")
            self.query_one("#sb-disk",  Label).update(f"| Disco:{disk}% Up:{up}")
        except NoMatches:
            pass

    # ── workers: Serie ────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_log(self):
        try:
            lines = self.api.logs(100)
            self.call_from_thread(self._upd_log, lines)
        except Exception as e:
            self.notify_msg(f"Log: {e}", error=True)

    def _upd_log(self, lines):
        txt = self.query_one("#log-text", Static)
        txt.update("\n".join(lines))
        if getattr(self, "auto_scroll", False):
            self.query_one("#log-scroll").scroll_end(animate=False)

    @work(thread=True)
    def _load_series(self):
        try:
            data  = self.api.series()
            stats = self.api.series_stats()
            comp  = self.api.completeness()
            self.call_from_thread(self._upd_series, data, stats, comp)
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Serie: {e}", 4, True)

    def _upd_series(self, data, stats, comp):
        self._series = data
        try:
            t = self.query_one("#dt-series", DataTable)
            t.clear()
            for s in data:
                try:
                    sid  = s.get("id")
                    name = (s.get("name") or "?")[:36]
                    eps  = s.get("episodes_count", 0)
                    q_   = qual(s.get("quality_score"))

                    # Lettura sicura di stagione ed episodio
                    l_s = s.get("last_season")
                    l_e = s.get("last_episode")
                    if l_s is not None and l_e is not None:
                        lu = f"S{int(l_s):02d}E{int(l_e):02d}"
                    else:
                        lu = "—"

                    st   = stats.get(str(sid), {})
                    if comp.get(s.get("name", "")):
                        stato = "Completa"
                    elif st.get("is_ended"):
                        stato = "Conclusa"
                    else:
                        stato = "Attiva"

                    t.add_row(str(sid), name, str(eps), q_, lu, stato)
                except Exception:
                    # Se una serie va in errore, ignorala ma non bloccare le altre!
                    continue
        except NoMatches:
            pass

    def _show_series_det(self, sid):
        s = next((x for x in self._series if x.get("id") == sid), None)
        if not s:
            return
        try:
            self.query_one("#det-s-title", Label).update(s.get("name", "?"))
            self.query_one("#det-s-body", Static).update(
                f"ID:      {s.get('id')}\n"
                f"Episodi: {s.get('episodes_count', '?')}\n"
                f"Qualita: {qual(s.get('quality_score'))} ({s.get('quality_score', '?')})\n"
                f"Ultimo:  {fmts(s.get('last_download'))}\n"
                f"TMDB:    {s.get('tvdb_id', '?')}\n"
                f"Archive: {s.get('archive_path') or '—'}"
            )
        except NoMatches:
            pass

    @work(thread=True)
    def _load_episodes(self, sid):
        try:
            data = self.api.episodes(sid)
            self.call_from_thread(self._upd_episodes, data)
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Episodi: {e}", 4, True)

    def _upd_episodes(self, data):
        try:
            t = self.query_one("#dt-eps", DataTable)
            t.clear()
            for ep in sorted(data, key=lambda x: (x.get("season", 0), x.get("episode", 0))):
                t.add_row(
                    str(ep.get("id", "")),
                    f"S{ep.get('season', 0):02d}",
                    f"E{ep.get('episode', 0):02d}",
                    (ep.get("title") or "")[:30],
                    qual(ep.get("quality_score")),
                    fmts(ep.get("downloaded_at")),
                )
            self.query_one("#ep-label", Label).update(
                f"{len(data)} episodi — seleziona con ↑↓"
            )
        except NoMatches:
            pass

    # ── workers: Film ─────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_movies(self):
        try:
            cfg_movies = self.api.movies()
            try:
                db_movies  = self.api.movies_db()
            except Exception:
                db_movies  = []
            # Mappa nome (lowercase) → record DB per join
            db_map = {m.get("name", "").lower(): m for m in (db_movies or [])}
            self.call_from_thread(self._upd_movies, cfg_movies, db_map)
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Film: {e}", 4, True)

    def _upd_movies(self, data, db_map=None):
        self._movies   = data
        self._movies_db = db_map or {}
        try:
            t = self.query_one("#dt-movies", DataTable)
            t.clear()
            for i, m in enumerate(data):
                key        = m.get("name", "").lower()
                db_rec     = self._movies_db.get(key)
                scaricato  = "✓" if db_rec else "—"
                stato_str  = ("In Pausa" if not m.get("enabled", True) else
                              ("Scaricato" if db_rec else "In Ricerca"))
                t.add_row(
                    str(i),
                    m.get("name", "?")[:38],
                    str(m.get("year") or "*"),
                    m.get("quality", "?"),
                    m.get("language", "?").upper(),
                    scaricato,
                )
        except NoMatches:
            pass

    def _show_movie_det(self, mid):
        if mid < 0 or mid >= len(self._movies): return
        m      = self._movies[mid]
        key    = m.get("name", "").lower()
        db_rec = getattr(self, "_movies_db", {}).get(key)
        try:
            self.query_one("#det-m-title", Label).update(m.get("name", "?"))
            stato = ("In Pausa"   if not m.get("enabled", True) else
                     "Scaricato"  if db_rec else
                     "In Ricerca")
            body  = (
                f"Anno:      {m.get('year') or 'Qualsiasi'}\n"
                f"Qualità:   {m.get('quality', 'N/D')}\n"
                f"Lingua:    {m.get('language', 'N/D').upper()}\n"
                f"Stato:     {stato}\n"
            )
            if db_rec:
                dl_at   = (db_rec.get("downloaded_at") or "")[:10]
                title_r = db_rec.get("title") or ""
                score   = db_rec.get("quality_score", "?")
                body += (
                    f"─────────────────────────\n"
                    f"Release:   {title_r[:50]}\n"
                    f"Score:     {score}\n"
                    f"Download:  {dl_at}\n"
                )
            self.query_one("#det-m-body", Static).update(body)
        except NoMatches:
            pass

    # ── workers: Torrent ──────────────────────────────────────────────────────

    @work(thread=True)
    def _load_torrents(self):
        try:
            data = self.api.torrents()
            # Se riceviamo un dizionario con l'errore del backend (503)
            if isinstance(data, dict) and "error" in data:
                self.call_from_thread(self.notify_msg, f"Backend: {data['error']}", 4, True)
                self.call_from_thread(self._upd_torrents, [])
                return

            if not data:
                # Se è una lista vuota o None, non è necessariamente un errore
                pass
            self.call_from_thread(self._upd_torrents, data)
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Connessione: {e}", 4, True)
            self.call_from_thread(self._upd_torrents, [])

    def _upd_torrents(self, data):
        # DEBUG per capire cosa arriva dal server
        # self.call_from_thread(self.notify_msg, f"RAW DATA: {type(data)} {str(data)[:200]}", 5)

        if isinstance(data, dict):
            lst = data.get("torrents", data.get("data", []))
        elif isinstance(data, list):
            lst = data
        else:
            lst = []

        # Se lst è un dizionario (perché extto3 ha mandato {'torrents': { ... }})
        if isinstance(lst, dict):
            # Proviamo a vedere se è un dizionario di torrent indicizzati per hash o altro
            # In tal caso prendiamo i valori
            lst = list(lst.values())

        # Altro DEBUG
        # self.call_from_thread(self.notify_msg, f"LST: {type(lst)} len={len(lst) if isinstance(lst, list) else 'N/A'}", 3)

        try:
            t = self.query_one("#dt-torr", DataTable)
            t.clear()
            for tor in lst:
                try: # <--- SALVAGENTE: Se un torrent è corrotto, non blocca gli altri
                    name_raw = tor.get("name") or tor.get("info_hash") or "?"
                    name = str(name_raw)[:44]
                    raw_s = str(tor.get("state", tor.get("status", "?")))
                    state = TORRENT_STATES.get(raw_s, raw_s)

                    # Se progress manca, lo cerchiamo in percent_done o lo mettiamo a 0
                    prog = tor.get("progress")
                    if prog is None:
                        prog = tor.get("percent_done", 0)

                    if prog is None: prog = 0
                    if isinstance(prog, (float, int)) and prog <= 1.0:
                        prog *= 100

                    # --- LA MAGIA ANCHE NEL TERMINALE ---
                    file_on_disk = tor.get("physical_file_found", False)
                    if file_on_disk:
                        prog = 100
                        # Se il file è sul NAS, lo stato deve essere informativo
                        # Se non è in uno stato finale del client, mostriamo SALVATO
                        final_states = ("SEED", "FINE", "FINE_T")
                        if state not in final_states:
                            state = "SALVATO"
                    # ------------------------------------

                    dl_rate = tor.get("download_rate", tor.get("rateDownload", 0)) or 0
                    ul_rate = tor.get("upload_rate",   tor.get("rateUpload",   0)) or 0
                    tot_size = tor.get("total_size",   tor.get("totalSize",    0)) or 0

                    dl_s  = spd(dl_rate)
                    ul_s  = spd(ul_rate)
                    size  = sz(tot_size)

                    # Calcolo Ratio a prova di bomba
                    raw_ratio = tor.get('ratio')
                    if raw_ratio is None:
                        raw_ratio = tor.get('uploadRatio')
                    if raw_ratio is None:
                        d_done = tor.get('total_done', 0) or 0
                        u_done = tor.get('total_uploaded', 0) or 0
                        raw_ratio = (u_done / d_done) if d_done > 0 else 0.0

                    try:
                        ratio = f"{float(raw_ratio):.2f}"
                    except (ValueError, TypeError):
                        ratio = "0.00"

                    eta_v = tor.get("eta", -1)
                    if file_on_disk:
                        eta = "✓ NAS"
                    elif isinstance(eta_v, int) and eta_v > 0:
                        h, r = divmod(eta_v, 3600)
                        eta = f"{h}h{r // 60:02d}m"
                    else:
                        eta = "—"

                    t.add_row(name, state, pbar(prog), dl_s, ul_s, size, ratio, eta)
                except Exception:
                    continue # Salta silenziosamente la riga problematica
        except NoMatches:
            pass

    # ── workers: Log ──────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_log(self):
        try:
            data = self.api.logs(300)
            self.call_from_thread(self._upd_log, data.get("logs", []))
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Log: {e}", 4, True)

    def _upd_log(self, lines):
        try:
            self.query_one("#log-text", Static).update("".join(lines))
            if self._log_auto:
                try:
                    self.query_one("#log-scroll", ScrollableContainer).scroll_end(animate=False)
                except NoMatches:
                    pass
        except NoMatches:
            pass

    # ── workers: Archivio ─────────────────────────────────────────────────────

    @work(thread=True)
    def _load_archive(self, q=""):
        try:
            data = self.api.archive(q=q, limit=100)
            self.call_from_thread(self._upd_archive, data)
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Archivio: {e}", 4, True)

    def _upd_archive(self, data):
        try:
            t = self.query_one("#dt-arch", DataTable)
            t.clear()
            items = data.get("items", [])
            total = data.get("total", len(items))
            w = max(30, self.size.width - 35) # Calcola lo spazio libero
            for item in items:
                t.add_row(
                    str(item.get("id", "")),
                    (item.get("title") or "?")[:w],
                    fmts(item.get("added_at")),
                )
            self.query_one("#arch-count", Label).update(f"  {len(items)}/{total}")
        except NoMatches:
            pass

    # ── workers: Sorgenti ─────────────────────────────────────────────────────

    @work(thread=True)
    def _load_sources(self):
        self.call_from_thread(self.notify_msg, "Test in corso...", 35)
        try:
            data = self.api.sources()
            self.call_from_thread(self._upd_sources, data.get("sources", []))
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Sorgenti: {e}", 4, True)

    def _upd_sources(self, sources):
        try:
            t = self.query_one("#dt-src", DataTable)
            t.clear()
            ok_n = 0
            for s in sources:
                st  = s.get("status", "?")
                lbl = "OK" if st == "online" else ("TIMEOUT" if st == "timeout" else "ERR")
                if st == "online":
                    ok_n += 1
                t.add_row(
                    s.get("name", "?"),
                    (s.get("url", "?"))[:44],
                    lbl,
                    f"{s.get('ping', '?')}ms",
                )
            self.query_one("#src-status", Label).update(f"  {ok_n}/{len(sources)} online")
            self.notify_msg(f"Test: {ok_n}/{len(sources)} sorgenti online")
        except NoMatches:
            pass

    # ── workers: Salute ───────────────────────────────────────────────────────

    @work(thread=True)
    def _load_health(self):
        self.call_from_thread(self.notify_msg, "Caricamento stato sistema...", 2)
        try:
            data = self.api.health()
            self.call_from_thread(self._upd_health, data)
        except Exception as e:
            self.call_from_thread(self.notify_msg, f"Health: {e}", 4, True)

    def _upd_health(self, data):
        try:
            # 1. Testo Generale (CPU, RAM, Uptime)
            sys_stats = data.get("system", {})
            ts = data.get("timestamp", "N/D")
            cpu = sys_stats.get("cpu_percent", "?")
            ram = sys_stats.get("ram_percent", "?")
            host = sys_stats.get("hostname", "?")
            
            txt_widget = self.query_one("#health-txt", Static)
            txt_widget.update(f"Host: {host}  |  CPU: {cpu}%  |  RAM: {ram}%  |  Aggiornato: {ts[:16].replace('T', ' ')}")

            # 2. Tabella Disco
            dt_disk = self.query_one("#dt-health-disk", DataTable)
            dt_disk.clear()
            dt_disk.display = True
            for d in data.get("disk", []):
                nome = d.get("label", "?")
                spazio = f"{d.get('free_gb', 0)}GB liberi su {d.get('total_gb', 0)}GB ({d.get('percent', 0)}%)"
                stato = "ATTENZIONE" if d.get("status") == "warning" else "OK"
                dt_disk.add_row(nome, spazio, stato)

            # 3. Tabella Indexer (Jackett/Prowlarr)
            dt_idx = self.query_one("#dt-health-idx", DataTable)
            dt_idx.clear()
            dt_idx.display = True
            for idx in data.get("indexers", []):
                dt_idx.add_row(idx.get("name", "?"), str(idx.get("status", "?")).upper())

            # 4. Tabella Servizi (Motore, ecc.)
            dt_serv = self.query_one("#dt-health-serv", DataTable)
            dt_serv.clear()
            dt_serv.display = True
            for srv in data.get("services", []):
                dt_serv.add_row(srv.get("name", "?"), srv.get("status", "?"))

            # 5. Tabella Errori Log
            dt_err = self.query_one("#dt-health-err", DataTable)
            dt_err.clear()
            dt_err.display = True
            for err in data.get("logs", []):
                dt_err.add_row(err)

        except Exception as e:
            self.notify_msg(f"Errore UI Health: {e}", error=True)

    # ── azioni ciclo ──────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        """Uscita immediata: cancella tutti i worker prima di chiudere."""
        self.workers.cancel_all()
        self.exit()

    def action_run_all(self):    self._do_run("all")
    def action_run_series(self): self._do_run("series")
    def action_run_movies(self): self._do_run("movies")
    def action_run_comics(self): self._do_run("comics")

    @work(thread=True)
    def _do_run(self, domain):
        try:
            self.api.run(domain)
            self.call_from_thread(self.notify_msg, f"Ciclo '{domain}' avviato")
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    # ── navigazione frecce ────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        tid = event.data_table.id
        key = event.row_key
        try:
            row = event.data_table.get_row(key)
        except Exception:
            return

        if tid == "dt-series":
            try:
                sid = int(row[0])
                if sid != self._sel_sid:
                    self._sel_sid  = sid
                    self._sel_name = row[1] if len(row) > 1 else None  # salva nome per fallback
                    self._sel_eid  = None
                    self._show_series_det(sid)
                    self._load_episodes(sid)
            except (ValueError, IndexError):
                pass

        elif tid == "dt-eps":
            try:
                self._sel_eid = int(row[0])
                self.query_one("#ep-label", Label).update(
                    f"{row[1]}{row[2].lstrip('E')}  {row[3]}  ({row[4]})"
                )
            except (ValueError, IndexError):
                pass

        elif tid == "dt-movies":
            try:
                mid = int(row[0])
                if mid != self._sel_mid:
                    self._sel_mid = mid
                    self._show_movie_det(mid)
            except (ValueError, IndexError):
                pass

    # ── cambio tab ────────────────────────────────────────────────────────────

    # ── cambio tab ────────────────────────────────────────────────────────────

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated):
        try:
            tab_attiva = self.query_one(TabbedContent).active
            # DEBUG: self.notify_msg(f"Tab attivata: {tab_attiva}")
            if tab_attiva == "tab-log":
                self._load_log()
            elif tab_attiva == "tab-torrents":
                self._load_torrents()
            elif tab_attiva == "tab-series":
                self._load_series()
            elif tab_attiva == "tab-movies":
                self._load_movies()
            elif tab_attiva == "tab-archive":
                self._load_archive()
            elif tab_attiva == "tab-sources":
                self._load_sources()
            elif tab_attiva == "tab-dash":
                self._load_dash()
            elif tab_attiva == "tab-health":
                self._load_health()
        except NoMatches:
            pass

    # ── bottoni ───────────────────────────────────────────────────────────────

    @on(Button.Pressed)
    def on_btn(self, event: Button.Pressed):
        b = event.button.id

        if b == "b-run-s":    self._do_run("series")
        elif b == "b-add-s":
            self.push_screen(AskModal("Aggiungi Serie", "Nome serie:"), callback=self._cb_add_series)
        elif b == "b-ref-s":  self._load_series()
        elif b == "b-ren-s":
            if self._sel_sid:
                s = next((x for x in self._series if x.get("id") == self._sel_sid), {})
                self.push_screen(
                    AskModal("Rinomina Serie", "Nuovo nome:", default=s.get("name", "")),
                    callback=self._cb_rename)
            else:
                self.notify_msg("Seleziona prima una serie")
        elif b == "b-del-s":
            if self._sel_sid:
                s = next((x for x in self._series if x.get("id") == self._sel_sid), {})
                nome_serie = s.get("name", f"ID {self._sel_sid}")
                self.push_screen(
                    ConfirmModal(f"Eliminare la serie '{nome_serie}' dal DB?"),
                    callback=self._cb_del_series)
            else:
                self.notify_msg("Seleziona prima una serie")
        elif b == "b-miss-s":
            if self._sel_sid:
                self.push_screen(
                    AskModal("Cerca Mancanti", "Stagione (es: 2):", "1"),
                    callback=self._cb_search_miss)
            else:
                self.notify_msg("Seleziona prima una serie")

        elif b == "b-ep-redl":
            if self._sel_eid: self._do_ep("redownload", self._sel_eid)
            else: self.notify_msg("Seleziona prima un episodio")
        elif b == "b-ep-ign":
            if self._sel_eid: self._do_ep("ignore", self._sel_eid)
            else: self.notify_msg("Seleziona prima un episodio")
        elif b == "b-ep-miss":
            if self._sel_eid:
                self.push_screen(
                    ConfirmModal(f"Forza episodio ID {self._sel_eid} come mancante?"),
                    callback=self._cb_ep_missing)
            else:
                self.notify_msg("Seleziona prima un episodio")

        elif b == "b-run-m":   self._do_run("movies")
        elif b == "b-add-m":
            self.push_screen(AskModal("Aggiungi Film", "Nome film:"), callback=self._cb_add_movie)
        elif b == "b-ref-m":   self._load_movies()
        elif b == "b-del-m":
            if self._sel_mid is not None:
                m = self._movies[self._sel_mid] if 0 <= self._sel_mid < len(self._movies) else {}
                nome_film = m.get("name", m.get("title", f"ID {self._sel_mid}"))
                self.push_screen(
                    ConfirmModal(f"Eliminare il film '{nome_film}' dalla lista?"),
                    callback=self._cb_del_movie)
            else:
                self.notify_msg("Seleziona prima un film")

        elif b == "b-add-mag":
            self.push_screen(MagnetModal(), callback=self._cb_magnet)
        elif b == "b-rm-comp":
            self.push_screen(
                ConfirmModal("Rimuovere tutti i torrent completati (senza cancellare i file)?"),
                callback=self._cb_rm_completed)
        elif b == "b-clean-act":
            self.push_screen(CleanupActionModal(), callback=self._cb_cleanup_action)
        elif b == "b-scores":
            self._open_scores()
        elif b == "b-ref-t":   self._load_torrents()
        elif b == "b-speed":   self._open_speed()
        elif b == "b-iface":   self._open_iface()

        elif b == "b-ldebug":  self._do_log_level("debug")
        elif b == "b-linfo":   self._do_log_level("info")
        elif b == "b-lwarn":   self._do_log_level("warning")
        elif b == "b-lerr":    self._do_log_level("error")
        elif b == "b-ref-log": self._load_log()
        elif b == "b-ascroll":
            self._log_auto = not self._log_auto
            event.button.label = f"AutoScroll {'ON' if self._log_auto else 'OFF'}"

        elif b == "b-arch-srch":
            try:
                q = self.query_one("#arch-inp", Input).value.strip()
                self._load_archive(q)
            except NoMatches:
                pass

        elif b == "b-test-src":
            self._load_sources()

    @on(Input.Submitted, "#arch-inp")
    def arch_enter(self, e: Input.Submitted):
        self._load_archive(e.value.strip())

    # ── callbacks modal ───────────────────────────────────────────────────────

    @work(thread=True)
    def _cb_rename(self, name):
        if not name or not self._sel_sid: return
        try:
            self.api.rename_series(self._sel_sid, name)
            self.call_from_thread(self.notify_msg, f"Rinominata: {name}")
            self.call_from_thread(self._load_series)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_del_series(self, ok):
        if not ok: return
        if not self._sel_sid and not self._sel_name: return
        try:
            self.api.del_series(self._sel_sid, name=self._sel_name)
            self.call_from_thread(self.notify_msg, "Serie eliminata dal DB")
            self._sel_sid  = None
            self._sel_name = None
            self.call_from_thread(self._load_series)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_search_miss(self, s_str):
        if not s_str or not self._sel_sid: return
        try:
            self.api.search_miss(self._sel_sid, int(s_str))
            self.call_from_thread(self.notify_msg, f"Ricerca mancanti S{int(s_str):02d} avviata")
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _do_ep(self, action, eid):
        try:
            if action == "redownload": self.api.ep_redl(eid)
            elif action == "ignore":   self.api.ep_ignore(eid)
            self.call_from_thread(self.notify_msg, f"Ep {eid}: {action} OK")
            if self._sel_sid:
                self.call_from_thread(self._load_episodes, self._sel_sid)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_ep_missing(self, ok):
        if not ok or not self._sel_eid: return
        try:
            self.api.ep_missing(self._sel_eid)
            self.call_from_thread(self.notify_msg, "Episodio forzato mancante")
            if self._sel_sid:
                self.call_from_thread(self._load_episodes, self._sel_sid)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_del_movie(self, ok):
        if not ok or self._sel_mid is None: return
        try:
            self.api.del_movie(self._sel_mid)
            self.call_from_thread(self.notify_msg, "Film eliminato")
            self._sel_mid = None
            self._load_movies()
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_magnet(self, magnet):
        if not magnet: return

        try:
            # 1. È un URL HTTP/HTTPS?
            if magnet.startswith("http://") or magnet.startswith("https://"):
                self.call_from_thread(self.notify_msg, "Scaricamento file .torrent...")
                res = self.api.fetch_url(magnet)

                # Se il backend lo scarica con successo
                if res.get("success"):
                    self.call_from_thread(self.notify_msg, "Invio a libtorrent...")
                    up_res = self.api.upload_torrent(res.get("filename", "downloaded.torrent"), res.get("data", ""))
                    if up_res.get("success"):
                        self.call_from_thread(self.notify_msg, "Torrent aggiunto da URL!")
                    else:
                        self.call_from_thread(self.notify_msg, f"Errore: {up_res.get('error', 'Errore upload')}", 4, True)

                # Se in realtà era un Magnet camuffato da redirect HTTP
                elif res.get("is_magnet"):
                    self.call_from_thread(self.notify_msg, "Redirect a magnet rilevato, invio...")
                    r = self.api.send_magnet(res.get("magnet"))
                    msg = r.get("message", "OK") if isinstance(r, dict) else "OK"
                    self.call_from_thread(self.notify_msg, msg)

                else:
                    self.call_from_thread(self.notify_msg, f"Errore: {res.get('error', 'Sito non raggiungibile')}", 4, True)

            # 2. È un Magnet classico
            else:
                self.call_from_thread(self.notify_msg, "Invio magnet...")
                r = self.api.send_magnet(magnet)
                msg = r.get("message", "OK") if isinstance(r, dict) else "OK"
                self.call_from_thread(self.notify_msg, msg)

            # Ricarichiamo la lista per visualizzarlo subito
            self.call_from_thread(self._load_torrents)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_rm_completed(self, ok):
        if not ok: return
        try:
            self.api.rm_completed()
            self.call_from_thread(self.notify_msg, "Torrent completati rimossi")
            self.call_from_thread(self._load_torrents)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _open_scores(self):
        try:
            # Recupera i punteggi attuali dal backend
            r = requests.get(f"{self.api.url}/api/scores/settings", timeout=5)
            if r.status_code == 200:
                scores = r.json()
                self.call_from_thread(self.push_screen, ScoreModal(scores), callback=self._cb_scores)
            else:
                self.call_from_thread(self.notify_msg, "Errore caricamento punteggi", 4, True)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_scores(self, new_scores):
        if not new_scores: return
        try:
            r = requests.post(f"{self.api.url}/api/scores/settings", json=new_scores, timeout=5)
            if r.status_code == 200:
                self.call_from_thread(self.notify_msg, "Punteggi salvati correttamente!")
            else:
                self.call_from_thread(self.notify_msg, "Errore salvataggio punteggi", 4, True)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _open_speed(self):
        try:
            cfg = self.api.config()
            s   = cfg.get("settings", {})
            dl  = str(int(s.get("libtorrent_dl_limit", 0)) // 1024)
            ul  = str(int(s.get("libtorrent_ul_limit", 0)) // 1024)
        except Exception:
            dl, ul = "0", "0"
        self.call_from_thread(self.push_screen, SpeedModal(), callback=self._cb_speed)

    @work(thread=True)
    def _cb_speed(self, result):
        if result is None: return
        dl_kbps, ul_kbps = result
        try:
            res = self.api.set_speed_limits(dl_kbps, ul_kbps)
            msg = res.get("message", "Limiti applicati")
            self.call_from_thread(self.notify_msg, msg)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _open_iface(self):
        try:
            ifaces  = self.api.network_interfaces()
            cfg     = self.api.config()
            current = cfg.get("settings", {}).get("libtorrent_interface", "")
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)
            return
        self.call_from_thread(
            self.push_screen,
            NetworkInterfaceModal(ifaces, current),
            callback=self._cb_iface
        )

    @work(thread=True)
    def _cb_iface(self, iface):
        if iface is None: return  # annullato
        try:
            cfg = self.api.config()
            s   = cfg.get("settings", {})
            s["libtorrent_interface"] = iface
            self.api.save_settings(s)
            label = iface if iface else "tutte le interfacce"
            self.call_from_thread(self.notify_msg, f"Interfaccia impostata: {label}")
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_cleanup_action(self, action):
        if not action: return
        try:
            cfg = self.api.config()
            s = cfg.get("settings", {})
            s["cleanup_action"] = action
            self.api.save_settings(s)
            self.call_from_thread(self.notify_msg, f"Azione pulizia impostata su: {action.upper()}")
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _do_log_level(self, level):
        try:
            self.api.set_log_level(level)
            self.call_from_thread(self.notify_msg, f"Log level: {level.upper()}")
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)


    @work(thread=True)
    def _cb_add_series(self, name):
        if not name: return
        try:
            self.api.add_series(name)
            self.call_from_thread(self.notify_msg, f"Serie '{name}' aggiunta!")
            self.call_from_thread(self._load_series)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)

    @work(thread=True)
    def _cb_add_movie(self, name):
        if not name: return
        try:
            self.api.add_movie(name)
            self.call_from_thread(self.notify_msg, f"Film '{name}' aggiunto!")
            self.call_from_thread(self._load_movies)
        except Exception as e:
            self.call_from_thread(self.notify_msg, str(e), 4, True)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="EXTTO TUI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    args = p.parse_args()

    api = API(args.host, args.port)
    try:
        api.stats()
    except Exception as e:
        print(f"Impossibile connettersi a EXTTO su {args.host}:{args.port}")
        print(f"Errore: {e}")
        print("Verifica che extto_web.py sia in esecuzione.")
        sys.exit(1)

    ExttoTUI(api=api).run()


if __name__ == "__main__":
    main()
