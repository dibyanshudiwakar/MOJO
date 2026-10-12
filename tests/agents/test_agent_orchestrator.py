"""The orchestrator's autonomy is bounded by governance, not by its own caution.

These tests exist to prove the second half of that claim: that the loop really
does stop at each gate, that it cannot walk a job to COMPLETED without the
signatures and the evidence bundle, and that an operator can halt it in flight.
"""
from app.agents.orchestrator import MigrationOrchestratorAgent
from app.services.factory_v3 import AuditLedger, WorkflowOrchestrator
from tests.agents.conftest import ALL_ROLES


class StubRuntime:
    def __init__(self, name, body):
        self.name, self.body = name, body

    def invoke(self, case):
        return {'adapter': self.name, 'status': 200, 'body': dict(self.body), 'headers': {}}


def _seed(ace_repo, framework_repo, tmp_path, **extra):
    return {'ace_path': str(ace_repo), 'framework_path': str(framework_repo),
            'output_root': str(tmp_path / 'generated'), 'service_name': 'orders',
            'package_name': 'com.example.orders', **extra}


def _run(repo, job, seed, roles=ALL_ROLES):
    return MigrationOrchestratorAgent(repo).run(
        job['id'], identity='svc@example', roles=roles, params=seed)


def test_it_runs_the_early_pipeline_then_stops_at_the_first_gate(repo, job, ace_repo,
                                                                framework_repo, tmp_path):
    result = _run(repo, job, _seed(ace_repo, framework_repo, tmp_path))
    assert result.metrics['final_state'] == 'DORMANCY_ASSESSED'
    assert result.metrics['stop_reason'].startswith('gate_required:FRAMEWORK_BASELINED')
    assert 'required_gate_approval_missing:G0' in result.metrics['stop_reason']
    invoked = [x['agent_id'] for x in result.outputs['commands']]
    assert 'repository-intake' in invoked and 'dormancy-intelligence' in invoked
    assert 'framework-baseline' in invoked
    # It produced artefacts but signed nothing.
    assert repo.one('select count(*) n from intake_manifests where job_id=?', (job['id'],))['n'] == 1
    assert repo.one('select count(*) n from approvals where job_id=?', (job['id'],))['n'] == 0
    assert result.outputs['next_state_proposal'] == 'FRAMEWORK_BASELINED'


def test_a_second_run_does_not_duplicate_the_first_runs_artefacts(repo, job, ace_repo,
                                                                 framework_repo, tmp_path, approve):
    """Otherwise the gate approval would cover an artefact nothing reads any more."""
    seed = _seed(ace_repo, framework_repo, tmp_path)
    _run(repo, job, seed)
    approve(job['id'], 'G0', 'PROCEED')
    _run(repo, job, seed)
    for table in ('intake_manifests', 'dormancy_assessments', 'framework_baselines'):
        assert repo.one('select count(*) n from %s where job_id=?' % table, (job['id'],))['n'] == 1


def test_it_will_not_invoke_an_agent_whose_role_the_principal_lacks(repo, job, ace_repo,
                                                                   framework_repo, tmp_path):
    """Autonomy does not widen the principal's privileges."""
    result = _run(repo, job, _seed(ace_repo, framework_repo, tmp_path),
                  roles=('ORCHESTRATOR', 'GOVERNANCE_ENGINE'))
    assert result.metrics['stop_reason'].startswith('blocked:repository-intake')
    assert 'required_agent_role_missing' in result.metrics['stop_reason']
    assert result.metrics['final_state'] == 'CREATED'


def test_a_missing_seed_input_becomes_a_clarification(repo, job, framework_repo, tmp_path):
    result = _run(repo, job, {'framework_path': str(framework_repo)})
    assert result.status == 'CLARIFICATION_REQUIRED'
    assert result.metrics['stop_reason'] == 'input_required_for:repository-intake'
    assert 'repository-intake' in result.outputs['clarification_request']
    assert result.clarifications[0].owner == 'ORCHESTRATOR'


def test_the_step_budget_is_respected(repo, job, ace_repo, framework_repo, tmp_path):
    result = _run(repo, job, _seed(ace_repo, framework_repo, tmp_path, max_steps=1))
    assert result.metrics['stop_reason'] == 'step_budget_exhausted'
    assert result.metrics['final_state'] == 'CREATED'


def test_a_paused_checkpoint_halts_the_run(repo, job, ace_repo, framework_repo, tmp_path):
    seed = _seed(ace_repo, framework_repo, tmp_path)
    _run(repo, job, seed)
    WorkflowOrchestrator(repo).pause(job['id'])
    halted = _run(repo, job, seed)
    assert halted.metrics['stop_reason'] == 'paused'
    assert halted.metrics['steps'] == 0
    WorkflowOrchestrator(repo).resume(job['id'])
    assert _run(repo, job, seed).metrics['stop_reason'] != 'paused'


def test_a_cancelled_activity_halts_the_run(repo, job, ace_repo, framework_repo, tmp_path):
    workflow = WorkflowOrchestrator(repo)
    queued = workflow.schedule(job['id'], 'MIGRATION')
    workflow.cancel(queued['id'])
    result = _run(repo, job, _seed(ace_repo, framework_repo, tmp_path))
    assert result.metrics['stop_reason'] == 'cancel_requested'
    assert result.metrics['steps'] == 0


def test_proposal_only_mode_moves_nothing(repo, job, ace_repo, framework_repo, tmp_path):
    result = _run(repo, job, _seed(ace_repo, framework_repo, tmp_path,
                                   invoke_agents=False, apply_transition=False))
    assert result.metrics['stop_reason'] == 'transition_not_applied:INTAKE_VALIDATED'
    assert result.metrics['final_state'] == 'CREATED'
    assert repo.one('select count(*) n from intake_manifests where job_id=?', (job['id'],))['n'] == 0


def test_it_cannot_promote_without_the_evidence_bundle(repo, job, state, approve):
    """A signed G5 is not by itself grounds for promotion."""
    state(job['id'], 'READY_FOR_PROMOTION')
    approve(job['id'], 'G5', 'PROMOTE')
    result = _run(repo, job, {})
    assert result.metrics['final_state'] == 'READY_FOR_PROMOTION'
    assert 'differential_package_missing' in result.metrics['stop_reason']


def test_a_full_governed_lifecycle_reaches_completed(repo, job, ace_repo, framework_repo,
                                                    tmp_path, approve, evidence, monkeypatch):
    """End to end: agents produce, approvals bind their evidence, gates open.

    The interleaving is the point -- each gate needs a signature over an artefact
    an agent produced, and the run cannot continue until it exists.
    """
    monkeypatch.setenv('FACTORY_REMEDIATION_CONFIDENCE_THRESHOLD', '0.1')
    identical = {'id': 1}
    seed = _seed(ace_repo, framework_repo, tmp_path,
                 ace_adapter=StubRuntime('ace', identical),
                 spring_adapter=StubRuntime('spring-boot', identical))

    def latest(kind):
        return repo.one('select * from evidence where job_id=? and kind=? order by created_at desc limit 1',
                        (job['id'], kind))

    _run(repo, job, seed)                                  # -> stops at G0
    approve(job['id'], 'G0', 'PROCEED')
    _run(repo, job, seed)                                  # -> stops at G1
    framework = latest('FRAMEWORK_TEMPLATE')
    approve(job['id'], 'G1', 'APPROVE', framework['id'], framework['content_hash'])
    _run(repo, job, seed)                                  # -> stops at G2
    for kind in ('ACE_ANALYSIS', 'MIGRATION_SPECIFICATION'):
        ref = latest(kind)
        approve(job['id'], 'G2', 'APPROVE', ref['id'], ref['content_hash'])
    _run(repo, job, seed)                                  # -> plans, stops at G3
    plan = repo.one('select * from migration_plans where job_id=? order by created_at desc limit 1',
                    (job['id'],))
    # The planner holds plan.write, not evidence.write, so attaching the plan to
    # the G3 decision is the approver's act.
    bound = evidence.register(job['id'], 'MIGRATION_PLAN', plan['version'],
                              'plan://' + plan['id'], plan['tasks'], 'product-owner@example')
    assert bound['content_hash'] == plan['content_hash']
    approve(job['id'], 'G3', 'APPROVE', bound['id'], bound['content_hash'])
    result = _run(repo, job, seed)                         # -> generates, replays, compares
    assert result.metrics['final_state'] == 'VARIANCE_REVIEW', result.metrics['stop_reason']
    assert 'G4' in result.metrics['stop_reason']
    # Identical runtime results, so nothing is left open to disposition.
    assert repo.one("select count(*) n from differential_evidence where job_id=? and"
                    " disposition='OPEN'", (job['id'],))['n'] == 0
    approve(job['id'], 'G4', 'ACCEPT_EXCEPTION')
    result = _run(repo, job, seed)                         # -> stops at G5
    assert result.metrics['final_state'] == 'READY_FOR_PROMOTION', result.metrics['stop_reason']
    approve(job['id'], 'G5', 'PROMOTE')
    evidence.snapshot(job['id'])                           # the rollback precondition
    result = _run(repo, job, seed)
    assert result.metrics['final_state'] == 'COMPLETED', result.metrics['stop_reason']
    assert repo.one('select state from jobs where id=?', (job['id'],))['state'] == 'COMPLETED'
    assert AuditLedger(repo).verify()
