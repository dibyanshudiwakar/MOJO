---
name: iib-to-ms-repository-intake
description: Source intake for an IIB/ACE to Spring Boot migration — validate the IIB repo, capture the 5 mandatory user inputs plus the extra intake answers, check the toolchain, and produce the pre-flight table and gaps. Use when starting a migration or when intake inputs are missing or unconfirmed.
---

# Repository Intake — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`repository-intake`** agent (INTAKE Batches A, C and D of the source workflow).

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `repository-intake` (Repository Intake Agent) |
| Required role | `REPOSITORY_INTAKE` |
| Depends on | — |
| Accepted inputs | `repository_references`, `credential_secret_references` |
| Produced outputs | `intake_manifest`, `gaps` |
| Tool allow-list | `repository.read`, `evidence.write` |

## What this agent does with the skill

No `.java` file is written until INTAKE completes. This agent covers the parts of INTAKE that are about the repository and the user's answers; backend-type detection (Batch B) belongs to `iib-to-ms-ace-analysis`.

Full procedure, verbatim: `references/intake-procedure.md`.

### The 5 mandatory inputs

Standing instruction (2026-10-09): all five need an explicit answer. A suggested default may accompany a question but never replaces the answer.

1. IIB source repo path — must contain at least one `.msgflow` file.
2. Migrated MS destination directory — suggested default: sibling of the IIB source repo (Rule 13).
3. Framework version — suggested default: `framework-starter` 3.2.2 and Java 21 (Rule 7).
4. One successful request's data (for the HANDOFF-C smoke test).
5. The expected response for that same request.

Items 4 and 5 are persisted once, as `smoke_test.request` / `smoke_test.expected_response`, and never re-asked at HANDOFF. They must not be invented.

### Also collected in the same batch

- Reference OCI project path — auto-discover a sibling `*-oci` repo first; otherwise use the reference-project catalog for the detected backend type.
- JUnit preference: full test cases with real content, or structure-only skeletons (Rule 11).
- CVS programme phase (1 or 2).

### Toolchain and pre-flight

- `java -version` must show 21; `mvn -v` must show 3.8 or higher.
- Produce the pre-flight table: IIB source readable and flow count; backend type detected; every flow has msgflow + esql; target dir writable; JDK 21 and Maven 3.8+; reference OCI repo found.

## Output

- `intake_manifest` — file inventory with a hash per file, plus the pre-flight results.
- `gaps` — anything that makes the intake incomplete: a missing or unanswered mandatory input, no `.msgflow` found, an unreadable or unwritable path, a failed toolchain check, no reference repo.

## Boundaries

- This agent does **not** create clarifications (its contract withholds `clarification.create`). It reports gaps; the orchestrator turns them into questions.
- It does not detect the backend type, read ESQL semantics, or choose a framework version for the user.
- It never defaults silently past one of the 5 inputs.

## References

Owned by this skill:

- `references/intake-procedure.md` — INTAKE Batches A–D, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/mandatory-migration-rules.md` — Rules 7, 11 and 13 govern the framework-version, JUnit and destination questions
- `references/backend-type-detection.md` — Batch B, needed to complete the pre-flight table
- `references/state-file-and-resumption.md` — where the smoke-test request and expected response are persisted
