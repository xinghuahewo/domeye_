# Domeye Agent Feature Breakdown

This document refines Epic-level planning into Features.

Important rule:

> Features are not architecture components. Each Feature must create a measurable capability improvement or remove a verified architectural risk.

## E0 Governance

### F0.1 Requirement Traceability
- Maintain mapping:
  Requirement → Capability → Issue → Implementation → Verification
- Prevent architecture discussion from becoming unmanaged backlog.

### F0.2 Architecture Decision Records
- Record irreversible decisions.
- Avoid repeated discussions about runtime, Host, Plan, Artifact boundaries.

## E1 Pi Agent Runtime

### F1.1 Pi Integration Layer
Outcome:
- Replace custom Agent loop responsibilities with Pi runtime.

Acceptance:
- User journey can execute through Pi → Capability → Host.

### F1.2 Capability-facing Tool Interface
Outcome:
- Model sees semantic capabilities, not internal execution units.

### F1.3 Session and Context Projection
Outcome:
- Conversation state is separated from trusted domain state.

## E2 Trust Kernel

### F2.1 Identity Binding
Outcome:
- All actions are bound to valid domain identity.

### F2.2 Policy and Admission
Outcome:
- Agent proposals cannot bypass authorization.

### F2.3 Receipt and Audit Model
Outcome:
- Important actions are reproducible and auditable.

## E3 Plan and Workflow

### F3.1 Common Plan Model
Outcome:
- Semantic intent can compile into executable plans.

### F3.2 Execution Profile Model
Outcome:
- Interactive and investigation workflows share the same semantic core.

### F3.3 Durable Workflow Integration
Outcome:
- Long-running investigations support recovery and controlled execution.

## E4 Capability Execution

### F4.1 Capability Registry
Outcome:
- Separate what the system can do from how it is implemented.

### F4.2 Execution Unit Registry
Outcome:
- Bind capability to versioned implementations.

### F4.3 Unified Execution Contract
Outcome:
- Language-specific implementations share execution semantics.

## E5 Artifact and Evidence

### F5.1 Typed Artifact Model
Outcome:
- Results become reusable verified artifacts.

### F5.2 ResultSet Contract
Outcome:
- Complete populations are distinguishable from previews.

### F5.3 Evidence and Claim Validation
Outcome:
- Published answers trace back to evidence.

## E6 Evaluation and Security

### F6.1 Agent Evaluation Harness
Outcome:
- Measure real task success, not only fixture classification.

### F6.2 Security Validation
Outcome:
- Test tool misuse, permission abuse and prompt injection risks.

## E7 Domain Capability

### F7.1 BGP Semantic Model
Outcome:
- Expand capability without exceeding evidence boundaries.

### F7.2 Domain Evidence Expansion
Outcome:
- Add capabilities with explicit evidence contracts.

---

## Self Review Rules

Before starting a feature:

1. Does it improve a user-visible capability or close a proven architectural risk?
2. Is the dependency clear?
3. Is there an acceptance test?
4. Is there a reason not to postpone it?

If the answer is no, keep it in backlog instead of implementing it.
