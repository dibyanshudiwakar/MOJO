"""A private constant read from another file is made public in its declaring file, so the reader compiles."""
from app.services import contract_reconcile as C

CONSTANTS = ('package com.esl.pharmacy.constants;\n'
             'public final class PharmacyServiceConstants {\n'
             '    private static final int NPI_LENGTH = 10;\n'
             '    public static final String OK = "ok";\n'
             '}\n')
UTIL = ('package com.esl.pharmacy.util;\n'
        'import com.esl.pharmacy.constants.PharmacyServiceConstants;\n'
        'public class PharmacyServiceUtil {\n'
        '    boolean valid(String s) { return s.length() == PharmacyServiceConstants.NPI_LENGTH; }\n'
        '}\n')


def test_a_private_constant_read_from_another_file_is_made_public_in_its_owner():
    files, actions = C.widen_private_constants([('src/Constants.java', CONSTANTS), ('src/Util.java', UTIL)])
    by_path = dict(files)
    assert 'public static final int NPI_LENGTH = 10;' in by_path['src/Constants.java']
    assert 'private static final int NPI_LENGTH' not in by_path['src/Constants.java']
    assert by_path['src/Util.java'] == UTIL
    assert any('NPI_LENGTH is private in src/Constants.java' in a for a in actions)


def test_a_private_constant_used_only_inside_its_own_file_is_left_alone():
    own = CONSTANTS.replace('}\n', '    static int twice() { return NPI_LENGTH * 2; }\n}\n')
    files, actions = C.widen_private_constants([('src/Constants.java', own), ('src/Util.java', UTIL.replace(
        'PharmacyServiceConstants.NPI_LENGTH', 'PharmacyServiceConstants.OK'))])
    assert actions == []
    assert 'private static final int NPI_LENGTH' in dict(files)['src/Constants.java']


def test_a_reader_that_declares_its_own_constant_of_that_name_is_not_a_reader_of_the_private_one():
    other = ('package com.esl.pharmacy.util;\n'
             'public class Other {\n    private static final int NPI_LENGTH = 9;\n'
             '    int f() { return NPI_LENGTH; }\n}\n')
    _files, actions = C.widen_private_constants([('src/Constants.java', CONSTANTS), ('src/Other.java', other)])
    assert actions == []


def test_a_wildcard_import_loads_the_framework_class_the_source_names(monkeypatch):
    """``import pkg.*;`` must be read too: a call to a method the framework class lacks is then caught."""
    seen = {}

    def fake(poms, m2_root, wanted, packages, used):
        seen.update(wanted=wanted, packages=packages, used=used)
        return {}

    monkeypatch.setattr(C, '_framework_contracts', fake)
    src = ('package com.x;\nimport com.cvshealth.eip.microservices.frameworks.connector.rxclaimsbo.model.*;\n'
           'class S { Object f() { return RxClaimRequest.builder(); } }\n')
    C.contracts([('src/S.java', src), ('pom.xml', '<project/>')], '/m2')
    assert seen['packages'] == {'com.cvshealth.eip.microservices.frameworks.connector.rxclaimsbo.model'}
    assert 'RxClaimRequest' in seen['used']
