# DORA Register of Information — the wedge module

This document specifies the DORA Register of Information (RoI) module in
enough detail that Claude Code can build it. The RoI module is the single
most important deliverable of Phase 1 and is what the first paying customer
will actually pay for.

---

## Why the Register of Information is the wedge

The Digital Operational Resilience Act (DORA, Regulation (EU) 2022/2554)
came into force on 17 January 2025. Every in-scope EU financial entity must
maintain and submit a Register of Information detailing every contractual
arrangement with an ICT third-party service provider supporting critical or
important functions. The ESAs' implementing technical standards specify
around 15 templates and dozens of fields per arrangement.

Deloitte's 2025 DORA survey found that 46% of in-scope financial entities
cite the Register of Information as their single hardest requirement. The
register must be maintained as a living document, re-submitted annually, and
updated on every material change to an ICT arrangement. It must capture the
full subcontractor chain, concentration risk, and contract terms such as
audit rights, exit strategies, and data-location assurances.

No current GRC platform does this natively with AI-grounded automation.
OneTrust has a Third-Party Risk module but it is spreadsheet-descended.
Harvey and Legora don't address it at all. Thomson Reuters and Wolters
Kluwer sell regulatory content about DORA, not a register generation system.

The Register of Information is the perfect wedge because:

1. It is urgent (annual re-submission, regulator-mandated).
2. It is concrete (known templates, known fields, known outputs).
3. It is structured (fits our two-corpus model perfectly).
4. It is high-stakes (regulatory penalties, executive-level attention).
5. It is budgeted (€2-5M typical DORA programme, with RoI as a distinct
   line item).
6. It is underserved by existing tooling.

---

## What the RoI module does end-to-end

### The customer's journey

1. The customer uploads a set of ICT third-party contracts (cloud services,
   SaaS, outsourcing, managed services, software licences).
2. The module extracts, from each contract, the fields required by the RoI
   templates: counterparty name, legal entity ID (LEI), registered country,
   parent entity, services provided, function criticality, contract start
   and end dates, notice period, audit rights, exit strategy terms, data
   location, subcontracting terms.
3. The module prompts the customer to confirm or correct each extracted
   field via a guided UI.
4. The module infers function criticality by cross-referencing the
   regulatory corpus (DORA Article 2 definitions, EBA guidance on critical
   functions).
5. The module detects subcontracting chains where evidence exists (in the
   contract itself or in supplementary documents uploaded by the customer).
6. The module computes concentration risk: how many critical functions rely
   on the same provider, how many providers are located in the same
   non-EEA country, how many are affiliated with the same parent group.
7. The module produces the RoI in the official ESAs template format,
   validates it against the technical standards, and exports it in the
   required XML/XBRL format for submission.
8. The module monitors for changes: if a contract is amended, a new
   subcontractor is added, or a provider's criticality changes, the RoI
   entry is flagged for re-review.

### What the customer sees

- `/gc/dora/register` — a table view of all ICT arrangements with
  criticality, provider country, concentration risk score, and last-review
  date.
- `/gc/dora/register/new` — guided intake flow for a new arrangement:
  upload contract → AI extracts fields → user confirms → entry saved.
- `/gc/dora/register/[id]` — detail view of a single arrangement with
  full field editor, subcontractor chain visualizer, linked contract
  documents, change history, and linked obligations.
- `/gc/dora/register/concentration` — concentration risk dashboard:
  top providers by critical-function reliance, top countries, top parent
  groups, with colour-coded risk indicators.
- `/gc/dora/register/export` — export in ESAs XML/XBRL format,
  human-readable PDF, and CSV for internal review.

---

## Data model

New tables:

### `gc_dora_register_entries`

One row per ICT third-party arrangement.

```
id                       uuid pk
company_id               uuid fk -> gc_companies
counterparty_legal_name  varchar
counterparty_lei         varchar(20)  -- Legal Entity Identifier
counterparty_country     varchar(2)   -- ISO 3166-1 alpha-2
parent_entity_name       varchar
parent_entity_lei        varchar(20)
services_provided        text
service_type             enum  -- per ESAs taxonomy
supports_critical_func   boolean
critical_function_desc   text
contract_start_date      date
contract_end_date        date
notice_period_days       integer
audit_rights_present     boolean
exit_strategy_present    boolean
exit_strategy_desc       text
data_location_country    varchar(2)
data_processing_country  varchar(2)
contract_doc_id          uuid fk -> gc_doc_registry
status                   enum  -- draft, confirmed, submitted, superseded
last_reviewed_at         timestamp
last_reviewed_by         uuid fk -> users
ai_confidence            decimal  -- 0-1, confidence of extraction
created_at               timestamp
updated_at               timestamp
```

### `gc_dora_register_subcontractors`

One row per subcontractor in an arrangement's chain.

```
id                       uuid pk
entry_id                 uuid fk -> gc_dora_register_entries
subcontractor_name       varchar
subcontractor_lei        varchar(20)
subcontractor_country    varchar(2)
services_provided        text
is_critical              boolean
chain_position           integer  -- 1 = direct subcontractor, 2 = sub-sub, etc.
evidence_doc_id          uuid fk -> gc_doc_registry  -- where we found this
ai_confidence            decimal
created_at               timestamp
```

### `gc_dora_register_changes`

Audit trail of every change to an entry.

```
id                       uuid pk
entry_id                 uuid fk -> gc_dora_register_entries
field_name               varchar
old_value                text
new_value                text
changed_by               uuid fk -> users (null if AI-initiated)
change_reason            text
ai_generated             boolean
created_at               timestamp
```

### `gc_dora_concentration_assessments`

Computed concentration risk snapshots (run nightly).

```
id                       uuid pk
company_id               uuid fk -> gc_companies
assessed_at              timestamp
dimension                enum  -- 'provider', 'country', 'parent_group'
dimension_value          varchar  -- e.g., 'AWS', 'US', 'Alphabet Inc.'
critical_func_count      integer
total_func_count         integer
risk_score               decimal  -- 0-1, higher = more concentrated
notes                    text
```

---

## The AI pipeline for the RoI

### Step 1 — contract upload

The user uploads a contract PDF (or DOCX). The existing GC upload pipeline
handles extraction, classification (this is a contract, not a policy), and
storage. Classification enriches the upload queue with `document_type =
'ict_contract'`.

### Step 2 — structured field extraction

Once the contract is extracted to text, a new worker
(`src/gc/pipeline/dora-register-extractor.ts`) runs. It uses the Tier A/B/C
LLM (per tenant config) with a structured extraction prompt and a JSON
schema matching the RoI template fields.

Extraction prompt skeleton (abbreviated):

```
You are analysing an ICT third-party service contract for the purpose of
populating a DORA Register of Information under Regulation (EU) 2022/2554.

Extract the following fields from the contract text provided. For each
field, output:
- the value
- the source text (exact span from the contract)
- a confidence score from 0 to 1
- if the field cannot be determined from the contract, output null with an
  explanation

Fields to extract: [full schema of ~40 fields]

Contract text: [chunked contract content]
```

Output is stored in `gc_dora_register_entries` with status `draft` and
linked to the source contract doc.

### Step 3 — function criticality inference

A second pass asks the LLM to classify whether the services described
support a "critical or important function" per DORA Article 2 and EBA
guidance. The regulatory corpus provides the grounding text. The output is
stored with a confidence score; the compliance team can override.

### Step 4 — subcontractor chain detection

A third pass looks for references to subcontractors within the contract
(e.g. "Service Provider may engage subcontractors including [X]") and within
supplementary documents the customer has uploaded (e.g. subcontractor lists,
due diligence packs). Each detected subcontractor becomes a row in
`gc_dora_register_subcontractors`.

### Step 5 — human review

The compliance team reviews every extracted entry via
`/gc/dora/register/[id]`. Each field shows the source span from the
contract and the confidence score. The team can accept, edit, or reject
each field. Rejections and edits are logged in `gc_dora_register_changes`.

Status transitions: `draft` → (human review) → `confirmed`.

### Step 6 — concentration risk calculation

A nightly job aggregates all `confirmed` entries and computes concentration
risk along three dimensions: provider, country, parent group. Results are
written to `gc_dora_concentration_assessments`.

### Step 7 — export

On demand, the customer can export the register in:
- ESAs XML/XBRL template format (the official submission format).
- Human-readable PDF for internal review and board reporting.
- CSV for internal data analysis.

Export validation is performed before generation: if any confirmed entry is
missing required fields, the export fails with a specific error per entry.

---

## Integration with the existing two-corpus model

The RoI module is not a separate silo. It integrates with the existing
architecture as follows:

- ICT contracts go into the company evidence corpus with
  `document_type = 'ict_contract'` and `concerns_framework = 'DORA'`.
- DORA regulatory text, EBA guidance, and ESAs implementing technical
  standards are in the EU regulatory hub.
- The RoI entries themselves are structured artefacts that reference both
  corpora: each entry has a source contract (company evidence) and is
  assessed against DORA articles (regulatory hub).
- The SREP scoring engine takes RoI data as an input for the DORA Third-
  Party Risk pillar (weight 25%). A customer with a complete, low-
  concentration-risk RoI scores well; a customer with incomplete or
  high-concentration data scores poorly.

---

## What the RoI module is NOT

- It is not a contract management system. We don't store contract terms for
  the customer's general use; we extract the fields needed for DORA. If the
  customer wants full contract management, they use their CLM.
- It is not a vendor risk management platform. The concentration risk
  calculation is scoped to DORA's specific definitions. We don't score
  vendors on cybersecurity posture or financial health.
- It is not an automated submission tool. We produce the export file; the
  customer submits it through their supervisor's portal. We do not connect
  directly to supervisory submission systems in Phase 1.

---

## Success criteria for the RoI module

In Phase 1, the module is successful if:

1. A customer can go from "I have 50 ICT contracts" to "I have a validated
   Register of Information ready to submit" in under 10 hours of combined
   user time (AI extraction + human review).
2. The AI field extraction achieves at least 85% accuracy on a benchmark
   of 100 real contracts, measured against a manual reference.
3. At least one design partner submits (or is ready to submit) a real
   Register of Information generated through Lawgic to their supervisor.
4. The module renders the customer's current manual process (typically
   spreadsheets + Word documents) visibly obsolete.

If any of these is not met, the module needs iteration before Phase 2
sovereignty work begins.
