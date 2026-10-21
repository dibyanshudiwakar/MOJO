"""Approved guidance reaches the model's request; unapproved lessons do not."""
from app.services import learning


def _seed(repo, agent, text):
    learning.record_run(repo, run_id='seed-' + text[:6], job_id='another-job', agent_id=agent, agent_version='1',
                        status='COMPLETED', started_at='2026-10-20T10:00:00+00:00',
                        reasoning={'prompt_name': 'p', 'all_corrections': ['Schema violation: ' + text]})
    return next(x for x in learning.overview(repo)['lessons'] if text in x['situation'])


def test_approved_guidance_is_in_every_request_with_its_note(repo, job, ace_repo, dispatch, model):
    lesson = _seed(repo, 'repository-intake', 'the path field was left out')
    learning.decide(repo, lesson['id'], 'APPROVE', 'curator', guidance='Always return the path field.')
    model.calls.clear()

    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})

    assert model.calls, 'the agent reached the model'
    for call in model.calls:
        assert call['payload']['_lessons'] == ['Always return the path field.']
        assert 'precedence' in call['payload']['_lessons_note']


def test_a_lesson_nobody_approved_is_never_sent(repo, job, ace_repo, dispatch, model):
    _seed(repo, 'repository-intake', 'something else went wrong')
    model.calls.clear()
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    assert model.calls and all('_lessons' not in c['payload'] for c in model.calls)


def test_the_run_that_used_a_lesson_is_counted_against_it(repo, job, ace_repo, dispatch, model):
    lesson = _seed(repo, 'repository-intake', 'the path field was left out')
    learning.decide(repo, lesson['id'], 'APPROVE', 'curator', guidance='Always return the path field.')
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    used = next(x for x in learning.overview(repo)['lessons'] if x['id'] == lesson['id'])
    assert used['applied'] >= 1
