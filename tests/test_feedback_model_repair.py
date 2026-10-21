"""The model-assisted repair stage of the feedback agent.

The deterministic stage runs first and clears the checks whose fix is already
defined by the gate's own rules, so the model is only ever consulted about what
is left -- in practice a compile or test failure. These tests script
``quality_validate`` so that branch is reachable without standing up a real
build, and assert the property that makes the stage safe: the gate's verdict
decides whether an edit survives, not the model's confidence.
"""
import json
import re

import pytest

# This module registers its own prompt and model rows, so it needs the registry
# empty: the suite seeds the catalogue APPROVED now, and register_prompt is
# unique on (name, version).
pytestmark = pytest.mark.pristine_registry

from app.repository import Repository, uid, now
from app.service import FactoryService
from app.services.feedback import CodeFeedbackService
from app.services.migration_lifecycle_v6 import MappingAndPlanningService
from app.services.platform_v7 import AIRegistryService

CHECK_NAMES = ('PROJECT_STRUCTURE', 'SECRET_SCAN', 'HARDCODED_ENDPOINT_SCAN',
               'PROVENANCE', 'COMPILE_AND_UNIT_TEST')


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setenv('FACTORY_DB', str(tmp_path / 'fb.db'))
    repo = Repository(str(tmp_path / 'fb.db'))
    job = FactoryService(repo).create_job(
        {'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    plan_id = uid()
    with repo.connect() as c:
        c.execute('insert into migration_plans(id,job_id,version,tasks,mappings,risks,'
                  'acceptance,content_hash,created_at,analysis_id,framework_id,'
                  'specification_id,status) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (plan_id, job['id'], '1.0.0', '[]', '[]', '[]', '[]', 'h', now(),
                   'an', 'fw', 'sp', 'DRAFT'))
    _approve_gateway(repo)
    return repo, job, plan_id, tmp_path


def _approve_gateway(repo):
    """An APPROVED prompt, gateway profile and model, so reason() can run."""
    import hashlib
    tpl = 'repair'
    with repo.connect() as c:
        repo.insert(c, 'prompt_registry', {
            'id': uid(), 'name': 'agent/feedback/code-repair', 'version': '1',
            'template': tpl, 'content_hash': hashlib.sha256(tpl.encode()).hexdigest(),
            'status': 'APPROVED', 'approved_by': 't', 'effective_from': now(),
            'created_at': now()})
        repo.insert(c, 'connector_profiles', {
            'id': uid(), 'category': 'model-gateway', 'name': 'mp', 'provider': 'x',
            'config': '{}', 'secret_reference': '', 'priority': 1, 'enabled': 1,
            'status': 'VERIFIED', 'last_checked_at': '', 'created_at': now(),
            'updated_at': now()})
        repo.insert(c, 'model_registry', {
            'id': uid(), 'name': 'coding', 'version': 'v1', 'provider_profile': 'mp',
            'policy': '{}', 'region': '', 'status': 'APPROVED', 'approved_by': 't',
            'created_at': now()})


def _project(root, yaml_text=None, java_text=None):
    proj = root / 'springboot-output'
    (proj / 'src/main/resources').mkdir(parents=True, exist_ok=True)
    (proj / 'src/main/java').mkdir(parents=True, exist_ok=True)
    (proj / 'pom.xml').write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies><dependency>'
        '<artifactId>framework-restclient</artifactId></dependency></dependencies></project>')
    (proj / 'provenance-manifest.json').write_text('{}')
    (proj / 'src/main/resources/application.yml').write_text(
        yaml_text if yaml_text is not None else 'spring:\n  name: svc\n')
    (proj / 'src/main/java/Svc.java').write_text(
        java_text if java_text is not None else 'class Svc{}')
    return proj


def _scripted_gate(monkeypatch, sequence):
    """Drive quality_validate through a scripted series of failing-check sets."""
    state = {'n': 0}

    def fake(self, job_id, plan_id, project_root, execute_build=False):
        failed = sequence[min(state['n'], len(sequence) - 1)]
        state['n'] += 1
        return {'quality_run_id': 'qr-%d' % state['n'],
                'status': 'FAIL' if failed else 'PASS',
                'checks': [{'name': n, 'status': 'FAIL' if n in failed else 'PASS',
                            'evidence': ''} for n in CHECK_NAMES]}

    monkeypatch.setattr(MappingAndPlanningService, 'quality_validate', fake)
    return state


class _StubModel:
    """Stands in for the Claude adapter, recording what it was sent."""
    model = 'claude-opus-5-5'

    def __init__(self, edits):
        self.edits = edits
        self.payload = None

    def invoke(self, template, payload, skill=None):
        self.payload = payload
        return {'edits': self.edits}


def _ctx(repo, job_id, provider):
    from app.agents.base import AgentContext
    return AgentContext(repo, job_id, provider=provider,
                        effective_model_alias='coding', agent_id='feedback')


def test_model_edit_is_kept_when_the_gate_improves(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, set(), set()])
    model = _StubModel([{'path': 'src/main/java/Svc.java', 'old_str': 'class Svc{}',
                         'new_str': 'class Svc{ /* repaired */ }', 'why': 'fix compile'}])

    out = CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    assert [r['outcome'] for r in out['model_rounds']] == ['kept']
    assert '/* repaired */' in (proj / 'src/main/java/Svc.java').read_text()
    assert any(f['check'] == 'MODEL_REPAIR' for f in out['applied_fixes'])


def test_model_edit_is_reverted_when_the_gate_does_not_improve(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    original = (proj / 'src/main/java/Svc.java').read_text()
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, {'COMPILE_AND_UNIT_TEST'}])
    model = _StubModel([{'path': 'src/main/java/Svc.java', 'old_str': 'class Svc{}',
                         'new_str': 'class Svc{ int pointless; }', 'why': 'noop'}])

    out = CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    assert {r['outcome'] for r in out['model_rounds']} == {'reverted_no_improvement'}
    assert (proj / 'src/main/java/Svc.java').read_text() == original


def test_model_edit_is_reverted_when_it_introduces_a_new_failure(env, monkeypatch):
    """A repair that breaks something else must leave no trace.

    The edit introduces a hardcoded endpoint, so the gate gains
    HARDCODED_ENDPOINT_SCAN on top of the failure it was asked to fix. That is
    not a strict improvement, so every touched file is restored.
    """
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    original = (proj / 'src/main/java/Svc.java').read_text()
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'},
                                 {'COMPILE_AND_UNIT_TEST', 'HARDCODED_ENDPOINT_SCAN'}])
    model = _StubModel([{
        'path': 'src/main/java/Svc.java', 'old_str': 'class Svc{}',
        'new_str': 'class Svc{ String ep="https://evil.prod.internal/x"; }', 'why': 'bad'}])

    out = CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    assert {r['outcome'] for r in out['model_rounds']} == {'reverted_no_improvement'}
    assert (proj / 'src/main/java/Svc.java').read_text() == original
    assert 'evil.prod.internal' not in (proj / 'src/main/java/Svc.java').read_text()


def test_a_credential_bearing_model_response_is_refused_by_the_gateway(env, monkeypatch):
    """Defence in depth ahead of the revert: the response is DLP-scanned too.

    complete_request scans the provider's answer, not just the outbound
    payload, so a model proposing an edit that embeds a credential is refused
    at the gateway and the edit never reaches the filesystem. The revert logic
    is the second line of defence, not the first.
    """
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    original = (proj / 'src/main/java/Svc.java').read_text()
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, {'COMPILE_AND_UNIT_TEST'}])
    model = _StubModel([{'path': 'src/main/java/Svc.java', 'old_str': 'class Svc{}',
                         'new_str': 'class Svc{ String password = "leaked"; }', 'why': 'bad'}])

    out = CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    assert [r['outcome'] for r in out['model_rounds']] == ['no_edits_proposed']
    assert 'dlp_violation_in_response' in out['model_rounds'][0]['reason']
    assert (proj / 'src/main/java/Svc.java').read_text() == original
    assert repo.one("select status from model_requests order by created_at desc limit 1")['status'] == 'BLOCKED'


def test_untrusted_model_paths_are_refused(env, monkeypatch):
    """Model output is data: traversal, absolute paths and pom.xml are refused."""
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, {'COMPILE_AND_UNIT_TEST'}])
    model = _StubModel([
        {'path': '../../evil.java', 'old_str': 'a', 'new_str': 'b', 'why': 'traversal'},
        {'path': '/tmp/evil.java', 'old_str': 'a', 'new_str': 'b', 'why': 'absolute'},
        {'path': 'README.md', 'old_str': '#', 'new_str': '##', 'why': 'wrong suffix'},
    ])

    out = CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    first = out['model_rounds'][0]
    assert first['outcome'] == 'all_edits_refused'
    assert len(first['refused']) == 3
    assert 'http://maven.apache.org/POM/4.0.0' in (proj / 'pom.xml').read_text()


def test_a_pom_edit_is_accepted_so_a_missing_dependency_can_be_repaired(env, monkeypatch):
    """A build that fails on a missing dependency is fixed in pom.xml, nowhere else."""
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, set(), set()])
    model = _StubModel([{'path': 'pom.xml', 'old_str': '<dependencies>',
                         'new_str': '<dependencies><dependency><artifactId>x</artifactId></dependency>',
                         'why': 'missing dependency'}])

    out = CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    assert [r['outcome'] for r in out['model_rounds']] == ['kept']
    assert '<artifactId>x</artifactId>' in (proj / 'pom.xml').read_text()


def test_the_build_errors_and_the_files_they_cite_reach_the_model(env, monkeypatch):
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    seen = {'n': 0}

    def fake(self, job_id, plan_id, project_root, execute_build='auto'):
        seen['n'] += 1
        return {'quality_run_id': 'qr', 'status': 'FAIL', 'checks': [
            {'name': 'COMPILE_AND_UNIT_TEST', 'status': 'FAIL', 'evidence': {
                'reason': 'build failed', 'errors': ['[ERROR] Svc.java:[1,1] cannot find symbol']}}]}

    monkeypatch.setattr(MappingAndPlanningService, 'quality_validate', fake)
    model = _StubModel([])

    CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=1, context=_ctx(repo, job['id'], model))

    detail = model.payload['check_detail']['COMPILE_AND_UNIT_TEST']
    assert 'cannot find symbol' in detail
    assert model.payload['files'][0]['path'].endswith('Svc.java'), 'the cited file comes first'


def test_payload_sent_to_the_model_passes_the_gateway_dlp_rules(env, monkeypatch):
    """Source reaches the gateway only after redaction.

    CREDENTIAL_ASSIGNMENT matches any ``password:`` line, including one already
    correctly externalised, so an unredacted application.yml would be refused
    outright and the repair stage would never run.
    """
    repo, job, plan_id, tmp = env
    proj = _project(tmp, yaml_text='db:\n  password: "${PASSWORD}"\n  url: x\n')
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, {'COMPILE_AND_UNIT_TEST'}])
    model = _StubModel([])

    CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    assert model.payload is not None, 'the model stage never ran'
    body = json.dumps(model.payload, sort_keys=True, default=str)
    assert not [r for r, rx in AIRegistryService.DLP_RULES if re.search(rx, body)]
    assert 'REDACTED_CREDENTIAL' in body


def test_model_is_not_consulted_without_a_provider(env, monkeypatch):
    """No provider means deterministic-only, which is what CI and tests get."""
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, {'COMPILE_AND_UNIT_TEST'}])

    out = CodeFeedbackService(repo).apply(job['id'], plan_id, str(proj), max_passes=2)

    assert out['model_rounds'] == []


def test_edit_count_is_capped(env, monkeypatch):
    """A pathological response must not rewrite the project wholesale."""
    from app.services.feedback import MAX_EDITS
    repo, job, plan_id, tmp = env
    proj = _project(tmp)
    _scripted_gate(monkeypatch, [{'COMPILE_AND_UNIT_TEST'}, {'COMPILE_AND_UNIT_TEST'}])
    model = _StubModel([{'path': 'nope-%d.java' % i, 'old_str': 'a', 'new_str': 'b',
                         'why': 'flood'} for i in range(MAX_EDITS + 40)])

    out = CodeFeedbackService(repo).apply(
        job['id'], plan_id, str(proj), max_passes=2, context=_ctx(repo, job['id'], model))

    assert len(out['model_rounds'][0]['refused']) <= MAX_EDITS
