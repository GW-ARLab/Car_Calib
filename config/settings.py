"""Environment-backed application settings.

All tunable parameters should be defined here and consumed by runtime modules.
Values are loaded from environment variables (including .env via python-dotenv)
with sane defaults for local development.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ValueError(
        f"Invalid boolean for {name}: {value!r}. "
        "Use one of: true/false, 1/0, yes/no, on/off."
    )


# --------------------------------------------------------------------------- #
# Main loop settings
# --------------------------------------------------------------------------- #
MAIN_TARGET_HZ = _get_float("MAIN_TARGET_HZ", 30.0)
MAIN_CAMERA_INDEX = _get_int("MAIN_CAMERA_INDEX", 0)
MAIN_CSV_LOG_FILE = _get_str("MAIN_CSV_LOG_FILE", "run_log.csv")
MAIN_FLIP_FRAME = _get_bool("MAIN_FLIP_FRAME", False)
MAIN_TERMINAL_LOG = _get_bool("MAIN_TERMINAL_LOG", True)
MAIN_DEBUG_MODE = _get_bool("MAIN_DEBUG_MODE", False)
MAIN_DEBUG_VISUALIZER = _get_str("MAIN_DEBUG_VISUALIZER", "")
MAIN_SHOW_PREVIEW = _get_bool("MAIN_SHOW_PREVIEW", False)
MAIN_SHOW_GUIDANCE_OVERLAY = _get_bool("MAIN_SHOW_GUIDANCE_OVERLAY", True)
MAIN_SHOW_VISION_DEBUG = _get_bool("MAIN_SHOW_VISION_DEBUG", False)
MAIN_WRITE_DEBUG_VIDEO = _get_bool("MAIN_WRITE_DEBUG_VIDEO", False)
MAIN_DEBUG_VIDEO_OUTPUT = _get_str("MAIN_DEBUG_VIDEO_OUTPUT", "main_debug.mp4")
MAIN_VIDEO_OUTPUT_FPS = _get_float("MAIN_VIDEO_OUTPUT_FPS", 20.0)
MAIN_DEBUG_FRAME_SCALE = _get_float("MAIN_DEBUG_FRAME_SCALE", 1.25)
MAIN_DEBUG_OVERLAY_SCALE = _get_float("MAIN_DEBUG_OVERLAY_SCALE", 0.75)
MAIN_CAMERA_RETRY_LIMIT = _get_int("MAIN_CAMERA_RETRY_LIMIT", 3)
MAIN_VIDEO_RETRY_LIMIT = _get_int("MAIN_VIDEO_RETRY_LIMIT", 5)
MAIN_HARDWARE_RETRY_LIMIT = _get_int("MAIN_HARDWARE_RETRY_LIMIT", 5)
LOG_ROTATE_MB = _get_int("LOG_ROTATE_MB", 64)
DATA_RETENTION_DAYS = _get_int("DATA_RETENTION_DAYS", 7)
DATA_MAX_BYTES = _get_int("DATA_MAX_BYTES", 2 * 1024 * 1024 * 1024)
DISK_MIN_FREE_BYTES = _get_int("DISK_MIN_FREE_BYTES", 512 * 1024 * 1024)

MAIN_HTTPS_STREAM_ENABLED = _get_bool("MAIN_HTTPS_STREAM_ENABLED", False)
MAIN_HTTPS_STREAM_HOST = _get_str("MAIN_HTTPS_STREAM_HOST", "127.0.0.1")
MAIN_HTTPS_STREAM_PORT = _get_int("MAIN_HTTPS_STREAM_PORT", 8443)
MAIN_HTTPS_STREAM_PUBLIC = _get_bool("MAIN_HTTPS_STREAM_PUBLIC", False)
MAIN_HTTPS_STREAM_PATH = _get_str("MAIN_HTTPS_STREAM_PATH", "/stream.mjpg")
MAIN_HTTPS_SNAPSHOT_PATH = _get_str("MAIN_HTTPS_SNAPSHOT_PATH", "/snapshot.jpg")
MAIN_HTTPS_STATUS_PATH = _get_str("MAIN_HTTPS_STATUS_PATH", "/status")
MAIN_HTTPS_TOKEN = _get_str("MAIN_HTTPS_TOKEN", "")
MAIN_HTTPS_CERT_FILE = _get_str("MAIN_HTTPS_CERT_FILE", "certs/main_stream_cert.pem")
MAIN_HTTPS_KEY_FILE = _get_str("MAIN_HTTPS_KEY_FILE", "certs/main_stream_key.pem")
MAIN_HTTPS_SELF_SIGNED_DAYS = _get_int("MAIN_HTTPS_SELF_SIGNED_DAYS", 365)

# --------------------------------------------------------------------------- #
# Direct dashboard resource and access limits
# --------------------------------------------------------------------------- #
DASHBOARD_TOKEN = _get_str("DASHBOARD_TOKEN", "")
DASHBOARD_STREAM_MAX_CLIENTS = _get_int("DASHBOARD_STREAM_MAX_CLIENTS", 2)
DASHBOARD_STREAM_FPS = _get_float("DASHBOARD_STREAM_FPS", 8.0)
DASHBOARD_STREAM_JPEG_QUALITY = _get_int("DASHBOARD_STREAM_JPEG_QUALITY", 60)
DASHBOARD_MAX_BODY_BYTES = _get_int("DASHBOARD_MAX_BODY_BYTES", 64 * 1024)
DASHBOARD_REQUEST_TIMEOUT_S = _get_float("DASHBOARD_REQUEST_TIMEOUT_S", 5.0)
DASHBOARD_MAX_THREADS = _get_int("DASHBOARD_MAX_THREADS", 16)
DASHBOARD_ROUTE_LIST_CACHE_S = _get_float("DASHBOARD_ROUTE_LIST_CACHE_S", 5.0)

# --------------------------------------------------------------------------- #
# Process video settings
# --------------------------------------------------------------------------- #
PROCESS_VIDEO_CSV_OUTPUT = _get_str("PROCESS_VIDEO_CSV_OUTPUT", "video_log.csv")
PROCESS_VIDEO_OUTPUT = _get_str("PROCESS_VIDEO_OUTPUT", "processed_video.mp4")
PROCESS_VIDEO_SEND_TO_SERVO = _get_bool("PROCESS_VIDEO_SEND_TO_SERVO", True)
PROCESS_VIDEO_TERMINAL_LOG = _get_bool("PROCESS_VIDEO_TERMINAL_LOG", False)
PROCESS_VIDEO_SHOW_GUIDANCE_OVERLAY = _get_bool("PROCESS_VIDEO_SHOW_GUIDANCE_OVERLAY", False)
PROCESS_VIDEO_SHOW_VISION_DEBUG = _get_bool("PROCESS_VIDEO_SHOW_VISION_DEBUG", False)
PROCESS_VIDEO_START_CALIB_THRESHOLD_DEG = _get_float("PROCESS_VIDEO_START_CALIB_THRESHOLD_DEG", 5.0)
PROCESS_VIDEO_STOP_CALIB_THRESHOLD_DEG = _get_float("PROCESS_VIDEO_STOP_CALIB_THRESHOLD_DEG", 3.0)
PROCESS_VIDEO_FLIP_FRAME = _get_bool("PROCESS_VIDEO_FLIP_FRAME", False)
PROCESS_VIDEO_FRAME_SLEEP_MS = _get_float("PROCESS_VIDEO_FRAME_SLEEP_MS", 0.0)

# --------------------------------------------------------------------------- #
# Robot state / PID defaults
# --------------------------------------------------------------------------- #
PID_KP = _get_float("PID_KP", 1.0)
PID_KI = _get_float("PID_KI", 0.05)
PID_KD = _get_float("PID_KD", 0.1)
PID_CALIBRATION_CLEAR_TOLERANCE_DEG = _get_float("PID_CALIBRATION_CLEAR_TOLERANCE_DEG", 1.0)
SERVO_CENTER_ANGLE = _get_float("SERVO_CENTER_ANGLE", 90.0)
SERVO_HOME_OFFSET_DEG = _get_float("SERVO_HOME_OFFSET_DEG", 0.0)
MAX_STEERING_OFFSET = _get_float("MAX_STEERING_OFFSET", 30.0)
ROI_HEIGHT_PCT = _get_float("ROI_HEIGHT_PCT", 0.6)
ROI_TOP_WIDTH_PCT = _get_float("ROI_TOP_WIDTH_PCT", 0.75)
ROI_BOTTOM_WIDTH_PCT = _get_float("ROI_BOTTOM_WIDTH_PCT", 1.0)
ROBOT_DEBUG_MODE = _get_bool("ROBOT_DEBUG_MODE", False)

# --------------------------------------------------------------------------- #
# Vision processing settings
# --------------------------------------------------------------------------- #
VISION_CLAHE_CLIP_LIMIT = _get_float("VISION_CLAHE_CLIP_LIMIT", 2.0)
VISION_CLAHE_TILE_GRID_W = _get_int("VISION_CLAHE_TILE_GRID_W", 8)
VISION_CLAHE_TILE_GRID_H = _get_int("VISION_CLAHE_TILE_GRID_H", 8)
VISION_BLUR_KERNEL_W = _get_int("VISION_BLUR_KERNEL_W", 5)
VISION_BLUR_KERNEL_H = _get_int("VISION_BLUR_KERNEL_H", 5)
VISION_CANNY_LOW = _get_int("VISION_CANNY_LOW", 50)
VISION_CANNY_HIGH = _get_int("VISION_CANNY_HIGH", 150)
VISION_HOUGH_RHO = _get_float("VISION_HOUGH_RHO", 1.0)
VISION_HOUGH_THETA_DEG = _get_float("VISION_HOUGH_THETA_DEG", 1.0)
VISION_HOUGH_THRESHOLD = _get_int("VISION_HOUGH_THRESHOLD", 50)
VISION_HOUGH_MIN_LINE_LEN = _get_int("VISION_HOUGH_MIN_LINE_LEN", 30)
VISION_HOUGH_MIN_LINE_LENGTH = _get_int("VISION_HOUGH_MIN_LINE_LENGTH", VISION_HOUGH_MIN_LINE_LEN)
VISION_HOUGH_MAX_LINE_GAP = _get_int("VISION_HOUGH_MAX_LINE_GAP", 10)
VISION_MIN_ABS_SLOPE = _get_float("VISION_MIN_ABS_SLOPE", 0.3)
VISION_ANGLE_THRESHOLD_DEG = _get_float("VISION_ANGLE_THRESHOLD_DEG", 5.0)
VISION_CLUSTER_ANGLE_BIAS_DEG = _get_float("VISION_CLUSTER_ANGLE_BIAS_DEG", 4.0)
VISION_CLUSTER_RHO_BIAS_PX = _get_float("VISION_CLUSTER_RHO_BIAS_PX", 25.0)
VISION_MIDPOINT_THRESHOLD_PX = _get_float("VISION_MIDPOINT_THRESHOLD_PX", 30.0)
VISION_SANITY_MAX_DELTA_DEG = _get_float("VISION_SANITY_MAX_DELTA_DEG", 40.0)
VISION_HORIZONTAL_MAX_ERROR_DEG = _get_float("VISION_HORIZONTAL_MAX_ERROR_DEG", 20.0)
VISION_MIN_GROUP_TOTAL_LENGTH_PX = _get_float("VISION_MIN_GROUP_TOTAL_LENGTH_PX", 120.0)
VISION_ROI_BORDER_BLACK_PX = _get_int("VISION_ROI_BORDER_BLACK_PX", 2)
VISION_ROI_EDGE_MARGIN_PX = _get_int("VISION_ROI_EDGE_MARGIN_PX", 4)
VISION_ROI_BOTTOM_CLEAR_ROWS = _get_int("VISION_ROI_BOTTOM_CLEAR_ROWS", 10)
VISION_DEBUG_MASK_FILE = _get_str("VISION_DEBUG_MASK_FILE", "debug_mask.jpg")

VP_INNER_THRESH = _get_float("VP_INNER_THRESH", 3.0)
VP_OUTER_THRESH = _get_float("VP_OUTER_THRESH", 5.0)
DANGER_MARGIN_PX = _get_int("DANGER_MARGIN_PX", 100)
DANGER_NUDGE_DEG = _get_float("DANGER_NUDGE_DEG", 5.0)
MAIN_FRAME_WIDTH = _get_int("MAIN_FRAME_WIDTH", 640)
MAIN_FRAME_HEIGHT = _get_int("MAIN_FRAME_HEIGHT", 480)

# --------------------------------------------------------------------------- #
# Heading controller settings
# --------------------------------------------------------------------------- #
CTRL_HYSTERESIS_HIGH = _get_float("CTRL_HYSTERESIS_HIGH", 5.0)
CTRL_HYSTERESIS_LOW = _get_float("CTRL_HYSTERESIS_LOW", 3.0)
CTRL_RELOCK_VALID_FRAMES = _get_int("CTRL_RELOCK_VALID_FRAMES", 3)

# --------------------------------------------------------------------------- #
# Driver defaults
# --------------------------------------------------------------------------- #
DRIVER_SERVO_CHANNEL = _get_int("DRIVER_SERVO_CHANNEL", 0)
DRIVER_SERVO_PULSE_MIN_US = _get_int("DRIVER_SERVO_PULSE_MIN_US", 1000)
DRIVER_SERVO_PULSE_MAX_US = _get_int("DRIVER_SERVO_PULSE_MAX_US", 2000)
DRIVER_SERVO_ANGLE_MIN = _get_float("DRIVER_SERVO_ANGLE_MIN", 0.0)
DRIVER_SERVO_ANGLE_MAX = _get_float("DRIVER_SERVO_ANGLE_MAX", 180.0)
DRIVER_MOTOR_PWM_MIN = _get_int("DRIVER_MOTOR_PWM_MIN", 0)
DRIVER_MOTOR_PWM_MAX = _get_int("DRIVER_MOTOR_PWM_MAX", 255)
DRIVER_MOTOR_PWM_CENTRE = _get_int("DRIVER_MOTOR_PWM_CENTRE", 128)

# --------------------------------------------------------------------------- #
# MQTT transport (servo publish + control subscribe)
# --------------------------------------------------------------------------- #
MQTT_BROKER_HOST = _get_str("MQTT_BROKER_HOST", "127.0.0.1")
MQTT_BROKER_PORT = _get_int("MQTT_BROKER_PORT", 1883)
MQTT_USERNAME = _get_str("MQTT_USERNAME", "")
MQTT_PASSWORD = _get_str("MQTT_PASSWORD", "")
MQTT_KEEPALIVE_S = _get_int("MQTT_KEEPALIVE_S", 60)

# ESP32 USB-serial actuator bridge (optional, disabled by default)
ESP32_SERIAL_ENABLED = _get_bool("ESP32_SERIAL_ENABLED", False)
ESP32_SERIAL_BAUD = _get_int("ESP32_SERIAL_BAUD", 115200)
ESP32_SERIAL_PORT_GLOBS = _get_str("ESP32_SERIAL_PORT_GLOBS", "/dev/ttyUSB*,/dev/ttyACM*")
# Actuator control mode: "auto" (try ESP32, fall back to MQTT if not found),
# "esp32" (ESP32 only, scan forever), or "mqtt" (never start the ESP32 bridge).
ACTUATOR_MODE = _get_str("ACTUATOR_MODE", "auto").strip().lower()
# In "auto" mode, give up scanning for the ESP32 after this many seconds and
# fall back to the MQTT/RPi path. "esp32" mode ignores this and scans forever.
ESP32_SCAN_TIMEOUT_S = _get_float("ESP32_SCAN_TIMEOUT_S", 10.0)
# Board target for arduino-cli compile/flash when updating ESP32 firmware
# from the dashboard. Override per board (e.g. esp32:esp32:esp32s3).
ESP32_FQBN = _get_str("ESP32_FQBN", "esp32:esp32:esp32")
MQTT_SERVO_TOPIC = _get_str("MQTT_SERVO_TOPIC", "car/servo/angle")
MQTT_BASE_COMMAND_TOPIC = _get_str("MQTT_BASE_COMMAND_TOPIC", "car/base/command")
MQTT_RELAY_TOPIC = _get_str("MQTT_RELAY_TOPIC", "car/relay")
MQTT_STATUS_TOPIC = _get_str("MQTT_STATUS_TOPIC", "car/status")
MQTT_CLIENT_ID_PREFIX = _get_str("MQTT_CLIENT_ID_PREFIX", "car-calib")
DRIVER_SERVO_MQTT_ENABLED = _get_bool("DRIVER_SERVO_MQTT_ENABLED", False)

# Pi5 vision -> Pi4 DataProcessingCenter combined steering+drive topic.
# Payload: "<angle 0-180>,<drive_code>" (see drivers/mqtt_control_publisher.py).
MQTT_RASPI5_CONTROL_TOPIC = _get_str("MQTT_RASPI5_CONTROL_TOPIC", "car/raspi5/control")
# DataProcessingCenter (Pi4) only acts on car/raspi5/control while its own
# `_mode` is "camera" -- Pi5 publishes this retained on connect so a Pi4
# left in "controller" mode from a prior session doesn't silently drop
# every command from vision.
MQTT_CONTROL_MODE_TOPIC = _get_str("MQTT_CONTROL_MODE_TOPIC", "car/control/mode")
# DataProcessingCenter (Pi4) power-relay trigger: a momentary pulse (fires
# CMD_TYPE_TRIG over UART, auto-resets itself back to "0" ~300ms later).
# Only accepts affirmative payloads ("1"/"true"/"pulse"/"trigger"/"on"); it
# has no separate on/off duration -- both dashboard power buttons fire the
# same pulse.
MQTT_TRIGGER_TOPIC = _get_str("MQTT_TRIGGER_TOPIC", "car/control/trigger")

# --------------------------------------------------------------------------- #
# Route logging / dataset acceptance
# --------------------------------------------------------------------------- #
ROUTE_LOG_ROOT = _get_str("ROUTE_LOG_ROOT", "/data/routes")
ROUTE_DIRECTION_EPS_DEG = _get_float("ROUTE_DIRECTION_EPS_DEG", 1.0)
ROUTE_ACCEPT_MIN_FRAMES = _get_int("ROUTE_ACCEPT_MIN_FRAMES", 60)
ROUTE_ACCEPT_MAX_HW_ERRORS = _get_int("ROUTE_ACCEPT_MAX_HW_ERRORS", 0)
ROUTE_ACCEPT_MAX_GAP_RATIO = _get_float("ROUTE_ACCEPT_MAX_GAP_RATIO", 0.25)
ROUTE_MAX_STEPS = _get_int("ROUTE_MAX_STEPS", 64)
ROUTE_MAX_STEP_DURATION_S = _get_float("ROUTE_MAX_STEP_DURATION_S", 30.0)
ROUTE_MAX_TOTAL_DURATION_S = _get_float("ROUTE_MAX_TOTAL_DURATION_S", 300.0)


__all__ = [name for name in globals() if name.isupper() or name.startswith("_get_")]
