<div align="center">

<img src="dashboard.png" alt="EXTTO Dashboard" width="860"/>

# EXTTO

### *The All-in-One Media Automation System*

**Stop running 5 bloated apps to manage your media.**  
EXTTO is a single, lightweight Python daemon that does everything — from RSS monitoring to Telegram notifications — with a beautiful Web UI you'll actually enjoy using.

[![License: EUPL 1.2](https://img.shields.io/badge/License-EUPL%201.2-blue.svg)](EUPL-1.2%20EN.txt)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-f7c948.svg)](https://www.python.org/)
[![Self-Hosted](https://img.shields.io/badge/Self--Hosted-✓-2ea44f.svg)]()
[![Donate](https://img.shields.io/badge/❤️_Support_EXTTO-PayPal-00457C.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=azanzani@gmail.com&item_name=Support+EXTTO+Project)

**English** · [Italiano ↓](#italiano)

</div>

---

## What is EXTTO?

EXTTO replaces your entire media stack with one self-hosted app.  
No more juggling Sonarr + Radarr + Prowlarr + Mylar + a torrent client + a notification bot.  
Just EXTTO, running quietly on your server, doing everything automatically.

```
RSS feeds → Smart search → Auto-download → Rename & Archive → Telegram notification
```

---

## ✨ Why EXTTO?

| Feature | EXTTO | Sonarr+Radarr+Prowlarr+qBittorrent |
|---|---|---|
| Apps to install | **1** | 4+ |
| RAM usage (idle) | **~80 MB** | ~600 MB+ |
| Comic book support | **✅ Native** | ❌ Needs Mylar |
| eMule / eD2k fallback | **✅ Built-in** | ❌ Not possible |
| RAM disk downloads | **✅ Built-in** | ❌ Not possible |
| Web UI | **✅ Modern, flat** | Mixed |

---

## 🚀 Killer Features

### 🧠 One App, Full Stack
Monitors RSS feeds, searches Jackett/Prowlarr indexers, scores release quality, downloads via libtorrent (embedded) or your existing qBittorrent / Transmission / aria2, renames files with TMDB metadata, archives to your NAS, notifies you. All automatic.

### 💾 RAM Disk Downloads
Protect your SSD. EXTTO downloads small torrents directly to a `tmpfs` RAM disk and moves them to permanent storage only when 100% complete. Zero SSD writes during download.

### 🫏 eMule / eD2k Resurrection
Still hunting that obscure 2003 documentary no one seeds anymore? EXTTO integrates with `amuled` and automatically falls back to the eD2k network when torrents fail. No other media manager does this.

### 📚 Comic Books — First Class
Weekly packs, single issues, automatic monitoring. Downloads directly from GetComics via Mega.nz or torrent. No plugins, no workarounds.

### 🏆 Smart Quality Upgrades
EXTTO scores every release (4K, HDR10, Dolby Vision, DTS-X, codec, source...) and automatically replaces your existing file when a better version is found. Set it once, forget it.

### 🌐 Browser Integration
Click a `magnet:` link or a `.torrent` file anywhere in your browser — EXTTO receives it instantly. One-time setup via a script generated directly from the Web UI, already configured with your server URL.

### 🔒 Privacy First
VPN killswitch that binds all traffic to `tun0`/`wg0`. Automatic IP blocklist updates. Your downloads stay private.

### 🌍 Fully Multilingual
Web UI available in **Italian, English, German, French, Spanish**. Translations managed via YAML files — easy to extend.

---

## 📦 Installation

```bash
git clone https://github.com/buzzqw/extto.git
cd extto
chmod +x setup.sh start.sh
./setup.sh
```

```bash
./setup.sh --upgrade    # force-upgrade all Python packages
```

---

## ▶️ Running EXTTO

**As a systemd service (recommended):**
```bash
sudo systemctl enable extto.service
sudo systemctl start extto.service
```

**Manually:**
```bash
./start.sh              # engine + Web UI on port 5000
./start.sh --tui        # engine + full-screen Terminal UI
```

Open **`http://localhost:5000`** in your browser.

---

## ❤️ Support the Project

EXTTO is free, open-source software built entirely in spare time.  
If it saves you hours of configuration, RAM, or disk wear — consider buying the author a coffee.

Every donation directly funds new features, bug fixes, and keeping the project alive.

<div align="center">

[![Donate with PayPal](https://img.shields.io/badge/Donate-PayPal-00457C?style=for-the-badge&logo=paypal)](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=azanzani@gmail.com&item_name=Support+EXTTO+Project)

*Thank you. Seriously.*

</div>

---

## ⚖️ Legal & Fair Use

EXTTO is a **download automation tool**. It does not host, index, or distribute any copyrighted content.

- EXTTO connects to **indexers you configure** (Jackett, Prowlarr, public RSS feeds). It has no built-in index.
- What you download is **entirely your responsibility**. Use EXTTO only for content you have the right to access — public domain, Creative Commons, or media you own.
- The eMule/eD2k and torrent integrations are neutral technologies. EXTTO does not encourage or facilitate piracy.
- This project is released under the **EUPL 1.2** open-source license.

> *"With great automation comes great responsibility."*

---
---

<a name="italiano"></a>

<div align="center">

# EXTTO

### *Il Sistema di Automazione Media Definitivo*

**Basta tenere accesi 5 programmi pesanti per gestire i tuoi media.**  
EXTTO è un singolo, leggerissimo servizio Python che fa tutto — dal monitoraggio RSS alle notifiche Telegram — con una Web UI moderna che userai volentieri.

</div>

---

## Cos'è EXTTO?

EXTTO sostituisce l'intero stack media con una sola app self-hosted.  
Niente più Sonarr + Radarr + Prowlarr + Mylar + client torrent + bot notifiche.  
Solo EXTTO, in esecuzione silenziosa sul tuo server, che fa tutto in automatico.

```
Feed RSS → Ricerca intelligente → Download automatico → Rinomina & Archivia → Notifica Telegram
```

---

## ✨ Perché EXTTO?

| Funzionalità | EXTTO | Sonarr+Radarr+Prowlarr+qBittorrent |
|---|---|---|
| App da installare | **1** | 4+ |
| RAM a riposo | **~80 MB** | ~600 MB+ |
| Fumetti nativi | **✅ Integrato** | ❌ Serve Mylar |
| Fallback eMule / eD2k | **✅ Integrato** | ❌ Impossibile |
| Download in RAM disk | **✅ Integrato** | ❌ Impossibile |
| Web UI | **✅ Moderna, flat** | Variabile |

---

## 🚀 Le Funzionalità Che Fanno la Differenza

### 🧠 Un'App, lo Stack Completo
Monitora feed RSS, cerca su indexer Jackett/Prowlarr, valuta la qualità delle release, scarica via libtorrent (integrato) oppure qBittorrent / Transmission / aria2 esistenti, rinomina con metadati TMDB, archivia sul NAS, notifica su Telegram. Tutto automatico.

### 💾 Download in RAM Disk
Proteggi il tuo SSD. EXTTO scarica i torrent piccoli direttamente su un RAM disk `tmpfs` e li sposta in archivio solo a completamento al 100%. Zero scritture SSD durante il download.

### 🫏 La Rinascita di eMule / eD2k
Cerchi quel documentario oscuro del 2003 che nessuno seedca più? EXTTO si integra con `amuled` e cade automaticamente sulla rete eD2k quando i torrent falliscono. Nessun altro media manager lo fa.

### 📚 Fumetti — Supporto Nativo
Weekly pack, numeri singoli, monitoraggio automatico. Download direttamente da GetComics via Mega.nz o torrent. Senza plugin, senza workaround.

### 🏆 Upgrade Qualità Intelligente
EXTTO valuta ogni release (4K, HDR10, Dolby Vision, DTS-X, codec, sorgente...) e sostituisce automaticamente il file esistente quando trova una versione migliore. Configuralo una volta, dimenticatelo.

### 🌐 Integrazione Browser
Clicca su un link `magnet:` o su un file `.torrent` ovunque nel browser — EXTTO lo riceve istantaneamente. Setup unico via uno script generato direttamente dalla Web UI, già configurato con l'URL del tuo server.

### 🔒 Privacy Prima di Tutto
VPN killswitch che vincola il traffico a `tun0`/`wg0`. Aggiornamento automatico delle IP blocklist. I tuoi download restano privati.

### 🌍 Completamente Multilingua
Web UI disponibile in **Italiano, Inglese, Tedesco, Francese, Spagnolo**. Traduzioni gestite via file YAML — facile da estendere.

---

## 📦 Installazione

```bash
git clone https://github.com/buzzqw/extto.git
cd extto
chmod +x setup.sh start.sh
./setup.sh
```

```bash
./setup.sh --upgrade    # forza l'aggiornamento dei pacchetti Python
```

---

## ▶️ Avvio

**Come servizio systemd (raccomandato):**
```bash
sudo systemctl enable extto.service
sudo systemctl start extto.service
```

**Manuale:**
```bash
./start.sh              # motore + Web UI sulla porta 5000
./start.sh --tui        # motore + interfaccia da Terminale a schermo intero
```

Apri **`http://localhost:5000`** nel browser.

---

## ❤️ Supporta il Progetto

EXTTO è software libero e open-source, costruito interamente nel tempo libero.  
Se ti fa risparmiare ore di configurazione, RAM, o usura del disco — considera di offrire un caffè all'autore.

Ogni donazione finanzia direttamente nuove funzionalità, correzioni di bug e la sopravvivenza del progetto.

<div align="center">

[![Dona con PayPal](https://img.shields.io/badge/Dona-PayPal-00457C?style=for-the-badge&logo=paypal)](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=azanzani@gmail.com&item_name=Support+EXTTO+Project)

*Grazie. Sul serio.*

</div>

---

## ⚖️ Uso Lecito & Responsabilità

EXTTO è uno **strumento di automazione dei download**. Non ospita, non indicizza e non distribuisce alcun contenuto protetto da copyright.

- EXTTO si connette agli **indexer che configuri tu** (Jackett, Prowlarr, feed RSS pubblici). Non ha un indice integrato.
- Ciò che scarichi è **interamente sotto la tua responsabilità**. Usa EXTTO solo per contenuti che hai il diritto di accedere — dominio pubblico, licenze Creative Commons, o media di tua proprietà.
- Le integrazioni eMule/eD2k e torrent sono tecnologie neutrali. EXTTO non incoraggia né facilita la pirateria.
- Questo progetto è rilasciato sotto licenza open-source **EUPL 1.2**.

> *"Con grande automazione viene grande responsabilità."*
