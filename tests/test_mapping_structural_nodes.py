"""Ordinary ACE constructs must not stop planning.

A real run (the memberseobpreferences service) ended with ``plan_not_found`` in code generation. The cause was
upstream: the mapping catalogue could not place ten ordinary nodes -- a subflow's Input/Output terminals
(``eflow:FCMSource`` / ``eflow:FCMSink``), a call to another subflow (``GET.subflow``) and a Reset Content
Descriptor -- so planning produced no plan at all. These pin the rules that now place them, using the node types
that service actually contains.
"""
import pytest

from app.services import springgen
from app.services.migration_lifecycle_v6 import (DEFAULT_MAPPINGS, PRESENTATION_ONLY_PREFIXES, is_presentation_only,
                                                 mapping_key)

#: Every node type in the memberseobpreferences service's analysed model, with how many there were.
REAL_SERVICE_NODE_TYPES = {
    'ComIbmCompute.msgnode:FCMComposite_1': 6, 'eflow:FCMSource': 4, 'eflow:FCMSink': 4,
    'ComIbmWSInput.msgnode:FCMComposite_1': 1, 'ComIbmRouteToLabel.msgnode:FCMComposite_1': 1,
    'ComIbmWSReply.msgnode:FCMComposite_1': 1,
    'memberseobpreferencesInputCatchHandler.subflow:FCMComposite_1': 1,
    'memberseobpreferencesInputFailureHandler.subflow:FCMComposite_1': 1,
    'memberseobpreferencesInputTimeoutHandler.subflow:FCMComposite_1': 1,
    'ComIbmLabel.msgnode:FCMComposite_1': 1, 'GET.subflow:FCMComposite_1': 1,
    'ComIbmMQOutput.msgnode:FCMComposite_1': 1, 'ComIbmMQInput.msgnode:FCMComposite_1': 1,
    'ComIbmFilter.msgnode:FCMComposite_1': 1, 'ComIbmResetContentDescriptor.msgnode:FCMComposite_1': 1,
}


def test_every_node_type_of_the_real_service_is_placed_or_deliberately_skipped():
    unplaced = [t for t in REAL_SERVICE_NODE_TYPES
                if not is_presentation_only(t) and not mapping_key(t, DEFAULT_MAPPINGS)]
    assert unplaced == []


@pytest.mark.parametrize('terminal', ['eflow:FCMSource', 'eflow:FCMSink'])
def test_a_subflows_input_and_output_terminals_generate_nothing(terminal):
    assert is_presentation_only(terminal)


@pytest.mark.parametrize('real_node', ['ComIbmCompute.msgnode:FCMComposite_1', 'ComIbmWSInput.msgnode:FCMComposite_1',
                                       'ComIbmRouteToLabel.msgnode:FCMComposite_1', 'eflow:FCMComposite',
                                       'GET.subflow:FCMComposite_1'])
def test_nothing_with_logic_is_skipped(real_node):
    assert not is_presentation_only(real_node)


def test_the_palette_icon_and_label_are_still_skipped():
    assert is_presentation_only('utility:GIFFileGraphic') and is_presentation_only('ComIbmLabel.msgnode:FCMComposite_1')


def test_the_plan_and_the_generator_share_one_list_of_skipped_nodes():
    assert PRESENTATION_ONLY_PREFIXES is springgen.PRESENTATION_ONLY_PREFIXES


@pytest.mark.parametrize('node_type, key', [
    ('GET.subflow:FCMComposite_1', 'Subflow'),
    ('ComIbmResetContentDescriptor.msgnode:FCMComposite_1', 'ResetContentDescriptor'),
])
def test_the_new_constructs_resolve_to_an_approved_pattern(node_type, key):
    assert mapping_key(node_type, DEFAULT_MAPPINGS) == key
    entry = DEFAULT_MAPPINGS[key]
    assert entry['pattern'] and entry['module'] and entry['tests']
    assert entry['generated'] is False          # hand-written: nothing is generated for what a subflow call hands over to


def test_a_subflow_whose_name_contains_another_construct_is_still_a_subflow_call():
    """Not the first approved key whose name happens to be a substring of it."""
    assert mapping_key('OrderMappingSubflow.subflow:FCMComposite_1', DEFAULT_MAPPINGS) == 'Subflow'
    assert mapping_key('RouteFilterCompute.subflow:FCMComposite_1', DEFAULT_MAPPINGS) == 'Subflow'


def test_the_handler_subflows_are_still_exception_handlers_not_plain_subflow_calls():
    for kind in ('Catch', 'Failure', 'Timeout'):
        assert mapping_key('orderInput%sHandler.subflow:FCMComposite_1' % kind, DEFAULT_MAPPINGS) == 'Catch'


def test_a_construct_with_no_approved_pattern_is_still_unmapped():
    """The gate that stops a plan omitting something it cannot place is unchanged."""
    assert mapping_key('AcmeAdapterNode', DEFAULT_MAPPINGS) is None
    assert mapping_key('VendorCustomNode', DEFAULT_MAPPINGS) is None


def test_a_subflow_call_is_unmapped_if_the_approved_table_does_not_carry_it():
    approved = {k: v for k, v in DEFAULT_MAPPINGS.items() if k != 'Subflow'}
    assert mapping_key('GET.subflow:FCMComposite_1', approved) is None


def test_the_ordinary_routing_nodes_resolve_as_before():
    assert mapping_key('ComIbmRouteToLabel.msgnode:FCMComposite_1', DEFAULT_MAPPINGS) == 'Route'
    assert mapping_key('ComIbmFilter.msgnode:FCMComposite_1', DEFAULT_MAPPINGS) == 'Filter'
    assert mapping_key('ComIbmCompute.msgnode:FCMComposite_1', DEFAULT_MAPPINGS) == 'Compute'


# ---- the deterministic parser has to see these nodes in the first place --------------------------------

def _parse(tmp_path, files):
    from app.services.enterprise_v5 import ACEParser
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return ACEParser().parse(tmp_path)


def _types(model, path):
    return [n['type'] for kind in ('flows', 'subflows') for f in model[kind] if f['path'] == path for n in f['nodes']]


def test_the_parser_keeps_a_subflow_call_and_a_subflows_terminals(tmp_path):
    """None of the parser's other type tokens occur in these names, so they used to vanish from the model."""
    model = _parse(tmp_path, {
        'Main.msgflow': '<flow><node id="in" type="HTTPInput"/><node id="call" type="GET.subflow:FCMComposite_1"/></flow>',
        'GET.subflow': '<flow><node id="InTerminal.Input_1" type="eflow:FCMSource"/><node id="w" type="ComputeNode"/>'
                       '<node id="OutTerminal.Output_1" type="eflow:FCMSink"/></flow>'})
    assert _types(model, 'Main.msgflow') == ['HTTPInput', 'GET.subflow:FCMComposite_1']
    assert _types(model, 'GET.subflow') == ['eflow:FCMSource', 'ComputeNode', 'eflow:FCMSink']


def test_the_composite_that_wraps_a_flow_is_not_mistaken_for_a_node(tmp_path):
    real_format = (
        '<ecore:EPackage xmlns:xmi="http://www.omg.org/XMI" xmlns:ecore="http://www.eclipse.org/emf/2002/Ecore" '
        'xmlns:eflow="http://www.ibm.com/wbi/2005/eflow"><eClassifiers xmi:type="eflow:FCMComposite" name="FCMComposite_1">'
        '<composition><nodes xmi:type="eflow:FCMSource" xmi:id="InTerminal.Input_1"/>'
        '<nodes xmi:type="ComIbmCompute.msgnode:FCMComposite_1" xmi:id="FCMComposite_1_1"/>'
        '<nodes xmi:type="eflow:FCMSink" xmi:id="OutTerminal.Output_1"/></composition></eClassifiers></ecore:EPackage>')
    model = _parse(tmp_path, {'Real.subflow': real_format})
    assert _types(model, 'Real.subflow') == ['eflow:FCMSource', 'ComIbmCompute.msgnode:FCMComposite_1', 'eflow:FCMSink']


def test_everything_the_parser_now_keeps_is_placed_or_skipped(tmp_path):
    model = _parse(tmp_path, {
        'Main.msgflow': '<flow><node id="call" type="GET.subflow:FCMComposite_1"/></flow>',
        'GET.subflow': '<flow><node id="a" type="eflow:FCMSource"/><node id="b" type="eflow:FCMSink"/></flow>'})
    for path in ('Main.msgflow', 'GET.subflow'):
        for t in _types(model, path):
            assert is_presentation_only(t) or mapping_key(t, DEFAULT_MAPPINGS), t
