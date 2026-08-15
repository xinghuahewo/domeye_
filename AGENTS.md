# Codex Development Rules for Domeye

## Purpose

This repository uses Codex as an engineering assistant. Codex should work from requirements and architecture decisions, not isolated coding requests.

## Before Coding

Always:

1. Identify the related Capability.
2. Identify the Requirement ID.
3. Read related ADR and architecture documents.
4. Check dependencies and existing decisions.
5. Define acceptance evidence before implementation.

## Development Flow

```
Capability
  ↓
Requirement
  ↓
Issue
  ↓
Implementation
  ↓
Test
  ↓
Evidence
  ↓
Release
```

## Coding Principles

- Prefer incremental vertical slices over large rewrites.
- Do not introduce architecture changes without an ADR.
- Do not create code-only completion claims.
- Preserve existing contracts unless migration is planned.
- Separate product capability from implementation details.

## After Coding

Every change should update:

- requirement status
- affected architecture documents
- tests
- verification evidence

## Forbidden Shortcuts

Do not:

- mark a capability complete because code exists;
- bypass Registry or contracts;
- mix architecture decisions with implementation patches;
- expand scope without updating requirements.
