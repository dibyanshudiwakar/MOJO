# ACE Migration Factory: Multi-Model Defaults and Per-Agent LLM Overrides

**Status:** Proposed product design, not an implemented feature  
**Current stage:** Model routing and Edit Agent configuration design  
**Scope:** IBM ACE to Spring Boot migration agents, default open-source models, bring-your-own LLM (BYO-LLM), routing precedence, and GA controls

## 1. Decision in one sentence

**Ship the built-in agent catalogue with a default model assignment for each agent; in the Edit Agent tab, an authorised user may set a validated, active agent-specific LLM configuration. An active agent-specific configuration always takes precedence over that agent's default. When no active agent-specific LLM configuration exists, use that agent's default.**

This is a **proposed extension** to the formal architecture, which defines the agents, model gateway, per-job tool allow-lists, security, evidence and versioning but does not define the requested Edit Agent override precedence. [Internal A]

## 2. Inputs and licensing correction

- The formal architecture defines fourteen specialised agents, a model gateway, prompt registry, policy enforcement, immutable evidence and rollback snapshots. [Internal A]
- The product plan identifies model non-determinism and requires versioned prompts/models, schemas, deterministic tools and evidence. [Internal B]
- Official Qwen model cards label Qwen3-32B, Qwen3-8B, Qwen3-Coder-30B-A3B-Instruct and Qwen3-Embedding-0.6B as Apache 2.0. Verify each exact pinned revision during release approval. [External Q1-Q4]
- **Correction to the earlier stack proposal:** the BAAI/bge-m3 model card identifies its licence as **MIT, not Apache 2.0**. It is an optional approved alternative if MIT is allowed, not the default for an Apache-2.0-only model-weights policy. [External B1]
- LiteLLM documents public model aliases, model-group access control and database-backed model management. Team self-service model addition is documented as an Enterprise feature, so this design uses the product's own Edit Agent UI and a privileged gateway registration workflow; it does not assume that LiteLLM's team self-service feature is available in the chosen edition. [External L1-L4]

## 3. Scope and immutable defaults

On product installation, seed the fourteen agents below as **system agents**. Their identity, purpose, lifecycle stage, required outputs, prompt contract, tool allow-list and approval gates are controlled by the product and cannot be removed or bypassed by changing the model. Their **LLM binding** is editable subject to policy. Non-LLM work remains deterministic where indicated.

An agent's default assignment is a product baseline, not a claim that a model is already deployed. The deployment registry must mark a model **READY** before an agent can invoke it. If the required default is not deployed, block that stage and surface an actionable configuration error; do not invent a provider or silently switch models.

### 3.1 Default model registry

| Alias | Default model / capability | Usage | Licence gate |
|---|---|---|---|
| `reasoning-large` | `Qwen/Qwen3-32B` | ACE analysis, specification, planning, RCA | Apache 2.0 checkpoint review |
| `reasoning-small` | `Qwen/Qwen3-8B` | Intake, dormancy, orchestration explanations, governance explanations | Apache 2.0 checkpoint review |
| `coding` | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | Code, test design and bounded patches | Apache 2.0 checkpoint review |
| `embeddings` | `Qwen/Qwen3-Embedding-0.6B` | Retrieval embeddings, not chat generation | Apache 2.0 checkpoint review |
| `semantic-review` | `Qwen/Qwen3-32B` | Interpretation only after deterministic comparison | Apache 2.0 checkpoint review |
| `reranker` | No mandatory default | Optional reranking; requires separate approval and endpoint | Verify exact checkpoint |
| `bge-m3-optional` | `BAAI/bge-m3` | Optional alternative embedding model | MIT; only if policy permits |

The default embedding model is shared infrastructure for the Knowledge and Evidence Agent and RAG used by other agents. Changing a chat LLM on one agent does **not** change the embedding model or re-embed the vector index. An embedding override is a separate typed configuration, and any index change requires reindexing or a compatible parallel index.

### 3.2 Default agent-level mapping

| Built-in agent | Default LLM alias | Required tools / execution boundary | Default prompt ID |
|---|---|---|---|
| Migration Orchestrator | `reasoning-small` | Workflow state, policy engine, agent registry; state transitions enforced by control plane | `orchestrator.v1` |
| Repository Intake | `reasoning-small` | Read-only Git, ACE inventory, checksums; tools establish facts | `intake.v1` |
| Dormancy Intelligence | `reasoning-small` | Telemetry, deployment inventory, migration registry; unknown stays unknown | `dormancy.v1` |
| Framework Baseline | `reasoning-large` | Approved target repositories, convention extractor, retrieval | `framework.v1` |
| ACE Analysis | `reasoning-large` | ACE/ESQL/map/schema/policy parsers, dependency graph | `ace-analysis.v1` |
| Migration Specification | `reasoning-large` | Semantic model, YAML serializer, JSON Schema validator | `migration-spec.v1` |
| Planning | `reasoning-large` | Approved analysis, mapping catalogue, risk rules | `planning.v1` |
| Code Generation | `coding` | Controlled templates, isolated Git workspace, build/scanners | `codegen.v1` |
| Test Engineering | `coding` | Masked test-data references, test harness, ACE/Spring adapters | `test-engineering.v1` |
| Variance Analysis | `semantic-review` **only for semantic fallback** | Deterministic comparator always first; LLM cannot replace structural/value checks | `variance.v1` |
| RCA | `reasoning-large` | Variance evidence, logs, source/target trace, confidence service | `rca.v1` |
| Remediation | `coding` | Approved RCA, policy, isolated branch, tests and scanners | `remediation.v1` |
| Governance | `reasoning-small` **for explanation only** | Deterministic policy engine decides allow/deny/approval | `governance.v1` |
| Knowledge and Evidence | `embeddings` for retrieval; `reasoning-small` for optional synthesis | Authorised index/search, lineage, optional reranker | `knowledge.v1` |

**Agent count and names** match the formal architecture's catalogue. [Internal A] The exact default model mapping and prompt IDs above are this design's proposed product settings, not existing approved selections.

## 4. Edit Agent tab: user experience

The Edit Agent screen should display:

1. **Agent identity:** system agent name, purpose, lifecycle stage, default prompt, allowed tools, approval gate. These are read-only for ordinary editors.
2. **Model source:** `Use system default` (initially selected) or `Use agent-specific model`.
3. **Default preview:** default alias, resolved model name, deployment status, and licence posture.
4. **Override method:** `Choose approved model from registry` or `Register my own LLM` (subject to permission).
5. **Required configuration fields** (see Section 5), with secret entry through an approved vault workflow.
6. **Test connection / validate:** reachability, API contract, capability checks, access policy, region and licence review. Test with a synthetic non-sensitive prompt only.
7. **Effective model preview:** show which model will actually run and whether it comes from DEFAULT or AGENT_OVERRIDE.
8. **Save as draft / submit for approval / activate / revert to default:** activation is atomic; a failed validation never becomes active.
9. **Change history:** who changed what, when, why, previous and new binding, approval, model revision and test evidence.

Only the **LLM binding** changes through this feature. It does not grant new tools, change prompts without separate approval, disable gates, override data policy, or let a model approve its own output. [Internal A]

## 5. Fields required for a user-provided LLM

### 5.1 Always required for an agent-specific chat model

| Field | Meaning / validation |
|---|---|
| `binding_mode` | `AGENT_OVERRIDE` explicitly selected; absence means DEFAULT. |
| `provider_type` | Approved adapter type, such as `litellm_alias` or `openai_compatible`; other providers need a supported adapter. |
| `display_name` | Human-readable label unique within the project or tenant. |
| `gateway_model_alias` | The LiteLLM model group that the agent will call; must exist and be authorised. |
| `model_id` | Exact model identifier served by the provider; no ambiguous family-only name. |
| `model_revision` | Immutable checkpoint digest/revision or approved provider version. |
| `capabilities` | Must include `chat`; require `structured_output` or approved schema-validation path; require `tool_calling` only if this agent's workflow actually uses native tool calling. |
| `context_limit` and `output_limit` | Verified limits, not guessed values; validate against agent prompt and schema needs. |
| `licence_id` and `licence_evidence` | Exact checkpoint licence and approval status; BYO proprietary model only if product policy explicitly permits it. |
| `data_region` and `data_classification_allowed` | Must satisfy project and enterprise data policy. |
| `owner`, `scope`, `reason_for_change` | Ownership, tenant/project/agent scope and audit reason. |

### 5.2 Additional required fields for a new endpoint

| Field | Meaning / validation |
|---|---|
| `api_base_url` | Approved HTTPS endpoint, or approved internal TLS service; no arbitrary local, link-local or metadata-service URL. |
| `auth_type` and `credential_secret_ref` | Approved authentication mechanism and vault reference. Never store a raw key in agent configuration. |
| `api_protocol` | E.g., OpenAI-compatible chat completions; must match the selected adapter. |
| `tls_trust_profile` | Certificate trust / mTLS profile as required by platform policy. |
| `network_egress_policy` | Endpoint allow-list and tenant/environment isolation. |
| `healthcheck_status` | Successful safe connection and schema test before activation. |

### 5.3 Optional, bounded settings

`temperature`, `top_p`, `max_output_tokens`, timeout, retry budget, approved fallback alias, response-format mode, request budget and cost attribution. Apply policy limits and retain the validated effective values in the audit snapshot. No secret material, unmasked production payload, or unrestricted endpoint should be accepted.

**Important distinction:** selecting an already approved gateway alias requires only the binding and validation metadata, not a second endpoint or API key. Registering a new endpoint requires all additional connection and security fields. Never force users to re-enter credentials already stored in the approved model registry.

## 6. Resolution and precedence contract

**Precedence is per agent, not global:**

```text
ACTIVE + VALID + APPROVED agent-specific LLM binding
    > that agent's versioned product default binding
    > BLOCK if neither can be resolved safely
```

- If no agent-specific binding exists, or the user explicitly selects **Use system default**, route to the product default for that agent.
- If an active, valid override exists, **always** route that agent to the override, even when the default is healthy or a default alias changes.
- A saved draft, disabled override, rejected override, expired approval, incomplete configuration or revoked credential is **not** active; show its state and keep the last active binding or default. Do not interpret merely populated form fields as an active override.
- If the active override is unavailable at runtime, **do not silently use the default**. Fail closed and pause the affected stage. A separate, explicitly authorised fallback policy may permit a recorded alternative; the default is not an implicit fallback from an active override.
- A change to Agent A never changes Agent B's binding. A changed default affects only agents using DEFAULT and only for new workflow checkpoints under the configured release policy.
- Resolve once at each durable stage/iteration checkpoint, then pin the effective binding for that attempt. A UI edit mid-run takes effect only at the next checkpoint, or after a controlled pause/resume and revalidation. Keep prior artefacts linked to the old binding.
- A knowledge-agent embedding override must not be substituted for a chat model; capability types are enforced.

### 6.1 Pseudocode (proposed product behavior)

```python
def resolve_agent_model(agent, job, registry, policy):
    binding = registry.active_agent_binding(agent.id, job.project_id)
    if binding is None or binding.mode == "DEFAULT":
        candidate = registry.versioned_default(agent.id, job.release_id)
        source = "DEFAULT"
    else:
        candidate = binding
        source = "AGENT_OVERRIDE"

    if not candidate:
        raise ConfigurationBlocked("No model binding for agent")
    policy.assert_approved(candidate, agent, job)
    policy.assert_capabilities(candidate, agent.required_capabilities)
    policy.assert_region_and_data_classification(candidate, job)
    registry.assert_ready(candidate.gateway_model_alias)

    return registry.pin_for_checkpoint(
        job_id=job.id,
        agent_id=agent.id,
        source=source,
        model_alias=candidate.gateway_model_alias,
        model_revision=candidate.model_revision,
        binding_version=candidate.version,
        prompt_version=agent.prompt_version,
        tool_policy_version=agent.tool_policy_version,
    )
```

### 6.2 Decision examples

| Agent-specific setting | Status | Selected model | Expected action |
|---|---|---|---|
| None | N/A | Agent's default | Run if default is READY and approved. |
| Use system default | Active | Agent's default | Run if default is READY and approved. |
| Custom approved alias | Active | Custom alias | Override wins over default. |
| Custom endpoint submitted | Draft/pending | Last active binding or default | Do not run unapproved endpoint. |
| Custom alias with revoked credential | Active but invalid at runtime | None | Pause and alert; no silent default fallback. |
| Override removed | No active override | Agent's default | Apply at next checkpoint and record change. |
| Other agent changed | Active only on other agent | This agent's own binding | No cross-agent effects. |

## 7. Configuration records and API contract

### 7.1 Product-side records (illustrative JSON)

```json
{
  "agent_id": "ace-analysis",
  "system_agent": true,
  "default_model_alias": "reasoning-large",
  "default_model_revision": "<approved-revision>",
  "prompt_id": "ace-analysis.v1",
  "tool_policy_id": "ace-analysis-tools.v1",
  "llm_binding": {
    "mode": "AGENT_OVERRIDE",
    "status": "ACTIVE",
    "version": 3,
    "gateway_model_alias": "project-ace-analysis-byo",
    "provider_type": "openai_compatible",
    "model_id": "<exact-served-model-id>",
    "model_revision": "<immutable-revision-or-digest>",
    "credential_secret_ref": "vault://<approved-path>",
    "api_base_url_ref": "endpoint-registry://<endpoint-id>",
    "capabilities": ["chat", "structured_output"],
    "licence_id": "<approved-licence>",
    "data_region": "<approved-region>",
    "approved_by": "<authorised-identity>",
    "approval_record_id": "<approval-id>"
  }
}
```

The example contains placeholders, not credentials or a real endpoint. `api_base_url_ref` is a registry reference rather than an arbitrary URL in the agent record. For `DEFAULT`, store no active override object or set `mode: DEFAULT`; do not copy the default into every agent as though it were user-entered.

### 7.2 Proposed application APIs

```text
GET    /api/agents/{agentId}                         # default, override, effective preview
PATCH  /api/agents/{agentId}/llm-binding             # save draft; optimistic version check
POST   /api/agents/{agentId}/llm-binding/validate    # safe capability and policy tests
POST   /api/agents/{agentId}/llm-binding/submit      # request approval
POST   /api/agents/{agentId}/llm-binding/activate    # privileged, atomic activation
DELETE /api/agents/{agentId}/llm-binding             # revert to default at next checkpoint
GET    /api/agents/{agentId}/llm-binding/history     # audit history
```

These are **proposed product APIs**, not endpoints claimed to exist today. Restrict registration and activation by RBAC, record an approval decision, and use optimistic concurrency to avoid overwriting another editor's change.

### 7.3 Audit record

Record `agent_id`, `job_id`, `checkpoint`, `binding_source`, binding version, model alias, exact revision, endpoint registry ID, prompt/tool-policy/schema versions, editor and approver identities, approval/evidence references, policy result, correlation ID and system timestamp. Do not log raw secrets or sensitive prompts by default. Preserve the previous binding and rollback pointer for each iteration. [Internal A; Internal B]

## 8. Gateway integration: defaults and BYO-LLM

The product resolves **which alias to request**. LiteLLM resolves **which provider deployment serves that alias**. Do not expect a static LiteLLM YAML file alone to implement per-agent precedence: that is application control-plane logic. LiteLLM documents `model_name` aliases, per-key model access and DB-backed model management. [External L1-L3]

### 8.1 Illustrative static baseline for approved defaults

```yaml
model_list:
  - model_name: reasoning-large
    litellm_params:
      model: hosted_vllm/Qwen/Qwen3-32B
      api_base: os.environ/QWEN_32B_BASE_URL
      api_key: os.environ/QWEN_32B_API_KEY

  - model_name: reasoning-small
    litellm_params:
      model: hosted_vllm/Qwen/Qwen3-8B
      api_base: os.environ/QWEN_8B_BASE_URL
      api_key: os.environ/QWEN_8B_API_KEY

  - model_name: coding
    litellm_params:
      model: hosted_vllm/Qwen/Qwen3-Coder-30B-A3B-Instruct
      api_base: os.environ/QWEN_CODER_BASE_URL
      api_key: os.environ/QWEN_CODER_API_KEY

  - model_name: embeddings
    litellm_params:
      model: hosted_vllm/Qwen/Qwen3-Embedding-0.6B
      api_base: os.environ/QWEN_EMBED_BASE_URL
      api_key: os.environ/QWEN_EMBED_API_KEY
    model_info:
      mode: embedding

  - model_name: semantic-review
    litellm_params:
      model: hosted_vllm/Qwen/Qwen3-32B
      api_base: os.environ/QWEN_32B_BASE_URL
      api_key: os.environ/QWEN_32B_API_KEY

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/LITELLM_DATABASE_URL
```

This is a **draft example**. Confirm the `hosted_vllm/` adapter, model serving names, environment-variable handling, embeddings API compatibility and chosen LiteLLM version in an integration test. The model server must expose the corresponding endpoint and return the same served model ID. [External L1; External L5]

### 8.2 Registering a user-provided endpoint

1. Edit Agent collects the required fields and vault reference, never a persistent raw key.
2. Product validates RBAC, licence, region, network allow-list, TLS, model capability, safe test call, schema response and quota.
3. An authorised gateway administrator or platform automation registers a **scoped model alias** (for example `project-ace-analysis-byo`) in the LiteLLM model registry. Do not use a global wildcard or let the editor access arbitrary provider credentials.
4. After gateway readiness and product approval, activate the binding atomically in the product agent registry.
5. The orchestrator resolves that alias for the relevant agent and pins it for the next checkpoint.
6. On revocation or failure, pause the stage and show the failed binding; only an explicitly approved fallback policy may change the model.

LiteLLM's documented database-backed model management can support operational add/edit/retire workflows. However, its team self-service model-add feature is separately documented as requiring an Enterprise licence. Product-owned Edit Agent registration must therefore be implemented and edition-tested rather than assumed to be built in. [External L3; External L4]

## 9. Security and GA guardrails

- **Never bypass gates:** changing the LLM does not approve G0-G5 or production promotion. [Internal A; Internal B]
- **Least privilege:** per-agent gateway credentials must allow only authorised model groups; a model override cannot grant extra tool access. [Internal A; External L2]
- **Prompt safety:** treat repository text and retrieved documents as data, not instructions; preserve the versioned system prompt and output schema.
- **Endpoint safety:** prevent SSRF by validating endpoint scheme, DNS/IP resolution, redirects, network egress and internal metadata addresses; use approved endpoint registry.
- **Data handling:** DLP, masking, region policy, secret indirection, logging retention and tenant isolation apply equally to defaults and BYO-LLM. [Internal A]
- **Model capability:** reject embedding-only models for chat agents and chat-only models for embedding bindings; check actual structured-output behavior rather than relying on a checkbox.
- **Deterministic authority:** parsers, compilers, schema validators, test runners and security scanners remain authoritative. [Internal A]
- **Model versioning:** record the exact model checkpoint and effective agent binding in the iteration evidence; preserve rollback history. [Internal A; Internal B]
- **Licensing:** Apache-only policy rejects MIT BGE-M3 unless the policy is deliberately changed and approved. [External B1]

## 10. Acceptance tests

| ID | Test | Expected result |
|---|---|---|
| AT-01 | Start a job with no agent overrides | Every agent resolves its own seeded default. |
| AT-02 | Override only ACE Analysis | ACE Analysis uses the override; all other agents keep defaults. |
| AT-03 | Populate custom fields but leave draft | Draft is not used; last active binding/default remains effective. |
| AT-04 | Activate approved custom LLM | Custom model takes precedence on the next checkpoint. |
| AT-05 | Custom LLM goes down | Stage pauses; default is not silently substituted. |
| AT-06 | Revert ACE Analysis to default | Default resumes at next checkpoint; history retains override. |
| AT-07 | Attempt to use embedding model for code generation | Save/activation rejected for capability mismatch. |
| AT-08 | Enter raw secret or unapproved endpoint | Reject or store only a vault/endpoint-registry reference after validation. |
| AT-09 | Override Governance Agent | Explanatory LLM may change; deterministic policy decision cannot be overridden. |
| AT-10 | Override Variance Analysis | Structural/value comparator still runs first; LLM used only for approved semantic fallback. |
| AT-11 | Change binding while a stage is running | Current attempt stays pinned; new binding applies at a controlled checkpoint. |
| AT-12 | Set BGE-M3 under Apache-only model-weights policy | Reject licence mismatch; show MIT classification. |
| AT-13 | Missing default deployment with no override | Block and surface configuration error; no unapproved model selected. |
| AT-14 | Attempt to activate unapproved or expired BYO binding | Activation denied and audited. |
| AT-15 | Test rollback | Previous model/prompt/tool-policy versions and artefact evidence remain reconstructible. |

## 11. Unresolved questions and approval

**Inputs reviewed:** the formal architecture, product plan and official model/gateway documentation listed below. No existing implementation of Edit Agent override behavior was identified in the reviewed architecture or plan. The rules in this document are proposed requirements, not a claim that the feature is live.

**Decisions requiring approval:** whether Apache 2.0 applies to model weights only or the whole software stack; whether non-Apache BYO models are permitted; approved endpoint types and residency; who may register/approve a BYO model; exact checkpoint revisions; schema/tool-calling acceptance tests; explicit fallback policy; and whether embedding overrides are permitted per project or only at platform level. [Internal A; Internal B]

**Proposed decision:** accept agent-specific active override over the agent's seeded default, with fail-closed behavior on invalid/unavailable overrides and no silent fallback.

**Next permitted step:** approve the configuration schema and RBAC/policy rules, then test the default and override paths against representative ACE workflows before GA.

## 12. Source register

- **[Internal A]** IBM_ACE_Agentic_Migration_Factory_Formal_Architecture 1.docx, enterprise retrieval reference `turn8search33`.
- **[Internal B]** IBM to SpringBoot Migration Product Plan.xlsx, enterprise retrieval reference `turn8search32`.
- **[External Q1]** Qwen/Qwen3-32B model card, web reference `turn8search44`.
- **[External Q2]** Qwen/Qwen3-8B licence, web reference `turn8search75`.
- **[External Q3]** Qwen/Qwen3-Coder-30B-A3B-Instruct licence, web reference `turn8search61`.
- **[External Q4]** Qwen/Qwen3-Embedding-0.6B model card, web reference `turn8search81`.
- **[External B1]** BAAI/bge-m3 model card, web reference `turn8search69`.
- **[External L1]** LiteLLM proxy configuration, web reference `turn8search38`.
- **[External L2]** LiteLLM model access guide, web reference `turn8search51`.
- **[External L3]** LiteLLM model management, web reference `turn8search50`.
- **[External L4]** LiteLLM team model addition, web reference `turn8search54`.
- **[External L5]** LiteLLM vLLM provider documentation, web reference `turn7search27`.
