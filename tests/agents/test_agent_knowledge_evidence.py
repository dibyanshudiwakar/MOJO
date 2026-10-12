from app.agents.knowledge_evidence import KnowledgeEvidenceAgent


def test_the_jobs_artefacts_are_indexed_with_their_lineage(repo, job, planned):
    result = KnowledgeEvidenceAgent(repo).run(job['id'])
    kinds = {x['kind'] for x in result.outputs['version_lineage']}
    assert kinds == {'INTAKE_MANIFEST', 'FRAMEWORK_TEMPLATE', 'ACE_ANALYSIS',
                     'MIGRATION_SPECIFICATION', 'MIGRATION_PLAN'}
    assert result.metrics['indexed_count'] == result.metrics['artefact_count']
    rows = repo.rows('select * from knowledge_index where job_id=?', (job['id'],))
    # A citation names a specific version of an artefact, not "the analysis".
    assert all(x['lineage'] and '#' in x['lineage'] for x in rows)


def test_indexing_twice_does_not_split_citations_across_duplicates(repo, job, planned):
    first = KnowledgeEvidenceAgent(repo).run(job['id'])
    second = KnowledgeEvidenceAgent(repo).run(job['id'])
    assert first.metrics['indexed_count'] > 0
    assert second.metrics['indexed_count'] == 0
    assert second.metrics['artefact_count'] == first.metrics['artefact_count']
    assert repo.one('select count(*) n from knowledge_index where job_id=?',
                    (job['id'],))['n'] == first.metrics['indexed_count']


def test_a_query_returns_ranked_citations_carrying_content_hashes(repo, job, planned):
    KnowledgeEvidenceAgent(repo).run(job['id'])
    result = KnowledgeEvidenceAgent(repo).run(job['id'], params={'query': 'Orders.msgflow HTTPInput'})
    cited = result.outputs['cited_context']
    assert cited, 'the analysis mentions the flow, so it should be cited'
    assert cited == sorted(cited, key=lambda x: (-x['score'], x['reference']))
    assert all(x['content_hash'] for x in cited)


def test_it_registers_no_evidence(repo, job, planned):
    """Its contract grants index.write; an agent that could add to the evidence
    store while indexing it could manufacture the citation it returns."""
    result = KnowledgeEvidenceAgent(repo).run(job['id'])
    assert result.evidence == ()


def test_a_job_with_no_artefacts_indexes_nothing(repo, job):
    result = KnowledgeEvidenceAgent(repo).run(job['id'])
    assert result.outputs['version_lineage'] == []
    assert result.outputs['cited_context'] == []
