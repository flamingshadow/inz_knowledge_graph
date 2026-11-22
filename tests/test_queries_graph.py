"""Run every approved query, and the golden questions, against the loaded graph."""

from pathlib import Path

import pytest
import yaml

from inzkg.chat.assistant import _walk
from inzkg.db import ReadOnlyViolation, run_read
from inzkg.queries import QUERIES, execute

pytestmark = pytest.mark.neo4j
GOLDEN = yaml.safe_load((Path(__file__).parent / "golden" / "questions.yaml").read_text())


def _refs(rows):
    return {d["ref"] for d in _walk(rows) if isinstance(d.get("ref"), str)}


@pytest.mark.parametrize("case", GOLDEN, ids=[c["question"][:60] for c in GOLDEN])
def test_golden(driver, case):
    _, rows = execute(driver, case["query"], case["args"])
    assert len(rows) >= case.get("min_rows", 1)
    refs = _refs(rows)
    assert set(case.get("expect_refs", [])) <= refs, refs
    assert not set(case.get("forbid_refs", [])) & refs, refs
    for k, v in (case.get("expect") or {}).items():
        assert rows[0][k] == v


def test_golden_covers_most_queries():
    assert len({c["query"] for c in GOLDEN}) >= 10


@pytest.mark.parametrize("qid", sorted(QUERIES))
def test_every_query_plans_as_read_only(driver, qid):
    q = QUERIES[qid]
    with driver.session() as s:
        args = {p.name: None for p in q.params}
        args.update({"lucene": "visa", "d": None})
        summary = s.run("EXPLAIN " + q.cypher, args).consume()
    assert summary.query_type == "r"


def test_write_query_is_refused(driver):
    with pytest.raises(ReadOnlyViolation):
        run_read(driver, "CREATE (n:Hack) RETURN n", {})
    assert run_read(driver, "MATCH (n:Hack) RETURN count(n) AS n", {})[0]["n"] == 0


def test_citations_carry_wording_and_dates(driver):
    _, rows = execute(driver, "visa_requirements", {"visa_type": "VISITOR", "lodged_on": "2026-09-01"})
    cites = [d for d in _walk(rows) if d.get("verbatimText") and d.get("ref")]
    assert cites
    for c in cites:
        assert c["url"].startswith("https://www.immigration.govt.nz/opsmanual/")
        assert "effectiveFrom" in c and c["status"] in ("approved", "under_review")


def test_quotes_link_to_text_index(driver):
    n = run_read(driver, "MATCH (:Rule)-[:QUOTES]->(p:Passage) RETURN count(DISTINCT p) AS n", {})[0]["n"]
    assert n > 50
