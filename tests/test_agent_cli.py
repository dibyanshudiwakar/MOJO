"""The standalone runner every agent module exposes.

Run in-process rather than as a subprocess: the behaviour under test is the
argument handling, the identity resolution and the exit codes, none of which
needs a separate interpreter.
"""
import base64
import hashlib
import hmac
import json
import time
import pytest
from app.agents import cli
from app.agents.ace_analysis import ACEAnalysisAgent
from app.agents.repository_intake import RepositoryIntakeAgent
from app.repository import Repository
from app.service import FactoryService


@pytest.fixture
def workspace(tmp_path):
    """A database, a job and an ACE source tree the CLI can be pointed at."""
    db = str(tmp_path / 'cli.db')
    repo = Repository(db)
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    ace = tmp_path / 'ace'
    ace.mkdir()
    (ace / 'Orders.msgflow').write_text('<flow><node id="in" type="HTTPInput" url="/orders"/></flow>')
    return {'db': db, 'repo': repo, 'job': job['id'], 'ace': str(ace)}


def _token(roles, subject='cli@example', secret='cli-secret'):
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    header = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    claims = b64(json.dumps({'sub': subject, 'iss': 'ace-factory-idp', 'aud': 'ace-factory',
                             'exp': int(time.time()) + 900, 'roles': roles,
                             'projects': ['GLOBAL'], 'environments': ['DEV']}).encode())
    signature = b64(hmac.new(secret.encode(), (header + '.' + claims).encode(), hashlib.sha256).digest())
    return header + '.' + claims + '.' + signature


def test_direct_mode_runs_the_agent_without_a_run_record(workspace, capsys):
    code = cli.main(RepositoryIntakeAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                            '--param', 'path=' + workspace['ace'], '--direct'])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['mode'] == 'direct' and payload['status'] == 'COMPLETED'
    assert payload['evidence'][0]['kind'] == 'INTAKE_MANIFEST'
    # Direct mode is for isolated debugging: no governed run, no evidence row.
    assert workspace['repo'].one('select count(*) n from agent_runs')['n'] == 0
    assert workspace['repo'].one('select count(*) n from evidence')['n'] == 0


def test_the_governed_default_records_the_run_and_attributes_the_channel(workspace, capsys):
    code = cli.main(RepositoryIntakeAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                            '--param', 'path=' + workspace['ace'],
                                            '--roles', 'REPOSITORY_INTAKE', '--identity', 'ops'])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    run = workspace['repo'].one('select * from agent_runs where id=?', (payload['run_id'],))
    assert run['status'] == 'COMPLETED'
    # A token-less run is attributable, which is the honest control here: the
    # caller already has write access to the database file.
    assert run['operator_identity'] == 'cli:ops'
    assert workspace['repo'].one('select count(*) n from evidence')['n'] == 1


def test_a_bearer_token_supplies_identity_and_roles(workspace, capsys, monkeypatch):
    monkeypatch.setenv('FACTORY_JWT_HS256_SECRET', 'cli-secret')
    code = cli.main(RepositoryIntakeAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                            '--param', 'path=' + workspace['ace'],
                                            '--token', _token(['REPOSITORY_INTAKE'])])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    run = workspace['repo'].one('select * from agent_runs where id=?', (payload['run_id'],))
    assert run['operator_identity'] == 'cli@example'


def test_an_invalid_token_stops_before_the_agent_runs(workspace, monkeypatch):
    monkeypatch.setenv('FACTORY_JWT_HS256_SECRET', 'cli-secret')
    code = cli.main(RepositoryIntakeAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                            '--param', 'path=' + workspace['ace'],
                                            '--token', _token(['REPOSITORY_INTAKE'], secret='wrong')])
    assert code == 3
    assert workspace['repo'].one('select count(*) n from agent_runs')['n'] == 0


def test_a_missing_role_exits_non_zero(workspace):
    assert cli.main(RepositoryIntakeAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                            '--param', 'path=' + workspace['ace'],
                                            '--roles', 'NOBODY']) == 4


def test_an_unsatisfied_dependency_exits_non_zero(workspace):
    assert cli.main(ACEAnalysisAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                       '--param', 'path=' + workspace['ace'],
                                       '--roles', 'ACE_ANALYST']) == 4


def test_parameters_are_parsed_as_json_where_possible(workspace, capsys):
    cli.main(RepositoryIntakeAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                     '--params-json', json.dumps({'path': workspace['ace']}),
                                     '--param', 'branch=main', '--direct'])
    assert json.loads(capsys.readouterr().out)['status'] == 'COMPLETED'
    manifest = workspace['repo'].one('select * from intake_manifests')
    assert manifest['branch'] == 'main'


def test_a_malformed_parameter_is_rejected(workspace):
    assert cli.main(RepositoryIntakeAgent, ['--db', workspace['db'], '--job', workspace['job'],
                                            '--param', 'pathwithoutequals', '--direct']) == 2


def test_the_usage_line_names_the_real_module():
    assert cli._prog(RepositoryIntakeAgent) == 'python -m app.agents.repository_intake'
