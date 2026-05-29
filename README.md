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
| Multi-engine web search | **✅ 6 engines** | ❌ Indexer only |
| Cloudflare bypass | **✅ FlareSolverr** | ❌ Not possible |
| Tag-based folder routing | **✅ Built-in** | ❌ Manual |
| Webhook notifications | **✅ Built-in** | ❌ Via plugins only |
| Smart gap-fill cooldown | **✅ 24 h per episode** | ❌ Retries every cycle |
| Web UI | **✅ Modern, flat** | Mixed |

---

## 🚀 Killer Features

### 🧠 One App, Full Stack
Monitors RSS feeds, searches Jackett/Prowlarr indexers, scores release quality, downloads via libtorrent (embedded) or your existing qBittorrent / Transmission / aria2, renames files with TMDB metadata, archives to your NAS, notifies you. All automatic.

### 🌐 6-Engine Web Search with Cloudflare Bypass
When your indexers come up empty, EXTTO fans out across **6 public search engines** simultaneously:  
**BitSearch · The Pirate Bay · Knaben · BTDigg · LimeTorrents · Torrentz2**

Engines protected by Cloudflare are bypassed transparently via the optional **FlareSolverr** sidecar. Web search fires both in scheduled cycles and in manually triggered ones — so when you hit "Search Now" you get the full search network, not just RSS.

### 🏷️ Tag-Based Folder Rules
Assign a tag to any download (automatically or at the moment you add a magnet/torrent) and EXTTO routes the completed file to the matching folder. Series go to `/media/tv`, documentaries to `/media/docs`, comics to `/media/comics` — zero manual sorting.

### 💾 RAM Disk Downloads
Protect your SSD. EXTTO downloads small torrents directly to a `tmpfs` RAM disk and moves them to permanent storage only when 100% complete. Zero SSD writes during download.

### 🫏 eMule / eD2k Resurrection
Still hunting that obscure 2003 documentary no one seeds anymore? EXTTO integrates with `amuled` and automatically falls back to the eD2k network when torrents fail. No other media manager does this.

### 📚 Comic Books — First Class
Weekly packs, single issues, automatic monitoring. Downloads directly from GetComics via Mega.nz or torrent. No plugins, no workarounds.

### 🏆 Smart Quality Upgrades
EXTTO scores every release (4K, HDR10, Dolby Vision, DTS-X, codec, source...) and automatically replaces your existing file when a better version is found. Set it once, forget it.

### 🎛️ Modern Web UI — Accordion, Tabs & Live Dirty Tracking
The settings page is organized in collapsible accordion sections (state saved in localStorage) with a dedicated **Integrations tab** for Trakt and Jellyfin. A red dot appears on any tab with unsaved changes so you never lose edits by accident.

### 🔇 Category Filter (beyond Blacklist)
Two levels of filtering: the **Blacklist** blocks at parse time but still archives the item for deduplication. The **Category Filter** blocks *and* prevents archiving entirely — those items don't exist for EXTTO. Perfect for permanently ignoring cam-rips, CAM-quality, or entire release groups.

### 🌐 Browser Integration
Click a `magnet:` link or a `.torrent` file anywhere in your browser — EXTTO receives it instantly. One-time setup via a script generated directly from the Web UI, already configured with your server URL.

### 🔔 Webhook Notifications
Connect EXTTO to any external service — **ntfy, Gotify, n8n, Home Assistant, Make, Zapier** — with a single URL.  
Every download, upgrade, gap-fill, and system event fires a signed JSON POST to your endpoint.  
Optional HMAC-SHA256 signature for secure verification. No plugins, no extra setup.

```
notify_webhook_url    = https://ntfy.sh/my-channel
notify_webhook_secret = mysecret     # optional, signs payloads
```

### 🔍 Smart Gap-Fill with 24 h Cooldown
The gap-filler is smarter now. For each missing episode it tracks the last time a live indexer search was performed.  
The local archive is always scanned (free), but Jackett and web-search queries fire **at most once per 24 hours** per episode — no more hammering your indexers for the same buco every 6-hour cycle.  
A configurable `gap_fill_max_per_series` cap prevents a series with many gaps from monopolising search slots.

### 🏷️ Alias-Aware RSS Matching
When an Italian RSS feed publishes a release under an alternative title (e.g. *"Agenti di S.H.I.E.L.D."* instead of the configured *"Agents of S.H.I.E.L.D."*), EXTTO now recognises it automatically.  
Aliases defined in the series config are checked at every stage — RSS parsing, deduplication, feed-match tracking, and gap-fill search queries.

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

## 🎬 Movie Configuration Tips

Getting the right movie — and not a wrong one with a similar title — depends on how you configure it.

| Field | Recommendation |
|-------|----------------|
| **Name** | Use at least 2 distinctive words from the title |
| **Year** | **Always set it.** Without a year the first title match wins — risky for short or generic names |
| **Exclude** | Comma-separated words that must NOT appear: e.g. `extended,directors cut,romulus` — use this to separate a sequel from its original |

**How matching works:** EXTTO checks that every word in your configured name appears (word-boundary match) in the full RSS release title. If a year is set, the release year must be within ±1. The `exclude` list vetoes the match if any of those words appear.

**Safe config example:**
```
Name:    Gladiator II
Year:    2024
Exclude: (empty)
```

**Risky config (will match any release containing "alien"):**
```
Name:    Alien
Year:    (empty)
```

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
| Ricerca web multi-motore | **✅ 6 motori** | ❌ Solo indexer |
| Bypass Cloudflare | **✅ FlareSolverr** | ❌ Impossibile |
| Cartelle per tag | **✅ Integrato** | ❌ Manuale |
| Notifiche webhook | **✅ Integrato** | ❌ Solo tramite plugin |
| Gap fill con cooldown | **✅ 24 h per episodio** | ❌ Riprova ad ogni ciclo |
| Web UI | **✅ Moderna, flat** | Variabile |

---

## 🚀 Le Funzionalità Che Fanno la Differenza

### 🧠 Un'App, lo Stack Completo
Monitora feed RSS, cerca su indexer Jackett/Prowlarr, valuta la qualità delle release, scarica via libtorrent (integrato) oppure qBittorrent / Transmission / aria2 esistenti, rinomina con metadati TMDB, archivia sul NAS, notifica su Telegram. Tutto automatico.

### 🌐 Ricerca Web su 6 Motori con Bypass Cloudflare
Quando gli indexer non trovano nulla, EXTTO espande la ricerca su **6 motori pubblici** in parallelo:  
**BitSearch · The Pirate Bay · Knaben · BTDigg · LimeTorrents · Torrentz2**

I motori protetti da Cloudflare vengono attraversati in modo trasparente tramite il sidecar opzionale **FlareSolverr**. La ricerca web scatta sia nei cicli schedulati sia in quelli avviati manualmente — quindi premendo "Controlla ora" si ottiene l'intera rete di ricerca, non solo RSS.

### 🏷️ Regole Cartelle per Tag
Assegna un tag a qualsiasi download (automaticamente o al momento in cui aggiungi un magnet/torrent) e EXTTO instrada il file completato nella cartella corrispondente. Serie in `/media/tv`, documentari in `/media/docs`, fumetti in `/media/comics` — zero ordinamento manuale.

### 💾 Download in RAM Disk
Proteggi il tuo SSD. EXTTO scarica i torrent piccoli direttamente su un RAM disk `tmpfs` e li sposta in archivio solo a completamento al 100%. Zero scritture SSD durante il download.

### 🫏 La Rinascita di eMule / eD2k
Cerchi quel documentario oscuro del 2003 che nessuno seedca più? EXTTO si integra con `amuled` e cade automaticamente sulla rete eD2k quando i torrent falliscono. Nessun altro media manager lo fa.

### 📚 Fumetti — Supporto Nativo
Weekly pack, numeri singoli, monitoraggio automatico. Download direttamente da GetComics via Mega.nz o torrent. Senza plugin, senza workaround.

### 🏆 Upgrade Qualità Intelligente
EXTTO valuta ogni release (4K, HDR10, Dolby Vision, DTS-X, codec, sorgente...) e sostituisce automaticamente il file esistente quando trova una versione migliore. Configuralo una volta, dimenticatelo.

### 🎛️ Web UI Moderna — Accordion, Tab e Dirty Tracking
La pagina impostazioni è organizzata in sezioni accordion collassabili (stato salvato in localStorage) con un tab dedicato **Integrazioni** per Trakt e Jellyfin. Un pallino rosso compare su ogni tab con modifiche non salvate, così non perdi mai le impostazioni per errore.

### 🔇 Filtro Categorie (oltre la Blacklist)
Due livelli di filtraggio: la **Blacklist** blocca al parsing ma archivia comunque l'item per deduplicazione. Il **Filtro Categorie** blocca *e* impedisce l'archiviazione — quegli item non esistono per EXTTO. Perfetto per ignorare definitivamente cam-rip, qualità CAM o interi release group.

### 🌐 Integrazione Browser
Clicca su un link `magnet:` o su un file `.torrent` ovunque nel browser — EXTTO lo riceve istantaneamente. Setup unico via uno script generato direttamente dalla Web UI, già configurato con l'URL del tuo server.

### 🔔 Notifiche Webhook
Collega EXTTO a qualsiasi servizio esterno — **ntfy, Gotify, n8n, Home Assistant, Make, Zapier** — con un solo URL.  
Ogni download, upgrade, gap-fill ed evento di sistema invia un POST JSON firmato al tuo endpoint.  
Firma HMAC-SHA256 opzionale per la verifica sicura. Nessun plugin, nessuna configurazione aggiuntiva.

```
notify_webhook_url    = https://ntfy.sh/mio-canale
notify_webhook_secret = segreto     # opzionale, firma i payload
```

### 🔍 Gap Fill Intelligente con Cooldown 24 h
Il gap filler è diventato più intelligente. Per ogni episodio mancante tiene traccia dell'ultima volta che è stata eseguita una ricerca live su un indexer.  
L'archivio locale viene sempre interrogato (è gratis), ma le query a Jackett e ai motori web vengono eseguite **al massimo una volta ogni 24 ore** per episodio — fine allo spam degli indexer ad ogni ciclo di 6 ore.  
Un parametro `gap_fill_max_per_series` configurabile impedisce a una serie con molti buchi di monopolizzare tutti gli slot di ricerca.

### 🏷️ Matching RSS con Alias
Quando un feed RSS italiano pubblica una release con un titolo alternativo (es. *"Agenti di S.H.I.E.L.D."* invece del nome configurato *"Agents of S.H.I.E.L.D."*), EXTTO ora la riconosce automaticamente.  
Gli alias definiti nella configurazione della serie vengono controllati in ogni fase — parsing RSS, deduplicazione, tracciamento feed-match e query di gap-fill.

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

## 🎬 Configurazione Film: Come Evitare Match Errati

Ottenere il film giusto — e non uno con un titolo simile — dipende da come lo configuri.

| Campo | Raccomandazione |
|-------|----------------|
| **Nome** | Usa almeno 2 parole caratterizzanti del titolo |
| **Anno** | **Impostalo sempre.** Senza anno vince il primo match per nome — pericoloso per titoli corti o generici |
| **Escludi** | Parole separate da virgola che NON devono comparire nel titolo: es. `extended,directors cut,romulus` — usalo per separare un sequel dall'originale |

**Come funziona il matching:** EXTTO verifica che ogni parola del nome configurato sia presente (word-boundary) nel titolo RSS completo. Se l'anno è impostato, l'anno della release deve corrispondere ±1. La lista `Escludi` veta il match se una di quelle parole compare.

**Config sicura:**
```
Nome:    Il Gladiatore II
Anno:    2024
Escludi: (vuoto)
```

**Config rischiosa (cattura qualsiasi release con "alien"):**
```
Nome:    Alien
Anno:    (vuoto)
```

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
