# Domeye Development Principles

## 1. Capability over Code

A completed class, service, or module does not mean a capability is complete.

Completion requires:

- implementation
- integration
- verification
- user-visible effect

## 2. Vertical Slice First

Prefer:

User Request
→ Agent
→ Host
→ Capability
→ Execution
→ Artifact
→ Answer

over completing isolated layers without an end-to-end path.

## 3. Architecture Skepticism

Before adding a component ask:

- Is this solving a demonstrated problem?
- Does this duplicate an existing responsibility?
- Does this increase future governance cost?
- Can an existing mature component solve it?

## 4. Avoid Premature Generalization

A generic abstraction is justified only when:

- multiple real use cases exist;
- semantics are stable;
- the abstraction reduces complexity.

## 5. Preserve Existing Assets

Refactoring should first identify:

- what is valuable;
- what is duplicated;
- what is incorrectly placed;
- what should be retired.

## 6. Evidence-driven Completion

Every milestone requires evidence:

- code evidence;
- test evidence;
- runtime evidence;
- product journey evidence.

## 7. AI System Specific Rules

For Agent features:

- model output is a proposal;
- trusted systems decide execution;
- facts require evidence;
- evaluations measure outcomes and trajectories.

## 8. Red-team Question

Before accepting any design:

"If this system grows 10x, what part becomes impossible to govern?"

That answer should influence the architecture today.
