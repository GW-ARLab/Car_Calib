"""Tests for direct Jetson route script runner."""

from __future__ import annotations

import time

import pytest

from runtime.pi5_script_runner import Pi5ScriptRunner


@pytest.mark.parametrize("pause_reason", ["object_detected", "manual_override"])
def test_runner_pause_freezes_step_elapsed_and_resume_republishes(pause_reason: str):
    base_cmds: list[str] = []
    servo_angles: list[float] = []
    runner = Pi5ScriptRunner()
    runner.set_handlers(base_cmds.append, servo_angles.append, lambda _state: None)

    assert runner.submit([{"action": "left", "duration_s": 0.25}]) is True
    time.sleep(0.08)
    runner.set_paused(True, pause_reason)
    paused = runner.status()
    elapsed = paused["step_elapsed_s"]

    assert paused["paused"] is True
    assert paused["pause_reason"] == pause_reason
    time.sleep(0.16)
    assert runner.status()["step_elapsed_s"] == pytest.approx(elapsed, abs=0.02)

    runner.set_paused(False)
    assert base_cmds[-1] == "FORWARD"
    assert len(servo_angles) >= 2

    deadline = time.monotonic() + 1.0
    while runner.is_running() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert runner.is_running() is False
    assert base_cmds[-1] == "STOP"
