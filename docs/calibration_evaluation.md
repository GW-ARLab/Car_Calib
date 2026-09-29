# Calibration Evaluation Workflow

Use `evaluate_calibration.py` before changing calibration behavior. It runs the
shared `UnifiedCalibrator.process_frame()` path without hardware, MQTT,
streaming, or runtime telemetry side effects.

## Video Evaluation

```bash
python evaluate_calibration.py \
  --input videos/route.mp4 \
  --output-dir evaluations/baseline \
  --review-every 30
```

Useful sampling options:

```text
--start-frame N
--max-frames N
--stride N
--review-every N
--no-review-missing
--no-review-errors
--max-review-panels N
```

Each run produces:

```text
evaluations/baseline/
├── frames.jsonl
├── summary.json
└── review_frames/
    └── frame_000030_valid_observation.jpg
```

`frames.jsonl` contains one complete review record per evaluated frame:

- frame number and video timestamp;
- valid observation, missing observation, or processing error status;
- steering angle and control state;
- observation angle, vanishing point, and bottom intercepts;
- active danger boundary, threshold, and commanded recovery direction;
- raw line count and explicitly identified selected pair;
- structured failure stage/process/type/detail;
- optional review-panel path.

Selected-pair diagnostics identify:

- `selected_left_line_info`: lane line left of the image centre, labeled `LEFT`;
- `selected_right_line_info`: lane line right of the image centre, labeled
  `RIGHT`;
- endpoints, slope, length, and the line's own bottom intercept.

Vanishing-point coordinates are never clamped in records. `vp_location`
classifies the raw point as `inside`, `above`, `below`, `left`, `right`, or a
combined off-frame region such as `above_right`. Review overlays draw an arrow
to the relevant frame boundary while displaying the original coordinates.

`summary.json` contains aggregate outcome counts, control-state counts, and
failure-process counts for quick comparison between runs.

## Stateful Controller Warning

`UnifiedCalibrator` contains stateful steering and hysteresis behavior.

- Use `--stride 1 --start-frame 0` when comparing steering or controller
  behavior.
- A larger stride processes only sampled frames and therefore changes
  controller history.
- A later start frame does not warm up the controller with skipped frames.
- Sampling and late starts are appropriate for vision-only inspection when
  both baseline and changed runs use identical options.

## Notebook Use

The evaluator accepts either plain frames or `(frame_num, timestamp_s, frame)`
tuples:

The quickstart notebook discovers the experiment worktree root automatically,
so it works when VS Code starts the kernel from `notebooks/`. Set `VIDEO_PATH`
in its first code cell to an existing recorded video before running evaluation.

```python
from calibration_evaluation import CalibrationEvaluator, iter_video_frames

evaluator = CalibrationEvaluator(
    output_dir="evaluations/experiment_a",
    review_every=30,
)
records, summary = evaluator.evaluate_frames(
    iter_video_frames("videos/route.mp4", max_frames=300)
)
evaluator.close()

summary.as_dict()
records[:5]
```

For frames already loaded in memory:

```python
records, summary = evaluator.evaluate_frames(frames)
```

## Review Rule

Review panels are saved when:

- `frame_num` is divisible by `review_every`;
- an observation is missing and `review_missing=True`; or
- processing fails and `review_errors=True`.

The CLI saves at most 200 review panels by default to bound evaluation storage.
Use `--max-review-panels -1` to remove the limit.

The six-panel contact sheet contains the diagnostic overlay, grayscale, edges,
Hough candidates, explicitly labeled selected lines, and the structured
result/error context. The overlay shows the off-frame VP boundary indicator;
the result panel reports raw VP coordinates/location and both selected-line
descriptions.

## Calibration Refinement Gate

For each calibration behavior change:

1. Generate a baseline evaluation from representative videos.
2. Implement one behavior change.
3. Generate a new evaluation using the same frame range and stride.
4. Compare `summary.json`, `frames.jsonl`, and review panels.
5. Document findings and commit only after manual verification.
