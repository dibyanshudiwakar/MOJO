"""Session-wide test configuration.

Two things every test in this suite needs, neither of which was wired up.

**Password hashing cost.** ``accounts.iterations()`` reads
``FACTORY_PBKDF2_ITERATIONS`` and its docstring says the value exists so "the
test suite is not dominated by hashing" -- but nothing ever set it, so every
sign-up and every sign-in ran the production 600,000 rounds. Lowered here to
the floor ``iterations()`` enforces.

**Workspace root.** ``workspace.resolve`` confines every caller-supplied path
to ``FACTORY_WORKSPACE_ROOT``, defaulting to ``<repo>/workspace``. Tests that
hand a service a ``tmp_path`` were therefore refused with
``path_outside_workspace_root``, and services that resolve internally reported
``repository_not_accessible``. The root is pointed at a per-session temporary
directory so a test can use its own ``tmp_path`` subtree, and so a test run can
never read or write the developer's real ``workspace/``.

Both are set with ``setdefault`` so an individual test, or the developer, can
still override them.
"""
import os
import tempfile

import pytest

_SESSION_ROOT = tempfile.mkdtemp(prefix='ace-factory-tests-')

os.environ.setdefault('FACTORY_PBKDF2_ITERATIONS', '1000')
os.environ.setdefault('FACTORY_WORKSPACE_ROOT', _SESSION_ROOT)
# Nothing in the suite should reach a model provider. default_provider() returns
# None unless this is exactly 'gateway', but it is pinned so a developer's shell
# cannot turn the suite into a live gateway client.
os.environ['AI_PROVIDER'] = 'none'


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    """Point the workspace root at this test's own ``tmp_path``.

    ``workspace.resolve`` confines every caller-supplied path to
    ``FACTORY_WORKSPACE_ROOT``, so a test that builds a tree under its
    ``tmp_path`` and hands those paths to a service was refused with
    ``path_outside_workspace_root`` -- or, where the service resolves
    internally, the less obvious ``repository_not_accessible``. Between them
    those two accounted for the large majority of the suite's failures.

    Autouse because it is a property of running under pytest at all, not
    something each test should have to remember. ``workspace.root()`` re-reads
    the variable on every call, so there is nothing cached to invalidate, and a
    test that wants a different root just sets the variable itself afterwards.
    """
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(tmp_path))
    return tmp_path
