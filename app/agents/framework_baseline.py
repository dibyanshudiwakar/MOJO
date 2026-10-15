"""Framework Baseline Agent.

Extracts the target framework's conventions from reference repositories and
verifies them against a golden project, producing the framework template that
G1 approves. Wraps ``PriorityOneService.baseline_framework``, which uses
``FrameworkConventionExtractor`` -- the richer of the two extractors in the
codebase (``FrameworkService.analyze`` only lists build files).

Each detected conflict becomes a clarification. A conflict here means the
reference repositories do not agree on a convention, or a mandatory standard was
not detected at all; generating code against an ambiguous baseline would bake
that ambiguity into every migrated service, so the question is raised before G1
rather than discovered after generation.
"""
from app.agents.base import AgentResult, BaseAgent, Clarification, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.services.priority1 import PriorityOneService

OWNER = 'JAVA_ARCHITECT'


class FrameworkBaselineAgent(BaseAgent):
    AGENT_ID = 'framework-baseline'
    SKILL = 'iib-to-ms-framework-baseline'
    TOOLS = ('repository.read', 'evidence.write', 'clarification.create')
    OUTPUTS = ('framework_template', 'conflicts')
    PROMPT = 'agent/framework-baseline/convention-conflict-triage'

    def execute(self, context):
        version = context.param('version', '1.0.0')
        result = PriorityOneService(self.repo).baseline_framework(
            context.job_id,
            context.param('path'),
            version,
            context.param('effective_from', None),
            context.param('golden_path', None))
        row = self.repo.one('select * from framework_baselines where id=?', (result['framework_id'],))
        conflicts = result['conflicts']
        # The conflict labels and the golden verification, not ``template_md``:
        # the template is the whole extracted convention set and sending it would
        # be both the largest payload in the factory and the likeliest to be
        # refused. Every conflict still becomes a clarification regardless of
        # what the model says -- this only tells the architect what the
        # references favour.
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'conflicts': conflicts,
            'golden_verification': result['golden_verification'],
            'version': version})
        clarifications = tuple(
            Clarification('FRAMEWORK_BASELINE',
                          'Which approved convention resolves the framework conflict "%s"?' % x,
                          OWNER,
                          'Generation would encode an unresolved framework convention',
                          'framework://' + result['framework_id'])
            for x in conflicts)
        return AgentResult(
            status='CLARIFICATION_REQUIRED' if conflicts else 'COMPLETED',
            outputs={'framework_template': result['framework_id'], 'conflicts': conflicts},
            # content_hash of a framework baseline is the digest of its template
            # markdown, so this evidence id and the artefact share a hash and one
            # G1 approval binds both.
            evidence=(EvidenceRef('FRAMEWORK_TEMPLATE', version,
                                  'framework://' + result['framework_id'], row['template_md'],
                                  {'conflicts': conflicts,
                                   'golden_verification': result['golden_verification'],
                                   'validation_report_id': result['report_id']}),),
            clarifications=clarifications,
            metrics={'conflict_count': len(conflicts),
                     'golden_status': result['golden_verification']['status'],
                     'model_conflict_advice': (answer or {}).get('conflicts', [])
                     if isinstance(answer, dict) else []},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(FrameworkBaselineAgent))
