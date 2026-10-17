"""The skill is the primary source of standards for every agent, mechanically.

Two failures in this area are silent, which is why they are checked by
enumeration rather than left to per-agent tests.

**A reference an agent names but its skill does not carry.** ``Skill.focus``
and ``focus_many`` raise ``KeyError``, every caller catches it -- correctly,
because guidance must not block a run -- and the agent proceeds on the
*unfocused* skill. The narrowing stops happening and nothing reports it. This
had actually happened: ace-analysis named ``references/esql-translation.md``,
which no skill contains, so every ESQL file was read with the whole SKILL.md
instead of the construct-mapping reference.

**An agent whose skill never reaches the provider at all.** ``ReasoningGateway``
passes the agent's skill only to an adapter declaring ``accepts_skill``, and
substitutes the agent's own skill when a caller supplies none. If that fallback
regressed, agents would reason with no standards attached and every run would
still look healthy.
"""
import json
import re
from pathlib import Path

import pytest

from app.agents.skills import library, skill_for

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = json.loads((ROOT / 'config' / 'agent-catalogue.v1.json').read_text(encoding='utf-8'))
AGENT_IDS = sorted(a['agent_id'] for a in CATALOGUE['agents'])

#: Any ``'...md'`` literal in an agent module is taken to be a skill reference.
_REFERENCE = re.compile(r"'(references/[\w./-]+\.md|[\w-]+\.md)'")

#: Modules that are infrastructure rather than agents: they name reference
#: files in prose or defaults, not as a focus target for their own skill.
_NOT_AGENTS = {'reasoning', 'skills', 'base', 'cli', 'artefact', '__init__'}


def _agent_modules():
    for path in sorted((ROOT / 'app' / 'agents').glob('*.py')):
        if path.stem in _NOT_AGENTS:
            continue
        yield path.stem.replace('_', '-'), path


@pytest.mark.parametrize('agent_id,path', list(_agent_modules()),
                         ids=[a for a, _ in _agent_modules()])
def test_every_reference_an_agent_names_exists_in_its_own_skill(agent_id, path):
    """Otherwise focus_many raises, the caller swallows it, and narrowing stops."""
    named = sorted(set(_REFERENCE.findall(path.read_text(encoding='utf-8'))))
    if not named:
        pytest.skip('%s names no skill reference' % agent_id)
    skill = skill_for(agent_id)
    if skill is None:
        pytest.fail('%s names %s but has no skill' % (agent_id, named))
    missing = []
    for name in named:
        try:
            skill.reference(name)
        except KeyError:
            missing.append(name)
    assert missing == [], (
        '%s names reference(s) its skill does not carry: %s. focus()/focus_many() '
        'would raise, the caller would fall back to the unfocused skill, and the '
        'narrowing would silently stop. Available: %s'
        % (agent_id, missing, sorted(skill.references)))


def test_every_catalogue_agent_either_has_a_skill_or_says_why_not():
    """A skill-less agent must be a recorded decision, not an oversight."""
    lib = library()
    unexplained = []
    for agent_id in AGENT_IDS:
        if lib.has(agent_id):
            continue
        if not (lib.absence_reason(agent_id) or '').strip():
            unexplained.append(agent_id)
    assert unexplained == [], (
        'no skill and no recorded reason: %s -- see agent-skills/README.md' % unexplained)


@pytest.mark.parametrize('agent_id', AGENT_IDS)
def test_a_skill_that_exists_loads_and_verifies(agent_id):
    """A skill whose files no longer match the manifest is an integrity failure."""
    lib = library()
    if not lib.has(agent_id):
        pytest.skip('%s has no skill by design' % agent_id)
    skill = skill_for(agent_id)
    assert skill is not None and skill.content_hash
    assert skill.text.strip(), '%s has an empty SKILL.md' % agent_id
    assert skill.provenance()['skill_hash'] == skill.content_hash


def test_the_manifest_and_the_tree_agree():
    """``skills-map.json`` is hash-verified; drift means a silently altered standard."""
    assert library().verify() is not None


@pytest.mark.parametrize('agent_id', AGENT_IDS)
def test_focusing_on_any_of_a_skills_own_references_keeps_skill_md(agent_id):
    """``focus_many`` must add to the standards, never replace them.

    ``focus`` deliberately drops SKILL.md -- it is for a step whose guidance
    *is* one reference. ``focus_many`` is what agents use to narrow while
    keeping the mandatory rules, so a regression that dropped SKILL.md would
    remove exactly the part that is not negotiable.
    """
    lib = library()
    if not lib.has(agent_id):
        pytest.skip('%s has no skill by design' % agent_id)
    skill = skill_for(agent_id)
    if not skill.references:
        pytest.skip('%s has no references to focus on' % agent_id)
    first = sorted(skill.references)[0]
    focused = skill.focus_many([first])
    assert skill.text.strip()[:60] in focused.text, 'SKILL.md must survive focus_many'
    assert skill.reference(first).strip()[:60] in focused.text
    assert focused.content_hash != skill.content_hash
    assert focused.provenance()['skill_focus'] == first
