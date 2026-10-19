# ACE Migration Factory — Agent & Gate Approval: Testing Reference

> **Purpose:** If you are testing the migration flow, this table tells you exactly which role your test user account must have assigned before a specific action will work. Without the correct role, the system raises a `PermissionError` and the action is blocked.

---

## 1. Gate Approvals — Exact Roles Required

These are the roles enforced at runtime by the approval policy engine. The role must be in the user's JWT **and** passed as `approver_role` in the request body.

| Gate | What It Unlocks | Required Role (exact) | Decision to ADVANCE pipeline | Who Grants This Role |
|---|---|---|---|---|
| **G0** | DORMANCY_ASSESSED → FRAMEWORK_BASELINED | `SERVICE_OWNER` | `PROCEED` | `GOVERNANCE_ADMIN` |
| **G1** | FRAMEWORK_BASELINED → ACE_ANALYSED | `JAVA_ARCHITECT` | `APPROVE` | `GOVERNANCE_ADMIN` |
| **G2** | ACE_ANALYSED → PLAN_APPROVED | `ACE_SME` | `APPROVE` | `GOVERNANCE_ADMIN` |
| **G3** | PLAN_APPROVED → IMPLEMENTING | `PRODUCT_OWNER` | `APPROVE` | `GOVERNANCE_ADMIN` |
| **G4** | VARIANCE_REVIEW → READY_FOR_PROMOTION | `QA_LEAD` | `ACCEPT_EXCEPTION` or `CONTINUE_FEEDBACK` | `GOVERNANCE_ADMIN` |
| **G5** | READY_FOR_PROMOTION → COMPLETED | `RELEASE_MANAGER` | `PROMOTE` | `GOVERNANCE_ADMIN` |

> **Important:** `SERVICE_OWNER`, `JAVA_ARCHITECT`, `ACE_SME`, `PRODUCT_OWNER`, and `QA_LEAD` are **not** in the self-service role list and not listed in Section 4 — they are gate-approver-only roles. A `GOVERNANCE_ADMIN` must grant them via `POST /api/v2/auth/grant-role`.

---

## 2. Agent Invocation — Exact Roles Required

Each agent checks that the caller (or the Orchestrator acting on behalf) holds the agent's `required_role`.

| Agent | Required Role (exact) | Can Also Be Invoked By | Error if Missing |
|---|---|---|---|
| Migration Orchestrator | `ORCHESTRATOR` | `GOVERNANCE_ADMIN` | `required_agent_role_missing` |
| Repository Intake | `REPOSITORY_INTAKE` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Dormancy Intelligence | `DORMANCY_ANALYST` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Framework Baseline | `FRAMEWORK_ANALYST` | `ORCHESTRATOR` | `required_agent_role_missing` |
| ACE Analysis | `ACE_ANALYST` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Migration Specification | `SPECIFICATION_AUTHOR` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Planning | `MIGRATION_PLANNER` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Code Generation | `CODE_GENERATOR` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Test Engineering | `TEST_ENGINEER` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Variance Analysis | `VARIANCE_ANALYST` | `ORCHESTRATOR` | `required_agent_role_missing` |
| RCA | `RCA_ANALYST` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Remediation | `REMEDIATION_ENGINEER` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Governance | `GOVERNANCE_ENGINE` | `ORCHESTRATOR` | `required_agent_role_missing` |
| Knowledge & Evidence | `KNOWLEDGE_CURATOR` | `GOVERNANCE_ADMIN` | `required_agent_role_missing` |

> **Note:** `MIGRATION_PLANNER`, `VARIANCE_ANALYST`, `REMEDIATION_ENGINEER`, and `GOVERNANCE_ENGINE` appear only in the agent catalogue — they are not in `SELF_SERVICE_ROLES` or `ELEVATED_ROLES`. A `GOVERNANCE_ADMIN` must grant them.

---

## 3. Self-Service Roles (users can assign themselves at signup)

These roles can be self-assigned — no `GOVERNANCE_ADMIN` needed.

| Role | Needed To Test |
|---|---|
| `ACE_ANALYST` | Directly invoke ACE Analysis agent |
| `FRAMEWORK_ANALYST` | Directly invoke Framework Baseline agent |
| `SPECIFICATION_AUTHOR` | Directly invoke Migration Specification agent |
| `CODE_GENERATOR` | Directly invoke Code Generation agent |
| `TEST_ENGINEER` | Directly invoke Test Engineering agent |
| `RCA_ANALYST` | Directly invoke RCA agent; confidence score; remediation eligibility |
| `REPOSITORY_INTAKE` | Directly invoke Repository Intake agent |
| `DORMANCY_ANALYST` | Directly invoke Dormancy Intelligence agent |
| `DELIVERY_ENGINEER` | Git delivery, delivery requests |

---

## 4. Elevated Roles (require `GOVERNANCE_ADMIN` to grant)

These roles cannot be self-assigned. The first account registered gets `GOVERNANCE_ADMIN` automatically (bootstrap).

| Role | Needed To Test |
|---|---|
| `GOVERNANCE_ADMIN` | Grant any elevated role; approve anything via override; full admin access |
| `GATE_ADMIN` | Configure gate controls (`quorum`, `timeout`); retrigger lifecycle |
| `ORCHESTRATOR` | Run the full orchestrated pipeline; invoke any agent on behalf of a job |
| `SERVICE_OWNER` | **Approve G0** |
| `JAVA_ARCHITECT` | **Approve G1** |
| `ACE_SME` | **Approve G2** |
| `PRODUCT_OWNER` | **Approve G3** |
| `QA_LEAD` | **Approve G4** |
| `RELEASE_MANAGER` | **Approve G5**; rollback jobs; git delivery |
| `AGENT_REGISTRY_ADMIN` | Register agents/models; manage LLM bindings |
| `AI_GOVERNANCE_ADMIN` | Register prompts and models in the AI registry |
| `AUDITOR` | Read audit ledger (`GET /api/v2/audit-ledger`) |
| `CONNECTOR_ADMIN` | Manage connectors and routing policies |
| `KNOWLEDGE_CURATOR` | Index knowledge base |
| `SERVICE_REGISTRAR` | Register services in the service registry |
| `NOTIFICATION_PUBLISHER` | Post notifications |
| `OBSERVABILITY_PUBLISHER` | Post observability data |
| `AGENT_OPERATOR` | Send model gateway requests |

---

## 5. Testing the Full Flow — Minimum Role Set Per Stage

Use this as a checklist. If a stage fails with a permission error, verify the test user has the role in this column.

| What You Are Testing | Roles the Test User MUST Have |
|---|---|
| Create a job | Any authenticated user |
| Run intake + dormancy agents manually | `REPOSITORY_INTAKE`, `DORMANCY_ANALYST` |
| Run framework baseline manually | `FRAMEWORK_ANALYST` |
| Run ACE analysis manually | `ACE_ANALYST` |
| Run migration specification manually | `SPECIFICATION_AUTHOR` |
| **Approve G0** (advance past dormancy) | `SERVICE_OWNER` |
| **Approve G1** (advance past framework baseline) | `JAVA_ARCHITECT` |
| **Approve G2** (advance past analysis/spec) | `ACE_SME` |
| Run planning | `MIGRATION_PLANNER` (also needs G1 + G2 approved) |
| **Approve G3** (advance to code generation) | `PRODUCT_OWNER` |
| Run code generation | `CODE_GENERATOR` (also needs G3 approved) |
| Run test engineering | `TEST_ENGINEER` |
| Run variance analysis | `VARIANCE_ANALYST` |
| Run RCA | `RCA_ANALYST` |
| Run remediation | `REMEDIATION_ENGINEER` |
| **Approve G4** (variance disposition) | `QA_LEAD` |
| **Approve G5** (final promotion) | `RELEASE_MANAGER` |
| Run the full pipeline via orchestrator | `ORCHESTRATOR` |
| Configure gate quorum/timeout | `GOVERNANCE_ADMIN` OR `GATE_ADMIN` |
| Grant roles to other test users | `GOVERNANCE_ADMIN` |
| Manage LLM bindings for agents | `AGENT_REGISTRY_ADMIN` OR `GOVERNANCE_ADMIN` |
| View audit trail | `GOVERNANCE_ADMIN` OR `AUDITOR` |
| Rollback a job | `RELEASE_MANAGER` OR `GOVERNANCE_ADMIN` |

---

## 6. How Roles Are Assigned

```
1. First account registered → automatically gets GOVERNANCE_ADMIN, CONNECTOR_ADMIN,
                               GATE_ADMIN, AUDITOR  (bootstrap roles)

2. Any user can self-assign at signup:
   ACE_ANALYST, FRAMEWORK_ANALYST, SPECIFICATION_AUTHOR, CODE_GENERATOR,
   TEST_ENGINEER, RCA_ANALYST, REPOSITORY_INTAKE, DORMANCY_ANALYST,
   DELIVERY_ENGINEER

3. Elevated roles require a GOVERNANCE_ADMIN to call:
   POST /api/v2/auth/grant-role
   Body: { "username": "<user>", "role": "<ROLE_NAME>" }
```

---

## 7. Common Test Failures and Their Cause

| Error Message | Root Cause | Fix |
|---|---|---|
| `required_role_missing:<ROLE>` | JWT does not contain the role for the API endpoint | Grant the role via `POST /api/v2/auth/grant-role` |
| `required_agent_role_missing` | Agent's `required_role` not in caller's JWT | Assign the agent's required role to the test user |
| `required_approver_role_missing` | Gate approval submitted but user lacks `required_role` OR `approver_role` in request body does not match | Assign the gate's exact role; ensure `approver_role` field in request body matches |
| `required_gate_approval_missing:G<N>:quorum_N_of_M` | Gate has not been approved yet (or quorum not met) | Submit approval with correct role before triggering transition |
| `decision_not_allowed` | The `decision` value sent is not in the gate's `allowed_decisions` | Use only the decisions listed in the Gate Approvals table above |
| `gate_disabled_or_unknown` | Gate `enabled=0` in `approval_policies` table | Re-enable via `GOVERNANCE_ADMIN` or `GATE_ADMIN` |

---

## 8. Lifecycle Progress — What Percentage to Expect at Each Stage

The dashboard shows progress as a percentage (0 – 100%). Use the table below to verify that your pipeline is at the right stage for the agents you ran and the gates you approved.

There are two types of states:

- **Gated states** (★): can be reached by the agent completing **OR** by the agent being skipped and the gate being approved. The gate approval is treated as governance sign-off for the deliberate skip.
- **Non-gated states**: the agent **must actually run and complete**. No gate fallback exists. If the agent is disabled, the job will stall at the previous state permanently.

| # | Lifecycle State | Dashboard % | Gate | Agent required | Can reach without agent? |
|---|---|---|---|---|---|
| 1 | CREATED | 0% | — | — | — |
| 2 | INTAKE_VALIDATED | 8% | — | `repository-intake` | ✗ No gate fallback |
| 3 | DORMANCY_ASSESSED | 17% | — | `dormancy-intelligence` | ✗ No gate fallback |
| 4 | FRAMEWORK_BASELINED ★ | 25% | **G0** SERVICE_OWNER → PROCEED | `framework-baseline` | ✅ Yes — skip agent + approve G0 |
| 5 | ACE_ANALYSED ★ | 33% | **G1** JAVA_ARCHITECT → APPROVE | `ace-analysis` | ✅ Yes — skip agent + approve G1 |
| 6 | PLAN_APPROVED ★ | 42% | **G2** ACE_SME → APPROVE | `planning` | ✅ Yes — skip agent + approve G2 |
| 7 | IMPLEMENTING ★ | 50% | **G3** PRODUCT_OWNER → APPROVE | `code-generation` | ✅ Yes — skip agent + approve G3 |
| 8 | BUILD_VALIDATED | 58% | — | `build-validation` | ✗ No gate fallback |
| 9 | DIFFERENTIAL_TESTING | 67% | — | `test-engineering` | ✗ No gate fallback |
| 10 | VARIANCE_REVIEW | 75% | — | `variance-analysis` | ✗ No gate fallback |
| 11 | REMEDIATING ★ | 83% | — | `remediation` | ✅ Yes — skip agent (gate covers it) |
| 12 | READY_FOR_PROMOTION ★ | 92% | **G4** QA_LEAD → ACCEPT_EXCEPTION | *(no agent)* | ✅ Gate-only transition |
| 13 | COMPLETED ★ | 100% | **G5** RELEASE_MANAGER → PROMOTE | *(no agent)* | ✅ Gate-only transition |

> **WAITING\_Gx states** (e.g. `WAITING_G0`, `WAITING_G2`) are temporary holding states. They do **not** change the percentage — the job stays at the previous percentage until the gate is approved. A job sitting at 25% for a long time is almost always waiting at a gate.

---

## 9. Agent Selection vs. Expected Progress — "Am I on the Right Track?"

When you create a pipeline you choose which agents to include. Skipping an agent means the lifecycle **will stall at that agent's required state** unless the corresponding governance gate is approved. Use this table as a sanity check.

### 9a. Full-Pipeline Run (all agents enabled)

If every agent is enabled, the pipeline advances automatically after each gate approval:

```
[Pipeline starts]
  → repository-intake completes      → 8%  (INTAKE_VALIDATED)
  → dormancy-intelligence completes  → 17% (DORMANCY_ASSESSED)  ← waits here for G0
  → [Approve G0]                     → 25% (FRAMEWORK_BASELINED) + framework-baseline runs
  → [Approve G1]                     → 33% (ACE_ANALYSED) + ace-analysis runs  ← waits here for G1
  → [Approve G2]                     → 42% (PLAN_APPROVED) + planning runs      ← waits here for G2
  → [Approve G3]                     → 50% (IMPLEMENTING) + code-generation runs ← waits here for G3
  → build-validation completes       → 58% (BUILD_VALIDATED)
  → test-engineering completes       → 67% (DIFFERENTIAL_TESTING)
  → variance-analysis completes      → 75% (VARIANCE_REVIEW)    ← waits here for G4
  → remediation completes            → 83% (REMEDIATING)
  → [Approve G4]                     → 92% (READY_FOR_PROMOTION)
  → [Approve G5]                     → 100% (COMPLETED)
```

### 9b. Analysis-Only Run — skipping code-generation and later agents (common for initial triage)

If you only enable `repository-intake`, `dormancy-intelligence`, `ace-analysis` (and skip everything else):

```
  → repository-intake completes      → 8%  (INTAKE_VALIDATED)
  → dormancy-intelligence completes  → 17% (DORMANCY_ASSESSED)
  → [Approve G0]                     → 25% (FRAMEWORK_BASELINED) ← framework-baseline was skipped,
                                                                     but G0 approval covers the skip
  → [Approve G1]                     → 33% (ACE_ANALYSED)        ← same — ace-analysis ran, G1 approves
  → [Approve G2]                     → 42% (PLAN_APPROVED)       ← planning skipped, G2 covers it
  → [Approve G3]                     → 50% (IMPLEMENTING)        ← code-generation skipped, G3 covers it

  ⛔ STALLS HERE at 50% — permanently.
     BUILD_VALIDATED (58%), DIFFERENTIAL_TESTING (67%), VARIANCE_REVIEW (75%) are NOT gated states.
     Their agents (build-validation, test-engineering, variance-analysis) MUST actually run.
     No gate approval can substitute for them.
```

> **Key insight:** Reaching 50% (IMPLEMENTING) with code-generation disabled is normal and expected — G3 approval is the gate sign-off for that deliberate skip. But 50% is the ceiling for an analysis-only run. The second half of the lifecycle (58%–100%) requires the remaining agents to physically execute.

To continue from 50%, run a **second pipeline on the same job** with these agents enabled:
`build-validation` → `test-engineering` → `variance-analysis` → `remediation`

### 9c. Checklist — "Why is my job not advancing?"

Work through this list top to bottom when a job is stuck:

| Check | How to Verify | Fix |
|---|---|---|
| Is the job waiting at a gate? | State contains `WAITING_G` (e.g. `WAITING_G2`) | Approve the gate with the required role (see Section 1) |
| Did the required agent run? | Open the pipeline view — look for ✅ Completed next to the agent name | Re-run the pipeline with that agent enabled |
| Was the agent explicitly disabled in your pipeline? | Step shows **Skipped** with label "explicitly disabled" | Run a new pipeline on the same job with the agent toggled on |
| Did an agent fail and retry successfully? | Pipeline shows **Failed** but all steps are green | This is a display bug — the pipeline status self-corrects after the retry. Refresh the page. |
| Is the pipeline at its final state for the agents selected? | All enabled agents completed, all applicable gates approved | Run a second pipeline on the same job with the next phase of agents |
| Is there an open approval package in the Approval Inbox? | Check the Approval Inbox in the sidebar | Action the pending approval with the correct role account |

### 9d. Minimum Agent Set per Testing Goal

| Testing Goal | Agents to Enable | Expected Final State |
|---|---|---|
| Verify repository can be cloned and indexed | `repository-intake` | INTAKE_VALIDATED (8%) |
| Verify dormancy classification | `repository-intake`, `dormancy-intelligence` | DORMANCY_ASSESSED (17%) |
| Verify ACE analysis output | `repository-intake`, `dormancy-intelligence`, `ace-analysis` | ACE_ANALYSED (33%) after G0 + G1 |
| Verify full analysis + governance gates (no code gen) | Above + `framework-baseline`, `planning` | IMPLEMENTING (50%) after G0–G3 |
| Verify code generation | Above + `code-generation`, `build-validation` | BUILD_VALIDATED (58%) |
| End-to-end migration (all phases) | All agents | COMPLETED (100%) after G0–G5 |
