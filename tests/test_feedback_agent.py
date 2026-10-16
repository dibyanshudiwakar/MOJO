"""The feedback agent: repairs the generated code and proves it with the gate.

What matters here is that a claimed fix is backed by the quality gate's own
verdict after the edit. Each test therefore asserts the gate result, not just
that bytes changed on disk.
"""
import json
import pytest

from app.repository import Repository, uid, now
from app.service import FactoryService
from app.services.agents import AgentRegistry
from app.services.feedback import CodeFeedbackService
from app.services.migration_lifecycle_v6 import MappingAndPlanningService


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setenv('FACTORY_DB', str(tmp_path / 'fb.db'))
    repo = Repository(str(tmp_path / 'fb.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    plan_id = uid()
    with repo.connect() as c:
        c.execute('insert into migration_plans(id,job_id,version,tasks,mappings,risks,acceptance,'
                  'content_hash,created_at,analysis_id,framework_id,specification_id,status) '
                  'values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (plan_id, job['id'], '1.0.0', '[]', '[]', '[]', '[]', 'h', now(),
                   'an', 'fw', 'sp', 'DRAFT'))
    return repo, job, plan_id, tmp_path


def _project(root, yaml_text=None, java_text=None):
    proj = root / 'springboot-output'
    (proj / 'src/main/resources').mkdir(parents=True, exist_ok=True)
    (proj / 'src/main/java').mkdir(parents=True, exist_ok=True)
    (proj / 'pom.xml').write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"/>')
    (proj / 'src/main/resources/application.yml').write_text(
        yaml_text if yaml_text is not None else 'spring:\n  name: svc\n')
    (proj / 'src/main/java/Svc.java').write_text(
        java_text if java_text is not None else 'class Svc{}')
    return proj


def test_embedded_secret_and_endpoint_are_repaired_and_the_gate_agrees(env):
    repo, job, plan_id, tmp = env
    proj = _project(
        tmp,
        yaml_text='db:\n  password: "hunter2"\nbackend:\n  url: https://orders.prod.internal/api\n',
        java_text='class Svc{ String token = "abc123"; }')
    quality = MappingAndPlanningService(repo)
    before = quality.quality_validate(job['id'], plan_id, str(proj))
    failed = {c['name'] for c in before['checks'] if c['status'] == 'FAIL'}
    assert {'SECRET_SCAN', 'HARDCODED_ENDPOINT_SCAN'} <= failed

    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=3)

    # The gate, re-run over the edited files, is the evidence.
    assert out['unfixed_checks'] == []
    assert out['quality_status'] != 'FAIL'
    assert {f['check'] for f in out['applied_fixes']} >= {'SECRET_SCAN', 'HARDCODED_ENDPOINT_SCAN'}
    text = (proj / 'src/main/resources/application.yml').read_text()
    assert '${PASSWORD}' in text and 'hunter2' not in text
    assert '${EXTERNAL_ENDPOINT}' in text


def test_only_the_scanned_file_types_are_touched(env):
    """pom.xml carries Maven namespace URLs and must not be rewritten.

    quality_validate reads only .java/.yml/.yaml/.properties, so the fixer is
    scoped to the same set -- otherwise it would mangle the POM to satisfy a
    check that never looked at it.
    """
    repo, job, plan_id, tmp = env
    proj = _project(tmp, yaml_text='api: https://x.prod.internal/v1\n')
    CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=2)
    assert 'http://maven.apache.org/POM/4.0.0' in (proj / 'pom.xml').read_text()


def test_permitted_localhost_urls_are_left_alone(env):
    """The gate allows localhost and example hosts, so the fixer must too."""
    repo, job, plan_id, tmp = env
    proj = _project(tmp, java_text='class Svc{ String dev="http://localhost:8080"; }')
    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=2)
    assert 'http://localhost:8080' in (proj / 'src/main/java/Svc.java').read_text()
    assert out['unfixed_checks'] == []


def test_missing_provenance_manifest_is_reconstructed(env):
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    assert not (proj / 'provenance-manifest.json').exists()
    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=2)
    assert (proj / 'provenance-manifest.json').exists()
    manifest = json.loads((proj / 'provenance-manifest.json').read_text())
    assert manifest['plan_id'] == plan_id
    assert out['unfixed_checks'] == []


def test_loop_is_bounded_and_stops_when_nothing_can_be_fixed(env):
    """An unfixable failure must not spin the budget.

    PROJECT_STRUCTURE fails with no pom.xml or build.gradle, and nothing here
    synthesises a build file. The loop has to give up and report it rather than
    run every pass producing no fixes.
    """
    repo, job, plan_id, tmp = env
    proj = tmp / 'springboot-output'
    (proj / 'src').mkdir(parents=True)
    (proj / 'src/App.java').write_text('class App{}')

    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=5)

    assert 'PROJECT_STRUCTURE' in out['unfixed_checks']
    assert len(out['passes']) <= 3, 'should stop early, not exhaust the budget'


def test_behavioural_variance_is_reported_not_claimed_as_fixed(env):
    """Differential variance belongs to remediation, and must be surfaced."""
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    with repo.connect() as c:
        repo.insert(c, 'variances', {
            'id': uid(), 'comparison_id': uid(), 'job_id': job['id'], 'path': '$.body.total',
            'expected': '10', 'actual': '11', 'severity': 'HIGH', 'disposition': 'OPEN',
            'created_at': now(), 'kind': 'VALUE', 'normalisation': '{}'})

    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=2)

    assert any(x['kind'] == 'VARIANCE' and x['owner'] == 'remediation' for x in out['unresolved'])
    assert not any('VARIANCE' in f.get('check', '') for f in out['applied_fixes'])


def test_governed_dispatch_enforces_role_and_records_evidence(env):
    repo, job, plan_id, tmp = env
    proj = _project(tmp, yaml_text='db:\n  password: "pw123"\n')
    with repo.connect() as c:
        repo.insert(c, 'agent_runs', {
            'id': uid(), 'job_id': job['id'], 'agent_name': 'Code Generation Agent',
            'agent_version': '1.0.0', 'status': 'COMPLETED', 'input_evidence': '[]',
            'output_evidence': '[]', 'correlation_id': uid(), 'error': '', 'started_at': now(),
            'completed_at': now(), 'agent_id': 'code-generation', 'operator_identity': 'tester',
            'requested_tools': '[]', 'iteration': 1})
    registry = AgentRegistry(repo)
    params = {'plan_id': plan_id, 'project_root': 'springboot-output'}

    with pytest.raises(PermissionError, match='required_agent_role_missing'):
        registry.dispatch(job['id'], 'feedback', '1.0.0', ['ACE_ANALYST'], 'tester', params)

    out = registry.dispatch(job['id'], 'feedback', '1.0.0', ['FEEDBACK_ENGINEER'], 'tester', params)
    assert out['status'] == 'COMPLETED'
    assert [e['kind'] for e in out['evidence']] == ['CODE_FEEDBACK']
    assert out['outputs']['quality_status'] != 'FAIL'


def test_agent_fails_when_the_gate_is_still_failing(env):
    """A gate still red after the budget must not report COMPLETED.

    Saying COMPLETED would hand the delivery step code the gate rejects.
    """
    repo, job, plan_id, tmp = env
    proj = tmp / 'springboot-output'
    proj.mkdir(parents=True)
    (proj / 'App.java').write_text('class App{}')          # no pom.xml -> PROJECT_STRUCTURE fails
    with repo.connect() as c:
        repo.insert(c, 'agent_runs', {
            'id': uid(), 'job_id': job['id'], 'agent_name': 'Code Generation Agent',
            'agent_version': '1.0.0', 'status': 'COMPLETED', 'input_evidence': '[]',
            'output_evidence': '[]', 'correlation_id': uid(), 'error': '', 'started_at': now(),
            'completed_at': now(), 'agent_id': 'code-generation', 'operator_identity': 'tester',
            'requested_tools': '[]', 'iteration': 1})

    out = AgentRegistry(repo).dispatch(
        job['id'], 'feedback', '1.0.0', ['FEEDBACK_ENGINEER'], 'tester',
        {'plan_id': plan_id, 'project_root': 'springboot-output'})

    assert out['status'] == 'FAILED'
    assert 'quality_gate_unresolved' in out['error']
