---
name: iib-to-ms-governance
description: The policy gate for an IIB/ACE to Spring Boot migration — mandatory-rule and checklist verification before each checkpoint, hard constraints that hold across every stage, and the conditions that must block a transition. Use when deciding whether a checkpoint may be passed, what approval or evidence is still required, or whether an action is permitted.
---

# Governance — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`governance`** agent. It evaluates; it does not produce migration artefacts and it decides nothing the rules do not already decide.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `governance` (Governance Agent) |
| Required role | `GOVERNANCE_ENGINE` |
| Depends on | — |
| Accepted inputs | `policies`, `lifecycle_events` |
| Produced outputs | `allow_deny_decision`, `approval_requirement`, `evidence_requirement` |
| Tool allow-list | `policy.read`, `policy.evaluate`, `audit.write` |

## What this agent does with the skill

For a job as it stands, answer: what is required to pass this checkpoint, is it satisfied, and what evidence is still missing.

### Checks before every checkpoint

`references/verification-checklists.md` (verbatim from the source skill §7) is the gate. It has four parts:

- **7.1 Mandatory-rules checklist** — one check per rule 1–13.
- **7.2 Gap checklist** — BLOCKING and HIGH always; MEDIUM once deployment artefacts exist; the REST-only or MQ_COBOL-only section if applicable.
- **7.3 Structural-conventions checklist.**
- **7.4 Common-patterns checklist.**

The rule text is in `references/mandatory-migration-rules.md`; the gap items are in `references/gap-checklist.md`. Read them there — do not restate a rule here.

### Hard constraints

`references/hard-constraints.md` (verbatim) holds across every stage. Treat any violation as a **deny**:

- no invented field mapping not present in the ESQL, XSD or copybook;
- no `RestClient` / `RestTemplate` for QL_CICS or MQ_COBOL transport;
- no Camel Bindy for MQ_COBOL;
- no hardcoded requester ID, no JWT/JWKS validation for one;
- no environment config files inside `src/main/resources/`;
- no unset `crlf` on a `@FixedLengthRecord` used for a concatenated request buffer;
- no `RefDataConnector` or hardcoded lookup table unless the ESQL actually calls reference data;
- **no uncompiled code at a checkpoint**;
- **no `git commit`, `git push` or `mvn deploy` unless explicitly asked**.

### Conditions that must block a transition

- Any of the 5 mandatory intake inputs unanswered (standing instruction: never silently default past one).
- `smoke_test.status` still `pending` at HANDOFF-B — the smoke test was skipped ([M25]).
- `smoke_test.status` = `failed_blocking` — a reported BLOCKING item. The governance answer is *not complete*, never *successful*.
- A mandatory rule violated: a custom `Exception` subclass or `exception` package (Rule 10), a Kong/token-validator reimplementation (Rule 9), `RestTemplate` anywhere (Rule 4), plaintext credentials (Rule 6).
- Open clarifications.

### Approvals and evidence to require

- User confirmation at the pre-flight ("Proceed with N flow(s) as {backend type}?") and at each `[CHECKPOINT]`.
- Evidence of a compile pass per layer, a gap-check run, and a build exit code of 0 — taken from the **exit code**, not from stack traces in log text.
- Evidence that HANDOFF-C actually ran: attempt number, path used, actual response.

## Output

- `allow_deny_decision` — per transition, with the rule or checklist id that decided it.
- `approval_requirement` — which confirmation is needed.
- `evidence_requirement` — what is missing, by name.

## Boundaries

- Read-only apart from its audit entry. Every rule is evaluated by the code or checklist that enforces it; a second implementation of a rule would be a governance answer that could disagree with the gate itself.
- It does not generate, fix or diagnose.

## References

Owned by this skill:

- `references/verification-checklists.md` — source `SKILL.md` §7, verbatim
- `references/hard-constraints.md` — "Hard constraints" from the workflow, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/mandatory-migration-rules.md`
- `references/gap-checklist.md`
- `references/structural-conventions.md` — §12 verification checklist
- `references/scope-boundary.md`
