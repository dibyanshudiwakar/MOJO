import pytest
from app.agents.framework_baseline import FrameworkBaselineAgent


def test_baseline_produces_a_template_the_g1_approval_can_bind(repo, job, framework_repo):
    result = FrameworkBaselineAgent(repo).run(job['id'], params={'path': str(framework_repo)})
    assert result.status == 'COMPLETED'
    assert result.outputs['conflicts'] == []
    row = repo.one('select * from framework_baselines where id=?', (result.outputs['framework_template'],))
    assert row['status'] == 'PENDING_APPROVAL'
    # A framework baseline hashes its template markdown, so this equality is what
    # lets one G1 approval cover both the evidence row and the artefact.
    ref, = result.evidence
    assert ref.content_hash == row['content_hash']
    assert result.metrics['golden_status'] == 'PASS'


def test_a_missing_standard_becomes_a_clarification(repo, job, tmp_path):
    """An ambiguous baseline would be encoded into every generated service."""
    bare = tmp_path / 'bare'
    bare.mkdir()
    (bare / 'pom.xml').write_text('<project><dependencies/></project>')
    result = FrameworkBaselineAgent(repo).run(job['id'], params={'path': str(bare)})
    assert result.status == 'CLARIFICATION_REQUIRED'
    assert 'security_standard_not_detected' in result.outputs['conflicts']
    assert len(result.clarifications) == len(result.outputs['conflicts'])
    assert all(x.owner == 'JAVA_ARCHITECT' for x in result.clarifications)


def test_baseline_requires_a_path(repo, job):
    with pytest.raises(ValueError, match='missing_param:path'):
        FrameworkBaselineAgent(repo).run(job['id'])


def test_baseline_refuses_an_unreadable_reference_repository(repo, job, tmp_path):
    with pytest.raises(ValueError, match='framework_repository_not_accessible'):
        FrameworkBaselineAgent(repo).run(job['id'], params={'path': str(tmp_path / 'absent')})
