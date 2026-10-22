"""Questions saved without a suggestion can be given one afterwards.

A run saves the questions it raises and then asks the model for a draft answer to
each; stopped part-way, or with the model unreachable, the questions stay and the
drafts never come. These cover asking again for just those.
"""
import pytest

from app.agents.artefact import ArtefactUnavailable
from app.services import suggestions
from app.services.agents import AgentRegistry
from tests.agents.test_precedents import _ask, raises  # noqa: F401  (raises is a fixture)


@pytest.fixture
def open_without_suggestions(repo, job, ace_repo, dispatch, raises, auto_mode_off):
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'What is the context root?')
    with repo.connect() as c:
        c.execute("update clarifications set suggested_answer='', suggestion_confidence='', suggested_at='' "
                  "where job_id=?", (job['id'],))
    rows = repo.rows("select id from clarifications where job_id=? and status='OPEN'", (job['id'],))
    assert rows, 'the fixture must leave open questions behind'
    return rows


def _suggested(repo, job):
    return repo.rows("select id from clarifications where job_id=? and status='OPEN' "
                     "and trim(coalesce(suggested_answer,''))<>''", (job['id'],))


def test_every_open_question_without_a_suggestion_gets_one(repo, job, model, open_without_suggestions):
    result = suggestions.suggest_missing(AgentRegistry(repo), job['id'], provider=model)
    assert result['requested'] == len(open_without_suggestions)
    assert result['suggested'] == result['requested'] and result['errors'] == []
    assert len(_suggested(repo, job)) == len(open_without_suggestions)


def test_it_only_suggests_and_answers_nothing(repo, job, model, open_without_suggestions):
    suggestions.suggest_missing(AgentRegistry(repo), job['id'], provider=model)
    still_open = repo.rows("select id from clarifications where job_id=? and status='OPEN'", (job['id'],))
    assert len(still_open) == len(open_without_suggestions), 'no question was answered on the model\'s say-so'


def test_a_suggestion_already_there_is_left_alone(repo, job, model, open_without_suggestions):
    with repo.connect() as c:
        c.execute("update clarifications set suggested_answer='mine' where id=?", (open_without_suggestions[0]['id'],))
    result = suggestions.suggest_missing(AgentRegistry(repo), job['id'], provider=model)
    assert result['requested'] == len(open_without_suggestions) - 1
    assert repo.one('select suggested_answer from clarifications where id=?',
                    (open_without_suggestions[0]['id'],))['suggested_answer'] == 'mine'


def test_nothing_missing_is_a_quiet_no_op(repo, job):
    assert suggestions.suggest_missing(AgentRegistry(repo), job['id']) == {'requested': 0, 'suggested': 0, 'errors': []}


def test_a_model_failure_is_reported_not_raised(repo, job, model, open_without_suggestions, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ArtefactUnavailable(suggestions.PROMPT, 'provider_execution_failed:provider_error:boom', 3, {})
    monkeypatch.setattr(suggestions, 'produce', unavailable)
    result = suggestions.suggest_missing(AgentRegistry(repo), job['id'], provider=model)
    assert 'boom' in result['errors'][0], 'the model failure is still reported'
    assert result['baseline'] == len(open_without_suggestions)
    assert len(_suggested(repo, job)) == len(open_without_suggestions), 'but no question is left bare'
    modes = {r['suggestion_mode'] for r in repo.rows(
        "select suggestion_mode from clarifications where job_id=? and status='OPEN'", (job['id'],))}
    assert modes == {'STANDARDS_BASELINE'}, 'and it says plainly that this is the platform standard'


def test_a_standards_placeholder_is_replaced_once_the_model_answers(repo, job, model, open_without_suggestions,
                                                                   monkeypatch):
    real = suggestions.produce

    def unavailable(*args, **kwargs):
        raise ArtefactUnavailable(suggestions.PROMPT, 'provider_execution_failed:provider_error:boom', 3, {})
    monkeypatch.setattr(suggestions, 'produce', unavailable)
    suggestions.suggest_missing(AgentRegistry(repo), job['id'], provider=model)
    monkeypatch.setattr(suggestions, 'produce', real)

    suggestions.suggest_missing(AgentRegistry(repo), job['id'], provider=model)
    modes = {r['suggestion_mode'] for r in repo.rows(
        "select suggestion_mode from clarifications where job_id=? and status='OPEN'", (job['id'],))}
    assert 'STANDARDS_BASELINE' not in modes
