"""stdlib HTTP server for the console — no framework, localhost or Cloud Run.

The asyncio loop owns the Store (firestore.AsyncClient is loop-bound); HTTP
handler threads bridge into it with run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .api import Conflict, ConsoleAPI, NotFound

CALL_TIMEOUT_SECONDS = 30.0


def _make_handler(api: ConsoleAPI, loop: asyncio.AbstractEventLoop, html: bytes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass

        def _call(self, coro):
            return asyncio.run_coroutine_threadsafe(coro, loop).result(CALL_TIMEOUT_SECONDS)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _api(self, coro) -> None:
            try:
                self._json(self._call(coro))
            except NotFound as exc:
                self._json({"error": str(exc)}, 404)
            except Conflict as exc:
                self._json({"error": str(exc)}, 409)
            except (ValueError, TypeError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            data = json.loads(self.rfile.read(length))
            return data if isinstance(data, dict) else {}

        def do_GET(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if not parts:
                self._send(200, html, "text/html; charset=utf-8")
            elif parts == ["api", "sessions"]:
                self._api(api.sessions())
            elif len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "poll":
                after = int((parse_qs(url.query).get("after") or ["0"])[0])
                self._api(api.poll(parts[2], after))
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            self._route_post(parts)

        def _route_post(self, parts: list[str]) -> None:
            if len(parts) < 4 or parts[:2] != ["api", "sessions"]:
                self._json({"error": "not found"}, 404)
                return
            sid = parts[2]
            try:
                body = self._body()
            except json.JSONDecodeError:
                self._json({"error": "invalid JSON body"}, 400)
                return
            if parts[3:] == ["prompt"]:
                self._api(api.prompt(sid, str(body.get("text") or "")))
            elif parts[3:] == ["interrupt"]:
                self._api(api.interrupt(sid))
            elif parts[3:] == ["kill"]:
                self._api(api.kill(sid))
            elif len(parts) == 5 and parts[3] == "approvals":
                self._api(
                    api.decide(
                        sid, parts[4], allow=bool(body.get("allow")), message=body.get("message")
                    )
                )
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def create_server(
    api: ConsoleAPI, loop: asyncio.AbstractEventLoop, host: str, port: int
) -> ThreadingHTTPServer:
    html = resources.files("syros.console").joinpath("index.html").read_bytes()
    server = ThreadingHTTPServer((host, port), _make_handler(api, loop, html))
    server.daemon_threads = True
    return server


async def run(api: ConsoleAPI, host: str, port: int, *, open_browser: bool) -> None:
    server = create_server(api, asyncio.get_running_loop(), host, port)
    bound_port = server.server_address[1]
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{bound_port}"
    print(f"syros console: {url}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        await asyncio.Event().wait()
    finally:
        server.shutdown()
