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
    assert any(x['severity'] == 'CRITICAL' for x in result.outputs['risk_register'])


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


# ---- auto mode: plan around gaps the model rates below HIGH ---------------------------------

@pytest.fixture
def mixed_inputs(repo, job, dispatch, approve, framework_repo, tmp_path):
    """Approved inputs for a service with one construct that has no Spring pattern."""
    ace = tmp_path / 'mixed'
    ace.mkdir()
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
    return {'framework_id': framework['outputs']['framework_template'],
            'analysis_id': analysis['outputs']['analysis_document'],
            'specification_id': specification['outputs']['migration_specification_yaml']}


def _gap_severity(model, severity):
    from tests.agents.model_double import ANSWERS

    def answer(payload):
        out = ANSWERS['agent/planning/mapping-catalogue'](payload)
        for entry in out['unsupported']:
            entry['severity'] = severity
        return out
    model.overrides['agent/planning/mapping-catalogue'] = answer


def test_a_gap_rated_below_high_is_planned_around_and_recorded(repo, job, mixed_inputs, model):
    _gap_severity(model, 'MEDIUM')
    result = PlanningAgent(repo).run(job['id'], params=mixed_inputs)
    assert result.status == 'COMPLETED', 'a MEDIUM gap does not stop the plan'
    assert result.outputs['plan']
    assert result.metrics['accepted_risk_count'] == 1
    # Recorded, not hidden: both rows name who decided, so the register and the
    # G3 package show exactly what the plan leaves out.
    gap = repo.one('select status from unsupported_mappings where catalogue_id=?',
                   (result.outputs['mapping'],))
    assert gap['status'] == 'ACCEPTED_BY_AUTO_MODE'
    assert {r['status'] for r in result.outputs['risk_register']
            if r['category'] == 'UNSUPPORTED_MAPPING'} == {'ACCEPTED_BY_AUTO_MODE'}
    package = repo.one('select payload from approval_packages where id=?',
                       (result.metrics['g3_package_id'],))
    assert 'ACCEPTED_BY_AUTO_MODE' in package['payload'] or 'ACCEPTED_BY_AUTO_MODE' in json.dumps(
        [dict(r) for r in repo.rows('select * from migration_risks where catalogue_id=?',
                                    (result.outputs['mapping'],))])


def test_a_critical_gap_still_stops_the_plan_to_be_asked(repo, job, mixed_inputs, model):
    _gap_severity(model, 'CRITICAL')
    result = PlanningAgent(repo).run(job['id'], params=mixed_inputs)
    assert result.status == 'CLARIFICATION_REQUIRED' and result.outputs['plan'] == ''
    assert result.metrics['accepted_risk_count'] == 0
    assert len(result.clarifications) == 1


def test_with_auto_mode_off_even_a_minor_gap_stops_the_plan(repo, job, mixed_inputs, model,
                                                            auto_mode_off):
    _gap_severity(model, 'LOW')
    result = PlanningAgent(repo).run(job['id'], params=mixed_inputs)
    assert result.status == 'CLARIFICATION_REQUIRED'


def test_a_gap_with_no_risk_rating_is_treated_as_high():
    """An unrated gap is the worst case, so it must stop the plan."""
    mapped = {'unsupported': [{'source_reference': 'M#odd'}], 'risks': []}
    out, accepted = PlanningAgent._accept_minor_gaps(mapped)
    assert accepted == 0 and 'status' not in out['unsupported'][0]


def test_the_worst_risk_for_a_construct_decides():
    mapped = {'unsupported': [{'source_reference': 'M#odd'}],
              'risks': [{'source_reference': 'M#odd', 'severity': 'LOW'},
                        {'source_reference': 'M#odd', 'severity': 'CRITICAL'}]}
    assert PlanningAgent._accept_minor_gaps(mapped)[1] == 0


# ---- the risk register is derived, not authored --------------------------------------------
#
# Twice the mapping reply overran max_tokens (39,313 then 45,270 characters), both
# times in the model's own risk register for one ESQL module. Prompt wording did
# not hold it down, so the register is no longer part of the answer.

def test_a_risk_is_derived_for_each_unsupported_entry():
    merged = PlanningAgent._merge([{'mappings': [], 'unsupported': [
        {'source_reference': 'M#odd', 'ace_construct': 'AcmeNode', 'severity': 'medium',
         'risk': 'Routing would be lost.', 'owner': 'ACE_SME'}]}])
    risk, = merged['risks']
    assert (risk['severity'], risk['statement'], risk['source_reference']) == (
        'MEDIUM', 'Routing would be lost.', 'M#odd')
    assert risk['category'] == 'UNSUPPORTED_MAPPING' and risk['status'] == 'OPEN'


def test_an_unrated_unsupported_entry_defaults_to_critical():
    """CRITICAL is the rating that stops the plan, so a missing one fails safe."""
    merged = PlanningAgent._merge([{'unsupported': [{'source_reference': 'M#odd'}]}])
    assert merged['risks'][0]['severity'] == 'CRITICAL'


def test_a_model_supplied_risk_is_not_duplicated_by_the_derived_one():
    merged = PlanningAgent._merge([{'unsupported': [{'source_reference': 'M#odd'}],
                                    'risks': [{'source_reference': 'M#odd', 'severity': 'LOW',
                                               'category': 'X', 'statement': 's'}]}])
    assert len(merged['risks']) == 1 and merged['risks'][0]['severity'] == 'LOW'


def test_the_current_prompt_does_not_ask_for_a_risk_register():
    import json
    from pathlib import Path
    prompts = json.loads((Path(__file__).resolve().parents[2] / 'config' / 'agent-prompts.v1.json')
                         .read_text(encoding='utf-8'))['prompts']
    latest = max((p for p in prompts if p['name'] == 'agent/planning/mapping-catalogue'),
                 key=lambda p: int(p['version']))
    assert '\"risks\"' not in latest['template'].split('Return JSON:')[1]


def test_a_high_gap_is_planned_around_not_asked(repo, job, mixed_inputs, model):
    """HIGH is below the line, the same as for questions."""
    _gap_severity(model, 'HIGH')
    result = PlanningAgent(repo).run(job['id'], params=mixed_inputs)
    assert result.status == 'COMPLETED' and result.metrics['accepted_risk_count'] == 1
