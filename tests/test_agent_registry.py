"""Registry-level behaviour: what governs an agent run rather than what it does.

The per-agent tests in ``tests/agents`` call ``execute`` directly. These cover the
half the registry owns -- contract conformance, dependency ordering, capability
gating, run ownership, the evidence loop and the audit trail -- because that is
what an agent cannot be trusted to enforce on itself.
"""
import json
import pytest
from app.agents.base import AgentResult, BaseAgent, Clarification, EvidenceRef
from app.repository import Repository
from app.service import FactoryService
from app.services.agents import AgentRegistry
from app.services.evidence import EvidenceService
from app.services.factory_v3 import AuditLedger

# Agent counts are derived from the governed catalogue rather than hardcoded, so
# adding a contract does not silently turn these invariants into stale numbers.
def _catalogue_agent_ids():
    import json, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / 'config' / 'agent-catalogue.v1.json'
    return {a['agent_id'] for a in json.loads(path.read_text(encoding='utf-8'))['agents']}


CATALOGUE_AGENT_COUNT = len(_catalogue_agent_ids())


ROLES = ('REPOSITORY_INTAKE', 'ACE_ANALYST', 'CODE_GENERATOR', 'KNOWLEDGE_CURATOR', 'ORCHESTRATOR')


@pytest.fixture
def repo(tmp_path):
    return Repository(str(tmp_path / 'registry.db'))


@pytest.fixture
def registry(repo):
    return AgentRegistry(repo)


@pytest.fixture
def job(repo):
    return FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc', 'rollbackPointer': 'tag/v1'},
        'tester')


@pytest.fixture
def ace_repo(tmp_path):
    root = tmp_path / 'ace'
    root.mkdir()
    (root / 'Orders.msgflow').write_text('<flow><node id="in" type="HTTPInput" url="/orders"/></flow>')
    return root


def test_every_catalogue_contract_has_a_conformant_implementation(registry):
    report = registry.conformance()
    assert len(report) == CATALOGUE_AGENT_COUNT
    assert [x for x in report if x['status'] != 'CONFORMANT'] == []
    assert registry.implementations()['status'] == 'UP'


def test_an_implementation_requesting_a_tool_its_contract_withholds_is_refused(registry, job, monkeypatch):
    """Worker drift is refused for the same reason a changed contract hash is."""
    class Overreaching(BaseAgent):
        AGENT_ID = 'repository-intake'
        TOOLS = ('repository.read', 'repository.write')
        OUTPUTS = ('intake_manifest',)

    monkeypatch.setattr('app.services.agents.load_implementation', lambda _: Overreaching)
    finding, = [x for x in registry.conformance('repository-intake')]
    assert finding['status'] == 'DRIFT'
    assert finding['findings'] == ['tool_not_in_contract:repository.write']
    with pytest.raises(PermissionError, match='agent_implementation_drift'):
        registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {})


def test_an_output_outside_the_contract_is_refused(registry, job, monkeypatch):
    class Inventive(BaseAgent):
        AGENT_ID = 'repository-intake'
        TOOLS = ('repository.read',)
        OUTPUTS = ('intake_manifest',)

        def execute(self, context):
            return AgentResult(outputs={'intake_manifest': 'x', 'secret_backdoor': 'y'})

    monkeypatch.setattr('app.services.agents.load_implementation', lambda _: Inventive)
    with pytest.raises(PermissionError, match='agent_output_not_in_contract:secret_backdoor'):
        registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {})


def test_evidence_and_clarifications_require_the_contract_capability(registry, job, monkeypatch):
    """Returning them in a result must not be a way to acquire the capability."""
    class Curator(BaseAgent):
        AGENT_ID = 'knowledge-evidence'
        TOOLS = ('index.write',)
        OUTPUTS = ('cited_context',)

        def execute(self, context):
            return AgentResult(outputs={'cited_context': []},
                              evidence=(EvidenceRef('SMUGGLED', '1', 'x://1', 'content'),))

    monkeypatch.setattr('app.services.agents.load_implementation', lambda _: Curator)
    with pytest.raises(PermissionError, match='tool_not_allowed:evidence.write'):
        registry.dispatch(job['id'], 'knowledge-evidence', '1.0.0', ROLES, 'svc', {})

    class Questioner(Curator):
        def execute(self, context):
            return AgentResult(outputs={'cited_context': []},
                              clarifications=(Clarification('S', 'q?', 'ACE_SME', 'blocks'),))

    monkeypatch.setattr('app.services.agents.load_implementation', lambda _: Questioner)
    with pytest.raises(PermissionError, match='tool_not_allowed:clarification.create'):
        registry.dispatch(job['id'], 'knowledge-evidence', '1.0.0', ROLES, 'svc', {})


def test_dispatch_records_the_run_the_evidence_and_the_audit_entry(registry, repo, job, ace_repo):
    outcome = registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES,
                                'intake@example', {'path': str(ace_repo)})
    run = repo.one('select * from agent_runs where id=?', (outcome['run_id'],))
    assert run['status'] == 'COMPLETED'
    assert run['agent_id'] == 'repository-intake'
    assert run['operator_identity'] == 'intake@example'
    assert json.loads(run['requested_tools']) == ['evidence.write', 'repository.read']
    assert json.loads(run['outputs'])['intake_manifest'] == outcome['outputs']['intake_manifest']
    # The evidence the agent produced is registered and attributed to the agent.
    registered, = outcome['evidence']
    row = repo.one('select * from evidence where id=?', (registered['id'],))
    assert row['producer'] == 'repository-intake'
    assert json.loads(row['metadata'])['run_id'] == outcome['run_id']
    assert json.loads(run['output_evidence']) == [registered['id']]
    event = repo.one("select * from workflow_events where job_id=? and event_type='AGENT_RUN_COMPLETED'",
                     (job['id'],))
    assert event['correlation_id'] == outcome['correlation_id']
    entry = repo.one("select * from audit_ledger where action='AGENT_RUN_COMPLETED'")
    assert json.loads(entry['payload_json'])['params_hash']
    assert AuditLedger(repo).verify()


def test_the_registered_evidence_hash_equals_the_artefact_hash(registry, repo, job, ace_repo):
    """This equality is what makes one approval cover both."""
    outcome = registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {'path': str(ace_repo)})
    manifest = repo.one('select * from intake_manifests where id=?',
                        (outcome['outputs']['intake_manifest'],))
    assert outcome['evidence'][0]['content_hash'] == manifest['content_hash']


def test_a_missing_required_role_is_refused(registry, job, ace_repo):
    with pytest.raises(PermissionError, match='required_agent_role_missing'):
        registry.dispatch(job['id'], 'repository-intake', '1.0.0', ('ACE_ANALYST',), 'svc',
                          {'path': str(ace_repo)})


def test_dependencies_are_enforced_rather_than_only_drawn(registry, repo, job, ace_repo):
    """The graph published these edges and nothing read them."""
    status = registry.dependency_status(job['id'], 'ace-analysis')
    assert status == {'agent_id': 'ace-analysis', 'required': ['repository-intake'],
                      'satisfied': [], 'outstanding': ['repository-intake']}
    with pytest.raises(PermissionError, match='agent_dependency_not_satisfied:repository-intake'):
        registry.dispatch(job['id'], 'ace-analysis', '1.0.0', ROLES, 'svc', {'path': str(ace_repo)})
    registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {'path': str(ace_repo)})
    assert registry.dependency_status(job['id'], 'ace-analysis')['outstanding'] == []
    assert registry.dispatch(job['id'], 'ace-analysis', '1.0.0', ROLES, 'svc',
                             {'path': str(ace_repo)})['status'] in ('COMPLETED', 'CLARIFICATION_REQUIRED')


def test_a_dependency_completed_for_another_job_does_not_count(registry, repo, job, ace_repo):
    other = FactoryService(repo).create_job(
        {'service': 'Other', 'owner': 'Retail', 'sourceRevision': 'z'}, 'tester')
    registry.dispatch(other['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {'path': str(ace_repo)})
    with pytest.raises(PermissionError, match='agent_dependency_not_satisfied'):
        registry.dispatch(job['id'], 'ace-analysis', '1.0.0', ROLES, 'svc', {'path': str(ace_repo)})


def test_unregistered_input_evidence_is_refused(registry, repo, job, ace_repo):
    """A run reading evidence the factory never registered cannot be traced to it."""
    with pytest.raises(ValueError, match='input_evidence_not_registered:ghost'):
        registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc',
                          {'path': str(ace_repo)}, input_evidence=['ghost'])
    real = EvidenceService(repo).register(job['id'], 'SEED', '1', 'mem://1', 'content', 'tester')
    outcome = registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc',
                                {'path': str(ace_repo)}, input_evidence=[real['id']])
    assert json.loads(repo.one('select input_evidence from agent_runs where id=?',
                               (outcome['run_id'],))['input_evidence']) == [real['id']]


def test_evidence_registered_against_another_job_is_refused(registry, repo, job, ace_repo):
    other = FactoryService(repo).create_job(
        {'service': 'Other', 'owner': 'Retail', 'sourceRevision': 'z'}, 'tester')
    theirs = EvidenceService(repo).register(other['id'], 'SEED', '1', 'mem://1', 'c', 'tester')
    with pytest.raises(ValueError, match='input_evidence_not_registered'):
        registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc',
                          {'path': str(ace_repo)}, input_evidence=[theirs['id']])


def test_a_run_may_only_be_completed_by_the_principal_that_started_it(registry, job):
    run = registry.start(job['id'], 'repository-intake', '1.0.0', [], ROLES,
                         ['repository.read'], 'alice@example')
    with pytest.raises(PermissionError, match='agent_run_not_owned'):
        registry.complete(run['id'], [], identity='bob@example')
    assert registry.complete(run['id'], [], identity='alice@example')['status'] == 'COMPLETED'


def test_a_run_cannot_be_completed_twice_or_before_it_exists(registry, job):
    run = registry.start(job['id'], 'repository-intake', '1.0.0', [], ROLES,
                         ['repository.read'], 'alice@example')
    registry.complete(run['id'], [], identity='alice@example')
    with pytest.raises(ValueError, match='agent_run_not_running:COMPLETED'):
        registry.complete(run['id'], [], identity='alice@example')
    with pytest.raises(ValueError, match='agent_run_not_found'):
        registry.complete('no-such-run', [])


def test_a_failing_agent_is_recorded_as_failed_and_not_swallowed(registry, repo, job):
    with pytest.raises(ValueError, match='missing_param:path'):
        registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {})
    run = repo.one('select * from agent_runs where job_id=?', (job['id'],))
    assert run['status'] == 'FAILED'
    assert 'missing_param:path' in run['error']
    assert repo.one("select id from workflow_events where job_id=? and event_type='AGENT_RUN_FAILED'",
                    (job['id'],))


def test_an_unapproved_or_unknown_agent_cannot_be_dispatched(registry, repo, job):
    with pytest.raises(ValueError, match='agent_not_registered_or_approved'):
        registry.dispatch(job['id'], 'no-such-agent', '1.0.0', ROLES, 'svc', {})
    with repo.connect() as c:
        c.execute("update agent_registry set enabled=0 where agent_id='repository-intake'")
    with pytest.raises(ValueError, match='agent_not_registered_or_approved'):
        registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {})


def test_reasoning_provenance_is_snapshotted_onto_the_run(registry, repo, job, ace_repo):
    registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {'path': str(ace_repo)})
    outcome = registry.dispatch(job['id'], 'dormancy-intelligence', '1.0.0',
                                ROLES + ('DORMANCY_ANALYST',), 'svc', {'traffic_count': 3, 'deployed': True})
    run = repo.one('select * from agent_runs where id=?', (outcome['run_id'],))
    provenance = json.loads(run['model_provenance'])
    assert run['reasoning'] == 'DETERMINISTIC_FALLBACK'
    assert provenance['prompt_name'] == 'agent/dormancy-intelligence/classification-rationale'
    assert provenance['reason'] == 'no_approved_prompt_or_model'


def test_a_dlp_refusal_is_recorded_and_the_agent_still_completes(registry, repo, job, ace_repo):
    """The data-loss rules refuse a lot of legitimate migration content, so a
    refusal must not take the run down with it."""
    from app.services.connectors import ConnectorRegistry
    from app.services.platform_v7 import AIRegistryService

    class Approver:
        identity = 'board@example'
        roles = ('AI_GOVERNANCE_ADMIN',)

    ConnectorRegistry(repo).upsert({'category': 'model-gateway', 'name': 'model-primary',
                                    'provider': 'generic-http', 'secret_reference': 'vault://m',
                                    'config': {'base_url': 'https://m.example.invalid',
                                               'region': 'approved-region'}})
    ai = AIRegistryService(repo)
    # Repository seeds prompt_registry and model_registry APPROVED on init, so
    # both of these may already exist and both tables are unique on
    # (name, version). Register only what is missing -- the point of this test
    # is the DLP rule, not who wrote the registry row.
    def ensure_prompt(name):
        if not repo.one('select 1 from prompt_registry where name=? and version=?', (name, '1')):
            ai.register_prompt({'name': name, 'version': '1', 'template': 't',
                                'status': 'APPROVED'}, Approver())

    def ensure_model(name):
        if not repo.one('select 1 from model_registry where name=?', (name,)):
            ai.register_model({'name': name, 'version': '1',
                               'provider_profile': 'model-primary', 'status': 'APPROVED',
                               'region': 'approved-region'}, Approver())

    ensure_prompt('agent/dormancy-intelligence/classification-rationale')
    # Registered under the alias the agent actually resolves to. Registering any
    # other name populates model_registry, which arms the AT-13 fail-closed
    # guard in dispatch for every agent, and the run is then refused with
    # default_model_alias_not_in_approved_model_registry before the gateway --
    # and therefore the DLP rule this test is about -- is ever reached.
    ensure_model('reasoning-small')
    registry.dispatch(job['id'], 'repository-intake', '1.0.0', ROLES, 'svc', {'path': str(ace_repo)})
    # A 16-digit telemetry counter trips the PAN rule.
    outcome = registry.dispatch(job['id'], 'dormancy-intelligence', '1.0.0',
                                ROLES + ('DORMANCY_ANALYST',), 'svc',
                                {'traffic_count': 1234567890123456, 'deployed': True})
    assert outcome['status'] == 'COMPLETED'
    assert outcome['outputs']['classification'] == 'ACTIVE'
    assert outcome['reasoning']['reason'].startswith('gateway_refused:dlp_violation')
    assert outcome['reasoning']['mode'] == 'DETERMINISTIC_FALLBACK'


def test_the_governed_registry_survives_reinitialisation(tmp_path):
    """The agent_runs upgrade must be idempotent like the rest of the schema."""
    path = str(tmp_path / 'twice.db')
    Repository(path)
    Repository(path)
    assert Repository(path).one('select count(*) n from agent_registry')['n'] == CATALOGUE_AGENT_COUNT
