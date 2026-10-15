# Agent skills — IIB → Spring Boot migration

`external-skills/iib-to-ms-migration` is one skill: a single linear workflow (`INTAKE → SCAFFOLD → GENERATE → HANDOFF`) with about 4,000 lines of references. The factory runs **14 agents**, each with a narrow tool contract. This folder breaks the single skill into **one self-contained skill per agent**.

The source skill is left untouched. `app/agents/skills.py` loads these skills (see `docs/AGENTS_DEVELOPMENT.md`, "Agent skills").

## Agent → skill

| Agent (`config/agent-catalogue.v1.json`) | Skill | Reference files it owns | Copies of others' files |
|---|---|---|---|
| `migration-orchestrator` | [`iib-to-ms-migration-orchestrator`](iib-to-ms-migration-orchestrator/SKILL.md) | `definition-of-done.md`, `handoff-and-action-items.md`, `scope-boundary.md`, `state-file-and-resumption.md` | 2 |
| `repository-intake` | [`iib-to-ms-repository-intake`](iib-to-ms-repository-intake/SKILL.md) | `intake-procedure.md` | 3 |
| `ace-analysis` | [`iib-to-ms-ace-analysis`](iib-to-ms-ace-analysis/SKILL.md) | `backend-type-detection.md` | 4 |
| `framework-baseline` | [`iib-to-ms-framework-baseline`](iib-to-ms-framework-baseline/SKILL.md) | `fixed-file-templates.md`, `structural-conventions.md`, `config-map-baseline.properties` | 3 |
| `migration-specification` | [`iib-to-ms-migration-specification`](iib-to-ms-migration-specification/SKILL.md) | `type-and-context-root-rules.md` | 4 |
| `planning` | [`iib-to-ms-planning`](iib-to-ms-planning/SKILL.md) | `construct-mapping.md` | 9 |
| `code-generation` | [`iib-to-ms-code-generation`](iib-to-ms-code-generation/SKILL.md) | `gap-checklist.md`, `handoff-build-and-reconcile.md`, `ibm-mq-cobol-copybook.md`, `mandatory-migration-rules.md`, `ql-cics-camel-bindy.md`, `rest-external-direct.md`, `rxclaim-sbo-xml-jaxb.md`, `scaffold-and-generate.md`, `soap-backend.md` | 7 |
| `test-engineering` | [`iib-to-ms-test-engineering`](iib-to-ms-test-engineering/SKILL.md) | `smoke-test-procedure.md` | 5 |
| `variance-analysis` | [`iib-to-ms-variance-analysis`](iib-to-ms-variance-analysis/SKILL.md) | `hardcoding-skill.md`, `known-variance-signatures.md`, `parity-skill.md` | 3 |
| `rca` | [`iib-to-ms-rca`](iib-to-ms-rca/SKILL.md) | `common-patterns-and-gotchas.md` | 4 |
| `remediation` | [`iib-to-ms-remediation`](iib-to-ms-remediation/SKILL.md) | `fix-and-retry-policy.md` | 4 |
| `governance` | [`iib-to-ms-governance`](iib-to-ms-governance/SKILL.md) | `hard-constraints.md`, `verification-checklists.md` | 4 |
| `knowledge-evidence` | [`iib-to-ms-knowledge-evidence`](iib-to-ms-knowledge-evidence/SKILL.md) | `evidence-hierarchy.md` | 3 |
| `dormancy-intelligence` | **none** | — | — |

### Why `dormancy-intelligence` has no skill

The source skill never decides whether a service is worth migrating. It has no traffic, telemetry, deployment-inventory or dormancy content, so any skill written for this agent would be invented.

If you later want one, the content has to come from somewhere other than this skill.

## How the split was made

- **By who does the work, not by file.** Each part of the pipeline was assigned to the agent whose contract performs it, and the tool allow-list was respected: `repository-intake` cannot raise clarifications (so its skill reports *gaps*), `remediation` plans changes but does not write source, `test-engineering` can run what was built but cannot rebuild.
- **Other external skills.** `parity-skill.md` (from `external-skills/gap-analysis/`) and `hardcoding-skill.md` (from `external-skills/hardcoding/`) are owned by `iib-to-ms-variance-analysis`; the sync script copies them like the others.
- **No retyping.** Owned reference files are byte-identical copies of the originals, or cuts of them by exact line range; each cut starts with an HTML comment naming the source and ranges.
- **Self-contained.** A skill that needs a file owned by another skill holds a byte-identical **copy** in its own `references/` folder, so any skill directory can be mounted alone. The copy set is derived from the `references/...` links in that skill's `SKILL.md`. Copies are never edited by hand.
- **Drift is detected, not trusted.** `python scripts/sync_agent_skills.py --check` fails if any copy differs from its owner, if a generated table is stale, or if a `SKILL.md` links outside its folder. Every file's SHA-256 is in `skills-map.json`, and the loader verifies them at load time.
- **Authored, not extracted:** all 13 `SKILL.md` files (summaries of the agent's part of the workflow with a *Boundaries* section from the tool allow-list, and in places an *Adaptation note*), and `iib-to-ms-variance-analysis/references/known-variance-signatures.md`. The "suggested class" column in that index is a labelling choice made here. Treat the authored files as the part to review.

## Editing

1. Change an **owned** file in its owning skill (or change the source skill and re-sync), or edit a `SKILL.md` body.
2. Run `python scripts/sync_agent_skills.py` to refresh copies, the *Agent binding* tables, `skills-map.json` and this README.
3. Run `python scripts/sync_agent_skills.py --check` (also run by `tests/agents/test_agent_skills.py`).

The extracted ranges are line-number based: if the source skill changes and lines shift, the ranges in the script need updating.

## Who owns each reference file

Text inside copied and extracted files still uses the **original** names (for example `references/gap-checklist.md`); each skill has the files it uses in its own `references/`.

| File | Owner |
|---|---|
| `backend-type-detection.md` | `iib-to-ms-ace-analysis` |
| `common-patterns-and-gotchas.md` | `iib-to-ms-rca` |
| `construct-mapping.md` | `iib-to-ms-planning` |
| `definition-of-done.md` | `iib-to-ms-migration-orchestrator` |
| `evidence-hierarchy.md` | `iib-to-ms-knowledge-evidence` |
| `fix-and-retry-policy.md` | `iib-to-ms-remediation` |
| `fixed-file-templates.md` | `iib-to-ms-framework-baseline` |
| `gap-checklist.md` | `iib-to-ms-code-generation` |
| `handoff-and-action-items.md` | `iib-to-ms-migration-orchestrator` |
| `handoff-build-and-reconcile.md` | `iib-to-ms-code-generation` |
| `hard-constraints.md` | `iib-to-ms-governance` |
| `hardcoding-skill.md` | `iib-to-ms-variance-analysis` |
| `ibm-mq-cobol-copybook.md` | `iib-to-ms-code-generation` |
| `intake-procedure.md` | `iib-to-ms-repository-intake` |
| `known-variance-signatures.md` | `iib-to-ms-variance-analysis` |
| `mandatory-migration-rules.md` | `iib-to-ms-code-generation` |
| `parity-skill.md` | `iib-to-ms-variance-analysis` |
| `ql-cics-camel-bindy.md` | `iib-to-ms-code-generation` |
| `rest-external-direct.md` | `iib-to-ms-code-generation` |
| `rxclaim-sbo-xml-jaxb.md` | `iib-to-ms-code-generation` |
| `scaffold-and-generate.md` | `iib-to-ms-code-generation` |
| `scope-boundary.md` | `iib-to-ms-migration-orchestrator` |
| `smoke-test-procedure.md` | `iib-to-ms-test-engineering` |
| `soap-backend.md` | `iib-to-ms-code-generation` |
| `state-file-and-resumption.md` | `iib-to-ms-migration-orchestrator` |
| `structural-conventions.md` | `iib-to-ms-framework-baseline` |
| `templates/config-map-baseline.properties` | `iib-to-ms-framework-baseline` |
| `type-and-context-root-rules.md` | `iib-to-ms-migration-specification` |
| `verification-checklists.md` | `iib-to-ms-governance` |
