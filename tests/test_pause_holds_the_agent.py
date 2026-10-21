"""Pausing a pipeline holds the agent that is already running.

Pause used to stop only the scheduling of new steps. A step already running kept
making model calls and finished, and its completion advanced the job to its next
state -- the pipeline said PAUSED while the work and the state moved on.

An agent cannot be suspended mid-request, but it asks one predicate before every
request and retry. While the pipeline is PAUSED that predicate does not return:
the agent waits at its next checkpoint, nothing is spent, and it carries on from
exactly there on resume. Stop still gets through.
"""
import threading
import time

import pytest

from app.repository import Repository
from app.service import FactoryService
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService
from app.worker import WorkerLoop


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(message)

    warn = info


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'pause.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    pipelines, workflow = PipelineService(repo), WorkflowOrchestrator(repo)
    pipeline = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake'], 'tester@example.com')
    pipelines.confirm(pipeline['id'])
    pipelines.run(pipeline['id'], workflow)
    step = pipelines.detail(pipeline['id'])['steps'][0]
    pipelines.mark_step(step['id'], 'RUNNING')
    worker = WorkerLoop.__new__(WorkerLoop)
    worker.repo = repo
    worker.CANCEL_POLL_SECONDS = 0
    worker.PAUSE_POLL_SECONDS = 0.05
    return repo, pipelines, workflow, pipeline['id'], step['id'], worker


def _wait_for(condition, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_a_running_pipeline_does_not_hold_the_agent(env):
    repo, pipelines, workflow, pipeline_id, step_id, worker = env
    assert worker._cancellation_check(step_id)() is False


def test_a_paused_pipeline_holds_the_agent_at_its_next_checkpoint(env):
    repo, pipelines, workflow, pipeline_id, step_id, worker = env
    pipelines.pause(pipeline_id, workflow)
    log = _Log()
    answered = []
    check = worker._cancellation_check(step_id, log)
    thread = threading.Thread(target=lambda: answered.append(check()), daemon=True)
    thread.start()

    assert not _wait_for(lambda: answered, seconds=0.5), 'the agent is held, not told to carry on'
    assert any('paused' in line.lower() for line in log.lines)
    assert pipelines.detail(pipeline_id)['steps'][0]['status'] == 'RUNNING', 'and its step is exactly where it was'

    pipelines.resume(pipeline_id, workflow)
    assert _wait_for(lambda: answered), 'resuming lets it carry on'
    assert answered == [False], 'carrying on, not stopping'
    assert any('resumed' in line.lower() for line in log.lines)


def test_stop_gets_through_a_pause(env):
    repo, pipelines, workflow, pipeline_id, step_id, worker = env
    pipelines.pause(pipeline_id, workflow)
    answered = []
    check = worker._cancellation_check(step_id)
    thread = threading.Thread(target=lambda: answered.append(check()), daemon=True)
    thread.start()
    assert not _wait_for(lambda: answered, seconds=0.3)

    pipelines.cancel(pipeline_id, workflow)

    assert _wait_for(lambda: answered), 'a held agent still hears Stop'
    assert answered == [True]
    assert check() is True, 'and stays stopped'


def test_agents_asking_together_wait_together(env):
    repo, pipelines, workflow, pipeline_id, step_id, worker = env
    pipelines.pause(pipeline_id, workflow)
    log = _Log()
    check = worker._cancellation_check(step_id, log)
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(check()), daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    assert not _wait_for(lambda: answers, seconds=0.4)
    assert len([line for line in log.lines if 'paused' in line.lower()]) == 1, 'the pause is announced once'

    pipelines.resume(pipeline_id, workflow)
    assert _wait_for(lambda: len(answers) == 4)
    assert answers == [False] * 4


def test_pausing_does_not_advance_the_job_while_the_step_waits(env):
    repo, pipelines, workflow, pipeline_id, step_id, worker = env
    job_id = repo.one('select job_id from pipelines where id=?', (pipeline_id,))['job_id']
    before = repo.one('select state from jobs where id=?', (job_id,))['state']
    pipelines.pause(pipeline_id, workflow)
    check = worker._cancellation_check(step_id)
    threading.Thread(target=check, daemon=True).start()
    time.sleep(0.3)
    assert repo.one('select state from jobs where id=?', (job_id,))['state'] == before
