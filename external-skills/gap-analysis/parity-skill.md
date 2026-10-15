---
name: parity-skill
description: Parity analysis for IIB to Microservice migrations. Returns a JSON payload consumed by a server-side HTML template.
---

# Purpose

Perform a thorough parity analysis between an IIB application and its migrated Spring Boot Microservice.

**Output a single fenced ```json ... ``` block and nothing else.** No HTML, no FILE: blocks, no commentary. The server owns presentation — your job is only the data.

---

# Input shape

The user message gives you two sides to compare:

**IIB side — one of two forms:**
- `iib_spec:` — a compact JSON spec extracted by a prior `iib_analysis` run. Keys include `flows`, `connectors`, `endpoints`, `message_models`, `hardcoded_values`, `error_handling`, `transformations`, `input_contract`, `output_contract`. **When this appears, treat it as the authoritative IIB behavioural spec** — it is the condensed source of truth for what the IIB application does. You do NOT need raw ESQL to proceed.
- `iib_files:` — raw ESQL / msgflow / subflow / mxsd / xsd / map file contents. **Only present when `iib_spec` is unavailable.** Read every file in this case.

**MS side — always raw files:**
- `ms_files:` — every migrated MS source file: Java (`src/main/java/**/*.java`), resources (`src/main/resources/**`), pom.xml, swagger / OpenAPI yaml, application*.properties / application*.yml. Read all of them.

The comparison is always: *does the MS implementation faithfully implement the IIB behaviour spec?*

# What to analyse

Compare across these areas:

**Field Mappings:** request/response field names, types, renames (e.g. IIB `facilityID = pharmacy.storeNumber`), CAST/type coercions, defaults for optional fields, array loop logic.

**Integration Points:** backend URL externalization, HTTP request headers forwarded to backend, timeout values (IIB `timeoutForServer` vs MS client timeout), TLS protocol version, token/auth header handling, response field parsing.

**Request Validation:** required header/body field checks with exact error message strings, enum/allowed-value checks, IIB error response format `{ httpCode, httpMessage, moreInformation }` including the `'. TransactionID:<id>'` suffix, transaction ID / correlation ID derivation (COALESCE chain).

**Transaction Flow:** end-to-end path, test-harness header redirect, timeout/error terminal paths, backEndErrorFlag flow (error terminal → extract status line → HTTP 500), HTTP status overrides (`responseCode > 599 → 500`, explicit 408 on timeout).

**Error Handling:** HTTP status codes per error type (validation → 400, backend error → 500, timeout → 408), exact error message text, exception handling coverage.

---

# Status definitions

| Status | When |
|---|---|
| MATCH | IIB behaviour is exactly replicated in MS |
| PARTIAL | Minor discrepancy — functionally similar but not identical |
| GAP | IIB behaviour is missing or wrong in MS |

Severity rules:
- `GAP` affecting a documented IIB output field or auth/validation → **CRITICAL**
- `GAP` affecting an internal transformation or default → **HIGH**
- `PARTIAL` that is functionally wrong (different value, different type) → **HIGH**
- `PARTIAL` that is cosmetic (ordering, label, extra null-safety) → **MEDIUM** or **LOW**
- `MATCH` entries still appear in `rows[]` but carry no severity

---

# Output JSON schema

Return exactly one fenced JSON block with this shape — nothing before, nothing after:

```json
{
  "action": "parity_check",
  "application_name": "",
  "iib_component": "SomeFlow",
  "ms_component": "some-service",
  "iib_file_count": 0,
  "ms_file_count": 0,
  "executive_summary": [
    "Paragraph 1 — overall readiness.",
    "Paragraph 2 — what matches.",
    "Paragraph 3 — what is partial or missing."
  ],
  "counts": { "match": 0, "partial": 0, "gap": 0 },
  "severity_counts": { "critical": 0, "high": 0, "medium": 0, "low": 0 },
  "rows": [
    {
      "area": "Field Mappings",
      "iib_source": "CarrierNameSearch_Compute.esql:88",
      "iib_value": "OutputRoot.JSON.Data.carrierName = InputRoot.MQMD.carrierName",
      "ms_source": "CarrierNameSearchService.java:112 (method mapResponse)",
      "ms_value": "response.setCarrierName(request.getCarrierName());",
      "status": "MATCH",
      "remarks": "Field mapping preserved; same source and target identifiers"
    },
    {
      "area": "Field Mappings",
      "iib_source": "CarrierNameSearch_Compute.esql:42",
      "iib_value": "facilityID = pharmacy.storeNumber",
      "ms_source": "CarrierNameSearchService.java:— (field not produced)",
      "ms_value": "— not implemented —",
      "status": "GAP",
      "remarks": "facilityID is never populated in the response record"
    },
    {
      "area": "Integration Points",
      "iib_source": "CarrierNameSearch.msgflow (HTTPRequest node) · timeout=30000",
      "iib_value": "timeoutForServer: 30000ms",
      "ms_source": "RxClaimMqConnectorService.java:67 (connect(RestTemplate rt))",
      "ms_value": "rt.setConnectTimeout(Duration.ofMillis(30000));",
      "status": "MATCH",
      "remarks": "Backend connect timeout preserved verbatim"
    }
  ],
  "findings": [
    {
      "id": "P-01",
      "severity": "CRITICAL",
      "status": "GAP",
      "area": "Field Mappings",
      "iib_source": "SomeFlow_Compute.esql:42",
      "ms_source": "SomeService.java:—",
      "description": "IIB computes facilityID = pharmacy.storeNumber; MS does not produce facilityID",
      "fix_hint": "map pharmacy.storeNumber → facilityID in response record and populate in SomeService"
    }
  ],
  "suggestions": [
    {
      "priority": "HIGH",
      "issue": "facilityID missing from response",
      "recommendation": "Populate facilityID from pharmacy.storeNumber in SomeService response mapping"
    }
  ]
}
```

Rules:
- `rows[]` must include every analysed comparison (MATCH, PARTIAL, GAP) grouped by `area` in order: Field Mappings → Integration Points → Validation → Transaction Flow → Error Handling. Within a group list MATCH first, then PARTIAL, then GAP.
- `findings[]` contains only PARTIAL and GAP entries. Every GAP in `rows[]` must have a matching `findings[]` entry; actionable PARTIALs must too.
- `suggestions[]` contains only HIGH-priority GAPs and behaviour-affecting PARTIALs. One entry per actionable issue. `priority` is HIGH / MEDIUM / LOW.
- `executive_summary` is 2–4 short paragraphs (strings in an array).
- Severities are uppercase: CRITICAL, HIGH, MEDIUM, LOW.
- Statuses are uppercase: MATCH, PARTIAL, GAP.
- `fix_hint` is one concrete line the migrate pass can act on.

## MS-side detail requirements (strict)

The report rendering uses `ms_source` and `ms_value` as the "Microservice (class · line)" column — these fields MUST carry concrete class-level details for the report to be useful. Treat the following as hard requirements:

**For every MATCH or PARTIAL row:**
- `ms_source` MUST be the **actual file path from `ms_files`** + a line number (or line range) + the enclosing method / class / bean name in parentheses. Example: `cvs-v1-carriernamesearch-oci-web/src/main/java/.../controller/CarrierNameSearchController.java:38 (search() method)`, `application.properties:12`, `pom.xml:45 (dependency rxclaim-client)`. Never leave it blank. Never write just the class name without a file path.
- `ms_value` MUST be the **actual code snippet or configuration value** copied verbatim from the MS file (one or two lines, trimmed). Example: `response.setCarrierName(request.getCarrierName());`, `server.ssl.protocol=TLSv1.2`, `<version>2.7.18</version>`. Never leave it blank. Never paraphrase — copy the real text.

**For GAP rows (behaviour missing in MS):**
- `ms_source` is still required. Format: `<nearest-expected-file>:— (field/method not produced)` — e.g. `CarrierNameSearchService.java:— (facilityID field never populated)`. This tells the fix pass exactly where the gap should be fixed.
- `ms_value` is `"— not implemented —"`.

**Enforcement:**
- Before emitting a row, scan `ms_files` for the matching symbol. If you cannot find any MS code even remotely related, the row becomes a GAP — not an empty MATCH / PARTIAL.
- Do NOT emit rows where `ms_source` is blank, `"-"`, `"?"`, `"TBD"`, or just a class name. If you can't locate the MS counterpart, drop the row entirely or classify it as GAP with a concrete expected location.
- Every `ms_source` must reference a file path that actually appears in the `ms_files` section of the input. If a file wasn't given to you, don't invent one.
- Coverage expectation: for a typical Spring Boot MS the report should include rows referencing — at minimum — the controller class, the service class, the connector class, the swagger / OpenAPI yaml, application.properties, and pom.xml. If any of these is missing from your rows, you probably skipped files you should have read.