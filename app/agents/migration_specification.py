"""Migration Specification Agent.

Authors the schema-validated migration specification from an approved ACE
analysis: the contract that says what is being migrated, to what, and what
remains unresolved. Validation is delegated to ``SpecificationService``, which
holds the JSON Schema and records the errors.

The document is authored by the model from its skill, and there is no
deterministic composer behind it any more. Two consequences worth knowing:

``AnalysisSpecificationConsistencyValidator`` -- which catches a specification
that has quietly dropped part of the analysis it claims to describe -- is no
longer satisfied by construction. It is a real check on a real answer now, and
the document schema is enforced inside the retry loop (see ``document_valid``)
so a specification that fails it is re-asked rather than replaced.

Target modules used to be resolved here through
``migration_lifecycle_v6.mapping_key`` against the approved mapping table. That
knowledge now lives in the ``iib-to-ms-migration-specification`` skill, which is
the stated source of standards; the table is deliberately *not* passed in the
payload, because doing so would make the skill decorative.

Everything the document requires that is not in the semantic model -- the
service name, the source commit, the analysis version, the governance block --
is passed explicitly. A model cannot guess those, and the composer that used to
supply them is gone.
"""
import json
from pathlib import Path
import yaml
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.artefact import produce
from app.services.specification import SpecificationService

SCHEMA = Path(__file__).resolve().parents[2] / 'schemas' / 'migration-specification.schema.json'
#: Passed to the model rather than written into the document here: the stop
#: conditions are a governance fact, not something a model may invent, and the
#: document schema requires them.
STOP_CONDITIONS = ['iteration-limit', 'unresolved-clarification', 'failed-gate']


class MigrationSpecificationAgent(BaseAgent):
    AGENT_ID = 'migration-specification'
    SKILL = 'iib-to-ms-migration-specification'
    TOOLS = ('evidence.read', 'schema.validate', 'evidence.write')
    OUTPUTS = ('migration_specification_yaml',)
    PROMPT = 'agent/migration-specification/authoring'

    def execute(self, context):
        job = context.job()
        version = context.param('version', '1.0.0')
        analysis = self.repo.one('select * from ace_analyses where id=? and job_id=?',
                                 (context.param('analysis_id'), context.job_id))
        if not analysis:
            raise ValueError('analysis_not_found')
        framework = None
        if context.param('framework_id', ''):
            framework = self.repo.one('select * from framework_baselines where id=? and job_id=?',
                                      (context.param('framework_id'), context.job_id))
        model = json.loads(analysis['semantic_model'])
        ambiguities = json.loads(analysis['ambiguities'] or '[]')
        gates = [x['gate'] for x in self.repo.rows(
            'select gate from approval_policies where enabled=1 order by gate')]
        dependencies = []
        if framework:
            dependencies = sorted(json.loads(framework['standards'] or '{}').get('dependencies') or [])
        service = SpecificationService(self.repo, SCHEMA)

        def document_valid(answer):
            """The specification's own schema, checked inside the retry loop.

            ``schemas/migration-specification.schema.json`` is a separate
            document with rules the response schema cannot express, and an
            invalid specification blocks G2. This used to substitute a composed
            one; now the violation goes back to the model as a correction, which
            is the only path that can actually produce a valid document.
            """
            spec = (answer or {}).get('specification')
            if not isinstance(spec, dict):
                return 'specification missing or not an object'
            errors = service.validate(yaml.safe_dump(spec))[1]
            return ('specification failed its document schema: '
                    + json.dumps(errors)[:300]) if errors else ''

        # The payload carries every fact the document requires that is not in
        # the semantic model. It used to carry only the model, the standards and
        # the schema -- enough to comment on a specification, not enough to
        # author one: metadata.service, sourceCommit, analysisVersion and the
        # whole governance block were supplied by the deterministic composer,
        # which the model has no way to guess. Asking it to author the document
        # without them would have produced an answer that fails the document
        # schema on every attempt.
        authored, provenance = produce(
            context, self.PROMPT,
            {'semantic_model': model,
             'framework_standards': json.loads(framework['standards'] or '{}') if framework else {},
             'schema': service.schema,
             'metadata': {'service': job['service'],
                          'sourceCommit': job['source_revision'],
                          'analysisVersion': analysis['version'],
                          'frameworkTemplateVersion': framework['version'] if framework else version},
             'ace_version': str(context.param('ace_version', '12')),
             'approved_dependencies': dependencies,
             'clarifications': [self._clarification(a) for a in ambiguities],
             'governance': {'enabledGates': gates,
                            'iterationLimit': int(context.param('iteration_limit', 5)),
                            'stopConditions': STOP_CONDITIONS}},
            validator=document_valid)
        spec = authored['specification']
        text = yaml.safe_dump(spec, sort_keys=True, default_flow_style=False)
        saved = service.save(context.job_id, version, text)
        if saved['status'] != 'VALID':
            raise ValueError('specification_invalid:' + json.dumps(saved['errors'])[:400])
        metrics = {'flow_count': len(spec.get('source', {}).get('flows', [])),
                   'module_count': len(spec.get('target', {}).get('springModules', [])),
                   'clarification_count': len(ambiguities),
                   'model_attempts': provenance.get('attempts', 0)}
        return AgentResult(
            outputs={'migration_specification_yaml': saved['id']},
            # A specification's content_hash is the digest of its YAML text, so a
            # G2 approval binding this evidence binds the specification itself.
            evidence=(EvidenceRef('MIGRATION_SPECIFICATION', version,
                                  'specification://' + saved['id'], text,
                                  {'analysis_id': analysis['id'],
                                   'validation_status': saved['status']}),),
            metrics=metrics, reasoning=provenance)

    def _clarification(self, a):
        """Backfill a model-authored ambiguity to the shape this document requires.

        ace-analysis's own response schema requires only 'question' on an
        ambiguity (status/owner/impact/evidenceReference are all optional
        there) -- confirmed live: a real analysis authored 84 with no
        'status' at all. This document's schema requires all five, so an
        ambiguity stored before enterprise_v5.ACEParser.finalise() started
        normalising this itself would otherwise break every specification
        built from it, including this deterministic fallback.
        """
        a = dict(a) if isinstance(a, dict) else {}
        a.setdefault('status', 'OPEN')
        a.setdefault('question', '')
        a.setdefault('owner', 'ACE_SME')
        a.setdefault('impact', '')
        a.setdefault('evidenceReference', '')
        return a


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(MigrationSpecificationAgent))
