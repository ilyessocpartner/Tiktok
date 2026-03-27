#!/usr/bin/env bash
# =============================================================================
# setup.sh — Polymarket Trading Bot — Ubuntu 22.04 VPS Setup Script
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Colour

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Configuration — adjust to your environment
# ---------------------------------------------------------------------------
BOT_USER="${BOT_USER:-$(whoami)}"
BOT_DIR="${BOT_DIR:-/opt/polymarket-bot}"
VENV_DIR="${BOT_DIR}/venv"
PYTHON_BIN="python3.11"
SERVICE_NAME="polymarket-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Detect the actual project root (one level above this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
info "Updating apt package lists..."
sudo apt-get update -qq

info "Installing Python 3.11 and supporting packages..."
sudo apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    git \
    curl \
    build-essential

success "System packages installed"

# ---------------------------------------------------------------------------
# 2. Create bot directory
# ---------------------------------------------------------------------------
info "Creating bot directory at ${BOT_DIR}..."
sudo mkdir -p "${BOT_DIR}"
sudo chown "${BOT_USER}:${BOT_USER}" "${BOT_DIR}"

# Copy project files if we are running from the repo
if [ "${PROJECT_ROOT}" != "${BOT_DIR}" ]; then
    info "Copying project files from ${PROJECT_ROOT} to ${BOT_DIR}..."
    rsync -a --exclude='.git' --exclude='venv' --exclude='__pycache__' \
        "${PROJECT_ROOT}/" "${BOT_DIR}/"
    success "Project files copied"
fi

# ---------------------------------------------------------------------------
# 3. Virtual environment
# ---------------------------------------------------------------------------
info "Creating Python virtual environment at ${VENV_DIR}..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
success "Virtual environment created"

info "Upgrading pip..."
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip wheel

info "Installing Python dependencies from requirements.txt..."
"${VENV_DIR}/bin/pip" install --quiet -r "${BOT_DIR}/requirements.txt"
success "Python dependencies installed"

# ---------------------------------------------------------------------------
# 4. Environment configuration
# ---------------------------------------------------------------------------
if [ ! -f "${BOT_DIR}/.env" ]; then
    info "Creating .env from .env.example — please fill in your credentials."
    cp "${BOT_DIR}/.env.example" "${BOT_DIR}/.env"
    chmod 600 "${BOT_DIR}/.env"
    warn ".env created at ${BOT_DIR}/.env — edit it before starting the bot!"
else
    info ".env already exists, skipping copy."
fi

# ---------------------------------------------------------------------------
# 5. Systemd service
# ---------------------------------------------------------------------------
info "Creating systemd service file at ${SERVICE_FILE}..."

sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=Polymarket Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${BOT_DIR}
EnvironmentFile=${BOT_DIR}/.env
ExecStart=${VENV_DIR}/bin/python ${BOT_DIR}/main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${BOT_DIR}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
success "Systemd service '${SERVICE_NAME}' created and enabled"

# ---------------------------------------------------------------------------
# 6. Done — print next steps
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e "  ${YELLOW}Next steps:${NC}"
echo ""
echo -e "  1. Edit your configuration:"
echo -e "     ${BLUE}nano ${BOT_DIR}/.env${NC}"
echo ""
echo -e "  2. Test the bot in DRY_RUN mode (no real money):"
echo -e "     ${BLUE}DRY_RUN=true ${VENV_DIR}/bin/python ${BOT_DIR}/main.py${NC}"
echo ""
echo -e "  3. Start the systemd service:"
echo -e "     ${BLUE}sudo systemctl start ${SERVICE_NAME}${NC}"
echo ""
echo -e "  4. Check logs:"
echo -e "     ${BLUE}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo ""
echo -e "  5. Stop the service:"
echo -e "     ${BLUE}sudo systemctl stop ${SERVICE_NAME}${NC}"
echo ""
echo -e "  ${RED}WARNING:${NC} Always test with DRY_RUN=true before enabling live trading!"
echo ""
