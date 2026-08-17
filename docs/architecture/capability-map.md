# Domeye Capability Map

本图描述可观察能力，不把架构层或模块数量当成进度。M0/M1 的首要验收依据是
[首个纵向切片锚点合同](Domeye_First_Vertical_Slice_Anchor_v1.0.md)中的 J1–J5。

## Agent Capability

- User intent understanding
- Rolling next-action selection
- Observe result and re-plan
- Multi-turn clarification

## Host Control Capability

- Domain identity binding
- Per-action policy and admission
- Permission, budget and timeout enforcement
- Receipt and trusted state transition

## Execution Capability

- Capability and execution-unit resolution
- Versioned Tool / Operator binding
- Structured dispatch and failure classification
- Deterministic Operator replay

## Answer Safety Capability

- Typed Finding construction
- Minimal Answer Context projection
- Constrained Renderer
- Deterministic Response Guard and fallback

## Artifact and Evidence Capability

- ResultSet and immutable Artifact management
- Execution provenance and data lineage
- Domain Evidence references
- Candidate-bound acceptance records

## Evaluation Capability

- J1–J5 real-agent evaluation
- Pass@1 and Pass³ stability measurement
- Admission, injection and answer-boundary security tests
- Candidate-bound regression evidence

## Domain Capability

- BGP event, series, ASN and path semantics
- Observer-scope and population boundaries
- Explicit limitation and unsupported conclusion handling

## Delivery maturity

每项能力只使用以下成熟度：

```text
Not Assessed
  → Designed
  → Implemented
  → Verified
  → Released
```

代码已集成、PR 已合并、Issue Done 和 Project Synced 都只是证据或工作状态，不能
自动推进 Delivery Maturity。Verified 必须绑定同一 Candidate 的独立接受记录。
