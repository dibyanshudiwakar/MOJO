"""planning maps constructs with the model instead of a hardcoded dict.

``DEFAULT_MAPPINGS`` is a 27-entry dict in ``migration_lifecycle_v6.py``, and
``mapping_key`` resolves a construct by substring-matching its name against
those keys. A construct with no entry becomes an unsupported mapping, which
blocks the plan outright -- however ordinary that construct is. That is the
hardcoding this conversion removes: the model maps, and the dict is the
fallback.

What does not move is the gate check. G1/G2 approval binding runs through
``planning_inputs`` before either path, and the hashes tying each mapping to
the artefacts it derives from are stamped in ``record_mapping_catalogue`` --
a model-supplied hash would be a guess, and those hashes are what G3 binds.
"""
import json

import pytest

from tests.agents.conftest import ArtefactProvider

PROMPT = 'agent/planning/mapping-catalogue'
#: planning's default_model_alias; AT-13 refuses the dispatch under any other.
ALIAS = 'reasoning-large'

#: A real ACE node that no DEFAULT_MAPPINGS key matches. ComIbmAggregateControl
#: is an ordinary IIB aggregation node, not an exotic one -- which is the point:
#: mapping_key resolves by substring over 27 dict keys, so a construct whose
#: name happens not to contain one of them is unsupported and blocks the plan.
#: (VendorCustomRouter, the obvious choice, is a bad example: it contains
#: "Router", so the substring match maps it to Route by accident.)
VENDOR = 'ComIbmAggregateControl.msgnode'


@pytest.fixture
def vendor_inputs(repo, job, tmp_path, dispatch, approve, framework_repo):
    """Approved analysis and specification for a flow with an unmappable node."""
    root = tmp_path / 'vendor-ace'
    root.mkdir()
    (root / 'Vendor.msgflow').write_text(
        '<flow><node id="in" type="ComIbmWSInput.msgnode" URLSpecifier="/v"'
        ' messageDomainProperty="JSON"/>'
        '<node id="route" type="%s"/>'
        '<connection source="in" target="route"/></flow>' % VENDOR)
    dispatch(job['id'], 'repository-intake', {'path': str(root)})
    framework = dispatch(job['id'], 'framework-baseline', {'path': str(framework_repo)})
    analysis = dispatch(job['id'], 'ace-analysis', {'path': str(root)})
    specification = dispatch(job['id'], 'migration-specification',
                             {'analysis_id': analysis['outputs']['analysis_document']})
    for gate, outcome in (('G1', framework), ('G2', analysis), ('G2', specification)):
        ref = outcome['evidence'][0]
        approve(job['id'], gate, 'APPROVE', ref['id'], ref['content_hash'])
    return {'framework_id': framework['outputs']['framework_template'],
            'analysis_id': analysis['outputs']['analysis_document'],
            'specification_id': specification['outputs']['migration_specification_yaml'],
            'version': '1.0.0'}


def test_the_hardcoded_dict_cannot_map_a_vendor_construct(repo, job, vendor_inputs, dispatch):
    """The baseline this conversion exists to fix.

    No provider, so the deterministic path runs: the vendor node has no
    DEFAULT_MAPPINGS entry, so it lands in unsupported and no plan is produced.
    """
    outcome = dispatch(job['id'], 'planning', vendor_inputs)
    assert outcome['status'] == 'CLARIFICATION_REQUIRED'
    assert outcome['metrics']['unsupported_count'] >= 1
    assert outcome['outputs']['plan'] == ''
    assert outcome['metrics']['catalogue_source'] == 'DETERMINISTIC'
    unsupported = repo.rows('select ace_construct from unsupported_mappings where job_id=?',
                            (job['id'],))
    assert VENDOR in {x['ace_construct'] for x in unsupported}


def test_the_model_maps_what_the_dict_could_not_and_the_plan_proceeds(
        repo, job, vendor_inputs, gateway, dispatch):
    gateway(PROMPT, model_name=ALIAS)
    catalogue = {'mappings': [
        {'source_reference': 'Vendor.msgflow#in', 'ace_construct': 'ComIbmWSInput.msgnode',
         'spring_pattern': 'SPRING_MVC_CONTROLLER', 'target_module': 'web',
         'boundary': 'GENERATED', 'tests': ['contract']},
        {'source_reference': 'Vendor.msgflow#route', 'ace_construct': VENDOR,
         'spring_pattern': 'ROUTER_STRATEGY', 'target_module': 'service',
         'boundary': 'HAND_WRITTEN_PROTECTED', 'tests': ['routing_matrix'],
         'rationale': 'vendor router selects a downstream branch'},
    ], 'unsupported': [], 'risks': []}
    outcome = dispatch(job['id'], 'planning', vendor_inputs,
                       provider=ArtefactProvider(lambda p: catalogue))

    assert outcome['reasoning']['mode'] == 'MODEL_GATEWAY'
    assert outcome['metrics']['catalogue_source'] == 'MODEL'
    assert outcome['metrics']['unsupported_count'] == 0
    # With nothing unsupported the plan is produced, which the deterministic
    # path could not reach for this flow.
    assert outcome['outputs']['plan']
    stored = json.loads(repo.one('select mappings from mapping_catalogues where id=?',
                                 (outcome['outputs']['mapping'],))['mappings'])
    assert {m['ace_construct'] for m in stored} == {'ComIbmWSInput.msgnode', VENDOR}


def test_the_artefact_hashes_are_stamped_not_taken_from_the_model(
        repo, job, vendor_inputs, gateway, dispatch):
    """G3 binds these hashes, so a model-supplied one would be a guess."""
    gateway(PROMPT, model_name=ALIAS)
    catalogue = {'mappings': [
        {'source_reference': 'Vendor.msgflow#in', 'ace_construct': 'ComIbmWSInput.msgnode',
         'spring_pattern': 'SPRING_MVC_CONTROLLER', 'target_module': 'web',
         'boundary': 'GENERATED',
         # The model claims hashes; they must be overwritten with the real ones.
         'analysis_hash': 'deadbeef', 'framework_hash': 'deadbeef',
         'specification_hash': 'deadbeef'},
    ], 'unsupported': [], 'risks': []}
    outcome = dispatch(job['id'], 'planning', vendor_inputs,
                       provider=ArtefactProvider(lambda p: catalogue))
    stored, = json.loads(repo.one('select mappings from mapping_catalogues where id=?',
                                  (outcome['outputs']['mapping'],))['mappings'])
    analysis = repo.one('select content_hash from ace_analyses where id=?',
                        (vendor_inputs['analysis_id'],))
    assert stored['analysis_hash'] == analysis['content_hash'] != 'deadbeef'
    assert stored['framework_hash'] != 'deadbeef'
    assert stored['specification_hash'] != 'deadbeef'


def test_a_mapping_missing_its_required_fields_falls_back(repo, job, vendor_inputs,
                                                          gateway, dispatch):
    """An incomplete mapping must not reach the catalogue a plan is built from."""
    gateway(PROMPT, model_name=ALIAS)
    outcome = dispatch(job['id'], 'planning', vendor_inputs,
                       provider=ArtefactProvider(
                           lambda p: {'mappings': [{'source_reference': 'Vendor.msgflow#in'}]}))
    assert outcome['reasoning']['mode'] == 'DETERMINISTIC_FALLBACK'
    assert 'model_response_schema_invalid' in outcome['reasoning']['reason']
    assert outcome['metrics']['catalogue_source'] == 'DETERMINISTIC'
    # And the deterministic outcome for this flow: still blocked.
    assert outcome['status'] == 'CLARIFICATION_REQUIRED'


def test_the_gate_check_runs_on_the_model_path_too(repo, job, tmp_path, dispatch, gateway,
                                                   framework_repo, ace_repo):
    """An unapproved analysis is refused whichever path maps it."""
    from app.services.policy import PolicyError
    gateway(PROMPT, model_name=ALIAS)
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    analysis = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)})
    specification = dispatch(job['id'], 'migration-specification',
                             {'analysis_id': analysis['outputs']['analysis_document']})
    with pytest.raises((PermissionError, PolicyError)):
        dispatch(job['id'], 'planning',
                 {'framework_id': '', 'analysis_id': analysis['outputs']['analysis_document'],
                  'specification_id': specification['outputs']['migration_specification_yaml'],
                  'version': '1.0.0'},
                 provider=ArtefactProvider(lambda p: {'mappings': []}))
