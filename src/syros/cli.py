"""Minimal ops CLI — the UI syros deliberately doesn't have.

syros sessions                          list recent sessions
syros tail <session_id>                 follow a session's message feed
syros approvals <session_id>            list pending approvals
syros approvals <session_id> allow <call_hash>
syros approvals <session_id> deny <call_hash> [-m reason]
syros kill <session_id>                 flip the kill switch
syros console                           serve the web console (localhost or Cloud Run)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os

from .store import Store
from .types import doc_to_message


def _project(args: argparse.Namespace) -> str:
    project = (
        args.project or os.environ.get("SYROS_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    if not project:
        raise SystemExit("set --project or $SYROS_PROJECT")
    return project


def _store(args: argparse.Namespace) -> Store:
    return Store(_project(args))


async def _sessions(args) -> None:
    store = _store(args)
    for session in await store.list_sessions():
        print(
            f"{session['id']}  {session.get('status'):<10}"
            f"  ${float(session.get('cost_usd') or 0):.4f}"
            f"  {session.get('stop_reason') or ''}"
        )


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


async def _console(args) -> None:
    from .console.api import ConsoleAPI
    from .console.server import run
    from .options import AgentOptions

    project = _project(args)
    options = AgentOptions(sandbox="gcp", project=project, region=args.region, job=args.job)
    api = ConsoleAPI(Store(project), options, approval_timeout=args.approval_timeout)
    local = args.host in ("127.0.0.1", "localhost", "::1")
    await run(api, args.host, args.port, open_browser=local and not args.no_open)


def main() -> None:
    parser = argparse.ArgumentParser(prog="syros")
    parser.add_argument("--project", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sessions").set_defaults(func=_sessions)

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

    console = sub.add_parser("console")
    console.add_argument("--host", default="127.0.0.1")
    console.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8484))
    console.add_argument("--region", default=None)
    console.add_argument("--job", default=None)
    console.add_argument(
        "--approval-timeout",
        type=float,
        default=float(os.environ.get("SYROS_APPROVAL_TIMEOUT") or 300.0),
    )
    console.add_argument("--no-open", action="store_true")
    console.set_defaults(func=_console)

    args = parser.parse_args()
    if getattr(args, "action", None) in ("allow", "deny") and not args.call_hash:
        parser.error("allow/deny require a call_hash")
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
