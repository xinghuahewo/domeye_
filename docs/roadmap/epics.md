# Domeye Architecture Refactor Epics

Roadmap 以用户旅程和已证实风险为主，不按层数或组件数计算完成度。M0/M1 的当前
权威锚点是 `docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md`。

## E0 Governance and Product Management

Goal:

Keep architecture, Roadmap, GitHub state and Candidate evidence consistent.

Outputs:

- Capability Map and requirement traceability
- First vertical slice anchor and digest
- Candidate-bound GitHub governance and decision records

---

## E1 Pi Agent Runtime

Goal:

Use Pi as the cognitive runtime for a rolling observe-and-decide loop.

Scope:

- User intent and context projection
- One next Action at a time
- Observe Tool / Operator result before the next decision
- Clarify, reselect or stop on failure

---

## E2 Trust Kernel / Host Control Plane

Goal:

Make Host the trusted business control layer.

Scope:

- Domain and Candidate identity
- Per-Action policy, admission and budget
- Trusted receipts and state transitions
- Credential and data-access boundaries

---

## E3 Rolling Interaction and Action State

Goal:

Make each interactive Action explicit, observable and recoverable without requiring a universal plan IR.

Scope:

- Proposed, admitted, executing and terminal Action states
- Result observation and next-action decision
- Stop, clarify and reselect branches
- Durable workflow only when a proven long-running use case requires it

---

## E4 Capability Execution

Goal:

Resolve admitted capabilities to versioned, auditable execution units.

Scope:

- Capability Registry and Execution Unit Registry
- Dispatcher and structured failures
- Atomic Tool contracts
- Deterministic Operator contracts

---

## E5 Artifact, Finding and Answer Safety

Goal:

Turn execution results into traceable domain findings and bounded user answers.

Scope:

- Immutable Artifact and ResultSet
- Execution provenance, data lineage and Domain Evidence
- Typed Finding and minimal Answer Context
- Renderer, deterministic Response Guard and safe fallback

M0/M1 does not build a general natural-language fact-candidate validation and publication subsystem.

---

## E6 Evaluation and Security

Goal:

Prove real Agent reliability and prevent control or answer-boundary bypass.

Scope:

- J1–J5 real-agent evaluation
- Pass@1 and Pass³ stability
- Admission, prompt-injection and Tool-output security
- Same-Candidate regression and independent acceptance

---

## E7 Domain Capability

Goal:

Expand BGP investigation capability without exceeding evidence boundaries.

Scope:

- BGP semantics and observer limits
- Population and unit definitions
- Allowed and forbidden external statements
- Evidence-backed capability expansion after DG1
