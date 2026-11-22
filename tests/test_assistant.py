"""The assistant's safeguards, with a scripted model and stubbed queries."""

import datetime as dt
import json

import pytest

from inzkg.chat import assistant as amod
from inzkg.chat.assistant import Assistant, Conversation
from inzkg.chat.audit_log import ChatLog, verify
from inzkg.chat.prompts import FALLBACK_LABEL, JUDGEMENT_LABEL, NO_ANSWER_TEXT, PERSON_ONLY_LABEL
from inzkg.config import get_settings

from .fakes import FakeClient, text, tool

TODAY = dt.date(2026, 11, 22)
CITE = {"ref": "V2.20", "verbatimText": "must have funds", "url": "https://www.immigration.govt.nz/opsmanual/#1.htm",
        "effectiveFrom": "2025-01-01", "effectiveTo": None, "status": "under_review", "pageId": "1"}


def interp(**kw):
    base = dict(reading="Visitor requirements", visa_type="VISITOR", visa_class="TEMPORARY", date=None,
                date_words=None, date_meaning="none", topic="requirements")
    base.update(kw)
    return tool("record_interpretation", **base)


@pytest.fixture
def run(monkeypatch, tmp_path):
    calls = []

    def fake_execute(driver, qid, args):
        calls.append((qid, args))
        return dict(args), ROWS.get(qid, [])

    monkeypatch.setattr(amod, "execute", fake_execute)
    monkeypatch.setattr(Assistant, "_manual_as_at", lambda self: "2026-11-22")

    def go(client, *questions):
        a = Assistant(None, client=client, settings=get_settings(), log=ChatLog(tmp_path), today=TODAY)
        conv = Conversation()
        answers = [a.ask(conv, q, "staff-1") for q in questions]
        return answers, calls, conv, tmp_path

    return go


ROWS = {
    "visa_requirements": [{"key": "VIS-FUNDS", "officerJudgement": False, "tier": 1, "citation": CITE}],
    "officer_judgement_rules": [{"criterion": "GEN-TEMP-BONAFIDE", "officerJudgement": True, "tier": 2,
                                 "citation": {**CITE, "ref": "E5.1"}}],
    "search_manual_text": [{"ref": "V2.20.5", "verbatimText": "maternity", "url": "u", "effectiveFrom": "2025-01-01"}],
    "entry_refusal_grounds": [{"ground": "Y4.1", "tier": 3, "citation": {**CITE, "ref": "Y4.1"}}],
}


def test_query_with_verified_date_runs_and_cites(run):
    q = "What does a visitor need if they lodged on 1 September 2026?"
    client = FakeClient(
        [interp(date="2026-09-01", date_words="1 September 2026", date_meaning="lodgement")],
        [tool("visa_requirements", visa_type="VISITOR", lodged_on="2026-09-01")],
        [text("They need funds.")],
    )
    (ans,), calls, _, logdir = run(client, q)
    assert calls == [("visa_requirements", {"visa_type": "VISITOR", "lodged_on": "2026-09-01"})]
    assert ans.kind == "answer" and ans.text == "They need funds."
    assert ans.sources[0]["ref"] == "V2.20" and ans.sources[0]["verbatimText"] == "must have funds"
    assert "lodged 1 September 2026" in ans.interpretation_display
    log = next(logdir.glob("chat-*.jsonl"))
    assert verify(log)[0]
    rec = json.loads(log.read_text().splitlines()[-1])
    assert rec["staffId"] == "staff-1" and rec["toolCalls"][1]["tool"] == "visa_requirements"


def test_invented_date_is_refused(run):
    client = FakeClient(
        [interp(date="2026-11-22", date_words="today", date_meaning="lodgement")],
        [tool("visa_requirements", visa_type="VISITOR", lodged_on="2026-11-22")],
        [tool("ask_clarifying_question", question="What date was the application lodged?", missing=["lodgement_date"])],
    )
    (ans,), calls, conv, _ = run(client, "What does a visitor need?")
    assert calls == []  # the query never ran
    assert ans.kind == "clarification" and "lodged" in ans.text
    assert not conv.interpretation.date_verified


def test_clarification_then_answer(run):
    client = FakeClient(
        [interp(), tool("ask_clarifying_question", question="When was it lodged?", missing=["lodgement_date"])],
        [interp(date="2026-09-01", date_words="1/9/2026", date_meaning="lodgement")],
        [tool("visa_requirements", visa_type="VISITOR", lodged_on="2026-09-01")],
        [text("Answer.")],
    )
    (a1, a2), calls, conv, _ = run(client, "What does a visitor need?", "It was lodged 1/9/2026")
    assert a1.kind == "clarification" and a2.kind == "answer"
    # The API needs tool results before the next turn: the reply joins the same user message.
    second = client.calls[1]["messages"]
    roles = [m["role"] for m in second]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))


def test_no_rows_gives_fixed_no_answer(run):
    client = FakeClient(
        [interp(date="2026-09-01", date_words="1 September 2026", date_meaning="as_at")],
        [tool("funds_amounts", list_id="VISITOR_FUNDS")],
        [text("I think it's about $1,000 a month.")],
    )
    (ans,), *_ = run(client, "How much do visitors need on 1 September 2026?")
    assert ans.kind == "no_answer" and ans.text == NO_ANSWER_TEXT


def test_unapproved_tool_and_wrong_visa_type_are_refused(run):
    client = FakeClient(
        [interp(date="2026-09-01", date_words="1 September 2026", date_meaning="lodgement"),
         tool("run_cypher", cypher="MATCH (n) DETACH DELETE n"),
         tool("visa_requirements", visa_type="SMC_RESIDENT", lodged_on="2026-09-01")],
        [text("Done.")],
    )
    (ans,), calls, _, _ = run(client, "Visitor rules lodged 1 September 2026")
    assert calls == [] and ans.kind == "no_answer"
    results = client.calls[1]["messages"][-2]["content"]  # same list object, now with the reply appended
    assert [r["is_error"] for r in results] == [False, True, True]


def test_query_before_interpretation_is_refused(run):
    client = FakeClient([tool("funds_amounts", list_id="VISITOR_FUNDS")], [text("x")])
    (ans,), calls, _, _ = run(client, "funds?")
    assert calls == []


def test_labels(run):
    client = FakeClient(
        [interp(date="2026-09-01", date_words="1 September 2026", date_meaning="as_at")],
        [tool("search_manual_text", text="maternity", as_at="2026-09-01")],
        [text("See below.")],
    )
    (ans,), *_ = run(client, "maternity rules on 1 September 2026")
    assert FALLBACK_LABEL in ans.labels

    client = FakeClient(
        [interp(date="2026-09-01", date_words="1 September 2026", date_meaning="lodgement")],
        [tool("officer_judgement_rules", visa_type="VISITOR", lodged_on="2026-09-01")],
        [text("Bona fides is judged by the officer.")],
    )
    (ans,), *_ = run(client, "Does this applicant meet bona fides? Lodged 1 September 2026")
    assert JUDGEMENT_LABEL in ans.labels
    assert any("That decision is yours" in n for n in ans.notices)

    client = FakeClient(
        [interp(visa_type="NONE", visa_class="NONE")],
        [tool("entry_refusal_grounds")],
        [text("Grounds listed.")],
    )
    (ans,), *_ = run(client, "When can entry be refused?")
    assert PERSON_ONLY_LABEL in ans.labels
