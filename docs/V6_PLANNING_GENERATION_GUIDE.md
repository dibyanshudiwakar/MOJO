# V6 Planning, Generation and Validation Guide

## Workflow
1. `POST /api/v6/mappings` validates artifact-bound G1 and G2 approvals and creates a mapping catalogue, unsupported mapping register, risks and clarification records.
2. Resolve every unsupported mapping and clarification before calling `POST /api/v6/plans`.
3. `POST /api/v6/g3-package` creates a pending Solution Architect review package containing the plan, catalogue, risks and artifact hashes.
4. Record an authenticated G3 decision through the governed approval endpoint. Generation remains blocked without an APPROVE record bound to the plan hash.
5. `POST /api/v6/generate` creates the Spring project and provenance manifest.
6. `POST /api/v6/quality` runs local guardrails and optionally the build. Enterprise scanners report NOT_CONFIGURED until adapters are supplied.
7. `POST /api/v6/differential/prepare` creates canonical test envelopes and approved comparison policy evidence.
8. `POST /api/v6/differential/record` creates per-case PASS or VARIANCE evidence.

## Approval boundary
This implementation creates the G3 package but does not approve it. The Solution Architect decision must be authenticated and linked to the migration-plan content hash.

## Execution boundary
External ACE/Spring runtime invocations, enterprise static analysis, dependency scanning, licence scanning and SAST require approved tools, endpoints, credentials and network access.
