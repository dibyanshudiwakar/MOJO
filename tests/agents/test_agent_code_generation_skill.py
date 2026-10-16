"""The skill reaches the provider on code-generation's synthesis call.

Business-logic synthesis is the step that writes real logic into generated
source, and the skill is where the 13 mandatory rules, the structural
conventions and the backend-specific connector patterns live. Guidance that
never arrives is a silent failure: the model still answers, the edits still
apply, and the result is merely wrong in the ways the skill exists to prevent.

Two paths matter. When the detected backend type names a connector reference,
the skill is narrowed to SKILL.md plus structural-conventions plus that
reference. When it does not -- UNDETERMINED is the common case before any
backend has been identified -- the agent passes no skill, and what must then
happen is that ``ReasoningGateway`` substitutes the agent's full skill rather
than sending none at all.
"""
import json

import pytest

from app.agents.code_generation import CodeGenerationAgent
from app.agents.skills import skill_for
from tests.agents.conftest import SkillAwareArtefactProvider

PROMPT = CodeGenerationAgent.BUSINESS_LOGIC_PROMPT
#: code-generation's default_model_alias; AT-13 refuses the dispatch otherwise.
ALIAS = 'coding'


@pytest.fixture
def synthesise(repo, job, planned, tmp_path, gateway):
    """Run the agent with the synthesis stage on, under a forced backend type."""
    def _run(backend, answer=None):
        gateway(PROMPT, model_name=ALIAS)
        row = repo.one('select id,semantic_model from ace_analyses where job_id=?', (job['id'],))
        model = json.loads(row['semantic_model'])
        model['backend_type'] = {'type': backend, 'signal': 'forced by test'}
        with repo.connect() as c:
            c.execute('update ace_analyses set semantic_model=? where id=?',
                      (json.dumps(model, sort_keys=True), row['id']))
        provider = SkillAwareArtefactProvider(answer or (lambda payload: {'edits': []}))
        result = CodeGenerationAgent(repo).run(job['id'], params={
            'plan_id': planned['plan_id'],
            'output_root': str(tmp_path / ('out-' + backend.lower())),
            'service_name': 'orders', 'package_name': 'com.example.orders',
            'synthesise_business_logic': True}, provider=provider)
        return provider, result
    return _run


def _synthesis_skills(provider):
    """The skills passed on synthesis calls, ignoring the triage call."""
    return [skill for call, skill in zip(provider.calls, provider.skills)
            if 'stub_files' in (call['payload'] or {})]


def test_a_known_backend_focuses_the_skill_on_its_connector_reference(synthesise):
    provider, _ = synthesise('REST_EXTERNAL_DIRECT')
    skills = _synthesis_skills(provider)
    if not skills:
        pytest.skip('no stub bodies to synthesise in this fixture project')
    skill, = skills[:1]
    assert skill is not None, 'the skill must reach the provider'
    assert skill.focused_on == 'structural-conventions.md+rest-external-direct.md'
    # SKILL.md's own guidance is kept, not replaced: focus_many combines them,
    # and the mandatory rules live in SKILL.md.
    full = skill_for('code-generation')
    assert full.text.strip()[:80] in skill.text
    assert 'rest-external-direct' in skill.focused_on
    # The hash recorded on the run is the focused skill's, not the whole one's.
    assert skill.provenance()['skill_hash'] != full.content_hash


def test_an_undetermined_backend_still_sends_the_agents_full_skill(synthesise):
    """The case with no connector reference must not send no guidance.

    The agent passes skill=None here, and ReasoningGateway's fallback is what
    substitutes the agent's own skill. If that fallback regressed, synthesis
    would run with no mandatory rules at all and nothing would fail.
    """
    provider, _ = synthesise('UNDETERMINED')
    skills = _synthesis_skills(provider)
    if not skills:
        pytest.skip('no stub bodies to synthesise in this fixture project')
    skill = skills[0]
    assert skill is not None, 'an unknown backend must not mean unguided synthesis'
    assert skill.name == 'iib-to-ms-code-generation'
    assert skill.content_hash == skill_for('code-generation').content_hash
    assert not skill.focused_on


def test_the_synthesis_payload_carries_the_real_ace_source_and_the_stubs(synthesise):
    """Without both, the model is being asked to invent the logic."""
    provider, _ = synthesise('REST_EXTERNAL_DIRECT')
    calls = [c for c in provider.calls if 'stub_files' in (c['payload'] or {})]
    if not calls:
        pytest.skip('no stub bodies to synthesise in this fixture project')
    payload = calls[0]['payload']
    assert payload['stub_files'], 'the files whose bodies are placeholders'
    assert 'real_source' in payload, 'the ACE source each stub implements'


def test_the_synthesis_stage_actually_runs(synthesise):
    """The regression this file exists for.

    generate_spring returned a hardcoded stub_sources={} from when the
    generator moved to springgen.py, so _synthesize_business_logic returned
    'no_stubs' on every run and the model was never asked for the logic --
    silently, because an empty dict is a valid answer and nothing failed.
    """
    provider, result = synthesise('REST_EXTERNAL_DIRECT')
    outcome = result.metrics.get('business_logic_synthesis')
    assert outcome is not None, 'the stage must report an outcome'
    assert outcome != 'no_stubs', 'the generator must report its placeholder bodies'
    # And it got there by asking the model, with guidance attached.
    assert _synthesis_skills(provider), 'synthesis must reach the provider'


def test_the_generator_names_the_ace_source_each_stub_needs(repo, job, planned, tmp_path):
    """A stub with no source reference is a request the model cannot answer."""
    from app.services.migration_lifecycle_v6 import MappingAndPlanningService
    generated = MappingAndPlanningService(repo).generate_spring(
        job['id'], planned['plan_id'], str(tmp_path / 'stubs'), 'orders', 'com.example.orders')
    stubs = generated['stub_sources']
    assert stubs, 'the fixture flow has an untranslated body'
    for path, refs in stubs.items():
        assert path.endswith('.java'), path
        assert refs, 'every stub names the ACE source its logic comes from: ' + path
