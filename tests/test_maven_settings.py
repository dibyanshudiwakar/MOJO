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


# -- a supplied framework repository is built first ---------------------------------------------------------

@pytest.fixture
def maven(monkeypatch):
    """A fake Maven that records each command, answering by a per-pom outcome."""
    calls, outcomes = [], {}

    class Done:
        def __init__(self, code, err=''):
            self.returncode, self.stdout, self.stderr = code, '', err

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        pom = cmd[cmd.index('-f') + 1]
        code, err = outcomes.get(pom, (0, ''))
        return Done(code, err)

    build_check._framework_built.clear()
    monkeypatch.setattr(build_check, 'tools', lambda: ('/usr/bin/mvn', '/usr/bin/java', []))
    monkeypatch.setattr(build_check.subprocess, 'run', fake_run)
    return calls, outcomes


def _project(tmp_path, framework=True):
    root = tmp_path / 'service'
    root.mkdir()
    (root / 'pom.xml').write_text('<project/>')
    (root / 'settings.xml').write_text('<settings/>')
    fw = tmp_path / 'framework'
    fw.mkdir()
    if framework:
        (fw / 'pom.xml').write_text('<project/>')
    return root, {'root': str(fw), 'commit': 'abc123'}


def test_the_framework_is_built_and_installed_before_the_project(tmp_path, maven):
    calls, _ = maven
    root, fw = _project(tmp_path)
    result = build_check.build(root, framework=fw)
    assert result['status'] == 'PASS' and result['framework']['status'] == 'PASS'
    assert len(calls) == 2
    first, second = calls
    assert str(tmp_path / 'framework' / 'pom.xml') in first and 'install' in first and '-DskipTests' in first
    assert str(root / 'pom.xml') in second and 'verify' in second
    # The framework resolves through the same internal repositories as the project.
    assert first[first.index('-s') + 1] == str(root / 'settings.xml')


def test_a_framework_that_does_not_build_stops_the_project_build(tmp_path, maven):
    calls, outcomes = maven
    root, fw = _project(tmp_path)
    outcomes[str(tmp_path / 'framework' / 'pom.xml')] = (1, '[ERROR] COMPILATION ERROR in framework-core')
    result = build_check.build(root, framework=fw)
    assert result['status'] == 'FAIL' and 'framework' in result['reason']
    assert any(e.startswith('framework: ') for e in result['errors'])
    assert len(calls) == 1, 'the project was not built: it could only fail for the same reason'


def test_an_unchanged_framework_is_not_rebuilt_for_the_next_check(tmp_path, maven):
    calls, _ = maven
    root, fw = _project(tmp_path)
    build_check.build(root, framework=fw)
    again = build_check.build(root, framework=fw)
    assert again['framework']['note'].startswith('already built')
    assert sum(1 for c in calls if 'install' in c) == 1


def test_a_framework_without_a_pom_is_skipped_not_failed(tmp_path, maven):
    calls, _ = maven
    root, fw = _project(tmp_path, framework=False)
    result = build_check.build(root, framework=fw)
    assert result['status'] == 'PASS' and result['framework']['status'] == 'SKIPPED'
    assert len(calls) == 1


def test_no_framework_means_no_extra_build(tmp_path, maven):
    calls, _ = maven
    root, _fw = _project(tmp_path)
    result = build_check.build(root)
    assert 'framework' not in result and len(calls) == 1


def test_the_quality_gate_finds_the_jobs_framework_repository(tmp_path):
    from app.repository import Repository, now, uid
    from app.service import FactoryService
    from app.services.migration_lifecycle_v6 import MappingAndPlanningService
    repo = Repository(str(tmp_path / 'fw.db'))
    job = FactoryService(repo).create_job({'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    service = MappingAndPlanningService(repo)
    assert service._framework_repo(job['id']) is None
    fw = tmp_path / 'framework'
    fw.mkdir()
    with repo.connect() as c:
        repo.insert(c, 'job_repositories', {'id': uid(), 'job_id': job['id'], 'role': 'FRAMEWORK',
                                            'repo_url': 'https://github.com/org/fw.git', 'branch': 'main', 'enabled': 1,
                                            'local_path': str(fw), 'resolved_commit': 'deadbeef',
                                            'push_mode': 'LOCAL_OUTPUT', 'created_at': now(), 'updated_at': now()})
    assert service._framework_repo(job['id']) == {'root': str(fw), 'commit': 'deadbeef'}
