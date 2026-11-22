// Constraints and indexes for the INZ rules knowledge graph (schema v1, rulebook layer).
// Applied by `inzkg load`. Every statement is idempotent.

CREATE CONSTRAINT instruction_ref IF NOT EXISTS FOR (n:Instruction) REQUIRE n.ref IS UNIQUE;
CREATE CONSTRAINT section_page IF NOT EXISTS FOR (n:ManualSection) REQUIRE n.pageId IS UNIQUE;
CREATE CONSTRAINT passage_id IF NOT EXISTS FOR (n:Passage) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT legal_source_id IF NOT EXISTS FOR (n:LegalSource) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT visa_class_code IF NOT EXISTS FOR (n:VisaClass) REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT visa_category_code IF NOT EXISTS FOR (n:VisaCategory) REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT visa_type_code IF NOT EXISTS FOR (n:VisaType) REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT criterion_key_id IF NOT EXISTS FOR (n:CriterionKey) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT criterion_id IF NOT EXISTS FOR (n:Criterion) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT group_id IF NOT EXISTS FOR (n:CriterionGroup) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT evidence_id IF NOT EXISTS FOR (n:EvidenceType) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT factor_id IF NOT EXISTS FOR (n:Factor) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT condition_id IF NOT EXISTS FOR (n:Condition) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT reflist_id IF NOT EXISTS FOR (n:ReferenceList) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT refvalue_id IF NOT EXISTS FOR (n:ReferenceValue) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT entry_permission_id IF NOT EXISTS FOR (n:EntryPermission) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT entry_refusal_id IF NOT EXISTS FOR (n:EntryRefusalGround) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT liability_ground_id IF NOT EXISTS FOR (n:LiabilityGround) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT liability_period_id IF NOT EXISTS FOR (n:LiabilityPeriodRule) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT appeal_right_id IF NOT EXISTS FOR (n:AppealRight) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT no_appeal_id IF NOT EXISTS FOR (n:NoAppealRule) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT tribunal_id IF NOT EXISTS FOR (n:Tribunal) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT prohibition_id IF NOT EXISTS FOR (n:ProhibitionRule) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT power_id IF NOT EXISTS FOR (n:Power) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT loader_run_id IF NOT EXISTS FOR (n:LoaderRun) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT rule_change_id IF NOT EXISTS FOR (n:RuleChange) REQUIRE n.id IS UNIQUE;

CREATE INDEX section_ref IF NOT EXISTS FOR (n:ManualSection) ON (n.ref);
CREATE INDEX section_effective IF NOT EXISTS FOR (n:ManualSection) ON (n.effectiveFrom);
CREATE INDEX passage_ref IF NOT EXISTS FOR (n:Passage) ON (n.ref);
CREATE INDEX criterion_key IF NOT EXISTS FOR (n:Criterion) ON (n.key);

// The second index of the manual's full text (safeguards guide, staff chat assistant).
CREATE FULLTEXT INDEX manual_text IF NOT EXISTS FOR (n:Passage) ON EACH [n.text, n.heading, n.title];
