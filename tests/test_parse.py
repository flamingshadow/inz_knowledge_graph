import datetime as dt

from inzkg.manual.parse import build_snapshot, normalise_text, ref_of, split_passages

D = dt.date


def _raw():
    return {
        "retrieved": "2026-11-22T00:00:00Z", "source": "test", "issueDate": "06 November 2026",
        "toc": [{"text": "Temporary entry class visa", "url": "1.htm", "o": 1, "children": [
            {"text": "E4.1 Lodging applications", "url": "10.htm", "o": 10},
            {"text": "E4.1 Lodging applications (01/02/2025)", "url": "11.htm", "o": 11, "hidden": True},
        ]}],
        "pages": [
            {"url": "1.htm", "text": "Temporary entry class visa"},
            {"url": "10.htm", "text": "E4.1 Lodging applications  a. Applications must be made on the form. Effective 01/06/2026"},
            {"url": "11.htm", "text": "E4.1 Lodging applications  a. Old wording here. Effective 01/02/2025"},
        ],
    }


def test_versions_and_effective_dates():
    snap = build_snapshot(_raw())
    versions = snap.by_ref("E4.1")
    assert len(versions) == 2
    current = snap.current("E4.1")
    assert current.effective_from == D(2026, 6, 1) and current.effective_to is None
    old = [v for v in versions if not v.is_current][0]
    assert old.effective_from == D(2025, 2, 1)
    assert old.effective_to == D(2026, 6, 1)  # exclusive end = start of the next version
    assert current.previous_page_id == old.page_id
    assert snap.in_force("E4.1", D(2026, 5, 31)).page_id == old.page_id
    assert snap.in_force("E4.1", D(2026, 6, 1)).page_id == current.page_id
    assert snap.in_force("E4.1", D(2020, 1, 1)) is None


def test_normalise_and_refs():
    assert normalise_text("It&#39;s “quoted”<br/>  and   spaced") == 'It\'s "quoted" and spaced'
    assert ref_of("E4.1 Lodging applications") == "E4.1"
    assert ref_of("Appendix 6 Something") == "Appendix 6"


def test_split_passages_keeps_codes():
    ps = split_passages("10", "E4.1", "E4.1 Title  E4.1.1 Heading  Body text one. E4.1.2 Other  Body two.")
    refs = [p.ref for p in ps]
    assert "E4.1.1" in refs and "E4.1.2" in refs
    assert all(len(p.text) <= 1800 for p in ps)


def test_real_snapshot_has_versions(snapshot):
    assert len(snapshot.sections) > 5000
    refugee = [s for s in snapshot.sections if s.chapter == "Refugees and protection"]
    # Out of scope: kept as contents headings only, with no text and flagged.
    assert all(not s.in_scope and not s.text for s in refugee)
    smc = snapshot.by_ref("SR3.15")
    assert smc and snapshot.current("SR3.15") is not None
