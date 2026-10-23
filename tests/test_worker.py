import json
import pytest
from app.repository import Repository
from app.service import FactoryService
from app.services.accounts import AccountService
from app.services.agents import AgentRegistry
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService
from app.worker import WorkerLoop


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_PBKDF2_ITERATIONS', '1000')
    repo = Repository(str(tmp_path / 'worker.db'))
    accounts = AccountService(repo)
    # repository-intake's contract requires the REPOSITORY_INTAKE role
    # specifically -- not ACE_ANALYST -- since that is the agent every test
    # here dispatches.
    owner = accounts.sign_up({'name': 'Pipeline Owner', 'email': 'owner@example.com',
                              'password': 'a-long-enough-password', 'roles': ['REPOSITORY_INTAKE']}, ('GOVERNANCE_ADMIN',))
    job = FactoryService(repo).create_job({'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, owner['email'])
    pipelines = PipelineService(repo)
    workflow = WorkflowOrchestrator(repo)
    agents = AgentRegistry(repo)
    worker = WorkerLoop(repo, agents, pipelines, repo_clone=_NoClone(), workflow=workflow)
    return repo, job, owner, pipelines, workflow, worker


class _NoClone:
    """A repo_clone stub: these tests exercise agent steps, not real clones."""
    def clone(self, *a, **k):
        raise AssertionError('clone should not be called in these tests')

    def push(self, *a, **k):
        raise AssertionError('push should not be called in these tests')


def find(steps, agent_id):
    return next(s for s in steps if s['agent_id'] == agent_id)


def test_process_one_returns_false_when_queue_empty(env):
    *_, worker = env
    assert worker.process_one() is False


def test_process_one_runs_a_real_agent_step_and_advances(tmp_path, env):
    repo, job, owner, pipelines, workflow, worker = env
    ace_dir = tmp_path / 'ace'
    ace_dir.mkdir()
    (ace_dir / 'Orders.msgflow').write_text('<flow><node type="MQInput" id="n1"/></flow>')

    p = pipelines.create(job['id'], str(tmp_path).replace('\\', '/'),
                         {}, ['repository-intake'], owner['email'])
    # No CLONE step was requested (no repo configured), so repository-intake's
    # seeded path must point at the fixture directory directly.
    with repo.connect() as c:
        c.execute("update pipeline_steps set params=? where pipeline_id=? and agent_id='repository-intake'",
                  (json.dumps({'path': str(ace_dir), 'branch': 'main'}), p['id']))
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)

    claimed = worker.process_one()
    assert claimed is True
    detail = pipelines.detail(p['id'])
    step = find(detail['steps'], 'repository-intake')
    assert step['status'] == 'COMPLETED'
    outputs = json.loads(step['outputs'])
    assert 'intake_manifest' in outputs and outputs['_agent_status'] == 'COMPLETED'
    assert detail['status'] == 'COMPLETED'


def test_process_one_marks_step_failed_on_agent_error(tmp_path, env):
    repo, job, owner, pipelines, workflow, worker = env
    p = pipelines.create(job['id'], str(tmp_path).replace('\\', '/'), {}, ['repository-intake'], owner['email'])
    with repo.connect() as c:
        c.execute("update pipeline_steps set params=? where pipeline_id=? and agent_id='repository-intake'",
                  (json.dumps({'path': str(tmp_path / 'does-not-exist'), 'branch': 'main'}), p['id']))
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)

    worker.process_one()
    detail = pipelines.detail(p['id'])
    step = find(detail['steps'], 'repository-intake')
    assert step['status'] == 'FAILED'
    assert 'repository_not_accessible' in step['error']
    assert detail['status'] == 'FAILED'


def test_worker_only_claims_pipeline_step_activity(env):
    repo, job, owner, pipelines, workflow, worker = env
    # Something external scheduled a different activity on the same queue --
    # the worker must leave it alone rather than claiming and mis-processing it.
    workflow.schedule(job['id'], 'some-other-activity', {'note': 'not mine'})
    claimed = worker.process_one()
    assert claimed is False
    row = repo.one("select status from workflow_queue where activity='some-other-activity'")
    assert row['status'] == 'READY'


def test_roles_are_re_read_at_dispatch_time(tmp_path, env):
    """A role granted after pipeline creation must be honoured, not the role
    snapshot from when the pipeline was made."""
    repo, job, owner, pipelines, workflow, worker = env
    # Demote the owner below what ace-analysis requires, then create the
    # pipeline anyway (create() does not itself check dispatch-time roles).
    with repo.connect() as c:
        c.execute("update users set granted_roles='MIGRATION_VIEWER' where id=?", (owner['id'],))
    p = pipelines.create(job['id'], str(tmp_path).replace('\\', '/'), {}, ['repository-intake'], owner['email'])
    with repo.connect() as c:
        c.execute("update pipeline_steps set params=? where pipeline_id=? and agent_id='repository-intake'",
                  (json.dumps({'path': str(tmp_path), 'branch': 'main'}), p['id']))
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    worker.process_one()
    step = find(pipelines.detail(p['id'])['steps'], 'repository-intake')
    assert step['status'] == 'FAILED' and 'required_agent_role_missing' in step['error']

    # Grant the role back -- a genuinely later change -- and retry the same step.
    with repo.connect() as c:
        c.execute("update users set granted_roles='REPOSITORY_INTAKE' where id=?", (owner['id'],))
    pipelines.retry_step(step['id'])
    pipelines.advance(p['id'], workflow)
    worker.process_one()
    step = find(pipelines.detail(p['id'])['steps'], 'repository-intake')
    assert step['status'] == 'COMPLETED'


def test_an_operator_owner_dispatches_with_every_operator_role(env):
    """The default role stands for the operator roles, so a step must run with them."""
    repo, job, owner, pipelines, workflow, worker = env
    with repo.connect() as c:
        c.execute("update users set granted_roles='MIGRATION_OPERATOR' where id=?", (owner['id'],))
    _, roles = worker._principal(owner['email'])
    assert {'REPOSITORY_INTAKE', 'MIGRATION_PLANNER', 'CODE_GENERATOR'} <= set(roles)
