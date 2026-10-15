"""Variance Analysis Agent.

Compares the ACE and Spring Boot results the test engineering agent captured and
records the variances, classified as structural, type or value with a severity.

It writes to *both* variance stores deliberately, because the two are read by
different governance checks and neither alone is sufficient:

* ``DifferentialService.compare`` writes the ``comparisons`` and ``variances``
  rows -- the canonical variance store the RCA agent reads.
* ``MappingAndPlanningService.record_differential`` writes the
  ``differential_evidence`` row, which is what the ``evidence_bundle``
  precondition inspects before VARIANCE_REVIEW and COMPLETED.

Both are backed by the same ``DifferentialEvidenceEngine`` and the same policy,
so they cannot disagree about whether a case matched. Writing only one would
leave either RCA unable to see half the variances or promotion unable to see any.
"""
import json
from app.agents.base import AgentResult, BaseAgent
from app.agents.skills import SkillIntegrityError
from app.agents.reasoning import ReasoningGateway
from app.repository import digest, now, uid
from app.services import workspace
from app.services.differential import DifferentialService
from app.services.factory_v3 import IntakeService
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

RANK = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}

#: Governed prompt names and the reference file that is each check's guidance.
PARITY_PROMPT = 'agent/variance-analysis/parity-check'
PARITY_SKILL_FILE = 'parity-skill.md'
HARDCODING_PROMPT = 'agent/variance-analysis/hardcoding-check'
HARDCODING_SKILL_FILE = 'hardcoding-skill.md'
#: Total characters of source sent to the model for one static check. Beyond this the
#: step is skipped, not truncated: a report built from half a service would read as complete.
PARITY_MAX_CHARS = 600_000


def _parity_summary(answer):
    return {'counts': answer.get('counts', {}), 'severity_counts': answer.get('severity_counts', {}),
            'findings': len(answer.get('findings') or [])}


def _hardcoding_summary(answer):
    return {'counts': answer.get('counts', {}), 'suggestions': len(answer.get('suggestions') or [])}


def _is_java(path):
    """The hardcoding skill's scope: Java source only; config files are explicitly out."""
    return path.endswith('.java') and '/src/test/' not in '/' + path


#: The opt-in static comparisons of IIB source against the migrated code. Each is model-only,
#: has its own governed prompt, takes its own reference file as the guidance (so its output
#: contract is never mixed with another step's), and stores one validation report.
#: ``iib_ext`` is the IIB side the skill says it reads; ``ms_ok`` filters the migrated side.
CHECKS = {
    'parity': {
        'prompt': PARITY_PROMPT, 'skill_file': PARITY_SKILL_FILE, 'action': 'parity_check',
        'report_type': 'PARITY_ANALYSIS', 'summary': _parity_summary,
        'iib_ext': frozenset(IntakeService.EXT), 'ms_ok': lambda path: True,
        'no_ms_reason': 'no_generated_artifacts'},
    'hardcoding': {
        'prompt': HARDCODING_PROMPT, 'skill_file': HARDCODING_SKILL_FILE, 'action': 'hardcoding_check',
        'report_type': 'HARDCODING_ANALYSIS', 'summary': _hardcoding_summary,
        'iib_ext': frozenset({'.esql', '.msgmap', '.msgflow', '.subflow', '.xsd'}), 'ms_ok': _is_java,
        'no_ms_reason': 'no_ms_java_files'},
}


class VarianceAnalysisAgent(BaseAgent):
    AGENT_ID = 'variance-analysis'
    SKILL = 'iib-to-ms-variance-analysis'
    TOOLS = ('evidence.read', 'comparison.execute', 'variance.write')
    OUTPUTS = ('variance_records', 'severity')
    PROMPT = 'agent/variance-analysis/severity-rationale'

    def _executions(self, context, package_id):
        """The captured runtime result pairs for this package."""
        supplied = context.param('results', None)
        if supplied:
            return supplied, context.param('policy', {})
        report = self.repo.one(
            "select * from validation_reports where job_id=? and report_type='RUNTIME_EXECUTION'"
            ' and reference_id=? order by created_at desc limit 1', (context.job_id, package_id))
        if not report:
            raise ValueError('runtime_execution_report_not_found:' + str(package_id))
        payload = json.loads(report['payload'])
        if not payload.get('executions'):
            raise ValueError('differential_package_not_executed:' + str(package_id))
        return payload['executions'], context.param('policy', payload.get('policy') or {})

    def _static_check(self, context, name):
        """Static comparison of the IIB source against the migrated code. Opt-in, model-only.

        Inputs come from the job's own records -- never from a caller-supplied path -- and
        the result is stored as a validation report (``spec['report_type']``). It is not
        written to the variance store and is not an agent output: a model-produced row must
        not open or close a variance, and the contract's outputs stay as approved.
        """
        spec = CHECKS[name]
        manifest = self.repo.one('select * from intake_manifests where job_id=? order by created_at desc limit 1',
                                 (context.job_id,))
        analysis = self.repo.one('select id from ace_analyses where job_id=? order by created_at desc limit 1',
                                 (context.job_id,))
        artifacts = self.repo.rows('select path,content from generated_artifacts where job_id=? order by path',
                                   (context.job_id,))
        if not manifest:
            return {'status': 'SKIPPED', 'reason': 'no_intake_manifest'}
        if not artifacts:
            return {'status': 'SKIPPED', 'reason': 'no_generated_artifacts'}
        ms_files = {a['path']: a['content'] for a in artifacts if spec['ms_ok'](a['path'])}
        if not ms_files:
            return {'status': 'SKIPPED', 'reason': spec['no_ms_reason']}
        try:
            root = workspace.resolve(manifest['repository_uri'], must_exist=True)
        except Exception:
            return {'status': 'SKIPPED', 'reason': 'iib_source_unavailable'}
        iib_files = {}
        for f in sorted(root.rglob('*')):
            if f.is_file() and f.suffix in spec['iib_ext'] and '.git' not in f.parts:
                iib_files[f.relative_to(root).as_posix()] = f.read_text(encoding='utf-8', errors='replace')
        if not iib_files:
            return {'status': 'SKIPPED', 'reason': 'no_iib_source_files'}
        size = sum(len(v) for v in iib_files.values()) + sum(len(v) for v in ms_files.values())
        if size > PARITY_MAX_CHARS:
            return {'status': 'SKIPPED', 'reason': 'source_too_large:%d>%d' % (size, PARITY_MAX_CHARS)}
        try:
            own = context.skill()
            guidance = own.focus(spec['skill_file']) if own else None
        except (SkillIntegrityError, KeyError) as exc:
            return {'status': 'SKIPPED', 'reason': name + '_skill_unavailable:' + str(exc)}
        if guidance is None:
            return {'status': 'SKIPPED', 'reason': name + '_skill_unavailable:no_skill'}
        answer, provenance = ReasoningGateway(self.repo).reason(
            context, spec['prompt'], {'iib_files': iib_files, 'ms_files': ms_files}, skill=guidance)
        if answer is None:
            return {'status': 'NOT_ANALYSED', 'mode': provenance.get('mode', ''),
                    'reason': provenance.get('reason', '')}
        if not (isinstance(answer, dict) and answer.get('action') == spec['action']
                and isinstance(answer.get('rows'), list)):
            return {'status': 'INVALID_RESPONSE', 'reason': 'not_a_%s_report' % spec['action']}
        payload = json.dumps({'report': answer, 'provenance': provenance}, sort_keys=True, default=str)
        report_id = uid()
        with self.repo.connect() as c:
            self.repo.insert(c, 'validation_reports',
                             {'id': report_id, 'job_id': context.job_id, 'report_type': spec['report_type'],
                              'version': '1.0.0', 'payload': payload, 'content_hash': digest(payload),
                              'status': 'ANALYSED', 'created_at': now(),
                              'reference_id': (analysis or {}).get('id', '')})
        return {'status': 'ANALYSED', 'report_id': report_id, **spec['summary'](answer)}

    def execute(self, context):
        package_id = context.param('package_id')
        executions, policy = self._executions(context, package_id)
        lifecycle = MappingAndPlanningService(self.repo)
        differential = DifferentialService(self.repo)
        records = []
        for x in executions:
            recorded = lifecycle.record_differential(
                context.job_id, package_id, x['case_id'], x['ace_result'], x['spring_result'], policy)
            comparison = differential.compare(
                context.job_id, x['case_id'], x['ace_result'], x['spring_result'], policy)
            records.append({'case_id': x['case_id'], 'evidence_id': recorded['evidence_id'],
                            'comparison_id': comparison['comparison_id'],
                            'status': recorded['status'],
                            'variance_count': len(recorded['variances']),
                            'variances': recorded['variances']})
        flat = [v for x in records for v in x['variances']]
        severity = max((v['severity'] for v in flat), key=lambda s: RANK.get(s, 0), default='NONE')
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'policy': policy,
            'variances': [{'path': v['path'], 'kind': v['kind'], 'severity': v['severity']} for v in flat]})
        metrics = {'case_count': len(records), 'variance_count': len(flat),
                   'variance_cases': sum(1 for x in records if x['status'] == 'VARIANCE'),
                   'by_kind': {k: sum(1 for v in flat if v['kind'] == k)
                               for k in sorted({v['kind'] for v in flat})}}
        if isinstance(answer, dict) and answer.get('assessments'):
            # Advisory only: a model may not close a variance. Disposition is the
            # G4 decision recorded through ``disposition_differential``.
            metrics['model_behavioural_count'] = sum(1 for a in answer['assessments'] if a.get('behavioural'))
        for name in CHECKS:
            if context.param(name, False):
                metrics[name] = self._static_check(context, name)
        return AgentResult(
            outputs={'variance_records': [{'case_id': x['case_id'], 'evidence_id': x['evidence_id'],
                                           'comparison_id': x['comparison_id'], 'status': x['status'],
                                           'variance_count': x['variance_count']} for x in records],
                     'severity': severity},
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(VarianceAnalysisAgent))
