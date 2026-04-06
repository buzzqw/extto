# EXTTO — Handler Magnet e Torrent per Browser

Intercetta i click su link `magnet:` e file `.torrent` in Firefox, Chrome, Chromium e Vivaldi e li invia automaticamente a EXTTO.

---

## File inclusi

| File | Descrizione |
|------|-------------|
| `install-extto-handlers.sh` | Script di installazione (lancia questo) |
| `extto-magnet` | Handler per link `magnet:` |
| `extto-torrent` | Handler per file `.torrent` e URL `.torrent` |
| `extto-magnet.desktop` | Registrazione MIME per i magnet |
| `extto-torrent.desktop` | Registrazione MIME per i .torrent |

---

## Requisiti

```bash
sudo apt install curl python3 xdg-utils libnotify-bin
```

`libnotify-bin` è opzionale — serve solo per le notifiche desktop dopo ogni invio.

---

## Installazione

Metti tutti e 5 i file nella stessa directory, poi:

```bash
chmod +x install-extto-handlers.sh
./install-extto-handlers.sh
```

Se EXTTO gira su un host o porta diversa da `localhost:5000`:

```bash
./install-extto-handlers.sh http://192.168.1.100:5000
```

Lo script:
- Copia `extto-magnet` e `extto-torrent` in `/usr/local/bin/`
- Installa i `.desktop` in `~/.local/share/applications/`
- Registra i tipi MIME `x-scheme-handler/magnet` e `application/x-bittorrent`
- Configura Firefox per delegare `magnet:` al sistema

> **Riavvia i browser** dopo l'installazione.

---

## Come funziona

**Click su un link `magnet:`**
→ il browser chiama `extto-magnet` via `xdg-open`
→ lo script invia `POST /api/send-magnet` a EXTTO
→ notifica desktop con esito

**Click su un link `.torrent`** (URL o file scaricato)
→ il browser chiama `extto-torrent` via `xdg-open`
→ se è un URL: scarica il file, lo codifica in base64, invia `POST /api/upload-torrent`
→ se è un file locale: legge il file direttamente e lo invia
→ se il link `.torrent` redirige a un magnet: lo gestisce come magnet automaticamente

---

## Configurazione Firefox

Firefox a volte mostra un popup di conferma la prima volta che clicchi un magnet. Seleziona **"Usa sempre questa applicazione"** per non chiedere più.

In alternativa puoi configurarlo manualmente:

1. Apri `about:config`
2. Cerca `network.protocol-handler.expose.magnet` → impostalo a `false`
3. Cerca `network.protocol-handler.external.magnet` → impostalo a `true`

---

## Cambiare l'URL di EXTTO dopo l'installazione

```bash
sudo nano /usr/local/bin/extto-magnet
sudo nano /usr/local/bin/extto-torrent
```

Modifica la riga:
```bash
EXTTO_URL="${EXTTO_URL:-http://localhost:5000}"
```

Oppure esporta la variabile d'ambiente nel tuo `.bashrc` / `.profile`:
```bash
export EXTTO_URL="http://192.168.1.100:5000"
```

---

## Disinstallazione

```bash
sudo rm /usr/local/bin/extto-magnet /usr/local/bin/extto-torrent
rm ~/.local/share/applications/extto-magnet.desktop
rm ~/.local/share/applications/extto-torrent.desktop
update-desktop-database ~/.local/share/applications
```

---

## Troubleshooting

**Il click non fa nulla / apre il client torrent di sistema**
Verifica che la registrazione MIME sia attiva:
```bash
xdg-mime query default x-scheme-handler/magnet
# deve rispondere: extto-magnet.desktop

xdg-mime query default application/x-bittorrent
# deve rispondere: extto-torrent.desktop
```
Se non è corretto, rilancia lo script di installazione.

**"EXTTO non raggiungibile"**
Verifica che EXTTO sia in esecuzione e che l'URL nella variabile `EXTTO_URL` sia corretto:
```bash
curl http://localhost:5000/api/torrent-tags
```

**Le notifiche desktop non appaiono**
Installa `libnotify-bin`:
```bash
sudo apt install libnotify-bin
```

**Firefox ignora la configurazione**
Chiudi completamente Firefox prima di modificare `prefs.js`. Il file viene riscritto all'avvio se il browser è aperto.

**Vivaldi / Chrome non intercettano i .torrent**
Controlla le impostazioni del browser in `Impostazioni → Download` — assicurati che non sia impostato "Apri sempre questo tipo di file" per un'altra applicazione.
