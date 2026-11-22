"""Static checks on the approved query library (no database needed)."""

import re

import pytest

from inzkg.queries import QUERIES
from inzkg.queries.catalogue import lucene_terms

WRITE_WORDS = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|CALL\s+\{|FOREACH)\b", re.I)


def test_library_size():
    assert 10 <= len(QUERIES) <= 20


@pytest.mark.parametrize("qid", sorted(QUERIES))
def test_no_write_clauses(qid):
    cypher = re.sub(r"'[^']*'|\"[^\"]*\"", "''", QUERIES[qid].cypher)
    assert not WRITE_WORDS.search(cypher), qid


@pytest.mark.parametrize("qid", sorted(QUERIES))
def test_tool_schema_is_strict(qid):
    q = QUERIES[qid]
    s = q.tool_schema()
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert set(s["required"]) <= set(s["properties"])
    for p in q.params:
        assert f"${p.name}" in q.cypher or p.name == "text", f"{qid}: ${p.name} unused"


@pytest.mark.parametrize("qid", sorted(QUERIES))
def test_dates_are_never_defaulted(qid):
    for p in QUERIES[qid].params:
        if p.type == "date" and p.name in ("lodged_on", "as_at"):
            assert p.lodgement_date and p.required, f"{qid}.{p.name}"


def test_validation_rejects_bad_values():
    q = QUERIES["visa_requirements"]
    with pytest.raises(ValueError):
        q.validate({"visa_type": "WORKING_HOLIDAY", "lodged_on": "2026-09-01"})
    with pytest.raises(ValueError):
        q.validate({"visa_type": "VISITOR", "lodged_on": "1 Sept"})
    with pytest.raises(ValueError):
        q.validate({"visa_type": "VISITOR", "lodged_on": "2026-09-01", "cypher": "MATCH (n) DETACH DELETE n"})
    with pytest.raises(ValueError):
        q.validate({"visa_type": "VISITOR"})
    assert q.validate({"visa_type": "VISITOR", "lodged_on": "2026-09-01"}) == {"visa_type": "VISITOR", "lodged_on": "2026-09-01"}


def test_lucene_terms_are_escaped():
    t = lucene_terms('funds OR *:* AND "x" (y)')
    assert "*" not in t.replace("\\*", "") and ":" not in t.replace("\\:", "")
