---
name: iib-to-ms-variance-analysis
description: Comparing the actual response of a migrated Spring Boot service against the expected IIB response, and classifying differences as structural, type or value with a severity. Use when evaluating the HANDOFF-C smoke-test result or any captured pair of ACE and Spring results; includes the known signatures of IIB-versus-MS differences.
---

# Variance Analysis — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`variance-analysis`** agent (the "compare" step of HANDOFF-C).

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `variance-analysis` (Variance Analysis Agent) |
| Required role | `VARIANCE_ANALYST` |
| Depends on | `test-engineering` |
| Accepted inputs | `ace_results`, `java_results` |
| Produced outputs | `variance_records`, `severity` |
| Tool allow-list | `evidence.read`, `comparison.execute`, `variance.write` |

## What this agent does with the skill

It compares what the test-engineering agent captured against what the user said to expect, and records each difference as a **variance**.

### The comparison standard

The source skill sets a deliberately modest bar for the smoke test:

- Compare the **status code**, and the **presence and match of the key fields and values the user gave**.
- It is a **reasonable-match comparison, not a byte-perfect diff.** Byte-level and many-case comparison is the separate `iib-ms-testing` skill's job.
- Exact business-logic fidelity is secondary to "a working call to the backend", and gets fixed after testing.

So the question to answer first is: *did the service reach its backend and return a response of the expected shape?* A transport or wiring failure is a different kind of finding from a field-level difference.

### Classification

Use the factory's three classes:

| Class | Meaning here |
|---|---|
| STRUCTURAL | a field or construct present in one response and absent in the other |
| TYPE | the two disagree on a value's type |
| VALUE | same shape, different value (including status code and message text) |

Rate severity so that anything preventing a usable response (no backend reached, wrong endpoint, 5xx where a success was expected) outranks a cosmetic field difference.

### Known signatures

`references/known-variance-signatures.md` indexes the differences this skill's own evidence shows really occur between IIB and the generated service — what the symptom looks like, the suggested class, and the checklist item or gotcha that explains it. Use it to label a variance accurately, so the RCA agent starts from the right place. It is an index: the explanations live in the sources it cites.

## Output

- `variance_records` — one per difference: location, class, expected, actual, severity.
- `severity` — overall, and whether the backend call itself worked.

## Boundaries

- Reads evidence and writes variances only. It does not diagnose causes (`iib-to-ms-rca`), fix anything (`iib-to-ms-remediation`), or re-run the call (`iib-to-ms-test-engineering`).
- It does not decide that a variance is acceptable; that is a governance and approval matter.
- Never smooth over a missing comparison: if there is no captured actual response, record that, not a pass.

## References

Owned by this skill:

- `references/known-variance-signatures.md` — authored index (not extracted text)

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/common-patterns-and-gotchas.md`
- `references/gap-checklist.md`
- `references/scope-boundary.md`
