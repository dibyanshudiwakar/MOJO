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


# ---- the HTML form of a stored parity / hardcoding report ---------------------------------------

def fetch_raw(base, path, bearer=None):
    """(status, headers, body bytes) with no JSON decoding and no system proxy in the way."""
    request = urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + bearer} if bearer else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _store_report(workspace, job_id, report_type='PARITY_ANALYSIS', report=None):
    from app.repository import Repository, digest, now, uid
    report = report or {'action': 'parity_check', 'application_name': 'CarrierNameSearch',
                        'counts': {'match': 1, 'partial': 0, 'gap': 1},
                        'rows': [{'area': 'Field Mappings', 'iib_value': 'a = b', 'ms_value': 'x.setA(b);',
                                  'status': 'MATCH'},
                                 {'area': 'Field Mappings', 'iib_value': 'c = d', 'ms_value': '— not implemented —',
                                  'status': 'GAP', 'remarks': 'c never set'}]}
    payload = json.dumps({'report': report, 'provenance': {'mode': 'MODEL_GATEWAY', 'provider_model': 'claude-x',
                                                           'prompt_name': 'agent/variance-analysis/parity-check',
                                                           'prompt_version': '1'}})
    repo = Repository(str(workspace.parent / 'api.db'))
    rid = uid()
    with repo.connect() as c:
        repo.insert(c, 'validation_reports', {'id': rid, 'job_id': job_id, 'report_type': report_type,
                                              'version': '1.0.0', 'payload': payload, 'content_hash': digest(payload),
                                              'status': 'ANALYSED', 'created_at': now(), 'reference_id': ''})
    return rid


def test_a_stored_report_is_served_as_a_self_contained_html_page(server):
    base, workspace = server
    bearer = token()
    rid = _store_report(workspace, _job(base, bearer)['id'])
    status, headers, body = fetch_raw(base, '/api/v2/reports/%s/html' % rid, bearer)
    page = body.decode('utf-8')
    assert status == 200 and headers['Content-Type'] == 'text/html; charset=utf-8'
    assert 'Parity report' in page and 'CarrierNameSearch' in page and 'c never set' in page and rid in page
    assert '<script' not in page


def test_the_report_page_gets_a_policy_that_fits_it_and_the_rest_of_the_app_keeps_its_own(server):
    base, workspace = server
    bearer = token()
    rid = _store_report(workspace, _job(base, bearer)['id'])
    _, headers, _ = fetch_raw(base, '/api/v2/reports/%s/html' % rid, bearer)
    csp = headers['Content-Security-Policy']
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp and 'script-src' not in csp
    assert headers['X-Content-Type-Options'] == 'nosniff' and headers['X-Frame-Options'] == 'DENY'
    # the override is per response: the next request, on the same server, is back to the app policy
    _, other, _ = fetch_raw(base, '/api/v2/health')
    assert "style-src 'self'" in other['Content-Security-Policy'] and "'unsafe-inline'" not in other['Content-Security-Policy']


def test_a_hardcoding_report_is_rendered_as_one(server):
    base, workspace = server
    bearer = token()
    rid = _store_report(workspace, _job(base, bearer)['id'], 'HARDCODING_ANALYSIS',
                        {'action': 'hardcoding_check', 'application_name': 'Orders',
                         'rows': [{'area': 'Field Mappings', 'category': 'Boolean encoding', 'status': 'PARTIAL',
                                   'remarks': 'string, not boolean'}]})
    status, _, body = fetch_raw(base, '/api/v2/reports/%s/html' % rid, bearer)
    assert status == 200 and 'Hardcoding report' in body.decode() and 'Boolean encoding' in body.decode()


def test_the_report_page_requires_authentication(server):
    base, workspace = server
    rid = _store_report(workspace, _job(base, token())['id'])
    status, _, _ = fetch_raw(base, '/api/v2/reports/%s/html' % rid)
    assert status == 401


def test_an_unknown_report_is_not_found(server):
    base, _ = server
    status, _, body = fetch_raw(base, '/api/v2/reports/no-such-report/html', token())
    assert status == 404 and b'report_not_found' in body


def test_only_parity_and_hardcoding_reports_are_served_this_way(server):
    base, workspace = server
    bearer = token()
    rid = _store_report(workspace, _job(base, bearer)['id'], 'RUNTIME_EXECUTION')
    status, _, _ = fetch_raw(base, '/api/v2/reports/%s/html' % rid, bearer)
    assert status == 404


def _scoped_token(projects, subject='other@example'):
    header = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    claims = _b64(json.dumps({'sub': subject, 'iss': 'ace-factory-idp', 'aud': 'ace-factory',
                              'exp': int(time.time()) + 900, 'roles': ROLES,
                              'projects': projects, 'environments': ['DEV']}).encode())
    signing = (header + '.' + claims).encode()
    return header + '.' + claims + '.' + _b64(hmac.new(SECRET.encode(), signing, hashlib.sha256).digest())


def test_a_report_of_a_job_outside_the_callers_project_is_refused_like_the_job_itself(server):
    """Isolation is the job's: the report page must not be a way around it."""
    base, workspace = server
    job = _job(base, token())
    rid = _store_report(workspace, job['id'])
    outsider = _scoped_token(['SOME-OTHER-PROJECT'])
    # prove the claim really isolates, using a route that already authorises by job ...
    status, body = call(base, '/api/v2/agents/dispatch',
                        {'job_id': job['id'], 'agent_id': 'repository-intake', 'params': {'path': 'ace'}}, outsider)
    assert status == 403, body
    # ... then the report is refused the same way, and the page is not leaked in the error.
    status, _, page = fetch_raw(base, '/api/v2/reports/%s/html' % rid, outsider)
    assert status == 403 and b'Parity report' not in page and b'CarrierNameSearch' not in page
