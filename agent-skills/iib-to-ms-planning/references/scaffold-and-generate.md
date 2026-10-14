<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 122–177. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

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
