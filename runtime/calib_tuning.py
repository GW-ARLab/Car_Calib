"""Runtime tuning for live calibration parameters."""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

IdleGetter = Callable[[], tuple[bool, str]]


class TuneValidationError(ValueError):
    """Raised when a tune payload cannot be applied."""


class TuneBlockedError(RuntimeError):
    """Raised when live apply is blocked by vehicle state."""


@dataclass(frozen=True)
class TuneParam:
    key: str
    group: str
    label: str
    value_type: str
    minimum: float
    maximum: float
    step: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "group": self.group,
            "label": self.label,
            "type": self.value_type,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
        }


PARAMS: tuple[TuneParam, ...] = (
    TuneParam("roi_height_pct", "Vision", "ROI height", "float", 0.1, 1.0, 0.01),
    TuneParam("roi_top_width_pct", "Vision", "ROI top width", "float", 0.05, 1.0, 0.01),
    TuneParam("roi_bottom_width_pct", "Vision", "ROI bottom width", "float", 0.05, 1.0, 0.01),
    TuneParam("canny_low", "Vision", "Canny low", "int", 0, 500, 1),
    TuneParam("canny_high", "Vision", "Canny high", "int", 0, 500, 1),
    TuneParam("hough_threshold", "Vision", "Hough threshold", "int", 1, 300, 1),
    TuneParam("hough_min_line_length", "Vision", "Hough min line", "int", 1, 500, 1),
    TuneParam("hough_max_line_gap", "Vision", "Hough max gap", "int", 0, 200, 1),
    TuneParam("min_abs_slope", "Vision", "Min abs slope", "float", 0.0, 5.0, 0.01),
    TuneParam("pid_kp", "Steering", "PID Kp", "float", 0.0, 10.0, 0.01),
    TuneParam("pid_kd", "Steering", "PID Kd", "float", 0.0, 10.0, 0.01),
    TuneParam("vp_inner_thresh", "Steering", "VP inner", "float", 0.0, 45.0, 0.1),
    TuneParam("vp_outer_thresh", "Steering", "VP outer", "float", 0.0, 45.0, 0.1),
    TuneParam("danger_margin_px", "Steering", "Danger margin", "int", 0, 640, 1),
    TuneParam("danger_nudge_deg", "Steering", "Danger nudge", "float", 0.0, 45.0, 0.1),
)
_PARAMS_BY_KEY = {param.key: param for param in PARAMS}


class CalibTuneManager:
    """Applies validated tune values to the live calibrator instance."""

    def __init__(
        self,
        calibrator: Any,
        tune_file: str | Path,
        *,
        idle_getter: IdleGetter | None = None,
    ) -> None:
        self._calibrator = calibrator
        self._tune_file = Path(tune_file)
        self._idle_getter = idle_getter or (lambda: (True, "idle"))
        self._defaults = self._read_current()
        self._saved_values: dict[str, int | float] | None = None
        self._saved_updated_utc: str | None = None
        self.load_saved()

    @property
    def tune_file(self) -> Path:
        return self._tune_file

    def status(self) -> dict[str, Any]:
        values = self._read_current()
        idle_ok, idle_reason = self._idle_state()
        baseline = self._saved_values if self._saved_values is not None else self._defaults
        return {
            "values": values,
            "defaults": dict(self._defaults),
            "saved": {
                "exists": self._saved_values is not None,
                "updated_utc": self._saved_updated_utc,
                "values": dict(self._saved_values or {}),
                "path": str(self._tune_file),
            },
            "dirty": not _same_values(values, baseline),
            "schema": [param.as_dict() for param in PARAMS],
            "idle": {"ok": idle_ok, "reason": idle_reason},
        }

    def apply(self, values: dict[str, Any], *, require_idle: bool = True) -> dict[str, Any]:
        if require_idle:
            idle_ok, reason = self._idle_state()
            if not idle_ok:
                raise TuneBlockedError(reason)
        validated = self._validate(values, base=self._read_current())
        self._apply_values(validated)
        return self.status()

    def save(self) -> dict[str, Any]:
        values = self._read_current()
        updated_utc = _utc_now()
        payload = {
            "version": 1,
            "updated_utc": updated_utc,
            "values": values,
        }
        self._tune_file.parent.mkdir(parents=True, exist_ok=True)
        self._tune_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._saved_values = dict(values)
        self._saved_updated_utc = updated_utc
        return self.status()

    def reset(self, target: str) -> dict[str, Any]:
        if target == "defaults":
            return self.apply(self._defaults)
        if target == "saved":
            if self._saved_values is None:
                raise TuneValidationError("no saved tune values")
            return self.apply(self._saved_values)
        raise TuneValidationError("target must be 'saved' or 'defaults'")

    def load_saved(self) -> bool:
        if not self._tune_file.is_file():
            return False
        try:
            payload = json.loads(self._tune_file.read_text(encoding="utf-8"))
            if payload.get("version") != 1:
                raise TuneValidationError("unsupported tune file version")
            values = payload.get("values")
            if not isinstance(values, dict):
                raise TuneValidationError("tune file values must be an object")
            validated = self._validate(values, base=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ignoring invalid calib tune file %s: %s", self._tune_file, exc)
            return False
        self._saved_values = dict(validated)
        self._saved_updated_utc = str(payload.get("updated_utc") or "")
        self._apply_values(validated)
        return True

    def _idle_state(self) -> tuple[bool, str]:
        try:
            ok, reason = self._idle_getter()
        except Exception as exc:  # noqa: BLE001
            return False, f"idle check failed: {exc}"
        return bool(ok), str(reason or ("idle" if ok else "blocked"))

    def _validate(
        self,
        values: dict[str, Any],
        *,
        base: dict[str, int | float] | None,
    ) -> dict[str, int | float]:
        if not isinstance(values, dict):
            raise TuneValidationError("values must be an object")
        for key in values:
            if key not in _PARAMS_BY_KEY:
                raise TuneValidationError(f"unknown tune param: {key}")

        merged: dict[str, int | float] = dict(base or {})
        if base is None:
            missing = [param.key for param in PARAMS if param.key not in values]
            if missing:
                raise TuneValidationError(f"missing tune params: {', '.join(missing)}")

        for key, raw_value in values.items():
            merged[key] = _coerce_value(_PARAMS_BY_KEY[key], raw_value)

        for param in PARAMS:
            if param.key not in merged:
                raise TuneValidationError(f"missing tune param: {param.key}")

        if float(merged["canny_high"]) < float(merged["canny_low"]):
            raise TuneValidationError("canny_high must be >= canny_low")
        if float(merged["vp_outer_thresh"]) < float(merged["vp_inner_thresh"]):
            raise TuneValidationError("vp_outer_thresh must be >= vp_inner_thresh")
        return merged

    def _read_current(self) -> dict[str, int | float]:
        vision = self._calibrator._vision
        steering = self._calibrator.steering_controller
        state = self._calibrator.robot_state
        return {
            "roi_height_pct": float(vision._roi_height_pct),
            "roi_top_width_pct": float(vision._roi_top_width_pct),
            "roi_bottom_width_pct": float(vision._roi_bottom_width_pct),
            "canny_low": int(vision._canny_low),
            "canny_high": int(vision._canny_high),
            "hough_threshold": int(vision._hough_threshold),
            "hough_min_line_length": int(vision._hough_min_line_length),
            "hough_max_line_gap": int(vision._hough_max_line_gap),
            "min_abs_slope": float(vision._min_abs_slope),
            "pid_kp": float(state.pid.kp),
            "pid_kd": float(state.pid.kd),
            "vp_inner_thresh": float(steering._inner_thresh),
            "vp_outer_thresh": float(steering._outer_thresh),
            "danger_margin_px": int(steering._danger_margin),
            "danger_nudge_deg": float(steering._nudge_deg),
        }

    def _apply_values(self, values: dict[str, int | float]) -> None:
        vision = self._calibrator._vision
        steering = self._calibrator.steering_controller
        state = self._calibrator.robot_state

        vision._roi_height_pct = float(values["roi_height_pct"])
        vision._roi_top_width_pct = float(values["roi_top_width_pct"])
        vision._roi_bottom_width_pct = float(values["roi_bottom_width_pct"])
        vision._canny_low = int(values["canny_low"])
        vision._canny_high = int(values["canny_high"])
        vision._hough_threshold = int(values["hough_threshold"])
        vision._hough_min_line_length = int(values["hough_min_line_length"])
        vision._hough_max_line_gap = int(values["hough_max_line_gap"])
        vision._min_abs_slope = float(values["min_abs_slope"])

        state.pid.kp = float(values["pid_kp"])
        state.pid.kd = float(values["pid_kd"])
        steering._inner_thresh = float(values["vp_inner_thresh"])
        steering._outer_thresh = float(values["vp_outer_thresh"])
        steering._danger_margin = int(values["danger_margin_px"])
        steering._nudge_deg = float(values["danger_nudge_deg"])
        steering._tracking_active = False
        steering._last_error = 0.0
        state.pid_last_error = 0.0

        self._sync_overlay(values)

    def _sync_overlay(self, values: dict[str, int | float]) -> None:
        attrs = {
            "_inner_thresh": float(values["vp_inner_thresh"]),
            "_outer_thresh": float(values["vp_outer_thresh"]),
            "_danger_margin_px": int(values["danger_margin_px"]),
        }
        drawer_attrs = {
            "inner_thresh": float(values["vp_inner_thresh"]),
            "outer_thresh": float(values["vp_outer_thresh"]),
            "danger_margin_px": int(values["danger_margin_px"]),
            "roi_height_pct": float(values["roi_height_pct"]),
            "roi_top_width_pct": float(values["roi_top_width_pct"]),
            "roi_bottom_width_pct": float(values["roi_bottom_width_pct"]),
        }
        for owner in (self._calibrator, getattr(self._calibrator, "_telemetry", None)):
            if owner is None:
                continue
            for attr, value in attrs.items():
                if hasattr(owner, attr):
                    setattr(owner, attr, value)
            drawer = getattr(owner, "_overlay_drawer", None)
            if drawer is not None:
                for attr, value in drawer_attrs.items():
                    if hasattr(drawer, attr):
                        setattr(drawer, attr, value)


def _coerce_value(param: TuneParam, value: Any) -> int | float:
    if isinstance(value, bool):
        raise TuneValidationError(f"{param.key} must be a number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise TuneValidationError(f"{param.key} must be a number") from exc
    if not math.isfinite(numeric):
        raise TuneValidationError(f"{param.key} must be finite")
    if numeric < param.minimum or numeric > param.maximum:
        raise TuneValidationError(
            f"{param.key} must be between {param.minimum:g} and {param.maximum:g}"
        )
    if param.value_type == "int":
        if not numeric.is_integer():
            raise TuneValidationError(f"{param.key} must be an integer")
        return int(numeric)
    return float(numeric)


def _same_values(left: dict[str, int | float], right: dict[str, int | float]) -> bool:
    for param in PARAMS:
        if param.key not in left or param.key not in right:
            return False
        if abs(float(left[param.key]) - float(right[param.key])) > 1e-9:
            return False
    return True


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
