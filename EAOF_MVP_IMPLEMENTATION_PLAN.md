# EAOF --- Enterprise Agent Orchestration Framework

## MVP Full Implementation Plan

### Autonomous Implementation Specification for Claude Code

**Version:** 1.0\
**Status:** Proposed\
**Backend:** Python / FastAPI\
**Frontend:** React / TypeScript\
**Database:** PostgreSQL\
**Execution model:** Async Python DAG executor\
**Deployment:** Docker Compose for MVP

------------------------------------------------------------------------

# 1. Executive Summary

EAOF (Enterprise Agent Orchestration Framework) is a model-agnostic,
schema-driven platform for registering reusable AI agents and composing
them into validated pipelines.

The MVP must prove the following end-to-end capability:

> **Register an agent → discover the agent → compose agents into a
> pipeline → map inputs/outputs using schemas → validate the pipeline →
> execute it → inspect execution results → change the underlying model
> without changing the pipeline.**

The MVP is deliberately focused. It should establish the architectural
foundation without prematurely introducing Kubernetes, Kafka,
microservices, distributed workflow infrastructure, or enterprise-scale
operational complexity.

------------------------------------------------------------------------

# 2. MVP Success Criteria

The MVP is successful when a user can:

1.  Register an agent.
2.  Define its capabilities.
3.  Define its input/output JSON schemas.
4.  Search and discover registered agents.
5.  Create a pipeline visually.
6.  Add registered agents as nodes.
7.  Connect nodes.
8.  Map output fields from one node to input fields of another.
9.  Validate schema compatibility.
10. Detect invalid mappings before execution.
11. Save and version the pipeline.
12. Execute the pipeline.
13. Execute independent nodes in parallel where possible.
14. Persist execution state.
15. View node-level execution status.
16. Retry a failed node.
17. View execution errors.
18. Switch model providers without changing pipeline definitions.
19. Run the same agent against OpenAI-compatible, Anthropic and
    Ollama/local providers.
20. Run all automated tests successfully.

------------------------------------------------------------------------

# 3. Non-Goals for MVP

Do NOT implement these in MVP unless required by an existing repository:

-   Kubernetes
-   Kafka
-   Service mesh
-   Multi-region deployment
-   Distributed workflow engine
-   Enterprise SSO
-   Complex RBAC
-   Billing
-   Multi-tenancy
-   Production secrets vault
-   A2A protocol
-   Advanced MCP marketplace
-   Autonomous agent generation
-   Autonomous pipeline generation
-   Production-grade autoscaling
-   Complex human approval workflows
-   Advanced cost optimization
-   Agent marketplace
-   Fine-tuning infrastructure

The architecture must allow these capabilities later without requiring a
fundamental rewrite.

------------------------------------------------------------------------

# 4. Core Architectural Principle

The framework must NOT be an "LLM chain framework".

The fundamental abstraction is:

``` text
Pipeline
  |
  +-- Node
       |
       +-- Agent
       +-- Tool
       +-- API
       +-- Transform
       +-- Decision
       +-- Pipeline
```

For MVP, implement:

``` text
AGENT
API
TRANSFORM
PIPELINE
```

Other node types can be extension points.

The orchestration engine must operate on the generic `Node` abstraction.

------------------------------------------------------------------------

# 5. High-Level Architecture

``` text
                    ┌───────────────────────────┐
                    │       React UI            │
                    │                           │
                    │ Agent Registry             │
                    │ Pipeline Designer          │
                    │ Schema Mapping             │
                    │ Execution Monitor          │
                    └─────────────┬─────────────┘
                                  │ REST/JSON
                                  ▼
                    ┌───────────────────────────┐
                    │       FastAPI API          │
                    └─────────────┬─────────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
    Agent Registry        Pipeline Service       Execution Service
            │                     │                     │
            └─────────────────────┼─────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │     Schema / Mapping      │
                    │          Engine           │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │    Python DAG Executor    │
                    │                           │
                    │ Async execution            │
                    │ Dependencies               │
                    │ Retry                      │
                    │ Context                    │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
                 Agent          API          Transform
                    │
                    ▼
                 Model Router
              ┌─────┼──────┐
              ▼     ▼      ▼
            OpenAI Claude Ollama

                    │
                    ▼
              PostgreSQL
```

------------------------------------------------------------------------

# 6. Technology Stack

## Backend

Use:

-   Python 3.12+
-   FastAPI
-   Pydantic v2
-   SQLAlchemy 2.x
-   Alembic
-   PostgreSQL
-   httpx
-   pytest
-   pytest-asyncio
-   Docker

## Frontend

Use:

-   React
-   TypeScript
-   Vite
-   React Flow
-   React Query or equivalent
-   A lightweight component library only if useful

## Observability

MVP:

-   structured application logs
-   correlation ID
-   execution ID
-   node execution logs

Prepare extension points for OpenTelemetry.

## Model Providers

MVP:

-   OpenAI-compatible provider
-   Anthropic provider
-   Ollama provider

Do not couple the core framework to any provider SDK.

------------------------------------------------------------------------

# 7. Repository Structure

Use a clean modular structure.

``` text
eaof/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── agents/
│   │   ├── schemas/
│   │   ├── pipelines/
│   │   ├── execution/
│   │   ├── runtime/
│   │   ├── models/
│   │   ├── mapping/
│   │   ├── database/
│   │   ├── common/
│   │   └── main.py
│   │
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── api/
│
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── agents/
│   │   ├── pipelines/
│   │   ├── execution/
│   │   ├── components/
│   │   └── types/
│   └── tests/
│
├── examples/
│   ├── agents/
│   └── pipelines/
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md
│   ├── AGENT_CONTRACT.md
│   ├── PIPELINE_CONTRACT.md
│   ├── IMPLEMENTATION_STATUS.md
│   ├── DECISIONS.md
│   └── KNOWN_ISSUES.md
│
├── docker-compose.yml
├── README.md
└── .env.example
```

If the repository already exists, Claude Code must inspect and adapt it
rather than blindly creating this structure.

------------------------------------------------------------------------

# 8. Phase 0 --- Repository Discovery

## Objective

Understand the existing repository before implementation.

## Claude tasks

1.  Inspect repository.
2.  Identify backend/frontend.
3.  Identify Python/Node versions.
4.  Identify database configuration.
5.  Identify existing tests.
6.  Run current build.
7.  Run current tests.
8.  Identify reusable code.
9.  Identify technical debt.
10. Record baseline.

Create/update:

``` text
docs/ARCHITECTURE.md
docs/IMPLEMENTATION_STATUS.md
docs/DECISIONS.md
docs/KNOWN_ISSUES.md
```

## Gate

Claude must stop after Phase 0 and request approval if the existing
repository requires significant architectural changes.

------------------------------------------------------------------------

# 9. Phase 1 --- Backend Foundation

## Objective

Create a runnable FastAPI application.

Implement:

``` text
GET /health
GET /version
```

Configure:

-   environment settings
-   database connection
-   structured logging
-   exception handling
-   request correlation ID
-   API versioning

Recommended API base:

``` text
/api/v1
```

## Tests

-   application startup
-   health endpoint
-   invalid configuration
-   database connection
-   error handling

## Definition of Done

``` text
✓ Application starts
✓ Docker starts
✓ PostgreSQL connects
✓ Health endpoint works
✓ Tests pass
```

------------------------------------------------------------------------

# 10. Phase 2 --- Agent Contract

## Objective

Define the canonical agent contract.

An agent contains:

``` text
id
name
description
version
status
capabilities
input_schema
output_schema
configuration_schema
runtime_type
model_requirements
metadata
```

Example:

``` json
{
  "id": "summarizer",
  "name": "Document Summarizer",
  "version": "1.0.0",
  "description": "Summarizes text",
  "capabilities": ["summarization"],
  "input_schema": {
    "type": "object",
    "properties": {
      "text": {
        "type": "string"
      }
    },
    "required": ["text"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "summary": {
        "type": "string"
      }
    },
    "required": ["summary"]
  },
  "runtime_type": "LLM"
}
```

Use Pydantic for application-level validation.

Use JSON Schema as the portable schema contract.

------------------------------------------------------------------------

# 11. Phase 3 --- Agent Registry

## Database

Create:

``` text
agents
agent_versions
agent_capabilities
```

Suggested fields:

### agents

``` text
id
name
description
status
created_at
updated_at
```

### agent_versions

``` text
id
agent_id
version
input_schema
output_schema
configuration_schema
runtime_type
metadata
created_at
```

### agent_capabilities

``` text
agent_version_id
capability
```

## APIs

``` text
POST   /api/v1/agents
GET    /api/v1/agents
GET    /api/v1/agents/{agent_id}
GET    /api/v1/agents/{agent_id}/versions
GET    /api/v1/agents/{agent_id}/versions/{version}
PUT    /api/v1/agents/{agent_id}
DELETE /api/v1/agents/{agent_id}
```

Support filtering:

``` text
GET /api/v1/agents?capability=summarization
```

## Validation

Reject:

-   duplicate version
-   malformed schema
-   missing required metadata
-   invalid version
-   invalid runtime type

------------------------------------------------------------------------

# 12. Phase 4 --- Generic Node Contract

Create:

``` python
Node
NodeType
NodeConfiguration
```

Supported MVP types:

``` text
AGENT
API
TRANSFORM
PIPELINE
```

Example:

``` json
{
  "id": "node-1",
  "type": "AGENT",
  "agent_id": "summarizer",
  "agent_version": "1.0.0",
  "configuration": {}
}
```

The pipeline engine must not directly depend on specific Agent
implementations.

------------------------------------------------------------------------

# 13. Phase 5 --- Schema Engine

This is a critical MVP component.

Implement:

``` text
SchemaValidator
SchemaCompatibilityChecker
SchemaMapper
SchemaPathResolver
TransformationResolver
```

## Schema validation

Validate runtime input against JSON Schema.

## Compatibility

Determine whether:

``` text
Source Output
```

can satisfy:

``` text
Target Input
```

## Mapping

Support JSON-path-like mappings:

``` text
source.customer.email
    →
target.recipient
```

## Mapping model

``` json
{
  "source_node": "extract",
  "source_path": "$.customer.email",
  "target_node": "email",
  "target_path": "$.recipient"
}
```

## MVP transformations

Support:

``` text
string
number
boolean
array
object
identity
string conversion
number conversion
```

Do not build a general-purpose programming language into the mapping
engine.

------------------------------------------------------------------------

# 14. Phase 6 --- Pipeline Contract

Pipeline contains:

``` text
id
name
description
version
status
inputs
nodes
edges
mappings
metadata
```

Example:

``` json
{
  "id": "document-processing",
  "version": "1.0.0",
  "inputs": {
    "document": {
      "type": "string"
    }
  },
  "nodes": [
    {
      "id": "extract",
      "type": "AGENT",
      "agent_id": "document-extractor",
      "agent_version": "1.0.0"
    },
    {
      "id": "summarize",
      "type": "AGENT",
      "agent_id": "summarizer",
      "agent_version": "1.0.0"
    }
  ],
  "edges": [
    {
      "from": "extract",
      "to": "summarize"
    }
  ],
  "mappings": [
    {
      "source_node": "extract",
      "source_path": "$.text",
      "target_node": "summarize",
      "target_path": "$.text"
    }
  ]
}
```

------------------------------------------------------------------------

# 15. Phase 7 --- Pipeline Validation

Create:

``` text
PipelineValidator
```

Validation stages:

``` text
1. Structural validation
2. Node validation
3. Agent existence
4. Version existence
5. DAG validation
6. Circular dependency detection
7. Schema validation
8. Mapping validation
9. Required input validation
10. Configuration validation
```

Return machine-readable errors.

Example:

``` json
{
  "valid": false,
  "errors": [
    {
      "node": "risk-agent",
      "path": "$.customer.email",
      "code": "REQUIRED_FIELD_NOT_MAPPED",
      "message": "Required input customer.email has no mapping"
    }
  ]
}
```

------------------------------------------------------------------------

# 16. Phase 8 --- Pipeline Persistence

Create:

``` text
pipelines
pipeline_versions
pipeline_nodes
pipeline_edges
pipeline_mappings
```

The versioned pipeline definition should be immutable once published.

Draft versions may be edited.

Statuses:

``` text
DRAFT
VALIDATED
PUBLISHED
DISABLED
```

------------------------------------------------------------------------

# 17. Phase 9 --- DAG Execution Engine

Implement a Python async DAG executor.

## Execution algorithm

``` text
Load pipeline
    ↓
Validate
    ↓
Create execution
    ↓
Find nodes with no unresolved dependencies
    ↓
Execute ready nodes concurrently
    ↓
Persist outputs
    ↓
Resolve dependencies
    ↓
Execute next nodes
    ↓
Continue
    ↓
Complete pipeline
```

Use `asyncio`.

Independent nodes must be eligible for concurrent execution.

Example:

``` text
        ┌── B ──┐
A ──────┤       ├── D
        └── C ──┘
```

B and C should run concurrently.

------------------------------------------------------------------------

# 18. Phase 10 --- Execution Context

Create:

``` text
ExecutionContext
NodeExecutionContext
```

Execution context should contain:

``` text
execution_id
pipeline_id
pipeline_version
input
node_outputs
variables
metadata
errors
```

Do not expose the entire context to agents.

Use explicit mappings to construct node inputs.

------------------------------------------------------------------------

# 19. Phase 11 --- Execution Persistence

Create:

``` text
pipeline_executions
node_executions
```

### pipeline_executions

``` text
execution_id
pipeline_id
pipeline_version
status
started_at
completed_at
input
output
error
```

### node_executions

``` text
node_execution_id
execution_id
node_id
status
input
output
error
started_at
completed_at
retry_count
```

Statuses:

``` text
PENDING
RUNNING
SUCCESS
FAILED
RETRYING
SKIPPED
```

------------------------------------------------------------------------

# 20. Phase 12 --- Agent Runtime

Define:

``` python
class AgentRuntime:
    async def execute(
        self,
        agent_definition,
        input_data,
        context
    ):
        ...
```

Runtime resolution:

``` text
Agent
  ↓
Runtime Type
  ↓
Runtime Adapter
  ↓
Execution
```

The orchestration engine must never know how a specific LLM agent works.

------------------------------------------------------------------------

# 21. Phase 13 --- Model Provider Abstraction

Define:

``` python
class ModelProvider:
    async def generate(request) -> ModelResponse:
        ...
```

Create:

``` text
OpenAIProvider
AnthropicProvider
OllamaProvider
```

Model request:

``` text
model
messages
temperature
max_tokens
response_format
metadata
```

Model response:

``` text
content
structured_output
usage
model
provider
finish_reason
metadata
```

------------------------------------------------------------------------

# 22. Phase 14 --- Model Router

Implement:

``` text
ModelRouter
```

MVP strategies:

``` text
EXPLICIT
DEFAULT
FALLBACK
```

Example:

``` json
{
  "provider": "ollama",
  "model": "llama3"
}
```

Later strategies can include:

``` text
LOWEST_COST
LOWEST_LATENCY
BEST_QUALITY
LOCAL_ONLY
```

------------------------------------------------------------------------

# 23. Phase 15 --- Reference Agents

Implement at least four reusable agents.

## 1. Text Summarizer

Input:

``` json
{
  "text": "..."
}
```

Output:

``` json
{
  "summary": "..."
}
```

## 2. Entity Extractor

Input:

``` json
{
  "text": "..."
}
```

Output:

``` json
{
  "entities": [
    {
      "type": "PERSON",
      "value": "..."
    }
  ]
}
```

## 3. Classifier

Input:

``` json
{
  "text": "..."
}
```

Output:

``` json
{
  "category": "...",
  "confidence": 0.95
}
```

## 4. Document Analyzer

Input:

``` json
{
  "text": "..."
}
```

Output:

``` json
{
  "summary": "...",
  "entities": [],
  "classification": "..."
}
```

Agents must use the ModelProvider abstraction.

------------------------------------------------------------------------

# 24. Phase 16 --- API Node

Implement an API node capable of:

``` text
HTTP method
URL
headers
query parameters
body
timeout
```

Output:

``` text
status_code
headers
body
```

Security requirement:

Do not allow unrestricted SSRF-prone URLs in production configuration.

MVP may restrict outbound hosts through configuration.

------------------------------------------------------------------------

# 25. Phase 17 --- Transform Node

Implement safe transformations:

``` text
pick field
rename field
merge objects
create object
array mapping
string conversion
number conversion
```

Example:

``` text
Input:
{
  firstName: "John",
  lastName: "Smith"
}

Transform:

firstName + " " + lastName

Output:
{
  fullName: "John Smith"
}
```

Do not execute arbitrary Python code from pipeline definitions.

------------------------------------------------------------------------

# 26. Phase 18 --- Pipeline Node

A pipeline should be executable as a node.

This enables:

``` text
Pipeline A
   ↓
Pipeline B
   ↓
Pipeline C
```

The parent pipeline passes input into the child pipeline.

The child returns its declared output schema.

This establishes composability.

------------------------------------------------------------------------

# 27. Phase 19 --- React Foundation

Create:

``` text
Agent Registry page
Pipeline List page
Pipeline Designer page
Pipeline Execution page
Agent Detail page
```

Backend remains the source of truth.

Frontend must never contain hard-coded agent definitions.

------------------------------------------------------------------------

# 28. Phase 20 --- Agent Registry UI

Display:

``` text
Agent
Capability
Version
Runtime
Status
```

Actions:

``` text
View
Register
Create New Version
Disable
Search
Filter
```

Agent detail should show:

``` text
Metadata
Capabilities
Input Schema
Output Schema
Configuration
Versions
```

------------------------------------------------------------------------

# 29. Phase 21 --- Pipeline Designer

Use React Flow.

Capabilities:

``` text
Drag Agent
Drop Agent
Connect Nodes
Delete Node
Configure Node
Save Draft
Validate
Publish
Execute
```

Agent list should be fetched from:

``` text
GET /api/v1/agents
```

------------------------------------------------------------------------

# 30. Phase 22 --- Schema Mapping UI

When two nodes are connected:

Display:

``` text
SOURCE                       TARGET

$.customer.name       →     $.name
$.customer.email      →     $.recipient
$.invoice.amount      →     $.amount
```

Visual indicators:

``` text
✓ compatible
⚠ transformation required
✗ incompatible
```

The UI must call the backend validation engine rather than duplicating
schema logic.

------------------------------------------------------------------------

# 31. Phase 23 --- Pipeline Validation UI

Add:

``` text
Validate Pipeline
```

Show:

``` text
Pipeline Validation

✓ All nodes valid
✓ Agents exist
✓ Versions exist
✓ DAG valid
✓ Required fields mapped
✓ Schema compatible

Pipeline is ready for execution.
```

Or:

``` text
Pipeline validation failed

✗ risk-agent.customer.email
  Required field is not mapped
```

------------------------------------------------------------------------

# 32. Phase 24 --- Execution UI

Show:

``` text
Pipeline
Status
Execution ID
Started
Duration
```

Node graph should show:

``` text
✓ Completed
● Running
○ Pending
✗ Failed
↻ Retrying
```

Node detail:

``` text
Input
Output
Duration
Model
Provider
Error
Retry count
```

------------------------------------------------------------------------

# 33. Phase 25 --- End-to-End Demo Pipeline

Create the canonical MVP demo:

``` text
                 Input Text
                     │
                     ▼
              Entity Extractor
                     │
                     ▼
                 Summarizer
                     │
                     ▼
                 Classifier
                     │
                     ▼
                  Output
```

Also demonstrate parallel execution:

``` text
                 Input
                   │
          ┌────────┴────────┐
          ▼                 ▼
      Summarizer        Entity Extractor
          │                 │
          └────────┬────────┘
                   ▼
               Analyzer
```

------------------------------------------------------------------------

# 34. MVP Model-Agnostic Demonstration

The same pipeline must execute using:

``` text
Run 1 → OpenAI
Run 2 → Anthropic
Run 3 → Ollama
```

Pipeline definition must remain unchanged.

Only runtime/model configuration changes.

This is a mandatory acceptance criterion.

------------------------------------------------------------------------

# 35. Testing Strategy

Testing is mandatory at every phase.

## Unit tests

Test:

``` text
Agent validation
Schema validation
Schema compatibility
Path resolution
Mapping
Transformations
DAG building
Cycle detection
Dependency resolution
Model routing
Execution state
Retry
```

## Integration tests

Test:

``` text
FastAPI + PostgreSQL
Registry persistence
Pipeline persistence
Execution persistence
Provider adapters
Full pipeline execution
```

## API tests

Test all public APIs.

## Frontend tests

Test:

``` text
Agent loading
Pipeline creation
Node connection
Mapping
Validation
Execution display
```

## End-to-end

At minimum:

``` text
Register agent
 ↓
Create pipeline
 ↓
Validate
 ↓
Execute
 ↓
Inspect result
```

------------------------------------------------------------------------

# 36. Failure Scenarios

The MVP must explicitly test:

``` text
Unknown agent
Unknown agent version
Duplicate agent version
Malformed schema
Invalid mapping
Missing required field
Circular pipeline
Disconnected node
Agent timeout
Model timeout
Provider unavailable
Malformed model response
API node failure
Transformation failure
Node retry
Pipeline failure
Partial execution
Concurrent execution
```

------------------------------------------------------------------------

# 37. Retry Policy

MVP supports node-level retry.

Configuration:

``` json
{
  "max_retries": 2,
  "retryable_errors": [
    "TIMEOUT",
    "PROVIDER_UNAVAILABLE"
  ]
}
```

Do not retry deterministic validation errors.

------------------------------------------------------------------------

# 38. Error Model

Use a standard error structure:

``` json
{
  "code": "SCHEMA_VALIDATION_FAILED",
  "message": "Required field is missing",
  "node_id": "summarizer",
  "path": "$.text",
  "retryable": false,
  "details": {}
}
```

Errors must be machine-readable and human-readable.

------------------------------------------------------------------------

# 39. Security Baseline

MVP must:

-   validate all API inputs
-   never execute arbitrary Python from pipeline definitions
-   protect database credentials through environment variables
-   avoid logging API keys
-   avoid logging sensitive model prompts by default
-   restrict outbound API hosts where appropriate
-   validate uploaded/configured URLs
-   separate configuration from secrets

Prepare interfaces for future secrets management.

------------------------------------------------------------------------

# 40. Configuration

Use environment variables.

Example:

``` text
DATABASE_URL
OPENAI_API_KEY
ANTHROPIC_API_KEY
OLLAMA_BASE_URL
LOG_LEVEL
CORS_ORIGINS
ALLOWED_OUTBOUND_HOSTS
```

Provide:

``` text
.env.example
```

Never commit real credentials.

------------------------------------------------------------------------

# 41. Docker Compose

MVP services:

``` text
frontend
backend
postgres
ollama (optional)
```

Do not add Redis unless required by implementation.

Expected command:

``` text
docker compose up
```

should bring up the development environment.

------------------------------------------------------------------------

# 42. API Summary

## Agents

``` text
POST   /api/v1/agents
GET    /api/v1/agents
GET    /api/v1/agents/{id}
GET    /api/v1/agents/{id}/versions
POST   /api/v1/agents/{id}/versions
```

## Pipelines

``` text
POST   /api/v1/pipelines
GET    /api/v1/pipelines
GET    /api/v1/pipelines/{id}
POST   /api/v1/pipelines/{id}/validate
POST   /api/v1/pipelines/{id}/publish
POST   /api/v1/pipelines/{id}/execute
```

## Executions

``` text
GET /api/v1/executions/{execution_id}
GET /api/v1/executions/{execution_id}/nodes
POST /api/v1/executions/{execution_id}/retry
```

## Schema

``` text
POST /api/v1/schema/validate
POST /api/v1/schema/compatibility
POST /api/v1/schema/map
```

------------------------------------------------------------------------

# 43. Database Migration Strategy

Use Alembic.

Every schema change must:

1.  Create migration.
2.  Apply migration.
3.  Test clean database creation.
4.  Test upgrade.
5.  Test downgrade where practical.
6.  Run integration tests.

Never manually alter production-like schemas without migrations.

------------------------------------------------------------------------

# 44. Documentation Requirements

Maintain:

``` text
README.md
docs/ARCHITECTURE.md
docs/AGENT_CONTRACT.md
docs/PIPELINE_CONTRACT.md
docs/API.md
docs/IMPLEMENTATION_STATUS.md
docs/DECISIONS.md
docs/KNOWN_ISSUES.md
```

Every architectural decision must be recorded in `DECISIONS.md`.

------------------------------------------------------------------------

# 45. Claude Code Autonomous Development Protocol

Claude Code must operate in small increments.

For every task:

``` text
1. Read relevant documentation.
2. Inspect existing implementation.
3. Identify affected modules.
4. Create implementation plan.
5. Implement.
6. Run formatting/linting.
7. Run unit tests.
8. Run integration tests.
9. Run regression tests.
10. Inspect failures.
11. Identify root cause.
12. Fix.
13. Re-run tests.
14. Review architecture.
15. Update documentation.
16. Mark task complete.
```

------------------------------------------------------------------------

# 46. Self-Healing Development Rule

If a test fails:

``` text
DO NOT:
- delete the test
- weaken the assertion
- skip the test
- hard-code the expected result
- suppress the exception
```

Instead:

``` text
Failure
  ↓
Root cause
  ↓
Implementation correction
  ↓
Test
  ↓
Regression
```

If multiple failures indicate an architectural issue:

``` text
Stop implementation
      ↓
Analyze architecture
      ↓
Determine correct abstraction
      ↓
Refactor
      ↓
Re-run complete regression
```

------------------------------------------------------------------------

# 47. Autonomous Rebuild Rule

If Claude determines that a module should be substantially redesigned,
it may recreate that module if:

-   the public contract remains compatible, OR
-   the change is within the current approval boundary.

If the public contract or architecture must materially change:

``` text
STOP
Explain:
1. Current design
2. Problem
3. Proposed design
4. Impact
5. Migration requirement
6. Why patching is inferior
REQUEST APPROVAL
```

Do not silently redesign major components.

------------------------------------------------------------------------

# 48. Approval Gates

## Gate 1 --- Architecture

After Phase 0.

Review:

``` text
Repository
Architecture
Technology choices
Risks
```

## Gate 2 --- Core Contracts

After Agent + Node + Schema contracts.

Review:

``` text
Agent Contract
Pipeline Contract
Schema Contract
```

These contracts should be frozen before extensive implementation.

## Gate 3 --- Execution Engine

Review:

``` text
DAG execution
Context model
Failure handling
Concurrency
```

## Gate 4 --- MVP Release

Review:

``` text
End-to-end demo
Tests
Known issues
Architecture
Security
```

------------------------------------------------------------------------

# 49. Definition of Done --- MVP

The MVP is complete only when all of the following are true:

``` text
✓ Backend starts
✓ Frontend starts
✓ PostgreSQL works
✓ Agent registry works
✓ Agent versioning works
✓ Capability search works
✓ JSON schema validation works
✓ Schema compatibility works
✓ Schema mapping works
✓ Pipeline creation works
✓ Pipeline validation works
✓ DAG execution works
✓ Parallel execution works
✓ Retry works
✓ Execution persistence works
✓ Node execution status works
✓ Model abstraction works
✓ OpenAI provider works
✓ Anthropic provider works
✓ Ollama provider works
✓ Same pipeline can switch providers
✓ API node works
✓ Transform node works
✓ Pipeline node architecture exists
✓ React pipeline designer works
✓ Agent catalog works
✓ Schema mapping UI works
✓ Execution dashboard works
✓ Error handling works
✓ Unit tests pass
✓ Integration tests pass
✓ API tests pass
✓ Frontend tests pass
✓ End-to-end tests pass
✓ Docker environment works
✓ Documentation updated
✓ No critical known defects
```

------------------------------------------------------------------------

# 50. MVP Acceptance Test

A fresh developer should be able to do:

``` text
1. Start Docker Compose.

2. Open the UI.

3. Register:
   Entity Extractor v1

4. Register:
   Summarizer v1

5. Create pipeline:
   Entity Extractor → Summarizer

6. Connect:
   extractor.output.text
       →
   summarizer.input.text

7. Validate.

8. Pipeline reports:
   VALID

9. Execute with sample input.

10. Observe:
    Entity Extractor → SUCCESS
    Summarizer → SUCCESS

11. View final output.

12. Change model:
    OpenAI → Ollama

13. Execute again.

14. Pipeline definition remains unchanged.

15. View second execution.
```

This is the primary MVP demonstration.

------------------------------------------------------------------------

# 51. Performance Targets for MVP

These are engineering targets, not production SLAs.

``` text
API health response       < 200ms
Registry search           < 500ms
Pipeline validation       < 1s for normal MVP graph
Pipeline persistence      < 500ms
Non-LLM node execution    < 1s excluding external API
```

LLM latency is provider-dependent and should not be included in platform
latency targets.

------------------------------------------------------------------------

# 52. Extensibility Requirements

The following must be possible without rewriting the orchestration
engine:

``` text
Add new model provider
Add new agent
Add new node type
Add MCP support
Add A2A support
Add distributed workers
Add Redis
Add Temporal
Add authentication
Add RBAC
Add multi-tenancy
Add agent marketplace
```

This is a core architectural acceptance criterion.

------------------------------------------------------------------------

# 53. Future Roadmap

After MVP:

## Phase 2 --- Enterprise

``` text
RBAC
SSO
Audit
Secrets management
Policy engine
Agent approval
Pipeline approval
Advanced observability
```

## Phase 3 --- Distributed Execution

``` text
Redis
Workers
Temporal evaluation
Kubernetes
Autoscaling
```

## Phase 4 --- Agent Ecosystem

``` text
MCP
A2A
External agents
Agent marketplace
Agent health scoring
Agent certification
```

## Phase 5 --- Intelligent Orchestration

``` text
Semantic agent discovery
Automatic schema mapping
Agent recommendation
Model routing
Cost optimization
AI-assisted pipeline generation
Pipeline optimization
```

------------------------------------------------------------------------

# 54. Strategic Product Positioning

EAOF should NOT be positioned as another LLM framework.

Position it as:

> **A model-agnostic enterprise AI capability and orchestration layer.**

Core value:

``` text
Agents
   ↓
Registered capabilities
   ↓
Schema contracts
   ↓
Composable pipelines
   ↓
Governed execution
```

The framework should be capable of integrating with:

``` text
OpenAI
Anthropic
Google
Ollama
LangGraph
CrewAI
AWS AgentCore
MCP
A2A
Enterprise APIs
Internal AI services
```

These are integrations, not dependencies of the core architecture.

------------------------------------------------------------------------

# 55. Final Architecture Principle

The framework must preserve this separation:

``` text
┌───────────────────────────────┐
│         BUSINESS FLOW         │
│           Pipeline            │
└───────────────┬───────────────┘
                │
┌───────────────▼───────────────┐
│          CAPABILITY           │
│             Agent             │
└───────────────┬───────────────┘
                │
┌───────────────▼───────────────┐
│            RUNTIME            │
│        Agent Runtime          │
└───────────────┬───────────────┘
                │
┌───────────────▼───────────────┐
│         MODEL PROVIDER        │
│ GPT / Claude / Gemini / Local │
└───────────────────────────────┘
```

**Pipeline must never depend directly on a model.**

**Agent must never hard-code a model provider.**

**Orchestration must never depend on a specific agent implementation.**

**Schema must be the contract between nodes.**

**Registry must be the source of reusable capabilities.**

------------------------------------------------------------------------

# 56. Claude Code Initial Prompt

After placing this document at:

``` text
/docs/EAOF_MVP_IMPLEMENTATION_PLAN.md
```

use this as the initial Claude Code instruction:

``` text
Read /docs/EAOF_MVP_IMPLEMENTATION_PLAN.md completely.

You are the autonomous lead engineer responsible for implementing this MVP.

Do not start by writing code.

First inspect the repository, determine its current state, run the existing build/tests, and execute Phase 0.

Maintain:
- docs/IMPLEMENTATION_STATUS.md
- docs/DECISIONS.md
- docs/KNOWN_ISSUES.md

Work incrementally.

For every implementation:
1. Understand the requirement.
2. Inspect existing code.
3. Plan the smallest coherent change.
4. Implement it.
5. Run formatting/linting.
6. Run unit tests.
7. Run integration tests.
8. Run regression tests.
9. Inspect every failure.
10. Determine root cause.
11. Fix the implementation.
12. Re-run affected tests.
13. Run the regression suite again.
14. Update documentation.
15. Reassess architecture.

Never:
- remove tests to make the build pass
- weaken assertions without justification
- suppress errors
- bypass schema validation
- hard-code agents into the frontend
- hard-code model providers into agents
- execute arbitrary Python from pipeline definitions
- introduce unnecessary infrastructure

If you discover an architectural problem, fix the root cause rather than adding a workaround.

You may refactor or recreate internal modules when necessary.

If a proposed change crosses an approval gate in the implementation plan, stop and request approval before proceeding.

Do not claim a phase is complete until its Definition of Done is satisfied.

Start with Phase 0 only.
```

------------------------------------------------------------------------

# 57. Final MVP Philosophy

The MVP should prove **architecture**, not feature quantity.

The most important demonstration is:

``` text
REGISTER
   ↓
DISCOVER
   ↓
COMPOSE
   ↓
MAP
   ↓
VALIDATE
   ↓
EXECUTE
   ↓
OBSERVE
   ↓
CHANGE MODEL
   ↓
EXECUTE AGAIN
```

If this flow works reliably, the framework has a strong foundation for
the subsequent enterprise platform.

**Primary architectural thesis:**

> **Agents provide capabilities. Schemas provide contracts. Pipelines
> provide composition. The runtime provides execution. The registry
> provides reuse. Models remain replaceable infrastructure.**
