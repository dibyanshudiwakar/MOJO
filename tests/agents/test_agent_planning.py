import json
import pytest
from app.agents.planning import PlanningAgent


def test_planning_produces_a_catalogue_and_an_ordered_plan(repo, job, approved_inputs):
    result = PlanningAgent(repo).run(job['id'], params=approved_inputs)
    assert result.status == 'COMPLETED'
    plan = repo.one('select * from migration_plans where id=?', (result.outputs['plan'],))
    tasks = json.loads(plan['tasks'])
    assert [x['id'] for x in tasks] == ['IMP-%03d' % i for i in range(1, len(tasks) + 1)]
    # Each task depends on its predecessor, so the sequence is a real order.
    assert tasks[0]['depends_on'] == []
    assert all(t['depends_on'] == [tasks[i]['id']] for i, t in enumerate(tasks[1:]))
    assert result.outputs['task_graph'][0]['boundary'] in ('GENERATED', 'HAND_WRITTEN_PROTECTED')
    # It holds plan.write, not evidence.write: the plan is attached to the G3
    # decision by the approver, and what the planner produces is the package.
    assert result.evidence == ()
    package = repo.one('select * from approval_packages where id=?', (result.metrics['g3_package_id'],))
    assert package['gate'] == 'G3' and package['status'] == 'PENDING_APPROVAL'
    assert plan['content_hash'] in json.dumps(json.loads(package['artefact_bindings']))


def test_planning_refuses_without_the_bound_gate_approvals(repo, job, approved_inputs):
    with repo.connect() as c:
        c.execute("delete from approvals where gate='G1'")
    with pytest.raises(PermissionError, match='g1_framework_approval_required'):
        PlanningAgent(repo).run(job['id'], params=approved_inputs)


def test_an_unmappable_construct_stops_at_the_catalogue(repo, job, dispatch, approve,
                                                       framework_repo, tmp_path):
    """A plan omitting an unmappable construct would look complete while leaving behaviour behind."""
    ace = tmp_path / 'mixed'
    ace.mkdir()
    # A node the parser recognises but for which no approved Spring pattern
    # exists, so the gap surfaces in the mapping catalogue rather than earlier.
    (ace / 'M.msgflow').write_text('<flow><node id="in" type="HTTPInput" url="/x"/>'
                                   '<node id="odd" type="AcmeAdapterNode"/></flow>')
    dispatch(job['id'], 'repository-intake', {'path': str(ace)})
    framework = dispatch(job['id'], 'framework-baseline', {'path': str(framework_repo)})
    analysis = dispatch(job['id'], 'ace-analysis', {'path': str(ace)})
    specification = dispatch(job['id'], 'migration-specification',
                             {'analysis_id': analysis['outputs']['analysis_document']})
    for gate, outcome in (('G1', framework), ('G2', analysis), ('G2', specification)):
        ref = outcome['evidence'][0]
        approve(job['id'], gate, 'APPROVE', ref['id'], ref['content_hash'])
    result = PlanningAgent(repo).run(job['id'], params={
        'framework_id': framework['outputs']['framework_template'],
        'analysis_id': analysis['outputs']['analysis_document'],
        'specification_id': specification['outputs']['migration_specification_yaml']})
    assert result.status == 'CLARIFICATION_REQUIRED'
    assert result.outputs['plan'] == ''
    assert result.outputs['mapping']
    assert result.metrics['unsupported_count'] == 1
    assert result.evidence == ()
    assert any(x['severity'] == 'HIGH' for x in result.outputs['risk_register'])
