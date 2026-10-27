from app.services import pom_properties as P

PARENT = ('<project>\n    <packaging>pom</packaging>\n    <properties>\n'
          '        <java.version>21</java.version>\n    </properties>\n</project>')


def test_a_known_undefined_property_is_added_to_the_parent():
    files, notes = P.complete([('p/pom.xml', PARENT), ('w/pom.xml', '<a>${springdoc.version}</a>')])
    assert '<springdoc.version>' in files[0][1] and files[0][1].count('<properties>') == 1
    assert any('springdoc.version' in n and 'Confirm' in n for n in notes)


def test_properties_the_starter_or_maven_provide_are_left_alone():
    web = '<a>${framework.version}${project.version}${java.version}${maven-compiler-plugin.version}</a>'
    assert P.undefined([('p/pom.xml', PARENT), ('w/pom.xml', web)]) == {}


def test_an_unknown_property_is_reported_not_invented():
    files, notes = P.complete([('p/pom.xml', PARENT), ('w/pom.xml', '<a>${mystery.version}</a>')])
    assert files[0][1] == PARENT and 'mystery.version' in notes[0] and 'No default' in notes[0]


def test_a_property_in_a_comment_is_ignored():
    assert P.undefined([('w/pom.xml', '<!-- ${springdoc.version} --><a/>')]) == {}


def test_a_property_in_the_parent_block_is_replaced_by_its_value():
    parent = ('<project><parent><artifactId>framework-starter</artifactId><version>${framework.version}</version>'
              '</parent><properties><framework.version>3.2.2</framework.version></properties></project>')
    files, notes = P.complete([('p/pom.xml', parent)])
    assert '<version>3.2.2</version></parent>' in files[0][1]
    assert '${framework.version}' not in files[0][1].split('</parent>')[0]
    assert '<framework.version>3.2.2</framework.version>' in files[0][1], 'the property itself stays for the web module'
    assert any('Maven reads the parent' in n for n in notes)


def test_a_parent_property_with_no_value_is_reported():
    files, notes = P.complete([('p/pom.xml', '<project><parent><version>${nothing.here}</version></parent></project>')])
    assert '${nothing.here}' in files[0][1] and any('no value' in n for n in notes)
