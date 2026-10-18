"""No code may log while holding a write transaction.

``StepLogger._write`` opens its own connection to append a row to
``pipeline_step_logs``, and SQLite permits exactly one writer. So a log call
inside ``with repo.connect() as c:`` -- after that connection has written
anything -- is a self-deadlock: the logger's connection waits out
``busy_timeout`` (30 seconds) and the step dies with "database is locked". It
needs no second process and no contention with the server.

It happened. A real ace-analysis of IDCardProfile raised 95 clarifications, and
``AgentRegistry.dispatch`` logged one line per clarification inside the
transaction that inserted them:

    04:12:09 [INFO]  Produced 17 artefact(s), all model-authored
    04:12:10 [INFO]  Evidence registered: ACE_ANALYSIS -> 241ecee9-324
    04:12:46 [ERROR] Step failed: database is locked

Thirty-six seconds: the first insert took the write lock, the first log call
blocked for the full timeout, and the agent's completed work was thrown away at
the last step.

A static check rather than a functional one, deliberately. Reproducing the
deadlock costs 30 seconds of waiting per assertion and only covers the one call
site that happens to be exercised; this covers every call site in the codebase
and runs in milliseconds. It is the whole class, not the instance.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: ``with <anything>.connect() as <name>:`` -- the transaction scope.
TRANSACTION = re.compile(r'^(\s*)with\s+[\w.]*\.?connect\(\)\s+as\s+\w+\s*:')

#: Anything that appends to pipeline_step_logs. StepLogger is reached through
#: several names: the bound methods, the local aliases dispatch builds, and the
#: ``logger`` an agent context carries.
LOG_CALL = re.compile(
    r'\b(_log_info|_log_warn|_log_error|_log_success'
    r'|log\.info|log\.warn|log\.error|log\.success'
    r'|logger\.info|logger\.warn|logger\.error|logger\.success)\s*\(')

SKIP = ('.venv', '__pycache__', 'tests', 'node_modules')


def _offences():
    """``(path, line number, source)`` for every log call inside a transaction."""
    found = []
    for path in sorted(ROOT.rglob('*.py')):
        if any(part in SKIP for part in path.parts):
            continue
        lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
        index = 0
        while index < len(lines):
            opened = TRANSACTION.match(lines[index])
            if not opened:
                index += 1
                continue
            indent = len(opened.group(1))
            cursor = index + 1
            while cursor < len(lines):
                line = lines[cursor]
                # Dedent to the `with` or shallower ends the block. Blank lines
                # carry no indentation, so they never end it.
                if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                    break
                if LOG_CALL.search(line):
                    found.append((path.relative_to(ROOT), cursor + 1, line.strip()))
                cursor += 1
            index = cursor
    return found


def test_nothing_logs_while_holding_a_write_transaction():
    offences = _offences()
    assert offences == [], (
        'A log call inside a write transaction deadlocks against itself: the '
        'logger opens a second connection and SQLite allows one writer, so it '
        'waits out busy_timeout and the step fails with "database is locked". '
        'Move the logging after the transaction, or collect the messages and '
        'emit one line.\n' + '\n'.join(
            '  %s:%d  %s' % (p, n, t) for p, n, t in offences))


def test_the_check_can_actually_see_an_offence(tmp_path):
    """A detector that never fires is worth nothing.

    Pins the two things the scan has to get right: that it looks inside the
    block, and that it stops at the dedent rather than flagging the rest of
    the file.
    """
    sample = tmp_path / 'sample.py'
    sample.write_text(
        'def f(self):\n'
        '    with self.repo.connect() as c:\n'
        '        self.repo.insert(c, "t", {})\n'
        '        _log_info("inside -- deadlocks")\n'
        '    _log_info("after -- fine")\n', encoding='utf-8')
    lines = sample.read_text(encoding='utf-8').splitlines()
    opened = TRANSACTION.match(lines[1])
    assert opened, 'the transaction opener must match'
    indent = len(opened.group(1))
    inside, after = [], []
    for number, line in enumerate(lines[2:], start=3):
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            after.append(number)
            continue
        if LOG_CALL.search(line):
            inside.append(number)
    assert inside == [4], 'the call inside the block is the offence'
    assert 5 in after, 'the call after the dedent is not'
