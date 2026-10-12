"""Filesystem confinement for caller-supplied paths.

Several endpoints accept a repository or output directory from the request
body. Unconfined, those let any caller walk or write arbitrary parts of the
host filesystem -- a scan of ``C:/Windows/System32`` was reachable without a
token. Section 15 requires build and test work to run against restricted
workspaces, and section 14 requires least privilege.

Every path arriving from outside the trust boundary must pass through
``resolve()``. Services keep accepting plain paths so they remain unit
testable; the HTTP layer is responsible for confining what it forwards.
"""
import os
from pathlib import Path

ENV_ROOT = 'FACTORY_WORKSPACE_ROOT'


class WorkspaceError(PermissionError):
    """Raised when a supplied path escapes the approved workspace root."""


def root():
    """The approved workspace root.

    Defaults to ``<repo>/workspace`` so the factory is confined out of the box
    rather than depending on an operator remembering to set a variable.
    """
    configured = os.getenv(ENV_ROOT)
    base = Path(configured) if configured else Path(__file__).resolve().parents[2] / 'workspace'
    return base.resolve()


def resolve(candidate, must_exist=True, create=False):
    """Resolve ``candidate`` and assert it stays inside the workspace root.

    Symlinks are resolved before the containment check so a link inside the
    workspace cannot be used to reach outside it.
    """
    if candidate is None or not str(candidate).strip():
        raise WorkspaceError('workspace_path_required')
    base = root()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError('workspace_root_unavailable:%s' % base) from exc

    target = Path(str(candidate))
    target = (base / target) if not target.is_absolute() else target
    target = target.resolve()

    if target != base and base not in target.parents:
        raise WorkspaceError('path_outside_workspace_root')
    if create:
        target.mkdir(parents=True, exist_ok=True)
    elif must_exist and not target.exists():
        raise WorkspaceError('workspace_path_not_found')
    return target


def runtime_url(value):
    """Validate a caller-supplied runtime base URL before an agent calls it.

    The test engineering agent replays differential cases against an ACE runtime
    and a generated Spring service. Over HTTP those base URLs arrive in a request
    body, which makes them an outbound request the caller chooses -- so an
    unvalidated value would let a token holder aim the factory at the cloud
    metadata endpoint or at an internal host it cannot otherwise reach, and read
    the response back out of the differential evidence.

    Restricted to https with a resolvable dotted hostname, no embedded
    credentials, and no loopback, link-local or private literal. Reaching a
    genuinely internal runtime is a connector profile's job, not a request body's.
    """
    from urllib.parse import urlparse
    text = str(value or '').strip()
    if not text:
        raise WorkspaceError('runtime_url_required')
    parsed = urlparse(text)
    if parsed.scheme != 'https':
        raise WorkspaceError('runtime_url_must_be_https')
    if parsed.username or parsed.password:
        raise WorkspaceError('runtime_url_must_not_embed_credentials')
    host = (parsed.hostname or '').lower()
    if not host or '.' not in host:
        raise WorkspaceError('runtime_url_host_not_qualified')
    blocked = ('localhost', '127.', '0.', '10.', '169.254.', '192.168.', '::1')
    if host.startswith(blocked) or host.endswith('.localhost'):
        raise WorkspaceError('runtime_url_host_not_permitted')
    if host.startswith('172.'):
        try:
            second = int(host.split('.')[1])
        except (IndexError, ValueError):
            second = -1
        if 16 <= second <= 31:
            raise WorkspaceError('runtime_url_host_not_permitted')
    return text


SAFE_REF = None


def git_ref(value):
    """Validate a branch or tag name before it reaches ``git`` as an argument.

    A value beginning with ``-`` would be parsed by git as an option rather
    than a ref, so refs are restricted to a conservative character set and may
    not start with a dash or dot.
    """
    import re
    global SAFE_REF
    if SAFE_REF is None:
        SAFE_REF = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,126}$')
    text = str(value or '')
    if not SAFE_REF.match(text) or '..' in text or text.endswith('.lock'):
        raise WorkspaceError('invalid_git_ref')
    return text
