<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- SKILL.md lines 175–217. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->


## 7. Verification — run before every checkpoint

### 7.1 Mandatory-rules checklist (`references/mandatory-migration-rules.md`)

- [ ] Every `.builder()` call on a framework model class verified to actually exist on that class — not assumed by analogy with a sibling class
- [ ] pom.xml has no dependency not directly imported by the generated code — but does NOT omit `framework-restclient` (needed regardless of backend type — it's the source of `EieHeader`/`ErrorUtil`/`ErrorStatus`)
- [ ] Dockerfile at repo root matches the exact template in `structural-conventions.md` §3 (base image, all 4 LABELs, non-root user, JVM flags — not just the maintainer label)
- [ ] No `RestTemplate` anywhere, including test-harness bypass paths, including QL/CICS
- [ ] No `StringBuilder`/manual string concat for QL buffer field content, request **or** response side — Camel Bindy throughout
- [ ] Whenever this service has any credential, live Akeyless scaffolding is present (not a disabled placeholder, and not something the migration merely asked the user about) — `application-local.properties` still carries the CVS-encrypt-endpoint ciphertext, since Akeyless has no sidecar locally; every deployed config-map carries the credential through Akeyless alone, never also as ciphertext (Rule 6)
- [ ] Framework/Java version was **explicitly asked at INTAKE and the user's actual answer was captured** (Rule 7) — not silently defaulted to 3.2.2/21 without asking, and never inferred from sibling repos. Checked both in the toolchain (`java -version`) and in the parent pom's `<java.version>` property
- [ ] HANDOFF-C's smoke test ran using the request/expected-response collected at INTAKE, passed (noting which attempt) or was reported as a named BLOCKING gap after 3 attempts — never silently skipped or silently passed
- [ ] `EieHeader` used only via confirmed real accessors; `framework-eieusercontext` included only if the target flow's category/service genuinely needs to populate the context from an ID token (checked, not assumed); no JWT/JWKS validation anywhere
- [ ] No Kong/gateway token-validator connector, service, exception, or config-map block generated for an ESQL ID-token-check node, even though the ESQL genuinely has one — Kong handles it upstream (Rule 9)
- [ ] No custom `Exception` subclass and no `exception` package anywhere — not even a `@RestControllerAdvice` fallback handler; every error path calls `ErrorUtil.errorResponse(...)` directly (Rule 10)
- [ ] Every generated JUnit test class is an empty skeleton — no `@Test` methods, assertions, or mocks; `mvn test -q` passes with 0 tests run (Rule 11)
- [ ] Every internal-CVS-backend connector URI, in every environment file (not just the local profile), uses the plain `<env>eippbm-oci.cvshealth.com` host — never an `az`/`ri` suffix (Rule 12)
- [ ] `application-local.properties`'s internal-CVS-backend connector URI(s) point at the `qa`/`qa2` tier, never `dev`/`prod`; external-vendor connectors use that vendor's own non-prod endpoint instead (Rule 12)

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
- Any internal-CVS-backend connector URI, in any environment file, carrying an `az`/`ri` suffix instead of plain `pbm-oci` **[M23b]**
- A leftover `target/` folder under the parent module at HANDOFF **[M24]**
- HANDOFF-C's smoke test left at `"pending"` instead of `"passed"`/`"failed_blocking"` **[M25]**
- A `NEW:<name>` (frameworks-repo fallback) run that doesn't cite the module, branch, and commit it actually read, or that uses `framework-adapter` **[M26]**

### 7.3 Structural-conventions checklist (`references/structural-conventions.md` §12)

`.github/` workflows; Helm chart; full environment property list; **config-map self-containment (every packaged-`application.properties` key present in all ten config-maps — [M15])**; **baseline block present byte-for-byte in the packaged file AND all ten config-maps, checked against the physical `references/templates/config-map-baseline.properties`, not from memory (HANDOFF-A0)**; concern-split constants; dedicated validator/utility classes (or, for RxClaim SBO, the thin-`Service`-plus-`*ServiceUtil`-under-`util/` pattern — see `rxclaim-sbo-xml-jaxb.md` §8); records with full annotations (where records are the convention — not RxClaim SBO, see `rxclaim-sbo-xml-jaxb.md` §0); framework `ErrorStatus`; `@EIPMicroserviceOperation` + concrete return types; `@Validated`; `@Data`-only Bindy models; contiguous wire segments as one Bindy class; controller delegates to the service only, no inline error-response calls or request/response mapping logic.
