"""Per-user GitHub connection: stored URL, encrypted token, real connectivity test.

The token is a live credential, so it is never returned to any client and never
written to ``connector_profiles.config`` -- that path rejects credential-shaped
keys precisely because configuration is readable by connector administrators.
It is encrypted at rest here and decrypted only to make an outbound call.

The connectivity check is a real HTTPS request. When it fails, the upstream
status and message are returned verbatim so the UI can show the operator what
GitHub actually said and let them correct it, rather than reporting a generic
failure.
"""
import json, os, ssl, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from app.repository import uid, now, digest
from app.services import workspace

TIMEOUT = 15
USER_AGENT = 'ACE-Migration-Factory'


def _key():
    """Fernet key for token encryption, persisted beside the database."""
    from cryptography.fernet import Fernet
    env = os.getenv('FACTORY_SECRET_KEY', '').strip()
    if env:
        return Fernet(env.encode())
    db = os.getenv('FACTORY_DB') or str(Path(__file__).resolve().parents[2] / 'factory-v2.db')
    keyfile = Path(db).with_suffix('.secret-key')
    if keyfile.exists():
        raw = keyfile.read_text(encoding='utf-8').strip()
        if raw:
            return Fernet(raw.encode())
    generated = Fernet.generate_key().decode()
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.write_text(generated, encoding='utf-8')
    try:
        os.chmod(keyfile, 0o600)
    except OSError:
        pass
    return Fernet(generated.encode())


def api_base(url):
    """Map a GitHub web URL to its REST base.

    github.com serves its API from api.github.com; GitHub Enterprise Server
    serves it from <host>/api/v3. Getting this wrong is the most common cause of
    a 404 that looks like a bad token.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if host in ('github.com', 'www.github.com', 'api.github.com'):
        return 'https://api.github.com'
    port = ':%d' % parsed.port if parsed.port else ''
    return urlunparse(('https', host + port, '/api/v3', '', '', ''))


class GitHubIntegrationService:
    def __init__(self, repo):
        self.repo = repo

    def config(self, user_id):
        """The stored connection, without the token."""
        row = self.repo.one('select * from user_integrations where user_id=? and provider=?', (user_id, 'github'))
        if not row:
            return {'configured': False, 'provider': 'github', 'status': 'NOT_CONFIGURED'}
        return {'configured': True, 'provider': 'github', 'url': row['base_url'],
                'api_base': api_base(row['base_url']), 'status': row['status'],
                'account_login': row['account_login'], 'scopes': row['scopes'],
                'last_checked_at': row['last_checked_at'], 'last_error': row['last_error'],
                'token_fingerprint': row['token_fingerprint']}

    def save(self, user_id, url, token):
        url = workspace.runtime_url(url)
        token = str(token or '').strip()
        if not token:
            raise ValueError('github_token_required')
        if len(token) < 8:
            raise ValueError('github_token_too_short')
        sealed = _key().encrypt(token.encode()).decode()
        # A fingerprint lets the UI confirm which credential is stored, and lets
        # an operator tell two tokens apart, without the value being readable.
        fingerprint = digest(token)[:12]
        existing = self.repo.one('select id,created_at from user_integrations where user_id=? and provider=?',
                                 (user_id, 'github'))
        i = existing['id'] if existing else uid()
        created = existing['created_at'] if existing else now()
        with self.repo.connect() as c:
            c.execute('delete from user_integrations where user_id=? and provider=?', (user_id, 'github'))
            self.repo.insert(c, 'user_integrations', {
                'id': i, 'user_id': user_id, 'provider': 'github', 'base_url': url,
                'token_encrypted': sealed, 'token_fingerprint': fingerprint, 'status': 'UNVERIFIED',
                'account_login': '', 'scopes': '', 'last_checked_at': '', 'last_error': '',
                'created_at': created, 'updated_at': now()})
        return self.config(user_id)

    def _token(self, user_id):
        row = self.repo.one('select token_encrypted from user_integrations where user_id=? and provider=?',
                            (user_id, 'github'))
        if not row:
            raise ValueError('github_not_configured')
        from cryptography.fernet import InvalidToken
        try:
            return _key().decrypt(row['token_encrypted'].encode()).decode()
        except InvalidToken as exc:
            raise ValueError('github_token_unreadable_reenter_credentials') from exc

    def disconnect(self, user_id):
        """Remove the current user's GitHub credential and connection state."""
        with self.repo.connect() as c:
            c.execute(
                'delete from user_integrations where user_id=? and provider=?',
                (user_id, 'github'),
            )
        return {'configured': False, 'provider': 'github', 'status': 'NOT_CONFIGURED'}

    def token_for(self, user_id):
        """The decrypted token, for another backend service to use directly.

        Unlike ``connect``, this makes no outbound call -- it exists for
        RepoCloneService, which needs the credential to authenticate its own
        git subprocess. Never wire this to an HTTP route.
        """
        return self._token(user_id)

    def connect(self, user_id):
        """Verify the stored credential against the configured GitHub.

        Returns a status the UI can render, and on failure records and returns
        the upstream detail so the operator can retry with a correction.
        """
        row = self.repo.one('select * from user_integrations where user_id=? and provider=?', (user_id, 'github'))
        if not row:
            raise ValueError('github_not_configured')
        token = self._token(user_id)
        url = api_base(row['base_url']) + '/user'
        request = urllib.request.Request(url, method='GET', headers={
            'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=ssl.create_default_context()) as resp:
                body = json.loads(resp.read().decode('utf-8') or '{}')
                scopes = resp.headers.get('X-OAuth-Scopes', '') or ''
                return self._record(row['id'], user_id, 'CONNECTED', body.get('login', ''), scopes, '')
        except urllib.error.HTTPError as exc:
            detail = self._detail(exc)
            reason = {401: 'github_credentials_rejected', 403: 'github_access_forbidden',
                      404: 'github_api_not_found_check_url'}.get(exc.code, 'github_http_error')
            self._record(row['id'], user_id, 'FAILED', '', '', '%s (HTTP %d): %s' % (reason, exc.code, detail))
            raise PermissionError('%s|HTTP %d|%s' % (reason, exc.code, detail))
        except urllib.error.URLError as exc:
            detail = str(getattr(exc, 'reason', exc))
            self._record(row['id'], user_id, 'FAILED', '', '', 'github_unreachable: ' + detail)
            raise ConnectionError('github_unreachable|0|' + detail)

    @staticmethod
    def _detail(exc):
        try:
            payload = json.loads(exc.read().decode('utf-8') or '{}')
            return str(payload.get('message') or exc.reason or 'no detail')[:300]
        except Exception:
            return str(getattr(exc, 'reason', 'no detail'))[:300]

    def _record(self, row_id, user_id, status, login, scopes, error):
        with self.repo.connect() as c:
            c.execute('update user_integrations set status=?,account_login=?,scopes=?,last_checked_at=?,'
                      'last_error=?,updated_at=? where id=?',
                      (status, login, scopes, now(), error, now(), row_id))
        return self.config(user_id)
