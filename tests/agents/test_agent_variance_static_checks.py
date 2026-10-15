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
    accepts_skill = True

    def __init__(self, answer=None):
        self.answer = REPORT if answer is None else answer
        self.calls = []

    def invoke(self, template, payload, skill=None):
        self.calls.append({'template': template, 'payload': payload, 'skill': skill})
        return self.answer


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
    package_id = executed_package(job['id'])
    provider = params.pop('provider', None)
    return VarianceAnalysisAgent(repo).run(job['id'], params={'package_id': package_id, **params},
                                           provider=provider)


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
    provider = ParityProvider()
    result = run(repo, job, executed_package, provider=provider)
    assert 'parity' not in result.metrics
    assert PARITY_PROMPT not in [c['template'] for c in provider.calls]


def test_the_agent_contract_outputs_are_unchanged_by_parity(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider())
    assert set(result.outputs) == {'variance_records', 'severity'}


# ---- the opt-in step -----------------------------------------------------------------------------

def test_parity_runs_with_the_parity_skill_as_the_guidance(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT, template='APPROVED PARITY TEMPLATE')
    provider = ParityProvider()
    result = run(repo, job, executed_package, parity=True, provider=provider)
    parity = result.metrics['parity']
    assert parity['status'] == 'ANALYSED'
    assert parity['counts'] == REPORT['counts'] and parity['findings'] == 1
    # The parity call got the parity skill -- not the agent's general SKILL.md.
    parity_call, = [c for c in provider.calls if c['template'] == 'APPROVED PARITY TEMPLATE']
    assert parity_call['skill'].focused_on == 'parity-skill.md'
    assert parity_call['skill'].name == 'iib-to-ms-variance-analysis#parity-skill.md'


def test_parity_is_given_the_jobs_own_iib_and_generated_files(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    provider = ParityProvider()
    run(repo, job, executed_package, parity=True, provider=provider)
    payload = provider.calls[-1]['payload']
    assert set(payload) == {'iib_files', 'ms_files'}
    assert set(payload['iib_files']) == {'Orders.msgflow', 'Orders.esql', 'Order.xsd'}
    assert set(payload['ms_files']) == {'src/main/java/OrdersService.java',
                                        'src/main/resources/application.properties'}


def test_the_report_is_stored_but_never_enters_the_variance_store(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    before = repo.one('select count(*) n from variances')['n']
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider())
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
    withparity = run(repo, job, executed_package, parity=True, provider=ParityProvider())
    assert withparity.outputs['severity'] == plain.outputs['severity']
    assert [r['status'] for r in withparity.outputs['variance_records']] == \
        [r['status'] for r in plain.outputs['variance_records']]


# ---- when parity cannot or should not run ---------------------------------------------------------

def test_without_an_approved_prompt_parity_is_not_analysed_and_nothing_is_stored(repo, job, executed_package,
                                                                                seeded):
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider())
    assert result.metrics['parity'] == {'status': 'NOT_ANALYSED', 'mode': 'DETERMINISTIC_FALLBACK',
                                        'reason': 'no_approved_prompt_or_model'}
    assert repo.one("select count(*) n from validation_reports where report_type='PARITY_ANALYSIS'")['n'] == 0


def test_without_a_provider_parity_is_not_analysed(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    result = run(repo, job, executed_package, parity=True)
    assert result.metrics['parity']['status'] == 'NOT_ANALYSED'
    assert result.metrics['parity']['reason'] == 'no_provider_execution_adapter'


def test_a_response_that_is_not_a_parity_report_is_rejected_and_not_stored(repo, job, executed_package, seeded,
                                                                          gateway):
    gateway(PARITY_PROMPT)
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider({'answer': 'looks fine'}))
    assert result.metrics['parity'] == {'status': 'INVALID_RESPONSE', 'reason': 'not_a_parity_check_report'}
    assert repo.one("select count(*) n from validation_reports where report_type='PARITY_ANALYSIS'")['n'] == 0


def test_without_generated_code_parity_is_skipped(repo, job, executed_package, gateway, ace_repo):
    gateway(PARITY_PROMPT)
    with repo.connect() as c:
        repo.insert(c, 'intake_manifests',
                    {'id': uid(), 'job_id': job['id'], 'repository_uri': str(ace_repo), 'branch': 'main',
                     'revision': '', 'inventory': '{}', 'gaps': '[]', 'content_hash': 'h', 'created_at': now()})
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider())
    assert result.metrics['parity'] == {'status': 'SKIPPED', 'reason': 'no_generated_artifacts'}


def test_without_an_intake_manifest_parity_is_skipped(repo, job, executed_package, gateway):
    gateway(PARITY_PROMPT)
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider())
    assert result.metrics['parity'] == {'status': 'SKIPPED', 'reason': 'no_intake_manifest'}


def test_oversized_source_is_skipped_not_truncated(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)
    big = 'x' * (PARITY_MAX_CHARS + 1)
    with repo.connect() as c:
        repo.insert(c, 'generated_artifacts',
                    {'id': uid(), 'job_id': job['id'], 'plan_id': 'plan-1', 'path': 'Big.java', 'content': big,
                     'provenance': '{}', 'region_policy': '', 'content_hash': digest(big), 'created_at': now()})
    provider = ParityProvider()
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
    provider = ParityProvider()
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
    result = run(repo, job, executed_package, parity=True, provider=ParityProvider())
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
    """Answers each static check with its own report, chosen by the skill it was handed."""
    accepts_skill = True

    def __init__(self, parity=REPORT, hardcoding=HARDCODING_REPORT):
        self.by_focus = {PARITY_SKILL_FILE: parity, HARDCODING_SKILL_FILE: hardcoding}
        self.calls = []

    def invoke(self, template, payload, skill=None):
        self.calls.append({'template': template, 'payload': payload, 'skill': skill})
        return self.by_focus.get(getattr(skill, 'focused_on', ''), {'answer': 'unrelated'})


@pytest.fixture
def both_prompts(repo, gateway):
    """Both checks' prompts APPROVED. ``gateway`` registers the model once, so the second prompt is added directly."""
    gateway(PARITY_PROMPT, template='PARITY TEMPLATE')

    class _Approver:
        identity = 'board@example'
        roles = ('AI_GOVERNANCE_ADMIN',)

    AIRegistryService(repo).register_prompt(
        {'name': HARDCODING_PROMPT, 'version': '1', 'template': 'HARDCODING TEMPLATE', 'status': 'APPROVED'},
        _Approver())


def calls_for(provider, template):
    return [c for c in provider.calls if c['template'] == template]


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
    provider = RoutingProvider()
    result = run(repo, job, executed_package, provider=provider)
    assert 'hardcoding' not in result.metrics and 'parity' not in result.metrics
    assert not calls_for(provider, 'HARDCODING TEMPLATE')


def test_hardcoding_runs_with_its_own_skill_and_leaves_the_contract_alone(repo, job, executed_package, seeded,
                                                                          both_prompts):
    provider = RoutingProvider()
    result = run(repo, job, executed_package, hardcoding=True, provider=provider)
    assert set(result.outputs) == {'variance_records', 'severity'}
    assert 'parity' not in result.metrics
    summary = result.metrics['hardcoding']
    assert summary['status'] == 'ANALYSED' and summary['counts'] == HARDCODING_REPORT['counts']
    assert summary['suggestions'] == 1
    call, = calls_for(provider, 'HARDCODING TEMPLATE')
    assert call['skill'].focused_on == 'hardcoding-skill.md'
    assert not calls_for(provider, 'PARITY TEMPLATE')


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
    provider = RoutingProvider()
    run(repo, job, executed_package, hardcoding=True, parity=True, provider=provider)
    hardcoding, = calls_for(provider, 'HARDCODING TEMPLATE')
    assert set(hardcoding['payload']['ms_files']) == {'src/main/java/OrdersService.java'}
    assert set(hardcoding['payload']['iib_files']) == {'Orders.msgflow', 'Orders.esql', 'Order.xsd',
                                                       'Orders.msgmap'}
    parity, = calls_for(provider, 'PARITY TEMPLATE')
    assert 'src/main/resources/application.yml' in parity['payload']['ms_files']     # parity keeps config
    assert 'Orders.wsdl' in parity['payload']['iib_files']


def test_hardcoding_is_stored_under_its_own_report_type_and_never_as_a_variance(repo, job, executed_package,
                                                                               seeded, both_prompts):
    before = repo.one('select count(*) n from variances')['n']
    result = run(repo, job, executed_package, hardcoding=True, provider=RoutingProvider())
    row = repo.one("select * from validation_reports where report_type='HARDCODING_ANALYSIS'")
    assert row['status'] == 'ANALYSED' and row['reference_id'] == 'analysis-1'
    stored = json.loads(row['payload'])
    assert stored['report']['action'] == 'hardcoding_check'
    assert stored['provenance']['skill_focus'] == 'hardcoding-skill.md'
    assert result.metrics['hardcoding']['report_id'] == row['id']
    assert [r['report_type'] for r in analysis_reports(repo)] == ['HARDCODING_ANALYSIS']
    assert repo.one('select count(*) n from variances')['n'] == before + 1       # only the runtime comparison's


def test_both_checks_in_one_run_stay_separate(repo, job, executed_package, seeded, both_prompts):
    provider = RoutingProvider()
    result = run(repo, job, executed_package, parity=True, hardcoding=True, provider=provider)
    assert result.metrics['parity']['status'] == 'ANALYSED'
    assert result.metrics['hardcoding']['status'] == 'ANALYSED'
    assert result.metrics['parity']['report_id'] != result.metrics['hardcoding']['report_id']
    assert calls_for(provider, 'PARITY TEMPLATE')[0]['skill'].focused_on == 'parity-skill.md'
    assert calls_for(provider, 'HARDCODING TEMPLATE')[0]['skill'].focused_on == 'hardcoding-skill.md'
    assert [r['report_type'] for r in analysis_reports(repo)] == ['HARDCODING_ANALYSIS', 'PARITY_ANALYSIS']


def test_one_checks_report_is_not_accepted_as_the_others(repo, job, executed_package, seeded, both_prompts):
    """A model that answers a check with the other check's JSON must not be stored as that check."""
    swapped = RoutingProvider(parity=HARDCODING_REPORT, hardcoding=REPORT)
    result = run(repo, job, executed_package, parity=True, hardcoding=True, provider=swapped)
    assert result.metrics['parity'] == {'status': 'INVALID_RESPONSE', 'reason': 'not_a_parity_check_report'}
    assert result.metrics['hardcoding'] == {'status': 'INVALID_RESPONSE',
                                            'reason': 'not_a_hardcoding_check_report'}
    assert analysis_reports(repo) == []


def test_hardcoding_without_an_approved_prompt_is_not_analysed(repo, job, executed_package, seeded, gateway):
    gateway(PARITY_PROMPT)                              # parity is approved, hardcoding is not
    result = run(repo, job, executed_package, hardcoding=True, parity=True, provider=RoutingProvider())
    assert result.metrics['parity']['status'] == 'ANALYSED'
    assert result.metrics['hardcoding'] == {'status': 'NOT_ANALYSED', 'mode': 'DETERMINISTIC_FALLBACK',
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
    result = run(repo, job, executed_package, hardcoding=True, parity=True, provider=RoutingProvider())
    assert result.metrics['hardcoding'] == {'status': 'SKIPPED', 'reason': 'no_ms_java_files'}
    assert result.metrics['parity']['status'] == 'ANALYSED'


def test_a_damaged_skill_skips_hardcoding_without_touching_the_runtime_comparison(
        repo, job, executed_package, seeded, both_prompts, tmp_path, monkeypatch):
    import shutil
    copy = tmp_path / 'skills'
    shutil.copytree(DEFAULT_ROOT, copy)
    (copy / 'iib-to-ms-variance-analysis' / 'references' / 'hardcoding-skill.md').write_text('tampered')
    monkeypatch.setenv('FACTORY_AGENT_SKILLS_DIR', str(copy))
    result = run(repo, job, executed_package, hardcoding=True, provider=RoutingProvider())
    assert result.metrics['hardcoding']['status'] == 'SKIPPED'
    assert 'hardcoding_skill_unavailable:skill_file_modified' in result.metrics['hardcoding']['reason']
    assert result.outputs['variance_records'][0]['status'] == 'VARIANCE'
