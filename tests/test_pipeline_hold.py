"""Hold-for-approval and strict fail-fast.

These pin the two rules the pipeline runner is required to follow: a step
waiting on a governance approval holds the whole pipeline until the approval is
given, and one failure stops the whole pipeline with everything that has not run
left at PENDING.

Both were broken in ways the existing suite could not see. ``advance`` counted a
parked step as neither active nor blocked, so a gate-blocked last step let the
pipeline finalise to COMPLETED; the worker recorded the same failure twice and
the second write replaced the park with FAILED; the queue turned a failed step
into a ``2**attempt`` RETRY that re-ran it seconds later; a cancelled queue row
could never be revived, so a pulled-back step sat at QUEUED forever; and
``resume_gate_step`` released the first parked step it found regardless of which
gate had actually been approved. Every existing green test marked steps by hand
and never ran the worker, which is why none of it showed up.
"""
import json
import pytest
from app.repository import Repository
from app.service import FactoryService
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService, PipelineError

GATE_ERROR = 'PermissionError:required_gate_approval_missing:G2:quorum_0_of_1'


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'hold.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, PipelineService(repo), WorkflowOrchestrator(repo)


def agent_step(steps, agent_id):
    return next((s for s in steps if s['agent_id'] == agent_id), None)


def clone_step(steps, role):
    return next((s for s in steps if s['kind'] == 'CLONE'
                 and json.loads(s['params']).get('role') == role), None)


ACE_REPO = {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}}


def held(env, agents=('repository-intake',)):
    """A started pipeline whose first agent step is parked at G2.

    The job is moved to ACE_ANALYSED first because ``_enter_gate_wait`` parks by
    transitioning the job to WAITING_G2, and the state machine only allows that
    edge from ACE_ANALYSED.
    """
    repo, job, pipelines, workflow = env
    with repo.connect() as c:
        c.execute("update jobs set state='ACE_ANALYSED' where id=?", (job['id'],))
    p = pipelines.create(job['id'], 'workspace/orders', {}, list(agents), 'tester@example.com')
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    step = agent_step(pipelines.detail(p['id'])['steps'], agents[0])
    pipelines.mark_step(step['id'], 'FAILED', error=GATE_ERROR)
    return p['id'], step['id']


def two_branch(env):
    """A started pipeline with a CLONE and an independent agent, both QUEUED.

    ``governance`` is one of only three dependency-free agents in the catalogue
    and consumes no repository, so it is scheduled alongside the clone rather
    than behind it -- which is what makes it a genuine independent branch.
    Most agents (``dormancy-intelligence`` included) depend on
    ``repository-intake`` and so are merely downstream of the clone.
    """
    _, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders', ACE_REPO,
                         ['repository-intake', 'governance'], 'tester@example.com')
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    steps = pipelines.detail(p['id'])['steps']
    return p['id'], clone_step(steps, 'ACE'), agent_step(steps, 'governance')


# -- the hold ---------------------------------------------------------------

def test_gate_blocked_last_step_holds_instead_of_completing(env):
    repo, job, pipelines, workflow = env
    pid, step_id = held(env)
    pipelines.advance(pid, workflow)
    detail = pipelines.detail(pid)
    assert detail['status'] == 'WAITING_APPROVAL'
    assert detail['completed_at'] == ''
    assert repo.one('select status from pipeline_steps where id=?', (step_id,))['status'] == 'WAITING_APPROVAL'
    assert repo.one('select state from jobs where id=?', (job['id'],))['state'] == 'WAITING_G2'
    pkg = repo.one("select * from approval_packages where job_id=? and gate='G2'", (job['id'],))
    assert pkg and pkg['status'] == 'PENDING_APPROVAL'


def test_a_hold_stops_scheduling_on_independent_branches(env):
    _, _, pipelines, workflow = env
    pid, _ = held(env, agents=('repository-intake', 'dormancy-intelligence'))
    pipelines.advance(pid, workflow)
    other = agent_step(pipelines.detail(pid)['steps'], 'dormancy-intelligence')
    assert other['status'] == 'PENDING' and other['queue_id'] == ''
    assert pipelines.detail(pid)['status'] == 'WAITING_APPROVAL'


def test_the_original_gate_message_survives_the_park(env):
    repo, _, pipelines, workflow = env
    _, step_id = held(env)
    error = str(repo.one('select error from pipeline_steps where id=?', (step_id,))['error'])
    assert error.startswith('gate_retry:G2')
    assert 'required_gate_approval_missing' in error


def test_marking_a_held_step_failed_again_does_not_unpark_it(env):
    repo, _, pipelines, workflow = env
    _, step_id = held(env)
    pipelines.mark_step(step_id, 'FAILED', error=GATE_ERROR)
    step = repo.one('select * from pipeline_steps where id=?', (step_id,))
    assert step['status'] == 'WAITING_APPROVAL'
    assert str(step['error']).startswith('gate_retry:G2')


# -- releasing the hold -----------------------------------------------------

def test_approval_releases_the_held_step_and_returns_to_running(env):
    repo, job, pipelines, workflow = env
    pid, step_id = held(env)
    pipelines.advance(pid, workflow)
    pipelines.resume_gate_step(job['id'], workflow, 'G2')
    assert pipelines.detail(pid)['status'] == 'RUNNING'
    step = repo.one('select * from pipeline_steps where id=?', (step_id,))
    assert step['status'] == 'QUEUED'
    q = repo.one('select * from workflow_queue where id=?', (step['queue_id'],))
    assert q['status'] == 'READY' and not q['cancel_requested']


def test_an_approval_for_another_gate_does_not_release_the_hold(env):
    repo, job, pipelines, workflow = env
    pid, step_id = held(env)
    pipelines.advance(pid, workflow)
    pipelines.resume_gate_step(job['id'], workflow, 'G1')
    assert repo.one('select status from pipeline_steps where id=?', (step_id,))['status'] == 'WAITING_APPROVAL'
    assert pipelines.detail(pid)['status'] == 'WAITING_APPROVAL'


def test_a_released_step_that_never_ran_is_not_recorded_as_completed(env):
    """Completed agent steps are lifecycle waypoints, so a step that never
    executed must come back as PENDING -- otherwise an approval advances the
    lifecycle past work nobody did."""
    repo, job, pipelines, workflow = env
    pid, step_id = held(env)
    pipelines.advance(pid, workflow)
    pipelines.resume_gate_step(job['id'], workflow, 'G2')
    assert repo.one('select status from pipeline_steps where id=?', (step_id,))['status'] != 'COMPLETED'


# -- strict fail-fast -------------------------------------------------------

def test_strict_fail_fast_halts_an_independent_branch(env):
    _, _, pipelines, workflow = env
    pid, clone, independent = two_branch(env)
    assert independent['status'] == 'QUEUED'
    pipelines.mark_step(clone['id'], 'FAILED', error='git_command_failed:host unreachable')
    pipelines.advance(pid, workflow)
    after = agent_step(pipelines.detail(pid)['steps'], 'governance')
    assert after['status'] == 'PENDING' and after['queue_id'] == ''
    assert pipelines.detail(pid)['status'] == 'FAILED'
    assert not workflow.claim('w', activity='pipeline_step')


def test_a_failed_step_is_not_left_claimable_by_the_queue(env):
    """complete(success=False) schedules a RETRY, which would re-run a step the
    pipeline has deliberately halted."""
    repo, _, pipelines, workflow = env
    pid, clone, _ = two_branch(env)
    pipelines.mark_step(clone['id'], 'FAILED', error='boom')
    pipelines.advance(pid, workflow)
    q = repo.one('select * from workflow_queue where id=?', (clone['queue_id'],))
    assert q['cancel_requested']


def test_retry_after_a_strict_halt_resumes_the_whole_pipeline(env):
    _, _, pipelines, workflow = env
    pid, clone, _ = two_branch(env)
    pipelines.mark_step(clone['id'], 'FAILED', error='git_command_failed:host unreachable')
    pipelines.advance(pid, workflow)
    pipelines.retry_step(clone['id'])
    assert pipelines.detail(pid)['status'] == 'RUNNING'
    pipelines.advance(pid, workflow)
    steps = pipelines.detail(pid)['steps']
    assert clone_step(steps, 'ACE')['status'] == 'QUEUED'
    assert agent_step(steps, 'governance')['status'] == 'QUEUED'


def test_a_failure_outranks_a_concurrent_gate_hold(env):
    repo, _, pipelines, workflow = env
    pid, held_id = held(env, agents=('repository-intake', 'dormancy-intelligence'))
    other = agent_step(pipelines.detail(pid)['steps'], 'dormancy-intelligence')
    pipelines.mark_step(other['id'], 'FAILED', error='boom')
    pipelines.advance(pid, workflow)
    assert pipelines.detail(pid)['status'] == 'FAILED'
    assert repo.one('select status from pipeline_steps where id=?', (held_id,))['status'] == 'WAITING_APPROVAL'
    pipelines.retry_step(other['id'])
    pipelines.advance(pid, workflow)
    assert pipelines.detail(pid)['status'] == 'WAITING_APPROVAL'


# -- operator controls ------------------------------------------------------

def test_pause_and_resume_cannot_bypass_an_unapproved_gate(env):
    _, _, pipelines, workflow = env
    pid, _ = held(env)
    pipelines.advance(pid, workflow)
    pipelines.pause(pid, workflow)
    assert pipelines.detail(pid)['status'] == 'PAUSED'
    pipelines.resume(pid, workflow)
    detail = pipelines.detail(pid)
    assert detail['status'] == 'WAITING_APPROVAL'
    assert not [s for s in detail['steps'] if s['status'] == 'QUEUED']


def test_cancel_from_waiting_approval_cancels_the_held_step(env):
    repo, _, pipelines, workflow = env
    pid, step_id = held(env)
    pipelines.advance(pid, workflow)
    pipelines.cancel(pid, workflow)
    assert pipelines.detail(pid)['status'] == 'CANCELLED'
    assert repo.one('select status from pipeline_steps where id=?', (step_id,))['status'] == 'CANCELLED'


def test_run_is_refused_while_a_pipeline_is_held(env):
    _, _, pipelines, workflow = env
    pid, _ = held(env)
    pipelines.advance(pid, workflow)
    with pytest.raises(PipelineError, match='pipeline_not_ready:WAITING_APPROVAL'):
        pipelines.run(pid, workflow)
