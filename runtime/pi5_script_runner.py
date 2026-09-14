"""Route script runner for Pi5 (no direct hardware)."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from runtime.resource_limits import validate_route_steps

logger = logging.getLogger(__name__)

BASE_MAP: dict[str, tuple[int, int, int]] = {
    "STOP": (0, 0, 0),
    "FORWARD": (0, 1, 0),
    "BACKWARD": (0, 0, 1),
    "LOCK": (1, 0, 1),
    "UNLOCK": (1, 1, 0),
    "TURN_LEFT": (1, 0, 0),
    "TURN_RIGHT": (0, 1, 1),
}

_VALID_ACTIONS = {"forward", "backward", "straight", "left", "right", "turn_left", "turn_right", "stop", "pause"}


class Pi5ScriptRunner:
    """Runs route scripts in a background thread using direct callbacks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._paused = False
        self._pause_reason = ""
        self._steps: list[dict[str, Any]] = []
        self._current_step: dict[str, Any] | None = None
        self._current_step_idx = -1
        self._step_elapsed_s = 0.0
        self._step_duration_s = 0.0
        self._current_base_cmd = "STOP"
        self._current_angle: float | None = None
        self._base_cb: Callable[[str], None] | None = None
        self._servo_cb: Callable[[float], None] | None = None
        self._relay_cb: Callable[[str], None] | None = None
        self._center_angle: float = 90.0 + float(os.getenv("SERVO_CENTER_ANGLE", "-8"))
        self._max_steer: float = float(os.getenv("MAX_STEERING_OFFSET", "60"))

    def set_handlers(
        self,
        base_cb: Callable[[str], None],
        servo_cb: Callable[[float], None],
        relay_cb: Callable[[str], None],
    ) -> None:
        self._base_cb = base_cb
        self._servo_cb = servo_cb
        self._relay_cb = relay_cb

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def vision_pid_active(self) -> bool:
        """True when current step should let vision PID control servo."""
        with self._lock:
            if self._paused:
                return False
            if not self._running or self._current_step is None:
                return True
            action = self._current_step.get("action", "stop")
        return action not in ("left", "right", "backward")

    def status(self) -> dict[str, Any]:
        with self._lock:
            remaining = max(0.0, self._step_duration_s - self._step_elapsed_s)
            return {
                "running": self._running,
                "paused": self._paused,
                "pause_reason": self._pause_reason,
                "current_step": self._current_step_idx + 1 if self._running else 0,
                "step": dict(self._current_step) if self._current_step is not None else None,
                "total": len(self._steps),
                "step_elapsed_s": self._step_elapsed_s,
                "step_remaining_s": remaining,
            }

    def submit(self, steps: list[dict[str, Any]]) -> bool:
        normalized_steps = validate_route_steps(steps)
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._paused = False
            self._pause_reason = ""
            self._step_elapsed_s = 0.0
            self._step_duration_s = 0.0
            self._steps = normalized_steps
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._paused = False
            self._pause_reason = ""

    def set_paused(self, paused: bool, reason: str = "") -> None:
        base_cmd: str | None = None
        angle: float | None = None
        with self._lock:
            if not self._running:
                self._paused = False
                self._pause_reason = ""
                return
            was_paused = self._paused
            self._paused = bool(paused)
            self._pause_reason = str(reason) if paused else ""
            if was_paused and not self._paused:
                base_cmd = self._current_base_cmd
                angle = self._current_angle

        if base_cmd is not None and self._base_cb is not None:
            self._base_cb(base_cmd)
        if angle is not None and self._servo_cb is not None:
            self._servo_cb(angle)

    def _run(self) -> None:
        logger.info("Route script start (%d steps)", len(self._steps))
        try:
            for idx, step in enumerate(self._steps):
                if not self.is_running():
                    break
                with self._lock:
                    self._current_step_idx = idx
                    self._current_step = step
                self._execute_step(step)
        except Exception as exc:
            logger.exception("Route script crashed: %s", exc)
        finally:
            with self._lock:
                was_paused = self._paused
                self._current_step = None
                self._current_step_idx = -1
                self._step_elapsed_s = 0.0
                self._step_duration_s = 0.0
                self._current_base_cmd = "STOP"
                self._current_angle = None
                self._running = False
                self._paused = False
                self._pause_reason = ""
            if self._base_cb is not None:
                self._base_cb("STOP")
            if self._servo_cb is not None and not was_paused:
                self._servo_cb(self._center_angle)
            logger.info("Route script finished")

    def _execute_step(self, step: dict[str, Any]) -> None:
        action = step.get("action", "stop")
        duration_s = max(0.0, float(step.get("duration_s", 1.0)))

        if action in ("forward", "straight"):
            base_cmd = "FORWARD"
            angle: float | None = None
        elif action == "backward":
            base_cmd = "BACKWARD"
            angle = self._center_angle
        elif action == "left":
            base_cmd = "FORWARD"
            angle = self._center_angle + self._max_steer
        elif action == "right":
            base_cmd = "FORWARD"
            angle = self._center_angle - self._max_steer
        elif action == "turn_left":
            base_cmd = "TURN_LEFT"
            angle = None
        elif action == "turn_right":
            base_cmd = "TURN_RIGHT"
            angle = None
        else:
            base_cmd = "STOP"
            angle = None

        with self._lock:
            self._step_elapsed_s = 0.0
            self._step_duration_s = duration_s
            self._current_base_cmd = base_cmd
            self._current_angle = angle

        self._publish_current_command(base_cmd, angle)

        last_tick = time.monotonic()
        while True:
            time.sleep(0.05)
            now = time.monotonic()
            with self._lock:
                if not self._running:
                    break
                if self._paused:
                    last_tick = now
                    continue
                self._step_elapsed_s += now - last_tick
                done = self._step_elapsed_s >= self._step_duration_s
                last_tick = now
            if done:
                break

    def _publish_current_command(self, base_cmd: str, angle: float | None) -> None:
        if self._base_cb is not None:
            self._base_cb(base_cmd)
        if angle is not None and self._servo_cb is not None:
            self._servo_cb(angle)
