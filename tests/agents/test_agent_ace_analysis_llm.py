"""ace-analysis reads the source with the model, and degrades honestly.

The agent used to call the gateway and discard the answer into
``metrics['model_ambiguity_count']`` while ``ACEParser`` built the artefact a
G2 approval binds. Now the model reads each source file and the parser is the
fallback. Three things have to hold for that to be trustworthy, and each has a
test here: the model's artefact is what gets persisted; a model that cannot
answer still yields an analysis, marked as computed; and a response that does
not match its schema is treated as no answer rather than persisted.

The fourth is the one that makes the first three safe -- a model-authored model
and a parsed one must hash identically, or the two paths would produce artefacts
that no single approval can bind.
"""
import json

import pytest

from app.agents.ace_analysis import ACEAnalysisAgent
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
    assert all(s.name == 'iib-to-ms-ace-analysis' for s in supplied)


def test_without_a_provider_the_analysis_is_still_produced_and_says_it_was_computed(
        repo, job, params, dispatch):
    outcome = dispatch(job['id'], 'ace-analysis', params)
    assert outcome['status'] in ('COMPLETED', 'CLARIFICATION_REQUIRED')
    assert outcome['reasoning']['mode'] == 'DETERMINISTIC_FALLBACK'
    assert outcome['reasoning']['artefact_source'] == 'DETERMINISTIC'
    row = repo.one('select * from ace_analyses where id=?',
                   (outcome['outputs']['analysis_document'],))
    model = json.loads(row['semantic_model'])
    # The parser's own reading of the fixture, not the model's.
    assert model['interfaces'][0]['endpoint'] == '/orders'
    assert outcome['metrics']['sources_from_model'] == 0
    assert outcome['metrics']['sources_computed'] > 0


def test_a_response_that_breaks_its_schema_is_not_persisted(repo, job, params, gateway, dispatch):
    """An invalid artefact must be refused, not stored and approved.

    A node with no ``type`` violates the schema. Everything downstream joins on
    those ids, so persisting the fragment would corrupt the mapping catalogue
    and the generated code -- and silently, since a fallback is a recorded
    degradation rather than a failure.
    """
    gateway(PROMPT, model_name=ALIAS)
    provider = ArtefactProvider(lambda p: {'flows': [{'path': p['path'],
                                                      'nodes': [{'id': 'in'}]}]})
    outcome = dispatch(job['id'], 'ace-analysis', params, provider=provider)

    assert outcome['reasoning']['mode'] == 'DETERMINISTIC_FALLBACK'
    assert 'model_response_schema_invalid' in outcome['reasoning']['reason']
    assert "'type' is a required property" in outcome['reasoning']['reason']
    row = repo.one('select * from ace_analyses where id=?',
                   (outcome['outputs']['analysis_document'],))
    assert json.loads(row['semantic_model'])['interfaces'][0]['endpoint'] == '/orders'


def test_one_unreadable_file_does_not_cost_the_whole_repository(
        repo, job, params, gateway, dispatch):
    """Per-file fallback is why a 500-file repo stays usable.

    The ESQL answer is invalid and the flow's is fine, so the flow stays
    model-authored and only the ESQL is computed -- recorded as partial
    authorship rather than claimed as either.
    """
    gateway(PROMPT, model_name=ALIAS)

    def answer(payload):
        if payload['path'].endswith('.esql'):
            return {'esql': [{'modules': ['NoPath']}]}      # 'path' is required
        return _fragment(payload)

    outcome = dispatch(job['id'], 'ace-analysis', params, provider=ArtefactProvider(answer))

    assert outcome['reasoning']['mode'] == 'PARTIAL_MODEL_GATEWAY'
    assert outcome['metrics']['sources_from_model'] >= 1
    assert outcome['metrics']['sources_computed'] >= 1
    model = json.loads(repo.one('select * from ace_analyses where id=?',
                                (outcome['outputs']['analysis_document'],))['semantic_model'])
    assert model['interfaces'][0]['endpoint'] == '/model-authored'   # the flow: from the model
    assert model['esql'][0]['path'].endswith('.esql')                # the ESQL: computed


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
