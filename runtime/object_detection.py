"""Object detection safety gate for dashboard route scripts."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

try:
    import numpy as np  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    import cv2  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


@dataclass(frozen=True)
class DetectionBox:
    x: int
    y: int
    w: int
    h: int
    label: str
    conf: float
    state: str = "far"

    def as_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "label": self.label,
            "conf": round(float(self.conf), 4),
            "state": self.state,
        }


@dataclass(frozen=True)
class ObjectDetectionConfig:
    enabled: bool = False
    model_path: str = ""
    conf_threshold: float = 0.5
    input_size: tuple[int, int] = (640, 640)
    async_enabled: bool = True
    interval_s: float = 0.2
    near_roi: str = "lower_center"
    near_area_ratio: float = 0.03
    detect_hold_s: float = 0.1
    clear_hold_s: float = 0.5
    stop_on_any_detection: bool = True
    stop_on_person: bool = True
    person_label: str = "person"
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectDetectionStatus:
    object_state: str
    object_pause_active: bool
    object_count: int
    object_label: str | None
    object_conf: float | None
    object_boxes: tuple[DetectionBox, ...]
    object_detector_error: str | None = None

    def telemetry(self) -> dict[str, Any]:
        return {
            "object_state": self.object_state,
            "object_pause_active": self.object_pause_active,
            "object_count": self.object_count,
            "object_label": self.object_label,
            "object_conf": self.object_conf,
            "object_boxes": [box.as_dict() for box in self.object_boxes],
            "object_detector_error": self.object_detector_error,
        }


class ObjectPauseGate:
    """Debounces near/clear transitions so route pause does not flicker."""

    def __init__(self, *, detect_hold_s: float, clear_hold_s: float) -> None:
        self._detect_hold_s = max(0.0, float(detect_hold_s))
        self._clear_hold_s = max(0.0, float(clear_hold_s))
        self._active = False
        self._near_since: float | None = None
        self._clear_since: float | None = None

    @property
    def active(self) -> bool:
        return self._active

    def update(self, near: bool, now: float | None = None) -> bool:
        ts = time.monotonic() if now is None else float(now)
        if near:
            self._clear_since = None
            if self._near_since is None:
                self._near_since = ts
            if ts - self._near_since >= self._detect_hold_s:
                self._active = True
            return self._active

        self._near_since = None
        if self._active:
            if self._clear_since is None:
                self._clear_since = ts
            if ts - self._clear_since >= self._clear_hold_s:
                self._active = False
                self._clear_since = None
        return self._active

    def trigger_immediately(self) -> bool:
        """Latch a confirmed critical object without the normal detect debounce."""
        self._near_since = None
        self._clear_since = None
        self._active = True
        return True


class ObjectDetector:
    """OpenCV DNN ONNX detector with near/far safety classification."""

    def __init__(self, config: ObjectDetectionConfig) -> None:
        self.config = config
        self._gate = ObjectPauseGate(
            detect_hold_s=config.detect_hold_s,
            clear_hold_s=config.clear_hold_s,
        )
        self._net: Any | None = None
        self._error: str | None = None

        if not config.enabled:
            return
        if cv2 is None:
            self._error = "opencv-python is not installed"
            logger.warning("Object detection enabled but OpenCV is not installed")
            return
        if np is None:
            self._error = "numpy is not installed"
            logger.warning("Object detection enabled but NumPy is not installed")
            return
        if not config.model_path:
            self._error = "OBJECT_DETECTION_MODEL not set"
            logger.warning("Object detection enabled but OBJECT_DETECTION_MODEL is not set")
            return
        model_path = Path(config.model_path)
        if not model_path.is_file():
            self._error = f"model not found: {model_path}"
            logger.warning("Object detection model not found: %s", model_path)
            return
        try:
            self._net = cv2.dnn.readNetFromONNX(str(model_path))
        except cv2.error as exc:
            self._error = f"failed to load model: {exc}"
            logger.warning("Object detection model load failed: %s", exc)

    @classmethod
    def from_env(cls) -> "ObjectDetector | AsyncObjectDetector":
        config = ObjectDetectionConfig(
            enabled=_env_bool("OBJECT_DETECTION_ENABLED", False),
            model_path=os.getenv("OBJECT_DETECTION_MODEL", "").strip(),
            conf_threshold=_env_float("OBJECT_DETECTION_CONF", 0.5),
            input_size=_parse_size(os.getenv("OBJECT_DETECTION_INPUT_SIZE", "640x640")),
            async_enabled=_env_bool("OBJECT_DETECTION_ASYNC", True),
            interval_s=_env_float("OBJECT_DETECTION_INTERVAL_S", 0.2),
            near_roi=os.getenv("OBJECT_NEAR_ROI", "lower_center").strip().lower() or "lower_center",
            near_area_ratio=_env_float("OBJECT_NEAR_AREA_RATIO", 0.03),
            detect_hold_s=_env_float("OBJECT_DETECT_HOLD_S", 0.1),
            clear_hold_s=_env_float("OBJECT_CLEAR_HOLD_S", 0.5),
            stop_on_any_detection=_env_bool("OBJECT_STOP_ON_ANY_DETECTION", True),
            stop_on_person=_env_bool("OBJECT_STOP_ON_PERSON", True),
            person_label=os.getenv("OBJECT_PERSON_LABEL", "person").strip().casefold() or "person",
            labels=_load_labels(os.getenv("OBJECT_DETECTION_LABELS", "")),
        )
        detector = cls(config)
        if config.enabled and config.async_enabled:
            return AsyncObjectDetector(detector, interval_s=config.interval_s)
        return detector

    @property
    def error(self) -> str | None:
        return self._error

    def close(self) -> None:
        return None

    def process(self, frame: np.ndarray, now: float | None = None) -> ObjectDetectionStatus:
        if not self.config.enabled:
            return _status("none", False, (), None)
        if self._error is not None:
            active = self._gate.update(False, now)
            return _status("none", active, (), self._error)
        if self._net is None:
            active = self._gate.update(False, now)
            return _status("none", active, (), "detector unavailable")

        try:
            boxes = classify_near_far(
                self._detect_boxes(frame),
                frame.shape,
                near_roi=self.config.near_roi,
                near_area_ratio=self.config.near_area_ratio,
            )
            person_detected = self.config.stop_on_person and has_label(
                boxes,
                self.config.person_label,
            )
            near_detected = any(box.state == "near" for box in boxes)
            critical_detected = is_critical_detection(
                boxes,
                stop_on_any_detection=self.config.stop_on_any_detection,
                person_detected=person_detected,
            )
            # Any configured critical detection is a hard-stop event; do not
            # wait for the generic near-object debounce. The clear debounce
            # still applies once the detector no longer sees the object.
            active = (
                self._gate.trigger_immediately()
                if critical_detected
                else self._gate.update(near_detected, now)
            )
            raw_state = "near" if (near_detected or critical_detected) else ("far" if boxes else "none")
            return _status(raw_state, active, boxes, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Object detection failed: %s", exc)
            active = self._gate.update(False, now)
            return _status("none", active, (), str(exc))

    def _detect_boxes(self, frame: np.ndarray) -> tuple[DetectionBox, ...]:
        assert cv2 is not None
        assert np is not None
        assert self._net is not None
        width, height = self.config.input_size
        blob = cv2.dnn.blobFromImage(frame, 1.0 / 255.0, (width, height), swapRB=True, crop=False)
        self._net.setInput(blob)
        outputs = self._net.forward()
        return _parse_dnn_output(
            outputs,
            frame.shape,
            self.config.input_size,
            self.config.conf_threshold,
            self.config.labels,
        )

class AsyncObjectDetector:
    """Runs DNN inference off the control loop and returns the latest result."""

    def __init__(self, detector: ObjectDetector, *, interval_s: float) -> None:
        self.config = detector.config
        self._detector = detector
        self._interval_s = max(0.0, float(interval_s))
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._closed = False
        self._last_submit = 0.0
        self._pending_frame: Any | None = None
        self._pending_now: float | None = None
        self._status = _status("none", False, (), detector.error)
        self._thread = threading.Thread(
            target=self._run,
            name="object-detector",
            daemon=True,
        )
        self._thread.start()

    def process(self, frame: np.ndarray, now: float | None = None) -> ObjectDetectionStatus:
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            status = self._status
            if self._closed or ts - self._last_submit < self._interval_s:
                return status
            self._last_submit = ts
            self._pending_frame = frame.copy()
            self._pending_now = ts
            self._event.set()
            return status

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._event.set()
        self._thread.join(timeout=1.0)
        self._detector.close()

    def _run(self) -> None:
        while True:
            self._event.wait()
            with self._lock:
                if self._closed:
                    return
                frame = self._pending_frame
                ts = self._pending_now
                self._pending_frame = None
                self._pending_now = None
                self._event.clear()
            if frame is None:
                continue
            status = self._detector.process(frame, now=ts)
            with self._lock:
                self._status = status


def classify_near_far(
    boxes: Iterable[DetectionBox],
    frame_shape: tuple[int, ...],
    *,
    near_roi: str,
    near_area_ratio: float,
) -> tuple[DetectionBox, ...]:
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    rx1, ry1, rx2, ry2 = _roi_rect(frame_w, frame_h, near_roi)
    min_area = max(0.0, float(near_area_ratio)) * float(frame_w * frame_h)
    classified: list[DetectionBox] = []
    for box in boxes:
        cx = box.x + box.w / 2.0
        cy = box.y + box.h / 2.0
        in_roi = rx1 <= cx <= rx2 and ry1 <= cy <= ry2
        near = in_roi and (box.w * box.h) >= min_area
        classified.append(replace(box, state="near" if near else "far"))
    return tuple(classified)


def has_label(boxes: Iterable[DetectionBox], label: str) -> bool:
    """Return whether a detector result contains a case-insensitive label."""
    wanted = label.strip().casefold()
    return bool(wanted) and any(box.label.strip().casefold() == wanted for box in boxes)


def is_critical_detection(
    boxes: Iterable[DetectionBox],
    *,
    stop_on_any_detection: bool,
    person_detected: bool,
) -> bool:
    """Apply the configured hard-stop policy to accepted detector boxes."""
    box_tuple = tuple(boxes)
    return bool(box_tuple) and (stop_on_any_detection or person_detected)


def draw_object_boxes(frame: np.ndarray, boxes: Iterable[DetectionBox]) -> np.ndarray:
    if cv2 is None:
        return frame
    for box in boxes:
        color = (0, 0, 255) if box.state == "near" else (0, 200, 255)
        x1, y1 = box.x, box.y
        x2, y2 = box.x + box.w, box.y + box.h
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        label = f"{box.state} {box.label} {box.conf:.2f}"
        cv2.putText(
            frame,
            label,
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return frame


def _status(
    state: str,
    pause_active: bool,
    boxes: Iterable[DetectionBox],
    error: str | None,
) -> ObjectDetectionStatus:
    box_tuple = tuple(boxes)
    primary = max(box_tuple, key=lambda box: box.conf, default=None)
    return ObjectDetectionStatus(
        object_state=state,
        object_pause_active=bool(pause_active),
        object_count=len(box_tuple),
        object_label=primary.label if primary is not None else None,
        object_conf=round(float(primary.conf), 4) if primary is not None else None,
        object_boxes=box_tuple,
        object_detector_error=error,
    )


def _parse_dnn_output(
    outputs: Any,
    frame_shape: tuple[int, ...],
    input_size: tuple[int, int],
    conf_threshold: float,
    labels: tuple[str, ...],
) -> tuple[DetectionBox, ...]:
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    rects: list[list[int]] = []
    scores: list[float] = []
    boxes: list[DetectionBox] = []

    for row in _iter_output_rows(outputs):
        parsed = _parse_row(row, frame_w, frame_h, input_size, conf_threshold, labels)
        if parsed is None:
            continue
        rects.append([parsed.x, parsed.y, parsed.w, parsed.h])
        scores.append(parsed.conf)
        boxes.append(parsed)

    if not boxes:
        return ()
    if cv2 is None:
        return tuple(boxes)
    indices = cv2.dnn.NMSBoxes(rects, scores, float(conf_threshold), 0.45)
    if len(indices) == 0:
        return ()
    keep = np.asarray(indices).reshape(-1).tolist()
    return tuple(boxes[int(i)] for i in keep)


def _iter_output_rows(outputs: Any) -> Iterable[np.ndarray]:
    if np is None:
        return
    output_list = outputs if isinstance(outputs, (list, tuple)) else [outputs]
    for output in output_list:
        arr = np.asarray(output)
        if arr.size == 0:
            continue
        arr = np.squeeze(arr)
        if arr.ndim == 1:
            yield arr
            continue
        if arr.ndim != 2:
            continue
        if arr.shape[0] <= 256 and arr.shape[0] < arr.shape[1]:
            arr = arr.T
        for row in arr:
            yield np.asarray(row)


def _parse_row(
    row: np.ndarray,
    frame_w: int,
    frame_h: int,
    input_size: tuple[int, int],
    conf_threshold: float,
    labels: tuple[str, ...],
) -> DetectionBox | None:
    if np is None:
        return None
    values = np.asarray(row, dtype=np.float32).reshape(-1)
    if values.size < 6:
        return None

    if values.size == 6 and values[2] > values[0] and values[3] > values[1]:
        conf = float(values[4])
        class_id = int(round(float(values[5])))
        x1, y1, x2, y2 = _scale_xyxy(values[:4], frame_w, frame_h, input_size)
    else:
        class_scores = values[4:]
        class_id = int(np.argmax(class_scores))
        conf = float(class_scores[class_id])
        if values.size > 6:
            obj = float(values[4])
            scores = values[5:]
            if 0.0 <= obj <= 1.0 and scores.size:
                alt_class_id = int(np.argmax(scores))
                alt_conf = obj * float(scores[alt_class_id])
                if alt_conf > conf:
                    conf = alt_conf
                    class_id = alt_class_id
        x1, y1, x2, y2 = _scale_cxcywh(values[:4], frame_w, frame_h, input_size)

    if conf < conf_threshold:
        return None
    x1 = max(0, min(frame_w - 1, int(round(x1))))
    y1 = max(0, min(frame_h - 1, int(round(y1))))
    x2 = max(0, min(frame_w - 1, int(round(x2))))
    y2 = max(0, min(frame_h - 1, int(round(y2))))
    if x2 <= x1 or y2 <= y1:
        return None
    label = labels[class_id] if 0 <= class_id < len(labels) else "object"
    return DetectionBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1, label=label, conf=conf)


def _scale_cxcywh(
    coords: np.ndarray,
    frame_w: int,
    frame_h: int,
    input_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    cx, cy, w, h = [float(v) for v in coords]
    if max(abs(cx), abs(cy), abs(w), abs(h)) <= 1.5:
        cx *= frame_w
        w *= frame_w
        cy *= frame_h
        h *= frame_h
    else:
        in_w, in_h = input_size
        cx *= frame_w / float(in_w)
        w *= frame_w / float(in_w)
        cy *= frame_h / float(in_h)
        h *= frame_h / float(in_h)
    return cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0


def _scale_xyxy(
    coords: np.ndarray,
    frame_w: int,
    frame_h: int,
    input_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = [float(v) for v in coords]
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
        return x1 * frame_w, y1 * frame_h, x2 * frame_w, y2 * frame_h
    in_w, in_h = input_size
    return (
        x1 * frame_w / float(in_w),
        y1 * frame_h / float(in_h),
        x2 * frame_w / float(in_w),
        y2 * frame_h / float(in_h),
    )


def _roi_rect(frame_w: int, frame_h: int, near_roi: str) -> tuple[float, float, float, float]:
    if near_roi == "full":
        return 0.0, 0.0, float(frame_w), float(frame_h)
    return frame_w * 0.2, frame_h * 0.45, frame_w * 0.8, float(frame_h)


def _parse_size(value: str) -> tuple[int, int]:
    raw = value.strip().lower().replace(",", "x")
    parts = [part for part in raw.split("x") if part]
    if len(parts) != 2:
        return 640, 640
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return 640, 640
    return max(1, width), max(1, height)


def _load_labels(value: str) -> tuple[str, ...]:
    raw = value.strip()
    if not raw:
        return ()
    path = Path(raw)
    if path.is_file():
        return tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default
