from __future__ import annotations

import pytest

from control.steering_controller import SteeringController
from models.robot_state import PIDConstants


def _controller(danger_margin: int = 130) -> SteeringController:
    return SteeringController(
        pid_constants=PIDConstants(kp=1.0, ki=0.0, kd=0.3),
        danger_margin=danger_margin,
        nudge_deg=7.0,
        inner_thresh=3.0,
        outer_thresh=8.0,
        center_angle=90.0,
        max_offset=90.0,
    )


def test_gapping_when_vision_lost() -> None:
    angle, state = _controller().compute_steering(
        vp_angle=None,
        left_intercept=None,
        right_intercept=None,
        frame_width=640,
    )
    assert state == "GAPPING"
    assert angle == pytest.approx(90.0)


def test_intercepts_just_outside_margin_stay_in_tracking_not_danger() -> None:
    """Reproduces the real-car report: L=131, R=490, margin=130, heading error
    only +3.1 deg -- should land in TRACKING_COAST, not a DANGER state, since
    neither intercept is actually inside the danger band near either edge."""
    angle, state = _controller(danger_margin=130).compute_steering(
        vp_angle=93.1,
        left_intercept=131,
        right_intercept=490,
        frame_width=640,
    )
    assert state == "TRACKING_COAST"
    assert angle == pytest.approx(90.0)


def test_danger_left_when_left_intercept_encroaches_left_edge() -> None:
    """Left intercept inside the left edge band means both lines shifted left in
    the image, i.e. the car drifted toward the right line -- recover by turning
    left (angle above center, same side PD uses for a VP left of center)."""
    angle, state = _controller(danger_margin=130).compute_steering(
        vp_angle=90.0,
        left_intercept=50,
        right_intercept=490,
        frame_width=640,
    )
    assert state == "DANGER_LEFT"
    assert angle == pytest.approx(97.0)


def test_danger_right_when_right_intercept_encroaches_right_edge() -> None:
    """Right intercept inside the right edge band means the car drifted toward
    the left line -- recover by turning right (angle below center, same side
    PD uses for a VP right of center)."""
    angle, state = _controller(danger_margin=130).compute_steering(
        vp_angle=90.0,
        left_intercept=150,
        right_intercept=600,
        frame_width=640,
    )
    assert state == "DANGER_RIGHT"
    assert angle == pytest.approx(83.0)


@pytest.mark.parametrize(
    "state, expected",
    [("DANGER_LEFT", "LEFT"), ("DANGER_RIGHT", "RIGHT"), ("TRACKING_PD", "NONE")],
)
def test_recovery_direction_matches_nudge_side(state: str, expected: str) -> None:
    details = _controller().describe_control_state(state, 640)
    assert details["recovery_direction"] == expected


def test_tracking_coast_inside_inner_threshold() -> None:
    angle, state = _controller().compute_steering(
        vp_angle=91.0,
        left_intercept=200,
        right_intercept=440,
        frame_width=640,
    )
    assert state == "TRACKING_COAST"
    assert angle == pytest.approx(90.0)


def test_tracking_pd_past_outer_threshold() -> None:
    angle, state = _controller().compute_steering(
        vp_angle=100.0,
        left_intercept=200,
        right_intercept=440,
        frame_width=640,
    )
    assert state == "TRACKING_PD"
    assert angle != pytest.approx(90.0)
