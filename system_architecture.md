# Current Core Architecture

## Shared Calibration Path

```text
live camera or offline video frame
  -> UnifiedCalibrator.process_frame(frame, frame_num)
  -> VisionProcessor preprocessing and Hough extraction
  -> horizon-voted lane pair selection
  -> GeometryCalculator
  -> SteeringController
  -> CalibrationResult
```

Each frame passes through vision extraction and pair selection once.

`CalibrationResult` centralizes the steering angle, control state, observation
angle, calibration-active flag, telemetry snapshot, and visualization debug
data. Runtime entrypoints consume this result instead of recalculating
calibration values.

Unexpected calibration and output-side failures are raised as
`CalibrationProcessingError`. The diagnostic identifies the frame, stage,
process, exception type, and detail while preserving the original traceback.
The live runtime logs this context before safe shutdown; offline processing
logs it before re-raising.

## Runtime Ownership

`main.py` owns live lifecycle concerns: camera recovery, control subscriptions,
route sessions, actuators, streaming, CSV/video output, and safe shutdown. It
calls `process_frame()` and does not implement calibration mathematics.

`process_video.py` owns offline video input and output lifecycle. It calls
`UnifiedCalibrator.update()`, a compatibility wrapper around `process_frame()`
that applies the current telemetry and visualization side effects.

## Current Calibration Behavior

`VisionProcessor` crops the top `ROI_HEIGHT_PCT` of the frame, converts it to
grayscale, blurs it, runs Canny, and extracts probabilistic Hough segments.
The geometric filter keeps longitudinal lines, votes each at the
lane-convergence row (`VISION_VP_HORIZON_Y`), keeps the dominant family, and
selects the nearest lane group on each side of the image centre. See
`docs/vision.md`.

`GeometryCalculator` projects selected lines to the frame bottom, calculates
their infinite-line intersection, and maps vanishing-point x to a linear
`0..180` angle proxy.

`SteeringController` applies missing-geometry centering, danger overrides,
hysteresis, and frame-to-frame PD correction. It does not yet use elapsed time
or the configured integral gain.

## Remaining Responsibility Problems

- Calibration components remain together in `unified_calibration_components.py`.
- `UnifiedCalibrator.update()` still combines result consumption with
  telemetry and visualization side effects.
- `TelemetryLogger` owns CSV, overlays, video, streaming, artifacts, and sleep.
- Components still exchange broad telemetry/debug dictionaries.

The governing rules and target boundaries are in
[docs/architecture_governance.md](docs/architecture_governance.md).
