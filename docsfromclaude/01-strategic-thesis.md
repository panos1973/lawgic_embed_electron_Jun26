# Strategic thesis — why Lawgic is repositioning

## The one-line thesis

Lawgic is repositioning from "Greek legal AI platform" to **the AI-native
sovereign compliance operating system for EU regulated enterprises**, with the
DORA Register of Information as the commercial wedge and a hub-and-spoke EU
architecture as the expansion vehicle.

## Why this thesis and not another

### The legal AI market is the wrong fight for Lawgic

Harvey raised at an $11B valuation in March 2026 on roughly $100M ARR. Legora
tripled to a $5.55B valuation on a $550M Accel-led Series D in the same month.
Both sell to law firms at $1,000-$3,000 per lawyer per month with high seat
minimums. They are funded, staffed, and contractually embedded in AmLaw 100
and UK Magic Circle firms.

Lawgic is self-funded. Trying to out-feature Harvey or Legora on billable-hour
productivity for law firms is a war of attrition Lawgic cannot win. Every
euro Lawgic spends chasing law-firm feature parity is a euro not spent on the
fight Lawgic can actually win.

### The GRC market has an AI-native gap that is Lawgic-shaped

The EU enterprise GRC category is older, fragmented, and structurally
un-AI-native. OneTrust, Thomson Reuters Regulatory Intelligence, Wolters
Kluwer Enablon, LogicGate, MetricStream, Diligent, Archer — all of them have
strong workflow engines and content libraries built between 2012 and 2020,
and all of them have "added AI" by bolting chat widgets onto pre-LLM data
models.

None of them offer what Lawgic GC already architecturally has: a two-corpus
retrieval model (regulatory + company evidence) with AI-powered continuous
compliance scoring grounded in the company's own documents, producing a live
posture score per regulation per domain.

The category buyers know — the Chief Compliance Officer at a DORA-supervised
bank, a BaFin-regulated German insurer, a French mid-cap under CSRD — are
drowning in overlapping regulations and actively looking for something better.
Their current tooling is workflow-era. Lawgic is LLM-era.

### The sovereignty window is real and closes in 18-24 months

The research made the sovereignty picture clearer than expected. In
December 2025, S3NS (Thales + Google) achieved full SecNumCloud 3.2
qualification for its PREMI3NS platform — meaning there is now a truly
sovereign Google Cloud in France. Microsoft Bleu reached only "Milestone 1"
in November 2025 and is not yet fully qualified. In February 2026, France
opened a formal procurement to move the national Health Data Hub off Azure.
EU data protection authorities are openly questioning whether the EU-US Data
Privacy Framework remains defensible after the Trump administration's
dismissal of PCLOB members in January 2025.

For French banks under ACPR, German entities under BaFin and BSI, Italian
entities under PSN, Dutch institutions under DNB — an AI platform running on
US-controlled hyperscaler infrastructure is now a live procurement debate,
not an automatic approval. This was not the case 24 months ago.

The window will close. Microsoft and AWS will eventually complete their
SecNumCloud partnerships, likely in 2027. The ~18 months between now and then
is the window in which a sovereign-by-construction challenger can establish
reference customers and certifications that hyperscaler-hosted competitors
will not be able to match after the fact.

### The regulatory spend is convergent and large

Four simultaneous compliance programmes are hitting EU enterprises in 2026-27:

- **DORA** — 22,000 in-scope financial entities, typical budget €2-5M,
  large banking groups running €100M programmes. Register of Information is
  cited by 46% of entities as their single hardest requirement.
- **EU AI Act** — high-risk system obligations fully enforceable August 2026.
  Large enterprise budgets $8-15M, mid-market $2-5M.
- **NIS2** — mid-sized organisations typically spending €200K-€600K in year
  one. Germany's BSI registration live April 2026.
- **CSRD** (post-Omnibus) — narrowed but still mandatory for Wave 1
  reporters in 2026-27.

Gartner estimated the AI governance tooling market at $492M in 2026, growing
to $1B+ by 2030 — a category that did not exist 24 months ago. Every large
regulated EU enterprise now runs at least four parallel compliance programmes
with separate regulators, separate evidence requirements, separate audit
trails. This is the condition under which buyers stop tolerating point tools
and start buying platforms.

## The three commitments that define the positioning

The positioning reduces to three commitments that no US-based competitor can
credibly make simultaneously:

1. **Sovereign by construction** — all inference happens inside EU
   sovereign infrastructure (SecNumCloud-qualified or equivalent) on
   EU-developed or open-weight models, with no customer data ever leaving EU
   jurisdiction.
2. **Auditable grounding** — every AI output carries citation lineage back
   to the regulatory source text and the company evidence that informed it,
   with confidence intervals, not black-box answers.
3. **No training on customer data** — contractually guaranteed, with
   technical enforcement. Customer compliance documents, internal policies,
   and audit trails never enter a shared model training pipeline.

Harvey cannot make commitment #1 without rebuilding on non-OpenAI
infrastructure. OneTrust cannot make commitment #2 without rebuilding their
retrieval layer. Thomson Reuters and Wolters Kluwer will struggle with all
three given their global data architectures.

## The buyer is the CCO, not the GC

This is the most important tactical shift. Every existing Lawgic GC decision
was made with the General Counsel as the buyer persona. That persona needs
to be updated.

ACC's 2026 Chief Legal Officers survey found 79% of CLOs now report directly
to the CEO, 70% manage functions beyond legal, and 72% cite industry-specific
regulatory enforcement as their top concern. But the actual budget for DORA,
AI Act, NIS2 and CSRD compliance typically sits with the Chief Compliance
Officer or Head of Regulatory, not the GC. In DORA-supervised entities the
CISO and DPO have veto rights over any ICT third-party arrangement.

The practical implication: procurement cycles are 9-14 months, involve the
CCO + CISO + DPO + procurement + often the internal audit function, and
mandate a concentration-risk assessment of the vendor itself. A law-firm
seat-based sales motion fails immediately in this environment.

The product implication: the UX, the dashboards, the language, the metrics
must all be compliance-oriented, not legal-oriented. "Audit readiness,"
"regulatory exposure," "control effectiveness," "gap closure rate" —
not "matter management" or "research productivity."

## What this means for the product

1. Lawgic GC is no longer a "version of Lawgic." It is the flagship product.
   The Greek legal platform becomes a jurisdictional spoke underneath it.
2. The first paying customer workflow must be DORA Register of Information,
   not general compliance monitoring. Wedge narrow, then expand.
3. The infrastructure must move to EU-sovereign hosting with an on-prem
   deployment path, on the same codebase, within 12 months.
4. Every roadmap decision must be testable against the three commitments
   above. If a feature weakens any of them, it does not ship.

## What this means for fundraising or revenue

The thesis supports two viable commercial paths:

- **Self-funded expansion** — if the first 3 paying customers land at
  €150-400K ACV with 80%+ gross margins, Lawgic can self-finance expansion
  into Germany and France without dilution. This is the preferred path and
  preserves optionality.
- **Strategic EU-sovereign funding round** — if European sovereign-tech
  investors (e.g. the EU's European Tech Champions Initiative, France's
  Definvest, Germany's Sovereign Tech Fund, or private sovereign-thesis VCs
  like Project A, HV Capital, Elaia) engage, the thesis is fundable at
  €5-15M Series A. This accelerates expansion but is not required.

Whichever path, the product roadmap in this package is designed to stand up
under either.
