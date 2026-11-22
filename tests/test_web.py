"""Smoke test the staff web app with a scripted model and the real graph."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from inzkg.chat.assistant import Assistant
from inzkg.chat.audit_log import ChatLog
from inzkg.config import get_settings
from inzkg.web import app as webapp

from .fakes import FakeClient, text, tool

pytestmark = pytest.mark.neo4j


def test_chat_end_to_end(driver, tmp_path, monkeypatch):
    fake = FakeClient(
        [tool("record_interpretation", reading="Visitor requirements, lodged 1 September 2026",
              visa_type="VISITOR", visa_class="TEMPORARY", date="2026-09-01", date_words="1 September 2026",
              date_meaning="lodgement", topic="requirements")],
        [tool("visa_requirements", visa_type="VISITOR", lodged_on="2026-09-01")],
        [text("A visitor must meet the generic temporary entry rules and the V2 rules below.")],
    )
    a = Assistant(driver, client=fake, settings=get_settings(), log=ChatLog(tmp_path), today=dt.date(2026, 11, 22))
    monkeypatch.setitem(webapp._state, "assistant", a)
    monkeypatch.setitem(webapp._state, "driver", driver)
    c = TestClient(webapp.app)

    assert c.get("/").status_code == 200
    assert {v["code"] for v in c.get("/api/visa-types").json()} == {"VISITOR", "FEE_PAYING_STUDENT", "SMC_RESIDENT"}

    r = c.post("/api/chat", json={"message": "What does a visitor need? Lodged 1 September 2026"},
               headers={"X-Staff-Id": "officer-7"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "answer"
    assert any(s["ref"] == "V2.20" for s in body["sources"])
    assert all(s["verbatimText"] and s["url"] for s in body["sources"])
    assert "Officer judgement" in body["labels"]  # bona fides is a judgement rule
    assert body["manual_as_at"]

    r = c.post("/api/query/funds_amounts", json={"list_id": "VISITOR_FUNDS"})
    assert r.status_code == 200 and r.json()["rows"]
    assert c.post("/api/query/nope", json={}).status_code == 404
