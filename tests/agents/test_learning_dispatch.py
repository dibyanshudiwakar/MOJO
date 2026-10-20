"""The recording is wired into dispatch, not only present as a module."""


def test_every_dispatched_run_leaves_a_metrics_row(repo, job, approved_inputs):
    rows = repo.rows('select agent_id,status,attempts from run_metrics where job_id=?', (job['id'],))
    assert rows, 'dispatching the input agents recorded nothing'
    runs = repo.rows('select count(*) n from agent_runs where job_id=?', (job['id'],))[0]['n']
    assert len(rows) == runs
    assert all(r['status'] == 'COMPLETED' for r in rows)
