# Domeye Roadmap Visualization

```mermaid
flowchart LR

V[Vision: Trusted Agent Investigation System]

V --> C1[Capability Model]
V --> C2[Architecture Evolution]

C1 --> M0[M0 Governance Baseline]
C1 --> M1[M1 First Vertical Slice]
C1 --> M2[M2 Trusted Execution]
C1 --> M3[M3 Evidence-backed Answers]
C1 --> M4[M4 Multi Capability Agent]
C1 --> M5[M5 Durable Investigation]

C2 --> A1[Pi Agent Runtime]
C2 --> A2[Trust Kernel]
C2 --> A3[Plan / Workflow]
C2 --> A4[Capability Execution]
C2 --> A5[Artifact / Evidence]
C2 --> A6[Evaluation]

A1 --> M1
A2 --> M2
A3 --> M3
A4 --> M3
A5 --> M3
A6 --> M4
```

## Release Thinking

Each milestone is a capability increment, not only an engineering milestone.

Example:

```
M1
Before:
Agent cannot complete a real end-to-end investigation.

After:
One bounded question can travel through:
Pi → Host → Capability → Tool → Artifact → Answer.
```

## Progress Interpretation

Green:
Capability verified.

Yellow:
Infrastructure exists but user journey is incomplete.

Red:
Design exists but implementation evidence is missing.

This avoids confusing architecture progress with product progress.
