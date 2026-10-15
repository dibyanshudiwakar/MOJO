import json
import pytest
from app.repository import Repository
from app.service import FactoryService
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService, PipelineError


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'pipe.db'))
    job = FactoryService(repo).create_job({'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, PipelineService(repo), WorkflowOrchestrator(repo)


def by_key(steps, key, agent_id=None):
    for s in steps:
        if key == 'kind' and s['kind'] == agent_id:
            return s
    return None


def find_agent_step(steps, agent_id):
    return next((s for s in steps if s['agent_id'] == agent_id), None)


def find_clone_step(steps, role):
    for s in steps:
        if s['kind'] == 'CLONE' and json.loads(s['params']).get('role') == role:
            return s
    return None


def test_selectable_agents_excludes_orchestrator(env):
    _, _, pipelines, _ = env
    ids = {a['agent_id'] for a in pipelines.selectable_agents()}
    assert 'migration-orchestrator' not in ids
    assert 'ace-analysis' in ids and 'code-generation' in ids
    assert len(ids) == 13


def test_impact_prunes_dependents_with_clear_reasons(env):
    _, _, pipelines, _ = env
    selected = ['repository-intake', 'ace-analysis', 'migration-specification', 'planning', 'code-generation']
    # framework-baseline is deliberately left out.
    out = pipelines.impact(selected)
    runnable = set(out['runnable'])
    skipped = {x['agent_id']: x['reason'] for x in out['skipped']}
    assert 'repository-intake' in runnable and 'ace-analysis' in runnable
    # framework-baseline is optional: planning no longer depends on it,
    # so planning and code-generation remain runnable even without it.
    assert 'planning' in runnable and 'code-generation' in runnable
    assert skipped['framework-baseline'] == 'explicitly disabled'


def test_impact_matches_what_create_builds(env):
    _, job, pipelines, _ = env
    selected = ['repository-intake', 'ace-analysis']
    preview = pipelines.impact(selected)
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         selected, 'tester@example.com')
    runnable_steps = {s['agent_id'] for s in p['steps'] if s['status'] != 'SKIPPED' and s['agent_id']}
    skipped_steps = {s['agent_id'] for s in p['steps'] if s['status'] == 'SKIPPED'}
    assert runnable_steps == set(preview['runnable'])
    assert skipped_steps == {x['agent_id'] for x in preview['skipped']}


def test_create_wires_clone_step_before_repo_consuming_agents(env):
    _, job, pipelines, _ = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake', 'ace-analysis'], 'tester@example.com')
    clone = find_clone_step(p['steps'], 'ACE')
    intake = find_agent_step(p['steps'], 'repository-intake')
    analysis = find_agent_step(p['steps'], 'ace-analysis')
    assert clone is not None
    assert clone['id'] in json.loads(intake['depends_on'])
    assert clone['id'] in json.loads(analysis['depends_on'])
    assert json.loads(intake['params'])['path'] == 'workspace/orders/ace'


def test_create_adds_delivery_step_only_when_pushing(env):
    _, job, pipelines, _ = env
    selected = ['repository-intake', 'ace-analysis', 'framework-baseline', 'migration-specification', 'planning', 'code-generation']
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True},
                          'FRAMEWORK': {'repo_url': 'https://github.com/org/fw.git', 'branch': 'main', 'enabled': True},
                          'TARGET': {'repo_url': 'https://github.com/org/target.git', 'branch': 'main', 'push_mode': 'PUSH_TO_REPO'}},
                         selected, 'tester@example.com')
    delivery = [s for s in p['steps'] if s['kind'] == 'DELIVERY']
    codegen = find_agent_step(p['steps'], 'code-generation')
    assert len(delivery) == 1
    assert delivery[0]['id'] not in json.loads(codegen['depends_on'])
    assert codegen['id'] in json.loads(delivery[0]['depends_on'])

    p2 = pipelines.create(job['id'], 'workspace/orders2',
                          {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                          ['repository-intake'], 'tester@example.com')
    assert not [s for s in p2['steps'] if s['kind'] == 'DELIVERY']


def test_run_schedules_only_dependency_free_steps(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake', 'ace-analysis'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')
    intake = find_agent_step(p['steps'], 'repository-intake')
    refreshed = {s['id']: s['status'] for s in pipelines.detail(p['id'])['steps']}
    assert refreshed[clone['id']] == 'QUEUED'
    assert refreshed[intake['id']] == 'PENDING'


def test_advance_unblocks_dependents_and_completes_pipeline(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')
    intake = find_agent_step(p['steps'], 'repository-intake')

    pipelines.mark_step(clone['id'], 'RUNNING')
    pipelines.mark_step(clone['id'], 'COMPLETED', outputs={'path': 'workspace/orders/ace'})
    pipelines.advance(p['id'], workflow)
    after = {s['id']: s['status'] for s in pipelines.detail(p['id'])['steps']}
    assert after[intake['id']] == 'QUEUED'

    pipelines.mark_step(intake['id'], 'RUNNING')
    pipelines.mark_step(intake['id'], 'COMPLETED', outputs={'intake_manifest': 'm1', 'gaps': []})
    pipelines.advance(p['id'], workflow)
    final = pipelines.detail(p['id'])
    assert final['status'] == 'COMPLETED'


def test_failed_step_does_not_schedule_its_dependents(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake', 'ace-analysis'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')
    intake = find_agent_step(p['steps'], 'repository-intake')

    pipelines.mark_step(clone['id'], 'RUNNING')
    pipelines.mark_step(clone['id'], 'FAILED', error='git_command_failed:host unreachable')
    pipelines.advance(p['id'], workflow)
    after = {s['id']: s['status'] for s in pipelines.detail(p['id'])['steps']}
    assert after[intake['id']] == 'PENDING'
    assert pipelines.detail(p['id'])['status'] == 'FAILED'


def test_transitively_blocked_step_does_not_leave_pipeline_running_forever(env):
    """Reproduces exactly what was found stuck in a real database: a CLONE
    failure blocks repository-intake (direct dependency, one hop -- already
    covered above), but dormancy-intelligence depends on repository-intake,
    not on the clone step directly. A one-hop "is my dependency FAILED"
    check sees repository-intake sitting at PENDING (not FAILED) and wrongly
    concludes dormancy-intelligence might still run, leaving the whole
    pipeline at RUNNING forever with nothing left that can ever progress.
    """
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake', 'dormancy-intelligence'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')

    pipelines.mark_step(clone['id'], 'RUNNING')
    pipelines.mark_step(clone['id'], 'FAILED', error='git_command_failed:User cancelled dialog')
    pipelines.advance(p['id'], workflow)

    final = pipelines.detail(p['id'])
    statuses = {s['agent_id'] or s['kind']: s['status'] for s in final['steps']}
    assert statuses['repository-intake'] == 'PENDING'
    assert statuses['dormancy-intelligence'] == 'PENDING'
    assert final['status'] == 'FAILED', (
        'pipeline must finalize to FAILED once nothing can make further '
        'progress, even though dormancy-intelligence is two hops from the '
        'actual failure rather than depending on it directly')


def test_pause_retracts_queued_step_and_resume_continues(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')
    intake = find_agent_step(p['steps'], 'repository-intake')
    assert pipelines.detail(p['id'])['steps'][0]['status'] == 'QUEUED'

    paused = pipelines.pause(p['id'], workflow)
    assert paused['status'] == 'PAUSED'
    clone_after = next(s for s in paused['steps'] if s['id'] == clone['id'])
    assert clone_after['status'] == 'PENDING' and clone_after['queue_id'] == ''
    # advance() must not schedule anything, or silently re-finalize, while paused.
    pipelines.advance(p['id'], workflow)
    assert pipelines.detail(p['id'])['status'] == 'PAUSED'
    assert pipelines.detail(p['id'])['steps'][0]['status'] == 'PENDING'

    resumed = pipelines.resume(p['id'], workflow)
    assert resumed['status'] == 'RUNNING'
    clone_resumed = next(s for s in resumed['steps'] if s['id'] == clone['id'])
    assert clone_resumed['status'] == 'QUEUED'

    pipelines.mark_step(clone['id'], 'RUNNING')
    pipelines.mark_step(clone['id'], 'COMPLETED', outputs={'path': 'workspace/orders/ace'})
    pipelines.advance(p['id'], workflow)
    pipelines.mark_step(intake['id'], 'RUNNING')
    pipelines.mark_step(intake['id'], 'COMPLETED', outputs={'intake_manifest': 'm1', 'gaps': []})
    pipelines.advance(p['id'], workflow)
    assert pipelines.detail(p['id'])['status'] == 'COMPLETED'


def test_cancel_marks_unstarted_steps_cancelled_and_finalizes_immediately(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')
    intake = find_agent_step(p['steps'], 'repository-intake')

    cancelled = pipelines.cancel(p['id'], workflow)
    assert cancelled['status'] == 'CANCELLED' and cancelled['completed_at']
    statuses = {s['id']: s['status'] for s in cancelled['steps']}
    assert statuses[clone['id']] == 'CANCELLED'  # was QUEUED
    assert statuses[intake['id']] == 'CANCELLED'  # was PENDING


def test_advance_after_cancel_does_not_resurrect_a_late_finishing_step(env):
    """A step already RUNNING when Stop is pressed cannot be preempted -- it
    keeps executing and the worker calls advance() once it finishes. That must
    not flip an already-CANCELLED pipeline back to COMPLETED or FAILED.
    """
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')
    pipelines.mark_step(clone['id'], 'RUNNING')

    pipelines.cancel(p['id'], workflow)
    assert pipelines.detail(p['id'])['status'] == 'CANCELLED'

    pipelines.mark_step(clone['id'], 'COMPLETED', outputs={'path': 'workspace/orders/ace'})
    pipelines.advance(p['id'], workflow)
    assert pipelines.detail(p['id'])['status'] == 'CANCELLED'


def test_pause_resume_cancel_reject_the_wrong_starting_status(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake'], 'tester@example.com')
    with pytest.raises(PipelineError, match='pipeline_not_running'):
        pipelines.pause(p['id'], workflow)
    with pytest.raises(PipelineError, match='pipeline_not_paused'):
        pipelines.resume(p['id'], workflow)
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    pipelines.cancel(p['id'], workflow)
    with pytest.raises(PipelineError, match='pipeline_not_cancellable'):
        pipelines.cancel(p['id'], workflow)


def test_retry_step_only_works_on_failed(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake'], 'tester@example.com')
    pipelines.confirm(p['id'])
    p = pipelines.run(p['id'], workflow)
    clone = find_clone_step(p['steps'], 'ACE')
    with pytest.raises(PipelineError, match='pipeline_step_not_failed'):
        pipelines.retry_step(clone['id'])
    pipelines.mark_step(clone['id'], 'FAILED', error='boom')
    retried = pipelines.retry_step(clone['id'])
    assert retried['status'] == 'PENDING' and retried['error'] == ''


def test_resolved_params_forwards_outputs_between_steps(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake', 'ace-analysis', 'migration-specification'], 'tester@example.com')
    analysis = find_agent_step(p['steps'], 'ace-analysis')
    spec = find_agent_step(p['steps'], 'migration-specification')
    pipelines.mark_step(analysis['id'], 'COMPLETED', outputs={'analysis_document': 'ana-123', 'semantic_model': 'h', 'ambiguities': []})
    resolved = pipelines.resolved_params(pipelines.repo.one('select * from pipeline_steps where id=?', (spec['id'],)))
    assert resolved['analysis_id'] == 'ana-123'


def test_confirm_requires_draft_and_run_requires_ready(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders',
                         {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}},
                         ['repository-intake'], 'tester@example.com')
    with pytest.raises(PipelineError, match='pipeline_not_ready'):
        pipelines.run(p['id'], workflow)
    pipelines.confirm(p['id'])
    with pytest.raises(PipelineError, match='pipeline_not_in_draft'):
        pipelines.confirm(p['id'])
