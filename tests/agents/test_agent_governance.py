from app.agents.governance import GovernanceAgent
from app.services.factory_v3 import AuditLedger


def test_an_ungated_transition_is_allowed_from_the_start(repo, job):
    result = GovernanceAgent(repo).run(job['id'], params={'target_state': 'INTAKE_VALIDATED'})
    assert result.outputs['allow_deny_decision'] == {'INTAKE_VALIDATED': 'ALLOW'}
    assert result.outputs['approval_requirement'] == {}
    assert result.next_state == 'INTAKE_VALIDATED'


def test_a_gate_reports_its_role_quorum_and_shortfall(repo, job, state):
    state(job['id'], 'DORMANCY_ASSESSED')
    result = GovernanceAgent(repo).run(job['id'], params={'target_state': 'FRAMEWORK_BASELINED'})
    assert result.outputs['allow_deny_decision'] == {'FRAMEWORK_BASELINED': 'DENY'}
    requirement = result.outputs['approval_requirement']['FRAMEWORK_BASELINED']
    assert requirement['gate'] == 'G0'
    assert requirement['required_role'] == 'SERVICE_OWNER'
    assert requirement['allowed_decisions'] == ['PROCEED']
    assert requirement['quorum'] == 1 and requirement['approvals'] == 0
    reasons = result.outputs['evidence_requirement']['FRAMEWORK_BASELINED']['reasons']
    assert any('required_gate_approval_missing:G0:quorum_0_of_1' in r for r in reasons)


def test_a_signed_gate_flips_the_decision(repo, job, state, approve):
    state(job['id'], 'DORMANCY_ASSESSED')
    approve(job['id'], 'G0', 'PROCEED')
    result = GovernanceAgent(repo).run(job['id'], params={'target_state': 'FRAMEWORK_BASELINED'})
    assert result.outputs['allow_deny_decision'] == {'FRAMEWORK_BASELINED': 'ALLOW'}
    assert result.outputs['approval_requirement']['FRAMEWORK_BASELINED']['approvals'] == 1


def test_the_configured_quorum_is_reported_not_assumed(repo, job, state, approve):
    from app.services.platform_v7 import ApprovalWorkspaceService
    ApprovalWorkspaceService(repo).configure_gate({'gate': 'G0', 'quorum': 2}, 'admin')
    state(job['id'], 'DORMANCY_ASSESSED')
    approve(job['id'], 'G0', 'PROCEED', identity='owner-one@example')
    result = GovernanceAgent(repo).run(job['id'], params={'target_state': 'FRAMEWORK_BASELINED'})
    requirement = result.outputs['approval_requirement']['FRAMEWORK_BASELINED']
    assert requirement['quorum'] == 2 and requirement['approvals'] == 1
    assert result.outputs['allow_deny_decision']['FRAMEWORK_BASELINED'] == 'DENY'


def test_promotion_reports_the_missing_evidence_bundle(repo, job, state, approve):
    """The approval says who agreed; the bundle says what they agreed about."""
    state(job['id'], 'READY_FOR_PROMOTION')
    approve(job['id'], 'G5', 'PROMOTE')
    result = GovernanceAgent(repo).run(job['id'], params={'target_state': 'COMPLETED'})
    assert result.outputs['allow_deny_decision']['COMPLETED'] == 'DENY'
    reasons = result.outputs['evidence_requirement']['COMPLETED']['reasons']
    assert any('differential_package_missing' in r for r in reasons)


def test_an_open_clarification_blocks_every_forward_transition(repo, job):
    from app.repository import now, uid
    with repo.connect() as c:
        repo.insert(c, 'clarifications', {'id': uid(), 'job_id': job['id'], 'stage': 'T',
                                          'question': 'q', 'owner': 'ACE_SME', 'impact': 'i',
                                          'evidence_ref': '', 'status': 'OPEN', 'answer': '',
                                          'created_at': now(), 'answered_at': '', 'answered_by': ''})
    result = GovernanceAgent(repo).run(job['id'])
    assert result.outputs['allow_deny_decision']['INTAKE_VALIDATED'] == 'DENY'
    # STOPPED and FAILED stay reachable, as the state machine allows.
    assert result.outputs['allow_deny_decision']['STOPPED'] == 'ALLOW'
    assert result.outputs['allow_deny_decision']['FAILED'] == 'ALLOW'


def test_evaluating_every_legal_target_when_none_is_named(repo, job):
    result = GovernanceAgent(repo).run(job['id'])
    assert set(result.outputs['allow_deny_decision']) == {'INTAKE_VALIDATED', 'STOPPED', 'FAILED'}


def test_the_evaluation_is_recorded_in_the_audit_ledger(repo, job):
    GovernanceAgent(repo).run(job['id'], params={'target_state': 'INTAKE_VALIDATED'})
    entry = repo.one("select * from audit_ledger where action='GOVERNANCE_EVALUATED'")
    assert entry and entry['job_id'] == job['id']
    assert AuditLedger(repo).verify()
