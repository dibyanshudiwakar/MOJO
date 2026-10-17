"""Clarification hold: an agent that returns CLARIFICATION_REQUIRED parks its
step instead of completing it, blocks dependents the same way a gate hold
does (test_pipeline_hold.py), and resumes -- by re-running the agent, not by
completing it -- once every clarification tied to its run is answered.

Before this, ``WorkerLoop._execute`` only distinguished "raised an exception"
(FAILED) from "didn't" (COMPLETED), so an agent returning
CLARIFICATION_REQUIRED (no exception) was marked COMPLETED with an
empty/partial output and let a dependent step dispatch on it -- e.g.
code-generation reading an empty ``plan_id`` and failing with
``plan_not_found``.
"""
import pytest
from app.repository import Repository, uid, now
from app.service import FactoryService
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'clarify.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, PipelineService(repo), WorkflowOrchestrator(repo)


def agent_step(steps, agent_id):
    return next((s for s in steps if s['agent_id'] == agent_id), None)


def open_clarification(repo, job_id, run_id, question='Pick a mapping for the unsupported construct'):
    cid = uid()
    with repo.connect() as c:
        repo.insert(c, 'clarifications', {
            'id': cid, 'job_id': job_id, 'stage': 'PLANNING', 'question': question,
            'owner': 'ACE_ANALYST', 'impact': 'blocks plan generation', 'evidence_ref': '',
            'status': 'OPEN', 'answer': '', 'created_at': now(), 'answered_at': '',
            'answered_by': '', 'run_id': run_id})
    return cid


def clarification_held(env, agents=('repository-intake',)):
    """A started pipeline whose first agent step is parked on a clarification.

    Calls ``park_for_clarification`` directly with a synthetic run id and
    outputs, the same way ``WorkerLoop._execute`` would after
    ``_dispatch_agent`` returns ``_agent_status: 'CLARIFICATION_REQUIRED'`` --
    these tests exercise the pipeline-state machinery, not a real agent
    dispatch (the existing gate-hold tests take the same shortcut).
    """
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders', {}, list(agents), 'tester@example.com')
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    step = agent_step(pipelines.detail(p['id'])['steps'], agents[0])
    run_id = 'run-' + step['id']
    cid = open_clarification(repo, job['id'], run_id)
    outputs = {'_run_id': run_id, '_agent_status': 'CLARIFICATION_REQUIRED', 'risk_register': []}
    pipelines.park_for_clarification(step['id'], outputs, [cid])
    return p['id'], step['id'], cid, run_id


# -- the hold -----------------------------------------------------------------

def test_clarification_required_parks_instead_of_completing(env):
    repo, job, pipelines, workflow = env
    pid, step_id, cid, run_id = clarification_held(env)
    pipelines.advance(pid, workflow)
    detail = pipelines.detail(pid)
    assert detail['status'] == 'WAITING_APPROVAL'
    step = repo.one('select * from pipeline_steps where id=?', (step_id,))
    assert step['status'] == 'WAITING_CLARIFICATION'
    assert step['error'] == 'clarification_required:' + cid
    # Paused, not finished -- same as a gate hold (_enter_gate_wait never sets
    # completed_at either), and what the duration ticker's LIVE_STEP_STATUS
    # relies on to keep counting while parked instead of freezing.
    assert not step['completed_at']


def test_a_clarification_hold_stops_scheduling_on_independent_branches(env):
    _, _, pipelines, workflow = env
    pid, _, _, _ = clarification_held(env, agents=('repository-intake', 'dormancy-intelligence'))
    pipelines.advance(pid, workflow)
    other = agent_step(pipelines.detail(pid)['steps'], 'dormancy-intelligence')
    assert other['status'] == 'PENDING' and other['queue_id'] == ''
    assert pipelines.detail(pid)['status'] == 'WAITING_APPROVAL'


def test_marking_a_clarification_held_step_failed_again_does_not_unpark_it(env):
    repo, _, pipelines, workflow = env
    _, step_id, cid, _ = clarification_held(env)
    pipelines.mark_step(step_id, 'FAILED', error='boom')
    step = repo.one('select * from pipeline_steps where id=?', (step_id,))
    assert step['status'] == 'WAITING_CLARIFICATION'
    assert step['error'] == 'clarification_required:' + cid


# -- releasing the hold ---------------------------------------------------------

def test_answering_the_clarification_resumes_the_step_for_a_retry(env):
    repo, job, pipelines, workflow = env
    pid, step_id, cid, run_id = clarification_held(env)
    pipelines.advance(pid, workflow)
    with repo.connect() as c:
        c.execute("update clarifications set status='ANSWERED',answer='use pattern X' where id=?", (cid,))
    resumed = pipelines.resume_clarification_steps(job['id'], workflow)
    assert resumed
    assert pipelines.detail(pid)['status'] == 'RUNNING'
    step = repo.one('select * from pipeline_steps where id=?', (step_id,))
    assert step['status'] == 'QUEUED'
    assert step['error'] == ''
    q = repo.one('select * from workflow_queue where id=?', (step['queue_id'],))
    assert q['status'] == 'READY' and not q['cancel_requested']


def test_resuming_with_a_still_open_clarification_does_nothing(env):
    repo, job, pipelines, workflow = env
    pid, step_id, cid, run_id = clarification_held(env)
    pipelines.advance(pid, workflow)
    resumed = pipelines.resume_clarification_steps(job['id'], workflow)
    assert not resumed
    assert repo.one('select status from pipeline_steps where id=?', (step_id,))['status'] == 'WAITING_CLARIFICATION'
    assert pipelines.detail(pid)['status'] == 'WAITING_APPROVAL'


def test_answering_a_different_runs_clarification_does_not_resume_this_step(env):
    repo, job, pipelines, workflow = env
    pid, step_id, cid, run_id = clarification_held(env)
    pipelines.advance(pid, workflow)
    other_cid = open_clarification(repo, job['id'], 'some-other-run')
    with repo.connect() as c:
        c.execute("update clarifications set status='ANSWERED',answer='x' where id=?", (other_cid,))
    resumed = pipelines.resume_clarification_steps(job['id'], workflow)
    assert not resumed
    assert repo.one('select status from pipeline_steps where id=?', (step_id,))['status'] == 'WAITING_CLARIFICATION'
