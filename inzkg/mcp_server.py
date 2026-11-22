"""Local MCP server so Claude Desktop can be the staff chat interface.

Claude Desktop starts ``inzkg mcp`` and talks to it over stdin/stdout (JSON-RPC, one
message per line). The server offers only the approved read-only queries from
``inzkg.queries.catalogue`` as tools, runs them through ``execute`` (validated values,
read-only plan check), and writes every call to the hash-chained chat log. It never
accepts Cypher, so nothing can write its own query. No API key is needed: Claude Desktop
is the language model.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .chat.audit_log import ChatLog
from .chat.prompts import SYSTEM_PROMPT
from .config import Settings, get_settings
from .db import ReadOnlyViolation
from .queries import QUERIES, execute

PROTOCOL_VERSION = "2025-06-18"

INSTRUCTIONS = SYSTEM_PROMPT.replace(
    "1. First call record_interpretation to say how you read the question",
    "1. First state, in one line the officer can correct, how you read the question",
).replace(
    "call ask_clarifying_question to ask for it", "ask the officer for it"
).replace(
    "ask one short clarifying question instead of guessing", "ask the officer one short clarifying question instead of guessing"
) + """
In this chat the manual's exact wording is not shown beside your answer, so quote the verbatimText from the results for each point (briefly), with its instruction code, effective date and url.
"""


class Server:
    def __init__(self, driver: Any = None, settings: Settings | None = None, log: ChatLog | None = None):
        self.settings = settings or get_settings()
        self._driver = driver
        self.log = log or ChatLog(self.settings.chat_log_dir)

    @property
    def driver(self) -> Any:
        if self._driver is None:
            from .db import reader_driver

            self._driver = reader_driver(self.settings)
        return self._driver

    def tools(self) -> list[dict]:
        return [{"name": q.id, "title": q.title, "description": f"{q.title}. {q.description}",
                 "inputSchema": q.tool_schema(), "annotations": {"readOnlyHint": True}}
                for q in QUERIES.values()]

    def call(self, name: str, args: dict) -> dict:
        entry: dict[str, Any] = {"source": "claude-desktop-mcp", "tool": name, "input": args}
        try:
            if name not in QUERIES:
                raise ValueError(f"{name} is not an approved query")
            params, rows = execute(self.driver, name, args)
            entry.update(params={k: v for k, v in params.items() if k != "lucene"}, rows=len(rows))
            text, error = (json.dumps(rows, default=str, ensure_ascii=False)[:60000] if rows else "No results."), False
        except ValueError as exc:
            entry["error"] = str(exc)
            text, error = f"Invalid values: {exc}", True
        except ReadOnlyViolation as exc:
            entry["error"] = str(exc)
            text, error = "That query is not allowed.", True
        except Exception as exc:  # noqa: BLE001 - e.g. Neo4j not running; tell the model, keep serving
            entry["error"] = f"{type(exc).__name__}: {exc}"
            text, error = f"The knowledge graph could not be reached ({type(exc).__name__}). Is Neo4j running?", True
        self.log.append(entry)
        return {"content": [{"type": "text", "text": text}], "isError": error}

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notification
            return None
        if method == "initialize":
            result: dict = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "inz-knowledge-graph", "version": "0.1.0"},
                            "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self.tools()}
        elif method == "tools/call":
            p = msg.get("params") or {}
            result = self.call(str(p.get("name")), p.get("arguments") or {})
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:
    server = Server()
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply: dict | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = server.handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply, default=str, ensure_ascii=False) + "\n")
            stdout.flush()
