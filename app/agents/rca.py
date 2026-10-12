"""RCA Agent.

Groups a job's open variances by classification and proposes one root-cause
hypothesis per group, with a confidence score and an explicit list of the
evidence that is still missing.

Every confidence component is a ratio measured over the job's own records rather
than a number chosen by the agent. That matters because the score decides
whether remediation may be applied directly:
``ConfidenceRemediationService.eligibility`` compares it against a governed
threshold, so a component the agent could pick freely would let it authorise its
own fix. The weights belong to that service, which is the governed authority.

Structural, type and value variances are kept apart because they imply different
causes -- a missing field is a mapping omission, a changed type is a contract
mismatch, a changed value is transformation logic -- and a hypothesis spanning
all three would be too vague to act on.
"""
import json
from app.agents.base import AgentResult, BaseAgent
from app.agents.reasoning import ReasoningGateway
from app.services.enterprise_v5 import ConfidenceRemediationService
from app.services.rca import RCAService

HYPOTHESIS = {
    'STRUCTURAL': 'A field present in one runtime is absent in the other: the mapping for %s omits or adds a construct.',
    'TYPE': 'The runtimes disagree on the type at %s: the generated contract model diverges from the ACE message model.',
    'VALUE': 'The runtimes agree on shape but not value at %s: transformation logic differs from the ESQL or map it replaced.',
}
STRENGTH = {'STRUCTURAL': 1.0, 'TYPE': 0.8, 'VALUE': 0.6}


class RCAAgent(BaseAgent):
    AGENT_ID = 'rca'
    TOOLS = ('evidence.read', 'repository.read', 'rca.write')
    OUTPUTS = ('rca_hypotheses', 'confidence', 'missing_evidence')
    PROMPT = 'agent/rca/hypothesis'

    def _open_variances(self, job_id):
        return self.repo.rows(
            "select v.*,c.case_id from variances v left join comparisons c on c.id=v.comparison_id"
            " where v.job_id=? and v.disposition='OPEN' order by v.created_at", (job_id,))

    def _history(self, job_id):
        """How often a prior root cause on this job reached a remediation.

        A job whose earlier hypotheses were acted on is a job whose analysis has
        been holding up; one with none has no track record, which is 0.0 rather
        than a neutral guess.
        """
        prior = self.repo.one('select count(*) n from root_causes where job_id=?', (job_id,))['n']
        if not prior:
            return 0.0
        acted = self.repo.one('select count(distinct root_cause_id) n from remediations where job_id=?', (job_id,))['n']
        return min(1.0, acted / prior)

    def _components(self, group, variances, history):
        size = len(group)
        paths = [x['path'] for x in group]
        # A path seen in more than one test case reproduces; one seen once may be
        # specific to that case's data.
        repeats = sum(1 for p in paths if sum(1 for v in variances if v['path'] == p) > 1)
        return {
            # Normalisation records the policy hash the comparison ran under. A
            # variance without it cannot be re-derived, so it is not covered.
            'evidence_coverage': sum(1 for x in group if json.loads(x.get('normalisation') or '{}').get('policy_hash')) / size,
            'reproducible': repeats / size,
            # Traceable when the variance ties back to a named differential case
            # rather than floating free of the approved test package.
            'traceability': sum(1 for x in group if x.get('case_id')) / size,
            'rule_strength': STRENGTH.get(group[0]['kind'], 0.6),
            # Isolated when this group is the only kind failing in its cases.
            'test_isolation': sum(1 for x in group if len({v['kind'] for v in variances if v.get('case_id') == x.get('case_id')}) == 1) / size,
            'historical_validation': history,
        }

    def execute(self, context):
        variances = self._open_variances(context.job_id)
        if not variances:
            return AgentResult(status='COMPLETED',
                               outputs={'rca_hypotheses': [], 'confidence': 0.0, 'missing_evidence': []},
                               metrics={'variance_count': 0, 'hypothesis_count': 0})
        service = RCAService(self.repo)
        scorer = ConfidenceRemediationService()
        history = self._history(context.job_id)
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'variances': [{'path': x['path'], 'kind': x['kind'], 'severity': x['severity']} for x in variances]})
        kinds = sorted({x['kind'] for x in variances})
        hypotheses = []
        missing = []
        for kind in kinds:
            group = [x for x in variances if x['kind'] == kind]
            components = self._components(group, variances, history)
            score = scorer.score(**components)
            gaps = sorted({g for g in (
                '' if components['reproducible'] else 'a second test case exercising the same path',
                '' if components['traceability'] else 'the differential case each variance came from',
                '' if components['evidence_coverage'] else 'the comparison policy each variance was normalised under',
                '' if components['historical_validation'] else 'a previously validated root cause on this job',
            ) if g})
            paths = sorted({x['path'] for x in group})
            statement = HYPOTHESIS[kind] % ', '.join(paths[:5]) if kind in HYPOTHESIS else 'Unclassified variance at ' + ', '.join(paths[:5])
            recorded = service.record_root_cause(
                context.job_id, [x['id'] for x in group], statement, score['score'],
                [{'variance_id': x['id'], 'path': x['path'], 'case_id': x.get('case_id') or ''} for x in group],
                gaps, 'PATHS:' + ','.join(paths[:10]))
            hypotheses.append({'root_cause_id': recorded['id'], 'kind': kind, 'hypothesis': statement,
                               'confidence': score['score'], 'components': score['components'],
                               'variance_ids': [x['id'] for x in group], 'missing_evidence': gaps})
            missing += gaps
        metrics = {'variance_count': len(variances), 'hypothesis_count': len(hypotheses),
                   'kinds': kinds, 'history': history}
        if isinstance(answer, dict) and answer.get('hypotheses'):
            metrics['model_hypothesis_count'] = len(answer['hypotheses'])
        return AgentResult(
            outputs={'rca_hypotheses': hypotheses,
                     'confidence': max(x['confidence'] for x in hypotheses),
                     'missing_evidence': sorted(set(missing))},
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(RCAAgent))
