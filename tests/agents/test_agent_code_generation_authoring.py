"""The skill decides the layout, so the parent module actually gets generated.

``springgen.py`` has no knowledge of the skill: it hardcodes a flat
single-module plain-Spring-Boot tree. So a skill prescribing an
``EIPMS_cvs-v1-<service>-oci`` root with separate parent and web Maven modules
could never be honoured, and the one thing it reliably produced was an
``exception`` package that ``mandatory-migration-rules.md`` Rule 10 forbids by
name.

The synthesis call could not fix that either: it is edit-only, and
``accept_edit`` refuses a path that is not already a file. A model correctly
proposing the parent POM had every edit refused while the run still reported
green.

So the layout is now asked for first, and the files are authored one per call.
These tests pin that the model's layout is what lands on disk, that the gate is
no softer for the path that wrote the code, and that an incomplete answer falls
back to a coherent generated project rather than leaving a half-authored tree.
"""
import json
from pathlib import Path

import pytest

from app.agents.code_generation import CodeGenerationAgent
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
        return '<project><modelVersion>4.0.0</modelVersion></project>\n'
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

    assert outcome['metrics']['authored_by'] == 'MODEL'
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


def test_an_incomplete_answer_falls_back_to_a_coherent_project(
        repo, job, params, gateway, dispatch):
    """A tree missing its parent POM does not build; a partial one is worse.

    One empty file is enough to abandon the authored tree, because there is no
    deterministic way to supply the missing one -- springgen's layout is a
    different shape entirely.
    """
    gateway(LAYOUT, model_name=ALIAS)
    gateway(FILE, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params,
                       provider=_provider(omit={'Dockerfile'}))
    assert outcome['metrics']['authored_by'] == 'DETERMINISTIC'
    assert 'incomplete_authorship' in outcome['metrics']['authoring_reason']
    # And what landed is the generated project, whole.
    root = Path(outcome['outputs']['source_code'])
    assert (root / 'pom.xml').is_file()


def test_no_layout_from_the_model_falls_back(repo, job, params, gateway, dispatch):
    gateway(LAYOUT, model_name=ALIAS)
    outcome = dispatch(job['id'], 'code-generation', params,
                       provider=_provider(layout={'files': []}))
    assert outcome['metrics']['authored_by'] == 'DETERMINISTIC'
    assert Path(outcome['outputs']['source_code'], 'pom.xml').is_file()


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
