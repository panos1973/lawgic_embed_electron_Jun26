# Target architecture — hub-and-spoke, sovereign, multi-deployment

This document describes what Lawgic should look like at the end of the
18-month roadmap. It is deliberately ambitious. Not every part of it exists
in month 1; the sequencing is in `04-product-roadmap.md`.

## The four architectural pillars

### Pillar 1 — hub-and-spoke regulatory corpus

The regulatory corpus is restructured into two layers:

**The EU hub** — a single, shared, continuously updated corpus of EU-level
regulation. Every tenant, regardless of jurisdiction, has access to this hub.

Contents of the EU hub:
- Primary regulations: DORA, EU AI Act, NIS2, GDPR, CSRD, Cyber Resilience
  Act, Data Act, MiFID II, AML directives, ePrivacy, REMIT, MDR, IVDR,
  DSA, DMA.
- Secondary legislation: delegated acts, implementing acts, RTS, ITS.
- Supervisory guidance: ESAs (EBA, EIOPA, ESMA), EDPB, ENISA, ECB.
- ECJ case law and opinions of Advocates General.
- EUR-Lex structured source data via SPARQL.

**National spokes** — one per EU member state. Each spoke activates for
tenants that operate in that jurisdiction. Spokes share the same chunking
model, metadata schema, and retrieval pattern as the hub; only the content
and the national regulator taxonomy differ.

Spokes by priority:
1. **Greece (live)** — FEK A/B, Supreme Court, ΙΣΟΚΡΑΤΗΣ, NOMOS, Diavgeia.
2. **Germany (next)** — Bundesanzeiger, BGBl, BaFin, BSI, Juris, BeckOnline.
3. **France** — Légifrance (JORF, codes), ACPR, CNIL, Cour de cassation,
   Conseil d'État.
4. **Italy** — Gazzetta Ufficiale, Banca d'Italia, Garante Privacy, Corte
   di Cassazione.
5. **Spain** — BOE, Banco de España, AEPD, Tribunal Supremo.
6. **Netherlands, Belgium, Nordics, Poland** — second wave.

**Implementation principle.** The same Weaviate collection schema must work
for both the hub and any spoke. The only differences are the
`jurisdiction` metadata field and the ingestion source. Do not create
per-jurisdiction collections; use a single schema with jurisdiction as a
filter.

### Pillar 2 — three deployment modes, one codebase

Lawgic must ship in three deployment shapes from the same codebase:

| Mode                     | Infrastructure                              | Buyer                              |
|--------------------------|---------------------------------------------|------------------------------------|
| **EU sovereign SaaS**    | Multi-tenant on SecNumCloud or equivalent   | Mid-market EU regulated enterprise |
| **Private cloud tenant** | Single-tenant, dedicated EU region          | Large banks, insurers, regulators  |
| **On-prem air-gapped**   | Customer infrastructure, no external calls  | Ministries, central banks, defence |

The implementation implications for this are detailed in
`06-sovereignty-technical-plan.md`. At an architectural level, the critical
decisions are:

1. Every external service dependency must be abstracted behind a service
   interface with at least two implementations: a hosted version (for SaaS)
   and a self-contained version (for on-prem). This includes the LLM layer,
   the embedding service, the vector store, the object storage, and the
   identity provider.
2. No feature may depend on internet access at runtime. All models, all
   reference data, all UI assets must be installable into an air-gapped
   environment.
3. Configuration and licensing must degrade gracefully: an on-prem
   deployment with no Claude API access must still be able to run compliance
   assessments using a self-hosted open-weight model.

### Pillar 3 — the AI layer is model-pluggable

The current Lawgic GC uses Claude (Anthropic) for gap analysis and advisory
chat, Gemini for classification, and Voyage AI for embeddings. This is
excellent for the SaaS tier but is incompatible with sovereign deployments.

The target architecture has three model tiers:

**Tier A — Frontier (SaaS default)**
- Anthropic Claude for gap analysis and chat.
- Gemini for classification.
- Voyage AI for embeddings.
- Use when: customer has accepted EU-region-hosted API providers and wants
  best-in-class quality.

**Tier B — EU-developed commercial (private cloud default)**
- Mistral Large (EU-developed, EU-hosted) for gap analysis and chat.
- Mistral Embed for embeddings.
- Fine-tuned Mistral Small for classification.
- Use when: customer requires EU-headquartered model providers and does not
  want any US vendor contractual exposure.

**Tier C — Open-weight self-hosted (on-prem + sovereign-critical)**
- Qwen 3 27B or Llama 3.3 70B fine-tuned for compliance reasoning.
- Multilingual-e5-large or BGE-M3 for embeddings.
- A lightweight distilled model (e.g. Qwen 3 4B) for classification.
- Use when: customer requires full sovereignty including over the model
  weights, or customer is air-gapped.

The routing layer — which model handles which request — is per-tenant
configuration. A single tenant might use Tier A for advisory chat but Tier B
for compliance assessments; this is a procurement reality, not a technical
constraint.

### Pillar 4 — every output is grounded and auditable

Every AI-generated output in Lawgic must satisfy four properties. These are
not optional and they are what makes the product defensible to a regulator.

1. **Source-linked.** Every claim cites the specific regulatory chunk(s)
   and company evidence chunk(s) that grounded it. The citations are stored
   with the output, not re-derived on retrieval.
2. **Reproducible.** Every output is stored with the exact prompt, the exact
   model version, the exact retrieved context, and the exact output. A
   regulator asking "how did you reach this SREP 3 rating" must be answerable
   months later.
3. **Confidence-annotated.** Where the model is uncertain — for example,
   when retrieved evidence is weak or contradictory — the output must carry
   an explicit confidence indicator. Do not suppress uncertainty to look
   confident.
4. **Human-in-the-loop for high-stakes assessments.** SREP scores that
   inform regulatory submissions must be reviewable and overridable by the
   compliance team, with the override reason stored alongside.

## New surfaces to build on top of the existing GC platform

The existing Lawgic GC surfaces (dashboard, knowledge base, upload,
compliance detail, alerts, timeline, spend, notification settings) remain.
The following new surfaces need to be added.

### `/gc/dora/register` — DORA Register of Information
The flagship wedge module. Full spec in `05-dora-wedge-module.md`.

### `/gc/jurisdictions` — jurisdiction management
Where a customer selects which national jurisdictions they operate in, which
activates the corresponding spokes. A Greek bank expanding into Cyprus would
activate a Cyprus spoke here.

### `/gc/sovereignty` — sovereignty and residency control
Per-tenant configuration of which model tier handles which workflow, which
region data is stored in, and which deployment mode is in effect. Customers
can see their residency posture at a glance and export an attestation.

### `/gc/audit-trail` — AI output audit trail
A full searchable log of every AI-generated output: prompt, model, retrieval
context, output, human overrides. Filterable by regulation, domain, user,
time window. Exportable for regulator requests.

### `/gc/cross-jurisdiction` — cross-jurisdictional comparison
For customers operating across multiple EU jurisdictions, the ability to
compare the same obligation (e.g. "incident reporting under DORA Article 19")
across jurisdictions and see where national implementing measures diverge.
This is a feature no current competitor offers at depth.

### `/gc/evidence-library` — evidence library
A structured, deduplicated view of all company evidence documents, tagged
with the obligations they support. The current upload flow treats documents
as an unstructured ingestion pipeline; the evidence library treats them as
first-class compliance artefacts.

## What the new data model looks like

New tables (or additions to existing tables) that support the target
architecture:

- `gc_jurisdictions` — per-tenant jurisdiction activations.
- `gc_dora_register` — DORA Register of Information entries, one row per
  ICT third-party arrangement.
- `gc_dora_register_contracts` — contract metadata linked to register entries.
- `gc_dora_register_subcontractors` — subcontractor chain per arrangement.
- `gc_ai_audit_trail` — one row per AI-generated output with full context.
- `gc_model_routing` — per-tenant model tier configuration.
- `gc_residency_attestations` — periodic exports of the tenant's residency
  posture for procurement and audit purposes.
- `gc_evidence_library` — structured view over uploaded evidence documents,
  with obligation mappings.

Extensions to existing tables:
- `gc_doc_registry`: add `jurisdiction` (varchar, default 'EU'),
  `hub_or_spoke` (enum: 'hub', 'spoke'), `spoke_country` (varchar, nullable).
- `organizationMembers` (Clerk): add `compliance_role` (enum: 'cco', 'gc',
  'dpo', 'ciso', 'compliance_analyst', 'auditor', 'admin').
- `gc_domain_scores`: add `confidence` (decimal 0-1) and
  `human_reviewed_by` (uuid, nullable).

## Multi-tenancy and org scoping — hardening required

The current Clerk-based org scoping is correct for SaaS. For private cloud
and on-prem deployments it needs to harden:

- Every Weaviate query must filter by `company_id` at the retrieval layer,
  not only at the post-processing layer. A bug at the post-processing layer
  in a multi-tenant context is a data breach.
- Every database query must go through an org-scoped query helper that
  rejects queries without an explicit company_id or a verified admin context.
- On-prem deployments must support a "single-tenant mode" config flag that
  short-circuits tenant isolation logic (because there is only one tenant)
  but still enforces role-based permissions.

This is a security posture question, not just a feature. It needs to be
verified with dedicated tests before any customer goes into production.
