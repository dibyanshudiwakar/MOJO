import pytest
import yaml
from app.agents.ace_analysis import ACEAnalysisAgent
from app.agents.framework_baseline import FrameworkBaselineAgent
from app.agents.migration_specification import MigrationSpecificationAgent
from app.services.priority1 import AnalysisSpecificationConsistencyValidator


@pytest.fixture
def analysis(repo, job, ace_repo):
    return ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)}).outputs['analysis_document']


def test_specification_validates_against_the_schema(repo, job, analysis):
    result = MigrationSpecificationAgent(repo).run(job['id'], params={'analysis_id': analysis})
    row = repo.one('select * from migration_specs where id=?',
                   (result.outputs['migration_specification_yaml'],))
    assert row['validation_status'] == 'VALID'
    spec = yaml.safe_load(row['yaml_text'])
    assert spec['metadata']['service'] == 'Orders'
    assert spec['metadata']['sourceCommit'] == 'abc123'
    assert spec['governance']['enabledGates'] == ['G0', 'G1', 'G2', 'G3', 'G4', 'G5']
    # A specification hashes its YAML text, so a G2 approval binding this evidence
    # binds the document itself.
    ref, = result.evidence
    assert ref.content_hash == row['content_hash']


def test_the_specification_it_authors_passes_the_consistency_check(repo, job, analysis):
    """The check exists to catch a specification that dropped part of its analysis."""
    result = MigrationSpecificationAgent(repo).run(job['id'], params={'analysis_id': analysis})
    model = repo.one('select semantic_model from ace_analyses where id=?', (analysis,))['semantic_model']
    text = repo.one('select yaml_text from migration_specs where id=?',
                    (result.outputs['migration_specification_yaml'],))['yaml_text']
    report = AnalysisSpecificationConsistencyValidator().validate(model, text)
    assert report['status'] == 'PASS', report['findings']


def test_target_modules_come_from_the_approved_mapping_table(repo, job, analysis):
    result = MigrationSpecificationAgent(repo).run(job['id'], params={'analysis_id': analysis})
    spec = yaml.safe_load(repo.one('select yaml_text from migration_specs where id=?',
                                   (result.outputs['migration_specification_yaml'],))['yaml_text'])
    # HTTPInput/HTTPReply -> web, ComputeNode -> service, ESQL -> service, SCHEMA -> model
    assert spec['target']['springModules'] == ['model', 'service', 'web']


def test_approved_dependencies_are_taken_from_the_framework_baseline(repo, job, analysis, framework_repo):
    framework = FrameworkBaselineAgent(repo).run(
        job['id'], params={'path': str(framework_repo)}).outputs['framework_template']
    result = MigrationSpecificationAgent(repo).run(
        job['id'], params={'analysis_id': analysis, 'framework_id': framework})
    spec = yaml.safe_load(repo.one('select yaml_text from migration_specs where id=?',
                                   (result.outputs['migration_specification_yaml'],))['yaml_text'])
    assert 'spring-boot-starter-web' in spec['target']['approvedDependencies']


def test_an_ambiguity_missing_status_from_the_model_still_produces_a_valid_specification(repo, job, analysis):
    """ace-analysis's own response schema requires only 'question' on an
    ambiguity -- confirmed live against a real model-authored run that
    produced 84 of them with no 'status' at all, which broke every
    specification built from that analysis. Pins that this is backfilled."""
    import json
    with repo.connect() as c:
        c.execute('update ace_analyses set ambiguities=? where id=?',
                  (json.dumps([{'question': 'no status at all', 'owner': 'x',
                                'impact': 'y', 'evidenceReference': 'z'}]), analysis))
    result = MigrationSpecificationAgent(repo).run(job['id'], params={'analysis_id': analysis})
    row = repo.one('select validation_status,yaml_text from migration_specs where id=?',
                   (result.outputs['migration_specification_yaml'],))
    assert row['validation_status'] == 'VALID'
    spec = yaml.safe_load(row['yaml_text'])
    assert spec['clarifications'][0]['status'] == 'OPEN'


def test_specification_requires_an_analysis_that_exists(repo, job):
    with pytest.raises(ValueError, match='missing_param:analysis_id'):
        MigrationSpecificationAgent(repo).run(job['id'])
    with pytest.raises(ValueError, match='analysis_not_found'):
        MigrationSpecificationAgent(repo).run(job['id'], params={'analysis_id': 'nope'})
