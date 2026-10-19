"""planning maps constructs with the model, and the hardcoded dict is gone.

``DEFAULT_MAPPINGS`` is a 27-entry dict in ``migration_lifecycle_v6.py``, and
``mapping_key`` resolves a construct by substring-matching its name against
those keys. A construct with no entry becomes an unsupported mapping, which
blocks the plan outright -- however ordinary that construct is. That is the
hardcoding this conversion removed, and the dict is no longer behind the agent
at all: it remains the service backing ``POST /api/v6/mappings``, which the
first test here still pins, but a plan the agent produces is mapped by the model
or not produced.

What does not move is the gate check. G1/G2 approval binding runs through
``planning_inputs`` before the model is asked, and the hashes tying each mapping
to the artefacts it derives from are stamped in ``record_mapping_catalogue`` --
a model-supplied hash would be a guess, and those hashes are what G3 binds.
"""
import json

import pytest

from app.agents.artefact import ArtefactUnavailable
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


def test_the_hardcoded_dict_still_cannot_map_a_vendor_construct(repo, job, vendor_inputs):
    """The baseline this conversion exists to fix, asserted against the service.

    It used to dispatch the agent with no provider, which exercised the dict
    through the fallback. There is no fallback now, so the claim is made where
    the dict still lives: ``compute_mapping_catalogue`` is what
    ``POST /api/v6/mappings`` calls, and for this flow it produces an
    unsupported entry and no plan -- however ordinary ComIbmAggregateControl is.

    Kept rather than deleted because it is the reason the model path exists. If
    this ever started passing, the argument for model-authored mapping would
    have changed and somebody should notice.
    """
    from app.services.migration_lifecycle_v6 import MappingAndPlanningService
    computed = MappingAndPlanningService(repo).compute_mapping_catalogue(
        job['id'], vendor_inputs['framework_id'], vendor_inputs['analysis_id'],
        vendor_inputs['specification_id'])
    assert VENDOR in {x['ace_construct'] for x in computed['unsupported']}
    assert VENDOR not in {m['ace_construct'] for m in computed['mappings']}


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
    assert outcome['reasoning']['artefact_source'] == 'MODEL'
    assert outcome['metrics']['model_attempts'] == 1
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


def test_a_mapping_missing_its_required_fields_fails_the_agent(repo, job, vendor_inputs,
                                                               gateway, dispatch):
    """An incomplete mapping must not reach the catalogue a plan is built from.

    It is re-asked twice with the violation and then the agent fails. Nothing
    is stored: a catalogue is what G3 binds, so a half-formed one is worse than
    none, and the substituted deterministic catalogue this used to produce
    claimed a mapping the model never made.
    """
    gateway(PROMPT, model_name=ALIAS)
    with pytest.raises(ArtefactUnavailable) as raised:
        dispatch(job['id'], 'planning', vendor_inputs,
                 provider=ArtefactProvider(
                     lambda p: {'mappings': [{'source_reference': 'Vendor.msgflow#in'}]}))
    assert 'model_response_schema_invalid' in raised.value.reason
    assert raised.value.attempts == 3
    assert repo.one('select count(*) n from mapping_catalogues where job_id=?',
                    (job['id'],))['n'] == 0


def test_the_failed_run_is_recorded_as_failed(repo, job, vendor_inputs, gateway, dispatch):
    """A model outage has to be visible on the run, not only in a traceback.

    This is the property the fallback destroyed: the step used to complete, so
    ``agent_runs`` showed a successful planning run against a catalogue the
    model never produced.
    """
    gateway(PROMPT, model_name=ALIAS)
    with pytest.raises(ArtefactUnavailable):
        dispatch(job['id'], 'planning', vendor_inputs,
                 provider=ArtefactProvider(lambda p: {'nonsense': True}))
    run = repo.one("select status,error from agent_runs where job_id=? and agent_id='planning'"
                   ' order by started_at desc limit 1', (job['id'],))
    assert run['status'] == 'FAILED'
    assert 'artefact_unavailable:agent/planning/mapping-catalogue' in run['error']
    assert 'model_response_schema_invalid' in run['error']


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
