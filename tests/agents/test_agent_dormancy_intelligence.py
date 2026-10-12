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
    assert result.reasoning['reason'] == 'no_approved_prompt_or_model'
    assert result.outputs['classification'] == 'ACTIVE'


def test_the_model_is_a_second_opinion_not_the_record(repo, job, gateway, provider):
    """A model may disagree; the persisted classification stays deterministic."""
    gateway(DormancyIntelligenceAgent.PROMPT)
    provider.answers['default'] = {'classification': 'ACTIVE', 'decisive_evidence': 'traffic',
                                   'missing_evidence': ['telemetry window']}
    result = DormancyIntelligenceAgent(repo).run(
        job['id'], params={'traffic_count': 0, 'last_seen_days': 120, 'deployed': True,
                           'already_migrated': False}, provider=provider)
    assert result.reasoning['mode'] == MODEL_GATEWAY
    assert result.outputs['classification'] == 'DORMANT'
    assert result.metrics['model_classification'] == 'ACTIVE'
    assert result.metrics['model_agrees'] is False
    assert repo.one('select classification from dormancy_assessments where id=?',
                    (result.outputs['evidence'],))['classification'] == 'DORMANT'
