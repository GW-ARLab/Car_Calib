# Unified Calibration Components

`UnifiedCalibrator` is the only calibration facade used by live and offline
entrypoints. A frame is processed once by the shared vision path.

## Current Components

- `ConfigManager`: reads grouped runtime settings.
- `VisionProcessor`: performs ROI, grayscale, blur, Canny, Hough extraction,
  and current opposite-sign pair selection.
- `GeometryCalculator`: calculates line intersection, bottom intercepts, and a
  linear vanishing-point angle.
- `SteeringController`: applies danger overrides, hysteresis, and current PD
  steering.
- `TelemetryLogger`: owns current CSV, overlay, debug panel, video, HTTPS frame
  publishing, artifacts, and loop sleep.
- `UnifiedCalibrator`: orchestrates one calibration computation.

## Public Calibration API

```python
result = calibrator.process_frame(frame, frame_num)
```

`process_frame()` processes vision once, computes geometry and steering once,
and returns a frozen `CalibrationResult`:

- `steering_angle: float`
- `control_state: str`
- `observation_angle: float | None`
- `calibration_active: bool`
- `telemetry: dict[str, Any]`
- `debug_data: dict[str, Any]`

The `vision_debug` entry inside `debug_data` contains unified vision
intermediates consumed by existing HUD helpers.

Diagnostic geometry includes raw unbounded `vp_x`/`vp_y`, `vp_location`, and
explicit `selected_left_line_info` / `selected_right_line_info`. Terminal logs,
runtime CSV, stream telemetry, and evaluation JSONL expose the same identifiers
so the selected pair can be traced without relying on color alone.

## Failure Diagnostics

Unexpected processing failures raise `CalibrationProcessingError`. Its
`diagnostic` value provides:

- `frame_num`
- `stage`
- `process`
- `error_type`
- `detail`

The original exception is preserved as `__cause__` for traceback debugging.
Current computation stages identify input validation, vision processing,
lane-pair selection, vision-debug rendering, geometry operations, steering
control, control-state updates, and result assembly.

| Stage | Processes |
| --- | --- |
| `input` | `validate_frame` |
| `vision` | `preprocess_and_extract_lines` |
| `lane_pair_selection` | `select_lane_pair` |
| `vision_debug` | `draw_selected_lines` |
| `geometry` | `calculate_bottom_intercepts`, `calculate_vanishing_point`, `map_vanishing_point_to_angle` |
| `steering_control` | `compute_steering_command` |
| `control_state` | `update_robot_state` |
| `result_assembly` | `assemble_calibration_result` |
| `runtime_output` | `render_visuals`, `write_telemetry`, `write_debug_video`, `publish_stream`, `log_terminal_status` |

Example:

```text
frame=22 stage=vision process=preprocess_and_extract_lines
error=RuntimeError: synthetic extraction failure
```

## Offline Compatibility API

```python
angle = calibrator.update(frame, frame_num)
```

`update()` calls `process_frame()` and then applies visualization, CSV, video,
stream, terminal-log, and timing side effects. It returns the steering angle
for compatibility with `process_video.py`. Output-side failures identify the
specific `runtime_output` process such as `render_visuals`, `write_telemetry`,
`write_debug_video`, `publish_stream`, or `log_terminal_status`.

## Current Pair Selection

The current filter rejects vertical and near-horizontal candidates, then
selects the longest negative-slope and positive-slope segments. The approved
next refinement will select the most opposite valid slopes and add explicit
geometry rejection reasons.

## Immediate Danger Override

Danger overrides use projected bottom intercepts and bypass tracking
hysteresis:

- `left_intercept > DANGER_MARGIN_PX` produces `DANGER_RIGHT` and a
  positive/right recovery.
- `right_intercept < frame_width - DANGER_MARGIN_PX` produces `DANGER_LEFT`
  and a negative/left recovery.

Telemetry and overlays expose `danger_boundary`, `recovery_direction`, and
`danger_threshold_x`. The danger state names the recovery direction, while
`danger_boundary` identifies the selected boundary that caused it. If both
selected boundaries cross their thresholds, the result is `AMBIGUOUS_DANGER`
and center is commanded instead of guessing from frame-center distance. No
confirmation-frame or release delay is applied.

## Remaining Work

Later approved gates will split computation stages into focused modules,
replace broad dictionaries with stronger contracts, add plugins/capabilities,
refine validation and PID control, and separate telemetry sinks. See
[architecture_governance.md](architecture_governance.md).
