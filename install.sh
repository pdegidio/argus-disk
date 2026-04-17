#\!/usr/bin/env bash
# =============================================================
#  Argus — Predictive Disk Health Monitor
#  Interactive installer
#
#  Usage:
#    bash install.sh
#
#  Or one-liner from GitHub:
#    bash <(curl -sSL https://raw.githubusercontent.com/pdegidio/argus-disk/main/install.sh)
#
#  What this script does:
#    1. Checks system requirements (Python 3.10+, smartctl, sudo)
#    2. Creates a dedicated 'argus' system user
#    3. Installs scripts and config to /opt/argus
#    4. Installs Python dependency (requests)
#    5. Auto-discovers disks via smartctl --scan
#    6. Walks you through argus.conf configuration
#    7. Sets up cron jobs
#    8. Optionally installs systemd service for the exporter
#    9. Runs a dry-run test cycle
# =============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }
section() { echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }
ask()     { echo -en "${YELLOW}?${NC} $* "; }

INSTALL_DIR="/opt/argus"
SCRIPTS_DIR="${INSTALL_DIR}/scripts"
CONFIG_DIR="${INSTALL_DIR}/config"
DATA_DIR="/var/lib/argus"
ARGUS_USER="argus"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

clear
echo -e "${BOLD}"
cat <<'EOF'
     _
    / \   _ __ __ _ _   _ ___
   / _ \ | '__/ _` | | | / __|
  / ___ \| | | (_| | |_| \__ \
 /_/   \_\_|  \__, |\__,_|___/
               |___/
  Predictive Disk Health Monitor — Installer v1.0.0
EOF
echo -e "${NC}"
echo "  Watches your disks. Forecasts failures before they happen."
echo "  No cloud. No subscriptions. SMART data stays on your machine."
echo ""
ask "Ready to begin? [Y/n]"
read -r REPLY
[[ "${REPLY,,}" == "n" ]] && echo "Aborted." && exit 0

# ── 1. Requirements ──────────────────────────────────────────
section "Checking requirements"

[[ "$(uname -s)" == "Linux" ]] || die "Argus requires Linux."

if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 10 ]] || \
        die "Python 3.10+ required, found ${PY_VER}."
    ok "Python ${PY_VER}"
else
    die "Python 3 not found. Install: sudo apt install python3"
fi

if command -v smartctl &>/dev/null; then
    SC_VER=$(smartctl --version | head -1 | awk '{print $2}')
    ok "smartctl ${SC_VER}"
else
    die "smartctl not found. Install: sudo apt install smartmontools"
fi

if python3 -c "import requests" &>/dev/null; then
    ok "Python 'requests' found"
    NEED_REQUESTS=false
else
    warn "Python 'requests' not found — will install"
    NEED_REQUESTS=true
fi

if \! sudo -n true 2>/dev/null; then
    info "This installer needs sudo for a few steps."
    sudo -v || die "sudo authentication failed."
fi
ok "sudo access confirmed"

# ── 2. Install dependencies ──────────────────────────────────
if [[ "$NEED_REQUESTS" == true ]]; then
    section "Installing Python dependencies"
    pip3 install --quiet requests || \
    pip3 install --quiet --break-system-packages requests || \
    die "Failed to install 'requests'. Run: pip3 install requests"
    ok "'requests' installed"
fi

# ── 3. Create user and directories ──────────────────────────
section "Setting up system user and directories"

if id "$ARGUS_USER" &>/dev/null; then
    ok "User '${ARGUS_USER}' already exists"
else
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$ARGUS_USER"
    ok "User '${ARGUS_USER}' created"
fi

# argus needs disk group to run smartctl without root
if getent group disk &>/dev/null; then
    sudo usermod -aG disk "$ARGUS_USER"
    ok "Added '${ARGUS_USER}' to 'disk' group"
fi

for DIR in "$SCRIPTS_DIR" "$CONFIG_DIR" "$DATA_DIR"; do
    sudo mkdir -p "$DIR"
    sudo chown -R "${ARGUS_USER}:${ARGUS_USER}" "$DIR"
    ok "Directory: $DIR"
done

# ── 4. Install scripts ───────────────────────────────────────
section "Installing Argus scripts"

for SCRIPT in argus-collector.py argus-analyzer.py argus-watcher.py argus-exporter.py; do
    SRC="${SCRIPT_DIR}/scripts/${SCRIPT}"
    [[ -f "$SRC" ]] || { warn "Not found: ${SCRIPT} — skipping"; continue; }
    sudo cp "$SRC" "${SCRIPTS_DIR}/${SCRIPT}"
    sudo chmod +x "${SCRIPTS_DIR}/${SCRIPT}"
    sudo chown "${ARGUS_USER}:${ARGUS_USER}" "${SCRIPTS_DIR}/${SCRIPT}"
    ok "Installed: ${SCRIPTS_DIR}/${SCRIPT}"
done

# ── 5. Disk discovery ────────────────────────────────────────
section "Disk discovery"

info "Scanning for disks via smartctl --scan..."
DISCOVERED=$(sudo smartctl --scan 2>/dev/null | awk '{print $1}' || true)

if [[ -n "$DISCOVERED" ]]; then
    echo ""
    echo "  Found devices:"
    while IFS= read -r dev; do
        MODEL=$(sudo smartctl -i "$dev" 2>/dev/null | grep -E "Device Model|Product" | head -1 | cut -d: -f2 | xargs || echo "unknown")
        echo "    $dev  →  $MODEL"
    done <<< "$DISCOVERED"
    echo ""
else
    warn "No disks found via smartctl --scan (may need root)"
fi

# ── 6. Configure argus.conf ──────────────────────────────────
section "Configuration"

CONFIG_FILE="${CONFIG_DIR}/argus.conf"

if [[ -f "$CONFIG_FILE" ]]; then
    warn "Config already exists at ${CONFIG_FILE}"
    ask "Overwrite? [y/N]"
    read -r REPLY
    [[ "${REPLY,,}" == "y" ]] && SKIP_CONFIG=false || SKIP_CONFIG=true
else
    SKIP_CONFIG=false
fi

if [[ "$SKIP_CONFIG" == false ]]; then
    sudo cp "${SCRIPT_DIR}/config/argus.conf.example" "$CONFIG_FILE"
    sudo chown "${ARGUS_USER}:${ARGUS_USER}" "$CONFIG_FILE"
    sudo chmod 640 "$CONFIG_FILE"

    echo ""
    info "Let's configure the key settings."
    echo ""

    ask "ntfy base URL (e.g. http://your-ntfy:8080) — leave blank to skip:"
    read -r NTFY_URL
    if [[ -n "$NTFY_URL" ]]; then
        sudo sed -i "s|url = http://your-ntfy-instance:8080|url = ${NTFY_URL}|" "$CONFIG_FILE"
        ask "ntfy topic [argus-disk]:"
        read -r NTFY_TOPIC
        NTFY_TOPIC="${NTFY_TOPIC:-argus-disk}"
        sudo sed -i "s|topic = argus-disk|topic = ${NTFY_TOPIC}|" "$CONFIG_FILE"
        ok "ntfy configured"
    else
        warn "ntfy not configured — alerts will be skipped"
    fi

    echo ""
    info "Disk configuration:"
    info "Edit ${CONFIG_FILE} to add your disks as [disk:alias] sections."
    info "See the comments in the config file for examples including DAS enclosures."
    info ""
    info "Example for a simple SATA disk:"
    echo "    [disk:my-disk]"
    echo "    device = /dev/sdb"
    echo "    type   = sat"
    echo "    class  = hdd"
    echo ""
fi

# ── 7. Cron jobs ─────────────────────────────────────────────
section "Setting up cron jobs"

PYTHON=$(command -v python3)
CRON_COLLECT="0 */6 * * * ${PYTHON} ${SCRIPTS_DIR}/argus-collector.py >> /var/log/argus-collector.log 2>&1"
CRON_WATCH="*/30 * * * * ${PYTHON} ${SCRIPTS_DIR}/argus-watcher.py >> /var/log/argus-watcher.log 2>&1"
CURRENT_CRON=$(crontab -l 2>/dev/null || true)

if echo "$CURRENT_CRON" | grep -q "argus-collector"; then
    ok "argus-collector cron already exists"
else
    ask "Install cron for argus-collector (every 6h)? [Y/n]"
    read -r REPLY
    if [[ "${REPLY,,}" \!= "n" ]]; then
        (crontab -l 2>/dev/null; echo "# Argus — SMART collection"; echo "$CRON_COLLECT") | crontab -
        ok "argus-collector cron installed"
    fi
fi

if echo "$CURRENT_CRON" | grep -q "argus-watcher"; then
    ok "argus-watcher cron already exists"
else
    ask "Install cron for argus-watcher (every 30 min)? [Y/n]"
    read -r REPLY
    if [[ "${REPLY,,}" \!= "n" ]]; then
        (crontab -l 2>/dev/null; echo "# Argus — disk health watcher"; echo "$CRON_WATCH") | crontab -
        ok "argus-watcher cron installed"
    fi
fi

# ── 8. Systemd exporter ──────────────────────────────────────
section "Prometheus exporter"

SYSTEMD_FILE="/etc/systemd/system/argus-exporter.service"
SERVICE_SRC="${SCRIPT_DIR}/docs/argus-exporter.service"

if [[ -f "$SYSTEMD_FILE" ]]; then
    ok "argus-exporter.service already installed"
elif command -v systemctl &>/dev/null; then
    ask "Install argus-exporter as systemd service? [Y/n]"
    read -r REPLY
    if [[ "${REPLY,,}" \!= "n" ]]; then
        if [[ -f "$SERVICE_SRC" ]]; then
            sudo cp "$SERVICE_SRC" "$SYSTEMD_FILE"
            sudo systemctl daemon-reload
            sudo systemctl enable argus-exporter
            sudo systemctl start argus-exporter
            ok "argus-exporter.service enabled and started"
        else
            CRON_EXP="@reboot ${PYTHON} ${SCRIPTS_DIR}/argus-exporter.py &"
            (crontab -l 2>/dev/null; echo "# Argus — Prometheus exporter"; echo "$CRON_EXP") | crontab -
            ok "Exporter @reboot cron installed"
        fi
    fi
fi

# ── 9. Dry-run test ──────────────────────────────────────────
section "Test run"

ask "Run a dry-run collection now? [Y/n]"
read -r REPLY
if [[ "${REPLY,,}" \!= "n" ]]; then
    info "Running argus-collector.py --dry-run..."
    echo ""
    "$PYTHON" "${SCRIPTS_DIR}/argus-collector.py" \
        --config "${CONFIG_FILE}" --dry-run 2>&1 | head -40
    echo ""
    ok "Test complete — check output above for errors"
fi

# ── Done ─────────────────────────────────────────────────────
section "Installation complete"

echo ""
echo -e "  ${GREEN}${BOLD}Argus is installed.${NC}"
echo ""
echo -e "  ${BOLD}Key paths:${NC}"
echo "    Scripts : ${SCRIPTS_DIR}/"
echo "    Config  : ${CONFIG_FILE}"
echo "    Data    : ${DATA_DIR}/"
echo "    Logs    : /var/log/argus-*.log"
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo "    1. Add your disks to: sudo nano ${CONFIG_FILE}"
echo "    2. Run first collection: python3 ${SCRIPTS_DIR}/argus-collector.py"
echo "    3. Check status: python3 ${SCRIPTS_DIR}/argus-analyzer.py"
echo "    4. Check metrics: python3 ${SCRIPTS_DIR}/argus-exporter.py --once"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo "    Manual collect : python3 ${SCRIPTS_DIR}/argus-collector.py"
echo "    Analyze now    : python3 ${SCRIPTS_DIR}/argus-analyzer.py"
echo "    Watch (dry-run): python3 ${SCRIPTS_DIR}/argus-watcher.py --dry-run"
echo "    Metrics dump   : python3 ${SCRIPTS_DIR}/argus-exporter.py --once"
echo "    View logs      : tail -f /var/log/argus-watcher.log"
echo ""
