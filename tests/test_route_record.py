from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import runtime.route_logging as route_logging
from runtime.pi5_http import Pi5HttpServer
from runtime.resource_limits import ScriptValidationError
from runtime.route_logging import RouteSession, compute_average_speed_mps


@pytest.fixture
def route_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(route_logging, "ROUTE_LOG_ROOT", str(tmp_path))
    return tmp_path


def _write_route(root: Path, name: str, elapsed: float, distance: float, **extra_meta) -> None:
    route_dir = root / name
    route_dir.mkdir()
    summary = {
        "status": "COMPLETED",
        "total_elapsed_seconds": elapsed,
        "distance_m": distance,
        "extra_meta": extra_meta,
    }
    (route_dir / "route_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def test_append_meta_collects_entries_in_summary(route_root: Path) -> None:
    session = RouteSession(route_mode="RECORD")
    session.append_meta("script_runs", {"start_frame": 0})
    session.append_meta("script_runs", {"start_frame": 42})

    session.finalize(mono_now=time.monotonic(), status="COMPLETED")

    summary = json.loads((session.route_dir / "route_summary.json").read_text(encoding="utf-8"))
    assert summary["route_mode"] == "RECORD"
    assert summary["extra_meta"]["script_runs"] == [{"start_frame": 0}, {"start_frame": 42}]


def test_average_speed_ignores_record_hold_routes(route_root: Path) -> None:
    # distance = 0.7 * elapsed - 0.1 on the script routes
    _write_route(route_root, "route-a", 5.0, 3.4)
    _write_route(route_root, "route-b", 10.0, 6.9)
    _write_route(route_root, "route-c", 20.0, 13.9)
    # Idle time inside a record hold would drag the fitted speed down.
    _write_route(route_root, "route-d", 60.0, 2.0, record_hold=True)

    result = compute_average_speed_mps()

    assert result["n_samples"] == 3
    assert result["avg_speed_mps"] == pytest.approx(0.7)
    assert result["intercept_m"] == pytest.approx(-0.1)


def _post(port: int, path: str) -> tuple[int, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        return json.loads(response.read())


@pytest.fixture
def http_server():
    server = Pi5HttpServer(host="127.0.0.1", port=0, token="")
    yield server
    server.stop()
    if server._server is not None:
        server._server.server_close()


def _start(server: Pi5HttpServer) -> int:
    server.start()
    deadline = time.monotonic() + 2.0
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server._server is not None
    return server._server.server_address[1]


def test_record_endpoints_toggle_hold_and_report_status(http_server: Pi5HttpServer) -> None:
    state = {"active": False, "route_id": None}

    def start() -> dict:
        state.update(active=True, route_id="route-x")
        return dict(state)

    def stop() -> dict:
        state["active"] = False
        return dict(state)

    http_server.set_script_runner(lambda: {"running": False})
    http_server.set_record_handlers(starter=start, stopper=stop, status_getter=lambda: dict(state))
    port = _start(http_server)

    assert _get(port, "/route/script/status")["record"] == {"active": False, "route_id": None}
    assert _post(port, "/route/record/start") == (200, {"record": {"active": True, "route_id": "route-x"}})
    assert _get(port, "/route/script/status")["record"]["active"] is True
    assert _post(port, "/route/record/stop") == (200, {"record": {"active": False, "route_id": "route-x"}})


def test_record_start_failure_returns_400(http_server: Pi5HttpServer) -> None:
    def start() -> dict:
        raise ScriptValidationError("recording unavailable: disk full")

    http_server.set_record_handlers(starter=start, stopper=dict, status_getter=dict)
    port = _start(http_server)

    assert _post(port, "/route/record/start") == (400, {"detail": "recording unavailable: disk full"})


def test_record_endpoints_absent_without_handlers(http_server: Pi5HttpServer) -> None:
    port = _start(http_server)

    status, body = _post(port, "/route/record/start")
    assert status == 404
    assert _get(port, "/route/script/status")["record"] == {"active": False, "route_id": None}
