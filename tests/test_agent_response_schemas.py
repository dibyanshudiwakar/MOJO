"""The response schemas are a safety boundary, so they are tested like one.

A model answer is untrusted input: ``app/agents/artefact.py`` persists it,
hashes it, binds it to a gate approval and hands it to the next agent. These
schemas are the only thing between a malformed or inventive answer and that
chain, which makes two failure modes worth guarding mechanically.

A prompt with no schema would be validated against nothing. A schema that
rejects its own prompt's documented shape would reject every real answer and
send the agent down the fallback path forever -- silently, because a fallback
is a recorded degradation rather than an error.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agents.artefact import SCHEMA_FILE, schemas, validate

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = json.loads((ROOT / 'config' / 'agent-prompts.v1.json').read_text(encoding='utf-8'))['prompts']

#: One sample per prompt, in the exact shape that prompt's own template tells
#: the model to return. Written from the templates, not from the schemas, so
#: this fails if the two ever drift apart.
SAMPLES = {
    'agent/dormancy-intelligence/classification-rationale': {
        'classification': 'DORMANT', 'decisive_evidence': 'no traffic in 120 days',
        'missing_evidence': []},
    'agent/ace-analysis/ambiguity-triage': {
        'ambiguities': [{'question': 'Which queue?', 'owner': 'ACE_SME',
                         'impact': 'routing unknown', 'evidenceReference': 'F.msgflow#n1'}]},
    'agent/ace-analysis/semantic-model': {
        'flows': [{'path': 'Orders.msgflow',
                   'nodes': [{'id': 'in', 'type': 'ComIbmWSInput.msgnode', 'properties': {}}],
                   'connections': [{'from': 'in', 'to': 'map'}]}],
        'interfaces': [{'source': 'Orders.msgflow', 'node': 'in', 'protocol': 'HTTP',
                        'endpoint': '/orders'}],
        'ambiguities': []},
    'agent/repository-intake/inventory': {
        'inventory': {'files': ['Orders.msgflow'], 'flows': ['Orders.msgflow'], 'esql': []},
        'gaps': ['no .esql for Orders.msgflow']},
    'agent/repository-intake/gap-triage': {
        'ranked_gaps': [{'gap': 'no esql', 'severity': 'BLOCKING', 'closes_with': 'source drop'}],
        'notes': 'n'},
    'agent/framework-baseline/standards': {
        'standards': {'build': {'tool': 'MAVEN', 'javaVersion': '21', 'springBootVersion': '3.3.0'},
                      'dependencies': ['spring-boot-starter-web']},
        'conflicts': ['security_standard_not_detected']},
    'agent/framework-baseline/convention-conflict-triage': {
        'conflicts': [{'conflict': 'two java versions', 'favoured': '21', 'rationale': 'r',
                       'undecidable': False}]},
    'agent/migration-specification/authoring': {
        'springModules': ['web'], 'approvedDependencies': ['spring-boot-starter-web'],
        'notes': []},
    'agent/planning/mapping-catalogue': {
        'mappings': [{'source_reference': 'Orders.msgflow#in',
                      'ace_construct': 'ComIbmWSInput.msgnode',
                      'spring_pattern': 'SPRING_MVC_CONTROLLER', 'target_module': 'web',
                      'boundary': 'GENERATED', 'tests': ['contract']}],
        'unsupported': [], 'risks': []},
    'agent/planning/implementation-plan': {
        'tasks': [{'id': 'IMP-001', 'sequence': 1, 'source_reference': 'Orders.msgflow#in',
                   'spring_pattern': 'SPRING_MVC_CONTROLLER', 'boundary': 'GENERATED',
                   'depends_on': [], 'tests': ['contract']}],
        'acceptance': ['compile passes']},
    'agent/planning/risk-assessment': {
        'risks': [{'category': 'UNSUPPORTED_MAPPING', 'severity': 'HIGH', 'statement': 's',
                   'response': 'r', 'owner': 'o'}]},
    'agent/code-generation/business-logic-synthesis': {
        'edits': [{'path': 'src/main/java/A.java', 'old_str': 'TODO', 'new_str': 'return x;',
                   'why': 'Orders.esql'}]},
    'agent/code-generation/project-layout': {
        'root': 'EIPMS_cvs-v1-validatebridgesupply-oci',
        'files': [
            {'path': 'cvs-v1-validatebridgesupply-oci-parent/pom.xml',
             'purpose': 'parent module POM pinning framework and java versions',
             'skill_reference': 'references/structural-conventions.md'},
            {'path': 'Dockerfile', 'purpose': 'container image at the repo root',
             'skill_reference': 'references/structural-conventions.md'}],
        'omitted': ['config/deploy-config/Secret.yaml: service needs no secret']},
    'agent/code-generation/file-synthesis': {
        'path': 'src/main/java/com/example/A.java', 'content': 'package com.example;\n',
        'language': 'java', 'provenance': {'source_reference': 'Orders.msgflow#in'},
        'action_items': []},
    'agent/code-generation/quality-failure-triage': {
        'failures': [{'check': 'SECRET_SCAN', 'likely_cause': 'c', 'suggested_fix': 'f',
                      'needs': ''}]},
    'agent/test-engineering/case-design': {
        'cases': [{'id': 'TC1', 'description': 'nominal', 'input': {'a': 1}, 'headers': {},
                   'covers': ['Orders.msgflow#in']}],
        'policy': {'numeric_tolerance': 0}},
    'agent/test-engineering/case-coverage-review': {
        'weak_coverage': [{'interface': 'i', 'gap': 'g', 'why_it_matters': 'w'}], 'adequate': []},
    'agent/variance-analysis/findings': {
        'findings': [{'path': '$.total', 'kind': 'VALUE', 'severity': 'HIGH', 'material': True,
                      'expected': 1, 'actual': 2, 'explanation': 'rounding differs'}]},
    'agent/variance-analysis/severity-rationale': {
        'assessments': [{'path': '$.a', 'behavioural': True, 'rationale': 'r'}]},
    'agent/variance-analysis/parity-check': {'action': 'parity_check', 'findings': []},
    'agent/variance-analysis/hardcoding-check': {'action': 'hardcoding_check', 'findings': []},
    'agent/rca/hypothesis': {
        'hypotheses': [{'hypothesis': 'h', 'supporting_evidence': ['e'], 'missing_evidence': [],
                        'affected_scope': '$.a'}]},
    'agent/remediation/patch-plan': {
        'pattern': 'p', 'changes': [{'path': 'A.java', 'intent': 'i'}], 'tests': ['t'],
        'bounded': True},
    'agent/governance/gate-assessment': {
        'recommendation': 'DENY', 'target_state': 'FRAMEWORK_BASELINED', 'rationale': 'r',
        'evidence_sufficient': False, 'missing_evidence': ['G0 approval'], 'risks': []},
    'agent/governance/blocking-explanation': {
        'explanations': [{'target': 'T', 'blocked_by': 'G0', 'steps': ['s']}]},
    'agent/knowledge-evidence/citations': {
        'citations': [{'evidence_id': 'ev-1', 'claim': 'c', 'locator': 'l'}],
        'uncited_claims': []},
    'agent/knowledge-evidence/citation-sufficiency': {
        'sufficient': True, 'missing_kinds': [], 'unverifiable': [], 'rationale': 'r'},
    'agent/migration-orchestrator/next-action': {
        'next_agent': 'planning', 'params': {}, 'next_state': 'PLAN_APPROVED', 'blocked_by': None},
    'agent/feedback/finding-triage': {
        'recommendations': [{'item': 'i', 'why_manual': 'w', 'owner': 'o'}]},
    'agent/feedback/code-repair': {
        'edits': [{'path': 'src/main/resources/application.yml', 'old_str': 'password: x',
                   'new_str': 'password: ${DB_PASSWORD}', 'why': 'SECRET_SCAN'}]},
}


def test_every_prompt_has_a_schema_and_every_schema_has_a_prompt():
    """An unmatched name on either side is a hole, not an inconvenience."""
    names = {p['name'] for p in PROMPTS}
    assert sorted(names - set(schemas())) == [], 'prompt with no schema: validated against nothing'
    assert sorted(set(schemas()) - names) == [], 'schema with no prompt'


def test_every_schema_is_itself_a_valid_json_schema():
    for name, schema in schemas().items():
        Draft202012Validator.check_schema(schema)


def test_every_prompt_shape_is_covered_by_a_sample():
    """So a new prompt cannot be added without pinning its response shape."""
    assert sorted({p['name'] for p in PROMPTS} - set(SAMPLES)) == []


@pytest.mark.parametrize('name', sorted(SAMPLES))
def test_the_schema_accepts_the_shape_its_own_prompt_asks_for(name):
    """The failure this catches is silent.

    A schema that rejects its prompt's documented shape sends the agent down
    the deterministic fallback on every single run -- and a fallback is a
    recorded degradation, not an error, so nothing fails and nobody notices
    that the model stopped being used.
    """
    assert validate(SAMPLES[name], name) == ''


def test_an_unregistered_prompt_name_does_not_validate_vacuously():
    """No schema must mean refused, never allowed."""
    assert validate({'anything': 1}, 'agent/not-a-real/prompt') == \
        'no_schema_registered:agent/not-a-real/prompt'


def test_a_non_object_response_is_refused():
    assert validate(['not', 'an', 'object'], 'agent/rca/hypothesis') == 'response_not_an_object'


def test_the_schema_file_is_the_one_the_module_loads():
    assert SCHEMA_FILE.is_file()
    assert json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))['schemas'].keys() == schemas().keys()
