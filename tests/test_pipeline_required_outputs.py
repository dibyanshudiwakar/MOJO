"""A pipeline step that finishes without the output the rest depends on has failed, where it failed.

Planning stops at the mapping catalogue, with ``plan=''``, when a construct has no approved Spring pattern. The
pipeline used to mark that step COMPLETED and hand the empty plan id to code generation, which then reported
``plan_not_found`` -- the wrong step, and no hint of the cause.
"""
import pytest

from app.repository import Repository
from app.service import FactoryService
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService, REQUIRED_OUTPUTS


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'pipe.db'))
    job = FactoryService(repo).create_job({'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    pipelines = PipelineService(repo)
    workflow = WorkflowOrchestrator(repo)
    pipe = pipelines.create(job['id'], 'workspace/orders', {},
                            ['repository-intake', 'planning', 'code-generation'], 'tester@example.com')
    pipelines.confirm(pipe['id'])
    pipe = pipelines.run(pipe['id'], workflow)
    return repo, pipelines, workflow, pipe


def step(pipelines, pipe, agent_id):
    return next(s for s in pipelines.detail(pipe['id'])['steps'] if s['agent_id'] == agent_id)


RISKS = ([{'category': 'UNSUPPORTED_MAPPING', 'severity': 'HIGH', 'statement': 'No approved Spring mapping for eflow:FCMSource'}] * 4
         + [{'category': 'UNSUPPORTED_MAPPING', 'severity': 'HIGH', 'statement': 'No approved Spring mapping for GET.subflow:FCMComposite_1'}])


def test_planning_with_no_plan_fails_the_planning_step_and_says_why(env):
    repo, pipelines, workflow, pipe = env
    planning = step(pipelines, pipe, 'planning')
    pipelines.mark_step(planning['id'], 'RUNNING')
    pipelines.mark_step(planning['id'], 'COMPLETED', outputs={'plan': '', 'task_graph': [], 'mapping': 'c1', 'risk_register': RISKS})
    failed = step(pipelines, pipe, 'planning')
    assert failed['status'] == 'FAILED'
    assert failed['error'].startswith('planning_produced_no_plan: 5 construct(s) have no approved Spring mapping')
    assert 'eflow:FCMSource x4' in failed['error'] and 'GET.subflow:FCMComposite_1' in failed['error']
    assert 'retry planning' in failed['error']


def test_the_outputs_the_step_did_produce_are_kept_for_inspection(env):
    repo, pipelines, workflow, pipe = env
    planning = step(pipelines, pipe, 'planning')
    pipelines.mark_step(planning['id'], 'COMPLETED', outputs={'plan': '', 'mapping': 'c1', 'risk_register': RISKS})
    import json
    assert json.loads(step(pipelines, pipe, 'planning')['outputs'])['mapping'] == 'c1'


def test_code_generation_never_starts_on_an_empty_plan(env):
    repo, pipelines, workflow, pipe = env
    planning = step(pipelines, pipe, 'planning')
    pipelines.mark_step(planning['id'], 'COMPLETED', outputs={'plan': '', 'risk_register': RISKS})
    pipelines.advance(pipe['id'], workflow)
    assert pipelines.detail(pipe['id'])['status'] == 'FAILED'
    assert step(pipelines, pipe, 'code-generation')['status'] not in ('QUEUED', 'RUNNING', 'COMPLETED')


def test_planning_with_a_plan_completes_as_before(env):
    repo, pipelines, workflow, pipe = env
    planning = step(pipelines, pipe, 'planning')
    pipelines.mark_step(planning['id'], 'COMPLETED', outputs={'plan': 'plan-1', 'risk_register': []})
    done = step(pipelines, pipe, 'planning')
    assert done['status'] == 'COMPLETED' and not done['error']


def test_a_failure_without_a_risk_register_still_names_the_missing_output(env):
    repo, pipelines, workflow, pipe = env
    planning = step(pipelines, pipe, 'planning')
    pipelines.mark_step(planning['id'], 'COMPLETED', outputs={'plan': ''})
    assert step(pipelines, pipe, 'planning')['error'].startswith('planning_produced_no_plan:')


def test_the_failure_is_a_failure_not_a_gate_hold(env):
    """Its text must not be mistaken for a missing gate approval, which parks the step instead."""
    repo, pipelines, workflow, pipe = env
    planning = step(pipelines, pipe, 'planning')
    pipelines.mark_step(planning['id'], 'COMPLETED', outputs={'plan': '', 'risk_register': RISKS})
    assert step(pipelines, pipe, 'planning')['status'] == 'FAILED'


def test_other_agents_are_not_held_to_a_required_output(env):
    repo, pipelines, workflow, pipe = env
    intake = step(pipelines, pipe, 'repository-intake')
    pipelines.mark_step(intake['id'], 'COMPLETED', outputs={'intake_manifest': 'm1', 'gaps': []})
    assert step(pipelines, pipe, 'repository-intake')['status'] == 'COMPLETED'
    assert set(REQUIRED_OUTPUTS) == {'planning'}
