# Migration Workflow — Stages, State File, Compile-Gate Loop, Resumption

This is the operating procedure for the migration, adapted from a more mature sibling agent's stage-based, checkpoint-driven workflow. Follow this instead of a flat "generate everything then check" approach — it catches errors early (compile after every layer, not at the end) and survives interruption (state file).

**Evidence hierarchy** — when a claim in this skill's reference files conflicts, prefer in this order: (1) live-compiled-and-executed against a real backend (see the `iib-migration-github-workflows-rule` memory and the ValidateBridgeSupply live-test findings throughout `common-patterns-and-gotchas.md`) — the strongest evidence, since it caught bugs no amount of reading source would have; (2) cross-repo grep evidence tagged N/M in the connector-specific reference files — solid but not runtime-verified; (3) a single reference project's pattern with no cross-check — weakest, treat with suspicion until verified against at least one more repo or a real compile/run.

---

## INTAKE — source intake, backend-type detection, confirmation (blocking, run once per migration)

No `.java` file is written until this stage completes.

### Batch A — Repos & Identity

**Standing instruction from the user (2026-10-09): always take these 5 inputs directly from the user before moving ahead — ask explicitly, wait for a real answer, never silently default past any of them.** A suggested default is fine to offer *when asking* (it speeds up the answer), but offering a default is not the same as skipping the question — INTAKE does not proceed to Batch B until all 5 have an explicit answer:

1. **IIB source repo path** — validate it contains at least one `.msgflow` file.
2. **Migrated MS destination directory** — when asking, suggest the same parent directory the IIB source repo lives in as the default answer (Rule 13) — e.g. if the source is `<parent>/CVSEDB_PBM-<Service>-EDB-EIL`, suggest the sibling `<parent>/EIPMS_cvs-v1-<service>-oci` — but still ask and capture the actual answer, don't assume it.
3. **Framework version** — when asking, suggest `framework-starter` 3.2.2 and Java 21 as the default answer (Rule 7), but still ask and capture the actual answer.
4. **One successful request's data** — payload/params for one endpoint, for the end-to-end smoke test that runs at HANDOFF-C. Don't invent this yourself; wait for a real answer.
5. **The expected response for that same request** — status code and/or key fields/values. Collected in the same batch as #4, not separately.

Persist #4 and #5 in `state.json` (`smoke_test.request`/`smoke_test.expected_response`) so they survive a resume — collected once, here, never re-asked at HANDOFF.

**Also ask in this same batch** (not part of the mandatory 5, but still collected here): reference OCI project path (an existing sibling MS repo for the same or a similar backend type, for pom/import cross-checking) — try to auto-discover a sibling `*-oci` repo in the workspace first; if none found, use the confirmed reference-project catalog in the connector-specific reference file for the detected backend type. **Always ask: "Generate full JUnit 5 test cases with real content, or structure-only empty skeletons?" (Rule 11)** — if the user wants full content, generate it per the backend type's test-density convention (`rest-external-direct.md` §0c's coverage list, or the fuller per-layer suite for other categories) with real assertions and mocked behavior — not placeholders; if structure-only (or no answer), fall back to empty skeleton classes exactly as Rule 11 describes. Also ask the CVS programme phase (1 or 2).

### Batch B — Auto-detect backend type
Read all `.msgflow` and `.esql` files fully, then apply the Backend Type Detection table (see below). Record the result. If ambiguous, ask rather than guess.

**Fallback for an unrecognized backend (standing instruction from the user, 2026-10-13).** The 5 backend types this skill is trained on are: **SOAP, QL/CICS + Camel Bindy, RxClaim SBO (XML/JAXB), IBM MQ + COBOL Copybook, REST**. If detection finds a backend that matches none of these, **or** matches one but no trained reference repo exists for it in the workspace, do not guess a pattern from the closest-looking type. Instead take the reference from the org's frameworks repo, `https://github.com/cvs-legacy-source-code/EIPMS_cvs-technical-edb-cs-frameworks`, and migrate against the connector/framework module it provides for that backend.

- **Full procedure, module map, and version-branch rules: `references/framework-repo-fallback.md`** — read it before using the fallback. Key points: the repo is private (check for a local clone, ask the user if none, stop with a blocker if unreadable — never invent framework APIs); read the branch matching the chosen framework version (`origin/3.2.x`, **not** `master`, which is 4.0.0-SNAPSHOT with a breaking-change commit) via `git show`/`git grep` without checking out; and `framework-adapter` is **not** a migration target (it fronts a still-running IIB).
- Read the matching framework module's source for the real classes, annotations, and property keys, and cite the module, branch, and commit in the action-items list.
- Tag everything generated this way as **single-source / unverified** (evidence-hierarchy tier 3), same as `soap-backend.md` and `ibm-mq-cobol-copybook.md` are tagged, and say so in the HANDOFF output.
- Record in `state.json` under `backend_type` as `NEW:<short-name>` so a resume knows this run used the fallback.
- All mandatory rules and HANDOFF-C's smoke test still apply unchanged.

### Batch C — Toolchain check
```bash
java -version 2>&1 | head -1   # must contain "21"
mvn -v 2>&1 | head -1          # must contain "3.8" or higher
```

### Batch D — Pre-flight table
Print before proceeding:
```
[✓/✗] IIB source readable, N flows found
[✓/✗] Backend type detected: {SOAP|QL_CICS|RXCLAIM_SBO|MQ_COBOL|REST}
[✓/✗] All flows have msgflow + esql
[✓/✗] Target dir writable
[✓/✗] JDK 21, Maven 3.8+
[✓/✗] Reference OCI repo found (pom cross-check)
```
Confirm with the user before proceeding: "Proceed with {N} flow(s) as {backend type} service?"

---

## Backend Type Detection

Read every `.msgflow` and `.esql` file before deciding. This determines everything downstream — connector, pom deps, package layout, all of it.

| IIB Signal | Backend Type | MS Connector | Reference file |
|---|---|---|---|
| SOAP envelope in ESQL + HTTP Request node | **SOAP** | `framework-soapclient` | `references/soap-backend.md` |
| `MQOutput`/`MQGet`/`Aggregation` + QL TRAN code in ESQL | **QL_CICS** | `framework-connector-qlcics` | `references/ql-cics-camel-bindy.md` |
| `RxClaim`/`SBO`/`GETIDCARDDETAIL`-style transaction codes in ESQL or subflow names | **RXCLAIM_SBO** | `framework-connector-rxclaimsbo` | `references/rxclaim-sbo-xml-jaxb.md` |
| IBM MQ + raw COBOL copybook fields, **no** QL TRAN code, **no** Bindy/DFDL message set | **MQ_COBOL** | `framework-mqclient` (`MQUtility.sendwithID`/`receivewithID`) | `references/ibm-mq-cobol-copybook.md` |
| `HTTP Request` node to a plain REST endpoint (no SOAP envelope, no QL/RxClaim routing) | **REST** | `RestClientWrapper` | `references/rest-external-direct.md` |
| **Anything not matching the 5 rows above, or a match with no trained reference repo** | **NEW:\<name\>** | the matching module in the frameworks repo | see "Fallback for an unrecognized backend" in Batch B — read `EIPMS_cvs-technical-edb-cs-frameworks`, don't guess |
| `RECAP`/`Tandem` in subflow/queue names | **RECAP** | `framework-connector-recap` | not yet covered — flag as gap, ask before proceeding |
| `Stratus` in subflow/queue names | **STRATUS** | `framework-connector-stratus` | not yet covered — flag as gap, ask before proceeding |
| `DB2`/`JDBC` node | **DB2** | flag as GAP — no direct DB in MS, but check for `framework-connector-db` (routes SQL through REST — legitimate, confirmed in `fepclaimdetail`) | see `ibm-mq-cobol-copybook.md` §MQCOBOL-1 note |
| IBM MQ with **no** QL TRAN pattern and **no** COBOL copybook fields | **MQ** (plain) | Camel JMS or Spring Integration — rare; confirm it isn't actually MQ_COBOL first | — |

Don't default "MQ is involved" straight to QL_CICS — check for the actual QL TRAN code signal, and don't default it to MQ_COBOL either without checking for raw COBOL copybook field references with no DFDL/Bindy message set.

---

## State File

Every migration in progress is tracked at `<target>/<project-name>/.iib2spring/state.json`:

```json
{
  "target_dir": "...",
  "iib_dir": "...",
  "project_name": "EIPMS_cvs-v1-{service}-oci",
  "service_name": "{service}",
  "package_root": "com.cvshealth.eip.microservices",
  "backend_type": "SOAP|QL_CICS|RXCLAIM_SBO|MQ_COBOL|RECAP|STRATUS|REST|MQ|DB2",
  "framework": {
    "parent_pom": { "groupId": "com.cvshealth.eip.microservices.frameworks", "artifactId": "framework-starter", "version": "..." },
    "reference_repo": "..."
  },
  "decisions": {
    "generate_tests": true,
    "lombok_on_models": false,
    "lombok_on_services": true,
    "profiles": ["local", "dev-pbmaz", "dev2-pbmaz", "dev3-pbmaz", "qa-pbmaz", "qa2-pbmaz", "qa3-pbmaz", "uat-pbmaz", "stress-pbmaz", "prod-pbmaz", "prod-pbmri"]
  },
  "smoke_test": {
    "request": "... collected at INTAKE Batch A item 6, verbatim from the user ...",
    "expected_response": "... collected at the same time, verbatim from the user ...",
    "attempts": [],
    "status": "pending|passed|failed_blocking"
  },
  "flows": [{
    "name": "...",
    "msgflow": "...",
    "esql": ["..."],
    "subflows": ["..."],
    "backend_type": "...",
    "tran_id": "...",
    "status": "pending|in_progress|done|skipped",
    "layers_done": [],
    "compile_passed": {},
    "gap_check_passed": false,
    "iterations": 0,
    "last_checkpoint": "INTAKE-A"
  }]
}
```

**Resumption protocol** — first action on every re-invocation of a migration already in progress:
1. Look for `<target>/.iib2spring/state.json`
2. If found and any flow is `in_progress` or `pending` → announce "Resuming flow X at checkpoint Y" → jump there, don't restart from INTAKE
3. If not found → start at INTAKE Batch A

---

## SCAFFOLD — repo root, Maven modules, poms, deployment artifacts

**First, create the actual repo-root directory itself, named `EIPMS_cvs-v1-<service>-oci` (or whatever the real sibling repo's exact spelling is, e.g. `EIPMS_cvs-v1-<service>service-oci` — check a sibling if one exists, don't just append the generic pattern blindly) — this is a real step, not just a documentation convention.** Everything else in this stage (parent/web modules, `.github/`, `config/`, `Dockerfile`) goes *inside* that directory. This was missed on the very first blind migration this skill ran (`ValidateBridgeSupply` was scaffolded directly under the target folder with no `EIPMS_`-prefixed wrapper) precisely because `structural-conventions.md` §1 described the convention passively without this stage ever being told to actively create it — don't repeat that: create the wrapper folder as the literal first filesystem action of this stage, before the two Maven modules.

Then create the two-module structure (`*-parent`, `*-web`) plus: parent pom, web pom, base `application.properties`, Dockerfile (repo root — see `structural-conventions.md` §3), `logback-spring.xml` (SOAP/QL_CICS/RXCLAIM_SBO/MQ_COBOL only — see `common-patterns-and-gotchas.md` for the REST exception), `.github/` workflows (see `structural-conventions.md` §2), `state.json`.

Then `mvn compile -q`. Must pass before proceeding.

**[CHECKPOINT]** "Scaffold compiles. Proceed?"

---

## GENERATE — per-flow code generation

For each flow with `status == "pending"`, generate layer by layer **in the order specified for that backend type** (see the connector-specific reference file's "generation order" section). Do not generate everything then compile once — compile after **every** layer.

### Compile-gate auto-fix loop

```
attempt = 0
while attempt < 3:
  mvn compile -q 2>&1 | tee .iib2spring/last_compile.log
  exit 0 → return success
  parse first 5 errors:
    "cannot find symbol" on a framework class/method → check whether that framework class actually
       has the method you assumed (see mandatory-migration-rules.md Rule 1 — QLRequest has no
       builder() despite RestClientRequest having one; don't assume symmetry across framework classes)
    "package X does not exist" → add the missing dependency to the web pom
    "incompatible types" → adjust cast/return type
    else → attempt += 1, do not guess blindly
after 3 attempts → show the errors verbatim → ask the user for a hint, or to skip this flow
```

This loop is exactly what caught the real `QLRequest.builder()` compile error during the ValidateBridgeSupply live test — treat every "cannot find symbol: method X()" as a signal that an assumption about a framework class needs to be verified, not papered over with a wrapper.

### Mandatory gap check (after the service layer, before the tests checkpoint)

Run every item relevant to the detected backend type from `references/gap-checklist.md`. For each flagged item:
1. Auto-fix if unambiguous (rename a file, add a missing property key)
2. For complex gaps (wrong byte lengths, wrong transport, wrong annotation) — regenerate the affected class, recompile, recheck
3. Loop until 0 gaps or 3 iterations, then report any residual items as named GAPs, not silently dropped

**[CHECKPOINT]** "Gap check: {N} issues found / 0 gaps. Tests next?"

### Tests

**Depends on the Batch A answer — this is a required question, not a silent default (Rule 11, revised 2026-10-07).** Create the test class *files* per the usual per-category convention either way: REST gets one focused `{Service}ServiceTest` file, no Cucumber (`rest-external-direct.md` §0c — the only backend type with its own dedicated test-density write-up). Every other category follows a fuller per-layer suite; since none of them has its own test-density section yet, use that category's package-layout section (§8 in each connector reference file, or §6 in `structural-conventions.md`) as the **class list** — i.e. which files to create, correctly packaged and named.

- **If the user asked for full test cases:** write real `@Test` methods with real assertions and mocked behavior — for REST, the full coverage list in `rest-external-direct.md` §0c (happy path, not-found response, every validation error path, backend-exception-as-500, generic-exception-as-500, test-harness routing, every static helper's edge cases); for other categories, a real test per class in that category's package-layout list, covering at minimum the happy path and the obvious error paths. `mvn test -q` must pass with real tests actually running (not 0).
- **If the user asked for structure-only (or didn't answer):** every file is an empty skeleton class — no `@Test` methods, no assertions, no mocked behavior — per the original Rule 11 shape. `mvn test -q` passing with 0 tests run is expected and fine in this case.

Either way, state which mode was used at the top of the HANDOFF output, and if structure-only was chosen, note in the action-items section that real test-case content is deferred — don't leave the files silently empty without saying why.

`mvn test -q`. Fix genuine compile/syntax failures before the checkpoint; if full tests were generated, also fix any failing assertion before the checkpoint — never present a checkpoint with a real test failing.

**[CHECKPOINT]** "Tests: {N} files created ({mode}), {M} passing / 0 failing. Approve?"

---

## HANDOFF — full build, config reconcile, gap re-check, hand-off

`mvn clean verify`.

> ⚠️ **Two alarming-but-benign log outputs — neither is a build failure. Do NOT "fix" either one.** **[live-tested on `cumulativeCopaySummaries`, framework 3.2.2: `mvn clean verify` exit code `0`, `BUILD SUCCESS`, `Tests run: 52, Failures: 0, Errors: 0`.]**
> 1. **`cucumber-reporting:5.7.4:generate` prints a full `ValidationException: No JSON report file was found!` stack trace** for any service with no Cucumber tests. The plugin is bound by the `framework-starter` parent pom (not by generated code), logs this at `WARNING` via its own `generateErrorPage` handler, and **continues** — the build still succeeds. Cross-repo check: **0 surveyed workspace repos** add a skip/`<configuration>` block for it (the only `cucumber` mention found anywhere is an unrelated `<cucumber.version>` property in `fepclaimdetail`), so adding one would deviate from every real repo and violate Rule 2. Leave it; mention it in hand-off prose only if the user asks about the stack trace.
> 2. **`[Fatal Error] :1:8: XML document structures must start and end within the same entity.`** in the surefire log, for any service whose tests deliberately feed malformed XML to an error-path parser. That's the XML parser logging, not a test failure.
>
> 🛑 **Read the exit code, not the log text — and on Windows, get the exit code correctly.** Both outputs above look like failures and neither is. Worse, in **Windows PowerShell 5.1**, piping a native command's output with `2>&1` into the PS pipeline (`mvn ... 2>&1 | Select-String ...`) wraps each stderr line in a `NativeCommandError` and makes the tool report **exit 255 even when Maven returned 0** — this skill's own run was misled by it twice in a row and briefly recorded a non-existent build failure. Redirect to a file and read `$LASTEXITCODE` instead:
> ```powershell
> mvn -f <parent>/pom.xml clean verify --offline > "$env:TEMP\verify.log"
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
keys <web-module>/src/main/resources/application.properties > /tmp/runtime.keys
for f in config/config-map/application.*.properties; do
  echo "== $f"; keys "$f" > /tmp/cm.keys; comm -23 /tmp/runtime.keys /tmp/cm.keys
done
```

Every key printed must be either copied into that config-map or deliberately classified as local-only. `management.endpoints.web.exposure.include` **must** be copied (otherwise `info`/`metrics` return 404 in every environment — live-proven); `spring.profiles.active=local` must **never** be copied. Full key-by-key classification table and the live-test evidence: `gap-checklist.md` **[M15]**.

Re-run the loop after fixing until it prints nothing but consciously-excluded keys. Don't spot-check only `dev` — the whole point is per-file drift.

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

### HANDOFF-B — On success

- Re-run every applicable gap-checklist item on the final tree
- Print any residual open items as named GAPs with a remediation path (who to ask, what's missing)
- Print a suggested commit message
- **STOP** — never `git add`/`git commit`/`git push` without being explicitly asked

Final summary: backend type, flows converted, files written, iterations taken, open-GAP count.

---

## Scope boundary — HANDOFF is the end of this skill

There is no fifth stage. This skill's deliverable is the generated microservice code and project structure; it stops at the HANDOFF stage.

**One narrow exception, added 2026-10-09:** HANDOFF-C's single real API call, using request/expected-response data the user supplies at INTAKE — proving the generated service works end-to-end — is in scope. This is deliberately narrow: one request, one comparison, fix-and-retry up to 3 attempts.

**Still out of scope — belongs to the separate `iib-ms-testing` skill:** bulk/regression API-level testing, IIB-vs-Kong/MS response comparison across many cases, Bruno collections, cucumber-report-driven regression runs, ieonehub/Playwright automation, and any generated Python test-harness scripts. Do not generate those here, and do not create a `helper scripts/` folder in the migrated repo. If the user wants the migrated service validated against IIB at scale, hand off to `iib-ms-testing`.

**In scope:** the JUnit 5 unit tests for the service's own layers (GENERATE, Tests subsection) — those are part of the code deliverable. A short-lived `spring-boot:run` plus a single curl/`Invoke-WebRequest` call to confirm the generated service can actually reach its backend is also in scope, since "a working call to the backend" is part of what `SKILL.md`'s "What done means" defines as done — that's a smoke check, not a test suite.

---

## Hard constraints (apply across every stage)

- Never invent a field mapping not present in the ESQL/XSD/copybook — flag as GAP instead
- Never use `RestClient`/`RestTemplate` for QL_CICS or MQ_COBOL transport (use `QlConnector`/`MQUtility`) — see Rule 4 in `mandatory-migration-rules.md`
- Never use Camel Bindy for MQ_COBOL — that backend type is hand-rolled EBCDIC offset parsing, no annotations
- Never hardcode a requester-ID field to a literal like `"ECS"`; never reach for JWT/JWKS validation to extract a requester ID — no training repo across any backend type needs it (see `common-patterns-and-gotchas.md` cross-category takeaway)
- Never put environment config files inside `src/main/resources/`
- Never leave `crlf` unset on a `@FixedLengthRecord` used for a concatenated request buffer
- Never wire a `RefDataConnector` or hardcode a ref-data lookup table without first confirming the IIB ESQL actually calls reference data
- Never present uncompiled code at a checkpoint
- Never `git commit`, `git push`, or `mvn deploy` without being explicitly asked
- Allowed Maven goals without asking: `compile`, `test`, `verify`, `clean`, `spring-boot:run` (short-lived, e.g. for a live test), `dependency:resolve`, `dependency:tree`
