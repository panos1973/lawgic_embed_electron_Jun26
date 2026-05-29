# Competitive positioning — who we are and are not competing with

This document is about discipline. The single biggest failure mode for a
repositioning like this is scope creep driven by competitor-watching. Every
time Harvey ships a feature, the temptation is to match it. Every time
OneTrust adds an AI module, the temptation is to follow. This discipline
document exists to resist those temptations.

---

## The three competitive categories and our relationship to each

### Category 1 — law-firm legal AI (Harvey, Legora, Spellbook, Luminance)

**We are not competing with them.** Full stop. They sell to law firms at
seat-based pricing. They optimise for billable-hour workflows, associate
productivity, M&A diligence, and research. Their buyers are partners and
heads of innovation at AmLaw 100 and UK Magic Circle firms.

Lawgic's buyer is the Chief Compliance Officer at an in-house legal and
compliance function at a regulated EU enterprise. Different buyer,
different budget, different success metric, different procurement process.

When someone asks "how do you compare to Harvey?" the correct answer is:
"Harvey makes lawyers faster at law firm work. We make in-house compliance
teams able to prove to their regulator that they're compliant. Different
problem."

### Category 2 — traditional GRC suites (OneTrust, LogicGate, MetricStream, Archer, Diligent, Navex)

**This is our nominal competitive category, but we are architecturally
positioned against them, not alongside them.** These tools are workflow and
control library platforms built between 2012 and 2020. They have strong
implementation partnerships with Big 4 consultancies and deep customer
bases in large enterprises. Their weakness is that they are not AI-native,
their data models are pre-LLM, and their pricing is modular-and-opaque.

Our story against them is three-part:

1. **Built AI-native, not AI-bolted.** The two-corpus retrieval model and
   SREP scoring engine are LLM-grounded from day one. Their AI is a chat
   widget over a relational workflow engine.
2. **Sovereign by construction.** Every one of these vendors is US-
   headquartered or US-owned. We run on EU-sovereign infrastructure with
   open-weight model options. For EU regulated buyers, this is increasingly
   a procurement gate.
3. **Priced against compliance programme budgets, not per-module.** A
   typical OneTrust enterprise deal is six figures and modular; you pay for
   Privacy, then Consent, then TPRM, then Compliance. We offer a single
   compliance platform price.

### Category 3 — regulatory intelligence (Thomson Reuters, Wolters Kluwer, LexisNexis)

**These are content businesses more than software businesses.** They sell
regulatory text, case law, and commentary via subscriptions (Westlaw,
VitalLaw, Practical Law, Lexis+). They have added AI assistants (CoCounsel,
Protégé) that query their content.

Our story against them is simpler: we don't compete on content depth,
because we don't need to. Our regulatory corpus is sufficient for the
compliance workflows we automate. If a customer needs deep case law
research, they can keep their Thomson Reuters subscription. If they need to
continuously monitor their compliance posture against their regulations and
prove it to a regulator, they come to us. These are complements, not
substitutes.

---

## The features we deliberately do not build

The following list is as important as the feature roadmap. Every item here
is a feature that a competitor ships or will ship. We do not match them.

### From the law-firm legal AI category
- Matter management.
- Billable-hour reporting or attorney time tracking.
- Associate productivity analytics.
- Brief drafting for litigation.
- Deposition preparation and witness analysis.
- Legal research for case law across jurisdictions (beyond what our
  compliance workflows require).
- M&A diligence dataroom automation at the depth Harvey and Legora offer.
  We support contract review for DORA Third-Party Risk; we do not build a
  full diligence review product.
- Firm-wide knowledge management (playbooks, precedent libraries) at the
  depth Spellbook and Harvey offer.

### From the traditional GRC category
- General-purpose workflow engine outside compliance (no BPM platform).
- Policy management as a standalone product (we handle policies as
  compliance evidence, not as a separate lifecycle management system).
- Training and awareness content delivery (no LMS).
- Whistleblower hotline or case management (specialist vendors like Navex
  own this; we do not compete).
- GRC consultancy-style frameworks (COSO, COBIT) as deep implementations.
  We support COSO/COBIT tagging on evidence but we are not a COSO tooling
  company.
- SOX compliance as a primary use case. We focus on EU regulations; SOX is
  a US regulatory regime with established US tooling.

### From the regulatory intelligence category
- A general legal research engine for the broader market.
- News aggregation beyond regulatory change monitoring (no "legal news
  feed").
- Commentary and expert analysis content of our own authorship.
- Citator features (KeyCite, Shepard's).

### Horizontal temptations
- Mobile apps. Our buyer uses a laptop. Defer mobile until at least year 3.
- A consumer-facing version. Not applicable.
- A marketplace for third-party compliance modules. Deferred to post-Series A.
- AI agents that autonomously submit regulatory filings. Our human-in-the-
  loop constraint is non-negotiable for the first 24 months.

---

## The features we deliberately DO build

Complement to the above. These are the features that sharpen our
positioning and that no competitor does well today.

1. **DORA Register of Information as a dedicated first-class module.** See
   `05-dora-wedge-module.md`. This is the flagship.
2. **Continuous SREP-based compliance posture scoring** across DORA, AI
   Act, NIS2, GDPR, CSRD. No competitor does this AI-native.
3. **Cross-jurisdictional obligation comparison.** For customers operating
   across multiple EU jurisdictions, side-by-side views of the same
   obligation and its national implementing variations.
4. **Full AI output audit trail** for regulator defensibility. Every AI
   assessment reproducible months later.
5. **Three deployment modes from one codebase** (SaaS, private cloud,
   on-prem air-gapped). See `06-sovereignty-technical-plan.md`.
6. **Per-tenant model tier routing** so customers can choose Tier A, B, or
   C per workflow based on their procurement constraints.
7. **Residency attestation export.** A one-click document the customer can
   hand to their procurement team showing exactly where data lives and what
   models touched it.
8. **Regulatory update bundle** for air-gapped customers.
9. **Evidence library** with obligation mappings — treating company
   evidence as a first-class compliance artefact, not an upload blob.
10. **Greek-language support at depth** as a differentiator for Greek
    customers (a side benefit of our Lawgic legal heritage). No US
    competitor supports Greek well.

---

## How to use this document

Whenever a feature proposal arises, run it through this filter:

1. Does it appear on the "deliberately do not build" list? If yes, the
   default answer is no. If there is a strong reason to reconsider,
   escalate to the founder.
2. Does it appear on the "deliberately do build" list? If yes, it's in
   scope; sequence it against `04-product-roadmap.md`.
3. Does it appear on neither list? Ask: does it strengthen one of the
   three competitive stories above (AI-native, sovereign, EU-wide
   compliance)? If yes, it's a candidate. If no, it's scope creep.

The purpose of the discipline is to compound our positioning. Every
non-built law-firm feature makes our compliance positioning sharper. Every
non-built horizontal GRC feature makes our AI-native positioning sharper.
Every non-built US workflow makes our EU-sovereign positioning sharper.

Narrow wins.
