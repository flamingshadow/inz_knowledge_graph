# Architecture

## Source data

`data/snapshots/opsmanual-YYYY-MM-DD.json.gz` is one copy of the public Operational
Manual, taken from the two files the manual's own viewer loads:

* `toc.json?node=NaN`: the contents. Hidden entries with a date in their title, such as
  `E4.1 Lodging applications (01/02/2025)`, are earlier versions of an instruction. Titles
  ending `(to dd/mm/yyyy)` are versions that have ended.
* `search.json`: the text of every page. Each page ends with `Effective dd/mm/yyyy`.

`inzkg/manual/parse.py` turns these into versions with an `effectiveFrom` and an
exclusive `effectiveTo` (the next version's start date), and splits each version into
passages at its sub-codes (`V2.20.5`), capped at 1,800 characters.

## Graph

### Manual layer

| Node | Key | Notes |
|---|---|---|
| `Instruction` | `ref` | An instruction code, e.g. `SR3.15` |
| `ManualSection` | `pageId` | One version of an instruction, with `text`, `textHash`, `effectiveFrom`, `effectiveTo`, `url`, `chapter`, `inScope` |
| `Passage` | `id` | Part of a version's text, in the `manual_text` full-text index |
| `LegalSource` | `id` | Immigration Act 2009 sections (s 22, 25, 28) |

`(Instruction)-[:HAS_VERSION]->(ManualSection)`, `(ManualSection)-[:SUPERSEDES]->(ManualSection)`,
`(Passage)-[:PART_OF]->(ManualSection)`, `(ManualSection)-[:PART_OF]->(ManualSection)` for the
contents tree, and `(ManualSection)-[:ISSUED_UNDER]->(LegalSource)` for section 22 instructions.

### Rule layer

Every rule node also has the label `Rule` and carries its citation: `ref`, `pageId`,
`verbatimText`, `effectiveFrom`, `effectiveTo`, `url`, `status` and `reviewReason`.

| Node | What it is |
|---|---|
| `VisaClass`, `VisaCategory`, `VisaType` | The catalogue. `VisaClass` holds its lodgement rule (E7.10, R5.1). `VisaType` holds the A21 automation flag. |
| `CriterionGroup` | `ALL` or `ANY` set of criteria or sub-groups (e.g. the three SMC pathways) |
| `CriterionKey` → `Criterion` | A requirement and its dated versions (`KEY@v1`, `KEY@v2`), with `kind`, `tier` and `officerJudgement` |
| `Factor` | Matters an officer weighs for a judgement rule |
| `EvidenceType` | Documents that satisfy a criterion |
| `Condition` | Conditions a visa imposes |
| `ReferenceList`, `ReferenceValue` | Dated tables: wage thresholds, funds amounts |
| `EntryPermission`, `EntryRefusalGround` | Border entry (Y3, Y4) |
| `LiabilityGround`, `LiabilityPeriodRule`, `ProhibitionRule`, `Power` | Compliance and deportation (D2, D4) |
| `AppealRight`, `NoAppealRule`, `Tribunal` | Appeals (D3, R5.55) |

Main relationships: `REQUIRES`, `INHERITS_GENERIC`, `HAS_MEMBER`, `HAS_VERSION`,
`DEFINED_IN` (rule to the version it quotes), `QUOTES` (rule to the passage holding the
words), `SATISFIED_BY`, `HAS_FACTOR`, `USES_LIST`, `HAS_VALUE`, `IMPOSES`, `GOVERNED_BY`,
`EXPOSED_TO`, `HAS_PERIOD`, `LEADS_TO`, `APPEALABLE_VIA`, `HEARD_BY`, `BARRED_BY`,
`TESTED_BY`, `EXERCISED_FOR`.

### Tiers

| Tier | Meaning | Examples |
|---|---|---|
| 1 | Objective or documentary: can be checked and a pass recommended | age, funds, passport |
| 2 | Officer judgement: the assistant shows the rule and its factors, never a verdict | bona fides, character |
| 3 | Person-only decision | entry refusal, deportation, humanitarian appeals |

### Change history

Each `inzkg load` writes a `LoaderRun` (snapshot date, manual issue date, who ran it,
sections changed, quote problems) and a `RuleChange` for every rule added, changed or
retired, linked by `RECORDED`. Both are append-only. A rule removed from the YAML is
marked `retired`, not deleted.

## Which rules apply on a date

Temporary entry applications are decided under the instructions in force when they were
lodged (E7.10), and so are residence applications (R5.1). Queries that take
`lodged_on` or `as_at` pick the versions where
`effectiveFrom <= date < effectiveTo`. Groups introduced later (such as the SMC three
pathways from 24 August 2026) carry their own `effectiveFrom`, so an earlier lodgement
gets the earlier rules.

## The assistant

1. The model records how it read the question (`record_interpretation`): visa type from
   the fixed list, the date and the officer's own words for it.
2. Code checks the date against what the officer typed. If it's missing or doesn't match,
   the model must ask (`ask_clarifying_question`).
3. The model calls approved queries. Code validates every value, checks the visa type and
   date match the interpretation, and runs the query read-only.
4. The answer's citations, labels and notices are built by code from the query results.
5. The turn is appended to the hash-chained chat log.

The model is set by `INZKG_MODEL` and runs with adaptive thinking, and the
server-side fallback beta so a capacity problem falls back to another model.
