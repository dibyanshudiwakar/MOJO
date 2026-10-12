# V7 Architecture Compliance Matrix

| Architecture requirement | V7 reference component | Verification | Production boundary |
|---|---|---|---|
| Migration portal and administration | Expanded command-center resource views | UI resource navigation | Dedicated workflow-specific UX remains organisation-tailored |
| Approval inbox and gate controls | `ApprovalWorkspaceService`, `/api/v7/approval-inbox`, `/api/v7/gates/configure` | Pending-package role filtering and gate metadata | Enterprise group/quorum resolution requires identity integration |
| Artefact and variance review | Existing artefact, comparison, variance and evidence APIs | Resource views and persisted hashes | Rich diff rendering is UI-specific |
| Migration/service registry | `service_registry`, `RegistryService` | Registry create/read | Enterprise CMDB synchronization requires connector mapping |
| Prompt/model gateway governance | `prompt_registry`, `model_registry`, `model_requests` | Approved-version enforcement and secret blocking | Provider invocation requires approved model connector |
| Evidence retrieval/index | `knowledge_index`, `KnowledgeService` | Hash, lineage and deterministic retrieval tests | Vector embedding store is replaceable infrastructure |
| Git operations | `DeliveryAutomationService.git_operation` | Allow-listed local status/branch/tag | Remote authentication and pull-request APIs require provider adapter |
| CI/CD, artifact and deployment automation | `/api/v7/delivery/request` | Connector/category eligibility and audit request | Network execution remains pending external provider integration |
| Notification and escalation foundation | `notifications`, `NotificationService` | Durable queued record | Delivery/retry requires notification adapter worker |
| Operations observability | `observability_events`, summary API | Signal persistence and aggregation | Metrics/log/trace export requires approved observability stack |
| Retention governance | `retention_policies` | Versioned policy records | Enforcement worker and legal rules require organisation approval |
| Confidence-based remediation | Existing V5 confidence engine plus V7 governed AI/evidence registries | Existing tests and versioned inputs | Thresholds and eligible-change classes require approval |
