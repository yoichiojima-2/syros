"""stdlib HTTP server for the console — no framework, localhost or Cloud Run.

Serves the built frontend (console/, Next.js static export; `make console`
emits static/ into this package, and the Docker build does the same) and the
JSON API. The asyncio loop owns the
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

from ..errors import SyrosError
from .api import Conflict, ConsoleAPI, NotFound, TooLarge

CALL_TIMEOUT_SECONDS = 30.0

# Uploads make the request body caller-controlled, so it needs a ceiling. Sized
# above the 10 MiB write cap in api.py to leave room for base64's ~33% overhead.
MAX_BODY_BYTES = 16 * 1024 * 1024


def _qteam(query: dict[str, list[str]]) -> str | None:
    return (query.get("team") or [None])[0]


def _bteam(body: dict) -> str | None:
    return str(body["team"]) if body.get("team") else None


# The API surface as data: (method, path pattern, handler). None segments are
# wildcards, captured in order and passed to the handler after (api, body, query).
ROUTES: list[tuple[str, tuple[str | None, ...], Callable[..., Any]]] = [
    ("GET", ("api", "sessions"), lambda api, body, query: api.sessions()),
    ("POST", ("api", "sessions"), lambda api, body, query: api.create_session(body)),
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
        ("api", "sessions", "delete"),
        lambda api, body, query: api.delete_many(body.get("ids")),
    ),
    (
        "POST",
        ("api", "sessions", None, "approvals", None),
        lambda api, body, query, sid, call_hash: api.decide(
            sid, call_hash, allow=bool(body.get("allow")), message=body.get("message")
        ),
    ),
    ("GET", ("api", "deployments"), lambda api, body, query: api.deployments()),
    ("POST", ("api", "deployments"), lambda api, body, query: api.create_deployment(body)),
    ("GET", ("api", "deployments", None), lambda api, body, query, name: api.deployment(name)),
    (
        "POST",
        ("api", "deployments", None, "enabled"),
        lambda api, body, query, name: api.set_deployment_enabled(name, bool(body.get("enabled"))),
    ),
    (
        "POST",
        ("api", "deployments", None, "run"),
        lambda api, body, query, name: api.run_deployment(name),
    ),
    (
        "POST",
        ("api", "deployments", None, "delete"),
        lambda api, body, query, name: api.delete_deployment(name),
    ),
    ("GET", ("api", "agents"), lambda api, body, query: api.agents()),
    ("POST", ("api", "agents"), lambda api, body, query: api.create_agent(body)),
    ("GET", ("api", "agents", None), lambda api, body, query, name: api.agent(name)),
    (
        "POST",
        ("api", "agents", None, "update"),
        lambda api, body, query, name: api.update_agent(name, body),
    ),
    (
        "POST",
        ("api", "agents", None, "delete"),
        lambda api, body, query, name: api.delete_agent(name),
    ),
    ("GET", ("api", "teams"), lambda api, body, query: api.teams()),
    (
        "POST",
        ("api", "teams"),
        lambda api, body, query: api.create_team(body),
    ),
    (
        "POST",
        ("api", "teams", None, "delete"),
        lambda api, body, query, name: api.delete_team(name),
    ),
    (
        "POST",
        ("api", "teams", None, "update"),
        lambda api, body, query, name: api.update_team(name, body),
    ),
    (
        "POST",
        ("api", "settings"),
        lambda api, body, query: api.update_settings(body),
    ),
    ("GET", ("api", "settings"), lambda api, body, query: api.settings()),
    (
        "GET",
        ("api", "teams", None, "files"),
        lambda api, body, query, name: api.team_files(name),
    ),
    # Workspace file names may contain "/", so — as with artifacts — the file
    # rides the query string on GET and the JSON body on POST, never a segment.
    (
        "GET",
        ("api", "teams", None, "file"),
        lambda api, body, query, name: api.team_file(name, (query.get("name") or [""])[0]),
    ),
    (
        "POST",
        ("api", "teams", None, "file"),
        lambda api, body, query, name: api.write_team_file(
            name,
            str(body.get("name") or ""),
            str(body.get("content") or ""),
            str(body.get("encoding") or "utf-8"),
        ),
    ),
    (
        "POST",
        ("api", "teams", None, "file", "delete"),
        lambda api, body, query, name: api.delete_team_file(name, str(body.get("name") or "")),
    ),
    (
        "POST",
        ("api", "teams", None, "file", "rename"),
        lambda api, body, query, name: api.rename_team_file(
            name, str(body.get("from") or ""), str(body.get("to") or "")
        ),
    ),
    (
        "POST",
        ("api", "teams", None, "file", "tags"),
        lambda api, body, query, name: api.set_team_file_tags(
            name, str(body.get("name") or ""), body.get("tags") or []
        ),
    ),
    (
        "POST",
        ("api", "teams", None, "files", "delete"),
        lambda api, body, query, name: api.delete_team_files(name, body.get("names") or []),
    ),
    (
        "POST",
        ("api", "teams", None, "folder", "delete"),
        lambda api, body, query, name: api.delete_team_folder(name, str(body.get("folder") or "")),
    ),
    ("GET", ("api", "connectors"), lambda api, body, query: api.connectors()),
    (
        "GET",
        ("api", "skills"),
        lambda api, body, query: api.skills(_qteam(query)),
    ),
    ("POST", ("api", "skills", "sync"), lambda api, body, query: api.sync_official_skills()),
    (
        "GET",
        ("api", "skills", None, "files"),
        lambda api, body, query, name: api.skill_files(name, _qteam(query)),
    ),
    # Skill file names may contain "/", so — as with teams — the file
    # rides the query string on GET and the JSON body on POST, never a segment.
    (
        "GET",
        ("api", "skills", None, "file"),
        lambda api, body, query, name: api.skill_file(
            name, (query.get("name") or [""])[0], _qteam(query)
        ),
    ),
    (
        "POST",
        ("api", "skills", None, "file"),
        lambda api, body, query, name: api.write_skill_file(
            name,
            str(body.get("name") or ""),
            str(body.get("content") or ""),
            str(body.get("encoding") or "utf-8"),
            _bteam(body),
        ),
    ),
    (
        "POST",
        ("api", "skills", None, "file", "delete"),
        lambda api, body, query, name: api.delete_skill_file(
            name, str(body.get("name") or ""), _bteam(body)
        ),
    ),
    (
        "POST",
        ("api", "skills", None, "delete"),
        lambda api, body, query, name: api.delete_skill(name, _bteam(body)),
    ),
    ("GET", ("api", "artifacts"), lambda api, body, query: api.artifact_spaces()),
    (
        "POST",
        ("api", "artifacts"),
        lambda api, body, query: api.create_space(str(body.get("name") or "")),
    ),
    ("GET", ("api", "artifacts", None), lambda api, body, query, space: api.artifacts(space)),
    (
        "POST",
        ("api", "artifacts", None, "delete"),
        lambda api, body, query, space: api.delete_space(space),
    ),
    # The artifact name rides the query string: names may contain "/", which
    # the segment-based route match can't capture.
    (
        "GET",
        ("api", "artifacts", None, "file"),
        lambda api, body, query, space: api.artifact_file(space, (query.get("name") or [""])[0]),
    ),
    (
        "POST",
        ("api", "artifacts", None, "file"),
        lambda api, body, query, space: api.write_artifact(
            space,
            str(body.get("name") or ""),
            str(body.get("content") or ""),
            str(body.get("encoding") or "utf-8"),
        ),
    ),
    (
        "POST",
        ("api", "artifacts", None, "file", "delete"),
        lambda api, body, query, space: api.delete_artifact(space, str(body.get("name") or "")),
    ),
    (
        "POST",
        ("api", "artifacts", None, "file", "rename"),
        lambda api, body, query, space: api.rename_artifact(
            space, str(body.get("from") or ""), str(body.get("to") or "")
        ),
    ),
    (
        "POST",
        ("api", "artifacts", None, "file", "tags"),
        lambda api, body, query, space: api.set_artifact_tags(
            space, str(body.get("name") or ""), body.get("tags") or []
        ),
    ),
    (
        "POST",
        ("api", "artifacts", None, "files", "delete"),
        lambda api, body, query, space: api.delete_artifacts(space, body.get("names") or []),
    ),
    (
        "POST",
        ("api", "artifacts", None, "folder", "delete"),
        lambda api, body, query, space: api.delete_artifact_folder(
            space, str(body.get("folder") or "")
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

    root = resources.files("syros.console").joinpath("static")
    if not root.is_dir():
        # The bundle is generated, not committed — absent in a fresh checkout.
        print("console frontend not built; run `make console` (API still available)")
        return files
    walk(root)
    return files


def _content_type(name: str) -> str:
    guessed = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if guessed.startswith("text/") or guessed in ("application/javascript", "text/javascript"):
        return f"{guessed}; charset=utf-8"
    return guessed


def _make_handler(api: ConsoleAPI, loop: asyncio.AbstractEventLoop, static: dict[str, bytes]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # `format` is the stdlib signature
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
                result = self._call(coro)
                if isinstance(result, tuple):
                    # (bytes, content_type) — a raw artifact download. nosniff
                    # keeps browsers from second-guessing the declared type;
                    # rendering happens in the frontend's sandboxed iframes,
                    # never by navigating to this endpoint.
                    data, content_type = result
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json(result)
            except NotFound as exc:
                self._json({"error": str(exc)}, 404)
            except Conflict as exc:
                self._json({"error": str(exc)}, 409)
            except TooLarge as exc:
                self._json({"error": str(exc)}, 413)
            except (ValueError, TypeError, SyrosError) as exc:
                # SyrosError covers the whole validation family — a rejected
                # option, an unparsable cron, a deployment that isn't there.
                self._json({"error": str(exc)}, 400)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise TooLarge(f"request body is {length} bytes (limit {MAX_BODY_BYTES})")
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
                    except TooLarge as exc:
                        # The body is left unread, so the socket can't be reused —
                        # those bytes would parse as the next pipelined request.
                        self.close_connection = True
                        self._json({"error": str(exc)}, 413)
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
