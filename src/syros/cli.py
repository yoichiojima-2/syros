"""Minimal ops CLI — the UI syros deliberately doesn't have.

syros sessions                          list recent sessions
syros workspaces                        list shared workspaces and their leases
syros tail <session_id>                 follow a session's message feed
syros approvals <session_id>            list pending approvals
syros approvals <session_id> allow <call_hash>
syros approvals <session_id> deny <call_hash> [-m reason]
syros kill <session_id>                 flip the kill switch
syros agents                            list stored agents (personas)
syros agents create <name> --system-prompt "..." --allow Read --allow Bash
syros agents show|update|delete <name>
syros deployments                         list deployments and their next run
syros deployments create <name> --cron "0 9 * * *" --prompt "..." [--agent <name>]
syros deployments runs <name>             run history for one deployment
syros deployments run|pause|resume|delete <name>
syros tick                              fire every deployment that is due
syros artifacts                         list shared artifact spaces
syros artifacts <space>                 list files in a space
syros artifacts <space> push <path...>  upload local files/dirs into a space
syros artifacts <space> pull [dest]     download a space
syros artifacts <space> publish <session_id> <file...>
                                        copy files out of a session's workspace
syros skills                            list skills in the bucket
syros skills sync                       seed skills/ from the official anthropics/skills repo
syros connectors                        list platform connectors and credential status
syros connectors auth <name>            OAuth into a connector; stores the credential
syros connectors set <name> [--token X | --file p]
                                        store a static credential (e.g. a GitHub PAT)
syros connectors remove <name>          destroy the stored credential
syros console                           serve the web console (localhost or Cloud Run)
syros export                            snapshot Firestore into BigQuery for analysis
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os

from . import env
from .errors import SyrosError
from .options import AgentOptions
from .store import Store
from .types import doc_to_message


def _project(args: argparse.Namespace) -> str:
    project = env.find_project(args.project)
    if not project:
        raise SystemExit("set --project or $SYROS_PROJECT")
    return project


def _store(args: argparse.Namespace) -> Store:
    return Store(_project(args))


def _options(args: argparse.Namespace) -> AgentOptions:
    """The installation coordinates a command needs to trigger a run."""
    return AgentOptions(
        project=_project(args),
        region=getattr(args, "region", None),
        job=getattr(args, "job", None),
    )


def _local(epoch: float | None, timezone: str = "UTC") -> str:
    if not epoch:
        return "—"
    from datetime import datetime

    from .cron import zone

    return datetime.fromtimestamp(epoch, tz=zone(timezone)).strftime("%Y-%m-%d %H:%M")


async def _sessions(args) -> None:
    store = _store(args)
    for session in await store.list_sessions():
        published = session.get("published")
        print(
            f"{session['id']}  {session.get('status'):<10}"
            f"  ${float(session.get('cost_usd') or 0):.4f}"
            f"  {session.get('stop_reason') or '':<14}"
            f"  {'' if published is None else f'{published} published':<14}"
            f"  {(session.get('options') or {}).get('workspace') or ''}"
        )


async def _workspaces(args) -> None:
    from .store import lease_active

    store = _store(args)
    for doc in sorted(await store.list_workspaces(), key=lambda d: d["name"]):
        busy = lease_active(doc)
        holder = doc.get("lease_session_id") if busy else ""
        print(f"{doc['name']:<24}  {'busy' if busy else 'free':<6}  {holder or ''}")


async def _tail(args) -> None:
    store = _store(args)
    cursor = 0
    while True:
        events = await store.list_events(args.session_id, after=cursor)
        for event in events:
            cursor = int(event["seq"])
            print(f"[{cursor}] {doc_to_message(event['message'])}")
        if not events:
            await asyncio.sleep(1.0)


async def _approvals(args) -> None:
    store = _store(args)
    if args.action == "list":
        for approval in await store.list_pending_approvals(args.session_id):
            print(f"{approval['call_hash']}  {approval['tool_name']}")
            print(f"    {json.dumps(approval.get('input') or {})[:200]}")
        return
    await store.decide_approval(
        args.session_id,
        args.call_hash,
        allow=args.action == "allow",
        decided_by=getpass.getuser(),
        deny_message=args.message if args.action == "deny" else None,
    )
    print(f"{args.action}: {args.call_hash}")


async def _kill(args) -> None:
    store = _store(args)
    await store.update_session(args.session_id, disabled=True)
    print(f"disabled: {args.session_id}")


def _run_options(args) -> AgentOptions:
    """The AgentOptions subset worth having on the command line."""
    return AgentOptions(
        model=args.model,
        system_prompt=args.system_prompt,
        allowed_tools=list(args.allow or []),
        permission_mode=args.permission_mode,
        workspace=args.workspace,
        artifacts=args.artifacts,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
    )


async def _agents(args) -> None:
    from . import agents

    options = _options(args)
    store = _store(args)

    if args.action == "list":
        for agent in await agents.list_all(store=store):
            opts = agent.get("options") or {}
            print(
                f"{agent['name']:<24}  {opts.get('model') or '-':<24}"
                f"  {opts.get('permission_mode') or 'default':<18}"
                f"  {agent.get('description') or ''}"
            )
        return

    if not args.name:
        raise SystemExit(f"agents {args.action} requires a name")

    if args.action == "create":
        agent = await agents.create(
            args.name,
            _run_options(args),
            options=options,
            description=args.description,
            created_by=getpass.getuser(),
            store=store,
        )
        print(f"created {args.name}")
        return

    if args.action == "update":
        await agents.update(
            args.name,
            _run_options(args),
            options=options,
            description=args.description,
            store=store,
        )
        print(f"updated {args.name}")
        return

    if args.action == "delete":
        await agents.delete(args.name, store=store)
        print(f"deleted {args.name}  (sessions that ran as it keep their options)")
        return

    # show
    agent = await agents.get(args.name, store=store)
    if agent is None:
        raise SystemExit(f"no such agent: {args.name}")
    print(
        json.dumps(
            {k: v for k, v in agent.items() if k not in ("created_at", "updated_at")},
            indent=2,
            default=str,
        )
    )


async def _deployments(args) -> None:
    from . import deployments

    options = _options(args)
    store = _store(args)

    if args.action == "list":
        for deployment in await deployments.list_all(store=store):
            timezone = deployment.get("timezone") or deployments.DEFAULT_TIMEZONE
            state = "paused" if not deployment.get("enabled") else "on"
            print(
                f"{deployment['name']:<24}  {deployment.get('cron', ''):<16}  {state:<7}"
                f"  next {_local(deployment.get('next_run_at'), timezone)} {timezone}"
                f"  {deployment.get('runs') or 0} runs"
                f"  {deployment.get('last_session_id') or ''}"
            )
            if deployment.get("last_error"):
                print(f"    error: {deployment['last_error']}")
        return

    if not args.name:
        raise SystemExit(f"deployments {args.action} requires a name")

    if args.action == "create":
        if not args.cron or not args.prompt:
            raise SystemExit("create requires --cron and --prompt")
        deployment = await deployments.create(
            args.name,
            args.cron,
            args.prompt,
            _run_options(args),
            options=options,
            agent=args.agent,
            timezone=args.tz,
            created_by=getpass.getuser(),
            store=store,
        )
        print(f"created {args.name}: {deployment['cron']} ({args.tz})")
        print(f"    next run {_local(deployment['next_run_at'], args.tz)} {args.tz}")
        return

    if args.action in ("pause", "resume"):
        deployment = await deployments.set_enabled(args.name, args.action == "resume", store=store)
        print(f"{args.action}d {args.name}")
        if args.action == "resume":
            timezone = deployment.get("timezone") or deployments.DEFAULT_TIMEZONE
            print(f"    next run {_local(deployment['next_run_at'], timezone)} {timezone}")
        return

    if args.action == "delete":
        await deployments.delete(args.name, store=store)
        print(f"deleted {args.name}  (its past runs stay in `syros sessions`)")
        return

    if args.action == "run":
        session_id = await deployments.run_now(
            args.name, options=options, store=store, created_by=getpass.getuser()
        )
        print(f"started {session_id}")
        return

    # runs: the deployment's own history
    for session in await deployments.runs(args.name, limit=args.limit, store=store):
        print(
            f"{session['id']}  {session.get('status'):<10}"
            f"  {session.get('trigger') or '':<9}"
            f"  ${float(session.get('cost_usd') or 0):.4f}"
            f"  {session.get('stop_reason') or '':<22}"
            f"  {_local(_epoch(session.get('created_at')))}"
        )


def _epoch(value) -> float | None:
    from datetime import datetime

    return value.timestamp() if isinstance(value, datetime) else value


async def _tick(args) -> None:
    from . import deployments

    result = await deployments.tick(_options(args), store=_store(args))
    for run in result["fired"]:
        print(f"fired    {run['deployment']}  {run['session_id']}")
    for run in result["skipped"]:
        print(f"skipped  {run['deployment']}  (previous run {run['session_id']} still active)")
    for failure in result["errors"]:
        print(f"error    {failure['deployment']}  {failure['error']}")
    if not any((result["fired"], result["skipped"], result["errors"])):
        print("nothing due")


async def _artifacts(args) -> None:
    from pathlib import Path

    from . import artifacts, workspace

    project = _project(args)
    bucket = env.default_bucket(args.bucket, project)
    if not args.space:
        for space in await asyncio.to_thread(artifacts.list_spaces, project, bucket):
            print(space)
        return
    if args.action == "list":
        for item in await asyncio.to_thread(artifacts.list_artifacts, project, bucket, args.space):
            updated = item.updated.strftime("%Y-%m-%d %H:%M") if item.updated else ""
            print(f"{item.name}  {item.size}  {updated}")
        return
    if args.action == "push":
        paths = [Path(p) for p in args.args]
        count = await asyncio.to_thread(artifacts.push, project, bucket, args.space, paths)
        print(f"pushed {count} file(s) to {args.space}")
        return
    if args.action == "pull":
        dest = Path(args.args[0]) if args.args else Path(args.space)
        count = await asyncio.to_thread(artifacts.pull, project, bucket, args.space, dest)
        print(f"pulled {count} file(s) into {dest}")
        return
    # publish: copy files out of a session's checkpointed working directory
    session_id, names = args.args[0], args.args[1:]
    session = await _store(args).get_session(session_id)
    if not session:
        raise SystemExit(f"no such session: {session_id}")
    shared = (session.get("options") or {}).get("workspace")
    source = (
        workspace.workspace_prefix(shared) if shared else workspace.session_prefix(session_id, "ws")
    )
    count = await asyncio.to_thread(artifacts.publish, project, bucket, args.space, source, names)
    print(f"published {count} file(s) from {session_id} to {args.space}")


async def _skills(args) -> None:
    from . import skills
    from .console.objects import MAX_PREVIEW_BYTES, GcsObjects

    project = _project(args)
    bucket = env.default_bucket(args.bucket, project)
    if args.action == "sync":
        summary = await asyncio.to_thread(
            skills.sync_official, project, bucket, max_bytes=MAX_PREVIEW_BYTES
        )
        print(f"synced {summary['files']} file(s) across {len(summary['skills'])} skill(s)")
        for skipped in summary["skipped"]:
            print(f"    skipped {skipped['skill']}/{skipped['file']} ({skipped['size']} bytes)")
        return
    stats = await GcsObjects(project, bucket).skill_stats()
    for name in sorted(stats):
        stat = stats[name]
        print(f"{name:<24}  {stat['file_count']:>3} file(s)  {stat['total_size']} bytes")


async def _oauth_login(server_url: str, port: int) -> dict:
    """MCP-spec OAuth 2.1 against a hosted server: discovery, dynamic client
    registration, browser consent, loopback callback — all handled by the mcp
    SDK's own client. Returns the credential dict connectors.resolve_token
    understands (token endpoint included, so the runner can refresh)."""
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    from mcp.client.auth import OAuthClientProvider, TokenStorage
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.auth import OAuthClientMetadata

    class MemoryStorage(TokenStorage):
        tokens = None
        client_info = None

        async def get_tokens(self):
            return self.tokens

        async def set_tokens(self, tokens):
            self.tokens = tokens

        async def get_client_info(self):
            return self.client_info

        async def set_client_info(self, client_info):
            self.client_info = client_info

    result: dict = {}
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            result["code"] = (query.get("code") or [""])[0]
            result["state"] = (query.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"authorized - you can close this tab")
            loop.call_soon_threadsafe(done.set)

    server = HTTPServer(("127.0.0.1", port), Callback)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def on_redirect(url: str) -> None:
        print(f"open to authorize:\n  {url}")
        webbrowser.open(url)

    async def on_callback() -> tuple[str, str | None]:
        await done.wait()
        return result.get("code") or "", result.get("state")

    storage = MemoryStorage()
    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            redirect_uris=[f"http://localhost:{port}/callback"],
            client_name="syros",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=on_redirect,
        callback_handler=on_callback,
    )
    try:
        # initialize() forces the handshake, which forces the OAuth flow
        async with streamablehttp_client(server_url, auth=provider) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
    finally:
        server.shutdown()

    tokens, client_info = storage.tokens, storage.client_info
    if not tokens or not tokens.access_token:
        raise SystemExit("authorization did not produce a token")
    metadata = provider.context.oauth_metadata
    return {
        "type": "oauth",
        "server_url": server_url,
        "token_endpoint": str(metadata.token_endpoint) if metadata else None,
        "client_id": client_info.client_id if client_info else None,
        "client_secret": client_info.client_secret if client_info else None,
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
    }


def _google_login(client_secrets: str | None, port: int) -> str:
    """Standard Google installed-app flow -> authorized-user JSON (the same
    shape gcloud writes), which the runner refreshes with google-auth."""
    from .connectors import GOOGLE_SCOPES

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise SystemExit(
            "the google flow needs google-auth-oauthlib (run `uv sync` / pip install"
            " google-auth-oauthlib), or store a ready authorized-user JSON with"
            " `syros connectors set google --file <authorized_user.json>`"
        ) from None
    if not client_secrets:
        raise SystemExit(
            "auth google requires --client-secrets <oauth_client.json> — create an OAuth"
            " client (Desktop app) in the Google Cloud console and download its JSON"
        )
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, scopes=list(GOOGLE_SCOPES))
    return flow.run_local_server(port=port).to_json()


async def _connectors(args) -> None:
    from . import connectors

    project = _project(args)

    if args.action == "list":
        status = await asyncio.to_thread(connectors.credential_status, project)
        for connector in connectors.CATALOG.values():
            state = status.get(connector.name) or {}
            configured = bool(state.get("configured"))
            print(
                f"{connector.name:<8}  {connector.label:<18}  {connector.auth:<7}"
                f"  {'configured' if configured else '—':<12}"
                f"  {_local(state.get('updated')) if configured else ''}"
            )
        return

    if not args.name:
        raise SystemExit(f"connectors {args.action} requires a name")
    connector = connectors.CATALOG.get(args.name)
    if connector is None:
        raise SystemExit(
            f"unknown connector {args.name!r}: choose from {', '.join(sorted(connectors.CATALOG))}"
        )

    if args.action == "set":
        if args.token:
            payload = args.token
        elif args.file:
            from pathlib import Path

            payload = Path(args.file).read_text()
        else:
            payload = getpass.getpass(f"{args.name} credential: ")
        if not payload.strip():
            raise SystemExit("empty credential")
        await asyncio.to_thread(connectors.write_credential, project, args.name, payload.strip())
        print(f"stored credential for {args.name} in {connectors.secret_id(args.name)}")
        return

    if args.action == "remove":
        destroyed = await asyncio.to_thread(connectors.clear_credential, project, args.name)
        print(f"destroyed {destroyed} version(s) of {connectors.secret_id(args.name)}")
        return

    # auth
    if connector.auth == "token":
        raise SystemExit(
            f"{args.name} uses a static token (e.g. a GitHub PAT scoped to what the"
            f" agent should reach):\n  syros connectors set {args.name}"
        )
    if connector.auth == "google":
        payload = await asyncio.to_thread(_google_login, args.client_secrets, args.port)
    else:
        server_url = next(iter(connector.servers.values()))
        payload = json.dumps(await _oauth_login(server_url, args.port))
    await asyncio.to_thread(connectors.write_credential, project, args.name, payload)
    print(f"authorized {args.name}; credential stored in {connectors.secret_id(args.name)}")


async def _export(args) -> None:
    from .analytics import collect, load

    project = _project(args)
    tables = await collect(_store(args))
    counts = await asyncio.to_thread(load, project, tables, dataset=args.dataset)
    for name, count in counts.items():
        print(f"{project}.{args.dataset}.{name}  {count} rows")


async def _console(args) -> None:
    from .console.api import ConsoleAPI
    from .console.server import run
    from .options import AgentOptions

    project = _project(args)
    options = AgentOptions(project=project, region=args.region, job=args.job)
    api = ConsoleAPI(Store(project), options, approval_timeout=args.approval_timeout)
    local = args.host in ("127.0.0.1", "localhost", "::1")
    await run(api, args.host, args.port, open_browser=local and not args.no_open)


def main() -> None:
    parser = argparse.ArgumentParser(prog="syros")
    parser.add_argument("--project", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sessions").set_defaults(func=_sessions)

    sub.add_parser("workspaces").set_defaults(func=_workspaces)

    tail = sub.add_parser("tail")
    tail.add_argument("session_id")
    tail.set_defaults(func=_tail)

    approvals = sub.add_parser("approvals")
    approvals.add_argument("session_id")
    approvals.add_argument("action", nargs="?", default="list", choices=["list", "allow", "deny"])
    approvals.add_argument("call_hash", nargs="?")
    approvals.add_argument("-m", "--message", default=None)
    approvals.set_defaults(func=_approvals)

    kill = sub.add_parser("kill")
    kill.add_argument("session_id")
    kill.set_defaults(func=_kill)

    agents = sub.add_parser("agents")
    agents.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "create", "show", "update", "delete"],
    )
    agents.add_argument("name", nargs="?")
    agents.add_argument("--description", default=None)
    # Run options: the subset of AgentOptions worth having on the command line.
    agents.add_argument("--model", default=None)
    agents.add_argument("--system-prompt", default=None)
    agents.add_argument("--allow", action="append", metavar="TOOL", help="repeatable")
    agents.add_argument("--permission-mode", default=None)
    agents.add_argument("--workspace", default=None)
    agents.add_argument("--artifacts", default=None, metavar="SPACE")
    agents.add_argument("--max-turns", type=int, default=None)
    agents.add_argument("--max-budget-usd", type=float, default=None)
    agents.set_defaults(func=_agents)

    deployments = sub.add_parser("deployments")
    deployments.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "create", "pause", "resume", "delete", "run", "runs"],
    )
    deployments.add_argument("name", nargs="?")
    deployments.add_argument("--cron", default=None, help="5-field cron, or an @alias")
    deployments.add_argument("--tz", default="UTC", help="IANA timezone the cron is read in")
    deployments.add_argument("--prompt", default=None)
    deployments.add_argument("--agent", default=None, help="stored agent the runs default to")
    # Run options: the subset of AgentOptions worth having on the command line.
    deployments.add_argument("--model", default=None)
    deployments.add_argument("--system-prompt", default=None)
    deployments.add_argument("--allow", action="append", metavar="TOOL", help="repeatable")
    deployments.add_argument("--permission-mode", default=None)
    deployments.add_argument("--workspace", default=None)
    deployments.add_argument("--artifacts", default=None, metavar="SPACE")
    deployments.add_argument("--max-turns", type=int, default=None)
    deployments.add_argument("--max-budget-usd", type=float, default=None)
    deployments.add_argument("--limit", type=int, default=50, help="runs to list")
    deployments.add_argument("--region", default=None)
    deployments.add_argument("--job", default=None)
    deployments.set_defaults(func=_deployments)

    tick = sub.add_parser("tick", help="fire due deployments; the scheduler job's entrypoint")
    tick.add_argument("--region", default=None)
    tick.add_argument("--job", default=None)
    tick.set_defaults(func=_tick)

    artifacts = sub.add_parser("artifacts")
    artifacts.add_argument("space", nargs="?")
    artifacts.add_argument(
        "action", nargs="?", default="list", choices=["list", "push", "pull", "publish"]
    )
    artifacts.add_argument("args", nargs="*")
    artifacts.add_argument("--bucket", default=None)
    artifacts.set_defaults(func=_artifacts)

    skills = sub.add_parser("skills")
    skills.add_argument("action", nargs="?", default="list", choices=["list", "sync"])
    skills.add_argument("--bucket", default=None)
    skills.set_defaults(func=_skills)

    connectors = sub.add_parser("connectors")
    connectors.add_argument(
        "action", nargs="?", default="list", choices=["list", "auth", "set", "remove"]
    )
    connectors.add_argument("name", nargs="?")
    connectors.add_argument("--token", default=None, help="credential value for `set`")
    connectors.add_argument("--file", default=None, help="read the credential from a file")
    connectors.add_argument(
        "--client-secrets", default=None, help="OAuth client JSON for `auth google`"
    )
    connectors.add_argument(
        "--port", type=int, default=8765, help="loopback port for the OAuth callback"
    )
    connectors.set_defaults(func=_connectors)

    export = sub.add_parser("export")
    export.add_argument("--dataset", default=os.environ.get("SYROS_DATASET") or "syros")
    export.set_defaults(func=_export)

    console = sub.add_parser("console")
    console.add_argument("--host", default="127.0.0.1")
    console.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8484))
    console.add_argument("--region", default=None)
    console.add_argument("--job", default=None)
    console.add_argument(
        "--approval-timeout",
        type=float,
        default=env.approval_timeout(),
    )
    console.add_argument("--no-open", action="store_true")
    console.set_defaults(func=_console)

    args = parser.parse_args()
    if getattr(args, "action", None) in ("allow", "deny") and not args.call_hash:
        parser.error("allow/deny require a call_hash")
    if args.command == "artifacts":
        if args.action == "push" and not args.args:
            parser.error("push requires at least one path")
        if args.action == "publish" and len(args.args) < 2:
            parser.error("publish requires a session_id and at least one file")
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        pass
    except SyrosError as exc:
        # Bad cron, unknown deployment, unusable options: the user's problem to
        # fix, not a bug — print the message, skip the traceback.
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
