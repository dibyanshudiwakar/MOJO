"""Dispatch with the logger the worker actually passes.

Every test in this suite dispatched agents with ``logger=None``, and
``app/worker.py`` always passes a ``StepLogger``. So the one thing a real run
does that no test did was write log rows -- and that is where a deadlock lived:
``AgentRegistry.dispatch`` logged one line per clarification from inside the
transaction that inserted them, the logger opened a second connection, SQLite
allows one writer, and the step died on ``busy_timeout`` with "database is
locked".

Observed on a real ace-analysis of IDCardProfile, which raised 95
clarifications. The agent's work was complete -- 17 model-authored artefacts,
evidence registered -- and it was discarded at the last step:

    04:12:10 [INFO]  Evidence registered: ACE_ANALYSIS -> 241ecee9-324
    04:12:46 [ERROR] Step failed: database is locked

``tests/test_no_logging_inside_a_transaction.py`` forbids the pattern
statically, across the codebase and in milliseconds. This file is the other
half: it exercises the path, so a deadlock introduced some way the static scan
cannot see -- a helper that logs, a logger passed somewhere new -- still fails
a test rather than a migration.
"""
import json

import pytest

from app.services.agents import AgentRegistry
from app.services.pipelines import PipelineService, StepLogger

#: A framework repository with no build file and none of the standards the
#: extractor looks for, so the baseline comes back with several conflicts and
#: the agent raises a clarification for each. More than one matters: the
#: deadlock fired on the *first* log call inside the transaction, so a single
#: clarification would reproduce it, but several is what a real run looks like.
EMPTY_FRAMEWORK = {'README.md': 'no build file, no conventions'}


@pytest.fixture
def step_logger(repo, job):
    """A StepLogger bound to a real pipeline step, as the worker builds it."""
    pipelines = PipelineService(repo)
    pipeline = pipelines.create(job['id'], 'workspace/logged', {},
                                ['framework-baseline'], 'tester@example.com')
    step = pipelines.detail(pipeline['id'])['steps'][0]
    return StepLogger(repo, step['id'])


@pytest.fixture
def bare_framework(tmp_path, repo, job, ace_repo, dispatch):
    """The framework tree, with framework-baseline's dependency satisfied.

    The registry enforces the catalogue's dependency graph, so repository-intake
    has to have completed for this job before anything else dispatches.
    """
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    root = tmp_path / 'bare-framework'
    root.mkdir()
    for name, text in EMPTY_FRAMEWORK.items():
        (root / name).write_text(text, encoding='utf-8')
    return root


def test_dispatch_completes_while_writing_step_logs(repo, job, bare_framework, step_logger):
    """The regression. Before the fix this blocked for 30s and then raised.

    Nothing here asserts a duration: a deadlock surfaces as
    ``sqlite3.OperationalError: database is locked`` once ``busy_timeout``
    expires, so the failure is an error rather than a slow pass.
    """
    outcome = AgentRegistry(repo).dispatch(
        job['id'], 'framework-baseline', '1.0.0',
        ('FRAMEWORK_ANALYST',), 'tester@example.com',
        {'path': str(bare_framework)}, logger=step_logger)
    assert outcome['status'] == 'CLARIFICATION_REQUIRED', 'the conflicts must raise questions'
    assert outcome['clarification_ids'], 'and those questions must be persisted'


def test_the_clarifications_and_their_log_line_both_land(repo, job, bare_framework, step_logger):
    """Both writes survive, which is what the fix had to preserve.

    Moving the logging out of the transaction must not lose it: the Logs tab is
    how an operator sees that questions were raised at all.
    """
    outcome = AgentRegistry(repo).dispatch(
        job['id'], 'framework-baseline', '1.0.0',
        ('FRAMEWORK_ANALYST',), 'tester@example.com',
        {'path': str(bare_framework)}, logger=step_logger)
    raised = repo.rows('select stage,question,status,run_id from clarifications where job_id=?',
                       (job['id'],))
    assert len(raised) == len(outcome['clarification_ids'])
    assert {r['status'] for r in raised} == {'OPEN'}
    # Tied to the run that raised them, which is how park_for_clarification
    # finds its own questions rather than guessing by stage.
    assert {r['run_id'] for r in raised} == {outcome['run_id']}
    logs = repo.rows('select level,message from pipeline_step_logs where step_id=?',
                     (step_logger.step_id,))
    messages = [r['message'] for r in logs]
    assert any('Clarifications raised: %d' % len(raised) in m for m in messages), messages
    # One line, not one per clarification: the loop used to emit a write
    # transaction per question, which is both the deadlock and 95 rows of noise.
    assert sum(1 for m in messages if 'Clarifications raised' in m) == 1


def test_a_suggested_answer_is_attached_to_each_question(repo, job, bare_framework, step_logger):
    """The batched suggestion call runs outside the transaction too.

    It is a model call, so holding a write lock across it would block every
    other writer in the process for its whole duration -- a slower version of
    the same fault.
    """
    AgentRegistry(repo).dispatch(
        job['id'], 'framework-baseline', '1.0.0',
        ('FRAMEWORK_ANALYST',), 'tester@example.com',
        {'path': str(bare_framework)}, logger=step_logger)
    rows = repo.rows('select question,suggested_answer,suggestion_confidence'
                     ' from clarifications where job_id=?', (job['id'],))
    assert rows
    assert all(r['suggested_answer'] for r in rows), 'the double answers every question'
    assert all(r['suggestion_confidence'] for r in rows)
