"""The gate builds and starts the generated service -- without needing Maven here.

Maven and Java are not guaranteed on a CI host, so the subprocess calls are
replaced; what is pinned is the decision logic, the evidence the feedback agent
repairs from, and that a host without the tools is reported, not failed.
"""
import types

import pytest

from app.services import build_check


@pytest.fixture(autouse=True)
def _mode(monkeypatch):
    monkeypatch.delenv(build_check.MODE_ENV, raising=False)


def _tools(monkeypatch, have=('mvn', 'java')):
    monkeypatch.setattr(build_check.shutil, 'which', lambda n: ('/bin/' + n) if n in have else None)


def test_an_explicit_false_never_builds(monkeypatch):
    _tools(monkeypatch)
    assert build_check.decide(False) == (False, 'execute_build=false')


def test_off_mode_never_builds_even_when_asked_for_auto(monkeypatch):
    _tools(monkeypatch)
    monkeypatch.setenv(build_check.MODE_ENV, 'off')
    assert build_check.decide('auto')[0] is False


def test_auto_builds_when_the_tools_exist_and_reports_when_they_do_not(monkeypatch):
    _tools(monkeypatch)
    assert build_check.decide('auto') == (True, '')
    _tools(monkeypatch, have=('java',))
    run, why = build_check.decide('auto')
    assert run is False and 'maven' in why


def test_a_host_without_maven_is_not_executed_not_failed(monkeypatch, tmp_path):
    _tools(monkeypatch, have=())
    out = build_check.checks(tmp_path, 'auto')
    assert [(c['name'], c['status']) for c in out] == [('COMPILE_AND_UNIT_TEST', 'NOT_EXECUTED'),
                                                       ('STARTUP_SMOKE', 'NOT_EXECUTED')]


def test_an_explicit_request_on_a_host_without_maven_fails(monkeypatch, tmp_path):
    """Asked to prove it builds, and unable to: that is not a pass."""
    _tools(monkeypatch, have=())
    (tmp_path / 'pom.xml').write_text('<project/>')
    out = build_check.checks(tmp_path, True)
    assert out[0]['status'] == 'FAIL' and 'maven' in str(out[0]['evidence'])


def test_the_build_runs_from_the_parent_pom_as_the_skill_says(monkeypatch, tmp_path):
    (tmp_path / 'svc-parent').mkdir()
    (tmp_path / 'svc-parent' / 'pom.xml').write_text('<project/>')
    (tmp_path / 'svc-web').mkdir()
    (tmp_path / 'svc-web' / 'pom.xml').write_text('<project/>')
    assert build_check.build_pom(tmp_path) == tmp_path / 'svc-parent' / 'pom.xml'
    (tmp_path / 'pom.xml').write_text('<project/>')
    assert build_check.build_pom(tmp_path) == tmp_path / 'pom.xml'


def test_the_compiler_errors_are_kept_for_the_repair(monkeypatch, tmp_path):
    _tools(monkeypatch)
    (tmp_path / 'svc-parent').mkdir()
    (tmp_path / 'svc-parent' / 'pom.xml').write_text('<project/>')
    seen = {}

    def fake_run(cmd, **kw):
        seen['cmd'], seen['env'] = cmd, kw['env']
        return types.SimpleNamespace(
            returncode=1, stdout='[INFO] Scanning\n[ERROR] /w/Svc.java:[12,5] cannot find symbol\n'
                                 '[ERROR] /w/Svc.java:[12,5] cannot find symbol\n[INFO] done\n', stderr='')

    monkeypatch.setattr(build_check.subprocess, 'run', fake_run)
    result = build_check.build(tmp_path)

    assert result['status'] == 'FAIL'
    assert result['errors'] == ['[ERROR] /w/Svc.java:[12,5] cannot find symbol'], 'deduplicated, noise dropped'
    assert seen['cmd'][-2:] == ['clean', 'verify'] and '-f' in seen['cmd']


def test_the_build_environment_is_an_allow_list(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'not-for-generated-code')
    monkeypatch.setenv('ARTIFACTORY_USER', 'needed-by-settings-xml')
    monkeypatch.setenv(build_check.PASSTHROUGH_ENV, 'ARTIFACTORY_USER')
    env = build_check.environment()
    assert 'GITHUB_TOKEN' not in env
    assert env['ARTIFACTORY_USER'] == 'needed-by-settings-xml'


def test_the_parent_target_is_removed_after_the_build(monkeypatch, tmp_path):
    _tools(monkeypatch)
    (tmp_path / 'svc-parent' / 'target').mkdir(parents=True)
    (tmp_path / 'svc-parent' / 'pom.xml').write_text('<project/>')
    monkeypatch.setattr(build_check.subprocess, 'run',
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout='', stderr=''))
    build_check.build(tmp_path)
    assert not (tmp_path / 'svc-parent' / 'target').exists()


def test_a_service_that_never_starts_fails_with_its_log(monkeypatch, tmp_path):
    _tools(monkeypatch)
    jar = tmp_path / 'svc-web' / 'target' / 'svc-web-1.0.jar'
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b'x')

    class Dead:
        returncode = 1

        def poll(self):
            return 1

    def popen(cmd, stdout=None, **kw):
        stdout.write('APPLICATION FAILED TO START\nCaused by: java.lang.IllegalStateException: bad config\n')
        stdout.flush()
        return Dead()

    monkeypatch.setattr(build_check.subprocess, 'Popen', popen)
    result = build_check.start(tmp_path, timeout=2)

    assert result['status'] == 'FAIL'
    assert any('IllegalStateException' in e for e in result['errors'])


def test_the_startup_check_needs_a_packaged_jar(monkeypatch, tmp_path):
    _tools(monkeypatch)
    assert build_check.start(tmp_path)['status'] == 'FAIL'
