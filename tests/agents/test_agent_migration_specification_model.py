"""The migration specification's target is authored by the model, always.

The suite's own conftest opts back in to a computed target so the rest of the tests need no gateway; this
module removes that, so it tests what production does: the model decides the Spring modules and approved
dependencies, a rejected answer is sent back with the reason, and a step that cannot get a valid one
fails -- it does not compute.

The first group reproduces the three failures seen on a real run: the model answering ``approvedDependencies``
as objects, the specification refused for clarifications without ``status``, and a re-run dying on
``UNIQUE constraint failed: migration_specs.job_id, migration_specs.version``.
"""
import json

import pytest
import yaml

from app.agents.ace_analysis import ACEAnalysisAgent
from app.agents.artefact import ModelAuthoringError
from app.agents.migration_specification import SCHEMA, MigrationSpecificationAgent, computed_allowed
from app.agents.reasoning import ProviderExecutionAdapter
from app.repository import digest, now, uid
from app.services.priority1 import AnalysisSpecificationConsistencyValidator
from app.services.specification import SpecificationService

PROMPT = MigrationSpecificationAgent.PROMPT
GOOD = {'springModules': ['web', 'service', 'model'],
        'approvedDependencies': ['spring-boot-starter-web', 'com.cvs.frameworks:framework-restclient'],
        'notes': ['web: HTTPInput in Orders.msgflow']}


class Scripted(ProviderExecutionAdapter):
    """Answers in order; an Exception in the script is raised, and the last entry repeats."""
    accepts_skill = True

    def __init__(self, *answers):
        self.script = list(answers)
        self.calls = []

    def invoke(self, template, payload, skill=None):
        self.calls.append(payload)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def model_required(monkeypatch):
    monkeypatch.delenv('FACTORY_ALLOW_COMPUTED_SPEC', raising=False)


@pytest.fixture
def analysis(repo, job, ace_repo):
    return ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)}).outputs['analysis_document']


def author(repo, job, analysis, provider=None, **params):
    return MigrationSpecificationAgent(repo).run(job['id'], params={'analysis_id': analysis, **params},
                                                 provider=provider)


def spec_of(repo, result):
    return yaml.safe_load(repo.one('select yaml_text from migration_specs where id=?',
                                   (result.outputs['migration_specification_yaml'],))['yaml_text'])


def specs(repo, job):
    return repo.rows('select * from migration_specs where job_id=? order by version', (job['id'],))


# ---- the model authors the target -----------------------------------------------------------------

def test_the_model_decides_the_modules_and_dependencies(repo, job, analysis, gateway):
    gateway(PROMPT)
    provider = Scripted(GOOD)
    result = author(repo, job, analysis, provider)
    spec = spec_of(repo, result)
    assert spec['target'] == {'springModules': ['model', 'service', 'web'],
                              'approvedDependencies': ['com.cvs.frameworks:framework-restclient',
                                                       'spring-boot-starter-web']}
    assert result.reasoning['artefact_source'] == 'MODEL' and result.reasoning['attempts'] == 1
    assert result.metrics['specification_source'] == 'MODEL' and result.metrics['authoring_notes'] == 1
    assert result.evidence[0].metadata['authoring_notes'] == GOOD['notes']
    assert repo.one('select validation_status s from migration_specs')['s'] == 'VALID'


def test_the_model_is_told_the_approved_modules_and_the_answer_format(repo, job, analysis, gateway):
    gateway(PROMPT)
    provider = Scripted(GOOD)
    author(repo, job, analysis, provider)
    payload = provider.calls[0]
    assert 'web' in payload['approved_modules'] and 'service' in payload['approved_modules']
    assert 'STRINGS' in payload['answer_format']['approvedDependencies']
    assert '_correction' not in payload                                    # nothing to correct on a first attempt


def test_the_analysis_is_carried_over_in_code_so_the_model_cannot_drop_any_of_it(repo, job, analysis, gateway):
    """A full ``specification`` answer contributes its target and nothing else."""
    gateway(PROMPT)
    thin = {'specification': {'target': {'springModules': ['web'], 'approvedDependencies': ['spring-boot-starter-web']},
                              'source': {'flows': [], 'esql': []}, 'clarifications': []}}
    result = author(repo, job, analysis, Scripted(thin))
    spec = spec_of(repo, result)
    assert spec['target']['springModules'] == ['web']
    assert spec['source']['flows'] and spec['source']['esql']              # the model's empty lists were not used
    model = repo.one('select semantic_model from ace_analyses where id=?', (analysis,))['semantic_model']
    text = repo.one('select yaml_text from migration_specs where id=?',
                    (result.outputs['migration_specification_yaml'],))['yaml_text']
    assert AnalysisSpecificationConsistencyValidator().validate(model, text)['status'] == 'PASS'


# ---- reproduces failure 1: dependencies answered as objects ---------------------------------------

def test_dependencies_answered_as_objects_are_reduced_to_their_coordinate_without_a_second_call(
        repo, job, analysis, gateway):
    """The three shapes seen on the real run. Previously each discarded the whole answer."""
    gateway(PROMPT)
    objects = {'springModules': ['web', 'service'], 'approvedDependencies': [
        {'artifact': 'framework-connector-rxclaimsbo', 'purpose': 'RxClaim SBO XML over MQ', 'version': None},
        {'dependency': 'spring-boot-starter-web', 'status': 'PROPOSED', 'tracesTo': 'x.msgflow#FCMComposite_1_1'},
        {'groupId': 'com.cvs.frameworks', 'artifactId': 'framework-starter', 'version': None}]}
    provider = Scripted(objects)
    result = author(repo, job, analysis, provider)
    assert spec_of(repo, result)['target']['approvedDependencies'] == [
        'com.cvs.frameworks:framework-starter', 'framework-connector-rxclaimsbo', 'spring-boot-starter-web']
    assert len(provider.calls) == 1 and result.reasoning['attempts'] == 1          # no retry was needed
    assert len(result.reasoning['normalised']) == 3                                # and the repair is on record


def test_a_placeholder_that_is_not_a_dependency_is_sent_back_with_the_reason(repo, job, analysis, gateway):
    gateway(PROMPT)
    junk = dict(GOOD, approvedDependencies=[{'artifactId': 'framework parent / BOM',
                                             'groupId': 'TBD (from approved framework template)', 'version': None}])
    provider = Scripted(junk, GOOD)
    result = author(repo, job, analysis, provider)
    assert result.reasoning['attempts'] == 2 and len(provider.calls) == 2
    correction = provider.calls[1]['_correction']
    assert correction['attempt'] == 2 and 'coordinate' in correction['previous_answer_rejected_because']
    assert spec_of(repo, result)['target']['approvedDependencies'][0] == 'com.cvs.frameworks:framework-restclient'


def test_a_module_outside_the_approved_catalogue_is_sent_back(repo, job, analysis, gateway):
    gateway(PROMPT)
    provider = Scripted(dict(GOOD, springModules=['web', 'kubernetes']), GOOD)
    result = author(repo, job, analysis, provider)
    why = provider.calls[1]['_correction']['previous_answer_rejected_because']
    assert 'kubernetes' in why and 'approved mapping catalogue' in why and result.reasoning['attempts'] == 2


# ---- no computed substitute ----------------------------------------------------------------------

def test_a_model_that_never_gives_a_valid_answer_fails_the_step_and_stores_no_specification(repo, job, analysis,
                                                                                         gateway):
    gateway(PROMPT)
    provider = Scripted({'springModules': ['nope']})
    with pytest.raises(ModelAuthoringError, match='model_authoring_failed:after_3_attempts'):
        author(repo, job, analysis, provider)
    assert len(provider.calls) == 3 and specs(repo, job) == []


def test_the_number_of_attempts_is_a_parameter(repo, job, analysis, gateway):
    gateway(PROMPT)
    provider = Scripted({'springModules': ['nope']})
    with pytest.raises(ModelAuthoringError, match='after_2_attempts'):
        author(repo, job, analysis, provider, model_attempts=2)
    assert len(provider.calls) == 2


def test_a_provider_error_is_retried(repo, job, analysis, gateway):
    gateway(PROMPT)
    provider = Scripted(RuntimeError('provider_error:ReadTimeout'), GOOD)
    result = author(repo, job, analysis, provider)
    assert result.reasoning['attempts'] == 2 and result.metrics['specification_source'] == 'MODEL'


def test_a_provider_that_keeps_failing_fails_the_step_with_the_reason(repo, job, analysis, gateway):
    gateway(PROMPT)
    with pytest.raises(ModelAuthoringError, match='after_3_attempts:provider_execution_failed.*ReadTimeout'):
        author(repo, job, analysis, Scripted(RuntimeError('provider_error:ReadTimeout')))
    assert specs(repo, job) == []


def test_with_no_provider_configured_the_step_fails_rather_than_computing(repo, job, analysis, gateway):
    gateway(PROMPT)
    with pytest.raises(ModelAuthoringError, match='model_authoring_failed:no_provider_execution_adapter'):
        author(repo, job, analysis)
    assert specs(repo, job) == []


def test_with_no_approved_prompt_the_step_fails_and_never_calls_the_provider(repo, job, analysis):
    provider = Scripted(GOOD)
    with pytest.raises(ModelAuthoringError, match='model_authoring_failed:no_approved_prompt_or_model'):
        author(repo, job, analysis, provider)
    assert provider.calls == []


def test_a_refusal_by_policy_is_not_retried(repo, job, analysis, monkeypatch):
    calls = []

    class Refusing:
        def __init__(self, repo):
            pass

        def reason(self, context, prompt, payload, skill=None):
            calls.append(prompt)
            return None, {'reason': 'gateway_refused:dlp_rule_matched'}

    monkeypatch.setattr('app.agents.artefact.ReasoningGateway', Refusing)
    with pytest.raises(ModelAuthoringError, match='gateway_refused:dlp_rule_matched'):
        author(repo, job, analysis)
    assert len(calls) == 1


def test_the_production_default_requires_the_model(monkeypatch):
    assert computed_allowed() is False
    monkeypatch.setenv('FACTORY_ALLOW_COMPUTED_SPEC', '1')
    assert computed_allowed() is True


def test_opting_in_to_computed_is_recorded_not_silent(repo, job, analysis, gateway, monkeypatch):
    monkeypatch.setenv('FACTORY_ALLOW_COMPUTED_SPEC', '1')
    result = author(repo, job, analysis)                       # no provider at all
    assert result.reasoning['mode'] == 'DETERMINISTIC_FALLBACK'
    assert result.reasoning['reason'].startswith('model_authoring_failed:')
    assert result.metrics['specification_source'] == 'DETERMINISTIC'
    assert spec_of(repo, result)['target']['springModules'] == ['model', 'service', 'web']


def test_opting_in_does_not_bypass_a_model_that_can_answer(repo, job, analysis, gateway, monkeypatch):
    gateway(PROMPT)
    monkeypatch.setenv('FACTORY_ALLOW_COMPUTED_SPEC', '1')
    result = author(repo, job, analysis, Scripted(dict(GOOD, springModules=['web'])))
    assert result.metrics['specification_source'] == 'MODEL'
    assert spec_of(repo, result)['target']['springModules'] == ['web']


# ---- reproduces failure 2: clarifications without status -------------------------------------------

MODEL_STYLE_AMBIGUITY = [{'question': 'Which backend does the Compute node call?', 'owner': 'ACE_SME',
                          'impact': 'cannot choose a connector'}]


def _model_authored_analysis(repo, analysis):
    """The ambiguity shape the model-authored analysis produces: no ``status``, no ``evidenceReference``."""
    with repo.connect() as c:
        c.execute('update ace_analyses set ambiguities=? where id=?', (json.dumps(MODEL_STYLE_AMBIGUITY), analysis))


def test_ambiguities_without_status_become_valid_clarifications(repo, job, analysis, gateway):
    gateway(PROMPT)
    _model_authored_analysis(repo, analysis)
    result = author(repo, job, analysis, Scripted(GOOD))
    clarification, = spec_of(repo, result)['clarifications']
    assert clarification['status'] == 'OPEN' and clarification['evidenceReference'] == '$'
    assert clarification['question'] == MODEL_STYLE_AMBIGUITY[0]['question']
    assert clarification['owner'] == 'ACE_SME' and clarification['impact'] == 'cannot choose a connector'


def test_the_same_holds_on_the_computed_path_which_is_where_it_failed(repo, job, analysis, monkeypatch):
    monkeypatch.setenv('FACTORY_ALLOW_COMPUTED_SPEC', '1')
    _model_authored_analysis(repo, analysis)
    result = author(repo, job, analysis)
    assert specs(repo, job)[0]['validation_status'] == 'VALID'
    assert spec_of(repo, result)['clarifications'][0]['status'] == 'OPEN'


def test_what_an_ambiguity_already_says_is_kept(repo, job, analysis, gateway):
    gateway(PROMPT)
    full = [{'status': 'ANSWERED', 'question': 'q', 'owner': 'JAVA_ARCHITECT', 'impact': 'i',
             'evidenceReference': 'Orders.esql:3', 'extra': 'kept'}]
    with repo.connect() as c:
        c.execute('update ace_analyses set ambiguities=? where id=?', (json.dumps(full), analysis))
    clarification, = spec_of(repo, author(repo, job, analysis, Scripted(GOOD)))['clarifications']
    assert clarification == full[0]


# ---- reproduces failure 3: saving again --------------------------------------------------------------

def test_a_failed_attempts_invalid_row_is_replaced_not_a_unique_violation(repo, job, analysis, gateway):
    gateway(PROMPT)
    with repo.connect() as c:
        c.execute('insert into migration_specs values(?,?,?,?,?,?,?,?)',
                  ('husk', job['id'], '1.0.0', 'bad: yaml', digest('bad: yaml'), 'INVALID',
                   json.dumps([{'path': '$', 'message': 'x'}]), now()))
    result = author(repo, job, analysis, Scripted(GOOD))
    rows = specs(repo, job)
    assert len(rows) == 1 and rows[0]['id'] == 'husk' and rows[0]['validation_status'] == 'VALID'
    assert result.outputs['migration_specification_yaml'] == 'husk'


def test_running_again_with_a_different_answer_keeps_the_earlier_specification_and_uses_the_next_version(
        repo, job, analysis, gateway):
    """The step is not deterministic, so a re-run is expected to differ -- and the first may be approved."""
    gateway(PROMPT)
    first = author(repo, job, analysis, Scripted(GOOD))
    second = author(repo, job, analysis, Scripted(dict(GOOD, springModules=['web'])))
    rows = specs(repo, job)
    assert [r['version'] for r in rows] == ['1.0.0', '1.0.1']
    assert first.outputs['migration_specification_yaml'] == rows[0]['id'] != second.outputs['migration_specification_yaml']
    assert second.evidence[0].version == '1.0.1'
    assert yaml.safe_load(rows[0]['yaml_text'])['target']['springModules'] == ['model', 'service', 'web']   # untouched


def test_running_again_with_the_same_answer_is_the_same_specification(repo, job, analysis, gateway):
    gateway(PROMPT)
    first = author(repo, job, analysis, Scripted(GOOD))
    second = author(repo, job, analysis, Scripted(GOOD))
    assert first.outputs == second.outputs and len(specs(repo, job)) == 1


def test_a_third_different_run_takes_the_next_free_version(repo, job, analysis, gateway):
    gateway(PROMPT)
    for modules in (['web'], ['service'], ['model']):
        author(repo, job, analysis, Scripted(dict(GOOD, springModules=modules)))
    assert [r['version'] for r in specs(repo, job)] == ['1.0.0', '1.0.1', '1.0.2']


def test_next_version_skips_taken_ones(repo, job):
    service = SpecificationService(repo, SCHEMA)
    with repo.connect() as c:
        for v in ('1.0.0', '1.0.1'):
            c.execute('insert into migration_specs values(?,?,?,?,?,?,?,?)',
                      (uid(), job['id'], v, 't', digest(v), 'VALID', '[]', now()))
    assert service.next_version(job['id'], '1.0.0') == '1.0.2'
    assert service.next_version(job['id'], '2.0.0') == '2.0.0'
