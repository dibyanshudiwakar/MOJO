# Mandatory Migration Rules

These rules are **non-negotiable** and override any pattern shown elsewhere in this skill's reference files if they conflict. Several training-service examples (cited below) actually violate one or more of these rules — treat those specific spots as "do not copy" even though they came from real, deployed code.

Apply this checklist on every migration, before presenting generated code.

---

## Rule 1 — Builder pattern for `RestClientWrapper` calls

Never construct `RestClientRequest` with a long sequence of setter calls. Wrap construction in a builder chain (Lombok `@Builder` on the request object if the framework class supports it, or a local fluent builder wrapping it if it doesn't).

```java
// ❌ AVOID (setter-chain style seen in several training services, e.g. GetGrievanceByCriteriasUtil):
RestClientRequest request = new RestClientRequest();
request.setClientName(clientName);
request.setMethodType(RequestMethod.POST);
request.setPayload(payload);
request.setHeaders(headers);

// ✅ REQUIRED — builder pattern:
RestClientRequest request = RestClientRequest.builder()
    .clientName(clientName)
    .methodType(RequestMethod.POST)
    .payload(payload)
    .headers(headers)
    .build();
```

If `RestClientRequest` (the framework class) does not expose a `@Builder`/`builder()` method in the version pulled in, wrap it in a local builder helper class within the connector package rather than reverting to setters:

```java
public final class RestClientRequestBuilder {
    private final RestClientRequest request = new RestClientRequest();
    public RestClientRequestBuilder clientName(String v) { request.setClientName(v); return this; }
    public RestClientRequestBuilder methodType(RequestMethod v) { request.setMethodType(v); return this; }
    public RestClientRequestBuilder payload(Object v) { request.setPayload(v); return this; }
    public RestClientRequestBuilder headers(Map<String,String> v) { request.setHeaders(v); return this; }
    public RestClientRequest build() { return request; }
}
```

Same applies to `RxClaimRequest` construction in the RxClaim SBO group — use builder-style chaining wherever the framework model supports it.

⚠️ **Confirmed exception: `QLRequest` does NOT have a builder.** `com.cvshealth.eip.microservices.frameworks.connector.qlcics.model.QLRequest` was compiled against the real framework jar and does **not** expose a `.builder()` method — only plain setters (`setMessageBody(...)`, etc.). This is the opposite situation from Rule 8's `EieHeader` finding, but the lesson is the same: **verify against the actual framework class before assuming `.builder()` exists**, framework class by framework class — do not assume one framework model's construction style generalizes to another in the same connector group. For `QLRequest` specifically:

```java
// ✅ REQUIRED for QLRequest — setters, not a fabricated builder:
QLRequest qlRequest = new QLRequest();
qlRequest.setMessageBody(messageBody);
```

If a compile error reports `cannot find symbol: method builder()` against any framework model class, that is the framework telling you it doesn't support the pattern — revert that specific class to setters rather than trying to force a builder wrapper on a class you don't own the source of (the local-wrapper escape hatch above is for when you control the class; it does not apply to a `frameworks.*` model class shipped in a jar).

---

## Rule 2 — Trim pom.xml to only what's actually used

Before finalizing, review both `*-parent/pom.xml` and `*-web/pom.xml` and remove any dependency not exercised by the generated code. Common offenders seen across training services:

- `camel-bindy` / `camel-spring-boot-starter` — only needed for **QL/CICS** connector group. Never include for RxClaim SBO, IBM MQ, or REST External services.
- `framework-connector-qlcics` — only for QL/CICS.
- `framework-connector-rxclaimsbo` — only for RxClaim SBO.
- `framework-mqclient` — only for IBM MQ + COBOL Copybook.
- `framework-oauth` — only if the service actually does OAuth2 (not every REST External service does — EOB-ExplanationOfBenefit has none).
- `framework-connector-db` / `framework-connector-refdata` / `framework-cache` — only if the service actually performs a DB lookup, reference-data lookup, or caching (do not add "just in case").
- `jackson-databind-nullable`, `swagger-annotations`, `springdoc-openapi-starter-webmvc-ui` — standard for every service (keep).
- Test-only dependencies (`spring-boot-starter-test`, `junit-jupiter-api`) — `<scope>test</scope>` only, never main scope.

Checklist before finalizing the pom:
1. For every `<dependency>` block, confirm at least one generated class actually imports a class from that artifact.
2. Remove commented-out dependency blocks entirely — don't leave dead XML.
3. Do not carry over CVE-remediation version pins from a template service unless the same CVE applies to the new service's dependency tree.

---

## Rule 3 — Dockerfile: maintainer label AND full structural template

Every generated/updated `Dockerfile` must include:

```dockerfile
LABEL maintainer='IntegrationEngineeringTeam@CVSHealth.com'
```

This is necessary but **not sufficient on its own** — see `references/structural-conventions.md` §2 for the complete confirmed template (base image, all 4 required `LABEL` lines, non-root user setup, fixed JVM flags, and the requirement that the Dockerfile lives at the **repo root**, not inside the web module folder). Load that file before generating or modifying any Dockerfile. If an existing Dockerfile is being modified rather than created fresh, check for and correct any stale/incorrect maintainer value rather than adding a duplicate label.

⚠️ **Confirmed correct, don't change based on a single outlier.** `IntegrationEngineeringTeam@CVSHealth.com` is confirmed correct (user-confirmed 2026-09-29) across `ValidateBridgeSupply` and `RankedMatch`'s real Dockerfiles. `GetGrievanceSummary`'s real Dockerfile uses `ESDTeamMembers@cvscaremark.com` instead — that's a legacy/team-specific outlier for that one repo (matches the `com.cvscm.esd.common.util.ESDExceptionListUtil` broker-exception helper its ESQL calls, i.e. that domain's own long-standing team address), not evidence this rule should change. Always use `IntegrationEngineeringTeam@CVSHealth.com` unless the user explicitly says otherwise for a specific service.

---

## Rule 4 — Never use `RestTemplate`; always use `RestClientWrapper` / `RestClientService`

Several training services contain a `RestTemplate`-based fallback path (typically for the WireMock test-harness bypass) — **this is a pattern violation, not something to copy.** Known violating spots:
- `QLMessagingService.callTestHarnessForResult()` (beneficiarystatusservice / findallergiesanddiagnosis pattern) — uses `restTemplate.postForEntity(...)` directly.
- `PrescriptionRetrievalProcessor.callTestHarness()` (prescriptionstatusservice pattern) — same issue.

When generating test-harness bypass logic, route it through `RestClientWrapper`/`RestClientService` exactly like the production call path — do not introduce a second HTTP client:

```java
// ❌ AVOID:
ResponseEntity<String> response = restTemplate.postForEntity(testHarnessUri, body, String.class);

// ✅ REQUIRED:
RestClientRequest testRequest = RestClientRequest.builder()
    .clientName(clientName)
    .methodType(RequestMethod.POST)
    .url(testHarnessUri)
    .payload(body)
    .build();
ResponseEntity<String> response = restClientWrapper.executeRestCall(testRequest, String.class);
```

If a training-service reference file in this skill shows a `RestTemplate` example, treat that example as illustrative of the *old, incorrect* pattern only — always generate the `RestClientWrapper` version instead.

---

## Rule 5 — Never hand-build fixed-width buffers with `StringBuilder`; use Camel Bindy for QL backends

For any **QL/CICS backend** connector, buffer construction and parsing must go through Camel Bindy `@FixedLengthRecord`/`@DataField` models and the `BindyFixedLengthDataFormat` marshaller/unmarshaller — not manual `StringBuilder.append(...)` concatenation or raw `String.format`/`substring` offset math.

Known deviations in the training set that must NOT be copied as-is:
- `NameRequestBufferBuilder` (BenService-GetBeneficiaries MT28 buffer) — built manually via string concatenation because the IIB source used MRM/CWF (not DFDL). **When migrating, convert this to a Bindy `@FixedLengthRecord` model even if the IIB side used MRM/CWF** — the IIB transport format is not a justification for skipping Bindy on the Java side.
- `NameResponseParser` (MT27 TDS-tagged reply) — used `FixedWidthBufferUtil.extractField()` manual offset extraction instead of Bindy unmarshalling.
- `QLRequestBufferBuilder`/`FixedWidthBufferUtil` classes across several services that hand-assemble a full request via `StringBuilder` before Bindy-marshalling only the per-record pieces.

Required pattern:

```java
// ❌ AVOID:
StringBuilder buffer = new StringBuilder();
buffer.append(padAlpha(field1, 8));
buffer.append(padNumeric(field2, 4));
// ...manual concatenation of every field

// ✅ REQUIRED — define the record and let Bindy marshal it:
@FixedLengthRecord(length = 27, paddingChar = ' ')
public class RequestHeader {
    @DataField(pos = 1, length = 4, align = "R", paddingChar = '0') private String bufTypId;
    @DataField(pos = 5, length = 8, align = "L", paddingChar = ' ') private String usrId;
    // ...
}

// Marshal via the Camel Bindy utility, not string concatenation:
String buffer = camelBindyUtility.marshaller(requestHeader, RequestHeader.class, DIRECT_ROUTE);
```

Where a repeating/tagged section genuinely cannot be expressed as a single fixed-count Bindy record (e.g. a variable-length TDS-tagged group), still marshal/unmarshal **each individual fixed-length record** via Bindy, and use plain Java only for the *loop/scan* logic that locates record boundaries — never for building the field content itself.

This rule does not apply to the **IBM MQ + COBOL Copybook** group (fepclaimdetail pattern), where EBCDIC byte-offset construction is the established, correct approach for that transport — Bindy is a QL/CICS-specific requirement.

---

## Rule 6 — Credential handling: whenever a credential exists, wire it through live Akeyless by default — every new migration, regardless of phase

> **Revised 2026-10-23, superseding the 2026-10-07 per-service-determination version.** History of this rule: v1 (2026-09-28–10-01) said never generate Akeyless at all, treat it as a separate later step. v2 (2026-10-07) said generate it only where a per-service check determined it was "genuinely required" — confirmed against a real sibling repo, or by asking the user/team. **v3, now: stop determining it per-service and stop asking. Every new migration that has an actual credential to protect generates live Akeyless scaffolding by default — full stop.** This is a deliberate reversal of v2's "ask the user" clause specifically, because that clause is what was producing a repeated INTAKE-time question during testing ("should this follow Phase 1 or Phase 2 for Akeyless?") that has exactly one correct answer now and never needs re-asking.
>
> **Why phase is not the test, and never was the right question to ask about:** Akeyless integration did not exist as CVS infrastructure at the time the original Phase 1 services were built — that's *why* their real IIB/MS sibling repos show no Akeyless anywhere (a historical fact about those old repos, not a convention to replicate). It is not a Phase-1-vs-Phase-2 rule; it is a *when-was-this-generated* rule. **Every migration happening now generates live Akeyless, regardless of whether the service itself carries a "Phase 1" or "Phase 2" label** — the label describes which cohort the service's business logic belongs to, not which credential-handling era its migration output should follow.
>
> **Confirmed real mechanics, both pieces present together (`DMRClaimStatus`, live-verified 2026-10-23):** Akeyless replaces the credential in every *deployed* environment's config-map — `grep client-id config/config-map/application.qa-pbmaz.properties` returns nothing at all, not even ciphertext; the value arrives exclusively via the Akeyless-injected `application-akeyless.properties` file pulled in by `spring.config.import`. The CVS-encrypt-endpoint ciphertext approach below still applies, but now only to `application-local.properties` — Akeyless has no sidecar locally, so the local profile is the one place a credential still needs the encrypted-value-in-properties form.
>
> **The only placeholder left is the literal secret path value** — see "If you don't yet know the secret path" below. Generate everything else immediately; never pause to ask the user.
>
> **If the service genuinely has no credential to manage at all** (several real QL/CICS and RxClaim SBO services call an internal CVS gateway with no client-id/secret/API-key/cert property anywhere — confirmed real examples), there's nothing for Akeyless or the CVS-encrypt-endpoint to protect. Confirm this from the IIB source / a real sibling repo's actual properties — not by asking the user — and skip credential handling entirely rather than inventing a secret.

### `application-local.properties` only — encrypt via the CVS endpoint

The local profile has no Akeyless sidecar, so it still needs the credential as an encrypted value directly in the properties file (confirmed real on `DMRClaimStatus`'s `application-local.properties`, which carries `application.medforce.client-id`/`client-secret` as ciphertext even though every deployed config-map omits them entirely). Every deployed environment's config-map, by contrast, gets the credential exclusively through the live Akeyless scaffolding below — **don't also put an encrypted value in the config-map once Akeyless is wired in; that's the half-done middle state this rule is trying to eliminate.**

1. Take the plaintext client ID / password / secret value.
2. Encrypt it via the CVS encryption service:

```bash
curl --request POST \
  --url https://dev2eippbmaz-oci.cvshealth.com/pbmtechnical/cs/v1/edb/encrypt/logdata \
  --header 'content-type: application/json' \
  --data '{
  "encryptedData": "<PLAINTEXT_VALUE_HERE>"
}'
```

3. Put the **returned encrypted value** into the properties file under the corresponding key:

```properties
application.<service>.client-id=<ENCRYPTED_VALUE_RETURNED_BY_ENDPOINT>
application.<service>.client-secret=<ENCRYPTED_VALUE_RETURNED_BY_ENDPOINT>
application.<service>.password=<ENCRYPTED_VALUE_RETURNED_BY_ENDPOINT>
```

4. Confirm the framework's config-loading path decrypts this automatically (the codebase already uses `AESEncryption`/similar framework helpers in several training services — e.g. `EncryptionUtil` in orderimageinfoservice) — do not write custom decryption logic; rely on the existing framework decryption mechanism that reads these encrypted properties.

**Never commit plaintext credentials to any properties file, for either phase.** If the actual credential value is not provided by the user, use a clearly-marked placeholder (e.g. `<REPLACE_WITH_ENCRYPTED_VALUE>`) and flag it in the final output as an action item — do not fabricate a value.

### Every new migration with a real credential — generate live Akeyless, using the real syntax, not a disabled placeholder

Confirmed by direct re-investigation of the 5 real repos with any Akeyless/SSL-cert footprint (2026-10-07) — 3 of the 5 are genuinely live, enabled Akeyless consumers; the other 2 are **historical artifacts from before this rule's v3 "always" default, not examples to default a new migration toward**:

| Repo | Shape | Live? |
|---|---|---|
| `GetGrievancesAppeal` | creds-only | ✅ `AKEYLESS_ENABLED: true` — template for Form 1 below |
| `rankedMatch` | creds-only | ✅ `AKEYLESS_ENABLED: true` — second Form 1 confirmation |
| `DMRClaimStatus` | creds **+ certs** (vendor uses a private TLS cert) | ✅ `AKEYLESS_ENABLED: true` — template for Form 2 below |
| `GetGrievanceByCriterias` | values.yaml keys present, but **`AKEYLESS_ENABLED: false`** and `Deployment.yaml`'s annotations block is empty — scaffolded, never actually wired live | ❌ never generate this half-done shape, for any new migration |
| `RET-Pharmacy-Checkinimzprescriptions` | **no Akeyless anywhere** — uses a plain classpath SSL cert instead | ❌ a pre-v3 repo that predates the "always" default — don't let this justify skipping Akeyless on a new migration that has a real credential |

**Form 1 — creds-only** (no vendor TLS cert needed), verbatim from `GetGrievancesAppeal`'s real repo:

`Deployment.yaml` pod-template annotations:
```yaml
      annotations:
        akeyless/enabled: "{{ .Values.AKEYLESS_ENABLED }}"
        akeyless/inject_file_creds: {{ .Values.AKEYLESS_INJECT_FILE_CREDS | join "," | quote }}
```

`values.<env>.yaml`:
```yaml
AKEYLESS_ENABLED: true
AKEYLESS_INJECT_FILE_CREDS:
  - "/cvs/eip/ocp-apm0005337/secrets/static/cvs-v1-<service>-oci-<env>|location=/os/spring-boot-cvs-v1-<service>-oci/creds/application-akeyless.properties"
  - "/cvs/eip/ocp-apm0005337/secrets/static/aes_config.properties|location=/os/spring-boot-cvs-v1-<service>-oci/creds/application-aes_config.properties"
```

Config-map properties (every env):
```properties
spring.config.import=optional:/os/spring-boot-cvs-v1-<service>-oci/creds/application-akeyless.properties,optional:/os/spring-boot-cvs-v1-<service>-oci/creds/application-aes_config.properties
```
`application-local.properties` has **no** `spring.config.import` line at all in this form — the local profile doesn't go through Akeyless (confirmed: neither `GetGrievancesAppeal` nor `rankedMatch` has one locally).

**Form 2 — creds + certs** (vendor backend uses a private/internal TLS certificate — same PKIX-failure trigger documented below), verbatim from `DMRClaimStatus`'s real repo:

`Deployment.yaml` pod-template annotations (all three keys):
```yaml
      annotations:
        akeyless/enabled: "{{ .Values.AKEYLESS_ENABLED }}"
        akeyless/inject_file_creds: {{ .Values.AKEYLESS_INJECT_FILE_CREDS | join "," | quote }}
        akeyless/inject_file_certs: {{ .Values.AKEYLESS_INJECT_FILE_CERTS | join "," | quote }}
```

`values.<env>.yaml`:
```yaml
AKEYLESS_ENABLED: true
AKEYLESS_INJECT_FILE_CREDS:
  - "/cvs/eip/ocp-apm0005337/secrets/static/cvs-v1-<service>-oci-<env>|location=/os/spring-boot-cvs-v1-<service>-oci/creds/application-akeyless.properties"
  - "/cvs/eip/ocp-apm0005337/secrets/static/aes_config.properties|location=/os/spring-boot-cvs-v1-<service>-oci/creds/application-aes_config.properties"
AKEYLESS_INJECT_FILE_CERTS:
  - "/cvs/eip/ocp-apm0005337/secrets/static/<vendor>-truststore.crt.pem|location=/os/spring-boot-cvs-v1-<service>-oci/certs/<vendor>-truststore.crt.pem|jq=.certificate_pem"
```

Config-map properties (every env — note `spring.config.import` is **required**, not `optional:`, in this form):
```properties
spring.config.import=/os/spring-boot-cvs-v1-<service>-oci/creds/application-akeyless.properties,optional:/os/spring-boot-cvs-v1-<service>-oci/creds/application-aes_config.properties
application.http.restclient.<vendor>.custom-ssl-trust-store-enabled=true
spring.ssl.bundle.pem.<vendor>.truststore.certificate=/os/spring-boot-cvs-v1-<service>-oci/certs/<vendor>-truststore.crt.pem
```

`application-local.properties` (certs form only — the key property is still `custom-ssl-trust-store-enabled`, kebab-case under `application.http.restclient.<vendor>.*`, and the cert path becomes a local `classpath:` reference instead of the `/os/...` injected path):
```properties
application.http.restclient.<vendor>.custom-ssl-trust-store-enabled=true
spring.ssl.bundle.pem.<vendor>.truststore.certificate=classpath:<vendor>-truststore.crt.pem
```

**Choosing between the two forms is a technical check, not a question to ask the user:** use Form 2 (creds+certs) only when the vendor backend is confirmed to be on a private/internal TLS certificate (the same condition that triggers the PKIX failure below) — check the IIB source / a real sibling repo for that signal. Otherwise use Form 1 (creds-only); `GetGrievancesAppeal` and `rankedMatch` both confirm Form 1 alone is the real, correct, complete pattern for a vendor that doesn't need a custom trust store. **If genuinely ambiguous, default to Form 1** (the simpler, more common shape) and flag "confirm whether a custom TLS trust store is needed" as an action item — don't block generation on asking.

**Never generate the half-done `GetGrievanceByCriterias` shape** (values keys present but `AKEYLESS_ENABLED: false`, empty `Deployment.yaml` annotations) as if it were a legitimate target state — that pattern exists in one real repo but isn't something to replicate. Every new migration with a real credential wires Akeyless live (`true`), never scaffolded-and-off.

**⚠️ [live-proven, DMRClaimStatus → MedForce, 2026-09-30] If Form 2's SSL-bundle properties are omitted when the vendor actually needs them**, every backend call fails with `PKIX path building failed: unable to find valid certification path to requested target`. The service starts cleanly, passes health checks, and correctly returns 400s for bad input — but every real backend call 500s until the cert config is in place. This is exactly why Form 2 needs to be generated live when applicable, not deferred.

**You will almost never know the real secret path at migration time — that's expected, and it's the one, only placeholder.** Use a clearly-marked placeholder (e.g. `<AKEYLESS_SECRET_PATH_TBD_BY_TEAM>`) in `AKEYLESS_INJECT_FILE_CREDS`/`AKEYLESS_INJECT_FILE_CERTS`, generate the full surrounding structure (annotations, `AKEYLESS_ENABLED: true`, `spring.config.import`) immediately, and flag the real path as an action item for the Akeyless/platform team to provision and confirm. **Never pause the migration or ask the user for this value — placeholder it and move on**, the same way a not-yet-known credential value gets `<REPLACE_WITH_ENCRYPTED_VALUE>` above.

---

## Rule 7 — Always ask the user for the framework version explicitly; suggest 3.2.2/Java 21 as the default answer

> **Standing instruction from the user, framework version 2026-10-01, Java version 2026-10-09, sharpened to "always ask" 2026-10-09.** Don't inspect workspace repos to infer a version. **Don't silently default past this question either** — this is one of the 5 mandatory inputs INTAKE must get an explicit answer to before SCAFFOLD begins (see `workflow-and-state.md` Batch A). Ask directly, suggest **`framework-starter` 3.2.2** and **Java 21** as the recommended default when asking, and wait for the user's actual answer — even if that answer is just "yes, use the default."

```xml
<parent>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-starter</artifactId>
    <version>3.2.2</version>  <!-- default; override only if the user specifies a different version -->
</parent>
<properties>
    <java.version>21</java.version>  <!-- default; this is a hard toolchain requirement, not just a style pick -->
</properties>
```

**Java 21 is checked twice, not just declared once:** (1) the toolchain itself — `java -version` must report "21" before INTAKE's pre-flight table can pass (`workflow-and-state.md` Batch C/D) — and (2) the generated parent pom's `<java.version>` property must say `21` (`structural-conventions.md`'s structural checklist). A pom that compiles locally against a different installed JDK but still declares `21` is the failure mode this double-check exists to catch.

Always state which versions were used and why (`default 3.2.2` / `default Java 21` vs. `user-specified: x.y.z`) in the migration output — don't silently default without saying so. Observed `framework-starter` versions across training services range from `3.1.3` to `3.2.2`, which is part of why `3.2.2` (the highest confirmed version, and the one most live-testing in this skill was done against) is the sensible default rather than an arbitrary pick.

---

## Rule 8 — `EieHeader` accessors must be verified per-class; `framework-eieusercontext` need is category-dependent, not universally forbidden

> **Correction, re-verified against 3 repos across 2 connector categories after this rule was first written.** An earlier version of this rule said "never use `framework-eieusercontext`" as a blanket ban, generalized from a single live-tested QL/CICS service (`ValidateBridgeSupply`) where the EIE context happened to arrive pre-populated. That generalization was wrong — **RxClaim SBO (`carriersearchservice`, `memberidcardprofile`) and MQ_COBOL (`fepclaimdetail`) all genuinely depend on `framework-eieusercontext`**, confirmed via direct pom.xml and source inspection, not just a training-agent's claim. Only the one QL/CICS service checked didn't need it. This is exactly the mistake this skill's own lessons elsewhere warn about: a single-repo-derived absolute rule that doesn't survive a second check. Treat the corrected version below as the current rule.

### What is actually confirmed never to be needed, across every category checked so far

**JWT/JWKS validation** (fetching a JWKS endpoint to independently, cryptographically validate a token's signature) to extract a requester ID or any other EIE field — **zero training repos, across QL_CICS, RxClaim SBO, or MQ_COBOL, do this.** If a migration plan reaches for JWKS validation specifically for this purpose, stop and check — there's no precedent for it being necessary anywhere in the training set. This part of the original rule stands.

### What is category- (and sometimes service-) dependent — verify, don't assume either way

Whether `framework-eieusercontext`'s `EieHeaderBuilder.createEieUserContextString(idToken)` is needed depends on whether `EieHeader.getEieUserContext()` arrives **already populated** for that specific service, or needs to be **built** from a raw ID token first:

```java
// Confirmed pattern in carriersearchservice, memberidcardprofile (RxClaim SBO)
// and fepclaimdetail (MQ_COBOL) — framework-eieusercontext IS a real dependency here:
import com.cvshealth.eip.microservices.frameworks.eieusercontext.eieHeader.EieHeaderBuilder;

private final EieHeaderBuilder eieHeaderBuilder;   // requires framework-eieusercontext

public void populateUserContext() {
    if (eieHeader != null && StringUtils.isNotBlank(eieHeader.getIdToken()) && eieHeaderBuilder != null) {
        eieHeader.setEieUserContext(eieHeaderBuilder.createEieUserContextString(eieHeader.getIdToken()));
    }
}
```

```java
// Confirmed pattern in ValidateBridgeSupply (QL_CICS, live-tested) — no framework-eieusercontext needed:
// eieHeader.getEieUserContext() is already populated by the time this service sees it,
// so there is nothing to build — EieHeader's own getters (from framework-restclient) suffice.
```

**How to decide for a new migration**: check whether the target flow's IIB ESQL reads `X-Eieusercontext` directly off the inbound HTTP header (→ likely already-populated, `framework-eieusercontext` probably not needed) versus builds it from an ID token via some equivalent mechanism (→ `framework-eieusercontext`/`EieHeaderBuilder` likely needed). When in doubt, check the sibling reference project for the same connector category rather than defaulting either way — this rule differs by category and even by service within RxClaim SBO (see `rxclaim-sbo-xml-jaxb.md` §0 for the requester-ID-resolution nuance, which is a related but distinct question from whether `framework-eieusercontext` is a dependency at all).

### `EieHeader` accessors — verify per framework version, don't assume convenience getters exist

Whether or not `framework-eieusercontext` is also needed, `EieHeader` itself (from `framework-restclient`) does **not** expose `getAccountIdentifierValue()` or `getApplicationShortName()` as direct methods — those were incorrect assumptions in an earlier version of this rule. The **confirmed real accessors**, verified by compiling against a deployed service's actual framework jar, are:

```java
eieHeader.getEieUserContext()     // raw JSON string — accountIdentifierValue must be parsed out manually if needed
eieHeader.getApiTransactionId()   // transaction ID, when present
eieHeader.getEieCorrelationId()   // fallback correlation ID
eieHeader.getTestHarnessMode()    // "P" = production test-harness mode
eieHeader.getIdToken()            // raw ID token, if framework-eieusercontext is needed to build the context from it
eieHeader.getEieApplication()     // raw application-context JSON string (separate from getEieUserContext())
eieHeader.setTestHarnessMode(String)  // confirmed real setter, not just the getter — a real REST
                                       // service uses it to clear/restore test-harness mode around
                                       // one specific outbound call (rest-external-direct.md §0d)
```

Manual parsing of fields like `accountIdentifierValue` out of the raw `getEieUserContext()` string, or `applicationShortName` out of `getEieApplication()` (mirroring IIB's own `SUBSTRING(... AFTER ... BEFORE ...)` pattern) belongs in a dedicated `*Utility`/`*Extractor` component — see `references/structural-conventions.md` §10 for the QL_CICS-specific pattern and marker/terminator constants, and `rxclaim-sbo-xml-jaxb.md` §0 for why the same field might instead be passed straight through untouched depending on the service. Before writing any `eieHeader.getXxx()` call, confirm that method actually exists in the framework version this repo pins — never invent a getter name by analogy with what a field is called in the IIB header JSON, and never assume a pattern confirmed for one connector category holds for another without checking.

---

## Rule 9 — Never reimplement Kong/API-gateway token validation in the microservice

> **Confirmed by direct real-sibling-repo diff, `EmergencyOverrides`, 2026-10-01.** The IIB source for this service has a genuine `getEmergencyOverrides_IDTokenCheck` compute module that calls an HTTP request node to validate an `Id_token` header — so translating it isn't fabrication, it's a faithful reading of the ESQL. But the **real deployed MS sibling for this exact service implements none of it**: no connector, no service, no exception class, no config-map entry. Kong (the API gateway sitting in front of every microservice) already performs this validation before the request reaches the service — re-implementing it in Java duplicates work the gateway already does and introduces a second, divergent code path for something that isn't this service's responsibility.

**Never generate, regardless of what the ESQL source shows:**
- A connector class calling a `kongeieusercontext`/`jwt/tokenvalidator`-style endpoint (or any endpoint whose path/host suggests it's a Kong-internal validation call)
- A `*TokenValidationService`/`*TokenValidator*` service class
- A custom exception for token-validation failure (see Rule 10 — no custom exceptions at all)
- Config-map keys for a `tokenvalidator`/`kong`-named REST client (`application.http.restclient.tokenvalidator.*` or similar)

```java
// ❌ AVOID — reimplementing what Kong already does upstream:
@Component
public class TokenValidatorConnector {
    public ResponseEntity<String> validateToken(String idToken) { /* calls kongeieusercontext/jwt/tokenvalidator */ }
}
```

If the IIB ESQL has an ID-token-check node, note in the action-items section that Kong is expected to already enforce it at the gateway and that this is intentionally not reimplemented in the service — don't silently drop it without a note, but don't generate code for it either. This upgrades the older, weaker note in `common-patterns-and-gotchas.md` ("may be absent... confirm with team") to a hard never-generate rule, now that a real sibling repo confirms the absence rather than just suggesting it's possible.

---

## Rule 10 — Never define a custom exception class or `exception` package

> **Decision (2026-10-01), held twice against increasingly strong contrary evidence — do not "correct" this back toward the dominant real-repo pattern.** First comparison: `EmergencyOverrides`'s real sibling has an `exception/` package containing only a `@RestControllerAdvice` using Spring's own `NoResourceFoundException`/`NoHandlerFoundException` for a generic 404 fallback (no custom `Exception` subclass); `memberidcardprofile`'s real repo goes further and defines/uses a full custom `ApiErrorException` throughout controller/service/util/validator. Given those two, the user chose the stricter rule.
>
> That alone would be thin evidence to override on, so it was re-checked against the full workspace: **a `@RestControllerAdvice`-based handler catching only Spring/framework exception types (never a custom one) is in fact the dominant real convention — confirmed in 11 real repos, both phases, multiple backend types**: `GetGrievanceByCriterias` (`ApiExceptionHandler`), `rankedMatch` (`RankedMatchExceptionHandler`, scoped via `assignableTypes`), `DMRClaimStatus`, `EOB-ExplanationOfBenefit`, `GetGrievanceSummary` (`NotFoundExceptionHandler`), `memberseobpreferences`, `BenService-GetBeneficiaries`, `cumulativeCopaySummaries` (`RouteNotFoundExceptionHandler`), `RET-Pharmacy-Checkinimzprescriptions`, plus `EmergencyOverrides`'s own real sibling. Shown this count explicitly, **the user held the stricter rule anyway**: no `exception` package in either form — not a custom `Exception` subclass, and not a `@RestControllerAdvice` fallback handler either, even one that only catches `EIPMicroserviceException`/`NoResourceFoundException`. Every error path goes through the framework's own mechanism directly, inline, at the point of failure. Treat this as a deliberate, explicit, twice-confirmed override of the dominant real-repo pattern — not an unverified guess waiting to be corrected.
>
> **The "why," stated explicitly by the user (2026-10-01) so this isn't read as arbitrary:** this skill's migration goal (per `SKILL.md`'s "What done means" section) is a project that compiles and makes a **successful call to the backend** — exception-handling polish, including a generic-fallback advice class, is explicitly a *post-testing* refinement the user adds themselves once the `iib-ms-testing` skill's run shows what's actually needed, not something to generate speculatively during migration. This is the same philosophy already applied to business-logic precision (`SKILL.md` "Can be simplified, refined later") — exception handling stays on that "defer, don't fabricate ahead of need" list even though Akeyless (Rule 6) and JUnit tests (Rule 11) were later moved off it; exception handling is its own, still-current decision, not something that automatically follows those two reversals.

**Always note it, never generate it:** every migration's action-items section must include a line like *"No exception handler (e.g. a generic 404/fallback `@RestControllerAdvice`) was generated — add one after `iib-ms-testing` identifies what's actually needed."* This mirrors how Rule 9 handles Kong (note the ESQL node exists, generate nothing for it) — don't silently omit the note just because nothing was generated.

```java
// ❌ AVOID — a new Exception subclass:
public class ValidationException extends RuntimeException { ... }
public class TokenValidationFailedException extends RuntimeException { ... }

// ❌ AVOID — a @RestControllerAdvice fallback handler, even using only framework/Spring exception types:
@RestControllerAdvice
public class MyServiceExceptionHandler {
    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<ErrorStatus> handleNotFound(Exception e) { ... }
}

// ✅ REQUIRED — call the framework's error mechanism directly at the point of failure:
if (someValidationFails) {
    ErrorUtil.errorResponse(HttpStatus.BAD_REQUEST.value(), ErrorConstants.BAD_REQUEST_HTTP_MESSAGE, detail);
}
```

`ErrorUtil.errorResponse(...)` throws the framework's own `EIPMicroserviceException` — that's already the mechanism for producing a specific HTTP status (see `common-patterns-and-gotchas.md` item 8/§2 on why a surrounding generic `catch (Exception e)` must not swallow it). Catching the **framework's** `EIPMicroserviceException` to re-map or re-throw it (as `EmergencyOverridesService.getEmergencyOverridesById` does in the real sibling) is fine — that's using the framework's existing exception type, not defining a new one. The rule is specifically: never `extends RuntimeException`/`extends Exception` to create a new project-local exception type, and never add an `exception` package to hold one or a `@RestControllerAdvice` for one.

---

## Rule 11 — JUnit tests: ask the user; generate real content if they want it, structure-only if not

> **Revised 2026-10-07, superseding the earlier always-skeleton version of this rule.** The original version of this rule (2026-10-01) said to always generate empty skeleton test classes and never populate them, as a blanket "defer to post-testing" decision made on the user's behalf. **Standing instruction from the user now: don't decide this silently — always ask at INTAKE** ("Generate full JUnit 5 test cases with real content, or structure-only empty skeletons?"), and honor whichever answer comes back. The class list and file locations (`workflow-and-state.md`'s Tests subsection; REST's single-class convention in `rest-external-direct.md` §0c; every other category's fuller per-layer suite) are unchanged either way — the question only changes whether the method bodies get filled in.

**If the user wants full test cases:**
```java
// ✅ REQUIRED when the user asked for full test cases — real assertions, real mocking:
package com.cvshealth.eip.microservices.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class EmergencyOverridesServiceTest {

    @Mock private EmergencyOverridesServiceUtil serviceUtil;
    @InjectMocks private EmergencyOverridesService service;

    @Test
    void postEmergencyOverrides_happyPath_returnsBackendResponse() {
        // real assertion against the actual return value, not a placeholder
    }
}
```
Cover, at minimum: the happy path, every validation error path, a backend-exception path, a generic-exception path, and (for REST) test-harness routing and static-helper edge cases — the full list already written out in `rest-external-direct.md` §0c applies as real content now, not as deferred documentation. `mvn test -q` must pass with these tests actually running (not 0).

**If the user wants structure-only (or doesn't answer), fall back to the original shape:**
```java
// ✅ structure-only fallback — file exists, correctly packaged and named, intentionally empty:
@ExtendWith(MockitoExtension.class)
class EmergencyOverridesServiceTest {
    // Test cases added after iib-ms-testing confirms expected behavior — not populated during migration.
}
```
`mvn test` must still pass (0 tests run per class is not a failure in this mode). Don't fake it with a placeholder `@Test` method (`fail("TODO")`, `assertTrue(true)`, a `@Disabled` stub) in EITHER mode — in full-content mode that's a missing test, and in structure-only mode that's a test case, not "no test cases."

**Either way:** state which mode was used at the top of the HANDOFF output. In structure-only mode, also note in the action-items section that real test-case content is deferred — same framing as the exception-handler and Akeyless notes (Rules 6 and 10).

---

## Rule 12 — internal-CVS-backend host is always plain `pbm-oci` (no `az`/`ri`); `application-local.properties` always targets QA

> **Standing instruction from the user (2026-10-01; host-family correction added 2026-10-23, confirmed by a 23-repo cross-check across every internal-CVS-backend connector type).** Two separate rules live here, and both are about the **host**, not the environment-tier naming:
>
> 1. **The literal host is always `<env>eippbm-oci.cvshealth.com` — never `<env>eippbmaz-oci` or `<env>eippbmri-oci`.** This is one shared gateway host used by every internal-CVS-backend connector regardless of pattern — QL/CICS gateway (`/ql/request/v1`), RxClaim SBO gateway (`/rxclaim/request/v1`), the QL-DB2 proxy (`/qldb/execute/query/v1`), the ECS-DB2 proxy (`/ecsdb/executeQuery`), and reference-data (`/refdata`) all differ only by **path**, never by host family. The `az`/`ri` suffixes are a production-only artifact of two physical data centers (`structural-conventions.md` §5's `prod-pbmaz`/`prod-pbmri` config-map pair) — real evidence shows some phase-1 and early phase-2 services leaked that suffix into non-prod hosts and even into later-phase prod hosts inconsistently (`beneficiarystatusservice`, `carriersearchservice`, etc. use `pbmaz` everywhere; `BenService-GetBeneficiaries`, `GetGrievanceSummary`, `ValidateBridgeSupply`, `EmergencyOverrides`, `cumulativeCopaySummaries`, `memberseobpreferences` correctly use plain `pbm-oci`). **New migrations always use the plain `pbm-oci` form, in every environment, with no exceptions** — don't copy the `az`/`ri` suffix from an older sibling repo just because it's there.
> 2. **`application-local.properties` always targets the `qa` tier** (never `dev`/`prod`) for these connectors — local development runs against the shared QA backend, never dev (too unstable/single-tenant) or prod (obviously not for local testing).

```properties
# ✅ REQUIRED — application-local.properties, internal CVS backend (QA tier, plain pbm-oci host):
application.http.restclient.ql.uri=https://qaeippbm-oci.cvshealth.com/ql/request/v1
application.http.restclient.rxclaim.uri=https://qaeippbm-oci.cvshealth.com/rxclaim/request/v1
application.http.restclient.qlhc.uri=https://qaeippbm-oci.cvshealth.com/qldb/execute/query/v1
application.http.restclient.ecshc.uri=https://qaeippbm-oci.cvshealth.com/ecsdb/executeQuery
application.http.restclient.refdata.uri=https://qaeippbm-oci.cvshealth.com/refdata

# ❌ AVOID — dev/prod hostname in the local profile, OR an az/ri suffix in ANY environment:
application.http.restclient.ql.uri=https://deveippbmaz-oci.cvshealth.com/ql/request/v1
application.http.restclient.rxclaim.uri=https://qaeippbmaz-oci.cvshealth.com/rxclaim/request/v1
```

**Applies the same way to every per-environment config-map, not just `application-local.properties`** — only the `<env>` prefix changes (`dev`, `dev2`, `dev3`, `qa`, `qa2`, `qa3`, `stress`, `uat`, `prod`, `prod2`); the host suffix stays `eippbm-oci.cvshealth.com` in all of them. This is the one part of Rule 12 that is **not** limited to the local profile — rule 1 above (no `az`/`ri`) is a host-naming correction that applies everywhere; rule 2 (QA tier) is specifically about `application-local.properties`.

**Does not apply to external-vendor REST connectors** (MedForce, Precisely, Anthem, IngenioRx, etc.) — those don't have a CVS-style `qa` hostname, or a `pbm-oci` host, at all; their real `application-local.properties` points at the vendor's own non-prod/staging endpoint instead (`DMRClaimStatus` → MedForce's own test host; `rankedMatch` → `token-amer-staging.spectrum.precisely.com`). For those, use whatever the IIB source's own non-prod environment file shows for that vendor — there's no CVS "QA" equivalent to default to.

**Don't confuse the host with the config-map *filename*'s cluster suffix.** `config/config-map/application.<env>-<cluster>.properties` is still named with `pbmaz`/`pbmri`/etc. as its `<cluster>` segment (`structural-conventions.md` §5) — that names the Kubernetes deployment cluster (the Helm `CLUSTER:` value, `structural-conventions.md` §3), a completely different thing from the backend connector's URI host. A file named `application.prod-pbmaz.properties` still carries `application.http.restclient.ql.uri=https://prodeippbm-oci.cvshealth.com/ql/request/v1` (no `az`) inside it — the filename's cluster suffix is untouched by this rule.

---

## Rule 13 — Always ask the user for the MS destination explicitly; suggest same-location-as-source as the default answer

> **Standing instruction from the user, 2026-10-07, sharpened to "always ask" 2026-10-09.** This is one of the 5 mandatory INTAKE inputs (`workflow-and-state.md` Batch A) — ask directly and wait for an explicit answer, don't silently pick a location and move on. When asking, suggest the **same parent directory the IIB source repo lives in** as the recommended default — e.g. if the source is `<parent>/CVSEDB_PBM-<Service>-EDB-EIL`, suggest the sibling `<parent>/EIPMS_cvs-v1-<service>-oci` — rather than a separate "Migrated/" or other output location picked ad hoc.

```
# ✅ Suggested default layout, offered when asking (not assumed silently):
<parent>/
├── CVSEDB_PBM-EmergencyOverrides-EDB-EIL/      ← IIB source (already exists)
└── EIPMS_cvs-v1-emergencyoverrides-oci/        ← new MS repo, created as a sibling

# ❌ AVOID — a separate, disconnected output location picked without being asked:
SomeOtherFolder/Migrated/EmergencyOverrides/EIPMS_cvs-v1-emergencyoverrides-oci/
```

If the user names a different target directory, use that instead — the point of this rule is that the question is always asked and always gets a real, captured answer; the same-location suggestion is just what to recommend when asking, not a reason to skip asking.

---

## Rule 14 — Every generated repository carries `settings.xml`, and every Maven build uses it

The org's artifacts (the `framework-starter` parent, `framework-restclient` and the rest) are not on Maven Central. They resolve only through the internal Nexus repositories, which Maven reads from a settings file. Without it the parent POM is non-resolvable and nothing builds — not the HANDOFF build, not the quality gate, not a repair loop.

- Write `settings.xml` at the **repo root** (beside the `Dockerfile`), as a verbatim copy of `references/templates/maven-settings.xml`. Never retype it, never trim a repository out of it, never add a credential to it (credentials come from the build environment, not from the repository).
- **Every** `mvn` command for this repository — compile, test, `clean verify`, `spring-boot:run`, and the ones in `.github/workflows/` — passes it: `mvn -s <repo-root>/settings.xml ...`. A build that omits `-s` is not a valid build of this repository, whatever it reports.
- Mechanical check: `gap-checklist.md` [M27].

