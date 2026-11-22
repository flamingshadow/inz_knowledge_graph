"""Check that a date the model uses is one the officer actually gave.

The model reports the officer's own words for a date ("lodged 1 May 2026"). This module
parses those words. The assistant only accepts a date when (a) the words appear in what
the officer typed and (b) they parse to the same date. That way a missing lodgement date
can never be silently filled in with today or a guess.
"""

from __future__ import annotations

import datetime as dt
import re

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

TODAY_WORDS = {"today", "currently", "at present", "right now", "as of today"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def words_in_messages(words: str, messages: list[str]) -> bool:
    needle = _norm(words).strip(" .,")
    return bool(needle) and any(needle in _norm(m) for m in messages)


def parse_date_words(words: str, today: dt.date | None = None) -> dt.date | None:
    """Parse the officer's words for a date. New Zealand order (day before month)."""
    today = today or dt.date.today()
    w = _norm(words).strip(" .,")
    if w in TODAY_WORDS or any(re.fullmatch(rf".*\b{re.escape(t)}\b.*", w) for t in TODAY_WORDS):
        return today
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", w)
    if m:
        return _safe(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b", w)
    if m:
        year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return _safe(year, int(m[2]), int(m[1]))
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?(?: of)? ([a-z]+),? (\d{4})\b", w)
    if m and m[2] in MONTHS:
        return _safe(int(m[3]), MONTHS[m[2]], int(m[1]))
    m = re.search(r"\b([a-z]+) (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})\b", w)
    if m and m[1] in MONTHS:
        return _safe(int(m[3]), MONTHS[m[1]], int(m[2]))
    return None


def _safe(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def check_user_date(words: str | None, claimed: str | None, messages: list[str],
                    today: dt.date | None = None) -> tuple[bool, str]:
    """Return (ok, reason). ``claimed`` is the ISO date the model wants to use."""
    if not claimed:
        return False, "no date given"
    if not words:
        return False, "the officer's own words for the date are missing"
    if not words_in_messages(words, messages):
        return False, f"the officer did not write {words!r}"
    parsed = parse_date_words(words, today)
    if parsed is None:
        return False, f"could not read a full date (day, month and year) from {words!r}"
    if parsed.isoformat() != claimed:
        return False, f"{words!r} means {parsed.isoformat()}, not {claimed}"
    return True, "ok"
