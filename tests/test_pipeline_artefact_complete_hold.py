"""A step with questions put to a person is not COMPLETED, and not re-run either.

Auto mode lets an agent produce its artefact without waiting on every question,
and decides most of them itself. The ones it puts to a person are real, though:
a run that left 59 of them open was nonetheless shown as COMPLETED, which said
nothing was outstanding.

So the step parks while the agent run stays COMPLETED -- and because the
artefact already exists, answering the last question completes the step with
what it produced. Re-running ace-analysis for that would be half an hour of
repeated work (and ``resume_clarification_steps`` re-runs by default, for an
agent that had produced nothing).
"""
import pytest

from app.repository import Repository, now, uid
from app.service import FactoryService
from app.services.clarification_reuse import ACTOR, settle_siblings
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'hold.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, PipelineService(repo), WorkflowOrchestrator(repo)


def _question(repo, job_id, run_id, text='Which context root?', stage='ACE_ANALYSIS'):
    cid = uid()
    with repo.connect() as c:
        repo.insert(c, 'clarifications', {
            'id': cid, 'job_id': job_id, 'stage': stage, 'question': text, 'owner': 'ACE_SME',
            'impact': 'routing', 'evidence_ref': '', 'status': 'OPEN', 'answer': '',
            'created_at': now(), 'answered_at': '', 'answered_by': '', 'run_id': run_id})
    return cid


def _answer(repo, cid):
    with repo.connect() as c:
        c.execute("update clarifications set status='ANSWERED',answer='/v2' where id=?", (cid,))


def _parked(env, artefact_complete):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake'], 'tester@example.com')
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    step = next(s for s in pipelines.detail(p['id'])['steps'] if s['agent_id'] == 'repository-intake')
    run_id = 'run-' + step['id']
    cids = [_question(repo, job['id'], run_id, 'Q%d?' % i) for i in range(2)]
    outputs = {'_run_id': run_id, '_agent_status': 'CLARIFICATION_REQUIRED',
               '_artefact_complete': artefact_complete, 'inventory': {'files': 3}}
    pipelines.park_for_clarification(step['id'], outputs, cids)
    return p['id'], step['id'], cids


def _step(repo, step_id):
    return repo.one('select * from pipeline_steps where id=?', (step_id,))


def test_a_step_with_a_finished_artefact_completes_when_its_last_question_is_answered(env):
    repo, job, pipelines, workflow = env
    pid, step_id, cids = _parked(env, artefact_complete=True)
    for cid in cids:
        _answer(repo, cid)

    assert pipelines.resume_clarification_steps(job['id'], workflow)

    step = _step(repo, step_id)
    assert step['status'] == 'COMPLETED', 'not requeued: the artefact already exists'
    assert step['completed_at'] and step['error'] == ''
    import json
    outputs = json.loads(step['outputs'])
    assert outputs['inventory'] == {'files': 3}, 'what the agent produced is kept'
    assert outputs['_agent_status'] == 'COMPLETED' and not outputs['_artefact_complete']


def test_it_stays_parked_while_any_question_is_open(env):
    repo, job, pipelines, workflow = env
    _, step_id, cids = _parked(env, artefact_complete=True)
    _answer(repo, cids[0])
    assert not pipelines.resume_clarification_steps(job['id'], workflow)
    step = _step(repo, step_id)
    assert step['status'] == 'WAITING_CLARIFICATION' and not step['completed_at']


def test_an_agent_that_produced_nothing_is_still_re_run_not_completed(env):
    """The old behaviour, unchanged for planning with no plan."""
    repo, job, pipelines, workflow = env
    _, step_id, cids = _parked(env, artefact_complete=False)
    for cid in cids:
        _answer(repo, cid)
    assert pipelines.resume_clarification_steps(job['id'], workflow)
    assert _step(repo, step_id)['status'] == 'QUEUED'


# ---- the worker carries the flag -------------------------------------------------------------

def test_the_worker_passes_the_flag_into_the_step_outputs():
    import inspect
    from app import worker
    assert "'_artefact_complete': bool(result.get('artefact_complete'))" in inspect.getsource(worker)


# ---- answering a question closes its open copies ----------------------------------------------

def _item(repo, cid):
    return repo.one('select * from clarifications where id=?', (cid,))


def test_answering_closes_the_other_open_copies_of_the_same_question(env):
    repo, job, _, _ = env
    first = _question(repo, job['id'], 'r1', 'Which context root?')
    copy = _question(repo, job['id'], 'r2', 'which  CONTEXT root')
    _answer(repo, first)

    assert settle_siblings(repo, _item(repo, first), '/v2') == 1

    row = _item(repo, copy)
    assert (row['status'], row['answer'], row['answered_by'], row['reused_from']) == (
        'DUPLICATE', '/v2', ACTOR, first)


@pytest.mark.parametrize('text,stage', [('Which queue manager?', 'ACE_ANALYSIS'),
                                         ('Which context root?', 'PLANNING')])
def test_a_different_question_or_stage_is_left_open(env, text, stage):
    repo, job, _, _ = env
    first = _question(repo, job['id'], 'r1', 'Which context root?')
    other = _question(repo, job['id'], 'r2', text, stage=stage)
    _answer(repo, first)
    assert settle_siblings(repo, _item(repo, first), '/v2') == 0
    assert _item(repo, other)['status'] == 'OPEN'


def test_a_copy_in_another_job_is_left_to_that_job(env):
    repo, job, _, _ = env
    other_job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'def'}, 'tester')
    first = _question(repo, job['id'], 'r1')
    elsewhere = _question(repo, other_job['id'], 'r2')
    _answer(repo, first)
    assert settle_siblings(repo, _item(repo, first), '/v2') == 0
    assert _item(repo, elsewhere)['status'] == 'OPEN'
