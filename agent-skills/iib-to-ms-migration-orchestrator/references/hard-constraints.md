<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 282–293. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

## Hard constraints (apply across every stage)

- Never invent a field mapping not present in the ESQL/XSD/copybook — flag as GAP instead
- Never use `RestClient`/`RestTemplate` for QL_CICS or MQ_COBOL transport (use `QlConnector`/`MQUtility`) — see Rule 4 in `mandatory-migration-rules.md`
- Never use Camel Bindy for MQ_COBOL — that backend type is hand-rolled EBCDIC offset parsing, no annotations
- Never hardcode a requester-ID field to a literal like `"ECS"`; never reach for JWT/JWKS validation to extract a requester ID — no training repo across any backend type needs it (see `common-patterns-and-gotchas.md` cross-category takeaway)
- Never put environment config files inside `src/main/resources/`
- Never leave `crlf` unset on a `@FixedLengthRecord` used for a concatenated request buffer
- Never wire a `RefDataConnector` or hardcode a ref-data lookup table without first confirming the IIB ESQL actually calls reference data
- Never present uncompiled code at a checkpoint
- Never `git commit`, `git push`, or `mvn deploy` without being explicitly asked
- Allowed Maven goals without asking: `compile`, `test`, `verify`, `clean`, `spring-boot:run` (short-lived, e.g. for a live test), `dependency:resolve`, `dependency:tree`
