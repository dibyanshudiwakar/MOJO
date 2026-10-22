# ACE Migration Factory Enterprise Reference v2
A modular reference implementation for governed IBM ACE to Spring Boot migration workflows.

## Included
- Domain model and normalized SQLite persistence
- Validated lifecycle state machine with clarification blocking
- Policy-enforced G0–G5 approval service requiring role and evidence
- SHA-256 evidence registration and coordinated iteration snapshots
- Versioned agent registry and execution contracts
- JSON Schema validated migration YAML
- Deterministic JSON differential comparison with policy controls
- Root-cause hypotheses and remediation records with regression and rollback requirements
- Expanded enterprise workspace UI
- API, security, workflow, schema, policy, differential, RCA and regression tests

## Run
On Windows, `start.ps1` prepares the virtual environment, checks the identity
configuration and launches the server on `127.0.0.1`:

```powershell
.\start.ps1                   # start the server
.\start.ps1 -Port 9000        # on a different port
.\start.ps1 -Test             # run the suite instead of the server
```

Manually, or on other platforms:

```bash
pip install -r requirements.txt
HOST=127.0.0.1 python -m app.server
```

Open `http://localhost:8080`. `HOST` defaults to `0.0.0.0` for the container;
set it explicitly when running locally so the governance API is not on the LAN.

## Signing in

The console issues its own bearer tokens, so nothing needs to be minted by hand.
Choose **Create one** on the sign-in screen and register with a name, role,
email and password; then sign in with that email and password. The token is
returned by `POST /api/v2/auth/login` and carries the roles granted to the
account.

**First setup creates an administrator.** On an empty database the server creates
`admin@ace.local` (set `FACTORY_ADMIN_EMAIL` / `FACTORY_ADMIN_PASSWORD` to choose
them). With no password set, a random one is printed once on the console and saved to
`<db>.admin-credentials` - sign in, then delete that file. Registering first no longer
confers any privilege. The administrator holds only administrative roles
(`GOVERNANCE_ADMIN`, `AI_GOVERNANCE_ADMIN`, `AGENT_REGISTRY_ADMIN`, `GATE_ADMIN`,
`CONNECTOR_ADMIN`, `CLARIFICATION_ADMIN`, `AUDITOR`): it grants roles, configures gates,
models and agents, and reads the audit trail. It cannot hold an agent or gate-approver
role, and the API refuses it any pipeline, job, clarification or approval operation
(`admin_account_cannot_operate_pipelines`). An existing database that already has a
`GOVERNANCE_ADMIN` keeps it and is not re-seeded.

Registration itself only
offers operational roles — approver roles such as `PRODUCT_OWNER` are
granted afterwards through `POST /api/v2/auth/grant-role`, so an account cannot
self-assign the authority to approve its own gate.

`FACTORY_JWT_HS256_SECRET` is optional. When it is not set the server generates a
signing key on first run and persists it as `<database>.signing-key`; set the
variable explicitly to take the key from your own secret store. Generated key
files are git-ignored and must not be committed or shared.

## Connecting GitHub

After signing in, the console resolves the GitHub URL and token stored for the
account. When none is stored it prompts for them, verifies the credential
against GitHub, and shows the result in the header indicator. If GitHub rejects
the request the dialog reports what GitHub said and stays open for a retry.

Tokens are encrypted before storage and are never returned to the browser. The
encryption key comes from `FACTORY_SECRET_KEY`, or is generated on first use and
persisted as `<database>.secret-key`.

Signing out disconnects the current user's GitHub integration and removes its
stored credential. The next login must establish and verify its own GitHub
connection; connections are not shared between users.

## Boundary
This is a local enterprise reference adapter. Identity, Git, CI/CD, object storage, ACE runtime, model gateway, scanners and production orchestration connectors are deliberately not simulated.


### v2.1 completion update
- Completed responsive command-center UI with escaped rendering, state summary, refresh actions, accessible feedback, and migration creation workflow.
- Added job detail and job-scoped list queries.
- Added clarification creation and answer endpoints.
- Added agent registration, start, and completion endpoints.
- Expanded dashboard with state distribution and open clarification count.
- Added request-size validation and a pytest configuration for reproducible test discovery.

### Validation
Run `pytest -q`. The bundled enterprise tests cover lifecycle blocking, approval evidence/role enforcement, migration-specification validation, differential comparison, RCA, and remediation guardrails.


### Architecture agent catalogue seeding
Database initialization now idempotently registers the 14 architecture-defined agents at version 1.0.0 with the `AGENT_OPERATOR` execution role. They are exposed through `GET /api/v2/agent-registry`. Existing registry rows are preserved by `insert or ignore`.

## V2.2 agent governance release
- External JSON agent catalogue with stable machine IDs and complete contract validation.
- Startup contract-drift detection; an existing published version with a mismatched hash blocks initialization.
- Agent-specific execution roles and per-agent tool allow-lists.
- Governance metadata: contract hash, status, effective date, approval reference, and supersession field.
- Registry mutation disabled through the runtime API; catalogue changes require a version-controlled configuration change.
- Registry health and dependency graph endpoints plus UI views.
- Expanded tests for catalogue integrity, drift detection, RBAC, tool authorization, health, and graph generation.

### V2.2 API additions
- `GET /api/v2/agent-registry`
- `GET /api/v2/agent-registry/health`
- `GET /api/v2/agent-registry/graph`
- `POST /api/v2/agents/start` now uses `agent_id`, `version`, `requested_tools`, and evidence references.


## V4.0 governance hardening
See `V4.0_RELEASE_NOTES.md`. Protected mutation APIs require an authenticated
bearer token; sign in to obtain one. `FACTORY_JWT_ISSUER` and
`FACTORY_JWT_AUDIENCE` may be set to match your identity conventions, and
`FACTORY_JWT_HS256_SECRET` to supply the signing key from your own secret store
instead of the generated one.

## V5.0 engineering expansion
See `V5.0_RELEASE_NOTES.md` and `docs/V5_ENGINEERING_GUIDE.md`. V5 adds deterministic ACE/framework extraction, governed Spring project generation, build/runtime adapter contracts, deep differential evidence, evidence-weighted remediation routing, and OIDC/JWKS validation.

## V5.1 Priority-1 readiness
V5.1 adds executable G1/G2 preparation through `/api/v5/priority1/*`: ACE analysis and coverage, consistency validation, framework baseline extraction, and approval-package generation. Generated packages remain `PENDING_APPROVAL` until the existing governed approval API receives an authenticated authorized decision.

## V6.0 planning and validation foundation
V6 adds `/api/v6/*` endpoints for mapping catalogue generation, migration planning, G3 packaging, governed Spring generation, quality validation, and differential evidence preparation. Planning requires artifact-bound G1/G2 approvals; generation requires artifact-bound G3 approval.


### V7.0 architecture gap closure
See V7.0_RELEASE_NOTES.md and docs/V7_GAP_CLOSURE_GUIDE.md. V7 adds the experience, intelligence, delivery automation, notification and operations contracts identified by the formal architecture review.


### V7.2 unified administration release
- Preserves the V7.1 live approval governance APIs, RBAC, audit logging and five-second live refresh.
- Restores the V7.0 Integration Administration Portal for all fifteen connector categories.
- Adds environment filtering, connector health summaries, create-profile form, configuration validation, routing-policy and operations audit views.
- Uses logical secret references; credentials are not accepted in connector configuration.
- Health endpoint reports version 7.2.

## V7.3 agent workers

The 14 catalogue contracts now have implementations. Previously starting an agent
recorded a governed run and nothing else; the migration work sat in
`app/services/*` with no orchestration layer driving it. See
`docs/AGENTS_DEVELOPMENT.md`.

- One standalone module per agent in `app/agents/`, each runnable and testable on
  its own: `python -m app.agents.ace_analysis --db factory-v2.db --job <id> --param path=workspace/ace --token <jwt>`.
- All dispatched through `AgentRegistry.dispatch`, which enforces the approved
  contract: role, tool allow-list, dependency ordering, and the capabilities an
  agent's result may exercise. `GET /api/v2/agent-registry/implementations`
  reports whether every contract has a conformant worker.
- Agent outputs are registered as evidence whose hash equals the artefact's own
  `content_hash`, so one gate approval binds both the evidence row and the
  artefact the next agent reads.
- Model-assisted steps route through the governed model gateway (approved prompt
  and model version, DLP, region). The gateway records the request but cannot
  return a completion without a provider adapter, so the deterministic
  service-backed result is the answer and the reasoning mode is recorded per run.
- `migration-orchestrator` steps a job autonomously, bounded by the gates: from
  `CREATED` it stops at G0 rather than promoting anything unsigned.

New APIs: `POST /api/v2/agents/dispatch`, `POST /api/v2/agents/orchestrate`,
`GET /api/v2/agent-registry/implementations`,
`GET /api/v2/agent-registry/{agent_id}/dependencies?job_id=...`.
