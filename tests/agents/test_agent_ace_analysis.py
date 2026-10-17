import json
import pytest
from app.agents.ace_analysis import ACEAnalysisAgent


def test_analysis_builds_a_semantic_model_bindable_at_g2(repo, job, ace_repo):
    result = ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)})
    assert result.status == 'COMPLETED'
    row = repo.one('select * from ace_analyses where id=?', (result.outputs['analysis_document'],))
    model = json.loads(row['semantic_model'])
    assert [x['path'] for x in model['flows']] == ['Orders.msgflow']
    assert model['interfaces'][0]['protocol'] == 'HTTP'
    assert model['interfaces'][0]['endpoint'] == '/orders'
    # An analysis hashes its semantic model, so a G2 approval binding this
    # evidence also binds the model the planning agent then reads.
    ref, = result.evidence
    assert ref.content_hash == row['content_hash']
    assert result.outputs['semantic_model'] == row['content_hash']


def test_an_unsupported_node_is_recorded_and_the_agent_raises_the_clarification(repo, job, tmp_path):
    root = tmp_path / 'vendor'
    root.mkdir()
    (root / 'V.msgflow').write_text('<flow><node id="n1" type="VendorCustomNode"/></flow>')
    result = ACEAnalysisAgent(repo).run(job['id'], params={'path': str(root)})
    assert result.status == 'CLARIFICATION_REQUIRED'
    assert result.metrics['unsupported_count'] == 1
    assert repo.one('select count(*) n from unsupported_constructs where job_id=?', (job['id'],))['n'] == 1
    assert result.outputs['ambiguities'], 'the ambiguity must still be surfaced in outputs too'
    # record_analysis stores the ambiguity on the analysis record but does not
    # insert it as a clarification -- clarification blocking is disabled at
    # that layer (see its own comment). This agent holds clarification.create,
    # so it is the one that must turn the still-OPEN ambiguity into an
    # answerable question, or nothing downstream (AgentRegistry.dispatch, the
    # pipeline's WAITING_CLARIFICATION park) ever has anything to show.
    assert len(result.clarifications) == 1
    clar = result.clarifications[0]
    assert clar.stage == 'ACE_ANALYSIS'
    assert clar.question == result.outputs['ambiguities'][0]['question']


def test_coverage_is_reported_for_undetected_areas(repo, job, ace_repo):
    result = ACEAnalysisAgent(repo).run(job['id'], params={'path': str(ace_repo)})
    report = repo.one("select * from validation_reports where job_id=? and report_type='PARSER_COVERAGE'",
                      (job['id'],))
    assert report['status'] == 'REVIEW_REQUIRED'
    assert 0 < result.metrics['coverage_score'] < 100


def test_analysis_requires_a_path(repo, job):
    with pytest.raises(ValueError, match='missing_param:path'):
        ACEAnalysisAgent(repo).run(job['id'])
