"""The feedback agent repairs a failing build and proves it by building again.

A build failure arrived as "Quality gate failed" and the agent gave up, for two
reasons that these pin down:

* an edit was re-checked *without* the build, so a build failure showed as "not
  executed", the edit looked like an improvement and was kept uncompiled;
* an edit that took the build from 20 errors to 5 was thrown away, because the
  set of failing checks had not shrunk, and getting the build far enough to
  start the service counted as a new failure.
"""
import pytest

pytestmark = pytest.mark.pristine_registry

from app.services.feedback import CodeFeedbackService, improved, score
from app.services.migration_lifecycle_v6 import MappingAndPlanningService
from tests.test_feedback_model_repair import _ctx, _project, env  # noqa: F401  (env is a fixture)


def _compile(status, errors=()):
    return {'name': 'COMPILE_AND_UNIT_TEST', 'status': status,
            'evidence': {'errors': list(errors)} if status == 'FAIL' else {}}


def _start(status, errors=()):
    return {'name': 'STARTUP_SMOKE', 'status': status,
            'evidence': {'errors': list(errors)} if status == 'FAIL' else {}}


# ---- the scoring --------------------------------------------------------------------------------

def test_fewer_build_errors_is_progress_even_though_the_build_still_fails():
    twenty = score([_compile('FAIL', ['e'] * 20)])
    five = score([_compile('FAIL', ['e'] * 5)])
    assert improved(twenty, five) and not improved(five, twenty)


def test_a_build_that_now_compiles_but_does_not_start_is_progress_not_a_new_failure():
    assert improved(score([_compile('FAIL', ['e']), _start('NOT_EXECUTED')]),
                    score([_compile('PASS'), _start('FAIL', ['boom'])]))


def test_a_build_that_starts_beats_one_that_does_not():
    assert improved(score([_compile('PASS'), _start('FAIL', ['boom'])]),
                    score([_compile('PASS'), _start('PASS')]))


def test_nothing_that_was_passing_may_start_failing():
    other = {'name': 'SECRET_SCAN', 'status': 'FAIL', 'evidence': ''}
    assert not improved(score([_compile('FAIL', ['e'] * 9)]), score([_compile('FAIL', ['e']), other]))


def test_no_change_is_not_progress():
    same = score([_compile('FAIL', ['e', 'e'])])
    assert not improved(same, same)


def test_going_back_a_stage_is_not_progress_whatever_the_error_count():
    assert not improved(score([_compile('PASS'), _start('FAIL', ['a', 'b'])]),
                        score([_compile('FAIL', ['a'])]))


# ---- the loop -----------------------------------------------------------------------------------

class _Model:
    """Proposes one edit per call, recording the request it was sent."""
    model = 'claude-opus-5-5'

    def __init__(self, edits_per_call):
        self.edits_per_call = list(edits_per_call)
        self.payloads = []

    def invoke(self, template, payload, skill=None):
        self.payloads.append(payload)
        edits = self.edits_per_call[min(len(self.payloads) - 1, len(self.edits_per_call) - 1)]
        return {'edits': edits}


def _edit(old, new):
    return {'path': 'src/main/java/Svc.java', 'old_str': old, 'new_str': new, 'why': 'fix: ' + new}


def _gate(monkeypatch, runs):
    """quality_validate answering from ``runs`` in order, recording how it was called."""
    seen = {'builds': [], 'n': 0}

    def fake(self, job_id, plan_id, project_root, execute_build='auto'):
        seen['builds'].append(execute_build)
        checks = runs[min(seen['n'], len(runs) - 1)]
        seen['n'] += 1
        failing = any(c['status'] == 'FAIL' for c in checks)
        return {'quality_run_id': 'qr-%d' % seen['n'], 'status': 'FAIL' if failing else 'PASS', 'checks': checks}

    monkeypatch.setattr(MappingAndPlanningService, 'quality_validate', fake)
    return seen


def test_a_repair_is_verified_with_the_build_not_without_it(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    seen = _gate(monkeypatch, [[_compile('FAIL', ['e'])], [_compile('PASS'), _start('PASS')]])
    model = _Model([[_edit('class Svc{}', 'class Svc{ int ok; }')]])

    CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=3, execute_build=True,
                                    context=_ctx(repo, job['id'], model))

    assert seen['builds'] and all(b is True for b in seen['builds']), \
        'every validation, including the one that judges the edit, runs the build'


def test_a_repair_that_leaves_fewer_errors_is_kept_and_the_next_attempt_finishes_the_job(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp, java_text='class Svc{ A a; B b; }')
    _gate(monkeypatch, [
        [_compile('FAIL', ['cannot find symbol A', 'cannot find symbol B'])],     # before
        [_compile('FAIL', ['cannot find symbol B'])],                             # after the first edit
        [_compile('PASS'), _start('PASS')],                                       # after the second
    ])
    model = _Model([[_edit('A a;', '')], [_edit('B b;', '')]])

    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=4,
                                          context=_ctx(repo, job['id'], model))

    assert [r['outcome'] for r in out['model_rounds']] == ['kept', 'kept']
    assert out['unfixed_checks'] == [] and out['quality_status'] == 'PASS'
    assert (proj / 'src/main/java/Svc.java').read_text() == 'class Svc{   }'


def test_a_reverted_attempt_is_told_to_the_next_one(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp, java_text='class Svc{ A a; B b; }')
    _gate(monkeypatch, [
        [_compile('FAIL', ['cannot find symbol A', 'cannot find symbol B'])],
        [_compile('FAIL', ['cannot find symbol A', 'cannot find symbol B', 'worse'])],   # first attempt: worse
        [_compile('PASS'), _start('PASS')],                                              # second attempt works
    ])
    model = _Model([[_edit('B b;', 'C c;')], [_edit('A a;', ''), _edit('B b;', '')]])

    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=4,
                                          context=_ctx(repo, job['id'], model))

    assert [r['outcome'] for r in out['model_rounds']] == ['reverted_no_improvement', 'kept']
    second = model.payloads[1]
    assert second['previous_attempts'][0]['tried'] == ['fix: C c;']
    assert 'reverted' in second['previous_attempts'][0]['result']
    assert 'previous_attempts' not in model.payloads[0]
    assert out['quality_status'] == 'PASS'


def test_a_tree_the_gate_has_just_judged_is_not_built_a_second_time(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    seen = _gate(monkeypatch, [[_compile('FAIL', ['e'])], [_compile('PASS'), _start('PASS')]])
    model = _Model([[_edit('class Svc{}', 'class Svc{ int ok; }')]])

    CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=3,
                                    context=_ctx(repo, job['id'], model))

    assert len(seen['builds']) == 2, 'one build before the edit, one after -- not a third to read the result back'


def test_the_budget_still_bounds_the_attempts(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    _gate(monkeypatch, [[_compile('FAIL', ['e', 'e'])]])         # never improves
    model = _Model([[_edit('class Svc{}', 'class Svc{ int x; }')]])

    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=3,
                                          context=_ctx(repo, job['id'], model))

    assert len(out['model_rounds']) == 3 and out['unfixed_checks'] == ['COMPILE_AND_UNIT_TEST']
    assert (proj / 'src/main/java/Svc.java').read_text() == 'class Svc{}', 'every attempt was reverted'


def test_a_cited_file_is_shown_whole_not_cut_at_six_thousand_characters(env, monkeypatch):
    repo, job, plan_id, tmp = env
    body = 'class Svc{\n' + ''.join('  int f%d;\n' % i for i in range(1500)) + '  BROKEN here;\n}\n'
    proj = _project(tmp, java_text=body)
    assert len(body) > 6000
    _gate(monkeypatch, [[_compile('FAIL', ['[ERROR] /w/Svc.java:[1502,3] cannot find symbol BROKEN'])]])
    model = _Model([[]])

    CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=1,
                                    context=_ctx(repo, job['id'], model))

    shown = next(f for f in model.payloads[0]['files'] if f['path'].endswith('Svc.java'))
    assert 'BROKEN here;' in shown['content'], 'the line the compiler names is in what the model reads'
