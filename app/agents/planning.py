"""Planning Agent.

Produces the mapping catalogue, the ordered implementation task graph and the
risk register that G3 approves. Wraps ``MappingAndPlanningService``, which is
where the gate checks live: creating a catalogue requires an artefact-bound G1
approval of the framework and G2 approvals of both the analysis and the
specification, and refuses outright while any clarification is open.

When the catalogue contains a construct with no approved Spring pattern, the
service records an unsupported mapping, a HIGH risk and a clarification, and
``generate_plan`` refuses. This agent stops there and reports the catalogue and
risks rather than planning around the gap: a plan that silently omitted an
unmappable construct would present as complete while leaving behaviour behind.

Unlike the analysis and framework agents, this one registers **no evidence**: its
approved contract grants ``evidence.read`` and ``plan.write``, not
``evidence.write``. It assembles the G3 approval package instead, which is the
artefact-binding record for the gate. Registering the plan as the evidence a G3
approval cites is therefore the approver's act, not the planner's -- see
``docs/AGENTS_DEVELOPMENT.md``.
"""
import json
from app.agents.base import AgentResult, BaseAgent
from app.agents.reasoning import ReasoningGateway
from app.services.migration_lifecycle_v6 import MappingAndPlanningService


class PlanningAgent(BaseAgent):
    AGENT_ID = 'planning'
    TOOLS = ('evidence.read', 'plan.write', 'clarification.create')
    OUTPUTS = ('plan', 'task_graph', 'mapping', 'risk_register')
    PROMPT = 'agent/planning/risk-assessment'

    def execute(self, context):
        version = context.param('version', '1.0.0')
        service = MappingAndPlanningService(self.repo)
        catalogue = service.create_mapping_catalogue(
            context.job_id,
            context.param('framework_id'),
            context.param('analysis_id'),
            context.param('specification_id'),
            version,
            context.param('overrides', None))
        risks = self.repo.rows(
            'select id,category,severity,statement,response,owner,status from migration_risks'
            ' where catalogue_id=? order by created_at', (catalogue['catalogue_id'],))
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'mapping_count': catalogue['mapping_count'],
            'unsupported_count': catalogue['unsupported_count'],
            'risks': [x['statement'] for x in risks]})
        metrics = {'mapping_count': catalogue['mapping_count'],
                   'unsupported_count': catalogue['unsupported_count'],
                   'risk_count': len(risks)}
        if isinstance(answer, dict) and answer.get('risks'):
            metrics['model_risk_count'] = len(answer['risks'])
        if catalogue['unsupported_count']:
            # The service has already raised a clarification per unsupported
            # construct, so the questions exist; there is simply no plan yet.
            return AgentResult(
                status='CLARIFICATION_REQUIRED',
                outputs={'mapping': catalogue['catalogue_id'], 'risk_register': risks,
                         'task_graph': [], 'plan': ''},
                metrics=metrics, reasoning=provenance,
                error='')
        plan = service.generate_plan(context.job_id, catalogue['catalogue_id'], version)
        row = self.repo.one('select * from migration_plans where id=?', (plan['plan_id'],))
        tasks = json.loads(row['tasks'])
        package = service.g3_package(context.job_id, plan['plan_id'], catalogue['catalogue_id'])
        return AgentResult(
            outputs={'plan': plan['plan_id'],
                     'task_graph': [{'id': x['id'], 'depends_on': x['depends_on'],
                                     'source_reference': x['source_reference'],
                                     'boundary': x['boundary']} for x in tasks],
                     'mapping': catalogue['catalogue_id'],
                     'risk_register': risks},
            metrics={**metrics, 'task_count': plan['task_count'],
                     'plan_content_hash': row['content_hash'],
                     'g3_package_id': package['package_id'],
                     'implementation_sequence': plan['implementation_sequence']},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(PlanningAgent))
