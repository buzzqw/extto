#!/usr/bin/env bash
# ============================================================================
# start.sh — Prepara l'ambiente e avvia EXTTO
#
# Architettura:
#   1. Impedisce l'esecuzione come root.
#   2. Verifica che l'ambiente Python (.venv) e le dipendenze siano pronti.
#   3. Se manca qualcosa, avvia automaticamente ./setup.sh.
#   4. Chiede se installare/avviare il servizio systemd (background).
#   5. Avvia l'applicazione (foreground).
# ============================================================================

set -euo pipefail

# --- Colori ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERR ]${NC}  $*" >&2; }
section() { echo -e "\n${BOLD}$*${NC}"; }

# ============================================================================
# Sicurezza: Mai eseguire l'intero script come root!
# ============================================================================
if [[ $EUID -eq 0 ]]; then
    error "Non eseguire questo script come root (non usare sudo ./start.sh)!"
    error "Avvialo come utente normale. Il sistema chiederà sudo solo se necessario."
    exit 1
fi

# --- Percorsi e Opzioni ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${EXTTO_VENV:-$SCRIPT_DIR/.venv}"
VENV_PY="$VENV_DIR/bin/python"
EXTTO_PY="${EXTTO_SCRIPT:-$SCRIPT_DIR/extto3.py}"

MODE="engine"
PORT="5000" # Porta predefinita della WebUI

for arg in "$@"; do
    case "$arg" in
        --web-only)  MODE="web" ;;
        --tui)       MODE="tui" ;;
        --check)     MODE="check" ;;
    esac
done

# ============================================================================
section "── 1. Verifica Ambiente (Auto-Setup) ──────────────────────────"
# ============================================================================
NEEDS_SETUP=false

# Controlla se il venv esiste ed è funzionante
if [[ ! -f "$VENV_PY" ]]; then
    warn "Ambiente virtuale non trovato."
    NEEDS_SETUP=true
elif ! "$VENV_PY" -c "import flask" &>/dev/null; then
    warn "Dipendenze base (es. flask) mancanti nel venv."
    NEEDS_SETUP=true
fi

# Se l'ambiente non è pronto, chiama setup.sh
if $NEEDS_SETUP; then
    info "L'ambiente non è pronto. Avvio automatico di setup.sh..."
    echo -e "${YELLOW}------------------------------------------------------------${NC}"
    
    if [[ -x "$SCRIPT_DIR/setup.sh" ]]; then
        bash "$SCRIPT_DIR/setup.sh"
    else
        error "Impossibile trovare o eseguire $SCRIPT_DIR/setup.sh"
        exit 1
    fi
    
    echo -e "${YELLOW}------------------------------------------------------------${NC}"
    info "Ritorno a start.sh..."
    
    # Ricontrolla dopo il setup
    if [[ ! -f "$VENV_PY" ]] || ! "$VENV_PY" -c "import flask" &>/dev/null; then
        error "Il setup non è andato a buon fine. Impossibile avviare EXTTO."
        exit 1
    fi
fi

ok "Ambiente virtuale e dipendenze pronti: $("$VENV_PY" --version)"

# --- Ottieni IP Locale ---
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$LOCAL_IP" ]] && LOCAL_IP="localhost"

# ============================================================================
section "── 2. Configurazione Servizio Systemd ──────────────────────────"
# ============================================================================
SERVICE_NAME="extto.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

if [[ "$MODE" == "engine" ]] && command -v systemctl &>/dev/null; then
    if [[ -f "$SERVICE_PATH" ]]; then
        ok "Il servizio systemd ($SERVICE_NAME) è già installato."
        read -p "Vuoi riavviarlo (in background) e uscire? [S/n] " resp
        case "${resp,,}" in
            ""|s|y|yes|si)
                sudo systemctl daemon-reload
                sudo systemctl restart "$SERVICE_NAME"
                
                echo -e "\n${GREEN}============================================================${NC}"
                echo -e "${BOLD}▶ EXTTO è stato riavviato in background!${NC}"
                echo -e "🌐 Puoi aprire l'interfaccia all'indirizzo: ${CYAN}http://$LOCAL_IP:$PORT${NC}"
                echo -e "   (Log: ${YELLOW}sudo journalctl -u $SERVICE_NAME -f${NC})"
                echo -e "${GREEN}============================================================${NC}\n"
                exit 0
                ;;
            *)
                info "Continuo con l'avvio in primo piano..."
                ;;
        esac
    else
        read -p "Vuoi installare EXTTO come servizio di sistema in background (systemd)? [S/n] " resp
        case "${resp,,}" in
            ""|s|y|yes|si)
                info "Creazione dinamica del file $SERVICE_NAME..."
                TMP_SERVICE="/tmp/$SERVICE_NAME"
                
                cat <<EOF > "$TMP_SERVICE"
[Unit]
Description=EXTTO Automation Service
After=network.target

[Service]
Type=simple
User=$USER
Group=$(id -gn)
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PY $EXTTO_PY
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
                
                info "Richiedo i permessi sudo per installare il servizio in $SERVICE_PATH..."
                sudo mv "$TMP_SERVICE" "$SERVICE_PATH"
                sudo chown root:root "$SERVICE_PATH"
                sudo chmod 644 "$SERVICE_PATH"
                
                sudo systemctl daemon-reload
                sudo systemctl enable --now "$SERVICE_NAME"
                
                echo -e "\n${GREEN}============================================================${NC}"
                echo -e "${BOLD}▶ Servizio installato e avviato con successo!${NC}"
                echo -e "🌐 Puoi aprire l'interfaccia all'indirizzo: ${CYAN}http://$LOCAL_IP:$PORT${NC}"
                echo -e "   (Log: ${YELLOW}sudo journalctl -u $SERVICE_NAME -f${NC})"
                echo -e "${GREEN}============================================================${NC}\n"
                exit 0
                ;;
            *)
                info "Installazione del servizio saltata. Avvio in primo piano..."
                ;;
        esac
    fi
fi

# ============================================================================
section "── 3. Avvio EXTTO (Primo Piano) ──────────────────────────────"
# ============================================================================

if [[ "$MODE" == "check" ]]; then
    echo ""
    ok "Ambiente OK. Nessun avvio richiesto (--check)."
    echo -e "  Venv:        ${CYAN}$VENV_DIR${NC}"
    echo -e "  Python:      ${CYAN}$("$VENV_PY" --version)${NC}"
    exit 0
fi

TARGET_SCRIPT=""
case "$MODE" in
    engine)  TARGET_SCRIPT="$EXTTO_PY" ;;
    web)     TARGET_SCRIPT="$SCRIPT_DIR/extto_web.py" ;;
    tui)
        TARGET_SCRIPT="$SCRIPT_DIR/extto_tui.py"
        # Doppio controllo per la TUI
        if ! "$VENV_PY" -c "import textual" &>/dev/null; then
            error "La libreria 'textual' non è installata. Esegui ./setup.sh --upgrade"
            exit 1
        fi
        ;;
esac

echo -e "\n${GREEN}============================================================${NC}"
echo -e "${BOLD}▶ Avvio di EXTTO in corso...${NC}"
if [[ "$MODE" == "engine" || "$MODE" == "web" ]]; then
    echo -e "🌐 Tra pochi secondi sarà disponibile su: ${CYAN}http://$LOCAL_IP:$PORT${NC}"
fi
echo -e "${GREEN}============================================================${NC}\n"

exec "$VENV_PY" "$TARGET_SCRIPT"
