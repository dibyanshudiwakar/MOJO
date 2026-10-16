---
name: iib-to-ms-planning
description: Planning an IIB/ACE to Spring Boot migration — the IIB-construct-to-framework mapping catalogue, the ordered per-layer task graph for the detected backend type, and the risk register. Use when producing the plan that code generation will execute, or when deciding whether an ESQL construct has an approved target.
---

# Planning — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`planning`** agent.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `planning` (Planning Agent) |
| Required role | `MIGRATION_PLANNER` |
| Depends on | `migration-specification` |
| Accepted inputs | `approved_analysis`, `migration_yaml`, `framework_template` |
| Produced outputs | `plan`, `task_graph`, `mapping`, `risk_register` |
| Tool allow-list | `evidence.read`, `plan.write`, `clarification.create` |

## What this agent does with the skill

### 1. Mapping catalogue

`references/construct-mapping.md` (verbatim from the source translation table) maps IIB shared-library calls and constructs to their framework equivalents, for example `ESLConfigurationManager` → `application.properties` / `@ConfigurationProperties`, IIB EhCache → a lazily initialised `ConcurrentHashMap`, and the `ESL*CacheHelper` family → downstream REST calls.

**A construct with no row in the catalogue is an unsupported mapping.** Record it as a HIGH risk with a clarification, and stop. Do not plan around it. A plan that silently omits an unmappable construct looks complete while leaving behaviour behind.

Caution carried over from the source: before applying the reference-data row, confirm the IIB ESQL really calls reference data. The catalogue lists the mapping as *available*, not as *required* ([H10]).

### 2. Task graph

Order follows the source workflow:

1. **SCAFFOLD** — create the `EIPMS_cvs-v1-<service>-oci` root as the first filesystem action, then the two Maven modules, poms, base properties, Dockerfile, `logback-spring.xml` (not for REST), `.github/`, `state.json`. Compile gate.
2. **GENERATE, per flow** — layers in the order given by the matching connector reference's *Structural mapping template* (§1) and *Standard package layout* (§8) sections. Every layer ends with a compile gate; compile after each layer, not once at the end.
3. **Gap check** — run the gap checklist after the service layer; loop up to 3 times.
4. **Tests** — class files per the backend type's density convention; content per the user's Rule 11 answer.
5. **HANDOFF** — `mvn clean verify`, parent `target/` removal, baseline-block check (HANDOFF-A0), config-map reconcile (HANDOFF-A), smoke test (HANDOFF-C), final gap re-check.

Connector references, all owned by `iib-to-ms-code-generation`: `ql-cics-camel-bindy.md`, `rxclaim-sbo-xml-jaxb.md`, `ibm-mq-cobol-copybook.md` and `rest-external-direct.md` (each has the §1 and §8 sections named above), and `soap-backend.md` (single-source and unverified by the skill itself; its layout is in §2 *Package layout*).

### 3. Risk register

Seed it from the gap checklist's severity classes rather than inventing categories: BLOCKING (will not start or corrupts data), HIGH (compiles but silently wrong), MEDIUM (operational/deployment). Always include:

- any unsupported construct;
- queue names that may differ from IIB, for the infra team;
- QL buffer-layout questions, for the QL team;
- a credential value still needing encryption, for the service owner;
- the deferred exception handler (Rule 10), every migration;
- Kong's upstream ownership of token validation, when the ESQL has an ID-token node (Rule 9).

## Output

- `plan`, `task_graph`, `mapping`, `risk_register`.

## Boundaries

- Reads evidence and writes the plan only. It registers **no evidence** — this contract grants no `evidence.write`.
- It does not generate code. Code generation refuses to run without an approved plan.

## References

Owned by this skill:

- `references/construct-mapping.md` — IIB construct → MS equivalent, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/ql-cics-camel-bindy.md`, `references/rxclaim-sbo-xml-jaxb.md`, `references/ibm-mq-cobol-copybook.md`, `references/rest-external-direct.md`, `references/soap-backend.md` — connector references (§1 and §8 of each; SOAP §2)
- `references/gap-checklist.md`, `references/scaffold-and-generate.md` — severity classes for the risk register; the generation order
- `references/structural-conventions.md` — package layout and file set
- `references/type-and-context-root-rules.md`
