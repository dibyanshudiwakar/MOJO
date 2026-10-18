"""Answers people gave become context for the agent that asked.

``test_clarification_reuse`` covers a question being asked again. This covers
the broader half: a decision made once should shape every later run of that
agent, on any job, rather than only suppress an identical repeat.
"""
import pytest

from app.agents import artefact
from app.repository import now
from app.services import automode, precedents
from app.services.clarification_reuse import ACTOR as REUSED

PROMPT = 'agent/ace-analysis/semantic-model'


class Raises:
    """Which question the double makes ace-analysis raise on its next run."""

    def __init__(self):
        self.question = ''
        self.seen = []


@pytest.fixture
def raises(model):
    from tests.agents.model_double import ANSWERS
    base, state = ANSWERS[PROMPT], Raises()

    def answer(payload):
        state.seen.append(payload)
        out = dict(base(payload))
        if state.question:
            out['ambiguities'] = [{'question': state.question, 'owner': 'ACE_SME', 'impact': 'i',
                                   'status': 'OPEN', 'evidenceReference': 'Orders.msgflow#in'}]
            state.question = ''
        return out

    model.overrides[PROMPT] = answer
    return state


def _ask(dispatch, raises, job_id, ace_repo, question):
    raises.question = question
    dispatch(job_id, 'repository-intake', {'path': str(ace_repo)})
    return dispatch(job_id, 'ace-analysis', {'path': str(ace_repo)})


def _answer(repo, outcome, text, by='person@example.com'):
    with repo.connect() as c:
        for cid in outcome['clarification_ids']:
            c.execute("update clarifications set status='ANSWERED',answer=?,answered_by=?,answered_at=?"
                      " where id=?", (text, by, now(), cid))


def _job(factory, service):
    return factory.create_job({'service': service, 'owner': 'Retail', 'sourceRevision': 'r1',
                               'rollbackPointer': 't'}, 'tester')


# ---- what is learned -----------------------------------------------------------------------

def test_a_persons_answers_come_back_nearest_first_and_labelled(repo, job, factory, ace_repo,
                                                                dispatch, raises):
    same_service = _job(factory, job['service'])
    elsewhere = _job(factory, 'Billing')
    _answer(repo, _ask(dispatch, raises, elsewhere['id'], ace_repo, 'Q about billing?'), 'billing answer')
    _answer(repo, _ask(dispatch, raises, same_service['id'], ace_repo, 'Q about the sibling job?'),
            'sibling answer')
    _answer(repo, _ask(dispatch, raises, job['id'], ace_repo, 'Q about this job?'), 'own answer')

    found = precedents.decisions_for(repo, job['id'], 'ace-analysis')

    assert [(d['scope'], d['answer']) for d in found] == [
        ('this job', 'own answer'), ('same service', 'sibling answer'),
        ('other service', 'billing answer')]
    assert found[2]['service'] == 'Billing', 'a different service says so'


def test_the_models_own_answers_are_not_fed_back_to_it(repo, job, ace_repo, dispatch, raises):
    """Returning its guesses as rulings would only reinforce them."""
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Q the model decided?')
    _answer(repo, outcome, 'a guess', by=automode.ACTOR)
    assert precedents.decisions_for(repo, job['id'], 'ace-analysis') == []


def test_a_reused_answer_is_not_counted_twice(repo, job, ace_repo, dispatch, raises):
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Q reused?')
    _answer(repo, outcome, 'copied', by=REUSED)
    assert precedents.decisions_for(repo, job['id'], 'ace-analysis') == []


def test_only_the_agent_that_asked_gets_the_answer(repo, job, ace_repo, dispatch, raises):
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Q for analysis?')
    _answer(repo, outcome, 'analysis answer')
    assert len(precedents.decisions_for(repo, job['id'], 'ace-analysis')) == 1
    assert precedents.decisions_for(repo, job['id'], 'planning') == []


def test_an_unanswered_question_is_not_a_decision(repo, job, ace_repo, dispatch, raises):
    _ask(dispatch, raises, job['id'], ace_repo, 'Q still open?')
    assert precedents.decisions_for(repo, job['id'], 'ace-analysis') == []


def test_the_same_question_answered_twice_appears_once(repo, job, factory, ace_repo, dispatch, raises):
    other = _job(factory, job['service'])
    _answer(repo, _ask(dispatch, raises, other['id'], ace_repo, 'Same question?'), 'older')
    _answer(repo, _ask(dispatch, raises, job['id'], ace_repo, 'same  QUESTION'), 'newer')
    found = precedents.decisions_for(repo, job['id'], 'ace-analysis')
    assert [d['answer'] for d in found] == ['newer'], 'this job outranks the sibling'


def test_the_context_is_bounded(repo, job, ace_repo, dispatch, raises, monkeypatch):
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Q one?')
    _answer(repo, outcome, 'x' * 500)
    monkeypatch.setattr(precedents, 'MAX_CHARS', 100)
    assert precedents.decisions_for(repo, job['id'], 'ace-analysis') == [], 'one entry over budget is dropped'


# ---- what the model is sent ----------------------------------------------------------------

def test_the_decisions_reach_the_model_with_the_instruction(repo, job, factory, ace_repo, dispatch, raises):
    other = _job(factory, job['service'])
    _answer(repo, _ask(dispatch, raises, other['id'], ace_repo, 'What is the context root?'), '/api/v2')
    raises.seen.clear()

    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})

    assert raises.seen, 'the agent reached the model'
    for payload in raises.seen:
        assert [(d['question'], d['answer']) for d in payload['_precedents']] == [
            ('What is the context root?', '/api/v2')], 'every request carries them, not just the first'
        assert 'settled' in payload['_precedents_note']


def test_no_decisions_means_no_extra_keys(repo, job, ace_repo, dispatch, raises):
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert raises.seen
    assert all('_precedents' not in p and '_precedents_note' not in p for p in raises.seen)


def test_it_can_be_switched_off(repo, job, factory, ace_repo, dispatch, raises, monkeypatch):
    other = _job(factory, job['service'])
    _answer(repo, _ask(dispatch, raises, other['id'], ace_repo, 'Q?'), 'a')
    raises.seen.clear()
    monkeypatch.setenv('FACTORY_PRECEDENTS', '0')
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert raises.seen and all('_precedents' not in p for p in raises.seen)


def test_a_failed_lookup_runs_the_agent_without_context(repo, job, ace_repo, dispatch, raises, monkeypatch):
    """Context is a convenience, so losing it must not lose the run."""
    def boom(*args, **kwargs):
        raise RuntimeError('database exploded')
    monkeypatch.setattr(precedents, 'decisions_for', boom)
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['status'] == 'COMPLETED'
    assert all('_precedents' not in p for p in raises.seen)


def test_the_count_is_recorded_in_the_provenance(repo, job, factory, ace_repo, dispatch, raises):
    other = _job(factory, job['service'])
    _answer(repo, _ask(dispatch, raises, other['id'], ace_repo, 'Q?'), 'a')
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['reasoning']['precedents'] == 1


# ---- the same question in different words --------------------------------------------------
#
# "additionalInstances=10" was asked a different way for each properties file,
# at 60-74% textual similarity and with different evidence references. No text
# rule separates that from two genuinely different questions, so the model --
# which is already being asked for a suggestion for each -- says which earlier
# decision settles it.

SUGGEST = 'agent/clarification/suggested-answers'


def _settling(model, ref, confidence='HIGH'):
    def suggest(payload):
        return {'suggestions': [
            {'question': q['question'], 'suggested_answer': 'a paraphrase', 'confidence': confidence,
             'severity': 'LOW', 'settled_by': ref, 'caveat': ''} for q in payload.get('questions') or []]}
    model.overrides[SUGGEST] = suggest


def _decided_earlier(repo, job, factory, ace_repo, dispatch, raises):
    """A sibling job's person-given answer, then a reworded question on this job."""
    sibling = _job(factory, job['service'])
    first = _ask(dispatch, raises, sibling['id'], ace_repo, 'What context root should the MS use?')
    _answer(repo, first, '/api/v2')
    return first['clarification_ids'][0]


def test_a_reworded_question_is_settled_by_the_decision_the_model_cites(
        repo, job, factory, ace_repo, dispatch, raises, model):
    original = _decided_earlier(repo, job, factory, ace_repo, dispatch, raises)
    _settling(model, 'D1')
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Which base path will the MS expose to consumers?')
    row, = [r for r in repo.rows('select * from clarifications where job_id=?', (job['id'],))
            if r['id'] in outcome['clarification_ids']]
    assert row['status'] == 'DUPLICATE'
    assert row['answer'] == '/api/v2', "the person's decision, never the model's paraphrase"
    assert row['answered_by'] == REUSED and row['reused_from'] == original


@pytest.mark.parametrize('ref,confidence', [('D99', 'HIGH'), ('', 'HIGH'), ('D1', 'LOW')])
def test_it_is_not_settled_by_a_ref_it_was_not_shown_or_an_unsure_model(
        repo, job, factory, ace_repo, dispatch, raises, model, ref, confidence):
    _decided_earlier(repo, job, factory, ace_repo, dispatch, raises)
    _settling(model, ref, confidence)
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Which base path will the MS expose to consumers?')
    row, = [r for r in repo.rows('select * from clarifications where job_id=?', (job['id'],))
            if r['id'] in outcome['clarification_ids']]
    assert row['status'] != 'DUPLICATE'


def test_a_blocked_agent_is_never_settled_this_way(repo, job, factory, ace_repo, dispatch, raises,
                                                   model, monkeypatch):
    """Settling would leave a parked step with nothing to answer, or loop."""
    _decided_earlier(repo, job, factory, ace_repo, dispatch, raises)
    _settling(model, 'D1')
    monkeypatch.setattr('app.services.agents.automode.blocks', lambda status: True)
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Which base path will the MS expose to consumers?')
    row, = [r for r in repo.rows('select * from clarifications where job_id=?', (job['id'],))
            if r['id'] in outcome['clarification_ids']]
    assert row['status'] == 'OPEN'


def test_with_auto_mode_off_nothing_is_settled_by_the_model(repo, job, factory, ace_repo, dispatch,
                                                            raises, model, auto_mode_off):
    _decided_earlier(repo, job, factory, ace_repo, dispatch, raises)
    _settling(model, 'D1')
    outcome = _ask(dispatch, raises, job['id'], ace_repo, 'Which base path will the MS expose to consumers?')
    row, = [r for r in repo.rows('select * from clarifications where job_id=?', (job['id'],))
            if r['id'] in outcome['clarification_ids']]
    assert row['status'] == 'OPEN'


def test_the_model_is_given_refs_but_never_database_ids(repo, job, factory, ace_repo, dispatch, raises):
    original = _decided_earlier(repo, job, factory, ace_repo, dispatch, raises)
    raises.seen.clear()
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    entry, = raises.seen[0]['_precedents']
    assert entry['ref'] == 'D1' and 'id' not in entry and original not in str(raises.seen[0])
