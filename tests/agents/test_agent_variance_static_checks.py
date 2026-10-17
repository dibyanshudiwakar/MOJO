"""The parity and hardcoding skills integrated with the variance-analysis agent (opt-in
``parity=true`` / ``hardcoding=true``).

Both are model-only and advisory; the notes below are written for parity and hold for both:

Parity is model-only and advisory: it reads the job's own IIB source and generated
code, answers with the skill's own JSON contract, and stores a validation report. It
must never touch the variance store, the agent's outputs or the runtime comparison.
"""
import json
from pathlib import Path

import pytest

from app.agents.reasoning import MODEL_GATEWAY, ProviderExecutionAdapter
from app.agents.skills import DEFAULT_ROOT, skill_for
from app.agents.variance_analysis import (HARDCODING_PROMPT, HARDCODING_SKILL_FILE, PARITY_MAX_CHARS,
                                          PARITY_PROMPT, PARITY_SKILL_FILE, VarianceAnalysisAgent)
from app.repository import digest, now, uid
from app.services.platform_v7 import AIRegistryService
from tests.agents.model_double import ANSWERS, PROMPT_BY_TEMPLATE

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / 'external-skills' / 'gap-analysis' / 'parity-skill.md'
HARDCODING_SOURCE = REPO_ROOT / 'external-skills' / 'hardcoding' / 'hardcoding-skill.md'

REPORT = {'action': 'parity_check', 'application_name': 'Orders',
          'counts': {'match': 1, 'partial': 0, 'gap': 1},
          'severity_counts': {'critical': 1, 'high': 0, 'medium': 0, 'low': 0},
          'rows': [{'area': 'Field Mappings', 'status': 'MATCH'},
                   {'area': 'Field Mappings', 'status': 'GAP'}],
          'findings': [{'id': 'P-01', 'severity': 'CRITICAL', 'status': 'GAP'}],
          'suggestions': []}


@pytest.fixture(autouse=True)
def _workspace_root(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(tmp_path))


class ParityProvider(ProviderExecutionAdapter):
    """Answers the static-check prompts with a report, every other one normally.

    Two changes from the version that only ever returned ``REPORT``.

    It answered *every* prompt with the report, which worked only because
    ``variance-analysis/findings`` fell back when the report failed its schema.
    With no fallback the agent's own materiality call fails on it, so anything
    other than parity and hardcoding is delegated to the shared defaults --
    keeping each test's subject parity rather than an unrelated prompt it
    happens to share a double with.

    And it identifies the prompt by *name*, resolved through the registry the
    agent reasoned against, rather than by template text. The adapter seam only
    ever receives the template, so a test asserting on one was really asserting
    on a fixture's string; with the catalogue seeded, which template arrives
    depends on which version a test registered.
    """

    accepts_skill = True

    def __init__(self, repo=None, answer=None):
        self.repo = repo
        self.answer = REPORT if answer is None else answer
        self.calls = []

    def _name(self, template):
        if self.repo is not None:
            row = self.repo.one('select name from prompt_registry where template=?', (template,))
            if row:
                return row['name']
        return PROMPT_BY_TEMPLATE.get(template, template)

    def invoke(self, template, payload, skill=None):
        name = self._name(template)
        self.calls.append({'prompt': name, 'template': template, 'payload': payload,
                           'skill': skill})
        if name in (PARITY_PROMPT, HARDCODING_PROMPT):
            return self.answer
        handler = ANSWERS.get(name)
        return handler(payload) if handler else {}

    def prompts(self):
        return [c['prompt'] for c in self.calls]

    def calls_for(self, prompt):
        return [c for c in self.calls if c['prompt'] == prompt]


@pytest.fixture
def seeded(repo, job, ace_repo):
    """Everything parity reads: an intake manifest over real IIB files, an analysis, generated code."""
    with repo.connect() as c:
        repo.insert(c, 'intake_manifests',
                    {'id': uid(), 'job_id': job['id'], 'repository_uri': str(ace_repo), 'branch': 'main',
                     'revision': '', 'inventory': '{}', 'gaps': '[]', 'content_hash': 'h', 'created_at': now()})
        repo.insert(c, 'ace_analyses',
                    {'id': 'analysis-1', 'job_id': job['id'], 'version': '1', 'semantic_model': '{}',
                     'analysis_md': '', 'ambiguities': '[]', 'dependencies': '[]',
                     'content_hash': 'h', 'created_at': now()})
        for path, body in {'src/main/java/OrdersService.java': 'class OrdersService {}',
                           'src/main/resources/application.properties': 'server.port=8080'}.items():
            repo.insert(c, 'generated_artifacts',
                        {'id': uid(), 'job_id': job['id'], 'plan_id': 'plan-1', 'path': path, 'content': body,
                         'provenance': '{}', 'region_policy': '', 'content_hash': digest(body), 'created_at': now()})
    return job['id']


def run(repo, job, executed_package, **params):
    """Run the agent, leaving ``provider`` absent when the caller gave none.

    It used to pass ``provider=None`` unconditionally, which now matters: the
    autouse ``model`` fixture fills an *absent* provider, and an explicit None
    defeats that -- so a baseline run with no static checks would fail on the
    agent's mandatory materiality call instead of giving a comparison to
    compare against.
    """
    package_id = executed_package(job['id'])
    kwargs = {}
    if 'provider' in params:
        kwargs['provider'] = params.pop('provider')
    return VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id, **params},
                                           **kwargs)


# ---- the skill package ---------------------------------------------------------------------------

def test_the_parity_skill_is_a_verified_reference_of_the_variance_skill():
    skill = skill_for('variance-analysis')
    assert skill.reference(PARITY_SKILL_FILE).replace('\r\n', '\n') == \
        SOURCE.read_text(encoding='utf-8').replace('\r\n', '\n')
    assert 'references/parity-skill.md' in skill.text


def test_focusing_narrows_the_skill_to_the_parity_file_alone():
    skill = skill_for('variance-analysis')
    focused = skill.focus(PARITY_SKILL_FILE)
    assert focused.name == 'iib-to-ms-variance-analysis#parity-skill.md'
    assert focused.provenance()['skill_focus'] == 'parity-skill.md'
    prompt = focused.as_system_prompt()
    assert prompt.startswith('---\nname: parity-skill')
    assert 'Reference files available' not in prompt          # not the general SKILL.md + index
    assert 'Agent binding' not in prompt
    assert focused.content_hash != skill.content_hash
    with pytest.raises(KeyError):
        skill.focus('no-such-file.md')


# ---- default behaviour is unchanged --------------------------------------------------------------

def test_parity_is_off_by_default(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    provider = ParityProvider(repo)
    result = run(repo, job, executed_package, provider=provider)
    assert 'parity' not in result.metrics
    assert PARITY_PROMPT not in provider.prompts()


def test_the_agent_contract_outputs_are_unchanged_by_parity(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider(repo))
    assert set(result.outputs) == {'variance_records', 'severity'}


# ---- static checks do not need a runtime replay --------------------------------------------------

def test_parity_runs_even_when_the_package_was_never_replayed(repo, job, seeded, gateway):
    """Parity/hardcoding read ACE source and the generated code directly -- a
    package that test-engineering only prepared (no runtime adapters configured,
    so no RUNTIME_EXECUTION report exists at all) must not block them."""
    gateway(PARITY_PROMPT)
    package_id = uid()
    payload = json.dumps({'plan_id': 'plan-1', 'cases': [{'id': 'CASE-001'}]}, sort_keys=True)
    with repo.connect() as c:
        repo.insert(c, 'differential_packages',
                    {'id': package_id, 'job_id': job['id'], 'plan_id': 'plan-1',
                     'status': 'READY_FOR_EXECUTION', 'payload': payload,
                     'content_hash': digest(payload), 'created_at': now()})
    result = VarianceAnalysisAgent(repo).run(job['id'], params={
        'package_id': package_id, 'parity': True}, provider=ParityProvider(repo))
    assert result.outputs == {'variance_records': [], 'severity': 'NONE'}
    assert result.metrics['runtime_comparison'] == 'NOT_EXECUTED'
    assert result.metrics['parity']['status'] == 'ANALYSED'
    assert result.metrics['parity']['counts'] == REPORT['counts']


# ---- the opt-in step -----------------------------------------------------------------------------

def test_parity_runs_with_the_parity_skill_as_the_guidance(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT, template='APPROVED PARITY TEMPLATE', version='2')
    provider = ParityProvider(repo)
    result = run(repo, job, executed_package, parity=True, provider=provider)
    parity = result.metrics['parity']
    assert parity['status'] == 'ANALYSED'
    assert parity['counts'] == REPORT['counts'] and parity['findings'] == 1
    # The parity call got the parity skill -- not the agent's general SKILL.md.
    parity_call, = provider.calls_for(PARITY_PROMPT)
    assert parity_call['skill'].focused_on == 'parity-skill.md'
    assert parity_call['skill'].name == 'iib-to-ms-variance-analysis#parity-skill.md'


def test_parity_is_given_the_jobs_own_iib_and_generated_files(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    provider = ParityProvider(repo)
    run(repo, job, executed_package, parity=True, provider=provider)
    payload = provider.calls_for(PARITY_PROMPT)[-1]['payload']
    assert set(payload) == {'iib_files', 'ms_files'}
    assert set(payload['iib_files']) == {'Orders.msgflow', 'Orders.esql', 'Order.xsd'}
    assert set(payload['ms_files']) == {'src/main/java/OrdersService.java',
                                        'src/main/resources/application.properties'}


def test_the_report_is_stored_but_never_enters_the_variance_store(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    before = repo.one('select count(*) n from variances')['n']
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider(repo))
    row = repo.one("select * from validation_reports where report_type='PARITY_ANALYSIS'")
    assert row['reference_id'] == 'analysis-1' and row['status'] == 'ANALYSED'
    stored = json.loads(row['payload'])
    assert stored['report']['action'] == 'parity_check'
    assert stored['provenance']['skill_focus'] == 'parity-skill.md'
    assert stored['provenance']['mode'] == MODEL_GATEWAY
    assert result.metrics['parity']['report_id'] == row['id']
    # The runtime comparison still ran and produced exactly its own variance, no parity rows.
    assert repo.one('select count(*) n from variances')['n'] == before + 1
    assert repo.one("select count(*) n from variances where path like '%parity%'")['n'] == 0


def test_parity_does_not_disturb_the_runtime_comparison(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    plain = run(repo, job, executed_package)
    withparity = run(repo, job, executed_package, parity=True, provider=ParityProvider(repo))
    assert withparity.outputs['severity'] == plain.outputs['severity']
    assert [r['status'] for r in withparity.outputs['variance_records']] == \
        [r['status'] for r in plain.outputs['variance_records']]


# ---- when parity cannot or should not run ---------------------------------------------------------

def test_without_an_approved_prompt_parity_is_not_analysed_and_nothing_is_stored(repo, job, executed_package,
                                                                                seeded):
    # Parity's prompt is withdrawn rather than never registered: the catalogue
    # is seeded APPROVED now, so "unapproved" has to be made, and rejecting the
    # row is what an AI_GOVERNANCE_ADMIN withdrawing a prompt actually does.
    with repo.connect() as c:
        c.execute("update prompt_registry set status='REJECTED' where name=?", (PARITY_PROMPT,))
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider(repo))
    assert result.metrics['parity'] == {'status': 'NOT_ANALYSED', 'mode': 'MODEL_UNAVAILABLE',
                                        'reason': 'no_approved_prompt_or_model'}
    assert repo.one("select count(*) n from validation_reports where report_type='PARITY_ANALYSIS'")['n'] == 0


def test_a_parity_call_the_model_cannot_answer_is_not_analysed(repo, job, executed_package,
                                                               seeded, gateway):
    """Parity failing does not take the agent down. The subject, restated.

    This used to run with no provider at all, which is no longer a way to
    isolate parity: the agent's own materiality call is mandatory now, so a
    missing provider fails the whole agent before parity is reached. A provider
    that fails only the parity prompt tests the same thing more precisely --
    these checks are advisory, and an advisory step that cannot answer must
    record that and leave the run intact.
    """
    class _FailsParityOnly(ProviderExecutionAdapter):
        accepts_skill = True

        def invoke(self, template, payload, skill=None):
            row = repo.one('select name from prompt_registry where template=?', (template,))
            name = row['name'] if row else PROMPT_BY_TEMPLATE.get(template, template)
            if name == PARITY_PROMPT:
                raise RuntimeError('provider_error:AuthenticationError:Error code: 401')
            handler = ANSWERS.get(name)
            return handler(payload) if handler else {}

    result = run(repo, job, executed_package, parity=True, provider=_FailsParityOnly())
    assert result.metrics['parity']['status'] == 'NOT_ANALYSED'
    assert '401' in result.metrics['parity']['reason']
    # The runtime comparison, which is the agent's actual output, is unaffected.
    assert result.outputs['variance_records'][0]['status'] == 'VARIANCE'


def test_a_response_that_is_not_a_parity_report_is_rejected_and_not_stored(repo, job, executed_package, seeded,
                                                                          gateway):
    gateway(PARITY_PROMPT)
    result = run(repo, job, executed_package, parity=True,
                 provider=ParityProvider(repo, {'answer': 'looks fine'}))
    assert result.metrics['parity'] == {'status': 'INVALID_RESPONSE', 'reason': 'not_a_parity_check_report'}
    assert repo.one("select count(*) n from validation_reports where report_type='PARITY_ANALYSIS'")['n'] == 0


def test_without_generated_code_parity_is_skipped(repo, job, executed_package, gateway, ace_repo):
    gateway(PARITY_PROMPT)
    with repo.connect() as c:
        repo.insert(c, 'intake_manifests',
                    {'id': uid(), 'job_id': job['id'], 'repository_uri': str(ace_repo), 'branch': 'main',
                     'revision': '', 'inventory': '{}', 'gaps': '[]', 'content_hash': 'h', 'created_at': now()})
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider(repo))
    assert result.metrics['parity'] == {'status': 'SKIPPED', 'reason': 'no_generated_artifacts'}


def test_without_an_intake_manifest_parity_is_skipped(repo, job, executed_package, gateway):
    gateway(PARITY_PROMPT)
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider(repo))
    assert result.metrics['parity'] == {'status': 'SKIPPED', 'reason': 'no_intake_manifest'}


def test_oversized_source_is_skipped_not_truncated(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    big = 'x' * (PARITY_MAX_CHARS + 1)
    with repo.connect() as c:
        repo.insert(c, 'generated_artifacts',
                    {'id': uid(), 'job_id': job['id'], 'plan_id': 'plan-1', 'path': 'Big.java', 'content': big,
                     'provenance': '{}', 'region_policy': '', 'content_hash': digest(big), 'created_at': now()})
    provider = ParityProvider(repo)
    result = run(repo, job, executed_package, parity=True, provider=provider)
    assert result.metrics['parity']['status'] == 'SKIPPED'
    assert result.metrics['parity']['reason'].startswith('source_too_large:')
    assert not [c for c in provider.calls if c['skill'] is not None and c['skill'].focused_on]


def test_an_iib_source_outside_the_workspace_is_never_read(repo, job, executed_package, seeded, gateway,
                                                          tmp_path, monkeypatch):
    gateway(PARITY_PROMPT)
    inside = tmp_path / 'inside'
    inside.mkdir()
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(inside))        # the intake path is now outside it
    provider = ParityProvider(repo)
    result = run(repo, job, executed_package, parity=True, provider=provider)
    assert result.metrics['parity'] == {'status': 'SKIPPED', 'reason': 'iib_source_unavailable'}
    assert not [c for c in provider.calls if 'iib_files' in c['payload']]


def test_a_damaged_skill_skips_parity_but_not_the_runtime_comparison(repo, job, executed_package, seeded, gateway,
                                                                    tmp_path, monkeypatch):
    import shutil
    gateway(PARITY_PROMPT)
    copy = tmp_path / 'skills'
    shutil.copytree(DEFAULT_ROOT, copy)
    (copy / 'iib-to-ms-variance-analysis' / 'references' / 'parity-skill.md').write_text('tampered')
    monkeypatch.setenv('FACTORY_AGENT_SKILLS_DIR', str(copy))
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider(repo))
    assert result.metrics['parity']['status'] == 'SKIPPED'
    assert 'parity_skill_unavailable:skill_file_modified' in result.metrics['parity']['reason']
    assert result.outputs['variance_records'][0]['status'] == 'VARIANCE'


# =================================================================================================
# The hardcoding check: same machinery, a narrower scope and its own report.
# =================================================================================================

HARDCODING_REPORT = {'action': 'hardcoding_check', 'application_name': 'Orders',
                     'counts': {'match': 1, 'partial': 1, 'gap': 0},
                     'rows': [{'area': 'Transaction Flow', 'category': 'Routing', 'status': 'MATCH',
                               'remarks': 'identical'},
                              {'area': 'Field Mappings', 'category': 'Boolean encoding', 'status': 'PARTIAL',
                               'remarks': 'string instead of boolean'}],
                     'suggestions': [{'priority': 'HIGH', 'issue': 'boolean encoding',
                                      'recommendation': 'emit a native boolean'}]}


class RoutingProvider(ProviderExecutionAdapter):
    """Answers each static check with its own report, chosen by the skill it was handed.

    Anything that is not a static check is delegated to the shared defaults.
    It used to answer ``{'answer': 'unrelated'}``, which was harmless while the
    agent's materiality call had a fallback and is not now: that call would fail
    its schema three times and take the agent down before any assertion about
    parity or hardcoding was reached.
    """
    accepts_skill = True

    def __init__(self, repo=None, parity=REPORT, hardcoding=HARDCODING_REPORT):
        self.repo = repo
        self.by_focus = {PARITY_SKILL_FILE: parity, HARDCODING_SKILL_FILE: hardcoding}
        self.calls = []

    def _name(self, template):
        if self.repo is not None:
            row = self.repo.one('select name from prompt_registry where template=?', (template,))
            if row:
                return row['name']
        return PROMPT_BY_TEMPLATE.get(template, template)

    def invoke(self, template, payload, skill=None):
        name = self._name(template)
        self.calls.append({'prompt': name, 'template': template, 'payload': payload,
                           'skill': skill})
        focus = getattr(skill, 'focused_on', '')
        if focus in self.by_focus:
            return self.by_focus[focus]
        handler = ANSWERS.get(name)
        return handler(payload) if handler else {'answer': 'unrelated'}

    def calls_for(self, prompt):
        return [c for c in self.calls if c['prompt'] == prompt]


@pytest.fixture
def both_prompts(repo, gateway):
    """Both checks' prompts APPROVED. ``gateway`` registers the model once, so the second prompt is added directly."""
    gateway(PARITY_PROMPT, template='PARITY TEMPLATE', version='2')

    class _Approver:
        identity = 'board@example'
        roles = ('AI_GOVERNANCE_ADMIN',)

    AIRegistryService(repo).register_prompt(
        {'name': HARDCODING_PROMPT, 'version': '2', 'template': 'HARDCODING TEMPLATE', 'status': 'APPROVED'},
        _Approver())


def calls_for(provider, prompt):
    return provider.calls_for(prompt)


def analysis_reports(repo):
    return repo.rows("select report_type from validation_reports where report_type in "
                     "('PARITY_ANALYSIS','HARDCODING_ANALYSIS') order by report_type")


def test_the_hardcoding_skill_is_a_verified_reference_of_the_variance_skill():
    skill = skill_for('variance-analysis')
    assert skill.reference(HARDCODING_SKILL_FILE).replace('\r\n', '\n') == \
        HARDCODING_SOURCE.read_text(encoding='utf-8').replace('\r\n', '\n')
    assert 'references/hardcoding-skill.md' in skill.text
    focused = skill.focus(HARDCODING_SKILL_FILE)
    assert focused.name == 'iib-to-ms-variance-analysis#hardcoding-skill.md'
    assert focused.as_system_prompt().startswith('---\nname: hardcoding-skill')


def test_hardcoding_is_off_by_default(repo, job, executed_package, seeded, both_prompts):
    provider = RoutingProvider(repo)
    result = run(repo, job, executed_package, provider=provider)
    assert 'hardcoding' not in result.metrics and 'parity' not in result.metrics
    assert not calls_for(provider, HARDCODING_PROMPT)


def test_hardcoding_runs_with_its_own_skill_and_leaves_the_contract_alone(repo, job, executed_package, seeded,
                                                                          both_prompts):
    provider = RoutingProvider(repo)
    result = run(repo, job, executed_package, hardcoding=True, provider=provider)
    assert set(result.outputs) == {'variance_records', 'severity'}
    assert 'parity' not in result.metrics
    summary = result.metrics['hardcoding']
    assert summary['status'] == 'ANALYSED' and summary['counts'] == HARDCODING_REPORT['counts']
    assert summary['suggestions'] == 1
    call, = calls_for(provider, HARDCODING_PROMPT)
    assert call['skill'].focused_on == 'hardcoding-skill.md'
    assert not calls_for(provider, PARITY_PROMPT)


def test_hardcoding_sees_only_java_and_only_the_iib_files_its_skill_names(repo, job, ace_repo, executed_package,
                                                                          seeded, both_prompts):
    (ace_repo / 'Orders.msgmap').write_text('<map/>')       # in the hardcoding skill's scope
    (ace_repo / 'Orders.wsdl').write_text('<wsdl/>')         # in parity's scope, not hardcoding's
    with repo.connect() as c:
        for path in ('src/test/java/OrdersServiceTest.java', 'src/main/resources/application.yml'):
            repo.insert(c, 'generated_artifacts',
                        {'id': uid(), 'job_id': job['id'], 'plan_id': 'plan-1', 'path': path, 'content': 'x',
                         'provenance': '{}', 'region_policy': '', 'content_hash': digest(path),
                         'created_at': now()})
    provider = RoutingProvider(repo)
    run(repo, job, executed_package, hardcoding=True, parity=True, provider=provider)
    hardcoding, = calls_for(provider, HARDCODING_PROMPT)
    assert set(hardcoding['payload']['ms_files']) == {'src/main/java/OrdersService.java'}
    assert set(hardcoding['payload']['iib_files']) == {'Orders.msgflow', 'Orders.esql', 'Order.xsd',
                                                       'Orders.msgmap'}
    parity, = calls_for(provider, PARITY_PROMPT)
    assert 'src/main/resources/application.yml' in parity['payload']['ms_files']     # parity keeps config
    assert 'Orders.wsdl' in parity['payload']['iib_files']


def test_hardcoding_is_stored_under_its_own_report_type_and_never_as_a_variance(repo, job, executed_package,
                                                                               seeded, both_prompts):
    before = repo.one('select count(*) n from variances')['n']
    result = run(repo, job, executed_package, hardcoding=True, provider=RoutingProvider(repo))
    row = repo.one("select * from validation_reports where report_type='HARDCODING_ANALYSIS'")
    assert row['status'] == 'ANALYSED' and row['reference_id'] == 'analysis-1'
    stored = json.loads(row['payload'])
    assert stored['report']['action'] == 'hardcoding_check'
    assert stored['provenance']['skill_focus'] == 'hardcoding-skill.md'
    assert result.metrics['hardcoding']['report_id'] == row['id']
    assert [r['report_type'] for r in analysis_reports(repo)] == ['HARDCODING_ANALYSIS']
    assert repo.one('select count(*) n from variances')['n'] == before + 1       # only the runtime comparison's


def test_both_checks_in_one_run_stay_separate(repo, job, executed_package, seeded, both_prompts):
    provider = RoutingProvider(repo)
    result = run(repo, job, executed_package, parity=True, hardcoding=True, provider=provider)
    assert result.metrics['parity']['status'] == 'ANALYSED'
    assert result.metrics['hardcoding']['status'] == 'ANALYSED'
    assert result.metrics['parity']['report_id'] != result.metrics['hardcoding']['report_id']
    assert calls_for(provider, PARITY_PROMPT)[0]['skill'].focused_on == 'parity-skill.md'
    assert calls_for(provider, HARDCODING_PROMPT)[0]['skill'].focused_on == 'hardcoding-skill.md'
    assert [r['report_type'] for r in analysis_reports(repo)] == ['HARDCODING_ANALYSIS', 'PARITY_ANALYSIS']


def test_one_checks_report_is_not_accepted_as_the_others(repo, job, executed_package, seeded, both_prompts):
    """A model that answers a check with the other check's JSON must not be stored as that check."""
    swapped = RoutingProvider(repo, parity=HARDCODING_REPORT, hardcoding=REPORT)
    result = run(repo, job, executed_package, parity=True, hardcoding=True, provider=swapped)
    assert result.metrics['parity'] == {'status': 'INVALID_RESPONSE', 'reason': 'not_a_parity_check_report'}
    assert result.metrics['hardcoding'] == {'status': 'INVALID_RESPONSE',
                                            'reason': 'not_a_hardcoding_check_report'}
    assert analysis_reports(repo) == []


def test_hardcoding_without_an_approved_prompt_is_not_analysed(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)                              # parity stays approved
    with repo.connect() as c:                           # hardcoding is withdrawn
        c.execute("update prompt_registry set status='REJECTED' where name=?", (HARDCODING_PROMPT,))
    result = run(repo, job, executed_package, hardcoding=True, parity=True, provider=RoutingProvider(repo))
    assert result.metrics['parity']['status'] == 'ANALYSED'
    assert result.metrics['hardcoding'] == {'status': 'NOT_ANALYSED', 'mode': 'MODEL_UNAVAILABLE',
                                            'reason': 'no_approved_prompt_or_model'}


def test_hardcoding_is_skipped_when_there_is_no_java_but_parity_still_runs(repo, job, ace_repo,
                                                                          executed_package, both_prompts):
    with repo.connect() as c:
        repo.insert(c, 'intake_manifests',
                    {'id': uid(), 'job_id': job['id'], 'repository_uri': str(ace_repo), 'branch': 'main',
                     'revision': '', 'inventory': '{}', 'gaps': '[]', 'content_hash': 'h', 'created_at': now()})
        repo.insert(c, 'generated_artifacts',
                    {'id': uid(), 'job_id': job['id'], 'plan_id': 'plan-1', 'path': 'application.properties',
                     'content': 'server.port=8080', 'provenance': '{}', 'region_policy': '',
                     'content_hash': 'h', 'created_at': now()})
    result = run(repo, job, executed_package, hardcoding=True, parity=True, provider=RoutingProvider(repo))
    assert result.metrics['hardcoding'] == {'status': 'SKIPPED', 'reason': 'no_ms_java_files'}
    assert result.metrics['parity']['status'] == 'ANALYSED'


def test_a_damaged_skill_skips_hardcoding_without_touching_the_runtime_comparison(
        repo, job, executed_package, seeded, both_prompts, tmp_path, monkeypatch):
    import shutil
    copy = tmp_path / 'skills'
    shutil.copytree(DEFAULT_ROOT, copy)
    (copy / 'iib-to-ms-variance-analysis' / 'references' / 'hardcoding-skill.md').write_text('tampered')
    monkeypatch.setenv('FACTORY_AGENT_SKILLS_DIR', str(copy))
    result = run(repo, job, executed_package, hardcoding=True, provider=RoutingProvider(repo))
    assert result.metrics['hardcoding']['status'] == 'SKIPPED'
    assert 'hardcoding_skill_unavailable:skill_file_modified' in result.metrics['hardcoding']['reason']
    assert result.outputs['variance_records'][0]['status'] == 'VARIANCE'


# =================================================================================================
# The HTML report: each check writes a page next to its stored JSON report.
# =================================================================================================

def reports_dir(tmp_path, job):
    return tmp_path / 'reports' / str(job['id'])


def test_a_parity_run_writes_its_html_report(repo, job, executed_package, seeded, both_prompts, tmp_path):
    result = run(repo, job, executed_package, parity=True, provider=RoutingProvider(repo))
    parity = result.metrics['parity']
    assert parity['status'] == 'ANALYSED' and 'html_error' not in parity
    assert parity['html_path'] == 'reports/%s/parity-report.html' % job['id']
    assert parity['html_url'] == '/api/v2/reports/%s/html' % parity['report_id']
    page = (tmp_path / parity['html_path']).read_text(encoding='utf-8')
    assert page.startswith('<!DOCTYPE html>') and 'Parity report' in page and 'Orders' in page
    assert parity['report_id'] in page                                  # the footer names the stored report


def test_a_hardcoding_run_writes_its_own_separate_page(repo, job, executed_package, seeded, both_prompts, tmp_path):
    result = run(repo, job, executed_package, parity=True, hardcoding=True, provider=RoutingProvider(repo))
    folder = reports_dir(tmp_path, job)
    assert sorted(p.name for p in folder.iterdir()) == ['hardcoding-report.html', 'parity-report.html']
    hardcoding = (folder / 'hardcoding-report.html').read_text(encoding='utf-8')
    assert 'Hardcoding report' in hardcoding and 'Boolean encoding' in hardcoding
    assert 'Hardcoding report' not in (folder / 'parity-report.html').read_text(encoding='utf-8')
    assert result.metrics['hardcoding']['html_path'].endswith('hardcoding-report.html')


def test_the_stored_report_records_where_its_page_went(repo, job, executed_package, seeded, both_prompts):
    result = run(repo, job, executed_package, hardcoding=True, provider=RoutingProvider(repo))
    row = repo.one("select * from validation_reports where report_type='HARDCODING_ANALYSIS'")
    assert json.loads(row['payload'])['html_path'] == result.metrics['hardcoding']['html_path']


def test_the_page_lands_in_the_pipelines_workspace_when_the_job_has_one(repo, job, executed_package, seeded,
                                                                       both_prompts, tmp_path):
    with repo.connect() as c:
        repo.insert(c, 'pipelines', {'id': 'p1', 'job_id': job['id'], 'workspace_dir': 'membereobpreferences',
                                     'created_at': now()})
    result = run(repo, job, executed_package, parity=True, provider=RoutingProvider(repo))
    assert result.metrics['parity']['html_path'] == 'membereobpreferences/reports/parity-report.html'
    assert (tmp_path / 'membereobpreferences' / 'reports' / 'parity-report.html').is_file()


def test_a_second_run_replaces_the_latest_page_but_both_reports_stay_stored(repo, job, executed_package, seeded,
                                                                           both_prompts, tmp_path):
    first = run(repo, job, executed_package, parity=True, provider=RoutingProvider(repo))
    second = run(repo, job, executed_package, parity=True, provider=RoutingProvider(repo, parity=dict(
        REPORT, application_name='SecondRun')))
    page = (tmp_path / second.metrics['parity']['html_path']).read_text(encoding='utf-8')
    assert 'SecondRun' in page and first.metrics['parity']['report_id'] != second.metrics['parity']['report_id']
    assert repo.one("select count(*) n from validation_reports where report_type='PARITY_ANALYSIS'")['n'] == 2


def test_no_page_is_written_when_nothing_was_analysed(repo, job, executed_package, seeded, gateway, tmp_path):
    gateway(PARITY_PROMPT)
    # Hardcoding withdrawn so neither check yields a report: parity answers
    # something invalid and hardcoding is never asked. The catalogue is seeded
    # APPROVED now, so leaving the prompt unregistered no longer achieves this.
    with repo.connect() as c:
        c.execute("update prompt_registry set status='REJECTED' where name=?", (HARDCODING_PROMPT,))
    result = run(repo, job, executed_package, hardcoding=True, parity=True, provider=RoutingProvider(repo,
        parity={'answer': 'not a report'}))
    assert result.metrics['parity']['status'] == 'INVALID_RESPONSE'
    assert result.metrics['hardcoding']['status'] == 'NOT_ANALYSED'
    assert not (tmp_path / 'reports').exists()


def test_a_failure_to_write_the_page_does_not_lose_the_analysis(repo, job, executed_package, seeded, both_prompts,
                                                               monkeypatch):
    def broken(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr('app.agents.variance_analysis.static_check_reports.render_html', broken)
    result = run(repo, job, executed_package, parity=True, provider=RoutingProvider(repo))
    parity = result.metrics['parity']
    assert parity['status'] == 'ANALYSED' and parity['html_error'] == 'OSError:disk full'
    assert 'html_path' not in parity
    row = repo.one("select * from validation_reports where report_type='PARITY_ANALYSIS'")
    assert row['id'] == parity['report_id'] and json.loads(row['payload'])['html_error'] == 'OSError:disk full'


def test_the_page_cannot_be_written_outside_the_workspace(repo, job, executed_package, seeded, both_prompts, tmp_path):
    with repo.connect() as c:
        repo.insert(c, 'pipelines', {'id': 'p1', 'job_id': job['id'], 'workspace_dir': '../escape',
                                     'created_at': now()})
    result = run(repo, job, executed_package, parity=True, provider=RoutingProvider(repo))
    assert result.metrics['parity']['status'] == 'ANALYSED'                     # the analysis still stands
    assert result.metrics['parity']['html_error'].startswith('WorkspaceError')
    assert not (tmp_path.parent / 'escape').exists()


def test_the_html_form_does_not_change_what_the_runtime_comparison_produces(repo, job, executed_package, seeded,
                                                                          both_prompts):
    plain = run(repo, job, executed_package)
    with_pages = run(repo, job, executed_package, parity=True, hardcoding=True, provider=RoutingProvider(repo))
    assert set(with_pages.outputs) == {'variance_records', 'severity'}
    assert with_pages.outputs['severity'] == plain.outputs['severity']
