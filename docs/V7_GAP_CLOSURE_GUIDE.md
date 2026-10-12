# V7 Gap Closure Guide

## Experience plane
Use `/api/v7/approval-inbox` for pending packages. Existing resource lists expose artefacts, comparisons, variances, clarifications and governance records. Gate settings are managed through `/api/v7/gates/configure`.

## Intelligence plane
Prompts and models are immutable versioned registry entries. `/api/v7/model-gateway/requests` accepts only approved prompt/model combinations and records `PROVIDER_EXECUTION_REQUIRED`; secrets are rejected. `/api/v7/knowledge/index` and `/api/v7/knowledge/search` provide evidence-grounded retrieval with content hashes and lineage.

## Delivery automation
`/api/v7/delivery/git` executes allow-listed local Git operations. `/api/v7/delivery/request` records auditable external connector work for source control, CI/CD, artifact repositories, deployment and scanners. It deliberately does not claim network execution.

## Operations
Notifications are queued through `/api/v7/notifications`. Operational signals are recorded through `/api/v7/observability`; summaries are available through `/api/v7/observability/summary`.

## Production enablement
Replace local SQLite, local token retrieval and pending external operations with approved transactional database, object/index store, distributed queue and provider-specific adapters. Retain hashes, policy checks, correlation IDs and rollback pointers across replacements.
