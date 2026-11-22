# INZ rules knowledge graph

A Neo4j knowledge graph of Immigration New Zealand's rules, built from the public
[Operational Manual](https://www.immigration.govt.nz/opsmanual/), and a staff query
interface where officers ask questions in plain English.

Phase one holds **rules only, no applicant data**. It covers Visitor, Fee Paying Student
and Skilled Migrant Category visas, plus the generic health and character rules,
lodgement rules, border entry, compliance and deportation, and appeals. Refugees and
Protection is out of scope.

The design follows the approved *Graph schema v1* and *Safeguards guide v1.0*.

## Quick start

```bash
cp .env.example .env              # then set ANTHROPIC_API_KEY for the chat assistant
docker compose up -d neo4j        # Neo4j 5.26 on bolt://localhost:7687
pip install -e '.[dev]'

inzkg check                       # check every rule's quote against the manual (no database)
inzkg load --loaded-by "your name"   # load the manual snapshot and the rulebook
inzkg serve                       # staff web page on http://127.0.0.1:8000
pytest                            # 114 tests; graph tests skip if Neo4j isn't loaded
```

Other commands:

| Command | What it does |
|---|---|
| `inzkg fetch` | Download a new manual snapshot over HTTPS into `data/snapshots/` |
| `inzkg query visa_requirements visa_type=VISITOR lodged_on=2026-09-01` | Run one approved query |
| `inzkg ask "What does a visitor need if they lodged on 1 September 2026?"` | Ask the assistant once |
| `inzkg verify-log` | Check the chat log's hash chain has not been altered |

If the manual site blocks scripted downloads from your network, open the manual in a
browser and paste `scripts/browser_export.js` into the developer console. It saves the
same snapshot file.

## How it fits together

```
Operational Manual  ──fetch──►  snapshot (.json.gz)  ──parse──►  sections, versions, passages
                                                                         │
rules/*.yaml (rule stewards)  ──build + quote check──────────────────────┤
                                                                         ▼
                                                                 Neo4j graph + text index
                                                                         ▲
Officer ──► web page ──► assistant (Claude) ──picks one of 18 approved read-only queries
                              │
                              └──► chat log (append-only, hash-chained)
```

* **Manual layer.** Every instruction (`Instruction`, e.g. `V2.20`) has dated versions
  (`ManualSection`) linked by `SUPERSEDES`, with `effectiveFrom` and an exclusive
  `effectiveTo`. Version text is split into `Passage` nodes with a full-text index
  (`manual_text`). The current snapshot (retrieved 22 November 2026, manual issue date
  6 November 2026) has 6,081 versions and 21,028 passages.
* **Rule layer.** Rule stewards write rules in `rules/*.yaml`. Each rule quotes the
  manual's exact words and is linked to the version it quotes (`DEFINED_IN`) and to the
  passage holding the words (`QUOTES`). Visa types inherit shared rules (`INHERITS_GENERIC`)
  and criteria sit in `ALL`/`ANY` groups, such as the three SMC pathways.
* **Change history.** Each load writes an append-only `LoaderRun` with the manual issue
  date and a `RuleChange` node for every rule added, changed or retired.

See [docs/architecture.md](docs/architecture.md) for the node and relationship list, and
how dates are resolved.

## Safeguards and where they are enforced

| Safeguard | Where |
|---|---|
| The model never writes Cypher. It chooses one of 18 approved queries and fills in values, which are checked against strict schemas and fixed lists. | `inzkg/queries/catalogue.py`, `Assistant._run_tool` |
| Every query is read-only: a read-only account, a read session, and an `EXPLAIN` check that refuses any plan that writes. | `inzkg/db.py` |
| A lodgement or "as at" date is only used if the officer typed it. The assistant checks the officer's own words parse to that date. Otherwise it asks. It never assumes today. | `inzkg/chat/dates.py` |
| The assistant shows how it read the question, and the officer can correct the visa type or date. | web page, `Interpretation.display` |
| Citations (code, effective date, exact wording, link) come from query results, never from the model's text. | `_collect_sources` |
| Rules whose quote isn't found word for word in the cited version are marked `under_review`. | `inzkg/rules.py` |
| A rule counts as approved only when someone other than its author signs it off (four eyes). | `stewardship` in each rule file |
| Answers that come from the text index are labelled "From manual text, not the rulebook". | `_compose` |
| Judgement rules are labelled "Officer judgement", and the assistant gives no verdict. | `_compose`, system prompt |
| Border, compliance and appeal rules are tier 3 and labelled "Person-only decision". | `_compose` |
| If no query returns anything, the model's text is replaced with a fixed "no answer". | `_compose` |
| Automated grants are only flagged as possible where A21 allows (visitor visas). Declines always stay with an officer. | `automation_eligibility`, system prompt |
| Every chat turn is logged with the officer's id, the interpretation, every query and its values, the rule versions returned, and the answer. | `inzkg/chat/audit_log.py` |

## Current limits

* **No rule is approved yet.** All 110 modelled rules are `under_review` until a rule
  steward other than the author signs off each file (`approved_by` in `rules/*.yaml`).
  Answers show "Rule awaiting steward approval" until then.
* **Amendment circulars are not loaded yet.** Versions and dates come from the manual's
  own dated earlier versions and "Effective" dates.
* Earlier versions of the manual only go back as far as the manual keeps them. A
  question about a date before the oldest modelled version gets a "not modelled for this
  date" note rather than an answer.
* The chat assistant needs an Anthropic API key. Web sign-in is expected to come from
  INZ's single sign-on proxy, which passes the officer's id in `X-Staff-Id`.
* The assistant keeps conversations in memory for four hours. A restart clears them,
  but the chat log is kept.
