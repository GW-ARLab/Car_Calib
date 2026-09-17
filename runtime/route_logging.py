"""Route session logging and dataset acceptance helpers."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from config.settings import (
    ROUTE_ACCEPT_MAX_GAP_RATIO,
    ROUTE_ACCEPT_MAX_HW_ERRORS,
    ROUTE_ACCEPT_MIN_FRAMES,
    ROUTE_DIRECTION_EPS_DEG,
    ROUTE_LOG_ROOT,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


@dataclass
class RouteFinalizeResult:
    route_id: str
    route_mode: str
    accepted: bool
    rejection_reason: str
    summary_path: str
    route_dir: str


class RouteSession:
    """Collects route-level metadata and writes a summary JSON on finalize."""

    def __init__(self, route_mode: str = "AUTO") -> None:
        self.route_id = f"route-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        self.route_mode = route_mode
        self.started_at_utc = _utc_now_iso()
        self._start_monotonic: float | None = None
        self._end_monotonic: float | None = None
        self._ended_at_utc: str | None = None

        self.total_frames = 0
        self.frames_with_theta = 0
        self.hw_error_count = 0
        self.abstract_steps = 0

        self._pending_direction: str | None = None
        self._pending_count = 0
        self._stable_direction: str | None = None
        self._recent_directions: deque[str] = deque(maxlen=5)
        self._root_dir = self._resolve_root_dir()
        self._route_dir = self._root_dir / self.route_id
        self._route_dir.mkdir(parents=True, exist_ok=True)
        self._extra_meta: dict[str, object] = {}

    def attach_meta(self, key: str, value: object) -> None:
        """Attach extra metadata to be persisted in the route summary."""
        self._extra_meta[key] = value

    def start(self, mono_now: float) -> None:
        self._start_monotonic = mono_now

    def record_hw_error(self) -> None:
        self.hw_error_count += 1

    def update_frame(
        self,
        *,
        mono_now: float,
        theta: Optional[float],
        fsm_state: str,
        calibration_active: bool,
    ) -> tuple[Optional[float], str, str]:
        if self._start_monotonic is None:
            self.start(mono_now)

        self.total_frames += 1
        angle_diff: Optional[float] = None
        if theta is not None:
            self.frames_with_theta += 1
            angle_diff = abs(theta - 90.0)

        direction = self._direction_from_theta(theta)
        self._update_abstract_steps(direction)
        calib_status = self._calib_status(direction, fsm_state, calibration_active)
        return angle_diff, calib_status, direction

    def finalize(self, *, mono_now: float, status: str, explicit_rejection_reason: str = "") -> RouteFinalizeResult:
        self._end_monotonic = mono_now
        self._ended_at_utc = _utc_now_iso()

        elapsed_s = 0.0
        if self._start_monotonic is not None:
            elapsed_s = max(0.0, self._end_monotonic - self._start_monotonic)

        gap_frames = self.total_frames - self.frames_with_theta
        gap_ratio = (gap_frames / self.total_frames) if self.total_frames > 0 else 1.0

        accepted, rejection_reason = self._evaluate_acceptance(
            route_status=status,
            gap_ratio=gap_ratio,
            explicit_rejection_reason=explicit_rejection_reason,
        )

        payload = {
            "route_id": self.route_id,
            "route_mode": self.route_mode,
            "start_timestamp_utc": self.started_at_utc,
            "end_timestamp_utc": self._ended_at_utc,
            "abstract_steps": self.abstract_steps,
            "total_elapsed_seconds": elapsed_s,
            "status": status,
            "accepted": accepted,
            "rejection_reason": rejection_reason,
            "total_frames": self.total_frames,
            "frames_with_theta": self.frames_with_theta,
            "gap_ratio": gap_ratio,
            "hardware_error_count": self.hw_error_count,
            "route_direction_eps_deg": ROUTE_DIRECTION_EPS_DEG,
        }
        if self._extra_meta:
            payload["extra_meta"] = self._extra_meta

        summary_path = self._route_dir / "route_summary.json"
        summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # Bundle the route directory into a sibling .zip for quick download.
        archive_path: Path | None = None
        try:
            base_name = str(self._route_dir)
            archive = shutil.make_archive(base_name, "zip", root_dir=str(self._route_dir.parent), base_dir=self._route_dir.name)
            archive_path = Path(archive)
        except Exception:  # noqa: BLE001
            archive_path = None

        return RouteFinalizeResult(
            route_id=self.route_id,
            route_mode=self.route_mode,
            accepted=accepted,
            rejection_reason=rejection_reason,
            summary_path=str(summary_path),
            route_dir=str(self._route_dir),
        )

    @property
    def route_dir(self) -> Path:
        return self._route_dir

    @staticmethod
    def _resolve_root_dir() -> Path:
        root = Path(ROUTE_LOG_ROOT)
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError:
            fallback = Path("logs/routes")
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _direction_from_theta(self, theta: Optional[float]) -> str:
        if theta is None:
            return "UNKNOWN"
        delta = theta - 90.0
        if abs(delta) <= ROUTE_DIRECTION_EPS_DEG:
            return "STRAIGHT"
        if delta > 0:
            return "RIGHT"
        return "LEFT"

    def _calib_status(self, direction: str, fsm_state: str, calibration_active: bool) -> str:
        if fsm_state == "GAPPING":
            return "GAPPING"
        if direction == "UNKNOWN":
            return "NO_REFERENCE"
        if not calibration_active:
            return "DRIVING_STRAIGHT"
        if direction == "LEFT":
            return "CALIBRATING_LEFT"
        if direction == "RIGHT":
            return "CALIBRATING_RIGHT"
        return "CALIBRATED"

    def _update_abstract_steps(self, direction: str) -> None:
        if direction == "UNKNOWN":
            return

        self._recent_directions.append(direction)
        if self._pending_direction == direction:
            self._pending_count += 1
        else:
            self._pending_direction = direction
            self._pending_count = 1

        # Require 3 consecutive frames to accept a direction segment.
        if self._pending_count < 3:
            return

        if self._stable_direction != self._pending_direction:
            self._stable_direction = self._pending_direction
            self.abstract_steps += 1

    def _evaluate_acceptance(
        self,
        *,
        route_status: str,
        gap_ratio: float,
        explicit_rejection_reason: str,
    ) -> tuple[bool, str]:
        if explicit_rejection_reason:
            return False, explicit_rejection_reason
        if route_status != "COMPLETED":
            return False, f"route_status={route_status}"
        if self.total_frames < ROUTE_ACCEPT_MIN_FRAMES:
            return False, f"insufficient_frames<{ROUTE_ACCEPT_MIN_FRAMES}"
        if self.hw_error_count > ROUTE_ACCEPT_MAX_HW_ERRORS:
            return False, f"hardware_errors>{ROUTE_ACCEPT_MAX_HW_ERRORS}"
        if gap_ratio > ROUTE_ACCEPT_MAX_GAP_RATIO:
            return False, f"gap_ratio>{ROUTE_ACCEPT_MAX_GAP_RATIO:.3f}"
        return True, ""


def update_route_distance(route_id: str, distance_m: float) -> dict:
    """Patch a finished route's summary with a manually-entered distance.

    Called after RouteSession.finalize() already wrote route_summary.json
    and zipped route_dir -- the operator enters distance by hand from the
    dashboard once the run stops, so this re-writes the summary in place
    and re-zips the route directory to keep the download in sync.
    """
    root = RouteSession._resolve_root_dir().resolve()
    route_dir = (root / route_id).resolve()
    if route_dir.parent != root or not route_dir.is_dir():
        raise FileNotFoundError(route_id)
    summary_path = route_dir / "route_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(route_id)

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["distance_m"] = float(distance_m)
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    shutil.make_archive(str(route_dir), "zip", root_dir=str(route_dir.parent), base_dir=route_dir.name)
    return payload


_SHEET_NAME_INVALID_CHARS = re.compile(r"[\\/*?:\[\]]")


def _safe_sheet_name(route_id: str, used: set[str]) -> str:
    """Excel sheet names: <=31 chars, no \\/*?:[] -- and must be unique."""
    name = _SHEET_NAME_INVALID_CHARS.sub("_", route_id)[:31]
    base = name
    suffix = 1
    while name in used:
        suffix_str = f"~{suffix}"
        name = base[: 31 - len(suffix_str)] + suffix_str
        suffix += 1
    used.add(name)
    return name


def export_routes_xlsx(route_ids: list[str] | None = None) -> bytes:
    """Build one .xlsx workbook with one sheet per route.

    Each sheet has the route summary (mode/status/distance/elapsed/...) as a
    key-value header block, then the per-frame `route_frames.csv` data (if
    the route recorded one) below it.
    """
    from openpyxl import Workbook

    root = RouteSession._resolve_root_dir().resolve()
    if route_ids is None:
        route_ids = sorted(
            (d.name for d in root.iterdir() if d.is_dir() and d.name.startswith("route-")),
            reverse=True,
        )

    wb = Workbook()
    wb.remove(wb.active)
    used_sheet_names: set[str] = set()

    for route_id in route_ids:
        route_dir = (root / route_id).resolve()
        if route_dir.parent != root or not route_dir.is_dir():
            raise FileNotFoundError(route_id)

        ws = wb.create_sheet(_safe_sheet_name(route_id, used_sheet_names))

        summary: dict = {}
        summary_path = route_dir / "route_summary.json"
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                summary = {}

        ws.append(["field", "value"])
        for key in (
            "route_id", "route_mode", "status", "accepted", "distance_m",
            "total_elapsed_seconds", "total_frames", "start_timestamp_utc",
            "end_timestamp_utc",
        ):
            ws.append([key, summary.get(key)])
        ws.append([])

        frames_path = route_dir / "route_frames.csv"
        if frames_path.is_file():
            with frames_path.open("r", newline="", encoding="utf-8") as f:
                for row in csv.reader(f):
                    ws.append(row)
        else:
            ws.append(["(no route_frames.csv recorded for this route)"])

    if not wb.sheetnames:
        wb.create_sheet("empty").append(["no routes to export"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def compute_average_speed_mps() -> dict:
    """Fit distance = speed * elapsed + startup_offset_m from completed routes.

    A plain mean of (distance/elapsed) per route was consistently off by a
    few cm on short steps: the car doesn't move at cruise speed from t=0, it
    loses a bit of distance accelerating first. That startup loss is roughly
    constant regardless of how long the step runs, so it shows up as a
    negative y-intercept in a distance-vs-elapsed linear fit rather than in
    the slope -- treating it as pure "distance/elapsed" biases the average
    speed low and undershoots on short/large distances alike. A least
    squares fit separates the two: `speed_mps` (slope) is the actual cruise
    speed, `intercept_m` is the (negative) constant offset. Converting a
    desired distance to a duration should use
    `duration = (distance - intercept_m) / speed_mps`, not a flat division.
    """
    root = RouteSession._resolve_root_dir()
    points: list[tuple[float, float]] = []
    if root.is_dir():
        for d in root.iterdir():
            if not d.is_dir() or not d.name.startswith("route-"):
                continue
            summary_file = d / "route_summary.json"
            if not summary_file.is_file():
                continue
            try:
                s = json.loads(summary_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            dist = s.get("distance_m")
            elapsed = s.get("total_elapsed_seconds")
            if s.get("status") == "COMPLETED" and dist and elapsed and dist > 0 and elapsed > 0:
                points.append((elapsed, dist))

    if not points:
        return {"avg_speed_mps": None, "intercept_m": 0.0, "n_samples": 0}

    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    variance_x = sum((x - mean_x) ** 2 for x, _ in points)

    if n < 3 or variance_x <= 1e-9:
        # Not enough spread in step durations to fit a reliable line --
        # fall back to a flat mean speed with no startup-offset correction.
        speed = mean_y / mean_x
        return {"avg_speed_mps": speed, "intercept_m": 0.0, "n_samples": n}

    covariance = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = covariance / variance_x
    intercept = mean_y - slope * mean_x
    if slope <= 0:
        # Degenerate fit (e.g. noisy data) -- fall back rather than return a
        # nonsensical zero/negative speed.
        speed = mean_y / mean_x
        return {"avg_speed_mps": speed, "intercept_m": 0.0, "n_samples": n}
    return {"avg_speed_mps": slope, "intercept_m": intercept, "n_samples": n}


def bundle_routes_zip(route_ids: list[str] | None, dest_path: Path) -> int:
    """Bundle each route's already-finalized .zip into one zip file on disk.

    Stores rather than re-compresses the per-route archives (they're already
    compressed, and often contain a multi-MB route.avi) to keep this fast
    and light on a Pi5. Routes with no finalized .zip yet (still running,
    or the zip step failed) are skipped. Returns how many were included.
    """
    root = RouteSession._resolve_root_dir().resolve()
    if route_ids is None:
        route_ids = sorted((p.stem for p in root.glob("route-*.zip")), reverse=True)

    included = 0
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_STORED) as bundle:
        for route_id in route_ids:
            zip_path = (root / f"{route_id}.zip").resolve()
            if zip_path.parent != root or not zip_path.is_file():
                continue
            bundle.write(zip_path, arcname=f"{route_id}.zip")
            included += 1
    return included


def delete_route(route_id: str) -> None:
    """Delete a route's directory and its sibling .zip, if present."""
    root = RouteSession._resolve_root_dir().resolve()
    route_dir = (root / route_id).resolve()
    if route_dir.parent != root or not route_dir.is_dir():
        raise FileNotFoundError(route_id)
    shutil.rmtree(route_dir, ignore_errors=True)
    zip_path = route_dir.with_suffix(".zip")
    zip_path.unlink(missing_ok=True)


def delete_all_routes() -> dict:
    """Delete every route directory (and matching .zip) under ROUTE_LOG_ROOT."""
    root = RouteSession._resolve_root_dir().resolve()
    removed = 0
    errors: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("route-"):
            continue
        try:
            delete_route(entry.name)
            removed += 1
        except OSError as exc:
            errors.append(f"{entry.name}: {exc}")
    return {"removed": removed, "errors": errors}
