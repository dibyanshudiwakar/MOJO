"""Agent worker implementations, one module per catalogue contract.

Each agent is a standalone module: it imports the services it needs and nothing
from its siblings, so it can be run and tested on its own
(``python -m app.agents.ace_analysis --help``). Nothing here performs the
migration work itself -- the work already lives in ``app.services``; an agent
binds one approved contract to the service calls that fulfil it.

The map below holds *strings*, not classes, and no agent module is imported at
package import time. That is deliberate: ``app.services.agents`` imports this
package to resolve implementations, while ``app.agents.orchestrator`` imports
``AgentRegistry`` to dispatch them. Importing the submodules eagerly here would
close that loop into a circular import.
"""
from importlib import import_module

IMPLEMENTATIONS = {
    'migration-orchestrator': 'app.agents.orchestrator:MigrationOrchestratorAgent',
    'repository-intake': 'app.agents.repository_intake:RepositoryIntakeAgent',
    'dormancy-intelligence': 'app.agents.dormancy_intelligence:DormancyIntelligenceAgent',
    'framework-baseline': 'app.agents.framework_baseline:FrameworkBaselineAgent',
    'ace-analysis': 'app.agents.ace_analysis:ACEAnalysisAgent',
    'migration-specification': 'app.agents.migration_specification:MigrationSpecificationAgent',
    'planning': 'app.agents.planning:PlanningAgent',
    'code-generation': 'app.agents.code_generation:CodeGenerationAgent',
    'test-engineering': 'app.agents.test_engineering:TestEngineeringAgent',
    'variance-analysis': 'app.agents.variance_analysis:VarianceAnalysisAgent',
    'rca': 'app.agents.rca:RCAAgent',
    'remediation': 'app.agents.remediation:RemediationAgent',
    'governance': 'app.agents.governance:GovernanceAgent',
    'knowledge-evidence': 'app.agents.knowledge_evidence:KnowledgeEvidenceAgent',
}


def load(agent_id):
    """Resolve a catalogue ``agent_id`` to its implementation class."""
    target = IMPLEMENTATIONS.get(agent_id)
    if not target:
        raise ValueError('agent_implementation_not_found:' + str(agent_id))
    module, _, name = target.partition(':')
    return getattr(import_module(module), name)
