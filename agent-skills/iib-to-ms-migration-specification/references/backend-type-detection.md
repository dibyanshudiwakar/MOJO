<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 50–66. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

## Backend Type Detection

Read every `.msgflow` and `.esql` file before deciding. This determines everything downstream — connector, pom deps, package layout, all of it.

| IIB Signal | Backend Type | MS Connector | Reference file |
|---|---|---|---|
| SOAP envelope in ESQL + HTTP Request node | **SOAP** | `framework-soapclient` | `references/soap-backend.md` |
| `MQOutput`/`MQGet`/`Aggregation` + QL TRAN code in ESQL | **QL_CICS** | `framework-connector-qlcics` | `references/ql-cics-camel-bindy.md` |
| `RxClaim`/`SBO`/`GETIDCARDDETAIL`-style transaction codes in ESQL or subflow names | **RXCLAIM_SBO** | `framework-connector-rxclaimsbo` | `references/rxclaim-sbo-xml-jaxb.md` |
| IBM MQ + raw COBOL copybook fields, **no** QL TRAN code, **no** Bindy/DFDL message set | **MQ_COBOL** | `framework-mqclient` (`MQUtility.sendwithID`/`receivewithID`) | `references/ibm-mq-cobol-copybook.md` |
| `HTTP Request` node to a plain REST endpoint (no SOAP envelope, no QL/RxClaim routing) | **REST** | `RestClientWrapper` | `references/rest-external-direct.md` |
| `RECAP`/`Tandem` in subflow/queue names | **RECAP** | `framework-connector-recap` | not yet covered — flag as gap, ask before proceeding |
| `Stratus` in subflow/queue names | **STRATUS** | `framework-connector-stratus` | not yet covered — flag as gap, ask before proceeding |
| `DB2`/`JDBC` node | **DB2** | flag as GAP — no direct DB in MS, but check for `framework-connector-db` (routes SQL through REST — legitimate, confirmed in `fepclaimdetail`) | see `ibm-mq-cobol-copybook.md` §MQCOBOL-1 note |
| IBM MQ with **no** QL TRAN pattern and **no** COBOL copybook fields | **MQ** (plain) | Camel JMS or Spring Integration — rare; confirm it isn't actually MQ_COBOL first | — |

Don't default "MQ is involved" straight to QL_CICS — check for the actual QL TRAN code signal, and don't default it to MQ_COBOL either without checking for raw COBOL copybook field references with no DFDL/Bindy message set.
