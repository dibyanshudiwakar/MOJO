"""Run durations are shown in minutes and seconds, and tick while they run.

Two halves. The first runs ``app/static/duration.js`` in node and checks the
arithmetic, because "847.3s" for a fourteen-minute code-generation step is the
whole reason this code exists and an off-by-a-factor formatter would read as
plausible. The second checks the contract the live counter depends on: the
pipeline detail payload has to carry the pipeline's own ``started_at`` and
``completed_at``, or there is no total to show.

The node half skips where node is absent rather than failing -- it is the only
thing in this suite that needs it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.repository import Repository
from app.service import FactoryService
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'app' / 'static'
DURATION_JS = STATIC / 'duration.js'

#: ``(javascript expression, expected String() of it)``. Everything is compared
#: as a string so a NaN return is assertable instead of being flattened to null
#: by JSON.
CASES = [
    # Under a second is '<1s', never '0s': a step that ran did not take no time,
    # and '0s' beside a COMPLETED badge reads as a missing value.
    ('fmtDur(0)', '<1s'),
    ('fmtDur(1)', '<1s'),
    ('fmtDur(999)', '<1s'),
    # Whole seconds below a minute.
    ('fmtDur(1000)', '1s'),
    ('fmtDur(1499)', '1s'),
    ('fmtDur(45000)', '45s'),
    ('fmtDur(59999)', '59s'),
    # Minutes and seconds, with the seconds kept at a round minute so a column
    # of these holds one shape.
    ('fmtDur(60000)', '1m 0s'),
    ('fmtDur(74000)', '1m 14s'),
    ('fmtDur(300000)', '5m 0s'),
    # The case that prompted the change: a real code-generation step.
    ('fmtDur(847300)', '14m 7s'),
    ('fmtDur(3599000)', '59m 59s'),
    # Past an hour the seconds stop being information and give way to hours --
    # a pipeline parked at a gate overnight.
    ('fmtDur(3600000)', '1h 0m'),
    ('fmtDur(7830000)', '2h 10m'),
    ('fmtDur(86400000)', '24h 0m'),
    # Nothing to show rather than a nonsense string. A negative span reaches
    # here only from a clock disagreement, which spanMs already clamps.
    ('fmtDur(-1)', ''),
    ('fmtDur(NaN)', ''),
    ('fmtDur(Infinity)', ''),
    ("fmtDur('600000')", ''),
    ('fmtDur(null)', ''),
    ('fmtDur(undefined)', ''),
    # spanMs: both stamps present.
    ("spanMs('2026-10-17T10:00:00Z','2026-10-17T10:14:07Z')", '847000'),
    # An empty finish stamp means "until now", which is what makes the chip live.
    ("spanMs('2026-10-17T10:00:00Z','',Date.parse('2026-10-17T10:02:30Z'))", '150000'),
    # Clamped at zero: the server stamps UTC from its own clock, and a browser a
    # few seconds behind it would otherwise compute a negative span for a step
    # that just started -- which fmtDur blanks, so the counter would disappear
    # for the first seconds of every run.
    ("spanMs('2026-10-17T10:00:10Z','',Date.parse('2026-10-17T10:00:00Z'))", '0'),
    ("spanMs('','2026-10-17T10:14:07Z')", 'NaN'),
    ("spanMs('not-a-date','2026-10-17T10:14:07Z')", 'NaN'),
    ("spanMs('2026-10-17T10:00:00Z','not-a-date')", 'NaN'),
    # fmtSpan is the composition the UI actually calls.
    ("fmtSpan('2026-10-17T10:00:00Z','2026-10-17T10:14:07Z')", '14m 7s'),
    ("fmtSpan('2026-10-17T10:00:00Z','2026-10-17T10:00:00Z')", '<1s'),
    ("fmtSpan('','')", ''),
]


@pytest.fixture(scope='module')
def evaluated():
    """``{expression: String(result)}`` from one node process."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed; the duration formatter cannot be executed')
    harness = ('const {fmtDur,spanMs,fmtSpan}=require(%s);\n'
               'process.stdout.write(JSON.stringify([%s]));\n'
               % (json.dumps(DURATION_JS.as_posix()),
                  ','.join('String(%s)' % expr for expr, _ in CASES)))
    out = subprocess.run([node, '--input-type=commonjs', '-e', harness],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, 'node could not evaluate duration.js: ' + out.stderr
    return dict(zip([expr for expr, _ in CASES], json.loads(out.stdout)))


@pytest.mark.parametrize('expression,expected', CASES, ids=[c[0] for c in CASES])
def test_the_formatter_renders_minutes_and_seconds(evaluated, expression, expected):
    assert evaluated[expression] == expected


def test_no_duration_is_still_rendered_as_raw_seconds():
    """The three places that printed '847.3s' all go through the formatter now."""
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert "toFixed(1)+'s'" not in js


def test_the_page_loads_the_formatter_before_the_script_that_uses_it():
    """A classic script, so order is the only thing making fmtSpan defined."""
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    assert html.index('/static/duration.js') < html.index('/static/app.js')


def test_a_running_duration_is_marked_for_the_ticker_and_a_finished_one_is_not():
    """``data-dur-from`` is the whole contract between durationTag and the ticker.

    Asserted on the source because there is no DOM here: the attribute is
    written only when ``completed_at`` is empty, and the ticker only ever
    rewrites elements carrying it, so a finished duration can never be retimed.
    """
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert "row.completed_at?'':` data-dur-from=" in js
    assert "document.querySelectorAll('[data-dur-from]')" in js


def test_the_dashboard_live_card_reads_the_field_the_api_returns():
    """``finished_at`` is not a column on ``pipeline_steps`` and never was.

    The dashboard's live step card read ``s.finished_at``, which is always
    undefined, so its ternary fell through to the started-but-not-finished arm
    and every completed step was still labelled "Running…". Asserted as the
    absence of the name because that is the bug: any reappearance of it is the
    same mistake.
    """
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    code = '\n'.join(line for line in js.splitlines() if not line.strip().startswith('//'))
    assert 'finished_at' not in code


@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'dur.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, PipelineService(repo), WorkflowOrchestrator(repo)


def test_the_detail_payload_carries_the_pipelines_own_run_window(env):
    """The total is wall-clock, so it needs the pipeline's stamps, not the steps'.

    Summing the steps would miss the queue waits between them and the time
    parked at a gate, which on a real migration is most of the elapsed hour.
    """
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake'],
                         'tester@example.com')
    detail = pipelines.detail(p['id'])
    # Present and empty before the run, so the UI renders no total rather than
    # failing on an absent key.
    assert detail['started_at'] == ''
    assert detail['completed_at'] == ''

    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    running = pipelines.detail(p['id'])
    assert running['status'] == 'RUNNING'
    assert running['started_at'], 'no start stamp means no elapsed counter'
    # Still open, which is what the UI reads as "tick this one".
    assert running['completed_at'] == ''


def test_a_step_reports_the_window_the_card_shows(env):
    repo, job, pipelines, workflow = env
    p = pipelines.create(job['id'], 'workspace/orders', {}, ['repository-intake'],
                         'tester@example.com')
    pipelines.confirm(p['id'])
    pipelines.run(p['id'], workflow)
    step = pipelines.detail(p['id'])['steps'][0]
    pipelines.mark_step(step['id'], 'RUNNING')
    live = pipelines.detail(p['id'])['steps'][0]
    assert live['started_at'] and live['completed_at'] == ''
    pipelines.mark_step(step['id'], 'COMPLETED', outputs={'ok': True})
    done = pipelines.detail(p['id'])['steps'][0]
    assert done['started_at'] and done['completed_at']
