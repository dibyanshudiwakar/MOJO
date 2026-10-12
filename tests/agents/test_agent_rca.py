import json
from app.agents.rca import RCAAgent
from app.agents.variance_analysis import VarianceAnalysisAgent


def _variances(repo, job, executed_package, **kwargs):
    package_id = executed_package(job['id'], **kwargs)
    VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    return package_id


def test_one_hypothesis_per_variance_classification(repo, job, executed_package):
    """A hypothesis spanning structural and value variances would be too vague to act on."""
    _variances(repo, job, executed_package,
               ace_result={'status': 200, 'body': {'id': 1, 'note': 'x'}},
               spring_result={'status': 201, 'body': {'id': 2}})
    result = RCAAgent(repo).run(job['id'])
    kinds = {x['kind'] for x in result.outputs['rca_hypotheses']}
    assert kinds == {'STRUCTURAL', 'VALUE'}
    for hypothesis in result.outputs['rca_hypotheses']:
        row = repo.one('select * from root_causes where id=?', (hypothesis['root_cause_id'],))
        assert row['status'] == 'PROPOSED'
        assert json.loads(row['variance_ids'])
        assert 0.0 <= row['confidence'] <= 1.0


def test_every_confidence_component_is_measured_not_chosen(repo, job, executed_package):
    """The score gates direct remediation, so a free component would let the agent
    authorise its own fix."""
    _variances(repo, job, executed_package)
    result = RCAAgent(repo).run(job['id'])
    components = result.outputs['rca_hypotheses'][0]['components']
    assert set(components) == {'evidence_coverage', 'reproducible', 'traceability',
                               'rule_strength', 'test_isolation', 'historical_validation'}
    # One case, seen once, with no prior root cause on this job.
    assert components['evidence_coverage'] == 1.0
    assert components['traceability'] == 1.0
    assert components['reproducible'] == 0.0
    assert components['historical_validation'] == 0.0
    assert components['rule_strength'] == 0.6  # VALUE implies a cause less strongly


def test_missing_evidence_is_stated_rather_than_assumed_away(repo, job, executed_package):
    _variances(repo, job, executed_package)
    result = RCAAgent(repo).run(job['id'])
    gaps = result.outputs['missing_evidence']
    assert 'a second test case exercising the same path' in gaps
    assert 'a previously validated root cause on this job' in gaps
    stored = repo.one('select missing_evidence from root_causes where job_id=?', (job['id'],))
    assert json.loads(stored['missing_evidence'])


def test_a_reproducible_path_scores_higher(repo, job, executed_package):
    for case in ('CASE-001', 'CASE-002'):
        package_id = executed_package(job['id'], case_id=case)
        VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id})
    result = RCAAgent(repo).run(job['id'])
    assert result.outputs['rca_hypotheses'][0]['components']['reproducible'] == 1.0


def test_no_open_variance_means_no_hypothesis(repo, job):
    result = RCAAgent(repo).run(job['id'])
    assert result.status == 'COMPLETED'
    assert result.outputs['rca_hypotheses'] == []
    assert repo.one('select count(*) n from root_causes where job_id=?', (job['id'],))['n'] == 0
