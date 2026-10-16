---
name: iib-to-ms-variance-analysis
description: Comparing a migrated Spring Boot service against its IIB original — the actual-versus-expected response (classified as structural, type or value with a severity), and two opt-in static comparisons of IIB source against the migrated code, parity and hardcoding (MATCH / PARTIAL / GAP). Use when evaluating the HANDOFF-C smoke-test result, any captured pair of ACE and Spring results, or when a parity or hardcoding report is requested; includes the known signatures of IIB-versus-MS differences.
---

# Variance Analysis — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`variance-analysis`** agent (the "compare" step of HANDOFF-C).

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `variance-analysis` (Variance Analysis Agent) |
| Required role | `VARIANCE_ANALYST` |
| Depends on | `test-engineering` |
| Accepted inputs | `ace_results`, `java_results` |
| Produced outputs | `variance_records`, `severity` |
| Tool allow-list | `evidence.read`, `comparison.execute`, `variance.write` |

## What this agent does with the skill

It compares what the test-engineering agent captured against what the user said to expect, and records each difference as a **variance**.

### The comparison standard

The source skill sets a deliberately modest bar for the smoke test:

- Compare the **status code**, and the **presence and match of the key fields and values the user gave**.
- It is a **reasonable-match comparison, not a byte-perfect diff.** Byte-level and many-case comparison is the separate `iib-ms-testing` skill's job.
- Exact business-logic fidelity is secondary to "a working call to the backend", and gets fixed after testing.

So the question to answer first is: *did the service reach its backend and return a response of the expected shape?* A transport or wiring failure is a different kind of finding from a field-level difference.

### Classification

Use the factory's three classes:

| Class | Meaning here |
|---|---|
| STRUCTURAL | a field or construct present in one response and absent in the other |
| TYPE | the two disagree on a value's type |
| VALUE | same shape, different value (including status code and message text) |

Rate severity so that anything preventing a usable response (no backend reached, wrong endpoint, 5xx where a success was expected) outranks a cosmetic field difference.

### Known signatures

`references/known-variance-signatures.md` indexes the differences this skill's own evidence shows really occur between IIB and the generated service — what the symptom looks like, the suggested class, and the checklist item or gotcha that explains it. Use it to label a variance accurately, so the RCA agent starts from the right place. It is an index: the explanations live in the sources it cites.

## Parity analysis (opt-in: `parity=true`)

The runtime comparison above asks *what did the two services return?* **Parity analysis** asks a different question, from source alone: *does the migrated code implement what the IIB flow does?* It compares field mappings, integration points, request validation, transaction flow and error handling, row by row, and reports each as MATCH, PARTIAL or GAP. The procedure and the exact JSON it must return are in `references/parity-skill.md`, which comes from `external-skills/gap-analysis/` and is the authority for that output.

- **It complements the runtime comparison, never replaces it.** Neither can show what the other shows: parity finds behaviour that no test case happened to exercise; the runtime comparison finds a difference that source reading cannot predict.
- **Model-only.** There is no deterministic equivalent. It runs only when the model gateway has an approved `agent/variance-analysis/parity-check` prompt and an approved model; otherwise the agent reports `NOT_ANALYSED` with the reason and the runtime comparison is unaffected.
- **Inputs come from the job's own records,** never from the caller: the IIB source files (`.msgflow`, `.subflow`, `.esql`, `.xsd`, `.wsdl`, `.map`, `.xslt`, `.policyxml`) from the repository recorded at intake, and the migrated sources from the generated artefacts. Where a side is missing, the step is `SKIPPED` with a reason; it does not guess.
- **During this step the parity skill is the active guidance, not this file.** Its output contract (a single `parity_check` JSON block) differs from the `severity-rationale` step's `assessments` contract, so the two are never combined in one model call.
- **The HTML report is the server's, not the model's.** The skill returns JSON only and says so ("the server owns presentation"). The server renders that JSON to a self-contained HTML page (`app/services/static_check_reports.py`: no script, nothing external, every value escaped, figures recomputed from the rows) and writes it to `<pipeline workspace>/reports/parity-report.html` (`hardcoding-report.html` for the other check; `reports/<job id>/` when the job has no pipeline). The same page is served for any stored report at `GET /api/v2/reports/<report id>/html`. Never ask the model for HTML, and never put markup in its output.
- **Where the result goes:** a `PARITY_ANALYSIS` validation report, with counts and the page's location in the run's metrics. It is **not** written to the variance store and **not** returned as an agent output: a model-produced row must not open or close a variance, and the output contract is unchanged. A human or the governance path decides what a parity GAP means for promotion.

### Reading a parity report next to runtime variances

| Parity | Meaning | Factory equivalent |
|---|---|---|
| `MATCH` | IIB behaviour replicated | no variance |
| `PARTIAL` | functionally similar, not identical | usually VALUE or TYPE, severity by the skill's rules |
| `GAP` | behaviour missing or wrong | usually STRUCTURAL (absent) or VALUE (wrong) |

Parity severities are `CRITICAL / HIGH / MEDIUM / LOW`; the factory's variance severities are `HIGH / MEDIUM / LOW`. Do not convert one into the other automatically: a `CRITICAL` parity GAP is read as at least `HIGH`, and the mapping is a person's decision.

## Hardcoding check (opt-in: `hardcoding=true`)

A narrower, code-only sibling of the parity analysis, defined in `references/hardcoding-skill.md` (from `external-skills/hardcoding/`). It compares exactly three areas, in this order: **Transaction Flow**, **Integration Points** and **Field Mappings** (input passthrough, hardcoded literals, derived values, renames, casts, boolean encoding, null/default injection).

| | Parity analysis | Hardcoding check |
|---|---|---|
| Areas | field mappings, integration points, validation, transaction flow, error handling | transaction flow, integration points, field mappings |
| Migrated side | every generated file, including config, `pom.xml` and OpenAPI | **Java source only** (`.properties`, `.yml`, `.yaml`, `.xml`, `.json` are out of scope) |
| IIB side | `.msgflow`, `.subflow`, `.esql`, `.xsd`, `.wsdl`, `.map`, `.xslt`, `.policyxml` | `.esql`, `.msgmap`, `.msgflow`, `.subflow`, `.xsd` |
| Rows carry | file, line and the real code or config value | a short category and a one-line management-level remark, no code |
| Also returns | `findings` with `CRITICAL/HIGH/MEDIUM/LOW` severities | `suggestions` with `HIGH/MEDIUM/LOW` priority only |
| Report type | `PARITY_ANALYSIS` | `HARDCODING_ANALYSIS` |

Use the hardcoding check for a quick read on whether the Java reproduces the IIB flow, calls and field logic; use parity when a row-level, evidence-cited report with severities is needed. Both can be requested in one run; each is its own model call with its own governed prompt (`agent/variance-analysis/hardcoding-check`), its own skill as the only guidance, and its own stored report.

Everything said for parity applies unchanged: model-only (otherwise `NOT_ANALYSED` with the reason), inputs taken from the job's own intake source and generated artefacts, `SKIPPED` rather than truncated when the source is too large, nothing written to the variance store, nothing added to the agent's outputs. A migrated side with no Java file at all is `SKIPPED` (`no_ms_java_files`). Where the skill finds IIB behaviour with no corresponding Java class, it reports a GAP; the agent does not.

## Output

- `variance_records` — one per difference: location, class, expected, actual, severity.
- `severity` — overall, and whether the backend call itself worked.
- With `parity=true` or `hardcoding=true`, `metrics.parity` / `metrics.hardcoding` — `status` (`ANALYSED`, `NOT_ANALYSED`, `SKIPPED`, `INVALID_RESPONSE`), the report id, counts, and `html_path` / `html_url` (or `html_error` if the page could not be written; the analysis is kept either way). Not part of the contract outputs.

## Boundaries

- Reads evidence and writes variances only. It does not diagnose causes (`iib-to-ms-rca`), fix anything (`iib-to-ms-remediation`), or re-run the call (`iib-to-ms-test-engineering`).
- It does not decide that a variance is acceptable; that is a governance and approval matter.
- Never smooth over a missing comparison: if there is no captured actual response, record that, not a pass.

## References

Owned by this skill:

- `references/known-variance-signatures.md` — authored index (not extracted text)
- `references/parity-skill.md` — the parity analysis skill, verbatim from `external-skills/gap-analysis/parity-skill.md`
- `references/hardcoding-skill.md` — the hardcoding check skill, verbatim from `external-skills/hardcoding/hardcoding-skill.md`

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/common-patterns-and-gotchas.md`
- `references/gap-checklist.md`
- `references/scope-boundary.md`
