# Core Utility Contracts

This document records the core utility contracts after calibration
centralization. Transport, Docker, MQTT, and servo utilities are outside this
refinement gate.

## Unified Calibration

Module: `unified_calibration_components`

### `CalibrationResult`

Frozen result returned by `UnifiedCalibrator.process_frame(frame, frame_num)`:

- `steering_angle: float`
- `control_state: str`
- `observation_angle: float | None`
- `calibration_active: bool`
- `telemetry: dict[str, Any]`
- `debug_data: dict[str, Any]`

Live and offline callers must consume this result rather than independently
recomputing calibration geometry or steering.

### `CalibrationProcessingError`

Unexpected failures from `process_frame()` and `update()` are wrapped with:

- failing frame number;
- stage identifier;
- process identifier;
- original exception type;
- original exception detail.

The structured fields are available through `error.diagnostic` and
`error.diagnostic.as_dict()`. The original exception remains chained as
`error.__cause__`.

Live runtime logging includes these fields before safe shutdown. Offline video
processing logs them before re-raising the error.

### `VisionProcessor.process_frame_debug`

Input: BGR or grayscale NumPy frame.

Output: `(lines, debug)` where `lines` is a list of integer `(x1, y1, x2, y2)`
segments and `debug` contains current visualization intermediates:

- `gray`
- `roi`
- `preprocessed`
- `edges`
- `hough_vis`
- `grouped_vis`
- `lines_count`
- `selected_lines`

Existing HUD helpers consume the outer `vision_debug` key.

### `VisionProcessor._apply_geometric_filter`

Current private behavior (see `docs/vision.md`):

- keeps vertical lines and lines with `|slope| >= VISION_MIN_ABS_SLOPE`;
- keeps only the dominant family voting at horizon row `VISION_VP_HORIZON_Y`;
- selects the nearest line group on each side of the image centre;
- returns `None` unless both sides have a group.

### `GeometryCalculator`

- `calculate_vanishing_point(line1, line2) -> tuple[int, int] | None`
- `calculate_bottom_intercepts(line1, line2, frame_height) -> tuple[int, int]`
- `map_vp_to_angle(vp_x, frame_width) -> float`

These functions perform geometry only and do not select lines or steering
states.

### `UnifiedCalibrator.update`

Offline compatibility wrapper. It calls `process_frame()` exactly once, then
applies the current telemetry, visualization, video, streaming, and terminal
side effects. It returns only the final steering angle.

## Runtime Helpers

Module: `runtime.video_runtime_helpers`

The current helper module owns CLI builders, camera/video initialization, CSV
writers, overlays, the compatibility debug panel, and loop sleeping. These
responsibilities remain unchanged in this gate and are scheduled for later
separation.

Consumers of debug dictionaries must use `.get()` for optional values.

## Telemetry

The current telemetry dictionary includes frame timing, line count, geometry,
state, steering, PID snapshots, and stream information. CSV consumers must
tolerate missing optional fields. Dynamic namespaced telemetry and JSONL are
planned for the dedicated telemetry gate.
