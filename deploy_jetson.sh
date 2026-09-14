#!/usr/bin/env bash
set -euo pipefail

# =========================================================================== #
# Jetson Nano deploy script — one command to build & run
# =========================================================================== #

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
SKIP_BUILD=false
FORCE_RECREATE=false

usage() {
    cat <<'EOF'
Usage:
  ./deploy_jetson.sh [--no-build] [--recreate] [--env <path>]

Options:
  --no-build    Skip Docker image build (reuse existing image)
  --recreate    Force recreate container (docker up --force-recreate)
  --env <path>  Use custom env file (default: .env)

Setup:
  1. Copy .env.jetson → .env and set MQTT_BROKER_HOST to the Pi4/broker
  2. Run ./deploy_jetson.sh
  3. Dashboard: http://<pi5-ip>:8080
EOF
}

# --------------------------------------------------------------------------- #
# Parse args
# --------------------------------------------------------------------------- #
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build)
            SKIP_BUILD=true
            shift
            ;;
        --recreate)
            FORCE_RECREATE=true
            shift
            ;;
        --env)
            ENV_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# --------------------------------------------------------------------------- #
# Prerequisites
# --------------------------------------------------------------------------- #

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Jetson Nano Deploy${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Docker
if ! command -v docker &>/dev/null; then
    echo -e "${RED}Docker not found. Install first:${NC}"
    echo "  sudo apt-get update && sudo apt-get install -y docker.io"
    echo "  sudo usermod -aG docker \$USER && newgrp docker"
    exit 1
fi

# docker compose
COMPOSE_CMD=""
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo -e "${RED}Docker Compose not found.${NC}"
    exit 1
fi

# .env
if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${YELLOW}No .env found at $ENV_FILE${NC}"
    echo -e "${YELLOW}Copying .env.jetson → .env (review before deploying!)${NC}"
    cp .env.jetson "$ENV_FILE"
    echo ""
    echo -e "${YELLOW}Review $ENV_FILE and re-run.${NC}"
    echo -e "${YELLOW}Key vars to verify: MQTT_BROKER_HOST, DASHBOARD_TOKEN${NC}"
    exit 0
fi

# --------------------------------------------------------------------------- #
# Build & Start
# --------------------------------------------------------------------------- #

COMPOSE_FILE=docker-compose.jetson.yml
CONTAINER_NAME=car-calib-jetson

# Stop old if running
if docker ps -q --filter "name=$CONTAINER_NAME" | grep -q .; then
    echo -e "${YELLOW}[0/3] Stopping old container...${NC}"
    $COMPOSE_CMD -f "$COMPOSE_FILE" down 2>/dev/null || true
fi

# Build
if [[ "$SKIP_BUILD" != "true" ]]; then
    echo -e "${BLUE}[1/3] Building Docker image...${NC}"
    $COMPOSE_CMD -f "$COMPOSE_FILE" build
else
    echo -e "${YELLOW}[1/3] Skipping build (--no-build)${NC}"
fi

# Start
echo -e "${BLUE}[2/3] Starting container...${NC}"
if [[ "$FORCE_RECREATE" == "true" ]]; then
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans
else
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d --remove-orphans
fi

# Health check
echo -e "${BLUE}[3/3] Waiting for startup...${NC}"
sleep 2
if docker ps -q --filter "name=$CONTAINER_NAME" | grep -q .; then
    echo -e "${GREEN}Container running.${NC}"
else
    echo -e "${RED}Container failed to start. Checking logs:${NC}"
    docker logs --tail 30 "$CONTAINER_NAME" 2>/dev/null || true
    exit 1
fi

# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #

JETSON_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
PORT="${DASHBOARD_PORT:-8080}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   Jetson Nano Deploy Complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  Dashboard:  ${BLUE}http://${JETSON_IP}:${PORT}${NC}"
echo -e "  Logs:       ${BLUE}docker logs -f ${CONTAINER_NAME}${NC}"
echo -e "  Restart:    ${BLUE}docker restart ${CONTAINER_NAME}${NC}"
echo -e "  Stop:       ${BLUE}$COMPOSE_CMD -f ${COMPOSE_FILE} down${NC}"
echo -e "  Status:     ${BLUE}docker ps --filter name=${CONTAINER_NAME}${NC}"
echo ""

# Show first few log lines
echo -e "${BLUE}--- Last 10 log lines ---${NC}"
docker logs --tail 10 "$CONTAINER_NAME" 2>/dev/null || true
