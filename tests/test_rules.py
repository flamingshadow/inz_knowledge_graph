import datetime as dt

import pytest
import yaml

from inzkg.rules import build_rulebook, cite


def test_every_quote_matches_the_manual(rulebook):
    assert rulebook.issues == []
    assert sum(len(v) for v in rulebook.nodes.values()) > 100


def test_cite_finds_exact_wording(snapshot):
    sec = snapshot.current("A21.1") or snapshot.current("E4.1")
    quote = sec.text[200:260] if len(sec.text) > 260 else sec.text[:40]
    c = cite(snapshot, sec.ref, quote)
    assert c.found


def test_cite_rejects_wording_not_in_manual(snapshot):
    c = cite(snapshot, "E4.1", "Applicants may lodge on a napkin.")
    assert not c.found


def test_bad_quote_puts_rule_under_review(snapshot, tmp_path):
    GOOD = snapshot.current("E4.1").text[:60]
    rules = {
        "stewardship": {"prepared_by": "a", "approved_by": "b"},
        "groups": [{"id": "T-ROOT", "title": "Test", "operator": "ALL", "members": ["T-BAD", "T-GOOD"]}],
        "criteria": [
            {"id": "T-BAD", "title": "Bad quote", "kind": "evidential", "ref": "E4.1",
             "quote": "Applicants may lodge on a napkin."},
            {"id": "T-GOOD", "title": "Good quote", "kind": "evidential", "ref": "E4.1",
             "quote": GOOD},
        ],
    }
    (tmp_path / "t.yaml").write_text(yaml.safe_dump(rules))
    book = build_rulebook(snapshot, tmp_path)
    assert any(i.rule_id.startswith("T-BAD") for i in book.issues)
    crit = [n for n in book.nodes.get("Criterion", []) if n["id"].startswith("T-BAD")]
    assert crit and all(n["status"] == "under_review" for n in crit)
    good = [n for n in book.nodes["Criterion"] if n["id"].startswith("T-GOOD")]
    assert good and good[0]["status"] == "approved"  # prepared and approved by different people


def test_no_rule_is_approved_without_a_second_person(rulebook):
    for label, nodes in rulebook.nodes.items():
        for n in nodes:
            if "status" in n:
                assert n["status"] in ("approved", "under_review", "retired")


def test_judgement_rules_are_flagged(rulebook):
    bonafide = [n for n in rulebook.nodes["Criterion"] if n["id"].startswith("GEN-TEMP-BONAFIDE")]
    assert bonafide and all(n["officerJudgement"] for n in bonafide)
