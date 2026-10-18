"""The contract that replaced the deterministic fallback.

``produce`` has one job now: return a schema-valid model artefact or raise. What
makes that safe to ship rather than brittle is the retry, and what makes the
retry useful rather than wasteful is the classification -- some reasons can
plausibly answer differently next time and some are certainties.

Four properties are pinned here.

**A retryable reason is re-asked up to the budget.** One truncated response or
one 401 arriving mid-pipeline must not end a migration that has already spent an
hour.

**A terminal reason fails on the first attempt.** An unapproved prompt stays
unapproved and the same payload trips the same DLP rule, so spending two more
model calls on it only makes the failure slower and the error later.

**A schema failure is re-asked with the violation.** Otherwise a retry is three
identical questions, and the one failure the model can actually correct is the
one the retry would not help with.

**Every reason the gateway can emit is classified.** The default is terminal, so
a reason nobody classified would silently stop being retried; the last test
reads them out of ``reasoning.py`` so adding one without classifying it fails
here rather than in production.
"""
import re
from pathlib import Path

import pytest

from app.agents import artefact
from app.agents.artefact import (ArtefactUnavailable, RETRYABLE, TERMINAL,
                                 produce, produce_many, retryable)
from app.agents.dormancy_intelligence import DormancyIntelligenceAgent

PROMPT = DormancyIntelligenceAgent.PROMPT
VALID = {'classification': 'ACTIVE', 'rationale': 'traffic observed'}


class _Scripted:
    """Returns, or raises, whatever the script says for each attempt."""

    accepts_skill = True

    def __init__(self, *script):
        self.script = list(script)
        self.payloads = []

    def invoke(self, template, payload, skill=None):
        self.payloads.append(payload)
        step = self.script[min(len(self.payloads) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


def _context(repo, job, provider):
    agent = DormancyIntelligenceAgent(repo)
    return agent.context(job['id'], provider=provider)


# ---- retryable -----------------------------------------------------------------------------

def test_a_provider_failure_is_re_asked_to_the_budget(repo, job, gateway):
    gateway(PROMPT)
    provider = _Scripted(RuntimeError('provider_error:Timeout:read timed out'))
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert raised.value.attempts == artefact.ATTEMPTS == 3
    assert len(provider.payloads) == 3
    assert 'read timed out' in raised.value.reason


def test_a_failure_that_clears_on_the_second_attempt_succeeds(repo, job, gateway):
    """The case the retry exists for: a token refreshed between calls."""
    gateway(PROMPT)
    provider = _Scripted(RuntimeError('provider_error:AuthenticationError:401'), VALID)
    answer, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert answer['classification'] == 'ACTIVE'
    assert provenance['attempts'] == 2
    assert provenance['mode'] == 'MODEL_GATEWAY'


def test_a_truncated_answer_is_not_retried(repo, job, gateway):
    """What CvscodeClaudeAdapter raises when max_tokens cut the JSON short.

    Observed on the specification and then the mapping catalogue: three
    attempts of about ten minutes each, truncated identically every time. The
    request does not change between attempts, so neither does the length.
    """
    gateway(PROMPT)
    provider = _Scripted(RuntimeError('provider_response_truncated:max_tokens=32768'), VALID)
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert raised.value.attempts == 1
    assert len(provider.payloads) == 1, 'VALID was never reached; a second try is a wasted ten minutes'
    assert retryable('provider_execution_failed:provider_response_truncated:max_tokens=32768') is False


# ---- terminal ------------------------------------------------------------------------------

@pytest.mark.pristine_registry
def test_an_unapproved_prompt_fails_on_the_first_attempt(repo, job):
    provider = _Scripted(VALID)
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert raised.value.reason == 'no_approved_prompt_or_model'
    assert raised.value.attempts == 1
    assert provider.payloads == [], 'the provider is never reached, let alone three times'


def test_a_dlp_refusal_fails_on_the_first_attempt(repo, job, gateway):
    gateway(PROMPT)
    provider = _Scripted(VALID)
    with pytest.raises(ArtefactUnavailable) as raised:
        # 16 digits trips the PAN rule, and will trip it again identically.
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1234567890123456})
    assert raised.value.reason.startswith('gateway_refused:')
    assert raised.value.attempts == 1
    assert provider.payloads == []


def test_an_oversized_payload_fails_without_asking(repo, job, gateway):
    """It does not get smaller by asking again; splitting it is the caller's job."""
    gateway(PROMPT)
    provider = _Scripted(VALID)
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT,
                {'traffic_count': 1, 'blob': 'x' * (artefact.MAX_PAYLOAD_CHARS + 1)})
    assert raised.value.reason.startswith('payload_too_large:')
    assert raised.value.attempts == 0, 'nothing was attempted'
    assert provider.payloads == []


# ---- the correction loop --------------------------------------------------------------------

def test_a_schema_failure_is_re_asked_with_the_violation(repo, job, gateway):
    gateway(PROMPT)
    provider = _Scripted({'classification': 'PROBABLY_FINE'}, VALID)
    answer, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert answer == VALID
    assert provenance['attempts'] == 2
    correction = provider.payloads[1]['_correction']
    assert 'rejected' in correction
    assert 'classification' in correction, 'the violated path'
    assert 'PROBABLY_FINE' not in correction or 'classification' in correction
    # The keys that did come back are named, because a wrong shape is usually a
    # wrapper and seeing what arrived is what lets the model fix it.
    assert "['classification']" in correction
    assert provenance['corrections'] == [correction]


def test_the_first_attempt_carries_no_correction(repo, job, gateway):
    gateway(PROMPT)
    provider = _Scripted(VALID)
    produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert '_correction' not in provider.payloads[0]


def test_a_caller_validator_travels_the_same_retryable_path(repo, job, gateway):
    """``migration-specification`` needs this: its document has its own schema.

    A constraint the response schema cannot express still has to be correctable,
    or the agent would fail on an answer the model could have fixed.
    """
    gateway(PROMPT)
    seen = []

    def validator(answer):
        seen.append(answer)
        return '' if len(seen) > 1 else 'rationale must mention the evidence'

    provider = _Scripted(VALID, VALID)
    _, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1},
                            validator=validator)
    assert provenance['attempts'] == 2
    assert 'rationale must mention the evidence' in provider.payloads[1]['_correction']


def test_a_validator_that_never_passes_fails_the_agent(repo, job, gateway):
    gateway(PROMPT)
    provider = _Scripted(VALID)
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1},
                validator=lambda answer: 'never acceptable')
    assert 'never acceptable' in raised.value.reason
    assert raised.value.attempts == 3


# ---- produce_many --------------------------------------------------------------------------

def test_produce_many_is_all_or_nothing(repo, job, gateway):
    """One item the model cannot produce fails the batch.

    It used to degrade that item and keep the rest, which left an artefact that
    was part model-authored and part computed with nothing to say which.
    """
    gateway(PROMPT)

    def answer(payload):
        return VALID if payload['traffic_count'] == 1 else {'classification': 'NONSENSE'}

    class _PerItem:
        accepts_skill = True

        def __init__(self):
            self.count = 0

        def invoke(self, template, payload, skill=None):
            self.count += 1
            return answer(payload)

    provider = _PerItem()
    with pytest.raises(ArtefactUnavailable):
        produce_many(_context(repo, job, provider), PROMPT,
                     [('a', {'traffic_count': 1}), ('b', {'traffic_count': 2})])


def test_produce_many_with_nothing_to_ask_is_not_a_failure(repo, job, gateway):
    """An ACE repository with no message flows in it.

    Nothing was asked because there was nothing to ask about, which is a third
    thing: not a model outage and not a model-authored artefact. The caller
    decides whether an empty result is acceptable for its own artefact.
    """
    gateway(PROMPT)
    produced, provenance = produce_many(_context(repo, job, _Scripted(VALID)), PROMPT, [])
    assert produced == {}
    assert provenance['mode'] == 'MODEL_NOT_CONSULTED'
    assert provenance['reason'] == 'no_items_to_produce'


# ---- the classification is complete ---------------------------------------------------------

def test_every_reason_the_gateway_can_emit_is_classified():
    """The default is terminal, so an unclassified reason stops being retried.

    Read out of ``reasoning.py`` rather than listed here: a new blocker added
    there without a decision about whether it is worth re-asking would
    otherwise become terminal by omission, which for a transient failure means
    a dead migration.
    """
    source = (Path(artefact.__file__).parent / 'reasoning.py').read_text(encoding='utf-8')
    emitted = set(re.findall(r"provenance\['reason'\] = '([a-z_]+)", source))
    assert emitted, 'the regex has to keep finding them'
    unclassified = sorted(emitted - set(RETRYABLE) - set(TERMINAL))
    assert unclassified == [], 'classify these in artefact.RETRYABLE or artefact.TERMINAL'


def test_the_two_classifications_do_not_overlap():
    assert not set(RETRYABLE) & set(TERMINAL)


# ---- the backoff ---------------------------------------------------------------------------

def test_a_provider_failure_waits_before_the_next_attempt(monkeypatch):
    """Observed live: the gateway rejects a valid key intermittently.

    Calls either side of the failure succeeded with the same key fingerprint,
    and three attempts fired inside a few hundred milliseconds all got 401 --
    so without a wait the retry cannot absorb the one failure it most needs to.
    Asserted on the computed delay rather than by sleeping.
    """
    monkeypatch.delenv('MOJO_RETRY_BACKOFF_SECONDS', raising=False)
    assert artefact.backoff(1) == 0.0, 'the first attempt never waits'
    assert artefact.backoff(2) == 1.0
    assert artefact.backoff(3) == 3.0
    # Past the end of the table the last delay repeats rather than resetting.
    assert artefact.backoff(9) == 3.0


def test_the_backoff_is_configurable_and_survives_a_bad_value(monkeypatch):
    monkeypatch.setenv('MOJO_RETRY_BACKOFF_SECONDS', '0.5,2')
    assert (artefact.backoff(2), artefact.backoff(3)) == (0.5, 2.0)
    monkeypatch.setenv('MOJO_RETRY_BACKOFF_SECONDS', '0,0')
    assert (artefact.backoff(2), artefact.backoff(3)) == (0.0, 0.0)
    # A malformed value falls back to the defaults rather than crashing a run
    # or, worse, silently disabling the wait.
    monkeypatch.setenv('MOJO_RETRY_BACKOFF_SECONDS', 'soon,later')
    assert artefact.backoff(2) == artefact.BACKOFF_SECONDS[0]
    monkeypatch.setenv('MOJO_RETRY_BACKOFF_SECONDS', '')
    assert artefact.backoff(2) == 0.0, 'empty means no wait, explicitly'


# ---- diagnosing a dead credential ----------------------------------------------------------

AUTH_401 = ("provider_error:AuthenticationError:Error code: 401 - "
            "{'detail': 'Invalid token. Please check the token and try again.'}")


def test_a_401_on_every_attempt_says_the_credential_is_the_problem(repo, job, gateway):
    """The retry works and still cannot win, so the error has to say why.

    Observed live: two source files authored at 07:34 and 07:35, then every
    call from 07:36:03 onward returning "Invalid token" across a 47-second
    retry window. The token expired mid-run, ``get_cvs_api_key`` re-read the
    same expired one on each attempt, and the error reported the raw SDK text
    three times -- which reads as a flaky call rather than a dead credential,
    and is exactly how a correctly-retrying run got read as one that never
    retried.
    """
    gateway(PROMPT)
    provider = _Scripted(RuntimeError(AUTH_401))
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert raised.value.attempts == 3, 'the retry still happens -- the first 401 may be transient'
    assert len(provider.payloads) == 3, 'three real attempts were made'
    message = raised.value.reason
    # The prefix is untouched, so classification and every test matching on it
    # still work.
    assert message.startswith('provider_execution_failed:')
    assert retryable(message) is True
    # And the operator is told the thing they cannot see from three identical
    # 401s: that this one is not worth retrying, and what to do instead.
    assert 'all 3 attempts failed authentication' in message
    assert 're-authenticate' in message


def test_one_401_among_other_failures_is_not_diagnosed_as_a_dead_credential(repo, job, gateway):
    """A credential that worked once in the window is flaky, not dead.

    Claiming otherwise would send an operator to re-authenticate a perfectly
    good token while the real fault was the network.
    """
    gateway(PROMPT)
    provider = _Scripted(RuntimeError('provider_error:APITimeoutError:read timed out'),
                         RuntimeError(AUTH_401),
                         RuntimeError(AUTH_401))
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert 'failed authentication' not in raised.value.reason


def test_a_401_that_clears_is_never_diagnosed_at_all(repo, job, gateway):
    """The transient kind, which is the common one: one in thirty-seven calls."""
    gateway(PROMPT)
    provider = _Scripted(RuntimeError(AUTH_401), VALID)
    _, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert provenance['attempts'] == 2
    assert provenance['mode'] == 'MODEL_GATEWAY'


def test_the_retry_is_announced_before_it_happens(repo, job, gateway):
    """Three identical invocation lines are indistinguishable from one.

    Which is why a run that retried correctly was reported as a run that never
    retried: the only evidence was a count in the final error.
    """
    class _Recording:
        def __init__(self):
            self.lines = []
        info = property(lambda self: self.lines.append)

        def warn(self, message):
            self.lines.append(message)

    gateway(PROMPT)
    logger = _Recording()
    provider = _Scripted(RuntimeError(AUTH_401), VALID)
    context = _context(repo, job, provider)
    context.logger = logger
    produce(context, PROMPT, {'traffic_count': 1})
    assert any('attempt 2 of 3' in str(line) for line in logger.lines), logger.lines


def test_a_schema_failure_does_not_wait(repo, job, gateway, monkeypatch):
    """The failure is in the output, not in time, so waiting changes nothing.

    The correction is what makes the next attempt different. Sleeping here would
    slow every repair down for no benefit.
    """
    gateway(PROMPT)
    monkeypatch.setenv('MOJO_RETRY_BACKOFF_SECONDS', '30,30')
    slept = []
    monkeypatch.setattr(artefact.time, 'sleep', lambda s: slept.append(s))
    provider = _Scripted({'classification': 'PROBABLY_FINE'}, VALID)
    _, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert provenance['attempts'] == 2
    assert slept == [], 'a schema repair is immediate'


def test_a_provider_failure_actually_sleeps(repo, job, gateway, monkeypatch):
    gateway(PROMPT)
    monkeypatch.setenv('MOJO_RETRY_BACKOFF_SECONDS', '7,11')
    slept = []
    monkeypatch.setattr(artefact.time, 'sleep', lambda s: slept.append(s))
    provider = _Scripted(RuntimeError('provider_error:AuthenticationError:401'), VALID)
    produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert slept == [7.0]


@pytest.mark.parametrize('reason,expected', [
    ('provider_execution_failed:anything', True),
    ('model_response_schema_invalid:anything', True),
    ('gateway_refused:dlp_violation:PAN', False),
    ('no_approved_prompt_or_model', False),
    ('no_provider_execution_adapter', False),
    ('payload_too_large:200000_chars_over_180000', False),
    ('provider_alias_unavailable:embeddings', False),
    # Unrecognised is terminal: a path nobody anticipated fails with a precise
    # name rather than spending the whole budget discovering the same thing.
    ('something_nobody_classified', False),
    ('', False),
    (None, False),
])
def test_the_prefix_is_what_decides(reason, expected):
    assert retryable(reason) is expected


# ---- a violation names the rule, not the value ---------------------------------------------

def test_a_size_violation_says_how_far_over_it_was():
    """Observed: "risks: [{'category': 'BLOCKING', ..." -- the value, with the rule cut off.

    Three attempts failed identically because neither the operator nor the
    correction sent back to the model said what to change.
    """
    from app.agents.artefact import _describe
    from jsonschema import Draft202012Validator
    schema = {'type': 'object', 'properties': {
        'risks': {'type': 'array', 'maxItems': 2},
        's': {'type': 'string', 'maxLength': 3},
        'n': {'type': 'array', 'minItems': 2}}}
    def first(doc):
        return next(iter(Draft202012Validator(schema).iter_errors(doc)))
    assert _describe(first({'risks': [{'k': 'v' * 500}] * 5})) == 'has 5 items; at most 2 allowed'
    assert _describe(first({'s': 'abcdef'})) == 'is 6 characters; at most 3 allowed'
    assert _describe(first({'n': [1]})) == 'has 1 items; at least 2 required'
