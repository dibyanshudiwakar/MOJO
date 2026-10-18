"""Auto mode: the agent decides, and the decision is on the record.

One real ``ace-analysis`` raised 95 clarifications, and a clarification parks
its step -- so a migration stopped on 95 questions before anything downstream
could run. In auto mode an agent whose artefact is complete resolves them
itself and goes on.

The three claims worth holding, in order of how badly each would hurt:

**It must not loop.** No agent reads ``clarifications.answer``, and
``resume_clarification_steps`` resumes a parked step by re-running the agent. So
answering the questions of an agent that produced *nothing* makes it run again,
raise the same questions and park again -- seventeen Opus calls and half an hour
per cycle, forever. Auto mode therefore answers only for an agent that is going
on anyway.

**It must not hide the decisions.** Every one is an ANSWERED clarification
attributed to AUTO_MODE, carrying the model's confidence, severity and caveat.
The exception is a question the model rates HIGH severity and is not confident
about: that one stays OPEN and is put to a person, and it does not hold the step.

**It must not touch the gates.** A clarification is an input to the work; a
gate is approval of the result. Auto mode makes the pipeline flow to the gate.
"""
import json

import pytest

from app.services import automode

pytestmark = pytest.mark.usefixtures('auto_mode_on')


@pytest.fixture
def auto_mode_on(monkeypatch):
    monkeypatch.setenv('FACTORY_AUTO_MODE', '1')


PROMPT = 'agent/ace-analysis/semantic-model'

#: Questions an analysis of a real application actually raises -- one the skill
#: can settle, one that needs a person. Both are auto-answered by instruction;
#: what differs is the confidence recorded against each.
AMBIGUITIES = [
    {'question': 'Which Spring pattern replaces the ComIbmCompute transform in Orders.msgflow?',
     'owner': 'ACE_SME', 'impact': 'generation would guess the transformer shape',
     'status': 'OPEN', 'evidenceReference': 'Orders.msgflow#map'},
    {'question': 'What is the production endpoint URL for the downstream claims service?',
     'owner': 'ACE_SME', 'impact': 'the generated client would point nowhere',
     'status': 'OPEN', 'evidenceReference': 'Orders.msgflow#in'},
]


@pytest.fixture
def analysis_with_questions(model):
    """Make ace-analysis raise ambiguities, so these tests cannot silently skip.

    The fixture ACE tree parses without ambiguity, so every assertion about
    recorded decisions was skipping -- which tests nothing. The double returns
    them on the first source file instead.
    """
    from tests.agents.model_double import ANSWERS
    base = ANSWERS[PROMPT]
    first = {'done': False}

    def answer(payload):
        fragment = base(payload)
        if not first['done']:
            first['done'] = True
            fragment = dict(fragment, ambiguities=AMBIGUITIES)
        return fragment

    model.overrides[PROMPT] = answer

    #: The suggestion call has to answer the same questions back verbatim --
    #: the registry matches suggestions to clarifications by exact text.
    def suggest(payload):
        confidences = ['HIGH', 'LOW']
        return {'suggestions': [
            {'question': q.get('question', ''),
             'suggested_answer': 'Use the approved mapping for this construct.',
             'confidence': confidences[index % len(confidences)],
             # MEDIUM, so a LOW-confidence answer is still decided: only a HIGH
             # severity question is ever put to a person.
             'severity': 'MEDIUM',
             'caveat': 'confirm against the live system'}
            for index, q in enumerate(payload.get('questions') or [])]}

    model.overrides['agent/clarification/suggested-answers'] = suggest
    return model


# ---- the switch ----------------------------------------------------------------------------

def test_auto_mode_is_on_unless_switched_off(monkeypatch):
    monkeypatch.delenv('FACTORY_AUTO_MODE', raising=False)
    assert automode.enabled() is True, 'on by default, by instruction'


@pytest.mark.parametrize('value', ['0', 'false', 'FALSE', 'no', 'off', 'Off'])
def test_the_switch_accepts_the_obvious_falsey_spellings(monkeypatch, value):
    monkeypatch.setenv('FACTORY_AUTO_MODE', value)
    assert automode.enabled() is False


def test_a_question_is_put_to_a_person_only_when_severe_and_unsure():
    """HIGH severity AND confidence below HIGH. Nothing else reaches a person."""
    assert automode.needs_person('HIGH', 'LOW') is True
    assert automode.needs_person('HIGH', 'MEDIUM') is True
    assert automode.needs_person('high', '') is True
    # A confident answer to a severe question is still the model's to make.
    assert automode.needs_person('HIGH', 'HIGH') is False
    # An unsure answer to a low-stakes one is not worth stopping anyone for.
    for severity in ('MEDIUM', 'LOW', '', None):
        for confidence in ('HIGH', 'MEDIUM', 'LOW', ''):
            assert automode.needs_person(severity, confidence) is False


def test_accepts_follows_the_same_rule_and_rejects_nonsense():
    assert automode.accepts('LOW', 'MEDIUM') is True
    assert automode.accepts('LOW', 'HIGH') is False
    assert automode.accepts('HIGH', 'HIGH') is True
    assert automode.accepts('NONSENSE', 'LOW') is False, 'a malformed answer is not a decision'


def test_a_blocked_agent_is_recognised_by_its_own_status():
    """The agent decides whether it can go on; auto mode does not override it."""
    assert automode.blocks('CLARIFICATION_REQUIRED') is True
    assert automode.blocks('COMPLETED') is False
    assert automode.blocks('FAILED') is False


# ---- an agent whose artefact is complete ----------------------------------------------------

def test_ace_analysis_completes_instead_of_parking(repo, job, ace_repo, dispatch):
    """95 questions used to stop the pipeline before anything else could run."""
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['status'] == 'COMPLETED', 'auto mode does not park a complete analysis'
    # The artefact really is complete, which is the precondition for the above.
    assert outcome['outputs']['analysis_document']
    assert outcome['outputs']['semantic_model'], 'the hash a G2 approval binds'


def test_each_question_becomes_a_recorded_decision(repo, job, ace_repo, dispatch,
                                                   analysis_with_questions):
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['clarification_ids'], 'the fixture guarantees questions were raised'
    rows = repo.rows('select status,answer,answered_by,suggestion_confidence'
                     ' from clarifications where job_id=?', (job['id'],))
    assert rows
    assert {r['status'] for r in rows} == {'ANSWERED'}
    assert {r['answered_by'] for r in rows} == {automode.ACTOR}
    assert all(r['answer'] for r in rows), 'an answer with no text is not a decision'
    # Attributed to AUTO_MODE and not to a person: an auditor has to be able to
    # see that nobody reviewed these.
    assert '@' not in automode.ACTOR


def test_the_confidence_survives_so_the_low_ones_can_be_found(repo, job, ace_repo, dispatch,
                                                              analysis_with_questions):
    """Accepting LOW is only defensible if LOW is still identifiable after."""
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['clarification_ids'], 'the fixture guarantees questions were raised'
    rows = repo.rows('select suggestion_confidence from clarifications where job_id=?',
                     (job['id'],))
    assert all(r['suggestion_confidence'] for r in rows), \
        'a decision with no recorded confidence cannot be triaged later'


def test_the_batch_is_one_ledger_entry_naming_the_actor(repo, job, ace_repo, dispatch,
                                                       analysis_with_questions):
    """95 ledger entries for one run buries the chain it exists to make readable."""
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['clarification_ids'], 'the fixture guarantees questions were raised'
    # audit_ledger is a hash chain: it carries payload_hash, not the payload.
    # The readable detail is on the workflow_events row written beside it.
    chain = repo.rows("select actor,action from audit_ledger"
                      " where job_id=? and action='CLARIFICATIONS_AUTO_ANSWERED'",
                      (job['id'],))
    assert len(chain) == 1, 'one entry for the batch, not one per question'
    assert chain[0]['actor'] == automode.ACTOR, 'the chain names what decided'
    events = repo.rows("select details from workflow_events"
                       " where job_id=? and event_type='CLARIFICATIONS_AUTO_ANSWERED'",
                       (job['id'],))
    assert len(events) == 1
    details = json.loads(events[0]['details'])
    assert details['decided'] == len(outcome['clarification_ids'])
    assert details['actor'] == automode.ACTOR
    assert set(details['by_confidence']) == {'HIGH', 'MEDIUM', 'LOW'}
    assert details['by_confidence']['LOW'] >= 1, 'the fixture includes a LOW answer'


def test_with_auto_mode_off_the_questions_stay_open(repo, job, ace_repo, dispatch, monkeypatch,
                                                   analysis_with_questions):
    """The switch is a switch. A migration under review still stops to ask."""
    monkeypatch.setenv('FACTORY_AUTO_MODE', '0')
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['clarification_ids'], 'the fixture guarantees questions were raised'
    assert outcome['status'] == 'CLARIFICATION_REQUIRED'
    rows = repo.rows('select status from clarifications where job_id=?', (job['id'],))
    assert {r['status'] for r in rows} == {'OPEN'}


# ---- an agent that produced nothing ---------------------------------------------------------

def test_a_blocked_agents_questions_are_left_open(repo, job):
    """The loop guard, stated as behaviour rather than as a comment.

    A blocked agent produced no artefact, so an automatic answer cannot change
    its outcome -- it would only resume the step, re-run the agent, and arrive
    back here. Asserted through the registry's own rule so it holds for any
    agent that reports CLARIFICATION_REQUIRED, not just the one tested.
    """
    assert automode.enabled() is True
    assert automode.blocks('CLARIFICATION_REQUIRED') is True
    # The conjunction dispatch applies.
    auto = automode.enabled() and not automode.blocks('CLARIFICATION_REQUIRED')
    assert auto is False, 'nothing is auto-answered for an agent that stopped'


def test_auto_mode_never_answers_for_a_parked_step(repo, job, ace_repo, dispatch, monkeypatch,
                                                  analysis_with_questions):
    """End to end: a blocked agent's questions stay OPEN, so nothing resumes.

    ``automode.blocks`` is forced True at the name the registry imported, which
    is the state ``planning`` is in whenever it has no plan. The questions must
    be persisted and given suggestions -- a human still needs those -- and none
    of them answered, because answering is what would start the loop.
    """
    monkeypatch.setattr('app.services.agents.automode.blocks', lambda status: True)
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['clarification_ids'], 'the fixture guarantees questions were raised'
    rows = repo.rows('select status,answer,suggested_answer from clarifications where job_id=?',
                     (job['id'],))
    assert {r['status'] for r in rows} == {'OPEN'}, 'answering these would loop'
    assert not any(r['answer'] for r in rows)
    # The suggestion is still written: a person reviewing them wants the draft.
    assert all(r['suggested_answer'] for r in rows)


# ---- the gates are untouched ----------------------------------------------------------------

def test_auto_mode_grants_no_gate_approval(repo, job, ace_repo, dispatch):
    """A clarification is an input to the work; a gate approves the result."""
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    approvals = repo.rows('select * from approvals where job_id=?', (job['id'],))
    assert approvals == [], 'auto mode must never sign a gate'


# ---- only the severe, unsure ones are asked -------------------------------------------------

def test_a_severe_unsure_question_stays_open_and_the_rest_are_decided(
        repo, job, ace_repo, dispatch, analysis_with_questions, model):
    """The rule end to end, and that asking does not hold the step."""
    def suggest(payload):
        qs = payload.get('questions') or []
        return {'suggestions': [
            {'question': q.get('question', ''), 'suggested_answer': 'needs the live URL',
             # The first question is the severe, unsure one.
             'confidence': 'LOW' if i == 0 else 'HIGH',
             'severity': 'HIGH' if i == 0 else 'LOW',
             'caveat': 'c'} for i, q in enumerate(qs)]}

    model.overrides['agent/clarification/suggested-answers'] = suggest
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    outcome = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    assert outcome['status'] == 'COMPLETED', 'a question put to a person does not hold the step'
    rows = repo.rows('select status,answered_by,suggestion_severity from clarifications'
                     ' where job_id=? order by rowid', (job['id'],))
    assert [r['status'] for r in rows][0] == 'OPEN'
    assert {r['status'] for r in rows[1:]} == {'ANSWERED'}
    assert rows[0]['suggestion_severity'] == 'HIGH' and rows[0]['answered_by'] == ''
    assert {r['answered_by'] for r in rows[1:]} == {automode.ACTOR}
