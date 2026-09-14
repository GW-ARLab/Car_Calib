from __future__ import annotations

import os
import time

import numpy as np
import pytest

from runtime.dashboard_stream import DashboardStreamBroker
from runtime.pi5_http import Pi5HttpServer
from runtime.resource_limits import ScriptValidationError, StorageManager, validate_route_steps
import runtime.resource_limits as resource_limits


def test_route_steps_reject_unbounded_or_non_finite_input() -> None:
    assert validate_route_steps([{"action": "forward", "duration_s": 1}]) == [
        {"action": "forward", "duration_s": 1.0}
    ]
    for steps in (
        [{"action": "unknown", "duration_s": 1}],
        [{"action": "forward", "duration_s": float("nan")}],
        [{"action": "forward", "duration_s": 31}],
        [{"action": "forward", "duration_s": 1}] * 65,
    ):
        with pytest.raises(ScriptValidationError):
            validate_route_steps(steps)


def test_storage_retention_keeps_active_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(resource_limits, "DATA_RETENTION_DAYS", 1)
    monkeypatch.setattr(resource_limits, "DATA_MAX_BYTES", 1)
    logs = tmp_path / "logs"
    routes = tmp_path / "routes"
    logs.mkdir()
    routes.mkdir()
    old_log = logs / "old.csv"
    old_log.write_bytes(b"old")
    active_route = routes / "route-active"
    active_route.mkdir()
    (active_route / "route_frames.csv").write_bytes(b"active")
    old_time = time.time() - 3 * 86400
    os.utime(old_log, (old_time, old_time))

    manager = StorageManager(logs, routes)
    manager.set_active_paths((active_route,))
    manager.retain()

    assert not old_log.exists()
    assert active_route.exists()


def test_storage_retention_never_deletes_presets_or_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(resource_limits, "DATA_RETENTION_DAYS", 1)
    monkeypatch.setattr(resource_limits, "DATA_MAX_BYTES", 1)
    root = tmp_path / "shared-data"
    root.mkdir()
    presets = root / "presets.json"
    presets.write_text('{"square": []}', encoding="utf-8")
    tune = root / "calib_tune.json"
    tune.write_text("{}", encoding="utf-8")
    unrelated = root / "notes.zip"
    unrelated.write_bytes(b"keep")
    old_route = root / "route-old"
    old_route.mkdir()
    (old_route / "route_frames.csv").write_bytes(b"discard")
    old_time = time.time() - 3 * 86400
    for path in (presets, tune, unrelated, old_route):
        os.utime(path, (old_time, old_time))

    # Exercise the dangerous deployment shape: both mounts resolve to one root.
    StorageManager(root, root).retain()

    assert presets.exists()
    assert tune.exists()
    assert unrelated.exists()
    assert not old_route.exists()


def test_stream_broker_shares_one_bounded_stream() -> None:
    broker = DashboardStreamBroker(max_clients=1, fps=30, jpeg_quality=60)
    try:
        assert broker.acquire_client() is True
        assert broker.acquire_client() is False
        broker.submit(np.zeros((16, 16, 3), dtype=np.uint8))
        jpeg, sequence = broker.wait_for_frame(-1, timeout_s=1.0)
        assert sequence >= 0
        assert jpeg is not None and jpeg.startswith(b"\xff\xd8")
        assert broker.health()["stream_rejected_clients"] == 1
    finally:
        broker.release_client()
        broker.close()


def test_lan_dashboard_requires_token() -> None:
    server = Pi5HttpServer(host="0.0.0.0", port=0, token="")
    with pytest.raises(ValueError, match="DASHBOARD_TOKEN"):
        server.start()
