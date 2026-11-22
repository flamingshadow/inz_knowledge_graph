"""The approved, read-only, parameterised queries.

This file is the whole of what the staff chat assistant can ask the graph. The language
model picks one of these queries and fills in its parameters; it never writes Cypher.
Adding a query means adding it here, with a test in ``tests/test_queries_graph.py``, and
getting it reviewed like any other code change.

Every query that returns a rule returns the manual's own words (``verbatimText``), the
instruction code (``ref``), the version's effective dates, its link (``url``) and the rule's
review ``status``. The assistant builds its citations from these fields, not from the
model's text.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

# The fixed list of visa types. The assistant may only use these codes.
VISA_TYPES = {
    "VISITOR": "Visitor Visa (V2)",
    "FEE_PAYING_STUDENT": "Fee Paying Student Visa (U3)",
    "SMC_RESIDENT": "Skilled Migrant Category Resident Visa (SR3)",
}
VISA_CLASSES = {"TEMPORARY": "Temporary entry class", "RESIDENCE": "Residence class"}
LIABILITY_GROUNDS = {
    "S154": "Unlawfully in New Zealand (s 154)",
    "S155": "Visa granted in error (s 155)",
    "S157": "Temporary visa holder liable for cause (s 157)",
    "S158": "Residence visa obtained by fraud (s 158)",
    "S159": "Resident visa conditions breached (s 159)",
    "S160": "New character information (s 160)",
    "S161": "Resident convicted of an offence (s 161)",
}
CHAPTERS = [
    "Administration", "Residence", "Temporary entry class visa", "Transit visa",
    "Border Entry", "Compliance", "Appendices",
]

_REF_PATTERN = r"^(?:[A-Z]{1,3}\d+(?:\.\d+)*|Appendix \d+[A-Z]?)$"

_IN_FORCE = "({v}.effectiveFrom IS NULL OR {v}.effectiveFrom <= date($d)) AND ({v}.effectiveTo IS NULL OR date($d) < {v}.effectiveTo)"


def in_force(var: str, param: str) -> str:
    return _IN_FORCE.format(v=var).replace("$d", "$" + param)


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # "string" | "date" | "integer"
    description: str
    required: bool = True
    enum: tuple[str, ...] | None = None
    pattern: str | None = None
    max_length: int = 200
    # A "lodgement" date must be one the officer actually gave (never defaulted to today).
    lodgement_date: bool = False

    def json_schema(self) -> dict:
        if self.type == "date":
            return {"type": "string", "description": self.description + " Format YYYY-MM-DD.",
                    "pattern": r"^\d{4}-\d{2}-\d{2}$"}
        if self.type == "integer":
            return {"type": "integer", "description": self.description}
        schema: dict[str, Any] = {"type": "string", "description": self.description}
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.pattern:
            schema["pattern"] = self.pattern
        return schema


@dataclass(frozen=True)
class Query:
    id: str
    title: str
    description: str
    cypher: str
    params: tuple[Param, ...] = ()
    # "rulebook" answers come from modelled rules; "manual_text" from the text index
    # (labelled "From manual text, not the rulebook").
    source: str = "rulebook"
    limit: int = 200

    def tool_schema(self) -> dict:
        props = {p.name: p.json_schema() for p in self.params}
        return {
            "type": "object",
            "properties": props,
            "required": [p.name for p in self.params if p.required],
            "additionalProperties": False,
        }

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check and normalise arguments. Raises ValueError with a message for the model."""
        known = {p.name for p in self.params}
        extra = set(args) - known
        if extra:
            raise ValueError(f"unknown parameter(s): {', '.join(sorted(extra))}")
        out: dict[str, Any] = {}
        for p in self.params:
            value = args.get(p.name)
            if value in (None, ""):
                if p.required:
                    raise ValueError(f"missing required parameter {p.name}")
                out[p.name] = None
                continue
            if p.type == "date":
                try:
                    out[p.name] = dt.date.fromisoformat(str(value)).isoformat()
                except ValueError:
                    raise ValueError(f"{p.name} must be a date in YYYY-MM-DD format") from None
            elif p.type == "integer":
                out[p.name] = int(value)
            else:
                value = str(value).strip()
                if len(value) > p.max_length:
                    raise ValueError(f"{p.name} is too long")
                if p.enum and value not in p.enum:
                    raise ValueError(f"{p.name} must be one of: {', '.join(p.enum)}")
                if p.pattern and not re.match(p.pattern, value):
                    raise ValueError(f"{p.name} has an invalid format")
                out[p.name] = value
        return out

    @property
    def lodgement_params(self) -> list[str]:
        return [p.name for p in self.params if p.lodgement_date]


VISA_TYPE = Param("visa_type", "string", "Visa type code from the fixed list.", enum=tuple(VISA_TYPES))
LODGED_ON = Param("lodged_on", "date",
                  "The date the application was (or will be) lodged, as the officer stated it. "
                  "Instructions in force on this date apply (E7.10, R5.1). Never default to today.",
                  lodgement_date=True)
AS_AT = Param("as_at", "date", "The date the officer asked about, as they stated it. Never default to today.",
              lodgement_date=True)
REF = Param("ref", "string", "Instruction code, e.g. V2.20, SR3.10, D3.15 or 'Appendix 21'.", pattern=_REF_PATTERN, max_length=30)

_CITE = "ref: {v}.ref, verbatimText: {v}.verbatimText, url: {v}.url, effectiveFrom: {v}.effectiveFrom, effectiveTo: {v}.effectiveTo, pageId: {v}.pageId, status: {v}.status, reviewReason: {v}.reviewReason"


def cite(var: str) -> str:
    return "{" + _CITE.format(v=var) + "}"


QUERIES: dict[str, Query] = {}


def _q(query: Query) -> Query:
    QUERIES[query.id] = query
    return query


_q(Query(
    id="list_visa_types",
    title="Visa types the assistant can answer about",
    description="List the fixed set of visa types in the rulebook, with their category, class and whether A21 allows automated grants.",
    cypher="""
    MATCH (vt:VisaType)-[:IN_CATEGORY]->(cat:VisaCategory)-[:IN_CLASS]->(cls:VisaClass)
    RETURN vt.code AS code, vt.name AS name, vt.instructionRef AS instructionRef,
           cat.name AS category, cls.name AS visaClass, vt.a21Listed AS a21Listed
    ORDER BY code
    """,
))

_q(Query(
    id="instruction_in_force",
    title="Instruction text in force on a date",
    description="Get the version of one instruction (by code) that was in force on a date, with its full text, effective dates and link. Use for 'what does V2.20 say' or 'what did SR3.10 say on 1 May 2026'.",
    params=(REF, AS_AT),
    cypher=f"""
    MATCH (i:Instruction {{ref: $ref}})-[:HAS_VERSION]->(s:ManualSection)
    WHERE s.hasText AND {in_force('s', 'as_at')}
    OPTIONAL MATCH (s)-[:SUPERSEDES]->(prev:ManualSection)
    RETURN s.ref AS ref, s.title AS title, s.text AS verbatimText, s.url AS url, s.pageId AS pageId,
           s.effectiveFrom AS effectiveFrom, s.effectiveTo AS effectiveTo, s.isCurrent AS isCurrent,
           s.isSection22Instruction AS isSection22Instruction, s.chapter AS chapter,
           prev.effectiveFrom AS previousVersionFrom
    ORDER BY s.effectiveFrom DESC
    LIMIT 3
    """,
))

_q(Query(
    id="instruction_history",
    title="All dated versions of an instruction",
    description="List every version of one instruction with its effective dates, newest first. Use for 'when did V2.20 change' or 'what versions of SR3.10 exist'.",
    params=(REF,),
    cypher="""
    MATCH (i:Instruction {ref: $ref})-[:HAS_VERSION]->(s:ManualSection)
    RETURN s.ref AS ref, s.title AS title, s.pageId AS pageId, s.url AS url,
           s.effectiveFrom AS effectiveFrom, s.effectiveTo AS effectiveTo, s.isCurrent AS isCurrent,
           s.hasText AS hasText
    ORDER BY s.effectiveFrom DESC
    """,
))

_q(Query(
    id="visa_requirements",
    title="Requirements for a visa type on a lodgement date",
    description="Get every modelled requirement (criterion) for a visa type, using the versions in force on the lodgement date, including inherited generic health, character and bona fides rules. Shows the ALL/ANY grouping, each rule's kind (objective, evidential, discretionary) and the manual's exact wording.",
    params=(VISA_TYPE, LODGED_ON),
    cypher=f"""
    MATCH (vt:VisaType {{code: $visa_type}})-[rel:REQUIRES|INHERITS_GENERIC]->(root:CriterionGroup)
    MATCH path = (root)-[:HAS_MEMBER*0..6]->(g:CriterionGroup)
    WHERE all(n IN nodes(path) WHERE {in_force('n', 'lodged_on')})
    MATCH (g)-[m:HAS_MEMBER]->(k:CriterionKey)
    OPTIONAL MATCH (k)-[:HAS_VERSION]->(c:Criterion)
      WHERE c.status <> 'retired' AND {in_force('c', 'lodged_on')}
    WITH vt, rel, root, g, m, k, c, [n IN nodes(path) | n.title + ' [' + n.operator + ']'] AS groups,
         COLLECT {{ MATCH (k)-[:HAS_VERSION]->(o:Criterion) WHERE o.effectiveTo IS NOT NULL AND o.effectiveTo <= date($lodged_on) RETURN o.id }} AS ended,
         COLLECT {{ MATCH (k)-[:HAS_VERSION]->(o:Criterion) WHERE o.effectiveFrom > date($lodged_on) RETURN o.id }} AS later
    WHERE c IS NOT NULL OR size(ended) = 0
    RETURN CASE type(rel) WHEN 'REQUIRES' THEN 'visa-specific' ELSE 'generic (inherited)' END AS source,
           groups AS groupPath, k.id AS criterion, k.title AS title,
           c.id AS versionId, c.kind AS kind, c.tier AS tier, c.officerJudgement AS officerJudgement,
           c.conditionExpr AS conditionExpr,
           CASE WHEN c IS NULL THEN 'not modelled for this date: the modelled rule starts later, check the instruction history' ELSE 'in force' END AS coverage,
           {cite('c')} AS citation,
           COLLECT {{ MATCH (c)-[:SATISFIED_BY]->(e:EvidenceType) RETURN e.name }} AS evidence,
           COLLECT {{ MATCH (c)-[:HAS_FACTOR]->(f:Factor) RETURN f.verbatimText + ' (' + f.ref + ')' }} AS factors
    ORDER BY source DESC, root.id, size(groups), g.id, m.order
    """,
))

_q(Query(
    id="evidence_for_visa",
    title="Evidence that satisfies each requirement",
    description="List the evidence types that can satisfy each requirement of a visa type, for the versions in force on the lodgement date.",
    params=(VISA_TYPE, LODGED_ON),
    cypher=f"""
    MATCH (vt:VisaType {{code: $visa_type}})-[:REQUIRES|INHERITS_GENERIC]->(root:CriterionGroup)
    MATCH (root)-[:HAS_MEMBER*1..7]->(k:CriterionKey)-[:HAS_VERSION]->(c:Criterion)
    WHERE c.status <> 'retired' AND {in_force('c', 'lodged_on')}
    MATCH (c)-[:SATISFIED_BY]->(e:EvidenceType)
    RETURN k.id AS criterion, k.title AS title, c.kind AS kind,
           collect(DISTINCT e.name) AS evidence, collect(DISTINCT e.validity) AS validityNotes,
           {cite('c')} AS citation
    ORDER BY criterion
    """,
))

_q(Query(
    id="officer_judgement_rules",
    title="Rules that need the officer's judgement",
    description="List the discretionary requirements for a visa type on a lodgement date (where the manual asks the officer to be satisfied or to weigh evidence), with the factors the manual lists. The assistant must show these as a checklist and never give a verdict.",
    params=(VISA_TYPE, LODGED_ON),
    cypher=f"""
    MATCH (vt:VisaType {{code: $visa_type}})-[:REQUIRES|INHERITS_GENERIC]->(root:CriterionGroup)
    MATCH (root)-[:HAS_MEMBER*1..7]->(k:CriterionKey)-[:HAS_VERSION]->(c:Criterion)
    WHERE c.officerJudgement AND c.status <> 'retired' AND {in_force('c', 'lodged_on')}
    RETURN DISTINCT k.id AS criterion, k.title AS title, c.kind AS kind, c.tier AS tier, true AS officerJudgement,
           {cite('c')} AS citation,
           COLLECT {{ MATCH (c)-[:HAS_FACTOR]->(f:Factor) RETURN {{text: f.verbatimText, ref: f.ref, url: f.url}} }} AS factors,
           COLLECT {{ MATCH (c)-[:SATISFIED_BY]->(e:EvidenceType) RETURN e.name }} AS evidence
    ORDER BY criterion
    """,
))

_q(Query(
    id="visa_conditions",
    title="Conditions on a granted visa",
    description="List the conditions placed on a visa type, with the manual wording, for the versions in force on a date.",
    params=(VISA_TYPE, AS_AT),
    cypher=f"""
    MATCH (vt:VisaType {{code: $visa_type}})-[:IMPOSES]->(c:Condition)
    WHERE {in_force('c', 'as_at')}
    RETURN c.id AS condition, c.text AS summary, {cite('c')} AS citation
    ORDER BY condition
    """,
))

_q(Query(
    id="lodgement_rule",
    title="Which instructions apply to an application",
    description="Show the rule that decides which version of the instructions applies (the instructions in force when the application is made): E7.10 for temporary entry, R5.1 for residence.",
    params=(Param("visa_class", "string", "TEMPORARY or RESIDENCE.", enum=tuple(VISA_CLASSES)),),
    cypher="""
    MATCH (c:VisaClass {code: $visa_class})
    RETURN c.name AS visaClass,
           {ref: c.lodgementRuleRef, verbatimText: c.lodgementRuleText, url: c.lodgementRuleUrl,
            effectiveFrom: c.lodgementRuleEffectiveFrom, pageId: c.lodgementRulePageId} AS citation
    """,
))

_q(Query(
    id="instructions_changed",
    title="Instructions that changed in a period",
    description="List instruction versions that took effect between two dates, optionally within one chapter or code prefix (e.g. 'SR3' or 'V2'). Use for 'what changed since 1 August 2026'.",
    params=(
        Param("since", "date", "Start of the period (inclusive), as the officer stated it."),
        Param("until", "date", "End of the period (inclusive). Optional; leave out for 'up to the latest loaded manual'.", required=False),
        Param("ref_prefix", "string", "Optional code prefix such as SR3, V2, U3, D3 or A4.", required=False, pattern=r"^[A-Z]{1,3}\d*(?:\.\d+)*$", max_length=20),
    ),
    limit=100,
    cypher="""
    MATCH (s:ManualSection)
    WHERE s.ref IS NOT NULL AND s.effectiveFrom >= date($since)
      AND ($until IS NULL OR s.effectiveFrom <= date($until))
      AND ($ref_prefix IS NULL OR s.ref = $ref_prefix OR s.ref STARTS WITH $ref_prefix + '.')
      AND s.inScope
    OPTIONAL MATCH (s)-[:SUPERSEDES]->(prev:ManualSection)
    RETURN s.ref AS ref, s.title AS title, s.effectiveFrom AS effectiveFrom, s.isCurrent AS isCurrent,
           s.url AS url, s.pageId AS pageId, prev.effectiveFrom AS replacedVersionFrom, s.chapter AS chapter
    ORDER BY s.effectiveFrom DESC, s.ref
    LIMIT 100
    """,
))

_q(Query(
    id="smc_wage_threshold",
    title="SMC wage threshold on a date",
    description="Get the Skilled Migrant Category wage threshold row (hourly, NZD, with the 1.1, 1.2, 1.5, 2 and 3 times multiples) in effect on a date. Which date applies is set by SR3.10(f); say so.",
    params=(Param("as_at", "date", "The date whose threshold applies, as the officer stated it.", lodgement_date=True),),
    cypher="""
    MATCH (l:ReferenceList {id: 'SMC_WAGE_THRESHOLD'})-[:HAS_VALUE]->(v:ReferenceValue)
    WHERE v.validFrom <= date($as_at) AND (v.validTo IS NULL OR date($as_at) < v.validTo)
    RETURN l.name AS list, l.columns AS multipliers, v.values AS hourlyRates, v.validFrom AS from, v.validTo AS to,
           l.note AS dateRule, {ref: v.ref, verbatimText: v.verbatimText, url: v.url, effectiveFrom: v.effectiveFrom,
           pageId: v.pageId, status: v.status, reviewReason: v.reviewReason} AS citation
    """,
))

_q(Query(
    id="funds_amounts",
    title="Maintenance funds amounts",
    description="Get the modelled funds amounts for visitors (V2.20) or students (U3.20) from the current instructions.",
    params=(Param("list_id", "string", "VISITOR_FUNDS or STUDENT_FUNDS.", enum=("VISITOR_FUNDS", "STUDENT_FUNDS")),),
    cypher="""
    MATCH (l:ReferenceList {id: $list_id})-[:HAS_VALUE]->(v:ReferenceValue)
    RETURN l.name AS list, v.key AS item, v.value AS amount, v.unit AS unit, {ref: v.ref, verbatimText: v.verbatimText, url: v.url,
           effectiveFrom: v.effectiveFrom, pageId: v.pageId, status: v.status, reviewReason: v.reviewReason} AS citation
    ORDER BY v.id
    """,
))

_q(Query(
    id="liability_grounds",
    title="Deportation liability grounds for a visa class",
    description="List the grounds on which holders of a visa class can become liable for deportation, with the Act section, liability period and manual wording. Liability decisions are person-only (tier 3).",
    params=(Param("visa_class", "string", "TEMPORARY or RESIDENCE.", enum=tuple(VISA_CLASSES)),),
    cypher="""
    MATCH (:VisaClass {code: $visa_class})-[:EXPOSED_TO]->(g:LiabilityGround)
    OPTIONAL MATCH (g)-[:HAS_PERIOD]->(p:LiabilityPeriodRule)
    RETURN g.id AS ground, g.title AS title, g.actSection AS actSection, g.tier AS tier,
           """ + cite("g") + """ AS citation,
           CASE WHEN p IS NULL THEN null ELSE {duration: p.duration, citation: """ + cite("p") + """} END AS liabilityPeriod
    ORDER BY ground
    """,
))

_q(Query(
    id="appeal_rights",
    title="Appeal right, body and deadline for a liability ground",
    description="For a deportation liability ground, give the appeal right (facts or humanitarian), the body that hears it, the time limit and from when it runs, any rule that bars the appeal, and the humanitarian test. Use RESIDENCE_DECLINE for an appeal against a declined residence application.",
    params=(Param("ground", "string", "Liability ground code, or RESIDENCE_DECLINE.", enum=tuple(LIABILITY_GROUNDS) + ("RESIDENCE_DECLINE",)),),
    cypher="""
    CALL {
      MATCH (g:LiabilityGround {id: $ground})-[:APPEALABLE_VIA]->(a:AppealRight) RETURN a
      UNION
      MATCH (a:AppealRight {decision_type: $ground}) RETURN a
    }
    OPTIONAL MATCH (a)-[:HEARD_BY]->(t:Tribunal)
    RETURN a.id AS appeal, a.title AS title, a.appeal_type AS appealType, a.time_limit_days AS timeLimitDays,
           a.time_limit_from AS timeLimitRunsFrom, t.name AS heardBy, a.tier AS tier,
           """ + cite("a") + """ AS citation,
           COLLECT { MATCH (a)-[:BARRED_BY]->(n:NoAppealRule) RETURN {title: n.title, citation: """ + cite("n") + """} } AS barredBy,
           COLLECT { MATCH (a)-[:TESTED_BY]->(:CriterionGroup)-[:HAS_MEMBER]->(:CriterionKey)-[:HAS_VERSION]->(c:Criterion)
                     RETURN {test: c.title, officerJudgement: true, citation: """ + cite("c") + """} } AS humanitarianTest
    """,
))

_q(Query(
    id="prohibition_on_entry",
    title="Re-entry ban after deportation",
    description="Give the period of prohibition on entry (re-entry ban) after deportation on a liability ground, with each condition that changes the period (D2.45, Act s 179).",
    params=(Param("ground", "string", "Liability ground code.", enum=tuple(LIABILITY_GROUNDS)),),
    cypher="""
    MATCH (g:LiabilityGround {id: $ground})-[:LEADS_TO]->(p:ProhibitionRule)
    RETURN g.title AS ground, p.condition AS condition, p.years AS years, p.permanent AS permanent,
           """ + cite("p") + """ AS citation
    ORDER BY p.id
    """,
))

_q(Query(
    id="entry_refusal_grounds",
    title="Grounds to refuse entry permission at the border",
    description="List the grounds on which entry permission must or may be refused (Y4), with modality (must, must unless an exception is granted, may). Border refusals are person-only decisions (tier 3).",
    cypher="""
    MATCH (g:EntryRefusalGround)
    OPTIONAL MATCH (e:EntryPermission)
    RETURN g.id AS ground, g.title AS title, g.modality AS modality, g.tier AS tier,
           """ + cite("g") + """ AS citation, """ + cite("e") + """ AS entryPermissionRule
    ORDER BY g.ref
    """,
))

_q(Query(
    id="officer_powers",
    title="Officer powers of arrest and detention",
    description="List the modelled arrest and detention powers (who holds them, how long, which liability grounds), from D4.",
    cypher="""
    MATCH (p:Power)
    RETURN p.id AS power, p.title AS title, p.holder AS holder, p.maxDuration AS maxDuration, p.tier AS tier,
           COLLECT { MATCH (p)-[:EXERCISED_FOR]->(g:LiabilityGround) RETURN g.id } AS forGrounds,
           """ + cite("p") + """ AS citation
    ORDER BY p.id
    """,
))

_q(Query(
    id="automation_eligibility",
    title="Whether A21 allows automated grants for a visa type",
    description="Say whether the A21 instruction lets an automated system grant this visa type. The system never declines. Phase one is recommend-only.",
    params=(VISA_TYPE,),
    cypher="""
    MATCH (vt:VisaType {code: $visa_type})
    RETURN vt.code AS visaType, vt.name AS name, vt.a21Listed AS a21AllowsAutomatedGrant, vt.automationNote AS note,
           CASE WHEN vt.a21Text IS NULL THEN null ELSE {ref: 'A21', verbatimText: vt.a21Text, url: vt.a21Url,
           effectiveFrom: vt.a21EffectiveFrom} END AS citation
    """,
))

_q(Query(
    id="search_manual_text",
    title="Search the manual's text (fallback)",
    description="Full-text search of the manual, limited to versions in force on a date. Use only when no other query answers the question, for 'where does the manual talk about X'. Answers from this query are labelled 'From manual text, not the rulebook'.",
    source="manual_text",
    params=(
        Param("text", "string", "A few plain search words, e.g. 'maternity services' or 'working holiday'.", max_length=120),
        Param("as_at", "date", "Only show text in force on this date, as the officer stated it.", lodgement_date=True),
        Param("chapter", "string", "Optional chapter to search within.", required=False, enum=tuple(CHAPTERS)),
    ),
    limit=8,
    cypher=f"""
    CALL db.index.fulltext.queryNodes('manual_text', $lucene) YIELD node AS p, score
    WHERE {in_force('p', 'as_at')} AND ($chapter IS NULL OR p.chapter = $chapter)
    OPTIONAL MATCH (rule:Rule)-[:QUOTES]->(p)
    WITH p, score, collect(DISTINCT coalesce(rule.key, rule.id)) AS linkedRules
    RETURN p.ref AS ref, p.title AS instruction, p.heading AS heading, p.text AS verbatimText, p.url AS url,
           p.effectiveFrom AS effectiveFrom, p.effectiveTo AS effectiveTo, p.pageId AS pageId, score,
           [r IN linkedRules WHERE r IS NOT NULL] AS linkedRules
    ORDER BY score DESC
    LIMIT 8
    """,
))


def lucene_terms(text: str) -> str:
    """Turn plain words into a safe Lucene query (no operators from the model)."""
    words = re.findall(r"[A-Za-z0-9$][A-Za-z0-9.$,']*", text)[:12]
    words = [w.strip(".,'") for w in words if w.strip(".,'")]
    if not words:
        raise ValueError("search text has no words")
    escaped = [re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r"\\\1", w) for w in words]
    phrase = '"' + " ".join(escaped) + '"'
    return f"{phrase}^3 OR " + " AND ".join(escaped)


def extra_params(query: Query, args: dict[str, Any]) -> dict[str, Any]:
    if query.id == "search_manual_text":
        return {"lucene": lucene_terms(args["text"])}
    return {}
