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
from app.agents.reasoning import ReasoningGateway
from app.services.differential import DifferentialService
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

RANK = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}


class VarianceAnalysisAgent(BaseAgent):
    AGENT_ID = 'variance-analysis'
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
        return AgentResult(
            outputs={'variance_records': [{'case_id': x['case_id'], 'evidence_id': x['evidence_id'],
                                           'comparison_id': x['comparison_id'], 'status': x['status'],
                                           'variance_count': x['variance_count']} for x in records],
                     'severity': severity},
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(VarianceAnalysisAgent))
