"""Calls and overrides are checked against the real types, and a revision is kept only if it helps."""
from app.services import contract_reconcile as C

PKG = 'package com.x.service;\n'
MAP = {
    'Api': {'kind': 'interface', 'supers': [], 'methods': {'getThing': [('Response', ['String'])]}},
    'Connector': {'kind': 'class', 'supers': [],
                  'methods': {'send': [('String', ['String', 'String']), ('void', ['Object'])]}},
}


def test_an_override_of_a_method_the_interface_lacks_is_flagged():
    text = PKG + ('public class C implements Api {\n  @Override\n  public Response getthing(String a) { return null; }\n}\n')
    assert any('getthing' in p for p in C.problems(text, MAP))


def test_an_override_with_an_annotation_between_is_still_checked():
    text = PKG + ('public class C implements Api {\n  @Override\n  @Foo\n  public Response getthing(String a) { return null; }\n}\n')
    assert any('getthing' in p for p in C.problems(text, MAP))
    bad = PKG + ('public class C implements Api {\n  @Override\n  @Foo\n  public Response getThings(String a) { return null; }\n}\n')
    assert any('getThings' in p for p in C.problems(bad, MAP))


def test_a_correct_override_is_not_flagged():
    text = PKG + ('public class C implements Api {\n  @Override\n  public Response getThing(String a) { return null; }\n}\n')
    assert C.problems(text, MAP) == []


def test_a_call_to_a_method_the_field_type_lacks_is_flagged():
    text = PKG + ('public class S {\n  private final Connector connector;\n'
                  '  void run(String x) { connector.fetch(x); }\n}\n')
    assert any('connector.fetch' in p for p in C.problems(text, MAP))


def test_a_call_whose_argument_is_the_wrong_type_is_flagged():
    text = PKG + ('public class S {\n  private final Connector connector;\n'
                  '  void run(byte[] payload) { connector.send(payload, "x"); }\n}\n')
    assert any('connector.send' in p for p in C.problems(text, MAP))


def test_a_call_with_matching_arguments_is_not_flagged():
    text = PKG + ('public class S {\n  private final Connector connector;\n'
                  '  void run(String payload) { connector.send(payload, "x"); connector.send(payload); }\n}\n')
    assert C.problems(text, MAP) == []


def test_a_revision_that_fixes_the_file_is_kept(monkeypatch):
    bad = PKG + ('public class S {\n  private final Connector connector;\n'
                 '  void run(byte[] payload) { connector.send(payload, "x"); }\n}\n')
    good = bad.replace('byte[] payload', 'String payload')
    monkeypatch.setattr(C, 'contracts', lambda files, m2: MAP)

    def produce(context, prompt, items, skill=None, effort=None):
        return {path: {'content': good} for path, _payload in items}, {}

    files, actions = C.reconcile(None, [('S.java', bad)], '/m2', produce, 'p', None, None, {})
    assert files == [('S.java', good)] and any('revised S.java' in a for a in actions)


def test_a_revision_that_does_not_help_is_discarded(monkeypatch):
    bad = PKG + ('public class S {\n  private final Connector connector;\n'
                 '  void run(byte[] payload) { connector.send(payload, "x"); }\n}\n')
    monkeypatch.setattr(C, 'contracts', lambda files, m2: MAP)

    def produce(context, prompt, items, skill=None, effort=None):
        return {path: {'content': bad + '\n// no change to the call\n'} for path, _ in items}, {}

    files, actions = C.reconcile(None, [('S.java', bad)], '/m2', produce, 'p', None, None, {})
    assert files == [('S.java', bad)]
    assert any('still disagrees' in a for a in actions)


def test_no_contracts_means_nothing_to_do(monkeypatch):
    monkeypatch.setattr(C, 'contracts', lambda files, m2: {})
    text = PKG + 'public class S { void run() { x.y(); } }\n'
    files, actions = C.reconcile(None, [('S.java', text)], '/m2', lambda *a, **k: 1 / 0, 'p', None, None, {})
    assert files == [('S.java', text)] and actions == []


def test_a_var_local_takes_its_type_from_the_method_that_returns_it():
    maps = {
        'Parser': {'kind': 'class', 'supers': [], 'methods': {'parse': [('Reply', ['String'])]}},
        'Reply': {'kind': 'record', 'supers': [], 'methods': {'status': [('int', [])]}},
    }
    good = PKG + ('public class S {\n  private final Parser parser;\n  void run(String x) {\n'
                  '    var reply = parser.parse(x);\n    reply.status();\n  }\n}\n')
    bad = good.replace('reply.status();', 'reply.getStatus();')
    assert C.problems(good, maps) == []
    assert any('reply.getStatus' in p for p in C.problems(bad, maps))


def test_a_record_component_is_an_accessor_and_nothing_more():
    text = PKG + ('public class S {\n  record Message(String xmlBody, int length) {}\n'
                  '  void run() {\n    var m = new Message("a", 1);\n    m.xmlBody();\n    m.getPayload();\n  }\n}\n')
    found = C.problems(text, {})
    assert found == [], 'a record of the project is read from its declaration, not a contract map'
    maps = {'Message': C._types_of(text)['Message']}
    found = C.problems(text, maps)
    assert any('m.getPayload' in p for p in found) and not any('xmlBody' in p for p in found)


def test_a_framework_constant_that_does_not_exist_is_flagged():
    maps = {'Provider': {'kind': 'enum', 'supers': [], 'methods': {}, 'fields': {'IBMMQ'}, 'framework': True}}
    good = PKG + 'public class S { void run() { x(Provider.IBMMQ); } void x(Object o) {} }\n'
    bad = PKG + 'public class S { void run() { x(Provider.MQ); } void x(Object o) {} }\n'
    assert C.problems(good, maps) == []
    assert any('Provider.MQ' in p for p in C.problems(bad, maps))


def test_prose_in_a_comment_is_not_read_as_a_declaration():
    text = PKG + ('/** This is a class is defined elsewhere. */\n'
                  'public class Parser {\n  public String parse(String x) { return x; }\n}\n')
    assert 'Parser' in C._types_of(C._code(text))
    assert 'is' not in C._types_of(C._code(text))


def test_a_static_call_on_a_framework_class_is_checked():
    maps = {'Util': {'kind': 'class', 'supers': [], 'framework': True,
                     'methods': {'fail': [('void', ['int', 'String'])]}, 'fields': set()}}
    good = PKG + 'public class S { void run() { Util.fail(500, "x"); } }\n'
    bad = PKG + 'public class S { void run() { Util.fail("500"); } }\n'
    assert C.problems(good, maps) == []
    assert any('Util.fail' in p for p in C.problems(bad, maps))


# -- a stub that never calls the field it was wired with --------------------------------------------------
#
# problems() only flags a claim the real types refute; a controller that simply never calls its
# injected service makes no such claim, so it was never flagged, however wrong the outcome.

def test_a_stub_that_never_calls_its_wired_field_is_flagged():
    text = PKG + ('public class Controller {\n  private final Connector connector;\n'
                  '  String run(String a) {\n    throw new UnsupportedOperationException("not done");\n  }\n}\n')
    found = C._stub_problems(text, MAP)
    assert any('run' in p and 'connector' in p for p in found)


def test_a_stub_with_no_wired_field_is_not_flagged():
    text = PKG + 'public class Controller {\n  String run(String a) {\n    throw new UnsupportedOperationException("not done");\n  }\n}\n'
    assert C._stub_problems(text, MAP) == []


def test_a_stub_is_not_flagged_when_the_field_is_used_elsewhere_in_the_file():
    text = PKG + ('public class Controller {\n  private final Connector connector;\n'
                  '  void other() { connector.send("a", "b"); }\n'
                  '  String run(String a) {\n    throw new UnsupportedOperationException("not done");\n  }\n}\n')
    assert C._stub_problems(text, MAP) == []


def test_a_non_stub_body_is_not_flagged_even_with_an_unused_field():
    text = PKG + ('public class Controller {\n  private final Connector connector;\n'
                  '  String run(String a) {\n    return "ok";\n  }\n}\n')
    assert C._stub_problems(text, MAP) == []


def test_a_stub_that_gains_a_real_call_is_kept(monkeypatch):
    bad = PKG + ('public class Controller {\n  private final Connector connector;\n'
                 '  String run(String a) {\n    throw new UnsupportedOperationException("not done");\n  }\n}\n')
    good = bad.replace('throw new UnsupportedOperationException("not done");', 'return connector.send(a, "x");')
    monkeypatch.setattr(C, 'contracts', lambda files, m2: MAP)

    def produce(context, prompt, items, skill=None, effort=None):
        return {path: {'content': good} for path, _payload in items}, {}

    files, actions = C.reconcile(None, [('Controller.java', bad)], '/m2', produce, 'p', None, None, {})
    assert files == [('Controller.java', good)]
    assert any('revised Controller.java' in a for a in actions)


def test_a_stub_that_stays_a_stub_is_discarded(monkeypatch):
    bad = PKG + ('public class Controller {\n  private final Connector connector;\n'
                 '  String run(String a) {\n    throw new UnsupportedOperationException("not done");\n  }\n}\n')
    monkeypatch.setattr(C, 'contracts', lambda files, m2: MAP)

    def produce(context, prompt, items, skill=None, effort=None):
        return {path: {'content': bad + '\n// no change\n'} for path, _ in items}, {}

    files, actions = C.reconcile(None, [('Controller.java', bad)], '/m2', produce, 'p', None, None, {})
    assert files == [('Controller.java', bad)]
    assert any('still disagrees' in a for a in actions)


# -- the same constant value, declared independently in more than one file --------------------------------
#
# Each declaration is self-consistent -- the file compiles and does what it says -- so problems()
# has nothing to refute. The duplication is only visible across files.

def test_two_files_declaring_the_same_constant_value_are_flagged():
    a = PKG + 'public class A {\n  private static final String MODE = "P";\n}\n'
    b = PKG + 'public class B {\n  private static final String TEST_MODE = "P";\n}\n'
    dup = C.duplicate_constants([('A.java', a), ('B.java', b)])
    assert list(dup) == ['B.java'], 'A sorts first and neither looks like a shared-constants class'
    assert 'A.MODE' in dup['B.java'][0]


def test_a_file_that_looks_like_a_shared_constants_class_is_preferred_as_canonical():
    shared = PKG + 'public class Constants {\n  public static final String MODE = "P";\n}\n'
    other = PKG + 'public class Service {\n  private static final String TEST_MODE = "P";\n}\n'
    dup = C.duplicate_constants([('constant/Constants.java', shared), ('Service.java', other)])
    assert list(dup) == ['Service.java']
    assert 'Constants.MODE' in dup['Service.java'][0]


def test_distinct_constant_values_are_not_flagged():
    a = PKG + 'public class A {\n  private static final String MODE = "P";\n}\n'
    b = PKG + 'public class B {\n  private static final String OTHER = "Q";\n}\n'
    assert C.duplicate_constants([('A.java', a), ('B.java', b)]) == {}


def test_a_value_declared_once_is_not_flagged():
    a = PKG + 'public class A {\n  private static final String MODE = "P";\n}\n'
    assert C.duplicate_constants([('A.java', a)]) == {}


def test_a_duplicate_constant_removed_in_favor_of_the_shared_one_is_kept(monkeypatch):
    shared = PKG + 'public class Constants {\n  public static final String MODE = "P";\n}\n'
    bad = PKG + 'public class Service {\n  private static final String TEST_MODE = "P";\n}\n'
    good = PKG + 'public class Service {\n  // now references Constants.MODE instead\n}\n'
    monkeypatch.setattr(C, 'contracts', lambda files, m2: {})

    def produce(context, prompt, items, skill=None, effort=None):
        return {path: {'content': good} for path, _payload in items}, {}

    files, actions = C.reconcile(
        None, [('constant/Constants.java', shared), ('Service.java', bad)], '/m2', produce, 'p', None, None, {})
    by_path = dict(files)
    assert by_path['Service.java'] == good
    assert by_path['constant/Constants.java'] == shared, 'the canonical declaration is left alone'
    assert any('revised Service.java' in a for a in actions)
