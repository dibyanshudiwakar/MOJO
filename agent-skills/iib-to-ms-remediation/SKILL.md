---
name: iib-to-ms-remediation
description: Fix-and-retry policy for a migrated IIB service — deciding whether a failure is a generated-code problem, bounding attempts to 3, planning the patch and its regression tests, and reporting what cannot be fixed as a BLOCKING gap. Use when a smoke test, compile gate or gap check has failed and a bounded correction must be planned.
---

# Remediation — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`remediation`** agent (the failure branch of HANDOFF-C, and the auto-fix versus regenerate versus report rule of the gap checklist).

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `remediation` (Remediation Agent) |
| Required role | `REMEDIATION_ENGINEER` |
| Depends on | `rca` |
| Accepted inputs | `approved_rca`, `policy` |
| Produced outputs | `patch_plan`, `code_changes`, `tests` |
| Tool allow-list | `repository.branch`, `repository.write`, `test.execute`, `evidence.write` |

## What this agent does with the skill

Verbatim text: `references/fix-and-retry-policy.md`. The decision it applies:

1. **Is this a generated-code problem?** Wrong field mapping, wrong endpoint path, missing or wrong config key, wrong header, wrong error-handling branch — yes. Backend unreachable, no test data for that input, credentials not yet provisioned — **no regeneration can fix these.**
2. **Choose the smallest correct response**, in this order:
   - *auto-fix* what is unambiguous (rename a file, add a missing property key);
   - for complex gaps (wrong byte lengths, wrong transport, wrong annotation), *regenerate the affected class*, recompile, recheck — never patch around it;
   - otherwise *report* a named GAP.
3. **Bound it: 3 attempts in total.** This is the same bound used by the compile-gate loop, the gap-checklist loop and the smoke-test retry. Do not retry indefinitely.
4. **On the third failure, or a non-regenerable cause: stop.** Report a **BLOCKING** gap with the actual-versus-expected diff from *every* attempt and a remediation path — who to ask. Never present HANDOFF as successful while it is unresolved.

Each attempt is recorded (what failed, what was changed) in `smoke_test.attempts`.

### Compile-gate failures

The compile-gate loop and its error triage are in `references/scaffold-and-generate.md`. The key habit: treat every "cannot find symbol: method X()" as a signal that an assumption about a framework class must be verified — for example `QLRequest` has no `builder()` — not papered over with a local wrapper.

## Eligibility

Whether a fix may be applied directly depends on the RCA confidence against a governed threshold. That threshold comes from configuration, never from this agent or its parameters.

## Output

- `patch_plan` — the bounded change set: which classes or files, why, and the attempt number.
- `code_changes` — expressed as the change set an approved plan should carry.
- `tests` — the regression tests that must prove the fix (targeted regression, impacted suite), and for a smoke-test failure the same stored request retried.

## Boundaries

- **It plans the change; it does not edit generated source.** Generated code is produced by the code-generation agent from an approved plan, with provenance. Editing the target directly would create changes with no plan to trace them to.
- Every fix needs a rollback pointer and tests; a remediation lacking either is refused.
- Retries re-use the **same stored** request and expected response; the pair is never swapped to make a test pass.

## References

Owned by this skill:

- `references/fix-and-retry-policy.md` — extracted verbatim from the HANDOFF-C failure branch and the "Applying this checklist" section of the gap checklist

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/scaffold-and-generate.md` — compile-gate loop
- `references/gap-checklist.md`
- `references/common-patterns-and-gotchas.md`
- `references/smoke-test-procedure.md`
