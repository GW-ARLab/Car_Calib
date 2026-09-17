"""Tests for object detection near/far safety state."""

from __future__ import annotations

from runtime.object_detection import (
    DetectionBox,
    ObjectPauseGate,
    classify_near_far,
    has_label,
    is_critical_detection,
)


def test_classify_near_far_uses_lower_center_roi_and_area_ratio():
    boxes = classify_near_far(
        (
            DetectionBox(x=280, y=300, w=120, h=120, label="object", conf=0.9),
            DetectionBox(x=20, y=300, w=120, h=120, label="object", conf=0.8),
            DetectionBox(x=300, y=310, w=20, h=20, label="object", conf=0.7),
        ),
        (480, 640, 3),
        near_roi="lower_center",
        near_area_ratio=0.03,
    )

    assert [box.state for box in boxes] == ["near", "far", "far"]


def test_object_pause_gate_holds_detect_and_clear_transitions():
    gate = ObjectPauseGate(detect_hold_s=0.1, clear_hold_s=0.5)

    assert gate.update(True, now=1.0) is False
    assert gate.update(True, now=1.11) is True
    assert gate.update(False, now=1.2) is True
    assert gate.update(False, now=1.69) is True
    assert gate.update(False, now=1.71) is False


def test_person_label_is_case_insensitive_and_bypasses_detect_hold():
    boxes = (DetectionBox(x=10, y=10, w=20, h=20, label="PERSON", conf=0.9),)
    assert has_label(boxes, "person")

    gate = ObjectPauseGate(detect_hold_s=5.0, clear_hold_s=0.5)
    assert gate.trigger_immediately() is True
    # Once person disappears, retain stop long enough to avoid flicker.
    assert gate.update(False, now=1.0) is True
    assert gate.update(False, now=1.6) is False


def test_any_detected_box_can_trigger_the_same_immediate_stop_gate():
    boxes = (DetectionBox(x=10, y=10, w=20, h=20, label="bicycle", conf=0.9),)
    assert is_critical_detection(
        boxes,
        stop_on_any_detection=True,
        person_detected=False,
    )
    assert not is_critical_detection(
        boxes,
        stop_on_any_detection=False,
        person_detected=False,
    )

    gate = ObjectPauseGate(detect_hold_s=5.0, clear_hold_s=0.5)
    assert gate.trigger_immediately() is True
