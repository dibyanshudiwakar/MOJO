---
name: iib-to-ms-migration-specification
description: Authoring the migration specification for an IIB/ACE to Spring Boot migration — what is migrated, to which connector and module, which decisions were made, and what remains unresolved. Use when turning an approved ACE analysis into the schema-validated specification, including ESQL-to-Java type mapping and CONTEXT_ROOT derivation.
---

# Migration Specification — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`migration-specification`** agent.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `migration-specification` (Migration Specification Agent) |
| Required role | `SPECIFICATION_AUTHOR` |
| Depends on | `ace-analysis` |
| Accepted inputs | `semantic_model`, `approved_defaults` |
| Produced outputs | `migration_specification_yaml` |
| Tool allow-list | `evidence.read`, `schema.validate`, `evidence.write` |

## What this agent does with the skill

It writes down, in the specification, the decisions the rest of the skill treats as already made. The source skill has no separate specification stage, so this skill gathers the *contract-level* content that is scattered through the workflow: what each flow is, what it maps to, and what has been decided or is still open.

### What the specification must state

| Item | Where it comes from |
|---|---|
| Every flow, ESQL module, subflow and schema in scope | the ACE analysis (`iib-to-ms-ace-analysis`) |
| Backend type per flow and the matching connector module | the ACE analysis; connector table in `references/backend-type-detection.md` |
| Target `CONTEXT_ROOT` | derived by the rule in `references/type-and-context-root-rules.md` |
| ESQL → Java type for each field | the type table in the same file |
| Framework version and Java version | the user's answer at intake (Rule 7); never inferred |
| Destination directory | the user's answer at intake (Rule 13) |
| CVS programme phase (1 or 2) | the user's answer at intake |
| Test mode: full JUnit content or structure-only | the user's answer at intake (Rule 11) |
| Smoke-test request and expected response | the user's answer at intake; stored once and carried here, not re-asked |
| Whether the service needs Akeyless | decided per service, never assumed from phase or backend type (Rule 6) |
| Items deliberately **not** generated | the ID-token check (Kong enforces it, Rule 9); a custom exception handler (Rule 10) |
| Unresolved items | every parser ambiguity, carried into `clarifications` |

### Type and path rules (verbatim in `references/type-and-context-root-rules.md`)

- `DECIMAL` / monetary → `double`; never `int`/`long` for money. Match what the reference project for this service already does before introducing `BigDecimal`.
- `INTEGER` → `int` or `long`; `long` for any ID-like field that could exceed 2^31.
- `DATE` → `String`, ISO `yyyy-MM-dd`. Do not introduce `LocalDate` unless the reference project already does.
- `BOOLEAN` → nullable `Boolean` when the IIB field can be genuinely absent.
- `CONTEXT_ROOT`: replace the IIB HTTP Input path's `/edb/` segment with `/cs/`, in the property-overridable form `${CONTEXT_ROOT:/…/cs/…}`. Never leave `/edb/`.

## Consistency rule

The specification must not quietly drop part of the analysis it claims to describe. Every flow, ESQL module, map and schema path from the analysis appears under the source section, and every analysis ambiguity is carried forward as a clarification. Do not promise a target module the planning agent's mapping catalogue will refuse.

## Output

- `migration_specification_yaml`.

## Boundaries

- Reads evidence and validates against the schema; it writes evidence only.
- It records decisions. It does not make a decision the user has not answered — an unanswered mandatory input stays an open clarification.

## References

Owned by this skill:

- `references/type-and-context-root-rules.md` — data type mapping and CONTEXT_ROOT derivation, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/state-file-and-resumption.md` — field names used for the decisions block
- `references/mandatory-migration-rules.md` — Rules 6, 7, 9, 10, 11, 13
- `references/construct-mapping.md` — which constructs have an approved target
