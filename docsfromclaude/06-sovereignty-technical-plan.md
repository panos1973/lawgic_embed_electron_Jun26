# Sovereignty technical plan — how to actually build this

This document translates the "three deployment modes from one codebase"
architectural pillar into a concrete technical plan. It is the hardest
engineering track in Phase 2 and deserves careful sequencing.

---

## The three deployment shapes

Recap from `03-target-architecture.md`:

| Mode                     | Infrastructure                              | Network access              |
|--------------------------|---------------------------------------------|-----------------------------|
| **EU sovereign SaaS**    | Multi-tenant, SecNumCloud-qualified host    | Full outbound internet      |
| **Private cloud tenant** | Single-tenant, dedicated EU region          | Controlled outbound         |
| **On-prem air-gapped**   | Customer datacenter                         | Zero external dependencies  |

The engineering task is to make the same codebase run in all three without
feature forks. Every dependency must have a self-contained implementation.

---

## Dependency inventory — what the codebase currently relies on

Based on the current Lawgic GC stack, here is what needs abstracting:

| Concern          | Current (SaaS)        | Sovereign equivalent                    |
|------------------|-----------------------|-----------------------------------------|
| LLM chat/reasoning | Anthropic Claude    | Mistral Large (Tier B), Qwen 3 27B self-hosted (Tier C) |
| LLM classification | Google Gemini       | Mistral Small (Tier B), Qwen 3 4B self-hosted (Tier C)  |
| Embeddings       | Voyage AI (voyage-context-3) | Mistral Embed (Tier B), BGE-M3 or multilingual-e5-large (Tier C) |
| Vector store     | Weaviate              | Weaviate self-hosted (already portable) |
| Relational DB    | PostgreSQL            | PostgreSQL (already portable)           |
| Object storage   | Azure Blob            | S3-compatible (MinIO for on-prem)       |
| Auth             | Clerk                 | Keycloak or self-hosted OIDC for on-prem |
| Background jobs  | n8n (SaaS or self-hosted) | n8n self-hosted                    |
| Email            | SendGrid or equivalent | SMTP relay (customer-provided)         |
| Monitoring       | Hosted (Sentry, Datadog) | Self-hosted Grafana/Loki             |

Every one of these needs a clean service interface before Phase 2 can
progress.

---

## The service abstraction layer

Create a service abstraction layer under `src/services/`. Each service has:

1. A TypeScript interface describing its contract.
2. Two or more implementations (hosted + self-hosted).
3. A factory that returns the correct implementation based on deployment
   mode and tenant configuration.

### Example: LLM service

```typescript
// src/services/llm/interface.ts
export interface LlmService {
  generate(params: GenerateParams): Promise<GenerateResult>;
  stream(params: GenerateParams): AsyncIterator<GenerateChunk>;
  getModelId(): string;
  getTier(): 'A' | 'B' | 'C';
}

// src/services/llm/claude.ts
export class ClaudeLlmService implements LlmService { ... }

// src/services/llm/mistral.ts
export class MistralLlmService implements LlmService { ... }

// src/services/llm/vllm.ts  (for self-hosted Qwen/Llama)
export class VllmLlmService implements LlmService { ... }

// src/services/llm/factory.ts
export function getLlmService(
  tenant: Tenant,
  workflow: WorkflowType
): LlmService {
  const tier = tenant.modelRouting[workflow] ?? tenant.defaultTier;
  switch (tier) {
    case 'A': return new ClaudeLlmService(...);
    case 'B': return new MistralLlmService(...);
    case 'C': return new VllmLlmService(...);
  }
}
```

Every LLM call in the codebase must go through this factory. No direct
imports of `@anthropic-ai/sdk` or `@google/generative-ai` outside the
service layer.

### Same pattern for embeddings, classification, storage, auth

Repeat the pattern for every external dependency. This is unglamorous
engineering work but it is the foundation of the sovereign deployment model.
Without it, every new customer requiring a non-default configuration is a
custom branch.

---

## Self-hosted model infrastructure

For Tier C (open-weight self-hosted), the model serving layer is a new
infrastructure component.

### Recommended stack

- **Model server**: vLLM for inference. It is the highest-throughput
  open-weight serving engine as of 2026, supports most major model
  architectures, and has a stable OpenAI-compatible API.
- **Model gateway**: LiteLLM or Portkey self-hosted. Provides a unified
  OpenAI-compatible API over multiple backends, handles routing, logging,
  and rate limiting.
- **Hardware targets**:
  - SaaS sovereign tier: GPU-equipped VMs on OVHcloud or Outscale (A100 or
    L40S for Tier C).
  - Private cloud tenant: dedicated GPU allocation per customer, sizing
    based on concurrent user load.
  - On-prem air-gapped: customer-supplied hardware, with a supported-
    configuration matrix we publish and test against.

### Model selection

Based on the research and on practical considerations:

- **Tier B primary**: Mistral Large (via Mistral's enterprise API hosted
  in France) for reasoning and chat. Mistral Small for classification.
  Mistral Embed for embeddings. All EU-headquartered, EU-hosted, with
  contractual no-training-on-customer-data guarantees.
- **Tier C primary**: Qwen 3 27B for reasoning and chat. Qwen 3 4B
  distilled for classification. BGE-M3 or multilingual-e5-large for
  embeddings. All open-weight, all self-hostable.
- **Tier C fallback**: Llama 3.3 70B if Qwen performs poorly on benchmark
  tasks for legal/compliance reasoning.

### Benchmark requirement before Phase 2 completion

Before any customer goes live on Tier C, we must have a published
benchmark comparing Tier A (Claude Sonnet) to Tier C (Qwen 3 27B) on at
least 200 compliance-reasoning tasks across DORA, NIS2, AI Act, and GDPR.
The acceptance bar is 85% of Tier A accuracy on gap analysis, 80% on
criticality classification, 90% on structured extraction.

If Tier C cannot reach these thresholds, two options:
1. Fine-tune Qwen 3 27B on a Lawgic-specific compliance reasoning dataset
   generated from the regulatory corpus and synthetic customer evidence.
2. Move Tier C to Llama 3.3 70B with the same benchmarks.

---

## Deployment packaging

### Offline installation package

For on-prem air-gapped deployments, produce a single installation archive
containing:

- Container images for every service (Next.js app, Postgres, Weaviate,
  MinIO, Keycloak, LiteLLM, vLLM, n8n).
- Model weights for Tier C models (Qwen 3 27B, Qwen 3 4B, BGE-M3).
- A pre-populated EU regulatory hub snapshot (the latest EUR-Lex +
  guidance dataset as of the release).
- Installation scripts (Helm charts + Docker Compose alternatives).
- A supported-hardware matrix document.
- A runbook for day-2 operations: backup, restore, model update, regulatory
  data refresh via sneaker-net.

The archive is built by a CI pipeline on every release. Size budget: under
150GB compressed.

### Regulatory data refresh for air-gapped customers

Air-gapped deployments cannot poll EUR-Lex directly. The refresh flow:

1. Lawgic maintains a signed "regulatory update bundle" released monthly.
2. The bundle contains the diff of EUR-Lex, ESAs, EDPB content since the
   last release, in the same format the online ingestion pipeline produces.
3. The customer's admin downloads the bundle from a signed distribution
   channel (could be a web portal accessible from a separate network).
4. The admin imports the bundle via an offline import tool.
5. Import is signature-verified and idempotent.

This is the same pattern antivirus vendors have used for decades. It works.

---

## The Qwen testing question

From the earlier conversation: the founder explicitly wants to test Qwen
3.5 9B, and has not yet tried it. A separate Track 2 deep dive was
discussed. This section captures the technical approach to answer that
question properly.

### The test plan

1. **Hardware setup**: a single L4 or L40S GPU instance on Scaleway or
   OVHcloud. Approximately €150-300/month.
2. **Models to evaluate**:
   - Qwen 3 9B (baseline, the founder's preferred starting point)
   - Qwen 3 27B (the target for Tier C production)
   - Mistral Small 3 (EU-developed comparator)
   - Llama 3.3 70B (open-weight comparator, needs larger hardware)
3. **Benchmark tasks**:
   - Structured extraction of RoI fields from 50 real ICT contracts.
   - Gap analysis for 40 (company, regulation, domain) tuples with known
     correct SREP scores.
   - Criticality classification on 30 DORA function descriptions.
   - Multilingual performance: same tasks in Greek, German, French.
4. **Acceptance bar**:
   - Qwen 3 9B: if it reaches 80% of Claude Sonnet accuracy, ship it as
     the low-footprint Tier C option for small customers.
   - Qwen 3 27B: target 85% of Claude Sonnet accuracy. If achieved, the
     primary Tier C model.
   - Concurrency: Tier C must support at least 5 concurrent users per GPU
     with acceptable latency (<10s for a typical gap analysis).

### Realistic expectation

For retrieval-grounded, structured-extraction tasks, open-weight models in
the 9B-27B range are often within 90% of frontier model quality, because
the hard work is in retrieval, not generation. For complex multi-step
compliance reasoning without retrieval, the gap is wider — frontier models
will likely hold a 15-25% advantage through 2027.

The practical implication: use Tier A (Claude) for complex advisory chat
and Tier C (Qwen) for structured workflows like RoI generation, even within
the same tenant. The per-workflow routing model in the service abstraction
supports this exactly.

---

## What good looks like at end of Phase 2

- Every external dependency has a service abstraction with at least two
  working implementations.
- Production SaaS is running on an EU-sovereign host with a published
  qualification (SecNumCloud 3.2 or equivalent).
- At least one customer is running on a private cloud tenant deployment.
- At least one customer has a committed on-prem deployment plan.
- Tier C models (Qwen 3 27B) pass the benchmark bar for RoI workflows.
- The offline installation package builds cleanly from CI and has been
  tested end-to-end in a simulated air-gapped environment.
- The residency/sovereignty posture is visible to customers at
  `/gc/sovereignty` and exportable as an attestation for their procurement.

---

## Risks to flag

- **Model quality risk**: Tier C models may not reach the benchmark bar.
  Mitigation: Tier B (Mistral) is the fallback for customers who cannot
  accept Tier A but do not require fully self-hosted.
- **Hardware cost risk**: Self-hosting Qwen 3 27B at scale is not cheap.
  GPU inference for Tier C should be priced into the on-prem contract, not
  absorbed as a cost centre.
- **Regulatory data freshness risk**: Air-gapped customers depend on the
  monthly regulatory update bundle. If it slips, their compliance posture
  drifts. The release cadence must be reliable; missing a bundle is a
  material quality-of-service failure.
- **Scope creep risk**: The sovereignty work is large. Every non-essential
  feature deferred to Phase 3 or later protects this track. Resist the urge
  to add "while we're in there" improvements.
