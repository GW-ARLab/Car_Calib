# Car Calibration

Vision-based steering calibration for an autonomous RC car. A MiniPC processes
camera frames, computes a steering command, and publishes it to the vehicle
control path. Raspberry Pi control, MQTT transport, servo communication, and
deployment are outside the current core-architecture refinement.

## Current Integrated Calibration Method

Both live and offline processing use `UnifiedCalibrator.process_frame()` as the
single calibration computation path:

1. Crop the configured top portion of the BGR camera frame.
2. Convert the ROI to grayscale, apply Gaussian blur, and run Canny.
3. Extract line segments with probabilistic Hough transform.
4. Keep longitudinal segments, let each vote at the lane-convergence row
   (`VISION_VP_HORIZON_Y`), keep the dominant family, and pick the nearest
   lane group left and right of the image centre (slope sign is not used;
   see [Vision](docs/vision.md)).
5. Project both lines to the bottom of the frame and calculate their
   intersection as the vanishing point.
6. Map the vanishing-point x-coordinate linearly into an angle centered at
   `90` degrees.
7. Apply danger-zone overrides, tracking hysteresis, and a PD steering
   correction.

For day-to-day operation (dashboard, recording, tuning, deploy), see the
[User Guide](docs/user_guide.md) (Vietnamese).

Current steering states are:

| State | Current behavior |
| --- | --- |
| `GAPPING` | Required vision geometry is missing; return center steering. |
| `DANGER_LEFT` | Selected right boundary moved inward; immediately apply a negative/left recovery. |
| `DANGER_RIGHT` | Selected left boundary moved inward; immediately apply a positive/right recovery. |
| `AMBIGUOUS_DANGER` | Both selected boundaries moved inward; reject the directional guess and command center. |
| `TRACKING_COAST` | Error is inside hysteresis or tracking is not active; return center. |
| `TRACKING_PD` | Apply the current proportional-derivative correction. |

`PID_KI` is exposed by configuration but is not used by the current PD
controller. True PID control remains a later approved refinement phase.

Danger overrides use one immediate threshold per side and do not use delayed
confirmation or release hysteresis. For a 640-pixel frame and
`DANGER_MARGIN_PX=100`, left-boundary danger is `left_intercept > 100` and
right-boundary danger is `right_intercept < 540`. Normal VP tracking retains
its existing inner/outer hysteresis. Only the already-selected lane pair is
used. If both thresholds are crossed in one frame, the geometry is ambiguous;
the controller does not choose a boundary using frame-center distance.

## Runtime Paths

- `main.py` owns live camera, actuator, route, stream, and shutdown lifecycle.
- `process_video.py` owns offline video input and artifact lifecycle.
- Both call `UnifiedCalibrator` as the only calibration facade.
- `UnifiedCalibrator.process_frame(frame, frame_num)` returns one typed
  `CalibrationResult`.
- Unexpected failures identify the exact frame, stage, process, exception type,
  and detail through `CalibrationProcessingError`.
- `UnifiedCalibrator.update(frame, frame_num)` is the offline compatibility
  wrapper that adds visualization, logging, video, and stream side effects.

## Core Repository Map

```text
main.py                              Live runtime orchestration
process_video.py                     Offline video entrypoint
evaluate_calibration.py              Recorded-video evaluation CLI
calibration_evaluation.py            Notebook-friendly evaluation API
unified_calibration_components.py    Single calibration facade and current components
control/steering_controller.py       Current danger/hysteresis/PD controller
models/robot_state.py                Current control state and PID constants
runtime/overlay_drawer.py            Calibration visualization
runtime/video_runtime_helpers.py     CSV, video, camera, CLI, and timing helpers
tests/                               Pytest and characterization coverage
notebooks/                           Calibration evaluation quickstart
```

The target architecture and delivery rules are documented in
[Core Architecture Governance](docs/architecture_governance.md).

## Running

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
python main.py
python process_video.py --input videos/example.mp4 --output processed_video.mp4
python evaluate_calibration.py --input videos/example.mp4 --output-dir evaluations/baseline
```

Runtime and calibration defaults are environment-backed through
`config/settings.py`; see `.env.example`.

Use [Calibration Evaluation Workflow](docs/calibration_evaluation.md) and the
notebook quickstart before refining preprocessing, pair selection, geometry,
or steering behavior.

## Current Limitations

- Calibration components still share one large module.
- Telemetry, visualization, streaming, and loop timing remain mixed in
  `TelemetryLogger`.
- Vision debug data still uses a broad dictionary contract.
- CSV output is fixed rather than a projection of fully dynamic telemetry.
- Geometry validation and true time-based PID are not implemented yet.

## Tests

```bash
python -m pytest tests/test_unified_calibration_characterization.py -q
python -m pytest tests -q
```

Each refinement gate requires implementation, tests, documentation, one
focused commit, and user verification before the next gate starts.
