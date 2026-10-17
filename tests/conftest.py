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
# Nothing in the suite should reach a *real* model provider. default_provider()
# returns None unless this is exactly 'gateway', but it is pinned so a
# developer's shell cannot turn the suite into a live gateway client. The
# ``model`` fixture below supplies a double instead.
os.environ['AI_PROVIDER'] = 'none'
# Repository seeds prompt_registry, model_registry and a model-gateway profile
# APPROVED on init, and the suite now runs with that on. It was off here while
# agents had a deterministic fallback -- a pristine database was what let a test
# reach the no_approved_prompt_or_model path, and the fallback made that path a
# working one. It is not any more: an unapproved prompt fails the agent, so a
# test that dispatches one needs the governance rows the same way production
# does. ``@pytest.mark.pristine_registry`` opts a test back out.
os.environ.setdefault('FACTORY_SEED_MODEL_GATEWAY', '1')


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


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'no_model: run without a provider adapter, so the agent fails')
    config.addinivalue_line(
        'markers', 'pristine_registry: no seeded prompts or models, so nothing routes')


@pytest.fixture(autouse=True)
def _registry_seeding(request, monkeypatch):
    """Honour ``pristine_registry`` before any Repository is built.

    Autouse and ordered ahead of the fixtures that construct a repository,
    because ``Repository.init`` reads the variable once at construction. A test
    that builds its own repository later in the body is unaffected either way.
    """
    if request.node.get_closest_marker('pristine_registry'):
        monkeypatch.setenv('FACTORY_SEED_MODEL_GATEWAY', '0')
    else:
        monkeypatch.setenv('FACTORY_SEED_MODEL_GATEWAY', '1')


@pytest.fixture(autouse=True)
def model(request, monkeypatch, _registry_seeding):
    """The provider every agent reasons through, injected wherever none was given.

    With no deterministic fallback left, ``artefact.produce`` either returns a
    schema-valid model answer or raises -- so an agent with no provider does not
    complete at all, and a test that dispatches one needs a provider whether or
    not the model is its subject. Injected at ``BaseAgent.context`` rather than
    threaded through every call site, because ``provider=`` on a hundred of them
    would say nothing a reader needs at each one.

    A test that passes its own provider still wins: this only fills an absent
    one, and an explicit ``provider=None`` defeats it, which is how a test
    asserts the no-provider failure for one call without disarming the fixtures
    that set its inputs up.

    Returned so a test can read ``calls``/``skills`` or install ``overrides``.
    """
    from app.agents.base import BaseAgent
    from tests.agents.model_double import ModelDouble
    double = ModelDouble()
    if request.node.get_closest_marker('no_model'):
        return double
    original = BaseAgent.context

    def context(self, job_id, **kwargs):
        kwargs.setdefault('provider', double)
        return original(self, job_id, **kwargs)

    monkeypatch.setattr(BaseAgent, 'context', context, raising=True)
    # AgentRegistry.dispatch resolves its own default when the caller passes
    # none, reaching for app.providers.default_provider -- which returns None
    # under AI_PROVIDER=none. Patched at the name dispatch imported it under.
    monkeypatch.setattr('app.services.agents.default_provider', lambda: double,
                        raising=True)
    return double
