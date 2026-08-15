# Domeye Progress Visualization Model

## Purpose

Progress must be visible from three independent perspectives:

1. Product capability progress
2. Architecture migration progress
3. Engineering execution progress

Do not measure progress only by completed code or merged PRs.

## Three Views

### 1. Capability View

Question:

> What can users do now that they could not do before?

Status:

```
Not Started
    ↓
Design
    ↓
Skeleton
    ↓
Executable
    ↓
Evidence Closed
    ↓
Product Verified
    ↓
Released
```

### 2. Architecture View

Question:

> Which architectural layer has reached the target state?

Layers:

```
Pi Agent Runtime
Trusted Control Plane
Plan / Workflow
Capability Execution
Artifact / Evidence
Answer Publication
Evaluation
```

### 3. Delivery View

Question:

> What engineering work is currently moving?

```
Epic
 ↓
Feature
 ↓
Issue
 ↓
PR
 ↓
Verification
```

## Critical Rule

A component being implemented does not mean the capability is completed.

Example:

```
Operator implemented
≠
User capability available
```

Capability completion requires:

- implementation
- integration
- verification
- user journey evidence

## Recommended Dashboard Views

GitHub Project should provide:

- Roadmap view: milestones over time
- Capability view: product ability maturity
- Architecture view: migration status
- Risk view: unresolved architecture debt
- Verification view: completed evidence

## Milestone Review Questions

Every milestone must answer:

1. What could users do before?
2. What can users do after?
3. Which architecture layers changed?
4. What evidence proves completion?
5. What remains intentionally unfinished?
