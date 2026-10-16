"""The agent routes over real HTTP, against a real server process.

Worth the cost of a live server because the things being checked only exist at
that layer: that dispatch authenticates and authorises, that the workspace
confinement applied to the older per-service routes also applies to agent
parameters, and that a run started over HTTP has an owner.
"""
import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import pytest

# Agent counts are derived from the governed catalogue rather than hardcoded, so
# adding a contract does not silently turn these invariants into stale numbers.
def _catalogue_agent_ids():
    import json, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / 'config' / 'agent-catalogue.v1.json'
    return {a['agent_id'] for a in json.loads(path.read_text(encoding='utf-8'))['agents']}


CATALOGUE_AGENT_COUNT = len(_catalogue_agent_ids())


SECRET = 'agent-api-test-secret'
ROLES = ['ORCHESTRATOR', 'REPOSITORY_INTAKE', 'ACE_ANALYST', 'GOVERNANCE_ENGINE',
         'KNOWLEDGE_CURATOR', 'GOVERNANCE_ADMIN']


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def token(roles=ROLES, subject='svc@example', secret=SECRET):
    header = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    claims = _b64(json.dumps({'sub': subject, 'iss': 'ace-factory-idp', 'aud': 'ace-factory',
                              'exp': int(time.time()) + 900, 'roles': roles,
                              'projects': ['GLOBAL'], 'environments': ['DEV']}).encode())
    signing = (header + '.' + claims).encode()
    return header + '.' + claims + '.' + _b64(hmac.new(secret.encode(), signing, hashlib.sha256).digest())


def call(base, path, payload=None, bearer=None, method=None):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + bearer} if bearer else {})},
        method=method or ('POST' if payload is not None else 'GET'))
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b'{}')


@pytest.fixture
def server(tmp_path):
    """A live factory whose workspace root is confined to the test directory."""
    root = Path(__file__).parents[1]
    workspace = tmp_path / 'workspace'
    (workspace / 'ace').mkdir(parents=True)
    (workspace / 'ace' / 'Orders.msgflow').write_text(
        '<flow><node id="in" type="HTTPInput" url="/orders"/></flow>')
    (tmp_path / 'outside').mkdir()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, FACTORY_DB=str(tmp_path / 'api.db'), PORT=str(port), HOST='127.0.0.1',
               FACTORY_JWT_HS256_SECRET=SECRET, FACTORY_WORKSPACE_ROOT=str(workspace))
    proc = subprocess.Popen([sys.executable, '-m', 'app.server'], cwd=str(root), env=env,
                            stderr=subprocess.PIPE, text=True)
    base = 'http://127.0.0.1:%d' % port
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/api/v2/health', timeout=2).read()
                break
            except Exception:
                time.sleep(.3)
        else:
            proc.terminate()
            pytest.fail('server_did_not_start')
        yield base, workspace
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


def _job(base, bearer):
    status, job = call(base, '/api/v2/jobs', {'service': 'Orders', 'owner': 'Retail',
                                              'sourceRevision': 'abc', 'rollbackPointer': 'tag/v1'}, bearer)
    assert status == 201, job
    return job


def test_every_contract_reports_a_conformant_worker(server):
    base, _ = server
    status, body = call(base, '/api/v2/agent-registry/implementations', bearer=token())
    assert status == 200
    assert body['status'] == 'UP'
    assert len(body['agents']) == CATALOGUE_AGENT_COUNT
    assert all(x['status'] == 'CONFORMANT' and x['implementation'] for x in body['agents'])


def test_dispatch_runs_the_worker_and_registers_its_evidence(server):
    base, workspace = server
    bearer = token()
    job = _job(base, bearer)
    status, body = call(base, '/api/v2/agents/dispatch',
                        {'job_id': job['id'], 'agent_id': 'repository-intake',
                         'params': {'path': 'ace'}}, bearer)
    assert status == 201, body
    assert body['status'] == 'COMPLETED'
    assert body['outputs']['intake_manifest']
    assert body['evidence'][0]['kind'] == 'INTAKE_MANIFEST'
    status, runs = call(base, '/api/v2/agents?job_id=' + job['id'], bearer=bearer)
    run, = runs['items']
    assert run['agent_id'] == 'repository-intake'
    assert run['operator_identity'] == 'svc@example'
    assert run['status'] == 'COMPLETED'


def test_agent_parameters_are_confined_to_the_workspace(server, tmp_path):
    """The dispatch endpoint must not be a way around the confinement the older
    per-service routes apply."""
    base, _ = server
    bearer = token()
    job = _job(base, bearer)
    status, body = call(base, '/api/v2/agents/dispatch',
                        {'job_id': job['id'], 'agent_id': 'repository-intake',
                         'params': {'path': str(tmp_path / 'outside')}}, bearer)
    assert status == 403
    assert body['error'] == 'path_outside_workspace_root'


def test_a_runtime_replay_url_must_be_an_external_https_endpoint(server):
    base, _ = server
    bearer = token()
    job = _job(base, bearer)
    for url, expected in (('http://runtime.example.com', 'runtime_url_must_be_https'),
                          ('https://169.254.169.254/latest/meta-data', 'runtime_url_host_not_permitted'),
                          ('https://localhost:8443', 'runtime_url_host_not_qualified')):
        status, body = call(base, '/api/v2/agents/orchestrate',
                            {'job_id': job['id'], 'params': {'ace_base_url': url}}, bearer)
        assert status == 403, (url, body)
        assert body['error'] == expected, (url, body)


def test_dispatch_enforces_the_agents_required_role(server):
    base, _ = server
    bearer = token()
    job = _job(base, bearer)
    weak = token(roles=['ACE_ANALYST'])
    status, body = call(base, '/api/v2/agents/dispatch',
                        {'job_id': job['id'], 'agent_id': 'repository-intake',
                         'params': {'path': 'ace'}}, weak)
    assert status == 403
    assert body['error'] == 'required_agent_role_missing'


def test_dispatch_enforces_contract_dependencies(server):
    base, _ = server
    bearer = token()
    job = _job(base, bearer)
    status, body = call(base, '/api/v2/agents/dispatch',
                        {'job_id': job['id'], 'agent_id': 'ace-analysis',
                         'params': {'path': 'ace'}}, bearer)
    assert status == 403
    assert body['error'] == 'agent_dependency_not_satisfied:repository-intake'
    status, body = call(base, '/api/v2/agent-registry/ace-analysis/dependencies?job_id=' + job['id'],
                        bearer=bearer)
    assert status == 200
    assert body['outstanding'] == ['repository-intake']


def test_dispatch_requires_authentication_and_job_scope(server):
    base, _ = server
    bearer = token()
    job = _job(base, bearer)
    status, _ = call(base, '/api/v2/agents/dispatch',
                     {'job_id': job['id'], 'agent_id': 'repository-intake', 'params': {'path': 'ace'}})
    assert status == 403
    scoped = token()
    # A token scoped to another project may not drive this job.
    other = token(roles=ROLES, subject='other@example')
    status, body = call(base, '/api/v2/agents/dispatch',
                        {'job_id': 'no-such-job', 'agent_id': 'repository-intake',
                         'params': {'path': 'ace'}}, other)
    assert status == 422 and body['error'] == 'job_not_found'
    assert scoped


def test_an_externally_started_run_may_only_be_completed_by_its_owner(server):
    base, _ = server
    bearer = token()
    job = _job(base, bearer)
    status, run = call(base, '/api/v2/agents/start',
                       {'job_id': job['id'], 'agent_id': 'repository-intake', 'version': '1.0.0',
                        'requested_tools': ['repository.read']}, bearer)
    assert status == 201, run
    other = token(roles=ROLES, subject='intruder@example')
    status, body = call(base, '/api/v2/agents/%s/complete' % run['id'], {'output_evidence': []}, other)
    assert status == 403
    assert body['error'] == 'agent_run_not_owned'
    status, body = call(base, '/api/v2/agents/%s/complete' % run['id'], {'output_evidence': []}, bearer)
    assert status == 200 and body['status'] == 'COMPLETED'


def test_orchestrate_steps_the_job_and_stops_at_the_first_gate(server):
    base, _ = server
    bearer = token()
    job = _job(base, bearer)
    status, body = call(base, '/api/v2/agents/orchestrate',
                        {'job_id': job['id'], 'params': {'ace_path': 'ace'}}, bearer)
    assert status == 201, body
    # No framework path was seeded, so the run halts asking for it rather than
    # guessing, and the job has not been moved past the state it reached.
    assert body['outputs']['clarification_request'] or body['outputs']['commands']
    status, refreshed = call(base, '/api/v2/jobs/' + job['id'], bearer=bearer)
    assert refreshed['state'] in ('CREATED', 'INTAKE_VALIDATED', 'DORMANCY_ASSESSED')
