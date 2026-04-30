#!/usr/bin/env bash
# ============================================================================
# setup.sh — Installa tutte le dipendenze di sistema per EXTTO
#
# Cosa fa:
#   1. Rileva il sistema operativo
#   2. Installa Python 3.10+ e strumenti base (curl, uv)
#   3. Installa python-libtorrent (opzionale, client torrent integrato)
#   4. Installa libmediainfo + pymediainfo (rinomina avanzata con tag tecnici)
#   5. Crea il venv e installa i pacchetti Python da requirements.txt
#   6. Verifica tutto e stampa un riepilogo
#   7. Installa amuled e configura il servizio systemd (opzionale, rete ed2k)
#
# Dopo setup.sh, usa start.sh per avviare EXTTO normalmente.
#
# Uso:
#   chmod +x setup.sh && ./setup.sh
#   ./setup.sh --no-libtorrent    # salta libtorrent
#   ./setup.sh --no-mediainfo     # salta libmediainfo/pymediainfo
#   ./setup.sh --no-amule         # salta installazione amuled
#   ./setup.sh --upgrade          # forza aggiornamento pacchetti Python
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
section() { echo -e "\n${BOLD}── $* ──────────────────────────────────────────────────${NC}"; }

# ============================================================================
# Argomenti
# ============================================================================
SKIP_LIBTORRENT=false
SKIP_MEDIAINFO=false
FORCE_UPGRADE=false
SKIP_AMULE=false

for arg in "$@"; do
    case "$arg" in
        --no-libtorrent) SKIP_LIBTORRENT=true ;;
        --no-mediainfo)  SKIP_MEDIAINFO=true  ;;
        --no-amule)      SKIP_AMULE=true      ;;
        --upgrade)       FORCE_UPGRADE=true   ;;
    esac
done

# ============================================================================
# Sicurezza: Mai eseguire come root
# ============================================================================
if [[ $EUID -eq 0 ]]; then
    error "Non eseguire questo script come root!"
    error "Avvialo come utente normale: ./setup.sh"
    error "Lo script chiederà la password sudo solo quando necessario."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${EXTTO_VENV:-$SCRIPT_DIR/.venv}"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"

# ============================================================================
# Rilevamento OS
# ============================================================================
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_LIKE="${ID_LIKE:-$OS_ID}"
    OS_NAME="${PRETTY_NAME:-$OS_ID}"
else
    OS_ID="unknown"
    OS_LIKE="unknown"
    OS_NAME="sconosciuto"
fi

is_debian() { [[ "$OS_LIKE" == *"debian"* ]] || [[ "$OS_ID" == "debian" ]] || [[ "$OS_ID" == "ubuntu" ]] || [[ "$OS_ID" == "linuxmint" ]] || [[ "$OS_ID" == "pop" ]] || [[ "$OS_ID" == "raspbian" ]]; }
is_arch()   { [[ "$OS_LIKE" == *"arch"* ]]   || [[ "$OS_ID" == "arch" ]] || [[ "$OS_ID" == "manjaro" ]] || [[ "$OS_ID" == "endeavouros" ]]; }
is_fedora() { [[ "$OS_LIKE" == *"fedora"* ]] || [[ "$OS_LIKE" == *"rhel"* ]] || [[ "$OS_ID" == "fedora" ]] || [[ "$OS_ID" == "centos" ]] || [[ "$OS_ID" == "rocky" ]] || [[ "$OS_ID" == "alma" ]]; }
is_suse()   { [[ "$OS_LIKE" == *"suse"* ]]   || [[ "$OS_ID" == "opensuse-leap" ]] || [[ "$OS_ID" == "opensuse-tumbleweed" ]]; }

install_pkg() {
    local pkgs="$*"
    if is_debian; then
        sudo apt-get update -qq
        sudo apt-get install -y $pkgs
    elif is_arch; then
        sudo pacman -Sy --noconfirm --needed $pkgs
    elif is_fedora; then
        sudo dnf install -y $pkgs
    elif is_suse; then
        sudo zypper install -y $pkgs
    else
        error "Gestore pacchetti non riconosciuto (OS: $OS_NAME)."
        error "Installa manualmente: $pkgs"
        return 1
    fi
}

ask_and_install() {
    local name="$1"
    local mandatory="${2:-false}"
    shift 2
    local pkgs="$*"

    warn "$name non trovato."
    read -rp "$(echo -e "${YELLOW}Installare${NC} $name (${pkgs}) con sudo? [S/n] ")" resp
    case "${resp,,}" in
        ""|s|y|yes|si)
            info "Richiedo sudo per: $pkgs"
            if install_pkg "$pkgs"; then
                ok "$name installato."
                return 0
            else
                error "Installazione di $name fallita."
                [[ "$mandatory" == "true" ]] && exit 1
                return 1
            fi
            ;;
        *)
            if [[ "$mandatory" == "true" ]]; then
                error "$name è obbligatorio. Esco."
                exit 1
            fi
            warn "Saltato: $name"
            return 1
            ;;
    esac
}

# ============================================================================
echo -e "\n${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║              EXTTO — Setup dipendenze di sistema             ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
info "Sistema: $OS_NAME"
info "Directory: $SCRIPT_DIR"

# ============================================================================
section "1. Strumenti base (curl)"
# ============================================================================
if ! command -v curl &>/dev/null; then
    CURL_PKG="curl"
    ask_and_install "curl" "true" "$CURL_PKG"
fi
ok "curl: $(curl --version | head -1)"

# ============================================================================
section "2. Python 3.10+"
# ============================================================================
find_python() {
    for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$candidate" &>/dev/null; then
            ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
            maj="${ver%%.*}"; min="${ver##*.}"
            if [[ "$maj" =~ ^[0-9]+$ ]] && [[ "$min" =~ ^[0-9]+$ ]] && (( maj >= 3 && min >= 10 )); then
                PYTHON_BIN="$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_BIN="${EXTTO_PYTHON:-}"
if ! find_python; then
    PY_PKG="python3 python3-venv python3-dev"
    is_arch && PY_PKG="python"
    ask_and_install "Python 3.10+" "true" "$PY_PKG"
    find_python || { error "Python 3.10+ non trovato dopo installazione."; exit 1; }
fi
ok "Python: $PYTHON_BIN ($($PYTHON_BIN --version))"

# ============================================================================
section "3. uv (gestore venv e pacchetti)"
# ============================================================================
if ! command -v uv &>/dev/null; then
    for p in "$HOME/.cargo/bin" "$HOME/.local/bin"; do
        [[ -f "$p/uv" ]] && export PATH="$p:$PATH" && break
    done
fi

if ! command -v uv &>/dev/null; then
    warn "uv non trovato."
    read -rp "$(echo -e "${YELLOW}Installare${NC} uv (senza sudo, nella cartella utente)? [S/n] ")" resp
    case "${resp,,}" in
        ""|s|y|yes|si)
            info "Download e installazione di uv..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
            ;;
        *)
            error "uv è obbligatorio. Esco."
            exit 1
            ;;
    esac
fi
ok "uv: $(uv --version)"

# ============================================================================
section "4. python-libtorrent (client torrent integrato, opzionale)"
# ============================================================================
# libtorrent viene installato direttamente nel venv tramite requirements.txt
# (sezione 8). Questa sezione verifica solo se è disponibile come fallback
# sul sistema (apt/dnf/pacman) nel caso PyPI non abbia il wheel per questa
# architettura/versione Python.
HAS_LIBTORRENT=false

if $SKIP_LIBTORRENT; then
    info "Saltato (--no-libtorrent)."
else
    # Prova prima se è già disponibile via PyPI (sarà installato nel venv alla sezione 8)
    if uv pip install --python "$PYTHON_BIN" --dry-run libtorrent 2>/dev/null | grep -q "libtorrent"; then
        ok "libtorrent disponibile su PyPI — verrà installato nel venv (sezione 8)."
        HAS_LIBTORRENT=true
    elif "$PYTHON_BIN" -c "import libtorrent" 2>/dev/null; then
        # Fallback: già installato nel sistema (apt/dnf/pacman)
        HAS_LIBTORRENT=true
        LT_VER=$("$PYTHON_BIN" -c "import libtorrent; print(libtorrent.__version__)" 2>/dev/null || echo "?")
        ok "libtorrent di sistema disponibile come fallback: $LT_VER"
        info "Il venv userà --system-site-packages per accedervi."
    else
        LT_PKG="python3-libtorrent"
        is_arch && LT_PKG="python-libtorrent"
        is_fedora && LT_PKG="python3-libtorrent"

        info "libtorrent non trovato su PyPI né nel sistema."
        if ask_and_install "python-libtorrent (fallback di sistema)" "false" "$LT_PKG"; then
            if "$PYTHON_BIN" -c "import libtorrent" 2>/dev/null; then
                HAS_LIBTORRENT=true
                LT_VER=$("$PYTHON_BIN" -c "import libtorrent; print(libtorrent.__version__)" 2>/dev/null || echo "?")
                ok "libtorrent di sistema pronto: $LT_VER"
            else
                warn "Installato ma Python non lo vede. Il venv userà --system-site-packages."
                warn "Se il problema persiste, prova: sudo apt install python3-libtorrent"
            fi
        else
            info "Senza libtorrent integrato (usa qBittorrent/Transmission/aria2)."
        fi
    fi
fi

# ============================================================================
section "5. libmediainfo (libreria C per rinomina avanzata, opzionale)"
# ============================================================================
# libmediainfo è la libreria C usata da pymediainfo per estrarre
# risoluzione, codec, HDR, lingue audio dai file video.
# Necessaria per i formati di rinomina 'standard' e 'completo'.
# pymediainfo (il pacchetto Python) viene installato nella sezione 8
# tramite requirements.txt — qui verifichiamo/installiamo solo la lib C.
HAS_MEDIAINFO=false

if $SKIP_MEDIAINFO; then
    info "Saltato (--no-mediainfo)."
else
    # Usa il venv se già esiste, altrimenti Python di sistema
    VENV_PY_EARLY="${VENV_DIR}/bin/python"
    CHECK_PY="$PYTHON_BIN"
    [[ -f "$VENV_PY_EARLY" ]] && CHECK_PY="$VENV_PY_EARLY"

    # --- Controllo 1: libreria C libmediainfo presente nel sistema? ---
    LIBMI_C_OK=false
    if ldconfig -p 2>/dev/null | grep -q "libmediainfo" ||        find /usr/lib* /usr/local/lib* 2>/dev/null | grep -q "libmediainfo"; then
        LIBMI_C_OK=true
    fi

    # --- Controllo 2: pymediainfo installato nel venv E funzionante? ---
    PYMI_OK=false
    if "$CHECK_PY" -c "from pymediainfo import MediaInfo; assert MediaInfo.can_parse()" 2>/dev/null; then
        PYMI_OK=true
    fi

    if $LIBMI_C_OK && $PYMI_OK; then
        # Tutto già a posto — nessuna azione necessaria
        MI_VER=$("$CHECK_PY" -c "import pymediainfo; print(pymediainfo.__version__)" 2>/dev/null || echo "?")
        ok "libmediainfo + pymediainfo già funzionanti: $MI_VER"
        HAS_MEDIAINFO=true
    else
        # Installa la libreria C se mancante
        if ! $LIBMI_C_OK; then
            info "Libreria C libmediainfo non trovata, installo..."
            LIBMI_PKG=""
            if is_debian;  then LIBMI_PKG="libmediainfo0v5"; fi
            if is_arch;    then LIBMI_PKG="libmediainfo"; fi
            if is_fedora;  then LIBMI_PKG="libmediainfo"; fi
            if is_suse;    then LIBMI_PKG="libmediainfo0"; fi

            if [[ -n "$LIBMI_PKG" ]]; then
                if ask_and_install "libmediainfo (libreria C runtime)" "false" "$LIBMI_PKG"; then
                    LIBMI_C_OK=true
                fi
            else
                warn "Distribuzione non riconosciuta. Installa manualmente:"
                warn "  Ubuntu/Debian: sudo apt install libmediainfo0v5"
                warn "  Arch:          sudo pacman -S libmediainfo"
                warn "  Fedora:        sudo dnf install libmediainfo"
            fi
        else
            ok "libmediainfo già presente nel sistema."
        fi

        # pymediainfo viene installato nel venv dalla sezione 8 (requirements.txt).
        # Qui verifichiamo solo se la lib C è presente — se sì, l'install della
        # sezione 8 funzionerà correttamente; se no, pymediainfo sarà inutilizzabile.
        if $LIBMI_C_OK; then
            info "libreria C OK — pymediainfo verrà installato nel venv alla sezione 8."
            HAS_MEDIAINFO=true
        else
            warn "libmediainfo non installata. Il formato rinomina 'completo' non sarà disponibile."
            warn "I formati 'base' e 'standard' (senza info audio/HDR/lingue) funzioneranno comunque."
        fi
    fi
fi

# ============================================================================
section "6. textual (TUI extto_tui.py, opzionale)"
# ============================================================================
# textual è il framework TUI usato da extto_tui.py.
# Viene installato nel venv da requirements.txt al passo 7,
# ma qui verifichiamo che non manchi nulla a livello di sistema.
# Su Linux non servono dipendenze di sistema aggiuntive.
HAS_TEXTUAL=false
VENV_PY_EARLY_TUI="${VENV_DIR}/bin/python"
CHECK_PY_TUI="$PYTHON_BIN"
[[ -f "$VENV_PY_EARLY_TUI" ]] && CHECK_PY_TUI="$VENV_PY_EARLY_TUI"

if "$CHECK_PY_TUI" -c "import textual" 2>/dev/null; then
    TUI_VER=$("$CHECK_PY_TUI" -c "import textual; print(textual.__version__)" 2>/dev/null || echo "?")
    ok "textual già installato: $TUI_VER"
    HAS_TEXTUAL=true
else
    info "textual verrà installato nel venv tramite requirements.txt al passo 7."
    info "(opzionale: serve solo per usare extto_tui.py)"
fi

# ============================================================================
section "7. amuled (client rete ed2k/Kad, opzionale)"
# ============================================================================
# amuled è il daemon di aMule per la rete ed2k/Kad.
# EXTTO si connette ad esso via protocollo EC (porta 4712).
# Il servizio viene installato come amule-extto.service in /etc/systemd/system/.
HAS_AMULE=false
AMULE_SERVICE_NAME="amule-extto"
AMULE_CONFIG_DIR="$SCRIPT_DIR/core/clients/amule/config"
AMULE_SERVICE_FILE="$SCRIPT_DIR/core/clients/amule/amule-extto.service"

if $SKIP_AMULE; then
    info "Saltato (--no-amule)."
else
    # Verifica se amuled è già installato
    if command -v amuled &>/dev/null; then
        AMULE_VER=$(amuled --version 2>&1 | head -1 || echo "?")
        ok "amuled già installato: $AMULE_VER"
        HAS_AMULE=true
    else
        AMULE_PKG="amule-daemon"
        is_arch   && AMULE_PKG="amule"
        is_fedora && AMULE_PKG="amule"
        is_suse   && AMULE_PKG="amule"

        if ask_and_install "amuled (rete ed2k/Kad)" "false" "$AMULE_PKG"; then
            if command -v amuled &>/dev/null; then
                ok "amuled installato."
                HAS_AMULE=true
            else
                warn "amuled installato ma non trovato nel PATH. Controlla: which amuled"
            fi
        else
            info "amuled non installato. La rete ed2k non sarà disponibile."
            info "Puoi installarlo in seguito e configurarlo dalla UI aMule / ed2k."
        fi
    fi

    if $HAS_AMULE; then
        # Crea la directory di configurazione se non esiste
        mkdir -p "$AMULE_CONFIG_DIR"
        ok "Config dir: $AMULE_CONFIG_DIR"

        # Genera il file .service se non esiste già
        if [[ ! -f "$AMULE_SERVICE_FILE" ]]; then
            info "Generazione $AMULE_SERVICE_NAME.service..."
            AMULED_BIN=$(command -v amuled)
            cat > "$AMULE_SERVICE_FILE" << SVCEOF
[Unit]
Description=aMule Daemon (gestito da EXTTO)
After=network.target

[Service]
Type=simple
User=$USER
ExecStart=$AMULED_BIN --config-dir=$AMULE_CONFIG_DIR --full-gui=0
ExecStop=/bin/kill -TERM \$MAINPID
Restart=on-failure
RestartSec=10s
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
SVCEOF
            ok "Creato: $AMULE_SERVICE_FILE"
        else
            ok "File .service già presente: $AMULE_SERVICE_FILE"
        fi

        # Chiedi se installare il servizio systemd
        if systemctl list-unit-files "$AMULE_SERVICE_NAME.service" 2>/dev/null | grep -q "$AMULE_SERVICE_NAME"; then
            ok "Servizio $AMULE_SERVICE_NAME già registrato in systemd."
        else
            read -rp "$(echo -e "${YELLOW}Installare${NC} il servizio systemd ${CYAN}$AMULE_SERVICE_NAME${NC}? [S/n] ")" resp
            case "${resp,,}" in
                ""|s|y|yes|si)
                    info "Installazione servizio systemd..."
                    sudo cp "$AMULE_SERVICE_FILE" "/etc/systemd/system/$AMULE_SERVICE_NAME.service"
                    sudo systemctl daemon-reload
                    sudo systemctl enable "$AMULE_SERVICE_NAME.service"
                    ok "Servizio $AMULE_SERVICE_NAME installato e abilitato."
                    read -rp "$(echo -e "${YELLOW}Avviare${NC} amuled ora? [S/n] ")" resp2
                    case "${resp2,,}" in
                        ""|s|y|yes|si)
                            sudo systemctl start "$AMULE_SERVICE_NAME.service"
                            sleep 2
                            if systemctl is-active --quiet "$AMULE_SERVICE_NAME.service"; then
                                ok "amuled in esecuzione."
                            else
                                warn "amuled non si è avviato. Controlla: systemctl status $AMULE_SERVICE_NAME"
                            fi
                            ;;
                        *) info "Avvia manualmente: sudo systemctl start $AMULE_SERVICE_NAME" ;;
                    esac
                    ;;
                *) info "Servizio non installato. Puoi farlo manualmente:" 
                   info "  sudo cp $AMULE_SERVICE_FILE /etc/systemd/system/"
                   info "  sudo systemctl daemon-reload && sudo systemctl enable $AMULE_SERVICE_NAME"
                   ;;
            esac
        fi

        echo ""
        info "Configurazione aMule in EXTTO:"
        info "  Nome servizio : $AMULE_SERVICE_NAME"
        info "  Config dir    : $AMULE_CONFIG_DIR"
        info "  Porta EC      : 4712 (da configurare in aMule / ed2k → Impostazioni)"
        info "  Porta TCP     : 4662 (aprire nel router per High ID)"
        info "  Porta UDP     : 4672 (aprire nel router per High ID)"
    fi
fi

# ============================================================================
section "8. Creazione / aggiornamento venv Python"
# ============================================================================
VENV_PYCFG="$VENV_DIR/pyvenv.cfg"
NEEDS_CREATE=false

if [[ ! -d "$VENV_DIR" || ! -f "$VENV_PYCFG" ]]; then
    NEEDS_CREATE=true
    info "Venv non trovato, creazione in: $VENV_DIR"
elif grep -qi "include-system-site-packages = false" "$VENV_PYCFG" && $HAS_LIBTORRENT; then
    warn "Venv senza --system-site-packages ma libtorrent è installato. Ricreazione..."
    rm -rf "$VENV_DIR"
    NEEDS_CREATE=true
else
    ok "Venv esistente: $VENV_DIR"
fi

if $NEEDS_CREATE; then
    uv venv "$VENV_DIR" --python "$PYTHON_BIN" --system-site-packages
    ok "Venv creato: $VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"

# Crea symlink python3 se manca (systemd cerca /bin/python3, uv crea solo /bin/python)
if [[ -f "$VENV_PY" && ! -f "$VENV_DIR/bin/python3" ]]; then
    ln -sf python "$VENV_DIR/bin/python3"
    ok "Symlink python3 creato nel venv."
fi
# Crea anche il symlink python3.x per completezza
VENV_PY_VER=$(basename "$(readlink -f "$VENV_PY" 2>/dev/null || echo "$VENV_PY")" 2>/dev/null || echo "")
if [[ -n "$VENV_PY_VER" && ! -f "$VENV_DIR/bin/$VENV_PY_VER" ]]; then
    ln -sf python "$VENV_DIR/bin/$VENV_PY_VER" 2>/dev/null || true
fi

# ============================================================================
section "9. Installazione dipendenze Python (requirements.txt)"
# ============================================================================
if [[ ! -f "$REQUIREMENTS" ]]; then
    error "requirements.txt non trovato: $REQUIREMENTS"
    exit 1
fi

UV_FLAGS="--quiet"
$FORCE_UPGRADE && UV_FLAGS="$UV_FLAGS --upgrade"

PKGS=()
while IFS= read -r line; do
    pkg="${line%%#*}"
    pkg="${pkg// /}"
    [[ -n "$pkg" ]] && PKGS+=("$pkg")
done < <(grep -v '^\s*#' "$REQUIREMENTS" | grep -v '^\s*$')

if [[ ${#PKGS[@]} -gt 0 ]]; then
    info "Installazione ${#PKGS[@]} pacchetti Python..."
    uv pip install --python "$VENV_PY" $UV_FLAGS "${PKGS[@]}"
    ok "${#PKGS[@]} pacchetti installati nel venv."
fi

# ============================================================================
section "10. Verifica finale"
# ============================================================================
echo ""
echo -e "${BOLD}Riepilogo installazione:${NC}"
echo -e "  Python       : ${CYAN}$("$VENV_PY" --version)${NC}"

# Verifica critica: python3 nel venv (richiesto da systemd)
if [[ -f "$VENV_DIR/bin/python3" ]]; then
    ok "python3 nel venv: OK (richiesto da systemd)"
else
    warn "python3 non trovato nel venv — creazione symlink..."
    ln -sf python "$VENV_DIR/bin/python3"
    ok "Symlink python3 creato."
fi

# Verifica ogni dipendenza chiave
check_module() {
    local module="$1"
    local label="$2"
    if "$VENV_PY" -c "import $module" 2>/dev/null; then
        ver=$("$VENV_PY" -c "import $module; print(getattr($module, '__version__', 'ok'))" 2>/dev/null || echo "ok")
        echo -e "  $label : ${GREEN}✓ $ver${NC}"
        return 0
    else
        echo -e "  $label : ${RED}✗ non disponibile${NC}"
        return 1
    fi
}

check_module "flask"        "Flask        "
check_module "requests"     "Requests     "
check_module "cloudscraper" "cloudscraper "
check_module "bs4"          "BeautifulSoup"
check_module "waitress"    "Waitress     "
check_module "psutil"      "psutil       "
check_module "yaml"        "PyYAML       "
check_module "textual"     "textual (TUI)"

if $HAS_LIBTORRENT; then
    check_module "libtorrent" "libtorrent   " || warn "libtorrent installato ma non visibile nel venv (system-site-packages?)"
else
    echo -e "  libtorrent   : ${YELLOW}— saltato (opzionale)${NC}"
fi

if $HAS_MEDIAINFO || ! $SKIP_MEDIAINFO; then
    if check_module "pymediainfo" "pymediainfo  "; then
        # can_parse() verifica esplicitamente che la libreria C libmediainfo sia trovata
        if "$VENV_PY" -c "from pymediainfo import MediaInfo; assert MediaInfo.can_parse()" 2>/dev/null; then
            echo -e "  libmediainfo : ${GREEN}✓ libreria C trovata${NC}"
        else
            echo -e "  libmediainfo : ${YELLOW}⚠ pymediainfo ok ma libreria C non trovata${NC}"
            warn "Installa libmediainfo0v5 (apt) o libmediainfo (pacman/dnf)"
        fi
    fi
else
    echo -e "  pymediainfo  : ${YELLOW}— saltato (opzionale)${NC}"
fi

# ============================================================================
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Setup completato con successo!                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo -e ""
echo -e "  Per avviare EXTTO:  ${CYAN}./start.sh${NC}"
echo -e "  Per aggiornare:     ${CYAN}./setup.sh --upgrade${NC}"
echo -e ""
