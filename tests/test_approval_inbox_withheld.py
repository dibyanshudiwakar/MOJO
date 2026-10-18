"""A pending approval the viewer cannot act on must not look like a clear queue.

``inbox_for`` filters by role, project scope and environment. Its only output
was the filtered list, so an account holding none of the gate approver roles saw
"No pending approvals" on the dashboard and the inbox while three packages
waited -- the same screen as a genuinely empty queue. ``withheld_for`` reports
what was filtered out and why, which is what lets the page say so.
"""
import pytest

from app.repository import Repository, now, uid
from app.service import FactoryService
from app.services.auth import Principal
from app.services.platform_v7 import ApprovalWorkspaceService


@pytest.fixture
def pending_g2(tmp_path):
    repo = Repository(str(tmp_path / 'inbox.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc', 'rollbackPointer': 't'}, 'tester')
    with repo.connect() as c:
        repo.insert(c, 'approval_packages', {
            'id': uid(), 'job_id': job['id'], 'gate': 'G2', 'status': 'PENDING_APPROVAL',
            'payload': '{}', 'content_hash': 'h', 'artefact_bindings': '{}', 'conditions': '[]',
            'created_at': now()})
    return ApprovalWorkspaceService(repo)


def _who(*roles, projects=('GLOBAL',), environments=('DEV',)):
    return Principal('someone@example.com', tuple(roles), tuple(projects), tuple(environments))


def test_a_missing_role_is_reported_not_hidden(pending_g2):
    viewer = _who('GOVERNANCE_ADMIN')
    assert pending_g2.inbox_for(viewer) == []
    assert pending_g2.withheld_for(viewer) == [
        {'gate': 'G2', 'required_role': 'ACE_SME', 'reason': 'role_not_held', 'count': 1}]


def test_holding_the_role_moves_it_into_the_inbox(pending_g2):
    viewer = _who('ACE_SME')
    assert len(pending_g2.inbox_for(viewer)) == 1
    assert pending_g2.withheld_for(viewer) == []


def test_an_out_of_scope_environment_is_reported_with_its_reason(pending_g2):
    viewer = _who('ACE_SME', environments=('PROD',))
    assert pending_g2.inbox_for(viewer) == []
    assert [w['reason'] for w in pending_g2.withheld_for(viewer)] == ['environment']
