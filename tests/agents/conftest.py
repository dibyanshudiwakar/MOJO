"""Fixtures shared by the standalone per-agent tests.

Each agent is tested on its own: a fresh database, only the artefacts that agent
actually needs, and a direct ``execute`` call. The registry-level behaviour
(dependency ordering, contract conformance, run ownership, the evidence loop) is
covered separately in ``tests/test_agent_registry.py``.
"""
import json
import pytest
from app.repository import Repository, digest, now, uid
from app.service import FactoryService
from app.services.connectors import ConnectorRegistry
from app.services.evidence import EvidenceService
from app.services.platform_v7 import AIRegistryService

#: Every agent's required_role, plus the gate roles. An autonomous orchestration
#: acts as one principal throughout, so it needs the roles of what it invokes.
ALL_ROLES = ('ORCHESTRATOR', 'REPOSITORY_INTAKE', 'DORMANCY_ANALYST', 'FRAMEWORK_ANALYST',
             'ACE_ANALYST', 'SPECIFICATION_AUTHOR', 'MIGRATION_PLANNER', 'CODE_GENERATOR',
             'TEST_ENGINEER', 'VARIANCE_ANALYST', 'RCA_ANALYST', 'REMEDIATION_ENGINEER',
             'GOVERNANCE_ENGINE', 'KNOWLEDGE_CURATOR')

GATE_ROLES = {'G0': 'SERVICE_OWNER', 'G1': 'JAVA_ARCHITECT', 'G2': 'ACE_SME',
              'G3': 'PRODUCT_OWNER', 'G4': 'QA_LEAD', 'G5': 'RELEASE_MANAGER'}


class RecordingProvider:
    """A deterministic stand-in for a model provider.

    The factory ships no provider execution: an external model call is an
    enterprise adapter boundary. Tests supply this so the gateway path can be
    exercised end to end -- request recorded, response recorded, provenance
    carrying the approved prompt hash -- without a network call.
    """

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def invoke(self, template, payload):
        self.calls.append({'template': template, 'payload': payload})
        return self.answers.get('default', {'note': 'deterministic test answer'})


@pytest.fixture
def repo(tmp_path):
    return Repository(str(tmp_path / 'agents.db'))


@pytest.fixture
def factory(repo):
    return FactoryService(repo)


@pytest.fixture
def evidence(repo):
    return EvidenceService(repo)


@pytest.fixture
def job(factory):
    return factory.create_job({'service': 'Orders', 'owner': 'Retail',
                               'sourceRevision': 'abc123', 'rollbackPointer': 'tag/v1'}, 'tester')


@pytest.fixture
def ace_repo(tmp_path):
    """An ACE source tree whose every node has an approved Spring mapping."""
    root = tmp_path / 'ace'
    root.mkdir()
    (root / 'Orders.msgflow').write_text(
        '<flow><node id="in" type="HTTPInput" url="/orders"/>'
        '<node id="map" type="ComputeNode"/>'
        '<node id="out" type="HTTPReply"/>'
        '<connection source="in" target="map"/>'
        '<connection source="map" target="out"/></flow>')
    (root / 'Orders.esql').write_text('CREATE COMPUTE MODULE OrdersMain; DECLARE C SHARED CHARACTER;')
    (root / 'Order.xsd').write_text('<schema><element name="Order"/></schema>')
    return root


@pytest.fixture
def framework_repo(tmp_path):
    """A reference Spring project with no unresolved framework conflicts."""
    root = tmp_path / 'framework'
    src = root / 'src' / 'main' / 'java'
    src.mkdir(parents=True)
    (root / 'pom.xml').write_text(
        '<project><parent><artifactId>spring-boot-starter-parent</artifactId>'
        '<version>3.3.0</version></parent><properties><java.version>21</java.version></properties>'
        '<dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency>'
        '</dependencies></project>')
    (root / 'A.java').write_text(
        '@RestController class A { SecurityFilterChain chain; MeterRegistry metrics; '
        'LoggerFactory log; @ControllerAdvice Object advice; }')
    (src / 'B.java').write_text('class B {}')
    return root


@pytest.fixture
def provider():
    return RecordingProvider()


@pytest.fixture
def gateway(repo):
    """Approve a prompt, a model and a gateway profile so reasoning can route.

    Returns a callable taking the prompt name an agent declares, because a
    request is only permitted against an APPROVED prompt of that exact name.
    """
    ConnectorRegistry(repo).upsert({
        'category': 'model-gateway', 'name': 'model-primary', 'provider': 'generic-http',
        'secret_reference': 'vault://models/primary',
        'config': {'base_url': 'https://models.example.invalid', 'region': 'approved-region'}})
    ai = AIRegistryService(repo)

    class _Approver:
        identity = 'board@example'
        roles = ('AI_GOVERNANCE_ADMIN',)

    def approve(prompt_name, template='Cite only the supplied evidence.'):
        prompt = ai.register_prompt({'name': prompt_name, 'version': '1', 'template': template,
                                     'status': 'APPROVED'}, _Approver())
        model = ai.register_model({'name': 'approved-model', 'version': '1',
                                   'provider_profile': 'model-primary', 'status': 'APPROVED',
                                   'region': 'approved-region'}, _Approver())
        return prompt, model
    return approve


@pytest.fixture
def approve(repo, factory, evidence):
    """Record a gate approval bound to a specific artefact hash.

    When ``content_hash`` is supplied it must be the artefact's own hash, which is
    how an agent's registered evidence and the artefact it describes end up under
    one approval.
    """
    def _approve(job_id, gate, decision, evidence_id=None, content_hash=None, identity=None):
        role = GATE_ROLES[gate]
        identity = identity or (role.lower() + '@example')
        if evidence_id is None:
            registered = evidence.register(job_id, gate, '1', 'mem://' + gate,
                                           gate + decision + identity, 'tester')
            evidence_id, content_hash = registered['id'], registered['content_hash']
        return factory.approve({'job_id': job_id, 'gate': gate, 'decision': decision,
                                'approver_role': role, 'approver_identity': identity,
                                'evidence_ids': [evidence_id],
                                'artifact_bindings': {evidence_id: content_hash}},
                               (role,), identity)
    return _approve


@pytest.fixture
def state(repo):
    """Force a job's state, for agents that only run late in the lifecycle."""
    def _state(job_id, value):
        with repo.connect() as c:
            c.execute('update jobs set state=? where id=?', (value, job_id))
    return _state


@pytest.fixture
def dispatch(repo):
    """Run an agent through the registry, as a broadly-privileged service principal."""
    from app.services.agents import AgentRegistry
    registry = AgentRegistry(repo)

    def _dispatch(job_id, agent_id, params=None, roles=ALL_ROLES, identity='svc@example', **kwargs):
        return registry.dispatch(job_id, agent_id, '1.0.0', roles, identity, params or {}, **kwargs)
    return _dispatch


@pytest.fixture
def approved_inputs(repo, job, ace_repo, framework_repo, dispatch, approve):
    """Framework, analysis and specification, each under its own bound approval.

    Dispatched through the registry rather than called directly, because it is the
    registry that registers each artefact as evidence -- and the approvals below
    bind those evidence ids to the artefact hashes the planning agent's service
    then re-checks.
    """
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    framework = dispatch(job['id'], 'framework-baseline', {'path': str(framework_repo)})
    analysis = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    specification = dispatch(job['id'], 'migration-specification',
                             {'analysis_id': analysis['outputs']['analysis_document']})
    for gate, outcome in (('G1', framework), ('G2', analysis), ('G2', specification)):
        ref = outcome['evidence'][0]
        approve(job['id'], gate, 'APPROVE', ref['id'], ref['content_hash'])
    return {'framework_id': framework['outputs']['framework_template'],
            'analysis_id': analysis['outputs']['analysis_document'],
            'specification_id': specification['outputs']['migration_specification_yaml']}


@pytest.fixture
def planned(repo, job, dispatch, approve, evidence, approved_inputs):
    """An approved migration plan, with G3 bound to its task-list hash.

    The planning agent holds ``plan.write`` but not ``evidence.write``, so unlike
    the framework and analysis artefacts the plan is registered as evidence by the
    approver rather than by the agent -- which is the act of attaching the plan to
    the G3 decision.
    """
    outcome = dispatch(job['id'], 'planning', approved_inputs)
    plan = repo.one('select * from migration_plans where id=?', (outcome['outputs']['plan'],))
    ref = evidence.register(job['id'], 'MIGRATION_PLAN', plan['version'],
                            'plan://' + plan['id'], plan['tasks'], 'product-owner@example')
    assert ref['content_hash'] == plan['content_hash']
    approve(job['id'], 'G3', 'APPROVE', ref['id'], ref['content_hash'])
    return {'plan_id': plan['id'], 'catalogue_id': outcome['outputs']['mapping'], **approved_inputs}


@pytest.fixture
def executed_package(repo):
    """A differential package plus the runtime execution report for one case."""
    def _seed(job_id, plan_id='plan-1', case_id='CASE-001',
              ace_result=None, spring_result=None, policy=None):
        ace_result = ace_result if ace_result is not None else {'status': 200, 'body': {'id': 1}}
        spring_result = spring_result if spring_result is not None else {'status': 200, 'body': {'id': 2}}
        package_id = uid()
        payload = json.dumps({'plan_id': plan_id, 'cases': [{'id': case_id}]}, sort_keys=True)
        report = json.dumps({'package_id': package_id, 'plan_id': plan_id, 'policy': policy or {},
                             'executions': [{'case_id': case_id, 'ace_result': ace_result,
                                             'spring_result': spring_result}]}, sort_keys=True)
        with repo.connect() as c:
            repo.insert(c, 'differential_packages',
                        {'id': package_id, 'job_id': job_id, 'plan_id': plan_id,
                         'status': 'READY_FOR_EXECUTION', 'payload': payload,
                         'content_hash': digest(payload), 'created_at': now()})
            repo.insert(c, 'validation_reports',
                        {'id': uid(), 'job_id': job_id, 'report_type': 'RUNTIME_EXECUTION',
                         'version': '1.0.0', 'payload': report, 'content_hash': digest(report),
                         'status': 'EXECUTED', 'created_at': now(), 'reference_id': package_id})
        return package_id
    return _seed
