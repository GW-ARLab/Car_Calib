"""Unified calibration module core classes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from importlib import import_module
from math import hypot
from pathlib import Path
from typing import Any, TextIO

import cv2
import numpy as np

from config.settings import _get_bool, _get_float, _get_int, _get_str
from control.steering_controller import SteeringController
from models.robot_state import FSMState, PIDConstants, RobotState
from runtime.overlay_drawer import OverlayDrawer


def _normalize_debug_visualizer(value: Any) -> str:
    """Normalize debug visualizer mode from environment/config input."""
    mode = str(value).strip().lower()
    return mode if mode in {"imshow", "video", "both"} else ""


def _get_optional_float(name: str) -> float | None:
    value = _get_str(name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        logging.getLogger(__name__).warning("Ignoring invalid %s=%r", name, value)
        return None


@dataclass(frozen=True)
class CalibrationResult:
    """Central result returned by one unified calibration computation."""

    steering_angle: float
    control_state: str
    observation_angle: float | None
    calibration_active: bool
    telemetry: dict[str, Any]
    debug_data: dict[str, Any]


@dataclass(frozen=True)
class CalibrationDiagnostic:
    """Structured context identifying a failed calibration operation."""

    frame_num: int
    stage: str
    process: str
    error_type: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serializable diagnostic fields."""
        return {
            "frame_num": self.frame_num,
            "stage": self.stage,
            "process": self.process,
            "error_type": self.error_type,
            "detail": self.detail,
        }


class CalibrationProcessingError(RuntimeError):
    """Failure raised with the exact calibration stage and process."""

    def __init__(
        self,
        *,
        frame_num: int,
        stage: str,
        process: str,
        cause: Exception,
    ) -> None:
        self.diagnostic = CalibrationDiagnostic(
            frame_num=frame_num,
            stage=stage,
            process=process,
            error_type=type(cause).__name__,
            detail=str(cause),
        )
        super().__init__(
            f"frame={frame_num} stage={stage} process={process} "
            f"error={type(cause).__name__}: {cause}"
        )


class ConfigManager:
    """Registry for runtime configuration values used by the unified module."""

    def __init__(self) -> None:
        self._system_configs = {
            "MAIN_TARGET_HZ": _get_float("MAIN_TARGET_HZ", 30.0),
            "MAIN_CAMERA_INDEX": _get_int("MAIN_CAMERA_INDEX", 0),
            "MAIN_FLIP_FRAME": _get_bool("MAIN_FLIP_FRAME", False),
            "MAIN_TERMINAL_LOG": _get_bool("MAIN_TERMINAL_LOG", True),
        }
        self._debug_configs = {
            "MAIN_DEBUG_MODE": _get_bool("MAIN_DEBUG_MODE", False),
            "MAIN_DEBUG_VISUALIZER": _get_str("MAIN_DEBUG_VISUALIZER", ""),
            "MAIN_SHOW_PREVIEW": _get_bool("MAIN_SHOW_PREVIEW", False),
            "MAIN_SHOW_GUIDANCE_OVERLAY": _get_bool(
                "MAIN_SHOW_GUIDANCE_OVERLAY",
                True,
            ),
        }
        self._video_configs = {
            "MAIN_WRITE_DEBUG_VIDEO": _get_bool("MAIN_WRITE_DEBUG_VIDEO", False),
            "MAIN_DEBUG_VIDEO_OUTPUT": _get_str(
                "MAIN_DEBUG_VIDEO_OUTPUT",
                "main_debug.mp4",
            ),
            "MAIN_VIDEO_OUTPUT_FPS": _get_float("MAIN_VIDEO_OUTPUT_FPS", 20.0),
        }
        self._stream_configs = {
            "MAIN_HTTPS_STREAM_ENABLED": _get_bool("MAIN_HTTPS_STREAM_ENABLED", False),
            "MAIN_HTTPS_STREAM_HOST": _get_str("MAIN_HTTPS_STREAM_HOST", "127.0.0.1"),
            "MAIN_HTTPS_STREAM_PORT": _get_int("MAIN_HTTPS_STREAM_PORT", 8443),
            "MAIN_HTTPS_STREAM_PUBLIC": _get_bool("MAIN_HTTPS_STREAM_PUBLIC", False),
            "MAIN_HTTPS_STREAM_PATH": _get_str("MAIN_HTTPS_STREAM_PATH", "/stream.mjpg"),
            "MAIN_HTTPS_SNAPSHOT_PATH": _get_str(
                "MAIN_HTTPS_SNAPSHOT_PATH",
                "/snapshot.jpg",
            ),
            "MAIN_HTTPS_STATUS_PATH": _get_str("MAIN_HTTPS_STATUS_PATH", "/status"),
            "MAIN_HTTPS_TOKEN": _get_str("MAIN_HTTPS_TOKEN", ""),
            "MAIN_HTTPS_CERT_FILE": _get_str(
                "MAIN_HTTPS_CERT_FILE",
                "certs/main_stream_cert.pem",
            ),
            "MAIN_HTTPS_KEY_FILE": _get_str(
                "MAIN_HTTPS_KEY_FILE",
                "certs/main_stream_key.pem",
            ),
            "MAIN_HTTPS_SELF_SIGNED_DAYS": _get_int("MAIN_HTTPS_SELF_SIGNED_DAYS", 365),
        }
        self._vp_inner_thresh = _get_float("VP_INNER_THRESH", 3.0)
        self._vp_outer_thresh = _get_float("VP_OUTER_THRESH", 5.0)
        self._danger_margin_px = _get_int("DANGER_MARGIN_PX", 100)
        self._danger_nudge_deg = _get_float("DANGER_NUDGE_DEG", 5.0)

    def get_system_configs(self) -> dict:
        """Return system loop and camera configuration."""
        return dict(self._system_configs)

    def get_debug_configs(self) -> dict:
        """Return debug and visualization toggles."""
        return dict(self._debug_configs)

    def get_video_configs(self) -> dict:
        """Return debug video output settings."""
        return dict(self._video_configs)

    def get_stream_configs(self) -> dict:
        """Return HTTPS stream configuration values."""
        return dict(self._stream_configs)

    def get_vp_thresholds(self) -> tuple[float, float]:
        """Return vanishing-point hysteresis thresholds (inner, outer)."""
        return self._vp_inner_thresh, self._vp_outer_thresh

    def get_danger_margins(self) -> tuple[int, float]:
        """Return danger margin in pixels and fixed nudge angle in degrees."""
        return self._danger_margin_px, self._danger_nudge_deg


class VisionProcessor:
    """Frame pre-processor and Stage-1 line extractor."""

    def __init__(
        self,
        roi_height_pct: float,
        roi_top_width_pct: float = 1.0,
        roi_bottom_width_pct: float = 1.0,
    ) -> None:
        self._roi_height_pct = max(0.0, min(1.0, roi_height_pct))
        self._roi_top_width_pct = max(0.05, min(1.0, roi_top_width_pct))
        self._roi_bottom_width_pct = max(0.05, min(1.0, roi_bottom_width_pct))
        self._blur_w = _get_int("VISION_BLUR_KERNEL_W", 5)
        self._blur_h = _get_int("VISION_BLUR_KERNEL_H", 5)
        self._canny_low = _get_int("VISION_CANNY_LOW", 50)
        self._canny_high = _get_int("VISION_CANNY_HIGH", 150)
        self._hough_threshold = _get_int("VISION_HOUGH_THRESHOLD", 40)
        self._hough_min_line_length = _get_int("VISION_HOUGH_MIN_LINE_LENGTH", 40)
        self._hough_max_line_gap = _get_int("VISION_HOUGH_MAX_LINE_GAP", 20)
        self._min_abs_slope = _get_float("VISION_MIN_ABS_SLOPE", 0.3)
        # Image row where lane lines converge (camera pitch is fixed). Default
        # is the median VP row measured on the Pi5 car camera at 640x480.
        self._vp_horizon_y = _get_float("VISION_VP_HORIZON_Y", -518.0)

    def _apply_trapezoid_mask(self, edges: np.ndarray) -> np.ndarray:
        """Zero out edges outside a trapezoid narrowing from bottom to top width."""
        if self._roi_top_width_pct >= 1.0 and self._roi_bottom_width_pct >= 1.0:
            return edges

        h, w = edges.shape[:2]
        top_w = w * self._roi_top_width_pct
        bottom_w = w * self._roi_bottom_width_pct
        polygon = np.array(
            [[
                ((w - top_w) / 2, 0),
                ((w + top_w) / 2, 0),
                ((w + bottom_w) / 2, h - 1),
                ((w - bottom_w) / 2, h - 1),
            ]],
            dtype=np.int32,
        )
        mask = np.zeros_like(edges)
        cv2.fillPoly(mask, polygon, 255)
        return cv2.bitwise_and(edges, mask)

    def process_frame(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Extract raw Hough line segments from the configured top ROI."""
        lines, _ = self.process_frame_debug(frame)
        return lines

    def process_frame_debug(
        self,
        frame: np.ndarray,
    ) -> tuple[list[tuple[int, int, int, int]], dict[str, Any]]:
        """Extract line segments and return the visualization intermediates."""
        if frame is None or frame.size == 0:
            blank = np.zeros((1, 1), dtype=np.uint8)
            return [], {
                "gray": blank,
                "roi": blank,
                "preprocessed": blank,
                "edges": blank,
                "hough_vis": cv2.cvtColor(blank, cv2.COLOR_GRAY2BGR),
                "grouped_vis": cv2.cvtColor(blank, cv2.COLOR_GRAY2BGR),
                "roi_height": 1,
                "edge_pixels": 0,
                "lines_count": 0,
                "left_candidate_lines": 0,
                "right_candidate_lines": 0,
                "selected_lines": [],
            }

        frame_height = frame.shape[0]
        roi_height = max(1, int(frame_height * self._roi_height_pct))
        roi = frame[:roi_height, :]

        if len(roi.shape) == 3 and roi.shape[2] == 3:
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            roi_bgr = roi.copy()
        else:
            gray = roi
            roi_bgr = cv2.cvtColor(roi, cv2.COLOR_GRAY2BGR)

        blur_w = self._blur_w if self._blur_w % 2 == 1 else self._blur_w + 1
        blur_h = self._blur_h if self._blur_h % 2 == 1 else self._blur_h + 1
        preprocessed = cv2.GaussianBlur(gray, (blur_w, blur_h), 0)
        edges = cv2.Canny(preprocessed, self._canny_low, self._canny_high)
        edges = self._apply_trapezoid_mask(edges)

        raw_lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=self._hough_threshold,
            minLineLength=self._hough_min_line_length,
            maxLineGap=self._hough_max_line_gap,
        )
        lines = [] if raw_lines is None else [
            (int(x1), int(y1), int(x2), int(y2))
            for x1, y1, x2, y2 in raw_lines.reshape(-1, 4).tolist()
        ]
        hough_vis = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
        for x1, y1, x2, y2 in lines:
            cv2.line(hough_vis, (x1, y1), (x2, y2), (80, 80, 255), 1)
        left_candidates, right_candidates = self._candidate_line_counts(lines)
        return lines, {
            "gray": gray,
            "roi": roi_bgr,
            "preprocessed": preprocessed,
            "edges": edges,
            "hough_vis": hough_vis,
            "grouped_vis": roi_bgr.copy(),
            "roi_height": roi_height,
            "edge_pixels": int(cv2.countNonZero(edges)),
            "lines_count": len(lines),
            "left_candidate_lines": left_candidates,
            "right_candidate_lines": right_candidates,
            "selected_lines": [],
        }

    def _candidate_line_counts(
        self,
        lines: list[tuple[int, int, int, int]],
    ) -> tuple[int, int]:
        left = 0
        right = 0
        for x1, y1, x2, y2 in lines:
            dx = x2 - x1
            if dx == 0:
                continue
            slope = (y2 - y1) / dx
            if abs(slope) < self._min_abs_slope:
                continue
            if slope < 0:
                left += 1
            elif slope > 0:
                right += 1
        return left, right

    def _apply_geometric_filter(
        self,
        lines: list[tuple[int, int, int, int]],
        frame_width: int,
        frame_height: int,
    ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
        """Select the lane lines nearest the image centre on each side.

        Only lines of the dominant longitudinal family are considered: each
        line votes where it crosses the horizon row, and the family with the
        most supporting length wins. This keeps transverse tile grout lines
        out of the pair and does not depend on slope sign, which flips to
        the same side for both lane lines once the car is yawed.
        """
        longitudinal = [
            line for line in lines
            if line[0] == line[2]
            or abs((line[3] - line[1]) / (line[2] - line[0])) >= self._min_abs_slope
        ]
        family = self._dominant_family(longitudinal, frame_width)
        if not family:
            return None

        # Merge collinear fragments by bottom intercept; keep the longest one.
        groups: list[dict[str, Any]] = []
        for x_bottom, line in sorted(
            (_x_at_row(line, frame_height), line) for line in family
        ):
            if groups and x_bottom - groups[-1]["last_x"] <= LANE_CLUSTER_TOL_PX:
                group = groups[-1]
            else:
                group = {"lines": [], "length": 0.0}
                groups.append(group)
            group["last_x"] = x_bottom
            group["lines"].append(line)
            group["length"] += _line_length(line)

        center_x = frame_width / 2.0
        left: tuple[float, tuple[int, int, int, int]] | None = None
        right: tuple[float, tuple[int, int, int, int]] | None = None
        for group in groups:
            if group["length"] < LANE_MIN_GROUP_LENGTH_PX:
                continue
            line = max(group["lines"], key=_line_length)
            x_bottom = _x_at_row(line, frame_height)
            if x_bottom < center_x:
                if left is None or x_bottom > left[0]:
                    left = (x_bottom, line)
            elif right is None or x_bottom < right[0]:
                right = (x_bottom, line)

        if left is None or right is None:
            return None
        return left[1], right[1]

    def _dominant_family(
        self,
        lines: list[tuple[int, int, int, int]],
        frame_width: int,
    ) -> list[tuple[int, int, int, int]]:
        """Return the lines whose horizon crossings cluster with the most length."""
        votes = [
            (x_horizon, _line_length(line), line)
            for line in lines
            if abs(x_horizon := _x_at_row(line, self._vp_horizon_y)) < 4 * frame_width
        ]
        best: list[tuple[float, float, tuple[int, int, int, int]]] = []
        best_length = 0.0
        for anchor, _, _ in votes:
            members = [vote for vote in votes if abs(vote[0] - anchor) <= VP_FAMILY_TOL_PX]
            support = sum(vote[1] for vote in members)
            if support > best_length:
                best, best_length = members, support
        return [vote[2] for vote in best]


# Lane-pair selection tolerances (pixels, 640x480 frame).
VP_FAMILY_TOL_PX = 30.0
LANE_CLUSTER_TOL_PX = 25.0
LANE_MIN_GROUP_LENGTH_PX = 60.0


def _line_length(line: tuple[int, int, int, int]) -> float:
    return hypot(line[2] - line[0], line[3] - line[1])


def _x_at_row(line: tuple[int, int, int, int], y: float) -> float:
    x1, y1, x2, y2 = line
    if y2 == y1:
        return float(x1)
    return x1 + (y - y1) * (x2 - x1) / (y2 - y1)


class GeometryCalculator:
    """Stateless geometry helpers for line intersection and mapping."""

    @staticmethod
    def describe_line(
        line: tuple[int, int, int, int],
        *,
        role: str,
        bottom_intercept: int | None = None,
    ) -> dict[str, Any]:
        """Return explicit diagnostic geometry for a selected line."""
        x1, y1, x2, y2 = line
        dx = x2 - x1
        dy = y2 - y1
        slope = None if dx == 0 else float(dy) / float(dx)
        return {
            "role": role,
            "endpoints": [int(x1), int(y1), int(x2), int(y2)],
            "slope": slope,
            "length": hypot(dx, dy),
            "bottom_intercept": bottom_intercept,
        }

    @staticmethod
    def classify_point(
        point: tuple[int, int] | None,
        frame_width: int,
        frame_height: int,
    ) -> str:
        """Classify a point without clamping its raw coordinates."""
        if point is None:
            return "missing"
        x, y = point
        horizontal = "left" if x < 0 else "right" if x >= frame_width else ""
        vertical = "above" if y < 0 else "below" if y >= frame_height else ""
        if not horizontal and not vertical:
            return "inside"
        return "_".join(part for part in (vertical, horizontal) if part)

    @staticmethod
    def calculate_vanishing_point(
        line1: tuple[int, int, int, int],
        line2: tuple[int, int, int, int],
    ) -> tuple[int, int] | None:
        """Return the intersection point of two lines, or None if parallel."""
        x1, y1, x2, y2 = line1
        x3, y3, x4, y4 = line2

        denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if denominator == 0:
            return None

        det1 = x1 * y2 - y1 * x2
        det2 = x3 * y4 - y3 * x4

        px = (det1 * (x3 - x4) - (x1 - x2) * det2) / denominator
        py = (det1 * (y3 - y4) - (y1 - y2) * det2) / denominator
        return int(round(px)), int(round(py))

    @staticmethod
    def calculate_bottom_intercepts(
        line1: tuple[int, int, int, int],
        line2: tuple[int, int, int, int],
        frame_height: int,
    ) -> tuple[int, int]:
        """Return x-intercepts where each line crosses y=frame_height."""
        y_target = float(frame_height)

        def x_at_y(line: tuple[int, int, int, int]) -> int:
            x1, y1, x2, y2 = line
            if y2 == y1:
                return int(x1)
            if x2 == x1:
                return int(x1)
            t = (y_target - y1) / (y2 - y1)
            x = x1 + t * (x2 - x1)
            return int(round(x))

        return x_at_y(line1), x_at_y(line2)

    @staticmethod
    def map_vp_to_angle(vp_x: int, frame_width: int) -> float:
        """Map vanishing-point x-coordinate to linear proxy heading angle."""
        if frame_width <= 0:
            return 90.0
        return (180.0 / float(frame_width)) * float(vp_x)


class TelemetryLogger:
    """Bridge telemetry, visuals, CSV logging, and streaming to legacy utilities."""

    def __init__(self, config: ConfigManager) -> None:
        self._logger = logging.getLogger(__name__)
        self._danger_margin_px, _ = config.get_danger_margins()
        self._inner_thresh, self._outer_thresh = config.get_vp_thresholds()
        self._debug_configs = config.get_debug_configs()
        self._video_configs = config.get_video_configs()
        self._debug_mode_enabled = bool(self._debug_configs.get("MAIN_DEBUG_MODE", False))
        self._debug_visualizer = _normalize_debug_visualizer(
            self._debug_configs.get("MAIN_DEBUG_VISUALIZER", ""),
        )
        self._stream_configs = config.get_stream_configs()
        self._stream_enabled = bool(self._stream_configs.get("MAIN_HTTPS_STREAM_ENABLED", False))
        self._csv_fieldnames = [
            "frame_num",
            "mono_timestamp",
            "utc_timestamp",
            "loop_ms",
            "loop_overrun_ms",
            "frame_width",
            "frame_height",
            "roi_height",
            "edge_pixels",
            "fsm_state",
            "danger_boundary",
            "recovery_direction",
            "danger_threshold_x",
            "danger_zone_active",
            "danger_margin_px",
            "danger_action",
            "vision_lost",
            "lost_frames",
            "recovery_active",
            "recovery_angle",
            "calibration_active",
            "theta",
            "theta_source",
            "theta_for_overlay",
            "lines_count",
            "total_hough_lines",
            "left_candidate_lines",
            "right_candidate_lines",
            "selected_pair_found",
            "vp_location",
            "selected_left_line",
            "selected_left_slope",
            "selected_right_line",
            "selected_right_slope",
            "tracking_active",
            "servo_angle",
            "servo_center_angle",
            "servo_offset",
            "pid_error",
            "derivative_error",
            "pid_p_term",
            "pid_i_term",
            "pid_d_term",
            "pid_integral",
            "pid_last_error",
            "hardware_send_latency_ms",
            "stream_enabled",
            "stream_host",
            "stream_port",
            "vp_x",
            "vp_y",
            "vp_angle",
            "left_intercept",
            "right_intercept",
            "lane_width_px",
            "expected_lane_width_px",
            "width_error_px",
        ]

        self._draw_overlay_fn: Any = None
        self._build_vision_debug_panel_fn: Any = None
        self._sleep_remainder_fn: Any = None
        self._overlay_drawer = OverlayDrawer(
            inner_thresh=self._inner_thresh,
            outer_thresh=self._outer_thresh,
            danger_margin_px=self._danger_margin_px,
            roi_height_pct=_get_float("ROI_HEIGHT_PCT", 0.6),
            roi_top_width_pct=_get_float("ROI_TOP_WIDTH_PCT", 0.75),
            roi_bottom_width_pct=_get_float("ROI_BOTTOM_WIDTH_PCT", 1.0),
        )
        self._csv_writer: Any = None
        self._csv_file: TextIO | None = None
        self._csv_sink: Any = None
        self._logging_error = ""
        self._video_writer: Any = None
        self._video_path: Path | None = None
        self._video_width = _get_int("MAIN_FRAME_WIDTH", 640)
        self._video_height = _get_int("MAIN_FRAME_HEIGHT", 480)
        self._run_id = self._build_run_id()
        self._run_day_folder = time.strftime("%d_%m_%Y")
        self._run_dir = Path("logs") / self._run_day_folder / self._run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._logger.info("Run artifacts will be stored in %s", self._run_dir)
        self._frame_store: Any = None
        self._stream_server: Any = None
        self._load_legacy_visual_and_logging_bridges()
        self._load_stream_bridge()

    def _build_run_id(self) -> str:
        """Generate a stable run identifier for grouping artifacts."""
        timestamp = time.strftime("%H_%M_%S")
        millis = int((time.time() % 1.0) * 1000.0)
        return f"run_{timestamp}_{millis:03d}"

    def _resolve_run_artifact_path(self, configured_path: str, fallback_name: str) -> str:
        """Resolve output artifacts to the shared run directory for relative paths."""
        src = Path(configured_path)
        if src.is_absolute():
            return str(src)
        file_name = src.name or fallback_name
        return str(self._run_dir / file_name)

    def _load_legacy_visual_and_logging_bridges(self) -> None:
        """Load legacy runtime.video_runtime_helpers APIs if available."""
        try:
            helpers = import_module("runtime.video_runtime_helpers")
        except ModuleNotFoundError:
            return

        self._draw_overlay_fn = getattr(helpers, "draw_overlay", None)
        self._build_vision_debug_panel_fn = getattr(helpers, "build_vision_debug_panel", None)
        self._sleep_remainder_fn = getattr(helpers, "sleep_remainder", None)
        init_csv_logger = getattr(helpers, "init_csv_logger", None)
        rotating_csv_logger = getattr(helpers, "RotatingCsvLogger", None)
        init_video_writer = getattr(helpers, "init_video_writer", None)

        if callable(rotating_csv_logger):
            csv_path = _get_str("MAIN_CSV_LOG_FILE", "run_logs.csv")
            resolved_csv_path = self._resolve_run_artifact_path(csv_path, "run_logs.csv")
            try:
                self._csv_sink = rotating_csv_logger(
                    resolved_csv_path,
                    self._csv_fieldnames,
                    max_bytes=max(1, _get_int("LOG_ROTATE_MB", 64)) * 1024 * 1024,
                )
                self._logger.info("Telemetry CSV logging to %s", self._csv_sink.path)
            except OSError as exc:
                self._logging_error = str(exc)
                self._logger.warning("Telemetry CSV disabled: %s", exc)
        elif callable(init_csv_logger):
            # Compatibility fallback for installations that still ship old helpers.
            csv_path = _get_str("MAIN_CSV_LOG_FILE", "run_logs.csv")
            resolved_csv_path = self._resolve_run_artifact_path(csv_path, "run_logs.csv")
            self._csv_writer, self._csv_file = init_csv_logger(resolved_csv_path, self._csv_fieldnames, use_daily_layout=False)

        legacy_video_enabled = bool(self._video_configs.get("MAIN_WRITE_DEBUG_VIDEO", False))
        visualizer_video_enabled = self._debug_mode_enabled and (
            self._debug_visualizer in {"video", "both"}
        )
        if callable(init_video_writer) and (legacy_video_enabled or visualizer_video_enabled):
            path = str(self._video_configs.get("MAIN_DEBUG_VIDEO_OUTPUT", "main_debug.mp4"))
            resolved_video_path = self._resolve_run_artifact_path(path, "main_debug.mp4")
            fps = float(self._video_configs.get("MAIN_VIDEO_OUTPUT_FPS", 20.0))
            self._video_writer = init_video_writer(
                resolved_video_path,
                fps,
                self._video_width,
                self._video_height,
            )
            self._video_path = Path(resolved_video_path)
            self._logger.info("Telemetry debug video logging to %s", resolved_video_path)

    def _load_stream_bridge(self) -> None:
        """Load and start legacy HTTPS stream transport when enabled."""
        if not self._stream_enabled:
            return
        try:
            https_stream = import_module("runtime.https_stream")
        except ModuleNotFoundError:
            return
        shared_frame_store_cls = getattr(https_stream, "SharedFrameStore", None)
        server_cls = getattr(https_stream, "HttpsMjpegServer", None)
        ensure_cert_fn = getattr(https_stream, "ensure_self_signed_cert", None)
        if callable(shared_frame_store_cls):
            self._frame_store = shared_frame_store_cls()
        if (
            self._frame_store is None
            or not callable(server_cls)
            or not callable(ensure_cert_fn)
        ):
            return
        try:
            ensure_cert_fn(
                str(self._stream_configs.get("MAIN_HTTPS_CERT_FILE", "certs/main_stream_cert.pem")),
                str(self._stream_configs.get("MAIN_HTTPS_KEY_FILE", "certs/main_stream_key.pem")),
                str(self._stream_configs.get("MAIN_HTTPS_STREAM_HOST", "127.0.0.1")),
                int(self._stream_configs.get("MAIN_HTTPS_SELF_SIGNED_DAYS", 365)),
            )
            self._stream_server = server_cls(
                host=str(self._stream_configs.get("MAIN_HTTPS_STREAM_HOST", "127.0.0.1")),
                port=int(self._stream_configs.get("MAIN_HTTPS_STREAM_PORT", 8443)),
                stream_path=str(self._stream_configs.get("MAIN_HTTPS_STREAM_PATH", "/stream.mjpg")),
                snapshot_path=str(self._stream_configs.get("MAIN_HTTPS_SNAPSHOT_PATH", "/snapshot.jpg")),
                status_path=str(self._stream_configs.get("MAIN_HTTPS_STATUS_PATH", "/status")),
                token=str(self._stream_configs.get("MAIN_HTTPS_TOKEN", "")),
                cert_file=str(self._stream_configs.get("MAIN_HTTPS_CERT_FILE", "certs/main_stream_cert.pem")),
                key_file=str(self._stream_configs.get("MAIN_HTTPS_KEY_FILE", "certs/main_stream_key.pem")),
                frame_store=self._frame_store,
            )
            self._stream_server.start()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("HTTPS stream unavailable: %s", exc)
            self._stream_server = None

    def log_state(self, frame_num: int, telemetry_data: dict[str, Any]) -> None:
        """Persist one telemetry row in CSV format when CSV logger exists."""
        if self._csv_sink is not None:
            row = {key: telemetry_data.get(key, "") for key in self._csv_fieldnames}
            row["frame_num"] = frame_num
            if not self._csv_sink.write(row):
                self._logging_error = self._csv_sink.error or "telemetry CSV disabled"
            return
        if self._csv_writer is None:
            return
        row = {key: telemetry_data.get(key, "") for key in self._csv_fieldnames}
        row["frame_num"] = frame_num
        try:
            self._csv_writer.writerow(row)
            if self._csv_file is not None:
                self._csv_file.flush()
        except OSError as exc:
            self._logging_error = str(exc)
            self._csv_writer = None
            if self._csv_file is not None:
                try:
                    self._csv_file.close()
                except OSError:
                    pass
                self._csv_file = None

    def logging_health(self) -> dict[str, Any]:
        """Expose a non-fatal CSV sink state to the dashboard runtime."""
        enabled = self._csv_writer is not None
        if self._csv_sink is not None:
            enabled = bool(self._csv_sink.enabled)
        return {"telemetry_logging_enabled": enabled, "telemetry_logging_error": self._logging_error}

    def active_storage_paths(self) -> tuple[Path, ...]:
        """Return files currently owned by live telemetry sinks."""
        paths: list[Path] = []
        if self._csv_sink is not None:
            paths.append(Path(self._csv_sink.path))
        elif self._csv_file is not None:
            paths.append(Path(self._csv_file.name))
        if self._video_path is not None:
            paths.append(self._video_path)
        return tuple(paths)

    def update_visuals(
        self,
        frame: np.ndarray,
        telemetry_data: dict[str, Any],
        debug_data: dict[str, Any],
    ) -> np.ndarray:
        """Draw the HUD and optional vision debug panel."""
        output = frame.copy()
        frame_h, frame_w = output.shape[:2]

        if self._overlay_drawer is not None:
            output = self._overlay_drawer.draw(
                output,
                {
                    "state": str(telemetry_data.get("fsm_state", "VISION_LOST")),
                    "danger_boundary": telemetry_data.get("danger_boundary"),
                    "recovery_direction": telemetry_data.get("recovery_direction"),
                    "danger_threshold_x": telemetry_data.get("danger_threshold_x"),
                    "raw_vp_angle": telemetry_data.get("vp_angle"),
                    "left_intercept_x": telemetry_data.get("left_intercept"),
                    "right_intercept_x": telemetry_data.get("right_intercept"),
                    "final_steering_cmd": telemetry_data.get("servo_angle", 90.0),
                    "lines": debug_data.get("vision_debug", {}).get("selected_lines", []),
                    "left_line": debug_data.get("vision_debug", {}).get("selected_left_line"),
                    "right_line": debug_data.get("vision_debug", {}).get("selected_right_line"),
                    "vp_coord": (
                        (
                            int(telemetry_data["vp_x"]),
                            int(telemetry_data["vp_y"]),
                        )
                        if telemetry_data.get("vp_x") is not None
                        and telemetry_data.get("vp_y") is not None
                        else None
                    ),
                    "vp_location": telemetry_data.get("vp_location", "missing"),
                },
            )
        else:
            vp_x = telemetry_data.get("vp_x")
            vp_y = telemetry_data.get("vp_y")
            if vp_x is not None and vp_y is not None:
                cv2.circle(output, (int(vp_x), int(vp_y)), 6, (0, 255, 255), -1)

            left_intercept = telemetry_data.get("left_intercept")
            right_intercept = telemetry_data.get("right_intercept")
            bottom_y = max(0, frame_h - 1)
            if left_intercept is not None:
                cv2.circle(output, (int(left_intercept), bottom_y), 6, (255, 255, 0), -1)
            if right_intercept is not None:
                cv2.circle(output, (int(right_intercept), bottom_y), 6, (255, 255, 0), -1)

            margin = max(0, min(self._danger_margin_px, frame_w))
            cv2.line(output, (margin, 0), (margin, frame_h - 1), (0, 0, 255), 2)
            cv2.line(
                output,
                (max(0, frame_w - margin), 0),
                (max(0, frame_w - margin), frame_h - 1),
                (0, 0, 255),
                2,
            )

            if callable(self._draw_overlay_fn):
                output = self._draw_overlay_fn(
                    output,
                    int(telemetry_data.get("frame_num", 0)),
                    telemetry_data.get("vp_angle"),
                    telemetry_data.get("vp_angle"),
                    float(telemetry_data.get("servo_angle", 90.0)),
                    float(telemetry_data.get("servo_center_angle", 90.0)),
                    str(telemetry_data.get("fsm_state", "GAPPING")),
                    bool(self._debug_configs.get("MAIN_SHOW_GUIDANCE_OVERLAY", True)),
                    self._outer_thresh,
                    self._inner_thresh,
                )

        vision_debug = debug_data.get("vision_debug")
        show_panel = bool(debug_data.get("show_vision_debug", False))
        if (
            show_panel
            and isinstance(vision_debug, dict)
            and callable(self._build_vision_debug_panel_fn)
        ):
            panel = self._build_vision_debug_panel_fn(frame_w, 240, vision_debug)
            if panel is not None:
                if panel.shape[1] != frame_w:
                    panel = cv2.resize(panel, (frame_w, panel.shape[0]))
                output = np.vstack((output, panel))

        return output

    def publish_stream(self, frame: np.ndarray, telemetry_data: dict[str, Any]) -> None:
        """Push frame to shared stream store only when HTTPS streaming is enabled."""
        if not self._stream_enabled or self._frame_store is None:
            return
        set_frame = getattr(self._frame_store, "set_frame", None)
        if callable(set_frame):
            set_frame(frame, telemetry_data)

    def write_video(self, frame: np.ndarray) -> None:
        """Write debug frame when video writer is active."""
        if self._video_writer is None:
            return
        frame_to_write = frame
        if frame.shape[1] != self._video_width or frame.shape[0] != self._video_height:
            frame_to_write = cv2.resize(frame, (self._video_width, self._video_height))
        self._video_writer.write(frame_to_write)

    def close(self) -> None:
        """Release legacy resources if initialized."""
        if self._csv_sink is not None:
            self._csv_sink.close()
        if self._csv_file is not None:
            self._csv_file.close()
        if self._video_writer is not None:
            release = getattr(self._video_writer, "release", None)
            if callable(release):
                release()
        if self._stream_server is not None:
            stop = getattr(self._stream_server, "stop", None)
            if callable(stop):
                stop()

    def sleep_remainder(self, loop_start: float, loop_period: float) -> None:
        """Call legacy sleep helper when present, fallback to local sleep otherwise."""
        if callable(self._sleep_remainder_fn):
            self._sleep_remainder_fn(loop_start, loop_period, self._logger)
            return
        elapsed = time.perf_counter() - loop_start
        remaining = loop_period - elapsed
        if remaining > 0:
            time.sleep(remaining)


class UnifiedCalibrator:
    """Facade orchestrating vision, geometry, steering, telemetry, and loop timing."""

    def __init__(
        self,
        config: ConfigManager | None = None,
        *,
        telemetry_enabled: bool = True,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._config = config or ConfigManager()
        self._system_configs = self._config.get_system_configs()
        self._debug_configs = self._config.get_debug_configs()
        self._debug_mode_enabled = bool(self._debug_configs.get("MAIN_DEBUG_MODE", False))
        self._debug_visualizer = _normalize_debug_visualizer(
            self._debug_configs.get("MAIN_DEBUG_VISUALIZER", ""),
        )
        self._preview_enabled = self._resolve_preview_enabled()

        self._robot_state = RobotState()
        self._vision = VisionProcessor(
            roi_height_pct=_get_float("ROI_HEIGHT_PCT", 0.6),
            roi_top_width_pct=_get_float("ROI_TOP_WIDTH_PCT", 0.75),
            roi_bottom_width_pct=_get_float("ROI_BOTTOM_WIDTH_PCT", 1.0),
        )
        self._geometry = GeometryCalculator()
        inner_thresh, outer_thresh = self._config.get_vp_thresholds()
        danger_margin, danger_nudge = self._config.get_danger_margins()
        self._steering = SteeringController(
            pid_constants=self._robot_state.pid,
            danger_margin=danger_margin,
            nudge_deg=danger_nudge,
            inner_thresh=inner_thresh,
            outer_thresh=outer_thresh,
            center_angle=self._robot_state.servo_center_angle,
            max_offset=self._robot_state.max_steering_offset,
        )
        self._telemetry = TelemetryLogger(self._config) if telemetry_enabled else None
        self._target_hz = float(self._system_configs.get("MAIN_TARGET_HZ", 30.0))
        self._terminal_log_enabled = bool(self._system_configs.get("MAIN_TERMINAL_LOG", True))
        self._terminal_log_interval_sec = 1.0
        self._last_terminal_log_time = 0.0
        self._lost_frames = 0
        self._camera_retry_limit = _get_int("MAIN_CAMERA_RETRY_LIMIT", 3)
        self._stream_configs = self._config.get_stream_configs()
        self._stream_enabled = bool(self._stream_configs.get("MAIN_HTTPS_STREAM_ENABLED", False))
        self._last_rendered_frame: np.ndarray | None = None
        self._expected_lane_width_px = _get_optional_float("EXPECTED_LANE_WIDTH_PX")
        self._overlay_drawer = OverlayDrawer(
            inner_thresh=inner_thresh,
            outer_thresh=outer_thresh,
            danger_margin_px=danger_margin,
            roi_height_pct=self._vision._roi_height_pct,
            roi_top_width_pct=self._vision._roi_top_width_pct,
            roi_bottom_width_pct=self._vision._roi_bottom_width_pct,
        )

    @property
    def steering_controller(self) -> SteeringController:
        """Expose the shared controller for the existing runtime tuning API."""
        return self._steering

    @property
    def robot_state(self) -> RobotState:
        """Expose the unified state for compatibility with route/runtime code."""
        return self._robot_state

    def render_frame(self, frame: np.ndarray, result: CalibrationResult) -> np.ndarray:
        """Return latest 664fb32 overlay frame for direct dashboard streaming."""
        if self._last_rendered_frame is not None:
            return self._last_rendered_frame.copy()
        return frame.copy()


    def process_frame(self, frame: np.ndarray, frame_num: int) -> CalibrationResult:
        """Compute one frame through the single centralized calibration path."""
        loop_start = time.perf_counter()
        stage = "input"
        process = "validate_frame"
        try:
            if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
                raise ValueError("frame must be a non-empty NumPy array")
            frame_h, frame_w = frame.shape[:2]
            vp: tuple[int, int] | None = None
            vp_angle: float | None = None
            left_intercept: int | None = None
            right_intercept: int | None = None
            selected_left_line: tuple[int, int, int, int] | None = None
            selected_right_line: tuple[int, int, int, int] | None = None
            selected_left_info: dict[str, Any] | None = None
            selected_right_info: dict[str, Any] | None = None

            stage = "vision"
            process = "preprocess_and_extract_lines"
            lines, vision_debug = self._vision.process_frame_debug(frame)

            stage = "lane_pair_selection"
            process = "select_lane_pair"
            selected = self._vision._apply_geometric_filter(lines, frame_w, frame_h)
            if selected is not None:
                line1, line2 = selected
                vision_debug["selected_lines"] = [line1, line2]
                selected_left_line = line1
                selected_right_line = line2

                stage = "vision_debug"
                process = "draw_selected_lines"
                grouped_vis = vision_debug["grouped_vis"]
                cv2.line(grouped_vis, (line1[0], line1[1]), (line1[2], line1[3]), (0, 255, 0), 2)
                cv2.line(grouped_vis, (line2[0], line2[1]), (line2[2], line2[3]), (255, 0, 255), 2)
                cv2.putText(
                    grouped_vis,
                    "LEFT",
                    (max(0, line1[0]), max(18, line1[1])),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (0, 255, 0),
                    1,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    grouped_vis,
                    "RIGHT",
                    (max(0, line2[0]), max(18, line2[1])),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (255, 0, 255),
                    1,
                    cv2.LINE_AA,
                )

                stage = "geometry"
                process = "calculate_bottom_intercepts"
                intercept_a, intercept_b = self._geometry.calculate_bottom_intercepts(
                    line1,
                    line2,
                    frame_h,
                )
                left_intercept, right_intercept = sorted((intercept_a, intercept_b))
                selected_left_info = self._geometry.describe_line(
                    selected_left_line,
                    role="left",
                    bottom_intercept=intercept_a,
                )
                selected_right_info = self._geometry.describe_line(
                    selected_right_line,
                    role="right",
                    bottom_intercept=intercept_b,
                )
                vision_debug["selected_left_line"] = selected_left_line
                vision_debug["selected_right_line"] = selected_right_line
                vision_debug["selected_left_line_info"] = selected_left_info
                vision_debug["selected_right_line_info"] = selected_right_info

                process = "calculate_vanishing_point"
                vp = self._geometry.calculate_vanishing_point(line1, line2)
                if vp is not None:
                    process = "map_vanishing_point_to_angle"
                    vp_angle = self._geometry.map_vp_to_angle(vp[0], frame_w)

            stage = "steering_control"
            process = "compute_steering_command"
            steering_angle, fsm_state = self._steering.compute_steering(
                vp_angle=vp_angle,
                left_intercept=left_intercept,
                right_intercept=right_intercept,
                frame_width=frame_w,
            )
            control_details = self._steering.describe_control_state(fsm_state, frame_w)
            vision_debug.update(control_details)

            stage = "control_state"
            process = "update_robot_state"
            self._robot_state.transition_to(FSMState(fsm_state))
            self._robot_state.calibration_active = fsm_state == "TRACKING_PD"
            self._robot_state.last_valid_servo_angle = steering_angle

            stage = "result_assembly"
            process = "assemble_calibration_result"
            loop_ms = (time.perf_counter() - loop_start) * 1000.0
            target_period_ms = 1000.0 / self._target_hz if self._target_hz > 0 else 0.0
            overrun_ms = max(0.0, loop_ms - target_period_ms) if target_period_ms > 0 else 0.0
            pid_error = 0.0 if vp_angle is None else float(vp_angle) - 90.0
            derivative_error = pid_error - self._robot_state.pid_last_error
            pid_p = self._robot_state.pid.kp * pid_error
            pid_d = self._robot_state.pid.kd * derivative_error
            pid_i = self._robot_state.pid.ki * self._robot_state.pid_integral
            vp_location = self._geometry.classify_point(vp, frame_w, frame_h)
            vision_lost = vp_angle is None or fsm_state == "GAPPING"
            self._lost_frames = self._lost_frames + 1 if vision_lost else 0
            recovery_direction = str(control_details.get("recovery_direction", "NONE"))
            recovery_active = recovery_direction != "NONE"
            vision_debug["vp_x"] = None if vp is None else vp[0]
            vision_debug["vp_y"] = None if vp is None else vp[1]
            vision_debug["vp_location"] = vp_location
            vision_debug["left_intercept"] = left_intercept
            vision_debug["right_intercept"] = right_intercept
            lane_width_px = (
                None
                if left_intercept is None or right_intercept is None
                else int(right_intercept - left_intercept)
            )
            width_error_px = (
                None
                if lane_width_px is None or self._expected_lane_width_px is None
                else float(lane_width_px) - self._expected_lane_width_px
            )
            telemetry_data: dict[str, Any] = {
                "frame_num": frame_num,
                "mono_timestamp": f"{time.perf_counter():.6f}",
                "utc_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "loop_ms": f"{loop_ms:.3f}",
                "loop_overrun_ms": f"{overrun_ms:.3f}",
                "frame_width": frame_w,
                "frame_height": frame_h,
                "roi_height": vision_debug.get("roi_height"),
                "edge_pixels": vision_debug.get("edge_pixels", 0),
                "vp_x": None if vp is None else vp[0],
                "vp_y": None if vp is None else vp[1],
                "vp_location": vp_location,
                "vp_angle": vp_angle,
                "theta": "" if vp_angle is None else f"{vp_angle:.6f}",
                "theta_source": "none" if vp_angle is None else "live",
                "theta_for_overlay": "" if vp_angle is None else f"{vp_angle:.6f}",
                "lines_count": len(lines),
                "total_hough_lines": len(lines),
                "left_candidate_lines": vision_debug.get("left_candidate_lines", 0),
                "right_candidate_lines": vision_debug.get("right_candidate_lines", 0),
                "selected_pair_found": int(selected is not None),
                "left_intercept": left_intercept,
                "right_intercept": right_intercept,
                "lane_width_px": lane_width_px,
                "expected_lane_width_px": self._expected_lane_width_px,
                "width_error_px": width_error_px,
                "selected_left_line": selected_left_line,
                "selected_right_line": selected_right_line,
                "selected_left_slope": (
                    None if selected_left_info is None else selected_left_info["slope"]
                ),
                "selected_right_slope": (
                    None if selected_right_info is None else selected_right_info["slope"]
                ),
                "fsm_state": fsm_state,
                **control_details,
                "tracking_active": int(fsm_state == "TRACKING_PD"),
                "danger_zone_active": int(str(fsm_state).startswith("DANGER")),
                "danger_margin_px": self._steering._danger_margin,
                "danger_action": "NONE" if not recovery_active else f"NUDGE_{recovery_direction}",
                "vision_lost": int(vision_lost),
                "lost_frames": self._lost_frames,
                "recovery_active": int(recovery_active),
                "recovery_angle": steering_angle if recovery_active else 0.0,
                "servo_angle": steering_angle,
                "servo_center_angle": self._robot_state.servo_center_angle,
                "servo_offset": steering_angle - self._robot_state.servo_center_angle,
                "pid_error": f"{pid_error:.6f}",
                "derivative_error": f"{derivative_error:.6f}",
                "pid_p_term": f"{pid_p:.6f}",
                "pid_i_term": f"{pid_i:.6f}",
                "pid_d_term": f"{pid_d:.6f}",
                "pid_integral": f"{self._robot_state.pid_integral:.6f}",
                "pid_last_error": f"{self._robot_state.pid_last_error:.6f}",
                "hardware_send_latency_ms": "0.000",
                "stream_enabled": int(self._stream_enabled),
                "stream_host": self._stream_configs.get("MAIN_HTTPS_STREAM_HOST", ""),
                "stream_port": self._stream_configs.get("MAIN_HTTPS_STREAM_PORT", ""),
                "calibration_active": int(bool(self._robot_state.calibration_active)),
            }
            debug_data: dict[str, Any] = {
                "show_vision_debug": bool(_get_bool("MAIN_SHOW_VISION_DEBUG", False)),
                "vision_debug": vision_debug,
            }
            self._robot_state.pid_last_error = pid_error

            # Render overlay now so render_frame() has something to return
            if self._telemetry is not None:
                try:
                    self._last_rendered_frame = self._telemetry.update_visuals(
                        frame, telemetry_data, debug_data
                    )
                except Exception:
                    self._last_rendered_frame = frame.copy()
            elif self._last_rendered_frame is None:
                self._last_rendered_frame = frame.copy()

            return CalibrationResult(
                steering_angle=float(steering_angle),
                control_state=fsm_state,
                observation_angle=vp_angle,
                calibration_active=self._robot_state.calibration_active,
                telemetry=telemetry_data,
                debug_data=debug_data,
            )
        except CalibrationProcessingError:
            raise
        except Exception as exc:
            raise CalibrationProcessingError(
                frame_num=frame_num,
                stage=stage,
                process=process,
                cause=exc,
            ) from exc

    def update(self, frame: np.ndarray, frame_num: int) -> float:
        """Process one frame and apply the offline telemetry side effects."""
        result = self.process_frame(frame, frame_num)
        if self._telemetry is None:
            self._last_rendered_frame = frame.copy()
            return result.steering_angle

        stage = "runtime_output"
        process = "render_visuals"
        try:
            rendered = self._telemetry.update_visuals(frame, result.telemetry, result.debug_data)
            self._last_rendered_frame = rendered
            process = "write_telemetry"
            self._telemetry.log_state(frame_num, result.telemetry)
            process = "write_debug_video"
            self._telemetry.write_video(rendered)
            process = "publish_stream"
            self._telemetry.publish_stream(rendered, result.telemetry)
            process = "log_terminal_status"
            self._log_terminal_status(frame_num, result.telemetry)
            return result.steering_angle
        except CalibrationProcessingError:
            raise
        except Exception as exc:
            raise CalibrationProcessingError(
                frame_num=frame_num,
                stage=stage,
                process=process,
                cause=exc,
            ) from exc

    def _log_terminal_status(self, frame_num: int, telemetry_data: dict[str, Any]) -> None:
        """Emit periodic terminal status logs for live runtime visibility."""
        if not self._terminal_log_enabled:
            return
        now = time.monotonic()
        if now - self._last_terminal_log_time < self._terminal_log_interval_sec:
            return
        self._last_terminal_log_time = now
        self._logger.info(
            "frame=%s state=%s danger=%s threshold=%s recovery=%s vp=(%s,%s)[%s] vp_angle=%s "
            "left=%s m=%s right=%s m=%s steering=%.2f loop_ms=%s overrun_ms=%s",
            frame_num,
            telemetry_data.get("fsm_state", ""),
            telemetry_data.get("danger_boundary", ""),
            telemetry_data.get("danger_threshold_x", ""),
            telemetry_data.get("recovery_direction", ""),
            telemetry_data.get("vp_x", ""),
            telemetry_data.get("vp_y", ""),
            telemetry_data.get("vp_location", "missing"),
            telemetry_data.get("vp_angle", ""),
            telemetry_data.get("selected_left_line"),
            telemetry_data.get("selected_left_slope"),
            telemetry_data.get("selected_right_line"),
            telemetry_data.get("selected_right_slope"),
            float(telemetry_data.get("servo_angle", 90.0)),
            telemetry_data.get("loop_ms", ""),
            telemetry_data.get("loop_overrun_ms", ""),
        )

    def _manage_loop_timing(self, loop_start_time: float) -> None:
        """Use legacy sleep_remainder to maintain configured loop rate."""
        if self._target_hz <= 0:
            return
        loop_period = 1.0 / self._target_hz
        if self._telemetry is not None:
            self._telemetry.sleep_remainder(loop_start_time, loop_period)

    def _resolve_preview_enabled(self) -> bool:
        """Determine if OpenCV preview window should be shown."""
        if self._debug_mode_enabled and self._debug_visualizer:
            return self._debug_visualizer in {"imshow", "both"}
        return bool(self._debug_configs.get("MAIN_SHOW_PREVIEW", False))

    def run(self) -> None:
        """Run the continuous capture-update loop until camera read fails."""
        frame_num = 0
        capture = None
        try:
            helpers = import_module("runtime.video_runtime_helpers")
            init_camera_with_retries = getattr(helpers, "init_camera_with_retries")
            capture = init_camera_with_retries(
                int(self._system_configs.get("MAIN_CAMERA_INDEX", 0)),
                self._camera_retry_limit,
                self._logger,
            )
        except (ModuleNotFoundError, AttributeError):
            capture = cv2.VideoCapture(int(self._system_configs.get("MAIN_CAMERA_INDEX", 0)))

        try:
            while capture is not None and capture.isOpened():
                loop_start = time.perf_counter()
                ok, frame = capture.read()
                if not ok or frame is None:
                    break

                if bool(self._system_configs.get("MAIN_FLIP_FRAME", False)):
                    frame = cv2.flip(frame, 1)

                self.update(frame, frame_num)
                if self._preview_enabled:
                    preview = self._last_rendered_frame if self._last_rendered_frame is not None else frame
                    cv2.imshow("Unified Calibration Preview", preview)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                frame_num += 1
                self._manage_loop_timing(loop_start)
        finally:
            if capture is not None:
                capture.release()
            if self._preview_enabled:
                cv2.destroyAllWindows()
            if self._telemetry is not None:
                self._telemetry.close()

    def close(self) -> None:
        """Release telemetry and video resources without running the loop."""
        if self._telemetry is not None:
            self._telemetry.close()
