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
from app.agents.artefact import produce
from app.services.priority1 import PriorityOneService

OWNER = 'JAVA_ARCHITECT'


class FrameworkBaselineAgent(BaseAgent):
    AGENT_ID = 'framework-baseline'
    SKILL = 'iib-to-ms-framework-baseline'
    TOOLS = ('repository.read', 'evidence.write', 'clarification.create')
    OUTPUTS = ('framework_template', 'conflicts')
    PROMPT = 'agent/framework-baseline/standards'

    def execute(self, context):
        version = context.param('version', '1.0.0')
        service = PriorityOneService(self.repo)
        path = context.param('path')
        effective_from = context.param('effective_from', None)
        extractor = service.framework
        # The build files whole, then a bounded sample of Java and
        # configuration: the versions and dependencies the whole baseline turns
        # on live in the build files, and credential values are redacted before
        # anything reaches the gateway.
        try:
            sources = extractor.sources(path)
        except ValueError:
            sources = None
        if sources is None:
            read = service.compute_framework(path, version, effective_from)
            provenance = {'mode': 'DETERMINISTIC_FALLBACK', 'prompt_name': self.PROMPT,
                          'reason': 'framework_repository_not_accessible',
                          'artefact_source': 'DETERMINISTIC'}
        else:
            answer, provenance = produce(
                context, self.PROMPT,
                {'version': version, 'files': [{'path': p, 'text': t} for p, t in sources]},
                lambda: service.compute_framework(path, version, effective_from))
            # Assembled deterministically either way: the template markdown is
            # what a framework baseline hashes, so both paths must derive it
            # the same way for one G1 approval to bind either.
            read = (extractor.assemble(answer.get('standards') or {},
                                       answer.get('conflicts') or [], version, effective_from)
                    if provenance.get('artefact_source') == 'MODEL' else answer)
        result = service.record_framework(context.job_id, read, version,
                                          context.param('golden_path', None) or path)
        row = self.repo.one('select * from framework_baselines where id=?', (result['framework_id'],))
        conflicts = result['conflicts']
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
