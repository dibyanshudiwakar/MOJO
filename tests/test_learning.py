"""Runs leave lessons behind, and a recurring one is counted rather than repeated."""
import pytest

from app.repository import Repository
from app.services import learning
from app.services.learning import lessons_from, normalise, overview, record_run


@pytest.fixture
def repo(tmp_path):
    return Repository(str(tmp_path / 'learn.db'))


def _kinds(found):
    return sorted({x['kind'] for x in found})


def test_a_clean_run_teaches_nothing():
    assert lessons_from('planning', 'COMPLETED', {}, {'attempts': 1}, {}) == []


def test_schema_corrections_and_effort_step_downs_become_lessons():
    found = lessons_from('planning', 'COMPLETED', {}, {
        'prompt_name': 'agent/planning/mapping-catalogue',
        'all_corrections': ['Schema violation: risks has 41 items; at most 40'],
        'all_effort_lowered': ['high->medium']}, {})
    assert _kinds(found) == ['EFFORT_STEP_DOWN', 'SCHEMA_CORRECTION']


def test_code_generation_gaps_and_failed_checks_become_lessons():
    found = lessons_from('code-generation', 'COMPLETED', {
        'gaps': [{'check': 'SKILL_A0', 'skill_ref': 'HANDOFF-A0', 'findings': ['lacks server.port'],
                  'remediation': 'copy the baseline block'}],
        'failed_checks': ['PROJECT_STRUCTURE']}, {}, {})
    assert _kinds(found) == ['GATE_FAIL', 'GATE_GAP']
    gap = next(x for x in found if x['kind'] == 'GATE_GAP')
    assert 'baseline' in gap['resolution'] and 'SKILL_A0' in gap['situation']


def test_the_feedback_agents_repairs_and_what_it_could_not_fix_become_lessons():
    found = lessons_from('feedback', 'COMPLETED', {'unfixed_gaps': ['SKILL_R3']},
                         {}, {'applied_fixes': [{'check': 'SKILL_A0', 'action': 'added 3 baseline key(s)'}]})
    assert _kinds(found) == ['GAP_UNREPAIRED', 'REPAIR']


def test_a_failed_run_is_a_lesson_with_its_error():
    found = lessons_from('planning', 'FAILED', {}, {}, {}, error='ArtefactUnavailable:schema_invalid')
    assert _kinds(found) == ['RUN_FAILURE']


def test_numbers_and_ids_do_not_make_two_occurrences_look_different():
    assert normalise('risks has 41 items; at most 40') == normalise('risks has 57 items; at most 40')
    assert normalise("key 'a'") == normalise('key "a"')


def test_the_same_lesson_in_another_job_is_counted_not_duplicated(repo):
    for n, job in enumerate(('job-1', 'job-2', 'job-2')):
        record_run(repo, run_id='run-%d' % n, job_id=job, agent_id='feedback', agent_version='1',
                   status='COMPLETED', started_at='2026-10-20T10:00:00+00:00',
                   outputs={'applied_fixes': [{'check': 'SKILL_A0', 'action': 'added 3 baseline key(s)'}]})
    seen = overview(repo)
    assert len(seen['lessons']) == 1
    assert seen['lessons'][0]['occurrences'] == 3 and seen['lessons'][0]['jobs'] == 2
    assert seen['summary'] == {'runs': 3, 'lessons': 1, 'recurring': 1}


def test_run_metrics_are_kept_per_agent(repo):
    record_run(repo, run_id='a', job_id='j', agent_id='planning', agent_version='1', status='COMPLETED',
               started_at='2026-10-20T10:00:00+00:00', reasoning={'attempts_total': 6, 'all_corrections': ['x']},
               clarifications=4)
    record_run(repo, run_id='b', job_id='j', agent_id='planning', agent_version='1', status='FAILED',
               started_at='2026-10-20T10:00:00+00:00', error='boom')
    row = overview(repo)['agents'][0]
    assert row['agent_id'] == 'planning' and row['runs'] == 2 and row['completed'] == 1
    assert row['corrections'] == 1


def test_recording_can_never_fail_a_run(repo, monkeypatch):
    monkeypatch.setattr(learning, 'lessons_from', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('bad')))
    assert record_run(repo, run_id='x', job_id='j', agent_id='a', agent_version='1', status='COMPLETED',
                      started_at='') == 0

