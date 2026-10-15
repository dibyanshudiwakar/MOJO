# Agent Development Guide

## Purpose

This document explains how the 14 architecture-defined agents are declared,
implemented, dispatched and tested in the ACE Migration Factory.

There are two halves, and keeping them apart is the whole design:

- **The contract** is declarative and version controlled
  (`config/agent-catalogue.v1.json`). It governs *who may run an agent and what it
  may reach*.
- **The worker** is operational code (`app/agents/<name>.py`). It performs the
  business operation and returns traceable evidence.

Neither trusts the other. The registry refuses a worker that requests a tool its
approved contract withholds; the worker cannot grant itself a capability by
returning something the contract does not declare.

## Agent contracts

Contracts live in:

```text
config/agent-catalogue.v1.json
```

`Repository.init()` seeds them into `agent_registry` and computes a contract hash.
If a registered contract changes without a versioned catalogue update, startup
raises `agent_contract_drift` and the factory does not come up.

Each entry declares `agent_id`, `display_name`, `version`, `accepted_inputs`,
`produced_outputs`, `required_role`, `tool_allowlist`, `dependencies`, `status`,
`effective_from`, `approved_by`, `approval_evidence_id`, `supersedes_version` and
`enabled`.

## Registered agents and their workers

| Agent ID | Required role | Worker module | Wraps |
| --- | --- | --- | --- |
| `migration-orchestrator` | `ORCHESTRATOR` | `app/agents/orchestrator.py` | the registry itself |
| `repository-intake` | `REPOSITORY_INTAKE` | `app/agents/repository_intake.py` | `IntakeService` |
| `dormancy-intelligence` | `DORMANCY_ANALYST` | `app/agents/dormancy_intelligence.py` | `DormancyService` |
| `framework-baseline` | `FRAMEWORK_ANALYST` | `app/agents/framework_baseline.py` | `PriorityOneService.baseline_framework` |
| `ace-analysis` | `ACE_ANALYST` | `app/agents/ace_analysis.py` | `PriorityOneService.analyze_ace` |
| `migration-specification` | `SPECIFICATION_AUTHOR` | `app/agents/migration_specification.py` | `SpecificationService` |
| `planning` | `MIGRATION_PLANNER` | `app/agents/planning.py` | `MappingAndPlanningService` |
| `code-generation` | `CODE_GENERATOR` | `app/agents/code_generation.py` | `generate_spring`, `quality_validate` |
| `test-engineering` | `TEST_ENGINEER` | `app/agents/test_engineering.py` | `prepare_differential`, runtime adapters |
| `variance-analysis` | `VARIANCE_ANALYST` | `app/agents/variance_analysis.py` | `DifferentialService`, `record_differential` |
| `rca` | `RCA_ANALYST` | `app/agents/rca.py` | `RCAService`, `ConfidenceRemediationService` |
| `remediation` | `REMEDIATION_ENGINEER` | `app/agents/remediation.py` | `ConfidenceRemediationService`, `RCAService` |
| `governance` | `GOVERNANCE_ENGINE` | `app/agents/governance.py` | `GATE_TRANSITIONS`, `evidence_bundle` |
| `knowledge-evidence` | `KNOWLEDGE_CURATOR` | `app/agents/knowledge_evidence.py` | `KnowledgeService` |

`app/agents/__init__.py` maps each `agent_id` to its implementation as a *string*
and imports lazily, so `app.services.agents` can resolve workers while
`app.agents.orchestrator` imports the registry without a circular import.

## Implementing a worker

Subclass `BaseAgent` (`app/agents/base.py`) and declare what the implementation
actually does:

```python
class ACEAnalysisAgent(BaseAgent):
    AGENT_ID = 'ace-analysis'
    VERSION = '1.0.0'
    TOOLS = ('repository.read', 'parser.execute', 'evidence.write', 'clarification.create')
    OUTPUTS = ('analysis_document', 'semantic_model', 'ambiguities')
    PROMPT = 'agent/ace-analysis/ambiguity-triage'

    def execute(self, context):
        ...
        return AgentResult(outputs={...}, evidence=(EvidenceRef(...),))
```

`execute` receives an `AgentContext` (`repo`, `job_id`, `identity`, `roles`,
`params`, `iteration`, `correlation_id`, `provider`) and returns an `AgentResult`.
`context.param('name')` raises `missing_param:<name>` when a required parameter is
absent, so the CLI and the HTTP dispatch validate identically.

The context is deliberately narrow: an agent gets the job *id*, not the job row,
and parameters, not a request body, so it cannot act on a caller-supplied
identity, role or project scope. Those were verified before it was constructed.

### Conformance

`AgentRegistry.conformance()` compares every implementation against its approved
contract and reports `CONFORMANT`, `DRIFT` or `NOT_IMPLEMENTED`:

- `TOOLS` must be a subset of the contract's `tool_allowlist`
- keys in `AgentResult.outputs` must be a subset of `produced_outputs`
- `AGENT_ID` and `VERSION` must match the registered contract

`dispatch` refuses a drifting worker with `agent_implementation_drift`. Exposed at
`GET /api/v2/agent-registry/implementations`.

## Dispatching a run

`AgentRegistry.dispatch(job_id, agent_id, version, roles, identity, params, ...)`
performs the full governed flow:

1. Resolve the approved contract; refuse a drifting implementation.
2. Verify every contract dependency has a `COMPLETED` run **for this job**
   (`agent_dependency_not_satisfied`). Reportable without running anything via
   `GET /api/v2/agent-registry/{agent_id}/dependencies?job_id=...`.
3. `start()` — role check, tool-allow-list check, input-evidence check, and an
   `agent_runs` row owned by the calling principal.
4. `execute()`.
5. Gate the result against the contract's capabilities, register the produced
   artefacts as evidence, and record any clarifications.
6. `complete()` with the evidence ids, or with the failure reason.
7. Emit an `AGENT_RUN_COMPLETED` workflow event and an audit-ledger entry carrying
   the agent id and version, params hash, requested tools, reasoning mode and
   model provenance.

An agent that raises is recorded as a `FAILED` run with an `AGENT_RUN_FAILED`
event, and the exception is re-raised rather than swallowed.

### Capabilities are enforced on the result

- Returning `evidence` requires `evidence.write` in the contract.
- Returning `clarifications` requires `clarification.create`.

This is why `repository-intake` reports its gaps as an *output* rather than raising
clarifications: its contract grants `repository.read` and `evidence.write` only.
It is also why `variance-analysis`, `rca`, `governance` and `knowledge-evidence`
register no evidence — they hold `variance.write`, `rca.write`, `audit.write` and
`index.write` respectively.

### The evidence loop and gate binding

Agents that hold `evidence.write` register each artefact through
`EvidenceService`, choosing the content so that `digest(content)` equals the
artefact's own stored `content_hash`:

| Agent | Evidence kind | Content |
| --- | --- | --- |
| `repository-intake` | `INTAKE_MANIFEST` | the manifest `inventory` |
| `dormancy-intelligence` | `DORMANCY_ASSESSMENT` | the canonical assessment payload |
| `framework-baseline` | `FRAMEWORK_TEMPLATE` | `template_md` (binds G1) |
| `ace-analysis` | `ACE_ANALYSIS` | `semantic_model` (binds G2) |
| `migration-specification` | `MIGRATION_SPECIFICATION` | `yaml_text` (binds G2) |
| `code-generation` | `GENERATED_PROJECT` | the canonical provenance manifest |
| `test-engineering` | `RUNTIME_EXECUTION` | the executed case payload |
| `remediation` | `REMEDIATION_PROPOSAL` | the proposal payload |

That equality is what lets a **single** approval satisfy both
`FactoryService._valid_approvals` (which re-checks the evidence row's hash against
`artifact_bindings`) and `MappingAndPlanningService._approved` (which looks for the
*artefact's* hash among those same bindings). If the two hashes diverge, a gate
approval silently stops authorising the artefact it was granted for.

**G3 is the exception.** The planning agent holds `plan.write`, not
`evidence.write`, so it produces the plan, the mapping catalogue, the risk register
and the G3 approval package — but registering the plan as the evidence a G3
approval cites is the approver's act:

```python
ref = evidence.register(job_id, 'MIGRATION_PLAN', plan['version'],
                        'plan://' + plan['id'], plan['tasks'], approver)
# ref['content_hash'] == plan['content_hash']
```

## Running an agent standalone

Every worker is executable on its own:

```powershell
python -m app.agents.ace_analysis --db factory-v2.db --job <job-id> `
    --param path=workspace/ace --param version=1.0.0 --token <jwt>
```

- `--token` is the governed path: identity, roles and project/environment scope
  come from the verified bearer token, exactly as over HTTP.
- Without a token, `--roles` and `--identity` are accepted, a warning goes to
  stderr, and the actor is recorded as `cli:<identity>` with `channel=CLI` on the
  run, the workflow event and the ledger entry. This is not a way around RBAC and
  does not pretend to be: a caller who can run this command already has write
  access to the database file, so the honest control is *attribution*. A CLI run
  can produce artefacts; it cannot approve them or promote a job.
- `--direct` calls `execute()` without the registry — no run record, no evidence
  registration, no audit entry. For isolated debugging only.
- Exit codes: `0` success, `1` agent returned an error, `2` bad arguments,
  `3` identity rejected, `4` refused by governance.

## Model-assisted reasoning

Judgement-heavy agents declare a `PROMPT` and route through
`app/agents/reasoning.py`, which calls `AIRegistryService.gateway_request` — the
only place that enforces an approved prompt version, an approved model version, an
approved `model-gateway` connector profile, the data-loss rules and the region
policy. Agents hold a prompt *name*, never a credential or an endpoint.

**The gateway cannot return a completion on its own.** It records
`PROVIDER_EXECUTION_REQUIRED` and returns; there is no provider execution in this
codebase, because an external model call is an enterprise adapter boundary and
those are not simulated here. Three things follow:

1. `ProviderExecutionAdapter` is the seam. Supply an implementation as
   `context.provider`; the response is recorded through the new
   `AIRegistryService.complete_request`, which DLP-scans the *response* too — a
   provider that echoes a secret back is the same disclosure as sending one.
2. Prompts in `config/agent-prompts.v1.json` are registered through
   `POST /api/v7/prompts` and require an `AI_GOVERNANCE_ADMIN` to reach `APPROVED`.
   Out of the box none are approved.
3. Every agent keeps its deterministic, service-backed computation as the answer.

So the normal mode is `DETERMINISTIC_FALLBACK`, recorded on the run in
`agent_runs.reasoning` with full provenance in `agent_runs.model_provenance`:

| `mode` | Meaning |
| --- | --- |
| `MODEL_GATEWAY` | An approved prompt/model answered through a configured provider. |
| `DETERMINISTIC_FALLBACK` | No approved prompt/model, or no provider adapter. |
| `DETERMINISTIC_FALLBACK` + `reason: gateway_refused:...` | The gateway refused — DLP, region or approval. |

Expect refusals routinely: the `PAN` data-loss rule matches any run of 13 or more
digits, which a semantic model or a differential payload trips easily. A refusal is
a governed outcome, not an agent failure.

Where a model does answer, its output is **advisory**. The deterministic result is
what gets persisted, and a disagreement is recorded (for example
`metrics.model_agrees` on the dormancy agent) rather than resolved silently. A
classification that decides whether a migration proceeds must be reproducible from
the evidence alone.

## Autonomous orchestration

`migration-orchestrator` steps a job through the lifecycle: it asks the
`governance` agent what the next transition requires, invokes the agents that
produce the missing artefacts, indexes them, and transitions when the gate is
satisfied.

```text
POST /api/v2/agents/orchestrate
{"job_id": "...", "params": {"ace_path": "ace", "framework_path": "framework",
                             "output_root": "generated", "service_name": "orders",
                             "package_name": "com.example.orders"}}
```

What bounds it is the governance it cannot reach around. Every transition goes
through `FactoryService.transition`, so gate quorum, approval iteration, artefact
bindings and the `evidence_bundle` preconditions apply exactly as they would to a
human operator. A run from `CREATED` therefore stops at G0 rather than walking a
job to `COMPLETED`. Reaching `COMPLETED` takes seven orchestration runs
interleaved with six gate approvals — see
`tests/agents/test_agent_orchestrator.py::test_a_full_governed_lifecycle_reaches_completed`.

Guards, which are runaway protection rather than governance:

- a step budget (`max_steps`, default 24)
- one visit per `(iteration, state, agent)`, plus a skip for any pipeline agent
  already `COMPLETED` at the current iteration — otherwise a second run would
  produce a second framework baseline that the existing G1 approval is not bound
  to, and the artefact the approval covers would stop being the one the next agent
  reads
- halt on a `PAUSED` workflow checkpoint or a `cancel_requested` queue activity
- `invoke_agents=false` / `apply_transition=false` for a propose-only run

**RBAC is not relaxed for autonomy.** Each invoked agent's `required_role` is
checked against the orchestration principal's roles, because the run acts as that
principal throughout. An autonomous run needs a service principal holding the roles
of the agents it invokes; when one is missing the loop stops with
`blocked:<agent>:required_agent_role_missing` rather than escalating its own
privileges.

### Ordering: gate-first versus produce-first

Most states run their agents *before* the gate is evaluated, because what the
agents produce is what the gate decides on — RCA informs the G4 decision, and
variance analysis produces the differential evidence the `VARIANCE_REVIEW`
precondition looks for. Requiring the gate first would deadlock those states.

`GATE_FIRST = {'ACE_ANALYSED', 'PLAN_APPROVED'}` is the exception: `planning`
requires artefact-bound G1 and G2 approvals and `generate_spring` requires G3, so
invoking them earlier would surface a governance refusal as an agent failure.

## API surface

```text
GET  /api/v2/agent-registry
GET  /api/v2/agent-registry/health
GET  /api/v2/agent-registry/graph
GET  /api/v2/agent-registry/implementations
GET  /api/v2/agent-registry/{agent_id}/dependencies?job_id=...
POST /api/v2/agents/dispatch          # run the factory's own worker
POST /api/v2/agents/orchestrate       # autonomous stepping
POST /api/v2/agents/start             # external worker: governed admission
POST /api/v2/agents/{run_id}/complete # external worker: governed completion
```

Agent parameters are a request body reaching a service directly, so `dispatch` and
`orchestrate` confine them: `path`, `golden_path`, `ace_path`, `framework_path`,
`project_root` and `output_root` go through `workspace.resolve`, and
`ace_base_url` / `spring_base_url` through `workspace.runtime_url` (https, dotted
public hostname, no embedded credentials, no loopback or link-local literal —
otherwise a token holder could aim the factory at a cloud metadata endpoint and
read the response back out of the differential evidence).

`start`/`complete` remain for an external worker performing its own execution.
Both now carry the operator identity, so a run has an owner and
`agent_run_not_owned` is refused. Note that `start` deliberately does **not**
enforce dependencies — it is the contract-level admission check, and dependency
validation is a worker-flow step. Query the dependencies endpoint first.

## Deliberate worker boundaries

- **`remediation` writes no source**, though its contract grants
  `repository.write`. Generated code comes from `code-generation` under an approved
  plan with a provenance manifest, and generated artefacts carry
  `GENERATED_REGION_LOCKED`. What remediation produces is the change set an
  approved plan should carry.
- **`test-engineering` claims no execution without adapters.** With neither
  `ace_adapter`/`spring_adapter` nor base URLs, it prepares the package and reports
  `replay: NOT_EXECUTED`. The `evidence_bundle` precondition on `VARIANCE_REVIEW`
  then refuses the transition, which is exactly what it exists to catch. It also
  merges `$.adapter`, `$.correlation_id` and `$.latency_ms` into the comparison
  policy's `ignore` list — these can never be equal between two runtimes, and
  comparing them would report a variance for every case and bury the real ones.
- **`variance-analysis` writes both variance stores.** `differential_evidence` is
  what the promotion preconditions inspect; `variances` is what RCA reads. Both are
  backed by the same `DifferentialEvidenceEngine` and policy, so they cannot
  disagree; writing only one would blind one of them.
- **`rca` measures every confidence component** as a ratio over the job's own
  records. The score gates direct remediation via
  `ConfidenceRemediationService.eligibility`, so a component the agent could choose
  freely would let it authorise its own fix.
- **`remediation`'s threshold is configuration**
  (`FACTORY_REMEDIATION_CONFIDENCE_THRESHOLD`), never a parameter.
- **`governance` re-implements no rule.** Legality comes from
  `state_machine.TRANSITIONS`, gates from `GATE_TRANSITIONS`, quorum and binding
  validity from `FactoryService._valid_approvals`, and evidence from
  `preconditions.evidence_bundle`. A second implementation could disagree with the
  gate itself, which is worse than no answer.

## Adding or updating an agent

1. Add or update the entry in `config/agent-catalogue.v1.json`; assign a new
   `version` for any behavioural or contract change.
2. Define a least-privilege `required_role` and `tool_allowlist`, and declare all
   prerequisite agents in `dependencies`.
3. Add the worker module and register it in `app/agents/__init__.py`.
4. Keep `TOOLS` and `OUTPUTS` within the contract, or `dispatch` will refuse the
   worker.
5. Add `tests/agents/test_agent_<name>.py`. Test basenames must stay unique across
   the whole suite — pytest imports them by basename, as there is no
   `tests/agents/__init__.py`.
6. Run the suite: `.\.venv\Scripts\python.exe -m pytest -q`.

Never modify the database registry directly. Registry rows are seeded from the
version-controlled catalogue and contract drift is detected at startup.

## What the tests cover

`tests/agents/` holds one module per agent (each seeding only what that agent
needs and calling `execute` directly); `tests/test_agent_registry.py` covers
conformance, dependency enforcement, capability gating, run ownership, the
evidence loop, input-evidence validation and the DLP-refusal path;
`tests/test_agent_cli.py` covers the standalone runner; `tests/test_agent_api.py`
drives the routes against a live server process.

Proven: contract conformance for all 14, dependency ordering, RBAC, tool
allow-lists, evidence-hash equality with artefact hashes, run ownership,
workspace and runtime-URL confinement, the gateway path with a fake provider,
deterministic fallback, and a full `CREATED` → `COMPLETED` lifecycle through
agents and real gate approvals.

Not proven: that a real model provider produces useful output (none is shipped),
that a real ACE or Spring runtime replays correctly (adapters are doubles in
tests), or that the enterprise scanner, SAST and licence tools run — those remain
`NOT_CONFIGURED` in the quality gate.

## Design principle

Keep the catalogue declarative and version controlled. Keep workers operational and
testable. The registry governs who may run an agent and what it may access; the
worker performs the business operation and returns traceable evidence. Where the
two disagree, the catalogue wins.

## Agent skills

`agent-skills/` holds one self-contained skill per agent, split from
`external-skills/iib-to-ms-migration` (see `agent-skills/README.md`). A skill is
**guidance for a model-assisted step, not authority**: it grants no tool, changes
no contract and never replaces the deterministic result.

```python
agent.skill                 # verified Skill for this agent, or None (e.g. dormancy-intelligence)
context.skill()             # same, for the agent being run
context.skill_provenance()  # {'skill_name', 'skill_hash'} | {'skill_error'} | {}  -- never raises
```

- **Declared per agent.** Every worker sets `SKILL = 'iib-to-ms-<agent-id>'` next to
  `AGENT_ID`; `dormancy-intelligence` sets `SKILL = None` (the source skill has no
  dormancy content). `AgentRegistry.conformance()` reports `skill_mismatch:` drift when
  a declaration disagrees with `skills-map.json`. An undeclared `SKILL = ''` is not checked.
- **Active on every run.** `dispatch` resolves the agent's skill and records its name
  and hash on the run (`agent_runs.model_provenance`), on **every evidence row the
  agent writes** (`metadata.skill`) and in the `AGENT_RUN_COMPLETED` audit entry --
  for all agents, whether or not they reason through the gateway.
- **Strict mode.** Set `FACTORY_REQUIRE_AGENT_SKILLS=1` and `dispatch` refuses an
  agent whose skill is missing or modified (`agent_skill_unavailable:...`) before a
  run is started. Off by default: a skill is guidance, so by default a damaged one is
  recorded as `skill_error` and the run continues.
- **Integrity.** `agent-skills/skills-map.json` records a SHA-256 for every file.
  `app/agents/skills.py` verifies them on load (CRLF and LF compare equal, because git on
  Windows with `core.autocrlf` checks the files out as CRLF; any content change still fails); a missing or edited file raises
  `SkillIntegrityError`. Check the whole library with
  `python -m app.agents.skills --verify`.
- **Recorded, not silent.** `ReasoningGateway` adds `skill_name` and `skill_hash`
  to the model provenance in every mode, and `AGENT_RUN_COMPLETED` carries
  `skill`. A damaged skill is recorded as `skill_error` and the step carries on
  deterministically.
- **Opt-in to the provider.** `Skill.as_system_prompt()` renders SKILL.md plus an index of its references for an adapter to use.  A skill is passed as `invoke(template, payload,
  skill=skill)` only to an adapter that sets `accepts_skill = True`; existing
  `invoke(template, payload)` adapters are called as before. The skill travels
  outside `payload` because the payload is job evidence that the gateway
  DLP-scans, whereas the skill is static reviewed content. The record notes
  `skill_included`.
- **Location.** `agent-skills/` by default; override with
  `FACTORY_AGENT_SKILLS_DIR`.
- **Parity analysis (variance-analysis, opt-in).** `parity=true` adds a static
  IIB-source-versus-migrated-code comparison, guided by `parity-skill.md` (sourced from
  `external-skills/gap-analysis/`). It is model-only and uses its own governed prompt,
  `agent/variance-analysis/parity-check`, which an `AI_GOVERNANCE_ADMIN` must register and
  approve like any other (otherwise the step reports `NOT_ANALYSED`). For that one call the
  parity skill is the guidance in place of the agent's general `SKILL.md`
  (`Skill.focus()` / `ReasoningGateway.reason(..., skill=)`), because its output contract
  differs from the `severity-rationale` step's. Inputs are the job's own intake source and
  generated artefacts. The report is stored as a `PARITY_ANALYSIS` validation report; it is
  not an agent output and never enters the variance store.
- **Hardcoding check (variance-analysis, opt-in).** `hardcoding=true` adds a narrower, Java-only
  comparison (transaction flow, integration points, field mappings), guided by
  `hardcoding-skill.md` (from `external-skills/hardcoding/`), with its own governed prompt
  `agent/variance-analysis/hardcoding-check` and a stored `HARDCODING_ANALYSIS` report. It shares
  the parity step's machinery (`CHECKS` in `app/agents/variance_analysis.py`), so the same rules
  hold: model-only, advisory, never in the variance store or the agent's outputs.
- **Editing.** Change an owning file or a `SKILL.md`, then run
  `python scripts/sync_agent_skills.py`. `--check` fails on drift and is run by
  `tests/agents/test_agent_skills.py`.
