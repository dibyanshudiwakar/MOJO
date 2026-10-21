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

    def connect(self, user_id, user_email=None):
        """Verify the stored credential against the configured GitHub.

        Returns a status the UI can render, and on failure records and returns
        the upstream detail so the operator can retry with a correction.

        user_email: when provided, the token's GitHub identity must resolve to
        this address, preventing one user from registering another's token.
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

        # GitHub credentials accepted. Now verify the token belongs to this user.
        owner_note = ''
        if user_email:
            try:
                owner_note = self._check_owner(user_email, body, scopes, token, api_base(row['base_url']))
            except PermissionError as exc:
                self._record(row['id'], user_id, 'FAILED', body.get('login', ''), scopes, str(exc))
                raise
        return self._record(row['id'], user_id, 'CONNECTED', body.get('login', ''), scopes, owner_note)

    # Scopes that grant access to /user/emails on GitHub.com and GHEC.
    _EMAIL_SCOPES = frozenset(('user', 'user:email', 'read:user'))

    @staticmethod
    def _base_email(email):
        """Normalise an email for ownership comparison.

        GitHub Enterprise Cloud appends the organisation slug as a plus-tag
        (e.g. somya.paliwal+cvsh@cvshealth.com).  Stripping the tag lets us
        compare it against the plain corporate address stored in the app.
        """
        local, _, domain = email.lower().strip().partition('@')
        return local.partition('+')[0] + '@' + domain

    def _check_owner(self, user_email, github_body, scopes, token, base_url):
        """Verify the token's GitHub identity matches the logged-in user's email.

        Strategy (most reliable source wins, comparison is always case-insensitive):

        1. All emails via /user/emails when any email-granting scope is present.
           Includes SAML/SCIM-provisioned addresses which may not be marked
           verified=true on GitHub Enterprise Cloud.
        2. Public profile email from /user response — reliable in enterprise
           deployments where accounts use the same corporate email for both the
           app and GitHub.
        3. No email accessible at all — allow with an audit note; the
           account_login is still captured for administrator review.

        Error messages include the GitHub-returned email so mismatches are
        diagnosable without server logs.
        """
        # Normalise the app email once; all GitHub emails are normalised on the fly.
        normalized = self._base_email(user_email)
        login = github_body.get('login') or 'unknown'

        # Emails from /user/emails — include all (not just verified=true) because
        # SAML-provisioned addresses on GitHub Enterprise Cloud are often
        # verified=false even though they genuinely belong to the account.
        scope_set = {s.strip() for s in scopes.split(',')} if scopes else set()
        if scope_set & self._EMAIL_SCOPES:
            all_emails = set()
            try:
                req = urllib.request.Request(
                    base_url + '/user/emails', method='GET',
                    headers={'Authorization': 'Bearer ' + token,
                             'Accept': 'application/vnd.github+json',
                             'X-GitHub-Api-Version': '2022-11-28',
                             'User-Agent': USER_AGENT})
                with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as r:
                    for entry in json.loads(r.read().decode('utf-8') or '[]'):
                        if isinstance(entry, dict) and entry.get('email'):
                            all_emails.add(self._base_email(entry['email']))
            except Exception:
                pass  # best-effort; fall through to public-email check
            if all_emails:
                if normalized not in all_emails:
                    raise PermissionError(
                        'github_token_owner_mismatch|@%s|GitHub emails (%s) do not include %s'
                        % (login, ', '.join(sorted(all_emails)), user_email))
                return ''

        # Public profile email from /user — strip plus-tag and compare.
        public_email = self._base_email(github_body.get('email') or '')
        if public_email and public_email != '@':
            if normalized != public_email:
                raise PermissionError(
                    'github_token_owner_mismatch|@%s|GitHub profile email %s does not match %s'
                    % (login, public_email, user_email))
            return ''

        # No email accessible at all — allow with an audit note.
        return ('github_owner_unverifiable: token owner @%s has no accessible email; '
                'add user:email or read:user scope to enforce ownership' % login)

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
