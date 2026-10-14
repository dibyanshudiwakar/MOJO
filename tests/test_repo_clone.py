import pytest
from app.repository import Repository
from app.services import workspace
from app.services.accounts import AccountService
from app.services.github_integration import GitHubIntegrationService
from app.services.repo_clone import RepoCloneService, RepoCloneError


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(tmp_path / 'workspace'))
    monkeypatch.delenv('FACTORY_SECRET_KEY', raising=False)
    monkeypatch.setenv('FACTORY_PBKDF2_ITERATIONS', '1000')
    repo = Repository(str(tmp_path / 'clone.db'))
    github = GitHubIntegrationService(repo)
    # user_integrations.user_id is a real foreign key against users(id), so
    # cloning tests need a real account, not an arbitrary string.
    user = AccountService(repo).sign_up({'name': 'Clone Tester', 'email': 'clone@example.com',
                                          'password': 'a-long-enough-password', 'role': 'ACE_ANALYST'})
    return repo, github, RepoCloneService(repo), user['id']


def _configure_github(github, user_id, url='https://github.com', token='ghp_faketokenvalue123456'):
    github.save(user_id, url, token)


class _FakeCompleted:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_clone_rejects_non_https_url(env):
    repo, github, svc, uid = env
    _configure_github(github, uid)
    with pytest.raises(workspace.WorkspaceError, match='runtime_url_must_be_https'):
        svc.clone(uid, 'http://github.com/org/repo.git', 'main', 'ace')


def test_clone_rejects_unsafe_branch_name(env):
    repo, github, svc, uid = env
    _configure_github(github, uid)
    with pytest.raises(workspace.WorkspaceError, match='invalid_git_ref'):
        svc.clone(uid, 'https://github.com/org/repo.git', '--upload-pack=evil', 'ace')


def test_clone_requires_github_configured(env):
    repo, github, svc, uid = env
    with pytest.raises(RepoCloneError, match='github_not_configured'):
        svc.clone(uid, 'https://github.com/org/repo.git', 'main', 'ace')


def test_clone_rejects_host_mismatch(env):
    repo, github, svc, uid = env
    _configure_github(github, uid, url='https://github.com')
    with pytest.raises(RepoCloneError, match='repo_host_does_not_match_connected_github'):
        svc.clone(uid, 'https://gitlab.com/org/repo.git', 'main', 'ace')


def test_clone_never_puts_token_in_argv_or_url(env, monkeypatch):
    repo, github, svc, uid = env
    _configure_github(github, uid, token='ghp_supersecrettoken000000')
    captured = {}

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        # The unauthenticated `git rev-parse HEAD` check after a successful
        # clone goes through this same stub without an env kwarg -- only
        # capture the credentialed call. Read the askpass file's content now:
        # its temp directory is removed as soon as the `with _Askpass(...)`
        # block in clone() exits, which happens before clone() returns.
        if env is not None:
            captured['cmd'] = cmd
            captured['env'] = env
            captured['askpass_contents'] = open(env['GIT_ASKPASS'], encoding='utf-8').read()
        return _FakeCompleted(0, stdout='ok')

    monkeypatch.setattr('app.services.repo_clone.subprocess.run', fake_run)
    result = svc.clone(uid, 'https://github.com/org/repo.git', 'main', 'ace-repo')

    joined = ' '.join(captured['cmd'])
    assert 'ghp_supersecrettoken000000' not in joined
    assert 'ghp_supersecrettoken000000' not in result['path']
    # The token travels only through the env var the askpass helper reads --
    # never as part of the remote URL, which would leak it into git config.
    assert captured['env'].get('ACE_GIT_TOKEN') == 'ghp_supersecrettoken000000'
    assert 'ghp_supersecrettoken000000' not in captured['askpass_contents']
    assert '--branch' in captured['cmd'] and 'main' in captured['cmd']
    assert 'https://github.com/org/repo.git' in captured['cmd']
    assert '@' not in [a for a in captured['cmd'] if a.startswith('https://')][0]


def test_clone_rejects_nonempty_destination(env, monkeypatch, tmp_path):
    repo, github, svc, uid = env
    _configure_github(github, uid)
    dest = workspace.resolve('ace-repo', must_exist=False, create=True)
    (dest / 'existing.txt').write_text('already here')
    with pytest.raises(RepoCloneError, match='clone_destination_not_empty'):
        svc.clone(uid, 'https://github.com/org/repo.git', 'main', 'ace-repo')


def test_clone_idempotent_when_same_url_already_cloned(env, monkeypatch):
    """Retry after a partial clone (files written, DB record not) must succeed.

    If the destination is non-empty AND already points at the same remote URL,
    treat it as a completed clone and return the existing HEAD -- do not raise.
    This covers the race where git clone succeeds at the filesystem level but
    the worker crashes before writing the completion record, causing the queue
    to retry and find a populated directory.
    """
    repo, github, svc, uid = env
    _configure_github(github, uid)

    calls = []

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        calls.append(cmd)
        if 'remote' in cmd and 'get-url' in cmd:
            return _FakeCompleted(0, stdout='https://github.com/org/repo.git\n')
        if 'rev-parse' in cmd:
            return _FakeCompleted(0, stdout='abc123def456\n')
        return _FakeCompleted(0, stdout='ok')

    monkeypatch.setattr('app.services.repo_clone.subprocess.run', fake_run)

    dest = workspace.resolve('ace-repo', must_exist=False, create=True)
    (dest / 'existing.txt').write_text('already here')

    result = svc.clone(uid, 'https://github.com/org/repo.git', 'main', 'ace-repo')

    assert result['commit'] == 'abc123def456'
    assert result['path'].endswith('ace-repo')
    # Must not have attempted a fresh git clone
    assert not any('clone' in c for c in calls)


def test_clone_rejects_nonempty_destination_with_different_remote(env, monkeypatch):
    """Non-empty directory pointing at a DIFFERENT repo must still be rejected."""
    repo, github, svc, uid = env
    _configure_github(github, uid)

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        if 'remote' in cmd and 'get-url' in cmd:
            return _FakeCompleted(0, stdout='https://github.com/org/OTHER-repo.git\n')
        return _FakeCompleted(0, stdout='ok')

    monkeypatch.setattr('app.services.repo_clone.subprocess.run', fake_run)

    dest = workspace.resolve('ace-repo', must_exist=False, create=True)
    (dest / 'existing.txt').write_text('already here')

    with pytest.raises(RepoCloneError, match='clone_destination_not_empty'):
        svc.clone(uid, 'https://github.com/org/repo.git', 'main', 'ace-repo')


def test_clone_surfaces_git_failure_detail(env, monkeypatch):
    repo, github, svc, uid = env
    _configure_github(github, uid)

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        return _FakeCompleted(128, stderr='fatal: repository not found')

    monkeypatch.setattr('app.services.repo_clone.subprocess.run', fake_run)
    with pytest.raises(RepoCloneError, match='repository not found'):
        svc.clone(uid, 'https://github.com/org/missing.git', 'main', 'ace-repo')


def test_push_rejects_missing_source_dir(env):
    repo, github, svc, uid = env
    _configure_github(github, uid)
    with pytest.raises(workspace.WorkspaceError, match='workspace_path_not_found'):
        svc.push(uid, 'https://github.com/org/target.git', 'main', 'nonexistent-output')


def test_every_git_invocation_disables_credential_helper(env, monkeypatch):
    """Regression test for the actual reported bug: with credential.helper
    left at whatever the host has configured (Git Credential Manager, on a
    typical Git for Windows install), git can show its own interactive
    GitHub sign-in instead of using the token from GIT_ASKPASS -- from the
    operator's side this looks exactly like "why is it asking me to log in
    when I already gave it a token". Every git call this module makes must
    disable it explicitly, not rely on GIT_ASKPASS/GIT_TERMINAL_PROMPT alone.
    """
    repo, github, svc, uid = env
    _configure_github(github, uid)
    calls = []

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        calls.append(cmd)
        return _FakeCompleted(0, stdout='ok')

    monkeypatch.setattr('app.services.repo_clone.subprocess.run', fake_run)
    svc.clone(uid, 'https://github.com/org/repo.git', 'main', 'ace-repo')
    # rev-parse (a purely local, read-only follow-up with no remote and no
    # credential involvement at all) legitimately bypasses _run entirely --
    # the assertion is specifically about the network-touching clone call.
    remote_calls = [c for c in calls if 'clone' in c]
    assert remote_calls, 'expected a clone invocation'
    for cmd in remote_calls:
        assert cmd[0] == 'git' and cmd[1:3] == ['-c', 'credential.helper='], (
            'git invocation did not disable credential.helper: %r' % cmd)


def test_push_creates_branch_when_clone_of_branch_fails(env, monkeypatch):
    repo, github, svc, uid = env
    _configure_github(github, uid)
    source = workspace.resolve('springboot-output', must_exist=False, create=True)
    (source / 'pom.xml').write_text('<project/>')
    calls = []

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        calls.append(cmd)
        # 'clone' is no longer necessarily cmd[1] -- every invocation is now
        # prefixed with -c credential.helper= to stop Git Credential Manager
        # from intercepting with its own interactive GitHub sign-in.
        if 'clone' in cmd and '--branch' in cmd:
            return _FakeCompleted(128, stderr='fatal: Remote branch new-branch not found')
        return _FakeCompleted(0, stdout='ok')

    monkeypatch.setattr('app.services.repo_clone.subprocess.run', fake_run)
    svc.push(uid, 'https://github.com/org/target.git', 'new-branch', 'springboot-output')
    # Membership, not cmd[1]: every call is now prefixed with
    # -c credential.helper=, so the subcommand is no longer at a fixed index.
    assert any('clone' in c for c in calls)
    assert any('checkout' in c for c in calls)
    assert any('push' in c for c in calls)
