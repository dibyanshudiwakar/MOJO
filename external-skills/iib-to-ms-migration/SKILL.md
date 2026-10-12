---
name: iib-to-ms-migration
description: Migrate an IIB/ACE service to a Spring Boot microservice by analyzing the ESQL/msgflow source and generating the equivalent Java code following CVS EIP microservice patterns. Trained on 12 Phase-1/Phase-2 service pairs plus a broader evidence-tagged corrections pass. Supports SOAP, QL/CICS+Camel-Bindy, RxClaim SBO (XML/JAXB), IBM MQ+COBOL Copybook, and REST backend types. Stage-based, checkpoint-driven, state-file-backed workflow with a compile-gate loop and a 21+-item mandatory gap checklist. Use when asked to migrate, generate, or port an IIB service to a Java microservice.
---

# IIB → Spring Boot Microservice Migration Skill

## 1. Introduction

This skill migrates an IBM Integration Bus / ACE service to a Spring Boot microservice: it reads the ESQL/msgflow source, detects the backend connector type, and generates Java code that follows CVS EIP microservice patterns — including deliberate bug-for-bug parity with the IIB behavior where downstream consumers require it.

**Evidence base.** Originally trained on 12 production service pairs (Phase 1 & Phase 2). One of them (`ValidateBridgeSupply`) live-compiled and executed end-to-end against a real backend — that's the strongest evidence tier in this skill, and it's what caught the bugs no amount of reading source would have (see `references/common-patterns-and-gotchas.md` §9, §15, and `references/mandatory-migration-rules.md` Rules 1 and 8). Everything else is cross-repo grep evidence, tagged N/M where available.

A 2026-09-29 survey found 22 real, buildable MS repos actually sitting in the same workspace (`phase1/`+`phase2/`, one folder per service) — most of the 12 originally-trained services plus ~10 more this skill hadn't specifically studied before. That survey answered one narrow question (config-map conventions — see `gap-checklist.md` [M11]/[H16] and `mandatory-migration-rules.md` Rule 6) across all 22; it did **not** re-derive Java code patterns, buffer layouts, or Helm schemas for the ~10 newly-found ones.

> **Treat "confirmed against N real repos" claims in this skill literally** — N is usually 1–3, not 22 — but know that a broader check is one directory listing away (`ls phase1 phase2`) if a specific claim needs re-verifying.

### What this skill covers

| | |
|---|---|
| **Backend types supported** | SOAP, QL/CICS + Camel-Bindy, RxClaim SBO (XML/JAXB), IBM MQ + COBOL Copybook, REST |
| **Workflow** | Stage-based, checkpoint-driven, state-file-backed: `INTAKE → SCAFFOLD → GENERATE → HANDOFF` |
| **Safety nets** | Compile-gate auto-fix loop after every layer; 13 mandatory rules; 20+-item gap checklist run twice |
| **Scope** | Code and project-structure generation, plus one narrow HANDOFF-C smoke test — stops at `HANDOFF` (see [§8 Scope boundary](#8-scope-boundary--code-generation-plus-one-narrow-smoke-test)) |

### Table of contents

1. [Introduction](#1-introduction) *(this section)*
2. [What "done" means — read this before GENERATE](#2-what-done-means--read-this-before-generate)
3. [Mandatory rules — load before generating any code](#3-mandatory-rules--load-before-generating-any-code)
4. [Required reading before INTAKE](#4-required-reading-before-intake)
5. [The migration workflow](#5-the-migration-workflow)
   - [Step 1 — INTAKE](#step-1--intake-intake-and-backend-type-detection)
   - [Step 2 — SCAFFOLD](#step-2--scaffold-project-skeleton)
   - [Step 3 — GENERATE](#step-3--generate-per-flow-generation)
   - [Step 4 — HANDOFF](#step-4--handoff-full-build--hand-off)
6. [Resumption](#6-resumption)
7. [Verification — run before every checkpoint](#7-verification--run-before-every-checkpoint)
8. [Scope boundary — code generation, plus one narrow smoke test](#8-scope-boundary--code-generation-plus-one-narrow-smoke-test)

---

## 2. What "done" means — read this before GENERATE

**Standing instruction from the user (2026-09-29):** the primary goal of a migration is a project that starts from nothing and ends with:

- (a) correct folder/file structure,
- (b) all setup files present and correct (poms, Dockerfile, `.github/`, config-map, Helm), and
- (c) **a working call to the backend** — the generated service compiles, wires the right framework classes and annotations for the detected connector type, and can actually reach the backend (production path or test-harness).

**Exact business-logic fidelity is secondary and gets fixed after testing, not perfected up front.** Concretely:

- **Always get right, no shortcuts**: the framework classes and annotations for the detected backend type — `QlConnector`/`QLRequest`/`QLResponse` + Camel Bindy for QL_CICS, `RxClaimConnector`/`RxClaimRequest` for RxClaim SBO, `MQUtility` for MQ_COBOL, `RestClientWrapper`/`RestClientRequest` for REST, `OAuthClient` where the backend needs OAuth2, plus the cross-cutting ones every type needs — `EieHeader`, `ErrorUtil`/`ErrorStatus`, `@EIPMicroserviceProviderCall`, `@EIPMicroserviceOperation`. These are what make the generated service *able* to reach the backend at all — get them wrong and no amount of correct business logic matters.
- **Can be simplified, refined later**: exact field-by-field ESQL→Java translation, precise validation order/text, exact response transformation nuances. Generate a reasonable, working version (validate the obvious required fields, map the response fields that are clearly identifiable), don't burn time perfecting every byte offset or every error-message wording against the ESQL before the very first test run — that's exactly the kind of detail that's cheap to fix once a real test against the backend shows what's actually wrong, and expensive to get right blind.
- **This does *not* relax the mandatory rules** below (builder-pattern verification, no `RestTemplate`, no `StringBuilder` for QL buffers, credential encryption, `EieHeader` accessor verification) — those are about not introducing *wrong* framework usage, which is a structural/correctness concern, not a business-logic-precision one.

---

## 3. Mandatory rules — load before generating any code

> ⚠️ **Load and apply `references/mandatory-migration-rules.md` in full before INTAKE.** These 13 rules override any pattern shown elsewhere in this skill.

| # | Rule | Quick summary |
|---|---|---|
| 1 | Builder pattern — verified per framework class | Use `.builder()...build()` where the framework class actually supports it (`RestClientRequest`, `RxClaimRequest`) — but `QLRequest` does **not** have one, confirmed by compilation; never assume symmetry across a connector group |
| 2 | Trim pom.xml | Remove every dependency not actually imported by generated code |
| 3 | Dockerfile — full template, not just the label | Repo root, exact base image/labels/non-root-user/JVM-flags — see `structural-conventions.md` §3 |
| 4 | No `RestTemplate` | Always `RestClientWrapper` — even for test-harness bypass, even for QL/CICS |
| 5 | No `StringBuilder` for QL buffers | Always Camel Bindy `@FixedLengthRecord` + marshaller for QL/CICS request **and** response field content |
| 6 | Credential encryption | Encrypt via the CVS endpoint by default, every phase — never plaintext. **Generate real, live Akeyless scaffolding when this specific service genuinely requires it** (checked per-service, never assumed from phase/backend type alone — a Phase 2 REST-to-vendor service can still need zero Akeyless, confirmed real counter-example) |
| 7 | Framework version and Java version | Default `framework-starter` to **3.2.2** and the JDK to **21**; ask the user only if that doesn't apply (they want a different/specific version) — don't inspect sibling repos to infer either one. Java 21 is checked twice: the toolchain itself (Batch C/D) and the generated parent pom's `<java.version>` property |
| 8 | `EieHeader` verified per accessor; `framework-eieusercontext` need is category/service-dependent | Never invent a getter by analogy; check whether the context arrives pre-populated (skip the dependency) or must be built from an ID token (`EieHeaderBuilder.createEieUserContextString`, include it) — confirmed genuinely needed in RxClaim SBO/MQ_COBOL, not needed in the one QL_CICS service checked. JWT/JWKS **validation** for a requester ID stays an absolute never, in every category |
| 9 | No Kong/gateway token-validator reimplementation | Even if the IIB ESQL has an ID-token-check node, never generate a connector/service/exception/config for it — Kong already validates it upstream, confirmed by real-sibling-repo diff (`EmergencyOverrides`) |
| 10 | No custom exception classes or `exception` package | Never `extends RuntimeException`/`Exception`, and no `@RestControllerAdvice` fallback handler either — call `ErrorUtil.errorResponse(...)` (the framework's mechanism) directly at the point of failure; catching the framework's own `EIPMicroserviceException` to re-map it is fine |
| 11 | JUnit tests — ask the user, then generate accordingly | Always ask at INTAKE: full test cases with real content, or structure-only empty skeletons? Honor the answer — generate real `@Test` methods/assertions if they want full content, empty classes if not |
| 12 | `application-local.properties` → always QA for internal CVS backends | Internal-CVS-backend connector URIs point at `qa`/`qa2`, never `dev`/`prod`, confirmed across every HTTP-based backend type checked; external-vendor connectors use that vendor's own non-prod endpoint instead, no CVS "QA" equivalent exists for those |
| 13 | Target directory — same location as the IIB source by default | New MS repo defaults to being created as a sibling of the IIB source repo, not a separate output location picked ad hoc — state the default, let the user override it |

---

## 4. Required reading before INTAKE

| File | Why |
|---|---|
| `references/workflow-and-state.md` | The INTAKE→HANDOFF workflow, the state-file schema, the compile-gate auto-fix loop, and the resumption protocol — **this is the operating procedure**, follow it instead of a flat generate-everything-then-check approach |
| `references/gap-checklist.md` | The full BLOCKING/HIGH/MEDIUM/REST-ONLY/MQCOBOL-ONLY checklist, run after the service layer and again at HANDOFF |
| `references/translation-table.md` | IIB shared-library → MS framework mapping table, used during ESQL→Java mapping |
| `references/structural-conventions.md` | Folder layout, file organization, and annotation conventions common to every backend type — Dockerfile, Helm chart, `.github/` workflows, full environment list, DTO annotations, `EieHeader` accessors |
| `references/common-patterns-and-gotchas.md` | Cross-cutting behavioral gotchas, several confirmed by live execution, plus the cross-category meta-lessons at the end |

---

## 5. The migration workflow

The migration runs as four sequential steps — **INTAKE → SCAFFOLD → GENERATE → HANDOFF** — each ending in a `[CHECKPOINT]` that gets written to `state.json` so the migration can resume safely (see [§6 Resumption](#6-resumption)).

### Step 1 — INTAKE: intake and backend-type detection

Follow `references/workflow-and-state.md` INTAKE in full:

1. **Always take these 5 inputs directly from the user before moving ahead — ask explicitly, wait for a real answer, never silently default past any of them** (standing instruction, 2026-10-09): (1) IIB source repo path, (2) migrated MS destination directory, (3) framework version, (4) one successful request's data, (5) the expected response for that request. A suggested default is fine to *offer when asking* — same-location-as-source for #2 (Rule 13), `framework-starter` 3.2.2/Java 21 for #3 (Rule 7) — but offering a default never replaces actually asking and capturing the answer. #4/#5 are for HANDOFF-C's smoke test — don't invent them, don't re-ask at HANDOFF. Also collected in this batch: reference OCI project, test-generation preference (Rule 11), CVS programme phase (1/2). *(Batch A, see `workflow-and-state.md` for the full detail)*
2. Auto-detect the backend type by reading every `.msgflow`/`.esql` file against the detection table below. *(Batch B)*
3. Check the toolchain. *(Batch C)*
4. Print the pre-flight table and confirm before proceeding. *(Batch D)*

#### Backend Type Detection

| Signal in IIB | Signal in MS pom | Connector Group | Reference file |
|---|---|---|---|
| SOAP envelope in ESQL + HTTP Request node | `framework-soapclient` | **SOAP** | `references/soap-backend.md` (single-source, unverified by this skill — see its own caveat) |
| `ComIbmMQOutput` queue containing `QL.ESL.PBM.QL.REQUEST` + a QL transaction code in ESQL | `framework-connector-qlcics` + `camel-bindy` | **QL/CICS + Camel Bindy** | `references/ql-cics-camel-bindy.md` |
| `ComIbmMQOutput` queue referencing `RXCLAIM`/`SBO` | `framework-connector-rxclaimsbo` | **RxClaim SBO (XML/JAXB)** | `references/rxclaim-sbo-xml-jaxb.md` |
| IBM MQ + raw COBOL copybook fields, **no** QL TRAN code, **no** Bindy/DFDL message set | `framework-mqclient` | **IBM MQ + COBOL Copybook** | `references/ibm-mq-cobol-copybook.md` (single-source — see its own caveat) |
| `ComIbmWSRequest` (HTTP) to an external URL with no QL/RxClaim/SOAP routing | `framework-restclient` (no `camel-bindy`, no QL/MQ/SOAP framework) | **REST → External / Direct** | `references/rest-external-direct.md` |

> Don't default "MQ is involved" straight to QL_CICS, and don't default it to MQ_COBOL either — check for the actual signal (QL transaction code vs. raw copybook fields with no DFDL/Bindy message set) before picking. This is the quick-glance version — `workflow-and-state.md`'s own Backend Type Detection table is canonical and additionally covers RECAP/STRATUS/DB2/plain-MQ edge cases not shown here; check it before flagging an unrecognized signal as a gap.

### Step 2 — SCAFFOLD: project skeleton

1. **First action: create the repo-root directory itself**, named `EIPMS_cvs-v1-<service>-oci` (match a sibling repo's exact spelling if one exists in the workspace — the suffix isn't always `service-oci` vs `-oci` in a way you can guess). Everything below goes inside it — this is an active step, not just a naming convention to keep in mind.
2. **For Dockerfile, `.github/`, and Helm chart (`config/deploy-config/`) — use `references/fixed-file-templates.md`, don't hand-generate from memory.** That file has literal copy-ready templates extracted from 12 real training repos across all 4 backend types, plus explicit decision tables for the 3 things that genuinely vary (JVM flags, Helm family RICH vs SIMPLE, `permissions:` block). Hand-generating these files from memory is how `RankedMatch` and `GetGrievanceSummary` each shipped real bugs (sed-substitution miss, missing Helm values keys) — the templates exist to prevent that entire class of error.
3. Per `workflow-and-state.md` SCAFFOLD, build out: two-module structure, parent + web pom, base `application.properties`, Dockerfile (from `fixed-file-templates.md`), `logback-spring.xml` (SOAP/QL_CICS/RXCLAIM_SBO/MQ_COBOL only — REST skips it, `common-patterns-and-gotchas.md`), `.github/` workflows (from `fixed-file-templates.md`), `state.json`.
4. Compile. **[CHECKPOINT]**

### Step 3 — GENERATE: per-flow generation

1. For each flow, generate layer by layer in the backend-type-specific order given in the matching connector reference file's "structural mapping" / package-layout section, compiling after **every** layer via the compile-gate auto-fix loop (`workflow-and-state.md`) — not once at the end.
2. Map ESQL modules to Java classes using the connector reference file's structural-mapping template, cross-checked against `references/translation-table.md` for shared-library calls (`ESLReferenceDataCacheHelper`, `ESLConfigurationManager`, etc. — verify the IIB source actually calls these before wiring an equivalent; `gap-checklist.md` [H10]).
3. After the service layer compiles, run every applicable item from `references/gap-checklist.md` (BLOCKING + HIGH + the detected backend type's category-specific section). Auto-fix what's unambiguous; regenerate-and-recompile for anything requiring a judgment call; loop up to 3 times; report the rest as named GAPs. **[CHECKPOINT]**
4. Generate test **class files** per the density/structure convention for the detected backend type: REST gets one focused `{Service}ServiceTest` class, no Cucumber (`rest-external-direct.md` §0c — the only category with its own test-density write-up so far). Every other category follows a fuller per-layer suite — use that category's package-layout section as the class list until a dedicated test-density note exists for it (see `workflow-and-state.md`'s Tests subsection).
   - **Content depends on the INTAKE answer (Rule 11):** if the user asked for full test cases, write real `@Test` methods with real assertions and mocked behavior covering the happy path, validation errors, and backend/generic-exception paths; otherwise every file is an empty skeleton — no `@Test` methods, assertions, or mocked behavior.
   - `mvn test -q` must pass either way (0 tests run is only fine in structure-only mode). **[CHECKPOINT]**

### Step 4 — HANDOFF: full build & hand-off

1. Run `mvn clean verify`.
2. **Delete `*-oci-parent/target/`** — the parent module is a pure aggregator pom with no jar of its own, so its `target/` is just empty build noise, not a deliverable; never leave it in the handed-off tree. **Leave `*-oci-web/target/` alone** — that one holds the actual built jar and is expected (`workflow-and-state.md` HANDOFF for the full rationale).
3. **Run HANDOFF-A0 before HANDOFF-A** (`workflow-and-state.md`): check the literal baseline block in `references/templates/config-map-baseline.properties` byte-for-byte against the packaged `application.properties` AND every config-map independently — this catches the recurring real failure (RankedMatch, GetGrievanceSummary) where the block was missing from *both*, which a packaged-vs-config-map diff alone can't see. Copy that physical file's content, don't retype it from memory.
4. **Run HANDOFF-A — reconcile runtime properties into every config-map** (`workflow-and-state.md` HANDOFF-A): diff the packaged `application.properties` key list against each of the ten `config/config-map/application.<env>-<cluster>.properties` files.
   - `SPRING_CONFIG_LOCATION` *replaces* the classpath config locations, so the packaged file is never loaded in a deployed pod and the config-map must be self-contained.
   - `management.endpoints.web.exposure.include` must be copied (live-proven: without it `info`/`metrics` 404 in every environment).
   - `spring.profiles.active=local` must never be copied.
   - See `gap-checklist.md` **[M15]**.
5. **Run HANDOFF-C — one real API hit, fix-and-retry on failure** (`workflow-and-state.md` HANDOFF-C): using the request/expected-response collected at INTAKE (never re-asked here), start the built service, make exactly one real HTTP call, and compare the actual response to the expected one.
   - If it passes, record which attempt succeeded and continue.
   - If it fails on a *generated-code* problem (wrong mapping, wrong config key, wrong endpoint, wrong header), fix it, recompile, restart, and retry the same stored request — up to **3 attempts total**, the same bound used by the gap-checklist and compile-gate loops.
   - If it still fails after 3 attempts, or the cause clearly isn't something a regenerate can fix (backend unreachable, no matching test data, credentials not provisioned), **stop retrying** and report it as a **BLOCKING** gap with the actual-vs-expected diff from every attempt — never report HANDOFF as successful while this is unresolved.
6. Re-run the full gap checklist on the final tree. Print residual GAPs with a remediation path (who to ask — QL team for buffer questions, infra team for queue/deployment questions, the service owner for any plaintext credential value still needing encryption).
7. If this service was determined to require Akeyless, confirm the scaffolding was generated live (not disabled/scaffolded-and-off) and list the actual secret path as an action item if it wasn't yet confirmed by the team; if it was determined NOT to require Akeyless, say so explicitly rather than leaving it unaddressed (Rule 6).
8. **Always list the deferred exception handler as a future action item** (Rule 10) — e.g. *"No exception handler was generated; add one after `iib-ms-testing` identifies what's actually needed"* — and, if the ESQL had an ID-token-check node, that Kong is expected to enforce it upstream and nothing was generated for it (Rule 9).
9. Print a suggested commit message.

> **Never** `git commit`/`git push`/`mvn deploy` without being explicitly asked.

#### Output format

State at the top:
- **Framework version selected:** `x.y.z` (reason: user-specified / highest found in workspace repo `<name>`)
- **CVS programme phase:** 1 or 2 (determines credential handling approach)
- **Backend type:** determined in INTAKE

Then present files in this order: `.github/` workflows → parent pom → web pom → Dockerfile → config-map properties + Helm chart → controller/API interface → service → connector → buffer builders/XML converters/request mappers → buffer parsers/response mappers → Bindy/JAXB models → error/validator classes → constants.

**Action items section** — every item needing human confirmation: credential values, MQ queue names, QL/reference-data team confirmations, JWT/token pre-validation ownership (Kong gateway vs. in-service — note it, don't generate it, Rule 9), framework version if inferred rather than user-specified, the exact Akeyless secret path if the service needed one but the path wasn't yet confirmed by the team (Rule 6), **and the deferred exception handler, every migration, every backend type (Rule 10)**.

---

## 6. Resumption

First action on any re-invocation: look for `<target>/.iib2spring/state.json` per `workflow-and-state.md`. If a flow is `in_progress`/`pending`, resume at its `last_checkpoint` rather than restarting INTAKE.

---

## 7. Verification — run before every checkpoint

### 7.1 Mandatory-rules checklist (`references/mandatory-migration-rules.md`)

- [ ] Every `.builder()` call on a framework model class verified to actually exist on that class — not assumed by analogy with a sibling class
- [ ] pom.xml has no dependency not directly imported by the generated code — but does NOT omit `framework-restclient` (needed regardless of backend type — it's the source of `EieHeader`/`ErrorUtil`/`ErrorStatus`)
- [ ] Dockerfile at repo root matches the exact template in `structural-conventions.md` §3 (base image, all 4 LABELs, non-root user, JVM flags — not just the maintainer label)
- [ ] No `RestTemplate` anywhere, including test-harness bypass paths, including QL/CICS
- [ ] No `StringBuilder`/manual string concat for QL buffer field content, request **or** response side — Camel Bindy throughout
- [ ] Credentials encrypted via the CVS endpoint by default, every CVS phase, no plaintext — **and if this service was determined to need Akeyless, the live scaffolding (not a disabled placeholder) is actually present** (Rule 6)
- [ ] Framework/Java version was **explicitly asked at INTAKE and the user's actual answer was captured** (Rule 7) — not silently defaulted to 3.2.2/21 without asking, and never inferred from sibling repos. Checked both in the toolchain (`java -version`) and in the parent pom's `<java.version>` property
- [ ] HANDOFF-C's smoke test ran using the request/expected-response collected at INTAKE, passed (noting which attempt) or was reported as a named BLOCKING gap after 3 attempts — never silently skipped or silently passed
- [ ] `EieHeader` used only via confirmed real accessors; `framework-eieusercontext` included only if the target flow's category/service genuinely needs to populate the context from an ID token (checked, not assumed); no JWT/JWKS validation anywhere
- [ ] No Kong/gateway token-validator connector, service, exception, or config-map block generated for an ESQL ID-token-check node, even though the ESQL genuinely has one — Kong handles it upstream (Rule 9)
- [ ] No custom `Exception` subclass and no `exception` package anywhere — not even a `@RestControllerAdvice` fallback handler; every error path calls `ErrorUtil.errorResponse(...)` directly (Rule 10)
- [ ] Every generated JUnit test class is an empty skeleton — no `@Test` methods, assertions, or mocks; `mvn test -q` passes with 0 tests run (Rule 11)
- [ ] `application-local.properties`'s internal-CVS-backend connector URI(s) point at `qa`/`qa2`, never `dev`/`prod`; external-vendor connectors use that vendor's own non-prod endpoint instead (Rule 12)

### 7.2 Gap checklist (`references/gap-checklist.md`)

Run every BLOCKING + HIGH item, plus MEDIUM once deployment artifacts exist, plus the REST-ONLY or MQCOBOL-ONLY section if applicable. See that file for the full 20+ items — highlights easy to miss:

- `crlf=""` on request-buffer Bindy records **[H9]**
- SLS fields normalized on the way *out* of a response record, not just built on the way in **[H5]**
- Blank fields trim-to-`""` not trim-to-`null` under `@JsonInclude(NON_NULL)` **[H6]**
- Every already-parsed Bindy field checked against unmapped DTO fields before flagging one as "needs team confirmation" **[H7]**
- "not-found → 200" verified per-flow, not assumed repo-wide **[R6]**
- `application.circuitbreaker.*` properties present with no matching `isCircuitBreakerError()` branch in the service layer **[M18]**
- Kong/token-validator reimplementation **[M19]**
- Controller containing error-response calls or inline business logic **[M20]**
- Any `exception` package or custom `Exception` subclass **[M21]**
- `swagger/model/xml` nesting instead of top-level `model/xml` **[M22]**
- `application-local.properties` pointing at `dev`/`prod` instead of `qa` **[M23]**
- A leftover `target/` folder under the parent module at HANDOFF **[M24]**
- HANDOFF-C's smoke test left at `"pending"` instead of `"passed"`/`"failed_blocking"` **[M25]**

### 7.3 Structural-conventions checklist (`references/structural-conventions.md` §12)

`.github/` workflows; Helm chart; full environment property list; **config-map self-containment (every packaged-`application.properties` key present in all ten config-maps — [M15])**; **baseline block present byte-for-byte in the packaged file AND all ten config-maps, checked against the physical `references/templates/config-map-baseline.properties`, not from memory (HANDOFF-A0)**; concern-split constants; dedicated validator/utility classes (or, for RxClaim SBO, the thin-`Service`-plus-`*ServiceUtil`-under-`util/` pattern — see `rxclaim-sbo-xml-jaxb.md` §8); records with full annotations (where records are the convention — not RxClaim SBO, see `rxclaim-sbo-xml-jaxb.md` §0); framework `ErrorStatus`; `@EIPMicroserviceOperation` + concrete return types; `@Validated`; `@Data`-only Bindy models; contiguous wire segments as one Bindy class; controller delegates to the service only, no inline error-response calls or request/response mapping logic.

### 7.4 Common-patterns checklist (`references/common-patterns-and-gotchas.md`)

408 timeout reproduced; error text verbatim; buffer initiators exact; routing keys not copy-pasted; queue names flagged for infra; and the cross-category meta-lessons at the end of that file — especially: don't generalize a single-repo finding into an absolute rule without cross-checking, the way this skill's own Rule 8 once did and had to be corrected.

---

## 8. Scope boundary — code generation, plus one narrow smoke test

This skill generates the microservice **code and project structure**. It does **not** do bulk/regression API testing, IIB-vs-MS response comparison at scale, Kong/Bruno collections, or cucumber-report-driven regression runs — that is the separate **`iib-ms-testing`** skill's job. Finish at HANDOFF and stop; if the user wants the migrated service validated against IIB at scale, point them at `iib-ms-testing` rather than generating test-harness scripts here.

**One narrow, deliberate exception, added 2026-10-09: HANDOFF-C.** A single real API call — using one request/expected-response pair the user supplies at INTAKE, with a fix-and-retry loop (up to 3 attempts) on failure — is in scope, because it's the most direct proof of this skill's own definition of done (§2: "a working call to the backend"). This is still narrow: one request, one comparison, not a test suite. See `workflow-and-state.md` HANDOFF-C for the full procedure.

Generating JUnit 5 unit tests for the service's own layers is in scope (GENERATE) — that's part of the deliverable code, not API-level comparison testing.
