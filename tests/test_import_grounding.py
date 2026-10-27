"""Generated imports are pointed at the one real class the dependencies contain, and nothing is invented."""
from app.services import import_grounding as G

BASE = 'com.cvshealth.eip.microservices'
INDEX = {
    'EieHeader': [BASE + '.frameworks.eieheader.EieHeader'],
    'MQUtility': [BASE + '.frameworks.ibmmq.MQUtility'],
    'Dup': [BASE + '.frameworks.a.Dup', BASE + '.frameworks.b.Dup'],
}


def _index(monkeypatch, index=INDEX):
    monkeypatch.setattr(G.dependency_jars, 'project_classes', lambda poms, m2: index)


def _java(pkg, imports, name='Thing'):
    body = ''.join('import %s;\n' % i for i in imports)
    return 'package %s;\n%s\npublic class %s {}\n' % (pkg, body, name)


POM = ('web/pom.xml', '<project><dependencies/></project>')


def test_a_wrong_framework_package_is_corrected_to_the_real_class(monkeypatch):
    _index(monkeypatch)
    text = _java(BASE + '.utility', [BASE + '.frameworks.utilities.EieHeader'], 'Util')
    out, actions = G.ground([POM, ('web/src/main/java/com/x/Util.java', text)], '/m2')
    assert 'import %s.frameworks.eieheader.EieHeader;' % BASE in out[1][1]
    assert any('corrected to' in a for a in actions)


def test_an_import_that_already_resolves_is_left_alone(monkeypatch):
    _index(monkeypatch)
    text = _java(BASE + '.x', [BASE + '.frameworks.eieheader.EieHeader'])
    out, actions = G.ground([POM, ('web/src/main/java/A.java', text)], '/m2')
    assert out[1][1] == text and actions == []


def test_jdk_and_third_party_imports_are_never_touched(monkeypatch):
    _index(monkeypatch)
    text = _java('a', ['java.util.List', 'org.springframework.stereotype.Service', 'lombok.Data'])
    out, actions = G.ground([POM, ('web/src/main/java/A.java', text)], '/m2')
    assert out[1][1] == text and actions == []


def test_an_ambiguous_name_is_reported_not_rewritten(monkeypatch):
    _index(monkeypatch)
    text = _java('a', [BASE + '.frameworks.wrong.Dup'])
    out, actions = G.ground([POM, ('web/src/main/java/A.java', text)], '/m2')
    assert out[1][1] == text and any('2 classes' in a for a in actions)


def test_a_project_class_in_a_declared_package_is_reported_missing(monkeypatch):
    _index(monkeypatch)
    model = _java(BASE + '.swagger.model.request', [], 'BeneficiaryKeyRequest')
    consumer = _java(BASE + '.api', [BASE + '.swagger.model.request.IDCardProfileRequest'], 'Api')
    files = [POM, ('web/src/main/java/swagger/model/request/BeneficiaryKeyRequest.java', model),
             ('web/src/main/java/api/Api.java', consumer)]
    assert G.missing(files, '/m2') == [('web/src/main/java/api/Api.java',
                                         BASE + '.swagger.model.request.IDCardProfileRequest')]


def test_a_class_in_a_package_the_project_never_declares_is_not_invented(monkeypatch):
    _index(monkeypatch)
    consumer = _java(BASE + '.api', [BASE + '.frameworks.nothere.Ghost'], 'Api')
    assert G.missing([POM, ('web/src/main/java/api/Api.java', consumer)], '/m2') == []
