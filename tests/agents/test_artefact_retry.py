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


def test_a_truncated_answer_is_retried(repo, job, gateway):
    """What CvscodeClaudeAdapter raises when max_tokens cut the JSON short."""
    gateway(PROMPT)
    provider = _Scripted(RuntimeError('provider_response_truncated:max_tokens=32768'), VALID)
    _, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert provenance['attempts'] == 2


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
