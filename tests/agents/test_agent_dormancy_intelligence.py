import json
from app.agents.dormancy_intelligence import DormancyIntelligenceAgent
from app.agents.reasoning import DETERMINISTIC, MODEL_GATEWAY


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


def test_without_an_approved_prompt_the_deterministic_path_answers(repo, job):
    result = DormancyIntelligenceAgent(repo).run(job['id'], params={'traffic_count': 5, 'deployed': True})
    assert result.reasoning['mode'] == DETERMINISTIC
    # The prompts and aliases are seeded APPROVED by default now, so what is
    # absent out of the box is the provider adapter, not the approval.
    assert result.reasoning['reason'] == 'no_provider_execution_adapter'
    assert result.outputs['classification'] == 'ACTIVE'


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
    """The schema's enum is what stops an invented classification being stored."""
    gateway(DormancyIntelligenceAgent.PROMPT)
    provider.answers['default'] = {'classification': 'PROBABLY_FINE'}
    result = DormancyIntelligenceAgent(repo).run(
        job['id'], params={'traffic_count': 5, 'deployed': True}, provider=provider)
    assert result.reasoning['mode'] == DETERMINISTIC
    assert 'model_response_schema_invalid' in result.reasoning['reason']
    assert result.outputs['classification'] == 'ACTIVE'
