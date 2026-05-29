# Lawgic — strategic repositioning package

This folder contains the full strategic context, product repositioning, and
implementation plan for transforming Lawgic GC from a "legal AI platform for
general counsels" into **the AI-native sovereign compliance operating system
for EU regulated enterprises**.

This package is written for Claude Code to read end-to-end before making any
changes to the existing codebase. Do not start editing files until every
document in this folder has been read.

---

## How to read this folder

Read the files in numerical order. Each file builds on the previous ones.

| # | File                                 | Purpose                                                                 |
|---|--------------------------------------|-------------------------------------------------------------------------|
| 00 | `00-README.md`                      | This file. Orientation and reading order.                              |
| 01 | `01-strategic-thesis.md`            | The full repositioning thesis and why it's defensible.                 |
| 02 | `02-current-state.md`               | Recap of what already exists (Lawgic + Lawgic GC).                     |
| 03 | `03-target-architecture.md`         | The target architecture: hub-and-spoke, sovereign, multi-tenant.       |
| 04 | `04-product-roadmap.md`             | The 18-month roadmap: Prove → Sovereign → Expand.                      |
| 05 | `05-dora-wedge-module.md`           | The DORA Register of Information module spec. This is the wedge.       |
| 06 | `06-sovereignty-technical-plan.md`  | How to actually build the sovereign deployment stack.                   |
| 07 | `07-competitive-positioning.md`     | How to position vs. Harvey, OneTrust, etc. — and what NOT to build.    |
| 08 | `08-claude-code-instructions.md`    | Specific instructions for Claude Code when extending the codebase.     |

There is also a reference document already in the repo called
`general-counsel-platform.md`. That file describes the current state of Lawgic
GC in detail. Claude Code should treat it as the single source of truth for
"what exists today." Every change proposed in this package builds on that file.

---

## The one-sentence version

**Lawgic is becoming the sovereign AI compliance OS for the EU's Chief
Compliance Officer, and the DORA Register of Information is the wedge.**

If you keep that sentence in mind while reading everything else, the design
decisions in these files will make sense.

---

## What this package is NOT

- It is not a greenfield rewrite. The existing Lawgic GC architecture
  (two-corpus model, SREP scoring, n8n ingestion, Weaviate, Clerk org-scoping)
  is the foundation. We extend it; we do not replace it.
- It is not a pivot away from Greek legal. The Greek legal platform becomes
  one jurisdictional "spoke" under the new EU-wide hub.
- It is not a "build everything Harvey has" plan. The whole point of the
  repositioning is to NOT compete with Harvey feature-for-feature. Read
  `07-competitive-positioning.md` carefully before suggesting any new feature
  that overlaps with what Harvey or Legora already does.

---

## How Claude Code should use this package

1. Read every file in this folder in order.
2. Read `general-counsel-platform.md` to understand the current implementation.
3. Before proposing any code change, verify that the change is consistent with
   the strategic thesis in `01-strategic-thesis.md`.
4. When implementing, follow the sequencing in `04-product-roadmap.md`. Do not
   jump ahead to Phase 2 or Phase 3 work without completing Phase 1 foundations.
5. When in doubt about scope, consult `08-claude-code-instructions.md`.
