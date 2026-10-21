"""The feedback agent closes the skill's GAPs that the skill says to auto-fix.

Each repair is checked against the gate itself: the evidence that a GAP is closed
is ``skill_gate.run`` no longer reporting it, not the repair claiming so.
"""
import pytest

from app.services import skill_gate, skill_repair
from tests.test_skill_gate import ROOT_NAME, _write, project  # noqa: F401  (fixture)

WEB = 'cvs-v1-orders-oci-web'
RES = WEB + '/src/main/resources/'


def _status(root):
    return {c['name']: c['status'] for c in skill_gate.run(root)}


def _open(root):
    return [n for n, s in _status(root).items() if s != 'PASS']


def test_a_project_with_no_gaps_needs_no_repair(project):
    assert _open(project) == []
    assert skill_repair.repair(project, ['SKILL_A0', 'SKILL_A', 'SKILL_M5_M16']) == []


def test_the_missing_mvc_key_is_added(project):
    path = project / (RES + 'application.properties')
    path.write_text('spring.application.name=orders\n', encoding='utf-8')
    assert 'SKILL_M5_M16' in _open(project)

    skill_repair.repair(project, ['SKILL_M5_M16'])

    assert 'SKILL_M5_M16' not in _open(project)
    assert path.read_text(encoding='utf-8').count('throw-exception-if-no-handler-found') == 1


def test_a_wrong_mvc_value_is_replaced_not_duplicated(project):
    path = project / (RES + 'application.properties')
    path.write_text('spring.mvc.throw-exception-if-no-handler-found=false\n', encoding='utf-8')

    skill_repair.repair(project, ['SKILL_M5_M16'])

    text = path.read_text(encoding='utf-8')
    assert text.count('throw-exception-if-no-handler-found') == 1 and 'found=true' in text


def test_the_baseline_block_lands_in_the_packaged_file_and_every_config_map(project):
    """HANDOFF-A0: the block missing from both was the real project's gap."""
    (project / (RES + 'application-local.properties')).write_text('a=1\nb=2\nc=3\nd=4\ne=5\nf=6\ng=7\n', encoding='utf-8')
    _write(project, 'config/config-map/application.qa2-pbmaz.properties', 'spring.application.name=orders\n')
    assert 'SKILL_A0' in _open(project)

    fixes = skill_repair.repair(project, ['SKILL_A0'])

    assert 'SKILL_A0' not in _open(project)
    assert {f['check'] for f in fixes} == {'SKILL_A0'}
    qa = (project / 'config/config-map/application.qa2-pbmaz.properties').read_text(encoding='utf-8')
    assert 'logging.config=classpath:logback-qa.xml' in qa, 'the logback file is the config-map\'s tier'
    assert '{{' not in qa


def test_existing_values_are_never_overwritten_by_the_baseline(project):
    path = project / 'config/config-map/application.dev-pbmaz.properties'
    path.write_text('server.port=9999\nspring.mvc.throw-exception-if-no-handler-found=true\n', encoding='utf-8')

    skill_repair.repair(project, ['SKILL_A0'])

    text = path.read_text(encoding='utf-8')
    assert text.count('server.port=') == 1 and 'server.port=9999' in text


@pytest.mark.parametrize('name,tier', [('application.dev-pbmaz.properties', 'dev'),
                                       ('application.qa3-pbmaz.properties', 'qa'),
                                       ('application.prod-pbmri.properties', 'prod'),
                                       ('application.uat-pbmaz.properties', 'uat'),
                                       ('application.stress-pbmaz.properties', 'stress')])
def test_the_tier_is_taken_from_the_config_map_name(name, tier):
    assert skill_repair.tier(name) == tier


def test_a_runtime_key_missing_from_a_config_map_is_copied_but_never_the_local_profile(project):
    packaged = project / (RES + 'application.properties')
    packaged.write_text(packaged.read_text(encoding='utf-8') + 'my.runtime.key=42\n', encoding='utf-8')
    cmap = project / 'config/config-map/application.dev-pbmaz.properties'
    cmap.write_text(cmap.read_text(encoding='utf-8') + 'spring.profiles.active=local\n', encoding='utf-8')
    assert 'SKILL_A' in _open(project)

    skill_repair.repair(project, ['SKILL_A'])

    text = cmap.read_text(encoding='utf-8')
    assert 'my.runtime.key=42' in text
    assert 'spring.profiles.active' not in text
    assert 'SKILL_A' not in _open(project)


def test_a_dev_or_prod_hostname_in_the_local_profile_is_pointed_at_qa(project):
    local = project / (RES + 'application-local.properties')
    local.write_text(local.read_text(encoding='utf-8')
                     + 'backend.uri=https://dev3-api.example.test/x\n# old.uri=https://prod-api.example.test/\n',
                     encoding='utf-8')
    assert 'SKILL_M23' in _open(project)

    skill_repair.repair(project, ['SKILL_M23'])

    text = local.read_text(encoding='utf-8')
    assert 'backend.uri=https://qa3-api.example.test/x' in text
    assert '# old.uri=https://prod-api' in text, 'comments are left alone'
    assert 'SKILL_M23' not in _open(project)


def test_the_parent_modules_target_directory_is_removed(project):
    _write(project, 'cvs-v1-orders-oci-parent/target/classes/x.class', 'x')
    assert 'SKILL_M24' in _open(project)

    skill_repair.repair(project, ['SKILL_M24'])

    assert not (project / 'cvs-v1-orders-oci-parent/target').exists()
    assert (project / WEB).is_dir(), 'the web module is untouched'


def test_a_config_map_bundled_in_the_jar_is_moved_out(project):
    _write(project, RES + 'application.qa-pbmaz.properties', 'a=1\n')
    assert 'SKILL_M1_M2' in _open(project)

    skill_repair.repair(project, ['SKILL_M1_M2'])

    assert not (project / (RES + 'application.qa-pbmaz.properties')).exists()
    assert (project / 'config/config-map/application.qa-pbmaz.properties').is_file()
    assert (project / (RES + 'application-local.properties')).exists(), 'the local profile stays'


def test_what_needs_judgment_is_not_repaired(project):
    """A missing Dockerfile, an exception package: the skill leaves these to a person or the model."""
    (project / 'Dockerfile').unlink()
    assert 'SKILL_R3' in _open(project)
    assert 'SKILL_R3' not in skill_repair.REPAIRABLE
    assert skill_repair.repair(project, ['SKILL_R3']) == []
    assert 'SKILL_R3' in _open(project)
