#!/usr/bin/env python3
"""Analyze average speed / travel time from a routes_export.xlsx file.

Reads the workbook produced by the dashboard's "export csv (xlsx)" button
(one sheet per route, a field/value summary block in the first rows
including `distance_m` and `total_elapsed_seconds`) and prints:
  - a per-route table (distance, elapsed, speed)
  - overall totals and average speed
  - average speed grouped by rounded script duration

Usage:
    python scripts/analyze_routes_speed.py routes_export.xlsx
"""

from __future__ import annotations

import argparse
import statistics as stats
import sys
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook

_SUMMARY_KEYS = {
    "route_id", "route_mode", "status", "accepted", "distance_m",
    "total_elapsed_seconds", "total_frames", "start_timestamp_utc",
    "end_timestamp_utc",
}


def read_route_summaries(xlsx_path: Path) -> list[dict]:
    wb = load_workbook(xlsx_path, data_only=True, read_only=True)
    routes = []
    for name in wb.sheetnames:
        ws = wb[name]
        info: dict = {}
        for row in ws.iter_rows(min_row=1, max_row=12, values_only=True):
            key, value = row[0], row[1]
            if key in _SUMMARY_KEYS:
                info[key] = value
        if info.get("distance_m") is not None and info.get("total_elapsed_seconds"):
            info["speed_mps"] = info["distance_m"] / info["total_elapsed_seconds"]
        else:
            info["speed_mps"] = None
        routes.append(info)
    routes.sort(key=lambda r: r.get("start_timestamp_utc") or "")
    return routes


def fit_speed_model_report(routes: list[dict]) -> str:
    """Linear fit distance = speed*elapsed + intercept (see route_logging.py
    compute_average_speed_mps for why the intercept matters: a flat
    distance/elapsed ratio undershoots by a roughly-constant startup-lag
    distance regardless of step length)."""
    points = [
        (r["total_elapsed_seconds"], r["distance_m"])
        for r in routes
        if r.get("status") == "COMPLETED" and r.get("distance_m") and r.get("total_elapsed_seconds")
    ]
    n = len(points)
    if n < 3:
        return "\n(not enough samples for a speed-model fit)"
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    variance_x = sum((x - mean_x) ** 2 for x, _ in points)
    if variance_x <= 1e-9:
        return "\n(all steps have the same duration -- can't fit a line)"
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / variance_x
    intercept = mean_y - slope * mean_x
    startup_lag_s = -intercept / slope if slope > 0 else float("nan")
    naive_speed = mean_y / mean_x
    return (
        f"\nSpeed model fit (distance = speed*elapsed + intercept, n={n}):\n"
        f"  speed (slope)   : {slope:.4f} m/s\n"
        f"  intercept       : {intercept:+.4f} m\n"
        f"  implied startup lag : {startup_lag_s:.3f} s\n"
        f"  naive mean(dist/elapsed) speed for comparison: {naive_speed:.4f} m/s\n"
        f"  -> to convert a target distance to a step duration, use:\n"
        f"     duration_s = (distance_m - {intercept:+.4f}) / {slope:.4f}"
    )


def print_report(routes: list[dict]) -> None:
    print(f"{'route_id':38} {'status':10} {'dist_m':>8} {'elapsed_s':>10} {'m/s':>8} {'km/h':>8}")
    total_dist = 0.0
    total_time = 0.0
    speeds: list[float] = []
    for r in routes:
        dist = r.get("distance_m") or 0.0
        elapsed = r.get("total_elapsed_seconds") or 0.0
        speed = r["speed_mps"]
        total_dist += dist
        total_time += elapsed
        if speed is not None and dist > 0:
            speeds.append(speed)
        speed_s = f"{speed:.3f}" if speed is not None else "-"
        speed_kmh = f"{speed * 3.6:.2f}" if speed is not None else "-"
        print(f"{r.get('route_id', ''):38} {str(r.get('status')):10} {dist:8.2f} {elapsed:10.2f} {speed_s:>8} {speed_kmh:>8}")

    print()
    print(f"N routes: {len(routes)}")
    print(f"N routes with distance>0: {len(speeds)}")
    print(f"Total distance (m): {total_dist:.3f}")
    print(f"Total elapsed (s): {total_time:.3f}")
    if speeds:
        print(f"Avg speed (mean of per-route speed): {stats.mean(speeds):.4f} m/s -> {stats.mean(speeds) * 3.6:.3f} km/h")
        print(f"Median speed: {stats.median(speeds):.4f} m/s")
        print(f"Min/Max speed: {min(speeds):.4f} / {max(speeds):.4f} m/s")
    if total_time > 0:
        overall = total_dist / total_time
        print(f"Overall avg speed (total_dist/total_time): {overall:.4f} m/s -> {overall * 3.6:.3f} km/h")

    print(fit_speed_model_report(routes))
    print("\nBy script duration (rounded elapsed seconds):")
    groups: dict[int, list[float]] = defaultdict(list)
    for r in routes:
        if r.get("distance_m") and r.get("total_elapsed_seconds"):
            groups[round(r["total_elapsed_seconds"])].append(r["speed_mps"])
    for bucket in sorted(groups):
        vals = groups[bucket]
        mean_v = stats.mean(vals)
        stdev_v = stats.pstdev(vals) if len(vals) > 1 else 0.0
        print(f"  ~{bucket}s (n={len(vals)}): avg={mean_v:.3f} m/s ({mean_v * 3.6:.2f} km/h), stdev={stdev_v:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx_path", type=Path, help="path to routes_export.xlsx")
    args = parser.parse_args()

    if not args.xlsx_path.is_file():
        print(f"file not found: {args.xlsx_path}", file=sys.stderr)
        sys.exit(1)

    routes = read_route_summaries(args.xlsx_path)
    if not routes:
        print("no sheets/routes found in workbook", file=sys.stderr)
        sys.exit(1)

    print_report(routes)


if __name__ == "__main__":
    main()
