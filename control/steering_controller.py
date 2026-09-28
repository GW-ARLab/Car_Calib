"""Control module: V2 vanishing-point steering state machine.

Mirrors the SteeringController used by the offline UnifiedCalibrator so the
live loop and the offline processor share one calibration law. States:

* ``GAPPING``        — vision lost (no VP / no intercepts), hold center
* ``DANGER_LEFT``    — left intercept inside left edge band (car drifted
  toward the right line), fixed nudge left (angle above center)
* ``DANGER_RIGHT``   — right intercept inside right edge band (car drifted
  toward the left line), fixed nudge right (angle below center)
* ``TRACKING_COAST`` — error inside hysteresis dead-band, hold center
* ``TRACKING_PD``    — error past outer threshold, PD correction active

Angles are emitted in the configured servo convention. The VP error stays
90-centered, then output maps onto ``SERVO_CENTER_ANGLE`` ± max offset so
MiniPC MQTT commands match the RPi signed-physical angle convention.
"""

from __future__ import annotations

from models.robot_state import PIDConstants


class SteeringController:
    """State-machine governor implementing vision-lost, danger, and tracking stages."""

    def __init__(
        self,
        pid_constants: PIDConstants,
        danger_margin: int,
        nudge_deg: float,
        inner_thresh: float,
        outer_thresh: float,
        center_angle: float = 90.0,
        max_offset: float = 90.0,
    ) -> None:
        self._pid = pid_constants
        self._danger_margin = max(0, int(danger_margin))
        self._nudge_deg = float(nudge_deg)
        self._inner_thresh = abs(float(inner_thresh))
        self._outer_thresh = max(abs(float(outer_thresh)), self._inner_thresh)
        self._center = float(center_angle)
        self._max_offset = abs(float(max_offset))
        self._tracking_active = False
        self._last_error = 0.0

    def compute_steering(
        self,
        vp_angle: float | None,
        left_intercept: int | None,
        right_intercept: int | None,
        frame_width: int,
    ) -> tuple[float, str]:
        """Return steering angle and active state according to 3-stage logic."""
        center = self._center
        lo = center - self._max_offset
        hi = center + self._max_offset

        # Stage 1: Vision Lost (Gapping)
        if vp_angle is None or left_intercept is None or right_intercept is None:
            self._tracking_active = False
            self._last_error = 0.0
            return center, "GAPPING"

        # Stage 3: Danger Zone override (bypass PD)
        left_margin = self._danger_margin
        right_margin = max(0, int(frame_width) - self._danger_margin)
        if left_intercept < left_margin:
            self._tracking_active = False
            self._last_error = 0.0
            return center + self._nudge_deg, "DANGER_LEFT"
        if right_intercept > right_margin:
            self._tracking_active = False
            self._last_error = 0.0
            return center - self._nudge_deg, "DANGER_RIGHT"

        # Stage 2: Tracking with hysteresis
        error = float(vp_angle) - 90.0
        abs_error = abs(error)
        if abs_error <= self._inner_thresh:
            self._tracking_active = False
            self._last_error = 0.0
            return center, "TRACKING_COAST"

        if abs_error > self._outer_thresh:
            self._tracking_active = True

        if not self._tracking_active:
            return center, "TRACKING_COAST"

        pd_correction = self._apply_pd(error)
        steering_angle = max(lo, min(hi, center - pd_correction))
        return steering_angle, "TRACKING_PD"

    def _apply_pd(self, error: float) -> float:
        """Apply proportional-derivative smoothing and return steering correction."""
        derivative = error - self._last_error
        self._last_error = error
        return (self._pid.kp * error) + (self._pid.kd * derivative)

    def describe_control_state(self, state: str, frame_width: int) -> dict[str, Any]:
        """Return per-frame control metadata for telemetry/debug."""
        margin = self._danger_margin
        return {
            "danger_boundary": f"LEFT < {margin}px | RIGHT > {frame_width - margin}px",
            "recovery_direction": "LEFT" if state == "DANGER_LEFT" else ("RIGHT" if state == "DANGER_RIGHT" else "NONE"),
            "danger_threshold_x": str(margin),
        }

