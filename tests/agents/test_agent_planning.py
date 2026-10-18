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


# ---- mapped in bounded groups --------------------------------------------------------------
#
# The whole semantic model plus the whole specification came to 245,531
# characters against the gateway's 180,000 limit on a real service, so planning
# failed before reaching the model, with no retry possible.

def _model(flows, esql):
    return {'flows': [{'path': 'F%d.msgflow' % i,
                       'nodes': [{'id': 'n%d' % j, 'type': 'ComputeNode'} for j in range(flows[1])]}
                      for i in range(flows[0])],
            'esql': [{'path': 'E%d.esql' % i, 'body': 'x' * esql[1]} for i in range(esql[0])],
            'interfaces': [{'name': 'i'}], 'behaviour': {'k': 'v'},
            'ambiguities': [{'question': 'q' * 1000}] * 100}


def test_a_large_model_is_split_into_groups_under_the_budget():
    groups = PlanningAgent(None)._groups(_model((2, 3), (6, 5000)))
    assert len(groups) > 1
    assert all(len(json.dumps(g)) < PlanningAgent.GROUP_CHARS * 2 for g in groups)


def test_every_construct_lands_in_exactly_one_group():
    model = _model((3, 2), (4, 20000))
    groups = PlanningAgent(None)._groups(model)
    assert sorted(f['path'] for g in groups for f in g.get('flows', [])) == [f['path'] for f in model['flows']]
    assert sorted(e['path'] for g in groups for e in g.get('esql', [])) == [e['path'] for e in model['esql']]


def test_context_travels_with_every_group_and_ambiguities_do_not():
    for group in PlanningAgent(None)._groups(_model((1, 1), (4, 20000))):
        assert group['interfaces'] and group['behaviour']
        assert 'ambiguities' not in group


def test_the_merge_keeps_each_construct_once():
    m = {'source_reference': 'F#n', 'ace_construct': 'ComputeNode'}
    merged = PlanningAgent._merge([{'mappings': [m], 'risks': [{'a': 1}]},
                                   {'mappings': [m, {**m, 'source_reference': 'G#n'}]}])
    assert [x['source_reference'] for x in merged['mappings']] == ['F#n', 'G#n']
    assert merged['risks'] == [{'a': 1}] and merged['unsupported'] == []
