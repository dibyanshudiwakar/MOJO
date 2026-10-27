<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 282–293. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->


**One narrow exception, added 2026-10-09:** HANDOFF-C's single real API call, using request/expected-response data the user supplies at INTAKE — proving the generated service works end-to-end — is in scope. This is deliberately narrow: one request, one comparison, fix-and-retry up to 3 attempts.

**Still out of scope — belongs to the separate `iib-ms-testing` skill:** bulk/regression API-level testing, IIB-vs-Kong/MS response comparison across many cases, Bruno collections, cucumber-report-driven regression runs, ieonehub/Playwright automation, and any generated Python test-harness scripts. Do not generate those here, and do not create a `helper scripts/` folder in the migrated repo. If the user wants the migrated service validated against IIB at scale, hand off to `iib-ms-testing`.

**In scope:** the JUnit 5 unit tests for the service's own layers (GENERATE, Tests subsection) — those are part of the code deliverable. A short-lived `spring-boot:run` plus a single curl/`Invoke-WebRequest` call to confirm the generated service can actually reach its backend is also in scope, since "a working call to the backend" is part of what `SKILL.md`'s "What done means" defines as done — that's a smoke check, not a test suite.

---

## Hard constraints (apply across every stage)

- Never invent a field mapping not present in the ESQL/XSD/copybook — flag as GAP instead
