"""MQTT publisher for Pi5 vision -> Pi4 DataProcessingCenter.

Pi5 no longer drives any GPIO directly (no servo/base/relay hardware here).
It only computes steering from vision and publishes it over MQTT for the
Pi4 data-processing center to relay to the ESP32 over UART.

Payload format on MQTT_RASPI5_CONTROL_TOPIC matches what the Pi4 side
(`DataProcessingCenter._process_raspi5_control`) expects: "<angle>,<drive_code>"
e.g. "105,1" -> angle=105, drive_code=1 (forward).

NOTE on drive_code: per hex_protocol.py (the 16-char HEX-ASCII frame sent to
ESP32), a packet only carries {cmd_type, drive_code, servo_angle, flags} --
there is no per-direction command. Steering (left/right) is expressed
entirely through servo_angle, not drive_code. Only drive_code=0 (STOP) and
1 (FORWARD) are confirmed by the Pi4 reference implementation; BACKWARD is
an unconfirmed guess (2) since DataProcessingCenter never sets the `flags`
byte's "rev" bit (0x01) when forwarding drive_code, so a firmware built
around that flag would never see it via this path. This repo's legacy
tri-state base commands (TURN_LEFT/TURN_RIGHT/LOCK/UNLOCK) have no ESP32
equivalent at all and are mapped to STOP with a warning rather than
inventing codes the firmware likely doesn't recognize.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from config.settings import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
    MQTT_CLIENT_ID_PREFIX,
    MQTT_CONTROL_MODE_TOPIC,
    MQTT_KEEPALIVE_S,
    MQTT_PASSWORD,
    MQTT_RASPI5_CONTROL_TOPIC,
    MQTT_RELAY_TOPIC,
    MQTT_USERNAME,
)

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

# Confirmed against the Pi4 reference implementation: STOP=0, FORWARD=1.
# BACKWARD=2 is an unconfirmed guess. Commands with no ESP32 equivalent
# (steering is done via servo_angle, not drive_code) fall back to STOP.
DRIVE_CODE_MAP: dict[str, int] = {
    "STOP": 0,
    "FORWARD": 1,
    "BACKWARD": 2,  # unconfirmed -- verify against ESP32 firmware
}
_NO_EQUIVALENT_COMMANDS = {"TURN_LEFT", "TURN_RIGHT", "LOCK", "UNLOCK"}


class Raspi5MqttPublisher:
    """Publishes steering + drive state from Pi5 vision to the MQTT broker."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or MQTT_BROKER_HOST
        self.port = port or MQTT_BROKER_PORT
        self._client: Any = None
        self._connected = False
        self._lock = threading.Lock()

        if mqtt is None:
            logger.warning("paho-mqtt not installed; Pi5 MQTT publisher disabled")
            return
        try:
            if hasattr(mqtt, "CallbackAPIVersion"):
                self._client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"{MQTT_CLIENT_ID_PREFIX}-pi5-vision",
                )
            else:
                self._client = mqtt.Client(client_id=f"{MQTT_CLIENT_ID_PREFIX}-pi5-vision")
            if MQTT_USERNAME:
                self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.will_set("car/status/state", payload="offline", retain=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to initialize Pi5 MQTT publisher: %s", exc)
            self._client = None

    def connect(self) -> bool:
        if self._client is None:
            return False
        try:
            self._client.connect(self.host, self.port, keepalive=MQTT_KEEPALIVE_S)
            self._client.loop_start()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Pi5 MQTT connect failed %s:%s - %s", self.host, self.port, exc)
            return False

    def _on_connect(self, client: Any, userdata: Any, flags: Any = None, rc: int = 0, properties: Any = None, *args: Any, **kwargs: Any) -> None:
        self._connected = True
        client.publish("car/status/state", payload="online", retain=True)
        client.publish("car/status/device", payload="RPi5_Vision", retain=True)
        # Force the Pi4 back into "camera" mode so it doesn't silently
        # ignore car/raspi5/control if it was left in "controller" mode
        # from a prior manual-control session.
        client.publish(MQTT_CONTROL_MODE_TOPIC, payload="camera", retain=True)
        logger.info("Pi5 MQTT publisher connected to %s:%s", self.host, self.port)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any, **kwargs: Any) -> None:
        self._connected = False
        logger.warning("Pi5 MQTT publisher disconnected")

    @property
    def connected(self) -> bool:
        return self._connected

    def publish_mode_camera(self) -> None:
        """Re-assert car/control/mode=camera on the Pi4 (retained).

        Call this whenever a control command originates from this Pi5 --
        vision-auto, a route script starting, or a joystick/manual override
        becoming active -- so a Pi4 that drifted into "controller" mode
        (e.g. a phone app driving it directly) is pulled back before the
        command that follows is dropped.
        """
        if self._client is None or not self._connected:
            return
        try:
            self._client.publish(MQTT_CONTROL_MODE_TOPIC, payload="camera", retain=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pi5 MQTT publish_mode_camera error: %s", exc)

    def publish_control(self, servo_angle_0_180: float, base_command: str) -> None:
        """Publish steering + drive state as "<angle>,<drive_code>"."""
        if self._client is None or not self._connected:
            return
        cmd = base_command.upper()
        if cmd in _NO_EQUIVALENT_COMMANDS:
            logger.warning("base command %s has no ESP32 drive_code equivalent, sending STOP", cmd)
        drive_code = DRIVE_CODE_MAP.get(cmd, 0)
        angle_int = max(0, min(180, int(round(servo_angle_0_180))))
        try:
            self._client.publish(MQTT_RASPI5_CONTROL_TOPIC, payload=f"{angle_int},{drive_code}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pi5 MQTT publish_control error: %s", exc)

    def publish_relay(self, state: str) -> None:
        if self._client is None or not self._connected:
            return
        try:
            self._client.publish(MQTT_RELAY_TOPIC, payload=state.upper())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pi5 MQTT publish_relay error: %s", exc)

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.publish("car/status/state", payload="offline", retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass
