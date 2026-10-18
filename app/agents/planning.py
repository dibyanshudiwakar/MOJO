"""Planning Agent.

Produces the mapping catalogue, the ordered implementation task graph and the
risk register that G3 approves. Wraps ``MappingAndPlanningService``, which is
where the gate checks live: creating a catalogue requires an artefact-bound G1
approval of the framework and G2 approvals of both the analysis and the
specification, and refuses outright while any clarification is open.

When the catalogue contains a construct with no approved Spring pattern, the
service records an unsupported mapping, a HIGH risk and a clarification, and
``generate_plan`` refuses. This agent stops there and reports the catalogue and
risks rather than planning around the gap: a plan that silently omitted an
unmappable construct would present as complete while leaving behaviour behind.

Unlike the analysis and framework agents, this one registers **no evidence**: its
approved contract grants ``evidence.read`` and ``plan.write``, not
``evidence.write``. It assembles the G3 approval package instead, which is the
artefact-binding record for the gate. Registering the plan as the evidence a G3
approval cites is therefore the approver's act, not the planner's -- see
``docs/AGENTS_DEVELOPMENT.md``.
"""
import json
import yaml
from app.agents.base import AgentResult, BaseAgent, Clarification
from app.agents.artefact import produce_many
from app.services.migration_lifecycle_v6 import MappingAndPlanningService


class PlanningAgent(BaseAgent):
    AGENT_ID = 'planning'
    SKILL = 'iib-to-ms-planning'
    TOOLS = ('evidence.read', 'plan.write', 'clarification.create')
    OUTPUTS = ('plan', 'task_graph', 'mapping', 'risk_register')
    PROMPT = 'agent/planning/mapping-catalogue'

    def execute(self, context):
        version = context.param('version', '1.0.0')
        service = MappingAndPlanningService(self.repo)
        # framework_id is optional: when framework-baseline was skipped, the
        # param resolves to '' and planning proceeds with default conventions.
        framework_id = context.param('framework_id', '') or ''
        analysis_id = context.param('analysis_id')
        specification_id = context.param('specification_id')
        # Gate-checked first and separately: G1/G2 approval binding is not
        # something a mapping strategy gets to skip, so it runs identically
        # whether the catalogue is model-authored or computed.
        framework, analysis, specification = service.planning_inputs(
            context.job_id, framework_id, analysis_id, specification_id)
        model = json.loads(analysis['semantic_model'])
        standards = json.loads(framework['standards']) if framework else {}
        # What the specification contributes to a mapping decision is the target
        # it fixed, not the document: the rest repeats the semantic model (and,
        # for a real service, 95 clarifications).
        spec = yaml.safe_load(specification['yaml_text']) or {}
        brief = {k: spec.get(k) for k in ('metadata', 'target') if k in spec}
        # Mapped in groups, not in one request. The whole model plus the whole
        # specification came to 245,531 characters against a 180,000 limit, so
        # planning failed before it reached the model -- and no retry can shrink
        # a request. The same bound that keeps ace-analysis per file.
        groups = self._groups(model)
        pieces, provenance = produce_many(
            context, self.PROMPT,
            [(i, {'semantic_model': g, 'framework_standards': standards, 'specification': brief})
             for i, g in enumerate(groups)])
        mapped = self._merge(pieces[i] for i in sorted(pieces))
        catalogue = service.record_mapping_catalogue(
            context.job_id, framework_id, analysis_id, specification_id, mapped, version)
        risks = self.repo.rows(
            'select id,category,severity,statement,response,owner,status from migration_risks'
            ' where catalogue_id=? order by created_at', (catalogue['catalogue_id'],))
        metrics = {'mapping_count': catalogue['mapping_count'],
                   'unsupported_count': catalogue['unsupported_count'],
                   'risk_count': len(risks),
                   'model_attempts': provenance.get('attempts', 0)}
        if catalogue['unsupported_count']:
            # The service records the gap as migration_risks/unsupported_mappings
            # rows but does not raise clarifications itself (clarification
            # blocking is disabled there -- see migration_lifecycle_v6.py). This
            # agent holds clarification.create, so it turns each still-OPEN risk
            # into an answerable question rather than leaving a human to find
            # the gap only in the risk register.
            clarifications = tuple(
                Clarification(stage='PLANNING', question=r['statement'],
                               owner=r['owner'], impact=r['response'] or r['category'],
                               evidence_ref='migration_risk://' + r['id'])
                for r in risks if r['status'] == 'OPEN')
            return AgentResult(
                status='CLARIFICATION_REQUIRED',
                outputs={'mapping': catalogue['catalogue_id'], 'risk_register': risks,
                         'task_graph': [], 'plan': ''},
                clarifications=clarifications,
                metrics=metrics, reasoning=provenance,
                error='')
        plan = service.generate_plan(context.job_id, catalogue['catalogue_id'], version)
        row = self.repo.one('select * from migration_plans where id=?', (plan['plan_id'],))
        tasks = json.loads(row['tasks'])
        package = service.g3_package(context.job_id, plan['plan_id'], catalogue['catalogue_id'])
        return AgentResult(
            outputs={'plan': plan['plan_id'],
                     'task_graph': [{'id': x['id'], 'depends_on': x['depends_on'],
                                     'source_reference': x['source_reference'],
                                     'boundary': x['boundary']} for x in tasks],
                     'mapping': catalogue['catalogue_id'],
                     'risk_register': risks},
            metrics={**metrics, 'task_count': plan['task_count'],
                     'plan_content_hash': row['content_hash'],
                     'g3_package_id': package['package_id'],
                     'implementation_sequence': plan['implementation_sequence']},
            reasoning=provenance)

    #: Constructs that need a mapping, in the order they are grouped.
    KINDS = ('flows', 'subflows', 'esql', 'maps', 'schemas', 'policies')
    #: Characters of constructs per request. This bounds the *answer* as much as
    #: the request: at 60000 a whole service's 26 constructs went in one request
    #: and the mapping reply ran past 32768 tokens on every attempt, ten minutes
    #: each. Small groups keep each reply a fraction of the limit and make a
    #: failure cost one group, not the whole catalogue.
    GROUP_CHARS = 8000
    #: Carried into every group unchanged: they inform a mapping but are not
    #: themselves mapped.
    CONTEXT = ('interfaces', 'behaviour', 'backend_type')

    def _groups(self, model):
        """The model split into requests of bounded size, one construct never split."""
        context = {k: model[k] for k in self.CONTEXT if k in model}
        groups, current, size = [], {}, 0
        for kind in self.KINDS:
            for item in model.get(kind) or []:
                length = len(json.dumps(item))
                if current and size + length > self.GROUP_CHARS:
                    groups.append({**context, **current})
                    current, size = {}, 0
                current.setdefault(kind, []).append(item)
                size += length
        if current:
            groups.append({**context, **current})
        return groups

    @staticmethod
    def _merge(pieces):
        """One catalogue from the groups' answers, each construct once."""
        merged = {'mappings': [], 'unsupported': [], 'risks': []}
        seen = set()
        for piece in pieces:
            for m in piece.get('mappings') or []:
                key = (m.get('source_reference'), m.get('ace_construct'))
                if key in seen:
                    continue
                seen.add(key)
                merged['mappings'].append(m)
            merged['unsupported'] += piece.get('unsupported') or []
            merged['risks'] += piece.get('risks') or []
        return merged


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(PlanningAgent))
