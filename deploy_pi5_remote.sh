#!/usr/bin/env bash
set -uo pipefail

# =========================================================================== #
# Raspberry Pi 5 remote deploy — archive → SCP → SSH → Docker build & start
# =========================================================================== #
# Run from your dev machine. Requires:
#   - sshpass (optional, only if PI5_PASSWORD is set)
#   - tar + pigz (optional, fallback gzip)
#
# Usage:
#   ./deploy_pi5_remote.sh [--yes] [--no-build] [--env .env.pi5]
#
# Env (set in your env file or export):
#   PI5_HOST        Pi5 IP or hostname
#   PI5_USER        SSH user (default: root)
#   PI5_PORT        SSH port (default: 22)
#   PI5_PASSWORD    SSH password (blank = use key auth)
#   PI5_DEST_DIR    Target dir on Pi5 (default: /opt/car-calib-pi5)
# =========================================================================== #

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROJECT_NAME="${PROJECT_NAME:-car-calib-pi5}"
VERSION="$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"

AUTO_CONFIRM=false
SKIP_BUILD=false

usage() {
    cat <<'EOF'
Usage:
  ./deploy_pi5_remote.sh [--yes] [--no-build] [--env <path>]

Options:
  --yes         Skip confirmation prompt
  --no-build    Skip docker build on remote (just upload + restart)
  --env <path>  Custom env file (default: .env.pi5)

Setup:
  1. Create .env.pi5.remote with lines:
       PI5_HOST=192.168.x.x
       PI5_USER=root
       PI5_PASSWORD=yourpass   (or leave blank for key auth)
  2. Run: ./deploy_pi5_remote.sh
EOF
}

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
log_step()  { echo -e "${BLUE}$1${NC}"; }
log_ok()    { echo -e "${GREEN}$1${NC}"; }
log_warn()  { echo -e "${YELLOW}$1${NC}"; }
log_err()   { echo -e "${RED}$1${NC}"; }

trim() {
    local v="$1"
    v="${v#"${v%%[![:space:]]*}"}"
    v="${v%"${v##*[![:space:]]}"}"
    printf '%s' "$v"
}

shell_quote() {
    printf "'%s'" "${1//\'/\'\\\'\'}"
}

# --------------------------------------------------------------------------- #
# Parse args
# --------------------------------------------------------------------------- #
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env.pi5}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes)       AUTO_CONFIRM=true; shift ;;
        --no-build)  SKIP_BUILD=true; shift ;;
        --env)       ENV_FILE="$2"; shift 2 ;;
        -h|--help)   usage; exit 0 ;;
        *)           log_err "Unknown: $1"; usage; exit 1 ;;
    esac
done

if [[ ! -f "$ENV_FILE" ]]; then
    log_err "Env file not found: $ENV_FILE"
    log_err "Copy .env.pi5 to $ENV_FILE and add PI5_HOST, PI5_USER, PI5_PASSWORD"
    exit 1
fi

# Source env (these override anything in .env.pi5)
export PI5_HOST PI5_USER PI5_PORT PI5_PASSWORD PI5_DEST_DIR
export PI5_HOST PI5_USER PI5_PORT PI5_PASSWORD PI5_DEST_DIR

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Defaults
PI5_HOST="${PI5_HOST:-}"
PI5_USER="${PI5_USER:-root}"
PI5_PORT="${PI5_PORT:-22}"
PI5_PASSWORD="$(trim "${PI5_PASSWORD:-}")"
PI5_DEST_DIR="$(trim "${PI5_DEST_DIR:-/opt/car-calib-pi5}")"
COMPOSE_FILE="docker-compose.pi5.yml"

if [[ -z "$PI5_HOST" ]]; then
    log_err "PI5_HOST not set. Add it to $ENV_FILE"
    exit 1
fi

# --------------------------------------------------------------------------- #
# SSH helper
# --------------------------------------------------------------------------- #
ssh_cmd() {
    local password="$1"
    shift
    if [[ -n "$password" ]]; then
        local sp
        sp="$(command -v sshpass 2>/dev/null || true)"
        if [[ -n "$sp" ]]; then
            "$sp" -p "$password" ssh -p "$PI5_PORT" \
                -o StrictHostKeyChecking=accept-new \
                -o ConnectTimeout=10 \
                "$@" 2>/dev/null
            return
        fi
        local pl
        pl="$(command -v plink 2>/dev/null || true)"
        if [[ -n "$pl" ]]; then
            # plink -batch refuses unknown host keys instead of prompting --
            # if this fails on first run, cache the key once interactively:
            #   plink -ssh <user>@<host> -P <port> exit
            "$pl" -ssh -batch -pw "$password" -P "$PI5_PORT" "$@"
            return
        fi
        log_err "PI5_PASSWORD is set but neither sshpass nor plink (PuTTY) is on PATH."
        log_err "Install sshpass, install PuTTY (provides plink/pscp), or switch to SSH key auth."
        exit 1
    fi
    ssh -p "$PI5_PORT" \
        -o BatchMode=yes \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10 \
        "$@"
}

scp_cmd() {
    local password="$1"
    shift
    if [[ -n "$password" ]]; then
        local sp
        sp="$(command -v sshpass 2>/dev/null || true)"
        if [[ -n "$sp" ]]; then
            "$sp" -p "$password" scp -P "$PI5_PORT" \
                -o StrictHostKeyChecking=accept-new \
                -o ConnectTimeout=10 \
                "$@"
            return
        fi
        local pl
        pl="$(command -v pscp 2>/dev/null || true)"
        if [[ -n "$pl" ]]; then
            "$pl" -batch -pw "$password" -P "$PI5_PORT" "$@"
            return
        fi
        log_err "PI5_PASSWORD is set but neither sshpass nor pscp (PuTTY) is on PATH."
        exit 1
    fi
    scp -P "$PI5_PORT" \
        -o BatchMode=yes \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10 \
        "$@"
}

# --------------------------------------------------------------------------- #
# Confirm
# --------------------------------------------------------------------------- #
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Raspberry Pi 5 Remote Deploy${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "  Target:    ${BLUE}${PI5_USER}@${PI5_HOST}:${PI5_PORT}${NC}"
echo -e "  Dest:      ${BLUE}${PI5_DEST_DIR}${NC}"
echo -e "  Version:   ${BLUE}${VERSION}${NC}"
echo ""

if [[ "$AUTO_CONFIRM" != "true" ]]; then
    read -r -p "Deploy to Raspberry Pi 5? [y/N]: " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log_warn "Cancelled"
        exit 0
    fi
fi

# --------------------------------------------------------------------------- #
# Step 1: Check SSH
# --------------------------------------------------------------------------- #
log_step "[1/4] Checking SSH connectivity..."
if ! ssh_cmd "$PI5_PASSWORD" "${PI5_USER}@${PI5_HOST}" exit; then
    log_err "Cannot SSH to ${PI5_USER}@${PI5_HOST}:${PI5_PORT} (see error above)"
    log_err "Check PI5_HOST, PI5_PASSWORD, or SSH key access."
    log_err "If plink reported an uncached host key, accept it once with:"
    log_err "  plink -ssh ${PI5_USER}@${PI5_HOST} -P ${PI5_PORT} exit"
    exit 1
fi
log_ok "SSH OK"

# --------------------------------------------------------------------------- #
# Step 2: Build archive
# --------------------------------------------------------------------------- #
log_step "[2/4] Building source archive..."

ARCHIVE_PATH="/tmp/${PROJECT_NAME}-${VERSION}.tar.gz"
remote_env="/tmp/${PROJECT_NAME}-${VERSION}.env"

# Create archive (exclude bulky files)
if command -v pigz &>/dev/null; then
    tar --exclude='.git' --exclude='.pytest_cache' --exclude='__pycache__' \
        --exclude='.venv' --exclude='venv' --exclude='*.pyc' \
        --exclude='*.log' --exclude='logs' --exclude='.env' \
        -I pigz -cf "$ARCHIVE_PATH" -C "$SCRIPT_DIR" .
else
    tar --exclude='.git' --exclude='.pytest_cache' --exclude='__pycache__' \
        --exclude='.venv' --exclude='venv' --exclude='*.pyc' \
        --exclude='*.log' --exclude='logs' --exclude='.env' \
        -czf "$ARCHIVE_PATH" -C "$SCRIPT_DIR" .
fi

log_ok "Archive: $ARCHIVE_PATH ($(du -h "$ARCHIVE_PATH" | cut -f1))"

# --------------------------------------------------------------------------- #
# Step 3: Upload
# --------------------------------------------------------------------------- #
log_step "[3/4] Uploading to Pi5..."

ssh_cmd "$PI5_PASSWORD" "${PI5_USER}@${PI5_HOST}" "mkdir -p ${PI5_DEST_DIR}/releases" 2>/dev/null || true

scp_cmd "$PI5_PASSWORD" "$ARCHIVE_PATH" "${PI5_USER}@${PI5_HOST}:/tmp/${PROJECT_NAME}-${VERSION}.tar.gz"
scp_cmd "$PI5_PASSWORD" "$ENV_FILE" "${PI5_USER}@${PI5_HOST}:${remote_env}"

log_ok "Upload complete"

# --------------------------------------------------------------------------- #
# Step 4: Deploy remote
# --------------------------------------------------------------------------- #
log_step "[4/4] Deploying on Pi5..."

ssh_cmd "$PI5_PASSWORD" "${PI5_USER}@${PI5_HOST}" \
    DEST_DIR="$(shell_quote "$PI5_DEST_DIR")" \
    VERSION="$(shell_quote "$VERSION")" \
    PROJECT_NAME="$(shell_quote "$PROJECT_NAME")" \
    COMPOSE_FILE="$(shell_quote "$COMPOSE_FILE")" \
    SKIP_BUILD="$(shell_quote "$SKIP_BUILD")" \
    REMOTE_ENV="$(shell_quote "$remote_env")" \
    bash -s <<'REMOTE_EOF'
echo "[remote] START deploy version=$VERSION"
set -uo pipefail

root_dir="${DEST_DIR%/}"
release_dir="${root_dir}/releases/${VERSION}"
current_dir="${root_dir}/current"
remote_archive="/tmp/${PROJECT_NAME}-${VERSION}.tar.gz"

echo "[remote] Extracting release..."
if ! mkdir -p "$release_dir" 2>/tmp/mkdir_err.$$; then
    if sudo -n mkdir -p "$release_dir" 2>/dev/null && sudo -n chown -R "$(id -un):$(id -gn)" "$root_dir" 2>/dev/null; then
        echo "[remote] $root_dir needed sudo to create; chowned to $(id -un)"
    else
        echo "[remote] ERROR: cannot create $release_dir ($(cat /tmp/mkdir_err.$$ 2>/dev/null))"
        echo "[remote] Fix: sudo mkdir -p $root_dir && sudo chown -R \$USER:\$USER $root_dir"
        echo "[remote]   or set PI5_DEST_DIR to a path this user already owns (e.g. \$HOME/car-calib-pi5)"
        rm -f /tmp/mkdir_err.$$
        exit 1
    fi
fi
rm -f /tmp/mkdir_err.$$
tar -xzf "$remote_archive" -C "$release_dir" || { echo "[remote] ERROR: extracting $remote_archive failed"; exit 1; }
cp "$REMOTE_ENV" "$release_dir/.env" || { echo "[remote] ERROR: copying env file into release failed"; exit 1; }
ln -sfn "$release_dir" "$current_dir" || { echo "[remote] ERROR: symlinking $current_dir failed"; exit 1; }
rm -f "$remote_archive" "$REMOTE_ENV"

echo "[remote] Docker compose..."
cd "$current_dir"

if ! command -v docker &>/dev/null; then
    echo "[remote] ERROR: Docker not installed."
    exit 1
fi

# Detect sudo need
DOCKER_BIN="docker"
if ! docker ps &>/dev/null 2>&1; then
    if sudo -n docker ps &>/dev/null 2>&1; then
        DOCKER_BIN="sudo docker"
        echo "[remote] Using sudo docker"
    else
        echo "[remote] ERROR: docker not accessible (try: sudo usermod -aG docker \$USER && newgrp docker)"
        exit 1
    fi
fi

COMPOSE_CMD=""
if $DOCKER_BIN compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="$DOCKER_BIN compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo "[remote] ERROR: docker compose not found"
    exit 1
fi

echo "[remote] Compose: $COMPOSE_CMD -f $COMPOSE_FILE"

# Stop old
$COMPOSE_CMD -f "$COMPOSE_FILE" down 2>/dev/null || true

# Build & start (may take 5-10 min first time on Raspberry Pi 5)
BUILD_LOG="/tmp/car-calib-build-${VERSION}.log"
echo "[remote] Build starting... (log: $BUILD_LOG)"
if [[ "$SKIP_BUILD" == "true" ]]; then
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d --remove-orphans > "$BUILD_LOG" 2>&1 &
else
    $COMPOSE_CMD -f "$COMPOSE_FILE" up --build -d --remove-orphans > "$BUILD_LOG" 2>&1 &
fi
BUILD_PID=$!

# Show log tail while building (timeout 120s)
DEADLINE=$((SECONDS + 120))
while kill -0 $BUILD_PID 2>/dev/null && (( SECONDS < DEADLINE )); do
    if [[ -s "$BUILD_LOG" ]]; then
        tail -3 "$BUILD_LOG" 2>/dev/null
    fi
    sleep 3
done

# Final status
if kill -0 $BUILD_PID 2>/dev/null; then
    echo "[remote] Build still running (PID=$BUILD_PID) — will continue in background"
    echo "[remote] Check progress: tail -f $BUILD_LOG"
else
    wait $BUILD_PID || true
    echo "[remote] Build done"
fi
sleep 1
$COMPOSE_CMD -f "$COMPOSE_FILE" ps 2>/dev/null || true
echo "[remote] Remote deploy complete — check with: docker ps | grep car-calib-pi5"

# Clean old releases (keep 3)
cd "${root_dir}/releases"
ls -1dt */ 2>/dev/null | tail -n +4 | xargs -r rm -rf -- || true

echo "[remote] Deploy complete"
REMOTE_EOF

# --------------------------------------------------------------------------- #
# Done
# --------------------------------------------------------------------------- #
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   Remote Deploy Complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
echo -e "  Dashboard:  ${BLUE}http://${PI5_HOST}:${DASHBOARD_PORT}${NC}"
echo -e "  Logs:       ${BLUE}ssh ${PI5_USER}@${PI5_HOST} 'docker logs -f car-calib-pi5'${NC}"
echo ""

rm -f "$ARCHIVE_PATH"
