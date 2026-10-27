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
