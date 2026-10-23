import json, io, urllib.error
import pytest
from app.repository import Repository
from app.services import github_integration
from app.services.accounts import AccountService, DEFAULT_ROLE, OPERATOR_ROLES
from app.services.auth import IdentityService
from app.services.github_integration import GitHubIntegrationService, api_base

SECRET = 'unit-test-signing-key'
ADMIN = ('GOVERNANCE_ADMIN',)


def _create(accounts, d):
    """Profiles are created by an administrator; tests act as one."""
    return accounts.sign_up(d, ADMIN)

GOOD = {'name': 'Ada Lovelace', 'email': 'ada@example.com', 'password': 'correct-horse-battery', 'role': 'MIGRATION_OPERATOR'}


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
    user = _create(accounts, dict(GOOD))
    assert user['email'] == 'ada@example.com' and user['role'] == DEFAULT_ROLE
    assert 'password' not in user and 'password_hash' not in user
    row = r.one('select * from users where email=?', ('ada@example.com',))
    # The password must not be recoverable from the stored row.
    assert GOOD['password'] not in json.dumps(dict(row))
    assert row['password_hash'] and row['password_salt'] and int(row['password_iterations']) >= 1000


def test_registering_first_does_not_make_anyone_an_administrator(svc):
    accounts, _ = svc
    first = _create(accounts, dict(GOOD))
    assert first['roles'] == [DEFAULT_ROLE], 'administration is seeded, never won by registering first'


def test_first_setup_seeds_one_administrator_with_only_administrative_roles(svc, monkeypatch):
    from app.services.accounts import ADMIN_ROLES, GATE_APPROVER_ROLES, SELF_SERVICE_ROLES, is_admin_only
    accounts, _ = svc
    monkeypatch.delenv('FACTORY_ADMIN_EMAIL', raising=False)
    monkeypatch.delenv('FACTORY_ADMIN_PASSWORD', raising=False)
    email, password = accounts.seed_admin()
    assert email == 'admin@ace.local' and len(password) >= 12
    roles = accounts.roles_of(accounts.by_email(email))
    assert set(roles) == set(ADMIN_ROLES) and is_admin_only(roles)
    assert not set(roles) & (set(SELF_SERVICE_ROLES) | set(GATE_APPROVER_ROLES)), 'cannot run or approve a migration'
    assert accounts.login(email, password)['user']['email'] == email
    assert accounts.seed_admin() is None, 'restarting never creates a second administrator'


def test_the_administrator_comes_from_the_environment_when_set(svc, monkeypatch):
    accounts, _ = svc
    monkeypatch.setenv('FACTORY_ADMIN_EMAIL', 'Root@Example.com')
    monkeypatch.setenv('FACTORY_ADMIN_PASSWORD', 'a-long-enough-password')
    assert accounts.seed_admin() == ('root@example.com', 'a-long-enough-password')


def test_an_administrator_account_cannot_be_given_pipeline_or_approver_roles(svc):
    accounts, _ = svc
    email, _pw = accounts.seed_admin()
    for role in (DEFAULT_ROLE, 'PRODUCT_OWNER', 'QA_LEAD', 'ORCHESTRATOR'):
        with pytest.raises(ValueError, match='admin_account_cannot_hold'):
            accounts.grant_role(['GOVERNANCE_ADMIN'], email, role)
    assert accounts.grant_role(['GOVERNANCE_ADMIN'], email, 'CONNECTOR_ADMIN')['email'] == email


def test_a_normal_account_can_still_be_granted_roles(svc):
    accounts, _ = svc
    accounts.seed_admin()
    _create(accounts, dict(GOOD))
    assert 'PRODUCT_OWNER' in accounts.grant_role(['GOVERNANCE_ADMIN'], GOOD['email'], 'PRODUCT_OWNER')['roles']


def test_signup_rejects_duplicate_weak_and_elevated(svc):
    accounts, _ = svc
    _create(accounts, dict(GOOD))
    with pytest.raises(ValueError, match='email_already_registered'):
        _create(accounts, dict(GOOD))
    with pytest.raises(ValueError, match='password_too_short'):
        _create(accounts, {**GOOD, 'email': 'b@example.com', 'password': 'short'})
    with pytest.raises(ValueError, match='valid_email_required'):
        _create(accounts, {**GOOD, 'email': 'not-an-email'})
    with pytest.raises(ValueError, match='unsupported_role'):
        _create(accounts, {**GOOD, 'email': 'd@example.com', 'role': 'NOT_A_ROLE'})


def test_login_issues_token_that_authenticates(svc):
    accounts, _ = svc
    _create(accounts, {**GOOD, 'email': 'grace@example.com'})   # first = bootstrap
    _create(accounts, dict(GOOD))
    out = accounts.login('ada@example.com', GOOD['password'])
    assert out['token_type'] == 'Bearer' and out['user']['email'] == 'ada@example.com'
    # The issued token must satisfy the same verifier every endpoint uses.
    principal = IdentityService(secret=SECRET).authenticate('Bearer ' + out['access_token'])
    assert principal.identity == 'ada@example.com' and DEFAULT_ROLE in principal.roles


def test_login_failures_are_indistinguishable(svc):
    accounts, _ = svc
    _create(accounts, dict(GOOD))
    with pytest.raises(PermissionError, match='invalid_credentials'):
        accounts.login('ada@example.com', 'wrong-password-entirely')
    # An unknown address must not be reported differently, or accounts can be enumerated.
    with pytest.raises(PermissionError, match='invalid_credentials'):
        accounts.login('nobody@example.com', 'wrong-password-entirely')


def test_repeated_failures_lock_the_account(svc):
    accounts, _ = svc
    _create(accounts, dict(GOOD))
    for _ in range(5):
        with pytest.raises(PermissionError):
            accounts.login('ada@example.com', 'bad-password-value')
    # Correct credentials are refused while the lockout stands.
    with pytest.raises(PermissionError, match='account_locked_until'):
        accounts.login('ada@example.com', GOOD['password'])


def test_grant_role_requires_governance_admin(svc):
    accounts, _ = svc
    _create(accounts, dict(GOOD))
    with pytest.raises(PermissionError, match='required_role_missing:GOVERNANCE_ADMIN'):
        accounts.grant_role((DEFAULT_ROLE,), 'ada@example.com', 'PRODUCT_OWNER')
    granted = accounts.grant_role(('GOVERNANCE_ADMIN',), 'ada@example.com', 'PRODUCT_OWNER')
    assert 'PRODUCT_OWNER' in granted['roles']


def test_assignable_roles_exclude_approvers():
    assert 'PRODUCT_OWNER' not in AccountService.assignable_roles()
    assert AccountService.assignable_roles() == [DEFAULT_ROLE]


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
    user = _create(accounts, dict(GOOD))
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


def test_connect_accepts_matching_email_owner(gh, monkeypatch):
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen',
                        lambda *a, **k: _resp({'login': 'ada', 'email': 'ada@example.com'}))
    state = service.connect(user['id'], user_email='ada@example.com')
    assert state['status'] == 'CONNECTED' and state['last_error'] == ''


def test_connect_accepts_matching_email_case_insensitive(gh, monkeypatch):
    """GitHub returns mixed-case email (e.g. Ada.Lovelace@Corp.com); app stores lowercase."""
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen',
                        lambda *a, **k: _resp({'login': 'ada', 'email': 'Ada@Example.com'}))
    state = service.connect(user['id'], user_email='ada@example.com')
    assert state['status'] == 'CONNECTED' and state['last_error'] == ''


def test_connect_accepts_github_enterprise_cloud_plus_tag_email(gh, monkeypatch):
    """GitHub Enterprise Cloud appends the org slug as a plus-tag in email addresses.

    e.g. ada+orgname@example.com must match app email ada@example.com.
    """
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')
    # GHEC returns the org-scoped plus-tag email; the app stores the plain corporate one.
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen',
                        lambda *a, **k: _resp({'login': 'ada', 'email': 'ada+orgname@example.com'}))
    state = service.connect(user['id'], user_email='ada@example.com')
    assert state['status'] == 'CONNECTED' and state['last_error'] == ''


def test_connect_rejects_public_email_mismatch(gh, monkeypatch):
    """Public email mismatch is a hard block.

    In enterprise deployments all GitHub accounts use the same corporate email
    as the app account, so a mismatch on the public /user email conclusively
    means a different person's token.
    """
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen',
                        lambda *a, **k: _resp({'login': 'other-person', 'email': 'other@example.com'}))
    with pytest.raises(PermissionError, match='github_token_owner_mismatch'):
        service.connect(user['id'], user_email='ada@example.com')
    assert service.config(user['id'])['status'] == 'FAILED'


def test_connect_rejects_confirmed_mismatch_via_verified_emails(gh, monkeypatch):
    """Hard-block when /user/emails (user:email scope) confirms the token belongs to someone else."""
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')

    call_log = []
    def fake_urlopen(req, **kw):
        call_log.append(req.full_url)
        if req.full_url.endswith('/user/emails'):
            # Verified emails — definitely someone else's account.
            return _resp([{'email': 'other@example.com', 'verified': True}], scopes='')
        return _resp({'login': 'other-person', 'email': None}, scopes='user:email')

    monkeypatch.setattr(github_integration.urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(PermissionError, match='github_token_owner_mismatch'):
        service.connect(user['id'], user_email='ada@example.com')
    assert service.config(user['id'])['status'] == 'FAILED'
    assert any('/user/emails' in u for u in call_log), 'must have fetched verified emails'


def test_connect_accepts_matching_verified_email(gh, monkeypatch):
    """Token is accepted when a verified email matches the logged-in user."""
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')

    def fake_urlopen(req, **kw):
        if req.full_url.endswith('/user/emails'):
            return _resp([{'email': 'ada@example.com', 'verified': True},
                          {'email': 'ada@personal.com', 'verified': True}], scopes='')
        return _resp({'login': 'ada', 'email': None}, scopes='user:email')

    monkeypatch.setattr(github_integration.urllib.request, 'urlopen', fake_urlopen)
    state = service.connect(user['id'], user_email='ada@example.com')
    assert state['status'] == 'CONNECTED' and state['last_error'] == ''


def test_connect_warns_when_owner_email_is_unverifiable(gh, monkeypatch):
    service, _, user = gh
    service.save(user['id'], 'https://github.com', 'ghp_token_value')
    # GitHub returns no email (private profile, repo-only scope — no user:email).
    monkeypatch.setattr(github_integration.urllib.request, 'urlopen',
                        lambda *a, **k: _resp({'login': 'ada', 'email': None}))
    state = service.connect(user['id'], user_email='ada@example.com')
    assert state['status'] == 'CONNECTED'
    assert 'github_owner_unverifiable' in state['last_error']


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


# -- only roles the code enforces ------------------------------------------

def test_retired_roles_are_not_offered_or_grantable(svc):
    from app.services.accounts import RETIRED_ROLES, ELEVATED_ROLES
    accounts, _ = svc
    offered = set(AccountService.assignable_roles()) | set(ELEVATED_ROLES)
    assert not offered & set(RETIRED_ROLES)
    _create(accounts, dict(GOOD))
    with pytest.raises(ValueError, match='unsupported_role'):
        accounts.grant_role(('GOVERNANCE_ADMIN',), 'ada@example.com', 'MIGRATION_VIEWER')


def test_every_listed_role_is_required_by_something():
    """A role is listed because an agent, endpoint or gate policy checks it."""
    import json
    from pathlib import Path
    from app.services.accounts import OPERATOR_ROLES, DEFAULT_ROLE, ELEVATED_ROLES
    root = Path(__file__).resolve().parents[1]
    code = ' '.join(p.read_text(encoding='utf-8', errors='ignore')
                    for p in list((root / 'app').rglob('*.py')) if p.name != 'accounts.py')
    code += (root / 'config' / 'agent-catalogue.v1.json').read_text(encoding='utf-8')
    unused = [r for r in OPERATOR_ROLES + ELEVATED_ROLES if "'%s'" % r not in code and '"%s"' % r not in code]
    assert unused == []


def test_startup_cleanup_strips_retired_roles_from_stored_accounts(svc):
    accounts, repo = svc
    _create(accounts, dict(GOOD))
    with repo.connect() as c:
        c.execute("update users set role='MIGRATION_VIEWER',"
                  "granted_roles='MIGRATION_VIEWER,ACE_ANALYST,SOLUTION_ARCHITECT'")
    assert accounts.prune_retired_roles() == 1
    user = accounts.by_email('ada@example.com')
    assert accounts.roles_of(user) == (DEFAULT_ROLE,) and user['role'] == DEFAULT_ROLE
    assert accounts.prune_retired_roles() == 0


# -- profiles are created by an administrator, who may name any role --

def test_an_administrator_creates_a_profile_with_a_gate_role_directly(svc):
    accounts, _ = svc
    _create(accounts, {**GOOD, 'email': 'ben@example.com', 'roles': ['QA_LEAD']})
    assert accounts.roles_of(accounts.by_email('ben@example.com')) == ('QA_LEAD',)


def test_only_an_administrator_can_create_a_profile(svc):
    accounts, _ = svc
    with pytest.raises(PermissionError, match='required_role_missing:GOVERNANCE_ADMIN'):
        accounts.sign_up(dict(GOOD), ('MIGRATION_OPERATOR',))


def test_an_operator_token_carries_every_operator_role(svc):
    accounts, _ = svc
    _create(accounts, dict(GOOD))
    out = accounts.login(GOOD['email'], GOOD['password'])
    assert set(OPERATOR_ROLES) <= set(accounts.token_roles([DEFAULT_ROLE]))
    assert out['user']['roles'] == [DEFAULT_ROLE], 'the account shows one role'


def _admin_and_user(accounts):
    admin = _create(accounts, {**GOOD, 'email': 'root@example.com', 'name': 'Root Admin', 'roles': ['GOVERNANCE_ADMIN']})
    user = _create(accounts, {**GOOD, 'email': 'ben@example.com', 'name': 'Ben Reviewer'})
    return admin, user


def test_only_a_governance_admin_can_see_decide_or_revoke(svc):
    accounts, _ = svc
    _admin_and_user(accounts)
    for call in (lambda: accounts.access_overview((DEFAULT_ROLE,)),
                 lambda: accounts.revoke_role((DEFAULT_ROLE,), 'x', 'ben@example.com', DEFAULT_ROLE)):
        with pytest.raises(PermissionError, match='required_role_missing:GOVERNANCE_ADMIN'):
            call()


def test_revoking_a_role_and_the_last_administrator_guard(svc):
    accounts, _ = svc
    _admin_and_user(accounts)
    accounts.grant_role(('GOVERNANCE_ADMIN',), 'ben@example.com', 'QA_LEAD')
    out = accounts.revoke_role(('GOVERNANCE_ADMIN',), 'root@example.com', 'ben@example.com', 'QA_LEAD')
    assert 'QA_LEAD' not in out['roles']
    with pytest.raises(ValueError, match='last_governance_admin'):
        accounts.revoke_role(('GOVERNANCE_ADMIN',), 'root@example.com', 'root@example.com', 'GOVERNANCE_ADMIN')
