"""How hard the model reasons is chosen per call, not only per alias.

Reasoning is billed against ``max_tokens`` but is not in the reply. The code
generation alias runs at ``xhigh``, and a project layout of 15,096 characters
spent all 32,768 tokens before the answer finished -- a file list, cut off.
"""
import pytest

from app.agents import artefact
from app.agents.artefact import (ArtefactUnavailable, LISTING_EFFORT, STRUCTURED_EFFORT,
                                 lower_effort, produce)
from app.agents.dormancy_intelligence import DormancyIntelligenceAgent

PROMPT = DormancyIntelligenceAgent.PROMPT
VALID = {'classification': 'ACTIVE', 'rationale': 'traffic observed'}


class _TakesEffort:
    accepts_skill = True
    accepts_effort = True

    def __init__(self):
        self.efforts = []

    def invoke(self, template, payload, skill=None, effort=None):
        self.efforts.append(effort)
        return VALID


class _Predates:
    """A provider written against invoke(template, payload) only."""

    def __init__(self):
        self.calls = 0

    def invoke(self, template, payload):
        self.calls += 1
        return VALID


def _context(repo, job, provider):
    return DormancyIntelligenceAgent(repo).context(job['id'], provider=provider)


def test_the_effort_asked_for_reaches_a_provider_that_takes_one(repo, job, gateway):
    gateway(PROMPT)
    provider = _TakesEffort()
    _, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1}, effort='low')
    assert provider.efforts == ['low'] and provenance['effort'] == 'low'


def test_no_effort_means_the_provider_decides(repo, job, gateway):
    gateway(PROMPT)
    provider = _TakesEffort()
    produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1})
    assert provider.efforts == [None]


def test_a_provider_that_predates_effort_is_never_handed_it(repo, job, gateway):
    """The same guarantee `skill` has: older doubles and adapters keep working."""
    gateway(PROMPT)
    provider = _Predates()
    _, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1}, effort='low')
    assert provider.calls == 1 and 'effort' not in provenance


def test_the_levels_are_ones_the_gateway_accepts():
    """Measured against the real gateway: low and medium are accepted, xhigh is the alias's own."""
    assert LISTING_EFFORT == 'low' and STRUCTURED_EFFORT == 'medium'


# ---- the call sites ---------------------------------------------------------------------------

def _recording(model):
    """Wrap the suite's double so each prompt's effort is recorded."""
    seen = {}

    class Recorder:
        accepts_skill = True
        accepts_effort = True

        def invoke(self, template, payload, skill=None, effort=None):
            from tests.agents.model_double import PROMPT_BY_TEMPLATE
            seen.setdefault(PROMPT_BY_TEMPLATE.get(template, template), set()).add(effort)
            return model.invoke(template, payload, skill=skill)

    return Recorder(), seen


def test_planning_the_specification_and_suggestions_use_structured_effort(
        repo, job, ace_repo, framework_repo, dispatch, model):
    recorder, seen = _recording(model)
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)}, provider=recorder)
    analysis = dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)}, provider=recorder)
    dispatch(job['id'], 'migration-specification',
             {'analysis_id': analysis['outputs']['analysis_document']}, provider=recorder)
    assert seen['agent/migration-specification/section'] == {STRUCTURED_EFFORT}


def test_analysis_keeps_the_aliases_own_effort(repo, job, ace_repo, dispatch, model):
    """Reading source is analysis, not transformation: it is not turned down."""
    recorder, seen = _recording(model)
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)}, provider=recorder)
    dispatch(job['id'], 'ace-analysis', {'path': str(ace_repo)}, provider=recorder)
    assert seen['agent/ace-analysis/semantic-model'] == {None}


def test_planning_maps_at_structured_effort(repo, job, approved_inputs, model):
    from app.agents.planning import PlanningAgent
    recorder, seen = _recording(model)
    PlanningAgent(repo).run(job['id'], params=approved_inputs, provider=recorder)
    assert seen['agent/planning/mapping-catalogue'] == {STRUCTURED_EFFORT}


# ---- a reply cut off after reasoning is retried with less of it ------------------------------

CUT_OFF = ('provider_response_truncated:max_tokens=32768: the answer was cut off mid-JSON '
           '(8791 chars; output_tokens=32768, blocks=text:8791,thinking:0 -- the model reasoned '
           'before answering; its text is omitted but its tokens count against max_tokens)')


class _CutOffThenFine(_TakesEffort):
    def __init__(self, cut_offs):
        super().__init__()
        self.cut_offs = cut_offs

    def invoke(self, template, payload, skill=None, effort=None):
        self.efforts.append(effort)
        if len(self.efforts) <= self.cut_offs:
            raise RuntimeError(CUT_OFF)
        return VALID


@pytest.mark.parametrize('start,expected', [
    (None, [None, 'medium']),
    ('xhigh', ['xhigh', 'high']),
    ('high', ['high', 'medium']),
])
def test_a_reply_cut_off_after_reasoning_is_retried_one_rung_down(repo, job, gateway, start, expected):
    """The request changed, so unlike a plain retry it can succeed -- and did not take 30 minutes."""
    gateway(PROMPT)
    provider = _CutOffThenFine(cut_offs=1)
    _, provenance = produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1}, effort=start)
    assert provider.efforts == expected
    assert provenance['attempts'] == 2 and provenance['effort_lowered']


def test_it_keeps_stepping_down_until_it_fits(repo, job, gateway):
    gateway(PROMPT)
    provider = _CutOffThenFine(cut_offs=2)
    produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1}, effort='high')
    assert provider.efforts == ['high', 'medium', 'low']


def test_at_the_bottom_rung_there_is_nothing_left_to_lower(repo, job, gateway):
    gateway(PROMPT)
    provider = _CutOffThenFine(cut_offs=5)
    with pytest.raises(ArtefactUnavailable) as raised:
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1}, effort='low')
    assert provider.efforts == ['low'] and raised.value.attempts == 1


def test_a_plain_truncation_with_no_reasoning_is_still_not_retried(repo, job, gateway):
    """Nothing to lower: the same request would be cut off the same way."""
    gateway(PROMPT)

    class _Plain(_TakesEffort):
        def invoke(self, template, payload, skill=None, effort=None):
            self.efforts.append(effort)
            raise RuntimeError('provider_response_truncated:max_tokens=32768: cut off (9000 chars)')

    provider = _Plain()
    with pytest.raises(ArtefactUnavailable):
        produce(_context(repo, job, provider), PROMPT, {'traffic_count': 1}, effort='high')
    assert provider.efforts == ['high']


@pytest.mark.parametrize('effort,lower', [(None, 'medium'), ('xhigh', 'high'), ('high', 'medium'),
                                          ('medium', 'low'), ('low', None), ('nonsense', None)])
def test_the_ladder(effort, lower):
    assert lower_effort(effort) == lower
