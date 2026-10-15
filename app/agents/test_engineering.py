"""Test Engineering Agent.

Derives the differential test cases from the analysed ACE interfaces, prepares
the governed differential package, and -- when runtime adapters are configured --
replays each case against both the ACE runtime and the generated Spring service,
recording the raw pair of results.

Replay is what makes a differential package meaningful, and it is the step that
needs an external runtime. Adapters are therefore supplied rather than
constructed from ambient configuration: pass ``ace_adapter``/``spring_adapter``
objects, or ``ace_base_url``/``spring_base_url`` to build the shipped HTTP
adapters. With neither, the package is prepared and no execution is claimed --
``prepare_differential`` marks it READY_FOR_EXECUTION and the
``evidence_bundle`` precondition on VARIANCE_REVIEW will refuse the transition
until the cases actually run. Reporting an unexecuted package as tested is
precisely the failure that precondition exists to catch.

The raw results are stored as a RUNTIME_EXECUTION validation report because the
comparison is the variance analysis agent's job, under ``comparison.execute``.
An evidence row records only a hash and a URI, so the payloads have to live
somewhere the next agent can actually read them.
"""
import json
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.repository import digest, now, uid
from app.services.enterprise_v5 import ACERuntimeAdapter, SpringBootRuntimeAdapter
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

HTTP = ('HTTP', 'SOAP')

#: Adapter envelope fields, ignored by default in the comparison policy.
#:
#: These can never be equal between two runtimes: ``adapter`` is the adapter's own
#: name, and latency and correlation id differ by construction on every call.
#: Comparing them would report a variance for every case in every package, which
#: would bury the behavioural differences the comparison exists to find. The merge
#: is recorded in the package's policy, so what was ignored stays auditable, and a
#: caller can still add its own masks and tolerances on top.
TRANSPORT_FIELDS = ('$.adapter', '$.correlation_id', '$.latency_ms')


class TestEngineeringAgent(BaseAgent):
    AGENT_ID = 'test-engineering'
    SKILL = 'iib-to-ms-test-engineering'
    TOOLS = ('test.execute', 'runtime.invoke', 'evidence.write')
    OUTPUTS = ('test_suites', 'execution_evidence')
    PROMPT = 'agent/test-engineering/case-coverage-review'

    def _interfaces(self, job_id):
        """Interface shapes from the latest analysis, for the coverage review."""
        analysis = self.repo.one(
            'select * from ace_analyses where job_id=? order by created_at desc limit 1', (job_id,))
        if not analysis:
            return []
        return [{'node': x.get('node', ''), 'protocol': x.get('protocol', ''),
                 'endpoint': x.get('endpoint', '')}
                for x in json.loads(analysis['semantic_model']).get('interfaces', [])]

    def _cases(self, context):
        """Supplied cases, or one per analysed interface."""
        supplied = context.param('cases', None)
        if supplied:
            return supplied
        analysis = self.repo.one(
            'select * from ace_analyses where job_id=? order by created_at desc limit 1',
            (context.job_id,))
        if not analysis:
            raise ValueError('analysis_required_to_derive_test_cases')
        interfaces = json.loads(analysis['semantic_model']).get('interfaces', [])
        cases = []
        for index, x in enumerate(interfaces, 1):
            endpoint = x.get('endpoint') or ''
            cases.append({'id': 'CASE-%03d' % index,
                          'input': {'interface': x.get('node', ''), 'protocol': x.get('protocol', '')},
                          'method': 'POST' if x.get('protocol') in HTTP else 'GET',
                          'path': endpoint if endpoint.startswith('/') else '/',
                          'headers': {}, 'timing': {}, 'side_effect_observations': [],
                          'secure_references': []})
        if not cases:
            raise ValueError('no_interface_to_derive_test_cases')
        return cases

    def _adapters(self, context):
        ace = context.param('ace_adapter', None)
        spring = context.param('spring_adapter', None)
        if ace is None and context.param('ace_base_url', ''):
            ace = ACERuntimeAdapter('ace', context.param('ace_base_url'))
        if spring is None and context.param('spring_base_url', ''):
            spring = SpringBootRuntimeAdapter('spring-boot', context.param('spring_base_url'))
        return ace, spring

    def execute(self, context):
        service = MappingAndPlanningService(self.repo)
        plan_id = context.param('plan_id')
        cases = self._cases(context)
        policy = dict(context.param('policy', {}) or {})
        policy['ignore'] = sorted(set(policy.get('ignore') or []) | set(TRANSPORT_FIELDS))
        package = service.prepare_differential(context.job_id, plan_id, cases, policy)
        ace, spring = self._adapters(context)
        executions = []
        if ace is not None and spring is not None:
            for case in cases:
                invocation = {'path': case.get('path', '/'), 'method': case.get('method', 'POST'),
                              'headers': case.get('headers', {}), 'body': case.get('input')}
                executions.append({'case_id': case['id'],
                                   'ace_result': ace.invoke(dict(invocation)),
                                   'spring_result': spring.invoke(dict(invocation))})
            payload = json.dumps({'package_id': package['package_id'], 'plan_id': plan_id,
                                  'policy': policy, 'executions': executions}, sort_keys=True, default=str)
            with self.repo.connect() as c:
                self.repo.insert(c, 'validation_reports',
                                 {'id': uid(), 'job_id': context.job_id,
                                  'report_type': 'RUNTIME_EXECUTION', 'version': '1.0.0',
                                  'payload': payload, 'content_hash': digest(payload),
                                  'status': 'EXECUTED', 'created_at': now(),
                                  'reference_id': package['package_id']})
        else:
            payload = json.dumps({'package_id': package['package_id'], 'plan_id': plan_id,
                                  'policy': policy, 'executions': []}, sort_keys=True)
        # Case shapes only -- never ``case['input']`` or a replayed body, which
        # carry the caller's own request data straight into the payload.
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'interfaces': self._interfaces(context.job_id),
            'cases': [{'id': x.get('id', ''), 'method': x.get('method', ''),
                       'path': x.get('path', '')} for x in cases],
            'replayed': bool(executions)})
        return AgentResult(
            outputs={'test_suites': package['package_id'],
                     'execution_evidence': [x['case_id'] for x in executions]},
            evidence=(EvidenceRef('RUNTIME_EXECUTION', '1.0.0',
                                  'differential://' + package['package_id'], payload,
                                  {'package_id': package['package_id'],
                                   'case_count': package['case_count'],
                                   'executed': len(executions),
                                   'runtimes': ['ACE', 'SPRING_BOOT'] if executions else []}),),
            metrics={'case_count': package['case_count'], 'executed_count': len(executions),
                     'replay': 'EXECUTED' if executions else 'NOT_EXECUTED',
                     'model_weak_coverage': (answer or {}).get('weak_coverage', [])
                     if isinstance(answer, dict) else []},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(TestEngineeringAgent))
