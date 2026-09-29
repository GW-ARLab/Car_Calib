"""Tests for runtime calibration tuning."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from runtime.calib_tuning import CalibTuneManager, TuneBlockedError, TuneValidationError


def _fake_calibrator():
    vision = SimpleNamespace(
        _roi_height_pct=0.6,
        _roi_top_width_pct=0.75,
        _roi_bottom_width_pct=1.0,
        _canny_low=50,
        _canny_high=150,
        _hough_threshold=40,
        _hough_min_line_length=40,
        _hough_max_line_gap=20,
        _min_abs_slope=0.3,
        _vp_horizon_y=-518.0,
    )
    steering = SimpleNamespace(
        _inner_thresh=3.0,
        _outer_thresh=8.0,
        _danger_margin=130,
        _nudge_deg=10.0,
        _tracking_active=True,
        _last_error=12.0,
    )
    state = SimpleNamespace(
        pid=SimpleNamespace(kp=0.9, ki=0.05, kd=0.08),
        pid_last_error=9.0,
    )
    return SimpleNamespace(
        _vision=vision,
        steering_controller=steering,
        robot_state=state,
        _overlay_drawer=SimpleNamespace(_inner_thresh=3.0, _outer_thresh=8.0, _danger_margin_px=130),
        _telemetry=SimpleNamespace(
            _inner_thresh=3.0,
            _outer_thresh=8.0,
            _danger_margin_px=130,
            _overlay_drawer=SimpleNamespace(_inner_thresh=3.0, _outer_thresh=8.0, _danger_margin_px=130),
        ),
    )


def test_tune_apply_updates_runtime_fields_and_resets_pd_state(tmp_path):
    calibrator = _fake_calibrator()
    manager = CalibTuneManager(calibrator, tmp_path / "calib_tune.json")

    status = manager.apply(
        {
            "roi_height_pct": 0.7,
            "roi_top_width_pct": 0.8,
            "roi_bottom_width_pct": 0.95,
            "canny_low": 60,
            "canny_high": 170,
            "hough_threshold": 55,
            "hough_min_line_length": 65,
            "hough_max_line_gap": 25,
            "min_abs_slope": 0.45,
            "pid_kp": 1.1,
            "pid_kd": 0.2,
            "vp_inner_thresh": 4.0,
            "vp_outer_thresh": 9.0,
            "danger_margin_px": 140,
            "danger_nudge_deg": 12.0,
        }
    )

    assert status["values"]["roi_height_pct"] == pytest.approx(0.7)
    assert calibrator._vision._canny_low == 60
    assert calibrator.steering_controller._danger_margin == 140
    assert calibrator.robot_state.pid.kp == pytest.approx(1.1)
    assert calibrator.robot_state.pid.kd == pytest.approx(0.2)
    assert calibrator.steering_controller._tracking_active is False
    assert calibrator.steering_controller._last_error == pytest.approx(0.0)
    assert calibrator.robot_state.pid_last_error == pytest.approx(0.0)
    assert calibrator._telemetry._danger_margin_px == 140


@pytest.mark.parametrize(
    "values, message",
    [
        ({"canny_low": "x"}, "canny_low must be a number"),
        ({"canny_low": 200, "canny_high": 100}, "canny_high must be >= canny_low"),
        ({"vp_inner_thresh": 10, "vp_outer_thresh": 5}, "vp_outer_thresh must be >="),
        ({"hough_threshold": 0}, "hough_threshold must be between"),
    ],
)
def test_tune_validation_rejects_bad_values(tmp_path, values, message):
    manager = CalibTuneManager(_fake_calibrator(), tmp_path / "calib_tune.json")

    with pytest.raises(TuneValidationError, match=message):
        manager.apply(values)


def test_tune_apply_blocked_when_not_idle(tmp_path):
    manager = CalibTuneManager(
        _fake_calibrator(),
        tmp_path / "calib_tune.json",
        idle_getter=lambda: (False, "blocked: base not STOP"),
    )

    with pytest.raises(TuneBlockedError, match="base not STOP"):
        manager.apply({"pid_kp": 1.2})


def test_tune_save_and_reload_saved_values(tmp_path):
    tune_file = tmp_path / "calib_tune.json"
    manager = CalibTuneManager(_fake_calibrator(), tune_file)
    manager.apply({"pid_kp": 1.25, "danger_margin_px": 150})
    manager.save()

    calibrator = _fake_calibrator()
    loaded = CalibTuneManager(calibrator, tune_file)

    assert loaded.status()["saved"]["exists"] is True
    assert calibrator.robot_state.pid.kp == pytest.approx(1.25)
    assert calibrator.steering_controller._danger_margin == 150


def test_tune_file_missing_new_params_still_loads(tmp_path):
    tune_file = tmp_path / "calib_tune.json"
    manager = CalibTuneManager(_fake_calibrator(), tune_file)
    manager.apply({"pid_kp": 1.25})
    manager.save()
    payload = json.loads(tune_file.read_text(encoding="utf-8"))
    del payload["values"]["vp_horizon_y"]  # file saved before the param existed
    tune_file.write_text(json.dumps(payload), encoding="utf-8")

    calibrator = _fake_calibrator()
    calibrator._vision._vp_horizon_y = -600.0
    loaded = CalibTuneManager(calibrator, tune_file)

    assert loaded.status()["saved"]["exists"] is True
    assert calibrator.robot_state.pid.kp == pytest.approx(1.25)
    assert calibrator._vision._vp_horizon_y == pytest.approx(-600.0)


def test_tune_apply_updates_vp_horizon(tmp_path):
    calibrator = _fake_calibrator()
    manager = CalibTuneManager(calibrator, tmp_path / "calib_tune.json")

    status = manager.apply({"vp_horizon_y": -450})

    assert status["values"]["vp_horizon_y"] == -450
    assert calibrator._vision._vp_horizon_y == pytest.approx(-450.0)
