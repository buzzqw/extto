# EXTTO — Media Automation System

> **English** | [Italiano](#italiano)

---

EXTTO is a self-hosted media automation system for Linux. It monitors RSS/torrent feeds, automatically downloads TV episodes and movies, archives them, and notifies you via Telegram — all from a web interface or a full-screen TUI.

## Features

- **Multi-client torrent support**: embedded libtorrent, qBittorrent, Transmission
- **eMule/ed2k network**: integrated aMule daemon via EC protocol
- **Indexer support**: Jackett and Prowlarr
- **Smart renaming**: configurable formats with technical tags (resolution, codec, HDR, audio, languages) via pymediainfo
- **Quality scoring**: automatic upgrade of existing files when a better version is found
- **TMDB integration**: season/episode metadata, episode titles for renaming
- **Trakt.tv integration**: watchlist sync, calendar, scrobbling via OAuth2 Device Flow
- **Telegram notifications**: cycle reports, download events, health alerts
- **Web UI**: responsive Flask interface with multilingual support (IT, EN, DE, FR, ES)
- **TUI**: full-screen terminal interface (Textual framework)
- **Health monitor**: disk space, indexer status, system stats, folder permissions
- **Comics support**: download from Mega.nz links
- **Systemd ready**: ships with `.service` files for engine and aMule daemon

## Requirements

- Linux (Debian/Ubuntu, Arch, Fedora/RHEL, openSUSE)
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (installed automatically by `setup.sh`)

Optional system packages:
- `libmediainfo` — for advanced technical tag extraction
- `amuled` — for ed2k/eMule network support

## Installation

```bash
git clone https://github.com/buzzqw/extto.git
cd extto
chmod +x setup.sh start.sh
./setup.sh
```

Options:
```bash
./setup.sh --no-libtorrent   # skip embedded libtorrent
./setup.sh --no-mediainfo    # skip libmediainfo/pymediainfo
./setup.sh --no-amule        # skip amuled installation
./setup.sh --upgrade         # force upgrade of Python packages
```

## Usage

```bash
./start.sh              # start engine + web UI (default port 8889)
./start.sh --tui        # start with full-screen TUI
```

The web interface is available at `http://localhost:8889`.

## Configuration

On first run, configure EXTTO from the web UI:

- Torrent client (libtorrent / qBittorrent / Transmission)
- Jackett or Prowlarr URL and API key
- Archive path(s) on your NAS
- TMDB API key (free at [themoviedb.org](https://www.themoviedb.org/settings/api))
- Telegram bot token and chat ID
- Trakt.tv client credentials (optional)

## Project Structure

```
extto/
├── extto3.py           # Main engine
├── extto_web.py        # Flask web UI
├── extto_ui.py         # UI helpers
├── core/
│   ├── engine.py       # Download cycle logic
│   ├── config.py       # Configuration parser
│   ├── database.py     # SQLite database layer
│   ├── models.py       # Parser, quality scoring
│   ├── renamer.py      # File renaming logic
│   ├── cleaner.py      # Duplicate/upgrade cleanup
│   ├── tagger.py       # qBittorrent tag management
│   ├── notifier.py     # Telegram notifications
│   ├── tmdb.py         # TMDB API client
│   ├── trakt.py        # Trakt.tv client
│   ├── health.py       # System health monitor
│   ├── mediainfo_helper.py  # Technical tag extraction
│   └── clients/
│       ├── libtorrent.py
│       ├── qbittorrent.py
│       ├── transmission.py
│       ├── amule.py
│       └── aria2.py
├── languages/          # UI translations (it, en, de, fr, es)
├── static/             # CSS, JS, games
├── templates/          # HTML templates
├── setup.sh            # Dependency installer
├── start.sh            # Launcher
└── requirements.txt
```

## License

Licensed under the [European Union Public Licence 1.2 (EUPL-1.2)](EUPL-1.2%20EN.txt).

## Support the Project

If you find EXTTO useful, consider supporting its development with a donation via PayPal:

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://www.paypal.com/paypalme/azanzani)

Or send directly to: **azanzani@gmail.com**

---

<a name="italiano"></a>

# EXTTO — Sistema di Automazione Media

> [English](#extto--media-automation-system) | **Italiano**

---

EXTTO è un sistema di automazione media self-hosted per Linux. Monitora feed RSS e torrent, scarica automaticamente episodi TV e film, li archivia e ti notifica via Telegram — tutto dalla web interface o da una TUI a schermo intero.

## Funzionalità

- **Client torrent multipli**: libtorrent integrato, qBittorrent, Transmission
- **Rete eMule/ed2k**: integrazione con daemon aMule via protocollo EC
- **Supporto indexer**: Jackett e Prowlarr
- **Rinomina avanzata**: formati configurabili con tag tecnici (risoluzione, codec, HDR, audio, lingue) tramite pymediainfo
- **Scoring qualità**: upgrade automatico dei file esistenti quando viene trovata una versione migliore
- **Integrazione TMDB**: metadati stagioni/episodi, titoli episodi per la rinomina
- **Integrazione Trakt.tv**: sincronizzazione watchlist, calendario, scrobbling via OAuth2 Device Flow
- **Notifiche Telegram**: report ciclo, eventi download, alert di salute
- **Web UI**: interfaccia Flask responsive con supporto multilingua (IT, EN, DE, FR, ES)
- **TUI**: interfaccia terminale a schermo intero (framework Textual)
- **Monitor salute**: spazio disco, stato indexer, statistiche sistema, permessi cartelle
- **Supporto fumetti**: download da link Mega.nz
- **Pronto per systemd**: include file `.service` per il motore e il daemon aMule

## Requisiti

- Linux (Debian/Ubuntu, Arch, Fedora/RHEL, openSUSE)
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (installato automaticamente da `setup.sh`)

Pacchetti di sistema opzionali:
- `libmediainfo` — per l'estrazione avanzata di tag tecnici dai file video
- `amuled` — per il supporto alla rete ed2k/eMule

## Installazione

```bash
git clone https://github.com/buzzqw/extto.git
cd extto
chmod +x setup.sh start.sh
./setup.sh
```

Opzioni disponibili:
```bash
./setup.sh --no-libtorrent   # salta libtorrent integrato
./setup.sh --no-mediainfo    # salta libmediainfo/pymediainfo
./setup.sh --no-amule        # salta installazione amuled
./setup.sh --upgrade         # forza aggiornamento pacchetti Python
```

## Avvio

```bash
./start.sh              # avvia motore + web UI (porta predefinita 8889)
./start.sh --tui        # avvia con TUI a schermo intero
```

L'interfaccia web è disponibile su `http://localhost:8889`.

## Configurazione

Al primo avvio, configura EXTTO dalla web UI:

- Client torrent (libtorrent / qBittorrent / Transmission)
- URL e API key di Jackett o Prowlarr
- Percorso/i archivio sul NAS
- API key TMDB (gratuita su [themoviedb.org](https://www.themoviedb.org/settings/api))
- Token bot Telegram e chat ID
- Credenziali Trakt.tv (opzionale)

## Struttura del Progetto

```
extto/
├── extto3.py           # Motore principale
├── extto_web.py        # Web UI Flask
├── extto_ui.py         # Helper UI
├── core/
│   ├── engine.py       # Logica ciclo download
│   ├── config.py       # Parser configurazione
│   ├── database.py     # Layer SQLite
│   ├── models.py       # Parser, scoring qualità
│   ├── renamer.py      # Logica rinomina file
│   ├── cleaner.py      # Pulizia duplicati/upgrade
│   ├── tagger.py       # Gestione tag qBittorrent
│   ├── notifier.py     # Notifiche Telegram
│   ├── tmdb.py         # Client API TMDB
│   ├── trakt.py        # Client Trakt.tv
│   ├── health.py       # Monitor salute sistema
│   ├── mediainfo_helper.py  # Estrazione tag tecnici
│   └── clients/
│       ├── libtorrent.py
│       ├── qbittorrent.py
│       ├── transmission.py
│       ├── amule.py
│       └── aria2.py
├── languages/          # Traduzioni UI (it, en, de, fr, es)
├── static/             # CSS, JS, giochi
├── templates/          # Template HTML
├── setup.sh            # Installatore dipendenze
├── start.sh            # Launcher
└── requirements.txt
```

## Licenza

Rilasciato sotto [European Union Public Licence 1.2 (EUPL-1.2)](EUPL-1.2%20EN.txt).

## Supporta il Progetto

Se EXTTO ti è utile, considera di supportarne lo sviluppo con una donazione via PayPal:

[![Dona con PayPal](https://img.shields.io/badge/Dona-PayPal-blue.svg)](https://www.paypal.com/paypalme/azanzani)

Oppure invia direttamente a: **azanzani@gmail.com**
