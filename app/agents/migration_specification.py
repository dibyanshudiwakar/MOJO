"""Migration Specification Agent.

Authors the schema-validated migration specification from an approved ACE
analysis: the contract that says what is being migrated, to what, and what
remains unresolved. Validation is delegated to ``SpecificationService``, which
holds the JSON Schema and records the errors.

The specification is written so
``AnalysisSpecificationConsistencyValidator`` passes: every flow, ESQL module,
map, schema and policy path from the semantic model is listed under ``source``,
and every parser ambiguity is carried into ``clarifications``. That validator
exists to catch a specification that has quietly dropped part of the analysis it
claims to describe, so an agent that authored a specification failing it would be
producing exactly the artefact the check was written to reject.

Target modules are resolved through ``migration_lifecycle_v6.mapping_key``
against the same approved mapping table the planning agent uses, so the
specification cannot promise a Spring module the mapping catalogue will refuse.
"""
import json
from pathlib import Path
import yaml
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.services.migration_lifecycle_v6 import DEFAULT_MAPPINGS, mapping_key
from app.services.specification import SpecificationService

SCHEMA = Path(__file__).resolve().parents[2] / 'schemas' / 'migration-specification.schema.json'
STOP_CONDITIONS = ['iteration-limit', 'unresolved-clarification', 'failed-gate']


def _paths(items):
    return sorted({x.get('path') for x in items or [] if isinstance(x, dict) and x.get('path')})


class MigrationSpecificationAgent(BaseAgent):
    AGENT_ID = 'migration-specification'
    SKILL = 'iib-to-ms-migration-specification'
    TOOLS = ('evidence.read', 'schema.validate', 'evidence.write')
    OUTPUTS = ('migration_specification_yaml',)
    PROMPT = 'agent/migration-specification/authoring'

    def _modules(self, model):
        """Approved target modules for the constructs the analysis found."""
        constructs = [n.get('type', '') for f in model.get('flows', []) + model.get('subflows', [])
                      for n in f.get('nodes', [])]
        constructs += ['ESQL'] * len(model.get('esql', []))
        constructs += [x.get('type', 'Mapping') for x in model.get('maps', [])]
        constructs += ['SCHEMA'] * len(model.get('schemas', []))
        constructs += ['POLICY'] * len(model.get('policies', []))
        keys = {mapping_key(x, DEFAULT_MAPPINGS) for x in constructs}
        return sorted({DEFAULT_MAPPINGS[k]['module'] for k in keys if k})

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
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'counts': {k: len(model.get(k, [])) for k in ('flows', 'subflows', 'esql', 'maps', 'schemas', 'policies')},
            'modules': self._modules(model)})
        spec = {
            'metadata': {'service': job['service'], 'sourceCommit': job['source_revision'],
                         'analysisVersion': analysis['version'],
                         'frameworkTemplateVersion': framework['version'] if framework else version},
            'source': {'aceVersion': str(context.param('ace_version', '12')),
                       'applications': [job['service']],
                       'libraries': [], 'flows': _paths(model.get('flows')),
                       'subflows': _paths(model.get('subflows')),
                       'nodes': sorted({n.get('type', '') for f in model.get('flows', []) + model.get('subflows', [])
                                        for n in f.get('nodes', []) if n.get('type')}),
                       'esql': _paths(model.get('esql')), 'maps': _paths(model.get('maps')),
                       'schemas': _paths(model.get('schemas')), 'policies': _paths(model.get('policies'))},
            'interfaces': model.get('interfaces', []),
            'behaviour': model.get('behaviour', {}),
            'target': {'springModules': self._modules(model), 'approvedDependencies': dependencies},
            # Construct-level mappings are the planning agent's output, under G2
            # approval of this specification. Naming them here would pre-empt a
            # decision this document is an input to.
            'mappings': [],
            'tests': {'inputSources': sorted({x.get('protocol', '') for x in model.get('interfaces', [])}),
                      'invariants': [], 'masks': [], 'tolerances': []},
            'clarifications': ambiguities,
            'governance': {'enabledGates': gates,
                           'iterationLimit': int(context.param('iteration_limit', 5)),
                           'stopConditions': STOP_CONDITIONS}}
        text = yaml.safe_dump(spec, sort_keys=True, default_flow_style=False)
        saved = SpecificationService(self.repo, SCHEMA).save(context.job_id, version, text)
        if saved['status'] != 'VALID':
            raise ValueError('specification_invalid:' + json.dumps(saved['errors'])[:400])
        metrics = {'flow_count': len(spec['source']['flows']),
                   'module_count': len(spec['target']['springModules']),
                   'clarification_count': len(ambiguities)}
        if isinstance(answer, dict) and answer.get('springModules'):
            metrics['model_modules'] = answer['springModules']
        return AgentResult(
            outputs={'migration_specification_yaml': saved['id']},
            # A specification's content_hash is the digest of its YAML text, so a
            # G2 approval binding this evidence binds the specification itself.
            evidence=(EvidenceRef('MIGRATION_SPECIFICATION', version,
                                  'specification://' + saved['id'], text,
                                  {'analysis_id': analysis['id'],
                                   'validation_status': saved['status']}),),
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(MigrationSpecificationAgent))
