import json
import pytest
from app.agents.rca import RCAAgent
from app.agents.remediation import RemediationAgent, THRESHOLD_ENV
from app.agents.variance_analysis import VarianceAnalysisAgent


@pytest.fixture
def root_cause(repo, job, planned, executed_package):
    """A root cause reproducible across two cases, so its confidence can clear the bar."""
    for case in ('CASE-001', 'CASE-002'):
        VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': executed_package(job['id'], case_id=case)})
    return RCAAgent(repo).run(job['id']).outputs['rca_hypotheses'][0]


def test_an_eligible_cause_yields_a_recorded_patch_plan(repo, job, root_cause, monkeypatch):
    monkeypatch.setenv(THRESHOLD_ENV, '0.1')
    result = RemediationAgent(repo).run(job['id'], params={'root_cause_id': root_cause['root_cause_id']})
    assert result.status == 'COMPLETED'
    row = repo.one('select * from remediations where id=?', (result.outputs['patch_plan'],))
    assert row['status'] == 'PROPOSED'
    assert row['rollback_pointer'] == 'tag/v1'
    assert json.loads(row['regression_tests']) == result.outputs['tests']
    assert result.outputs['code_changes'][0]['intent']
    ref, = result.evidence
    assert ref.metadata['threshold'] == 0.1


def test_the_threshold_is_configuration_not_a_parameter(repo, job, root_cause, monkeypatch):
    """A remediation that could pick its own bar is not judged against one."""
    monkeypatch.setenv(THRESHOLD_ENV, '0.99')
    result = RemediationAgent(repo).run(job['id'], params={
        'root_cause_id': root_cause['root_cause_id'], 'threshold': 0.0, 'confidence': 1.0})
    assert result.status == 'CLARIFICATION_REQUIRED'
    assert result.metrics['threshold'] == 0.99
    assert result.outputs['patch_plan']['status'] == 'CLARIFICATION_REQUIRED'
    assert repo.one('select count(*) n from remediations where job_id=?', (job['id'],))['n'] == 0


def test_an_unbounded_impact_is_not_eligible(repo, job, root_cause, monkeypatch):
    monkeypatch.setenv(THRESHOLD_ENV, '0.1')
    result = RemediationAgent(repo).run(job['id'], params={
        'root_cause_id': root_cause['root_cause_id'], 'impact_bound': 0})
    assert result.status == 'CLARIFICATION_REQUIRED'
    assert 'bounded_impact' in result.metrics['unmet']


def test_a_prohibited_dependency_blocks_a_direct_fix(repo, job, root_cause, monkeypatch):
    monkeypatch.setenv(THRESHOLD_ENV, '0.1')
    result = RemediationAgent(repo).run(job['id'], params={
        'root_cause_id': root_cause['root_cause_id'], 'prohibited_dependency': True})
    assert result.status == 'CLARIFICATION_REQUIRED'
    assert result.metrics['route'] == 'CLARIFICATION_OR_ACE_INSPECTION'


def test_remediation_writes_no_source(repo, job, root_cause, monkeypatch, tmp_path):
    """Generated code comes from an approved plan under G3, with provenance."""
    monkeypatch.setenv(THRESHOLD_ENV, '0.1')
    before = sorted(p.name for p in tmp_path.iterdir())
    RemediationAgent(repo).run(job['id'], params={'root_cause_id': root_cause['root_cause_id']})
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert repo.one('select count(*) n from generated_artifacts where job_id=?', (job['id'],))['n'] == 0


def test_remediation_with_nothing_to_remediate_completes_and_a_named_cause_must_exist(repo, job):
    result = RemediationAgent(repo).run(job['id'])
    assert result.status == 'COMPLETED' and result.metrics['reason'] == 'no_root_causes'
    with pytest.raises(ValueError, match='root_cause_not_found'):
        RemediationAgent(repo).run(job['id'], params={'root_cause_id': 'nope'})


def test_a_job_with_no_rollback_pointer_does_not_fail_the_step(repo, job, root_cause, monkeypatch):
    """Jobs created from the wizard have none; the step used to die on 'remediation_requires_pattern_tests_and_rollback'."""
    monkeypatch.setenv(THRESHOLD_ENV, '0.1')
    with repo.connect() as c:
        c.execute("update jobs set rollback_pointer='' where id=?", (job['id'],))
    result = RemediationAgent(repo).run(job['id'], params={'root_cause_id': root_cause['root_cause_id']})
    assert result.status == 'COMPLETED'
    row = repo.one('select rollback_pointer from remediations where id=?', (result.outputs['patch_plan'],))
    assert row['rollback_pointer'] == 'none:no-source-changed', 'nothing was edited, so there is nothing to undo'


def test_a_job_pointer_is_kept_when_it_has_one(repo, job, root_cause, monkeypatch):
    monkeypatch.setenv(THRESHOLD_ENV, '0.1')
    result = RemediationAgent(repo).run(job['id'], params={'root_cause_id': root_cause['root_cause_id']})
    row = repo.one('select rollback_pointer from remediations where id=?', (result.outputs['patch_plan'],))
    assert row['rollback_pointer'] == 'tag/v1'
