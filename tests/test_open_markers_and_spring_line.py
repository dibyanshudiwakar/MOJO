"""Generated code carries no open-decision markers, and springdoc stays on the Spring line the framework imports."""
import re
from pathlib import Path

from app.agents import code_generation as CG
from app.services import pom_properties as P


def test_a_file_with_a_todo_is_refused_with_a_correction():
    assert CG.no_open_markers('A.java', {'content': 'class A { // TODO decide }'}) is not None


def test_a_file_with_fixme_or_xxx_is_refused_too():
    assert CG.no_open_markers('A.java', {'content': '// FIXME later'}) is not None
    assert CG.no_open_markers('A.java', {'content': '// XXX'}) is not None


def test_a_word_that_only_contains_the_marker_is_not_refused():
    assert CG.no_open_markers('A.java', {'content': 'class TodoList { int todos; }'}) is None


def test_a_clean_file_is_accepted():
    assert CG.no_open_markers('A.java', {'content': 'class A { void run() { } }'}) is None


def test_springdoc_follows_the_framework_spring_line_and_drops_the_pom_todo(tmp_path):
    m2 = tmp_path / 'm2'
    starter = m2 / 'com' / 'cvshealth' / 'eip' / 'microservices' / 'frameworks' / 'framework-starter' / '3.2.2'
    starter.mkdir(parents=True)
    (starter / 'framework-starter-3.2.2.pom').write_text(
        '<project><properties><org-springframework-boot.version>4.1.1</org-springframework-boot.version>'
        '</properties></project>')
    for v in ('2.8.9', '3.0.3'):
        (m2 / 'org' / 'springdoc' / 'springdoc-openapi-starter-webmvc-ui' / v).mkdir(parents=True)
    parent = ('<project><properties><framework.version>3.2.2</framework.version>'
              '<!-- TODO(decision): pin springdoc -->'
              '<springdoc.version>2.8.4</springdoc.version></properties></project>')
    files, actions = P.pin_spring_line([('parent/pom.xml', parent)], str(m2))
    text = files[0][1]
    assert '<springdoc.version>3.0.3</springdoc.version>' in text
    assert 'TODO' not in text
    assert any('springdoc.version 2.8.4' in a for a in actions)


# -- Spring versions come from the framework parent only -------------------------------------------------------------
from app.services import spring_versions as S

WEB = ('<project><properties><spring-boot.version>3.2.0</spring-boot.version><keep.me>1</keep.me></properties>'
       '<dependencies>'
       '<dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId>'
       '<version>3.2.0</version></dependency>'
       '<dependency><groupId>org.springdoc</groupId><artifactId>springdoc-openapi-starter-webmvc-ui</artifactId>'
       '<version>${springdoc.version}</version></dependency>'
       '</dependencies>'
       '<build><plugins><plugin><groupId>org.springframework.boot</groupId>'
       '<artifactId>spring-boot-maven-plugin</artifactId><version>3.2.0</version></plugin></plugins></build></project>')


def test_a_spring_version_a_pom_sets_itself_is_removed():
    files, actions = S.strip_spring_versions([('web/pom.xml', WEB)])
    text = files[0][1]
    assert '3.2.0' not in text
    assert '<artifactId>spring-boot-starter-web</artifactId>' in text and 'spring-boot-maven-plugin' in text
    assert '<keep.me>1</keep.me>' in text
    assert len(actions) == 3


def test_a_version_on_a_non_spring_artifact_is_kept():
    files, _actions = S.strip_spring_versions([('web/pom.xml', WEB)])
    assert '<version>${springdoc.version}</version>' in files[0][1]


def test_a_pom_with_no_spring_version_is_unchanged():
    clean = '<project><dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>x</artifactId></dependency></dependencies></project>'
    files, actions = S.strip_spring_versions([('web/pom.xml', clean)])
    assert files[0][1] == clean and actions == []


def test_the_boot_line_is_read_from_the_supplied_framework_clone_first(tmp_path):
    clone = tmp_path / 'fw'
    (clone / 'framework-starter').mkdir(parents=True)
    (clone / 'framework-starter' / 'pom.xml').write_text(
        '<project><artifactId>framework-starter</artifactId><properties>'
        '<org-springframework-boot.version>3.5.0</org-springframework-boot.version></properties></project>')
    m2 = tmp_path / 'm2'
    for v in ('2.8.9', '3.0.3'):
        (m2 / 'org' / 'springdoc' / 'springdoc-openapi-starter-webmvc-ui' / v).mkdir(parents=True)
    parent = '<project><properties><framework.version>3.2.2</framework.version><springdoc.version>3.0.3</springdoc.version></properties></project>'
    files, actions = P.pin_spring_line([('parent/pom.xml', parent)], str(m2), framework_root=str(clone))
    assert '<springdoc.version>2.8.9</springdoc.version>' in files[0][1], 'Boot 3.5 in the clone means springdoc 2.x'
