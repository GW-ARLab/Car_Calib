#!/usr/bin/env python3
"""Replay a recorded route through the lane pipeline and compare metrics.

Runs the current UnifiedCalibrator over a route's footage and prints the same
stability metrics for the live run (route_frames.csv) and for the replay,
plus how closely the replayed steering angle matches the recorded one.

The route's tune snapshot (route_summary.json -> extra_meta.calib_tune) is
applied first so the replay starts from the settings the car actually used;
--set overrides single values on top of it for A/B experiments.

Usage:
    python scripts/replay_route.py ROUTE_DIR --env-file .env.pi5
    python scripts/replay_route.py ROUTE_DIR --env-file .env.pi5 --set hough_threshold=30
    python scripts/replay_route.py ROUTE_DIR --env-file .env.pi5 --csv replay.csv --json

Footage comes from route_raw.avi when the route has one
(ROUTE_RAW_VIDEO_ENABLED=true). Older routes only have route.avi with the
dashboard overlay burned in; that overlay adds edges the live pipeline never
saw, so replay numbers on those routes are biased.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_overrides(items: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--set expects KEY=VALUE, got {item!r}")
        overrides[key.strip()] = value.strip()
    return overrides


def build_calibrator() -> Any:
    from unified_calibration_components import UnifiedCalibrator

    calibrator = UnifiedCalibrator(telemetry_enabled=False)
    # Mirror main_pi5.py: steering output is centered on logic 90 over 0..180;
    # the physical servo offset is applied later by map_calibrated_servo.
    state = calibrator.robot_state
    controller = calibrator.steering_controller
    state.servo_center_angle = 90.0
    state.max_steering_offset = 90.0
    state.last_valid_servo_angle = 90.0
    state.last_valid_command = 90.0
    controller._center = 90.0
    controller._max_offset = 90.0
    return calibrator


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "int":
        return f"{int(value)}"
    return f"{value:.2f}"


def format_report(report: dict[str, Any]) -> str:
    recorded = report.get("recorded") or {}
    replay = report["replay"]
    lines = [
        f"route : {report['route_dir']}",
        f"video : {report['video']} ({'raw' if report['video_is_raw'] else 'OVERLAY - biased'})",
        f"tune  : {len(report['tune_applied'])} values applied"
        + (f", overrides {report['overrides']}" if report["overrides"] else ""),
        "",
        f"{'metric':<30}{'recorded':>12}{'replay':>12}",
    ]

    def add(label: str, rec: Any, rep: Any, kind: str = "num") -> None:
        lines.append(f"{label:<30}{_fmt(rec, kind):>12}{_fmt(rep, kind):>12}")

    def pick(metrics: dict[str, Any], *path: str) -> Any:
        value: Any = metrics
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        return value

    add("frames", recorded.get("frames"), replay["frames"], "int")
    states = list(replay["state_share"]) + [
        s for s in recorded.get("state_share", {}) if s not in replay["state_share"]
    ]
    for state in states:
        add(f"state {state}", pick(recorded, "state_share", state), replay["state_share"].get(state), "pct")
    reasons = sorted(set(replay["gapping_reasons"]) | set(recorded.get("gapping_reasons", {})))
    for reason in reasons:
        add(f"gapping {reason} (frames)", pick(recorded, "gapping_reasons", reason),
            replay["gapping_reasons"].get(reason), "int")
    add("state switches / 100 frames", recorded.get("state_switches_per_100"), replay["state_switches_per_100"])
    for stat in ("median", "p90", "p99"):
        add(f"vp jitter {stat} (deg)", pick(recorded, "vp_jitter_deg", stat), replay["vp_jitter_deg"][stat])
    add("left intercept jumps >40px", recorded.get("left_jump_ratio"), replay["left_jump_ratio"], "pct")
    add("right intercept jumps >40px", recorded.get("right_jump_ratio"), replay["right_jump_ratio"], "pct")
    add("lane width median (px)", pick(recorded, "lane_width_px", "median"), replay["lane_width_px"]["median"])
    add("lane width outside +-20%", pick(recorded, "lane_width_px", "outlier_ratio"),
        replay["lane_width_px"]["outlier_ratio"], "pct")
    add("pipeline ms median", None, replay["proc_ms"]["median"])
    add("pipeline ms p95", None, replay["proc_ms"]["p95"])

    agreement = report.get("servo_agreement")
    if agreement and agreement["compared_frames"]:
        lines += [
            "",
            f"steering vs recorded over {agreement['compared_frames']} frames: "
            f"MAE {_fmt(agreement['mae_deg'], 'num')} deg, p95 {_fmt(agreement['p95_deg'], 'num')} deg, "
            f"within 1 deg {_fmt(agreement['within_1deg_ratio'], 'pct')}",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("route_dir", type=Path, help="route-* directory with route_summary.json")
    parser.add_argument("--video", type=Path, help="replay this video instead of the route's own")
    parser.add_argument("--env-file", type=Path, help="env file loaded (override) before the pipeline, e.g. .env.pi5")
    parser.add_argument("--ignore-route-tune", action="store_true", help="skip the route's calib_tune snapshot")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="tune override applied after the route snapshot (repeatable)")
    parser.add_argument("--max-frames", type=int, help="replay only the first N frames")
    parser.add_argument("--csv", type=Path, help="write per-frame replay rows to this CSV")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)

    try:
        overrides = _parse_overrides(args.overrides)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.route_dir.is_dir():
        parser.error(f"route directory not found: {args.route_dir}")

    # Pipeline modules read settings at import time, so the env file has to
    # be loaded before anything below imports them.
    if args.env_file is not None:
        if not args.env_file.is_file():
            parser.error(f"env file not found: {args.env_file}")
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=True)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from runtime import route_replay
    from runtime.calib_tuning import PARAMS, CalibTuneManager

    summary = route_replay.load_route_summary(args.route_dir)
    if args.video is not None:
        video = args.video
        video_is_raw = video.name not in route_replay.OVERLAY_VIDEO_NAMES
    else:
        video, video_is_raw = route_replay.pick_route_video(args.route_dir, summary)

    calibrator = build_calibrator()
    tune: dict[str, Any] = {}
    if not args.ignore_route_tune:
        tune.update(route_replay.route_tune_values(summary, (p.key for p in PARAMS)))
    tune.update(overrides)
    if tune:
        with tempfile.TemporaryDirectory() as tmp:
            manager = CalibTuneManager(calibrator, Path(tmp) / "tune.json")
            try:
                manager.apply(tune, require_idle=False)
            except ValueError as exc:
                parser.error(f"invalid tune values: {exc}")

    replayed, frame_width = route_replay.replay_video(video, calibrator, max_frames=args.max_frames)
    recorded = route_replay.load_recorded_rows(args.route_dir)
    if args.max_frames is not None:
        recorded = recorded[: len(replayed)]
    width = frame_width or 640

    report = {
        "route_dir": str(args.route_dir),
        "video": str(video),
        "video_is_raw": video_is_raw,
        "tune_applied": tune,
        "overrides": overrides,
        "recorded": route_replay.compute_metrics(recorded, frame_width=width) if recorded else None,
        "replay": route_replay.compute_metrics(replayed, frame_width=width),
        "servo_agreement": route_replay.servo_agreement(recorded, replayed) if recorded else None,
    }
    if args.csv is not None:
        route_replay.write_rows_csv(args.csv, replayed)
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
