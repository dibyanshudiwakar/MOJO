import json
import pytest
from app.agents.test_engineering import TestEngineeringAgent as EngineeringAgent


class StubRuntime:
    """A runtime adapter double, so replay is exercised without a network call."""

    def __init__(self, name, body):
        self.name = name
        self.body = body
        self.invocations = []

    def invoke(self, case):
        self.invocations.append(case)
        return {'adapter': self.name, 'status': 200, 'body': dict(self.body),
                'headers': {}, 'latency_ms': 1.0}


def test_cases_are_derived_from_the_analysed_interfaces(repo, job, planned):
    result = EngineeringAgent(repo).run(job['id'], params={'plan_id': planned['plan_id']})
    package = repo.one('select * from differential_packages where id=?',
                       (result.outputs['test_suites'],))
    cases = json.loads(package['payload'])['cases']
    assert [x['id'] for x in cases] == ['CASE-001', 'CASE-002']
    assert package['status'] == 'READY_FOR_EXECUTION'


def test_without_adapters_no_execution_is_claimed(repo, job, planned):
    """Reporting an unexecuted package as tested is what the VARIANCE_REVIEW
    precondition exists to catch, so the agent must not imply a replay happened."""
    result = EngineeringAgent(repo).run(job['id'], params={'plan_id': planned['plan_id']})
    assert result.metrics['replay'] == 'NOT_EXECUTED'
    assert result.outputs['execution_evidence'] == []
    assert repo.one("select count(*) n from validation_reports where job_id=? and"
                    " report_type='RUNTIME_EXECUTION'", (job['id'],))['n'] == 0


def test_with_adapters_both_runtimes_are_replayed_and_recorded(repo, job, planned):
    ace = StubRuntime('ace', {'id': 1})
    spring = StubRuntime('spring-boot', {'id': 1})
    result = EngineeringAgent(repo).run(job['id'], params={
        'plan_id': planned['plan_id'], 'ace_adapter': ace, 'spring_adapter': spring})
    assert result.metrics['replay'] == 'EXECUTED'
    assert len(ace.invocations) == len(spring.invocations) == result.metrics['case_count']
    report = repo.one("select * from validation_reports where job_id=? and"
                      " report_type='RUNTIME_EXECUTION'", (job['id'],))
    executions = json.loads(report['payload'])['executions']
    assert {x['case_id'] for x in executions} == set(result.outputs['execution_evidence'])
    assert executions[0]['ace_result']['adapter'] == 'ace'
    # The raw payloads must be recoverable, because an evidence row keeps only a
    # hash and the variance agent has to read the results themselves.
    assert executions[0]['spring_result']['body'] == {'id': 1}
    ref, = result.evidence
    assert ref.metadata['runtimes'] == ['ACE', 'SPRING_BOOT']


def test_supplied_cases_take_precedence_over_derivation(repo, job, planned):
    result = EngineeringAgent(repo).run(job['id'], params={
        'plan_id': planned['plan_id'], 'cases': [{'id': 'GOLDEN-1', 'input': {'x': 1}}]})
    cases = json.loads(repo.one('select payload from differential_packages where id=?',
                                (result.outputs['test_suites'],))['payload'])['cases']
    assert [x['id'] for x in cases] == ['GOLDEN-1']


def test_the_model_is_told_which_interfaces_there_are_to_design_against(repo, job, planned,
                                                                        model):
    """The design call carries the analysed interfaces, or it is a guess.

    This test previously deleted the analysis and expected
    ``analysis_required_to_derive_test_cases`` from the one-case-per-interface
    derivation. That derivation is gone -- it produced exactly one nominal case
    per interface, with no boundary values and no error paths, so it could not
    distinguish the two systems behaving differently, which is the only thing a
    differential case is for. What is left to assert is that the model is given
    the interfaces to design from.
    """
    EngineeringAgent(repo).run(job['id'], params={'plan_id': planned['plan_id']})
    payload, = [c['payload'] for c in model.calls
                if c['prompt'] == 'agent/test-engineering/case-design']
    assert payload['plan_id'] == planned['plan_id']
    assert payload['interfaces'], 'the analysed interfaces are what a case exercises'
    assert {x['protocol'] for x in payload['interfaces']} == {'HTTP'}
