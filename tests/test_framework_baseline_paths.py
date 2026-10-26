"""Framework baseline reads a framework repository whose build output is deeper than Windows opens.

A cloned framework repository carries ``target/site/jacoco-aggregate/<module>/<package>/...`` reports whose
paths pass 300 characters. ``Path.rglob`` raised ``[WinError 3]`` on them and the step failed before it
reached the model. Build output says nothing about conventions, and source that is itself deep must still be read.
"""
from pathlib import Path

from app.services import feedback
from app.services.enterprise_v5 import FrameworkConventionExtractor

POM = ('<project><parent><artifactId>spring-boot-starter-parent</artifactId><version>3.2.0</version></parent>'
       '<properties><java.version>17</java.version></properties><dependencies><dependency>'
       '<artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>')


def _make(path, text='x'):
    p = feedback.lp(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def _deep(base, leaf):
    d = base
    for part in ['framework-restclient'] + ['com.cvshealth.eip.microservices.frameworks.restclient'] * 3:
        d = d / part
    return d / leaf


def _repo(tmp_path):
    root = tmp_path / ('EIPMS_cvs-technical-edb-cs-frameworks-' + 'x' * 20)
    root.mkdir()
    (root / 'pom.xml').write_text(POM)
    _make(root / 'src/main/java/com/x/Api.java', '@RestController class Api { }')
    _make(root / 'src/main/resources/application.properties', 'management.endpoints.web.exposure.include=*')
    # Build output, deeper than the Windows limit, with a Java-looking file in it.
    report = _deep(root / 'framework-adapter/target/site/jacoco-aggregate', 'Stale.java')
    assert len(str(report)) > 260, 'the fixture must pass MAX_PATH to prove anything'
    _make(report, 'class Stale { }')
    return root


def test_sources_skip_build_output_and_do_not_fail_on_a_deep_tree(tmp_path):
    names = [p.replace('\\', '/') for p, _ in FrameworkConventionExtractor().sources(_repo(tmp_path))]
    assert 'pom.xml' in names and 'src/main/java/com/x/Api.java' in names
    assert not any('target/' in n for n in names)


def test_extract_reads_the_repository_and_ignores_build_output(tmp_path):
    result = FrameworkConventionExtractor().extract(_repo(tmp_path))
    standards = result['standards']
    assert standards['build']['tool'] == 'MAVEN' and 'spring-boot-starter-web' in standards['dependencies']
    assert [Path(x['path']).as_posix() for x in standards['api']] == ['src/main/java/com/x/Api.java']
    assert not any('target' in p for p in standards['projectStructure'])


def test_source_that_is_itself_deeper_than_the_limit_is_still_read(tmp_path):
    root = tmp_path / ('EIPMS_cvs-technical-edb-cs-frameworks-' + 'x' * 20)
    root.mkdir()
    (root / 'pom.xml').write_text(POM)
    deep = _deep(root / 'framework-adapter/src/main/java', 'Deep.java')
    assert len(str(deep)) > 260
    _make(deep, '@RestController class Deep { }')
    assert any(p.endswith('Deep.java') for p, _ in FrameworkConventionExtractor().sources(root))
    assert any(x['path'].endswith('Deep.java') for x in FrameworkConventionExtractor().extract(root)['standards']['api'])


def test_find_prunes_only_the_named_directories(tmp_path):
    _make(tmp_path / 'a/target/A.java')
    _make(tmp_path / 'a/src/B.java')
    assert sorted(p.name for p in feedback.find(tmp_path, '*.java')) == ['A.java', 'B.java']
    assert [p.name for p in feedback.find(tmp_path, '*.java', prune=feedback.BUILD_DIRS)] == ['B.java']
