# Current state — what exists today

This document summarises what Lawgic already has. It is a deliberately concise
companion to the detailed `general-counsel-platform.md` file that already lives
in the repo. Claude Code should read both before making any changes.

## The two products as they stand today

### Lawgic (the original platform)

The original Lawgic product serves the Greek legal market.

- ~90 years of Greek legislation, fully ingested, article-level chunked.
- 350,000+ Greek court decisions, including Supreme Court (ΙΣΟΚΡΑΤΗΣ, NOMOS).
- FEK A and B documents (the Greek official gazette), automatically ingested.
- Diavgeia (Greek public sector transparency) polling via n8n.
- Voyage AI embeddings (voyage-context-3, 1024 dimensions) + Weaviate +
  Elasticsearch for hybrid search.
- Gemini for document classification and metadata extraction.
- A proprietary Greek legal tokenizer with roughly 1.34 tokens/word efficiency.
- A planned sovereign Greek Legal LLM (pre-training on GRNET's ARIS
  supercomputer using Qwen3 30B base).
- Enterprise clients in production: AADE (Greek Tax Authority), DoValue Greece.
- Pipeline: Alpha Bank, Ministry of the Aegean.
- Awards: Platinum & Gold at AI & Data Awards 2026. Top 4 Legal Application
  in Europe per IFLR1000.

This is three years of self-funded development and represents a genuinely
strong defensible asset in the Greek market.

### Lawgic GC (the new compliance-facing product)

The Lawgic GC product extends the Lawgic infrastructure to serve in-house
legal and compliance teams. It is fully documented in
`general-counsel-platform.md`. The headline elements:

- A two-corpus architecture: (1) regulatory corpus and (2) company evidence
  corpus, both chunked and embedded into Weaviate with company + framework
  metadata.
- SREP 1-4 scoring engine: AI-powered gap analysis compares regulatory chunks
  against company evidence chunks and assigns an SREP score per (company,
  regulation, domain), with domain-weighted aggregation.
- First-class framework support with domain decompositions: GDPR (9 domains),
  DORA (5 pillars with explicit weights), NIS2, AI Act (sector-tier aware),
  MiFID II, AML, ePrivacy, REMIT, MDR, Cyber Resilience Act.
- Automated regulatory ingestion via n8n from EUR-Lex SPARQL, Greek Diavgeia,
  and RSS feeds.
- Compliance advisory chat (Claude-powered) grounded in the regulation and
  company document corpus.
- A live compliance posture dashboard with overall score, per-framework
  scores, urgent obligations, regulatory alerts, legal spend tracking, and
  savings attribution.
- Stack: Next.js 14 + App Router, React 18, TypeScript, PostgreSQL + Drizzle,
  Clerk (org-aware), Anthropic Claude, Gemini, Voyage AI, Weaviate, Azure
  Blob Storage.

Lawgic GC has **not been tested with a real paying customer yet**. This is
the single most important fact for the next six months.

## Additional Lawgic assets worth knowing about

- **Lawgic Translate** — bidirectional AI legal translation engine (Greek,
  English, French). Already launched.
- **Nomikon** — Electron app for cleaning and structuring Greek legal
  documents into JSONL for LLM pre-training.
- **NVIDIA Inception** — application submitted.
- **Koutalidis Law Firm integration** — deep operational integration (ISO
  certifications, SharePoint conference management, homepage, LinkedIn
  strategy). This is a channel asset.
- **AI & Data Awards 2026** — Platinum and Gold winner, which is a material
  credibility asset for enterprise sales and for sovereign-tech fundraising.

## What is good enough to keep and build on

The architectural decisions behind Lawgic GC are the foundation the new
strategy rests on. Do not rebuild any of the following — extend them:

- The two-corpus model (regulatory + company evidence).
- The SREP 1-4 scoring with domain weights.
- The n8n-driven regulatory ingestion with three tiers (RSS/API,
  Playwright, manual upload).
- The Weaviate-based hybrid retrieval with framework + company metadata tags.
- The Clerk-based org-scoping and role model (GC, Lawyer, Admin).
- The Next.js App Router + Server Actions pattern for the GC surfaces.

## What needs to change

The strategic repositioning requires the following conceptual shifts. The
implementation shifts that follow from these are detailed in
`03-target-architecture.md` and `04-product-roadmap.md`.

1. **The buyer persona shifts from General Counsel to Chief Compliance
   Officer.** The existing role model needs a new primary role (CCO or
   "Compliance Owner") with appropriate dashboard and permission semantics.
2. **The flagship workflow shifts from "general compliance overview" to
   "DORA Register of Information generation."** The RoI needs to become a
   first-class, named, dedicated module, not a view over the existing
   obligations table.
3. **The deployment model needs three shapes from the same codebase: EU
   sovereign SaaS, private cloud tenant, on-prem air-gapped.** The existing
   Azure Blob + Azure OpenAI assumptions do not survive this.
4. **The regulation corpus needs to be restructured as a hub-and-spoke
   model.** The EU-level hub (DORA, AI Act, NIS2, GDPR, CSRD, CRA, Data Act,
   MiFID II) is shared across all tenants. National law "spokes" are
   per-jurisdiction and activate based on the customer's operating countries.
   The Greek legislation corpus is the first spoke; Germany is the second.
5. **The model layer needs a sovereign option.** At least one deployment
   tier must run on EU-developed or open-weight models (Mistral, Llama 3.3,
   Qwen 3) hosted on EU-sovereign infrastructure, with no inference ever
   traversing US-controlled APIs.

## What absolutely must not change

- The commitment to grounded, citable AI output. Every compliance assessment
  must be traceable to source regulatory text and source company evidence.
- The org-scoping model. A compliance team's internal documents must never
  be retrievable outside their organisation's scope under any circumstance.
- The audit trail. Every AI-generated assessment, score, or recommendation
  must be logged with inputs, outputs, model version, and timestamp for
  regulator defensibility.
