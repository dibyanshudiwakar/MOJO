<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 50–66. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

[✓/✗] Target dir writable
[✓/✗] JDK 21, Maven 3.8+
[✓/✗] Reference OCI repo found (pom cross-check)
```
Confirm with the user before proceeding: "Proceed with {N} flow(s) as {backend type} service?"

---

## Backend Type Detection

Read every `.msgflow` and `.esql` file before deciding. This determines everything downstream — connector, pom deps, package layout, all of it.

| IIB Signal | Backend Type | MS Connector | Reference file |
|---|---|---|---|
| SOAP envelope in ESQL + HTTP Request node | **SOAP** | `framework-soapclient` | `references/soap-backend.md` |
| `MQOutput`/`MQGet`/`Aggregation` + QL TRAN code in ESQL | **QL_CICS** | `framework-connector-qlcics` | `references/ql-cics-camel-bindy.md` |
| `RxClaim`/`SBO`/`GETIDCARDDETAIL`-style transaction codes in ESQL or subflow names | **RXCLAIM_SBO** | `framework-connector-rxclaimsbo` | `references/rxclaim-sbo-xml-jaxb.md` |
