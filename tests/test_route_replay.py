from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from runtime import route_replay


def _row(state: str, vp_x=None, left=None, right=None, width=None, lc=1, rc=1, servo=90.0) -> dict:
    return {
        "fsm_state": state,
        "vp_x": vp_x,
        "left_intercept": left,
        "right_intercept": right,
        "lane_width_px": width,
        "left_candidate_lines": lc,
        "right_candidate_lines": rc,
        "servo_angle": servo,
    }


def test_compute_metrics_counts_states_switches_and_gap_reasons() -> None:
    rows = [
        _row("TRACKING_PD", 320, 90, 555, 465),
        _row("TRACKING_PD", 320, 90, 555, 465),
        _row("GAPPING", lc=0, rc=0),
        _row("GAPPING", lc=0, rc=2),
        _row("GAPPING", lc=3, rc=0),
        _row("TRACKING_COAST", 320, 90, 555, 465),
    ]

    metrics = route_replay.compute_metrics(rows)

    assert metrics["frames"] == 6
    assert metrics["state_share"]["GAPPING"] == pytest.approx(0.5)
    assert metrics["gapping_ratio"] == pytest.approx(0.5)
    assert metrics["gapping_reasons"] == {"no_lines_both": 1, "no_left": 1, "no_right": 1}
    # PD -> GAPPING -> COAST
    assert metrics["state_switches_per_100"] == pytest.approx(2 / 6 * 100)


def test_compute_metrics_jitter_and_jumps_only_between_consecutive_valid_frames() -> None:
    rows = [
        _row("TRACKING_PD", 320, 90, 555, 465),
        _row("TRACKING_PD", 330, 95, 555, 460),   # vp +10px, no jump
        _row("TRACKING_PD", 330, 560, 555, 5),    # left jumps 465px (tile-row swap)
        _row("GAPPING"),                          # breaks the pair chain
        _row("TRACKING_PD", 0, 0, 0, 465),        # no pair with the frame before GAPPING
    ]

    metrics = route_replay.compute_metrics(rows, frame_width=640)

    assert metrics["vp_jitter_deg"]["median"] == pytest.approx(10 * 180 / 640 / 2)
    assert metrics["left_jump_ratio"] == pytest.approx(0.5)
    assert metrics["right_jump_ratio"] == pytest.approx(0.0)
    assert metrics["lane_width_px"]["median"] == pytest.approx(462.5)
    assert metrics["lane_width_px"]["outlier_ratio"] == pytest.approx(0.25)


def test_compute_metrics_accepts_csv_strings_and_empty_input() -> None:
    rows = [
        {"fsm": "TRACKING_PD", "vp_x": "320", "left_intercept": "90", "right_intercept": "555",
         "lane_width_px": "465", "proc_ms": "3.5"},
        {"fsm": "TRACKING_PD", "vp_x": "", "left_intercept": "", "right_intercept": ""},
    ]
    metrics = route_replay.compute_metrics(rows)
    assert metrics["state_share"] == {"TRACKING_PD": 1.0}
    assert metrics["proc_ms"]["median"] == pytest.approx(3.5)

    empty = route_replay.compute_metrics([])
    assert empty["frames"] == 0
    assert empty["gapping_ratio"] is None


def test_servo_agreement_uses_common_prefix() -> None:
    recorded = [{"servo_angle": "90"}, {"servo_angle": "100"}, {"servo_angle": ""}]
    replayed = [{"servo_angle": 90.0}, {"servo_angle": 97.0}, {"servo_angle": 90.0}, {"servo_angle": 1.0}]

    agreement = route_replay.servo_agreement(recorded, replayed)

    assert agreement["compared_frames"] == 2
    assert agreement["mae_deg"] == pytest.approx(1.5)
    assert agreement["within_1deg_ratio"] == pytest.approx(0.5)


def test_route_tune_values_keeps_only_known_keys() -> None:
    summary = {"extra_meta": {"calib_tune": {"values": {"pid_kp": 0.75, "retired_knob": 3}}}}
    assert route_replay.route_tune_values(summary, ["pid_kp", "pid_kd"]) == {"pid_kp": 0.75}
    assert route_replay.route_tune_values({}, ["pid_kp"]) == {}


def test_pick_route_video_prefers_raw_footage(tmp_path: Path) -> None:
    (tmp_path / "route.avi").write_bytes(b"x")
    assert route_replay.pick_route_video(tmp_path, {}) == (tmp_path / "route.avi", False)

    (tmp_path / "route_raw.avi").write_bytes(b"x")
    summary = {"extra_meta": {"raw_video_file": "route_raw.avi", "video_file": "route.avi"}}
    assert route_replay.pick_route_video(tmp_path, summary) == (tmp_path / "route_raw.avi", True)


def test_pick_route_video_raises_without_footage(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        route_replay.pick_route_video(tmp_path, {})


def _write_video(path: Path, frames: list[np.ndarray]) -> None:
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 15.0, (w, h))
    assert writer.isOpened()
    for frame in frames:
        writer.write(frame)
    writer.release()


def _lane_frame(shift: int = 0) -> np.ndarray:
    """Light floor with two dark grout lines converging toward the top."""
    frame = np.full((480, 640, 3), 200, dtype=np.uint8)
    cv2.line(frame, (80 + shift, 479), (280 + shift, 0), (40, 40, 40), 6)
    cv2.line(frame, (560 + shift, 479), (360 + shift, 0), (40, 40, 40), 6)
    return frame


def test_replay_video_feeds_every_frame_in_order(tmp_path: Path) -> None:
    video = tmp_path / "route_raw.avi"
    _write_video(video, [_lane_frame() for _ in range(5)])
    seen: list[int] = []

    def process_frame(frame, frame_num):
        seen.append(frame_num)
        assert frame.shape == (480, 640, 3)
        return SimpleNamespace(telemetry={"fsm_state": "TRACKING_PD", "servo_angle": 90.0, "unused": 1})

    rows, width = route_replay.replay_video(video, SimpleNamespace(process_frame=process_frame))

    assert seen == [0, 1, 2, 3, 4]
    assert width == 640
    assert rows[0]["fsm_state"] == "TRACKING_PD"
    assert "unused" not in rows[0]
    assert rows[0]["proc_ms"] >= 0.0

    limited, _ = route_replay.replay_video(video, SimpleNamespace(process_frame=process_frame), max_frames=2)
    assert len(limited) == 2


def test_replay_route_cli_runs_real_pipeline_on_synthetic_route(tmp_path: Path, capsys) -> None:
    from scripts import replay_route

    route_dir = tmp_path / "route-test"
    route_dir.mkdir()
    _write_video(route_dir / "route_raw.avi", [_lane_frame(shift) for shift in (0, 2, 4, 6)])
    (route_dir / "route_summary.json").write_text(
        json.dumps({"extra_meta": {"raw_video_file": "route_raw.avi", "calib_tune": {"values": {"pid_kp": 0.75}}}}),
        encoding="utf-8",
    )
    out_csv = tmp_path / "replay.csv"

    assert replay_route.main([str(route_dir), "--set", "pid_kd=0.3", "--csv", str(out_csv), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["video_is_raw"] is True
    assert report["tune_applied"] == {"pid_kp": 0.75, "pid_kd": "0.3"}
    assert report["recorded"] is None
    assert report["replay"]["frames"] == 4
    assert report["replay"]["lane_width_px"]["median"] is not None
    assert out_csv.read_text(encoding="utf-8").startswith("frame,fsm_state")
