import json
import pytest
from app.repository import Repository
from app.services.agent_admin import AgentAdminService, AgentAdminError

NEW_AGENT = {
    'agent_id': 'test-fixture-agent', 'display_name': 'Test Fixture Agent', 'version': '1.0.0',
    'description': 'Created by a test.', 'required_role': 'TEST_ENGINEER',
    'accepted_inputs': ['x'], 'produced_outputs': ['y'], 'tool_allowlist': ['evidence.write'],
    'dependencies': [], 'status': 'APPROVED',
}


@pytest.fixture
def repo(tmp_path):
    return Repository(str(tmp_path / 'admin.db'))


@pytest.fixture
def admin(repo, tmp_path, monkeypatch):
    svc = AgentAdminService(repo)
    fixture_catalogue = tmp_path / 'catalogue.json'
    fixture_catalogue.write_text(json.dumps({'agents': []}), encoding='utf-8')
    svc.catalogue_path = fixture_catalogue
    return svc


def test_create_new_agent_is_live_immediately(admin, repo):
    result = admin.create_or_update(dict(NEW_AGENT), 'admin@example.com')
    assert result['agent_id'] == 'test-fixture-agent'
    assert result['origin'] == 'RUNTIME'
    assert result['approved_by'] == 'admin@example.com'
    row = repo.one('select * from agent_registry where agent_id=?', ('test-fixture-agent',))
    assert row is not None and row['enabled'] == 1


def test_create_mirrors_to_catalogue_file(admin):
    result = admin.create_or_update(dict(NEW_AGENT), 'admin@example.com')
    assert result['catalogue_file_synced'] is True
    data = json.loads(admin.catalogue_path.read_text(encoding='utf-8'))
    assert any(a['agent_id'] == 'test-fixture-agent' for a in data['agents'])


def test_update_existing_agent_preserves_name_and_updates_fields(admin, repo):
    admin.create_or_update(dict(NEW_AGENT), 'admin@example.com')
    updated = dict(NEW_AGENT, description='Updated description.', required_role='RCA_ANALYST')
    result = admin.create_or_update(updated, 'second-admin@example.com')
    assert result['description'] == 'Updated description.'
    assert result['required_role'] == 'RCA_ANALYST'
    assert result['approved_by'] == 'second-admin@example.com'
    assert repo.one('select count(*) n from agent_registry where agent_id=?', ('test-fixture-agent',))['n'] == 1


def test_rejects_reserved_agent_id_and_missing_fields(admin):
    with pytest.raises(AgentAdminError, match='agent_id_reserved'):
        admin.create_or_update({**NEW_AGENT, 'agent_id': 'migration-orchestrator'}, 'admin@example.com')
    with pytest.raises(AgentAdminError, match='missing_'):
        admin.create_or_update({**NEW_AGENT, 'description': ''}, 'admin@example.com')
    with pytest.raises(AgentAdminError, match='dependencies_must_be_a_list'):
        admin.create_or_update({**NEW_AGENT, 'dependencies': 'ace-analysis'}, 'admin@example.com')


def test_file_sync_failure_does_not_block_the_live_write(admin, repo):
    """The hardened container runs read-only; the live change must still land."""
    class ReadOnlyPath:
        def exists(self): return False
        def write_text(self, *a, **k): raise OSError('read-only filesystem')
    admin.catalogue_path = ReadOnlyPath()
    result = admin.create_or_update(dict(NEW_AGENT), 'admin@example.com')
    assert result['catalogue_file_synced'] is False
    assert repo.one('select * from agent_registry where agent_id=?', ('test-fixture-agent',)) is not None


def test_runtime_origin_row_survives_reseed_without_crashing(tmp_path):
    """A RUNTIME row must not be re-seeded or drift-checked against the file
    on the next Repository.init() -- that used to crash the whole app the
    moment the database and the catalogue file disagreed for any reason."""
    db_path = str(tmp_path / 'restart.db')
    repo = Repository(db_path)
    admin = AgentAdminService(repo)
    admin.catalogue_path = tmp_path / 'unrelated-catalogue.json'
    admin.catalogue_path.write_text(json.dumps({'agents': []}), encoding='utf-8')

    # Directly seed a RUNTIME row whose contract_hash could never match
    # anything Repository._catalogue() would compute, simulating an admin
    # edit that has genuinely diverged from the shipped catalogue file.
    with repo.connect() as c:
        repo.insert(c, 'agent_registry', {
            'name': 'Custom Agent', 'version': '9.9.9', 'accepted_inputs': '[]', 'produced_outputs': '[]',
            'required_role': 'TEST_ENGINEER', 'enabled': 1, 'agent_id': 'custom-agent', 'display_name': 'Custom Agent',
            'description': 'x', 'tool_allowlist': '[]', 'dependencies': '[]', 'contract_hash': 'deliberately-wrong',
            'status': 'APPROVED', 'effective_from': '', 'approved_by': 'admin', 'approval_evidence_id': '',
            'supersedes_version': '', 'last_health_status': 'UNKNOWN', 'last_health_at': '', 'origin': 'RUNTIME'})

    # Re-running init (what a server restart does) must not raise, and must
    # leave the RUNTIME row exactly as it was.
    repo.init()
    row = repo.one('select * from agent_registry where agent_id=?', ('custom-agent',))
    assert row['contract_hash'] == 'deliberately-wrong'
    assert row['origin'] == 'RUNTIME'


def test_catalogue_origin_drift_still_crashes_as_before(tmp_path):
    """The original protection is untouched for rows that came from the file."""
    db_path = str(tmp_path / 'drift.db')
    repo = Repository(db_path)
    with repo.connect() as c:
        c.execute("update agent_registry set contract_hash='corrupted' where agent_id='ace-analysis'")
    with pytest.raises(RuntimeError, match='agent_contract_drift'):
        repo.init()
