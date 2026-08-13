"""Minimal ops CLI — the UI syros deliberately doesn't have.

syros sessions                          list recent sessions
syros workspaces                        list shared workspaces and their leases
syros tail <session_id>                 follow a session's message feed
syros approvals <session_id>            list pending approvals
syros approvals <session_id> allow <call_hash>
syros approvals <session_id> deny <call_hash> [-m reason]
syros kill <session_id>                 flip the kill switch
syros artifacts                         list shared artifact spaces
syros artifacts <space>                 list files in a space
syros artifacts <space> push <path...>  upload local files/dirs into a space
syros artifacts <space> pull [dest]     download a space
syros artifacts <space> publish <session_id> <file...>
                                        copy files out of a session's workspace
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
from .store import Store
from .types import doc_to_message


def _project(args: argparse.Namespace) -> str:
    project = env.find_project(args.project)
    if not project:
        raise SystemExit("set --project or $SYROS_PROJECT")
    return project


def _store(args: argparse.Namespace) -> Store:
    return Store(_project(args))


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

    artifacts = sub.add_parser("artifacts")
    artifacts.add_argument("space", nargs="?")
    artifacts.add_argument(
        "action", nargs="?", default="list", choices=["list", "push", "pull", "publish"]
    )
    artifacts.add_argument("args", nargs="*")
    artifacts.add_argument("--bucket", default=None)
    artifacts.set_defaults(func=_artifacts)

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


if __name__ == "__main__":
    main()
