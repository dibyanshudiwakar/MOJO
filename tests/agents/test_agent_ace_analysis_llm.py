"""ace-analysis reads the source with the model, and fails if it cannot.

The agent used to call the gateway and discard the answer into
``metrics['model_ambiguity_count']`` while ``ACEParser`` built the artefact a
G2 approval binds. The model reads each source file now, and the parser is no
longer behind it.

Three things have to hold: the model's artefact is what gets persisted; a
response that does not match its schema is refused rather than stored; and a
model that cannot read one file fails the analysis instead of producing one that
is part model-read and part regex-parsed. That last is a deliberate reversal --
per-file fallback kept a 500-file repository usable, but it also meant nothing
downstream could tell a construct the parser does not understand from one the
model decided was absent.

The hashing test is what makes the rest safe: the stored hash has to be a
function of the stored model alone, or no approval could bind the artefact.
"""
import json

import pytest

from app.agents.ace_analysis import ACEAnalysisAgent
from app.agents.artefact import ArtefactUnavailable
from app.services.enterprise_v5 import ACEParser
from tests.agents.conftest import ArtefactProvider, SkillAwareArtefactProvider

PROMPT = 'agent/ace-analysis/semantic-model'
#: ace-analysis's default_model_alias in config/agent-catalogue.v1.json. The
#: model must be registered under this exact alias or AT-13 refuses the
#: dispatch before the gateway is reached.
ALIAS = 'reasoning-large'


def _fragment(payload):
    """A plausible model answer for one source file.

    Deliberately not a copy of what the parser would produce: the node id is
    the one in the source (it has to be -- everything downstream joins on it),
    but the interface endpoint differs so a test can tell which path ran.
    """
    path = payload['path']
    if path.endswith('.msgflow'):
        return {'flows': [{'path': path,
                           'nodes': [{'id': 'in', 'type': 'ComIbmWSInput.msgnode',
                                      'properties': {'URLSpecifier': '/model-authored'}},
                                     {'id': 'map', 'type': 'ComIbmCompute.msgnode',
                                      'properties': {}}],
                           'connections': [{'from': 'in', 'to': 'map'}]}],
                'interfaces': [{'source': path, 'node': 'in', 'protocol': 'HTTP',
                                'endpoint': '/model-authored'}],
                'ambiguities': []}
    if path.endswith('.esql'):
        return {'esql': [{'path': path, 'modules': ['OrdersMain'],
                          'assignments': [{'target': ['orderId'], 'source': ['id'],
                                           'kind': 'COPY',
                                           'esql': 'SET OutputRoot.JSON.Data.orderId = '
                                                   'InputRoot.JSON.Data.id;'}]}],
                'ambiguities': []}
    return {'schemas': [{'path': path, 'type': 'XSD', 'declarations': ['Order']}]}


@pytest.fixture
def params(ace_repo, job, dispatch):
    """Analysis params, with its one dependency satisfied.

    The registry enforces the catalogue's dependency graph, so ace-analysis
    cannot be dispatched until repository-intake has completed for this job.
    """
    dispatch(job['id'], 'repository-intake', {'path': str(ace_repo)})
    return {'path': str(ace_repo), 'version': '1.0.0'}


def test_the_model_reads_the_source_and_its_artefact_is_what_is_persisted(
        repo, job, params, gateway, dispatch):
    gateway(PROMPT, model_name=ALIAS)
    provider = ArtefactProvider(_fragment)
    outcome = dispatch(job['id'], 'ace-analysis', params, provider=provider)

    assert outcome['reasoning']['mode'] == 'MODEL_GATEWAY'
    assert outcome['reasoning']['artefact_source'] == 'MODEL'
    row = repo.one('select * from ace_analyses where id=?',
                   (outcome['outputs']['analysis_document'],))
    model = json.loads(row['semantic_model'])
    # The endpoint only the model supplied survived into the stored artefact,
    # which is the whole claim: the model authored what the gate will approve.
    assert model['interfaces'][0]['endpoint'] == '/model-authored'
    assert [a['target'] for a in model['esql'][0]['assignments']] == [['orderId']]
    # One request per source file, not one per repository.
    assert len(provider.calls) == len(ACEParser().sources(params['path']))


def test_the_skill_reaches_the_provider_focused_on_the_files_at_hand(
        repo, job, params, gateway, dispatch):
    """Guidance that never arrives is the regression this catches."""
    gateway(PROMPT, model_name=ALIAS)
    provider = SkillAwareArtefactProvider(_fragment)
    dispatch(job['id'], 'ace-analysis', params, provider=provider)
    supplied = [s for s in provider.skills if s is not None]
    assert supplied, 'the agent has a skill and the adapter accepts one'
    assert all(s.name.startswith('iib-to-ms-ace-analysis') for s in supplied)
    # Narrowed to the construct-mapping reference, not the whole SKILL.md.
    # This assertion previously required the bare name, and passed only
    # because FOCUS named a reference no skill carried: focus_many raised,
    # the agent fell back to the unfocused skill, and the narrowing silently
    # never happened.
    assert all(s.focused_on == 'references/construct-mapping.md' for s in supplied)
    # SKILL.md survives the narrowing -- focus_many adds to the standards.
    from app.agents.skills import skill_for
    whole = skill_for('ace-analysis')
    assert all(whole.text.strip()[:60] in s.text for s in supplied)


def test_without_a_provider_no_analysis_is_produced(repo, job, params):
    """What used to be a COMPLETED run with a parsed artefact.

    The parsed analysis was a real reading of the source, which is exactly why
    this was dangerous: a G2 approval bound it, planning mapped it, and nothing
    on the run said the model had never been reached.

    ``provider=None`` explicitly rather than the ``no_model`` marker: the marker
    would also strip the provider from the ``params`` fixture's own
    repository-intake dispatch, so the test would fail in setup on a different
    agent. An explicit None defeats the autouse ``setdefault`` for this call
    only.
    """
    with pytest.raises(ArtefactUnavailable) as raised:
        ACEAnalysisAgent(repo).run(job['id'], params=params, provider=None)
    assert raised.value.reason == 'no_provider_execution_adapter'
    assert repo.one('select count(*) n from ace_analyses where job_id=?',
                    (job['id'],))['n'] == 0


def test_a_response_that_breaks_its_schema_is_not_persisted(repo, job, params, gateway, dispatch):
    """An invalid artefact must be refused, not stored and approved.

    A node with no ``type`` violates the schema. Everything downstream joins on
    those ids, so persisting the fragment would corrupt the mapping catalogue
    and the generated code.
    """
    gateway(PROMPT, model_name=ALIAS)
    provider = ArtefactProvider(lambda p: {'flows': [{'path': p['path'],
                                                      'nodes': [{'id': 'in'}]}]})
    with pytest.raises(ArtefactUnavailable) as raised:
        dispatch(job['id'], 'ace-analysis', params, provider=provider)
    assert 'model_response_schema_invalid' in raised.value.reason
    assert "'type' is a required property" in raised.value.reason
    assert raised.value.attempts == 3
    assert repo.one('select count(*) n from ace_analyses where job_id=?',
                    (job['id'],))['n'] == 0


def test_one_unreadable_file_costs_the_whole_analysis(repo, job, params, gateway, dispatch):
    """All or nothing across the source files, which is a reversal.

    Per-file fallback kept a 500-file repository usable, and that was the
    argument for it. Against it: the merged model then carried some flows the
    model had read and some the parser had, with nothing to say which. A
    construct the regex parser does not understand and a construct the model
    judged absent produce the same artefact, and the mapping catalogue built on
    top of it cannot tell them apart either.

    So a file the model cannot read fails the analysis. The blast radius is a
    repository rather than a file, and it is visible.
    """
    gateway(PROMPT, model_name=ALIAS)

    def answer(payload):
        if payload['path'].endswith('.esql'):
            return {'esql': [{'modules': ['NoPath']}]}      # 'path' is required
        return _fragment(payload)

    with pytest.raises(ArtefactUnavailable) as raised:
        dispatch(job['id'], 'ace-analysis', params, provider=ArtefactProvider(answer))
    assert 'model_response_schema_invalid' in raised.value.reason
    assert repo.one('select count(*) n from ace_analyses where job_id=?',
                    (job['id'],))['n'] == 0


def test_a_model_authored_analysis_hashes_like_a_parsed_one(repo, job, params, gateway, dispatch):
    """Otherwise no single G2 approval could bind both paths' output.

    The two models differ in content, so the hashes differ -- that is expected.
    What must hold is that the hash is a function of the model alone: the stored
    hash equals the digest of the stored semantic model, whichever path produced
    it, because that equality is what makes an approval bind the artefact.
    """
    from app.repository import digest
    gateway(PROMPT, model_name=ALIAS)
    for provider in (ArtefactProvider(_fragment), None):
        outcome = dispatch(job['id'], 'ace-analysis', params, provider=provider)
        row = repo.one('select * from ace_analyses where id=?',
                       (outcome['outputs']['analysis_document'],))
        assert row['content_hash'] == digest(row['semantic_model'])
        # And the evidence the gate binds carries that same hash.
        assert outcome['evidence'][0]['content_hash'] == row['content_hash']
