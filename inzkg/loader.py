"""Load a manual snapshot and the curated rulebook into Neo4j.

The loader is the only code that writes to the graph. Each run:

1. applies constraints and indexes (``schema.cypher``);
2. upserts every manual page version as a ``ManualSection`` with its effective dates,
   grouped under an ``Instruction`` (one per code) and chained by ``SUPERSEDES``;
3. upserts ``Passage`` nodes, the full-text index of the manual;
4. upserts the rulebook, after checking every quote against the manual;
5. links each rule to the passage that holds its quote (``QUOTES``);
6. appends a ``LoaderRun`` record, and a ``RuleChange`` record for each rule that is new,
   changed or retired. These two are never edited or deleted.
"""

from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from neo4j import Driver

from .config import Settings, get_settings
from .manual.parse import Snapshot, load_snapshot, normalise_text
from .rules import RuleBook, build_rulebook

BATCH = 500


def _batches(items: list, size: int = BATCH) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def apply_schema(session) -> None:
    text = (Path(__file__).parent / "schema.cypher").read_text()
    for stmt in [s.strip() for s in text.split(";")]:
        lines = [l for l in stmt.splitlines() if not l.strip().startswith("//")]
        stmt = "\n".join(lines).strip()
        if stmt:
            session.run(stmt).consume()


def _section_rows(snapshot: Snapshot) -> list[dict]:
    rows = []
    for s in snapshot.sections:
        rows.append({
            "pageId": s.page_id, "ref": s.ref, "title": s.title, "url": s.url, "chapter": s.chapter,
            "path": s.path, "isCurrent": s.is_current, "effectiveFrom": s.effective_from,
            "effectiveTo": s.effective_to, "isSection22Instruction": s.is_section22, "inScope": s.in_scope,
            "text": s.text, "textHash": s.text_hash, "hasText": bool(s.text),
            "parentRef": s.parent_ref, "previousPageId": s.previous_page_id,
        })
    return rows


def _passage_rows(snapshot: Snapshot) -> list[dict]:
    rows = []
    for s in snapshot.sections:
        for p in s.passages:
            rows.append({
                "id": p.id, "pageId": s.page_id, "ref": p.ref, "heading": p.heading, "text": p.text,
                "order": p.order, "title": s.title, "chapter": s.chapter, "isCurrent": s.is_current,
                "effectiveFrom": s.effective_from, "effectiveTo": s.effective_to, "url": s.url,
            })
    return rows


def _quote_passages(snapshot: Snapshot, book: RuleBook) -> list[dict]:
    """Find the passage that holds each rule's quote, so text-search hits lead to rules."""
    by_page = {s.page_id: s for s in snapshot.sections}
    links = []
    for label in ("Criterion", "Condition", "Factor", "ReferenceValue", "EntryRefusalGround",
                  "LiabilityGround", "AppealRight", "NoAppealRule", "ProhibitionRule",
                  "LiabilityPeriodRule", "Power", "EntryPermission", "Tribunal"):
        for node in book.nodes.get(label, []):
            page = by_page.get(node.get("pageId"))
            if not page or not node.get("verbatimText"):
                continue
            quote = node["verbatimText"]
            head = quote[:80]
            best = None
            for p in page.passages:
                body = normalise_text(p.text)
                if quote in body:
                    best = p
                    break
                if best is None and head in body:
                    best = p
            if best:
                links.append({"label": label, "id": node.get("id") or node.get("code"), "passageId": best.id})
    return links


def _rule_hash(props: dict) -> str:
    keep = {k: v for k, v in props.items() if k not in ("status", "reviewReason")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


RULE_LABELS = ["LegalSource", "VisaClass", "VisaCategory", "VisaType", "CriterionKey", "Criterion",
               "CriterionGroup", "EvidenceType", "Factor", "Condition", "ReferenceList", "ReferenceValue",
               "EntryPermission", "EntryRefusalGround", "Tribunal", "LiabilityGround", "LiabilityPeriodRule",
               "AppealRight", "NoAppealRule", "ProhibitionRule", "Power"]

KEY_OF = {"VisaClass": "code", "VisaCategory": "code", "VisaType": "code"}


def load(driver: Driver, snapshot: Snapshot, book: RuleBook, settings: Settings | None = None,
         loaded_by: str | None = None) -> dict:
    settings = settings or get_settings()
    run_id = str(uuid.uuid4())
    started = dt.datetime.now(dt.timezone.utc)
    sections = _section_rows(snapshot)
    passages = _passage_rows(snapshot)

    with driver.session(database=settings.neo4j_database) as session:
        apply_schema(session)

        previous = {r["pageId"]: r["textHash"] for r in session.run(
            "MATCH (s:ManualSection) RETURN s.pageId AS pageId, s.textHash AS textHash")}
        changed_pages = [r["pageId"] for r in sections if r["pageId"] in previous and previous[r["pageId"]] != r["textHash"]]
        new_pages = [r["pageId"] for r in sections if r["pageId"] not in previous]

        for batch in _batches(sections):
            session.run(
                """
                UNWIND $rows AS r
                MERGE (s:ManualSection {pageId: r.pageId})
                SET s += r {.ref, .title, .url, .chapter, .path, .isCurrent, .effectiveFrom, .effectiveTo,
                            .isSection22Instruction, .inScope, .text, .textHash, .hasText}
                WITH s, r WHERE r.ref IS NOT NULL
                MERGE (i:Instruction {ref: r.ref})
                ON CREATE SET i.title = r.title
                SET i.title = CASE WHEN r.isCurrent THEN r.title ELSE coalesce(i.title, r.title) END,
                    i.chapter = r.chapter
                MERGE (i)-[:HAS_VERSION]->(s)
                """, rows=batch).consume()
        for batch in _batches(sections):
            session.run(
                """
                UNWIND $rows AS r
                WITH r WHERE r.previousPageId IS NOT NULL
                MATCH (s:ManualSection {pageId: r.pageId}), (p:ManualSection {pageId: r.previousPageId})
                MERGE (s)-[:SUPERSEDES]->(p)
                """, rows=batch).consume()
            session.run(
                """
                UNWIND $rows AS r
                WITH r WHERE r.parentRef IS NOT NULL AND r.ref IS NOT NULL AND r.parentRef <> r.ref
                MATCH (i:Instruction {ref: r.ref}), (p:Instruction {ref: r.parentRef})
                MERGE (i)-[:PART_OF]->(p)
                """, rows=batch).consume()
        for batch in _batches(passages):
            session.run(
                """
                UNWIND $rows AS r
                MERGE (p:Passage {id: r.id})
                SET p += r {.ref, .heading, .text, .order, .title, .chapter, .isCurrent, .effectiveFrom, .effectiveTo, .url, .pageId}
                WITH p, r
                MATCH (s:ManualSection {pageId: r.pageId})
                MERGE (p)-[:PART_OF]->(s)
                """, rows=batch).consume()

        # --- rulebook ------------------------------------------------------------
        before = {(r["label"], r["key"]): r for r in session.run(
            """
            MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) AND n.ruleHash IS NOT NULL
            RETURN [l IN labels(n) WHERE l <> "Rule"][0] AS label, coalesce(n.id, n.code) AS key, n.ruleHash AS hash
            """, labels=RULE_LABELS)}
        changes = []
        seen = set()
        for label in RULE_LABELS:
            items = book.nodes.get(label, [])
            key = KEY_OF.get(label, "id")
            rows = []
            for props in items:
                props = dict(props)
                props["ruleHash"] = _rule_hash(props)
                k = props[key]
                seen.add((label, k))
                old = before.get((label, k))
                if old is None:
                    changes.append({"label": label, "key": k, "change": "added", "oldHash": None, "newHash": props["ruleHash"]})
                elif old["hash"] != props["ruleHash"]:
                    changes.append({"label": label, "key": k, "change": "changed", "oldHash": old["hash"], "newHash": props["ruleHash"]})
                rows.append(props)
            for batch in _batches(rows):
                session.run(
                    f"UNWIND $rows AS r MERGE (n:{label} {{{key}: r.{key}}}) SET n += r, n:Rule, n.loaderRunId = $run",
                    rows=batch, run=run_id).consume()
        for (_, k), old in before.items():
            if (old["label"], k) not in seen:
                changes.append({"label": old["label"], "key": k, "change": "retired", "oldHash": old["hash"], "newHash": None})
                session.run("MATCH (n {ruleHash: $h}) WHERE coalesce(n.id, n.code) = $k SET n.status = 'retired'",
                            h=old["hash"], k=k).consume()

        # Relationships: clear the rulebook's own edges and rebuild them from the YAML,
        # so a removed edge does not linger. Manual and log nodes are untouched.
        session.run(
            """
            MATCH (a:Rule)-[r]->(b) WHERE type(r) IN $types DELETE r
            """, types=sorted({r[2] for r in book.rels} | {"QUOTES"})).consume()
        rel_rows: dict[tuple, list] = {}
        for a_label, a_id, rtype, b_label, b_id, props in book.rels:
            rel_rows.setdefault((a_label, rtype, b_label), []).append({"a": a_id, "b": b_id, "props": props})
        for (a_label, rtype, b_label), rows in rel_rows.items():
            a_key = KEY_OF.get(a_label, "id")
            if b_label == "*":
                match_b = "MATCH (b) WHERE (b:CriterionKey OR b:CriterionGroup) AND b.id = r.b"
            elif b_label == "Instruction":
                match_b = "MATCH (b:Instruction {ref: r.b})"
            elif b_label == "ManualSection":
                match_b = "MATCH (b:ManualSection {pageId: r.b})"
            elif b_label == "Criterion" and rtype == "USES":
                match_b = "MATCH (b:CriterionKey {id: r.b})"
            else:
                match_b = f"MATCH (b:{b_label} {{{KEY_OF.get(b_label, 'id')}: r.b}})"
            for batch in _batches(rows):
                res = session.run(
                    f"""
                    UNWIND $rows AS r
                    MATCH (a:{a_label} {{{a_key}: r.a}})
                    {match_b}
                    MERGE (a)-[x:{rtype}]->(b)
                    SET x += r.props
                    RETURN count(x) AS n
                    """, rows=batch).single()
                if res["n"] != len(batch):
                    raise RuntimeError(f"{a_label}-{rtype}->{b_label}: linked {res['n']} of {len(batch)}")

        quote_links = _quote_passages(snapshot, book)
        for label in {l["label"] for l in quote_links}:
            rows = [l for l in quote_links if l["label"] == label]
            key = KEY_OF.get(label, "id")
            session.run(
                f"""
                UNWIND $rows AS r
                MATCH (n:{label} {{{key}: r.id}}), (p:Passage {{id: r.passageId}})
                MERGE (n)-[:QUOTES]->(p)
                """, rows=rows).consume()

        # Section 22 certified instructions are issued under s 22 of the Act.
        session.run(
            """
            MATCH (l:LegalSource {id: 'IA2009-s22'})
            MATCH (s:ManualSection) WHERE s.isSection22Instruction AND s.ref IS NOT NULL
            MERGE (s)-[:ISSUED_UNDER]->(l)
            """).consume()

        # --- append-only logs ----------------------------------------------------
        finished = dt.datetime.now(dt.timezone.utc)
        run = {
            "id": run_id, "startedAt": started.isoformat(), "finishedAt": finished.isoformat(),
            "loadedBy": loaded_by or getpass.getuser(), "snapshotRetrieved": snapshot.retrieved,
            "snapshotSource": snapshot.source, "manualIssueDate": snapshot.issue_date, "sections": len(sections), "passages": len(passages),
            "newSections": len(new_pages), "changedSections": len(changed_pages),
            "changedPageIds": changed_pages[:500], "rulesHash": book.content_hash(),
            "ruleChanges": len(changes), "issues": [f"{i.rule_id}: {i.message}" for i in book.issues],
            "rulesUnderReview": sum(1 for l in book.nodes.values() for n in l if n.get("status") == "under_review"),
        }
        session.run("CREATE (r:LoaderRun) SET r = $run", run=run).consume()
        if changes:
            session.run(
                """
                MATCH (run:LoaderRun {id: $run})
                UNWIND $changes AS c
                CREATE (x:RuleChange {id: $run + ':' + c.label + ':' + c.key})
                SET x += c, x.at = $at, x.loadedBy = $by, x.approvedBy = null
                CREATE (run)-[:RECORDED]->(x)
                """, run=run_id, changes=changes, at=finished.isoformat(), by=run["loadedBy"]).consume()
    return run


def load_from_settings(settings: Settings | None = None, loaded_by: str | None = None) -> dict:
    from .db import writer_driver

    settings = settings or get_settings()
    snapshot = load_snapshot(settings.snapshot_path)
    book = build_rulebook(snapshot, settings.rules_dir)
    driver = writer_driver(settings)
    try:
        return load(driver, snapshot, book, settings, loaded_by)
    finally:
        driver.close()
