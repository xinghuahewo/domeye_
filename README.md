# Domeye Agent Architecture Management

## Purpose

This repository is the engineering management and architecture governance repository for Domeye Agent.

It does not initially contain product source code. It manages:

- architecture evolution
- requirements traceability
- capability roadmap
- ADR decisions
- milestones
- verification evidence
- Codex development workflow

## Development Management Model

Domeye follows:

```
Outcome
  ↓
Capability
  ↓
Release / Milestone
  ↓
Vertical Slice
  ↓
Requirement
  ↓
Engineering Task
  ↓
Code + Test + Evidence
```

## Repository Structure

```
.
├── docs/
│   ├── architecture/
│   ├── roadmap/
│   ├── requirements/
│   ├── adr/
│   └── verification/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
└── README.md
```

## Core Principles

1. Capability completion is more important than code completion.
2. Every task must map to a requirement and capability.
3. Every milestone must produce a demonstrable user-facing improvement.
4. Architecture decisions must be recorded as ADRs.
5. AI behavior requires evaluation evidence, not only code tests.
