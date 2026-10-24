"""Direct unit coverage for the method-signature extraction ``_verified_contracts`` relies on.

The naive version of this (a single ``\\(([^()]*)\\)`` capture for the
parameter list) broke on the very first real interface it was built for:
every parameter annotated ``@Parameter(description = "...")`` has its own
nested parens, which a non-nested-paren character class can never span. These
tests pin the paren-balanced extraction directly, separately from the
higher-level ``_verified_contracts`` integration tests in
``test_feedback_model_repair.py``.
"""
from app.services.feedback import _local_contracts, _methods_in


def test_a_class_method_with_a_simple_signature():
    text = 'package app;\npublic class Svc {\n    public String doThing(String a, int b) { return null; }\n}\n'
    assert _methods_in(text) == [('String', 'doThing', ['String', 'int'])]


def test_an_interface_method_has_no_modifier_at_all():
    """Interface methods are implicitly public -- a regex requiring a modifier misses them entirely."""
    text = 'package app;\npublic interface Api {\n    ResponseEntity<Object> getThing(String id);\n}\n'
    assert _methods_in(text) == [('ResponseEntity<Object>', 'getThing', ['String'])]


def test_annotated_parameters_with_their_own_nested_parens():
    """The real bug: @Parameter(...) on every argument breaks a non-nested-paren capture."""
    text = (
        'package app;\n'
        'public interface Api {\n'
        '    ResponseEntity<Object> getThing(\n'
        '        @Parameter(description = "the id", example = "6003~123") String id,\n'
        '        @RequestParam(value = "groupID", required = false) String groupId);\n'
        '}\n')
    assert _methods_in(text) == [
        ('ResponseEntity<Object>', 'getThing', ['String', 'String'])]


def test_a_quoted_paren_inside_an_annotation_value_does_not_unbalance_the_scan():
    text = (
        'package app;\n'
        'public interface Api {\n'
        '    ResponseEntity<Object> getThing(\n'
        '        @Parameter(example = "idSource~idValue (eg: 6003~123)") String id);\n'
        '}\n')
    assert _methods_in(text) == [
        ('ResponseEntity<Object>', 'getThing', ['String'])]


def test_multiple_overloads_are_all_found():
    text = (
        'package app;\n'
        'public class Svc {\n'
        '    public String build(String a) { return null; }\n'
        '    public String build(String a, String b) { return null; }\n'
        '}\n')
    assert _methods_in(text) == [
        ('String', 'build', ['String']),
        ('String', 'build', ['String', 'String'])]


def test_a_void_method_with_no_parameters():
    text = 'package app;\npublic class Svc {\n    public void run() { }\n}\n'
    assert _methods_in(text) == [('void', 'run', [])]


def test_a_field_declaration_is_not_mistaken_for_a_method():
    text = 'package app;\npublic class Svc {\n    private final String name;\n}\n'
    assert _methods_in(text) == []


def test_a_generic_return_type_with_a_type_parameter():
    text = 'package app;\npublic class Svc {\n    public Map<String, String> headers() { return null; }\n}\n'
    assert _methods_in(text) == [('Map<String, String>', 'headers', [])]


# ---- _local_contracts: class/interface header + extends/implements ---------------------------

def test_local_contracts_records_implements_and_methods(tmp_path):
    (tmp_path / 'Api.java').write_text(
        'package app;\npublic interface Api {\n    String getX(String id);\n}\n')
    (tmp_path / 'Ctrl.java').write_text(
        'package app;\npublic class Ctrl implements Api {\n    public String getX(String id) { return id; }\n}\n')

    contracts = _local_contracts(tmp_path)

    assert contracts['Api']['kind'] == 'interface'
    assert contracts['Api']['implements_extends'] == []
    assert contracts['Api']['methods']['getX'] == ['String getX(String)']
    assert contracts['Ctrl']['implements_extends'] == ['Api']


def test_local_contracts_records_extends(tmp_path):
    (tmp_path / 'Base.java').write_text(
        'package app;\npublic class Base {\n    public void run() { }\n}\n')
    (tmp_path / 'Child.java').write_text(
        'package app;\npublic class Child extends Base {\n}\n')

    contracts = _local_contracts(tmp_path)

    assert contracts['Child']['implements_extends'] == ['Base']


def test_local_contracts_is_empty_for_a_project_with_no_java_files(tmp_path):
    assert _local_contracts(tmp_path) == {}
