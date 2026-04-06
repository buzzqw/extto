#!/bin/bash
# ============================================================
# EXTTO — Installazione handler magnet: e .torrent
# Compatibile con: Firefox, Chrome, Chromium, Vivaldi
# Requisiti: curl, python3, xdg-utils, libnotify (notify-send)
# ============================================================

set -e

EXTTO_URL="${1:-http://localhost:5000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "╔══════════════════════════════════════════════╗"
echo "║  EXTTO — Installazione handler torrent/magnet ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "▶ URL EXTTO: $EXTTO_URL"
echo ""

# ── Verifica dipendenze ──────────────────────────────────────
echo "→ Verifica dipendenze..."
for cmd in curl python3 xdg-mime xdg-open; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  ✗ Mancante: $cmd"
        echo "    Installa con: sudo apt install ${cmd/xdg-mime/xdg-utils}"
        exit 1
    fi
    echo "  ✓ $cmd"
done

# notify-send è opzionale
if command -v notify-send &>/dev/null; then
    echo "  ✓ notify-send (notifiche desktop attive)"
else
    echo "  ⚠ notify-send non trovato — le notifiche desktop saranno silenti"
    echo "    Installa con: sudo apt install libnotify-bin"
fi
echo ""

# ── Copia script in /usr/local/bin ──────────────────────────
echo "→ Installazione script in /usr/local/bin ..."

# Verifica se abbiamo i file nella stessa directory dello script
for f in extto-magnet extto-torrent; do
    if [[ -f "$SCRIPT_DIR/$f" ]]; then
        sudo cp "$SCRIPT_DIR/$f" "/usr/local/bin/$f"
    else
        echo "  ✗ File non trovato: $SCRIPT_DIR/$f"
        echo "    Assicurati che extto-magnet e extto-torrent siano nella stessa directory"
        exit 1
    fi
    sudo chmod +x "/usr/local/bin/$f"
    echo "  ✓ /usr/local/bin/$f"
done

# Imposta URL EXTTO personalizzato se diverso dal default
if [[ "$EXTTO_URL" != "http://localhost:5000" ]]; then
    sudo sed -i "s|EXTTO_URL:-http://localhost:5000|EXTTO_URL:-$EXTTO_URL|g" \
        /usr/local/bin/extto-magnet \
        /usr/local/bin/extto-torrent
    echo "  ✓ URL impostato: $EXTTO_URL"
fi
echo ""

# ── Installa file .desktop ────────────────────────────────────
echo "→ Installazione file .desktop ..."
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

for f in extto-magnet.desktop extto-torrent.desktop; do
    if [[ -f "$SCRIPT_DIR/$f" ]]; then
        cp "$SCRIPT_DIR/$f" "$DESKTOP_DIR/$f"
        echo "  ✓ $DESKTOP_DIR/$f"
    else
        echo "  ✗ File non trovato: $SCRIPT_DIR/$f"
        exit 1
    fi
done
echo ""

# ── Registra handler MIME ─────────────────────────────────────
echo "→ Registrazione handler MIME ..."

# Handler magnet:
xdg-mime default extto-magnet.desktop x-scheme-handler/magnet
echo "  ✓ x-scheme-handler/magnet → extto-magnet"

# Handler .torrent
xdg-mime default extto-torrent.desktop application/x-bittorrent
echo "  ✓ application/x-bittorrent → extto-torrent"

# Aggiorna database applicazioni
update-desktop-database "$DESKTOP_DIR" 2>/dev/null && echo "  ✓ database applicazioni aggiornato"
echo ""

# ── Configurazione specifica per browser ─────────────────────
echo "→ Configurazione browser ..."

# Firefox: imposta network.protocol-handler.expose.magnet = false
# così chiede (o usa il sistema) invece di ignorarlo
FIREFOX_PROFILES=$(find "$HOME/.mozilla/firefox" -name "prefs.js" 2>/dev/null)
if [[ -n "$FIREFOX_PROFILES" ]]; then
    for PREFS in $FIREFOX_PROFILES; do
        # Rimuovi eventuali impostazioni precedenti
        sed -i '/network.protocol-handler.expose.magnet/d' "$PREFS"
        sed -i '/network.protocol-handler.app.magnet/d' "$PREFS"
        # Aggiungi impostazione corretta
        echo 'user_pref("network.protocol-handler.expose.magnet", false);' >> "$PREFS"
        echo 'user_pref("network.protocol-handler.external.magnet", true);' >> "$PREFS"
        echo "  ✓ Firefox: $PREFS"
    done
else
    echo "  ⚠ Firefox: nessun profilo trovato (normale se non è installato)"
fi

# Chrome/Chromium/Vivaldi: usano xdg-open automaticamente — nessuna config necessaria
for browser in google-chrome chromium vivaldi; do
    if command -v "$browser" &>/dev/null; then
        echo "  ✓ $browser: usa xdg-open automaticamente"
    fi
done
echo ""

# ── Test connessione EXTTO ────────────────────────────────────
echo "→ Test connessione EXTTO ($EXTTO_URL) ..."
if curl -s --max-time 3 "$EXTTO_URL/api/torrent-tags" &>/dev/null; then
    echo "  ✓ EXTTO raggiungibile"
else
    echo "  ⚠ EXTTO non raggiungibile — assicurati che sia in esecuzione prima di usare gli handler"
fi
echo ""

# ── Riepilogo ─────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════╗"
echo "║  Installazione completata!                    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Come usare:"
echo "  • Clicca su un link magnet: → inviato automaticamente a EXTTO"
echo "  • Clicca su un .torrent:    → inviato automaticamente a EXTTO"
echo ""
echo "Disinstallazione:"
echo "  sudo rm /usr/local/bin/extto-magnet /usr/local/bin/extto-torrent"
echo "  rm ~/.local/share/applications/extto-magnet.desktop"
echo "  rm ~/.local/share/applications/extto-torrent.desktop"
echo "  update-desktop-database ~/.local/share/applications"
echo ""
echo "Cambiare URL EXTTO in futuro:"
echo "  sudo nano /usr/local/bin/extto-magnet   # modifica riga EXTTO_URL"
echo "  sudo nano /usr/local/bin/extto-torrent  # modifica riga EXTTO_URL"
echo ""
echo "⚠  Riavvia i browser aperti per applicare le modifiche."
