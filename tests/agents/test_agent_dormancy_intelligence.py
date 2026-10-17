import json
import pytest
from app.agents.artefact import ArtefactUnavailable
from app.agents.dormancy_intelligence import DormancyIntelligenceAgent
from app.agents.reasoning import MODEL_GATEWAY


def test_dormancy_classifies_and_records_confidence(repo, job):
    result = DormancyIntelligenceAgent(repo).run(
        job['id'], params={'traffic_count': 0, 'last_seen_days': 120,
                           'deployed': True, 'already_migrated': False})
    assert result.outputs['classification'] == 'DORMANT'
    assert result.outputs['confidence'] == 1.0
    row = repo.one('select * from dormancy_assessments where id=?', (result.outputs['evidence'],))
    assert row['classification'] == 'DORMANT'
    ref, = result.evidence
    assert json.loads(ref.content)['classification'] == 'DORMANT'


def test_absent_signals_lower_confidence_rather_than_reading_as_negative(repo, job):
    result = DormancyIntelligenceAgent(repo).run(job['id'], params={'traffic_count': 0})
    assert result.outputs['classification'] == 'UNKNOWN'
    assert result.outputs['confidence'] == 0.25


@pytest.mark.pristine_registry
def test_without_an_approved_prompt_the_agent_fails_rather_than_computing(repo, job):
    """What used to be the normal mode is now a failure, on purpose.

    This test previously asserted that an unapproved prompt produced a
    deterministic classification and a COMPLETED run. That is precisely what
    hid a switched-off model for a whole session: a recorded fallback is a
    successful run, so an operator saw a healthy pipeline and no indication
    Claude was never reached.
    """
    with pytest.raises(ArtefactUnavailable) as raised:
        DormancyIntelligenceAgent(repo).run(
            job['id'], params={'traffic_count': 5, 'deployed': True})
    assert raised.value.reason == 'no_approved_prompt_or_model'
    # First attempt, not the third: an unapproved prompt will still be
    # unapproved on a retry, so the budget is not spent discovering that.
    assert raised.value.attempts == 1
    assert 'no_approved_prompt_or_model' in str(raised.value)


@pytest.mark.no_model
def test_without_a_provider_the_agent_fails(repo, job):
    """A configured gateway with nothing behind it is still nothing."""
    with pytest.raises(ArtefactUnavailable) as raised:
        DormancyIntelligenceAgent(repo).run(
            job['id'], params={'traffic_count': 5, 'deployed': True})
    assert raised.value.reason == 'no_provider_execution_adapter'
    assert raised.value.attempts == 1


def test_nothing_is_recorded_when_the_model_cannot_answer(repo, job):
    """No half-written assessment behind a failed run.

    The classification is persisted by ``record`` *after* the verdict exists,
    so a failure leaves the table empty rather than leaving an UNKNOWN row a
    later gate could bind.
    """
    with pytest.raises(ArtefactUnavailable):
        DormancyIntelligenceAgent(repo).run(
            job['id'], params={'traffic_count': 5},
            provider=_Refusing())
    assert repo.one('select count(*) n from dormancy_assessments where job_id=?',
                    (job['id'],))['n'] == 0


class _Refusing:
    """A provider that fails every call the way an expired token does."""

    accepts_skill = True

    def invoke(self, template, payload, skill=None):
        raise RuntimeError('provider_error:AuthenticationError:Error code: 401')


def test_the_model_classifies_and_a_disagreement_is_recorded_not_hidden(
        repo, job, gateway, provider):
    """The model's classification is the record; the rules' one is kept beside it.

    This test used to assert the opposite -- that the deterministic
    classification stayed authoritative and the model was only a second
    opinion. That was reversed deliberately: agents now produce their artefacts
    through the model, with the deterministic path as a recorded fallback.

    What survives from the old contract is the part worth keeping. The computed
    classification is still recorded, and ``agrees_with_rules`` still says
    whether the two matched, because this verdict decides whether a migration
    proceeds at all and "the rules and the model differed here" is worth more
    than either answer alone. What changed is only which one is persisted.
    """
    gateway(DormancyIntelligenceAgent.PROMPT)
    provider.answers['default'] = {'classification': 'ACTIVE', 'decisive_evidence': 'traffic',
                                   'missing_evidence': ['telemetry window'],
                                   'rationale': 'traffic observed in the window'}
    result = DormancyIntelligenceAgent(repo).run(
        job['id'], params={'traffic_count': 0, 'last_seen_days': 120, 'deployed': True,
                           'already_migrated': False}, provider=provider)
    assert result.reasoning['mode'] == MODEL_GATEWAY
    assert result.reasoning['artefact_source'] == 'MODEL'
    assert result.outputs['classification'] == 'ACTIVE'
    assert repo.one('select classification,rationale from dormancy_assessments where id=?',
                    (result.outputs['evidence'],))['classification'] == 'ACTIVE'
    # The rules' verdict and the disagreement are both still on the record.
    assert result.metrics['computed_classification'] == 'DORMANT'
    assert result.metrics['agrees_with_rules'] is False
    assert result.metrics['missing_evidence'] == ['telemetry window']


def test_confidence_stays_measured_even_when_the_model_classifies(repo, job, gateway, provider):
    """Confidence is evidence coverage, not the model's self-assessment.

    A model reporting high confidence on two of four signals would otherwise
    raise the figure a gate reads without any more evidence existing.
    """
    gateway(DormancyIntelligenceAgent.PROMPT)
    provider.answers['default'] = {'classification': 'DORMANT', 'confidence': 0.99}
    result = DormancyIntelligenceAgent(repo).run(
        job['id'], params={'traffic_count': 0, 'last_seen_days': 120}, provider=provider)
    assert result.outputs['classification'] == 'DORMANT'
    # Two of four signals supplied: 0.5, regardless of what the model claimed.
    assert result.outputs['confidence'] == 0.5
    assert result.metrics['model_confidence'] == 0.99


def test_a_classification_outside_the_permitted_set_is_refused(repo, job, gateway, provider):
    """The schema's enum is what stops an invented classification being stored.

    It is retried rather than refused outright -- a wrong shape is the one
    failure the model can plausibly correct when told what was wrong -- but a
    model that keeps inventing classifications fails the agent instead of
    having its answer quietly replaced by the rules'.
    """
    gateway(DormancyIntelligenceAgent.PROMPT)
    provider.answers['default'] = {'classification': 'PROBABLY_FINE'}
    with pytest.raises(ArtefactUnavailable) as raised:
        DormancyIntelligenceAgent(repo).run(
            job['id'], params={'traffic_count': 5, 'deployed': True}, provider=provider)
    assert 'model_response_schema_invalid' in raised.value.reason
    assert raised.value.attempts == 3, 'a bad shape is worth re-asking about'
    assert len(provider.calls) == 3


def test_a_corrected_shape_on_the_second_attempt_is_accepted(repo, job, gateway):
    """The retry is a repair loop, not three identical questions."""
    gateway(DormancyIntelligenceAgent.PROMPT)

    class _Learns:
        accepts_skill = True

        def __init__(self):
            self.payloads = []

        def invoke(self, template, payload, skill=None):
            self.payloads.append(payload)
            if len(self.payloads) == 1:
                return {'classification': 'PROBABLY_FINE'}
            return {'classification': 'ACTIVE', 'rationale': 'traffic observed'}

    provider = _Learns()
    result = DormancyIntelligenceAgent(repo).run(
        job['id'], params={'traffic_count': 5, 'deployed': True}, provider=provider)
    assert result.reasoning['mode'] == MODEL_GATEWAY
    assert result.reasoning['attempts'] == 2
    assert result.outputs['classification'] == 'ACTIVE'
    # The second question carried what was wrong with the first answer.
    assert 'PROBABLY_FINE' in provider.payloads[1]['_correction']
    assert 'classification' in provider.payloads[1]['_correction']
    assert '_correction' not in provider.payloads[0]
    assert result.reasoning['corrections']
