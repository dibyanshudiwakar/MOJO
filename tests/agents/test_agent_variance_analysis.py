import json
from app.agents.variance_analysis import VarianceAnalysisAgent


def test_variances_land_in_both_stores(repo, job, executed_package):
    """The promotion precondition reads differential_evidence; RCA reads variances.
    Writing only one store would blind one of them."""
    package_id = executed_package(job['id'])
    result = VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    assert result.outputs['severity'] == 'MEDIUM'
    record, = result.outputs['variance_records']
    assert record['status'] == 'VARIANCE'
    evidence = repo.one('select * from differential_evidence where id=?', (record['evidence_id'],))
    assert evidence['package_id'] == package_id and evidence['disposition'] == 'OPEN'
    variances = repo.rows('select * from variances where job_id=?', (job['id'],))
    assert [x['path'] for x in variances] == ['$.body.id']
    assert variances[0]['kind'] == 'VALUE' and variances[0]['disposition'] == 'OPEN'


def test_a_matching_pair_produces_no_variance(repo, job, executed_package):
    package_id = executed_package(job['id'], ace_result={'status': 200, 'body': {'id': 1}},
                                  spring_result={'status': 200, 'body': {'id': 1}})
    result = VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    assert result.outputs['severity'] == 'NONE'
    assert result.outputs['variance_records'][0]['status'] == 'PASS'
    assert repo.one('select count(*) n from variances where job_id=?', (job['id'],))['n'] == 0


def test_a_structural_difference_is_classified_and_ranked_higher(repo, job, executed_package):
    package_id = executed_package(job['id'], ace_result={'status': 200, 'body': {'id': 1, 'note': 'x'}},
                                  spring_result={'status': 200, 'body': {'id': 1}})
    result = VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    assert result.outputs['severity'] == 'HIGH'
    assert result.metrics['by_kind'] == {'STRUCTURAL': 1}


def test_the_policy_recorded_with_the_package_is_applied(repo, job, executed_package):
    package_id = executed_package(job['id'], ace_result={'status': 200, 'body': {'id': 1, 'ts': 'a'}},
                                  spring_result={'status': 200, 'body': {'id': 1, 'ts': 'b'}},
                                  policy={'ignore': ['$.body.ts']})
    result = VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    assert result.outputs['severity'] == 'NONE'


def test_a_package_prepared_but_never_replayed_still_analyses_cleanly(repo, job, planned, dispatch):
    """Without runtime adapters no results were captured -- there is nothing to
    diff, but that must not block the static parity/hardcoding checks below,
    which read ACE source and generated code directly and need no replay."""
    prepared = dispatch(job['id'], 'test-engineering', {'plan_id': planned['plan_id']})
    package_id = prepared['outputs']['test_suites']
    result = VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    assert result.outputs == {'variance_records': [], 'severity': 'NONE'}
    assert result.metrics['runtime_comparison'] == 'NOT_EXECUTED'
    assert result.metrics['case_count'] == 0


def test_a_report_with_no_executions_also_analyses_cleanly(repo, job):
    """An empty report must not read as "compared and matched" -- it reads the
    same as "nothing to compare yet", same as no report at all."""
    from app.repository import digest, now, uid
    package_id = uid()
    payload = json.dumps({'package_id': package_id, 'executions': []}, sort_keys=True)
    with repo.connect() as c:
        repo.insert(c, 'validation_reports',
                    {'id': uid(), 'job_id': job['id'], 'report_type': 'RUNTIME_EXECUTION',
                     'version': '1.0.0', 'payload': payload, 'content_hash': digest(payload),
                     'status': 'EXECUTED', 'created_at': now(), 'reference_id': package_id})
    result = VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    assert result.outputs == {'variance_records': [], 'severity': 'NONE'}
    assert result.metrics['runtime_comparison'] == 'NOT_EXECUTED'


def test_variance_analysis_registers_no_evidence(repo, job, executed_package):
    """Its contract grants variance.write, not evidence.write."""
    result = VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': executed_package(job['id'])})
    assert result.evidence == ()
