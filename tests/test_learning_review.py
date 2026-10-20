"""Reviewing a lesson, and what an approved one does.

A lesson reaches an agent only after a reviewer approves it, and what the model
reads is the reviewer's guidance, not the raw record. These pin that, plus the
bounds that keep one agent's guidance from crowding out its actual input.
"""
import types

import pytest

from app.repository import Repository
from app.services import learning
from app.services.learning import approved_for, decide, for_context, overview, record_run


@pytest.fixture
def repo(tmp_path):
    return Repository(str(tmp_path / 'review.db'))


def _lesson(repo, kind='GATE_GAP', agent='code-generation', check='SKILL_A0', times=1):
    for n in range(times):
        record_run(repo, run_id='%s-%s-%d' % (agent, check, n), job_id='j%d' % n, agent_id=agent,
                   agent_version='1', status='COMPLETED', started_at='2026-10-20T10:00:00+00:00',
                   metrics={'gaps': [{'check': check, 'skill_ref': 'HANDOFF-A0', 'findings': ['lacks server.port'],
                                      'remediation': 'copy the baseline block'}]} if kind == 'GATE_GAP' else {},
                   outputs={'applied_fixes': [{'check': check, 'action': 'added keys'}]} if kind == 'REPAIR' else {})
    return next(x for x in overview(repo)['lessons'] if x['kind'] == kind and x['agent_id'] == agent
                and check.lower() in x['signature'])


def test_gate_checks_that_differ_only_in_digits_are_different_lessons(repo):
    """SKILL_M5_M16 and SKILL_M7_M10 once merged, because digits were erased from the key."""
    _lesson(repo, check='SKILL_M5_M16')
    _lesson(repo, check='SKILL_M7_M10')
    assert len([x for x in overview(repo)['lessons'] if x['kind'] == 'GATE_GAP']) == 2


def test_a_captured_lesson_is_not_given_to_any_agent(repo):
    _lesson(repo)
    assert approved_for(repo, 'code-generation') == []


def test_an_approved_lesson_is_given_to_its_agent_only(repo):
    lesson = _lesson(repo)
    decide(repo, lesson['id'], 'APPROVE', 'curator@example.com')
    got = approved_for(repo, 'code-generation')
    assert [g['id'] for g in got] == [lesson['id']]
    assert 'SKILL_A0' in got[0]['guidance'] and 'baseline' in got[0]['guidance']
    assert approved_for(repo, 'planning') == [], 'another agent is not told'


def test_the_reviewers_wording_replaces_the_suggested_one(repo):
    lesson = _lesson(repo)
    row = decide(repo, lesson['id'], 'APPROVE', 'curator@example.com',
                 guidance='Always include the baseline block in every properties file.')
    assert row['guidance'] == 'Always include the baseline block in every properties file.'
    assert row['reviewed_by'] == 'curator@example.com'


def test_a_rejected_or_retracted_lesson_is_not_given(repo):
    first, second = _lesson(repo, check='SKILL_A0'), _lesson(repo, check='SKILL_M5')
    decide(repo, first['id'], 'REJECT', 'c')
    decide(repo, second['id'], 'APPROVE', 'c')
    assert [g['id'] for g in approved_for(repo, 'code-generation')] == [second['id']]
    decide(repo, second['id'], 'RETRACT', 'c')
    assert approved_for(repo, 'code-generation') == []


def test_a_lesson_about_the_platform_cannot_be_approved_into_a_prompt(repo):
    record_run(repo, run_id='r', job_id='j', agent_id='planning', agent_version='1', status='COMPLETED',
               started_at='2026-10-20T10:00:00+00:00',
               reasoning={'all_effort_lowered': ['high->medium'], 'prompt_name': 'p'})
    step = next(x for x in overview(repo)['lessons'] if x['kind'] == 'EFFORT_STEP_DOWN')
    with pytest.raises(ValueError, match='platform'):
        decide(repo, step['id'], 'APPROVE', 'c')


def test_the_guidance_given_is_bounded(repo):
    for n in range(20):
        decide(repo, _lesson(repo, check='SKILL_%d' % n)['id'], 'APPROVE', 'c', guidance='x' * 250)
    got = approved_for(repo, 'code-generation')
    assert len(got) <= learning.MAX_LESSONS and sum(len(g['guidance']) for g in got) <= learning.MAX_CHARS


def test_the_most_recurring_lesson_comes_first(repo):
    rare, common = _lesson(repo, check='SKILL_RARE'), _lesson(repo, check='SKILL_COMMON', times=4)
    decide(repo, rare['id'], 'APPROVE', 'c')
    decide(repo, common['id'], 'APPROVE', 'c')
    assert approved_for(repo, 'code-generation')[0]['id'] == common['id']


def test_deciding_needs_a_valid_decision_and_a_real_lesson(repo):
    with pytest.raises(ValueError, match='lesson_not_found'):
        decide(repo, 'nope', 'APPROVE', 'c')
    lesson = _lesson(repo)
    with pytest.raises(ValueError, match='decision_must_be'):
        decide(repo, lesson['id'], 'MAYBE', 'c')


def test_the_lookup_is_cached_switchable_and_never_fails_the_agent(repo, monkeypatch):
    lesson = _lesson(repo)
    decide(repo, lesson['id'], 'APPROVE', 'c')
    ctx = types.SimpleNamespace(repo=repo, agent_id='code-generation', logger=None)
    assert len(for_context(ctx)) == 1 and for_context(ctx) is ctx._lessons
    monkeypatch.setenv('FACTORY_LESSONS', '0')
    assert for_context(types.SimpleNamespace(repo=repo, agent_id='code-generation')) == []
    monkeypatch.delenv('FACTORY_LESSONS')
    monkeypatch.setattr(learning, 'approved_for', lambda *a: (_ for _ in ()).throw(RuntimeError('db')))
    assert for_context(types.SimpleNamespace(repo=repo, agent_id='code-generation')) == []


def test_a_lesson_used_by_a_run_is_counted(repo):
    lesson = _lesson(repo)
    decide(repo, lesson['id'], 'APPROVE', 'c')
    record_run(repo, run_id='again', job_id='jx', agent_id='code-generation', agent_version='1',
               status='COMPLETED', started_at='2026-10-20T10:00:00+00:00',
               reasoning={'lessons_used': [lesson['id']]})
    assert next(x for x in overview(repo)['lessons'] if x['id'] == lesson['id'])['applied'] == 1


def test_a_lessons_table_from_before_review_gains_the_new_columns(tmp_path):
    repo = Repository(str(tmp_path / 'old.db'))
    with repo.connect() as c:
        c.execute('create table lessons(id text primary key, agent_id text, kind text, signature text, '
                  'situation text, resolution text, evidence text, occurrences integer, jobs text, scope text, '
                  'status text, first_seen text, last_seen text, unique(agent_id, kind, signature))')
    assert overview(repo)['lessons'] == []
    with repo.connect() as c:
        assert {'guidance', 'applied', 'reviewed_by'} <= {r[1] for r in c.execute('pragma table_info(lessons)')}
