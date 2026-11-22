"""Turn a snapshot of the public INZ Operational Manual into versioned sections and passages.

A snapshot is the gzipped JSON written by ``inzkg.manual.fetch`` (or by the browser export
in ``scripts/browser_export.js``). It holds the manual's contents file (``toc``) and the
full text of each page (``pages``), exactly as the manual's own viewer serves them.

The contents file lists every instruction. Earlier versions of an instruction sit under
it as hidden entries titled with a date, for example ``V2.20 Funds or sponsorship
requirements (25/08/2014)``. Each page's text ends with ``Effective dd/mm/yyyy``.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MANUAL_BASE_URL = "https://www.immigration.govt.nz/opsmanual/"

# Chapters that "About this manual" describes as internal operational instructions,
# rather than Government immigration instructions certified under section 22.
INTERNAL_CHAPTERS = {
    "Administration",
    "Border Entry",
    "Compliance",
    "Refugees and protection",
    "Appendices",
}

# Phase one scope (schema v1, "Decisions made"). Refugees and Protection is out.
OUT_OF_SCOPE_CHAPTERS = {"Refugees and protection"}

_REF_RE = re.compile(r"^((?:[A-Z]{1,3}\d+(?:\.\d+)*)|(?:Appendix \d+[A-Z]?))\b")
_TITLE_DATE_RE = re.compile(r"\s*\((\d{1,2}/\d{1,2}/\d{4})\)\s*$")
_TITLE_TO_RE = re.compile(r"\s*\(to (\d{1,2}/\d{1,2}/\d{4})\)\s*$")
_EFFECTIVE_RE = re.compile(r"Effective\s+(\d{1,2}/\d{1,2}/\d{4})\s*$")
_WS_RE = re.compile(r"\s+")


def parse_date(text: str) -> dt.date:
    day, month, year = (int(x) for x in text.split("/"))
    return dt.date(year, month, day)


def normalise_text(text: str) -> str:
    """Normalise manual text for display and for verbatim-quote matching."""
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace(" ", " ").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return _WS_RE.sub(" ", text).strip()


def ref_of(title: str) -> str | None:
    m = _REF_RE.match(title.strip())
    return m.group(1) if m else None


@dataclass
class Passage:
    id: str
    ref: str  # the sub-instruction code the passage starts with, e.g. V2.20.5
    heading: str
    text: str
    order: int


@dataclass
class SectionVersion:
    """One version of one manual page (an instruction, chapter or appendix)."""

    page_id: str  # e.g. "43000" (from 43000.htm); stable id of this version
    ref: str | None  # e.g. "V2.20"; None for untitled pages
    title: str  # title without the version date
    url: str
    chapter: str  # top-level part of the manual, e.g. "Temporary entry class visa"
    path: list[str]  # contents path above this page
    is_current: bool
    effective_from: dt.date | None
    effective_to: dt.date | None  # exclusive: in force while from <= d < to
    is_section22: bool
    in_scope: bool
    text: str
    text_hash: str
    passages: list[Passage] = field(default_factory=list)
    parent_ref: str | None = None
    previous_page_id: str | None = None  # SUPERSEDES target

    def in_force_on(self, day: dt.date) -> bool:
        if self.effective_from and day < self.effective_from:
            return False
        if self.effective_to and day >= self.effective_to:
            return False
        return True


@dataclass
class Snapshot:
    retrieved: str
    source: str
    sections: list[SectionVersion]
    issue_date: str | None = None  # the manual's own "Issue Date", e.g. "06 November 2026"

    def by_ref(self, ref: str) -> list[SectionVersion]:
        return sorted(
            (s for s in self.sections if s.ref == ref),
            key=lambda s: s.effective_from or dt.date.min,
        )

    def in_force(self, ref: str, day: dt.date) -> SectionVersion | None:
        for s in self.by_ref(ref):
            if s.text and s.in_force_on(day):
                return s
        return None

    def current(self, ref: str) -> SectionVersion | None:
        for s in self.by_ref(ref):
            if s.is_current:
                return s
        return None


def load_snapshot_json(path: str | Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def split_passages(page_id: str, ref: str | None, text: str, max_len: int = 1800) -> list[Passage]:
    # ``text`` is the page text before whitespace is collapsed: double spaces mark the
    # end of a heading.
    """Split a page into passages at its sub-instruction headings (e.g. "V2.20.5 ...").

    Long passages are cut into chunks at sentence boundaries so that a text search hit
    points to a short span the officer can read.
    """
    if not text:
        return []
    text = re.sub(r"<br\s*/?>", "  ", text, flags=re.I).replace("\u00a0", " ")
    pieces: list[tuple[str, str]] = []
    if ref and re.match(r"^[A-Z]{1,3}\d", ref):
        pat = re.compile(r"(?:(?<=\s)|^)(" + re.escape(ref) + r"(?:\.\d+)+)\s+(?=[A-Z'‘\"(])")
        last = 0
        last_ref = ref
        for m in pat.finditer(text):
            if m.start() > last:
                pieces.append((last_ref, text[last : m.start()].strip()))
            last_ref = m.group(1)
            last = m.end()
        pieces.append((last_ref, text[last:].strip()))
    else:
        pieces = [(ref or "", text)]

    passages: list[Passage] = []
    order = 0
    for pref, body in pieces:
        if not body:
            continue
        heading = ""
        if pref != ref:
            # The heading is the run of words after the code, up to the first double space.
            heading = normalise_text(re.split(r"\s{2,}", body)[0])[:160]
        chunks = _chunk(normalise_text(body), max_len)
        for chunk in chunks:
            passages.append(
                Passage(id=f"{page_id}#{order}", ref=pref, heading=heading, text=chunk, order=order)
            )
            order += 1
    return passages


def _chunk(body: str, max_len: int) -> list[str]:
    if len(body) <= max_len:
        return [body]
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.;:])\s+", body):
        if cur and len(cur) + len(sentence) + 1 > max_len:
            out.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        out.append(cur)
    return out


def build_snapshot(raw: dict) -> Snapshot:
    pages = {p["url"]: p for p in raw["pages"]}
    sections: list[SectionVersion] = []
    seen_pages: set[str] = set()

    def visit(nodes: list[dict], path: list[str], parent_ref: str | None):
        # Group this level's entries by instruction code so that dated, hidden entries
        # become earlier versions of the current entry with the same code.
        for node in nodes:
            title_raw = node["text"].strip()
            chapter = path[0] if path else title_raw
            title = _TITLE_DATE_RE.sub("", _TITLE_TO_RE.sub("", title_raw))
            # Some page titles repeat their code ("D3.20 D3.20 ...").
            title = re.sub(r"^(\S+)\s+\1\s+", r"\1 ", title.replace("\u00a0", " "))
            ref = ref_of(title)
            dated = _TITLE_DATE_RE.search(title_raw)
            ended = _TITLE_TO_RE.search(title_raw)
            page = pages.get(node["url"])
            raw_text = html.unescape(page["text"]) if page else ""
            text = normalise_text(raw_text)
            eff = _EFFECTIVE_RE.search(text)
            effective_from = parse_date(eff.group(1)) if eff else (parse_date(dated.group(1)) if dated else None)
            page_id = str(node.get("o") or node["url"].split(".")[0])
            if page_id in seen_pages:
                # The same page is linked from two places in the contents; keep the first.
                if node.get("children"):
                    visit(node["children"], path + [title_raw], ref or parent_ref)
                continue
            seen_pages.add(page_id)
            is_current = not node.get("hidden") and not dated
            sec = SectionVersion(
                page_id=page_id,
                ref=ref,
                title=title,
                url=MANUAL_BASE_URL + "#" + node["url"],
                chapter=chapter,
                path=list(path),
                is_current=is_current and not ended,
                effective_from=effective_from,
                effective_to=parse_date(ended.group(1)) if ended else None,
                is_section22=chapter not in INTERNAL_CHAPTERS and chapter != "About this manual",
                in_scope=chapter not in OUT_OF_SCOPE_CHAPTERS,
                text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
                parent_ref=parent_ref,
            )
            sec.passages = split_passages(page_id, ref, raw_text) if sec.in_scope else []
            sections.append(sec)
            children = node.get("children") or []
            if children:
                visit(children, path + [title_raw], ref or parent_ref)

    visit(raw["toc"], [], None)
    _link_versions(sections)
    return Snapshot(retrieved=raw.get("retrieved", ""), source=raw.get("source", MANUAL_BASE_URL),
                    sections=sections, issue_date=raw.get("issueDate"))


def _link_versions(sections: list[SectionVersion]) -> None:
    """Set effective_to and SUPERSEDES links between versions of the same instruction."""
    by_ref: dict[str, list[SectionVersion]] = {}
    for s in sections:
        if s.ref:
            by_ref.setdefault(s.ref, []).append(s)
    for versions in by_ref.values():
        dated = [v for v in versions if v.effective_from]
        dated.sort(key=lambda v: (v.effective_from, v.is_current))
        # Only chain versions that are really the same instruction: the current page and
        # its dated earlier versions. Distinct pages that share a code (rare) are kept apart
        # by de-duplicating identical start dates.
        chain: list[SectionVersion] = []
        for v in dated:
            if chain and chain[-1].effective_from == v.effective_from:
                continue
            chain.append(v)
        for older, newer in zip(chain, chain[1:]):
            if older.effective_to is None or older.effective_to > newer.effective_from:
                older.effective_to = newer.effective_from
            newer.previous_page_id = older.page_id
            if older.is_current and not newer.is_current:
                # A dated entry newer than the "current" one would be a data error; keep the
                # current flag on the latest version only.
                pass


def load_snapshot(path: str | Path) -> Snapshot:
    return build_snapshot(load_snapshot_json(path))
