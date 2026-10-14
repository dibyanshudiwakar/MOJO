---
name: iib-to-ms-framework-baseline
description: The target Spring Boot framework conventions an IIB-migrated CVS EIP microservice must follow — repo layout, poms, Dockerfile, .github workflows, Helm chart families, config-map baseline, annotations, EieHeader accessors. Use when establishing or verifying the framework template, and when reference repos disagree on a convention.
---

# Framework Baseline — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`framework-baseline`** agent. The conventions in this skill *are* the framework template that the baseline agent extracts and verifies.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `framework-baseline` (Framework Baseline Agent) |
| Required role | `FRAMEWORK_ANALYST` |
| Depends on | `repository-intake` |
| Accepted inputs | `reference_repositories`, `architecture_standards` |
| Produced outputs | `framework_template`, `conflicts` |
| Tool allow-list | `repository.read`, `evidence.write`, `clarification.create` |

## What this agent does with the skill

It establishes the conventions that every generated service is built against, and raises the places where they conflict. The three reference files below hold the conventions:

| Reference | Covers |
|---|---|
| `references/structural-conventions.md` | repo-level layout, `.github` workflows, Dockerfile, parent and web poms, full environment property-file list, package layout, DTO annotations, reference-data lookups, connector/controller annotations, Bindy models, `EieHeader` accessors, Helm values schema |
| `references/fixed-file-templates.md` | literal copy-ready templates for `.github/`, Dockerfile and Helm chart extracted from 12 real repos across all 4 backend types, plus decision tables for the three things that genuinely vary |
| `references/templates/config-map-baseline.properties` | the physical baseline block that must appear byte-for-byte in the packaged properties and every config-map |

### Defaults to carry into the template

- `framework-starter` **3.2.2** and JDK **21** — Rule 7. The default is something to offer when asking the user. It is never inferred from sibling repos.
- Repo root directory named `EIPMS_cvs-v1-<service>-oci`, two Maven modules (`*-parent`, `*-web`).
- Parent pom version `0.0.1-SNAPSHOT`; `<java.version>` set to 21.
- `framework-restclient` is always required, whatever the backend type — it is the source of `EieHeader`, `ErrorUtil`, `ErrorStatus` and the `@EIPMicroserviceProvider*` annotations.
- `application.properties` is tiny; `application-local.properties` carries the bulk — never invert them ([M16]).
- Config-maps live in `config/config-map/`, outside `src/main/resources/`, and carry the whole baseline block.

## Conflicts to raise (clarifications)

This agent holds `clarification.create`. Raise a conflict instead of choosing silently when:

- Two reference repos disagree. The source skill documents real cases: Helm template filename casing is genuinely inconsistent across the org; two Helm families exist (RICH and SIMPLE); the `test-harness-host` key has four naming variants ([H16]); the baseline block sits in `application.properties` in some repos and `application-local.properties` in others.
- A mandatory standard is not detected at all in the reference repos.
- A reference repo shows a **known bug**. `fixed-file-templates.md` §5, "Known bugs in the training set — recognize, don't replicate", lists them; do not present a latent defect (for example a config-map missing `management.endpoints.web.exposure.include`) as a convention.

Evidence strength matters when weighing a conflict: live-executed beats cross-repo grep beats a single repo. See `references/evidence-hierarchy.md`.

## Output

- `framework_template` — the conventions above, resolved.
- `conflicts` — each unresolved disagreement, with the repos involved and the evidence tier.

## Boundaries

- Read-only on repositories; writes evidence and clarifications only.
- It extracts and verifies conventions. It does not generate the service, and it does not decide business logic.

## References

Owned by this skill:

- `references/structural-conventions.md`
- `references/fixed-file-templates.md`
- `references/templates/config-map-baseline.properties`

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/mandatory-migration-rules.md` — Rules 2, 3 and 7 constrain the template
- `references/gap-checklist.md` — [M1]–[M25] are the machine-checkable form of these conventions
