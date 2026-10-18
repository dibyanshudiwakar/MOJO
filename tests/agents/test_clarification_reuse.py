"""A question answered once is not asked again.

One job held the same four questions 4, 4, 3 and 2 times over: every re-run of
an agent raises its questions afresh, worded slightly differently, and each copy
waited on its own answer.
"""
import pytest

from app.repository import now, uid
from app.services import automode
from app.services.clarification_reuse import ACTOR, PriorAnswers, normalise

PROMPT = 'agent/ace-analysis/semantic-model'
SUGGEST = 'agent/clarification/suggested-answers'
QUESTION = 'Which context root does the REST API use behind Kong?'
AMBIGUITIES = [{'question': QUESTION, 'owner': 'ACE_SME', 'impact': 'routing',
                'status': 'OPEN', 'evidenceReference': 'Orders.msgflow#in'}]


class Scenario:
    """What the double does on the next run, set explicitly by each test."""

    def __init__(self):
        self.raise_next = False
        self.suggestion_requests = []


@pytest.fixture
def scenario(model):
    """ace-analysis raises QUESTION on the first source file of a run when asked to."""
    from tests.agents.model_double import ANSWERS
    base, state = ANSWERS[PROMPT], Scenario()

    def answer(payload):
        out = dict(base(payload))
        if state.raise_next:
            state.raise_next = False
            out['ambiguities'] = AMBIGUITIES
        return out

    def suggest(payload):
        questions = [q['question'] for q in payload.get('questions') or []]
        state.suggestion_requests.append(questions)
        return {'suggestions': [
            {'question': q, 'suggested_answer': 'model guess', 'confidence': 'MEDIUM',
             'severity': 'LOW', 'caveat': ''} for q in questions]}

    model.overrides[PROMPT] = answer
    model.overrides[SUGGEST] = suggest
    return state


def _run(dispatch, scenario, job_id, ace_repo, raises=True):
    scenario.raise_next = raises
    dispatch(job_id, 'repository-intake', {'path': str(ace_repo)})
    return dispatch(job_id, 'ace-analysis', {'path': str(ace_repo)})


def _rows(repo, job_id):
    return repo.rows('select * from clarifications where job_id=? order by created_at', (job_id,))


def _a_person_answers(repo, job_id, text='Use /api/v2'):
    with repo.connect() as c:
        c.execute("update clarifications set status='ANSWERED',answer=?,answered_by='person@example.com',"
                  "answered_at=? where job_id=?", (text, now(), job_id))


def _seed(repo, job_id, rows, evidence_ref=''):
    with repo.connect() as c:
        for stage, question, answer, by in rows:
            repo.insert(c, 'clarifications', {
                'id': uid(), 'job_id': job_id, 'stage': stage, 'question': question, 'owner': 'o',
                'impact': 'i', 'evidence_ref': evidence_ref, 'status': 'ANSWERED', 'answer': answer,
                'created_at': now(), 'answered_at': now(), 'answered_by': by, 'run_id': ''})


# ---- matching ------------------------------------------------------------------------------

def test_normalise_ignores_case_spacing_and_punctuation():
    assert normalise('  Which Context-Root?? ') == normalise('which context root')


def test_an_exact_question_matches_regardless_of_wording_noise(repo, job):
    _seed(repo, job['id'], [('ACE_ANALYSIS', 'What is the context root?', '/v1', 'p@x')])
    hit = PriorAnswers(repo, job['id']).match('ACE_ANALYSIS', 'what is the  CONTEXT ROOT')
    assert hit and hit['answer'] == '/v1'


def test_a_close_rewording_in_the_same_stage_matches(repo, job):
    """The two wordings actually seen in one job."""
    _seed(repo, job['id'], [('ACE_ANALYSIS',
                             'The catch, failure and timeout error bodies and statuses that consumers parse',
                             'keep them', 'p@x')], evidence_ref='Orders.msgflow#catch')
    hit = PriorAnswers(repo, job['id']).match(
        'ACE_ANALYSIS', 'The catch, failure and timeout error bodies that consumers parse',
        'Orders.msgflow#catch')
    assert hit and hit['answer'] == 'keep them'


def test_a_rewording_about_a_different_construct_does_not_match(repo, job):
    """Alike in wording, different in subject: a different question."""
    _seed(repo, job['id'], [('ACE_ANALYSIS',
                             'The catch, failure and timeout error bodies and statuses that consumers parse',
                             'keep them', 'p@x')], evidence_ref='Orders.msgflow#catch')
    assert PriorAnswers(repo, job['id']).match(
        'ACE_ANALYSIS', 'The catch, failure and timeout error bodies that consumers parse',
        'Orders.msgflow#other') is None


def test_a_rewording_with_no_evidence_reference_does_not_match(repo, job):
    _seed(repo, job['id'], [('ACE_ANALYSIS',
                             'The catch, failure and timeout error bodies and statuses that consumers parse',
                             'keep them', 'p@x')], evidence_ref='Orders.msgflow#catch')
    assert PriorAnswers(repo, job['id']).match(
        'ACE_ANALYSIS', 'The catch, failure and timeout error bodies that consumers parse') is None


def test_a_rewording_in_another_stage_does_not_match(repo, job):
    _seed(repo, job['id'], [('ACE_ANALYSIS', 'The catch and failure error bodies that consumers parse', 'x', 'p')],
          evidence_ref='M#c')
    assert PriorAnswers(repo, job['id']).match(
        'PLANNING', 'The catch and failure error bodies that consumers parse in full', 'M#c') is None


def test_a_different_question_about_the_same_thing_does_not_match(repo, job):
    _seed(repo, job['id'], [('ACE_ANALYSIS', 'Which context root does the API use?', '/v1', 'p')])
    assert PriorAnswers(repo, job['id']).match(
        'ACE_ANALYSIS', 'Which queue manager does the reply node use?') is None


def test_an_empty_answer_is_never_reused(repo, job):
    _seed(repo, job['id'], [('S', 'Which queue?', '   ', 'p')])
    assert PriorAnswers(repo, job['id']).match('S', 'Which queue?') is None


def test_a_persons_answer_beats_a_machines(repo, job):
    _seed(repo, job['id'], [('S', 'Which queue?', 'machine', automode.ACTOR),
                            ('S', 'Which queue?', 'person', 'dev@example.com')])
    assert PriorAnswers(repo, job['id']).match('S', 'Which queue?')['answer'] == 'person'


# ---- end to end ----------------------------------------------------------------------------

def test_a_repeated_question_takes_the_earlier_answer(repo, job, ace_repo, dispatch, scenario):
    first = _run(dispatch, scenario, job['id'], ace_repo)
    assert first['clarification_ids'], 'the fixture guarantees the question is raised'
    _a_person_answers(repo, job['id'])
    sent_before = len(scenario.suggestion_requests)

    second = _run(dispatch, scenario, job['id'], ace_repo)

    assert second['clarification_ids'], 'the question was raised again'
    new = [r for r in _rows(repo, job['id']) if r['id'] in second['clarification_ids']]
    assert [r['status'] for r in new] == ['DUPLICATE'], 'recorded, but not another answered row to list'
    assert new[0]['answer'] == 'Use /api/v2' and new[0]['answered_by'] == ACTOR
    # Pointing back at the question a person actually answered.
    assert new[0]['reused_from'] in first['clarification_ids']
    # And not sent to the model for a draft it no longer needs.
    assert len(scenario.suggestion_requests) == sent_before


def test_a_question_nobody_answered_before_is_still_asked(repo, job, ace_repo, dispatch, scenario):
    outcome = _run(dispatch, scenario, job['id'], ace_repo)
    rows = [r for r in _rows(repo, job['id']) if r['id'] in outcome['clarification_ids']]
    assert rows and not any(r['answered_by'] == ACTOR for r in rows)
    assert any(QUESTION in q for q in scenario.suggestion_requests[0])


def test_a_repeat_in_another_job_of_the_same_service_is_reused(repo, job, factory, ace_repo,
                                                               dispatch, scenario):
    """A re-created job raises the same questions as the last one."""
    _run(dispatch, scenario, job['id'], ace_repo)
    _a_person_answers(repo, job['id'], 'Use /api/v3')
    other = factory.create_job({'service': job['service'], 'owner': 'Retail',
                                'sourceRevision': 'def456', 'rollbackPointer': 'tag/v2'}, 'tester')
    outcome = _run(dispatch, scenario, other['id'], ace_repo)
    row, = [r for r in _rows(repo, other['id']) if r['id'] in outcome['clarification_ids']]
    assert row['answer'] == 'Use /api/v3' and row['answered_by'] == ACTOR


def test_another_services_answer_is_never_reused(repo, job, factory, ace_repo, dispatch, scenario):
    _run(dispatch, scenario, job['id'], ace_repo)
    _a_person_answers(repo, job['id'], 'Use /api/v3')
    other = factory.create_job({'service': 'SomethingElse', 'owner': 'Retail',
                                'sourceRevision': 'def456', 'rollbackPointer': 'tag/v2'}, 'tester')
    outcome = _run(dispatch, scenario, other['id'], ace_repo)
    row, = [r for r in _rows(repo, other['id']) if r['id'] in outcome['clarification_ids']]
    assert row['answered_by'] != ACTOR


def test_with_auto_mode_off_the_earlier_answer_is_offered_not_recorded(
        repo, job, ace_repo, dispatch, scenario, auto_mode_off):
    """Recording it would leave a parked step with nothing for anyone to answer."""
    _run(dispatch, scenario, job['id'], ace_repo)
    _a_person_answers(repo, job['id'])
    second = _run(dispatch, scenario, job['id'], ace_repo)
    row, = [r for r in _rows(repo, job['id']) if r['id'] in second['clarification_ids']]
    assert row['status'] == 'OPEN' and row['answered_by'] == ''
    assert row['suggestion_mode'] == 'PRIOR_ANSWER' and row['suggested_answer'] == 'Use /api/v2'
    assert row['reused_from']


def test_a_blocked_agents_repeat_is_offered_not_recorded(repo, job, ace_repo, dispatch, scenario,
                                                         monkeypatch):
    """Recording it would resume the step, re-run the agent and loop."""
    _run(dispatch, scenario, job['id'], ace_repo)
    _a_person_answers(repo, job['id'])
    monkeypatch.setattr('app.services.agents.automode.blocks', lambda status: True)
    second = _run(dispatch, scenario, job['id'], ace_repo)
    row, = [r for r in _rows(repo, job['id']) if r['id'] in second['clarification_ids']]
    assert row['status'] == 'OPEN'
    assert row['suggestion_mode'] == 'PRIOR_ANSWER' and row['suggested_answer'] == 'Use /api/v2'
