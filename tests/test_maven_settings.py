"""settings.xml: written into every generated project, checked by the gate, and passed to every Maven build."""
from pathlib import Path

import pytest

from app.services import build_check, maven_settings, skill_gate, skill_repair
from tests.test_skill_gate import project  # noqa: F401  (fixture)


def test_the_template_is_the_skills_and_names_the_internal_repositories():
    text = maven_settings.template()
    assert text is not None
    assert '<id>aetna-nexus</id>' in text and 'repo-man.aetna.com' in text
    assert '<activeProfile>aetna-nexus</activeProfile>' in text
    assert '\r' not in text
    # Not a copy kept in code: it is the skill's own file.
    skill_file = (Path(__file__).resolve().parents[1] / 'agent-skills' / 'iib-to-ms-code-generation'
                  / 'references' / 'templates' / 'maven-settings.xml')
    assert text == skill_file.read_bytes().decode('utf-8').replace('\r\n', '\n')


def test_the_flag_is_passed_only_when_the_project_has_the_file(tmp_path):
    assert maven_settings.args(tmp_path) == []
    (tmp_path / 'settings.xml').write_text('<settings/>')
    assert maven_settings.args(tmp_path) == ['-s', str(tmp_path / 'settings.xml')]


def test_the_build_passes_the_projects_settings(tmp_path, monkeypatch):
    (tmp_path / 'pom.xml').write_text('<project/>')
    (tmp_path / 'settings.xml').write_text('<settings/>')
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, '', ''

    def fake_run(cmd, **kwargs):
        seen['cmd'] = cmd
        return Done()

    monkeypatch.setattr(build_check, 'tools', lambda: ('/usr/bin/mvn', '/usr/bin/java', []))
    monkeypatch.setattr(build_check.subprocess, 'run', fake_run)
    result = build_check.build(tmp_path)
    assert result['status'] == 'PASS'
    cmd = seen['cmd']
    assert cmd[cmd.index('-s') + 1] == str(tmp_path / 'settings.xml')
    assert cmd.index('-s') < cmd.index('-f'), 'settings come before the goals'


def test_the_build_without_the_file_adds_no_flag(tmp_path, monkeypatch):
    (tmp_path / 'pom.xml').write_text('<project/>')
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, '', ''

    monkeypatch.setattr(build_check, 'tools', lambda: ('/usr/bin/mvn', '/usr/bin/java', []))
    monkeypatch.setattr(build_check.subprocess, 'run', lambda cmd, **k: seen.setdefault('cmd', cmd) and Done())
    build_check.build(tmp_path)
    assert '-s' not in seen['cmd']


def _status(root):
    return {c['name']: c['status'] for c in skill_gate.run(root)}


def test_a_project_with_the_settings_passes_the_check(project):
    assert _status(project)['SKILL_M26'] == 'PASS'


def test_a_missing_settings_file_is_a_gap_and_is_repaired(project):
    (project / 'settings.xml').unlink()
    assert _status(project)['SKILL_M26'] == 'GAP'
    fixes = skill_repair.repair(project, ['SKILL_M26'])
    assert fixes and fixes[0]['check'] == 'SKILL_M26'
    assert _status(project)['SKILL_M26'] == 'PASS'
    assert (project / 'settings.xml').read_text(encoding='utf-8') == maven_settings.template()


def test_a_settings_file_that_differs_from_the_template_is_a_gap(project):
    (project / 'settings.xml').write_text('<settings/>', encoding='utf-8')
    assert _status(project)['SKILL_M26'] == 'GAP'
    skill_repair.repair(project, ['SKILL_M26'])
    assert _status(project)['SKILL_M26'] == 'PASS'
