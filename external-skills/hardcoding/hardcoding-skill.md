---
name: hardcoding-skill
description: IIB ↔ MS comparison of transaction flow, integration points and field mappings. Returns a JSON payload consumed by a server-side HTML template.
---

# Purpose

Compare the IIB ESQL source against the Spring Boot Microservice Java source to verify that transaction flow, integration points, and field mappings are faithfully replicated.

**Output a single fenced ```json ... ``` block and nothing else.** No HTML, no FILE: blocks, no commentary. The server owns presentation — your job is only the data.

---

# Scope

**IIB side:** Read `.esql`, `.msgmap`, `.msgflow`, `.subflow`, `.xsd` files from `iib_files`.

**MS side:** Read only Java source files (`src/main/java/**/*.java`). **Exclude** `.properties`, `.yml`, `.yaml`, `.xml`, `.json` — those are infrastructure/config concerns outside this check's scope.

---

# What to analyse

Compare across exactly three areas in this order:

**1. Transaction Flow** — end-to-end request/response path, routing decisions (e.g. `standaloneIndicator` branching), test-harness header redirect, transaction ID / correlation ID derivation (COALESCE chain), HTTP status overrides (`responseCode > 599 → 500`, explicit 408 on timeout), backEndErrorFlag terminal paths.

**2. Integration Points** — how each backend is called: HTTP request headers forwarded, timeout values (IIB `timeoutForServer` vs MS client timeout), OAuth/auth token handling and caching (TTL, grant type, scope), TLS protocol version, backend error code propagation (does MS preserve or normalize upstream HTTP status codes?), retry behaviour.

**3. Field Mappings** — how each field is set: input passthrough, hardcoded literal values, computed/derived values, field renames (e.g. IIB `facilityID = pharmacy.storeNumber`), CAST/type coercions, boolean encoding (native JSON boolean vs string), null/default injection.

---

# Status definitions

| Status | When |
|---|---|
| MATCH | IIB behaviour is exactly replicated in the MS Java code |
| PARTIAL | Functionally similar but implemented differently (minor divergence) |
| GAP | IIB behaviour is missing or behaviourally wrong in the MS |

---

# Output JSON schema

Return exactly one fenced JSON block with this shape — nothing before, nothing after:

```json
{
  "action": "hardcoding_check",
  "application_name": "",
  "iib_component": "",
  "ms_component": "",
  "iib_file_count": 0,
  "ms_file_count": 0,
  "counts": { "match": 0, "partial": 0, "gap": 0 },
  "rows": [
    {
      "area": "Transaction Flow",
      "category": "standaloneIndicator routing",
      "status": "MATCH",
      "remarks": "Blank/null → HTTP 400; TRUE → IngenioRx; FALSE → Anthem — logic and error message identical in both"
    },
    {
      "area": "Integration Points",
      "category": "IngenioRx HTTP headers",
      "status": "PARTIAL",
      "remarks": "MS omits the Host header that IIB sets explicitly; Content-Type, Accept, apiKey, Authorization all match"
    },
    {
      "area": "Field Mappings",
      "category": "Boolean field encoding",
      "status": "PARTIAL",
      "remarks": "IIB passes native JSON booleans (true/false); MS toBool() converts to string \"true\"/\"false\" — downstream compatibility unverified"
    }
  ],
  "suggestions": [
    {
      "priority": "HIGH",
      "issue": "IngenioRx connector omits the Host header that IIB sets on every outbound request",
      "recommendation": "Add Host header in IngenioRxConnectorService.buildRequest(), sourced from the configured base URI"
    }
  ]
}
```

Rules:
- `rows[]` is grouped by area in this order: Transaction Flow → Integration Points → Field Mappings. Within each group list MATCH first, then PARTIAL, then GAP.
- `category` is a short descriptive label for the specific behaviour compared (3–6 words, e.g. "OAuth token caching", "Timeout handling", "Payload field names").
- `remarks` is one concise line — state what IIB does and what MS does, and what the difference is. Management-level, no code snippets.
- `suggestions[]` contains only actionable items for PARTIAL and GAP rows. One entry per distinct issue. `priority` is HIGH, MEDIUM, or LOW.
- `counts` must reflect the actual totals across all `rows[]` entries.
- `iib_component` is derived from the IIB file paths (strip APM prefix and environment suffix).
- `ms_component` is derived from the MS file paths.
- Do not emit any row where the MS has no corresponding Java file at all — classify those as GAP with a note about the missing class.
