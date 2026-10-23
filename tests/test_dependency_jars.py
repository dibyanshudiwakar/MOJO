"""``dependency_jars``: what a generated project's own declared dependencies really contain.

Nothing here names a vendor or framework -- every test builds its own throwaway
jar and pom, the same way any migration's own generated project would, so the
module is proven generic rather than proven against one framework's names.
"""
import zipfile

import pytest

from app.services import dependency_jars as dj

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <properties>
    <widgets.version>9.9.9</widgets.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>com.example.sdk</groupId>
      <artifactId>widgets-core</artifactId>
      <version>${widgets.version}</version>
    </dependency>
    <dependency>
      <groupId>com.example.sdk</groupId>
      <artifactId>widgets-io</artifactId>
      <version>1.0.0</version>
    </dependency>
  </dependencies>
</project>
"""


def _jar(path, classes):
    """A real jar with exactly these class entries, built the same shape javac would read."""
    with zipfile.ZipFile(path, 'w') as zf:
        for fqn in classes:
            zf.writestr(fqn.replace('.', '/') + '.class', b'')


def test_dependencies_are_parsed_with_property_substitution():
    deps = dj.dependencies(POM)
    assert {'group_id': 'com.example.sdk', 'artifact_id': 'widgets-core', 'version': '9.9.9'} in deps
    assert {'group_id': 'com.example.sdk', 'artifact_id': 'widgets-io', 'version': '1.0.0'} in deps


def test_an_unresolvable_property_is_left_as_is():
    pom = '<project><dependencies><dependency><groupId>g</groupId><artifactId>a</artifactId>' \
          '<version>${not.defined}</version></dependency></dependencies></project>'
    assert dj.dependencies(pom)[0]['version'] == '${not.defined}'


def test_a_dependency_missing_a_coordinate_is_skipped():
    pom = '<project><dependencies><dependency><groupId>g</groupId></dependency></dependencies></project>'
    assert dj.dependencies(pom) == []


def test_jar_path_follows_the_standard_maven_local_repo_layout(tmp_path):
    path = dj.jar_path(tmp_path, 'com.example.sdk', 'widgets-core', '9.9.9')
    assert path == tmp_path / 'com' / 'example' / 'sdk' / 'widgets-core' / '9.9.9' / 'widgets-core-9.9.9.jar'


def test_classes_in_jar_indexes_simple_name_to_fqn(tmp_path):
    jar = tmp_path / 'widgets-core-9.9.9.jar'
    _jar(jar, ['com.example.sdk.core.Widget', 'com.example.sdk.core.WidgetFactory'])

    index = dj.classes_in_jar(jar)

    assert index['Widget'] == ['com.example.sdk.core.Widget']
    assert index['WidgetFactory'] == ['com.example.sdk.core.WidgetFactory']


def test_nested_classes_are_not_indexed_as_top_level(tmp_path):
    jar = tmp_path / 'widgets-core-9.9.9.jar'
    _jar(jar, ['com.example.sdk.core.Widget', 'com.example.sdk.core.Widget$Builder'])

    index = dj.classes_in_jar(jar)

    assert index['Widget'] == ['com.example.sdk.core.Widget']
    assert 'Builder' not in index


def test_a_missing_jar_is_empty_not_an_error(tmp_path):
    assert dj.classes_in_jar(tmp_path / 'nope.jar') == {}


def test_a_corrupt_jar_is_empty_not_an_error(tmp_path):
    bad = tmp_path / 'bad.jar'
    bad.write_text('not a zip file')
    assert dj.classes_in_jar(bad) == {}


def test_project_classes_merges_every_resolvable_dependency(tmp_path):
    m2 = tmp_path / 'm2'
    core = dj.jar_path(m2, 'com.example.sdk', 'widgets-core', '9.9.9')
    io_ = dj.jar_path(m2, 'com.example.sdk', 'widgets-io', '1.0.0')
    core.parent.mkdir(parents=True)
    io_.parent.mkdir(parents=True)
    _jar(core, ['com.example.sdk.core.Widget'])
    _jar(io_, ['com.example.sdk.io.WidgetReader'])

    index = dj.project_classes([POM], m2)

    assert index['Widget'] == ['com.example.sdk.core.Widget']
    assert index['WidgetReader'] == ['com.example.sdk.io.WidgetReader']


def test_project_classes_is_empty_when_nothing_resolves_locally(tmp_path):
    """The graceful fallback: nothing downloaded, nothing to offer."""
    assert dj.project_classes([POM], tmp_path / 'empty-m2') == {}


def test_a_dependency_with_no_version_is_returned_with_version_none():
    """Normal for a transitive dependency: its version comes from a BOM, not this block."""
    pom = ('<project><dependencies><dependency><groupId>g</groupId>'
          '<artifactId>a</artifactId></dependency></dependencies></project>')
    assert dj.dependencies(pom) == [{'group_id': 'g', 'artifact_id': 'a', 'version': None}]


def test_project_classes_follows_one_hop_of_transitive_dependencies(tmp_path):
    """``Widget`` isn't declared directly -- it's pulled in via widgets-core's own pom.

    Mirrors the real bug this module exists for: EieHeader/EIPMicroserviceException
    were never declared in the generated project's own pom, only in the pom of a
    dependency it did declare.
    """
    m2 = tmp_path / 'm2'
    core = dj.jar_path(m2, 'com.example.sdk', 'widgets-core', '9.9.9')
    core.parent.mkdir(parents=True)
    _jar(core, ['com.example.sdk.core.Marker'])
    core_pom = dj.pom_path(m2, 'com.example.sdk', 'widgets-core', '9.9.9')
    core_pom.write_text(
        '<project><dependencies><dependency><groupId>com.example.sdk</groupId>'
        '<artifactId>widgets-io</artifactId></dependency></dependencies></project>')
    io_ = dj.jar_path(m2, 'com.example.sdk', 'widgets-io', '9.9.9')
    io_.parent.mkdir(parents=True)
    _jar(io_, ['com.example.sdk.io.WidgetReader'])

    index = dj.project_classes([POM], m2)

    assert index['WidgetReader'] == ['com.example.sdk.io.WidgetReader']


def test_transitive_resolution_is_bounded_and_does_not_loop_forever(tmp_path):
    """A declares B declares A: the cycle must terminate, not hang or recurse forever."""
    m2 = tmp_path / 'm2'
    pom_a = '<project><dependencies><dependency><groupId>g</groupId><artifactId>b</artifactId>' \
           '<version>1.0</version></dependency></dependencies></project>'
    pom_b = '<project><dependencies><dependency><groupId>g</groupId><artifactId>a</artifactId>' \
           '<version>1.0</version></dependency></dependencies></project>'
    a_jar = dj.jar_path(m2, 'g', 'a', '1.0')
    b_jar = dj.jar_path(m2, 'g', 'b', '1.0')
    a_jar.parent.mkdir(parents=True)
    b_jar.parent.mkdir(parents=True)
    _jar(a_jar, ['g.A'])
    _jar(b_jar, ['g.B'])
    dj.pom_path(m2, 'g', 'a', '1.0').write_text(pom_a)
    dj.pom_path(m2, 'g', 'b', '1.0').write_text(pom_b)

    index = dj.project_classes([pom_a], m2)

    assert index == {'A': ['g.A'], 'B': ['g.B']}
