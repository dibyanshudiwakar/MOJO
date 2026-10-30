"""The quality gate follows the code-generation skill's own checklist.

The gate used to ask for a pom.xml at the project root (failing every
multi-module layout the skill prescribes), and ran a bare ``https?://`` regex
that failed a project for a Javadoc mention of an XML namespace and for the
line that disables DOCTYPE declarations. Neither was the skill's. These tests
pin the replacement: each check cites its gap-checklist item, severities are the
skill's (BLOCKING fails, the rest are named GAPs), and a comment can never change
what a check concludes -- a generated pom says "NO framework-connector-qlcics"
in prose, and the gate once read that as a dependency.
"""
import shutil
from pathlib import Path

import pytest

from app.services import skill_gate
from app.services.skill_gate import BASELINE, Project, hardcoded_endpoints, run, strip_java_comments

ROOT_NAME = 'EIPMS_cvs-v1-orders-oci'
POM = '''<project>
  <!-- NO framework-connector-qlcics, NO camel-spring-boot-starter: comments are not dependencies. -->
  <dependencies>
    <dependency><artifactId>framework-restclient</artifactId></dependency>
    <dependency><artifactId>framework-mqclient</artifactId></dependency>
  </dependencies>
</project>
'''
DOCKERFILE = '''# a comment
FROM quay-registry.aetna.com/cvs-base-images/os-eng/redhat/ubi9/openjdk-micro:21
LABEL LINE_OF_BUSINESS='cvs-pss'
LABEL maintainer='IntegrationEngineeringTeam@CVSHealth.com'
LABEL Name='cvs-v1-orders-oci'
LABEL Version='0.0.1-SNAPSHOT'
USER root
COPY ./cvs-v1-orders-oci-web/target/cvs-v1-orders-oci-web-*.jar /os/app.jar
USER 1001
ENTRYPOINT ["java", "-jar", "app.jar"]
'''
CONTROLLER = '''package x;
@RestController
public class OrdersController implements OrdersApi {
    @Override @EIPMicroserviceOperation
    public ResponseEntity<R> get(Q q) { return service.get(q); }
}
'''
SERVICE = ('package x; public class OrdersService { '
           'boolean timeout(Exception e) { return isCircuitBreakerError(e); } }')
VALUES = 'APP_NAME: a\nPODS: 1\nCONTEXT_ROOT: /x\nENV: dev\nCLUSTER: pbmaz\n'


def _baseline_text():
    return BASELINE.read_text(encoding='utf-8').replace('{{LOGBACK_FILE_FOR_TIER}}', 'logback-dev.xml')


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


@pytest.fixture
def project(tmp_path):
    """A small project that satisfies every applicable check."""
    root = tmp_path / ROOT_NAME
    web = 'cvs-v1-orders-oci-web'
    _write(root, 'cvs-v1-orders-oci-parent/pom.xml', '<project/>')
    _write(root, web + '/pom.xml', POM)
    _write(root, web + '/src/main/java/x/controller/OrdersController.java', CONTROLLER)
    _write(root, web + '/src/main/java/x/OrdersService.java', SERVICE)
    _write(root, web + '/src/main/resources/application.properties',
           'spring.application.name=orders\nspring.profiles.active=local\n'
           'spring.mvc.throw-exception-if-no-handler-found=true\n')
    _write(root, web + '/src/main/resources/application-local.properties',
           _baseline_text() + 'a=1\nb=2\nc=3\nd=4\ne=5\nf=6\ng=7\n')
    _write(root, 'Dockerfile', DOCKERFILE)
    from app.services import maven_settings
    _write(root, 'settings.xml', maven_settings.template())
    _write(root, '.github/config/DeployMetadata.json', '{}')
    _write(root, '.github/workflows/build.yaml', 'name: build')
    _write(root, '.github/workflows/deploy.yaml', 'name: deploy')
    _write(root, 'config/config-map/application.dev-pbmaz.properties',
           _baseline_text() + 'spring.application.name=orders\n'
           'spring.mvc.throw-exception-if-no-handler-found=true\n')
    for name in ('Deployment', 'Route', 'Secret', 'Service'):
        _write(root, 'config/deploy-config/templates/%s.yaml' % name, 'x')
    _write(root, 'config/deploy-config/Chart.yaml', 'name: x')
    _write(root, 'config/deploy-config/values.dev-pbmaz.yaml', VALUES)
    return root


def _results(root):
    return {c['name']: c for c in run(root)}


def _status(root, check):
    return _results(root)[check]['status']


# ---- a compliant project ---------------------------------------------------------------------

def test_a_project_that_follows_the_skill_has_no_gaps(project):
    results = _results(project)
    assert {n: c['status'] for n, c in results.items() if c['status'] != 'PASS'} == {}
    assert len(results) >= 15, 'a gate that checked nothing would also have no gaps'


def test_every_check_cites_the_skill_item_it_comes_from(project):
    assert all(c['skill_ref'] for c in run(project))


# ---- comments never change a verdict ---------------------------------------------------------

def test_a_pom_comment_naming_a_dependency_is_not_a_dependency(project):
    """The real pom said 'NO framework-connector-qlcics'; the gate read it as the backend."""
    assert Project(project).backend() == 'MQ_COBOL'
    assert 'SKILL_MQ1' in _results(project), 'MQ checks apply, QL ones do not'
    assert 'SKILL_B3' not in _results(project) and 'SKILL_B1' not in _results(project)


def test_an_akeyless_mention_in_a_comment_is_not_the_key(project):
    _write(project, 'config/deploy-config/values.dev-pbmaz.yaml',
           VALUES + '# AKEYLESS_ENABLED is not generated here; no Akeyless keys are needed.\n')
    assert _status(project, 'SKILL_M13') == 'PASS'


def test_a_disabled_akeyless_key_is_a_gap(project):
    _write(project, 'config/deploy-config/values.dev-pbmaz.yaml', VALUES + 'AKEYLESS_ENABLED: false\n')
    assert _status(project, 'SKILL_M13') == 'GAP'


# ---- BLOCKING items fail; everything else is a named gap -------------------------------------

def test_a_missing_framework_restclient_fails_the_gate(project):
    _write(project, 'cvs-v1-orders-oci-web/pom.xml', POM.replace('framework-restclient', 'something-else'))
    result = _results(project)['SKILL_B4']
    assert result['status'] == 'FAIL' and result['severity'] == 'BLOCKING'
    assert 'framework-restclient' in result['evidence'][0] and result['remediation']


def test_ql_cics_blocking_items_apply_only_to_ql_cics(project):
    pom = POM.replace('framework-mqclient', 'framework-connector-qlcics')
    _write(project, 'cvs-v1-orders-oci-web/pom.xml', pom)
    results = _results(project)
    assert results['SKILL_B3']['status'] == 'FAIL', 'QL_CICS needs camel-spring-boot-starter'
    assert 'SKILL_MQ1' not in results


def test_a_ql_connector_using_a_generic_http_client_fails(project):
    _write(project, 'cvs-v1-orders-oci-web/pom.xml',
           POM.replace('framework-mqclient', 'framework-connector-qlcics').replace(
               '</dependencies>', '<dependency><artifactId>camel-spring-boot-starter</artifactId></dependency></dependencies>'))
    _write(project, 'cvs-v1-orders-oci-web/src/main/java/x/connector/C.java',
           'package x; class C { RestTemplate t; }')
    assert _status(project, 'SKILL_B1') == 'FAIL'


MUTATIONS = [
    # (check, description, mutation)
    ('SKILL_R4', 'RestTemplate', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/S.java', 'class S { RestTemplate t; }')),
    ('SKILL_MQ2', 'Bindy in an MQ service', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/M.java', '@FixedLengthRecord class M {}')),
    ('SKILL_M1_M2', 'no config-map', lambda r: shutil.rmtree(r / 'config' / 'config-map')),
    ('SKILL_M1_M2', 'config-map misnamed', lambda r: (r / 'config/config-map/application.dev-pbmaz.properties').rename(
        r / 'config/config-map/application-dev.properties')),
    ('SKILL_M5_M16', 'no MVC key', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/resources/application.properties', 'spring.profiles.active=local\n')),
    ('SKILL_M5_M16', 'properties files inverted', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/resources/application.properties',
        'spring.mvc.throw-exception-if-no-handler-found=true\n' + ''.join('k%d=v\n' % i for i in range(40)))),
    ('SKILL_M7_M10', 'no Helm chart', lambda r: shutil.rmtree(r / 'config' / 'deploy-config')),
    ('SKILL_M7_M10', 'camelCase values schema', lambda r: _write(
        r, 'config/deploy-config/values.dev-pbmaz.yaml', 'appName: a\nreplicaCount: 1\n')),
    ('SKILL_M9', 'no .github', lambda r: shutil.rmtree(r / '.github')),
    ('SKILL_A0', 'baseline key missing from a config-map', lambda r: _write(
        r, 'config/config-map/application.dev-pbmaz.properties', 'spring.application.name=orders\n')),
    ('SKILL_A', 'packaged runtime key missing from a config-map', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/resources/application.properties',
        'spring.application.name=orders\nspring.mvc.throw-exception-if-no-handler-found=true\nonly.packaged=1\n')),
    ('SKILL_A', 'actuator exposure missing', lambda r: _write(
        r, 'config/config-map/application.dev-pbmaz.properties',
        ''.join(l + '\n' for l in _baseline_text().splitlines() if 'exposure' not in l))),
    ('SKILL_A', 'local profile copied to a config-map', lambda r: _write(
        r, 'config/config-map/application.dev-pbmaz.properties',
        _baseline_text() + 'spring.profiles.active=local\n')),
    ('SKILL_R3', 'Dockerfile in the web module only', lambda r: (r / 'Dockerfile').unlink()),
    ('SKILL_R3', 'Dockerfile with EXPOSE', lambda r: _write(r, 'Dockerfile', DOCKERFILE + 'EXPOSE 8080\n')),
    ('SKILL_R3', 'Dockerfile running as root', lambda r: _write(
        r, 'Dockerfile', DOCKERFILE.replace('USER 1001', 'USER root'))),
    ('SKILL_M19', 'token validator', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/T.java', 'class TokenValidationService {}')),
    ('SKILL_M20', 'try block in a controller', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/controller/OrdersController.java',
        CONTROLLER.replace('return service.get(q);', 'try { return service.get(q); } catch (Exception e) { throw e; }'))),
    ('SKILL_M20', 'ErrorUtil in a controller', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/controller/OrdersController.java',
        CONTROLLER.replace('return service.get(q);', 'return ErrorUtil.errorResponse(400, "x");'))),
    ('SKILL_M21', 'exception package', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/exception/E.java', 'class E {}')),
    ('SKILL_M21', 'custom exception', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/E.java', 'class E extends RuntimeException {}')),
    ('SKILL_M21', 'RestControllerAdvice', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/A.java', '@RestControllerAdvice class A {}')),
    ('SKILL_M22', 'xml models under swagger', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/java/x/swagger/model/xml/X.java', 'class X {}')),
    ('SKILL_M23', 'local targets dev', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/resources/application-local.properties',
        _baseline_text() + 'application.http.restclient.q.uri=https://deveippbm-oci.cvshealth.com/q\n' + 'x=1\n' * 8)),
    ('SKILL_M24', 'leftover parent target', lambda r: (r / 'cvs-v1-orders-oci-parent' / 'target').mkdir()),
    ('SKILL_M28', 'env-var reference in the packaged file', lambda r: _write(
        r, 'cvs-v1-orders-oci-web/src/main/resources/application-local.properties',
        _baseline_text() + 'application.http.restclient.q.uri=${Q_URI}\n' + 'x=1\n' * 8)),
    ('SKILL_M12', 'wrong top-level name', None),
    ('SKILL_M18', 'circuit breaker with no branch', None),
]


@pytest.mark.parametrize('check,why,mutate', [m for m in MUTATIONS if m[2]],
                         ids=[m[1] for m in MUTATIONS if m[2]])
def test_each_skill_item_notices_its_own_violation(project, check, why, mutate):
    mutate(project)
    result = _results(project)[check]
    assert result['status'] == 'GAP', (why, result)
    assert result['evidence'] and result['remediation'] and result['severity'] == 'MEDIUM'


def test_a_wrong_top_level_directory_name_is_a_gap(tmp_path, project):
    renamed = tmp_path / 'orders'
    project.rename(renamed)
    assert _status(renamed, 'SKILL_M12') == 'GAP'


def test_circuit_breaker_properties_with_no_branch_are_a_gap(project):
    """The baseline block carries the circuit-breaker keys, so a compliant project needs the branch."""
    assert _status(project, 'SKILL_M18') == 'PASS'
    _write(project, 'cvs-v1-orders-oci-web/src/main/java/x/OrdersService.java',
           'package x; public class OrdersService {}')
    assert _status(project, 'SKILL_M18') == 'GAP'


# ---- [M28][Rule 15]: no properties file defers a value to an OS environment variable -----------

def test_a_spring_default_value_reference_is_not_a_gap(project):
    """${PORT:8080} -- the baseline block's own server.port -- has a literal default; not what M28 flags."""
    assert _status(project, 'SKILL_M28') == 'PASS'
    assert 'server.port=${PORT:8080}' in _baseline_text()


def test_a_config_map_env_var_reference_is_caught_too(project):
    _write(project, 'config/config-map/application.dev-pbmaz.properties',
          _baseline_text() + 'application.http.restclient.q.uri=${Q_URI}\n')
    result = _results(project)['SKILL_M28']
    assert result['status'] == 'GAP'
    assert any('Q_URI' in e for e in result['evidence'])


def test_a_literal_placeholder_is_not_a_gap(project):
    """Rule 15's own remedy -- a clearly-marked literal string -- must not itself trip the check."""
    _write(project, 'cvs-v1-orders-oci-web/src/main/resources/application-local.properties',
          _baseline_text() + 'application.http.restclient.q.uri=<Q_URI_TBD_BY_TEAM>\n' + 'x=1\n' * 8)
    assert _status(project, 'SKILL_M28') == 'PASS'


def test_a_reference_to_a_key_defined_in_the_same_file_is_not_a_gap(project):
    """A genuine property-to-property reference, not a dangling OS-environment lookup."""
    _write(project, 'cvs-v1-orders-oci-web/src/main/resources/application-local.properties',
          _baseline_text() + 'MY_BASE=https://x\napplication.http.restclient.q.uri=${MY_BASE}/q\n' + 'x=1\n' * 8)
    assert _status(project, 'SKILL_M28') == 'PASS'


def test_the_violating_line_and_file_are_named_in_the_evidence(project):
    _write(project, 'cvs-v1-orders-oci-web/src/main/resources/application-local.properties',
          _baseline_text() + 'application.http.restclient.q.uri=${Q_URI}\n' + 'x=1\n' * 8)
    evidence = _results(project)['SKILL_M28']['evidence']
    assert len(evidence) == 1
    assert 'application-local.properties' in evidence[0] and 'Q_URI' in evidence[0]


def test_gaps_do_not_fail_the_gate(project):
    shutil.rmtree(project / '.github')
    results = run(project)
    assert any(c['status'] == 'GAP' for c in results)
    assert not any(c['status'] == 'FAIL' for c in results)


# ---- the endpoint scan -----------------------------------------------------------------------

def test_a_javadoc_mention_of_an_xml_namespace_is_not_an_endpoint(tmp_path):
    f = tmp_path / 'R.java'
    f.write_text('/**\n * The namespace is http://service.ecs.caremark.com.\n */\nclass R {}\n')
    assert hardcoded_endpoints([f]) == []


def test_disabling_doctype_declarations_is_not_an_endpoint(tmp_path):
    """XXE protection: the gate used to fail the project for having it."""
    f = tmp_path / 'P.java'
    f.write_text('class P { void f() { factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true); } }')
    assert hardcoded_endpoints([f]) == []


def test_a_real_hardcoded_endpoint_is_still_caught(tmp_path):
    f = tmp_path / 'C.java'
    f.write_text('class C { String u = "https://prod.cvshealth.com/orders"; }')
    hits = hardcoded_endpoints([f])
    assert len(hits) == 1 and 'prod.cvshealth.com' in hits[0]


def test_a_url_in_a_string_is_not_mistaken_for_a_comment(tmp_path):
    """`//` inside "http://..." must not start a comment, or the scan would go blind."""
    f = tmp_path / 'C.java'
    f.write_text('class C { String u = "http://backend.internal/x"; // trailing note\n}')
    assert len(hardcoded_endpoints([f])) == 1


def test_a_commented_out_endpoint_is_ignored(tmp_path):
    f = tmp_path / 'a.properties'
    f.write_text('# uri=https://old.example.org/x\nname=ok\n')
    assert hardcoded_endpoints([f]) == []


def test_strip_java_comments_keeps_strings_and_drops_comments():
    src = 'a = "x//y"; // gone\n/* gone */ b = \'"\'; /** doc */ c = "/* kept */";'
    out = strip_java_comments(src)
    assert '"x//y"' in out and '"/* kept */"' in out and 'gone' not in out and 'doc' not in out


# ---- the gate's overall status ---------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    from app.repository import Repository
    return Repository(str(tmp_path / 'gate.db'))


@pytest.fixture
def job(repo):
    from app.service import FactoryService
    return FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')


@pytest.fixture
def lifecycle(repo):
    from app.services.migration_lifecycle_v6 import MappingAndPlanningService
    return MappingAndPlanningService(repo)


def test_a_project_with_only_gaps_is_pass_with_gaps(repo, job, lifecycle, project):
    shutil.rmtree(project / '.github')
    (project / 'provenance-manifest.json').write_text('{}')
    result = lifecycle.quality_validate(job['id'], 'plan-1', str(project))
    assert result['status'] == 'PASS_WITH_GAPS'


def test_a_blocking_item_fails_the_whole_gate(repo, job, lifecycle, project):
    _write(project, 'cvs-v1-orders-oci-web/pom.xml', POM.replace('framework-restclient', 'x'))
    (project / 'provenance-manifest.json').write_text('{}')
    assert lifecycle.quality_validate(job['id'], 'plan-1', str(project))['status'] == 'FAIL'


def test_the_skill_checks_are_recorded_with_the_run(repo, job, lifecycle, project):
    import json
    (project / 'provenance-manifest.json').write_text('{}')
    result = lifecycle.quality_validate(job['id'], 'plan-1', str(project))
    stored = json.loads(repo.one('select payload from quality_runs where id=?', (result['quality_run_id'],))['payload'])
    assert any(c['name'].startswith('SKILL_') for c in stored['checks'])
