"""ACE Analysis Agent.

Builds the ACE semantic model -- flows, subflows, ESQL, maps, schemas, policies,
interfaces, dependencies and observable behaviour -- and records the constructs
that have no supported target mapping. Wraps
``PriorityOneService.analyze_ace``, which parses through ``ACEParser`` and also
writes the parser coverage report and the unsupported-construct rows.

This agent requests ``clarification.create`` but returns no clarifications of its
own: ``analyze_ace`` already inserts one per ambiguity the parser could not
resolve, on this agent's behalf. Returning them again would duplicate every
question. The tool is in the requested set because the capability really is
exercised during the run -- the registry checks what an agent may do, and this
run does create clarifications.
"""
import json
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.services.priority1 import PriorityOneService


class ACEAnalysisAgent(BaseAgent):
    AGENT_ID = 'ace-analysis'
    SKILL = 'iib-to-ms-ace-analysis'
    TOOLS = ('repository.read', 'parser.execute', 'evidence.write', 'clarification.create')
    OUTPUTS = ('analysis_document', 'semantic_model', 'ambiguities')
    PROMPT = 'agent/ace-analysis/ambiguity-triage'

    def execute(self, context):
        version = context.param('version', '1.0.0')
        result = PriorityOneService(self.repo).analyze_ace(
            context.job_id, context.param('path'), version)
        row = self.repo.one('select * from ace_analyses where id=?', (result['analysis_id'],))
        model = json.loads(row['semantic_model'])
        ambiguities = json.loads(row['ambiguities'] or '[]')
        # The gateway sees the ambiguities and the shape of the model, never the
        # repository contents: a semantic model carries endpoint and queue names
        # from the source system.
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'ambiguities': ambiguities,
            'counts': {k: len(model.get(k, [])) for k in
                       ('flows', 'subflows', 'esql', 'maps', 'schemas', 'policies', 'interfaces')}})
        metrics = {'coverage_score': result['coverage_score'],
                   'unsupported_count': result['unsupported_count'],
                   'ambiguity_count': result['ambiguity_count'],
                   'interface_count': len(model.get('interfaces', [])),
                   # Determined in this step, the same as the master skill's
                   # own INTAKE output states "Backend type: determined in
                   # INTAKE" up front -- code-generation reads model['backend_type']
                   # directly rather than re-deriving it later.
                   'backend_type': model.get('backend_type', {}).get('type', 'UNDETERMINED'),
                   'backend_type_signal': model.get('backend_type', {}).get('signal', '')}
        if isinstance(answer, dict) and answer.get('ambiguities'):
            metrics['model_ambiguity_count'] = len(answer['ambiguities'])
        return AgentResult(
            status='CLARIFICATION_REQUIRED' if ambiguities else 'COMPLETED',
            outputs={'analysis_document': result['analysis_id'],
                     'semantic_model': row['content_hash'],
                     'ambiguities': ambiguities},
            # content_hash of an analysis is the digest of the semantic model
            # json, so a G2 approval binding this evidence also binds the model.
            evidence=(EvidenceRef('ACE_ANALYSIS', version,
                                  'analysis://' + result['analysis_id'], row['semantic_model'],
                                  {'coverage_report_id': result['coverage_report_id'],
                                   'coverage_score': result['coverage_score'],
                                   'unsupported_count': result['unsupported_count']}),),
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(ACEAnalysisAgent))
