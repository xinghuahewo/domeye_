# ADR-001: Use Pi as Agent Runtime

## Status
Accepted

## Context

Domeye requires an Agent runtime for model interaction, tool calling and conversation flow. Building a custom Agent runtime increases complexity and duplicates mature capabilities.

## Decision

Pi is used as the Agent Runtime.

Pi responsibilities:

- LLM interaction
- Agent loop
- context management
- tool proposal
- response generation

Domeye retains ownership of:

- identity
- authorization
- registry
- execution admission
- evidence
- claim publication

## Consequences

Positive:

- reduce runtime duplication
- focus engineering effort on domain trust layer

Negative:

- requires strict boundary between Pi and Domeye control plane

## Validation

The runtime boundary must be verified by integration tests.
