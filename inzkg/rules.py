"""Load the curated rulebook (rules/*.yaml) and check every rule against the manual.

Each rule quotes the manual. The quote must appear word for word (after whitespace and
punctuation normalisation) in the version of the cited instruction that the rule is
pinned to: the current version by default, or the version in force on ``as_at``. A rule
whose quote cannot be found is not loaded as approved; it is flagged ``under_review`` so
that officers see it is not trustworthy until a rule steward fixes it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .manual.parse import SectionVersion, Snapshot, normalise_text

TIER_BY_KIND = {"objective": 1, "evidential": 1, "discretionary": 2}


class RuleError(ValueError):
    pass


@dataclass
class Citation:
    ref: str
    quote: str
    page_id: str | None
    section_title: str | None
    url: str | None
    effective_from: dt.date | None
    effective_to: dt.date | None
    found: bool

    def as_props(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "verbatimText": self.quote,
            "pageId": self.page_id,
            "url": self.url,
            "effectiveFrom": self.effective_from,
            "effectiveTo": self.effective_to,
            "quoteVerified": self.found,
        }


@dataclass
class Issue:
    rule_id: str
    message: str


@dataclass
class RuleBook:
    files: dict[str, dict]
    status_by_file: dict[str, str]
    nodes: dict[str, list[dict]] = field(default_factory=dict)  # label -> props list
    rels: list[tuple[str, str, str, str, str, dict]] = field(default_factory=list)  # (fromLabel, fromId, type, toLabel, toId, props)
    issues: list[Issue] = field(default_factory=list)
    visa_type_codes: list[str] = field(default_factory=list)

    def add(self, label: str, props: dict) -> dict:
        self.nodes.setdefault(label, []).append(props)
        return props

    def rel(self, a_label: str, a_id: str, rtype: str, b_label: str, b_id: str, **props) -> None:
        self.rels.append((a_label, a_id, rtype, b_label, b_id, props))

    def content_hash(self) -> str:
        payload = json.dumps({k: v for k, v in sorted(self.files.items())}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _norm(text: str) -> str:
    return normalise_text(text).replace(" ;", ";")


def cite(snapshot: Snapshot, ref: str, quote: str, as_at: dt.date | None = None) -> Citation:
    version: SectionVersion | None
    if as_at:
        version = snapshot.in_force(ref, as_at)
    else:
        version = snapshot.current(ref)
        if version is None or not version.text:
            version = snapshot.in_force(ref, dt.date.today())
    if version is None:
        return Citation(ref, quote, None, None, None, None, None, False)
    found = _norm(quote) in _norm(version.text)
    return Citation(
        ref=ref,
        quote=_norm(quote),
        page_id=version.page_id,
        section_title=version.title,
        url=version.url,
        effective_from=version.effective_from,
        effective_to=version.effective_to,
        found=found,
    )


def _stewardship_status(meta: dict | None) -> str:
    meta = meta or {}
    prepared, approved = meta.get("prepared_by"), meta.get("approved_by")
    if approved and approved != prepared:
        return "approved"
    return "under_review"


def load_rule_files(rules_dir: str | Path) -> dict[str, dict]:
    files = {}
    for path in sorted(Path(rules_dir).glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            files[path.name] = yaml.safe_load(fh) or {}
    return files


def build_rulebook(snapshot: Snapshot, rules_dir: str | Path) -> RuleBook:
    files = load_rule_files(rules_dir)
    book = RuleBook(files=files, status_by_file={n: _stewardship_status(f.get("stewardship")) for n, f in files.items()})
    seen_ids: set[str] = set()

    def unique(rule_id: str) -> None:
        if rule_id in seen_ids:
            raise RuleError(f"duplicate rule id {rule_id}")
        seen_ids.add(rule_id)

    def cited(rule_id: str, file: str, ref: str, quote: str, as_at=None) -> dict:
        c = cite(snapshot, ref, quote, as_at)
        status = book.status_by_file[file]
        review_reason = None
        if not c.found:
            status = "under_review"
            review_reason = (
                f"quote not found in {ref} version {c.page_id or 'missing'}"
                + (f" in force on {as_at}" if as_at else " (current)")
            )
            book.issues.append(Issue(rule_id, review_reason))
        elif status == "under_review":
            review_reason = "awaiting rule steward approval (four-eyes check)"
        props = c.as_props()
        props.update({"status": status, "reviewReason": review_reason, "sourceFile": file})
        return props

    # --- catalogue -------------------------------------------------------------
    for file, data in files.items():
        for ls in data.get("legal_sources", []) or []:
            book.add("LegalSource", dict(ls))
        for vc in data.get("visa_classes", []) or []:
            p = {"code": vc["code"], "name": vc["name"]}
            if vc.get("lodgement_rule"):
                lr = vc["lodgement_rule"]
                c = cited(f"{vc['code']}-lodgement", file, lr["ref"], lr["quote"])
                p.update({"lodgementRuleRef": lr["ref"], "lodgementRuleText": c["verbatimText"],
                          "lodgementRuleUrl": c["url"], "lodgementRuleEffectiveFrom": c["effectiveFrom"],
                          "lodgementRulePageId": c["pageId"]})
            book.add("VisaClass", p)
        for cat in data.get("visa_categories", []) or []:
            book.add("VisaCategory", {"code": cat["code"], "name": cat["name"], "manualRef": cat["manual_ref"]})
            book.rel("VisaCategory", cat["code"], "IN_CLASS", "VisaClass", cat["class"])
        for vt in data.get("visa_types", []) or []:
            unique(vt["code"])
            book.visa_type_codes.append(vt["code"])
            auto = vt.get("automation") or {}
            p = {"code": vt["code"], "name": vt["name"], "instructionRef": vt["instruction_ref"],
                 "a21Listed": bool(auto.get("a21_listed")), "automationNote": (auto.get("note") or "").strip(),
                 "status": book.status_by_file[file]}
            if auto.get("quote"):
                c = cited(f"{vt['code']}-a21", file, auto["ref"], auto["quote"])
                p.update({"a21Text": c["verbatimText"], "a21Url": c["url"], "a21EffectiveFrom": c["effectiveFrom"]})
            book.add("VisaType", p)
            book.rel("VisaType", vt["code"], "IN_CATEGORY", "VisaCategory", vt["category"])
            book.rel("VisaType", vt["code"], "REQUIRES", "CriterionGroup", vt["requires"])
            for g in vt.get("inherits_generic", []):
                book.rel("VisaType", vt["code"], "INHERITS_GENERIC", "CriterionGroup", g)
            for ref in vt.get("governed_by", []):
                book.rel("VisaType", vt["code"], "GOVERNED_BY", "Instruction", ref)
            for cond in vt.get("conditions", []):
                book.rel("VisaType", vt["code"], "IMPOSES", "Condition", cond)
            for nxt in vt.get("pathway_to", []):
                book.rel("VisaType", vt["code"], "PATHWAY_TO", "VisaType", nxt)

    # --- eligibility rules -----------------------------------------------------
    for file, data in files.items():
        for ev in data.get("evidence_types", []) or []:
            unique(ev["id"])
            book.add("EvidenceType", {"id": ev["id"], "name": ev["name"], "validity": ev.get("validity")})
        for g in data.get("groups", []) or []:
            _add_group(book, unique, g, file, cited)
        for c in data.get("criteria", []) or []:
            _add_criterion(book, unique, cited, c, file)
        for cond in data.get("conditions", []) or []:
            unique(cond["id"])
            props = cited(cond["id"], file, cond["ref"], cond["quote"])
            props.update({"id": cond["id"], "text": cond["text"]})
            book.add("Condition", props)
            book.rel("Condition", cond["id"], "DEFINED_IN", "ManualSection", props["pageId"] or "")
        for rl in data.get("reference_lists", []) or []:
            unique(rl["id"])
            book.add("ReferenceList", {"id": rl["id"], "name": rl["name"], "ref": rl["ref"],
                                       "note": (rl.get("note") or "").strip(), "columns": rl.get("columns")})
            for i, row in enumerate(rl.get("rows", [])):
                rid = f"{rl['id']}#{i}"
                props = cited(rid, file, rl["ref"], row["quote"])
                props.update({"id": rid, "key": row.get("key"), "value": row.get("value"), "unit": row.get("unit"),
                              "values": row.get("values"), "validFrom": row.get("from"), "validTo": row.get("to")})
                book.add("ReferenceValue", props)
                book.rel("ReferenceList", rl["id"], "HAS_VALUE", "ReferenceValue", rid)

        # --- border, compliance, appeals ---------------------------------------
        ep = data.get("entry_permission")
        if ep:
            props = cited(ep["id"], file, ep["ref"], ep["quote"])
            props.update({"id": ep["id"], "title": "Entry permission"})
            book.add("EntryPermission", props)
        for g in data.get("entry_refusal_grounds", []) or []:
            unique(g["id"])
            props = cited(g["id"], file, g["ref"], g["quote"])
            props.update({"id": g["id"], "title": g["title"], "modality": g["modality"], "tier": 3})
            book.add("EntryRefusalGround", props)
            for crit in g.get("uses_criteria", []) or []:
                book.rel("EntryRefusalGround", g["id"], "USES", "Criterion", crit)
        tr = data.get("tribunal")
        if tr:
            props = cited(tr["id"], file, tr["ref"], tr["quote"])
            props.update({"id": tr["id"], "name": tr["name"]})
            book.add("Tribunal", props)
        ht = data.get("humanitarian_test")
        if ht:
            _add_group(book, unique, ht["group"], file)
            for c in ht["criteria"]:
                _add_criterion(book, unique, cited, c, file)
        for p in data.get("liability_periods", []) or []:
            unique(p["id"])
            props = cited(p["id"], file, p["ref"], p["quote"])
            props.update({"id": p["id"], "duration": p["duration"]})
            book.add("LiabilityPeriodRule", props)
        for a in data.get("appeal_rights", []) or []:
            unique(a["id"])
            props = cited(a["id"], file, a["ref"], a["quote"])
            props.update({k: a.get(k) for k in ("title", "appeal_type", "time_limit_days", "time_limit_from", "decision_type")})
            props["id"] = a["id"]
            props["tier"] = 3
            book.add("AppealRight", props)
            book.rel("AppealRight", a["id"], "HEARD_BY", "Tribunal", "IPT")
            if a.get("tested_by"):
                book.rel("AppealRight", a["id"], "TESTED_BY", "CriterionGroup", a["tested_by"])
        for n in data.get("no_appeal_rules", []) or []:
            unique(n["id"])
            props = cited(n["id"], file, n["ref"], n["quote"])
            props.update({"id": n["id"], "title": n["title"]})
            book.add("NoAppealRule", props)
            for target in n.get("bars", []):
                book.rel("AppealRight", target, "BARRED_BY", "NoAppealRule", n["id"])
        for pr in data.get("prohibitions", []) or []:
            unique(pr["id"])
            props = cited(pr["id"], file, pr["ref"], pr["quote"])
            props.update({"id": pr["id"], "ground": pr["ground"], "years": pr.get("years"),
                          "permanent": bool(pr.get("permanent")), "condition": pr["condition"]})
            book.add("ProhibitionRule", props)
        for lg in data.get("liability_grounds", []) or []:
            unique(lg["id"])
            props = cited(lg["id"], file, lg["ref"], lg["quote"])
            props.update({"id": lg["id"], "title": lg["title"], "actSection": lg["act_section"], "tier": 3})
            book.add("LiabilityGround", props)
            for cls in lg.get("applies_to", []):
                book.rel("VisaClass", cls, "EXPOSED_TO", "LiabilityGround", lg["id"])
            if lg.get("period"):
                book.rel("LiabilityGround", lg["id"], "HAS_PERIOD", "LiabilityPeriodRule", lg["period"])
            for pid in lg.get("prohibitions", []):
                book.rel("LiabilityGround", lg["id"], "LEADS_TO", "ProhibitionRule", pid)
            for aid in lg.get("appeals", []):
                book.rel("LiabilityGround", lg["id"], "APPEALABLE_VIA", "AppealRight", aid)
        for pw in data.get("powers", []) or []:
            unique(pw["id"])
            props = cited(pw["id"], file, pw["ref"], pw["quote"])
            props.update({"id": pw["id"], "title": pw["title"], "holder": pw["holder"], "maxDuration": pw["max_duration"], "tier": 3})
            book.add("Power", props)
            for gid in pw.get("exercised_for", []):
                book.rel("Power", pw["id"], "EXERCISED_FOR", "LiabilityGround", gid)

    _check_references(book)
    return book


def _add_group(book: RuleBook, unique, g: dict, file: str, cited=None) -> None:
    unique(g["id"])
    props = {"id": g["id"], "title": g["title"], "operator": g["operator"],
             "threshold": g.get("threshold"), "ref": g.get("ref"), "sourceFile": file,
             "effectiveFrom": None, "effectiveTo": None}
    intro = g.get("introduced_by")
    if intro and cited:
        c = cited(g["id"], file, intro["ref"], intro["quote"])
        props.update({"effectiveFrom": c["effectiveFrom"], "introducedByRef": intro["ref"],
                      "introducedByText": c["verbatimText"], "introducedByUrl": c["url"]})
    book.add("CriterionGroup", props)
    for order, member in enumerate(g["members"]):
        book.rel("CriterionGroup", g["id"], "HAS_MEMBER", "*", member, order=order)


def _add_criterion(book: RuleBook, unique, cited, c: dict, file: str) -> None:
    unique(c["id"])
    versions = c.get("versions") or [{"ref": c["ref"], "quote": c["quote"], "as_at": c.get("as_at")}]
    kind = c["kind"]
    tier = c.get("tier") or TIER_BY_KIND[kind]
    book.add("CriterionKey", {"id": c["id"], "title": c["title"]})
    for i, v in enumerate(versions):
        as_at = v.get("as_at")
        if isinstance(as_at, str):
            as_at = dt.date.fromisoformat(as_at)
        vid = f"{c['id']}@v{i + 1}"
        props = cited(vid, file, v["ref"], v["quote"], as_at)
        props.update({
            "id": vid, "key": c["id"], "title": c["title"], "kind": kind, "tier": tier,
            "officerJudgement": kind == "discretionary" or tier >= 2,
            "conditionExpr": c.get("condition"), "mandatory": c.get("mandatory", True),
            "pinnedAsAt": as_at,
        })
        book.add("Criterion", props)
        book.rel("CriterionKey", c["id"], "HAS_VERSION", "Criterion", vid)
        if props["pageId"]:
            book.rel("Criterion", vid, "DEFINED_IN", "ManualSection", props["pageId"])
        for ev in c.get("evidence", []) or []:
            book.rel("Criterion", vid, "SATISFIED_BY", "EvidenceType", ev)
        for rl in c.get("uses_list", []) or []:
            book.rel("Criterion", vid, "USES_LIST", "ReferenceList", rl)
        for j, f in enumerate(c.get("factors", []) or []):
            fid = f"{vid}#f{j}"
            fprops = cited(fid, file, f["ref"], f["quote"], as_at)
            fprops.update({"id": fid, "order": j})
            book.add("Factor", fprops)
            book.rel("Criterion", vid, "HAS_FACTOR", "Factor", fid)


def _check_references(book: RuleBook) -> None:
    ids: dict[str, set[str]] = {}
    for label, items in book.nodes.items():
        for it in items:
            key = it.get("id") or it.get("code")
            ids.setdefault(label, set()).add(key)
    member_targets = ids.get("CriterionKey", set()) | ids.get("CriterionGroup", set())
    for a_label, a_id, rtype, b_label, b_id, _ in book.rels:
        if b_label in ("Instruction", "ManualSection"):
            continue
        if a_id not in ids.get(a_label, set()):
            raise RuleError(f"{rtype}: unknown {a_label} {a_id}")
        targets = member_targets if b_label == "*" else ids.get(b_label, set())
        if b_label == "Criterion" and rtype == "USES":
            targets = ids.get("CriterionKey", set())
        if b_id not in targets:
            raise RuleError(f"{a_label} {a_id} -{rtype}-> unknown {b_label} {b_id}")
