"""Staff query interface: a small web app over the chat assistant and the approved queries.

Run with ``inzkg serve`` (or ``uvicorn inzkg.web.app:app``). In INZ it should sit behind the
agency's single sign-on proxy, which passes the signed-in officer's id in ``X-Staff-Id``;
that id goes into the chat log with every question.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..chat.assistant import Assistant, Conversation
from ..config import get_settings
from ..db import reader_driver
from ..queries import QUERIES, VISA_TYPES, execute

STATIC = Path(__file__).parent / "static"
CONVERSATION_TTL_SECONDS = 4 * 3600

app = FastAPI(title="INZ rules knowledge graph", docs_url=None, redoc_url=None)
_state: dict[str, Any] = {"assistant": None, "driver": None, "conversations": {}}


def _driver():
    if _state["driver"] is None:
        _state["driver"] = reader_driver(get_settings())
    return _state["driver"]


def _assistant() -> Assistant:
    if _state["assistant"] is None:
        _state["assistant"] = Assistant(_driver())
    return _state["assistant"]


def _conversation(conv_id: str | None) -> Conversation:
    convs: dict[str, tuple[float, Conversation]] = _state["conversations"]
    now = time.time()
    for k in [k for k, (t, _) in convs.items() if now - t > CONVERSATION_TTL_SECONDS]:
        del convs[k]
    if conv_id and conv_id in convs:
        conv = convs[conv_id][1]
    else:
        conv = Conversation()
    convs[conv.id] = (now, conv)
    return conv


class Correction(BaseModel):
    visa_type: str | None = Field(None, description="Visa type code from the fixed list")
    date: str | None = Field(None, description="YYYY-MM-DD")
    date_meaning: str = "lodgement"


class ChatRequest(BaseModel):
    message: str = Field("", max_length=2000)
    conversation_id: str | None = None
    correction: Correction | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/visa-types")
def visa_types() -> list[dict]:
    return [{"code": k, "name": v} for k, v in VISA_TYPES.items()]


@app.get("/api/queries")
def queries() -> list[dict]:
    return [{"id": q.id, "title": q.title, "description": q.description, "source": q.source,
             "params": [asdict(p) for p in q.params]} for q in QUERIES.values()]


@app.post("/api/query/{query_id}")
def run_query(query_id: str, args: dict[str, Any], x_staff_id: str = Header("unknown")) -> dict:
    """Run one approved query directly (for the 'browse the rulebook' view). Logged too."""
    if query_id not in QUERIES:
        raise HTTPException(404, "not an approved query")
    try:
        params, rows = execute(_driver(), query_id, args)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _assistant().log.append({"type": "direct_query", "staffId": x_staff_id, "query": query_id,
                             "params": {k: v for k, v in params.items() if k != "lucene"}, "rows": len(rows)})
    return {"query": query_id, "params": params, "rows": rows}


@app.post("/api/chat")
def chat(req: ChatRequest, x_staff_id: str = Header("unknown")) -> dict:
    conv = _conversation(req.conversation_id)
    message = req.message.strip()
    if req.correction:
        # A correction from the "how I read your question" panel becomes the officer's own
        # words, so the date check sees a date the officer gave.
        c = req.correction
        parts = []
        if c.visa_type:
            if c.visa_type not in VISA_TYPES:
                raise HTTPException(422, "unknown visa type")
            parts.append(f"the visa type is {VISA_TYPES[c.visa_type]}")
        if c.date:
            label = "the application was lodged on" if c.date_meaning == "lodgement" else "the date to use is"
            parts.append(f"{label} {c.date}")
        message = ("Correction: " + "; ".join(parts) + ". " + message).strip()
    if not message:
        raise HTTPException(422, "empty message")
    answer = _assistant().ask(conv, message, staff_id=x_staff_id)
    return asdict(answer)
