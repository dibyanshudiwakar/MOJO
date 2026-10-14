---
name: iib-to-ms-test-engineering
description: Running the HANDOFF-C smoke test for a migrated IIB service — start the built service, make exactly one real HTTP call with the request supplied at intake, and capture the raw result — and interpreting mvn test results correctly. Use when executing or reporting the end-to-end proof that the generated service reaches its backend.
---

# Test Engineering — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`test-engineering`** agent (HANDOFF-C of the source workflow).

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `test-engineering` (Test Engineering Agent) |
| Required role | `TEST_ENGINEER` |
| Depends on | `ace-analysis` |
| Accepted inputs | `behaviour_model`, `test_data`, `endpoint_adapters` |
| Produced outputs | `test_suites`, `execution_evidence` |
| Tool allow-list | `test.execute`, `runtime.invoke`, `evidence.write` |

## What this agent does with the skill

HANDOFF-C is the one deliberate exception to the source skill's scope boundary: **one real API call**, using a request/expected-response pair the user supplied at intake, to prove the source skill's own definition of done — *a working call to the backend*. It is a smoke check, not a test suite.

Procedure, verbatim: `references/smoke-test-procedure.md`. In short:

1. **Use the stored pair.** The request and expected response are in the job's smoke-test fields (`smoke_test.request`, `smoke_test.expected_response`). Never ask again and never invent one.
2. **Start the built service** — `java -jar <web-module>/target/<artifact>-*.jar`, or `mvn spring-boot:run` from the web module. Point it at the real backend if reachable; fall back to the test-harness bypass if not. **State which path was used.**
3. **Make exactly one HTTP call** with the stored request.
4. **Capture the raw actual response** — status, headers that matter, body — together with the application log for the attempt.
5. **Record the attempt** in `smoke_test.attempts` and set `smoke_test.status` to `passed` or hand off for diagnosis.

The comparison against the expected response, and any diagnosis and retry, belong to other agents:

- compare → `iib-to-ms-variance-analysis`
- diagnose → `iib-to-ms-rca`
- fix and retry (up to 3 attempts in total) → `iib-to-ms-remediation`

`smoke_test.status` must end as `passed` or `failed_blocking`, never `pending` ([M25]). Skipping the smoke test is itself a gap.

## Reading test and build results

- **Read the exit code, not the log text.** On Windows PowerShell 5.1, piping a native command with `2>&1` into the pipeline can report exit 255 even when Maven returned 0. Redirect to a file and read `$LASTEXITCODE`. Full rule in `references/handoff-build-and-reconcile.md`.
- Two alarming-but-benign outputs are not failures: the `cucumber-reporting` "No JSON report file was found!" stack trace, and `[Fatal Error] :1:8: XML document structures must start and end within the same entity.` from a test that feeds malformed XML on purpose.
- `mvn test -q` passing with **0 tests run** is correct when the user chose structure-only JUnit skeletons (Rule 11). With full test content chosen, real tests must run and pass.
- A REST service has one focused `{Service}ServiceTest` and no Cucumber; the coverage list is in `references/rest-external-direct.md` §0c.

## Output

- `test_suites` — what was run: mode (structure-only or full), count, result.
- `execution_evidence` — the raw smoke-test result per attempt: request used, path used (real backend or test harness), status, body, log excerpt.

## Boundaries

- Holds `test.execute` and `runtime.invoke`; it does **not** hold `build.execute` or any repository write. It runs what was built; it does not rebuild or edit.
- **Out of scope** — belongs to the separate `iib-ms-testing` skill: bulk or regression API testing, IIB-versus-MS comparison across many cases, Bruno collections, cucumber-report-driven regression, Playwright automation, generated Python harness scripts. Do not create a `helper scripts/` folder in the migrated repo.
- Reporting an unexecuted package as tested is the failure the evidence precondition exists to catch: if the call did not run, say so.

## References

Owned by this skill:

- `references/smoke-test-procedure.md` — HANDOFF-C, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/scaffold-and-generate.md` — the Tests subsection (modes and expectations)
- `references/mandatory-migration-rules.md` — Rule 11
- `references/scope-boundary.md`
