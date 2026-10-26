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
from jsonschema import Draft202012Validator
from app.agents import model_digest
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.artefact import STRUCTURED_EFFORT, produce_many
from app.services.specification import SpecificationService

SCHEMA = Path(__file__).resolve().parents[2] / 'schemas' / 'migration-specification.schema.json'
#: Passed to the model rather than written into the document here: the stop
#: conditions are a governance fact, not something a model may invent, and the
#: document schema requires them.
STOP_CONDITIONS = ['iteration-limit', 'unresolved-clarification', 'failed-gate']
#: The sections the model authors, one request each. The rest of the document
#: (metadata, governance, clarifications) is recorded fact attached by execute().
SECTIONS = ('source', 'interfaces', 'behaviour', 'target', 'mappings', 'tests')


class MigrationSpecificationAgent(BaseAgent):
    AGENT_ID = 'migration-specification'
    SKILL = 'iib-to-ms-migration-specification'
    TOOLS = ('evidence.read', 'schema.validate', 'evidence.write')
    OUTPUTS = ('migration_specification_yaml',)
    #: ``.../authoring`` (the whole document in one reply) stays registered but is
    #: no longer asked for; see the comment in execute().
    PROMPT = 'agent/migration-specification/section'

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

        metadata = {'service': job['service'],
                    'sourceCommit': job['source_revision'],
                    'analysisVersion': analysis['version'],
                    'frameworkTemplateVersion': framework['version'] if framework else version}
        governance = {'enabledGates': gates,
                      'iterationLimit': int(context.param('iteration_limit', 5)),
                      'stopConditions': STOP_CONDITIONS}
        slices = service.schema.get('properties', {})

        def section_valid(section, answer):
            """One section against its slice of the document schema, in the retry loop.

            ``schemas/migration-specification.schema.json`` has rules the
            response schema cannot express, and an invalid specification blocks
            G2. A violation goes back to the model as a correction, which is the
            only path that can produce a valid section.
            """
            if (answer or {}).get('section') != section:
                return 'section must be %r, got %r' % (section, (answer or {}).get('section'))
            errors = [e.message for e in Draft202012Validator(slices[section]).iter_errors(answer.get('content'))]
            return ('section %r failed its document schema: %s' % (section, json.dumps(errors)[:300])) if errors else ''

        # Authored a section at a time. The whole document in one reply overran
        # the gateway's 32768-token output limit on a real service (34
        # interfaces, 95 clarifications): the reply was cut off mid-JSON on all
        # three attempts, about four minutes each. A retry cannot fix a length
        # limit, so the request itself is bounded.
        #
        # The model writes the sections that are judgement over the semantic
        # model. metadata, governance and clarifications are recorded facts the
        # document must carry verbatim -- the service name, source commit and
        # enabled gates, and the analysis's own ambiguities -- so they are
        # attached here rather than asked for. Re-typing 95 clarifications
        # through a model is where most of the length went, and a transcription
        # a model can only get wrong.
        base = {'semantic_model': self._fit(model),
                'framework_standards': json.loads(framework['standards'] or '{}') if framework else {},
                'metadata': metadata,
                'ace_version': str(context.param('ace_version', '12')),
                'approved_dependencies': dependencies}
        authored, provenance = produce_many(
            context, self.PROMPT,
            [(s, {**base, 'section': s, 'section_schema': slices[s]}) for s in SECTIONS],
            validator=section_valid, effort=STRUCTURED_EFFORT)
        spec = {s: authored[s]['content'] for s in SECTIONS}
        spec.update({'metadata': metadata, 'governance': governance,
                     'clarifications': [self._clarification(a) for a in ambiguities]})
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

    #: Serialised size above which the semantic model is sent as a digest. The
    #: payload limit is 180,000 characters and every section request also carries
    #: the framework standards, the section's schema and the correction on a retry.
    MODEL_BUDGET = 120_000

    def _fit(self, model):
        """The semantic model as the request carries it, digested only if it must be.

        A real service came to 403,881 characters -- 124,087 of them 221
        ambiguities, which execute() attaches to the document verbatim, so the
        model never needs them. A model that fits is returned untouched; see
        ``app.agents.model_digest``.
        """
        for level in range(model_digest.LEVELS):
            if len(json.dumps(model)) <= self.MODEL_BUDGET:
                return model
            model = model_digest.shrink({'semantic_model': model}, level)['semantic_model']
        return model

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
