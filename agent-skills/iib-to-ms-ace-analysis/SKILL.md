---
name: iib-to-ms-ace-analysis
description: Reading IIB/ACE .msgflow and ESQL source to detect the backend connector type and record what downstream agents must reproduce exactly. Use when analysing an IIB service before specification — backend-type detection, unsupported constructs, ambiguities, and the source facts (validation order, error text, timeouts, reference-data calls, ID-token nodes) that code generation may not invent.
---

# ACE Analysis — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`ace-analysis`** agent (INTAKE Batch B of the source workflow, plus the "read the source, never invent" rules).

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `ace-analysis` (ACE Analysis Agent) |
| Required role | `ACE_ANALYST` |
| Depends on | `repository-intake` |
| Accepted inputs | `ace_repository_snapshot` |
| Produced outputs | `analysis_document`, `semantic_model`, `ambiguities` |
| Tool allow-list | `repository.read`, `parser.execute`, `evidence.write`, `clarification.create` |

## What this agent does with the skill

Read **every** `.msgflow` and `.esql` file fully, then classify. The classification decides the connector, pom dependencies, package layout and everything else downstream. The canonical table is `references/backend-type-detection.md` (verbatim from the source workflow).

| Backend type | Connector | Reference (in `iib-to-ms-code-generation`) |
|---|---|---|
| SOAP | `framework-soapclient` | `soap-backend.md` |
| QL_CICS | `framework-connector-qlcics` | `ql-cics-camel-bindy.md` |
| RXCLAIM_SBO | `framework-connector-rxclaimsbo` | `rxclaim-sbo-xml-jaxb.md` |
| MQ_COBOL | `framework-mqclient` | `ibm-mq-cobol-copybook.md` |
| REST | `RestClientWrapper` | `rest-external-direct.md` |
| RECAP, STRATUS, DB2, plain MQ | — | not covered: flag as gap, ask before proceeding |

Do not default "MQ is involved" to QL_CICS, and do not default it to MQ_COBOL either — look for the actual signal: a QL transaction code, versus raw COBOL copybook fields with no DFDL/Bindy message set.

## Facts the analysis must record

Downstream agents may not reconstruct these from memory. Each is a place where the source skill found real defects when it was guessed:

- **Validation order**, field by field, as the ESQL performs it ([H12]).
- **Error message text** verbatim, including BIP broker error text ([H13], gotchas §1 and §8).
- **Timeout and circuit-breaker handling** — whether the IIB flow returns 408 on timeout (gotchas §2, [M18]).
- **Not-found behaviour per flow** — HTTP 200 with empty body versus a real 404; this must be checked per flow, never assumed repo-wide ([R6], gotchas §5 and §14).
- **Whether the ESQL actually calls reference data** (`EXTERNAL NAME`, `ESLReferenceDataCacheHelper`) ([H10]). Zero of five surveyed QL/CICS repos do.
- **Requester-ID source** — hardcoded literal versus resolved from the EIE user context ([H1], [H1a]).
- **QL transaction code, routing key, queue names** — and flag queue names for the infra team, since they can differ between IIB and the MS (gotchas §6 and §10).
- **An ID-token-check node.** Record that one exists and that Kong is expected to enforce it upstream. Do not mark it for generation (Rule 9).
- **Credentials in IIB `.properties`** — note their presence; encryption is a generation concern (Rule 6, gotchas §11).

## Output

- `analysis_document` and `semantic_model` — per flow: name, msgflow, esql files, subflows, `backend_type`, `tran_id`, and the facts above.
- `ambiguities` — anything the source does not settle. Never fill a gap with an invented mapping; hard constraint: *never invent a field mapping not present in the ESQL/XSD/copybook — flag as GAP instead*.
- Unsupported constructs — RECAP, STRATUS, DB2 or plain-MQ signals, and any construct with no entry in the planning agent's construct mapping.

## Boundaries

- Read-only on the repository (`repository.read`, `parser.execute`). Writes evidence only.
- Detection and recording only. It does not choose package layout, generate code, or decide how an ID-token node is handled beyond recording it.

## References

Owned by this skill:

- `references/backend-type-detection.md` — the detection table, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/common-patterns-and-gotchas.md` — the behavioural gotchas listed above
- `references/gap-checklist.md` — [H1], [H10], [H12], [H13], [R6], [M18]
- `references/mandatory-migration-rules.md` — Rules 6 and 9
- `references/construct-mapping.md` — to decide whether a construct is unsupported
