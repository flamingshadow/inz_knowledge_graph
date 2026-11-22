import io
import json

from inzkg.chat.audit_log import ChatLog
from inzkg.mcp_server import Server, serve
from inzkg.queries import QUERIES


class NoDriver:
    """Any attempt to reach the database fails, so only validation paths are exercised."""

    def session(self, **kw):
        raise ConnectionError("no database in this test")


def make(tmp_path):
    return Server(driver=NoDriver(), log=ChatLog(tmp_path))


def test_lists_only_approved_queries(tmp_path):
    reply = make(tmp_path).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in reply["result"]["tools"]]
    assert names == list(QUERIES)
    assert all(t["annotations"]["readOnlyHint"] for t in reply["result"]["tools"])


def test_refuses_unknown_tool_and_logs_it(tmp_path):
    s = make(tmp_path)
    reply = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "run_cypher", "arguments": {"cypher": "MATCH (n) DETACH DELETE n"}}})
    assert reply["result"]["isError"]
    record = json.loads(next(tmp_path.glob("chat-*.jsonl")).read_text().splitlines()[-1])
    assert record["tool"] == "run_cypher" and "not an approved query" in record["error"]


def test_bad_values_are_rejected_before_the_database(tmp_path):
    reply = make(tmp_path).handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "visa_requirements", "arguments": {"visa_type": "NOPE"}}})
    assert reply["result"]["isError"]
    assert "Invalid values" in reply["result"]["content"][0]["text"]


def test_stdio_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("INZKG_CHAT_LOG_DIR", str(tmp_path))
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
    out = io.StringIO()
    serve(io.StringIO("\n".join(json.dumps(x) for x in lines) + "\n"), out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[0]["result"]["serverInfo"]["name"] == "inz-knowledge-graph"
    assert "approved query tools" in replies[0]["result"]["instructions"]
