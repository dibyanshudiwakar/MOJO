# ACE Migration Factory — Design Document (HLD + LLD)

> Governed, agentic IBM ACE → Spring Boot migration platform.
> Reference implementation: Python standard-library HTTP server, pluggable SQL persistence, 14 governed agents, a 6-gate (G0–G5) approval lifecycle, and a model gateway seam.

---

## 1. Purpose & Scope

The ACE Migration Factory automates the migration of IBM App Connect Enterprise (IBM Integration Bus / IIB) integration flows to Spring Boot microservices **under enterprise governance**. Every state change is gated, every artefact is hashed and bound to the approval that authorised it, and every action is recorded in an append-only audit ledger.

The system is deliberately a **governed orchestration layer, not a simulator**: identity, Git, CI/CD, object storage, the ACE runtime, the model gateway and scanners are *boundaries* (adapters), not mock implementations. When a boundary (e.g. a model provider) is unavailable, the pipeline **stops** rather than silently producing an unverified result.

### In scope
- Repository intake, dormancy assessment, ACE semantic analysis, framework baselining
- Migration specification (schema-validated YAML), planning, Spring code generation
- Build/test execution, differential testing, variance analysis, RCA, remediation
- 6-gate approval governance (G0–G5) with quorum, iteration-binding and evidence preconditions
- Role-based access control, model-gateway routing, append-only audit trail

### Out of scope (boundaries)
Production orchestration connectors, real ACE runtime, real CI/CD, secret storage backends — all represented as logical references / adapter seams.

---

## 2. High-Level Design (HLD)

### 2.1 System Context

```mermaid
graph TB
    subgraph Users
        OP[Migration Operator]
        GA[Gate Approvers<br/>G0-G5 roles]
        ADM[Governance Admin]
    end

    subgraph Factory["ACE Migration Factory (single process)"]
        UI[Static Web Console<br/>app/static]
        API[HTTP API Server<br/>app/server.py]
        ORCH[Orchestration &<br/>Agent Registry]
        WORK[Background Worker<br/>app/worker.py]
        GOV[Governance:<br/>Gates / RBAC / Audit]
        DB[(SQL Store<br/>SQLite/Postgres)]
    end

    subgraph Boundaries["External Boundaries (adapters)"]
        GH[GitHub]
        MG[Model Gateway<br/>LMS → Bedrock → Claude]
        ACE[ACE Runtime]
        CICD[CI/CD / Delivery]
    end

    OP --> UI
    GA --> UI
    ADM --> UI
    UI --> API
    API --> ORCH
    API --> GOV
    ORCH --> WORK
    ORCH --> DB
    GOV --> DB
    WORK --> DB
    ORCH -.adapter.-> MG
    API -.adapter.-> GH
    WORK -.adapter.-> ACE
    GOV -.adapter.-> CICD
```

### 2.2 Layered Architecture

```mermaid
graph TD
    subgraph L1["Presentation Layer"]
        A1[Static SPA — index.html / app.js]
    end
    subgraph L2["API Layer — app/server.py"]
        B1[ThreadingHTTPServer + Router]
        B2[Auth / JWT bearer + throttle]
        B3[Route handlers v2…v7]
    end
    subgraph L3["Orchestration Layer"]
        C1[MigrationOrchestratorAgent]
        C2[AgentRegistry.dispatch]
        C3[WorkerLoop — queue drain]
        C4[PipelineService]
    end
    subgraph L4["Agent Layer — app/agents (14 workers)"]
        D1[BaseAgent + AgentContext]
        D2[ReasoningGateway / artefact.produce]
    end
    subgraph L5["Domain Layer — app/domain"]
        E1[State Machine]
        E2[Preconditions / Evidence Bundles]
        E3[Model / Contracts]
    end
    subgraph L6["Service Layer — app/services (~60 modules)"]
        F1[FactoryService — gates/transitions]
        F2[Evidence / Policy / Specification]
        F3[Differential / RCA / Remediation]
        F4[Platform v7 / Connectors / Learning]
    end
    subgraph L7["Persistence Layer"]
        G1[Repository]
        G2[Driver seam — SQLite/Postgres]
        G3[(Database)]
    end
    subgraph L8["Provider Layer — app/providers"]
        H1[default_provider]
        H2[cvscode gateway adapter / http adapter]
    end

    A1-->B1-->B3-->C2
    B3-->F1
    C1-->C2-->D1-->D2-->H1-->H2
    C2-->F2
    C3-->C4-->C2
    F1-->E1 & E2
    D1-->F1
    F1-->G1-->G2-->G3
```

### 2.3 Core Design Principles

| Principle | Mechanism |
|-----------|-----------|
| No unsigned promotion | Every gated transition re-checks quorum, iteration & evidence bindings in `FactoryService.transition` |
| Immutable evidence | SHA-256 `content_hash`; approvals bind to `(evidence_id, hash)`; a changed hash invalidates the approval |
| Contract governance | `config/agent-catalogue.v1.json` is the source of truth; a changed contract hash blocks startup (`agent_contract_drift`) |
| Fail-closed model path | No provider ⇒ `ArtefactUnavailable` ⇒ agent FAILS. The factory never silently computes an unverified artefact |
| RBAC is never relaxed for autonomy | Autonomous runs act as a service principal and are checked against each agent's `required_role` |
| Append-only audit | `audit_ledger` is a hash-chained ledger (`previous_hash` → `entry_hash`) |

---

## 3. Migration Lifecycle & Gates

### 3.1 State Machine (`app/domain/state_machine.py`)

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> INTAKE_VALIDATED
    INTAKE_VALIDATED --> DORMANCY_ASSESSED
    INTAKE_VALIDATED --> FRAMEWORK_BASELINED: G0 (skip dormancy)
    DORMANCY_ASSESSED --> FRAMEWORK_BASELINED: G0
    FRAMEWORK_BASELINED --> ACE_ANALYSED: G1
    ACE_ANALYSED --> PLAN_APPROVED: G2
    PLAN_APPROVED --> IMPLEMENTING: G3
    IMPLEMENTING --> BUILD_VALIDATED
    BUILD_VALIDATED --> DIFFERENTIAL_TESTING
    DIFFERENTIAL_TESTING --> VARIANCE_REVIEW
    VARIANCE_REVIEW --> REMEDIATING: G4 CONTINUE_FEEDBACK
    VARIANCE_REVIEW --> READY_FOR_PROMOTION: G4 ACCEPT_EXCEPTION
    REMEDIATING --> IMPLEMENTING: new iteration
    READY_FOR_PROMOTION --> COMPLETED: G5 PROMOTE
    COMPLETED --> [*]

    VARIANCE_REVIEW --> ROLLED_BACK
    IMPLEMENTING --> ROLLED_BACK
    IMPLEMENTING --> FAILED
    FAILED --> IMPLEMENTING: rework
    CREATED --> STOPPED
```

> `WAITING_G0…WAITING_G5` are explicit holding states. When an agent cannot proceed because a gate is unsigned, the job parks in a `WAITING_Gx` state; an approval that satisfies quorum auto-advances it (`FactoryService.approve` → `_WAITING_TARGETS`).

### 3.2 Gate Map (`FactoryService.GATE_TRANSITIONS`)

| Gate | Transition | Approver Role | Allowed Decisions |
|------|-----------|---------------|-------------------|
| G0 | DORMANCY/INTAKE → FRAMEWORK_BASELINED | SERVICE_OWNER | PROCEED / STOP / CLARIFY |
| G1 | FRAMEWORK_BASELINED → ACE_ANALYSED | JAVA_ARCHITECT | APPROVE / REJECT / CLARIFY |
| G2 | ACE_ANALYSED → PLAN_APPROVED | ACE_SME | APPROVE / REJECT / CLARIFY |
| G3 | PLAN_APPROVED → IMPLEMENTING | PRODUCT_OWNER | APPROVE / REJECT / CLARIFY |
| G4 | VARIANCE_REVIEW → REMEDIATING / READY_FOR_PROMOTION | QA_LEAD | CONTINUE_FEEDBACK / STOP / ACCEPT_EXCEPTION |
| G5 | READY_FOR_PROMOTION → COMPLETED | RELEASE_MANAGER | REPEAT / STOP / PROMOTE |

### 3.3 Evidence-Bundle Preconditions (`app/domain/preconditions.py`)

An approval says *who* agreed; the evidence bundle says *what* they agreed about. Enforced on the three high-risk transitions:

| Target State | Required Bundle |
|--------------|----------------|
| `VARIANCE_REVIEW` | differential package exists & all cases executed |
| `READY_FOR_PROMOTION` | differential executed + quality run not FAIL |
| `COMPLETED` | differential **dispositioned** + quality pass + **no OPEN HIGH security findings** + rollback pointer + version snapshot |

---

## 4. The 14 Governed Agents

### 4.1 Agent Catalogue & Dependency Graph

```mermaid
graph LR
    RI[repository-intake] --> DI[dormancy-intelligence]
    RI --> FB[framework-baseline]
    RI --> AA[ace-analysis]
    AA --> MS[migration-specification]
    AA --> TE[test-engineering]
    MS --> PL[planning]
    PL --> CG[code-generation]
    CG --> FE[feedback]
    TE --> VA[variance-analysis]
    VA --> RC[rca]
    RC --> RM[remediation]
    GV[governance]:::util
    KE[knowledge-evidence]:::util
    MO[migration-orchestrator]:::orch

    classDef util fill:#eef,stroke:#88a
    classDef orch fill:#efe,stroke:#8a8
```

| Agent | Required Role | Key Outputs | Depends On |
|-------|--------------|-------------|------------|
| migration-orchestrator | ORCHESTRATOR | commands, next_state_proposal | — |
| repository-intake | REPOSITORY_INTAKE | intake_manifest, gaps | — |
| dormancy-intelligence | DORMANCY_ANALYST | classification, confidence | repository-intake |
| framework-baseline | FRAMEWORK_ANALYST | framework_template, conflicts | repository-intake |
| ace-analysis | ACE_ANALYST | analysis_document, semantic_model, ambiguities | repository-intake |
| migration-specification | SPECIFICATION_AUTHOR | migration_specification_yaml | ace-analysis |
| planning | MIGRATION_PLANNER | plan, task_graph, mapping, risk_register | migration-specification |
| code-generation | CODE_GENERATOR | source_code, tests, manifest | planning |
| feedback | FEEDBACK_ENGINEER | applied_fixes, quality_status | code-generation |
| test-engineering | TEST_ENGINEER | test_suites, execution_evidence | ace-analysis |
| variance-analysis | VARIANCE_ANALYST | variance_records, severity | test-engineering |
| rca | RCA_ANALYST | rca_hypotheses, confidence | variance-analysis |
| remediation | REMEDIATION_ENGINEER | patch_plan, code_changes, tests | rca |
| governance | GOVERNANCE_ENGINE | allow_deny_decision, evidence_requirement | — |
| knowledge-evidence | KNOWLEDGE_CURATOR | cited_context, version_lineage | — |

### 4.2 Agent Contract Enforcement (LLD)

Each worker extends `BaseAgent` and declares `AGENT_ID`, `TOOLS`, `OUTPUTS`, `PROMPT`, `SKILL`. `AgentRegistry.conformance` compares declared vs. catalogue; any extra tool/output is **drift** and refuses dispatch.

```mermaid
sequenceDiagram
    participant C as Caller (API/Orchestrator/Worker)
    participant R as AgentRegistry.dispatch
    participant Reg as agent_registry (DB)
    participant Impl as Agent impl (execute)
    participant GW as ReasoningGateway / produce
    participant Ev as EvidenceService
    participant L as audit_ledger

    C->>R: dispatch(job, agent_id, roles, identity, params)
    R->>Reg: contract(agent_id, version) — APPROVED?
    R->>R: conformance() — no tool/output drift
    R->>R: dependency_status() — prereqs COMPLETED
    R->>R: resolve LLM alias (fail-closed vs model_registry)
    R->>Reg: start() → agent_runs row (RUNNING, owner)
    R->>Impl: execute(context)
    Impl->>GW: produce(prompt, payload)
    alt provider available
        GW-->>Impl: model artefact + provenance
    else no provider / refused
        GW-->>Impl: ArtefactUnavailable → FAIL
    end
    Impl-->>R: AgentResult(outputs, evidence, clarifications)
    R->>R: verify outputs ⊆ contract; tool caps for evidence/clarification
    R->>Ev: register evidence (hash = artefact content_hash)
    R->>Reg: complete() → run COMPLETED/FAILED/CANCELLED
    R->>L: audit + learning records
    R-->>C: {run_id, status, outputs, evidence}
```

---

## 5. Orchestration & Execution Flow

### 5.1 Autonomous Orchestration Loop (`MigrationOrchestratorAgent.execute`)

```mermaid
flowchart TD
    S([Start run]) --> J[Read job state + iteration]
    J --> T{Terminal / halted?}
    T -- yes --> STOP[Stop with reason]
    T -- no --> TGT[Compute target state]
    TGT --> GOVR[Dispatch governance agent]
    GOVR --> GF{GATE_FIRST state<br/>& approval missing?}
    GF -- yes --> STOP
    GF -- no --> PEND{Pending pipeline<br/>agents for state?}
    PEND -- yes --> RUN[Dispatch each agent<br/>via AgentRegistry]
    RUN --> ERR{Failed / missing input / role?}
    ERR -- yes --> CLAR[Raise clarification / stop]
    ERR -- no --> KE[Dispatch knowledge-evidence]
    KE --> J
    PEND -- no --> ALLOW{Gate allows transition?}
    ALLOW -- no --> STOP
    ALLOW -- yes --> TR[FactoryService.transition]
    TR --> J
```

Guards: a step budget (`max_steps`, default 24), one visit per `(iteration, state, agent)`, and halt on a PAUSED checkpoint or a cancel-requested queue row. Governance is never bypassed — transition always runs `_transition_policy` + `evidence_bundle`.

### 5.2 Pipeline Worker Queue Model

```mermaid
sequenceDiagram
    participant API as POST /pipeline/run
    participant Q as workflow_queue
    participant W as WorkerLoop (daemon thread)
    participant PS as PipelineService
    participant R as AgentRegistry

    API->>Q: enqueue pipeline_step activities
    loop poll every N seconds
        W->>Q: claim(lease, activity='pipeline_step')
        alt step preflight gate (planning→G2, code-gen→G3)
            W->>W: not signed ⇒ park WAITING_APPROVAL
        else ready
            W->>R: dispatch(agent) with StepLogger + cancel probe
            R-->>W: result → outputs/evidence
            W->>PS: record pipeline_context, advance lifecycle
        end
        W->>Q: complete / park / fail (heartbeat lease)
    end
```

The worker decouples execution from the request lifetime: a pipeline keeps running after the browser tab closes. Cancellation is cooperative — checkpoints sit *between* model calls.

---

## 6. Low-Level Design (LLD)

### 6.1 Module Map

| Path | Responsibility |
|------|----------------|
| `app/server.py` | `ThreadingHTTPServer`, routing (`do_GET/POST/PUT/DELETE`), JWT auth, throttle, static files, API v2–v7 |
| `app/service.py` | `FactoryService` — job creation, gated transitions, approvals, quorum, iteration binding, rollback |
| `app/domain/state_machine.py` | States + legal transitions |
| `app/domain/preconditions.py` | Evidence-bundle checks for late-lifecycle states |
| `app/domain/model.py` | `Actor`, `AgentContract`, `ApprovalRequest`, `ComparisonPolicy` dataclasses |
| `app/agents/base.py` | `BaseAgent`, `AgentContext`, `AgentResult`, `EvidenceRef`, `Clarification` |
| `app/agents/orchestrator.py` | Autonomous lifecycle driver |
| `app/agents/*.py` | 14 agent workers (one module each, individually runnable via CLI) |
| `app/agents/reasoning.py` | Governed model access; fail-closed modes |
| `app/services/agents.py` | `AgentRegistry` — contract, conformance, dependency, dispatch, evidence, clarification reuse |
| `app/services/pipelines.py` | `PipelineService`, `StepLogger`, context accumulator |
| `app/worker.py` | `WorkerLoop` draining `workflow_queue` |
| `app/services/springgen.py` | Spring Boot project generation (largest service, ~2.4k LOC) |
| `app/services/platform_v7.py` | Registry/AI/Knowledge/Approval/Delivery/Notification/Observability/Skill services |
| `app/services/evidence.py` | SHA-256 evidence registration, immutability |
| `app/services/policy.py` | `ApprovalPolicyService` role→decision policy |
| `app/services/factory_v3.py` | `WorkflowOrchestrator`, `AuditLedger`, intake/dormancy/framework/analysis/planning services |
| `app/repository.py` | `Repository` — schema, idempotent upgrades, catalogue seeding, helpers |
| `app/persistence.py` | Driver seam (SQLite + Postgres), transaction sessions |
| `app/providers/*` | `default_provider`, cvscode gateway + HTTP adapters |

### 6.2 Approval Validity (`FactoryService._valid_approvals`)

An approval counts toward gate quorum **only if all** of:
1. its `decision` is in the gate's allowed set;
2. it was granted against the job's **current iteration** (rework retires old signatures);
3. it has not timed out (`gate_controls.timeout_minutes`);
4. every cited evidence id resolves to an **immutable** row whose `content_hash` still matches the `artifact_bindings` recorded at approval time.

Quorum counts **distinct approver identities** (`gate_controls.quorum`).

```mermaid
flowchart LR
    A[approvals rows for gate] --> D{decision allowed?}
    D -- no --> X[discard]
    D -- yes --> I{iteration == job.iteration?}
    I -- no --> X
    I -- yes --> TO{within timeout?}
    TO -- no --> X
    TO -- yes --> EV{all evidence immutable<br/>& hash matches binding?}
    EV -- no --> X
    EV -- yes --> OK[valid approval]
    OK --> Q
    QN[distinct identities] --> Q{count ≥ quorum?}
    Q -- no --> REJ[required_gate_approval_missing]
    Q -- yes --> ALLOW[ALLOW transition]
```

### 6.3 Data Model (key entities)

```mermaid
erDiagram
    JOBS ||--o{ WORKFLOW_EVENTS : emits
    JOBS ||--o{ APPROVALS : has
    JOBS ||--o{ EVIDENCE : produces
    JOBS ||--o{ AGENT_RUNS : runs
    JOBS ||--o{ CLARIFICATIONS : raises
    JOBS ||--o{ VERSIONS : snapshots
    JOBS ||--o{ DIFFERENTIAL_PACKAGES : tests
    DIFFERENTIAL_PACKAGES ||--o{ DIFFERENTIAL_EVIDENCE : cases
    JOBS ||--o{ QUALITY_RUNS : validates
    JOBS ||--o{ SECURITY_FINDINGS : scans
    APPROVALS }o--|| GATE_CONTROLS : governed_by
    AGENT_RUNS }o--|| AGENT_REGISTRY : contract
    EVIDENCE }o--|| AGENT_RUNS : from
    AGENT_REGISTRY ||--o{ SKILL_AGENT_BINDINGS : uses
    AUDIT_LEDGER }o--|| JOBS : chains

    JOBS {
        text id PK
        text service
        text state
        int iteration
        text rollback_pointer
        text environment
    }
    APPROVALS {
        text id PK
        text gate
        text decision
        text approver_identity
        text evidence_ids
        text artifact_bindings
        int iteration
    }
    EVIDENCE {
        text id PK
        text content_hash
        int immutable
        text producer
    }
    AGENT_RUNS {
        text id PK
        text agent_id
        text status
        text operator_identity
        text effective_model_alias
    }
    AUDIT_LEDGER {
        int sequence PK
        text previous_hash
        text entry_hash
    }
```

The full schema (`app/repository.py::SCHEMA`) defines ~55 tables covering governance, agents, pipelines, differential/quality, connectors, model gateway, skills, users and audit. Schema evolves via idempotent `_upgrade_*` methods (additive `ALTER TABLE`).

### 6.4 Model Gateway Path (fail-closed)

```mermaid
flowchart TD
    AG[Agent.produce prompt] --> RG[ReasoningGateway.reason]
    RG --> P{approved prompt<br/>+ model version?}
    P -- no --> U1[no_approved_prompt_or_model → FAIL]
    P -- yes --> PR{provider adapter<br/>configured?}
    PR -- no --> U2[no_provider_execution_adapter → FAIL]
    PR -- yes --> DLP{DLP / region rules pass?}
    DLP -- no --> U3[gateway_refused → scrub or FAIL]
    DLP -- yes --> CALL[Provider call<br/>LMS→Bedrock→Claude]
    CALL -- error --> U4[provider_execution_failed → retry]
    CALL -- ok --> ART[artefact + provenance<br/>mode recorded on run]
```

`default_provider()` resolves `AI_PROVIDER`: `gateway` → cvscode adapter, `module:attr` → custom, else `None` (agents fail rather than fabricate).

### 6.5 Security & RBAC

- **Auth:** console-issued HS256 bearer tokens (`FACTORY_JWT_HS256_SECRET` or generated `.signing-key`); optional OIDC/JWKS. Public routes limited to health/login/roles/platform.
- **Admin isolation:** the seeded administrator holds only admin roles and is refused all pipeline/approval operations (`admin_account_cannot_operate_pipelines`).
- **Default operator** role carries 15 operator roles; gate-approver roles granted explicitly, never self-assigned.
- **Secrets:** GitHub tokens encrypted at rest (`FACTORY_SECRET_KEY`/`.secret-key`), never returned to browser; connectors use logical secret references only.
- **Throttling:** per-address sign-in throttle (10/60s).
- **Audit:** hash-chained `audit_ledger`; immutable evidence; contract-drift halts startup.

---

## 7. Deployment View

```mermaid
graph TB
    subgraph Host["Single process (container or start.ps1)"]
        SRV[app.server — ThreadingHTTPServer :8080]
        WKR[WorkerLoop daemon thread]
        SRV --- WKR
    end
    SRV --> FILE[(factory-v2.db + WAL)]
    WKR --> FILE
    SRV -.AI_PROVIDER=gateway.-> LMS[CVS LMS Gateway → Bedrock → Claude]
    SRV -.-> GH[GitHub API]
    note["FACTORY_DB_DRIVER=postgres swaps store<br/>without code change"]
```

- **Runtime:** Python stdlib HTTP server; no external web framework. `Dockerfile` + `compose.yaml` provided.
- **Config via env:** `FACTORY_DB`, `FACTORY_DB_DRIVER/DSN`, `FACTORY_JWT_*`, `FACTORY_SECRET_KEY`, `FACTORY_ADMIN_*`, `AI_PROVIDER`, `FACTORY_REMEDIATION_CONFIDENCE_THRESHOLD`, `FACTORY_SEED_MODEL_GATEWAY`.
- **Persistence:** SQLite (WAL, busy-timeout) default; Postgres driver behind same interface.
- **Scaling:** stateless API + queue-backed worker; worker can run as a separate process (`python -m app.worker`) against the same DB.

---

## 8. Cross-Cutting Concerns

| Concern | Approach |
|---------|----------|
| Idempotency | `workflow_queue.idempotency_key`; orchestrator visit-set; one-visit-per-(iteration,state,agent) |
| Clarifications | Agents raise `Clarification`; prior answers reused (`clarification_reuse`); auto-mode can answer high-confidence ones with `AUTO_MODE` attribution |
| Observability | `StepLogger` → `pipeline_step_logs`; `observability_events`, `metrics`, structured run provenance |
| Learning | `learning.record_run` captures run outcomes/metrics for precedent reuse |
| Concurrency | SQLite WAL + 30s busy-timeout; single-writer discipline (no logging inside write transactions) |
| Evolvability | Driver seam; additive schema upgrades; version-controlled agent catalogue & prompts |

---

## 9. Key Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Unsigned promotion | Gate quorum + iteration binding + evidence preconditions, re-checked on every transition |
| Silent model fallback | Fail-closed gateway; no provider ⇒ agent FAILS, mode recorded |
| Contract drift | Startup hash check halts; dispatch conformance check refuses drifted workers |
| Evidence tampering | Immutable rows + hash-bound approvals; hash-chained ledger |
| Orphaned RUNNING rows | Cooperative cancellation closes runs CANCELLED/FAILED, never left RUNNING |
| SQLite lock contention | WAL, busy-timeout, batched single-writer transactions |

---

*Generated from source analysis of `app/` (server, domain, agents, services, providers, persistence), `config/agent-catalogue.v1.json`, and `README.md`.*
