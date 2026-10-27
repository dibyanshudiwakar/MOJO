<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 181–240, 259–266. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->


Either way, state which mode was used at the top of the HANDOFF output, and if structure-only was chosen, note in the action-items section that real test-case content is deferred — don't leave the files silently empty without saying why.

`mvn -s <repo-root>/settings.xml test -q`. Fix genuine compile/syntax failures before the checkpoint; if full tests were generated, also fix any failing assertion before the checkpoint — never present a checkpoint with a real test failing.

**[CHECKPOINT]** "Tests: {N} files created ({mode}), {M} passing / 0 failing. Approve?"

---

## HANDOFF — full build, config reconcile, gap re-check, hand-off

`mvn -s <repo-root>/settings.xml clean verify` (Rule 14: `settings.xml` is at the repo root and every build passes it).

> ⚠️ **Two alarming-but-benign log outputs — neither is a build failure. Do NOT "fix" either one.** **[live-tested on `cumulativeCopaySummaries`, framework 3.2.2: `mvn clean verify` exit code `0`, `BUILD SUCCESS`, `Tests run: 52, Failures: 0, Errors: 0`.]**
> 1. **`cucumber-reporting:5.7.4:generate` prints a full `ValidationException: No JSON report file was found!` stack trace** for any service with no Cucumber tests. The plugin is bound by the `framework-starter` parent pom (not by generated code), logs this at `WARNING` via its own `generateErrorPage` handler, and **continues** — the build still succeeds. Cross-repo check: **0 surveyed workspace repos** add a skip/`<configuration>` block for it (the only `cucumber` mention found anywhere is an unrelated `<cucumber.version>` property in `fepclaimdetail`), so adding one would deviate from every real repo and violate Rule 2. Leave it; mention it in hand-off prose only if the user asks about the stack trace.
> 2. **`[Fatal Error] :1:8: XML document structures must start and end within the same entity.`** in the surefire log, for any service whose tests deliberately feed malformed XML to an error-path parser. That's the XML parser logging, not a test failure.
>
> 🛑 **Read the exit code, not the log text — and on Windows, get the exit code correctly.** Both outputs above look like failures and neither is. Worse, in **Windows PowerShell 5.1**, piping a native command's output with `2>&1` into the PS pipeline (`mvn ... 2>&1 | Select-String ...`) wraps each stderr line in a `NativeCommandError` and makes the tool report **exit 255 even when Maven returned 0** — this skill's own run was misled by it twice in a row and briefly recorded a non-existent build failure. Redirect to a file and read `$LASTEXITCODE` instead:
> ```powershell
> mvn -s <repo-root>/settings.xml -f <parent>/pom.xml clean verify --offline > "$env:TEMP\verify.log"
> $code = $LASTEXITCODE          # trustworthy; 0 = success
> Get-Content "$env:TEMP\verify.log" | Select-String "BUILD SUCCESS|BUILD FAILURE|Tests run:"
> ```
> Never report a build as failing on the strength of a stack trace in the log — confirm with the exit code and the `BUILD SUCCESS`/`BUILD FAILURE` line.

**Standing instruction from the user (2026-10-07): remove the `*-oci-parent/target/` directory after the build — never leave it in the handed-off tree.** `mvn clean verify` run from the parent creates a `target/` folder under *every* module it touches, including the parent — but the parent is a pure aggregator `pom.xml` with no source and no jar; its `target/` is just empty build metadata, not a deliverable. Delete it as a mechanical post-build step:
```bash
rm -rf <service>-oci-parent/target
```
**Do not touch `*-oci-web/target/`** — that one is expected and fine to leave as-is; it holds the actual built jar (the Dockerfile's `COPY` step references it directly — `structural-conventions.md` §3) and is useful for the handoff. This is specifically about the parent module's target folder being noise, not a blanket "clean up all target folders" rule.

### HANDOFF-A0 — Baseline-block literal check (mandatory, mechanical, run first)

**Why this exists as its own step, separate from HANDOFF-A below:** HANDOFF-A's diff only catches a key that's in the *packaged* `application.properties` but missing from a config-map. It is blind to the far more common real failure — the baseline block (`server.port`, the jackson pair, `logging.config`, the 3 header-size keys, `management.endpoints.web.exposure.include`, the 4 circuit-breaker keys) never got written into the packaged file *either*, so the diff has nothing to flag. This exact gap shipped in both `RankedMatch` and `GetGrievanceSummary` despite being written in prose in three other reference files — prose alone doesn't stop it. This step is a literal byte-level check against a physical template file instead:

```bash
# references/templates/config-map-baseline.properties is the literal, canonical source — never retype this block from memory.
base="<skill-dir>/references/templates/config-map-baseline.properties"
baseline_keys() { grep -oE '^[a-zA-Z][a-zA-Z0-9._-]*=' "$1" | sed 's/=$//' | sort -u; }
baseline_keys "$base" > /tmp/baseline.keys

echo "== packaged properties (check BOTH files — this block lives in application-local.properties in 8/20 real repos, not application.properties)"
cat <web-module>/src/main/resources/application.properties <web-module>/src/main/resources/application-local.properties 2>/dev/null | baseline_keys /dev/stdin > /tmp/pkg.keys
comm -23 /tmp/baseline.keys /tmp/pkg.keys

for f in config/config-map/application.*.properties; do
  echo "== $f"
  baseline_keys "$f" > /tmp/cm.keys
  comm -23 /tmp/baseline.keys /tmp/cm.keys
done
```

Any key this prints is missing from that file — go copy the actual block from `references/templates/config-map-baseline.properties` (substituting `{{LOGBACK_FILE_FOR_TIER}}` for that env's tier) into the packaged `application.properties` **and** every config-map, don't retype it. This step must print nothing anywhere before moving to HANDOFF-A. Treat the packaged-file omission as equally serious as the config-map one — several past failures had the block missing from both.

### HANDOFF-A — Reconcile runtime properties into every config-map (mandatory, mechanical)

Run this **after HANDOFF-A0 is clean**. A deployed pod loads its config *only* from the config-map: `Deployment.yaml` sets `SPRING_CONFIG_LOCATION`, and `spring.config.location` **replaces** the default config locations instead of adding to them — so `classpath:/application.properties` is never read in dev/qa/uat/stress/prod. Any key living only in the packaged file silently vanishes on deploy.

```bash
keys() { grep -oE '^[a-zA-Z][a-zA-Z0-9._-]*=' "$1" | sed 's/=$//' | sort -u; }
5. **If it passes:** record `smoke_test.status = "passed"`, note which attempt number succeeded, stop the service, move to HANDOFF-B.
6. **If it fails:**
   - Diagnose from the actual response and application logs. Is this a *generated-code* problem — wrong field mapping, wrong endpoint path, missing/wrong config key, wrong header, wrong error-handling branch? (As opposed to something no regeneration can fix — backend unreachable, no test data for that specific input, credentials not yet provisioned.)
   - If it's a code problem: fix it, recompile, restart the service, retry the **same stored** request/expected-response pair. Append the attempt (what failed, what was changed) to `state.json`'s `smoke_test.attempts`.
   - **Loop up to 3 attempts total** — the same bound used everywhere else in this skill (compile-gate loop, gap-checklist loop). Don't retry indefinitely.
   - If it still fails after 3 attempts, or the failure is clearly not something a regenerate can fix: **stop retrying**, set `smoke_test.status = "failed_blocking"`, and report it as a **BLOCKING** gap in HANDOFF-B with the actual-vs-expected diff from every attempt and a remediation path. Never report HANDOFF as successful while this is unresolved — surface it plainly, don't bury it.

**[CHECKPOINT]** "Smoke test: {passed on attempt N / failed after 3 attempts — see GAP}. Continuing to final gap re-check."
