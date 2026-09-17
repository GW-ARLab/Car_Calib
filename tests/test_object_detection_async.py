"""Tests for async object detection wrapper."""

from __future__ import annotations

import time

import numpy as np

from runtime.object_detection import (
    AsyncObjectDetector,
    ObjectDetectionConfig,
    ObjectDetectionStatus,
)


class SlowDetector:
    config = ObjectDetectionConfig(enabled=True)

    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    @property
    def error(self) -> str | None:
        return None

    def process(self, frame: np.ndarray, now: float | None = None) -> ObjectDetectionStatus:
        self.calls += 1
        time.sleep(0.05)
        return ObjectDetectionStatus(
            object_state="far",
            object_pause_active=False,
            object_count=1,
            object_label="person",
            object_conf=0.9,
            object_boxes=(),
            object_detector_error=None,
        )

    def close(self) -> None:
        self.closed = True


def test_async_object_detector_process_returns_without_waiting_for_dnn():
    detector = SlowDetector()
    async_detector = AsyncObjectDetector(detector, interval_s=0.0)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    start = time.monotonic()
    initial = async_detector.process(frame, now=1.0)
    elapsed = time.monotonic() - start

    assert elapsed < 0.02
    assert initial.object_state == "none"

    deadline = time.monotonic() + 1.0
    latest = initial
    while time.monotonic() < deadline:
        latest = async_detector.process(frame, now=2.0)
        if latest.object_state == "far":
            break
        time.sleep(0.01)

    async_detector.close()
    assert latest.object_state == "far"
    assert detector.calls >= 1
    assert detector.closed
