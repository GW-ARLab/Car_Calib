# Vision Processing

Vision calibration is exposed through `UnifiedCalibrator`.

`VisionProcessor.process_frame_debug(frame)` performs:

1. Empty-frame handling.
2. Top ROI crop using `ROI_HEIGHT_PCT`.
3. Grayscale conversion.
4. Gaussian blur.
5. Canny edge detection.
6. Probabilistic Hough line extraction.
7. Generation of visualization intermediates.

`VisionProcessor._apply_geometric_filter(lines, frame_width, frame_height)`
selects the lane pair:

1. Keep longitudinal segments: vertical, or `|slope| >= VISION_MIN_ABS_SLOPE`.
2. Each segment votes where it crosses the horizon row `VISION_VP_HORIZON_Y`
   (tune key `vp_horizon_y`, default -518 for the Pi5 camera at 640x480). The
   cluster of votes (+-30 px) with the most segment length is the lane family;
   transverse tile grout lines vote elsewhere and drop out.
3. Family segments are merged by bottom intercept (+-25 px); groups shorter
   than 60 px in total are ignored.
4. The nearest group left of the image centre and the nearest group right of
   it form the pair (longest segment of each). Slope sign is not used, so a
   yawed car whose lane lines both lean the same way still gets a pair.

It returns `None` when either side has no group. Re-measure the horizon row
(median `vp_y` of good frames in a replay) if the camera pitch changes.

Current debug data includes grayscale, ROI, blurred image, edges, Hough
visualization, selected-line visualization, raw line count, and selected lines.
The outer `vision_debug` key is consumed by existing overlay helpers.

Selected-pair diagnostics expose the line left of the image centre as
`selected_left_line` (`LEFT`) and the one right of it as `selected_right_line`
(`RIGHT`). Each line has endpoints, slope, length, and its own projected bottom
intercept.

The raw vanishing point remains unbounded. A point above, below, or horizontally
outside the frame keeps its original coordinates and receives a `vp_location`
classification rather than being clamped or hidden.
