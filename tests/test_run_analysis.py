"""The Metrics page: totals, the pipeline-by-step matrix and the per-agent summary."""
import pytest

from app.repository import Repository, now
from app.services import learning, run_analysis


@pytest.fixture
def repo(tmp_path):
    return Repository(str(tmp_path / 'metrics.db'))


def _pipeline(repo, pid, steps, status='COMPLETED'):
    with repo.connect() as c:
        c.execute("insert into pipelines(id,job_id,name,workspace_dir,status,created_at) values(?,?,?,?,?,?)",
                  (pid, 'j-' + pid, pid, 'ws/' + pid, status, now()))
        for i, (agent, st, started, done) in enumerate(steps):
            c.execute("insert into pipeline_steps(id,pipeline_id,sequence,kind,agent_id,status,started_at,completed_at) "
                      "values(?,?,?,?,?,?,?,?)", ('%s-%d' % (pid, i), pid, i, 'agent', agent, st, started, done))


def test_an_empty_database_reports_zeros_not_errors(repo):
    d = run_analysis.overview(repo)
    assert d['summary']['pipelines'] == 0 and d['summary']['runs'] == 0
    assert d['summary']['success_rate'] is None
    assert d['pipelines'] == [] and d['columns'] == []


def test_the_matrix_has_a_column_per_agent_and_a_cell_per_step(repo):
    _pipeline(repo, 'p1', [('repository-intake', 'COMPLETED', '2026-10-20T10:00:00+00:00', '2026-10-20T10:00:30+00:00'),
                           ('ace-analysis', 'FAILED', '2026-10-20T10:00:30+00:00', '2026-10-20T10:02:00+00:00')], 'FAILED')
    _pipeline(repo, 'p2', [('repository-intake', 'RUNNING', '2026-10-21T09:00:00+00:00', '')], 'RUNNING')
    d = run_analysis.overview(repo)
    assert d['columns'] == ['repository-intake', 'ace-analysis']
    p1 = next(p for p in d['pipelines'] if p['id'] == 'p1')
    assert p1['steps']['repository-intake'] == {'status': 'COMPLETED', 'seconds': 30}
    assert p1['steps']['ace-analysis']['status'] == 'FAILED' and p1['steps']['ace-analysis']['seconds'] == 90
    p2 = next(p for p in d['pipelines'] if p['id'] == 'p2')
    assert 'ace-analysis' not in p2['steps'], 'a step the pipeline did not include is simply absent'
    assert p2['steps']['repository-intake']['seconds'] is None, 'unfinished steps have no duration yet'
    assert d['summary']['by_status'] == {'FAILED': 1, 'RUNNING': 1}


def test_the_matrix_is_capped_to_the_newest_pipelines(repo):
    for i in range(run_analysis.MAX_PIPELINES + 5):
        _pipeline(repo, 'p%02d' % i, [('repository-intake', 'COMPLETED', '', '')])
    d = run_analysis.overview(repo)
    assert len(d['pipelines']) == run_analysis.MAX_PIPELINES
    assert d['summary']['pipelines'] == run_analysis.MAX_PIPELINES + 5, 'totals still count every pipeline'


def test_agent_totals_come_from_recorded_runs(repo):
    with repo.connect() as c:
        learning.ensure(c)
        for i, st in enumerate(('COMPLETED', 'COMPLETED', 'FAILED')):
            c.execute("insert into run_metrics(run_id,job_id,agent_id,status,duration_seconds,attempts,corrections,"
                      "effort_step_downs,gap_count,failed_checks,clarifications,created_at) "
                      "values(?,?,?,?,?,?,?,?,?,?,?,?)", ('r%d' % i, 'j', 'ace-analysis', st, 10.0, 1, 0, 0, 0, 0, 0, now()))
    s = run_analysis.overview(repo)['summary']
    assert (s['runs'], s['completed'], s['failed'], s['success_rate']) == (3, 2, 1, 67)
    assert s['avg_seconds'] == 10.0
