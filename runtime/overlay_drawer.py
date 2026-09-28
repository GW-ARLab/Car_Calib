"""OpenCV overlay drawing helpers for live calibration frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class OverlayTheme:
    """Color palette used by OverlayDrawer."""

    white: tuple[int, int, int] = (255, 255, 255)
    black: tuple[int, int, int] = (0, 0, 0)
    green: tuple[int, int, int] = (0, 220, 0)
    yellow: tuple[int, int, int] = (0, 255, 255)
    red: tuple[int, int, int] = (0, 0, 255)
    cyan: tuple[int, int, int] = (255, 255, 0)
    vp_yellow: tuple[int, int, int] = (0, 255, 255)
    roi_yellow: tuple[int, int, int] = (0, 235, 255)


class OverlayDrawer:
    """Draw a telemetry HUD and floor-space visualizers on a frame."""

    def __init__(
        self,
        inner_thresh: float = 3.0,
        outer_thresh: float = 10.0,
        danger_margin_px: int = 100,
        roi_height_pct: float = 0.6,
        roi_top_width_pct: float = 1.0,
        roi_bottom_width_pct: float = 1.0,
        angle_center: float = 90.0,
        angle_span: float = 60.0,
        gauge_width: int = 400,
        gauge_height: int = 34,
        theme: OverlayTheme | None = None,
    ) -> None:
        self.inner_thresh = float(inner_thresh)
        self.outer_thresh = float(outer_thresh)
        self.danger_margin_px = int(danger_margin_px)
        self.roi_height_pct = float(roi_height_pct)
        self.roi_top_width_pct = float(roi_top_width_pct)
        self.roi_bottom_width_pct = float(roi_bottom_width_pct)
        self.angle_center = float(angle_center)
        self.angle_span = float(angle_span)
        self.gauge_width = int(gauge_width)
        self.gauge_height = int(gauge_height)
        self.theme = theme or OverlayTheme()

    def draw(self, frame: np.ndarray, debug_packet: dict[str, Any]) -> np.ndarray:
        """Return a copy of frame annotated with HUD and lane guidance."""
        output = frame.copy()
        frame_h, frame_w = output.shape[:2]

        state = str(debug_packet.get("state", "VISION_LOST"))
        raw_vp_angle = self._as_float(debug_packet.get("raw_vp_angle"), self.angle_center)
        left_intercept_x = self._as_int_or_none(debug_packet.get("left_intercept_x"))
        right_intercept_x = self._as_int_or_none(debug_packet.get("right_intercept_x"))
        final_cmd = self._as_float(debug_packet.get("final_steering_cmd"), self.angle_center)
        vp_coord = self._as_point(debug_packet.get("vp_coord"), (frame_w // 2, frame_h // 3))
        lines = self._normalize_lines(debug_packet.get("lines") or [])

        self._draw_floor_visuals(
            output,
            frame_w=frame_w,
            frame_h=frame_h,
            lines=lines,
            vp_coord=vp_coord,
            left_intercept_x=left_intercept_x,
            right_intercept_x=right_intercept_x,
        )
        self._draw_telemetry_panel(
            output,
            state=state,
            raw_vp_angle=raw_vp_angle,
            left_intercept_x=left_intercept_x,
            right_intercept_x=right_intercept_x,
            final_cmd=final_cmd,
        )
        self._draw_hysteresis_gauge(output, frame_w=frame_w, frame_h=frame_h, raw_vp_angle=raw_vp_angle)
        return output

    def _draw_telemetry_panel(
        self,
        frame: np.ndarray,
        *,
        state: str,
        raw_vp_angle: float,
        left_intercept_x: int | None,
        right_intercept_x: int | None,
        final_cmd: float,
    ) -> None:
        panel_x = 8
        panel_y = 8
        panel_w = 330
        panel_h = 148

        self._fill_alpha_rect(frame, (panel_x, panel_y), (panel_x + panel_w, panel_y + panel_h), (0, 0, 0), 0.58)
        self._draw_text(frame, "HUD / TELEMETRY", (panel_x + 12, panel_y + 24), self.theme.white, scale=0.58)

        state_color = self._state_color(state)
        heading_error = raw_vp_angle - 90.0
        entries = [
            (f"STATE: {state}", state_color),
            (f"RAW VP ANGLE: {raw_vp_angle:.1f}", self.theme.white),
            (f"HEADING ERROR: {heading_error:+.1f}", self.theme.white),
            (
                "BOTTOM INTERCEPTS: "
                f"L={self._format_value(left_intercept_x)} R={self._format_value(right_intercept_x)}",
                self.theme.white,
            ),
            (f"FINAL CMD: {final_cmd:.1f}", self.theme.white),
        ]

        y = panel_y + 48
        for text, color in entries:
            self._draw_text(frame, text, (panel_x + 12, y), color, scale=0.52)
            y += 22

    def _draw_floor_visuals(
        self,
        frame: np.ndarray,
        *,
        frame_w: int,
        frame_h: int,
        lines: list[tuple[int, int, int, int]],
        vp_coord: tuple[int, int],
        left_intercept_x: int | None,
        right_intercept_x: int | None,
    ) -> None:
        self._draw_roi_trapezoid(frame, frame_w=frame_w, frame_h=frame_h)

        center_x = frame_w // 2
        self._draw_dashed_vertical_line(frame, center_x, 0, frame_h - 1, self.theme.white)

        margin = max(0, min(self.danger_margin_px, frame_w))
        if margin > 0:
            self._fill_alpha_rect(frame, (0, 0), (margin, frame_h), self.theme.red, 0.16)
            self._fill_alpha_rect(frame, (max(0, frame_w - margin), 0), (frame_w, frame_h), self.theme.red, 0.16)

        for line in lines:
            x1, y1, x2, y2 = line
            line_color = self.theme.green
            bottom_x = self._line_bottom_x(line)
            if bottom_x is not None and (bottom_x < margin or bottom_x > (frame_w - margin)):
                line_color = self.theme.red
            cv2.line(frame, (x1, y1), (x2, y2), line_color, 3, cv2.LINE_AA)

        self._draw_vp_crosshair(frame, vp_coord)

        if left_intercept_x is not None:
            self._draw_bottom_marker(frame, left_intercept_x, frame_h - 1, self._marker_color(left_intercept_x, frame_w))
        if right_intercept_x is not None:
            self._draw_bottom_marker(frame, right_intercept_x, frame_h - 1, self._marker_color(right_intercept_x, frame_w))

    def _draw_roi_trapezoid(self, frame: np.ndarray, *, frame_w: int, frame_h: int) -> None:
        roi_height = max(1, int(frame_h * max(0.0, min(1.0, self.roi_height_pct))))
        top_w = frame_w * max(0.0, min(1.0, self.roi_top_width_pct))
        bottom_w = frame_w * max(0.0, min(1.0, self.roi_bottom_width_pct))
        polygon = np.array(
            [[
                ((frame_w - top_w) / 2, 0),
                ((frame_w + top_w) / 2, 0),
                ((frame_w + bottom_w) / 2, roi_height - 1),
                ((frame_w - bottom_w) / 2, roi_height - 1),
            ]],
            dtype=np.int32,
        )
        self._fill_alpha_poly(frame, polygon, self.theme.roi_yellow, 0.14)
        cv2.polylines(frame, polygon, True, self.theme.roi_yellow, 1, cv2.LINE_AA)

    def _fill_alpha_poly(
        self,
        frame: np.ndarray,
        polygon: np.ndarray,
        color: tuple[int, int, int],
        alpha: float,
    ) -> None:
        overlay = frame.copy()
        cv2.fillPoly(overlay, polygon, color)
        cv2.addWeighted(overlay, float(alpha), frame, 1.0 - float(alpha), 0.0, frame)

    def _draw_vp_crosshair(self, frame: np.ndarray, vp_coord: tuple[int, int]) -> None:
        x, y = vp_coord
        size = 12
        cv2.line(frame, (x - size, y), (x + size, y), self.theme.vp_yellow, 2, cv2.LINE_AA)
        cv2.line(frame, (x, y - size), (x, y + size), self.theme.vp_yellow, 2, cv2.LINE_AA)
        cv2.circle(frame, (x, y), 4, self.theme.vp_yellow, -1, cv2.LINE_AA)

    def _draw_hysteresis_gauge(self, frame: np.ndarray, *, frame_w: int, frame_h: int, raw_vp_angle: float) -> None:
        gauge_w = min(self.gauge_width, max(260, frame_w - 120))
        gauge_h = self.gauge_height
        x1 = (frame_w - gauge_w) // 2
        x2 = x1 + gauge_w
        y2 = frame_h - 14
        y1 = y2 - gauge_h

        self._fill_alpha_rect(frame, (x1, y1), (x2, y2), (0, 0, 0), 0.58)
        self._draw_text(frame, "ANGLE", (x1 + 10, y1 - 8), self.theme.white, scale=0.45)

        left_angle = self.angle_center - self.angle_span
        right_angle = self.angle_center + self.angle_span
        segments = [
            (left_angle, self.angle_center - self.outer_thresh, self.theme.red),
            (self.angle_center - self.outer_thresh, self.angle_center - self.inner_thresh, self.theme.yellow),
            (self.angle_center - self.inner_thresh, self.angle_center + self.inner_thresh, self.theme.green),
            (self.angle_center + self.inner_thresh, self.angle_center + self.outer_thresh, self.theme.yellow),
            (self.angle_center + self.outer_thresh, right_angle, self.theme.red),
        ]
        for start, end, color in segments:
            self._draw_angle_segment(frame, x1, y1, x2, y2, start, end, color)

        for angle in (
            left_angle,
            self.angle_center - self.outer_thresh,
            self.angle_center - self.inner_thresh,
            self.angle_center,
            self.angle_center + self.inner_thresh,
            self.angle_center + self.outer_thresh,
            right_angle,
        ):
            x = self._angle_to_x(angle, x1, x2)
            cv2.line(frame, (x, y1), (x, y2), (60, 60, 60), 1, cv2.LINE_AA)

        indicator_x = self._angle_to_x(raw_vp_angle, x1, x2)
        cv2.line(frame, (indicator_x, y1 - 2), (indicator_x, y2 + 2), self.theme.white, 2, cv2.LINE_AA)
        self._draw_text(frame, f"{raw_vp_angle:.1f} deg", (x1 + gauge_w - 92, y1 - 8), self.theme.white, scale=0.45)

    def _draw_angle_segment(
        self,
        frame: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        start_angle: float,
        end_angle: float,
        color: tuple[int, int, int],
    ) -> None:
        if end_angle <= start_angle:
            return
        start_x = self._angle_to_x(start_angle, x1, x2)
        end_x = self._angle_to_x(end_angle, x1, x2)
        if end_x <= start_x:
            return
        overlay = frame.copy()
        cv2.rectangle(overlay, (start_x, y1), (end_x, y2), color, -1)
        cv2.addWeighted(overlay, 0.35, frame, 0.65, 0.0, frame)

    def _draw_bottom_marker(self, frame: np.ndarray, x: int, bottom_y: int, color: tuple[int, int, int]) -> None:
        cv2.circle(frame, (x, bottom_y), 6, color, -1, cv2.LINE_AA)
        cv2.line(frame, (x, bottom_y - 12), (x, bottom_y - 2), color, 2, cv2.LINE_AA)

    def _marker_color(self, x: int, frame_w: int) -> tuple[int, int, int]:
        margin = max(0, min(self.danger_margin_px, frame_w))
        return self.theme.red if x < margin or x > (frame_w - margin) else self.theme.green

    def _state_color(self, state: str) -> tuple[int, int, int]:
        normalized = state.upper()
        if normalized in {"TRACKING", "TRACKING_COAST", "TRACKING_PD"}:
            return self.theme.green
        if normalized in {"VISION_LOST", "GAPPING"}:
            return self.theme.yellow
        if normalized.startswith("DANGER"):
            return self.theme.red
        return self.theme.white

    def _draw_text(
        self,
        frame: np.ndarray,
        text: str,
        origin: tuple[int, int],
        color: tuple[int, int, int],
        *,
        scale: float = 0.5,
        thickness: int = 1,
    ) -> None:
        cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)

    def _fill_alpha_rect(
        self,
        frame: np.ndarray,
        top_left: tuple[int, int],
        bottom_right: tuple[int, int],
        color: tuple[int, int, int],
        alpha: float,
    ) -> None:
        x1, y1 = top_left
        x2, y2 = bottom_right
        x1 = max(0, min(int(x1), frame.shape[1]))
        y1 = max(0, min(int(y1), frame.shape[0]))
        x2 = max(0, min(int(x2), frame.shape[1]))
        y2 = max(0, min(int(y2), frame.shape[0]))
        if x2 <= x1 or y2 <= y1:
            return
        overlay = frame.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
        cv2.addWeighted(overlay, float(alpha), frame, 1.0 - float(alpha), 0.0, frame)

    def _draw_dashed_vertical_line(
        self,
        frame: np.ndarray,
        x: int,
        y1: int,
        y2: int,
        color: tuple[int, int, int],
        *,
        dash_len: int = 12,
        gap_len: int = 8,
        thickness: int = 1,
    ) -> None:
        y = y1
        while y <= y2:
            segment_end = min(y + dash_len, y2)
            cv2.line(frame, (x, y), (x, segment_end), color, thickness, cv2.LINE_AA)
            y = segment_end + gap_len

    def _angle_to_x(self, angle: float, x1: int, x2: int) -> int:
        min_angle = self.angle_center - self.angle_span
        max_angle = self.angle_center + self.angle_span
        clamped = float(np.clip(angle, min_angle, max_angle))
        ratio = (clamped - min_angle) / max(1e-6, (max_angle - min_angle))
        return int(round(x1 + ratio * (x2 - x1)))

    def _line_bottom_x(self, line: tuple[int, int, int, int]) -> int | None:
        x1, y1, x2, y2 = line
        if y1 == y2:
            return None
        return int(round(x1 if y1 > y2 else x2))

    def _normalize_lines(self, lines: Iterable[Any]) -> list[tuple[int, int, int, int]]:
        normalized: list[tuple[int, int, int, int]] = []
        for line in lines:
            if not isinstance(line, (tuple, list)) or len(line) != 4:
                continue
            try:
                normalized.append(tuple(int(round(float(value))) for value in line))
            except (TypeError, ValueError):
                continue
        return normalized

    def _as_point(self, value: Any, fallback: tuple[int, int]) -> tuple[int, int]:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            try:
                return int(round(float(value[0]))), int(round(float(value[1])))
            except (TypeError, ValueError):
                return fallback
        return fallback

    def _as_int_or_none(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    def _as_float(self, value: Any, fallback: float) -> float:
        if value is None:
            return float(fallback)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(fallback)

    def _format_value(self, value: int | None) -> str:
        return "-" if value is None else str(value)
