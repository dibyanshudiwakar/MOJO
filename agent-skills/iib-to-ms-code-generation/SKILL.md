---
name: iib-to-ms-code-generation
description: Generating the Spring Boot microservice from an approved IIB migration plan — SCAFFOLD, per-layer GENERATE with the compile-gate loop, the 13 mandatory rules, the gap checklist, backend-specific connector patterns (SOAP, QL/CICS + Camel Bindy, RxClaim SBO, IBM MQ + COBOL, REST), JUnit files, and the HANDOFF build and config-map reconcile. Use when writing or checking any generated code or project file.
---

# Code Generation — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`code-generation`** agent. It holds the largest share of the source skill: the rules and patterns that determine whether generated code is *structurally right*.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `code-generation` (Code Generation Agent) |
| Required role | `CODE_GENERATOR` |
| Depends on | `planning` |
| Accepted inputs | `approved_plan`, `target_template` |
| Produced outputs | `source_code`, `tests`, `manifest`, `provenance` |
| Tool allow-list | `repository.branch`, `repository.write`, `build.execute`, `evidence.write` |

## What this agent does with the skill

It runs SCAFFOLD, GENERATE and the build half of HANDOFF from an **approved plan**. It also owns the quality gate over what it produced, because this contract is the one granted `build.execute`.

Procedures, verbatim from the source workflow:

- `references/scaffold-and-generate.md` — SCAFFOLD; GENERATE; the **compile-gate auto-fix loop**; the mandatory **gap check**; the **Tests** subsection
- `references/handoff-build-and-reconcile.md` — `mvn clean verify`; reading results correctly; removing the parent `target/`; **HANDOFF-A0** baseline literal check; **HANDOFF-A** config-map reconcile; HANDOFF-B

## What "done" means here

Correct folder and file structure; all setup files present and correct (poms, Dockerfile, `.github/`, config-map, Helm); and a **working call to the backend**.

- **Always get right, no shortcuts:** the framework classes and annotations for the detected backend type — `QlConnector`/`QLRequest`/`QLResponse` + Camel Bindy for QL_CICS, `RxClaimConnector`/`RxClaimRequest` for RxClaim SBO, `MQUtility` for MQ_COBOL, `RestClientWrapper`/`RestClientRequest` for REST, `OAuthClient` where OAuth2 is needed, plus `EieHeader`, `ErrorUtil`/`ErrorStatus`, `@EIPMicroserviceProviderCall` and `@EIPMicroserviceOperation` everywhere.
- **May be simplified and refined after testing:** field-by-field ESQL translation, exact validation order and text, response-transformation nuance.
- This does **not** relax the mandatory rules below.

## The 13 mandatory rules

Load `references/mandatory-migration-rules.md` in full before generating anything. These override any pattern shown elsewhere.

| # | Rule |
|---|---|
| 1 | Builder pattern verified per framework class. `RestClientRequest` and `RxClaimRequest` have `.builder()`; `QLRequest` does **not** — never assume symmetry |
| 2 | Trim `pom.xml` — remove every dependency the generated code does not import |
| 3 | Full Dockerfile template at the repo root, not just the label |
| 4 | No `RestTemplate` — always `RestClientWrapper` |
| 5 | No `StringBuilder` for QL buffers — Camel Bindy `@FixedLengthRecord` plus a marshaller, request and response |
| 6 | Credentials encrypted by default; live Akeyless scaffolding only when the service genuinely requires it |
| 7 | Framework version and Java version asked, not inferred: default 3.2.2 / 21 |
| 8 | `EieHeader` accessors verified per accessor; `framework-eieusercontext` depends on category and service; never JWT/JWKS validation |
| 9 | No Kong/gateway token-validator reimplementation |
| 10 | No custom exception classes or `exception` package; call `ErrorUtil.errorResponse(...)` at the point of failure |
| 11 | JUnit content per the user's answer: real tests, or empty skeletons |
| 12 | `application-local.properties` targets `qa`/`qa2` for internal CVS backends |
| 13 | Destination defaults to a sibling of the IIB source; ask the user |

## Backend-specific generation

Read the reference for the detected type. Each has a *Structural mapping template*, request/response handling, error handling, `application.properties` keys, Maven dependencies and a *Standard package layout*.

| Backend type | Reference |
|---|---|
| QL_CICS | `references/ql-cics-camel-bindy.md` |
| RXCLAIM_SBO | `references/rxclaim-sbo-xml-jaxb.md` |
| MQ_COBOL | `references/ibm-mq-cobol-copybook.md` |
| REST | `references/rest-external-direct.md` |
| SOAP | `references/soap-backend.md` (single-source, unverified by the skill itself) |

Cross-check shared-library calls against `references/construct-mapping.md`.

## Gap checklist

`references/gap-checklist.md` — BLOCKING, HIGH, MEDIUM, plus the REST-only or MQ_COBOL-only section. Run after the service layer compiles and again on the final tree. Loop at most 3 times; anything still open is reported as a named GAP with who must confirm it. Easy-to-miss items: `crlf=""` on request-buffer Bindy records [H9]; SLS fields normalised on the way out [H5]; blank fields trimmed to `""`, not `null` [H6]; a controller containing error-response calls or inline logic [M20]; any `exception` package [M21]; a leftover parent `target/` [M24].

## Output

- `source_code`, `tests`, `manifest`, `provenance`.
- File presentation order: `.github/` workflows → parent pom → web pom → Dockerfile → config-map and Helm → controller/API interface → service → connector → buffer builders / XML converters / request mappers → buffer parsers / response mappers → Bindy/JAXB models → error/validator classes → constants (see `references/handoff-and-action-items.md`).

## Boundaries

- Generates only from an approved plan; it does not invent field mappings not present in the ESQL, XSD or copybook.
- Never `git commit`, `git push` or `mvn deploy` without being asked. Allowed Maven goals without asking: `compile`, `test`, `verify`, `clean`, short-lived `spring-boot:run`, `dependency:resolve`, `dependency:tree`.
- It does not run the HANDOFF-C smoke test (that is `iib-to-ms-test-engineering`) and does not do bulk regression comparison.

## References

Owned by this skill:

- `references/scaffold-and-generate.md`, `references/handoff-build-and-reconcile.md`
- `references/mandatory-migration-rules.md`, `references/gap-checklist.md`
- `references/ql-cics-camel-bindy.md`, `references/rxclaim-sbo-xml-jaxb.md`, `references/ibm-mq-cobol-copybook.md`, `references/rest-external-direct.md`, `references/soap-backend.md`

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/structural-conventions.md`, `references/fixed-file-templates.md`, `references/templates/config-map-baseline.properties` — folder layout, templates and the config-map baseline block
- `references/common-patterns-and-gotchas.md` — cross-cutting behavioural gotchas
- `references/construct-mapping.md`
- `references/type-and-context-root-rules.md`
