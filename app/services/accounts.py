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
#
# Every required_role in config/agent-catalogue.v1.json for an agent the
# pipeline wizard can select must appear here, or picking that agent produces
# required_agent_role_missing at dispatch for an account that ticked every box
# on the signup form. MIGRATION_PLANNER, VARIANCE_ANALYST,
# REMEDIATION_ENGINEER and GOVERNANCE_ENGINE were in neither this list nor
# ELEVATED_ROLES, so planning, variance-analysis, remediation and governance
# were unrunnable by any account the console could create; KNOWLEDGE_CURATOR
# was elevated-only for the same reason. None of the five grants approval
# authority -- they only execute agents -- so self-service is correct for them.
#
# ORCHESTRATOR stays in ELEVATED_ROLES deliberately: it also authorises
# POST /api/v2/jobs/<id>/transition, i.e. driving the lifecycle by hand, and
# migration-orchestrator is excluded from wizard selection by
# SELECTABLE_AGENTS_EXCLUDED, so no pipeline needs it.
SELF_SERVICE_ROLES = (
    'ACE_ANALYST', 'FRAMEWORK_ANALYST', 'SPECIFICATION_AUTHOR', 'CODE_GENERATOR',
    'TEST_ENGINEER', 'RCA_ANALYST', 'REPOSITORY_INTAKE', 'DORMANCY_ANALYST',
    'DELIVERY_ENGINEER',
    'MIGRATION_PLANNER', 'VARIANCE_ANALYST', 'REMEDIATION_ENGINEER',
    'GOVERNANCE_ENGINE', 'KNOWLEDGE_CURATOR', 'FEEDBACK_ENGINEER',
)
# The six roles approval_policies names as the approver for G0-G5. They must be
# grantable, or a pending package is invisible to everyone:
# ApprovalWorkspaceService.inbox_for drops any package whose gate's
# required_role the caller does not hold, so a gate with no possible approver
# can never be actioned from the console. Five of these were previously in
# neither list below, which made them unreachable through both registration and
# grant_role -- only scripts/grant_tester_roles.py writing straight to the
# database could set them.
GATE_APPROVER_ROLES = (
    'SERVICE_OWNER',    # G0
    'JAVA_ARCHITECT',   # G1
    'ACE_SME',          # G2
    'PRODUCT_OWNER',    # G3
    'QA_LEAD',          # G4
    'RELEASE_MANAGER',  # G5
)
# Roles that decide whether governed work may proceed. Section 13's rule that an
# approval is never self-asserted applies to the granting of approver roles too:
# if registration could hand out PRODUCT_OWNER, anyone could sign up and
# approve their own gate. These are granted by an existing administrator.
# GATE_APPROVER_ROLES are folded in here, deliberately NOT into
# SELF_SERVICE_ROLES, for exactly that reason.
# Anything already self-service is filtered out, so the two lists cannot
# disagree. _validate tests ELEVATED_ROLES first and refuses those outright, so
# a role appearing in both would be elevated-only and the self-service entry
# would silently do nothing.
ELEVATED_ROLES = tuple(r for r in dict.fromkeys((
    'GOVERNANCE_ADMIN', 'AI_GOVERNANCE_ADMIN',
    'AUDITOR', 'GATE_ADMIN', 'CONNECTOR_ADMIN', 'RELEASE_MANAGER', 'SERVICE_REGISTRAR',
    'KNOWLEDGE_CURATOR', 'NOTIFICATION_PUBLISHER', 'OBSERVABILITY_PUBLISHER',
    'CLARIFICATION_ADMIN', 'ORCHESTRATOR', 'AGENT_OPERATOR', 'AGENT_REGISTRY_ADMIN',
) + GATE_APPROVER_ROLES) if r not in SELF_SERVICE_ROLES)
# The administrator account seeded on first setup (seed_admin). It administers the
# platform -- who holds which role, gate configuration, model and agent settings,
# connectors, the audit trail -- and nothing else. It holds no agent role and no gate
# approver role, and none can be granted to it, so it can neither run nor approve a
# migration: the person who configures the controls is not the person they govern.
ADMIN_ROLES = ('GOVERNANCE_ADMIN', 'AI_GOVERNANCE_ADMIN', 'AGENT_REGISTRY_ADMIN', 'GATE_ADMIN',
               'CONNECTOR_ADMIN', 'CLARIFICATION_ADMIN', 'AUDITOR')
DEFAULT_ADMIN_EMAIL = 'admin@ace.local'


def is_admin_only(roles):
    """True for an account whose roles are all administrative: it must not operate pipelines."""
    roles = {r for r in (roles or ()) if r}
    return bool(roles) and roles <= set(ADMIN_ROLES)

# Roles an earlier build offered that nothing enforces. No endpoint, agent or
# gate policy requires them, so holding one grants nothing; they are stripped
# from stored accounts at startup so the console shows only roles that matter.
RETIRED_ROLES = ('MIGRATION_VIEWER', 'SOLUTION_ARCHITECT', 'CHIEF_ARCHITECT')

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

    def prune_retired_roles(self):
        """Remove RETIRED_ROLES from every stored account. Returns accounts changed."""
        changed = 0
        for user in self.repo.rows('select * from users'):
            roles = [r for r in self.roles_of(user) if r not in RETIRED_ROLES]
            primary = user['role'] if user['role'] not in RETIRED_ROLES else (roles[0] if roles else '')
            if ','.join(roles) != str(user.get('granted_roles') or '') or primary != user['role']:
                with self.repo.connect() as c:
                    c.execute('update users set granted_roles=?,role=?,updated_at=? where id=?',
                              (','.join(roles), primary, now(), user['id']))
                changed += 1
        return changed

    # -- registration -----------------------------------------------------
    @staticmethod
    def assignable_roles():
        return list(SELF_SERVICE_ROLES)

    def _validate(self, d):
        name = str(d.get('name', '')).strip()
        email = str(d.get('email', '')).strip().lower()
        password = str(d.get('password', ''))
        # Accept either a single 'role' string (legacy) or a 'roles' list.
        raw_roles = d.get('roles') or []
        if isinstance(raw_roles, str):
            raw_roles = [r.strip() for r in raw_roles.split(',') if r.strip()]
        if not raw_roles:
            single = str(d.get('role', '')).strip().upper()
            raw_roles = [single] if single else []
        roles = [r.strip().upper() for r in raw_roles if r.strip()]
        if len(name) < 2:
            raise ValueError('name_required')
        if not EMAIL.match(email) or len(email) > 254:
            raise ValueError('valid_email_required')
        if len(password) < MIN_PASSWORD:
            raise ValueError('password_too_short_minimum_%d' % MIN_PASSWORD)
        if password.lower() in (name.lower(), email, email.split('@')[0].lower()):
            raise ValueError('password_must_not_match_identity')
        if not roles:
            raise ValueError('at_least_one_role_required')
        for role in roles:
            if role in ELEVATED_ROLES:
                raise PermissionError('role_requires_administrator_grant:' + role)
            if role not in SELF_SERVICE_ROLES:
                raise ValueError('unsupported_role:' + role)
        return name, email, password, roles

    @staticmethod
    def requestable_roles():
        """Approver roles a person may ask for at signup; an administrator decides."""
        return list(GATE_APPROVER_ROLES)

    def _requested(self, d):
        raw = d.get('requested_roles') or []
        wanted = list(dict.fromkeys(str(r).strip().upper() for r in raw if str(r).strip()))
        for role in wanted:
            if role not in GATE_APPROVER_ROLES:
                raise ValueError('unsupported_role:' + role)
        return wanted

    def sign_up(self, d):
        name, email, password, roles = self._validate(d)
        requested = self._requested(d)
        if self.repo.one('select id from users where email=?', (email,)):
            raise ValueError('email_already_registered')
        # The first role in the list is stored as the primary display role. Nobody
        # becomes an administrator by registering first: that account is seeded.
        primary = roles[0]
        all_roles = list(dict.fromkeys(roles))
        pw, salt, rounds = hash_password(password)
        i = uid()
        ts = now()
        with self.repo.connect() as c:
            self.repo.insert(c, 'users', {
                'id': i, 'name': name, 'email': email, 'password_hash': pw, 'password_salt': salt,
                'password_iterations': rounds, 'role': primary, 'granted_roles': ','.join(all_roles),
                'status': 'ACTIVE', 'failed_attempts': 0, 'locked_until': '',
                'created_at': ts, 'updated_at': ts, 'last_login_at': ''})
            # A request, not a grant: nothing here gives the account approval
            # authority until an administrator decides it (decide_request).
            for role in requested:
                if role not in all_roles:
                    self.repo.insert(c, 'role_requests', {
                        'id': uid(), 'user_id': i, 'email': email, 'role': role,
                        'status': 'PENDING', 'requested_at': ts, 'decided_by': '', 'decided_at': ''})
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

    def seed_admin(self, email=None, password=None):
        """Create the administrator on first setup. Returns ``(email, password)`` when it
        created one, else None.

        Seeded only while no account holds GOVERNANCE_ADMIN, which is what a fresh
        database looks like, so restarting never creates a second one. The address and
        password come from FACTORY_ADMIN_EMAIL / FACTORY_ADMIN_PASSWORD; with no password
        set a random one is generated and returned for the caller to show once.
        """
        if any('GOVERNANCE_ADMIN' in self.roles_of(u) and u['status'] == 'ACTIVE'
               for u in self.repo.rows('select * from users')):
            return None
        email = str(email or os.getenv('FACTORY_ADMIN_EMAIL') or DEFAULT_ADMIN_EMAIL).strip().lower()
        if not EMAIL.match(email):
            raise ValueError('invalid_admin_email')
        password = password or os.getenv('FACTORY_ADMIN_PASSWORD') or secrets.token_urlsafe(18)
        if len(password) < MIN_PASSWORD:
            raise ValueError('admin_password_too_short')
        if self.by_email(email):
            return None          # the address is taken by a non-admin account: do not take it over
        pw, salt, rounds = hash_password(password)
        i, ts = uid(), now()
        with self.repo.connect() as c:
            self.repo.insert(c, 'users', {
                'id': i, 'name': 'Platform Administrator', 'email': email, 'password_hash': pw,
                'password_salt': salt, 'password_iterations': rounds, 'role': ADMIN_ROLES[0],
                'granted_roles': ','.join(ADMIN_ROLES), 'status': 'ACTIVE', 'failed_attempts': 0,
                'locked_until': '', 'created_at': ts, 'updated_at': ts, 'last_login_at': ''})
        return email, password

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
        if is_admin_only(roles) and role not in ADMIN_ROLES:
            raise ValueError('admin_account_cannot_hold:' + role)
        if role not in roles:
            roles.append(role)
        with self.repo.connect() as c:
            c.execute('update users set granted_roles=?,updated_at=? where id=?', (','.join(roles), now(), user['id']))
        return self.public(self.repo.one('select * from users where id=?', (user['id'],)))

    # -- administration ---------------------------------------------------
    @staticmethod
    def grantable_roles():
        return list(dict.fromkeys(SELF_SERVICE_ROLES + ELEVATED_ROLES))

    @staticmethod
    def _require_admin(actor_roles):
        if 'GOVERNANCE_ADMIN' not in set(actor_roles or ()):
            raise PermissionError('required_role_missing:GOVERNANCE_ADMIN')

    def access_overview(self, actor_roles):
        """Accounts with their roles, and the open requests for approver roles."""
        self._require_admin(actor_roles)
        users = [{'name': u['name'], 'email': u['email'], 'status': u['status'],
                  'roles': list(self.roles_of(u))} for u in self.repo.rows('select * from users order by email')]
        requests = self.repo.rows("select id,email,role,requested_at from role_requests "
                                  "where status='PENDING' order by requested_at")
        return {'users': users, 'requests': requests, 'grantable': self.grantable_roles(),
                'gate_roles': list(GATE_APPROVER_ROLES)}

    def revoke_role(self, actor_roles, actor, email, role):
        self._require_admin(actor_roles)
        role = str(role or '').strip().upper()
        user = self.by_email(email)
        if not user:
            raise ValueError('account_not_found')
        roles = [r for r in self.roles_of(user) if r != role]
        if role == 'GOVERNANCE_ADMIN':
            others = [u for u in self.repo.rows('select * from users') if u['id'] != user['id']
                      and 'GOVERNANCE_ADMIN' in self.roles_of(u) and u['status'] == 'ACTIVE']
            if not others:
                raise ValueError('cannot_remove_the_last_governance_admin')
        primary = user['role'] if user['role'] != role else (roles[0] if roles else '')
        with self.repo.connect() as c:
            c.execute('update users set granted_roles=?,role=?,updated_at=? where id=?',
                      (','.join(roles), primary, now(), user['id']))
        return self.public(self.repo.one('select * from users where id=?', (user['id'],)))

    def decide_request(self, actor_roles, actor, request_id, decision):
        """Approve (grants the role) or reject a signup request for an approver role."""
        self._require_admin(actor_roles)
        decision = str(decision or '').strip().upper()
        if decision not in ('APPROVE', 'REJECT'):
            raise ValueError('decision_must_be_APPROVE_or_REJECT')
        req = self.repo.one("select * from role_requests where id=? and status='PENDING'", (request_id,))
        if not req:
            raise ValueError('request_not_found_or_already_decided')
        if decision == 'APPROVE':
            self.grant_role(actor_roles, req['email'], req['role'])
        with self.repo.connect() as c:
            c.execute('update role_requests set status=?,decided_by=?,decided_at=? where id=?',
                      ('APPROVED' if decision == 'APPROVE' else 'REJECTED', actor, now(), request_id))
        return {'id': request_id, 'email': req['email'], 'role': req['role'], 'decision': decision}
