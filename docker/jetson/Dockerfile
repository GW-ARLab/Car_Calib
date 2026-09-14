# syntax=docker/dockerfile:1.7
# Raspberry Pi 5 / ARM64 — vision-only, publishes steering over MQTT
#
# No GPIO here anymore: Pi5 runs the vision loop and publishes to the Pi4
# DataProcessingCenter over MQTT (car/raspi5/control). Uses arm64v8/ubuntu
# (no registry auth, works everywhere). OpenCV via pip, not apt → no GPU
# but works.
#
# Build:
#   DOCKER_BUILDKIT=1 docker build -f docker/jetson/Dockerfile -t car-calib-jetson .

FROM arm64v8/ubuntu:22.04

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ------------------------------------------------------------------ #
# Layer 1 – system packages (cached, rarely changes)
# ------------------------------------------------------------------ #
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-dev \
        libglib2.0-0 \
        libgomp1

# ------------------------------------------------------------------ #
# Layer 2 – Python deps (cached unless requirements_jetson.txt changes)
# ------------------------------------------------------------------ #
COPY requirements_jetson.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install -r /tmp/requirements.txt

# ------------------------------------------------------------------ #
# Layer 3 – App source (only layer rebuilt on code change — seconds)
# ------------------------------------------------------------------ #
COPY . /app
RUN mkdir -p /data/logs /data/certs

ENV MAIN_CSV_LOG_FILE=/data/logs/run_log.csv \
    MAIN_TERMINAL_LOG=true \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080

ENTRYPOINT ["python3", "main_jetson.py"]
