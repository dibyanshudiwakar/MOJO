"""The skill decides the layout, so the parent module actually gets generated.

``springgen.py`` has no knowledge of the skill: it hardcodes a flat
single-module plain-Spring-Boot tree. So a skill prescribing an
``EIPMS_cvs-v1-<service>-oci`` root with separate parent and web Maven modules
could never be honoured, and the one thing it reliably produced was an
``exception`` package that ``mandatory-migration-rules.md`` Rule 10 forbids by
name.

The synthesis call could not fix that either: it was edit-only, and
``accept_edit`` refuses a path that is not already a file. A model correctly
proposing the parent POM had every edit refused while the run still reported
green. (That call is gone now -- its only input was springgen's stubs.)

So the layout is asked for first and the files are authored one per call. These
tests pin that the model's layout is what lands on disk, that the gate is no
softer for the path that wrote the code, and that an incomplete answer fails the
agent instead of leaving a half-authored tree -- there is nothing to fall back
to, which is the point: springgen's layout is a different shape entirely, so
substituting it would silently ship a project that breaches the skill it was
asked to follow.
"""
import json
from pathlib import Path

import pytest

from app.agents.artefact import ArtefactUnavailable
from app.agents.code_generation import CodeGenerationAgent
from app.services.migration_lifecycle_v6 import _long
from tests.agents.conftest import ArtefactProvider

LAYOUT = CodeGenerationAgent.LAYOUT_PROMPT
FILE = CodeGenerationAgent.FILE_PROMPT
ALIAS = 'coding'

ROOT = 'EIPMS_cvs-v1-validatebridgesupply-oci'
#: The layout the skill's structural conventions prescribe, in miniature:
#: a repository root, two Maven modules, the Dockerfile at the root, and no
#: exception package anywhere.
WANTED = [
    ('cvs-v1-validatebridgesupply-oci-parent/pom.xml', 'parent module POM'),
    ('cvs-v1-validatebridgesupply-oci-web/pom.xml', 'web module POM'),
    ('cvs-v1-validatebridgesupply-oci-web/src/main/java/com/cvshealth/eip/microservices/'
     'ValidateBridgeSupplyApplication.java', 'Spring Boot entry point'),
    ('cvs-v1-validatebridgesupply-oci-web/src/main/java/com/cvshealth/eip/microservices/'
     'controller/ValidateBridgeSupplyController.java', 'REST controller, delegates only'),
    ('Dockerfile', 'container image at the repository root'),
    ('config/config-map/application.dev-pbmaz.properties', 'per-environment config map'),
]


def _content(path):
    if path.endswith('pom.xml'):
        return ('<project><modelVersion>4.0.0</modelVersion><dependencies><dependency>'
                '<artifactId>framework-restclient</artifactId></dependency></dependencies></project>\n')
    if path.endswith('Dockerfile'):
        return 'FROM openjdk-micro:21\n'
    if path.endswith('.properties'):
        return 'management.endpoints.web.exposure.include=health,info,metrics\n'
    return 'package com.cvshealth.eip.microservices;\n'


def _provider(layout=None, content=_content, omit=()):
    """A double answering the layout call and then each file call."""
    def answer(payload):
        if 'purpose' in payload:                      # a file-synthesis call
            path = payload['path']
            if path in omit:
                return {'path': path, 'content': ''}
            return {'path': path, 'content': content(path)}
        return layout if layout is not None else {    # the layout call
            'root': ROOT,
            'files': [{'path': p, 'purpose': why,
                       'skill_reference': 'references/structural-conventions.md'}
                      for p, why in WANTED],
            'omitted': []}
    return ArtefactProvider(answer)


@pytest.fixture
def params(planned, tmp_path):
    return {'plan_id': planned['plan_id'], 'output_root': str(tmp_path / 'authored'),
            'service_name': 'ValidateBridgeSupply',
            'package_name': 'com.cvshealth.eip.microservices'}


def test_the_models_layout_is_what_lands_on_disk_parent_module_included(
        repo, job, params, gateway, dispatch):
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params, provider=_provider())

    assert outcome['reasoning']['artefact_source'] == 'MODEL'
    assert outcome['metrics']['layout_root'] == ROOT
    root = Path(outcome['outputs']['source_code'])
    assert root.name == ROOT, 'the repository root the skill prescribes'
    # The two things springgen could never produce.
    assert (root / 'cvs-v1-validatebridgesupply-oci-parent/pom.xml').is_file()
    assert (root / 'cvs-v1-validatebridgesupply-oci-web/pom.xml').is_file()
    assert (root / 'Dockerfile').is_file()
    assert (root / 'config/config-map/application.dev-pbmaz.properties').is_file()
    # And the thing Rule 10 forbids is absent, because the layout did not ask
    # for it -- rather than being generated and then needing removal.
    assert not list(root.rglob('exception/*.java'))
    assert not (root / 'src/main/java').exists(), 'no flat springgen tree alongside it'


def test_the_authored_files_are_registered_and_hashed_like_generated_ones(
        repo, job, params, gateway, dispatch):
    """A gate, the output panel and variance analysis must not tell them apart."""
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params, provider=_provider())
    rows = repo.rows('select path,content,content_hash from generated_artifacts where job_id=?',
                     (job['id'],))
    registered = {r['path'] for r in rows}
    assert 'cvs-v1-validatebridgesupply-oci-parent/pom.xml' in registered
    manifest = json.loads(Path(outcome['outputs']['manifest']).read_text())
    by_path = {f['path']: f['hash'] for f in manifest['files']}
    assert all(r['content_hash'] == by_path[r['path']] for r in rows)
    assert manifest['authored_by'] == 'MODEL'
    # The evidence a G3-bound approval cites still equals the manifest hash.
    assert outcome['evidence'][0]['content_hash'] == outcome['outputs']['provenance']


def test_the_quality_gate_still_runs_on_the_authored_project(repo, job, params, gateway, dispatch):
    """The gate is the independent verdict; it is not softer for this path."""
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    dispatch(job['id'], 'code-generation', params, provider=_provider())
    run = repo.one('select status,payload from quality_runs where job_id=?', (job['id'],))
    assert run is not None, 'a model-authored project is gated too'
    names = {c['name'] for c in json.loads(run['payload'])['checks']}
    assert {'SECRET_SCAN', 'HARDCODED_ENDPOINT_SCAN', 'PROVENANCE'} <= names


def test_one_empty_file_fails_the_agent_and_writes_nothing(
        repo, job, params, gateway, dispatch, tmp_path):
    """A tree missing one file does not build, and nothing may stand in for it.

    ``content`` carries minLength 1, so the blank file is a schema violation on
    that single request: it is re-asked twice and then the agent fails. This
    used to substitute springgen's whole project, which meant one blank response
    silently discarded the five files the model had authored correctly and
    shipped a layout the skill forbids.
    """
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    with pytest.raises(ArtefactUnavailable) as raised:
        dispatch(job['id'], 'code-generation', params,
                 provider=_provider(omit={'Dockerfile'}))
    assert raised.value.prompt_name == FILE
    assert 'is 0 characters; at least 1 required' in raised.value.reason
    assert raised.value.attempts == 3
    # No half-written tree, and nothing registered against the plan.
    assert not (tmp_path / 'authored').exists()
    assert repo.one('select count(*) n from generated_artifacts where job_id=?',
                    (job['id'],))['n'] == 0


def test_an_empty_layout_fails_the_agent(repo, job, params, gateway, dispatch):
    """``files`` carries minItems 1, so "no files" is not an answer.

    The agent short-circuits before asking when the plan has no tasks (see
    test_agent_code_generation_empty_plan); reaching the model and being told
    the repository needs no files is a different thing, and it is a failure.
    """
    gateway(LAYOUT, model_name=ALIAS)
    with pytest.raises(ArtefactUnavailable) as raised:
        dispatch(job['id'], 'code-generation', params,
                 provider=_provider(layout={'files': []}))
    assert raised.value.prompt_name == LAYOUT
    assert 'has 0 items; at least 1 required' in raised.value.reason


def test_a_path_escaping_the_project_is_refused_not_written(
        repo, job, params, gateway, dispatch, tmp_path):
    """The schema's pattern admits '..'; containment is enforced at write time.

    So this is the guard that actually matters: the paths came from a model.
    """
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    escaping = {'root': ROOT,
                'files': [{'path': '../../escaped.java', 'purpose': 'traversal attempt'}]
                         + [{'path': p, 'purpose': w} for p, w in WANTED],
                'omitted': []}
    outcome = dispatch(job['id'], 'code-generation', params,
                       provider=_provider(layout=escaping))
    assert not (tmp_path / 'escaped.java').exists()
    assert not (tmp_path.parent / 'escaped.java').exists()
    manifest = json.loads(Path(outcome['outputs']['manifest']).read_text())
    assert not any('escaped' in f['path'] for f in manifest['files'])
    assert any('refused' in str(a) for a in manifest['action_items'])


def test_the_layout_is_asked_at_listing_effort_and_the_files_at_authoring_effort(
        repo, job, params, gateway, dispatch):
    """A file list at the coding alias's xhigh spent all 32,768 tokens on hidden reasoning.

    Observed on a real project layout: 15,096 visible characters, cut off at
    max_tokens. Writing the files themselves is real authoring and is turned
    down only to `high`: at the alias's xhigh an 8,791-character file spent all
    32,768 tokens reasoning.
    """
    from app.agents.artefact import AUTHORING_EFFORT, LISTING_EFFORT

    class Recording(ArtefactProvider):
        accepts_skill = True
        accepts_effort = True

        def __init__(self, answer):
            super().__init__(answer)
            self.efforts = {'layout': set(), 'file': set()}

        def invoke(self, template, payload, skill=None, effort=None):
            from tests.agents.model_double import PROMPT_BY_TEMPLATE
            name = PROMPT_BY_TEMPLATE.get(template, template)
            if name in (LAYOUT, FILE):
                self.efforts['layout' if name == LAYOUT else 'file'].add(effort)
            return super().invoke(template, payload)

    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    inner = _provider()
    provider = Recording(inner._answer)
    dispatch(job['id'], 'code-generation', params, provider=provider)
    assert provider.efforts == {'layout': {LISTING_EFFORT}, 'file': {AUTHORING_EFFORT}}


def test_every_file_payload_carries_the_full_sibling_list(repo, job, params, gateway, dispatch):
    """Each file call is otherwise blind to its siblings -- a controller that believed its own
    service exposed no method at all, and six files each inventing their own copy of the same
    constant, both trace to this. The layout already knows every file's path and purpose before
    any file is written, so every file-synthesis payload carries that same list, unchanged."""
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    provider = _provider()
    dispatch(job['id'], 'code-generation', params, provider=provider)
    expected = [{'path': p, 'purpose': why} for p, why in WANTED]
    file_calls = [c['payload'] for c in provider.calls if 'purpose' in c['payload']]
    assert file_calls, 'at least one file-synthesis call was captured'
    for payload in file_calls:
        assert payload['project_files'] == expected
        assert payload['project_files_note']


# ---- an omission is a path and a reason ----------------------------------------------------
#
# The schema said omitted was a list of strings; its own description said "each
# with why", and the prompt said only "omitted":[...]. The model returned
# {path, reason} objects -- the more useful shape -- and the whole layout failed
# three times on that one field.

def _layout_with(omitted):
    return {'root': ROOT,
            'files': [{'path': p, 'purpose': why, 'skill_reference': 'references/structural-conventions.md'}
                      for p, why in WANTED],
            'omitted': omitted}


def test_an_omission_given_as_a_path_and_a_reason_is_accepted_and_recorded(
        repo, job, params, gateway, dispatch):
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    omitted = [{'path': 'cvs-v1-x-oci-web/src/main/java/exception/*',
                'reason': 'Rule 10 forbids an exception package'}]
    outcome = dispatch(job['id'], 'code-generation', params,
                       provider=_provider(layout=_layout_with(omitted)))
    manifest = json.loads(Path(outcome['outputs']['manifest']).read_text())
    assert ('omitted by the model: cvs-v1-x-oci-web/src/main/java/exception/*: '
            'Rule 10 forbids an exception package') in manifest['action_items']


def test_a_bare_string_omission_is_still_accepted(repo, job, params, gateway, dispatch):
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params,
                       provider=_provider(layout=_layout_with(['Secret.yaml: service needs no secret'])))
    manifest = json.loads(Path(outcome['outputs']['manifest']).read_text())
    assert 'omitted by the model: Secret.yaml: service needs no secret' in manifest['action_items']


def test_an_omission_object_without_a_path_is_still_rejected(repo, job, params, gateway, dispatch):
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    with pytest.raises(ArtefactUnavailable):
        dispatch(job['id'], 'code-generation', params,
                 provider=_provider(layout=_layout_with([{'reason': 'no path given'}])))


def test_the_current_layout_prompt_asks_for_objects():
    from app.agents.reasoning import prompt_catalogue, version_key
    latest = max((p for p in prompt_catalogue() if p['name'] == LAYOUT),
                 key=lambda p: version_key(p['version']))
    assert '"omitted":[{"path":...,"reason":...}]' in latest['template']


# ---- a failed gate is a result, not a failed run -------------------------------------------

def _no_build_file_layout():
    """A layout with no pom.xml or build.gradle anywhere: PROJECT_STRUCTURE must fail.

    Not a leaked secret, which would be the obvious way to fail the gate: the
    gateway's own response DLP refuses an answer containing a credential
    assignment before it can reach the gate at all.
    """
    return {'root': ROOT,
            'files': [{'path': p, 'purpose': why, 'skill_reference': 'references/structural-conventions.md'}
                      for p, why in WANTED if not p.endswith('pom.xml')],
            'omitted': []}


def test_a_failed_gate_completes_the_run_and_hands_the_project_to_feedback(
        repo, job, params, gateway, dispatch):
    """The feedback agent exists to repair exactly this, and was refused for it.

    code-generation used to return FAILED here. Its run was recorded FAILED
    while the pipeline showed the step green, and feedback then failed with
    agent_dependency_not_satisfied:code-generation.
    """
    from app.services.agents import AgentRegistry
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params, provider=_provider(layout=_no_build_file_layout()))

    assert outcome['metrics']['quality_status'] == 'FAIL', 'the fixture must actually fail the gate'
    assert outcome['metrics']['failed_checks'] == ['PROJECT_STRUCTURE'], 'and for the reason intended'
    assert outcome['status'] == 'COMPLETED' and outcome['error'] == ''
    assert outcome['metrics']['repair_by'] == 'feedback'
    assert outcome['metrics']['failed_checks'], 'and it says which checks failed'
    run = repo.one("select status from agent_runs where job_id=? and agent_id='code-generation'", (job['id'],))
    assert run['status'] == 'COMPLETED'
    # The gate's verdict is recorded, not softened.
    assert repo.one('select status from quality_runs where job_id=?', (job['id'],))['status'] == 'FAIL'
    assert AgentRegistry(repo).dependency_status(job['id'], 'feedback')['outstanding'] == []


def test_a_passing_gate_asks_for_no_repair(repo, job, params, gateway, dispatch):
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params, provider=_provider())
    assert outcome['metrics']['quality_status'] != 'FAIL' and outcome['metrics']['repair_by'] == ''


# ---- PROJECT_STRUCTURE understands the layout the skill prescribes ---------------------------
#
# The check wanted a pom.xml at the project root. The skill prescribes a
# repository root holding module directories (<service>-parent/pom.xml,
# <service>-web/pom.xml) and no pom.xml of its own, so every skill-compliant
# project failed the gate -- the 07:47 run's quality_gate_failed.

def _structure(repo, job, tmp_path, files):
    from app.services.migration_lifecycle_v6 import MappingAndPlanningService
    root = tmp_path / 'proj'
    root.mkdir(exist_ok=True)
    for rel in files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('x', encoding='utf-8')
    result = MappingAndPlanningService(repo).quality_validate(job['id'], 'plan-1', str(root))
    return {c['name']: c['status'] for c in result['checks']}['PROJECT_STRUCTURE']


def test_a_multi_module_layout_with_no_root_pom_passes(repo, job, tmp_path):
    assert _structure(repo, job, tmp_path,
                      ['svc-parent/pom.xml', 'svc-web/pom.xml', 'Dockerfile']) == 'PASS'


def test_a_flat_project_with_a_root_pom_still_passes(repo, job, tmp_path):
    assert _structure(repo, job, tmp_path, ['pom.xml']) == 'PASS'


def test_a_gradle_module_passes(repo, job, tmp_path):
    assert _structure(repo, job, tmp_path, ['svc/build.gradle']) == 'PASS'


def test_a_project_with_no_build_file_anywhere_still_fails(repo, job, tmp_path):
    assert _structure(repo, job, tmp_path, ['svc-web/src/main/java/App.java', 'Dockerfile']) == 'FAIL'


def test_a_build_file_buried_deep_does_not_count(repo, job, tmp_path):
    """Modules sit directly under the root; a pom three levels down is not one."""
    assert _structure(repo, job, tmp_path, ['a/b/c/pom.xml']) == 'FAIL'


def test_paths_that_repeat_the_root_are_not_nested_a_second_time(repo, job, params, gateway, dispatch):
    """The model sometimes writes the root into every path as well as naming it: the project must not end up
    at <root>/<root>/, where the gate (run on <root>) finds no pom.xml."""
    layout = {'root': ROOT,
              'files': [{'path': ROOT + '/' + p, 'purpose': why,
                         'skill_reference': 'references/structural-conventions.md'} for p, why in WANTED],
              'omitted': []}
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params, provider=_provider(layout=layout))
    root = Path(outcome['outputs']['source_code'])
    assert (root / 'cvs-v1-validatebridgesupply-oci-parent/pom.xml').is_file()
    assert (root / 'Dockerfile').is_file()
    assert not (root / ROOT).exists()
    manifest = json.loads((root / 'generated-manifest.json').read_text())
    assert all(not m['path'].startswith(ROOT) for m in manifest)


SERVICE = 'cvs-v1-validatebridgesupply-oci-web/src/main/java/com/cvshealth/eip/microservices/service/BridgeService.java'
PROPS = 'config/config-map/application.dev-pbmaz.properties'


def _gap_provider(revise_works=True):
    """A model that writes the circuit-breaker properties and a web pom using ${springdoc.version} it never
    defines, and writes the breaker branch only when asked to revise the service class."""
    def answer(payload):
        if 'purpose' in payload:
            path = payload['path']
            if payload.get('current_content') is not None:
                body = payload['current_content']
                return {'path': path, 'content': body + ('// isCircuitBreakerError() -> 408\n' if revise_works else '')}
            if path == PROPS:
                return {'path': path, 'content': 'application.circuitbreaker.failure-rate=50\n'}
            if path.endswith('-web/pom.xml'):
                return {'path': path, 'content': '<project><dependencies><dependency><artifactId>springdoc'
                        '</artifactId><version>${springdoc.version}</version></dependency></dependencies></project>\n'}
            if path.endswith('-parent/pom.xml'):
                return {'path': path, 'content': '<project><packaging>pom</packaging><properties>'
                        '<java.version>21</java.version></properties></project>\n'}
            return {'path': path, 'content': _content(path)}
        return {'root': ROOT, 'omitted': [], 'files': [
            {'path': p, 'purpose': why, 'skill_reference': 'references/structural-conventions.md'}
            for p, why in WANTED + [(SERVICE, 'service'), (PROPS.replace('config/', 'config/'), 'props')]
            if p != PROPS or True]}
    return ArtefactProvider(answer)


def _generate(job, params, gateway, dispatch, provider):
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    return dispatch(job['id'], 'code-generation', params, provider=provider)


def test_a_property_the_pom_uses_but_never_defines_is_added_to_the_parent(repo, job, params, gateway, dispatch):
    outcome = _generate(job, params, gateway, dispatch, _gap_provider())
    root = Path(outcome['outputs']['source_code'])
    parent = (root / 'cvs-v1-validatebridgesupply-oci-parent/pom.xml').read_text()
    assert '<springdoc.version>' in parent


def test_missing_circuit_breaker_branch_is_authored_during_generation(repo, job, params, gateway, dispatch):
    outcome = _generate(job, params, gateway, dispatch, _gap_provider())
    root = Path(outcome['outputs']['source_code'])
    assert 'isCircuitBreakerError' in (root / SERVICE).read_text()


def test_a_service_that_still_lacks_the_branch_does_not_fail_generation(repo, job, params, gateway, dispatch):
    outcome = _generate(job, params, gateway, dispatch, _gap_provider(revise_works=False))
    root = Path(outcome['outputs']['source_code'])
    assert 'isCircuitBreakerError' not in (root / SERVICE).read_text()


def test_a_class_the_project_imports_and_never_wrote_is_written(repo, job, params, gateway, dispatch):
    """The model imported its own request model and the layout left it out: the build could not compile."""
    model_pkg = 'cvs-v1-validatebridgesupply-oci-web/src/main/java/com/cvshealth/eip/microservices/swagger/model/request/'
    layout = {'root': ROOT, 'omitted': [], 'files': [
        {'path': p, 'purpose': why, 'skill_reference': 'references/structural-conventions.md'}
        for p, why in WANTED + [(model_pkg + 'BeneficiaryKeyRequest.java', 'existing model')]]}
    controller = WANTED[3][0]

    def content(path):
        if path.endswith('BeneficiaryKeyRequest.java'):
            return 'package com.cvshealth.eip.microservices.swagger.model.request;\npublic class BeneficiaryKeyRequest {}\n'
        if path == controller:
            return ('package com.cvshealth.eip.microservices.controller;\n'
                    'import com.cvshealth.eip.microservices.swagger.model.request.BridgeRequest;\n'
                    'public class ValidateBridgeSupplyController {}\n')
        return _content(path)

    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params,
                       provider=_provider(layout=layout, content=content))
    root = Path(outcome['outputs']['source_code'])
    written = root / model_pkg / 'BridgeRequest.java'
    # The path is past Windows' 260-character limit, so it is read through the same prefix the writer uses.
    assert _long(written).is_file(), 'the imported class is written in the package the project declares'
    assert _long(written).read_text().startswith('package ')


def test_contracts_are_written_in_the_first_wave_and_consumers_get_their_real_signatures(repo, job, params, gateway, dispatch):
    """The Api interface is written first; the controller that implements it is written second with the Api's signatures."""
    api = WANTED[3][0].replace('ValidateBridgeSupplyController.java', 'ValidateBridgeApi.java')
    controller = WANTED[3][0]
    layout = {'root': ROOT, 'omitted': [], 'files': [
        {'path': p, 'purpose': why, 'skill_reference': 'references/structural-conventions.md'}
        for p, why in WANTED + [(api, 'the interface the controller implements')]]}
    seen = []

    def answer(payload):
        if 'purpose' not in payload:
            return layout
        seen.append((payload['path'], payload))
        if payload['path'] == api:
            return {'path': api, 'content': ('package com.cvshealth.eip.microservices.controller;\n'
                                              'public interface ValidateBridgeApi {\n'
                                              '    String getBridge(String id);\n}\n')}
        if payload['path'] == controller:
            return {'path': controller, 'content': ('package com.cvshealth.eip.microservices.controller;\n'
                                                     'public class ValidateBridgeSupplyController implements ValidateBridgeApi {\n'
                                                     '    public String getBridge(String id) { return id; }\n}\n')}
        return {'path': payload['path'], 'content': _content(payload['path'])}

    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    from tests.agents.conftest import ArtefactProvider as _P
    outcome = dispatch(job['id'], 'code-generation', params, provider=_P(answer))
    assert outcome['status'] == 'COMPLETED'
    paths = [p for p, _ in seen]
    assert paths.index(api) < paths.index(controller), 'the contract is written before the class that implements it'
    consumer = dict(seen)[controller]
    assert consumer['project_contracts']['ValidateBridgeApi']['methods'] == ['String getBridge(String)']
    assert 'project_contracts' not in dict(seen)[api], 'the first wave is written without signatures to read'
