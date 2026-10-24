"""An agent that fails by returning a failure is a failed step.

``code-generation`` returned status FAILED when the quality gate failed. The
registry recorded its run as FAILED, but the worker only distinguished "raised"
from "didn't" (and CLARIFICATION_REQUIRED), so the step was reported as
"completed successfully" with a failed run behind it. Everything that depends
on that agent's completed run was then refused: the feedback agent -- which
exists to repair a project the gate has flagged -- failed with
agent_dependency_not_satisfied:code-generation on a job whose pipeline showed
code-generation green.
"""
import json

from tests.test_worker import env, find  # noqa: F401  (env is a fixture)


def _stub(worker, **result):
    base = {'run_id': 'run-1', 'agent_id': 'repository-intake', 'version': '1.0.0',
            'status': 'COMPLETED', 'outputs': {'intake_manifest': 'm'}, 'evidence': [],
            'clarification_ids': [], 'metrics': {}, 'reasoning': {}, 'error': ''}
    base.update(result)
    worker.agents.dispatch = lambda *a, **k: dict(base)


def _run(env, agents=('repository-intake',)):
    repo, job, owner, pipelines, workflow, worker = env
    p = pipelines.create(job['id'], 'workspace/orders', {}, list(agents), owner['email'])
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    worker.process_one()
    return pipelines.detail(p['id'])


def test_a_returned_failure_fails_the_step_with_the_agents_error(env):
    _, _, _, _, _, worker = env
    _stub(worker, status='FAILED', error='something_specific_went_wrong:abc')
    detail = _run(env)
    step = find(detail['steps'], 'repository-intake')
    assert step['status'] == 'FAILED'
    assert step['error'] == 'something_specific_went_wrong:abc'
    assert detail['status'] == 'FAILED', 'and the pipeline halts'


def test_a_returned_failure_without_an_error_still_fails_and_says_so(env):
    _, _, _, _, _, worker = env
    _stub(worker, status='FAILED', error='')
    step = find(_run(env)['steps'], 'repository-intake')
    assert step['status'] == 'FAILED' and step['error'] == 'agent_reported_failure'


def test_a_completed_agent_still_completes(env):
    _, _, _, _, _, worker = env
    _stub(worker)
    assert find(_run(env)['steps'], 'repository-intake')['status'] == 'COMPLETED'


# ---- repair_by -------------------------------------------------------------------------------

def test_a_result_that_needs_repair_completes_if_nothing_will_repair_it(env):
    """No repairing agent in the pipeline must not block every downstream step.

    Used to fail the step outright; now logs a warning and completes, so a
    pipeline run without a feedback step (e.g. feedback disabled) still
    reaches governance/knowledge-evidence/etc. instead of halting on step 1.
    """
    _, _, _, _, _, worker = env
    _stub(worker, metrics={'repair_by': 'feedback', 'quality_run_id': 'q-1'})
    step = find(_run(env)['steps'], 'repository-intake')
    assert step['status'] == 'COMPLETED'
    assert step['error'] == ''


def test_a_result_that_needs_repair_completes_when_the_repairing_step_is_there(env):
    repo, job, owner, pipelines, workflow, worker = env
    _stub(worker, metrics={'repair_by': 'feedback', 'quality_run_id': 'q-1'})
    p = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake', 'feedback'], owner['email'])
    # Selected alongside its dependency in a real pipeline; this harness has no
    # code-generation step, so the feedback step is marked pending by hand.
    with repo.connect() as c:
        c.execute("update pipeline_steps set status='PENDING',error='' where pipeline_id=? and agent_id='feedback'",
                  (p['id'],))
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    worker.process_one()
    assert find(pipelines.detail(p['id'])['steps'], 'repository-intake')['status'] == 'COMPLETED'


def test_a_skipped_repairing_step_does_not_count(env):
    """A feedback step present but SKIPPED is the same as absent -- still completes, with a warning."""
    repo, job, owner, pipelines, workflow, worker = env
    _stub(worker, metrics={'repair_by': 'feedback', 'quality_run_id': 'q-1'})
    p = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake', 'feedback'], owner['email'])
    with repo.connect() as c:
        c.execute("update pipeline_steps set status='SKIPPED' where pipeline_id=? and agent_id='feedback'",
                  (p['id'],))
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    worker.process_one()
    step = find(pipelines.detail(p['id'])['steps'], 'repository-intake')
    assert step['status'] == 'COMPLETED'
    assert step['error'] == ''
