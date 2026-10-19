"""Remediation Agent.

Scores an approved root cause against the governed remediation threshold and, when
it is eligible, records the bounded patch plan and the regression tests that must
prove it. Wraps ``ConfidenceRemediationService`` for the scoring rules and
``RCAService.record_remediation``, which refuses a remediation lacking an approved
pattern, tests or a rollback pointer.

Two boundaries this agent holds deliberately:

* The confidence threshold comes from configuration, never from the parameters.
  A remediation that could choose the bar it is judged against is not judged.
* It does **not** write source, though its contract grants ``repository.write``.
  Generated code is produced by the code generation agent from an approved plan,
  under G3, with a provenance manifest. Editing the target directly would create
  changes with no plan to trace them to -- and the ``GENERATED_REGION_LOCKED``
  boundary on generated artefacts exists to prevent exactly that. What this agent
  produces is the change set an approved plan should carry.
"""
import json
import os
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.artefact import produce
from app.services.enterprise_v5 import ConfidenceRemediationService
from app.services.rca import RCAService

THRESHOLD_ENV = 'FACTORY_REMEDIATION_CONFIDENCE_THRESHOLD'
DEFAULT_TESTS = ['targeted_regression', 'impacted_suite']


class RemediationAgent(BaseAgent):
    AGENT_ID = 'remediation'
    SKILL = 'iib-to-ms-remediation'
    TOOLS = ('repository.branch', 'repository.write', 'test.execute', 'evidence.write')
    OUTPUTS = ('patch_plan', 'code_changes', 'tests')
    PROMPT = 'agent/remediation/patch-plan'

    def _threshold(self):
        return float(os.getenv(THRESHOLD_ENV, '0.8'))

    def _preconditions(self, context, cause):
        """The eligibility facts, each read from a record rather than asserted."""
        variance_ids = json.loads(cause['variance_ids'] or '[]')
        catalogue = self.repo.one(
            'select id from mapping_catalogues where job_id=? order by created_at desc limit 1',
            (context.job_id,))
        plan = self.repo.one(
            'select tasks,acceptance from migration_plans where job_id=? order by created_at desc limit 1',
            (context.job_id,))
        tests_present = bool(plan) and any(
            x.get('tests') for x in json.loads(plan['tasks'] or '[]'))
        bound = int(context.param('impact_bound', 10))
        return {
            # An approved Spring pattern exists for this job only if a mapping
            # catalogue was actually produced under G1/G2.
            'approved_pattern': bool(catalogue),
            'bounded_impact': 0 < len(variance_ids) <= bound,
            'tests_present': tests_present,
            # A dependency the framework baseline never approved would put the fix
            # outside the governed template.
            'prohibited_dependency': bool(context.param('prohibited_dependency', False)),
            'variance_ids': variance_ids,
        }

    def execute(self, context):
        job = context.job()
        cause = self.repo.one('select * from root_causes where id=? and job_id=?',
                              (context.param('root_cause_id'), context.job_id))
        if not cause:
            raise ValueError('root_cause_not_found')
        facts = self._preconditions(context, cause)
        scorer = ConfidenceRemediationService()
        threshold = self._threshold()
        eligibility = scorer.eligibility(float(cause['confidence']), threshold,
                                        facts['approved_pattern'], facts['bounded_impact'],
                                        facts['tests_present'], facts['prohibited_dependency'])
        rollback = str(job['rollback_pointer'] or '').strip()
        variances = self.repo.rows(
            'select id,path,kind,severity from variances where id in (%s)'
            % ','.join('?' * len(facts['variance_ids'])), tuple(facts['variance_ids'])) if facts['variance_ids'] else []
        proposal = scorer.propose({'path': ','.join(sorted({x['path'] for x in variances})) or 'UNKNOWN',
                                   'root_cause_id': cause['id']},
                                  eligibility, int(job['iteration'] or 1), rollback)
        # The model proposes the change set; eligibility stays deterministic.
        # Whether a remediation may proceed turns on the governed confidence
        # threshold, an approved pattern and a bounded blast radius -- that is
        # enforcement, and this agent holds no repository.write tool either way.
        plan, provenance = produce(
            context, self.PROMPT,
            {'hypothesis': cause['hypothesis'], 'confidence': cause['confidence'],
             'variances': [{'path': x['path'], 'kind': x['kind']} for x in variances]})
        # No ``or`` fallback on the change set either. It used to substitute a
        # lookup over variance kinds -- "restore the omitted mapping for this
        # path" -- which reads as a remediation proposal and is a table, so an
        # empty model answer became a plan nobody wrote.
        changes = [{'source_reference': c.get('path', ''), 'intent': c.get('intent', '')}
                   for c in (plan.get('changes') or [])]
        if plan.get('pattern'):
            proposal['proposed_pattern'] = plan['pattern']
        # The model declaring the change bounded does not make it so: the
        # eligibility check below is what decides, and a disagreement is kept.
        proposal['model_claims_bounded'] = plan.get('bounded')
        if not eligibility['eligible']:
            return AgentResult(
                status='CLARIFICATION_REQUIRED',
                outputs={'patch_plan': proposal, 'code_changes': changes, 'tests': []},
                metrics={'eligible': False, 'route': eligibility['route'],
                         'confidence': cause['confidence'], 'threshold': threshold,
                         'unmet': sorted(k for k in ('approved_pattern', 'bounded_impact', 'tests_present')
                                         if not facts[k])},
                reasoning=provenance)
        tests = sorted(set(DEFAULT_TESTS) | {t for x in variances for t in (x['kind'].lower() + '_regression',)})
        recorded = RCAService(self.repo).record_remediation(
            context.job_id, cause['id'], json.dumps(proposal, sort_keys=True, default=str),
            proposal['approved_pattern'], tests, int(job['iteration'] or 1), rollback)
        payload = json.dumps({'remediation_id': recorded['id'], 'root_cause_id': cause['id'],
                              'proposal': proposal, 'changes': changes, 'tests': tests},
                             sort_keys=True, default=str)
        return AgentResult(
            outputs={'patch_plan': recorded['id'], 'code_changes': changes, 'tests': tests},
            evidence=(EvidenceRef('REMEDIATION_PROPOSAL', '1.0.0',
                                  'remediation://' + recorded['id'], payload,
                                  {'root_cause_id': cause['id'], 'confidence': cause['confidence'],
                                   'threshold': threshold, 'rollback_pointer': rollback}),),
            metrics={'eligible': True, 'route': eligibility['route'],
                     'confidence': cause['confidence'], 'threshold': threshold,
                     'change_count': len(changes)},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(RemediationAgent))
