import json, io, urllib.error
import pytest
from app.repository import Repository
from app.services import github_integration
from app.services.accounts import AccountService, SELF_SERVICE_ROLES
from app.services.auth import IdentityService
from app.services.github_integration import GitHubIntegrationService, api_base

SECRET = 'unit-test-signing-key'
GOOD = {'name': 'Ada Lovelace', 'email': 'ada@example.com', 'password': 'correct-horse-battery', 'role': 'ACE_ANALYST'}


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """Keep PBKDF2 honest but affordable in tests."""
    monkeypatch.setenv('FACTORY_PBKDF2_ITERATIONS', '1000')


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_DB', str(tmp_path / 'accounts.db'))
    monkeypatch.setenv('FACTORY_SECRET_KEY', '')
    r = Repository(str(tmp_path / 'accounts.db'))
    return AccountService(r, IdentityService(secret=SECRET)), r


def test_signup_stores_account_without_plaintext_password(svc):
    accounts, r = svc
    user = accounts.sign_up(dict(GOOD))
    assert user['email'] == 'ada@example.com' and user['role'] == 'ACE_ANALYST'
    assert 'password' not in user and 'password_hash' not in user
    row = r.one('select * from users where email=?', ('ada@example.com',))
    # The password must not be recoverable from the stored row.
    assert GOOD['password'] not in json.dumps(dict(row))
    assert row['password_hash'] and row['password_salt'] and int(row['password_iterations']) >= 1000


def test_first_account_bootstraps_administration(svc):
    accounts, _ = svc
    first = accounts.sign_up(dict(GOOD))
    assert 'GOVERNANCE_ADMIN' in first['roles'], 'a fresh deployment needs one administrator'
    second = accounts.sign_up({**GOOD, 'email': 'grace@example.com', 'name': 'Grace Hopper'})
    assert second['roles'] == ['ACE_ANALYST'], 'only the first account is privileged'


def test_signup_rejects_duplicate_weak_and_elevated(svc):
    accounts, _ = svc
    accounts.sign_up(dict(GOOD))
    with pytest.raises(ValueError, match='email_already_registered'):
        accounts.sign_up(dict(GOOD))
    with pytest.raises(ValueError, match='password_too_short'):
        accounts.sign_up({**GOOD, 'email': 'b@example.com', 'password': 'short'})
    with pytest.raises(ValueError, match='valid_email_required'):
        accounts.sign_up({**GOOD, 'email': 'not-an-email'})
    # Self-registration must not be able to hand out an approver role.
    with pytest.raises(PermissionError, match='role_requires_administrator_grant'):
        accounts.sign_up({**GOOD, 'email': 'c@example.com', 'role': 'SOLUTION_ARCHITECT'})
    with pytest.raises(ValueError, match='unsupported_role'):
        accounts.sign_up({**GOOD, 'email': 'd@example.com', 'role': 'NOT_A_ROLE'})


def test_login_issues_token_that_authenticates(svc):
    accounts, _ = svc
    accounts.sign_up({**GOOD, 'email': 'grace@example.com'})   # first = bootstrap
    accounts.sign_up(dict(GOOD))
    out = accounts.login('ada@example.com', GOOD['password'])
    assert out['token_type'] == 'Bearer' and out['user']['email'] == 'ada@example.com'
    # The issued token must satisfy the same verifier every endpoint uses.
    principal = IdentityService(secret=SECRET).authenticate('Bearer ' + out['access_token'])
    assert principal.identity == 'ada@example.com' and 'ACE_ANALYST' in principal.roles


def test_login_failures_are_indistinguishable(svc):
    accounts, _ = svc
    accounts.sign_up(dict(GOOD))
    with pytest.raises(PermissionError, match='invalid_credentials'):
        accounts.login('ada@example.com', 'wrong-password-entirely')
    # An unknown address must not be reported differently, or accounts can be enumerated.
    with pytest.raises(PermissionError, match='invalid_credentials'):
        accounts.login('nobody@example.com', 'wrong-password-entirely')


def test_repeated_failures_lock_the_account(svc):
    accounts, _ = svc
    accounts.sign_up(dict(GOOD))
    for _ in range(5):
        with pytest.raises(PermissionError):
            accounts.login('ada@example.com', 'bad-password-value')
    # Correct credentials are refused while the lockout stands.
    with pytest.raises(PermissionError, match='account_locked_until'):
        accounts.login('ada@example.com', GOOD['password'])


def test_grant_role_requires_governance_admin(svc):
    accounts, _ = svc
    accounts.sign_up(dict(GOOD))
    with pytest.raises(PermissionError, match='required_role_missing:GOVERNANCE_ADMIN'):
        accounts.grant_role(('ACE_ANALYST',), 'ada@example.com', 'SOLUTION_ARCHITECT')
    granted = accounts.grant_role(('GOVERNANCE_ADMIN',), 'ada@example.com', 'SOLUTION_ARCHITECT')
    assert 'SOLUTION_ARCHITECT' in granted['roles']


def test_assignable_roles_exclude_approvers():
    assert 'SOLUTION_ARCHITECT' not in AccountService.assignable_roles()
    assert set(AccountService.assignable_roles()) == set(SELF_SERVICE_ROLES)


# -- GitHub integration ---------------------------------------------------

def _resp(payload, scopes='repo'):
    class R(io.BytesIO):
        headers = {'X-OAuth-Scopes': scopes}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return R(json.dumps(payload).encode())


@pytest.fixture
def gh(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_DB', str(tmp_path / 'gh.db'))
    monkeypatch.delenv('FACTORY_SECRET_KEY', raising=False)
    r = Repository(str(tmp_path / 'gh.db'))
    accounts = AccountService(r, IdentityService(secret=SECRET))
    user = accounts.sign_up(dict(GOOD))
    return GitHubIntegrationService(r), r, user


def test_api_base_maps_cloud_and_enterprise():
    assert api_base('https://github.com') == 'https://api.github.com'
    assert api_base('https://github.com/some/org') == 'https://api.github.com'
    # Enterprise Server serves the API under /api/v3, not at the root.
    assert api_base('https://git.corp.example.com') == 'https://git.corp.example.com/api/v3'


def test_token_is_encrypted_at_rest_and_never_returned(gh):
    service, r, user = gh
    state = service.save(user['id'], 'https://github.com', 'ghp_secrettokenvalue123')
    row = r.one('select * from user_integrations where user_id=?', (user['id'],))
    serialised = json.dumps(dict(row))
    assert 'ghp_secrettokenvalue123' not in serialised, 'token must not be stored in clear'
    assert row['token_encrypted'] and row['token_fingerprint']
    # Nothing the API hands back may carry the credential.
    assert 'ghp_secrettokenvalue123' not in json.dumps(state)
    assert 'token' not in state or state.get('token') is None
    assert service._token(user['id']) == 'ghp_secrettokenvalue123'


def test_save_rejects_unsafe_urls(gh):
    service, _, user = gh
    for bad in ('http://github.com', 'https://localhost/gh', 'https://127.0.0.1',
                'https://user:pw@github.com', 'https://10.0.0.5', ''):
        with pytest.raises(PermissionError):
            service.save(user['id'], bad, 'ghp_token_value')
    with pytest.raises(ValueError, match='github_token_required'):
        service.save(user['id'], 'https://github.com', '')


def test_connect_records_verified_identity(gh, monkeypatch):
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen',
                        lambda *a, **k: _resp({'login': 'ada'}))
    state = service.connect(user['id'])
    assert state['status'] == 'CONNECTED' and state['account_login'] == 'ada' and state['scopes'] == 'repo'
    assert state['last_error'] == ''


def test_connect_surfaces_upstream_error_for_retry(gh, monkeypatch):
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')

    def unauthorized(*a, **k):
        raise urllib.error.HTTPError('u', 401, 'Unauthorized', {},
                                     io.BytesIO(json.dumps({'message': 'Bad credentials'}).encode()))
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen', unauthorized)
    # The operator must be told what GitHub said, so the dialog can offer a retry.
    with pytest.raises(PermissionError, match='github_credentials_rejected'):
        service.connect(user['id'])
    state = service.config(user['id'])
    assert state['status'] == 'FAILED' and 'Bad credentials' in state['last_error']

    def unreachable(*a, **k):
        raise urllib.error.URLError('name resolution failed')
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen', unreachable)
    with pytest.raises(ConnectionError, match='github_unreachable'):
        service.connect(user['id'])
    assert service.config(user['id'])['status'] == 'FAILED'


def test_config_reports_not_configured_before_setup(gh):
    service, _, user = gh
    state = service.config(user['id'])
    assert state == {'configured': False, 'provider': 'github', 'status': 'NOT_CONFIGURED'}
