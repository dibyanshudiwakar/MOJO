"""Code-generation's business-logic synthesis step (Phase B).

Fills in a HAND_WRITTEN_PROTECTED stub Phase A (SpringProjectGenerator) wrote
from the real ACE source cited on it, applying the model's proposed edits
with the same validation CodeFeedbackService's model-assisted repair uses
(accept_edit) -- but directly, with no gate-driven revert, since there is no
failing check to measure against: the honest TODO stub is itself a safe
fallback if an edit goes nowhere.
"""
import hashlib

import pytest

from app.agents.base import AgentContext
from app.agents.code_generation import CodeGenerationAgent, _resolve_sources
from app.repository import Repository, now, uid


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(tmp_path))
    r = Repository(str(tmp_path / 'cg.db'))
    # An APPROVED prompt, gateway profile and model, so reason() actually
    # reaches the stub provider instead of returning the deterministic
    # fallback before ever calling invoke().
    tpl = 'synthesize'
    with r.connect() as c:
        r.insert(c, 'prompt_registry', {
            'id': uid(), 'name': 'agent/code-generation/business-logic-synthesis', 'version': '1',
            'template': tpl, 'content_hash': hashlib.sha256(tpl.encode()).hexdigest(),
            'status': 'APPROVED', 'approved_by': 't', 'effective_from': now(), 'created_at': now()})
        r.insert(c, 'connector_profiles', {
            'id': uid(), 'category': 'model-gateway', 'name': 'mp', 'provider': 'x',
            'config': '{}', 'secret_reference': '', 'priority': 1, 'enabled': 1,
            'status': 'VERIFIED', 'last_checked_at': '', 'created_at': now(), 'updated_at': now()})
        r.insert(c, 'model_registry', {
            'id': uid(), 'name': 'coding', 'version': 'v1', 'provider_profile': 'mp',
            'policy': '{}', 'region': '', 'status': 'APPROVED', 'approved_by': 't', 'created_at': now()})
    return r


class _StubModel:
    """Stands in for the Claude adapter, recording what it was sent."""
    model = 'claude-opus-5-5'

    def __init__(self, edits):
        self.edits = edits
        self.payload = None

    def invoke(self, template, payload, skill=None):
        self.payload = payload
        return {'edits': self.edits}


def _ctx(repo, job_id, provider):
    return AgentContext(repo, job_id, provider=provider,
                        effective_model_alias='coding', agent_id='code-generation')


def _stub_project(root):
    svc = root / 'service'
    svc.mkdir(parents=True)
    path = svc / 'OrdersService.java'
    path.write_text(
        'package com.example.orders.service;\n\npublic class OrdersService {\n'
        '  public Object getOrders(String id) {\n'
        '  /* TODO (business-logic-synthesis): translate the real logic from --\n'
        '   * Orders.esql\n'
        '   * This method intentionally returns a placeholder until that step runs. */\n'
        '  return null;\n  }\n}\n')
    return path


def test_resolve_sources_dedupes_by_file_and_reads_the_real_text():
    # Neither node id ('x'/'y'/'n1') resolves to a real <nodes xmi:id="...">
    # element in these placeholder sources, so both fall back to the whole
    # file -- in source_refs order, deduped so 'Orders.esql' isn't repeated.
    model = {'esql': [{'path': 'Orders.esql', 'source': 'CREATE COMPUTE MODULE M; END;'}],
            'flows': [{'path': 'Orders.msgflow', 'source': '<flow/>'}]}
    out = _resolve_sources(model, ['Orders.esql#x', 'Orders.esql#y', 'Orders.msgflow#n1'])
    assert out == [('Orders.esql', 'CREATE COMPUTE MODULE M; END;'), ('Orders.msgflow', '<flow/>')]


def test_resolve_sources_extracts_just_the_cited_node_not_the_whole_flow():
    flow_xml = ('<flow><nodes xmi:id="other" type="X"/>'
               '<nodes xmi:id="FCMComposite_1_4" type="ComIbmWSRequest.msgnode" URLSpecifier="https://real/backend"/>'
               '</flow>')
    model = {'flows': [{'path': 'A.msgflow', 'source': flow_xml}]}
    out = _resolve_sources(model, ['A.msgflow#FCMComposite_1_4'])
    assert out == [('A.msgflow#FCMComposite_1_4',
                    '<nodes xmi:id="FCMComposite_1_4" type="ComIbmWSRequest.msgnode" URLSpecifier="https://real/backend"/>')]


def test_synthesis_applies_a_valid_edit_and_leaves_a_real_implementation(repo, tmp_path):
    stub_path = _stub_project(tmp_path)
    stub_sources = {'service/OrdersService.java': ['Orders.esql']}
    model = {'esql': [{'path': 'Orders.esql',
                       'source': "CREATE COMPUTE MODULE M\n  SET OutputRoot.JSON.Data.id = id;\nEND MODULE;"}],
            'backend_type': {'type': 'REST_EXTERNAL_DIRECT'}}
    old = ('  /* TODO (business-logic-synthesis): translate the real logic from --\n'
           '   * Orders.esql\n'
           '   * This method intentionally returns a placeholder until that step runs. */\n'
           '  return null;\n')
    model_double = _StubModel([{'path': 'service/OrdersService.java', 'old_str': old,
                               'new_str': '  return java.util.Map.of("id", id);\n',
                               'why': 'Orders.esql SET OutputRoot.JSON.Data.id'}])

    agent = CodeGenerationAgent(repo)
    result = agent._synthesize_business_logic(_ctx(repo, 'job-1', model_double), tmp_path, stub_sources, model)

    assert result['outcome'] == 'applied'
    assert result['applied'] == [{'path': 'service/OrdersService.java',
                                   'why': 'Orders.esql SET OutputRoot.JSON.Data.id'}]
    text = stub_path.read_text()
    assert 'java.util.Map.of' in text and 'TODO (business-logic-synthesis)' not in text
    # The real ESQL reached the model (redacted of nothing, since it has no
    # credential-shaped text) -- confirms the source lookup actually ran.
    assert 'SET OutputRoot.JSON.Data.id' in str(model_double.payload)


def test_synthesis_refuses_an_edit_whose_old_str_does_not_match(repo, tmp_path):
    stub_path = _stub_project(tmp_path)
    stub_sources = {'service/OrdersService.java': ['Orders.esql']}
    model = {'esql': [{'path': 'Orders.esql', 'source': 'CREATE COMPUTE MODULE M; END;'}]}
    model_double = _StubModel([{'path': 'service/OrdersService.java',
                               'old_str': 'this text is not in the file',
                               'new_str': 'return 1;', 'why': 'bad'}])

    agent = CodeGenerationAgent(repo)
    result = agent._synthesize_business_logic(_ctx(repo, 'job-1', model_double), tmp_path, stub_sources, model)

    assert result['outcome'] == 'all_edits_refused'
    assert result['refused'] and result['refused'][0].startswith('old_str_absent:')
    assert 'TODO (business-logic-synthesis)' in stub_path.read_text()


def test_synthesis_is_a_noop_without_stubs_or_without_a_provider(repo, tmp_path):
    agent = CodeGenerationAgent(repo)
    assert agent._synthesize_business_logic(_ctx(repo, 'job-1', None), tmp_path, {}, {})['outcome'] == 'no_stubs'
    stub_sources = {'service/OrdersService.java': ['Orders.esql']}
    assert agent._synthesize_business_logic(
        _ctx(repo, 'job-1', None), tmp_path, stub_sources, {})['outcome'] == 'no_provider_configured'


def test_synthesis_cannot_escape_the_project_or_edit_a_pom(repo, tmp_path):
    _stub_project(tmp_path)
    (tmp_path / 'pom.xml').write_text('<project/>')
    stub_sources = {'service/OrdersService.java': ['Orders.esql']}
    model = {'esql': [{'path': 'Orders.esql', 'source': 'CREATE COMPUTE MODULE M; END;'}]}
    model_double = _StubModel([
        {'path': '../../etc/passwd', 'old_str': 'x', 'new_str': 'y', 'why': 'escape'},
        {'path': 'pom.xml', 'old_str': '<project/>', 'new_str': '<pwned/>', 'why': 'tamper'},
    ])

    agent = CodeGenerationAgent(repo)
    result = agent._synthesize_business_logic(_ctx(repo, 'job-1', model_double), tmp_path, stub_sources, model)

    assert result['outcome'] == 'all_edits_refused'
    assert any('path_not_relative' in r or 'path_outside_project' in r for r in result['refused'])
    assert any('suffix_not_editable' in r for r in result['refused'])
    assert (tmp_path / 'pom.xml').read_text() == '<project/>'
