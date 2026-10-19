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
from app.agents.artefact import STRUCTURED_EFFORT, produce_many
from app.services import automode
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

#: Worst to least, for picking the worst of several ratings.
SEVERITIES = ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
#: What an unrated gap is taken to be. The worst, because it is the rating that
#: stops the plan: a missing rating must fail safe, not slip past as minor.
WORST = SEVERITIES[-1]


def _rank(rating):
    return SEVERITIES.index(rating) if rating in SEVERITIES else len(SEVERITIES)


#: Status of a gap auto mode planned around. Distinct from OPEN on purpose:
#: ``generate_plan`` blocks only on OPEN, and the name says who decided.
ACCEPTED = 'ACCEPTED_BY_AUTO_MODE'


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
             for i, g in enumerate(groups)],
            effort=STRUCTURED_EFFORT)
        mapped = self._merge(pieces[i] for i in sorted(pieces))
        accepted = 0
        if automode.enabled():
            mapped, accepted = self._accept_minor_gaps(mapped)
        catalogue = service.record_mapping_catalogue(
            context.job_id, framework_id, analysis_id, specification_id, mapped, version)
        risks = self.repo.rows(
            'select id,category,severity,statement,response,owner,status from migration_risks'
            ' where catalogue_id=? order by created_at', (catalogue['catalogue_id'],))
        still_open = self.repo.one(
            "select count(*) n from unsupported_mappings where catalogue_id=? and status='OPEN'",
            (catalogue['catalogue_id'],))['n']
        metrics = {'mapping_count': catalogue['mapping_count'],
                   'unsupported_count': catalogue['unsupported_count'],
                   'accepted_risk_count': accepted,
                   'risk_count': len(risks),
                   'model_attempts': provenance.get('attempts', 0)}
        if still_open:
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
    def _accept_minor_gaps(mapped):
        """Plan around an unsupported construct the model rates below HIGH.

        Auto mode's rule for questions, applied to the other thing planning
        stops on. The gap is not hidden: the unsupported entry and its risk are
        recorded with a status naming auto mode, so they appear in the risk
        register and in the G3 package, and the approver sees exactly what the
        plan leaves out. ``generate_plan`` blocks only on ``OPEN``, so this is
        the whole mechanism -- nothing is deleted and no mapping is invented.

        A CRITICAL gap, or one with no matching risk (unrated is treated as
        the worst case), stays OPEN and stops the plan to be asked.
        """
        severity = {}
        for risk in mapped.get('risks') or []:
            ref = risk.get('source_reference') or ''
            rating = str(risk.get('severity') or WORST).upper()
            # Several risks for one construct: the worst one decides.
            if ref not in severity or _rank(rating) > _rank(severity[ref]):
                severity[ref] = rating
        accepted_refs = set()
        unsupported = []
        for entry in mapped.get('unsupported') or []:
            ref = entry.get('source_reference') or ''
            if severity.get(ref, WORST) != automode.ASK_SEVERITY:
                accepted_refs.add(ref)
                entry = {**entry, 'status': ACCEPTED}
            unsupported.append(entry)
        risks = [{**r, 'status': ACCEPTED} if (r.get('source_reference') or '') in accepted_refs else r
                 for r in mapped.get('risks') or []]
        return {**mapped, 'unsupported': unsupported, 'risks': risks}, len(accepted_refs)

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
        # The risk for an unsupported construct is derived from the entry, not
        # authored as a separate register. The register was where the reply grew
        # past max_tokens -- twice, at 39,313 and then 45,270 characters, from
        # long "SILENTLY_WRONG" write-ups for a single ESQL module -- and no
        # prompt wording held it down, because the planning skill asks for an
        # exhaustive one. An unrated gap is HIGH, which is the service's own
        # default and the safe one: it is the rating that stops the plan.
        rated = {r.get('source_reference') for r in merged['risks']}
        for entry in merged['unsupported']:
            ref = entry.get('source_reference') or ''
            if ref in rated:
                continue
            merged['risks'].append({
                'category': 'UNSUPPORTED_MAPPING',
                'severity': str(entry.get('severity') or WORST).upper(),
                'source_reference': ref,
                'statement': (entry.get('risk') or 'No approved Spring mapping for %s'
                              % (entry.get('ace_construct') or 'this construct'))[:400],
                'response': 'Clarify or implement approved plug-in mapping',
                'owner': entry.get('owner') or 'ACE_SME_AND_JAVA_ARCHITECT', 'status': 'OPEN'})
        return merged


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(PlanningAgent))
