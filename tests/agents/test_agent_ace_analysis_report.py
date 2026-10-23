"""The ACE analysis agent writes the HTML hand-over report once the analysis is stored.

The renderer itself is covered in ``tests/test_ace_analysis_report.py``; this is only the hook: where the
file goes, what it records, and that a failure to write it cannot fail an analysis that is already stored.
"""
from app.agents.ace_analysis import ACEAnalysisAgent
from app.services import ace_analysis_report


def test_the_report_is_written_under_the_workspace_reports_folder(repo, job, ace_repo, tmp_path):
    result = ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)})
    assert result.status == 'COMPLETED'
    assert 'report_error' not in result.metrics, result.metrics.get('report_error')
    written = tmp_path / result.metrics['report_path']
    assert written.name == ace_analysis_report.FILENAME
    assert written.parent == tmp_path / 'reports' / str(job['id'])
    page = written.read_text(encoding='utf-8')
    assert 'IBM Integration Bus Application Analysis' in page
    assert 'Flow 1: Orders' in page
    # the analysis the report describes is the one that was stored
    assert result.outputs['analysis_document'] in page


def test_a_pipeline_workspace_is_preferred_when_the_job_has_one(repo, job, ace_repo, tmp_path):
    folder = tmp_path / 'pipe-1'
    with repo.connect() as c:
        c.execute('insert into pipelines(id,job_id,workspace_dir,created_at) values(?,?,?,?)',
                  ('p1', job['id'], str(folder), '2026-10-22T00:00:00'))
    result = ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)})
    assert (tmp_path / result.metrics['report_path']).parent == folder / 'reports'


def test_the_contract_outputs_are_unchanged_by_the_report(repo, job, ace_repo):
    result = ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)})
    assert set(result.outputs) == set(ACEAnalysisAgent.OUTPUTS)


def test_a_report_failure_never_fails_the_analysis(repo, job, ace_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('render exploded')
    monkeypatch.setattr(ace_analysis_report, 'build', boom)
    result = ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)})
    assert result.status == 'COMPLETED'
    assert result.metrics['report_error'] == 'RuntimeError:render exploded'
    assert 'report_path' not in result.metrics
    assert repo.one('select count(*) n from ace_analyses where job_id=?', (job['id'],))['n'] == 1
