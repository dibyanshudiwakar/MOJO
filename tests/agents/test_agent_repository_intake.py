import json
import pytest
from app.agents.repository_intake import RepositoryIntakeAgent


def test_intake_records_a_manifest_whose_hash_the_evidence_shares(repo, job, ace_repo):
    result = RepositoryIntakeAgent(repo).run(job['id'], params={'path': str(ace_repo)})
    assert result.status == 'COMPLETED'
    manifest = repo.one('select * from intake_manifests where id=?', (result.outputs['intake_manifest'],))
    assert manifest['job_id'] == job['id']
    assert 'Orders.msgflow' in json.loads(manifest['inventory'])['flows']
    # An approval binding this evidence id must also bind the manifest, which
    # only holds while the two hashes are equal.
    ref, = result.evidence
    assert ref.content_hash == manifest['content_hash']
    assert result.outputs['gaps'] == []


def test_intake_reports_a_gap_rather_than_raising_a_clarification(repo, job, tmp_path):
    """Its contract grants repository.read and evidence.write, not clarification.create."""
    empty = tmp_path / 'empty'
    empty.mkdir()
    result = RepositoryIntakeAgent(repo).run(job['id'], params={'path': str(empty)})
    assert result.outputs['gaps'] == ['no_message_flows_detected']
    assert result.clarifications == ()


def test_intake_requires_a_path(repo, job):
    with pytest.raises(ValueError, match='missing_param:path'):
        RepositoryIntakeAgent(repo).run(job['id'])


def test_intake_refuses_an_unreadable_repository(repo, job, tmp_path):
    with pytest.raises(ValueError, match='repository_not_accessible'):
        RepositoryIntakeAgent(repo).run(job['id'], params={'path': str(tmp_path / 'absent')})
