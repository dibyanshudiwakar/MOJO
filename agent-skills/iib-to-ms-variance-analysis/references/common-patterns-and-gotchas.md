# Common Migration Patterns and Gotchas

Apply this checklist to every migration regardless of connector group. All items are derived from real defects found across the 12 training service pairs.

---

## ✅ Universal checklist

### 1. Error message text — reproduce verbatim

IIB error message literals must be reproduced exactly as Java String constants, because downstream API consumers check the exact text:

```java
// Must match IIB character-for-character:
public static final String TIMEOUT_ERROR_MSG  = "TIME OUT ERROR FROM EDB Service TransactionID:";
public static final String BACKEND_UNAVAIL    = "Backend is not responding successfully. [transactionID: ";
public static final String ERROR_DETAIL_PREFIX = "[9|Service Error][";
public static final String ERROR_DETAIL_TXID  = "][TransactionID: ";
// Note the trailing space before ] in the TransactionID suffix:
public static final String TRANSACTION_ID_SUFFIX = "] [transactionID: ";  // (some services have the space before [)
```

Verify the exact format from the ESQL's `SET opRef.moreInformation = ...` concatenation.

---

### 2. Timeout handler — always 408, never 500

Every IIB service has a dedicated `*_TimeoutHandler_ComposeError` that returns **HTTP 408** with `httpMessage='TimeOut Error'`. Several MS implementations incorrectly collapse this to 500. Always generate:

```java
// In *MessagingService or *Service:
public boolean isTimeoutException(Exception e) {
    Throwable cause = e;
    while (cause != null) {
        if (cause instanceof SocketTimeoutException
            || cause instanceof TimeoutException) return true;
        cause = cause.getCause();
    }
    return false;
}

// Usage:
} catch (Exception e) {
    if (isTimeoutException(e)) {
        ErrorUtil.errorResponse(408, "TimeOut Error",
            TIMEOUT_ERROR_MSG + transactionId);
    } else {
        ErrorUtil.errorResponse(500, "Internal Service Error", ...);
    }
}
```

**[single-repo evidence, `RankedMatch`]** When `application.circuitbreaker.*` properties are present (`rest-external-direct.md` §6), an open breaker surfaces as an `EIPMicroserviceException` whose `isCircuitBreakerError()` accessor returns `true` — route that branch through the **same** 408/timeout path above, not a generic 500. See `rest-external-direct.md`'s "Circuit breaker — the Java-side consumption pattern" for the full real catch-block shape and test coverage. Treat "circuit-breaker properties present but no `isCircuitBreakerError()` branch anywhere" as the same class of defect as collapsing a real timeout to 500 — it's the same checklist item, just a second trigger for it.

---

### 3. Signed-decimal encoding — PIC S9(N) SIGN LEADING SEPARATE

COBOL `S9(N) SIGN LEADING SEPARATE` fields are `N+1` bytes total (1 sign char + N digits). In Java, use:

```java
// CORRECT — produces "+00000012" for value 12, N=8:
public static String padSigned(String value, int numericDigits) {
    if (value == null || value.isBlank()) return "+" + "0".repeat(numericDigits);
    long v = Long.parseLong(value.trim());
    return (v < 0 ? "-" : "+") + String.format("%0" + numericDigits + "d", Math.abs(v));
}

// WRONG — do NOT use padNumeric for signed fields (it drops the sign char):
// padNumeric("12", 9) → "000000012" (8 bytes, missing sign → corrupts buffer)
```

⚠️ **The parse direction needs an equally explicit helper — don't stop at build.** A response-side SLS field read via Bindy comes back as the raw padded string (e.g. `"+0018"`), not a normalized value. Confirmed live against a real backend: a migration added `padSignedNumeric` for the request side but no `parseSignedNumeric` counterpart, then passed the raw Bindy field straight through as both the displayed value **and** a reference-data lookup key. The displayed code came back as `"+0018"` instead of `"18"`, and the ref-data lookup silently missed (key `"1532_+0018"` never matches `"1532_18"`) — descriptions came back empty with no exception thrown anywhere, because a failed map lookup just returns the empty-string default.

```java
// REQUIRED — the parse-direction counterpart, applied at every point an SLS
// response field is displayed or used as a lookup key, not just where it's built:
public static String parseSignedNumeric(String raw) {
    if (raw == null || raw.isBlank()) return "0";
    String trimmed = raw.trim();
    char sign = trimmed.charAt(0);
    String digits = (sign == '+' || sign == '-') ? trimmed.substring(1) : trimmed;
    String stripped = digits.replaceFirst("^0+(?!$)", "");
    if (stripped.isEmpty()) stripped = "0";
    return sign == '-' ? "-" + stripped : stripped;
}
```

This class of bug is easy to miss by inspection because it doesn't throw — the code compiles, the call succeeds, and the response is well-formed JSON with merely a wrong/blank value. Treat every SLS-encoded field read out of a Bindy response record as needing this normalization before it reaches a DTO field or a lookup key, not just the fields that "look" numeric-and-displayed.

---

### 4. IIB `COALESCE(field, 'null')` → Java `null`

IIB sometimes emits the literal string `"null"` as a default. Java MS implementations intentionally leave the field as Java `null` (JSON `null`), relying on `@JsonInclude(NON_NULL)` to omit it. **Do NOT reproduce `"null"` strings.** This is a documented, intentional deviation.

---

### 5. IIB 404 vs 500 error-type distinction

IIB distinguishes backend errors by XML element name:
- `<ECSError>` → 404
- `<ECSResponseError>` → 500

Several MS implementations collapse both into 404. If the service contract requires the distinction, generate separate handling:

```java
if (body.contains("<ECSError>")) {
    ErrorUtil.errorResponse(404, "Not Found", ...);
} else if (body.contains("<ECSResponseError>")) {
    ErrorUtil.errorResponse(500, "System Unavailable", ...);
}
```

---

### 6. QL connector routing key — must match THIS service

`@EIPMicroserviceProviderCall(function = "TXNCODE.PROGRAM")` must exactly match the CICS transaction/program for this service. Do NOT copy-paste from another service. One training service (findallergiesanddiagnosis) has a confirmed defect where B470 constants appear in a W050 service — verify every `function=` string before finalizing.

---

### 7. EIE header parsing — verify `EieHeader`'s real accessors; `framework-eieusercontext` need is category-dependent

> `X-Eieusercontext` and `X-Eieapplication` are the **HTTP header names** sent by the EIE platform — they are IIB source-code references only, not Java method names. **Corrected** (see Rule 8 in `mandatory-migration-rules.md` for the full history): an earlier version of this item claimed `EieHeader` exposes `getAccountIdentifierValue()`/`getApplicationShortName()`/`getTransactionId()`/`getTestHarnessUri()` directly, and that `framework-eieusercontext` should never be used. Both claims were wrong — the getter names were fabricated by analogy with the IIB JSON field names rather than checked against the real class, and the "never use `framework-eieusercontext`" rule was over-generalized from a single QL/CICS example that turned out to be the exception, not the rule (2 of 3 RxClaim SBO repos and the one MQ_COBOL repo checked all genuinely depend on it).

```java
// ✅ CORRECT — EieHeader's confirmed real accessors (from framework-restclient, always present):
@Autowired private EieHeader eieHeader;

String rawContext = eieHeader.getEieUserContext();   // raw JSON string — NOT a direct accountIdentifierValue getter
String rawAppCtx  = eieHeader.getEieApplication();    // raw JSON string — NOT a direct applicationShortName getter
String apiTxId    = eieHeader.getApiTransactionId();
String corrId     = eieHeader.getEieCorrelationId();
boolean isPMode   = "P".equals(eieHeader.getTestHarnessMode());
String idToken    = eieHeader.getIdToken();           // needed only if framework-eieusercontext builds the context from it
// eieHeader.setTestHarnessMode(String) is also a confirmed real setter (not just the getter above) —
// a real RankedMatchUtil uses it to temporarily clear and restore test-harness mode around a
// vendor OAuth token call specifically, so that one call is never P-mode-redirected even when the
// overall request is (rest-external-direct.md §0d).
```

Extracting a specific field (e.g. `accountIdentifierValue`, `applicationShortName`) out of the raw JSON string is a **separate step**, and which approach to use is itself category/service-dependent — see `ql-cics-camel-bindy.md` §0 (prefer `ObjectMapper.readTree()` over a substring scan, though both are seen in practice) and `rxclaim-sbo-xml-jaxb.md` §0 (some services pass the raw context straight through with no extraction at all; others do extract). Don't assume one approach transfers to a new service without checking its own IIB ESQL and, ideally, a sibling reference project in the same connector category.

**Whether `framework-eieusercontext` (and its `EieHeaderBuilder.createEieUserContextString(idToken)`) is needed at all** depends on whether `eieHeader.getEieUserContext()` arrives pre-populated for that service, or must be built from `getIdToken()` first — confirmed genuinely needed in `carriersearchservice`, `memberidcardprofile` (RxClaim SBO) and `fepclaimdetail` (MQ_COBOL); confirmed *not* needed in the one QL_CICS service checked (`ValidateBridgeSupply`, live-tested). Check the target flow, don't assume either way.

IIB ESQL equivalent of the extraction step (for context only — do not blindly replicate this exact string-scan in Java without first checking whether the target service's own reference project does something more robust):
```
SUBSTRING(InputRoot.HTTPInputHeader."X-Eieusercontext" AFTER '"accountIdentifierValue":"' BEFORE '"')
```

**What stays an absolute "never," with no category exception found anywhere**: JWT/JWKS validation (fetching a JWKS endpoint to cryptographically validate a token's signature) to extract any EIE field. Zero training repos across QL_CICS, RxClaim SBO, or MQ_COBOL do this — it's unnecessary machinery for a value that's already sitting in the header or buildable via the framework's own `EieHeaderBuilder`.

---

### 8. BIP broker error text — reproduce as Java constants

When IIB DFDL serializer fails (wrong JSON type), it emits specific BIP diagnostic codes. The MS validator must produce the identical text before reaching the buffer builder:

```java
public static final String BIP2328E_TEMPLATE  =
    "[2|BIP2328E: %s field data value 'true' is not a valid java.lang.Integer value...]";
public static final String BIP5505E_TEMPLATE  =
    "[2|BIP5505E: non-numeric data '%s' found for field '%s' of type xs:integer...]";
public static final String BIP5507E_TEMPLATE  =
    "[2|BIP5507E: data '%s'...is too long for field '%s'...]";
```

Validate JSON input field-by-field against COBOL PIC constraints BEFORE building the buffer. Throw 500 (not 400) for type mismatch, matching IIB's behavior where the DFDL runtime — not the application — would have caught the error.

---

### 9. `@JsonInclude(NON_NULL)` on response DTOs — and blank fields must map to `""`, never to `null`

IIB omits JSON fields when `IS NULL`. Java must match:

```java
@JsonInclude(JsonInclude.Include.NON_NULL)
public record *Response(...) {}
```

**Models aren't universally records** — this only applies where records are actually the convention for that connector category. Confirmed: REST, QL_CICS, and SOAP default to Java 21 records for response models. RxClaim SBO is **Lombok-dominant instead** — records there are reserved for small immutable error-payload classes, not the default (see `rxclaim-sbo-xml-jaxb.md` §0). Don't cross-apply the records convention into RxClaim SBO by default.

⚠️ **[live-tested] The `@JsonInclude(NON_NULL)` + blank-field interaction is a real trap.** A mapping method that converts a blank/trimmed-empty string to `null` (a "trim-or-null" helper) will cause `@JsonInclude(NON_NULL)` to **silently omit that field from the JSON entirely**, when the correct behavior — confirmed by diffing against a real reference response — is to serialize it as `""`. Use a "trim-or-empty-string" helper (`value == null ? "" : value.trim()`), never "trim-or-null," for fields that go into a `NON_NULL`-annotated DTO, unless you've specifically confirmed the target service omits that field on blank input (some do, most in this training set don't — check, don't assume). See `gap-checklist.md` [H6] for the exact bug this produced in practice.

---

### 10. MQ queue names in MS config may differ from IIB

IIB msgflow queue names (e.g. `ESL.PBM.MVS.REQUEST`) are often NOT the same as the MS `application.<svc>.request-queue` property value (e.g. `ESM.REQUEST.MVS`). Do not mechanically copy queue names. Mark these as **requires infra/queue team confirmation** in the output.

---

### 11. IIB credentials stored in .properties — MS encrypts by default, generates live Akeyless when a service actually needs it

IIB `*.properties` files commit credentials in plaintext. **Default: every migration uses the CVS-encrypt-endpoint pattern, regardless of phase** (`mandatory-migration-rules.md` Rule 6) — encrypt the plaintext value via the CVS endpoint and put the returned ciphertext directly into the config-map property value.

**Revised 2026-10-07 — Akeyless is no longer deferred when a service genuinely requires it.** If the specific service is determined to need Akeyless (checked against its own real sibling repo or confirmed with the team — not assumed from phase alone), generate the live scaffolding:

```yaml
# Confirmed real, LIVE pattern (GetGrievancesAppeal, Phase 2) — generate this when required,
# don't defer it as a future action item:
spring.config.import=optional:/os/spring-boot-<service>/creds/application-akeyless.properties
```

Not every Phase 2 service needs this — `RET-Pharmacy-Checkinimzprescriptions` is a confirmed real Phase 2, REST-to-vendor service with **zero** Akeyless wiring anywhere, using a plain classpath SSL cert instead. Check per-service; don't default by phase.

Never write actual plaintext credential values into the MS config-map files, in either phase.

---

### 12. Test-harness URL must be present but defaultable

Every MS service needs a test-harness URL override. Pattern:

```java
@Value("${application.http.restclient.ql.test-harness-host:}")
private String testHarnessHost;

private String resolveTestHarnessUrl() {
    String headerUri = eieHeader.getTestHarnessUri();
    return StringUtils.isNotBlank(headerUri) ? headerUri : testHarnessHost + TEST_HARNESS_PATH;
}
```

---

### 13. Response DTO naming — `httpCode` / `httpMessage` / `moreInformation`

The error response JSON shape is standardised across ALL services (matches IIB's output):

```java
public record ErrorStatus(
    String httpCode,
    String httpMessage,
    String moreInformation  // NOT "moreInfo" — exact field name required
) {}
```

---

### 14. IIB "not found" → HTTP 200 with empty body (not 404)

Several IIB services return HTTP 200 with an empty list/body rather than 404 when no records are found. Check the `*_PrepareErrorResponse` or `*_ComposeESLError` modules for `ReplyStatusCode = '200'` logic. The MS must replicate this — returning 404 here would break API consumers. **This is per-flow, not a repo-wide constant** — even a single repo's two endpoints can legitimately disagree (see `rest-external-direct.md` §5's `EOB-ExplanationOfBenefit` example). Verify against the actual IIB behavior for the *specific* endpoint you're migrating.

---

### 15. Before flagging a response field as "needs team confirmation," check whether it's already parsed under a different name

⚠️ **[live-tested]** A response DTO field (`CRTBenefitRequestId`) was hardcoded to `null` with an "ACTION ITEM — confirm source buffer field with QL team" comment, when the actual source value was already being parsed into a Bindy model field under a *different* name (`brtId`, from `RPL_BSP_BRT_ID`) and simply never wired to that output slot. Diffing against one real reference response resolved what looked like a genuine unknown-mapping question in seconds. Before writing "confirm with QL/[whoever] team" on a field, check every already-parsed field in the response model first — the answer is sometimes sitting in code already written for a *different* output field, not actually missing from the wire format at all. See `gap-checklist.md` [H7].

---

### 16. Never call `ErrorUtil.errorResponse(...)` for one case from inside a `try` whose `catch` handles a different case

⚠️ **[live-tested]** `ErrorUtil.errorResponse(...)` throws — that's the whole point, it's the framework's mechanism for producing a specific HTTP status (see item 8's related framing). The exception it throws (`EIPMicroserviceException`) is itself an `Exception`. If a method's structure is:

```java
try {
    T value = riskyCall();
    if (isInvalid(value)) {
        ErrorUtil.errorResponse(401, ...);   // ← throws EIPMicroserviceException
    }
    return value;
} catch (Exception e) {                       // ← this ALSO catches the 401 thrown above
    ErrorUtil.errorResponse(500, ...);
}
```

...the 401 call's own thrown exception gets caught by the same `catch (Exception e)` and silently overwritten with a generic 500 — the specific error is lost with no compile-time warning and no obvious runtime symptom beyond "the status code is wrong." This was caught by a failing unit test (`expected: 401, but was: 500`) after structuring new code this exact way — see `gap-checklist.md` [H14] and `rest-external-direct.md` §0d for the concrete case (a "token missing" 401 nested inside a "token-fetch threw" 500 handler). Fix by scoping the `try` to *only* the call that can actually throw the exception you mean to catch, and moving any `errorResponse(...)` call for a different condition to after the try/catch/finally has fully exited — never nested inside it. This applies to every backend type, not just REST/OAuth; grep for methods containing more than one `errorResponse(...)` call and check the nesting whenever you see one.

---

## Cross-category meta-lessons

These apply across every connector category, distilled from repeatedly re-deriving and then having to correct single-category-derived "hard rules" over the course of this skill's development:

1. **The single most validated, cross-category-confirmed finding**: no training repo, across QL_CICS, RxClaim SBO, or MQ_COBOL, ever uses JWT/JWKS *validation* (fetching a JWKS endpoint to cryptographically validate a token) to get at EIE user-context data — they all read an already-populated or framework-built raw context string. If a migration plan reaches for JWKS validation specifically to extract a requester ID, stop and check — no training-set precedent supports that path being necessary, in any category checked so far.
2. **Backend-type-specific patterns don't transfer across categories, and sometimes not even across services within one category.** The QL_CICS "prefer `ObjectMapper.readTree()` over a substring scan" lesson and the RxClaim SBO "some services pass the context straight through with no extraction at all" lesson look contradictory out of context but both are correct *for their own service*, confirmed by direct source inspection. Always confirm which backend type — and ideally which specific sibling service — you're closest to before applying a "fix" learned elsewhere.
3. **Treat any single-repo-derived absolute rule with real suspicion until cross-checked, including this skill's own rules.** This skill's own Rule 8 (`mandatory-migration-rules.md`) went through exactly this failure mode once already: a real, live-tested QL_CICS finding ("this service doesn't need `framework-eieusercontext`") got written down as an absolute "never use this module" rule, which then turned out to be wrong for 3 other repos across 2 other categories once actually checked. The fix isn't "don't trust live-tested evidence" — it's "state the scope of what was actually tested," and re-verify before generalizing a finding from N=1 to "always."
4. **Version numbers drift across repos** (`framework-starter` 3.1.3–3.2.2, `camel.version` 4.20.0–4.22.1, `springdoc.version` 2.8.4–3.1.0 all observed across the training set) — always defer to the user's explicit spec (Rule 7), never to what a reference repo happens to pin. Version drift across sibling repos is expected and not itself a gap to fix.

---

## Per-group additional gotchas

| Group | Extra gotcha |
|---|---|
| QL/CICS | `OCCURS`-table loop may produce 1-based vs 0-based indexing bugs if ESQL uses `WHILE I <= J` without correctly indexing `Item[I]` — check whether IIB effectively wrote only index 1 (BenService-GetBeneficiaries `REQ_IMG_BRI_NB` pattern) |
| RxClaim SBO | `Marshaller.JAXB_FRAGMENT=true` required — omitting it adds `<?xml?>` prolog which the backend rejects |
| IBM MQ | `@ConditionalOnProperty(value="application.ibm.mq.enabled", havingValue="true")` on the MQ connector to allow local testing without a live MQ |
| REST External / RxClaim SBO | JWT/Id-token pre-validation done in IIB (e.g. GetGrievanceByCriterias `IDTokenValidation` node, EmergencyOverrides' `getEmergencyOverrides_IDTokenCheck`) is confirmed absent in the real MS sibling — Kong gateway handles it upstream. **Never generate a token-validator connector/service/exception/config for it** (`mandatory-migration-rules.md` Rule 9) — this is no longer a "confirm with team" judgment call, a direct real-sibling-repo diff confirmed the absence for `EmergencyOverrides` on 2026-10-01. Note the ESQL node's existence as an action-item ("Kong is expected to enforce this upstream"), don't silently drop it without a note, but don't generate code for it either |
