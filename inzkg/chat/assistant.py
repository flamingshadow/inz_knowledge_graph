"""The staff chat assistant.

A language model reads the officer's question, records how it read it, and answers only
by calling the approved queries in ``inzkg.queries.catalogue``. This module runs that
loop and enforces the safeguards in code, so they do not depend on the model behaving:

* the model can call only approved, read-only queries, with validated values;
* a query that needs a lodgement or "as at" date only runs with a date the officer typed
  (checked against their words), never a default;
* visa types come from the fixed list;
* citations (exact wording, code, effective date, link) are built from query results,
  not from the model's text;
* answers from the text index are labelled "From manual text, not the rulebook";
* judgement rules are labelled "Officer judgement", with no verdict;
* if nothing answers the question, the model's text is replaced with a fixed "no answer";
* every turn is written to the append-only chat log.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from neo4j import Driver

from ..config import Settings, get_settings
from ..db import ReadOnlyViolation
from ..queries import QUERIES, VISA_TYPES, execute
from .audit_log import ChatLog
from .dates import check_user_date
from .prompts import (CLARIFY_TOOL, FALLBACK_LABEL, INTERPRETATION_TOOL, JUDGEMENT_LABEL, JUDGEMENT_NOTICE,
                      NO_ANSWER_TEXT, PERSON_ONLY_LABEL, PERSON_ONLY_NOTICE, SYSTEM_PROMPT, UNDER_REVIEW_LABEL)

MANUAL_HOME = "https://www.immigration.govt.nz/opsmanual/"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_VERDICT_REQUEST = re.compile(
    r"\b(does|do|is|are|would|will|can|should)\b[^?]{0,80}\b(meet|meets|qualify|qualifies|eligible|genuine|bona fide|pass|passes|satisf(y|ies|ied)|approve|grant|decline)\b",
    re.I)


@dataclass
class Interpretation:
    reading: str = ""
    visa_type: str = "NONE"
    visa_class: str = "NONE"
    date: str | None = None
    date_words: str | None = None
    date_meaning: str = "none"
    topic: str = ""
    date_verified: bool = False
    date_problem: str | None = None

    def display(self) -> str:
        parts = []
        if self.visa_type != "NONE":
            parts.append(VISA_TYPES.get(self.visa_type, self.visa_type))
        if self.date and self.date_verified:
            label = "lodged" if self.date_meaning == "lodgement" else "as at"
            parts.append(f"{label} {dt.date.fromisoformat(self.date).strftime('%-d %B %Y')}")
        if self.topic:
            parts.append(self.topic)
        return ", ".join(parts) or self.reading


@dataclass
class Source:
    ref: str
    verbatimText: str
    url: str | None
    effectiveFrom: str | None
    effectiveTo: str | None
    status: str | None
    reviewReason: str | None
    title: str | None
    officerJudgement: bool
    fromQuery: str


@dataclass
class Answer:
    conversation_id: str
    kind: str  # "answer" | "clarification" | "no_answer" | "error"
    text: str
    interpretation: dict
    interpretation_display: str
    sources: list[dict] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)
    manual_as_at: str | None = None
    manual_link: str = MANUAL_HOME


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict] = field(default_factory=list)
    user_texts: list[str] = field(default_factory=list)
    interpretation: Interpretation = field(default_factory=Interpretation)


class Assistant:
    def __init__(self, driver: Driver, client: Any = None, settings: Settings | None = None,
                 log: ChatLog | None = None, today: dt.date | None = None):
        self.driver = driver
        self.settings = settings or get_settings()
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.log = log or ChatLog(self.settings.chat_log_dir)
        self._today = today
        self.tools = self._tool_definitions()

    @property
    def today(self) -> dt.date:
        return self._today or dt.date.today()

    # -- tools --------------------------------------------------------------------
    def _tool_definitions(self) -> list[dict]:
        tools = [INTERPRETATION_TOOL, CLARIFY_TOOL]
        for q in QUERIES.values():
            tools.append({"name": q.id, "description": f"{q.title}. {q.description}", "strict": True,
                          "input_schema": q.tool_schema()})
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    def _manual_as_at(self) -> str | None:
        try:
            from ..db import run_read

            rows = run_read(self.driver, "MATCH (r:LoaderRun) RETURN r.snapshotRetrieved AS at ORDER BY r.startedAt DESC LIMIT 1", {})
            return rows[0]["at"] if rows else None
        except Exception:  # noqa: BLE001 - the date is informative only
            return None

    # -- one turn -----------------------------------------------------------------
    def ask(self, conv: Conversation, message: str, staff_id: str = "unknown") -> Answer:
        conv.user_texts.append(message)
        last = conv.messages[-1] if conv.messages else None
        if last and last["role"] == "user" and isinstance(last["content"], list):
            # The previous turn ended on a clarifying question: its tool results have not
            # been sent yet, so the officer's reply joins that same user message.
            last["content"] = list(last["content"]) + [{"type": "text", "text": message}]
        else:
            conv.messages.append({"role": "user", "content": message})
        trace: list[dict] = []
        results: list[tuple[str, dict, list[dict]]] = []
        clarification: str | None = None
        final_text = ""
        model_used = None
        stop_reason = None
        usage: dict[str, int] = {}

        for _round in range(self.settings.max_tool_rounds + 1):
            response = self.client.beta.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=16000,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=self.tools,
                messages=conv.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.settings.anthropic_effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            model_used = getattr(response, "model", None)
            stop_reason = response.stop_reason
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
                usage[k] = usage.get(k, 0) + (getattr(response.usage, k, 0) or 0)
            conv.messages.append({"role": "assistant", "content": response.content})

            if stop_reason == "refusal":
                final_text = "The assistant could not answer this question. Please check the manual directly."
                break
            if stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                final_text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text").strip()
                break

            tool_results = []
            for block in tool_uses:
                content, is_error, ends_turn = self._run_tool(conv, block.name, dict(block.input), trace, results)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": content, "is_error": is_error})
                if ends_turn:
                    clarification = dict(block.input).get("question")
            conv.messages.append({"role": "user", "content": tool_results})
            if clarification:
                # Close the loop cleanly: the API needs the tool results recorded, and the
                # next officer message continues the conversation.
                break
        else:
            final_text = "The assistant ran out of steps before answering. Please rephrase or check the manual directly."

        answer = self._compose(conv, final_text, clarification, results, message)
        self.log.append({
            "type": "chat_turn", "staffId": staff_id, "conversationId": conv.id, "question": message,
            "interpretation": asdict(conv.interpretation), "toolCalls": trace,
            "ruleVersions": sorted({s["ref"] + "@" + str(s.get("effectiveFrom")) for s in answer.sources}),
            "answerKind": answer.kind, "answerText": answer.text, "labels": answer.labels,
            "notices": answer.notices, "model": model_used, "stopReason": stop_reason, "usage": usage,
            "manualAsAt": answer.manual_as_at,
        })
        return answer

    def _run_tool(self, conv: Conversation, name: str, args: dict, trace: list, results: list) -> tuple[str, bool, bool]:
        entry: dict[str, Any] = {"tool": name, "input": args}
        trace.append(entry)
        if name == "record_interpretation":
            interp = Interpretation(**{k: args.get(k) for k in ("reading", "visa_type", "visa_class", "date", "date_words", "date_meaning", "topic")})
            if interp.date:
                ok, why = check_user_date(interp.date_words, interp.date, conv.user_texts, self.today)
                interp.date_verified, interp.date_problem = ok, (None if ok else why)
            conv.interpretation = interp
            entry["dateVerified"] = interp.date_verified
            if interp.date and not interp.date_verified:
                entry["error"] = interp.date_problem
                return (f"Recorded, but the date was rejected: {interp.date_problem}. "
                        "Ask the officer for the date with ask_clarifying_question."), True, False
            return "Recorded. The officer sees: " + interp.display(), False, False
        if name == "ask_clarifying_question":
            return "Question shown to the officer.", False, True
        if name not in QUERIES:
            entry["error"] = "not an approved query"
            return f"{name} is not an approved query.", True, False
        if not conv.interpretation.reading:
            entry["error"] = "no interpretation"
            return "Call record_interpretation first.", True, False

        query = QUERIES[name]
        # Dates that set which rules apply must be ones the officer gave.
        for p in query.lodgement_params:
            value = args.get(p)
            if value is None:
                continue
            interp = conv.interpretation
            if not (interp.date_verified and interp.date == value):
                entry["error"] = f"unverified date for {p}"
                return (f"{p}={value} is not a date the officer gave. Ask the officer for the lodgement or "
                        "'as at' date with ask_clarifying_question. Do not assume today's date."), True, False
        if "visa_type" in args and conv.interpretation.visa_type not in ("NONE", args["visa_type"]):
            entry["error"] = "visa type differs from interpretation"
            return ("visa_type differs from the recorded interpretation. Record a new interpretation first "
                    "if the officer changed the visa type."), True, False
        try:
            params, rows = execute(self.driver, name, args)
        except ValueError as exc:
            entry["error"] = str(exc)
            return f"Invalid values: {exc}", True, False
        except ReadOnlyViolation as exc:  # should be impossible: catalogue queries are read-only
            entry["error"] = str(exc)
            return "That query is not allowed.", True, False
        entry["params"] = {k: v for k, v in params.items() if k != "lucene"}
        entry["rows"] = len(rows)
        entry["returned"] = _version_ids(rows)
        results.append((name, params, rows))
        if not rows:
            return "No results.", False, False
        return json.dumps(rows, default=str, ensure_ascii=False)[:60000], False, False

    # -- answer -------------------------------------------------------------------
    def _compose(self, conv: Conversation, text: str, clarification: str | None,
                 results: list[tuple[str, dict, list[dict]]], question: str) -> Answer:
        interp = conv.interpretation
        base = dict(conversation_id=conv.id, interpretation=asdict(interp),
                    interpretation_display=interp.display(), manual_as_at=self._manual_as_at())
        queries = [{"id": n, "params": {k: v for k, v in p.items() if k != "lucene"}, "rows": len(r),
                    "source": QUERIES[n].source} for n, p, r in results]
        if clarification:
            return Answer(kind="clarification", text=clarification, queries=queries, **base)

        sources = _collect_sources(results)
        answered = [q for q in queries if q["rows"]]
        if not answered:
            return Answer(kind="no_answer", text=NO_ANSWER_TEXT, queries=queries, **base)

        labels, notices = [], []
        if all(q["source"] == "manual_text" for q in answered):
            labels.append(FALLBACK_LABEL)
        if any(s.officerJudgement for s in sources):
            labels.append(JUDGEMENT_LABEL)
            notices.append(JUDGEMENT_NOTICE)
        if any(_has_tier3(r) for _, _, r in results):
            labels.append(PERSON_ONLY_LABEL)
            notices.append(PERSON_ONLY_NOTICE)
        if any(s.status == "under_review" for s in sources):
            labels.append(UNDER_REVIEW_LABEL)
        if _VERDICT_REQUEST.search(question) and JUDGEMENT_LABEL in labels:
            notices.insert(0, "The assistant can't say whether a particular case meets a judgement rule. That decision is yours.")
        return Answer(kind="answer", text=text or "See the manual wording below.", sources=[asdict(s) for s in sources],
                      labels=labels, notices=notices, queries=queries, **base)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def _version_ids(rows: list[dict]) -> list[str]:
    out = set()
    for d in _walk(rows):
        if d.get("ref") and (d.get("pageId") or d.get("effectiveFrom")):
            out.add(f"{d['ref']}@{d.get('pageId') or d.get('effectiveFrom')}")
    return sorted(out)


def _has_tier3(rows: list[dict]) -> bool:
    return any(d.get("tier") == 3 for d in _walk(rows))


def _collect_sources(results: list[tuple[str, dict, list[dict]]]) -> list[Source]:
    seen: set[tuple] = set()
    out: list[Source] = []
    for name, _params, rows in results:
        for row in rows:
            judgement = bool(row.get("officerJudgement")) if isinstance(row, dict) else False
            title = row.get("title") if isinstance(row, dict) else None
            for d in _walk(row):
                text = d.get("verbatimText")
                if not text or not d.get("ref"):
                    continue
                key = (d["ref"], d.get("pageId"), text)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Source(
                    ref=d["ref"], verbatimText=text, url=d.get("url"),
                    effectiveFrom=d.get("effectiveFrom"), effectiveTo=d.get("effectiveTo"),
                    status=d.get("status"), reviewReason=d.get("reviewReason"),
                    title=d.get("test") or title or d.get("instruction") or d.get("heading"),
                    officerJudgement=judgement or bool(d.get("officerJudgement")), fromQuery=name,
                ))
    return out
