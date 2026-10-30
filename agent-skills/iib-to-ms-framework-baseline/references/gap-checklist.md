# Mandatory Gap Checklist

Run every item applicable to the detected backend type after the service layer is generated, and again on the final tree at HANDOFF. Auto-fix what's unambiguous; report the rest as named GAPs with a remediation path — never silently drop a flagged item.

This checklist absorbs both the original 21-item list from the sibling migration agent and the items this skill's own live-testing turned up (marked **[live-tested]** below) — those carry the strongest evidence tier since they were caught by actual compilation/execution against a real backend, not just by reading source.

---

## BLOCKING — will fail to start or corrupt data

- **[B1]** QL connector: grep the connector package for `RestClient|RestTemplate|WebClient` → must use `QlConnector` only, for QL_CICS.
- **[B2]** Test-harness bypass path: grep the service/messaging layer → must exist (`RestClientWrapper`-based, preferred per Rule 4, or a legacy `RestTemplate` `@Bean` in `AppConfig` if that's what the sibling reference project for this specific service already uses consistently).
- **[B3]** `camel-spring-boot-starter` present: grep the web pom → must be present for QL_CICS (not `camel-core` alone — Bindy `RouteBuilder` needs the auto-configured `CamelContext`).
- **[B4]** `framework-restclient` dependency present: grep the web pom → must be present **regardless of backend type** — it's the source of `EieHeader`, `ErrorUtil`, `ErrorStatus`, and the `@EIPMicroserviceProvider*` annotations, not `framework-connector-qlcics` or any other connector-specific module. **[live-tested]** confirmed via compilation — omitting it breaks the build even for a "QL-only" service.
- **[B5]** `framework-starter` version: grep the parent pom → must match the user's spec, or the highest version found across sibling repos in the workspace if the user said "latest" (Rule 7) — never hardcode from memory or from a single training example.
- **[B6]** Builder-pattern assumptions verified per framework class: grep every `.builder()` call against a framework model class (`QLRequest`, `RxClaimRequest`, `RestClientRequest`, etc.) → each one must be confirmed to actually exist on that class, not assumed by analogy with a sibling class in the same connector group. **[live-tested]**: `RestClientRequest.builder()` exists; `QLRequest.builder()` does **not** — only plain setters. A `cannot find symbol: method builder()` compile error means the framework is telling you that class doesn't support it — revert that one class to setters, don't force a local wrapper onto a class you don't own the source of.

---

## HIGH — wrong business output (compiles fine, silently wrong)

- **[H1]** Requester-ID source, QL_CICS category: grep the buffer builder for a hardcoded literal (`"ECS"` or similar) → must instead resolve from the raw `EieUserContext` string. Prefer `ObjectMapper.readTree()` navigation with a flat-key fallback over a plain `indexOf`/substring scan — the scan works (and is what most existing QL_CICS repos actually do) but is the more fragile of the two, confirmed to break on whitespace/field-order variance. **Never** reach for JWT/JWKS validation for this in the QL_CICS category — zero training repos need it.
- **[H1a]** Requester-ID source, RXCLAIM_SBO/MQ_COBOL category: this is the **opposite** convention from H1 — both categories use the framework's JWT-populated `eieUserContext` string directly (via `eieHeaderBuilder.createEieUserContextString(eieHeader.getIdToken())`), with **no** `accountIdentifierValue` substring/JSON extraction step at all. Don't cross-apply the QL_CICS extraction pattern here, and don't cross-apply this direct-passthrough pattern back to QL_CICS — the two categories are genuinely different, both correct for their own category.
- **[H2]** Response parser input type, QL_CICS: grep the response buffer parser → must accept `String` (from `QLResponse.getResponseMsg()`), not `byte[]`.
- **[H3]** Section header length, QL_CICS: grep the response parser's section scan → outer data-section tags are 8 characters (e.g. `"00040061"`), not 4 (`"0061"`) — confirm against the actual `.mxsd`/`tdsElemRep` `tagLength` attribute for the specific service, since this can vary.
- **[H4]** Bindy used for response parsing, QL_CICS: grep the response parser for direct `substring(` field-content extraction → the buffer-locating scan (finding section/record boundaries) may legitimately use `indexOf`/`substring`, but the actual field **content** must come from a Camel Bindy unmarshal, not manual substring slicing field-by-field.
- **[H5]** Signed-decimal fields normalized on the way OUT of a response record, not just built on the way in: see `common-patterns-and-gotchas.md` §3 — **[live-tested]** a raw SLS field like `"+0018"` passed straight through (as both a displayed value and a ref-data lookup key) produced a wrong display value and a silently-failed lookup, with no exception anywhere. Every SLS field read from a Bindy response record needs a `parseSignedNumeric`-style helper applied before it reaches a DTO field or a lookup key.
- **[H6]** Blank-field convention is trim-or-empty-string, not trim-or-null: **[live-tested]** confirmed by diffing against a real reference response — a real service's mapping trims every string field to `""` when blank, never to `null`. If the DTO uses `@JsonInclude(NON_NULL)`, converting blank to `null` silently **omits the field from the JSON** instead of showing it as `""`; a consumer checking for key presence would see different behavior. Grep the mapping method for a `trimOrNull`-style helper → replace with `trimOrEmpty` (or equivalent) unless the specific reference project for this service confirms otherwise.
- **[H7]** Every response-buffer field that's actually being parsed is also being wired to an output field: **[live-tested]** a response DTO field (`CRTBenefitRequestId`) was left hardcoded to `null` with an "ACTION ITEM — confirm with QL team" comment, when the source value was already being parsed into a different-named Bindy field (`brtId`/`RPL_BSP_BRT_ID`) and simply never connected to the output slot. Before flagging a DTO field as "needs QL team confirmation," check whether every already-parsed Bindy field has actually been wired to an output field — the answer may already be sitting in code you wrote.
- **[H8]** LINKS encryption (QL_CICS, image-retrieval flows only): grep `EncryptionUtil` for AES usage → must exist if the flow calls LINKS; a raw unencrypted `LinksConnector` call must NOT exist.
- **[H9]** Bindy `crlf` set explicitly on request-buffer records: grep every `@FixedLengthRecord` used for a *concatenated* request buffer for the `crlf` attribute → must be set to `""` explicitly. Cross-repo evidence: 0 of 3 surveyed QL_CICS Bindy-for-request repos set this, and the 2 that marshal via real Bindy routes both independently reinvented the same `stripTrailingNewline()` workaround to strip the `\r\n` Bindy injects by default. Fix at the annotation — don't add another hand-rolled strip helper.
- **[H10]** Reference-data wiring only when the IIB source actually calls it: grep the IIB ESQL for an actual reference-data call (`EXTERNAL NAME`, `ESLReferenceDataCacheHelper`, or similar) before wiring a `RefDataConnector` or hardcoding a lookup table in the MS. Cross-repo evidence: 0 of 5 surveyed QL_CICS repos call ref-data at all — don't add one speculatively just because a translation-table entry mentions it exists as a pattern.
- **[H11]** Request-model field types match the IIB/JSON contract exactly: e.g. a `lineItemNumbers`-style field must be `List<String>`, not `List<Integer>`, if that's what the reference contract uses — check the actual JSON schema/OpenAPI spec, don't assume the "natural" Java type.
- **[H12]** IIB validation order preserved exactly: grep the validator for the sequence of checks → must match the ESQL's own validation order field-by-field (e.g., one flow validates an expiration-indicator field before the order-number field — reproducing the wrong order can surface the wrong error first for a multi-invalid-field request).
- **[H13]** Error message templates match verbatim, not paraphrased: grep the service/validator for inline error strings built with `new String(...)`/string concatenation at the callsite → should reference a named constant (`AppConstants`/`ErrorConstants`/etc.), and that constant's text must match the IIB error text exactly, not a reworded approximation.
- **[H14]** **[live-tested]** No `ErrorUtil.errorResponse(...)` call for one error case sits inside a `try` block whose own `catch (Exception e)` handles a *different* failure: grep every method containing more than one `errorResponse(...)` call → each one either sits outside any enclosing try/catch, or the enclosing catch is scoped narrowly enough (or typed narrowly enough) that it cannot catch the `EIPMicroserviceException` the *other* `errorResponse` call throws. Caught via a failing unit test (expected 401, got 500) — a "token missing → 401" call placed in the same try block as a "token-fetch threw → 500" catch gets its own thrown exception silently caught and overwritten by that same catch. See `rest-external-direct.md` §0d and `common-patterns-and-gotchas.md` item 16 for the general pattern — applies to any backend type, not just REST/OAuth.
- **[H15]** OAuth2 token acquisition uses `framework-oauth`'s `OAuthClient` bean, not a hand-rolled cache: grep the connector package for a `volatile`/TTL-checked cache field or a manual `Encryption.decrypt()` call feeding a Basic-Auth header → for a service that authenticates via a vendor OAuth2 token endpoint, prefer an injected `OAuthClient` (`oAuthClient.getAccessToken()`, no-arg or `getAccessToken(clientName)` depending on how many OAuth clients are configured) over reinventing caching/expiration in application code — confirmed via a real repo diff (`rest-external-direct.md` §0d) that this is the actual, deployed pattern, not just a documented ideal.

---

## MEDIUM — operational/deployment

- **[M1]** Config-map location: `config/config-map/` exists, and every env file is **outside** `src/main/resources/` (files bundled in the JAR can't be overridden at deploy time).
- **[M2]** Config-map filename convention: `application.{env}-{cluster}.properties` — dots, not hyphens, no subdirectory per environment.
- **[M3]** Constants class naming: don't hard-fail on an exact name like `AppConstants` — check for a single, consistently-named literals-holding class (or a concern-based split like `Constants`/`ErrorConstants`/`QLResponseConstants`, confirmed live against ValidateBridgeSupply) per service. Cross-repo evidence: only 1 of 5 surveyed QL_CICS repos actually uses the literal name `AppConstants`.
- **[M4]** Test-harness P-mode check present: grep the service/messaging layer for `getTestHarnessMode()` → must exist.
- **[M5]** Base `application.properties` has `spring.mvc.throw-exception-if-no-handler-found=true`.
- **[M6]** No `SoapClientConfig` exclusion filter on a non-SOAP `Application` class.
- **[M7]** Helm chart present: `config/deploy-config/Chart.yaml` exists, plus `templates/` and per-env `values.*.yaml` (see `structural-conventions.md` §1).
- **[M8]** `logback-spring.xml` present for SOAP/QL_CICS/RXCLAIM_SBO/MQ_COBOL; **absent** is correct for REST (see the REST-ONLY section below and `common-patterns-and-gotchas.md`).
- **[M9]** `.github/config/DeployMetadata.json` + `.github/workflows/{build,deploy}.yaml` present at repo root — easy to skip since it isn't Java source (see `structural-conventions.md` §2 and the standing memory rule).
- **[M14]** **[live-tested]** If `.github`/Dockerfile content was copied from a prior migrated repo and substituted via a literal find/replace (e.g. `sed 's/OldService/NewService/g'`), grep the result for the *old* service name afterward — a substitution keyed on the concatenated form (`ValidateBridgeSupply`) will silently miss a spaced display-name variant (`Validate Bridge Supply Service - Deploy`) sitting in the same file, leaving a completely wrong workflow name behind. Caught on `GetGrievanceSummary`'s `deploy.yaml`, which still said `"Validate Bridge Supply Service - Deploy"` after the copy. Check `name:` fields specifically — they're the most likely place a spaced variant hides.
- **[M12]** Generated output's top-level directory is named `EIPMS_cvs-v1-<service>-oci`, created as an explicit first step of SCAFFOLD — not a bare `<service>` folder with the two Maven modules sitting directly inside it. Missed on this skill's very first migration (`ValidateBridgeSupply`) because the convention was documented passively in `structural-conventions.md` §1 without SCAFFOLD ever being told to actively create it — fixed in `workflow-and-state.md` SCAFFOLD, but check for the regression on any older migrated output too.
- **[M11]** **[live-tested, cross-repo confirmed across 22 real repos, every backend type]** Config-map files duplicate the baseline server/jackson/circuit-breaker block into *every* environment's `application.<env>-<cluster>.properties`, not just into `application-local.properties` — this is universal across QL/CICS, RxClaim SBO, MQ, REST, and OAuth-REST, not REST-specific. Typical additions beyond the circuit-breaker block: `logging.config=classpath:logback-<env>.xml` (one shared file per tier: `logback-dev.xml` covers dev/dev2/dev3, `logback-qa.xml` covers every qa tier, etc.) and `server.max-http-request-header-size`/`server.tomcat.max-http-response-header-size`/`server.tomcat.max-http-header-count`. Confirmed safe to reference `logging.config=classpath:logback-<env>.xml` without authoring that file in the service's own repo — every one of those filenames (`logback-dev.xml`, `logback-qa.xml`, `logback-stress.xml`, `logback-uat.xml`, `logback-prod.xml`, `logback-local.xml`) is bundled directly inside the `framework-core` jar (confirmed across versions 3.0.0-3.2.2 by listing jar contents), which every service already depends on transitively. See `rest-external-direct.md` §6's "per-environment config-map variant."
  ⚠️ **Don't expect byte-perfect consistency, though.** Across the 22-repo survey, ~9 showed one baseline key present in some env-tier files and missing from others (usually `spring.main.banner-mode`, sometimes `management.endpoints.web.exposure.include` or the header-size trio) — human drift in the real repos, not a rule to reverse-engineer. Match the overall pattern (duplicate the baseline block into every config-map file); don't chase every real repo's exact per-key inconsistency as if it were meaningful.
  ⚠️⚠️ **Correction (2026-09-29, live-tested) — supersedes the "don't chase it" advice above for one specific key.** `management.endpoints.web.exposure.include` is **not** cosmetic drift; omitting it is a real defect. Proven by execution on `cumulativeCopaySummaries`: running the built jar with the pod's own `SPRING_CONFIG_LOCATION` pointed at a config-map *lacking* the key gave **`health`→200, `info`→404, `metrics`→404**; adding only that key to the same config-map and restarting gave **200/200/200**. The mechanism is in **[M15]**. So — treat `spring.main.banner-mode` and header-size-trio drift as the harmless variance this note describes, but **always** write `management.endpoints.web.exposure.include` into every config-map even when the reference repo you are copying from omits it in some env tiers. A reference repo missing that key has a latent bug; it is not a convention to reproduce.
- **[H16]** **[cross-repo confirmed, 22 real repos]** The `test-harness-host` key's naming is generic-by-connector (`application.http.restclient.<connector>.test-harness-host`) in the *majority* (~17/22) but not universal — confirmed real deviations: `GetGrievanceSummary` replaces it entirely with a service-scoped `application.grievancesummary.ql.test-harness-host`; `orderimageinfoservice`'s primary `ql` connector has no `-host` key at all and uses `application.orderimageinfo.test-harness-url` instead; `GetGrievanceByCriterias` drops the `.http.restclient.` infix entirely (`application.mhk.test-harness-url`, `-url` not `-host`); `inductionorderservices` and `BenService-GetBeneficiaries` add a redundant service-scoped key *alongside* the generic one (both present). Treat generic-by-connector as the default to generate, but grep the target service's own reference project for its actual key before assuming — don't be surprised by any of the four variants above.
- **[M10]** **[live-tested]** Helm `values.*.yaml` files use this org's real flat, SCREAMING_SNAKE_CASE key schema (`APP_NAME`, `PODS`, `CPU_LIMIT`/`MEM_LIMIT`, `CONTEXT_ROOT`, `ENV`, `CLUSTER`, `HEALTH_CHECK_PATH`/`HEALTH_CHECK_PORT`, `PATH`, `SERVICE_TYPE`, `Protocol`, `Revision_History`, `ENV_VAR`, `type`, `BASE_64_ENCODED_SECRET_TEXT`, etc. — see `structural-conventions.md` §13), not a generic camelCase/nested "Helm best-practices" schema (`appName`, `image.repository`, `resources.limits.cpu`). Confirmed by diffing a skill-generated chart against two real, deployed repos across two backend types: the two schemas are incompatible — a chart built on the wrong one renders with missing/null values almost everywhere. `Chart.yaml` uses a minimal `apiVersion: v1` with just `name`+`version`, not `apiVersion: v2` with `description`/`type`/`appVersion` — confirmed identical across both real repos checked. Template filename casing is genuinely inconsistent across the org (one real repo lowercases `deployment.yaml`/`service.yaml`, another capitalizes all four) — not a settled rule either way.
- **[M15]** **[live-tested]** **Every runtime property key in the packaged `application.properties` also exists in all ten config-map files** — the config-map is the *only* config source in a deployed pod, so a key that lives solely in the packaged file silently disappears on deploy. `Deployment.yaml` sets `SPRING_CONFIG_LOCATION=/etc/config/<APP_NAME>/data/`, and `spring.config.location` **replaces** the default config locations rather than adding to them (it is not `spring.config.additional-location`) — so `classpath:/application.properties` is **never loaded** in dev/qa/uat/stress/prod. Anything present only there is gone.
  Run this diff before HANDOFF, for **each** env file, not just `dev`:
  ```bash
  keys() { grep -oE '^[a-zA-Z][a-zA-Z0-9._-]*=' "$1" | sed 's/=$//' | sort -u; }
  keys <web-module>/src/main/resources/application.properties > /tmp/runtime.keys
  for f in config/config-map/application.*.properties; do
    echo "== $f"; keys "$f" > /tmp/cm.keys; comm -23 /tmp/runtime.keys /tmp/cm.keys
  done
  ```
  Every key the diff prints must be either copied into that config-map or consciously classified as local-only. The classification, confirmed on `cumulativeCopaySummaries`:
  | Key | Action | Why |
  |---|---|---|
  | `management.endpoints.web.exposure.include` | **copy — mandatory** | Not set by any env var. Spring Boot's default web exposure is `health` only → `info`/`metrics` 404 in every environment. This is the one that actually broke. |
  | `management.endpoints.web.base-path` | copy | `Deployment.yaml` does also supply `MANAGEMENT_ENDPOINTS_WEB_BASEPATH` via relaxed binding, so it survives either way — but copy it so the config-map stays self-contained and doesn't depend on the chart. |
  | `spring.main.banner-mode` | copy | Harmless, keeps the baseline block uniform ([M11]). |
  | `spring.profiles.active=local` | **never copy** | Copying it activates the *local* profile in prod. This is the one key the diff will surface that must stay behind. |
  Use `${CONTEXT_ROOT:<default>}` inside the config-map's `base-path` value rather than a hardcoded literal, so a chart-level `CONTEXT_ROOT` override still wins.
  ⚠️ **Why this was missed and why it is a checklist item rather than prose:** `structural-conventions.md` §5 *already* stated config-maps carry `management.endpoints.web.exposure.include` — but only as a passive clause inside a sentence, with no stage ever instructed to actively verify it. The generated output therefore put the whole ACTUATOR block in the packaged `application.properties` and none of it in the config-maps. This is the **identical failure mode as [M12]** (repo-root wrapper directory documented passively in §1, never actively created, missed on the first migration). Recurring lesson: **a convention that lives only in prose gets skipped — it needs an active step plus a mechanical check.**
- **[M16]** **[live-tested, cross-repo confirmed 20/20, every backend type]** **`application.properties` is tiny; `application-local.properties` is the big one — do not invert them.**
  | File | Size | Contents |
  |---|---|---|
  | `src/main/resources/application.properties` | **1-7 lines** | `spring.profiles.active=local`, `spring.mvc.throw-exception-if-no-handler-found=true`, `spring.web.resources.add-mappings=false` — essentially just profile activation ([M5] lives here) |
  | `src/main/resources/application-local.properties` | **16-97 lines** | the bulk: `server.*`, `spring.application.name`, `logging.config`, `spring.main.banner-mode`, jackson/general block, circuit breaker, the `application.http.restclient.<connector>.*` block, and the ACTUATOR block |
  Check with `wc -l` on both: if `application.properties` is the larger file, they are inverted. Inverting them **still runs locally**, so no build or test catches it — it silently diverges from every real repo. Where the ACTUATOR block appears in a packaged file at all (8/20 repos), it is **always** in `application-local.properties`. Caught on `cumulativeCopaySummaries`, where generation put all 32 lines in `application.properties` and left `application-local.properties` with 6. Independent of **[M15]**: the config-maps still need their own copy of the ACTUATOR keys, because `SPRING_CONFIG_LOCATION` means *neither* packaged file is read in a deployed pod.
- **[M17]** **[live-tested]** **Re-verify every dependency in a pom copied from a reference repo against YOUR generated code's imports** — not the reference's. Rule 2 is about the code you actually generated. A reference repo can carry a dead dependency *and* a dead import: `cumulativeCopaySummaries`' reference declared `commons-lang3` and had `import org.apache.commons.lang3.StringUtils;` with **zero call sites**. Generation correctly dropped the unused import but kept the pom entry — converting the reference's latent dead dependency into a live Rule 2 violation in new output.
  ```bash
  # per dependency: confirm at least one real import/call site exists in generated code
  grep -rn "org.apache.commons" <web-module>/src/main/java | grep -v '^\s*//'
  ```
  No import → remove it from the pom, even when the reference repo keeps it. Corollary: dropping an unused *import* without dropping the matching *dependency* is the half-fix that creates this gap — do both or neither.

- **[M18]** **[single-repo evidence, `RankedMatch` — N=1]** **If `application.circuitbreaker.*` properties are present anywhere in the service's config, the service layer must have a matching `isCircuitBreakerError()` branch** — otherwise the properties are dead config and an open breaker falls through to a generic 500 instead of the 408/timeout path. Mechanical check:
  ```bash
  if grep -rq "application.circuitbreaker\." config/ <web-module>/src/main/resources/ 2>/dev/null; then
    grep -rq "isCircuitBreakerError" <web-module>/src/main/java || echo "GAP: circuitbreaker properties present but no isCircuitBreakerError() branch found"
  fi
  ```
  Full real catch-block shape and the exact accessor name: `rest-external-direct.md`'s "Circuit breaker — the Java-side consumption pattern" and `common-patterns-and-gotchas.md` §2. Since this is N=1, the properties-side block is still safe to copy everywhere (confirmed 3-repo baseline), but don't assume every backend type wires the Java-side branch identically until a second real repo confirms it — flag as a GAP for human confirmation rather than silently asserting the pattern on an unconfirmed backend type.

- **[M19]** **[confirmed by real-sibling-repo diff, `EmergencyOverrides`, 2026-10-01]** **No Kong/gateway token-validator reimplementation anywhere** — see Rule 9. Mechanical check:
  ```bash
  grep -rli "tokenvalidator\|kongeieusercontext\|TokenValidat" <web-module>/src/main/java config/ 2>/dev/null
  ```
  Any hit is a GAP: delete the connector/service/exception/config and replace with an action-item note that Kong is expected to enforce it upstream. This applies even when the IIB ESQL genuinely has an ID-token-check module — the real sibling repo confirms it's intentionally not reimplemented, not an oversight to fix by adding more code.

- **[M20]** **[confirmed by real-sibling-repo diff, `EmergencyOverrides`, 2026-10-01]** **Controller contains no error-response calls or inline mapping/branching logic** — see Rule 9/10 and `structural-conventions.md` §9. Mechanical check:
  ```bash
  grep -n "ErrorUtil.errorResponse\|try {" <web-module>/src/main/java/*/controller/*.java
  ```
  Any hit is a GAP: that logic belongs in the `*Service`/`*ServiceUtil` layer. A controller method body should be one call to the service plus a `ResponseEntity` wrap — nothing else.

- **[M21]** **[decided 2026-10-01 against strong contrary evidence, standing rule — Rule 10]** **No custom `Exception` subclass and no `exception` package anywhere — including the `@RestControllerAdvice`-with-only-framework-exceptions form that is actually the dominant pattern in 11 real repos** (see Rule 10's evidence note). Don't treat a hit on this check as a false positive just because it matches common real code — it's a deliberate, twice-confirmed override. Mechanical check:
  ```bash
  find <web-module>/src/main/java -type d -iname "exception"
  grep -rn "extends RuntimeException\|extends Exception\b" <web-module>/src/main/java
  grep -rln "@RestControllerAdvice" <web-module>/src/main/java
  ```
  Any hit is a GAP — including a `@RestControllerAdvice` using only framework/Spring exception types for a generic fallback (e.g. 404). Replace with a direct `ErrorUtil.errorResponse(...)` call at the point of failure. Catching the **framework's own** `EIPMicroserviceException` to re-map/re-throw it (not define a new type) is fine and not a hit for this check.

- **[M22]** **[confirmed by real-sibling-repo diff, `EmergencyOverrides`, 2026-10-01, RxClaim SBO]** **Backend-only XML/JAXB models live in a top-level `model.xml` package, sibling to `swagger/` — never nested as `swagger.model.xml`.** Mechanical check:
  ```bash
  find <web-module>/src/main/java -type d -path "*/swagger/model/xml"
  ```
  Any hit is a GAP for a new RxClaim SBO (or similarly-shaped) migration — move the package up one level, out from under `swagger/model/`, so `swagger/model/` contains only `request/` and `response/`. (`memberidcardprofile`'s real repo nests it this way — that's the older shape being corrected, not a precedent to match for a new service.)

- **[M23]** **[Rule 12]** **`application-local.properties`'s internal-CVS-backend URI(s) target `qa`/`qa2`, never `dev`/`prod`.** Mechanical check:
  ```bash
  grep -n "\.uri=https://dev\|\.uri=https://prod" <web-module>/src/main/resources/application-local.properties
  ```
  Any hit is a GAP — swap the hostname prefix to `qa` (or `qa2`, matching whichever QA tier the service's own real sibling or IIB non-prod properties file uses). Does not apply to an external-vendor connector URI (MedForce/Precisely/Anthem/IngenioRx/etc.) — those have no CVS `qa` hostname and should instead match the vendor's own non-prod/staging endpoint from the IIB source.
- **[M23b]** **[Rule 12, host-family correction 2026-10-23]** **No internal-CVS-backend connector URI, in ANY environment file (including every config-map, not just `application-local.properties`), ever carries an `az`/`ri` suffix — always plain `pbm-oci`.** Mechanical check, run across every environment file at once:
  ```bash
  grep -rn "\.uri=https://[a-z0-9]*eippbm\(az\|ri\)-oci" <web-module>/src/main/resources/ <service-repo-root>/config/config-map/
  ```
  Any hit is a GAP for a new migration — strip the `az`/`ri` segment so the host reads `<env>eippbm-oci.cvshealth.com`. Applies to `ql`, `rxclaim`, `qlhc`, `ecshc`, and `refdata` alike — they're one shared host family, differentiated by path, not by host. Does not apply to the config-map **filename's** `<cluster>` suffix (`application.prod-pbmaz.properties` is still named that way — see `structural-conventions.md` §5) or to external-vendor connector hosts.
- **[M24]** **No `target/` folder left under the parent module after HANDOFF.** Mechanical check:
  ```bash
  [ -d <service>-oci-parent/target ] && echo "GAP: parent target/ not cleaned up"
  ```
  Any hit is a GAP — `rm -rf <service>-oci-parent/target` (`workflow-and-state.md` HANDOFF). The parent is a pure aggregator pom with no jar of its own, so its `target/` is empty build noise, not a deliverable. **Do not** apply this check to `<service>-oci-web/target/` — that one is expected to exist (it holds the actual built jar) and must be left alone.
- **[M25]** **HANDOFF-C's smoke test actually ran and has a recorded outcome — never silently skipped.** Mechanical check:
  ```bash
  grep -o '"status": *"[a-z_]*"' <target>/.iib2spring/state.json | grep -A0 smoke_test || true
  jq '.smoke_test.status' <target>/.iib2spring/state.json   # must be "passed" or "failed_blocking", never "pending" at HANDOFF-B
  ```
  `"pending"` at the point HANDOFF-B runs is a GAP — it means HANDOFF-C was skipped rather than run. `"failed_blocking"` is not itself a GAP to fix mechanically (it's already a reported BLOCKING item with its own remediation path from HANDOFF-C) — this check only catches the skip, not the failure itself.
- **[M26]** **A `NEW:<name>` backend (frameworks-repo fallback) is labelled and traceable.** If `state.json`'s `backend_type` starts with `NEW:`, the HANDOFF action items must name the framework module used, the branch and commit read, and state the result is single-source/unverified. Mechanical check:
  ```bash
  jq -r '.backend_type' <target>/.iib2spring/state.json | grep -q '^NEW:' && \
    git -C <frameworks-repo> rev-parse origin/<branch>   # the commit to cite; empty/failed = the repo was never actually read
  ```
  A `NEW:` run with no cited module/branch/commit is a GAP — it means the fallback was invoked in name only. Also a GAP: `framework-adapter` used as the backend module (`references/framework-repo-fallback.md` §4).
- **[M13]** **Every new migration with a real credential gets live Akeyless by default — never a half-done middle state, and never skipped by asking "is it needed."** Revised 2026-10-23 (Rule 6 v3 — supersedes the 2026-10-07 per-service-determination version): the only check left is whether this service has an actual credential to protect at all (client-id/secret/API-key/cert — confirmed from the IIB source or a real sibling repo, never by asking the user). If it genuinely has **none**: no `AKEYLESS_*` values keys, no `akeyless/*` `Deployment.yaml` annotations, no `spring.config.import=...application-akeyless.properties...` — there's nothing to wire. If it has **any** credential: the scaffolding must be live (`AKEYLESS_ENABLED: true`, annotations populated, `spring.config.import` present) using Form 1 (creds-only) or Form 2 (creds+certs) from `mandatory-migration-rules.md` Rule 6 — never the half-done `GetGrievanceByCriterias` shape (keys present, `AKEYLESS_ENABLED: false`, empty annotations block), and never deferred as a "confirm with the team first" question. Only the literal secret path value is a placeholder (Rule 6) — everything else is generated immediately. Mechanical check:
  ```bash
  grep -q "AKEYLESS_ENABLED" config/deploy-config/values.*.yaml && \
    grep -L "AKEYLESS_ENABLED: true" config/deploy-config/values.*.yaml
  ```
  Any file listed (meaning the key exists but isn't `true`) is a GAP — either remove the scaffolding entirely (if not required) or set it live (if required). Don't leave it present-but-disabled.

---

## REST-ONLY checks (skip for SOAP/QL_CICS/RXCLAIM_SBO/MQ_COBOL)

- **[R1]** Package-includes-service-name is inconsistent across training repos (confirmed split, not universal) — check sibling services in the same domain for convention; don't hard-fail on this alone.
- **[R2]** API interface exists at `swagger/api/{ServiceName}Api` with `@RequestMapping("${CONTEXT_ROOT:...}")`.
- **[R3]** `RestClientWrapper` used in the connector, never `RestClient`/`RestTemplate` directly in the service.
- **[R4]** A dedicated `{Backend}Connector` class exists; the service delegates all HTTP calls to it rather than calling `RestClientWrapper` inline.
- **[R5]** Backend URL/credentials configured via `@Value` directly on the connector (or a `*ConnectorUtil` helper) — the **default** for REST, confirmed 3/3 in training repos, correcting an earlier assumption that a dedicated `@ConfigurationProperties` class was the default. A dedicated properties class is an accepted alternative, not the primary pattern.
- **[R6]** "No record found" handling verified **per flow**, not assumed as a blanket rule: cross-repo evidence shows even a single repo's two endpoints can legitimately disagree (one wraps a 204-style body in HTTP 200, the sibling endpoint throws a real 404) — check the actual IIB behavior for that specific endpoint, don't apply one convention repo-wide.
- **[R7]** `substringBefore`/`substringAfter`-style helpers return `""` not `null` when the separator is absent — this is what makes a downstream blank-check catch all missing-segment cases correctly.
- **[R8]** `CONTEXT_ROOT` uses the property-overridable `${CONTEXT_ROOT:/.../cs/...}` form with `/cs/` (not `/edb/`) as the default — a hardcoded literal path with no property override is a confirmed real outlier in the training set, not the pattern to copy, though flag rather than auto-fix without checking that specific service's own convention.
- **[R9]** Parent pom version is `0.0.1-SNAPSHOT` for REST services, confirmed 3/3 in training repos — same convention as every other backend type, correcting an earlier assumption that REST specifically used `1.0.0-SNAPSHOT`.
- **[R10]** No Cucumber dependencies in a REST web pom.
- **[R11]** EIE headers (`Testharness`, `X-Apitransactionid`, `X-Global-Transaction-Id`, etc.) are never exposed as `@RequestHeader` controller/interface parameters — they come from the `EieHeader` bean only.
- **[R12]** No manual response-header setting for correlation headers in the controller — the framework handles this automatically.
- **[R13]** `CONTEXT_ROOT` contains no `/edb/` segment — the IIB `/edb/` path maps to the MS `/cs/` path.
- **[R14]** REST response (and request) models are Java 21 **records**, not Lombok classes — confirmed 3/3 in training repos, correcting an earlier assumption that REST models specifically should use Lombok. Lombok is still fine for `@RequiredArgsConstructor` on services/connectors — just never on a model class.
- **[R15]** Service methods take only path/query params, never a `testHarness`/`apiTransactionId`-style header parameter directly.
- **[R16]** Neither `logback-spring.xml` nor explicit `logging.level.*` properties are expected for REST — their absence is normal (logging is inherited from the shared `EIPMicroserviceLogger` platform module), confirmed 3/3 in training repos.

---

## MQCOBOL-ONLY checks (skip for every other backend type)

- **[MQ1]** Correct connector family: grep the connector for `framework-mqclient`/`MQUtility` → must exist; grep the pom for `framework-connector-qlcics` → must **not** appear (that's a different connector family entirely, not interchangeable with MQ_COBOL).
- **[MQ2]** No Bindy anywhere: grep `model/`/`util/` for `@FixedLengthRecord`/`BindyFixedLengthDataFormat` → must **not** appear. This backend type is manual EBCDIC offset writes, not annotated records.
- **[MQ3]** Byte offsets verified against the actual COBOL copybook, not guessed — check the request/response buffer builder's section-length comments against the `.cbl`/`.cpy` source directly.

- **[M27]** **[Rule 14]** **`settings.xml` is at the repo root, byte-identical to `references/templates/maven-settings.xml`.** Mechanical check:
  ```bash
  [ -f settings.xml ] || echo "GAP: settings.xml missing at the repo root"
  cmp -s settings.xml "<skill-dir>/references/templates/maven-settings.xml" || echo "GAP: settings.xml differs from the template"
  ```
  Any hit is a GAP — copy the template to the repo root. Every Maven command for the repository must also pass `-s <repo-root>/settings.xml` (`mandatory-migration-rules.md` Rule 14); the internal artifacts resolve nowhere else.

- **[M28]** **[Rule 15]** **No generated properties file defers a value to an OS environment variable.** Mechanical check, every packaged and config-map properties file:
  ```bash
  grep -nE '\$\{[A-Z_][A-Z0-9_]*\}' application.properties application-local.properties config/config-map/*.properties
  ```
  Spring's own `${...}` property-reference syntax (one property pointing at another already defined in the same file, or at `spring.config.import`) is not what this flags — only a reference with no corresponding `key=` anywhere in the file, which means it is silently expecting an OS/container environment variable to supply it. Any hit is a GAP — replace it with a literal, clearly-marked placeholder (`<VALUE_TBD_BY_TEAM>` or similarly named) and list it as an action item.

---

## Applying this checklist

1. After the service layer compiles, run every item in **BLOCKING** and **HIGH** for the detected backend type, plus the relevant **REST-ONLY** or **MQCOBOL-ONLY** section if applicable.
2. Run **MEDIUM** once the deployment artifacts (Dockerfile, Helm, config-map, `.github/`) are generated.
3. Auto-fix what's unambiguous; for anything requiring a judgment call (byte-length mismatches, ambiguous field mappings), regenerate and recompile rather than patching around it.
4. Loop up to 3 times; report anything still open as a named GAP with who needs to confirm it (usually "QL team" for buffer-layout questions, "infra team" for queue-name/deployment questions, or the service owner for a plaintext credential value still needing encryption). Akeyless provisioning itself is a future action item for Phase 2 services, not a current gap — see `mandatory-migration-rules.md` Rule 6.
