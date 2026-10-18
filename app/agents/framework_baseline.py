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
from app.services import automode
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
        # ValueError('framework_repository_not_accessible') propagates: a path
        # that is not a directory is an operator error. It used to be answered
        # by extracting from that same path, which produced a baseline of empty
        # standards and a G1 approval bound to it.
        sources = extractor.sources(path)
        answer, provenance = produce(
            context, self.PROMPT,
            {'version': version, 'files': [{'path': p, 'text': t} for p, t in sources]})
        # Assembly stays deterministic: the template markdown is what a
        # framework baseline hashes and what a G1 approval binds, so deriving it
        # in code is what keeps the hash a function of the standards rather than
        # of how the model chose to render them.
        read = extractor.assemble(answer.get('standards') or {},
                                  answer.get('conflicts') or [], version, effective_from)
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
            # The baseline is recorded and its template hashed before this
            # point, so an unresolved convention conflict does not make the
            # artefact incomplete -- it makes it annotated. In auto mode the
            # conflicts become recorded decisions and the step proceeds; see
            # app/services/automode.py for why that rule is "complete artefact"
            # rather than "any clarification".
            status='CLARIFICATION_REQUIRED' if conflicts and not automode.enabled() else 'COMPLETED',
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
