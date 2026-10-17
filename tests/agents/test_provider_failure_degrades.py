"""A provider failure degrades the run; it does not take the agent down.

``ReasoningGateway`` exists to make the model optional: when it cannot answer,
the agent computes instead and the run records why. That contract was broken for
the one exception type every shipped adapter actually raises.
``CvscodeClaudeAdapter`` wraps an SDK or network failure as
``provider_error:<type>:<detail>``, a gateway refusal as ``provider_refused``
and a missing key as ``cvscode_api_key_unavailable`` -- all ``RuntimeError`` --
and ``reason()`` caught only ``PermissionError``, ``ValueError`` and
``NotImplementedError``.

So an expired gateway token failed the whole agent. Observed in practice:
governance died mid-pipeline on
``RuntimeError:provider_error:AuthenticationError:Error code: 401 -
{'detail': 'Authorization header missing'}`` while four agents either side of it
had reached the model successfully.
"""
import pytest

from app.agents.reasoning import DETERMINISTIC, MODEL_GATEWAY, ReasoningGateway


class _Boom:
    """An adapter that fails the way the real ones fail."""

    def __init__(self, exc):
        self.exc = exc

    def invoke(self, template, payload):
        raise self.exc


@pytest.fixture
def ask(repo, job, gateway):
    """A reasoning call against an approved prompt and model."""
    prompt = 'agent/dormancy-intelligence/classification-rationale'
    gateway(prompt, model_name='reasoning-small')

    class _Context:
        job_id = job['id']
        correlation_id = ''
        agent_id = 'dormancy-intelligence'
        effective_model_alias = 'reasoning-small'
        binding_source = 'DEFAULT'
        logger = None
        provider = None

        def skill(self):
            return None

    def _ask(exc):
        context = _Context()
        context.provider = _Boom(exc)
        return ReasoningGateway(repo).reason(context, prompt, {'traffic_count': 1})
    return _ask


@pytest.mark.parametrize('exc,expected', [
    # Exactly what the CVS gateway adapter raises on an expired token.
    (RuntimeError("provider_error:AuthenticationError:Error code: 401 - "
                  "{'detail': 'Authorization header missing'}"), '401'),
    (RuntimeError('provider_refused:stop_details'), 'provider_refused'),
    (RuntimeError('cvscode_api_key_unavailable: install cvscode'), 'cvscode_api_key_unavailable'),
    (ValueError('bad payload'), 'bad payload'),
    (PermissionError('refused'), 'refused'),
    (NotImplementedError('no adapter'), 'no adapter'),
])
def test_a_failing_provider_degrades_and_says_why(ask, exc, expected):
    answer, provenance = ask(exc)
    assert answer is None, 'no answer means the agent computes instead'
    assert provenance['mode'] == DETERMINISTIC
    assert provenance['reason'].startswith('provider_execution_failed:')
    assert expected in provenance['reason'], 'the cause has to survive into the record'


def test_a_401_does_not_raise_out_of_reason(ask):
    """The regression, stated as its own case.

    This is what failed the governance step: the adapter's RuntimeError escaped
    ``reason()`` entirely, so the agent raised instead of falling back.
    """
    ask(RuntimeError("provider_error:AuthenticationError:Error code: 401"))


@pytest.mark.parametrize('exc', [KeyboardInterrupt(), MemoryError(), SystemExit()])
def test_what_is_not_a_degradation_still_propagates(ask, exc):
    """Catching RuntimeError must not have become catching everything.

    An interrupt or an exhausted machine is not the model declining to answer,
    and recording either as a deterministic fallback would hide it.
    """
    with pytest.raises(type(exc)):
        ask(exc)


def test_a_working_provider_is_unaffected(repo, job, gateway, provider):
    """The fix must not change the path where the model does answer."""
    prompt = 'agent/dormancy-intelligence/classification-rationale'
    gateway(prompt, model_name='reasoning-small')
    provider.answers['default'] = {'classification': 'ACTIVE'}

    class _Context:
        job_id = job['id']
        correlation_id = ''
        agent_id = 'dormancy-intelligence'
        effective_model_alias = 'reasoning-small'
        binding_source = 'DEFAULT'
        logger = None

        def skill(self):
            return None
    context = _Context()
    context.provider = provider
    answer, provenance = ReasoningGateway(repo).reason(context, prompt, {'traffic_count': 1})
    assert answer == {'classification': 'ACTIVE'}
    assert provenance['mode'] == MODEL_GATEWAY
