"""The PARTIAL and GAP rows of the variance-analysis reports reach RCA and are fixed by remediation."""
import json

import pytest

from app.agents.rca import RCAAgent
from app.agents.remediation import RemediationAgent, STATIC_THRESHOLD_ENV, REPORT_TYPE
from app.repository import digest, now, uid
from app.services import static_findings
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

ROWS = [
    {'area': 'Field Mappings', 'iib_source': 'orders.esql:12', 'iib_value': 'SET Out.Total = A + B',
     'ms_source': '', 'ms_value': '', 'status': 'GAP', 'remarks': 'total is never computed'},
    {'area': 'Field Mappings', 'iib_source': 'orders.esql:20', 'iib_value': 'x', 'ms_source': 'OrderService.java:3',
     'ms_value': 'y', 'status': 'PARTIAL', 'remarks': 'rounding differs'},
    {'area': 'Error Handling', 'iib_source': 'orders.msgflow:Catch', 'iib_value': 'x',
     'ms_source': 'OrderService.java:3', 'ms_value': 'y', 'status': 'PARTIAL', 'remarks': 'no retry'},
    {'area': 'Validation', 'iib_source': 'a', 'iib_value': 'a', 'ms_source': 'b', 'ms_value': 'b',
     'status': 'MATCH', 'remarks': 'same'},
]


def _report(repo, job_id, rows=ROWS, report_type='PARITY_ANALYSIS'):
    payload = json.dumps({'report': {'action': 'parity_check', 'rows': rows}})
    rid = uid()
    with repo.connect() as c:
        repo.insert(c, 'validation_reports', {
            'id': rid, 'job_id': job_id, 'report_type': report_type, 'version': '1.0.0', 'payload': payload,
            'content_hash': digest(payload), 'status': 'ANALYSED', 'created_at': now(), 'reference_id': ''})
    return rid


def test_only_partial_and_gap_rows_are_findings(repo, job):
    _report(repo, job['id'])
    rows = static_findings.load(repo, job['id'])
    assert sorted(r['status'] for r in rows) == ['GAP', 'PARTIAL', 'PARTIAL']
    assert {r['source'] for r in rows} == {'PARITY'}
    assert set(static_findings.groups(rows)) == {('PARITY', 'Field Mappings'), ('PARITY', 'Error Handling')}


def test_a_job_with_no_report_has_no_findings(repo, job):
    assert static_findings.load(repo, job['id']) == []


def test_rca_explains_each_report_area_as_a_root_cause(repo, job):
    _report(repo, job['id'])
    result = RCAAgent(repo).run(job['id'])
    hyp = {h['kind']: h for h in result.outputs['rca_hypotheses']}
    assert set(hyp) == {'PARITY · Field Mappings', 'PARITY · Error Handling'}
    fm = hyp['PARITY · Field Mappings']
    assert fm['source'] == 'PARITY' and fm['area'] == 'Field Mappings' and fm['finding_count'] == 2
    assert {f['status'] for f in fm['findings']} == {'GAP', 'PARTIAL'}
    assert 0.0 <= fm['confidence'] <= 1.0
    assert set(fm['components']) == {'evidence_coverage', 'reproducible', 'traceability',
                                     'rule_strength', 'test_isolation', 'historical_validation'}
    stored = repo.one("select * from root_causes where id=?", (fm['root_cause_id'],))
    assert stored['affected_scope'] == 'STATIC:PARITY:Field Mappings' and stored['status'] == 'PROPOSED'
    assert result.metrics['static_finding_count'] == 3


def test_a_location_cited_in_two_groups_counts_as_reproduced(repo, job):
    _report(repo, job['id'])
    hyp = {h['kind']: h for h in RCAAgent(repo).run(job['id']).outputs['rca_hypotheses']}
    # OrderService.java:3 is cited in both groups; the GAP row cites nothing shared.
    assert hyp['PARITY · Error Handling']['components']['reproducible'] == 1.0
    assert hyp['PARITY · Field Mappings']['components']['reproducible'] == 0.5


def test_no_variances_and_no_report_still_completes_empty(repo, job):
    result = RCAAgent(repo).run(job['id'])
    assert result.status == 'COMPLETED' and result.outputs['rca_hypotheses'] == []


@pytest.fixture
def project(repo, job, planned, tmp_path):
    """A generated project on disk with a quality run recorded against it, as code generation leaves it."""
    root = tmp_path / 'proj'
    src = root / 'src' / 'main' / 'java'
    src.mkdir(parents=True)
    (root / 'pom.xml').write_text('<project/>')
    (src / 'OrderService.java').write_text('class OrderService {\n  int total() { return 1; }\n}\n')
    MappingAndPlanningService(repo).quality_validate(job['id'], planned['plan_id'], str(root), False)
    return root


def _run(repo, job, project, edits, monkeypatch, model, threshold='0.0'):
    monkeypatch.setenv(STATIC_THRESHOLD_ENV, threshold)
    model.overrides['agent/remediation/code-fix'] = lambda payload: edits
    RCAAgent(repo).run(job['id'])
    return RemediationAgent(repo).run(job['id'], params={'execute_build': False})


def test_an_eligible_static_cause_is_fixed_in_the_project(repo, job, project, monkeypatch, model):
    _report(repo, job['id'])
    edit = {'path': 'src/main/java/OrderService.java', 'old_str': 'return 1;', 'new_str': 'return 2;',
            'why': 'completes the total'}
    result = _run(repo, job, project, {'edits': [edit], 'unfixable': []}, monkeypatch, model)

    assert result.status == 'COMPLETED'
    assert 'return 2;' in (project / 'src/main/java/OrderService.java').read_text()
    assert result.metrics['fixes_applied'] >= 1
    report = json.loads(repo.one("select payload from validation_reports where report_type=? order by created_at desc",
                                 (REPORT_TYPE,))['payload'])
    fixed = [c for c in report['causes'] if c['applied']]
    assert fixed and fixed[0]['verdict'] == 'kept'
    assert report['gate']['result'] == 'kept' and 'does not re-run' in report['gate']['basis']
    assert repo.one("select count(*) n from root_causes where status='REMEDIATED'")['n'] >= 1


def test_a_fix_that_makes_the_gate_worse_is_reverted(repo, job, project, monkeypatch, model):
    _report(repo, job['id'])
    # A hard-coded endpoint fails HARDCODED_ENDPOINT_SCAN, which passed before.
    edit = {'path': 'src/main/java/OrderService.java', 'old_str': 'return 1;',
            'new_str': 'String u = "https://orders.internal.example.org/api"; return 1;', 'why': 'bad idea'}
    before = (project / 'src/main/java/OrderService.java').read_text()
    result = _run(repo, job, project, {'edits': [edit], 'unfixable': []}, monkeypatch, model)

    assert (project / 'src/main/java/OrderService.java').read_text() == before
    report = json.loads(repo.one("select payload from validation_reports where report_type=? order by created_at desc",
                                 (REPORT_TYPE,))['payload'])
    assert report['gate']['result'] == 'reverted_regression'
    assert result.metrics['fixes_applied'] == 0
    assert any('reverted' in u['reason'] for c in report['causes'] for u in c['unfixed'])


def test_a_path_outside_the_project_is_refused(repo, job, project, monkeypatch, model):
    _report(repo, job['id'])
    edit = {'path': '../outside.java', 'old_str': 'x', 'new_str': 'y'}
    _run(repo, job, project, {'edits': [edit], 'unfixable': []}, monkeypatch, model)
    report = json.loads(repo.one("select payload from validation_reports where report_type=? order by created_at desc",
                                 (REPORT_TYPE,))['payload'])
    assert any('path_not_relative' in r for c in report['causes'] for r in c['refused'])


def test_a_cause_below_the_bar_is_reported_not_fixed(repo, job, project, monkeypatch, model):
    _report(repo, job['id'])
    edit = {'path': 'src/main/java/OrderService.java', 'old_str': 'return 1;', 'new_str': 'return 2;'}
    before = (project / 'src/main/java/OrderService.java').read_text()
    result = _run(repo, job, project, {'edits': [edit], 'unfixable': []}, monkeypatch, model, threshold='0.99')
    assert (project / 'src/main/java/OrderService.java').read_text() == before
    assert result.status == 'COMPLETED' and result.metrics['not_eligible'] >= 1


def test_declared_unfixable_findings_are_listed(repo, job, project, monkeypatch, model):
    _report(repo, job['id'])
    answer = {'edits': [], 'unfixable': [{'ref': 'orders.esql:12', 'reason': 'needs a design decision'}]}
    _run(repo, job, project, answer, monkeypatch, model)
    report = json.loads(repo.one("select payload from validation_reports where report_type=? order by created_at desc",
                                 (REPORT_TYPE,))['payload'])
    assert any(u['ref'] == 'orders.esql:12' for c in report['causes'] for u in c['unfixed'])


def test_remediation_runs_again_without_redoing_fixed_causes(repo, job, project, monkeypatch, model):
    _report(repo, job['id'])
    edit = {'path': 'src/main/java/OrderService.java', 'old_str': 'return 1;', 'new_str': 'return 2;'}
    _run(repo, job, project, {'edits': [edit], 'unfixable': []}, monkeypatch, model)
    again = RemediationAgent(repo).run(job['id'], params={'execute_build': False})
    # Each fixed cause is no longer PROPOSED, so only what was left over is attempted again.
    assert again.metrics.get('causes', 0) < 2
