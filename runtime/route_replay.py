"""Offline replay of recorded route footage through the lane pipeline.

Runs a calibrator over a route's video frame by frame and summarizes the
stability metrics used to study field logs (FSM state mix, GAPPING reasons,
VP jitter, intercept jumps, lane-width outliers), so a pipeline or tuning
change can be A/B compared on identical footage. The same metrics are
computed from the route's recorded ``route_frames.csv`` as the live baseline.

Nothing here imports the pipeline itself: callers construct the calibrator
after loading their env file, because pipeline modules read settings at
import time (see scripts/replay_route.py).
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

JUMP_THRESHOLD_PX = 40.0
WIDTH_OUTLIER_RATIO = 0.2

# Telemetry keys kept per replayed frame; names match route_frames.csv.
REPLAY_FIELDS = (
    "fsm_state",
    "vp_angle",
    "vp_x",
    "left_intercept",
    "right_intercept",
    "lane_width_px",
    "left_candidate_lines",
    "right_candidate_lines",
    "servo_angle",
)

# Preferred first: overlay-free footage. route.avi has the dashboard overlay
# burned in, which adds edges the live pipeline never saw.
RAW_VIDEO_NAMES = ("route_raw.avi", "route_raw.mp4")
OVERLAY_VIDEO_NAMES = ("route.avi", "route.mp4")


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for an empty sequence."""
    if not values:
        return None
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[k]


def _state_of(row: dict[str, Any]) -> str:
    return str(row.get("fsm_state") or row.get("fsm") or "")


def _gap_reason(row: dict[str, Any]) -> str:
    left = to_float(row.get("left_candidate_lines"))
    right = to_float(row.get("right_candidate_lines"))
    if left == 0 and right == 0:
        return "no_lines_both"
    if left == 0:
        return "no_left"
    if right == 0:
        return "no_right"
    return "other"


def compute_metrics(rows: Iterable[dict[str, Any]], *, frame_width: int = 640) -> dict[str, Any]:
    """Summarize per-frame pipeline output into comparable stability metrics."""
    deg_per_px = 180.0 / frame_width if frame_width > 0 else 0.0
    states: Counter[str] = Counter()
    gap_reasons: Counter[str] = Counter()
    switches = 0
    prev_state: str | None = None
    prev_obs: tuple[float, float, float] | None = None
    vp_jitter_deg: list[float] = []
    left_jumps = right_jumps = pairs = 0
    widths: list[float] = []
    proc_ms: list[float] = []

    for row in rows:
        state = _state_of(row)
        states[state] += 1
        if prev_state is not None and state != prev_state:
            switches += 1
        prev_state = state
        if state == "GAPPING":
            gap_reasons[_gap_reason(row)] += 1

        vp_x = to_float(row.get("vp_x"))
        left = to_float(row.get("left_intercept"))
        right = to_float(row.get("right_intercept"))
        obs = (vp_x, left, right) if None not in (vp_x, left, right) else None
        if obs is not None:
            width = to_float(row.get("lane_width_px"))
            if width is not None:
                widths.append(width)
            if prev_obs is not None:
                pairs += 1
                vp_jitter_deg.append(abs(obs[0] - prev_obs[0]) * deg_per_px)
                left_jumps += abs(obs[1] - prev_obs[1]) > JUMP_THRESHOLD_PX
                right_jumps += abs(obs[2] - prev_obs[2]) > JUMP_THRESHOLD_PX
        prev_obs = obs

        ms = to_float(row.get("proc_ms"))
        if ms is not None:
            proc_ms.append(ms)

    total = sum(states.values())
    width_median = statistics.median(widths) if widths else None
    width_outliers = (
        sum(abs(w - width_median) > WIDTH_OUTLIER_RATIO * width_median for w in widths) / len(widths)
        if widths and width_median
        else None
    )
    return {
        "frames": total,
        "state_share": {k: v / total for k, v in states.most_common()} if total else {},
        "gapping_ratio": states.get("GAPPING", 0) / total if total else None,
        "gapping_reasons": dict(gap_reasons),
        "state_switches_per_100": switches / total * 100.0 if total else None,
        "vp_jitter_deg": {
            "median": statistics.median(vp_jitter_deg) if vp_jitter_deg else None,
            "p90": percentile(vp_jitter_deg, 90),
            "p99": percentile(vp_jitter_deg, 99),
        },
        "left_jump_ratio": left_jumps / pairs if pairs else None,
        "right_jump_ratio": right_jumps / pairs if pairs else None,
        "lane_width_px": {"median": width_median, "outlier_ratio": width_outliers},
        "proc_ms": {
            "median": statistics.median(proc_ms) if proc_ms else None,
            "p95": percentile(proc_ms, 95),
        },
    }


def servo_agreement(recorded: Sequence[dict[str, Any]], replayed: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compare servo_angle frame by frame over the common prefix.

    Recorded video and CSV rows are written in the same loop tick, so index i
    in both refers to the same camera frame (the video may carry one extra
    trailing frame from the tick that finalized the route).
    """
    diffs: list[float] = []
    for rec, rep in zip(recorded, replayed):
        a, b = to_float(rec.get("servo_angle")), to_float(rep.get("servo_angle"))
        if a is not None and b is not None:
            diffs.append(abs(a - b))
    return {
        "compared_frames": len(diffs),
        "mae_deg": statistics.fmean(diffs) if diffs else None,
        "p95_deg": percentile(diffs, 95),
        "within_1deg_ratio": sum(d <= 1.0 for d in diffs) / len(diffs) if diffs else None,
    }


def load_route_summary(route_dir: Path) -> dict[str, Any]:
    path = route_dir / "route_summary.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def route_tune_values(summary: dict[str, Any], known_keys: Iterable[str]) -> dict[str, Any]:
    """Tune values active when the route started, limited to ``known_keys``."""
    tune = (summary.get("extra_meta") or {}).get("calib_tune") or {}
    values = tune.get("values") or {}
    known = set(known_keys)
    return {k: v for k, v in values.items() if k in known}


def pick_route_video(route_dir: Path, summary: dict[str, Any]) -> tuple[Path, bool]:
    """Return (video path, is_raw) preferring overlay-free footage."""
    meta = summary.get("extra_meta") or {}
    for names, is_raw, meta_key in (
        (RAW_VIDEO_NAMES, True, "raw_video_file"),
        (OVERLAY_VIDEO_NAMES, False, "video_file"),
    ):
        declared = str(meta.get(meta_key) or "")
        for name in ((declared,) if declared else ()) + names:
            path = route_dir / name
            if path.is_file():
                return path, is_raw
    raise FileNotFoundError(f"no route video found in {route_dir}")


def load_recorded_rows(route_dir: Path) -> list[dict[str, Any]]:
    path = route_dir / "route_frames.csv"
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8", errors="ignore") as fh:
        return list(csv.DictReader(fh))


def replay_video(
    video_path: Path,
    calibrator: Any,
    *,
    max_frames: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Run ``calibrator.process_frame`` over every frame; return (rows, frame width)."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise OSError(f"cannot open video: {video_path}")
    rows: list[dict[str, Any]] = []
    frame_width = 0
    try:
        while max_frames is None or len(rows) < max_frames:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frame_width = int(frame.shape[1])
            start = time.perf_counter()
            result = calibrator.process_frame(frame, len(rows))
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            row = {"frame": len(rows)}
            row.update({key: result.telemetry.get(key) for key in REPLAY_FIELDS})
            row["proc_ms"] = elapsed_ms
            rows.append(row)
    finally:
        cap.release()
    return rows, frame_width


def write_rows_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
