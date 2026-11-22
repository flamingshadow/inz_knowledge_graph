import datetime as dt
import json

from inzkg.chat.audit_log import ChatLog, verify
from inzkg.chat.dates import check_user_date, parse_date_words

TODAY = dt.date(2026, 11, 22)


def test_parse_nz_order():
    assert parse_date_words("1/9/2026") == dt.date(2026, 9, 1)
    assert parse_date_words("lodged 1 September 2026") == dt.date(2026, 9, 1)
    assert parse_date_words("September 1, 2026") == dt.date(2026, 9, 1)
    assert parse_date_words("2026-09-01") == dt.date(2026, 9, 1)
    assert parse_date_words("today", TODAY) == TODAY
    assert parse_date_words("September 2026") is None  # no day: ask, don't guess


def test_date_must_be_in_officers_words():
    msgs = ["What are the visitor requirements for an application lodged 1 Sept 2026?"]
    assert check_user_date("1 Sept 2026", "2026-09-01", msgs, TODAY)[0]
    ok, why = check_user_date("1 Oct 2026", "2026-10-01", msgs, TODAY)
    assert not ok and "did not write" in why
    ok, why = check_user_date("1 Sept 2026", "2026-11-22", msgs, TODAY)
    assert not ok and "means 2026-09-01" in why
    assert not check_user_date(None, "2026-09-01", msgs, TODAY)[0]


def test_today_only_when_officer_says_so():
    msgs = ["What are the visitor requirements?"]
    assert not check_user_date("today", TODAY.isoformat(), msgs, TODAY)[0]
    msgs = ["What are the visitor requirements today?"]
    assert check_user_date("today", TODAY.isoformat(), msgs, TODAY)[0]


def test_log_chain_detects_tampering(tmp_path):
    log = ChatLog(tmp_path)
    for i in range(3):
        log.append({"type": "chat_turn", "question": f"q{i}"})
    path = next(tmp_path.glob("chat-*.jsonl"))
    assert verify(path) == (True, 3, "ok")

    lines = path.read_text().splitlines()
    rec = json.loads(lines[1]); rec["question"] = "edited"
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    ok, n, msg = verify(path)
    assert not ok and n == 2 and "changed" in msg

    path.write_text("\n".join([lines[0], lines[2]]) + "\n")  # a record removed
    ok, _, msg = verify(path)
    assert not ok and "chain broken" in msg
