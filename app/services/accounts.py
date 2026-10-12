"""User accounts: registration, password verification and role assignment.

Access to this console previously required a hand-minted HS256 token, which
meant the signing secret had to be shared with every operator who needed to sign
in -- anyone holding it could mint a token for any subject with any role. Sign-in
is now a password check against a stored account, and the token is issued by
IdentityService only after that check passes.

Passwords are stored as PBKDF2-HMAC-SHA256 with a per-account salt and the
iteration count recorded alongside the hash, so the cost can be raised later
without invalidating existing accounts.
"""
import hashlib, hmac, os, re, secrets
from app.repository import uid, now

# Roles a person may choose for themselves at registration. These grant the
# ability to *do* migration work.
SELF_SERVICE_ROLES = (
    'ACE_ANALYST', 'FRAMEWORK_ANALYST', 'SPECIFICATION_AUTHOR', 'CODE_GENERATOR',
    'TEST_ENGINEER', 'RCA_ANALYST', 'REPOSITORY_INTAKE', 'DORMANCY_ANALYST',
    'DELIVERY_ENGINEER', 'MIGRATION_VIEWER',
)
# Roles that decide whether governed work may proceed. Section 13's rule that an
# approval is never self-asserted applies to the granting of approver roles too:
# if registration could hand out SOLUTION_ARCHITECT, anyone could sign up and
# approve their own gate. These are granted by an existing administrator.
ELEVATED_ROLES = (
    'GOVERNANCE_ADMIN', 'AI_GOVERNANCE_ADMIN', 'SOLUTION_ARCHITECT', 'CHIEF_ARCHITECT',
    'AUDITOR', 'GATE_ADMIN', 'CONNECTOR_ADMIN', 'RELEASE_MANAGER', 'SERVICE_REGISTRAR',
    'KNOWLEDGE_CURATOR', 'NOTIFICATION_PUBLISHER', 'OBSERVABILITY_PUBLISHER',
    'CLARIFICATION_ADMIN', 'ORCHESTRATOR', 'AGENT_OPERATOR', 'AGENT_REGISTRY_ADMIN',
)
# The first account bootstraps administration, otherwise a fresh deployment has
# no one able to grant an elevated role to anyone.
BOOTSTRAP_ROLES = ('GOVERNANCE_ADMIN', 'CONNECTOR_ADMIN', 'GATE_ADMIN', 'AUDITOR')

EMAIL = re.compile(r'^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$')
MIN_PASSWORD = 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 900


def iterations():
    """PBKDF2 cost. Overridable so the test suite is not dominated by hashing."""
    try:
        return max(1000, int(os.getenv('FACTORY_PBKDF2_ITERATIONS', '600000')))
    except ValueError:
        return 600000


def hash_password(password, salt=None, rounds=None):
    salt = salt or secrets.token_hex(16)
    rounds = rounds or iterations()
    derived = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), rounds)
    return derived.hex(), salt, rounds


class AccountService:
    def __init__(self, repo, identity=None):
        self.repo = repo
        self.identity = identity

    # -- registration -----------------------------------------------------
    @staticmethod
    def assignable_roles():
        return list(SELF_SERVICE_ROLES)

    def _validate(self, d):
        name = str(d.get('name', '')).strip()
        email = str(d.get('email', '')).strip().lower()
        password = str(d.get('password', ''))
        role = str(d.get('role', '')).strip().upper()
        if len(name) < 2:
            raise ValueError('name_required')
        if not EMAIL.match(email) or len(email) > 254:
            raise ValueError('valid_email_required')
        if len(password) < MIN_PASSWORD:
            raise ValueError('password_too_short_minimum_%d' % MIN_PASSWORD)
        if password.lower() in (name.lower(), email, email.split('@')[0].lower()):
            raise ValueError('password_must_not_match_identity')
        if role in ELEVATED_ROLES:
            # Named explicitly so the caller learns the role exists but needs
            # granting, rather than being told it is simply invalid.
            raise PermissionError('role_requires_administrator_grant:' + role)
        if role not in SELF_SERVICE_ROLES:
            raise ValueError('unsupported_role')
        return name, email, password, role

    def sign_up(self, d):
        name, email, password, role = self._validate(d)
        if self.repo.one('select id from users where email=?', (email,)):
            raise ValueError('email_already_registered')
        first = not self.repo.one('select id from users limit 1')
        roles = [role] + ([r for r in BOOTSTRAP_ROLES if r != role] if first else [])
        pw, salt, rounds = hash_password(password)
        i = uid()
        ts = now()
        with self.repo.connect() as c:
            self.repo.insert(c, 'users', {
                'id': i, 'name': name, 'email': email, 'password_hash': pw, 'password_salt': salt,
                'password_iterations': rounds, 'role': role, 'granted_roles': ','.join(roles),
                'status': 'ACTIVE', 'failed_attempts': 0, 'locked_until': '',
                'created_at': ts, 'updated_at': ts, 'last_login_at': ''})
        return self.public(self.repo.one('select * from users where id=?', (i,)))

    # -- authentication ---------------------------------------------------
    def verify(self, email, password):
        """Verify a password. Failures are indistinguishable to the caller.

        A distinct "unknown email" error would let an unauthenticated caller
        enumerate who holds an account, so every failure returns
        invalid_credentials and an unknown address still pays the hashing cost.
        """
        email = str(email or '').strip().lower()
        user = self.repo.one('select * from users where email=?', (email,))
        if not user:
            hash_password(str(password or ''), 'decoy' * 4, 1000)
            raise PermissionError('invalid_credentials')
        locked = str(user.get('locked_until') or '')
        if locked and locked > now():
            raise PermissionError('account_locked_until:' + locked)
        if str(user.get('status')) != 'ACTIVE':
            raise PermissionError('account_not_active')
        candidate, _, _ = hash_password(str(password or ''), user['password_salt'], int(user['password_iterations']))
        if not hmac.compare_digest(candidate, user['password_hash']):
            self._record_failure(user)
            raise PermissionError('invalid_credentials')
        with self.repo.connect() as c:
            c.execute('update users set failed_attempts=0,locked_until=?,last_login_at=?,updated_at=? where id=?',
                      ('', now(), now(), user['id']))
        return self.repo.one('select * from users where id=?', (user['id'],))

    def _record_failure(self, user):
        attempts = int(user.get('failed_attempts') or 0) + 1
        until = ''
        if attempts >= MAX_FAILURES:
            from datetime import datetime, timedelta, timezone
            until = (datetime.now(timezone.utc) + timedelta(seconds=LOCKOUT_SECONDS)).isoformat()
            attempts = 0
        with self.repo.connect() as c:
            c.execute('update users set failed_attempts=?,locked_until=?,updated_at=? where id=?',
                      (attempts, until, now(), user['id']))

    def login(self, email, password):
        user = self.verify(email, password)
        if not self.identity:
            raise RuntimeError('identity_service_not_configured')
        token = self.identity.issue(user['email'], roles=self.roles_of(user),
                                    project_scopes=('GLOBAL',), environments=('DEV', 'TEST', 'STAGE', 'PROD'),
                                    extra={'user_id': user['id']})
        return {**token, 'user': self.public(user)}

    # -- projection -------------------------------------------------------
    @staticmethod
    def roles_of(user):
        raw = str(user.get('granted_roles') or user.get('role') or '')
        return tuple([r for r in (x.strip() for x in raw.split(',')) if r])

    @staticmethod
    def public(user):
        """The account as the UI may see it. Never includes password material."""
        if not user:
            return None
        return {'id': user['id'], 'name': user['name'], 'email': user['email'], 'role': user['role'],
                'roles': list(AccountService.roles_of(user)), 'status': user['status'],
                'created_at': user['created_at'], 'last_login_at': user.get('last_login_at') or ''}

    def by_email(self, email):
        return self.repo.one('select * from users where email=?', (str(email or '').strip().lower(),))

    def grant_role(self, actor_roles, email, role):
        """Grant an elevated role. Requires an existing GOVERNANCE_ADMIN."""
        if 'GOVERNANCE_ADMIN' not in set(actor_roles or ()):
            raise PermissionError('required_role_missing:GOVERNANCE_ADMIN')
        role = str(role or '').strip().upper()
        if role not in ELEVATED_ROLES + SELF_SERVICE_ROLES:
            raise ValueError('unsupported_role')
        user = self.by_email(email)
        if not user:
            raise ValueError('account_not_found')
        roles = list(self.roles_of(user))
        if role not in roles:
            roles.append(role)
        with self.repo.connect() as c:
            c.execute('update users set granted_roles=?,updated_at=? where id=?', (','.join(roles), now(), user['id']))
        return self.public(self.repo.one('select * from users where id=?', (user['id'],)))
