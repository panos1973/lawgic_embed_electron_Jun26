# Product roadmap — 18 months, three phases

This document translates the thesis and target architecture into a
sequenced delivery plan. It is deliberately conservative: every phase must
be completable before the next begins, and the transition gates are explicit.

---

## Phase 1 — Prove (months 0-6)

### Goal

Land 2-3 paying design-partner customers on the DORA Register of Information
wedge module, and produce a publishable case study.

### Non-goals for Phase 1

- Do not start international expansion.
- Do not move to sovereign infrastructure yet (the existing Azure EU setup is
  adequate for Greek design partners).
- Do not build features that are not directly required to close the first
  paying contract.
- Do not pursue law firm customers; they are out of scope for this product.

### Deliverables

**Product**
- DORA Register of Information module, fully specified in
  `05-dora-wedge-module.md`. This is the wedge and the first paying
  workflow.
- Audit trail surface (`/gc/audit-trail`) — every AI output logged and
  exportable. This is non-negotiable for any regulated customer.
- Human-in-the-loop SREP review flow — compliance team can accept, override,
  or escalate any AI-generated SREP score, with the override reason stored.
- A DORA-specific onboarding flow that takes a new tenant from zero to a
  first populated Register of Information in under 4 hours of guided setup.
- Confidence indicators on every compliance assessment.

**Architecture hardening**
- Harden the multi-tenancy model. Every Weaviate query filtered by
  company_id at retrieval. Every SQL query routed through a company-scoped
  helper.
- Add the new roles: CCO (primary compliance buyer), CISO, DPO, Compliance
  Analyst, Auditor. The existing GC role persists as one of several.
- Add the `gc_ai_audit_trail` table and wire every AI call into it.

**Commercial**
- Close Alpha Bank as the first paying DORA design partner (if feasible;
  DoValue Greece is the backup).
- Close one public-sector customer on AI Act compliance (AADE is the
  natural candidate given existing relationship).
- Produce one published case study with real numbers — time saved on RoI
  generation, gap closure rate, audit readiness improvement.
- Define pricing: a platform base fee (€80-120K/year) plus per-module fees
  (DORA RoI €60-80K, AI Act €40-60K, NIS2 €40-60K, CSRD €40-60K). Target ACV
  for a first customer: €150-200K, rising to €300-400K as more modules
  activate.

### Phase 1 gates (must be met before Phase 2 begins)

1. At least two signed paying contracts with EU regulated enterprises.
2. At least one customer producing a live DORA Register of Information on
   the platform, submitted or ready for submission to their supervisor.
3. A documented, tested multi-tenancy isolation boundary with evidence
   (penetration test or equivalent) that no cross-tenant leakage is possible.
4. A published case study with the design partner's permission.

If any of these gates is not met by month 6, Phase 2 does not start.
Instead, extend Phase 1 until they are met.

---

## Phase 2 — Sovereign (months 4-12)

### Goal

Make the platform credibly sovereign. Move infrastructure to EU-sovereign
hosts, add EU-developed and open-weight model tiers, and prove the three
deployment modes work from a single codebase.

### Why this runs parallel to Phase 1 (months 4-12 overlaps 0-6)

Two reasons. First, the sovereignty window is closing — every month that
passes, a hyperscaler gets closer to a full SecNumCloud qualification.
Second, larger design-partner candidates (Alpha Bank, French banks) will ask
about sovereign deployment on call two; having a credible answer in
progress is worth more than having it later.

### Deliverables

**Infrastructure**
- Production SaaS deployment moved to an EU-sovereign host. Primary target:
  OVHcloud (fully EU-headquartered, SecNumCloud 3.2 qualified) or Outscale.
  Fallback: S3NS/PREMI3NS (Thales + Google, SecNumCloud 3.2 qualified in
  December 2025).
- Dedicated private cloud tenant mode. Same codebase, single-tenant
  deployment. Tested end-to-end.
- On-prem air-gapped deployment mode. Full offline installation package with
  bundled models, reference data, and installation scripts. Tested on at
  least one customer-simulated environment.
- SecNumCloud attestation published on the website (or equivalent
  qualification for the chosen host).

**Model layer**
- Model abstraction layer: every LLM call routed through a provider-agnostic
  interface with Tier A (Claude), Tier B (Mistral), and Tier C (Qwen 3 27B
  self-hosted) implementations.
- Per-tenant model tier configuration: a tenant can set "Tier B default,
  Tier A for advisory chat, Tier C for RoI generation" independently.
- Qwen 3 27B fine-tuning prototype for compliance reasoning, evaluated
  against Claude Sonnet on a 200-example benchmark across DORA, NIS2, and
  AI Act assessment tasks. Acceptance target: Qwen 3 27B reaches 85% of
  Claude Sonnet accuracy on the benchmark.
- Embedding layer abstraction: Voyage AI (SaaS default) and multilingual-e5
  or BGE-M3 (sovereign default) produce comparable retrieval quality on a
  50-query test set.
- Classification layer abstraction: Gemini (SaaS default) and a distilled
  Qwen 3 4B (sovereign default).

**Surfaces**
- `/gc/sovereignty` page — per-tenant configuration of model tier, region,
  deployment mode, with exportable attestation.
- Sovereignty posture badge on the customer's dashboard showing current
  tier and region.

**Commercial**
- One paying customer on the private cloud tenant deployment.
- One paying customer (or LOI) on the on-prem air-gapped deployment. A
  Greek ministry or a central bank-adjacent entity is the natural target.
- Updated positioning materials (website, pitch deck, proposal templates)
  reflecting sovereign-first positioning and the three deployment modes.

### Phase 2 gates (must be met before Phase 3 begins)

1. Production SaaS running on an EU-sovereign host with a published
   qualification.
2. All three deployment modes functional, each with at least one live or
   committed customer.
3. Tier B (Mistral) and Tier C (Qwen 3) models both capable of running a
   DORA RoI generation workflow end-to-end with quality comparable to Tier A.
4. A signed private cloud or on-prem contract with a non-Greek customer, or
   a clear LOI to sign within 90 days.

---

## Phase 3 — Expand (months 10-18)

### Goal

Activate Germany as the first international jurisdictional spoke, close at
least two German customers, and validate the hub-and-spoke model works
commercially.

### Why Germany first

The research was unambiguous on this. Germany has the largest EU regulated
enterprise base, the deepest sovereignty culture (BSI, IT-Grundschutz, BaFin
cloud guidance), the most complex regulatory environment (which drives
willingness to pay), and the most recent regulatory activity (NIS2UmsuCG
passed November 2025, BSI registration live April 2026). France is second
given SecNumCloud alignment but is a harder commercial entry due to local
language expectations and established OneTrust presence.

### Deliverables

**Content and ingestion**
- German jurisdictional spoke fully ingested:
  - Bundesanzeiger (federal gazette).
  - BGBl (Bundesgesetzblatt) — federal law publications.
  - BaFin circulars, guidance, and enforcement decisions.
  - BSI publications and IT-Grundschutz catalogues.
  - Juris and/or BeckOnline for case law (requires commercial agreement).
  - BGH (Bundesgerichtshof) and BVerfG (Bundesverfassungsgericht) decisions.
- German-language legal tokenizer and embedding evaluation. German legal
  language is compounded and high-density — out-of-the-box multilingual
  embeddings may underperform and need fine-tuning.
- Cross-jurisdictional comparison working: a customer can see DORA Article
  19 alongside its German implementing guidance (BaFin) and Greek
  implementing guidance side by side.

**Product**
- German-language UI (full localisation, not just a translation layer).
- German-specific DORA workflow with BaFin-specific submission templates
  and terminology.
- NIS2 compliance module tuned for the German NIS2UmsuCG transposition.

**Commercial**
- A German go-to-market partner: either a local reseller, a compliance
  consultancy, or a strategic hire. Attempting direct sales in Germany from
  Athens is not a viable motion.
- At least two paying German customers (tier-2 banks, insurers, or
  critical-infrastructure operators).
- Updated pricing in euros with German market benchmarks.

### Phase 3 gates (what success looks like at month 18)

1. Two or more paying non-Greek customers running production workloads.
2. Demonstrable cross-jurisdictional workflow with one customer operating
   in both Greece and Germany.
3. Revenue run-rate sufficient to self-finance expansion into France or
   alternatively to raise a Series A on strong sovereign-thesis metrics.

---

## What is explicitly NOT on the roadmap

Read `07-competitive-positioning.md` for the fuller treatment, but at a
roadmap level these items are deliberately out of scope for the first 18
months:

- Law-firm-oriented features (matter management, billable-hour reporting,
  associate productivity tooling).
- M&A diligence workflows beyond what DORA third-party risk already requires.
- Litigation analytics, brief analysis, deposition prep.
- US market entry or US legal content ingestion.
- A mobile app. The buyer is a CCO; the buyer uses a laptop.
- Consumer-facing anything.

Any proposal to add items from this list before month 18 should be
escalated to the founder before implementation.

---

## Cadence and checkpoints

- **Monthly review** — progress against phase gates and commercial pipeline.
- **Quarterly re-planning** — does the thesis still hold? Has a competitor
  moved? Has the regulatory landscape shifted?
- **Phase transition review** — gate check, explicit go/no-go decision,
  updated plan for the next phase.

The roadmap is deliberately designed so that a single bad phase does not
destroy the whole plan. If Phase 1 takes 9 months instead of 6, Phase 2
still works. If Germany is harder than expected, Phase 3 can shift to
France. The foundational work — the DORA module, the sovereignty layer, the
hub-and-spoke corpus — compounds regardless of which specific customer
closes first.
