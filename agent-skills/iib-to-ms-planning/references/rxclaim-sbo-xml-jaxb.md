# RxClaim SBO (XML/JAXB) — Migration Reference

**Training services:** carriersearchservice (GETCARID), memberidcardprofile (GETIDCARDDETAIL), messagecodeservice (GETMSGCODEDTLS), EmergencyOverrides (real sibling repo diffed directly against a generated migration, 2026-10-01)

## 0. Evidence-tagged corrections and open questions

- **Five concrete generation defects found by diffing a generated `EmergencyOverrides` migration against its real sibling repo (2026-10-01) — now fixed in this file and in `mandatory-migration-rules.md`:** (1) a Kong token-validator connector/service/config was generated for an ESQL ID-token-check node that Kong actually handles upstream — never generate it (**Rule 9**); (2) the controller had inline try/catch and response-mapping logic instead of delegating only; (3) the connector's `function=` string joined two constants with `"_"` — the real sibling uses `"."` (§3); (4) a custom `ValidationException`/`TokenValidationFailedException` pair and an `exception` package were generated — never define a custom exception type or package (**Rule 10**); (5) the backend-only XML/JAXB models were nested under `swagger.model.xml` instead of a top-level `model.xml` package sibling to `swagger/` (§8). Items 3-5 were cross-checked against `memberidcardprofile`'s real repo, which disagrees on all three — see the inline notes in §3 and §8 for the standing decision in each case and what the older, non-target shape looks like.

- **Requester-ID resolution is genuinely per-service in this category — not a clean "RxClaim SBO always passes the JWT context through directly" rule.** A sibling migration agent's spec claimed both checked repos use the JWT-populated `eieUserContext` string as `requesterID` directly with no `accountIdentifierValue` extraction — **verified against all three actual repos, this does not hold**:
  - `carriersearchservice.CarrierSearchService.resolveRequesterID()` — passes the raw `eieHeader.getEieUserContext()` string straight through, no extraction. (matches the "direct passthrough" claim)
  - `memberidcardprofile.EieHeaderExtractor.extractUserID()` — **does** substring-extract `accountIdentifierValue` out of the context string (`indexOf("accountIdentifierValue\":\"")...`), explicitly commented as mirroring the IIB ESQL's own `SUBSTRING(...AFTER...BEFORE...)` pattern. (contradicts the "direct passthrough" claim — matches the QL/CICS-style extraction instead)
  - `messagecodeservice` — doesn't resolve a requester ID from `EieHeader` at all; `requesterID` is a plain field on the inbound request DTO (`MessageCodeRequest.requesterID`). (a third pattern, neither of the above)

  **Lesson**: check the *specific* service's own ESQL and any sibling implementation before assuming either the QL/CICS-style extraction or a direct passthrough — this category doesn't have one dominant convention, at least not at n=3. Both `resolveRequesterID()`-direct-passthrough and `EieHeaderExtractor.extractUserID()`-substring-extraction are real, valid patterns seen in this category; which one applies depends on what the target flow's IIB ESQL actually does with the header. This is a good example of why every "N/M confirmed" claim — including this skill's own — needs re-verification rather than being taken as settled once N reaches "most of them."
- **`ErrorUtil` — usually the FRAMEWORK's class, not a custom service-level one.** Both `carriersearchservice` and `messagecodeservice` import `com.cvshealth.eip.microservices.frameworks.utilities.ErrorUtil` directly. A more elaborate custom service-level `ErrorUtil` (regex XML parsing, `BIP2328E` handling, `ECSError` pattern matching) is a real, working pattern but appears specific to flows like `memberidcardprofile` that actually surface raw-XML ECS-error responses needing that handling. **Default to the framework's `ErrorUtil` for new RxClaim SBO work; only build the custom regex-parsing version if the target flow's IIB ESQL actually surfaces that same class of raw-XML error response** — check the ESQL first, don't copy the custom class by default.
- **AES encryption is logging-only, consistently** — `AESEncryption.encrypt()` is used only inside `log.debug(...)` calls for request/response XML. The actual XML sent over the wire via the connector is plaintext. No change needed here; this is settled.
- **Records vs. Lombok — Lombok-dominant, records only for narrow error types.** `carriersearchservice` is 100% Lombok, no records anywhere. `messagecodeservice` is mostly Lombok but has 3 narrow error-related classes (`FaultErrorResponse`, `FaultInformation`, `MessageCodeError`) as records. Default RxClaim SBO models to Lombok `@Builder`/`@Data` — records are acceptable for small immutable error-payload classes but aren't the default here, **unlike REST and QL_CICS where records are the default for every response model** (`gap-checklist.md` [R14], `structural-conventions.md` §7). Don't cross-apply the REST/QL_CICS records convention into this category by default.
- **Package layout can deviate significantly from a "documented ideal" layout — check the specific repo, don't assume.** `messagecodeservice` matches a layered layout closely (`connector/`, `converter/`, `mapper/`, `util/`). `carriersearchservice` deviates heavily: no `connector/`, `converter/`, `mapper/`, or `validator/` packages at all — everything lives flat under `service/` (`RxClaimService`, `CarrierSearchXmlConverterService`, `CarrierSearchMapperService`, `CarrierSearchService`). The layered layout in §8 below is the *preferred* target for new generation, but don't assume an existing repo not following it is wrong.
- **`framework-starter` version drift observed, informational only** — versions seen across this category: `carriersearchservice` 3.1.3, `messagecodeservice` 3.1.5. Reference repos lag/lead whatever the current spec version is; this is not a reason to change a user-specified version (Rule 7) — always defer to the user's explicit spec over what a reference repo happens to pin.
- **Constants naming confirmed `{ServiceName}Constants`** across this category — no change needed, unlike the QL/CICS category where naming is inconsistent (see `ql-cics-camel-bindy.md` §0).

### 0a. `cumulativeCopaySummaries` live-migration findings (2026-09-29/30)

Migrated blind, then diffed file-by-file against the pre-existing human-written repo and **executed against the real QA RxClaim backend**. 57 files vs 57 files, no missing/extra files; all Java code functionally identical (comments/import-order/line-wrapping only); `{Service}Constants` identical at 54/54 names *and* values. Two real gaps surfaced, both **general, not RxClaim-specific** — now also enforced as `gap-checklist.md` **[M16]** and **[M17]**:

1. **`application.properties` vs `application-local.properties` were inverted.** The convention is a *tiny* `application.properties` and a *large* `application-local.properties` — **confirmed 20/20 across every repo in the workspace, every backend type**: `application.properties` is 1-7 lines (`spring.profiles.active=local` + `spring.mvc.throw-exception-if-no-handler-found=true` + `spring.web.resources.add-mappings=false`), while `application-local.properties` carries 16-97 lines (server/jackson/general block, circuit breaker, the `application.http.restclient.<connector>.*` block, and the ACTUATOR block). Generating it the other way round still *runs* locally, so nothing fails — it just silently diverges from every real repo. Where the ACTUATOR block appears in a packaged file at all (8/20 repos), it is **always** in `application-local.properties`, never in `application.properties`.
2. **Copying the reference repo's pom imported a dead dependency.** The reference declared `commons-lang3` *and* carried `import org.apache.commons.lang3.StringUtils;` with **zero call sites** — a dead import plus a dead dependency. The generated code correctly dropped the unused import but kept the pom entry, which turns the reference's latent dead dependency into a live **Rule 2** violation in new output. Re-verify **every** copied pom dependency against *your own* generated code's imports, not the reference's.

**RxClaim-SBO-specific runtime behaviour confirmed by live execution** (useful for the next service in this category):

- **Response shape:** a fixed-width control prefix precedes the payload, e.g. `102154         NRML    00000000{...json...}`. The **status token lives in that prefix** (`NRML` ok, `EDTR`/`FAIL` error), which is the HTTP-era stand-in for IIB's `MQRFH2.usr.status`. Scan **only the prefix** for `EDTR`/`FAIL`; scanning the whole body false-positives on ordinary data (a real response had `"addUser":"FAIL0001  "`, which routed a success payload into the XML error parser and produced `Content is not allowed in prolog.`).
- **Application errors arrive inside an HTTP 200.** The SBO transport returns `200 OK` while the business failure sits in the JSON body as `Error_Code[0].{Code,Description}` — confirmed live: `0555 "Data Not Found"` and `3378 "CAGM not found"`. Never treat HTTP 200 as success for this connector.
- **Numeric formatting is a genuine IIB-parity risk.** The backend sends `"accumulativeAmount":150.00`; IIB passed the backend JSON through raw, so IIB emitted `150.00`. A Java `Double` field re-serializes as `150.0`. The deployed MS also emits `150.0`, so MS-to-MS parity holds — but **MS-to-IIB byte parity does not**. If a consumer string-compares money fields, use `BigDecimal` with explicit scale; otherwise flag it rather than silently changing the type.
- **Path-parameter encoding:** single-encode (`%7E` for `~`, `%5E%5E` for `^^`). **Double-encoding (`%257E`) returns HTTP 400** `Invalid id format` — the service receives the literal text `%7E`, finds no `~`, and fails ID validation. Requires `server.tomcat.relaxed-path-chars=^` plus `application.general.encodedSolidusHandling=passthrough` in the config (both already in the baseline block).
- **Getting real data for a smoke test:** dev and QA backends hold different member data — the same member returned `0555 Data Not Found` against dev but a populated summary against QA. If dev has no data, run the built jar with `--application.http.restclient.<connector>.uri=<QA URI>` rather than concluding the mapping is broken.

---

## 1. Structural mapping template

| IIB module | Java class | Notes |
|---|---|---|
| `*_DIMToBackEnd` (validate + build outbound XML) | `*Service.process*()` + `*Mapper.mapToXmlRequest()` + `*XmlConverter.marshalToXml()` | Validation first, then JAXB marshal |
| `SetMQHeaders` / MQRFH2 plumbing | Absorbed by `framework-connector-rxclaimsbo` (`RxClaimConnector`) | No MQ code in Java |
| MQ Output/Input node pair (`QL.ESL.PBM.RXCLAIM.REQUEST`) | `*Connector.invokeRxClaim()` → `RxClaimConnector.invokeRxClaimConnector(RxClaimRequest)` | Framework handles HTTP |
| Envelope stripping (`SUBSTRING(... FROM 32)`) | `*XmlConverter.cleanXmlResponse()` → `responseBody.indexOf('<')` | Strips RFH2/status prefix |
| `*_BackEndToDIM` (parse XML response → JSON) | `*XmlConverter.unmarshalFromXml()` + `*Mapper.mapToApiResponse()` | JAXB unmarshal, then field map |
| FAIL/EDTR check + ECSError parsing | `*Service.isBackendErrorResponse()` / `containsError()` + `*XmlConverter` regex patterns | String.contains check |
| `ComposeESLError` | `ErrorUtil.errorResponse(...)` (framework) | |
| Timeout handler | `ErrorUtil.errorResponse(408, "TimeOut Error", ...)` — **may be missing** in some services, flag as gap | |

---

## 2. XML request building

### Namespace constant class

```java
public class XmlNamespace {
    public static final String ECS_SERVICE = "http://service.ecs.caremark.com";
    // Some request roots carry no namespace (e.g. memberidcardprofile) — check ESQL carefully:
    // If ESQL uses `DECLARE ns NAMESPACE 'http://...'` but does NOT prefix the root element,
    // the @XmlRootElement must have NO namespace= attribute.
    // If ESQL uses `ns:rootElement`, add namespace = XmlNamespace.ECS_SERVICE.
}
```

### JAXB request model template

```java
@XmlRootElement(name = "get*Request")           // from ESQL: CREATE LASTCHILD ... NAME 'get*Request'
@XmlAccessorType(XmlAccessType.FIELD)
public class Get*Request implements Serializable {
    // One @XmlElement per ESQL "SET outRef.fieldName = value"
    @XmlElement(name = "fieldName")              // no namespace if request is un-namespaced
    private String fieldName;
    ...
}
```

### ESQL → JAXB field mapping rules

| ESQL `SET outRef.ecs:fieldName = value` | Java `@XmlElement(name="fieldName", namespace=XmlNamespace.ECS_SERVICE)` |
|---|---|
| `DECLARE ecs NAMESPACE 'http://service.ecs.caremark.com'` | `namespace = XmlNamespace.ECS_SERVICE` on **response** elements; check if request uses namespace |
| `COALESCE(value, '')` | `Optional.ofNullable(v).orElse("")` in mapper |
| `TRANSLATE(value, ' ')` (strip spaces) | `.replace(" ", "")` in mapper |
| `CAST(value AS INTEGER)` | `Integer.parseInt(trimField(v))` in mapper |
| `'rxclaim'` literal | `XmlConverterConstants.EIS_NAME = "rxclaim"` constant |
| `applicationShortName` from `X-Eieapplication` header | `EieHeaderExtractor.extractApplication()` parsing `applicationShortName` JSON key |

### Marshalling

```java
// Always use JAXB_FRAGMENT=true to suppress <?xml ?> prolog
JAXBContext ctx = JAXBContext.newInstance(Get*Request.class);
Marshaller m = ctx.createMarshaller();
m.setProperty(Marshaller.JAXB_FORMATTED_OUTPUT, false);
m.setProperty(Marshaller.JAXB_FRAGMENT, true);
StringWriter sw = new StringWriter();
m.marshal(request, sw);
return sw.toString();
```

---

## 3. RxClaim Connector invocation

### `RxClaimRequest` building

```java
public RxClaimRequest createRxClaimRequest(String messageBody) {
    RxClaimRequest request = new RxClaimRequest();
    request.setTransactionCode(Constants.TRANSACTION_CODE);    // e.g. "GETCARID","GETIDCARDDETAIL","GETMSGCODEDTLS"
    request.setActionCode(Constants.ACTION_CODE);              // always "REQ"
    request.setResponseTimeoutMillis(Constants.TIMEOUT_MILLIS); // hardcoded constant, typically 30000-55000
    request.setRequestBody(messageBody);
    return request;
}
```

⚠️ **Key insight:** `TRANSACTION_CODE` and `ACTION_CODE` are **hardcoded Java constants**, not properties. Derive them from:
- IIB `MQRFH2.usr.OperationName` → becomes `TRANSACTION_CODE` (e.g. `'getCarrierById'` → `"GETCARID"`)
- Action code is always `"REQ"` for all RxClaim services

⚠️ **Timeout discrepancy pattern:** `RxClaimRequest.setResponseTimeoutMillis()` is often hardcoded (e.g. 30000ms) while `application.http.restclient.rxclaim.request-timeout` is set higher (e.g. 59000ms) in properties. This is a known inconsistency — preserve both values as-is from the reference MS service.

### `*Connector` class

```java
@Component
public class *Connector {
    @Autowired private RxClaimConnector rxClaimConnector;  // framework bean

    @EIPMicroserviceProviderCall(
        provider = EIPMicroserviceProvider.RXCLAIM_SBO,
        function = Constants.TRANSACTION_CODE + "." + Constants.ACTION_CODE)
    public ResponseEntity<RxClaimResponse> invokeRxClaim(String xmlBody) {
        return rxClaimConnector.invokeRxClaimConnector(createRxClaimRequest(xmlBody));
    }
}
```

⚠️ **Separator decision (2026-10-01, re-checked against all 7 real RxClaim SBO repos) — "." stays the standard, even though it's not actually the majority.** Checking the *final string value*, not just the Java join syntax, across every real repo in this category:

| Repo | Final `function=` value | Separator |
|---|---|---|
| `carriersearchservice` | `"GETCARRIERBYID_" + "REQ"` | `_` |
| `memberidcardprofile` | `TRANSACTION_CODE + "_" + ACTION_CODE` | `_` |
| `memberseobpreferences` | `FUNCTION_NAME("..._") + ACTION_CODE` → `GETEOBHISTBYEPH_REQ` | `_` |
| `RET-Pharmacy-Checkinimzprescriptions` | `TRANSACTION_CODE + "_" + ACTION_CODE` | `_` |
| `messagecodeservice` | `"GETMSGCODEDTLS." + ACTION_CODE` | `.` |
| `EmergencyOverrides` (real sibling) | `FUNCTION_NAME + "." + ACTION_CODE` | `.` |
| `cumulativeCopaySummaries` | `TRANSACTION_CODE + "." + ACTION_CODE` | `.` |

`"_"` is actually the slight majority (4/7), not `"."` (3/7) — the opposite of what a first 1-vs-1 comparison (`EmergencyOverrides` vs. `memberidcardprofile`) suggested. **Standing decision despite that: generate `"."` anyway.** This value most likely only affects the framework's own internal call-tracking/metrics tagging, not the actual backend transaction (which is driven by `setTransactionCode(...)`/`setActionCode(...)` on `RxClaimRequest` directly, separately from this annotation argument) — so it isn't a backend-connectivity risk either way, consistent with this migration's priority of a working call over this kind of cosmetic precision (see Rule 10's rationale note for the same "defer polish" philosophy). If a specific target service's own real sibling repo exists, match that sibling's actual value instead of this default. **QL/CICS is unaffected and stays consistent** — the only two real QL/CICS repos that join two parts (`orderimageinfoservice`, `Comments`) both use `"."`, no split there. REST/OAuth/vendor connectors (DMRClaimStatus, GetGrievancesAppeal, rankedMatch, EOB-ExplanationOfBenefit) don't have this `TRANSACTION_CODE`+`ACTION_CODE`-join pattern at all — their function names are standalone descriptive strings, so this separator question doesn't apply to that backend type.

---

## 4. Response parsing

### Envelope stripping

IIB: `SUBSTRING(CAST(InputRoot.BLOB.BLOB AS CHAR CCSID 1208) FROM 32)` — strips 31-byte status prefix  
Java equivalent:
```java
public String cleanXmlResponse(String responseBody) {
    int xmlStart = responseBody.indexOf('<');
    String cleaned = xmlStart > 0 ? responseBody.substring(xmlStart) : responseBody;
    return cleaned.replace(" xmlns= \"", " xmlns=\"");  // fix malformed namespace in some backends
}
```

### JAXB response model

```java
@XmlRootElement(name = "get*Reply", namespace = XmlNamespace.ECS_SERVICE)
@XmlAccessorType(XmlAccessType.FIELD)
public class Get*Response implements Serializable {
    @XmlElement(name = "fieldName", namespace = XmlNamespace.ECS_SERVICE)
    private String fieldName;

    // Repeated elements:
    @XmlElement(name = "itemName", namespace = XmlNamespace.ECS_SERVICE)
    private List<ItemType> items;

    // Error element:
    @XmlElement(name = "ecsResponseError", namespace = XmlNamespace.ECS_SERVICE)
    private EcsResponseError ecsResponseError;
}
```

### IIB → Java response field mapping

| ESQL path | Java field | Transform |
|---|---|---|
| `inRef.ecs:replyRoot.ecs:field` | `Response.field` | direct |
| `CAST(field AS INTEGER)` | `Integer.parseInt(trimField(raw))` | |
| `moreRecordsAvailable = 'Y'` | `"Y".equalsIgnoreCase(xmlResponse.getMoreRecordsAvailable())` | |
| `SUBSTRING(CAST(... FROM 32))` (envelope strip) | `cleanXmlResponse(body)` | see above |

### Error detection regex patterns

```java
// In *XmlConverter or *Service:
private static final Pattern ECS_ANY_ERROR_PATTERN =
    Pattern.compile("<(?:[a-zA-Z0-9]+:)?ECS(?:Response)?Error[\\s>/]");
private static final Pattern MSG_ID_PATTERN =
    Pattern.compile("<(?:[a-zA-Z0-9]+:)?(?:MessageID|messageID)>([^<]*)</");
private static final Pattern MSG_TEXT_PATTERN =
    Pattern.compile("<(?:[a-zA-Z0-9]+:)?(?:MessageText|messageText|message)>([^<]*)</");
private static final Pattern SHORT_TEXT_PATTERN =
    Pattern.compile("<(?:[a-zA-Z0-9]+:)?(?:ShortText|shortText)>([^<]*)</");

public boolean isBackendErrorResponse(String body) {
    return body.contains(TAG_EDTR) || body.contains(TAG_FAIL)
        || ECS_ANY_ERROR_PATTERN.matcher(body).find();
}
```

⚠️ **Known gap (IIB vs MS):** IIB differentiates `ECSError` (→ 404) from `ECSResponseError` (→ 500) by XML element name. Some MS implementations collapse both to 404. Check and flag this if behavioral parity with IIB is required.

---

## 5. Error handling

| Scenario | HTTP code | Java pattern |
|---|---|---|
| Input validation failure (missing/blank field) | 400 | `ErrorUtil.errorResponse(400, "Bad Request", detail)` |
| Backend error (`FAIL`/`EDTR`/`ECSError`) → not-found type | 404 | `ErrorUtil.errorResponse(404, "Not Found", "[9|" + msgId + "|" + shortTxt + "]")` |
| Backend error → hard failure | 500 | `ErrorUtil.errorResponse(500, "Internal Service Error", moreInfo)` |
| Empty/null response | 500 | `ErrorUtil.errorResponse(500, "Internal Server Error", "Error in RxClaim SBO")` |
| Timeout | **408** | `ErrorUtil.errorResponse(408, "TimeOut Error", "TIME OUT ERROR FROM EDB Service TransactionID:" + txId)` |

Error response shape (both IIB and MS): `{ "httpCode": "...", "httpMessage": "...", "moreInformation": "..." }`  
Java DTO: `ErrorStatus(String httpCode, String httpMessage, String moreInformation)`

---

## 6. application.properties keys template

**`<env>` below = `qa` (or `qa2`) when generating `application-local.properties` — never `dev`/`prod` (Rule 12). For a per-environment config-map file, `<env>` is that file's own actual tier instead.**

```properties
# RxClaim SBO connector (from IIB RXCLAIM queue config)
application.http.enabled=true
application.http.restclient.rxclaim.name=rxclaim
application.http.restclient.rxclaim.uri=https://<env>eippbm-oci.cvshealth.com/rxclaim/request/v1
# ^ plain "pbm-oci" host, never "pbmaz"/"pbmri" — standing instruction, 2026-10-23 (see Rule 12)
application.http.restclient.rxclaim.connect-timeout=10000
application.http.restclient.rxclaim.request-timeout=<IIB timeoutForClient * 1000>
application.http.restclient.rxclaim.socket-timeout=<IIB timeoutForClient * 1000>
application.http.restclient.rxclaim.max-total-connections=50
application.http.restclient.rxclaim.default-max-per-route=25
application.http.restclient.rxclaim.default-keep-alive-time-millis=20000
application.http.restclient.rxclaim.test-harness-host=https://esl-wiremock.corp.cvscaremark.com:9080
application.http.close-idle-connection-wait-time-secs=30

# NOTE (corrected — see Rule 8 in mandatory-migration-rules.md): whether an
# application.eieusercontext.enabled=true key + a framework-eieusercontext dependency
# are needed is category- and service-dependent, NOT universally forbidden.
# carriersearchservice and memberidcardprofile (2 of 3 RxClaim SBO training repos)
# both genuinely depend on framework-eieusercontext to populate eieUserContext from
# a raw ID token via EieHeaderBuilder.createEieUserContextString(). Check whether the
# target flow's EieHeader.getEieUserContext() arrives pre-populated (skip this dependency)
# or needs building from getIdToken() first (include it) — don't assume either way.
application.eieusercontext.enabled=true   # include only if EieHeaderBuilder is actually used — see above
```

---

## 7. Maven dependencies

```xml
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-connector-rxclaimsbo</artifactId>
</dependency>
<!-- EieHeader's basic getters come transitively via framework-starter/framework-restclient.
     framework-eieusercontext is ADDITIONALLY needed if this service must populate
     eieUserContext from a raw ID token (EieHeaderBuilder.createEieUserContextString) —
     confirmed genuinely needed in 2 of 3 RxClaim SBO training repos. Check the target
     flow before deciding either way (Rule 8, corrected). -->
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-eieusercontext</artifactId>
    <!-- include only if EieHeaderBuilder.createEieUserContextString() is actually needed -->
</dependency>
<dependency>
    <groupId>org.apache.commons</groupId>
    <artifactId>commons-lang3</artifactId>
</dependency>
<!-- JAXB is transitive via framework-starter — no explicit declaration needed -->
```

---

## 8. Standard package layout

> **Revised 2026-10-01 after a direct real-sibling-repo diff (`EmergencyOverrides`), which also contradicts `memberidcardprofile`'s older real repo on 3 of these 4 points.** Where the two disagree, the layout below is the standing decision — not a claim that `memberidcardprofile`'s shape is wrong, just that it's not the target for new generation. See the inline notes for what `memberidcardprofile` actually does differently.

```
com.cvshealth.eip.microservices
├── controller.*Controller                // implements *Api, delegates to the service ONLY — no try/catch,
│                                          //   no inline mapping/branching logic (see structural-conventions.md §9)
├── service.*Service                      // thin orchestrator: calls *ServiceUtil, nothing else
├── util.*ServiceUtil                     // ALL business logic AND field-presence/type validation live here,
│                                          //   together — not split into a separate validator class/package
├── connector.*Connector                  // builds RxClaimRequest, invokes RxClaimConnector
├── converter.*XmlConverter               // JAXB marshal/unmarshal + cleanXmlResponse
├── mapper.*Mapper                        // JSON request ↔ JAXB model ↔ JSON response
├── constants.*Constants                  // TRANSACTION_CODE, ACTION_CODE, TIMEOUT_MILLIS, namespace, error tags
├── model.                                // backend-call-only DTOs — NOT under swagger/, a separate top-level package
│   ├── *Error / *Context                 // plain backend-related DTOs, if any
│   └── xml.
│       ├── Get*Request                   // @XmlRootElement request
│       ├── Get*Reply                     // @XmlRootElement response
│       ├── XmlNamespace                  // ECS_SERVICE namespace constant
│       └── EcsResponseError              // error element
└── swagger.
    ├── api.*Api                          // the OpenAPI contract interface
    └── model.
        ├── request.*Request              // DTOs for THIS microservice's own REST API only
        └── response.*Response/*Status/…  // ditto
```

**No `validator` package, and no `exception` package — both are deliberate, not omissions:**
- Validation is **not** split into its own class. `EmergencyOverrides`'s real sibling puts a `validateRequest(...)` method directly inside `*ServiceUtil`, alongside the rest of the business logic (`buildContext`, `callBackend`, `mapResult`, etc.) — one fat `@Component` under `util/`, called by one thin `@Service` orchestrator. `memberidcardprofile`'s real repo instead keeps a separate `validator.RequestValidator` class next to a `*Service` that still has real logic in it — that's the older shape; don't generate it for a new service unless that service's own real sibling repo uses it.
- No custom exception class or `exception` package at all — see **Rule 10** in `mandatory-migration-rules.md`. `memberidcardprofile`'s `exception.ApiErrorException` is the older pattern this supersedes; `EmergencyOverrides`'s real sibling only has an `exception/` folder for a generic 404 `@RestControllerAdvice`, and per the standing decision even that form is no longer generated — call `ErrorUtil.errorResponse(...)` directly instead.
- `model.xml.*` is a **top-level package, sibling to `swagger/`** — not nested as `swagger.model.xml`. This mirrors the QL/CICS convention (`structural-conventions.md` §6 already puts `model/bindy/` at the top level, outside `swagger/`) and is what `EmergencyOverrides`'s real sibling does. `memberidcardprofile` nests it as `swagger.model.xml` instead (a third subfolder inside `swagger/model/`, alongside `request/`/`response/`) — again, the older shape, not the target.

**If a target service already has its own real sibling repo in the workspace, match that sibling's actual shape over this default** — this layout is what to generate when no sibling exists to check, consistent with this skill's general evidence-hierarchy principle.
