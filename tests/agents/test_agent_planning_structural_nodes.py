"""Planning with the kinds of node a real service has: subflow terminals, a subflow call, a Reset Content Descriptor.

The memberseobpreferences service produced no plan because ten such nodes had no approved Spring mapping.
"""
import json

from app.agents.planning import PlanningAgent


def _plan_for(repo, job, dispatch, approve, framework_repo, ace):
    dispatch(job['id'], 'repository-intake', {'path': str(ace)})
    framework = dispatch(job['id'], 'framework-baseline', {'path': str(framework_repo)})
    analysis = dispatch(job['id'], 'ace-analysis', {'path': str(ace)})
    specification = dispatch(job['id'], 'migration-specification',
                             {'analysis_id': analysis['outputs']['analysis_document']})
    for gate, outcome in (('G1', framework), ('G2', analysis), ('G2', specification)):
        ref = outcome['evidence'][0]
        approve(job['id'], gate, 'APPROVE', ref['id'], ref['content_hash'])
    return PlanningAgent(repo).run(job['id'], params={
        'framework_id': framework['outputs']['framework_template'],
        'analysis_id': analysis['outputs']['analysis_document'],
        'specification_id': specification['outputs']['migration_specification_yaml']})


def _service(tmp_path):
    ace = tmp_path / 'withsubflow'
    ace.mkdir()
    (ace / 'Main.msgflow').write_text(
        '<flow><node id="in" type="HTTPInput" url="/x"/>'
        '<node id="reset" type="ComIbmResetContentDescriptor.msgnode:FCMComposite_1"/>'
        '<node id="call" type="GET.subflow:FCMComposite_1"/>'
        '<node id="out" type="HTTPReply"/></flow>')
    (ace / 'GET.subflow').write_text(
        '<flow><node id="InTerminal.Input_1" type="eflow:FCMSource"/>'
        '<node id="work" type="ComputeNode"/>'
        '<node id="OutTerminal.Output_1" type="eflow:FCMSink"/></flow>')
    return ace


def test_a_service_with_subflow_terminals_a_subflow_call_and_a_reset_descriptor_gets_a_plan(
        repo, job, dispatch, approve, framework_repo, tmp_path):
    result = _plan_for(repo, job, dispatch, approve, framework_repo, _service(tmp_path))
    assert result.status == 'COMPLETED' and result.outputs['plan'], result.outputs
    assert result.metrics['unsupported_count'] == 0
    assert repo.one('select count(*) n from unsupported_mappings where job_id=?', (job['id'],))['n'] == 0


def test_the_terminals_are_not_tasks_and_the_new_constructs_carry_their_approved_pattern(
        repo, job, dispatch, approve, framework_repo, tmp_path):
    result = _plan_for(repo, job, dispatch, approve, framework_repo, _service(tmp_path))
    tasks = json.loads(repo.one('select tasks from migration_plans where id=?', (result.outputs['plan'],))['tasks'])
    references = ' '.join(t['source_reference'] for t in tasks)
    assert 'InTerminal' not in references and 'OutTerminal' not in references
    patterns = {t['source_reference'].split('#')[-1]: t['spring_pattern'] for t in tasks}
    assert patterns['call'] == 'SUBFLOW_INVOCATION' and patterns['reset'] == 'MESSAGE_REPARSE'
    assert patterns['work'] == 'SPRING_SERVICE_MAPPER'


def test_a_genuinely_unmappable_node_still_stops_planning(repo, job, dispatch, approve, framework_repo, tmp_path):
    ace = _service(tmp_path)
    (ace / 'Odd.msgflow').write_text('<flow><node id="odd" type="AcmeAdapterNode"/></flow>')
    result = _plan_for(repo, job, dispatch, approve, framework_repo, ace)
    assert result.status == 'CLARIFICATION_REQUIRED' and result.outputs['plan'] == ''
    assert result.metrics['unsupported_count'] == 1
