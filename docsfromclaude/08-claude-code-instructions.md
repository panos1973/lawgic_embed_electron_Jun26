# Claude Code — how to work on this codebase

This file is addressed directly to Claude Code. Read it carefully before
making any code changes.

---

## Before you touch any code

1. Read every file in this folder in numerical order (00 through 07).
2. Read `general-counsel-platform.md` in the repo root — this is the
   reference document for what exists today.
3. Explore the actual codebase to verify the current state:
   - `src/app/[locale]/gc/` — existing GC surfaces
   - `src/gc/` — GC-specific components, store, pipeline
   - `src/db/schema.ts` — database schema
   - `src/middleware.ts` — auth and Clerk integration
   - `src/app/[locale]/actions/gc_actions.ts` — server actions
4. Form an understanding of the delta between the current state and the
   target state before proposing any changes.

---

## The golden rule

Every code change must be defensible against the strategic thesis in
`01-strategic-thesis.md`. If a proposed change does not clearly support
(a) the DORA wedge, (b) the sovereignty positioning, (c) the hub-and-spoke
architecture, or (d) the CCO buyer persona — pause and ask the founder
before implementing.

---

## Sequencing discipline

The roadmap in `04-product-roadmap.md` is not a menu. It is a sequence.
Phase 1 work must be completed before Phase 2 work begins. Do not get
distracted by Phase 2 sovereignty refactoring while Phase 1 DORA module
work is still open. The phases overlap in the calendar sense, but each
phase has a primary deliverable that must complete before it is considered
done.

**Phase 1 primary deliverable**: a working DORA Register of Information
module that a real paying customer is using, with audit trail and
human-in-the-loop review flows.

**Phase 2 primary deliverable**: the service abstraction layer,
EU-sovereign hosting, and Tier B/Tier C model integrations, with all three
deployment modes demonstrable.

**Phase 3 primary deliverable**: the German jurisdictional spoke, with
ingestion, German-language UI, and at least one German customer live.

---

## When extending the existing codebase

### Preserve what works

The existing Lawgic GC architecture is sound. Preserve:

- The two-corpus retrieval model (regulatory + company evidence).
- The SREP 1-4 scoring with domain weights.
- The n8n-driven regulatory ingestion pipeline.
- The Weaviate-based hybrid search with framework + company metadata.
- The Clerk-based org-scoping (harden it, don't replace it).
- The Next.js App Router + Server Actions pattern.
- The existing GC surfaces (dashboard, knowledge-base, upload, compliance,
  alerts, timeline, spend, notification-settings).

### Extend, don't replace

When adding a new feature, ask: can this be an extension of an existing
module rather than a new one? For example, the DORA Register of
Information module extends `gc_doc_registry` with a new document type
(`ict_contract`) and adds new tables (`gc_dora_register_entries` etc.)
rather than creating a parallel document store.

### Follow the existing patterns

- New routes go under `src/app/[locale]/gc/<new-feature>/`.
- New screens go under `src/gc/screens/`.
- New server actions go in `src/app/[locale]/actions/gc_actions.ts` or a
  new sibling file if the module is large enough to warrant it.
- New pipeline workers go under `src/gc/pipeline/`.
- Database schema changes go in `src/db/schema.ts` with matching Drizzle
  migrations.
- UI components follow the existing Radix UI + Tailwind pattern.

### Internationalisation matters from day one

Every new user-facing string must be wrapped in `next-intl` translation
calls. The current codebase supports Greek and English. Phase 3 adds
German. Do not hardcode strings in English "to fix later."

---

## When implementing the DORA module specifically

Follow `05-dora-wedge-module.md` as the source of truth. A few specific
guidance points:

1. **The module is user-facing first, then automation-facing.** A
   compliance team must be able to manually create and edit an RoI entry
   without any AI assistance. The AI layer is an accelerant, not a
   prerequisite. Build the manual flow first, then layer the AI extraction
   on top.
2. **Validation must be strict.** The ESAs template has required fields,
   constrained vocabularies, and format requirements. Validation at the
   database level (via Drizzle constraints) and at the API level (via
   Zod schemas) must both be in place before any export functionality ships.
3. **The concentration risk calculation is a scheduled job, not a live
   computation.** It runs nightly and writes to
   `gc_dora_concentration_assessments`. Do not recalculate on every page
   load.
4. **Export must use the exact ESAs XML/XBRL schema.** The latest schema
   is published on the European Banking Authority website. Integrate the
   official schema validator; do not write a custom one.

---

## When implementing the sovereignty layer

Follow `06-sovereignty-technical-plan.md` as the source of truth. A few
specific guidance points:

1. **Start with the service abstraction layer, then migrate existing
   callers.** Do not attempt to swap Claude for Mistral in-place anywhere
   in the codebase until the abstraction layer is in place and tested.
2. **The factory must have deterministic tests.** A test suite that
   verifies the correct service implementation is selected for every
   combination of tenant config and workflow type is required before any
   production tenant uses non-default routing.
3. **The offline installation package is a CI artifact, not an ad-hoc
   build.** Create a dedicated pipeline that produces it on every release
   and tests it in a simulated air-gapped environment before it is
   considered shippable.
4. **Benchmark every model before it touches production.** The acceptance
   bars in `06-sovereignty-technical-plan.md` are not aspirational; they
   are gates.

---

## What to ask the founder about

Always ask, don't assume, when:

- A proposed change adds a new external SaaS dependency. Every new
  dependency complicates the on-prem deployment story.
- A proposed change would require a breaking migration of existing
  tenant data.
- A proposed feature appears on the "deliberately do not build" list in
  `07-competitive-positioning.md` and there is a reason to reconsider.
- A customer has asked for a feature that would substantially expand scope
  beyond the current roadmap phase.
- A security or compliance question arises that is not clearly resolved by
  the existing architectural decisions.

---

## What to just get on with

Don't wait for sign-off on:

- Refactoring that preserves behaviour and improves maintainability.
- Test coverage for existing code.
- Documentation and inline comments.
- Bug fixes that do not change product scope.
- Performance improvements that do not change external contracts.
- UI polish within the existing design language.

---

## The done-ness bar

A feature is done when:

1. It is functional and tested (unit + integration).
2. It respects multi-tenancy isolation (verified by a tenant-isolation test).
3. It is observable (structured logs, metrics, errors caught).
4. It is documented (both user-facing docs where relevant, and inline
   comments for non-obvious logic).
5. It is internationalised (no hardcoded user-facing strings).
6. It is accessible (WCAG 2.1 AA minimum for new UI).
7. Its AI outputs, if any, are in the audit trail.

A feature is not done when "it works on my branch."

---

## A note on scope

The founder is a self-funded founder with strong product instincts and a
history of shipping. The temptation will be to say yes to every feature
request. The role of this document and of the strategic files is to say no
on behalf of the product focus.

When in doubt, less is more. Narrow wins.
