# REST → External / Direct — Migration Reference

**Training services:**
- EOB-ExplanationOfBenefit (XML-over-REST to memberdoc.com)
- GetGrievancesAppeal (OAuth2 to Anthem + IngenioRx/CarelonRx)
- GetGrievanceByCriterias (custom-header auth to MHK/Aetna NITRO)
- rankedmatchservice / EIPMS_cvs-v1-rankedmatchservice-oci (OAuth2 to vendor Precisely, address ranked-match validation) — real repo read directly and diffed against a skill-generated migration; see §0d

⚠️ **Don't confuse `EOB-ExplanationOfBenefit` (singular) with `EOB-ExplanationOfBenefits` (plural).** Both folders exist side by side in the workspace. The singular one is the real training service referenced throughout this file. The plural one (`EIPMS_cvs-v1-explanationofbenefits-oci`) is **not a Spring Boot repo at all** — confirmed by a 22-repo survey: its "config-map" files are plain JSON (`{"environment":"prod","SIGNUP_URL":"/signup",...}`), and it has no `pom.xml`/`.java` anywhere. Don't spend time reading it expecting MS structure.

## 0. Corrections vs. a generic/assumed REST pattern — all 3/3 training repos agree

These four points contradict what a "REST should be different from the other backend types" assumption would predict. They aren't — REST follows the *same* conventions as SOAP/QL_CICS/RXCLAIM_SBO on every one of these, confirmed 3/3 across the training set:

1. **Parent pom version is `0.0.1-SNAPSHOT`**, same as every other backend type — not `1.0.0-SNAPSHOT`.
2. **Response (and request) models are Java 21 records**, `@JsonInclude(NON_NULL)` where appropriate — never Lombok `@Data` classes for API-facing models. Lombok is fine for `@RequiredArgsConstructor` on services/connectors, never on a model.
3. **No dedicated `{Backend}Properties` `@ConfigurationProperties` class by default** — all three training repos inject backend URL/credentials via plain `@Value(...)` directly on the connector, or via a `*ConnectorUtil` helper keyed off `Constants`. A dedicated properties class is an accepted alternative if the project already has one, not the primary pattern.
4. **Neither `logback-spring.xml` nor explicit `logging.level.*` properties appear** — logging is inherited entirely from the shared `EIPMicroserviceLogger` platform module. Don't add either; their absence is normal for this backend type, unlike SOAP/QL_CICS/RXCLAIM_SBO/MQ_COBOL where `logback-spring.xml` is expected. **This is about `logback-spring.xml` specifically — it does not mean skip `logging.config=classpath:logback-<env>.xml` in each env's config-map file.** That's a genuinely different, real, and safe pattern: `logging.config=classpath:logback-dev.xml` (etc., one per env, plus `logback-local.xml`) is confirmed present in every real per-env config-map, and every one of those filenames is bundled *inside the `framework-core` jar itself* (confirmed present in every `framework-core` version from 3.0.0 through 3.2.2 by listing jar contents) — the service's own repo never needs to author these files. Don't drop this property out of a mistaken worry that it points at a file the migration hasn't created.

Not universal, but worth checking rather than assuming either way: `RestClientRequest` construction is builder-based in some real REST files and plain-setter-based in others (`RankedMatchConnectorService`'s real, deployed version uses `new RestClientRequest()` + `setClientName()`/`setUrl()`/etc., not `.builder()`, even though the class supports both) — Rule 1's "verify per framework class" already covers whether a builder *exists*; this is the separate, narrower point that even where it exists, an individual file may not use it. When a real reference project exists for the *specific* service being migrated, mirror that file's own construction style over the general preference.

See `gap-checklist.md` [R9], [R14], [R5], [R16] for the checklist form of these, and `common-patterns-and-gotchas.md` for the cross-category framing.

## 0a. Maven — parent pom, CVE overrides, Cucumber, jacoco

```xml
<!-- Parent pom -->
<parent>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-starter</artifactId>
    <version><!-- per user spec, or highest in workspace (Rule 7) --></version>
</parent>
<!-- NO CVE overrides in <dependencyManagement> for REST services specifically -->
<!-- NO maven-cucumber-reporting plugin for REST services -->
```

Web pom additionally needs (beyond §7 below): `spring-boot-starter-web`, `spring-boot-starter-validation` (both excluding `spring-boot-starter-logging`), `springdoc-openapi-starter-webmvc-ui`, standard `jakarta.*` API deps, and for build plugins: `jacoco-maven-plugin` (LINE coverage minimum 0.65, excluding `**/{ServiceName}Application.class`) + `maven-compiler-plugin` (release 21) + `spring-boot-maven-plugin`. **No Cucumber test dependencies at all** — no `cucumber-java`/`cucumber-spring`/`AcceptanceTestSuite`/`features/` folder, unlike the other four backend types (`gap-checklist.md` [R10]).

## 0b. Package layout note

`com.cvshealth.eip.microservices.{service-name}` — whether the base package includes the service name is **inconsistent** across training repos (`GetGrievanceByCriterias` does, `EOB-ExplanationOfBenefit`/`GetGrievancesAppeal` don't) — check sibling services in the same domain before assuming either way (`gap-checklist.md` [R1]). Two separate `model` roots exist for this backend type specifically: `model/` (root) holds POJOs for talking to the downstream backend — the Jackson-deserialization target for the raw backend HTTP response; `swagger/model/` holds the service's own API-facing contract (what callers of *this* service send/receive). Don't conflate the two.

## 0c. Testing convention — single focused test class, not 20+

Unlike SOAP/QL_CICS/RXCLAIM_SBO/MQ_COBOL (which target 20+ test classes across every layer), REST gets **one** focused `{ServiceName}ServiceTest` (`@ExtendWith(MockitoExtension.class)`, pure Mockito, no Spring context loading).

**During migration (Rule 11): create only this one empty skeleton file — no content.** The coverage list below is deferred guidance for whoever writes the actual test cases later, after `iib-ms-testing` — it is not something to populate now. It should eventually cover: happy path, the "not-found" response for that specific flow (whatever HTTP code that flow actually uses — see §5's `R6` caveat below), every validation error path, a backend-exception-thrown-as-500 path, a generic-exception-thrown-as-500 path, test-harness routing, and every static helper (`substringBefore`/`substringAfter`/`coalesce`-style methods) — including their "separator absent → empty string, not null" behavior (`gap-checklist.md` [R7]).

---

## 0d. OAuth2 — use `framework-oauth`'s `OAuthClient`, don't hand-roll a token cache (confirmed by diffing a real repo against a migration)

A skill-generated migration for `rankedmatchservice` independently built a custom token mechanism — a `preciselytoken` `RestClientWrapper` connector, a manual `volatile`-field cache with a TTL check, and `Encryption.decrypt()` on a pre-encrypted Basic-Auth value — instead of using `framework-oauth`. Diffing the generated output against the real, deployed `EIPMS_cvs-v1-rankedmatchservice-oci` repo confirmed the real service does something structurally simpler: a `framework-oauth` dependency + an injected `OAuthClient` bean, configured entirely through properties (`application.oauth.client.<name>.*` + `application.http.restclient.<name>.*`, same as the GetGrievancesAppeal-style keys in §6 below). The framework owns caching/expiration/eviction itself — application code never manages a TTL or decrypts a token-endpoint credential by hand.

```java
// Confirmed real pattern (RankedMatchTokenService) — no manual cache, no Encryption bean:
@Component
@RequiredArgsConstructor
public class *TokenService {
    private final OAuthClient oAuthClient;
    private final *Util util;
    private final EieHeader eieHeader;

    @EIPMicroserviceProviderCall(provider = EIPMicroserviceProvider.*, function = Constants.FUNCTION_OAUTH)
    public String acquireToken() {
        String savedMode = util.saveAndClearEieTestHarnessMode(eieHeader);  // see below
        String accessToken;
        try {
            accessToken = oAuthClient.getAccessToken();   // no-arg; confirmed to throw checked Exception
        } catch (Exception e) {
            ErrorUtil.errorResponse(500, Constants.OAUTH_ERROR, ...);
            return null; // unreachable
        } finally {
            util.restoreEieTestHarnessMode(eieHeader, savedMode);
        }
        if (StringUtils.isNotBlank(accessToken)) return accessToken;
        ErrorUtil.errorResponse(401, Constants.OAUTH_ERROR, ...);  // MUST be outside the try/catch above — see the gotcha below
        return null; // unreachable
    }
}
```

Three confirmed, specific findings from this exercise:

1. **`OAuthClient.getAccessToken()` takes no arguments and declares a checked `Exception`** — confirmed by a real compile error (`unreported exception java.lang.Exception; must be caught or declared to be thrown`) when a test stubbed it without `throws Exception` on the test method. Don't assume a `getAccessToken(clientName)` overload exists (Pattern B's illustrative snippet elsewhere in this file shows one, from GetGrievancesAppeal's own `oAuthClient.getAccessToken(clientName)` — that overload is real too; which one to call depends on whether the service is configured with one OAuth client or several, check the target service's own property block).
2. **`EieHeader.setTestHarnessMode(String)` is a confirmed real setter**, not just the getter documented elsewhere in this skill — the real `RankedMatchUtil` calls it to temporarily null out and restore the test-harness mode around the token call specifically, so the OAuth token endpoint is never P-mode-redirected even when the overall request is. Add this to the confirmed-real-accessors list wherever `EieHeader` is documented (`mandatory-migration-rules.md` Rule 8, `structural-conventions.md` §11).
3. **⚠️ A live-tested gotcha, not just a style preference: never call `ErrorUtil.errorResponse(...)` for one error case from inside a `try` block whose `catch (Exception e)` handles a different case.** `ErrorUtil.errorResponse(...)` throws (it's the framework's only way to produce a specific HTTP status from this kind of code, per the `@EIPMicroserviceOperation` AOP note elsewhere in this file) — and the exception it throws (`EIPMicroserviceException`) **is itself an `Exception`**. Writing the "token missing → 401" call inside the same try block as the "token-fetch threw → 500" catch means the 401 call's own thrown exception gets caught by that same catch block and silently overwritten with a generic 500. Caught live via a failing unit test (`expected: 401, but was: 500`) after structuring the code exactly like a real reference file that has the same shape — restructure so only the actual risky call (the OAuth client invocation itself) is inside the try, and any `errorResponse(...)` call for a *different* condition happens after the try/catch/finally has already completed, not nested inside it. See `gap-checklist.md` [H14] and `common-patterns-and-gotchas.md` item 16 for the general form of this gotcha — it applies to any `ErrorUtil.errorResponse` call, not just OAuth.

---

## 1. Structural mapping template

| IIB element | Java class | Notes |
|---|---|---|
| `*_DIMtoBackend` / request build compute module | `*ConnectorUtil.buildRestRequest()` / `*ConnectorService` | Headers, URL, payload |
| `Get*Token` ESQL module (OAuth2 token flow) | `*TokenService.acquireToken()` via framework `OAuthClient` | Only for OAuth services |
| `ComIbmWSRequest` HTTP node | `RestClientWrapper.executeRestCall(RestClientRequest, Class<T>)` | framework-restclient |
| `*_BackendResponse` / response parse | `*ConnectorUtil.processResponse()` / `*Service.map*()` | Jackson or DOM parse |
| `*_ComposeError` / error compose | `ErrorUtil.errorResponse(...)` via catch blocks in connector | |
| Catch/Failure/Timeout handler subflows | `ErrorUtil.errorResponse(...)` called inline at the point of failure (connector/service) | No `@RestControllerAdvice`/`exception` package — Rule 10 |
| `Testharness='P'` redirect | `RestClientRequest.setTestHarnessUrl()` or `resolveUrl(isPMode(), testHarnessUrl, prodUrl)` | |

---

## 2. HTTP request construction patterns

### Pattern A — Custom header credentials (MHK style)

IIB: `SET OutputRoot.HTTPRequestHeader.MEDHOKUSER = user; SET OutputRoot.HTTPRequestHeader.PWD = pwd;`

Java:
```java
Map<String, String> headers = new HashMap<>();
headers.put("MEDHOKUSER", user);      // MhkConnectorConstants.HEADER_MEDHOKUSER
headers.put("PWD", password);         // MhkConnectorConstants.HEADER_PWD

RestClientRequest request = new RestClientRequest();
request.setClientName(clientName);    // "mhk" or "mhkNitro" — routes to correct .uri property
request.setMethodType(RequestMethod.POST);
request.setPayload(mhkRequest);       // Jackson-serialized request DTO
request.setHeaders(headers);
```

Key: the `clientName` routes to `application.http.restclient.<clientName>.uri`.

### Pattern B — OAuth2 Bearer token (GetGrievancesAppeal / rankedMatch style)

IIB: `GET_Anthem_Token.esql` builds a `Basic base64(clientId:clientSecret)` token request.

Java:
```java
// Token service (delegated to framework OAuthClient)
@Value(AnthemConnectorConstants.PROP_OAUTH_URI)   // "${application.oauth.client.anthem.uri}"
private String tokenUri;

public String acquireToken() {
    return oAuthClient.getAccessToken(clientName);  // framework handles Basic auth + POST
}

// Connector uses Bearer token:
headers.put(HttpHeaders.AUTHORIZATION, "Bearer " + token);
headers.put("apiKey", apiKey);  // some providers also need API key header
request.setAuthHeader("Bearer " + token);  // alternative: framework's setAuthHeader
```

### Pattern C — XML-over-REST (EOB style)

IIB: `CREATE LASTCHILD OF OutputRoot DOMAIN 'XMLNSC' NAME 'getEOBDocument'`

Java (hand-built XML, no JAXB):
```java
String xmlBody = "<getEOBDocument><DocumentID>" + documentId + "</DocumentID></getEOBDocument>";
headers.put(HttpHeaders.CONTENT_TYPE, "application/xml");
headers.put(HttpHeaders.ACCEPT, "application/xml");
request.setPayload(xmlBody);
// Response parsed with DOM:
DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
Document doc = builder.parse(new InputSource(new StringReader(responseXml)));
NodeList nodes = doc.getElementsByTagName("DOCUMENT_CONTENT");
```

### Test-harness URL resolution pattern

```java
// IIB: IF Testharness = 'P' AND testHarnessURL IS NOT NULL
// Java:
private boolean isPMode() {
    return "P".equals(eieHeader.getTestHarnessMode());
}

private String resolveUrl(String prodUrl, String testHarnessUrl, boolean isTestHarness) {
    return (isTestHarness && StringUtils.isNotBlank(testHarnessUrl)) ? testHarnessUrl : prodUrl;
}
```

---

## 3. Connector invocation

### `RestClientWrapper.executeRestCall` (framework)

```java
@EIPMicroserviceProviderCall(
    provider = EIPMicroserviceProvider.AETNA,   // or MHK, CARELON_RX, etc.
    function = "serviceName.operationName")
public ResponseEntity<String> callBackend(RestClientRequest request) {
    return restClientWrapper.executeRestCall(request, String.class);
}
```

`RestClientWrapper` is from `framework-restclient` — its source is not in the MS repo. Configuration (connection pool, timeouts, TLS) is driven by `application.http.restclient.<clientName>.*` properties. The `clientName` set on `RestClientRequest` determines which properties block is used.

### TLS enforcement (for services that need TLSv1.2)

```java
@PostConstruct
public void configureTls() {
    System.setProperty("jdk.tls.client.protocols", "TLSv1.2,TLSv1.3");
    System.setProperty("https.protocols", "TLSv1.2,TLSv1.3");
}
```

---

## 4. Response handling

### JSON response (MHK / grievances style)

IIB: `InputRoot.JSON.Data.Item[i].fieldName` ESQL field navigation

Java:
```java
// Backend response DTO:
@JsonIgnoreProperties(ignoreUnknown = true)
public record MhkGrievanceItem(
    String grievanceNo, String priority, ...
) {}

// Deserialize:
List<MhkGrievanceItem> items = objectMapper.readValue(body, new TypeReference<>(){});

// Map to output:
return items.stream()
    .map(item -> new Grievance(item.grievanceNo(), item.priority(), ...))
    .collect(Collectors.toList());
```

Key Jackson annotation for reserved-word JSON field names:
```java
@JsonProperty("case")
public GrievanceCase grievanceCase() { return grievanceCase; }
```

### Not-found / empty response pattern

IIB: `IF message = 'Grievance Not Found' OR message = 'Member not found' THEN return 200 with empty list`

Java:
```java
public boolean isNotFoundMessage(String body) {
    String message = extractDownstreamMessage(body);
    return Constants.MHK_ERROR_GRIEVANCE_NOT_FOUND.equals(message)
        || Constants.MHK_ERROR_MEMBER_NOT_FOUND.equals(message);
}

// In connector:
if (util.isNotFoundMessage(ex.getResponseBodyAsString())) {
    return util.emptyResponse();  // 200 with empty list
}
```

### XML response (EOB style)

```java
// DOM parse — NOT JAXB, since the backend XML is flat/simple
Document doc = DocumentBuilderFactory.newInstance().newDocumentBuilder()
    .parse(new InputSource(new StringReader(xml)));
String content = doc.getElementsByTagName("DOCUMENT_CONTENT").item(0).getTextContent();
// No content → 204:
if (content == null || content.isBlank()) {
    ErrorUtil.errorResponse(HttpStatus.NO_CONTENT.value(), "No Content", errorCode);
}
```

---

## 5. Error handling

| Scenario | HTTP code | Java pattern |
|---|---|---|
| Input validation (missing required param) | 400 | `ErrorUtil.errorResponse(400, "Data Validation Error", "[9|Data Validation Error][1002|" + detail + "][TransactionID: " + txId + " ]")` |
| Backend 400 → propagate as 400 | 400 | `ErrorUtil.errorResponse(400, Constants.HTTP_MSG_BAD_REQUEST, "The body of the request...invalid")` |
| Backend 404 → remap to 500 "Backend URL Not Found" | 500 | `ErrorUtil.errorResponse(500, "Internal Server Error", "Backend URL Not Found")` (EOB pattern) |
| Backend 4xx (not 400/404) | proxy code | `ErrorUtil.errorResponse(statusCode, "Server Error", ex.getResponseBodyAsString())` |
| Backend 5xx | 500 | `ErrorUtil.errorResponse(500, "Server Error", message)` |
| `ResourceAccessException` / connection timeout | 500 or 408 | `ErrorUtil.errorResponse(ex.getCause() instanceof SocketTimeoutException ? 408 : 500, ...)` |
| Not-found message in body (grievances) | 200 | `return emptyResponse()` |

⚠️ **"No record found" → 200 is per-*flow*, not a repo-wide constant — verify against IIB, don't assume.** `EOB-ExplanationOfBenefit` has two different strategies for its own two endpoints: the by-id lookup wraps a `204`-flavored `ErrorStatus` inside an HTTP `200` body, while the list endpoint instead throws a real HTTP `404` via `ErrorUtil.errorResponse(...)`. `GetGrievanceByCriterias` (search-style) does return `200` + empty array for "not found." Check the actual IIB behavior for **that specific endpoint** before picking a strategy — don't apply one convention repo-wide just because a sibling endpoint in the same service uses it (`gap-checklist.md` [R6]).

### No `@RestControllerAdvice` — route errors through `ErrorUtil.errorResponse(...)` directly (Rule 10)

> **Decision (2026-10-01), made against unusually strong contrary evidence — read this before "fixing" it back.** A `@RestControllerAdvice`-based exception handler (`ApiExceptionHandler`/`*ExceptionHandler`) catching only Spring/framework exception types (`NoResourceFoundException`, `EIPMicroserviceException`, `HttpMessageNotReadableException` — never a custom project-local type) is in fact the **dominant real convention**: confirmed present in 11 real repos across both phases and multiple backend types (`GetGrievanceByCriterias`, `rankedMatch`, `DMRClaimStatus`, `EOB-ExplanationOfBenefit`, `GetGrievanceSummary`, `memberseobpreferences`, `BenService-GetBeneficiaries`, `cumulativeCopaySummaries`, `RET-Pharmacy-Checkinimzprescriptions`, plus `EmergencyOverrides`'s own real sibling). Despite that, the user explicitly chose — twice, after being shown the 11-repo count — the stricter rule: **no `exception` package or `@RestControllerAdvice` at all, for any backend type.** This is a deliberate departure from the dominant real-repo pattern, not an oversight. Don't "correct" this file back toward the 11-repo convention based on reading the training repos again; the evidence was already weighed and the decision stands.

Generate the equivalent behavior inline instead — call `ErrorUtil.errorResponse(...)` directly at the point of failure (service/connector layer), and for the generic "no matching route" 404 case specifically, rely on Spring Boot's own default `NoResourceFoundException` → 404 handling rather than a custom advice class:

```java
// ❌ AVOID — no @RestControllerAdvice, no exception package, for any backend type:
@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(EIPMicroserviceException.class)
    public ResponseEntity<Object> handleEipException(EIPMicroserviceException ex) { ... }
}

// ✅ REQUIRED — call the framework's mechanism directly where the failure happens:
if (response.getStatusCode().is5xxServerError()) {
    ErrorUtil.errorResponse(HttpStatus.INTERNAL_SERVER_ERROR.value(), Constants.ERROR_BACKEND, detail);
}
```

---

## 5a. API interface & controller — CONTEXT_ROOT and EIE headers

```java
@RequestMapping(value = "${CONTEXT_ROOT:/pbmclaim/api/v1/cs/{service-name}}")
public interface {ServiceName}Api {
    @GetMapping(value = "/members/{id}/{sub-resource}", produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<Object> get{ServiceName}(
            @PathVariable String id,
            @RequestParam String startDate,
            @RequestParam String endDate,
            @RequestHeader(value = "Cvs-Consumer-Correlation-Id", required = false) String cvsConsumerCorrelationId);
}
```

- **CONTEXT_ROOT derivation**: take the IIB HTTP Input node's URL path and replace the `/edb/` segment with `/cs/` — e.g. `/pbmclaim/api/v1/edb/dmrclaimstatus` → `${CONTEXT_ROOT:/pbmclaim/api/v1/cs/dmrclaimstatus}` (see `translation-table.md`).
- **`Cvs-Consumer-Correlation-Id` is the only EIE-adjacent `@RequestHeader` on the interface.** `X-Apitransactionid`, `Testharness`, `X-Eiecorrelationid`, `X-Global-Transaction-Id` are handled entirely by the framework's `EieHeader` bean — **never** expose them as controller or interface parameters (`gap-checklist.md` [R11]), and never set them manually as response headers in the controller either — the framework handles correlation headers automatically (`gap-checklist.md` [R12]).
- Controller is zero-logic, delegates straight to the service — same as every other backend type.
- Service methods take only path/query params, never a `testHarness`/`apiTransactionId`-style parameter directly (`gap-checklist.md` [R15]) — resolve those from the injected `EieHeader` bean inside the service.

---

## 6. application.properties keys template

### Baseline (application-local.properties, all REST services)

```properties
server.port=${PORT:8081}
spring.main.banner-mode=off
server.servlet.context-path=/
server.tomcat.relaxed-path-chars=^
application.jackson.serializationInclusion=ALWAYS
application.jackson.enableLegacyErrorStructure=true
management.endpoints.web.exposure.include=health,info
application.circuitbreaker.failureRateThreshold=90.0
application.circuitbreaker.slowCallDurationThreshold=8s
application.circuitbreaker.slowCallRateThreshold=80.0
application.circuitbreaker.writableStackTraceEnabled=false
logging.level.com.cvshealth.eip.microservices=DEBUG
logging.level.org.springframework=WARN
application.http.enabled=true
application.http.close-idle-connection-wait-time-secs=30
```
Note the `logging.level.*` keys above — these ARE fine as ordinary Spring Boot logging config; the correction in §0 is specifically that no `logback-spring.xml` *file* is expected, and no elaborate `logging.level.*` block beyond framework defaults is required as a hard rule (some services have none at all, per confirmed training evidence) — don't treat the two as contradictory.

**Backend URI, by connector target (Rule 12):** if this REST connector talks to an **internal CVS backend** (e.g. a REST-shaped refdata/DB call), its `application.http.restclient.<name>.uri` in `application-local.properties` must point at the `qa`/`qa2` CVS hostname, same as every other backend type — never `dev`/`prod`. If it talks to an **external vendor** (MedForce, Precisely, Anthem, IngenioRx, etc.), there's no CVS "QA" equivalent — use that vendor's own non-prod/staging endpoint instead, exactly as the IIB source's own non-prod environment file shows it (confirmed real examples: `DMRClaimStatus`'s local profile points at MedForce's own test host; `rankedMatch`'s points at `token-amer-staging.spectrum.precisely.com`, the vendor's staging domain, not a CVS one).

### Baseline, per-environment config-map variant (confirmed real, distinct from the application-local.properties baseline above)

Each `config/config-map/application.<env>-<cluster>.properties` file gets its **own** copy of most of the baseline block above, plus two things application-local.properties doesn't have — swap `logging.level.*` for `logging.config`, and add HTTP header-size limits:

```properties
server.port=${PORT:8080}
spring.main.banner-mode=off
server.servlet.context-path=/
server.tomcat.relaxed-path-chars=^
application.jackson.serializationInclusion=ALWAYS
application.jackson.enableLegacyErrorStructure=true
logging.config=classpath:logback-<env>.xml
server.max-http-request-header-size=128KB
server.tomcat.max-http-response-header-size=128KB
server.tomcat.max-http-header-count=200

#CIRCUIT BREAKER PROPERTIES
application.circuitbreaker.failureRateThreshold=90.0
application.circuitbreaker.slowCallDurationThreshold=8s
application.circuitbreaker.slowCallRateThreshold=80.0
application.circuitbreaker.writableStackTraceEnabled=false
```
`<env>` is the tier, not the full env-cluster suffix (`logback-dev.xml` for `dev-pbmaz`/`dev2-pbmaz`/`dev3-pbmaz`, `logback-qa.xml` for every `qa*-pbmaz`, `logback-prod.xml` for `prod-pbmaz`/`prod-pbmri`, etc. — one shared file per tier, not one per config-map file). Don't skip this block just because it looks redundant with application-local.properties — the real convention duplicates it per environment rather than relying on the local-profile file at deploy time.

### Circuit breaker — the Java-side consumption pattern (not just properties)

**[single-repo evidence, `RankedMatch` only — N=1, not yet cross-checked against other services]** The `application.circuitbreaker.*` keys above aren't passive config the framework handles invisibly — the service layer has to actually detect a tripped breaker and respond to it, or it falls through to a generic 500. The confirmed real pattern, in the connector call's `try/catch`:

```java
try {
    // ... RestClientWrapper.executeRestCall(...) or equivalent connector call ...

} catch (ResourceAccessException rae) {
    // connection-level timeout/network failure
    raiseTimeoutError(rae);
    return null;

} catch (EIPMicroserviceException eme) {
    if (eme.isCircuitBreakerError()) {
        // breaker tripped (per application.circuitbreaker.failureRateThreshold/slowCallRateThreshold) —
        // treated as a timeout, NOT a generic 500
        raiseTimeoutError(eme);
        return null;
    }
    // any other EIPMicroserviceException -> fall through to 500
    return raiseInternalServerError(..., eme);

} catch (Exception e) {
    return raiseInternalServerError(..., e);
}

private void raiseTimeoutError(Exception cause) {
    ErrorUtil.errorResponse(HttpStatus.REQUEST_TIMEOUT.value(), HTTP_MSG_TIMEOUT,
            ERR_SERVICE_TIMEOUT_PREFIX + correlationId + "]");
}
```

Key points, all directly from `RankedMatchService.java`/`RankedMatchServiceTest.java`:
- `EIPMicroserviceException` has a real `isCircuitBreakerError()` accessor (framework-provided, javap-verifiable — don't invent a different name by analogy) — check it **before** falling through to the generic-exception branch, since an open-breaker exception is itself an `EIPMicroserviceException`, not a distinct exception type.
- A circuit-breaker trip is routed through the **same** `raiseTimeoutError` path as a real `ResourceAccessException` timeout — it returns HTTP 408 with the service's standard timeout message, not a 500. This mirrors the existing 408-timeout-handling checklist item (`common-patterns-and-gotchas.md`/gap-checklist "408 timeout reproduced") — a circuit-breaker branch is part of satisfying that item correctly, not a separate concern.
- Covered by unit tests mocking `eme.isCircuitBreakerError()` returning both `true` and `false` to hit each branch — reproduce that test shape rather than skipping breaker-path coverage.

Since this is N=1, don't treat the exact catch-block ordering/variable names as universal — but do treat "an open circuit breaker must map to a deliberate response, not silently become a generic 500" as the real requirement any REST (and likely other backend-type) service should satisfy once `application.circuitbreaker.*` properties are present. Flag as a GAP if a generated service has the properties but no corresponding `isCircuitBreakerError()` branch.

### Standard REST connector keys

```properties
application.http.enabled=true
application.http.restclient.<clientName>.name=<clientName>
application.http.restclient.<clientName>.uri=<backend URL from IIB *.properties>
application.http.restclient.<clientName>.connect-timeout=<IIB connectTimeout * 1000>
application.http.restclient.<clientName>.request-timeout=<IIB timeoutForServer * 1000>
application.http.restclient.<clientName>.socket-timeout=<IIB timeoutForServer * 1000>
application.http.restclient.<clientName>.max-total-connections=50
application.http.restclient.<clientName>.default-max-per-route=25
application.http.restclient.<clientName>.default-keep-alive-time-millis=20000
application.http.restclient.<clientName>.test-harness-host=http://esl-wiremock.corp.cvscaremark.com:9000
application.http.restclient.<clientName>.test-harness-url=<full test URL from IIB testHarnessURL>
```

### OAuth2 keys (for OAuth services like GetGrievancesAppeal)

```properties
application.oauth.enabled=true
application.oauth.client.<provider>.uri=<token URL>
application.oauth.client.<provider>.basic-auth=true
application.oauth.client.<provider>.content-type=application/x-www-form-urlencoded
application.oauth.client.<provider>.fixed-delay-in-minutes=13
application.oauth.client.<provider>.query-param-map.grant_type=client_credentials
application.oauth.client.<provider>.query-param-map.scope=public
```

⚠️ **Secrets:** IIB `.properties` files commit credentials in plaintext. Encrypt every credential via the CVS encrypt endpoint by default (`mandatory-migration-rules.md` Rule 6) — this applies to REST services in both phases. **If this specific service is determined to require Akeyless** (checked against its real sibling repo or confirmed with the team — REST-to-external-vendor services are the only category where this comes up, but not even all of those need it; `RET-Pharmacy-Checkinimzprescriptions` is a confirmed real counter-example), generate the live scaffolding using Form 1 or Form 2 from Rule 6, not a disabled placeholder. Never commit actual plaintext credential values to the MS repo.

---

## 7. Maven dependencies

```xml
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-restclient</artifactId>
</dependency>
<!-- Only for OAuth2 services: -->
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-oauth</artifactId>
</dependency>
<dependency>
    <groupId>org.apache.commons</groupId>
    <artifactId>commons-lang3</artifactId>
</dependency>
```

---

## 8. Standard package layout

```
com.cvshealth.eip.microservices
├── controller.*Controller           // REST entry, thin delegate — no error-response calls, no inline mapping (Rule 9/10)
├── service.*Service                 // orchestrate validate → connector → map; ErrorUtil.errorResponse(...) calls live here
├── connector.*ConnectorService      // RestClientWrapper.executeRestCall, catch blocks
├── connector.*TokenService          // OAuth2 token acquisition (if applicable)
├── util.*ConnectorUtil              // buildRestRequest, processResponse, isNotFoundMessage
├── util.*Util                       // cross-cutting: test-harness, transactionId, TLS config
├── constant.Constants               // header names, error messages, not-found strings
├── constant.*ConnectorConstants     // provider-specific property keys, log messages
├── model.request.*                  // inbound + outbound request DTOs (records, @JsonIgnoreProperties)
└── model.response.*                 // response DTOs (records, @JsonInclude(NON_NULL))
```
No `exception` package — see Rule 10. The 11 real repos that have one (cited above) are the dominant pattern but not the target for new generation; route every error through `ErrorUtil.errorResponse(...)` inline instead.
