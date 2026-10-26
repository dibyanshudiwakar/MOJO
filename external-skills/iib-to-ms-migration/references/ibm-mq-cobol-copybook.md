# IBM MQ + COBOL Copybook — Migration Reference

**Training service:** fepclaimdetail (MQ round-trip to mainframe, MITB002 copybook, EBCDIC) — **only one training repo for this entire category.** Every claim below is a single-repo finding, not cross-checked against a second example the way QL_CICS/RxClaim SBO/REST claims are. Treat this file as a starting hypothesis and update it the first time a second MQ_COBOL service is migrated.

## 0. Confirmed facts (single-repo evidence — see caveat above)

- **Genuinely a third connector family**, distinct from both `QlConnector` (QL_CICS) and `RxClaimConnector` (RxClaim SBO). Signal: `framework-mqclient` + `MQUtility.sendwithID`/`receivewithID` + raw COBOL copybook field references in the ESQL, with **no** QL transaction code and **no** Bindy/DFDL message set. Never add `framework-connector-qlcics` for this backend type — that's a different family entirely (`gap-checklist.md` [MQ1]).
- **No Camel Bindy anywhere** — zero hits for `Bindy`/`FixedLengthRecord`/`camel` repo-wide in `fepclaimdetail`. The wire format is fully hand-rolled, EBCDIC, offset-based (`gap-checklist.md` [MQ2]). This is the one connector group where Bindy is *not* the answer even though the transport is a fixed-length mainframe buffer, same as QL_CICS conceptually — the difference is IIB itself used raw COBOL copybook fields with no DFDL/CWF message set for this flow, so there's no annotated-schema equivalent to lean on in Java either.
- **Constants split by concern is confirmed and acceptable**: `FepClaimDetailConstants` (general/COBOL header) + `FepClaimDetailMqConstants` (MQ/MQRFH2-specific) — two classes, not one, same flexibility on naming as documented for QL_CICS in `ql-cics-camel-bindy.md` §0.
- **Other framework connectors present, confirmed via pom.xml**: `framework-connector-db` (routes SQL through REST — a legitimate way to reach DB2 data without a direct JDBC connection, worth knowing exists for any category, not just this one — see `translation-table.md`), `framework-connector-refdata`, `framework-cache`, `framework-eieusercontext` (see next point), `framework-restclient`.
- **`framework-eieusercontext` IS a genuine dependency here** — confirmed via direct pom.xml + source inspection, `fepclaimdetail` calls `EieHeaderBuilder.createEieUserContextString(eieHeader.getIdToken())` to populate `eieUserContext`, the same pattern confirmed in 2 of 3 RxClaim SBO repos. This directly contradicts an earlier, over-generalized version of Rule 8 in `mandatory-migration-rules.md` that banned this dependency outright — see that rule's current, corrected text. Requester-ID/AES-logging follows the same shape as the RxClaim SBO category: JWT-populated context via the framework, `AESEncryption.encrypt()` used for logging only (the actual MQ payload sent is plaintext EBCDIC, not encrypted).
- **Config-map convention is identical to every other backend type** — `application.{env}-{cluster}.properties`, dots not hyphens, outside `src/main/resources`. No deviation.

---

## 1. Structural mapping template

| IIB module / node | Java class | Notes |
|---|---|---|
| `DIMToBackend` (validate + build MQ request) | `*ServiceImpl.validateRequest()` + `CobolMessageBuilder.buildRequest()` | Manual byte-offset construction in EBCDIC |
| `SetMQHeaders` (MQMD/MQRFH2 fields) | Handled by `framework-mqclient` JMS layer from `usr_*` properties — no Java code needed | |
| MQ Output node (`ComIbmMQOutput`) | `FepClaimDetailMQConnector.sendAndReceive()` via `MQUtility.sendwithID()` + `receivewithID()` | framework-mqclient |
| MQ Input node (BLOB domain) | `MQUtility.receivewithID(replyQueue, correlationId, queueManager)` | |
| MQRFH2 stripping on reply | `FepClaimDetailMQConnector.stripMqrfh2Header(bytes)` | Detect EBCDIC "RFH " eye-catcher, read struct-length bytes 8-11 |
| `BackendToDIM` / canonical mapping | `CobolResponseParser.parse()` + `CobolClaimParser` (field-by-field offset) | EBCDIC charset, offset from `CobolFieldConstants` |
| `*_ComputeMVSIsErrorFEP` (RECAP status decode) | `assertSuccessfulResponseStatus()` — switch on `NRML/NFND/EDTR/FAIL/QZPI` | |
| `*_ComposeESLError` / catch/failure | `ErrorUtil.errorResponse(500, ...)` | |
| `*_TimeoutHandler` | `assertValidMqReply()` null check → `buildErrorResponse(408, "TIME OUT ERROR...")` | |

---

## 2. MQ request message construction

### Key constants

```java
public static final String EBCDIC_CHARSET = "IBM037";  // CCSID 37
public static final Charset EBCDIC = Charset.forName(EBCDIC_CHARSET);
```

### Fixed-length EBCDIC request buffer builder

```java
// Pattern from CobolMessageBuilder — byte-offset based, NOT Camel Bindy
byte[] message = new byte[REQUEST_BUFFER_LENGTH];  // e.g. 155 bytes

writeEbcdicString(message, 0, "API01.00", 8);                   // VERSION header
writeEbcdicString(message, 8, COBOL_FUNCTION_NAME, 48);          // e.g. "FEP-CLAIMS-INQ-DETL"
writeEbcdicNumericDisplay(message, 107, FUNCTION_NAME_LENGTH, 2); // 19 = length of function name
writeEbcdicNumericDisplay(message, 109, RECEIVE_BUFFER_MAX, 7);   // 32768
writeEbcdicNumericDisplay(message, 116, ERROR_BUFFER_MAX, 7);     // 32768
writeEbcdicNumericDisplay(message, 123, BODY_LENGTH, 7);          // 11
writeEbcdicNumericDisplay(message, 144, documentNumber, 9);       // MITB_RQST_DOC_NB
writeEbcdicNumericDisplay(message, 153, documentSeqNumber, 2);    // MITB_RQST_DOC_SEQ_NB

// IIB equivalent: SET OutputRoot.MRM.header.VERSION = 'API01.00';
//                 SET OutputRoot.MRM.requestBody.MITB_RQST_DOC_NB = CAST(docNb AS INTEGER);
```

### Helper methods

```java
private void writeEbcdicString(byte[] buf, int offset, String value, int len) {
    String padded = String.format("%-" + len + "s", value).substring(0, len);
    byte[] encoded = padded.getBytes(EBCDIC);
    System.arraycopy(encoded, 0, buf, offset, len);
}

private void writeEbcdicNumericDisplay(byte[] buf, int offset, long value, int len) {
    String formatted = String.format("%0" + len + "d", value);
    System.arraycopy(formatted.getBytes(EBCDIC), 0, buf, offset, len);
}
```

---

## 3. MQ Connector invocation

### Configuration properties

```java
@Data
@Component
@ConfigurationProperties(prefix = "application.fepclaim")
public class FepClaimDetailMqProperties {
    private String requestQueue;   // e.g. "queue:///ESM.REQUEST.MVS?targetClient=1"
    private String replyQueue;     // e.g. "queue:///ESL.PBM.FEPCLAIMDTL.MS.MVS.RESPONSE"
    private String queueManager;   // e.g. "PBM_A_ZAD1106"
}
```

### Send/Receive pattern

```java
@ConditionalOnProperty(value = "application.ibm.mq.enabled", havingValue = "true")
@Component
public class *MQConnector {
    @Autowired private MQUtility mqUtility;
    @Autowired private MultipleMQProperties multipleMQProperties;

    public String sendAndReceive(byte[] payload) {
        String queueManager = resolveQueueManager();
        String correlationId = UUID.randomUUID().toString().replace("-","").substring(0,24);

        String hexCorrelId = mqUtility.sendwithID(
            mqProperties.getRequestQueue(), payload, correlationId, queueManager,
            Optional.ofNullable(mqProperties.getReplyQueue()));

        String receiveCorrelId = buildReceiveCorrelationId(hexCorrelId, correlationId);
        return mqUtility.receivewithID(mqProperties.getReplyQueue(), receiveCorrelId, queueManager);
    }
}
```

### MQRFH2 header stripping

```java
// EBCDIC bytes for "RFH " eye-catcher
private static final int EBCDIC_R = 0xD9, EBCDIC_F = 0xC6, EBCDIC_H = 0xC8, EBCDIC_SPACE = 0x40;

public byte[] stripMqrfh2Header(byte[] bytes) {
    if (bytes.length >= 12
            && (bytes[0] & 0xFF) == EBCDIC_R && (bytes[1] & 0xFF) == EBCDIC_F
            && (bytes[2] & 0xFF) == EBCDIC_H && (bytes[3] & 0xFF) == EBCDIC_SPACE) {
        int strucLen = ((bytes[8] & 0xFF) << 24) | ((bytes[9] & 0xFF) << 16)
                     | ((bytes[10] & 0xFF) << 8) | (bytes[11] & 0xFF);
        return Arrays.copyOfRange(bytes, strucLen, bytes.length);
    }
    return bytes;
}
```

---

## 4. Response parsing

### QZ header layout (RECAP format)

```java
static final int BODY_LEN_OFFSET  = 130;  // IIB: QZ_HEADER_LENGTH - 2*QZ_LENGTH_FIELD_SIZE (1-based)
static final int ERROR_LEN_OFFSET = 137;  // IIB: QZ_HEADER_LENGTH - QZ_LENGTH_FIELD_SIZE (1-based)
static final int BODY_START_OFFSET = 144; // IIB: QZ_HEADER_LENGTH (1-based = 145 → 0-based = 144)
static final int TRUNCATE_LENGTH  = 1126; // must match IIB FEPClaimDetail_Truncate_Extra_Buffer

// Parse header:
int bodyLen  = parseHeaderLen(responseBytes, BODY_LEN_OFFSET);
int errorLen = parseHeaderLen(responseBytes, ERROR_LEN_OFFSET);

// Response code is first 4 chars of the error section:
String responseCode = new String(responseBytes, BODY_START_OFFSET + bodyLen,
                                  STANDARD80_RESPONSE_CODE_LENGTH, EBCDIC).toUpperCase();
// responseCode: "NRML"=success, "NFND"=not found, "EDTR"/"FAIL"/"QZPI"=error
```

### Sequential field reader (`FieldReader` pattern)

```java
// IIB: COBOL copybook field positions → MS: sequential cursor with named field constants
public class FieldReader {
    private int pos = 0;
    private final String buf;
    public String trimmed(int len) { return extract(len).trim(); }
    public String numeric(int len)  { return trimmed(len).replaceFirst("^0+(?!$)", ""); }
    public void skip(int len)       { pos += len; }
    private String extract(int len) { String s = buf.substring(pos, pos+len); pos += len; return s; }
}

// Field size constants in CobolFieldConstants:
public static final int DOC_NB_SIZE = 9;
public static final int INSRD_ID_NB_SIZE = 9;
public static final int PTNT_LAST_NME_SIZE = 19;
// ... one constant per copybook field
```

### Ignorable error text (carry verbatim from IIB)

```java
// IIB: IF CONTAINS(errorText, 'INSURANCE CODE NOT VALID...') THEN treat as 404 "not found"
// MS constants must match IIB literals exactly:
public static final String IGNORABLE_EDTR_1 = "INSURANCE CODE NOT VALID, PLEASE CHECK";
public static final String IGNORABLE_FAIL_1 = "FAILMITD002";
public static final String IGNORABLE_FAIL_2 = "99000";
public static final String IGNORABLE_FAIL_3 = "CICS HANDLE ABEND";
```

---

## 5. Error handling

| Scenario | HTTP code | Java pattern |
|---|---|---|
| Request validation | 400 | `ErrorUtil.errorResponse(400, ...)` |
| RECAP status `NFND` or ignorable EDTR/FAIL text | 404 | `ErrorUtil.errorResponse(404, "Data Not Found", ...)` |
| RECAP status `EDTR`/`FAIL`/`QZPI` (non-ignorable) | 500 | `ErrorUtil.errorResponse(500, "Internal Service Error", ...)` |
| Null MQ response | **408** | `ErrorUtil.errorResponse(408, "TIME OUT ERROR FROM EDB Service TransactionID:" + txId)` |
| MQ exception / send-receive failure | 500 | `ErrorUtil.errorResponse(500, ...)` |

---

## 6. application.properties keys template

**`<host>` in `application-local.properties` should be the QA queue manager's host (Rule 12 — same "local always points at QA" convention confirmed across every other backend type's HTTP connectors). This category's own evidence is single-source (`fepclaimdetail`), so confirm against that service's own `application-local.properties` before assuming the exact QA hostname pattern — unlike the HTTP-based backend types, there's no 22-repo cross-check backing this specific placeholder yet.**

```properties
application.ibm.mq.enabled=true
application.ibm.mq.resetMQCacheSessionTime=600000

# Queue Manager config (one block per QM name)
application.ibm.mq.queueName.<QM_NAME>.connectionNameList=<host>(<port>)
application.ibm.mq.queueName.<QM_NAME>.queue-manager=<QM_NAME>
application.ibm.mq.queueName.<QM_NAME>.channel=EIP.SVRCONN
application.ibm.mq.queueName.<QM_NAME>.message-body=1
application.ibm.mq.queueName.<QM_NAME>.ssl-auth-enabled=false
application.ibm.mq.queueName.<QM_NAME>.receive-timeout=3000
application.ibm.mq.queueName.<QM_NAME>.reconnect-on-exception=false
application.ibm.mq.queueName.<QM_NAME>.session-cache-size=50

# Service queues (from IIB *.properties: queueName + replyToQ)
application.<svcName>.request-queue=queue:///<REQUEST_QUEUE>?targetClient=1
application.<svcName>.reply-queue=queue:///<REPLY_QUEUE>
application.<svcName>.queue-manager=<QM_NAME>
application.<svcName>.mq.receive-timeout-ms=30000
```

**Auxiliary DB2 connectors, confirmed real on the training repo (`fepclaimdetail`'s `FepClaimDetailDbConnector` — "QL DB (NPI lookup) and ECS DB (drug master lookup) via REST connectors," replacing what IIB did as direct ODBC/`EXTERNAL NAME` DB calls with no URL at all):**

```properties
application.http.restclient.ecshc.name=ecshc
application.http.restclient.ecshc.uri=https://<env>eippbm-oci.cvshealth.com/ecsdb/executeQuery
application.http.restclient.ecshc.connect-timeout=10000
application.http.restclient.ecshc.request-timeout=10000
application.http.restclient.ecshc.socket-timeout=30000
application.http.restclient.ecshc.max-total-connections=50
application.http.restclient.ecshc.default-max-per-route=25
application.http.restclient.ecshc.default-keep-alive-time-millis=20000

application.http.restclient.qlhc.name=qlhc
application.http.restclient.qlhc.uri=https://<env>eippbm-oci.cvshealth.com/qldb/execute/query/v1
application.http.restclient.qlhc.connect-timeout=10000
application.http.restclient.qlhc.request-timeout=10000
application.http.restclient.qlhc.socket-timeout=30000
application.http.restclient.qlhc.max-total-connections=50
application.http.restclient.qlhc.default-max-per-route=25
application.http.restclient.qlhc.default-keep-alive-time-millis=20000
```

Same plain-`pbm-oci`, QA-for-local host rule as every other internal-CVS-backend connector (Rule 12) — `ecshc`/`qlhc` are a different **path** on the same gateway host, not a different host family. Only wire these if the IIB flow actually does the corresponding DB2 lookup via the shared-library DB helper — don't add them "just in case" (`gap-checklist.md` [H10]).

⚠️ **Queue name mismatch common:** IIB msgflow queue names often differ from the MS properties queue names — IIB uses shared ESL queues while MS uses dedicated MS response queues. Always confirm both queue names with the infra/MQ team.

---

## 7. Maven dependencies

```xml
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-mqclient</artifactId>
    <!-- transitively pulls IBM MQ JMS client jars -->
</dependency>
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-connector-db</artifactId>  <!-- if DB2 lookup needed -->
</dependency>
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-restclient</artifactId>    <!-- for REST auxiliary connectors -->
</dependency>
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-cache</artifactId>         <!-- if caching needed -->
</dependency>
```

---

## 8. Standard package layout (fepclaimdetail pattern)

```
com.cvshealth.eip.microservices
├── controller.*Controller
├── service.*ServiceImpl (implements *Service)
├── connector.*MQConnector           // MQ send/receive, MQRFH2 strip
├── connector.*DbConnector           // DB2 REST-proxy lookups (via framework-connector-db)
├── util.CobolMessageBuilder         // builds EBCDIC fixed-width request buffer
├── util.CobolResponseParser         // parses QZ header, dispatches to CobolClaimParser
├── util.CobolClaimParser            // field-by-field COBOL copybook → response DTO
├── util.CobolFieldConstants         // all field size constants
├── util.*MqProperties               // @ConfigurationProperties(prefix="application.<svc>")
├── constants.*Constants             // EBCDIC charset name, QZ header constants, response codes
├── constants.*MqConstants           // MQRFH2 eye-catcher byte values, correlation-ID constants
└── model.response.FieldReader       // sequential fixed-width cursor reader
```
