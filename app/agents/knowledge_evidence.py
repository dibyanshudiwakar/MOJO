"""Knowledge and Evidence Agent.

Indexes the job's produced artefacts so later agents can cite them, and returns
the version lineage of what it indexed.

Its contract grants ``index.write`` but not ``evidence.write``: the index is a
retrieval aid built *from* evidence, and an agent that could add to the evidence
store while indexing it could manufacture the citation it then returns. Each
entry records the artefact's own ``content_hash``, so a citation names a specific
version of an artefact rather than "the analysis" -- an index entry whose hash no
longer matches its source is a stale citation and visibly so.

Indexing is idempotent by (reference, content hash), so running this agent
repeatedly across a lifecycle does not split citations across duplicate entries.
"""
from app.agents.base import AgentResult, BaseAgent
from app.agents.artefact import produce
from app.repository import digest
from app.services.platform_v7 import KnowledgeService

#: source table, evidence kind, content column, version column, uri scheme
SOURCES = (
    ('intake_manifests', 'INTAKE_MANIFEST', 'inventory', None, 'intake'),
    ('framework_baselines', 'FRAMEWORK_TEMPLATE', 'template_md', 'version', 'framework'),
    ('ace_analyses', 'ACE_ANALYSIS', 'analysis_md', 'version', 'analysis'),
    ('migration_specs', 'MIGRATION_SPECIFICATION', 'yaml_text', 'version', 'specification'),
    ('migration_plans', 'MIGRATION_PLAN', 'tasks', 'version', 'plan'),
)


class KnowledgeEvidenceAgent(BaseAgent):
    AGENT_ID = 'knowledge-evidence'
    SKILL = 'iib-to-ms-knowledge-evidence'
    TOOLS = ('evidence.read', 'index.write', 'index.read')
    OUTPUTS = ('cited_context', 'version_lineage')
    #: Judges whether what was retrieved is enough to answer the query. Note
    #: this agent's catalogue alias was ``embeddings``, which Claude cannot
    #: serve -- there is no embeddings endpoint -- so it is now a chat alias and
    #: this is a judgement prompt, not a vector operation. Retrieval itself is
    #: still ``KnowledgeService.search``.
    PROMPT = 'agent/knowledge-evidence/citations'

    def _artefacts(self, job_id):
        for table, kind, column, version_column, scheme in SOURCES:
            for row in self.repo.rows('select * from %s where job_id=? order by created_at' % table, (job_id,)):
                content = str(row.get(column) or '')
                if not content.strip():
                    continue
                yield {'kind': kind, 'reference': scheme + '://' + row['id'],
                       'version': str(row.get(version_column) or '1') if version_column else '1',
                       'artefact_id': row['id'], 'content_hash': row.get('content_hash') or '',
                       'content': content}

    def _cite(self, context, query, lineage, retrieved):
        """Which indexed artefact answers the query, chosen by the model.

        Resolution against the lineage is deliberate and is what enforces the
        prompt's rule rather than trusting it: a citation naming a reference the
        job never indexed is dropped here, because a fabricated citation
        corrupts the audit trail the index exists to serve. The retrieval score
        is the deterministic one -- it is a measurement of the index, not a
        judgement.

        Hashes reach the model as a 12-character prefix: full digests are long
        hex runs and this payload is almost nothing but hashes, so sending them
        whole would have the data-loss scan refuse most runs as PANs. A prefix
        still identifies the version and still shows an absent hash.
        """
        if not query.strip():
            return retrieved, {'mode': 'DETERMINISTIC_FALLBACK', 'prompt_name': self.PROMPT,
                               'reason': 'no_query_supplied',
                               'artefact_source': 'DETERMINISTIC'}
        by_reference = {x['reference']: x for x in lineage}
        answer, provenance = produce(
            context, self.PROMPT,
            {'query': query,
             'lineage': [{'kind': x['kind'], 'reference': x['reference'], 'version': x['version'],
                          'content_hash': (x['content_hash'] or '')[:12]} for x in lineage],
             'retrieved': [{'kind': x['kind'], 'reference': x['reference'],
                            'version': x['version'], 'score': x['score'],
                            'content_hash': (x['content_hash'] or '')[:12]} for x in retrieved]},
            lambda: {'citations': [{'evidence_id': x['reference'], 'claim': query,
                                    'locator': x['kind']} for x in retrieved],
                     'uncited_claims': []})
        if provenance.get('artefact_source') != 'MODEL':
            return retrieved, provenance
        scores = {x['reference']: x['score'] for x in retrieved}
        out = []
        for citation in answer.get('citations') or []:
            row = by_reference.get(citation.get('evidence_id'))
            if row is None:
                continue
            out.append({**row, 'score': scores.get(row['reference'], 0.0),
                        'claim': citation.get('claim', ''),
                        'locator': citation.get('locator', '')})
        provenance['citations_dropped'] = len(answer.get('citations') or []) - len(out)
        provenance['uncited_claims'] = answer.get('uncited_claims') or []
        return out or retrieved, provenance

    def execute(self, context):
        service = KnowledgeService(self.repo)
        indexed = []
        lineage = []
        for item in self._artefacts(context.job_id):
            entry = {k: v for k, v in item.items() if k != 'content'}
            lineage.append(entry)
            if self.repo.one('select id from knowledge_index where job_id=? and reference=? and content_hash=?',
                             (context.job_id, item['reference'], digest(item['content']))):
                continue
            result = service.index({'job_id': context.job_id, 'kind': item['kind'],
                                    'reference': item['reference'], 'content': item['content'],
                                    'version': item['version'],
                                    'lineage': '%s:%s@%s#%s' % (item['kind'], item['artefact_id'],
                                                                item['version'], item['content_hash'])})
            indexed.append({**entry, 'index_id': result['id'], 'token_count': result['token_count']})
        query = str(context.param('query', ''))
        cited = service.search(query, context.job_id) if query.strip() else []
        # Hashes are truncated to a 12-character prefix: full digests are long
        # hex runs and this payload is almost nothing but hashes, so sending
        # them whole would have the data-loss scan refuse most runs as PANs. A
        # prefix still identifies the version and still shows an absent hash.
        cited, provenance = self._cite(context, query, lineage, cited)
        metrics = {'indexed_count': len(indexed), 'artefact_count': len(lineage),
                   'citation_count': len(cited), 'query': query,
                   'citations_from_model': provenance.get('artefact_source') == 'MODEL'}
        return AgentResult(
            outputs={'cited_context': [{'reference': x['reference'], 'kind': x['kind'],
                                        'version': x['version'], 'content_hash': x['content_hash'],
                                        'score': x['score'], 'lineage': x.get('lineage') or ''}
                                       for x in cited],
                     'version_lineage': lineage},
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(KnowledgeEvidenceAgent))
