<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 242–257. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

### HANDOFF-C — One real API hit, fix-and-retry on failure (mandatory, every migration)

**Standing instruction from the user (2026-10-09).** This is a narrow, deliberate exception to this skill's own scope boundary (see below) — not full API testing, just enough to prove the generated service actually works end-to-end, using data the user already gave at INTAKE Batch A item 6 (never invented here).

1. **Use the request/expected-response already collected at INTAKE** — `state.json`'s `smoke_test.request`/`smoke_test.expected_response`. Do not ask again here.
2. **Start the built service** — `java -jar <web-module>/target/<artifact>-*.jar` (or `mvn spring-boot:run` from the web module), pointed at the real backend if reachable from this environment, falling back to the test-harness bypass if not. State which path was used.
3. **Make exactly one HTTP call** with the stored request against the service's own endpoint.
4. **Compare** the actual response to the expected one — status code, and presence/match of the key fields/values the user gave. This is a reasonable-match comparison, not a byte-perfect diff (that level of comparison is still `iib-ms-testing`'s job).
5. **If it passes:** record `smoke_test.status = "passed"`, note which attempt number succeeded, stop the service, move to HANDOFF-B.
6. **If it fails:**
   - Diagnose from the actual response and application logs. Is this a *generated-code* problem — wrong field mapping, wrong endpoint path, missing/wrong config key, wrong header, wrong error-handling branch? (As opposed to something no regeneration can fix — backend unreachable, no test data for that specific input, credentials not yet provisioned.)
   - If it's a code problem: fix it, recompile, restart the service, retry the **same stored** request/expected-response pair. Append the attempt (what failed, what was changed) to `state.json`'s `smoke_test.attempts`.
   - **Loop up to 3 attempts total** — the same bound used everywhere else in this skill (compile-gate loop, gap-checklist loop). Don't retry indefinitely.
   - If it still fails after 3 attempts, or the failure is clearly not something a regenerate can fix: **stop retrying**, set `smoke_test.status = "failed_blocking"`, and report it as a **BLOCKING** gap in HANDOFF-B with the actual-vs-expected diff from every attempt and a remediation path. Never report HANDOFF as successful while this is unresolved — surface it plainly, don't bury it.

**[CHECKPOINT]** "Smoke test: {passed on attempt N / failed after 3 attempts — see GAP}. Continuing to final gap re-check."
