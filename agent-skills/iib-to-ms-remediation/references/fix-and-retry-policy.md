<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 251–257; references/gap-checklist.md lines 178–183. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

6. **If it fails:**
   - Diagnose from the actual response and application logs. Is this a *generated-code* problem — wrong field mapping, wrong endpoint path, missing/wrong config key, wrong header, wrong error-handling branch? (As opposed to something no regeneration can fix — backend unreachable, no test data for that specific input, credentials not yet provisioned.)
   - If it's a code problem: fix it, recompile, restart the service, retry the **same stored** request/expected-response pair. Append the attempt (what failed, what was changed) to `state.json`'s `smoke_test.attempts`.
   - **Loop up to 3 attempts total** — the same bound used everywhere else in this skill (compile-gate loop, gap-checklist loop). Don't retry indefinitely.
   - If it still fails after 3 attempts, or the failure is clearly not something a regenerate can fix: **stop retrying**, set `smoke_test.status = "failed_blocking"`, and report it as a **BLOCKING** gap in HANDOFF-B with the actual-vs-expected diff from every attempt and a remediation path. Never report HANDOFF as successful while this is unresolved — surface it plainly, don't bury it.

**[CHECKPOINT]** "Smoke test: {passed on attempt N / failed after 3 attempts — see GAP}. Continuing to final gap re-check."

## Applying this checklist

1. After the service layer compiles, run every item in **BLOCKING** and **HIGH** for the detected backend type, plus the relevant **REST-ONLY** or **MQCOBOL-ONLY** section if applicable.
2. Run **MEDIUM** once the deployment artifacts (Dockerfile, Helm, config-map, `.github/`) are generated.
3. Auto-fix what's unambiguous; for anything requiring a judgment call (byte-length mismatches, ambiguous field mappings), regenerate and recompile rather than patching around it.
4. Loop up to 3 times; report anything still open as a named GAP with who needs to confirm it (usually "QL team" for buffer-layout questions, "infra team" for queue-name/deployment questions, or the service owner for a plaintext credential value still needing encryption). Akeyless provisioning itself is a future action item for Phase 2 services, not a current gap — see `mandatory-migration-rules.md` Rule 6.
