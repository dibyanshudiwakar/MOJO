---
name: iib-to-ms-migration-orchestrator
description: Sequencing, checkpoints, state and resumption for an IIB/ACE to Spring Boot migration. Use when driving a migration job end to end — deciding which stage runs next, which agent skill owns it, when to stop for a checkpoint, how to resume, and how to turn unresolved items into clarifications and the final action-items list.
---

# Migration Orchestrator — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`migration-orchestrator`** agent: it owns *order and stopping*, not the work of any stage.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `migration-orchestrator` (Migration Orchestrator Agent) |
| Required role | `ORCHESTRATOR` |
| Depends on | — |
| Accepted inputs | `job_state`, `policies`, `approved_artefacts` |
| Produced outputs | `commands`, `next_state_proposal`, `clarification_request` |
| Tool allow-list | `workflow.read`, `workflow.transition`, `agent.invoke`, `clarification.create` |

## What this agent does with the skill

The source skill is one stage-based, checkpoint-driven, state-file-backed workflow:

`INTAKE → SCAFFOLD → GENERATE → HANDOFF`, each ending in a `[CHECKPOINT]` that is written to `state.json` so the migration can resume.

The orchestrator runs that sequence by invoking the agent that owns each part. It never does a stage's work itself.

| Source-skill stage | Owning agent skill |
|---|---|
| INTAKE — repos, identity, the 5 user inputs, toolchain, pre-flight | `iib-to-ms-repository-intake` |
| INTAKE — backend-type detection (Batch B) | `iib-to-ms-ace-analysis` |
| Framework / structural conventions to generate against | `iib-to-ms-framework-baseline` |
| What is being migrated, to what, what is unresolved | `iib-to-ms-migration-specification` |
| Task graph, construct mapping, risks | `iib-to-ms-planning` |
| SCAFFOLD, GENERATE, JUnit files, HANDOFF build + config reconcile | `iib-to-ms-code-generation` |
| HANDOFF-C smoke test (one real API call) | `iib-to-ms-test-engineering` |
| Comparing actual vs expected response | `iib-to-ms-variance-analysis` |
| Diagnosing why they differ | `iib-to-ms-rca` |
| Fix-and-retry (max 3), residual GAPs | `iib-to-ms-remediation` |
| Mandatory-rule and checklist gates, hard constraints | `iib-to-ms-governance` |
| Evidence tiers and citation | `iib-to-ms-knowledge-evidence` |

## Rules the orchestrator applies

1. **Resume before restarting.** First action on any re-invocation: look for `<target>/.iib2spring/state.json`; if a flow is `in_progress` or `pending`, resume at its `last_checkpoint`. See `references/state-file-and-resumption.md`.
2. **Checkpoints are stops.** Do not advance past a `[CHECKPOINT]` until the governance skill's checklist for that checkpoint is clean. Never present uncompiled code at a checkpoint.
3. **Ambiguity becomes a clarification, not a guess.** Raise one (this agent holds `clarification.create`) when:
   - backend detection is ambiguous, or the signal is RECAP / STRATUS / DB2 / plain MQ — the source skill marks these "not yet covered, flag as gap, ask before proceeding";
   - any of the 5 mandatory INTAKE inputs is missing (the intake agent reports it as a gap; it cannot raise the question itself);
   - a reference file is silent and the answer would be an invented field mapping.
4. **Respect the definition of done.** The goal is correct structure, correct setup files and a working call to the backend; exact business-logic fidelity is fixed after testing. See `references/definition-of-done.md`.
5. **Respect the scope boundary.** The job ends at HANDOFF plus one narrow smoke test. Bulk or regression comparison belongs to the separate `iib-ms-testing` skill. See `references/scope-boundary.md`.
6. **Never** `git commit`, `git push` or `mvn deploy` without being explicitly asked.
7. **Close with action items.** Collect every item needing a human (credential values, queue names, QL/reference-data confirmations, Kong ownership of token validation, un-confirmed Akeyless path, and — every migration — the deferred exception handler). Format is in `references/handoff-and-action-items.md`.

## References

Owned by this skill:

- `references/state-file-and-resumption.md` — `state.json` schema and resumption protocol
- `references/definition-of-done.md` — what "done" means (source `SKILL.md` §2)
- `references/scope-boundary.md` — what is in and out of scope
- `references/handoff-and-action-items.md` — HANDOFF steps and output format / action-items list

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/verification-checklists.md` — checks to pass before each checkpoint
- `references/hard-constraints.md` — constraints that hold across every stage

## Adaptation note

The source skill assumed a single interactive agent that both asks the user and acts. In the factory the orchestrator holds `workflow.*`, `agent.invoke` and `clarification.create` only, so "ask the user" in the source maps to "raise a clarification" here, and every stage's actual work is delegated through `agent.invoke`.
