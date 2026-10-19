"""The skill reaches the provider on code-generation's authoring calls.

The skill is where the 13 mandatory rules, the structural conventions and the
backend-specific connector patterns live, and it is now the *only* place: with
``springgen`` no longer reachable from this agent, nothing else knows that a
repository needs a parent POM under an ``EIPMS_`` root or that an ``exception``
package is forbidden. Guidance that never arrives is a silent failure -- the
model still answers, the files still get written, and the result is merely wrong
in the ways the skill exists to prevent.

Two paths matter. When the detected backend type names a connector reference,
the skill is narrowed to SKILL.md plus structural-conventions plus the mandatory
rules plus that reference. When it does not -- UNDETERMINED is the common case
before any backend has been identified -- the agent passes no skill, and what
must then happen is that ``ReasoningGateway`` substitutes the agent's full skill
rather than sending none at all.

This file previously covered business-logic synthesis, which filled in
``springgen``'s TODO transformer bodies. That stage is gone: its only input was
the generator's stub reporting, and a model-authored project has no stubs. The
generator itself still reports them for callers of ``POST /api/v6/*``, which the
last test here pins.
"""
import json

import pytest

from app.agents.code_generation import CodeGenerationAgent
from app.agents.skills import skill_for

LAYOUT = CodeGenerationAgent.LAYOUT_PROMPT
FILE = CodeGenerationAgent.FILE_PROMPT


@pytest.fixture
def author(repo, job, planned, tmp_path, model):
    """Run the agent under a forced backend type, returning the double."""
    def _run(backend):
        row = repo.one('select id,semantic_model from ace_analyses where job_id=?', (job['id'],))
        semantic = json.loads(row['semantic_model'])
        semantic['backend_type'] = {'type': backend, 'signal': 'forced by test'}
        with repo.connect() as c:
            c.execute('update ace_analyses set semantic_model=? where id=?',
                      (json.dumps(semantic, sort_keys=True), row['id']))
        result = CodeGenerationAgent(repo).run(job['id'], params={
            'plan_id': planned['plan_id'],
            'output_root': str(tmp_path / ('out-' + backend.lower())),
            'service_name': 'orders', 'package_name': 'com.example.orders'})
        return model, result
    return _run


def _skills_for(double, prompt):
    return [c['skill'] for c in double.calls if c['prompt'] == prompt]


def test_a_known_backend_focuses_the_skill_on_its_connector_reference(author):
    double, _ = author('REST_EXTERNAL_DIRECT')
    skills = _skills_for(double, LAYOUT)
    assert skills, 'the layout call must reach the provider'
    skill = skills[0]
    assert skill is not None, 'the skill must reach the provider'
    assert skill.focused_on == ('structural-conventions.md+mandatory-migration-rules.md'
                               '+rest-external-direct.md')
    # SKILL.md's own guidance is kept, not replaced: focus_many combines them,
    # and the mandatory rules live in SKILL.md as well as in their reference.
    full = skill_for('code-generation')
    assert full.text.strip()[:80] in skill.text
    # The hash recorded on the run is the focused skill's, not the whole one's.
    assert skill.provenance()['skill_hash'] != full.content_hash


def test_every_file_is_authored_with_the_same_focused_skill(author):
    """One narrowing per run, not per file.

    Each file is its own request, so a regression that lost the focus after the
    first call would leave most of the project authored without the connector
    reference -- and nothing would fail, because the files would still compile.
    """
    double, _ = author('REST_EXTERNAL_DIRECT')
    skills = _skills_for(double, FILE)
    assert len(skills) > 1, 'the fixture plan authors more than one file'
    assert all(s is not None for s in skills)
    assert len({s.focused_on for s in skills}) == 1


def test_an_undetermined_backend_still_sends_the_structural_rules(author):
    """The case with no connector reference must not send no guidance.

    UNDETERMINED is the common state before a backend has been identified, and
    the structural conventions and mandatory rules apply regardless of which
    connector a service eventually turns out to need. If this regressed, a
    project would be authored with no rules at all and nothing would fail --
    the output would compile and simply breach Rule 10.
    """
    double, _ = author('UNDETERMINED')
    skills = _skills_for(double, LAYOUT)
    assert skills, 'the layout call must reach the provider'
    skill = skills[0]
    assert skill is not None, 'an unknown backend must not mean unguided authoring'
    assert skill.focused_on == 'structural-conventions.md+mandatory-migration-rules.md'
    assert 'rest-external-direct' not in skill.focused_on
    # SKILL.md itself still travels with it, which is where the rules live.
    assert skill_for('code-generation').text.strip()[:80] in skill.text


def test_the_layout_payload_carries_what_the_skill_needs_to_decide_structure(author):
    """Without the tasks and the backend type the layout is a guess."""
    double, _ = author('REST_EXTERNAL_DIRECT')
    payload = [c['payload'] for c in double.calls if c['prompt'] == LAYOUT][0]
    assert payload['tasks'], 'the approved plan decides what files exist'
    assert payload['backend_type'] == 'REST_EXTERNAL_DIRECT'
    assert payload['service_name'] == 'orders'
    assert payload['package_name'] == 'com.example.orders'


def test_the_project_is_recorded_as_model_authored(author):
    """The run's provenance describes the authoring, not the quality triage.

    On a passing gate the triage is never made, so recording *its* provenance
    put mode=MODEL_NOT_CONSULTED on a project the model wrote in full.
    """
    double, result = author('REST_EXTERNAL_DIRECT')
    assert result.metrics['file_count'] >= 1
    assert result.metrics['layout_root'] == 'orders-parent'
    assert result.reasoning['artefact_source'] == 'MODEL'
    assert result.reasoning['mode'] == 'MODEL_GATEWAY'
    assert result.reasoning['prompt_name'] == FILE
    # The triage is still on the record, one level down.
    assert result.reasoning['quality_triage']['reason'] == \
        'quality_gate_passed_nothing_to_triage'
    # Every file the layout named was authored; produce_many is all-or-nothing,
    # so a shortfall would have raised rather than written a partial tree.
    assert double.count(FILE) == len(_layout_files(double))


def _layout_files(double):
    from tests.agents.model_double import ANSWERS
    payload = [c['payload'] for c in double.calls if c['prompt'] == LAYOUT][0]
    return ANSWERS[LAYOUT](payload)['files']


def test_the_generator_still_names_the_ace_source_each_stub_needs(repo, job, planned, tmp_path):
    """``springgen`` is unchanged and still reachable as a service.

    The agent no longer calls it -- this is the deterministic generation behind
    ``POST /api/v6/*``. Kept because scrapping the fallback was a change to the
    agents, not a removal of the service, and a stub with no source reference
    would still be a request nobody can answer.
    """
    from app.services.migration_lifecycle_v6 import MappingAndPlanningService
    generated = MappingAndPlanningService(repo).generate_spring(
        job['id'], planned['plan_id'], str(tmp_path / 'stubs'), 'orders', 'com.example.orders')
    stubs = generated['stub_sources']
    assert stubs, 'the fixture flow has an untranslated body'
    for path, refs in stubs.items():
        assert path.endswith('.java'), path
        assert refs, 'every stub names the ACE source its logic comes from: ' + path
