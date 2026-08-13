"""stdlib HTTP server for the console — no framework, localhost or Cloud Run.

Serves the built frontend (console/, Next.js static export; `npm run build`
emits static/ into this package) and the JSON API. The asyncio loop owns the
Store (firestore.AsyncClient is loop-bound); HTTP handler threads bridge into
it with run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import threading
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlparse

from .api import Conflict, ConsoleAPI, NotFound

CALL_TIMEOUT_SECONDS = 30.0

# The API surface as data: (method, path pattern, handler). None segments are
# wildcards, captured in order and passed to the handler after (api, body, query).
ROUTES: list[tuple[str, tuple[str | None, ...], Callable[..., Any]]] = [
    ("GET", ("api", "sessions"), lambda api, body, query: api.sessions()),
    ("GET", ("api", "approvals"), lambda api, body, query: api.approvals()),
    (
        "GET",
        ("api", "sessions", None, "poll"),
        lambda api, body, query, sid: api.poll(sid, int((query.get("after") or ["0"])[0])),
    ),
    (
        "POST",
        ("api", "sessions", None, "prompt"),
        lambda api, body, query, sid: api.prompt(sid, str(body.get("text") or "")),
    ),
    (
        "POST",
        ("api", "sessions", None, "interrupt"),
        lambda api, body, query, sid: api.interrupt(sid),
    ),
    ("POST", ("api", "sessions", None, "kill"), lambda api, body, query, sid: api.kill(sid)),
    ("POST", ("api", "sessions", None, "delete"), lambda api, body, query, sid: api.delete(sid)),
    (
        "POST",
        ("api", "sessions", None, "approvals", None),
        lambda api, body, query, sid, call_hash: api.decide(
            sid, call_hash, allow=bool(body.get("allow")), message=body.get("message")
        ),
    ),
]


def _match(pattern: tuple[str | None, ...], parts: tuple[str, ...]) -> list[str] | None:
    if len(pattern) != len(parts):
        return None
    args = []
    for expected, part in zip(pattern, parts):
        if expected is None:
            args.append(part)
        elif expected != part:
            return None
    return args


def _load_static() -> dict[str, bytes]:
    """Preload the built frontend (console/ → next build → static/) into memory."""
    files: dict[str, bytes] = {}

    def walk(node, prefix: str = "") -> None:
        for child in node.iterdir():
            if child.is_dir():
                walk(child, f"{prefix}{child.name}/")
            else:
                files[f"{prefix}{child.name}"] = child.read_bytes()

    walk(resources.files("syros.console").joinpath("static"))
    return files


def _content_type(name: str) -> str:
    guessed = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if guessed.startswith("text/") or guessed in ("application/javascript", "text/javascript"):
        return f"{guessed}; charset=utf-8"
    return guessed


def _make_handler(api: ConsoleAPI, loop: asyncio.AbstractEventLoop, static: dict[str, bytes]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass

        def _call(self, coro):
            return asyncio.run_coroutine_threadsafe(coro, loop).result(CALL_TIMEOUT_SECONDS)

        def _send(
            self, status: int, body: bytes, content_type: str, cache: str | None = None
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cache:
                self.send_header("Cache-Control", cache)
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
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            parts = tuple(p for p in url.path.split("/") if p)
            query = parse_qs(url.query)
            for route_method, pattern, handler in ROUTES:
                if route_method != method:
                    continue
                args = _match(pattern, parts)
                if args is None:
                    continue
                if method == "POST":
                    try:
                        body = self._body()
                    except json.JSONDecodeError:
                        self._json({"error": "invalid JSON body"}, 400)
                        return
                else:
                    body = {}
                self._api(handler(api, body, query, *args))
                return
            if method == "GET" and parts[:1] != ("api",):
                self._static("/".join(parts) or "index.html")
            else:
                self._json({"error": "not found"}, 404)

        def _static(self, name: str) -> None:
            # next content-hashes everything under _next/, so those are immutable
            cache = "public, max-age=31536000, immutable" if "/" in name else "no-cache"
            body = static.get(name)
            if body is None and "/" not in name:
                # exported pages are flat html files: /sessions -> sessions.html
                name, cache = f"{name}.html", "no-cache"
                body = static.get(name)
            if body is not None:
                self._send(200, body, _content_type(name), cache)
            elif "404.html" in static:
                self._send(404, static["404.html"], _content_type("404.html"), "no-cache")
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def create_server(
    api: ConsoleAPI,
    loop: asyncio.AbstractEventLoop,
    host: str,
    port: int,
    static: dict[str, bytes] | None = None,
) -> ThreadingHTTPServer:
    if static is None:
        static = _load_static()
    server = ThreadingHTTPServer((host, port), _make_handler(api, loop, static))
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
