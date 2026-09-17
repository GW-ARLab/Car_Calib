"""Embedded HTTP server for the Pi5 dashboard.

Serves:
  /                     → runtime/dashboard/index.html
  /dashboard/static/*   → runtime/dashboard/ (CSS, JS)
  /stream               → MJPEG live stream
  /api/status           → JSON telemetry
  /api/base/<cmd>       → base motor command
  /api/relay/<state>    → relay ON/OFF
  /api/power/<state>    → power ON/OFF
  /api/camera/list      → GET  scan available /dev/videoN indices
  /api/camera/select    → POST {"index": N} switch active camera
  /routes/<id>/distance → POST {"distance_m": N} save manually-entered distance
  /routes/export.xlsx   → GET  ?ids=a,b,c (or all routes) as one .xlsx, 1 sheet per route
  /routes/avg_speed     → GET  {"avg_speed_mps": float|null, "n_samples": int}
  /routes/download_all  → GET  ?ids=a,b,c (or all routes) as one .zip bundling each route's .zip
"""

from __future__ import annotations

import json
import hmac
import logging
import mimetypes
import os
import socket
import tempfile
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import numpy as np

from runtime.calib_tuning import TuneBlockedError, TuneValidationError
from runtime.manual_override import ManualOverrideError

logger = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"


class RequestBodyError(ValueError):
    """An HTTP request body was malformed or exceeded the configured limit."""


class _RequestHandler(BaseHTTPRequestHandler):
    """Minimal request handler with CORS support."""

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("HTTP %s", fmt % args)

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(float(getattr(self.server, "request_timeout_s", 5.0)))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _text(self, code: int, body: str) -> None:
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, data: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(data, default=str)
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def _bytes(self, code: int, data: bytes, content_type: str, filename: str) -> None:
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_download(self, path: Path, filename: str, *, content_type: str = "application/zip", cleanup: bool = False) -> None:
        """Stream a file from disk as an attachment, in chunks (not loaded into memory)."""
        if not path.is_file():
            self._text(404, "not found")
            return
        try:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            with path.open("rb") as fileobj:
                while chunk := fileobj.read(64 * 1024):
                    self.wfile.write(chunk)
        finally:
            if cleanup:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _authorized(self) -> bool:
        token = str(getattr(self.server, "dashboard_token", ""))
        if not token:
            return True
        candidate = (self._query().get("token") or [""])[0]
        return hmac.compare_digest(candidate, token)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._json({"detail": "unauthorized"}, 401)
        return False

    def _request_path(self) -> str:
        return urlparse(self.path).path

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RequestBodyError("invalid Content-Length") from exc
        if length <= 0:
            return {}
        max_bytes = int(getattr(self.server, "max_body_bytes", 64 * 1024))
        if length > max_bytes:
            raise RequestBodyError(f"request body exceeds {max_bytes} bytes")
        try:
            raw = self.rfile.read(length).decode("utf-8")
            value = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestBodyError("request body must be valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise RequestBodyError("JSON body must be an object")
        return value

    def _script_status(self) -> dict[str, Any]:
        runner = getattr(self.server, "script_runner", None)
        status = runner() if callable(runner) else {"running": False, "steps": [], "current": None}
        return {"status": status}

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._text(404, "not found")
            return
        content_type, _ = mimetypes.guess_type(str(path))
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        if path.name != "index.html":
            self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        if path.name == "index.html":
            token = json.dumps(str(getattr(self.server, "dashboard_token", "")))
            body = (
                path.read_text(encoding="utf-8")
                .replace("__STREAM_PATH__", "/stream")
                .replace('TOKEN: ""', f"TOKEN: {token}")
            )
            self.wfile.write(body.encode())
            return
        with path.open("rb") as fileobj:
            while chunk := fileobj.read(64 * 1024):
                self.wfile.write(chunk)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self._request_path()
        # Static assets contain no telemetry/control data and cannot carry a
        # token in their URL, so the dashboard shell remains the auth boundary.
        if path.startswith("/dashboard/static/"):
            rel = path[len("/dashboard/static/"):]
            safe = rel.lstrip("/").replace("\\", "/")
            if ".." in safe:
                self._text(403, "forbidden")
                return
            self._file(_DASHBOARD_DIR / safe)
            return
        if not self._require_auth():
            return
        # ---- Dashboard HTML ----
        if path == "/" or path == "/dashboard":
            self._file(_DASHBOARD_DIR / "index.html")
            return

        # ---- MJPEG stream ----
        if path == "/stream":
            broker = getattr(self.server, "stream_broker", None)
            if broker is None:
                self._text(503, "stream unavailable")
                return
            if not broker.acquire_client():
                self._text(429, "stream client limit reached")
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            sequence = -1
            try:
                while True:
                    jpeg, sequence = broker.wait_for_frame(sequence, timeout_s=5.0)
                    if jpeg is None:
                        continue
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            finally:
                broker.release_client()
            return

        # ---- API /status ----
        if path == "/api/status":
            getter = getattr(self.server, "status_getter", None)
            data = getter() if callable(getter) else {}
            self._json(data)
            return

        if path == "/api/tune":
            getter = getattr(self.server, "tune_getter", None)
            if not callable(getter):
                self._json({"detail": "tune unavailable"}, 404)
                return
            self._json(getter())
            return

        # ---- API /camera/list ----
        if path == "/api/camera/list":
            getter = getattr(self.server, "camera_list_getter", None)
            if not callable(getter):
                self._json({"detail": "camera list unavailable"}, 404)
                return
            self._json(getter())
            return

        # ---- API /base/<cmd> ----
        if path.startswith("/api/base/"):
            cmd = path.split("/api/base/")[-1]
            handler = getattr(self.server, "base_handler", None)
            if callable(handler):
                handler(cmd)
            self._text(200, f"base:{cmd}")
            return

        # ---- API /relay/<state> ----
        if path.startswith("/api/relay/"):
            state = path.split("/api/relay/")[-1].upper()
            handler = getattr(self.server, "relay_handler", None)
            if callable(handler):
                handler(state)
            self._text(200, f"relay:{state}")
            return

        # ---- API /power/<state> ----
        if path.startswith("/api/power/"):
            state = path.split("/api/power/")[-1].upper()
            handler = getattr(self.server, "power_handler", None)
            if callable(handler):
                handler(state)
            self._text(200, f"power:{state}")
            return

        # ---- Route script builder ----
        if path == "/route/script/status":
            self._json(self._script_status())
            return
        # ---- Presets ----
        if path == "/presets":
            pg = getattr(self.server, "presets_getter", None)
            self._json({"presets": pg() if callable(pg) else []})
            return
        if path.startswith("/presets/"):
            name = unquote(path.split("/presets/", 1)[1])
            pg = getattr(self.server, "presets_getter", None)
            presets = pg() if callable(pg) else []
            preset = next((p for p in presets if p.get("name") == name), None)
            self._json({"preset": preset} if preset else {"detail": "preset not found"}, 200 if preset else 404)
            return
        # ---- Routes list ----
        if path.startswith("/routes/list"):
            cached = getattr(self.server, "route_list_cache", None)
            now = time.monotonic()
            if cached is None or now - cached[0] >= self.server.route_list_cache_s:
                rg = getattr(self.server, "routes_getter", None)
                cached = (now, rg() if callable(rg) else [])
                self.server.route_list_cache = cached
            self._json({"routes": cached[1][:50]})
            return
        if path.startswith("/routes/") and path.endswith("/summary"):
            route_id = unquote(path[len("/routes/"):-len("/summary")])
            root = Path(os.getenv("ROUTE_LOG_ROOT", "/data/routes")).resolve()
            summary_path = (root / route_id / "route_summary.json").resolve()
            if root not in summary_path.parents or not summary_path.is_file():
                self._json({"summary": {"route_id": route_id, "status": "not_recorded"}}, 404)
                return
            self._json({"summary": json.loads(summary_path.read_text(encoding="utf-8"))})
            return
        if path == "/routes/download_all":
            ids_param = (self._query().get("ids") or [""])[0]
            route_ids = [r for r in ids_param.split(",") if r] or None
            bundler = getattr(self.server, "routes_zip_bundler", None)
            if not callable(bundler):
                self._json({"detail": "route bundle unavailable"}, 404)
                return
            tmp_path = Path(tempfile.gettempdir()) / f"routes_bundle_{uuid4().hex}.zip"
            try:
                count = bundler(route_ids, tmp_path)
            except OSError as exc:
                tmp_path.unlink(missing_ok=True)
                self._json({"detail": str(exc)}, 500)
                return
            if count == 0:
                tmp_path.unlink(missing_ok=True)
                self._json({"detail": "no finished routes to bundle"}, 404)
                return
            self._stream_download(tmp_path, "routes_bundle.zip", cleanup=True)
            return
        if path == "/routes/avg_speed":
            getter = getattr(self.server, "avg_speed_getter", None)
            self._json(getter() if callable(getter) else {"avg_speed_mps": None, "n_samples": 0})
            return
        if path == "/routes/export.xlsx":
            ids_param = (self._query().get("ids") or [""])[0]
            route_ids = [r for r in ids_param.split(",") if r] or None
            exporter = getattr(self.server, "routes_xlsx_exporter", None)
            if not callable(exporter):
                self._json({"detail": "route export unavailable"}, 404)
                return
            try:
                data = exporter(route_ids)
            except FileNotFoundError as exc:
                self._json({"detail": f"route not found: {exc}"}, 404)
                return
            self._bytes(
                200,
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "routes_export.xlsx",
            )
            return
        if path.startswith("/routes/download/"):
            route_id = unquote(path[len("/routes/download/"):])
            root = Path(os.getenv("ROUTE_LOG_ROOT", "/data/routes")).resolve()
            zip_path = (root / f"{route_id}.zip").resolve()
            if root not in zip_path.parents:
                self._text(400, "bad route")
                return
            self._file(zip_path)
            return

        self._text(404, "not found")

    def do_POST(self) -> None:
        if not self._require_auth():
            return
        try:
            self._do_POST()
        except RequestBodyError as exc:
            self._json({"detail": str(exc)}, 413 if "exceeds" in str(exc) else 400)

    def _do_POST(self) -> None:
        path = self._request_path()
        if path == "/api/manual_override":
            handler = getattr(self.server, "manual_override_handler", None)
            if not callable(handler):
                self._json({"detail": "manual override unavailable"}, 404)
                return
            try:
                self._json(handler(self._json_body()))
            except (json.JSONDecodeError, ManualOverrideError) as exc:
                self._json({"detail": str(exc)}, 400)
            return
        if path == "/api/camera/select":
            selector = getattr(self.server, "camera_selector", None)
            if not callable(selector):
                self._json({"detail": "camera select unavailable"}, 404)
                return
            try:
                self._json(selector(json.dumps(self._json_body())))
            except ValueError as exc:
                self._json({"detail": str(exc)}, 400)
            return
        if path == "/route/script":
            submitter = getattr(self.server, "script_submitter", None)
            body = self._json_body()
            try:
                ok = submitter(json.dumps(body)) if callable(submitter) else False
            except ValueError as exc:
                self._json({"detail": str(exc)}, 400)
                return
            self._json({"ok": bool(ok)}, 200 if ok else 400)
            return
        if path == "/route/script/step":
            submitter = getattr(self.server, "script_submitter", None)
            step = self._json_body()
            try:
                ok = submitter(json.dumps({"steps": [step]})) if callable(submitter) else False
            except ValueError as exc:
                self._json({"detail": str(exc)}, 400)
                return
            self._json({"ok": bool(ok)}, 200 if ok else 400)
            return
        if path == "/route/script/stop":
            stopper = getattr(self.server, "script_stopper", None)
            if callable(stopper):
                stopper()
            self._json({"ok": True})
            return
        if path == "/route/relay":
            on = (self._query().get("on") or ["0"])[0] == "1"
            handler = getattr(self.server, "relay_handler", None)
            if callable(handler):
                handler("ON" if on else "OFF")
            self._json({"ok": True, "on": on})
            return
        if path == "/control/power":
            on = (self._query().get("on") or ["0"])[0] == "1"
            handler = getattr(self.server, "power_handler", None)
            if callable(handler):
                handler("ON" if on else "OFF")
            self._json({"ok": True, "on": on})
            return
        if path == "/control/estop_reset":
            self._json({"ok": True})
            return
        if path.startswith("/routes/") and path.endswith("/distance"):
            route_id = unquote(path[len("/routes/"):-len("/distance")])
            handler = getattr(self.server, "route_distance_handler", None)
            if not callable(handler):
                self._json({"detail": "route distance unavailable"}, 404)
                return
            body = self._json_body()
            try:
                result = handler(route_id, body.get("distance_m"))
            except FileNotFoundError:
                self._json({"detail": "route not found"}, 404)
                return
            except ValueError as exc:
                self._json({"detail": str(exc)}, 400)
                return
            self._json(result)
            return
        if path == "/routes/delete_all":
            deleter = getattr(self.server, "routes_delete_all_handler", None)
            if not callable(deleter):
                self._json({"removed": 0, "errors": ["route delete unavailable"]})
                return
            result = deleter()
            self.server.route_list_cache = None
            self._json(result)
            return
        if path == "/api/tune/save":
            saver = getattr(self.server, "tune_saver", None)
            if not callable(saver):
                self._json({"detail": "tune unavailable"}, 404)
                return
            try:
                self._json(saver())
            except TuneValidationError as exc:
                self._json({"detail": str(exc)}, 400)
            return
        if path == "/api/tune/reset":
            resetter = getattr(self.server, "tune_resetter", None)
            if not callable(resetter):
                self._json({"detail": "tune unavailable"}, 404)
                return
            try:
                self._json(resetter(str(self._json_body().get("target", ""))))
            except TuneBlockedError as exc:
                self._json({"detail": str(exc)}, 409)
            except TuneValidationError as exc:
                self._json({"detail": str(exc)}, 400)
            return
        self._text(404, "not found")

    def do_PUT(self) -> None:
        if not self._require_auth():
            return
        try:
            self._do_PUT()
        except RequestBodyError as exc:
            self._json({"detail": str(exc)}, 413 if "exceeds" in str(exc) else 400)

    def _do_PUT(self) -> None:
        path = self._request_path()
        if path == "/api/tune":
            applier = getattr(self.server, "tune_applier", None)
            if not callable(applier):
                self._json({"detail": "tune unavailable"}, 404)
                return
            try:
                self._json(applier(self._json_body().get("values", {})))
            except TuneBlockedError as exc:
                self._json({"detail": str(exc)}, 409)
            except TuneValidationError as exc:
                self._json({"detail": str(exc)}, 400)
            return
        if path.startswith("/presets/"):
            name = unquote(path.split("/presets/", 1)[1])
            setter = getattr(self.server, "presets_setter", None)
            body = self._json_body()
            body["name"] = name
            try:
                if callable(setter):
                    setter(json.dumps(body))
            except ValueError as exc:
                self._json({"detail": str(exc)}, 400)
                return
            self._json({"ok": True, "preset": body})
            return
        self._text(404, "not found")

    def do_DELETE(self) -> None:
        if not self._require_auth():
            return
        path = self._request_path()
        if path.startswith("/presets/"):
            name = unquote(path.split("/presets/", 1)[1])
            deleter = getattr(self.server, "preset_deleter", None)
            if callable(deleter):
                deleter(name)
            self._json({"ok": True})
            return
        if path.startswith("/routes/"):
            route_id = unquote(path[len("/routes/"):])
            deleter = getattr(self.server, "route_deleter", None)
            if not callable(deleter):
                self._json({"detail": "route delete unavailable"}, 404)
                return
            try:
                deleter(route_id)
            except FileNotFoundError:
                self._json({"detail": "route not found"}, 404)
                return
            self.server.route_list_cache = None
            self._json({"ok": True})
            return
        self._text(404, "not found")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server with a bounded number of live request threads."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        max_threads: int,
        request_timeout_s: float,
        dashboard_token: str,
        max_body_bytes: int,
        route_list_cache_s: float,
    ) -> None:
        self._thread_slots = threading.BoundedSemaphore(max(1, max_threads))
        self.request_timeout_s = max(0.5, request_timeout_s)
        self.dashboard_token = dashboard_token
        self.max_body_bytes = max(1, max_body_bytes)
        self.route_list_cache_s = max(0.0, route_list_cache_s)
        self.route_list_cache: tuple[float, list[dict[str, Any]]] | None = None
        super().__init__(server_address, handler_class)

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self._thread_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._thread_slots.release()


class Pi5HttpServer:
    """Embedded HTTP server for the Pi5 dashboard."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        *,
        token: str | None = None,
        max_body_bytes: int | None = None,
        request_timeout_s: float | None = None,
        max_threads: int | None = None,
        route_list_cache_s: float | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._token = (os.getenv("DASHBOARD_TOKEN", "") if token is None else token).strip()
        self._max_body_bytes = int(os.getenv("DASHBOARD_MAX_BODY_BYTES", "65536")) if max_body_bytes is None else max_body_bytes
        self._request_timeout_s = float(os.getenv("DASHBOARD_REQUEST_TIMEOUT_S", "5")) if request_timeout_s is None else request_timeout_s
        self._max_threads = int(os.getenv("DASHBOARD_MAX_THREADS", "16")) if max_threads is None else max_threads
        self._route_list_cache_s = float(os.getenv("DASHBOARD_ROUTE_LIST_CACHE_S", "5")) if route_list_cache_s is None else route_list_cache_s
        self._server: ThreadedHTTPServer | None = None
        self._frame_getter: Callable[[], np.ndarray | None] | None = None
        self._stream_broker: Any | None = None
        self._status_getter: Callable[[], dict[str, Any]] | None = None
        self._base_handler: Callable[[str], None] | None = None
        self._relay_handler: Callable[[str], None] | None = None
        self._power_handler: Callable[[str], None] | None = None
        self._script_runner: Callable[[], dict[str, Any]] | None = None
        self._script_stopper: Callable[[], None] | None = None
        self._script_submitter: Callable[[str], None] | None = None
        self._steps_getter: Callable[[], list[dict[str, Any]]] | None = None
        self._steps_setter: Callable[[str], None] | None = None
        self._presets_getter: Callable[[], list[dict[str, Any]]] | None = None
        self._presets_setter: Callable[[str], None] | None = None
        self._preset_deleter: Callable[[str], None] | None = None
        self._routes_getter: Callable[[], list[dict[str, Any]]] | None = None
        self._route_distance_handler: Callable[[str, Any], dict[str, Any]] | None = None
        self._route_deleter: Callable[[str], None] | None = None
        self._routes_delete_all_handler: Callable[[], dict[str, Any]] | None = None
        self._routes_xlsx_exporter: Callable[[list[str] | None], bytes] | None = None
        self._avg_speed_getter: Callable[[], dict[str, Any]] | None = None
        self._routes_zip_bundler: Callable[[list[str] | None, Path], int] | None = None
        self._tune_getter: Callable[[], dict[str, Any]] | None = None
        self._tune_applier: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._tune_saver: Callable[[], dict[str, Any]] | None = None
        self._tune_resetter: Callable[[str], dict[str, Any]] | None = None
        self._manual_override_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._camera_list_getter: Callable[[], dict[str, Any]] | None = None
        self._camera_selector: Callable[[str], dict[str, Any]] | None = None
        self._thread: threading.Thread | None = None

    def set_frame_getter(self, fn: Callable[[], np.ndarray | None]) -> None:
        self._frame_getter = fn

    def set_stream_broker(self, broker: Any) -> None:
        self._stream_broker = broker

    def set_status_getter(self, fn: Callable[[], dict[str, Any]]) -> None:
        self._status_getter = fn

    def set_base_handler(self, fn: Callable[[str], None]) -> None:
        self._base_handler = fn

    def set_relay_handler(self, fn: Callable[[str], None]) -> None:
        self._relay_handler = fn

    def set_power_handler(self, fn: Callable[[str], None]) -> None:
        self._power_handler = fn

    def set_manual_override_handler(self, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._manual_override_handler = fn

    def set_camera_handlers(
        self,
        *,
        list_getter: Callable[[], dict[str, Any]],
        selector: Callable[[str], dict[str, Any]],
    ) -> None:
        self._camera_list_getter = list_getter
        self._camera_selector = selector

    def set_script_runner(self, fn: Callable[[], dict[str, Any]]) -> None:
        self._script_runner = fn

    def set_script_stopper(self, fn: Callable[[], None]) -> None:
        self._script_stopper = fn

    def set_script_submitter(self, fn: Callable[[str], None]) -> None:
        self._script_submitter = fn

    def set_steps_getter(self, fn: Callable[[], list[dict[str, Any]]]) -> None:
        self._steps_getter = fn

    def set_steps_setter(self, fn: Callable[[str], None]) -> None:
        self._steps_setter = fn

    def set_presets_getter(self, fn: Callable[[], list[dict[str, Any]]]) -> None:
        self._presets_getter = fn

    def set_presets_setter(self, fn: Callable[[str], None]) -> None:
        self._presets_setter = fn

    def set_preset_deleter(self, fn: Callable[[str], None]) -> None:
        self._preset_deleter = fn

    def set_routes_getter(self, fn: Callable[[], list[dict[str, Any]]]) -> None:
        self._routes_getter = fn

    def set_route_distance_handler(self, fn: Callable[[str, Any], dict[str, Any]]) -> None:
        self._route_distance_handler = fn

    def set_route_deleter(self, fn: Callable[[str], None]) -> None:
        self._route_deleter = fn

    def set_routes_delete_all_handler(self, fn: Callable[[], dict[str, Any]]) -> None:
        self._routes_delete_all_handler = fn

    def set_routes_xlsx_exporter(self, fn: Callable[[list[str] | None], bytes]) -> None:
        self._routes_xlsx_exporter = fn

    def set_avg_speed_getter(self, fn: Callable[[], dict[str, Any]]) -> None:
        self._avg_speed_getter = fn

    def set_routes_zip_bundler(self, fn: Callable[[list[str] | None, Path], int]) -> None:
        self._routes_zip_bundler = fn

    def set_tune_handlers(
        self,
        *,
        getter: Callable[[], dict[str, Any]],
        applier: Callable[[dict[str, Any]], dict[str, Any]],
        saver: Callable[[], dict[str, Any]],
        resetter: Callable[[str], dict[str, Any]],
    ) -> None:
        self._tune_getter = getter
        self._tune_applier = applier
        self._tune_saver = saver
        self._tune_resetter = resetter

    def start(self) -> None:
        if self._host not in {"127.0.0.1", "localhost", "::1"} and not self._token:
            raise ValueError("DASHBOARD_TOKEN is required when DASHBOARD_HOST is not localhost")
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        logger.info("Dashboard: http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        logger.info("Dashboard: stopped")

    def _serve(self) -> None:
        self._server = ThreadedHTTPServer(
            (self._host, self._port),
            _RequestHandler,
            max_threads=self._max_threads,
            request_timeout_s=self._request_timeout_s,
            dashboard_token=self._token,
            max_body_bytes=self._max_body_bytes,
            route_list_cache_s=self._route_list_cache_s,
        )
        self._server.frame_getter = self._frame_getter
        self._server.stream_broker = self._stream_broker
        self._server.status_getter = self._status_getter
        self._server.base_handler = self._base_handler
        self._server.relay_handler = self._relay_handler
        self._server.power_handler = self._power_handler
        self._server.manual_override_handler = self._manual_override_handler
        self._server.camera_list_getter = self._camera_list_getter
        self._server.camera_selector = self._camera_selector
        self._server.script_runner = self._script_runner
        self._server.script_stopper = self._script_stopper
        self._server.script_submitter = self._script_submitter
        self._server.steps_getter = self._steps_getter
        self._server.steps_setter = self._steps_setter
        self._server.presets_getter = self._presets_getter
        self._server.presets_setter = self._presets_setter
        self._server.preset_deleter = self._preset_deleter
        self._server.routes_getter = self._routes_getter
        self._server.route_distance_handler = self._route_distance_handler
        self._server.route_deleter = self._route_deleter
        self._server.routes_delete_all_handler = self._routes_delete_all_handler
        self._server.routes_xlsx_exporter = self._routes_xlsx_exporter
        self._server.avg_speed_getter = self._avg_speed_getter
        self._server.routes_zip_bundler = self._routes_zip_bundler
        self._server.tune_getter = self._tune_getter
        self._server.tune_applier = self._tune_applier
        self._server.tune_saver = self._tune_saver
        self._server.tune_resetter = self._tune_resetter
        self._server.serve_forever()
