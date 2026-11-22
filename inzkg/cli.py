"""Command line: ``inzkg check | load | fetch | query | ask | serve | verify-log``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inzkg", description="INZ rules knowledge graph")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="check every rule's quote against the manual snapshot (no database needed)")
    p.add_argument("--snapshot")

    p = sub.add_parser("load", help="load the manual snapshot and the rulebook into Neo4j")
    p.add_argument("--snapshot")
    p.add_argument("--loaded-by")

    p = sub.add_parser("fetch", help="download a new manual snapshot over HTTPS")
    p.add_argument("--out", default="data/snapshots")

    p = sub.add_parser("query", help="run one approved query")
    p.add_argument("query_id")
    p.add_argument("args", nargs="*", help="key=value pairs")

    p = sub.add_parser("ask", help="ask the chat assistant one question")
    p.add_argument("question")
    p.add_argument("--staff-id", default="cli")

    p = sub.add_parser("serve", help="run the staff web interface")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("verify-log", help="check the chat log's hash chain")
    p.add_argument("path", nargs="?")

    a = parser.parse_args(argv)
    settings = get_settings()
    if getattr(a, "snapshot", None):
        settings = replace(settings, snapshot_path=Path(a.snapshot))

    if a.cmd == "check":
        from .manual.parse import load_snapshot
        from .rules import build_rulebook

        snap = load_snapshot(settings.snapshot_path)
        book = build_rulebook(snap, settings.rules_dir)
        n = sum(len(v) for v in book.nodes.values())
        print(f"{n} rulebook nodes, {len(book.rels)} links, {len(book.issues)} quote problems")
        for i in book.issues:
            print(f"  {i.rule_id}: {i.message}")
        for f, s in book.status_by_file.items():
            print(f"  {f}: {s}")
        return 1 if book.issues else 0

    if a.cmd == "load":
        from .loader import load_from_settings

        run = load_from_settings(settings, a.loaded_by)
        print(json.dumps({k: v for k, v in run.items() if k != "changedPageIds"}, indent=2, default=str))
        return 1 if run["issues"] else 0

    if a.cmd == "fetch":
        from .manual.fetch import fetch_snapshot

        print(fetch_snapshot(a.out))
        return 0

    if a.cmd == "query":
        from .db import reader_driver
        from .queries import execute

        args = dict(kv.split("=", 1) for kv in a.args)
        params, rows = execute(reader_driver(settings), a.query_id, args)
        print(json.dumps({"params": params, "rows": rows}, indent=2, default=str))
        return 0

    if a.cmd == "ask":
        from .chat.assistant import Assistant, Conversation
        from .db import reader_driver

        answer = Assistant(reader_driver(settings), settings=settings).ask(Conversation(), a.question, a.staff_id)
        print(json.dumps(asdict(answer), indent=2, default=str))
        return 0

    if a.cmd == "serve":
        import uvicorn

        uvicorn.run("inzkg.web.app:app", host=a.host, port=a.port)
        return 0

    if a.cmd == "verify-log":
        from .chat.audit_log import verify

        paths = [Path(a.path)] if a.path else sorted(settings.chat_log_dir.glob("chat-*.jsonl"))
        ok_all = True
        for path in paths:
            ok, n, msg = verify(path)
            ok_all &= ok
            print(f"{path}: {n} records, {msg}")
        return 0 if ok_all else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
