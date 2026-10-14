# QL/CICS + Camel Bindy — Migration Reference

**Training services:** beneficiarystatusservice (B470), findallergiesanddiagnosis (W050), inductionorderservices (B520), orderimageinfoservice (W605), BenService-GetBeneficiaries (EW36/W373/MT28) — 5 repos surveyed for the corrections below.

## 0. Evidence-tagged corrections and open questions

- **`crlf` on request-buffer `@FixedLengthRecord` classes — 0/3 set it, and it's a real bug fix, not a style preference.** Camel Bindy injects a `\r\n`/`\n` after every marshalled record by default. None of the 3 Bindy-for-request repos surveyed set `crlf = ""` — and the 2 that marshal via real Bindy routes (`inductionorderservices`, `BenService-GetBeneficiaries`) each **independently reinvented the same workaround**: a hand-written `stripTrailingNewline()` (or inline `if (s.endsWith("\r\n"))...`) duplicated 2-3 times per repo. **Set `crlf = ""` explicitly on every `@FixedLengthRecord` used for concatenated buffer output** — fix it at the annotation, don't add another strip helper if you're touching this code (`gap-checklist.md` [H9]).
- **Test-harness bypass: prefer `RestClientWrapper.executeRestCall()` over a hand-rolled `RestTemplate` `@Bean`, but don't treat an existing `RestTemplate` bean as automatically wrong.** Only 1 of 3 checked in depth (`BenService-GetBeneficiaries`) uses the framework path; 2 of 3 (`findallergiesanddiagnosis`, `inductionorderservices`) use a `RestTemplate` `@Bean` in an `OpenApiConfig`/`AppConfig`. This is **not version-gated** — both `framework-starter` versions used across those repos support the framework path, it's just inconsistently adopted. Default new migrations to `RestClientWrapper` per Rule 4 in `mandatory-migration-rules.md` — but if the *specific sibling reference project* for the service family you're migrating already uses a `RestTemplate` bean consistently, don't manufacture an inconsistency by "fixing" only the new service.
- **Bindy-for-request is the majority pattern (2/3) but not universal — a manual `FixedWidthBufferUtil` approach is an accepted, deliberate alternative.** `findallergiesanddiagnosis` explicitly documents in a code comment why it deliberately skipped Bindy for the request buffer: manual field-position construction is "fast, deterministic," with a Bindy model addable later if desired. Prefer Bindy-for-request as the default for new work (declarative, less error-prone once `crlf=""` is set), but don't flag an existing manual approach as a gap on its own if the buffer is simple and well-tested.
- **Requester-ID extraction: prefer `ObjectMapper.readTree()` navigation over a plain `indexOf`/substring scan.** All 3 read the raw, already-populated `EieUserContext` string (none use JWT/JWKS validation for this — see the cross-category takeaway in `common-patterns-and-gotchas.md`). `findallergiesanddiagnosis` does a real JSON parse (`ObjectMapper.readTree()`, navigates `eieusercontext.AccountIdentifier.accountIdentifierValue` with a flat-key fallback) — more robust. `inductionorderservices`/`BenService-GetBeneficiaries` (2/3) do a manual `indexOf("accountIdentifierValue\":\"")` substring scan — confirmed fragile in practice (breaks on whitespace/field-order variance), though it's what most existing repos actually do and is what the live-tested `ValidateBridgeSupply` `Utility` class also uses (see `structural-conventions.md` §11). Prefer the `ObjectMapper` approach for new work; don't treat the substring-scan version as broken if that's the established pattern for the specific service family.
- **Reference data is NOT typically used in this category — 0/5 surveyed repos call a `RefDataConnector` or hardcode a lookup table.** Don't assume a migration needs one just because a translation-table entry mentions `ESLReferenceDataCacheHelper`. **Grep the actual ESQL for an `EXTERNAL NAME`/reference-data call before wiring one** (`gap-checklist.md` [H10]/`translation-table.md`). Exception: `ValidateBridgeSupply` (a testing-set repo, live-verified, not part of this training count) *does* use a live `ReferenceDataService`/`RefDataConnector` for a code→description lookup even though its own IIB ESQL hardcodes the same table via a `CASE` statement — see `structural-conventions.md` §8 for that specific, confirmed pattern. The two findings aren't contradictory: check the *target* flow's actual need, don't default either way from this category-level statistic alone.
- **Constants class naming has no universal name — don't hard-fail a gap check on the literal name `AppConstants`.** Only 1 of 5 surveyed repos (`findallergiesanddiagnosis`) uses it. The others use purpose-named classes (`QLConstants`, `+ ValidationConstants` in one case) — and the live-tested `ValidateBridgeSupply` splits into `Constants`/`ErrorConstants`/`QLResponseConstants` by concern. Check for a single, consistently-organized literals-holding scheme per service; `AppConstants` is a fine default suggestion for a brand-new service, not a hard requirement (`gap-checklist.md` [M3]).
- **Bindy model package location is split — lean `swagger/model/bindy/` for new work, but `model/bindy/` (top-level, no `swagger` prefix) is an accepted alternative.** 2 of 3 nest under `swagger/model/bindy/`; `findallergiesanddiagnosis` and `orderimageinfoservice` use top-level `model/bindy/` (also confirmed live for `ValidateBridgeSupply` — see `structural-conventions.md` §5).
- **Consistent, no change needed**: config-map file convention (`application.{env}-{cluster}.properties`, dots not hyphens, outside `src/main/resources`); the connector class always wraps `QlConnector.invokeQLConnector(...)`, named `{ServiceName}QLConnector` or a close variant.

---

## 1. Structural mapping template

| IIB module | Java class | Notes |
|---|---|---|
| `*_DIMToCanonical` / `*_DIMToBackend` | `*Service.process*(...)` + `*RequestBufferBuilder.build*()` | Request validation + fixed-width buffer construction |
| MQ Output/Input node pair | `*QLConnector.invoke*()` → `QLMessagingService.sendAndReceive()` | Framework `QlConnector.invokeQLConnector(QLRequest)` replaces MQ |
| `*_CanonicalToDIM` / `*_BackendToDIM` | `*QLResponseBufferParser.parse*()` + Bindy models | Fixed-width response parsing |
| `*_QLErrorMsg` | `QLResult.isFailed()` in `*MessagingService` | Checks body contains `<ECSError>` / `FAIL` / `EDTR` |
| `*_ComposeESLError` | `ErrorUtil.errorResponse(500, ...)` | Generic broker exception → 500 |
| `*_TimeoutHandler_ComposeError` | `isTimeoutException()` → `ErrorUtil.errorResponse(408, "TimeOut Error", "TIME OUT ERROR FROM EDB Service TransactionID:" + txId)` | Must produce 408, not 500 |

---

## 2. Fixed-length request buffer construction

### Shared header (27 bytes) — `CMREQWEB.CPY` pattern

Every QL/CICS service starts with a `@FixedLengthRecord(length=27)` header class:

```java
@FixedLengthRecord(length = 27, paddingChar = ' ')
public class *RequestHeader {
    @DataField(pos = 1,  length = 4, align = "R", paddingChar = '0') private String webBufTypId;   // PIC 9(4), default "0001"
    @DataField(pos = 5,  length = 8, align = "L", paddingChar = ' ') private String webUsrId;      // PIC X(8), requesterId
    @DataField(pos = 13, length = 4, align = "L", paddingChar = ' ') private String webTrnId;      // PIC X(4), e.g. "B470","W050","B520","W605"
    @DataField(pos = 17, length = 3, align = "L", paddingChar = ' ') private String webObjCd;      // PIC X(3), e.g. "ACT","BNF"
    @DataField(pos = 20, length = 3, align = "L", paddingChar = ' ') private String webSrvCd;      // PIC X(3), e.g. "BNF","AGL"
    @DataField(pos = 23, length = 1, align = "L", paddingChar = ' ') private String webAcnCd;      // PIC X(1), e.g. "R","U"
    @DataField(pos = 24, length = 4, align = "R", paddingChar = '0') private String webBufCnt;     // PIC 9(4), default "0001"
}
```

### PIC type → Bindy `@DataField` mapping rules

| COBOL PIC | Java type | align | paddingChar | Note |
|---|---|---|---|---|
| `PIC X(N)` | `String` | `L` (left) | `' '` | Alpha, space-pad right |
| `PIC 9(N)` | `String` | `R` (right) | `'0'` | Unsigned numeric, zero-pad left |
| `PIC S9(N) SIGN LEADING SEPARATE` | `String` | `L` | `' '` | Total length = N+1 (sign char + N digits); use `FixedWidthBufferUtil.padSigned(value, N)` NOT `padNumeric` |
| `PIC S9(N)V99 SIGN LEADING SEPARATE` | `String` | `L` | `' '` | Decimal: multiply value by 100, then pad as S9(N+2) with sign |

### `FixedWidthBufferUtil` methods (standard across all QL services)

```java
padAlpha(String value, int len)         // right-pad with spaces, truncate to len
padNumeric(String value, int len)       // left-pad with zeros, truncate to len
padSigned(String value, int len)        // '+' or '-' + zero-padded len digits = total len+1 bytes
padDecimalUnsigned(String value, int intLen) // zero-padded integer, no sign
extractField(String buffer, int start, int len) // 0-based offset, left-trim result
parseSignedDecimal(String raw, int decimalDigits) // strip sign, parse, divide by 10^decimalDigits
```

### Buffer section initiators (response side)

Response buffers use 4-byte or 8-byte tagged sections. Common patterns:
- `"0002"` — transaction header
- `"0003"` — error section
- `"00040006"`, `"0006"` — beneficiary/data section (repeating)
- `"00040061"`, `"00040062"`, `"00040063"` — W605-specific image sections
- Each section starts with an initiator tag; repeating groups loop while `buffer.startsWith(tag, offset)`

---

## 3. QL Connector invocation

### QLRequest building (`*MessagingService.buildQLRequest()`)

⚠️ **`QLRequest` does NOT have a `.builder()` method — confirmed by compiling against the real framework jar.** Use `new QLRequest()` + setters exactly as below, unlike `RestClientRequest` (used for the test-harness path below) which does have one. See Rule 1 in `mandatory-migration-rules.md` for the general lesson: verify `.builder()` support per framework class, never assume it generalizes across a connector group.

```java
QLRequest request = new QLRequest();
request.setTransactionCode(properties.getQl().getTransactionCode());  // e.g. "ACT","PSV","PCT"
request.setProgramName(properties.getQl().getProgramName());           // e.g. "B470","B520"
request.setOperationName(operationName);                                // from caller (e.g. "MORW373","ELGW036")
request.setServiceName(properties.getQl().getServiceName());           // e.g. "beneficiaryStatusService"
request.setAppName(applicationName.toLowerCase());                     // from EieHeader
request.setMessageBody(new String(requestBuffer, ISO_8859_1));
request.setMsgsize(messageBody.length());
request.setTimeout(properties.getTimeoutSeconds() * 1000L);
```

### `QLConnector` class

```java
@Component
public class *QLConnector {
    @Autowired private QlConnector qlConnector;  // framework bean

    @EIPMicroserviceProviderCall(
        provider = EIPMicroserviceProvider.QL_CICS,
        function = Constants.QL_CICS_PROVIDER_FUNCTION)   // e.g. "B470.PSVW0470"
    public ResponseEntity<QLResponse> invokeQLConnector(QLRequest request) {
        return qlConnector.invokeQLConnector(request);
    }
}
```

⚠️ **Critical gotcha:** the `function=` string in `@EIPMicroserviceProviderCall` must match THIS service's transaction, not be copy-pasted from another service. findallergiesanddiagnosis has a known defect where B470 constants appear in a W050 service — always verify the routing key matches the CICS program/transaction.

### Test-harness bypass pattern

```java
// PREFERRED (per Rule 4 and confirmed live in ValidateBridgeSupply) — RestClientWrapper, never RestTemplate:
if ("P".equals(eieHeader.getTestHarnessMode())) {
    RestClientRequest request = RestClientRequest.builder()   // RestClientRequest DOES have a builder (unlike QLRequest — see §3 below)
            .clientName(Constants.QL_RESTCLIENT_NAME)
            .methodType(RequestMethod.POST)
            .mediaType(MediaType.APPLICATION_JSON)
            .payload(messageBody)
            .build();
    ResponseEntity<String> r = restClientWrapper.executeRestCall(request, String.class);
    return r.getBody();
}
```
Only fall back to a hand-rolled `RestTemplate` `@Bean` in `AppConfig` if the specific sibling reference project for this service family already uses that pattern consistently — see the §0 note above. Never introduce a *new* `RestTemplate` usage.

---

## 4. Response parsing patterns

### Error detection

```java
// From QLMessagingService — check BEFORE attempting parse
public boolean isFailed(String body) {
    return body.contains("<ECSError>")
        || body.toUpperCase().contains("FAIL")
        || body.toUpperCase().contains("EDTR");
}
```

### Error record parsing (from buffer)

```java
// Standard error buffer layout (CMRPLWEB pattern):
// offset 0-3:  ERROR_INITIATOR "0003"
// offset 4-7:  REQ_BUF_NB (4 chars)
// offset 8-11: REQ_REF (4 chars)
// offset 12:   SEV_CD (1 char)
// offset 13:   TYP_CD (1 char)
// offset 14-23: MSG_ID (10 chars)
// offset 24-53: MSG_SHT_TXT (30 chars)
// offset 54-132: MSG_LNG_TXT (79 chars)
String msgId   = extractField(errBuf, 14, 10);
String shortTxt = extractField(errBuf, 24, 30);
String longTxt  = extractField(errBuf, 54, 79);
```

### Repeating group pattern (EW36 style — fixed-count OCCURS)

```java
for (int i = 0; i < MAX_RECORDS; i++) {
    String slice = buffer.substring(
        SECTION_OFFSET + i * RECORD_LEN,
        SECTION_OFFSET + (i + 1) * RECORD_LEN);
    RecordModel rec = (RecordModel) bindyUtility.unMarshaller(slice, RecordModel.class, DIRECT_ROUTE);
    if (isEmpty(rec)) continue;   // skip zero-filled slots
    results.add(mapToDto(rec));
}
```

### Repeating group pattern (TDS-tagged — initiator scan)

```java
int offset = SECTION_START;
while (offset + RECORD_LEN <= buffer.length()
       && buffer.startsWith(RECORD_TAG, offset)) {
    String slice = buffer.substring(offset, offset + RECORD_LEN);
    RecordModel rec = (RecordModel) bindyUtility.unMarshaller(slice, RecordModel.class, DIRECT_ROUTE);
    results.add(mapToDto(rec));
    offset += RECORD_LEN;
}
```

### Bindy unmarshal utility (`*CamelBindyUtility`)

```java
@Component
public class *CamelBindyUtility extends RouteBuilder {
    @Autowired private ProducerTemplate producerTemplate;

    @Override public void configure() {
        from("direct:unmarshal-*RecordType*")
            .unmarshal(new BindyFixedLengthDataFormat(*RecordType*.class));
    }

    @SuppressWarnings("unchecked")
    public <T> T unMarshaller(String data, Class<T> clazz, String directRoute) {
        List<Map<String, Object>> result = (List<Map<String, Object>>)
            producerTemplate.requestBody(directRoute, data.getBytes(StandardCharsets.ISO_8859_1));
        return clazz.cast(result.get(0).values().iterator().next());
    }
}
```

---

## 5. Error handling

| Scenario | HTTP code | Java pattern |
|---|---|---|
| Inbound validation failure | 400 | `ErrorUtil.errorResponse(400, "Bad Request", "[9|Data Validation Error][1002|" + detail + "][TransactionID: " + txId + " ]")` |
| QL error buffer present (`WEB_TRN_ERR_CNT > 0`) | 404 | `ErrorUtil.errorResponse(404, "Data Not Found", "[9|Service Error][" + msgId + "|" + shortTxt + ": " + longTxt + "][TransactionID: " + txId + " ]")` |
| ECS error (`<ECSError>` / FAIL / EDTR) | 500 | `ErrorUtil.errorResponse(500, "Internal Service Error", "[9|Service Error][" + code + "|" + shortTxt + ": " + longTxt + "][TransactionID: " + txId + " ]")` |
| Null / non-2xx QL response | 500 | `ErrorUtil.errorResponse(500, "Internal Service Error", "Backend is not responding successfully. [transactionID: " + txId + "]")` |
| Timeout | **408** | `ErrorUtil.errorResponse(408, "TimeOut Error", "TIME OUT ERROR FROM EDB Service TransactionID:" + txId)` |

---

## 6. application.properties keys template

**`<env>` below = `qa` (or `qa2`) when generating `application-local.properties` — never `dev`/`prod` (Rule 12). For a per-environment config-map file, `<env>` is that file's own actual tier instead.**

```properties
# QL/CICS connector (env-specific config-map file)
application.http.enabled=true
application.http.restclient.ql.name=ql
application.http.restclient.ql.uri=https://<env>eippbmaz-oci.cvshealth.com/ql/request/v1
application.http.restclient.ql.connect-timeout=10000
application.http.restclient.ql.request-timeout=<timeoutForServer * 1000>
application.http.restclient.ql.socket-timeout=<timeoutForServer * 1000>
application.http.restclient.ql.max-total-connections=50
application.http.restclient.ql.default-max-per-route=20
application.http.restclient.ql.default-keep-alive-time-millis=20000
application.http.restclient.ql.test-harness-host=https://esl-wiremock.corp.cvscaremark.com:9080
application.http.close-idle-connection-wait-time-secs=30

# Service-specific QL buffer config
application.<serviceName>.ql.transaction-code=<TRN_ID, e.g. "ACT","PSV">
application.<serviceName>.ql.program-name=<e.g. "B470","B520">
application.<serviceName>.ql.service-name=<e.g. "beneficiaryStatusService">
application.<serviceName>.ql.service-code=<SRV_CD, e.g. "BNF","AGL">
application.<serviceName>.ql.action-code=<ACN_CD, e.g. "R","U">
application.<serviceName>.timeout.seconds=<timeoutForServer value from IIB .properties>
```

---

## 7. Maven dependencies

```xml
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-connector-qlcics</artifactId>
</dependency>
<!-- EieHeader is provided transitively by framework-starter — no extra dependency needed (Rule 8) -->
<dependency>
    <groupId>org.apache.camel.springboot</groupId>
    <artifactId>camel-spring-boot-starter</artifactId>
    <version>${camel.version}</version>  <!-- typically 4.8–4.22 -->
</dependency>
<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-bindy</artifactId>
    <version>${camel.version}</version>
</dependency>
```

---

## 8. Standard package layout

```
com.cvshealth.eip.microservices
├── controller.*Controller                // REST entry, implements *Api, thin delegate
├── service.*Service                      // orchestrator: validate → build → send → parse
├── service.QLMessagingService            // QL round-trip, timeout detection, test-harness
├── connector.QLConnector                 // @EIPMicroserviceProviderCall wrapper
├── ql.*RequestBufferBuilder              // assembles fixed-width request buffer
├── ql.*ResponseBufferParser              // parses fixed-width response, detects errors
├── ql.QLErrorParser                      // builds ErrorStatus from ECS-XML or QL error buffer
├── ql.FixedWidthBufferUtil               // padNumeric/padAlpha/padSigned/extractField
├── model.bindy.*RequestHeader            // @FixedLengthRecord request record(s)
├── model.bindy.*ResponseRecord           // @FixedLengthRecord response record(s)
├── utility.*CamelBindyUtility            // Camel RouteBuilder for direct: unmarshal routes
├── config.*Properties                    // @ConfigurationProperties(prefix="application.<svc>")
├── constant.QLConstants                  // buffer type IDs, initiators, Camel route names
├── constant.ValidationConstants          // field names, lengths, BIP error templates
└── swagger.model.bindy.*                 // (alternate package used by some services)
```
