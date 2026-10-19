"""Cancelling a pipeline stops the agent that is already running.

It did not. ``cancel`` handled QUEUED, PENDING and HELD steps and left a
RUNNING one completely alone, and the confirm dialog said so out loud: "a step
already running will finish but no longer affects the outcome". For an agent
that spends half an hour making Opus calls per source file, "will finish" meant
the operator pressed Stop and the bill kept running.

Evidence it happened: pipeline 565a6c8d CANCELLED, with two ace-analysis runs
against its job still marked RUNNING -- one of them for an hour and a half,
with no worker process alive to be doing them.

Cancellation is cooperative. The agent is not interrupted mid-request: a
gateway call takes up to two minutes and killing the worker would stop every
other step in that process and leave exactly the orphaned rows above. Instead
``cancel`` writes CANCELLED onto the RUNNING step, and that status *is* the
signal -- ``artefact.produce_many`` checks it between source files and
``produce`` checks it between retries, then raises ``StepCancelled``.

So there are three claims to hold, and this file covers all three:
  * the signal is written, for a RUNNING step as much as a queued one;
  * an agent in flight sees it and stops, discarding partial work;
  * nothing afterwards overwrites the operator's decision with a fault or a
    success nobody wanted.
"""
import pytest

from app.agents.artefact import StepCancelled, produce, produce_many
from app.repository import Repository
from app.service import FactoryService
from app.services.agents import AgentRegistry
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService

PROMPT = 'agent/dormancy-intelligence/classification-rationale'
VALID = {'classification': 'ACTIVE', 'rationale': 'traffic observed'}


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'cancel.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, PipelineService(repo), WorkflowOrchestrator(repo)


@pytest.fixture
def running(env):
    """A pipeline with its first step RUNNING, as the worker leaves it."""
    repo, job, pipelines, workflow = env
    pipeline = pipelines.create(job['id'], 'workspace/orders', {},
                                ['repository-intake'], 'tester@example.com')
    pipelines.confirm(pipeline['id'])
    pipelines.run(pipeline['id'], workflow)
    step = pipelines.detail(pipeline['id'])['steps'][0]
    pipelines.mark_step(step['id'], 'RUNNING')
    return pipeline['id'], step['id']


# ---- the signal ----------------------------------------------------------------------------

def test_cancelling_marks_the_running_step_not_just_the_queued_ones(env, running):
    repo, job, pipelines, workflow = env
    pipeline_id, step_id = running
    assert pipelines.detail(pipeline_id)['steps'][0]['status'] == 'RUNNING'
    pipelines.cancel(pipeline_id, workflow)
    step = pipelines.detail(pipeline_id)['steps'][0]
    assert step['status'] == 'CANCELLED', 'a running step used to be left alone'
    assert step['completed_at'], 'and it is finished, so it has a finish time'


def test_the_running_steps_agent_run_is_closed(env, running):
    """An open run row says work is in progress that nothing is doing.

    Which is precisely what was found: two ace-analysis runs RUNNING against a
    CANCELLED pipeline, with no process alive. Agent health counts those.
    """
    repo, job, pipelines, workflow = env
    pipeline_id, step_id = running
    registry = AgentRegistry(repo)
    run = registry.start(job['id'], 'repository-intake', '1.0.0', [],
                         ('REPOSITORY_INTAKE',), [], 'tester@example.com')
    with repo.connect() as c:
        c.execute('update pipeline_steps set agent_run_id=? where id=?', (run['id'], step_id))
    assert repo.one('select status from agent_runs where id=?', (run['id'],))['status'] == 'RUNNING'
    pipelines.cancel(pipeline_id, workflow)
    closed = repo.one('select status,error,completed_at from agent_runs where id=?', (run['id'],))
    assert closed['status'] == 'CANCELLED', 'not FAILED: an operator chose this'
    assert closed['error'] == 'pipeline_cancelled'
    assert closed['completed_at']


def test_a_cancelled_step_cannot_be_dragged_back(env, running):
    """The decision is final, whatever the agent's unwinding writes.

    ``cancel`` races the agent by design -- the agent notices at its next
    checkpoint -- so the worker may still try to record a result on the way
    out. Neither a failure nor a success may replace the cancellation.
    """
    repo, job, pipelines, workflow = env
    pipeline_id, step_id = running
    pipelines.cancel(pipeline_id, workflow)
    pipelines.mark_step(step_id, 'FAILED', error='StepCancelled:step_cancelled')
    assert pipelines.detail(pipeline_id)['steps'][0]['status'] == 'CANCELLED'
    pipelines.mark_step(step_id, 'COMPLETED', outputs={'intake_manifest': 'x'})
    assert pipelines.detail(pipeline_id)['steps'][0]['status'] == 'CANCELLED'


# ---- an agent in flight sees it -------------------------------------------------------------

class _Counting:
    """A provider that answers every call and counts them."""

    accepts_skill = True

    def __init__(self):
        self.calls = 0

    def invoke(self, template, payload, skill=None):
        self.calls += 1
        return VALID


def _context(repo, job, provider, cancelled):
    from app.agents.dormancy_intelligence import DormancyIntelligenceAgent
    return DormancyIntelligenceAgent(repo).context(
        job['id'], provider=provider, cancelled=cancelled)


def test_produce_many_stops_between_items_and_discards_what_it_had(env):
    """The checkpoint that matters: this loop is where a long agent lives."""
    repo, job, pipelines, workflow = env
    provider = _Counting()
    stop = {'now': False}
    items = [(str(n), {'traffic_count': n}) for n in range(1, 6)]

    def cancelled():
        # Cancelled once two items are done, as an operator pressing Stop
        # part-way through would.
        return provider.calls >= 2

    with pytest.raises(StepCancelled) as raised:
        produce_many(_context(repo, job, provider, cancelled), PROMPT, items)
    assert raised.value.produced == 2, 'it stopped at the next item boundary'
    assert raised.value.total == 5
    assert provider.calls == 2, 'no further model calls were spent'
    # And nothing is returned: produce_many is all-or-nothing, so a partial
    # artefact is not a smaller artefact, it is an indistinguishable one.
    assert 'discarded' in str(raised.value)


def test_produce_stops_before_an_attempt(env):
    """A retry with its backoff can land after Stop was pressed."""
    repo, job, pipelines, workflow = env
    provider = _Counting()
    with pytest.raises(StepCancelled):
        produce(_context(repo, job, provider, lambda: True), PROMPT, {'traffic_count': 1})
    assert provider.calls == 0, 'cancelled before the first attempt was made'


def test_an_uncancelled_run_is_unaffected(env):
    """The check must not cost the normal path anything."""
    repo, job, pipelines, workflow = env
    provider = _Counting()
    items = [(str(n), {'traffic_count': n}) for n in range(1, 4)]
    produced, provenance = produce_many(
        _context(repo, job, provider, lambda: False), PROMPT, items)
    assert len(produced) == 3 and provider.calls == 3
    assert provenance['mode'] == 'MODEL_GATEWAY'


def test_a_broken_cancellation_check_does_not_stop_the_work(env):
    """Fails open. A predicate that raises must not abort a healthy run.

    The check is a database read on a worker that may be under load; a
    transient error there is not the operator asking for anything.
    """
    repo, job, pipelines, workflow = env
    provider = _Counting()

    def exploding():
        raise RuntimeError('the cancellation table is on fire')

    produced, _ = produce_many(_context(repo, job, provider, exploding), PROMPT,
                               [('a', {'traffic_count': 1})])
    assert len(produced) == 1


def test_a_context_without_a_cancellation_check_still_runs(env):
    """Every existing caller, and every direct run(), passes nothing."""
    repo, job, pipelines, workflow = env
    from app.agents.dormancy_intelligence import DormancyIntelligenceAgent
    provider = _Counting()
    context = DormancyIntelligenceAgent(repo).context(job['id'], provider=provider)
    assert context.cancelled() is False
    answer, _ = produce(context, PROMPT, {'traffic_count': 1})
    assert answer['classification'] == 'ACTIVE'


# ---- the worker's own check ----------------------------------------------------------------

def test_the_workers_check_reads_the_steps_status(env, running):
    """The step's status is the signal, so there is no second flag to drift."""
    from app.worker import WorkerLoop
    repo, job, pipelines, workflow = env
    pipeline_id, step_id = running
    worker = WorkerLoop(repo)
    check = worker._cancellation_check(step_id)
    assert check() is False, 'a RUNNING step is not cancelled'
    pipelines.cancel(pipeline_id, workflow)
    # Poll interval: the check caches its answer for a second.
    worker.CANCEL_POLL_SECONDS = 0
    assert worker._cancellation_check(step_id)() is True


def test_the_check_never_un_cancels(env, running):
    """Once true it stays true, whatever happens to the row afterwards."""
    from app.worker import WorkerLoop
    repo, job, pipelines, workflow = env
    pipeline_id, step_id = running
    worker = WorkerLoop(repo)
    worker.CANCEL_POLL_SECONDS = 0
    check = worker._cancellation_check(step_id)
    pipelines.cancel(pipeline_id, workflow)
    assert check() is True
    with repo.connect() as c:
        c.execute("update pipeline_steps set status='RUNNING' where id=?", (step_id,))
    assert check() is True, 'an unwinding agent must not be told to carry on'
