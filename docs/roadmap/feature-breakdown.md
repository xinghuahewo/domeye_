# Domeye Agent Feature Breakdown

This document refines Epic-level planning into Features.

Important rule:

> Features are not architecture components. Each Feature must create a measurable user capability improvement or close a verified risk. M0/M1 progress is measured by J1–J5, not by feature or layer count.

## E0 Governance

### F0.1 Requirement and Candidate Traceability

Outcome:

- Maintain `Requirement → Journey → Issue → Candidate → Evidence → Decision` mapping.
- Keep Roadmap and GitHub metadata subordinate to architecture, code and accepted evidence.

### F0.2 Architecture Decision Records

Outcome:

- Record irreversible decisions and digest-bearing anchor contracts.
- Avoid repeated debates about Pi, Host, execution, Artifact and answer boundaries.

## E1 Pi Agent Runtime

### F1.1 Pi Integration Layer

Outcome:

- A real user journey executes through Pi → Capability → Host.
- Pi proposes one next Action and observes its result before deciding again.

### F1.2 Capability-facing Tool Interface

Outcome:

- The model sees semantic capabilities, not internal execution units or Registry mutation interfaces.

### F1.3 Session and Context Projection

Outcome:

- Conversation context is separated from trusted identity, Candidate and domain state.

## E2 Trust Kernel

### F2.1 Identity Binding

Outcome:

- Every Action and result is bound to valid incident, publication, revision, observer and window identity.

### F2.2 Policy and Per-Action Admission

Outcome:

- Agent proposals cannot bypass authorization, permission, budget or timeout checks.
- Every subsequent Action is admitted again.

### F2.3 Receipt and Audit Model

Outcome:

- Important actions, rejections and state transitions are reproducible and auditable.

## E3 Rolling Interaction and Action State

### F3.1 Rolling Next-Action Loop

Outcome:

- Pi selects one next Action, observes the structured result, then continues, clarifies, reselects or stops.
- M0/M1 does not require a universal plan intermediate representation or a pre-generated DAG.

### F3.2 Interactive Action State

Outcome:

- Proposed, admitted, executing, succeeded, failed, rejected and cancelled states have explicit contracts.
- Control-plane Action state is never treated as a domain fact.

### F3.3 Durable Workflow Boundary

Outcome:

- Long-running recovery is introduced only after a concrete journey proves it is needed.
- Interactive execution does not inherit durable orchestration complexity by default.

## E4 Capability Execution

### F4.1 Capability Registry

Outcome:

- Separate what the system can do from how it is implemented.

### F4.2 Execution Unit Registry

Outcome:

- Bind admitted capabilities to versioned Tool or Operator implementations.

### F4.3 Unified Execution Contract

Outcome:

- Language-specific implementations share identity, input, output, error, receipt and replay semantics.

## E5 Artifact, Finding and Answer Safety

### F5.1 Typed Finding and Safe Answer Composition

Outcome:

- Registered Tool / Operator results become Candidate-bound Typed Findings.
- Host builds a minimal Answer Context; Renderer output must pass a deterministic Response Guard.
- Semantic amplification is blocked and replaced by a same-context deterministic fallback.

### F5.2 Immutable Artifact, ResultSet and Evidence

Outcome:

- Results preserve execution provenance, data lineage, population, completeness and Domain Evidence.
- Artifacts provide facts and references but do not autonomously authorize user-visible statements.

## E6 Evaluation and Security

### F6.1 Same-Candidate J1–J5 Evaluation

Outcome:

- Measure real task success, Pass@1, Pass³, latency, cost and failure class on one frozen Candidate.
- Independent Acceptance Records bind each decision to the same Candidate.

### F6.2 Admission and Answer Security

Outcome:

- Test denied second Actions, prompt injection, Tool-output injection, identity conflicts, scope expansion and Guard bypass.

## E7 Domain Capability

### F7.1 BGP Semantic and Statement Boundary

Outcome:

- Metric, unit, population, observer and time semantics are explicit.
- Allowed and forbidden external statements are testable.

### F7.2 Domain Evidence Expansion

Outcome:

- Add new investigation capabilities only with explicit Tool, Operator, Finding, limitation and Evidence contracts.

---

## Self Review Rules

Before starting a feature:

1. Does it advance an anchor journey or close a proven risk?
2. Is the dependency and Candidate boundary clear?
3. Is there an objective acceptance test and required Evidence?
4. Is there a reason not to postpone it?

If the answer is no, keep it in backlog instead of implementing it.
