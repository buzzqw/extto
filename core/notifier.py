import urllib.request
import json
import smtplib
import ssl
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Motore traduzioni locale (indipendente dalla UI)
# ---------------------------------------------------------------------------

def _load_i18n_dict(lang_code: str) -> dict:
    """
    Carica il dizionario traduzioni dal file YAML della lingua richiesta.
    languages/ è una cartella nella root di EXTTO (un livello sopra core/).
    Fallback silenzioso a dict vuoto se il file non esiste.
    """
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        yaml_path = os.path.join(base, 'languages', f'{lang_code}.yaml')
        if not os.path.isfile(yaml_path):
            return {}
        import yaml
        with open(yaml_path, encoding='utf-8') as f:
            d = yaml.safe_load(f) or {}
        return {str(k): str(v) for k, v in d.items()}
    except Exception as e:
        logger.debug(f"[i18n-notifier] load {lang_code}: {e}")
        return {}


def _get_active_lang() -> str:
    """Legge la lingua UI attiva dal DB. Fallback: 'it'."""
    try:
        import core.config_db as _cdb
        return (_cdb.get_ui_language() or 'it').lower().strip()
    except Exception:
        return 'it'


class Notifier:
    def __init__(self, cfg_dict):
        self.cfg = cfg_dict

        # Telegram
        self.tg_enabled = str(self.cfg.get('notify_telegram', 'no')).lower() in ['yes', 'true', '1']
        self.tg_token   = str(self.cfg.get('telegram_bot_token', '')).strip()
        self.tg_chat_id = str(self.cfg.get('telegram_chat_id', '')).strip()

        # Email
        self.em_enabled = str(self.cfg.get('notify_email', 'no')).lower() in ['yes', 'true', '1']
        self.em_smtp    = str(self.cfg.get('email_smtp', '')).strip()
        self.em_from    = str(self.cfg.get('email_from', '')).strip()
        self.em_to      = str(self.cfg.get('email_to', '')).strip()
        self.em_pass    = str(self.cfg.get('email_password', '')).strip()

        # SSL
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        # i18n: carica dizionario lingua attiva
        lang = _get_active_lang()
        self._i18n = _load_i18n_dict(lang)
        # Se la lingua non è italiano (master), tenta il fallback su ita
        if not self._i18n and lang != 'it':
            self._i18n = _load_i18n_dict('ita')

    def t(self, key: str) -> str:
        """Traduce una stringa italiana nella lingua UI attiva."""
        return self._i18n.get(key, key)

    # -----------------------------------------------------------------------

    def _send_telegram(self, text):
        if not self.tg_enabled or not self.tg_token or not self.tg_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {
                'chat_id': self.tg_chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={'Content-Type': 'application/json'}
            )
            urllib.request.urlopen(req, timeout=10, context=self.ctx)
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")

    def _send_email(self, subject, text):
        if not self.em_enabled or not self.em_smtp or not self.em_from or not self.em_to or not self.em_pass:
            return
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = self.em_from
            msg['To']      = self.em_to
            html_body = text.replace('\n', '<br>')
            msg.attach(MIMEText(html_body, 'html'))
            port = 587
            server_url = self.em_smtp
            if ':' in server_url:
                server_url, port_str = server_url.split(':')
                port = int(port_str)
            with smtplib.SMTP(server_url, port, timeout=10) as server:
                server.starttls(context=self.ctx)
                server.login(self.em_from, self.em_pass)
                server.send_message(msg)
        except Exception as e:
            logger.warning(f"Email send error: {e}")

    # -----------------------------------------------------------------------
    # Notifiche
    # -----------------------------------------------------------------------

    def notify_torrent_complete(self, torrent_name, total_bytes, active_time_secs,
                                pending_id=None, is_movie=False, is_series=False):
        try:
            from core.database import Database
            db = Database()
            if is_movie:
                c = db.conn.cursor()
                c.execute("UPDATE movies SET size_bytes = ? WHERE title = ? OR title LIKE ?",
                          (total_bytes, torrent_name, f"%{torrent_name}%"))
                db.conn.commit()
            elif pending_id:
                db.mark_downloaded(pending_id)
        except Exception as e:
            logger.debug(f"Error updating size in DB: {e}")

        # Per le serie TV la notifica finale viene inviata da notify_post_processing
        # (che include rinomina e path archiviazione). Qui non inviamo nulla.
        if is_series:
            return

        size_str = (f"{total_bytes / (1024**3):.2f} GB"
                    if total_bytes >= 1024**3
                    else f"{total_bytes / (1024**2):.2f} MB")

        m, s = divmod(active_time_secs, 60)
        h, m = divmod(m, 60)
        time_str = f"{int(h)}h {int(m)}m {int(s)}s" if h > 0 else f"{int(m)}m {int(s)}s"

        avg_speed = total_bytes / active_time_secs if active_time_secs > 0 else 0
        speed_str = f"{avg_speed / (1024**2):.1f} MB/s"

        msg = (
            f"🎉 <b>EXTTO: {self.t('Download Completato')}!</b>\n"
            f"<code>{torrent_name}</code>\n\n"
            f"📏 <b>{self.t('Dimensione')}:</b> {size_str}\n"
            f"⏱️ <b>{self.t('Tempo impiegato')}:</b> {time_str}\n"
            f"🚀 <b>{self.t('Velocità media')}:</b> {speed_str}"
        )
        self._send_telegram(msg)
        self._send_email(f"✅ EXTTO: {self.t('Download Completato')} - {torrent_name}", msg)

    def notify_download(self, series_name, season, episode, release_name, score, trigger):
        trigger_map = {
            'best-in-cycle-new':     f"🆕 {self.t('Episodio Mancante')}",
            'best-in-cycle-upgrade': f"⬆️ {self.t('Qualità Superiore Trovata')}",
            'best-in-cycle':         self.t('Ricerca Standard'),
            'timeframe-new':         f"⏱️ {self.t('Attesa Terminata')} — {self.t('Primo Download')}",
            'timeframe-upgrade':     f"⏱️ {self.t('Attesa Terminata')} — {self.t('Qualità Superiore')}",
            'timeframe':             self.t('Timeframe Scaduto (Miglior Qualità)'),
            'rss-fast':              f"⚡ {self.t('Fast RSS (Uscita Immediata)')}",
            'gap-fill':              f"🔍 {self.t('Recupero Mancanti (Gap Filling)')}",
        }
        trigger_name = trigger_map.get(trigger, trigger)

        # Season pack (E00): personalizza label episodio e trigger
        is_season_pack = (episode == 0)
        if is_season_pack:
            ep_label = f"S{season:02d} {self.t('Season Pack')}"
            if trigger == 'best-in-cycle-new':
                trigger_name = f"📦 {self.t('Season Pack')}"
            elif trigger == 'best-in-cycle-upgrade':
                trigger_name = f"⬆️ {self.t('Season Pack')} — {self.t('Qualità Superiore Trovata')}"
        else:
            ep_label = f"S{season:02d}E{episode:02d}"

        msg = (
            f"📺 <b>{self.t('NUOVO EPISODIO IN DOWNLOAD')}!</b>\n\n"
            f"🎬 <b>{self.t('Serie')}:</b> {series_name}\n"
            f"▶️ <b>{self.t('Episodio')}:</b> {ep_label}\n"
            f"🏷️ <b>Release:</b> <code>{release_name}</code>\n"
            f"🏆 <b>{self.t('Score Qualità')}:</b> {score}\n"
            f"⚙️ <b>{self.t('Motivo')}:</b> {trigger_name}"
        )
        self._send_telegram(msg)
        self._send_email(f"EXTTO: {series_name} {ep_label}", msg)

    def notify_movie(self, name, year, release_name, score):
        msg = (
            f"🍿 <b>{self.t('NUOVO FILM IN DOWNLOAD')}!</b>\n\n"
            f"🎬 <b>{self.t('Titolo')}:</b> {name} ({year})\n"
            f"🏷️ <b>Release:</b> <code>{release_name}</code>\n"
            f"🏆 <b>{self.t('Score Qualità')}:</b> {score}"
        )
        self._send_telegram(msg)
        self._send_email(f"EXTTO: {self.t('Download Film')} {name}", msg)

    def notify_gap_filled(self, name, season, episode):
        msg = (
            f"🕵️ <b>{self.t('RECUPERO MANCANTI COMPLETATO')}</b>\n\n"
            f"{self.t('Trovato e inviato l\'episodio mancante')}:\n"
            f"📌 <b>{self.t('Serie')}:</b> {name}\n"
            f"▶️ <b>{self.t('Episodio')}:</b> S{season:02d}E{episode:02d}\n"
            f"✨ <i>{self.t('L\'archivio è stato aggiornato.')}</i>"
        )
        self._send_telegram(msg)
        self._send_email(f"EXTTO: {self.t('Recuperato')} {name} S{season:02d}E{episode:02d}", msg)

    def notify_series_complete(self, series_name):
        msg = (
            f"🎊 <b>{self.t('SERIE COMPLETATA')}!</b> 🎊\n\n"
            f"{self.t('Hai scaricato tutti gli episodi previsti per')} <b>{series_name}</b> "
            f"{self.t('e la serie risulta ufficialmente conclusa (Ended).')}\n"
            f"{self.t('Collezione chiusa e pronta da guardare!')} 🍿"
        )
        self._send_telegram(msg)
        self._send_email(f"EXTTO: {self.t('Serie Completata')} - {series_name}", msg)

    def notify_comic(self, title: str, is_weekly: bool = False, method: str = 'torrent'):
        tipo = self.t('WEEKLY PACK') if is_weekly else self.t('FUMETTO')
        method_map = {
            'mega':    f"☁️ Mega",
            'http':    f"🌐 {self.t('Download Diretto')}",
            'torrent': f"🧲 Torrent",
        }
        method_str = method_map.get(method, '🧲 Torrent')
        msg = (
            f"📚 <b>{self.t('NUOVO')} {tipo} {self.t('IN DOWNLOAD')}!</b>\n\n"
            f"🏷️ <b>{self.t('Titolo')}:</b> <code>{title}</code>\n"
            f"⚙️ <b>{self.t('Metodo')}:</b> {method_str}\n"
            f"✨ <i>{self.t('Download avviato con successo.')}</i>"
        )
        self._send_telegram(msg)
        self._send_email(f"EXTTO: {self.t('Download')} {tipo} - {title}", msg)

    def notify_comic_complete(self, title: str, size_bytes: int = 0,
                               time_sec: float = 0, method: str = 'mega'):
        method_map = {'mega': '☁️ Mega', 'http': f"🌐 {self.t('Download Diretto')}"}
        method_str = method_map.get(method, method)

        stats_str = ""
        if size_bytes > 0:
            if size_bytes >= 1024**3:
                stats_str += f"💾 <b>{self.t('Dimensione')}:</b> {size_bytes/(1024**3):.2f} GB\n"
            else:
                stats_str += f"💾 <b>{self.t('Dimensione')}:</b> {size_bytes/(1024**2):.0f} MB\n"
        if time_sec > 1:
            m, s = divmod(int(time_sec), 60)
            stats_str += f"⏱️ <b>{self.t('Tempo')}:</b> {m}m {s}s\n"
            if size_bytes > 0:
                speed = (size_bytes / time_sec) / (1024**2)
                stats_str += f"🚀 <b>{self.t('Velocità media')}:</b> {speed:.1f} MB/s\n"

        msg = (
            f"✅ <b>{self.t('FUMETTO SCARICATO')}!</b>\n\n"
            f"🏷️ <b>{self.t('Titolo')}:</b> <code>{title}</code>\n"
            f"⚙️ <b>{self.t('Metodo')}:</b> {method_str}\n"
            f"{stats_str}"
            f"📂 <i>{self.t('File salvato nella cartella fumetti.')}</i>"
        )
        self._send_telegram(msg)
        self._send_email(f"✅ EXTTO: {self.t('Fumetto Completato')} - {title}", msg)

    def notify_comic_monitored(self, title: str):
        msg = (
            f"🔖 <b>{self.t('FUMETTO AGGIUNTO AI MONITORATI')}</b>\n\n"
            f"🏷️ <b>{self.t('Titolo')}:</b> <code>{title}</code>\n"
            f"✨ <i>{self.t('EXTTO cercherà automaticamente nuovi numeri ad ogni ciclo.')}</i>"
        )
        self._send_telegram(msg)
        self._send_email(f"EXTTO: {self.t('Monitorato')} - {title}", msg)

    def notify_backup_complete(self, zip_name: str, zip_mb: float, file_count: int,
                               kept: int, cloud_info: str = ''):
        """
        Messaggio unico di completamento backup.
        Inviato da extto_web.py al termine del backup ZIP.
        cloud_info: stringa opzionale tipo ' (Caricato su FTP: host)'.
        """
        cloud_str = f"\n☁️ <b>Cloud:</b> {cloud_info.strip()}" if cloud_info.strip() else ""
        msg = (
            f"🗂️ <b>EXTTO: {self.t('Backup Completato')}</b>\n\n"
            f"📦 <b>{self.t('File')}:</b> <code>{zip_name}</code>\n"
            f"💾 <b>{self.t('Dimensione')}:</b> {zip_mb:.1f} MB  "
            f"📁 <b>{self.t('File inclusi')}:</b> {file_count}  "
            f"🗂️ <b>{self.t('Conservati')}:</b> {kept}"
            f"{cloud_str}"
        )
        self._send_telegram(msg)
        self._send_email(f"✅ EXTTO: {self.t('Backup Completato')} — {zip_name}", msg)

    def notify_system_event(self, event_type, message):
        if event_type == 'startup':
            icon, title = "🚀", self.t('SISTEMA AVVIATO')
        else:
            icon, title = "🚨", self.t('CRASH DEL MOTORE')
        msg = (
            f"{icon} <b>EXTTO: {title}</b>\n\n"
            f"<code>{message}</code>\n"
        )
        self._send_telegram(msg)
        self._send_email(f"EXTTO Alert: {title}", msg)

    def notify_post_processing(self, title_name, size_bytes, time_sec,
                                action_log, is_series=True, is_processed=False,
                                final_path=None, renamed_to=None):
        """
        Messaggio finale unificato per serie TV: include stats download,
        rinomina e path archiviazione in un unico messaggio.
        Sostituisce notify_torrent_complete per le serie.
        """
        if time_sec <= 0:
            time_sec = 1

        size_str = (f"{size_bytes / (1024**3):.2f} GB"
                    if size_bytes >= 1024**3
                    else f"{size_bytes / (1024**2):.0f} MB")
        m, s = divmod(int(time_sec), 60)
        h, m2 = divmod(m, 60)
        time_str = (f"{h}h {m2}m {s}s" if h > 0 else f"{m}m {s}s")
        speed_mbps = (size_bytes / time_sec) / (1024**2)

        stats_line = f"💾 {size_str}  ⏱️ {time_str}  🚀 {speed_mbps:.1f} MB/s"

        if is_processed:
            # Serie spostata e/o rinominata
            lines = [
                f"✅ <b>{self.t('Download Completato')}</b>",
                f"📺 <b>{title_name}</b>",
                f"",
                stats_line,
            ]
            if renamed_to:
                lines.append(f"✏️ <b>{self.t('Rinominato in')}:</b> <code>{renamed_to}</code>")
            if final_path:
                import os as _os
                # Se final_path è già un file (path completo), lo usiamo direttamente.
                # Altrimenti lo combiniamo con renamed_to per ottenere il percorso completo.
                if _os.path.isfile(final_path):
                    full_file_path = final_path
                elif renamed_to:
                    full_file_path = _os.path.join(final_path, renamed_to)
                else:
                    full_file_path = final_path
                lines.append(f"📂 <b>{self.t('Archiviato in')}:</b> <code>{full_file_path}</code>")
            msg = "\n".join(lines)
        elif is_series:
            # Serie scaricata ma non spostata (nessun archive_path configurato)
            msg = (
                f"✅ <b>{self.t('Download Completato')}</b>\n"
                f"📺 <b>{title_name}</b>\n\n"
                f"{stats_line}\n"
                f"<i>{self.t('File lasciato nella cartella download (Nessuno spostamento configurato).')}</i>"
            )
        else:
            # Film o altro
            msg = (
                f"✅ <b>{self.t('Download Completato')}</b>\n"
                f"🎬 <code>{title_name}</code>\n\n"
                f"{stats_line}"
            )
            if final_path:
                msg += f"\n📂 <b>{self.t('Archiviato in')}:</b> <code>{final_path}</code>"

        self._send_telegram(msg)
        self._send_email(f"✅ EXTTO: {self.t('Completato')} - {title_name}", msg)
