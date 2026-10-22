"""The seeded administrator: created on first setup, and kept out of pipeline work.

Runs the server module in a subprocess against a throwaway database, because importing
it opens (and now seeds) whatever FACTORY_DB names.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = '''
import app.server as s
from app.services.auth import Principal
H = s.H
admin = Principal('admin@ace.local', ('GOVERNANCE_ADMIN', 'GATE_ADMIN'), ('GLOBAL',), ('DEV',))
operator = Principal('o@x.com', ('ACE_ANALYST',), ('GLOBAL',), ('DEV',))
mixed = Principal('m@x.com', ('GOVERNANCE_ADMIN', 'ACE_ANALYST'), ('GLOBAL',), ('DEV',))
h = H.__new__(H)
def blocked(pr, path):
    try:
        h.block_admin_operating(pr, path)
        return False
    except PermissionError as e:
        return str(e) == 'admin_account_cannot_operate_pipelines'
for path in ('/api/v2/pipelines', '/api/v2/pipelines/abc/run', '/api/v2/jobs', '/api/v2/clarifications/x/answer',
             '/api/v7/approval-inbox', '/api/v7/approval-packages/p/approve', '/api/v2/agents/dispatch'):
    assert blocked(admin, path), path
for path in ('/api/v2/access', '/api/v2/admin/access', '/api/v2/agent-models', '/api/v2/learning',
             '/api/v2/gate-controls', '/api/v2/audit-ledger'):
    assert not blocked(admin, path), path
assert not blocked(operator, '/api/v2/pipelines') and not blocked(mixed, '/api/v2/pipelines')
try:
    h.authorize_job(admin, 'job-1')
    raise SystemExit('administrator was allowed to act on a job')
except PermissionError as e:
    assert str(e) == 'admin_account_cannot_operate_pipelines'
print('OK')
'''


def test_the_administrator_is_seeded_and_cannot_operate_pipelines(tmp_path):
    env = {**os.environ, 'FACTORY_DB': str(tmp_path / 'fresh.db'), 'FACTORY_DISABLE_WORKER': '1',
           'FACTORY_ADMIN_EMAIL': 'admin@ace.local', 'FACTORY_PBKDF2_ITERATIONS': '1000',
           'FACTORY_JWT_HS256_SECRET': 'admin-test-secret'}
    env.pop('FACTORY_ADMIN_PASSWORD', None)
    done = subprocess.run([sys.executable, '-c', SCRIPT], cwd=str(ROOT), env=env, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'First setup: administrator account created.' in done.stderr, 'the password is announced once'
    assert 'password:' in done.stderr and 'password:' not in done.stdout
    creds = (tmp_path / 'fresh.admin-credentials')
    assert creds.exists() and 'admin@ace.local' in creds.read_text()
    assert done.stdout.rstrip().endswith('OK')
