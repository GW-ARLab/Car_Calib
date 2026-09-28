"""Characterization tests for the current unified calibration behavior.

These tests intentionally describe Phase 1 behavior. Later phases may update
them only when an approved phase deliberately changes the corresponding
contract.
"""

from __future__ import annotations

import csv
import io
import logging

import numpy as np
import pytest

from control.steering_controller import SteeringController
from models.robot_state import PIDConstants, RobotState
from runtime.video_runtime_helpers import build_main_arg_parser
from unified_calibration_components import (
    CalibrationProcessingError,
    GeometryCalculator,
    TelemetryLogger,
    UnifiedCalibrator,
    VisionProcessor,
)


class _VisionStub:
    def __init__(self) -> None:
        self.process_calls = 0
        self.filter_calls = 0

    def process_frame_debug(self, frame: np.ndarray):
        self.process_calls += 1
        return (
            [(50, 200, 100, 0), (100, 0, 150, 200)],
            {
                "lines_count": 2,
                "selected_lines": [],
                "grouped_vis": frame.copy(),
            },
        )

    def _apply_geometric_filter(self, lines):
        self.filter_calls += 1
        return lines[0], lines[1]


class _TelemetryStub:
    def __init__(self) -> None:
        self.logged: dict[str, object] | None = None

    def update_visuals(self, frame, telemetry_data, debug_data):
        return frame.copy()

    def log_state(self, frame_num, telemetry_data):
        self.logged = dict(telemetry_data)

    def write_video(self, frame):
        return None

    def publish_stream(self, frame, telemetry_data):
        return None


class _FailingVisionStub(_VisionStub):
    def process_frame_debug(self, frame: np.ndarray):
        raise RuntimeError("synthetic extraction failure")


class _FailingTelemetryStub(_TelemetryStub):
    def log_state(self, frame_num, telemetry_data):
        raise OSError("synthetic telemetry failure")


class _FailingGeometryStub(GeometryCalculator):
    @staticmethod
    def calculate_vanishing_point(line1, line2):
        raise ArithmeticError("synthetic geometry failure")


def _make_calibrator_for_characterization() -> UnifiedCalibrator:
    calibrator = UnifiedCalibrator.__new__(UnifiedCalibrator)
    calibrator._logger = logging.getLogger("test.unified")
    calibrator._robot_state = RobotState(
        pid=PIDConstants(kp=1.0, ki=0.05, kd=0.1),
        servo_center_angle=90.0,
        max_steering_offset=30.0,
    )
    calibrator._vision = _VisionStub()
    calibrator._geometry = GeometryCalculator()
    calibrator._steering = SteeringController(
        pid_constants=calibrator._robot_state.pid,
        danger_margin=50,
        nudge_deg=5.0,
        inner_thresh=3.0,
        outer_thresh=5.0,
        center_angle=90.0,
        max_offset=30.0,
    )
    calibrator._telemetry = _TelemetryStub()
    calibrator._target_hz = 0.0
    calibrator._terminal_log_enabled = False
    calibrator._last_terminal_log_time = 0.0
    calibrator._stream_configs = {}
    calibrator._stream_enabled = False
    calibrator._last_rendered_frame = None
    return calibrator


def test_vision_filter_selects_longest_opposite_slope_pair() -> None:
    processor = VisionProcessor(roi_height_pct=0.6)
    selected = processor._apply_geometric_filter(
        [
            (0, 20, 10, 0),
            (0, 100, 50, 0),
            (0, 0, 10, 20),
            (0, 0, 50, 100),
            (0, 0, 100, 1),
            (5, 0, 5, 100),
        ]
    )

    assert selected == ((0, 100, 50, 0), (0, 0, 50, 100))


def test_geometry_maps_intersection_intercepts_and_angle() -> None:
    geometry = GeometryCalculator()
    negative = (0, 100, 100, 0)
    positive = (100, 0, 200, 100)

    assert geometry.calculate_vanishing_point(negative, positive) == (100, 0)
    assert geometry.calculate_bottom_intercepts(negative, positive, 200) == (-100, 300)
    assert geometry.map_vp_to_angle(100, 200) == pytest.approx(90.0)
    assert geometry.map_vp_to_angle(50, 0) == pytest.approx(90.0)
    assert geometry.classify_point((100, -20), 200, 100) == "above"
    assert geometry.classify_point((220, 120), 200, 100) == "below_right"
    assert geometry.classify_point((100, 20), 200, 100) == "inside"


@pytest.mark.parametrize(
    ("vp_angle", "left", "right", "expected_angle", "expected_state"),
    [
        (None, None, None, 90.0, "GAPPING"),
        (90.0, 101, 600, 85.0, "DANGER_RIGHT"),
        (90.0, 0, 539, 95.0, "DANGER_LEFT"),
        (90.0, -250, 449, 95.0, "DANGER_LEFT"),
        (90.0, 300, 500, 90.0, "AMBIGUOUS_DANGER"),
        (90.0, 100, 540, 90.0, "TRACKING_COAST"),
        (92.0, 100, 540, 90.0, "TRACKING_COAST"),
        (100.0, 100, 540, 101.0, "TRACKING_PD"),
    ],
)
def test_steering_state_machine_current_outputs(
    vp_angle,
    left,
    right,
    expected_angle,
    expected_state,
) -> None:
    controller = SteeringController(
        pid_constants=PIDConstants(kp=1.0, ki=0.5, kd=0.1),
        danger_margin=100,
        nudge_deg=5.0,
        inner_thresh=3.0,
        outer_thresh=5.0,
        center_angle=90.0,
        max_offset=30.0,
    )

    angle, state = controller.compute_steering(vp_angle, left, right, 640)

    assert angle == pytest.approx(expected_angle)
    assert state == expected_state


def test_danger_state_diagnostics_identify_boundary_recovery_and_threshold() -> None:
    controller = SteeringController(
        pid_constants=PIDConstants(kp=1.0, ki=0.0, kd=0.0),
        danger_margin=100,
        nudge_deg=5.0,
        inner_thresh=3.0,
        outer_thresh=5.0,
        center_angle=90.0,
        max_offset=30.0,
    )

    assert controller.describe_control_state("DANGER_LEFT", 640) == {
        "danger_boundary": "RIGHT",
        "recovery_direction": "LEFT",
        "danger_threshold_x": 540,
    }
    assert controller.describe_control_state("DANGER_RIGHT", 640) == {
        "danger_boundary": "LEFT",
        "recovery_direction": "RIGHT",
        "danger_threshold_x": 100,
    }
    assert controller.describe_control_state("AMBIGUOUS_DANGER", 640) == {
        "danger_boundary": "BOTH",
        "recovery_direction": None,
        "danger_threshold_x": None,
    }


def test_steering_hysteresis_stays_active_until_inner_threshold() -> None:
    controller = SteeringController(
        pid_constants=PIDConstants(kp=1.0, ki=0.0, kd=0.0),
        danger_margin=0,
        nudge_deg=5.0,
        inner_thresh=3.0,
        outer_thresh=5.0,
        center_angle=90.0,
        max_offset=30.0,
    )

    assert controller.compute_steering(96.0, 0, 640, 640) == (96.0, "TRACKING_PD")
    assert controller.compute_steering(94.0, 0, 640, 640) == (94.0, "TRACKING_PD")
    assert controller.compute_steering(93.0, 0, 640, 640) == (90.0, "TRACKING_COAST")


def test_unified_process_frame_uses_one_vision_path() -> None:
    calibrator = _make_calibrator_for_characterization()
    frame = np.zeros((200, 200, 3), dtype=np.uint8)

    result = calibrator.process_frame(frame, frame_num=7)

    assert result.steering_angle == pytest.approx(90.0)
    assert result.observation_angle == pytest.approx(90.0)
    assert result.control_state == "TRACKING_COAST"
    assert result.calibration_active is False
    assert calibrator._vision.process_calls == 1
    assert calibrator._vision.filter_calls == 1
    assert result.telemetry["frame_num"] == 7
    assert result.telemetry["vp_angle"] == pytest.approx(90.0)
    assert result.debug_data["vision_debug"]["selected_lines"] == [
        (50, 200, 100, 0),
        (100, 0, 150, 200),
    ]
    assert result.debug_data["vision_debug"]["selected_left_line"] == (50, 200, 100, 0)
    assert result.debug_data["vision_debug"]["selected_right_line"] == (100, 0, 150, 200)
    assert result.debug_data["vision_debug"]["selected_left_line_info"]["slope"] == -4.0
    assert result.debug_data["vision_debug"]["selected_right_line_info"]["slope"] == 4.0
    assert result.telemetry["vp_location"] == "inside"


def test_unified_update_wraps_process_frame_with_telemetry_side_effects() -> None:
    calibrator = _make_calibrator_for_characterization()
    frame = np.zeros((200, 200, 3), dtype=np.uint8)

    angle = calibrator.update(frame, frame_num=8)

    assert angle == pytest.approx(90.0)
    assert calibrator._telemetry.logged is not None
    assert calibrator._telemetry.logged["frame_num"] == 8
    assert calibrator._telemetry.logged["fsm_state"] == "TRACKING_COAST"


def test_live_result_and_offline_wrapper_share_calibration_behavior() -> None:
    live_calibrator = _make_calibrator_for_characterization()
    offline_calibrator = _make_calibrator_for_characterization()
    frame = np.zeros((200, 200, 3), dtype=np.uint8)

    live_result = live_calibrator.process_frame(frame, frame_num=9)
    offline_angle = offline_calibrator.update(frame, frame_num=9)

    assert offline_angle == pytest.approx(live_result.steering_angle)
    assert offline_calibrator._telemetry.logged is not None
    assert offline_calibrator._telemetry.logged["vp_angle"] == pytest.approx(
        live_result.observation_angle
    )
    assert offline_calibrator._telemetry.logged["fsm_state"] == live_result.control_state
    assert live_calibrator._vision.process_calls == 1
    assert offline_calibrator._vision.process_calls == 1


def test_telemetry_logger_projects_known_fields() -> None:
    output = io.StringIO()
    logger = TelemetryLogger.__new__(TelemetryLogger)
    logger._csv_fieldnames = ["frame_num", "vp_angle", "lines_count", "optional"]
    logger._csv_writer = csv.DictWriter(output, fieldnames=logger._csv_fieldnames)
    logger._csv_file = None

    logger.log_state(
        3,
        {
            "vp_angle": 91.5,
            "lines_count": 4,
            "ignored": "not projected",
        },
    )

    assert output.getvalue().strip() == "3,91.5,4,"


def test_main_parser_exposes_only_vision_debug_cli() -> None:
    parser = build_main_arg_parser()

    assert parser.parse_args(["--show-vision-debug"]).show_vision_debug is True


def test_invalid_frame_error_identifies_input_process() -> None:
    calibrator = _make_calibrator_for_characterization()

    with pytest.raises(CalibrationProcessingError) as raised:
        calibrator.process_frame(None, frame_num=21)

    diagnostic = raised.value.diagnostic
    assert diagnostic.as_dict() == {
        "frame_num": 21,
        "stage": "input",
        "process": "validate_frame",
        "error_type": "ValueError",
        "detail": "frame must be a non-empty NumPy array",
    }
    assert "stage=input process=validate_frame" in str(raised.value)


def test_vision_error_identifies_exact_process_and_preserves_cause() -> None:
    calibrator = _make_calibrator_for_characterization()
    calibrator._vision = _FailingVisionStub()

    with pytest.raises(CalibrationProcessingError) as raised:
        calibrator.process_frame(np.zeros((20, 20, 3), dtype=np.uint8), frame_num=22)

    assert raised.value.diagnostic.stage == "vision"
    assert raised.value.diagnostic.process == "preprocess_and_extract_lines"
    assert raised.value.diagnostic.error_type == "RuntimeError"
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_output_error_identifies_failed_runtime_process() -> None:
    calibrator = _make_calibrator_for_characterization()
    calibrator._telemetry = _FailingTelemetryStub()

    with pytest.raises(CalibrationProcessingError) as raised:
        calibrator.update(np.zeros((200, 200, 3), dtype=np.uint8), frame_num=23)

    assert raised.value.diagnostic.stage == "runtime_output"
    assert raised.value.diagnostic.process == "write_telemetry"
    assert raised.value.diagnostic.error_type == "OSError"


def test_geometry_error_identifies_exact_operation() -> None:
    calibrator = _make_calibrator_for_characterization()
    calibrator._geometry = _FailingGeometryStub()

    with pytest.raises(CalibrationProcessingError) as raised:
        calibrator.process_frame(np.zeros((200, 200, 3), dtype=np.uint8), frame_num=24)

    assert raised.value.diagnostic.stage == "geometry"
    assert raised.value.diagnostic.process == "calculate_vanishing_point"
    assert raised.value.diagnostic.error_type == "ArithmeticError"
