"""A failed quality gate and a failed pipeline step each get an HTML report."""
import json

import pytest

from app.repository import Repository, now, uid
from app.service import FactoryService
from app.services import failure_reports as fr
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'fr.db'))
    job = FactoryService(repo).create_job({'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, PipelineService(repo), WorkflowOrchestrator(repo)


def _quality_run(repo, job_id, status='FAIL'):
    checks = [
        {'name': 'COMPILE_AND_UNIT_TEST', 'status': 'FAIL', 'evidence': {
            'status': 'FAIL', 'command': ['mvn', '-B', '-s', 'settings.xml', 'clean', 'verify'], 'returncode': 1,
            'errors': ['[ERROR] Non-resolvable parent POM for com.x:y:0.0.1: framework-starter:3.2.2 (absent)',
                       '[ERROR] <script>alert(1)</script>'],
            'framework': {'status': 'PASS', 'note': 'already built and installed for this commit'}}},
        {'name': 'STARTUP_SMOKE', 'status': 'NOT_EXECUTED', 'evidence': 'the build did not pass'},
        {'name': 'SKILL_M18', 'status': 'GAP', 'severity': 'MEDIUM', 'skill_ref': 'gap-checklist.md [M18]',
         'evidence': ['circuit-breaker properties are configured but no isCircuitBreakerError() branch exists'],
         'remediation': 'Add the branch.'},
        {'name': 'PROJECT_STRUCTURE', 'status': 'PASS'}]
    rid = uid()
    payload = json.dumps({'checks': checks, 'project_root': '/ws/proj', 'plan_id': 'p1'})
    with repo.connect() as c:
        c.execute('insert into quality_runs values(?,?,?,?,?,?,?,?)',
                  (rid, job_id, 'p1', status, payload, 'h', now(), '/ws/proj'))
    return rid


def _row(repo, report_id):
    return repo.one('select * from validation_reports where id=?', (report_id,))


def test_a_failed_quality_gate_gets_a_report_with_the_why_and_the_build_output(env):
    repo, job, *_ = env
    made = fr.record_quality(repo, job['id'], _quality_run(repo, job['id']))
    assert made['html_url'] == '/api/v2/reports/%s/html' % made['report_id']
    page = fr.render(_row(repo, made['report_id']))
    assert 'Quality gate report' in page and 'COMPILE_AND_UNIT_TEST' in page
    assert 'Non-resolvable parent POM' in page and 'mvn -B -s settings.xml clean verify' in page
    assert 'SKILL_M18' in page and 'Add the branch.' in page
    assert 'already built and installed' in page, 'the framework build is part of the story'


def test_nothing_in_a_report_is_interpreted_as_markup(env):
    repo, job, *_ = env
    page = fr.render(_row(repo, fr.record_quality(repo, job['id'], _quality_run(repo, job['id']))['report_id']))
    assert '<script>' not in page and '&lt;script&gt;' in page


def test_one_report_per_quality_run(env):
    repo, job, *_ = env
    run = _quality_run(repo, job['id'])
    a, b = fr.record_quality(repo, job['id'], run), fr.record_quality(repo, job['id'], run)
    assert a['report_id'] == b['report_id']
    assert fr.latest_for_job(repo, job['id'])['report_id'] == a['report_id']


def test_an_unknown_quality_run_makes_no_report(env):
    repo, job, *_ = env
    assert fr.record_quality(repo, job['id'], 'nope') is None


def _failed_step(env, error='required_agent_role_missing'):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake', 'dormancy-intelligence'], 'tester@example.com')
    step = next(s for s in pipelines.detail(p['id'])['steps'] if s['agent_id'] == 'repository-intake')
    pipelines.mark_step(step['id'], 'RUNNING')
    pipelines.mark_step(step['id'], 'FAILED', error=error)
    return step['id']


def test_a_step_that_fails_gets_its_report_at_once(env):
    repo, *_ = env
    step_id = _failed_step(env)
    found = fr.for_step(repo, step_id)
    assert found, 'mark_step(FAILED) writes the report'
    page = fr.render(_row(repo, found['report_id']))
    assert 'Failure report' in page and 'repository-intake' in page
    assert 'required_agent_role_missing' in page
    assert 'lacks the role' in page, 'the page says what it usually means'
    assert 'Pipeline progress' in page and 'dormancy-intelligence' in page


def test_the_failure_report_carries_the_steps_log(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake'], 'tester@example.com')
    step = pipelines.detail(p['id'])['steps'][0]
    with repo.connect() as c:
        c.execute('insert into pipeline_step_logs(id,step_id,level,message,created_at) values(?,?,?,?,?)',
                  (uid(), step['id'], 'ERROR', 'LLM response received: status=FAILED', now()))
    pipelines.mark_step(step['id'], 'FAILED', error='boom')
    page = fr.render(_row(repo, fr.for_step(repo, step['id'])['report_id']))
    assert 'Step log' in page and 'LLM response received: status=FAILED' in page


@pytest.mark.parametrize('error,needle', [
    ('required_agent_role_missing', 'lacks the role'),
    ('remediation_requires_pattern_tests_and_rollback', 'rollback'),
    ('agent_dependency_not_satisfied:code-generation', 'earlier agent'),
    ('Non-resolvable parent POM for x', 'parent POM'),
    ('something nobody has seen before', 'no specific advice')])
def test_known_errors_get_specific_advice(error, needle):
    title, meaning, steps = fr.hint_for(error)
    assert needle in (title + ' ' + meaning).lower() or needle in (title + ' ' + meaning), (title, meaning)
    assert steps


def test_a_failure_with_no_report_yet_has_none(env):
    repo, *_ = env
    assert fr.for_step(repo, 'missing') is None
