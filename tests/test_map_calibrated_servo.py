from __future__ import annotations

import pytest

from main_pi5 import map_calibrated_servo

HOME_OFFSET = -28
LIMIT = 80
HOME = 90 + HOME_OFFSET


def _map(cmd: float, reverse: bool = False) -> float:
    return map_calibrated_servo(cmd, home_offset_deg=HOME_OFFSET, limit_deg=LIMIT, reverse=reverse)


def test_center_maps_to_home() -> None:
    assert _map(90) == pytest.approx(HOME)


@pytest.mark.parametrize("cmd", [91, 93, 95, 97, 120])
def test_left_side_commands_stay_above_home(cmd: float) -> None:
    assert _map(cmd) > HOME


@pytest.mark.parametrize("cmd", [89, 87, 83, 60])
def test_right_side_commands_stay_below_home(cmd: float) -> None:
    assert _map(cmd) < HOME


def test_mapping_is_monotonic_across_full_range() -> None:
    outputs = [_map(cmd) for cmd in range(0, 181)]
    assert all(b >= a for a, b in zip(outputs, outputs[1:]))


def test_reverse_mirrors_side_around_home() -> None:
    assert _map(97, reverse=True) < HOME
    assert _map(83, reverse=True) > HOME
