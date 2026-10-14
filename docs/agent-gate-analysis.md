# ACE Migration Factory — Agent Gate & Clarification Analysis

## Overview

There are **14 agents** in the system. **10 can run freely** without any gate approval.
Only **3 agents hard-fail** if their required gate has not been approved.

---

## Full Agent List

| Code | Agent | Gate Required to RUN | Creates Clarifications | Blocked by Open Clarifications |
|------|-------|----------------------|------------------------|-------------------------------|
| A01 | Migration Orchestrator | No | Yes (can raise them) | No |
| A02 | Repository Intake | No | No (by design — gaps go as outputs only) | No |
| A03 | Dormancy Intelligence | No | No | No |
| A04 | Framework Baseline | No | **Yes** — conflicts become clarifications | No |
| A05 | ACE Analysis | No | **Yes** — unsupported ACE constructs become clarifications | No |
| A06 | Migration Specification | No | **Yes** — parser ambiguities become clarifications | No |
| A07 | Planning | **G1 + G2 both required** | Yes (unsupported mappings) | **YES — hard stop** |
| A08 | Code Generation | **G3 required** | No | No |
| A09 | Test Engineering | No own gate check (runs at BUILD_VALIDATED state → advances to DIFFERENTIAL_TESTING) | No | No |
| A10 | Variance Analysis | No | No | No |
| A11 | RCA | No | No | No |
| A12 | Remediation | G4 (ACCEPT_EXCEPTION path only) | No | No |
| A13 | Governance | No | No | No |
| A14 | Knowledge Evidence | No | No | No |

---

## Agents Blocked by Gates (Hard Fails)

### Planning (A07)
- Requires **G1** — framework artifact approved (artifact-bound)
- Requires **G2** — analysis + specification artifacts approved (both artifact-bound)
- Requires **zero open clarifications**
- Raises `PermissionError` immediately if any of the three conditions are unmet
- The orchestrator also will not invoke Planning until G2 is satisfied (`GATE_FIRST` rule)
- Source: `app/agents/planning.py`, `app/services/migration_lifecycle_v6.py:54-57`

### Code Generation (A08)
- Requires **G3** — plan approval (artifact-bound)
- Raises `PermissionError` immediately if G3 is not approved
- The orchestrator also will not invoke Code Generation until G3 is satisfied (`GATE_FIRST` rule)
- Source: `app/agents/code_generation.py`, `app/services/factory_v3.py:153-154`

### Remediation (A12) — partial
- Only the `ACCEPTED_EXCEPTION` disposition path requires **G4 ACCEPT_EXCEPTION** decision
- The agent can start running; G4 check fires only if it needs to record an exception acceptance
- Source: `app/services/migration_lifecycle_v6.py:149-150`

### Test Engineering (A09)
- No own gate check. Runs when the job reaches `BUILD_VALIDATED` state (produced by Code Generation completing with a passing build). Its lifecycle target is `DIFFERENTIAL_TESTING`.
- Blocked in practice only because Code Generation (A08) must complete first.

---

## Open Clarification Rules

Clarifications are questions raised by agents when they encounter constructs they cannot automatically resolve (e.g. unsupported custom ACE nodes, framework conflicts, parser ambiguities).

### Which agents CREATE clarifications
| Agent | When |
|-------|------|
| Framework Baseline (A04) | Framework conflict detected in reference repo |
| ACE Analysis (A05) | Unsupported or custom ACE construct found |
| Migration Specification (A06) | Parser ambiguity encountered |
| Planning (A07) | Unsupported mapping found during catalogue creation |
| Migration Orchestrator (A01) | Can escalate gaps to clarifications if judged critical |

> **Note:** Repository Intake (A02) deliberately does NOT create clarifications — gaps are returned as step outputs only. This is by design so a single repo gap does not block all downstream agents. The Orchestrator (A01) can still escalate a gap to a clarification if it judges it critical.

### Which agents are STOPPED by open clarifications
Only **Planning (A07)** checks for open clarifications before running:

```python
# migration_lifecycle_v6.py:56-57
open_count = repo.one("select count(*) n from clarifications where job_id=? and status='OPEN'")['n']
if open_count:
    raise ValueError('open_clarifications_block_planning')
```

All other agents run regardless of open clarifications.

---

## Pipeline Phase Flow

```
PHASE 1 — Runs freely (no gates, no clarification check)
─────────────────────────────────────────────────────────
  Repository Intake (A02)
  Dormancy Intelligence (A03)
  Framework Baseline (A04)      ← may CREATE clarifications
  ACE Analysis (A05)    ┐       ← may CREATE clarifications  [run in PARALLEL]
  Migration Spec (A06)  ┘       ← may CREATE clarifications  [run in PARALLEL]

GATE G0 → lifecycle advances: DORMANCY_ASSESSED → FRAMEWORK_BASELINED
GATE G1 → lifecycle advances: FRAMEWORK_BASELINED → ACE_ANALYSED
           (also required as artifact binding by Planning)
GATE G2 → lifecycle advances: ACE_ANALYSED → PLAN_APPROVED
           (also required as artifact binding by Planning)
  + All clarifications must be ANSWERED before Planning runs

PHASE 2 — Gated (requires human approval before agent is invoked)
─────────────────────────────────────────────────────────
  Planning (A07)           ← needs G1 + G2 + zero open clarifications
  Code Generation (A08)    ← needs G3

GATE G3 → lifecycle advances: PLAN_APPROVED → IMPLEMENTING

PHASE 3 — Runs freely to inform G4
─────────────────────────────────────────────────────────
  Test Engineering (A09)   ← runs at BUILD_VALIDATED → advances to DIFFERENTIAL_TESTING
  Variance Analysis (A10)
  RCA (A11)
  Remediation (A12)        ← G4 ACCEPT_EXCEPTION only for exception path

GATE G4 → variance disposition decision
GATE G5 → final promotion approval → COMPLETED
```

---

## Gate → State Machine Mapping

| Gate | Lifecycle transition gated | States involved |
|------|---------------------------|-----------------|
| G0 | DORMANCY_ASSESSED → FRAMEWORK_BASELINED | DORMANCY_ASSESSED → WAITING_G0 → FRAMEWORK_BASELINED |
| G1 | FRAMEWORK_BASELINED → ACE_ANALYSED | FRAMEWORK_BASELINED → WAITING_G1 → ACE_ANALYSED |
| G2 | ACE_ANALYSED → PLAN_APPROVED | ACE_ANALYSED → WAITING_G2 → PLAN_APPROVED |
| G3 | PLAN_APPROVED → IMPLEMENTING | PLAN_APPROVED → WAITING_G3 → IMPLEMENTING |
| G4 | VARIANCE_REVIEW → READY_FOR_PROMOTION | VARIANCE_REVIEW → WAITING_G4 → READY_FOR_PROMOTION |
| G5 | READY_FOR_PROMOTION → COMPLETED | READY_FOR_PROMOTION → WAITING_G5 → COMPLETED |

Progress percentages while waiting at each gate (from `app/static/app.js`):
`WAITING_G0: 25%`, `WAITING_G1: 25%`, `WAITING_G2: 33%`, `WAITING_G3: 42%`, `WAITING_G4: 75%`, `WAITING_G5: 92%`

---

## Key Source Files

| Topic | File |
|-------|------|
| Gate enforcement (Planning, Code Gen) | `app/services/factory_v3.py` |
| Gate enforcement (lifecycle v6) | `app/services/migration_lifecycle_v6.py` |
| State machine transitions | `app/domain/state_machine.py` |
| Pipeline gate-wait logic | `app/services/pipelines.py` |
| Orchestrator PIPELINE map + GATE_FIRST list | `app/agents/orchestrator.py` (lines 35–69) |
| Progress % per state (frontend) | `app/static/app.js` (line 6) |
