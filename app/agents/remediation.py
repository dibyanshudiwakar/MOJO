"""Remediation Agent.

Scores an approved root cause against the governed remediation threshold and, when
it is eligible, records the bounded patch plan and the regression tests that must
prove it. Wraps ``ConfidenceRemediationService`` for the scoring rules and
``RCAService.record_remediation``, which refuses a remediation lacking an approved
pattern, tests or a rollback pointer.

Two boundaries this agent holds deliberately:

* The confidence threshold comes from configuration, never from the parameters.
  A remediation that could choose the bar it is judged against is not judged.
* Differential root causes stay plan-only: the agent records the change set an
  approved plan should carry, and edits nothing.
* Root causes found in the static reports (parity, hardcoding) are fixed in the
  generated project. The model proposes targeted search/replace edits, they are
  applied through the same path-confined check the feedback agent uses, and they are
  kept only if the quality gate is no worse afterwards (``remediation_fix``). The
  gate does not re-run the comparison, so a kept fix is "not regressed", never
  "proved closed", and is recorded as exactly that.

With no ``root_cause_id`` every cause still waiting is remediated; the run's report
(fixes applied, what could not be fixed, the gate verdict) is stored as a
``REMEDIATION_REPORT`` for the Output tab.
"""
import json
import os
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.artefact import produce
from app.repository import digest, now, uid
from app.services import feedback, remediation_fix, static_findings
from app.services.enterprise_v5 import ConfidenceRemediationService
from app.services.rca import RCAService

THRESHOLD_ENV = 'FACTORY_REMEDIATION_CONFIDENCE_THRESHOLD'
#: Findings from the static reports are scored without differential test cases, so they cannot
#: reach the bar a runtime variance is held to. Their bar is its own governed setting, never a parameter.
STATIC_THRESHOLD_ENV = 'FACTORY_REMEDIATION_STATIC_CONFIDENCE_THRESHOLD'
DEFAULT_TESTS = ['targeted_regression', 'impacted_suite']
STATIC_IMPACT_BOUND = 25
MAX_CAUSES = 25
REPORT_TYPE = 'REMEDIATION_REPORT'
#: The rollback pointer of a remediation that changed no source: there is nothing to undo.
NO_SOURCE_CHANGED = 'none:no-source-changed'


class RemediationAgent(BaseAgent):
    AGENT_ID = 'remediation'
    SKILL = 'iib-to-ms-remediation'
    TOOLS = ('repository.branch', 'repository.write', 'test.execute', 'evidence.write')
    OUTPUTS = ('patch_plan', 'code_changes', 'tests')
    PROMPT = 'agent/remediation/patch-plan'

    def _threshold(self):
        return float(os.getenv(THRESHOLD_ENV, '0.8'))

    def _static_threshold(self):
        return float(os.getenv(STATIC_THRESHOLD_ENV, '0.6'))

    def _preconditions(self, context, cause):
        """The eligibility facts, each read from a record rather than asserted."""
        variance_ids = json.loads(cause['variance_ids'] or '[]')
        catalogue = self.repo.one(
            'select id from mapping_catalogues where job_id=? order by created_at desc limit 1',
            (context.job_id,))
        plan = self.repo.one(
            'select tasks,acceptance from migration_plans where job_id=? order by created_at desc limit 1',
            (context.job_id,))
        tests_present = bool(plan) and any(
            x.get('tests') for x in json.loads(plan['tasks'] or '[]'))
        bound = int(context.param('impact_bound', 10))
        return {
            # An approved Spring pattern exists for this job only if a mapping
            # catalogue was actually produced under G1/G2.
            'approved_pattern': bool(catalogue),
            'bounded_impact': 0 < len(variance_ids) <= bound,
            'tests_present': tests_present,
            # A dependency the framework baseline never approved would put the fix
            # outside the governed template.
            'prohibited_dependency': bool(context.param('prohibited_dependency', False)),
            'variance_ids': variance_ids,
        }

    # -- causes ---------------------------------------------------------------
    @staticmethod
    def _static_scope(cause):
        """``(source, area)`` for a root cause found in a static report, else None."""
        scope = str(cause.get('affected_scope') or '')
        if not scope.startswith('STATIC:'):
            return None
        parts = scope.split(':', 2)
        return (parts[1], parts[2]) if len(parts) == 3 else None

    def _static_facts(self, context, cause, rows):
        facts = self._preconditions(context, cause)
        # A static cause has no variances; its impact is the findings it covers.
        facts['bounded_impact'] = 0 < len(rows) <= int(context.param('static_impact_bound', STATIC_IMPACT_BOUND))
        return facts

    def _assess(self, context, job, cause):
        """Eligibility and the model's bounded plan for one root cause. Nothing is changed."""
        scope = self._static_scope(cause)
        rows = ([r for r in static_findings.load(self.repo, context.job_id)
                 if (r['source'], r['area']) == scope] if scope else [])
        facts = self._static_facts(context, cause, rows) if scope else self._preconditions(context, cause)
        threshold = self._static_threshold() if scope else self._threshold()
        scorer = ConfidenceRemediationService()
        eligibility = scorer.eligibility(float(cause['confidence']), threshold,
                                        facts['approved_pattern'], facts['bounded_impact'],
                                        facts['tests_present'], facts['prohibited_dependency'])
        rollback = str(job['rollback_pointer'] or '').strip()
        variances = self.repo.rows(
            'select id,path,kind,severity from variances where id in (%s)'
            % ','.join('?' * len(facts['variance_ids'])), tuple(facts['variance_ids'])) if facts['variance_ids'] else []
        paths = sorted({x['path'] for x in variances} | {r['ms_source'] or r['iib_source'] for r in rows} - {''})
        proposal = scorer.propose({'path': ','.join(paths) or 'UNKNOWN', 'root_cause_id': cause['id']},
                                  eligibility, int(job['iteration'] or 1), rollback)
        # The model proposes the change set; eligibility stays deterministic.
        # Whether a remediation may proceed turns on the governed confidence
        # threshold, an approved pattern and a bounded blast radius -- that is
        # enforcement, and the model's claim that a change is bounded does not make it so.
        plan, provenance = produce(
            context, self.PROMPT,
            {'hypothesis': cause['hypothesis'], 'confidence': cause['confidence'],
             'variances': ([{'path': x['path'], 'kind': x['kind']} for x in variances]
                           + [{'path': r['ms_source'] or r['iib_source'], 'kind': r['source'],
                               'status': r['status'], 'remarks': r['remarks'][:300]} for r in rows])})
        # No ``or`` fallback on the change set: an empty model answer is an empty plan,
        # not a table lookup dressed as one.
        changes = [{'source_reference': c.get('path', ''), 'intent': c.get('intent', '')}
                   for c in (plan.get('changes') or [])]
        if plan.get('pattern'):
            proposal['proposed_pattern'] = plan['pattern']
        proposal['model_claims_bounded'] = plan.get('bounded')
        return {'cause': cause, 'rows': rows, 'scope': scope, 'facts': facts, 'threshold': threshold,
                'eligibility': eligibility, 'rollback': rollback, 'variances': variances,
                'proposal': proposal, 'changes': changes, 'provenance': provenance,
                'fix': None, 'snapshot': {}}

    def _stage_fix(self, context, item, root, snapshot):
        """Propose and apply edits for one eligible static cause. They stay provisional until the gate runs."""
        cause = item['cause']
        fix = {'outcome': '', 'applied': [], 'refused': [], 'unfixed': [], 'files': []}
        answer, prov, files = remediation_fix.propose(context, self.repo, cause, item['rows'], root)
        fix['files'] = files
        fix['mode'] = prov.get('mode', '')
        if not isinstance(answer, dict):
            fix['outcome'] = 'model_unavailable'
            fix['reason'] = prov.get('reason', '')
            fix['unfixed'] = [{'ref': r['ms_source'] or r['iib_source'] or r['area'], 'reason': 'model unavailable'}
                              for r in item['rows']]
            return fix
        fix['unfixed'] = [{'ref': str(u.get('ref', '')), 'reason': str(u.get('reason', ''))}
                          for u in (answer.get('unfixable') or []) if isinstance(u, dict)]
        edits = answer.get('edits') or []
        if not edits:
            fix['outcome'] = 'no_edits_proposed'
            return fix
        applied, refused, snap = remediation_fix.apply_edits(root, edits)
        for path, text in snap.items():
            snapshot.setdefault(path, text)
        fix['applied'], fix['refused'] = applied, refused
        fix['outcome'] = 'applied' if applied else 'all_edits_refused'
        return fix

    def _verify(self, context, job_id, root, plan_id, snapshot, items):
        """Re-run the quality gate once for every provisional edit; keep them all or none."""
        from app.services.migration_lifecycle_v6 import MappingAndPlanningService
        before = feedback.score(remediation_fix.latest_checks(self.repo, job_id))
        after_run = MappingAndPlanningService(self.repo).quality_validate(
            job_id, plan_id, root, context.param('execute_build', 'auto'))
        after = feedback.score(after_run['checks'])
        ok = remediation_fix.not_worse(before, after)
        if not ok:
            remediation_fix.restore(snapshot)
        verdict = {'result': 'kept' if ok else 'reverted_regression',
                   'basis': 'quality gate no worse than before (this does not re-run the static comparison)',
                   'quality_run_id': after_run.get('quality_run_id', ''), 'status': after_run.get('status', ''),
                   'open_before': sorted(before[0]), 'open_after': sorted(after[0]),
                   'build_stage_before': before[1], 'build_stage_after': after[1]}
        for item in items:
            if item['fix'] and item['fix']['applied']:
                item['fix']['verdict'] = verdict['result']
                if not ok:
                    item['fix']['unfixed'] += [{'ref': a['path'], 'reason': 'reverted: the quality gate got worse'}
                                               for a in item['fix']['applied']]
                    item['fix']['applied'] = []
        return verdict

    def execute(self, context):
        job = context.job()
        explicit = context.param('root_cause_id', None)
        if explicit:
            cause = self.repo.one('select * from root_causes where id=? and job_id=?', (explicit, context.job_id))
            if not cause:
                raise ValueError('root_cause_not_found')
            causes = [cause]
        else:
            # Without a named cause, every cause still waiting is taken in turn, most
            # confident first. A step that fixes one arbitrary cause and leaves the rest
            # is not remediating the job.
            causes = self.repo.rows(
                "select * from root_causes where job_id=? and status='PROPOSED' order by confidence desc limit ?",
                (context.job_id, MAX_CAUSES))
            if not causes:
                n = (self.repo.one('select count(*) n from root_causes where job_id=?', (context.job_id,)) or {}).get('n', 0)
                return AgentResult(status='COMPLETED',
                                   outputs={'patch_plan': None, 'code_changes': [], 'tests': []},
                                   metrics={'skipped': True, 'reason': 'no_root_causes' if not n else 'nothing_left_to_remediate'})
        items = [self._assess(context, job, c) for c in causes]

        # A single named cause that is not eligible keeps the old behaviour: ask, do not proceed.
        if explicit and not items[0]['eligibility']['eligible']:
            it = items[0]
            return AgentResult(
                status='CLARIFICATION_REQUIRED',
                outputs={'patch_plan': it['proposal'], 'code_changes': it['changes'], 'tests': []},
                metrics={'eligible': False, 'route': it['eligibility']['route'],
                         'confidence': it['cause']['confidence'], 'threshold': it['threshold'],
                         'unmet': sorted(k for k in ('approved_pattern', 'bounded_impact', 'tests_present')
                                         if not it['facts'][k])},
                reasoning=it['provenance'])

        # Fixes for the eligible causes found in the static reports.
        root, plan_id = remediation_fix.locate_project(self.repo, context.job_id)
        snapshot, verdict = {}, None
        for it in items:
            if not (it['scope'] and it['eligibility']['eligible']):
                continue
            if not root:
                it['fix'] = {'outcome': 'no_generated_project', 'applied': [], 'refused': [], 'files': [],
                             'unfixed': [{'ref': r['ms_source'] or r['area'], 'reason': 'no generated project on disk'}
                                         for r in it['rows']]}
                continue
            it['fix'] = self._stage_fix(context, it, root, snapshot)
        if snapshot:
            verdict = self._verify(context, context.job_id, root, plan_id, snapshot, items)

        # A kept fix keeps the files as they were, so it can be undone; the pointer is that copy. A job's
        # own rollback pointer still wins where it has one. record_remediation refuses a remediation
        # with no pointer at all, which used to fail the step for every job created without one.
        saved = None
        if snapshot and verdict and verdict['result'] == 'kept':
            saved = remediation_fix.save_snapshot(root, uid(), snapshot)
        tests_all, changes_all, first_id, results = set(), [], None, []
        for it in items:
            changed = bool(it['fix'] and it['fix']['applied'])
            it['pointer'] = it['rollback'] or (('snapshot:' + saved) if (changed and saved) else NO_SOURCE_CHANGED)
            cause, eligible = it['cause'], it['eligibility']['eligible']
            tests = sorted(set(DEFAULT_TESTS) | {t for x in it['variances'] for t in (x['kind'].lower() + '_regression',)}
                           | ({it['scope'][0].lower() + '_regression'} if it['scope'] else set()))
            recorded_id = ''
            if eligible:
                fix = it['fix']
                body = dict(it['proposal'])
                if fix is not None:
                    body['fix'] = {k: fix.get(k) for k in ('outcome', 'applied', 'refused', 'unfixed', 'files', 'verdict', 'mode')}
                recorded = RCAService(self.repo).record_remediation(
                    context.job_id, cause['id'], json.dumps(body, sort_keys=True, default=str),
                    it['proposal']['approved_pattern'], tests, int(job['iteration'] or 1), it['pointer'])
                recorded_id = recorded['id']
                first_id = first_id or recorded_id
                tests_all |= set(tests)
                if fix and fix['applied']:
                    with self.repo.connect() as c:
                        c.execute("update root_causes set status='REMEDIATED' where id=?", (cause['id'],))
            changes_all += it['changes']
            fix = it['fix'] or {}
            results.append({
                'root_cause_id': cause['id'], 'remediation_id': recorded_id,
                'source': it['scope'][0] if it['scope'] else 'DIFFERENTIAL',
                'area': it['scope'][1] if it['scope'] else '',
                'hypothesis': cause['hypothesis'], 'confidence': cause['confidence'],
                'threshold': it['threshold'], 'eligible': eligible, 'route': it['eligibility']['route'],
                'unmet': sorted(k for k in ('approved_pattern', 'bounded_impact', 'tests_present') if not it['facts'][k]),
                'finding_count': len(it['rows']), 'planned_changes': it['changes'], 'tests': tests,
                'rollback_pointer': it['pointer'],
                'outcome': fix.get('outcome') or ('plan_only' if eligible else 'not_eligible'),
                'applied': fix.get('applied') or [], 'refused': fix.get('refused') or [],
                'unfixed': fix.get('unfixed') or [], 'files': fix.get('files') or [],
                'verdict': fix.get('verdict', '')})
        applied_total = sum(len(r['applied']) for r in results)
        report = {'causes': results, 'gate': verdict,
                  'totals': {'causes': len(results), 'eligible': sum(1 for r in results if r['eligible']),
                             'fixes_applied': applied_total,
                             'not_fixed': sum(len(r['unfixed']) for r in results),
                             'not_eligible': sum(1 for r in results if not r['eligible'])}}
        report_id = self._store_report(context.job_id, report)

        payload = json.dumps({'report_id': report_id, **report}, sort_keys=True, default=str)
        if explicit:
            it = items[0]
            return AgentResult(
                outputs={'patch_plan': first_id, 'code_changes': changes_all, 'tests': sorted(tests_all)},
                evidence=(EvidenceRef('REMEDIATION_PROPOSAL', '1.0.0', 'remediation://' + (first_id or report_id),
                                      payload, {'root_cause_id': it['cause']['id'], 'confidence': it['cause']['confidence'],
                                                'threshold': it['threshold'], 'rollback_pointer': it['pointer']}),),
                metrics={'eligible': True, 'route': it['eligibility']['route'],
                         'confidence': it['cause']['confidence'], 'threshold': it['threshold'],
                         'change_count': len(it['changes']), 'fixes_applied': applied_total,
                         'report_id': report_id},
                reasoning=it['provenance'])
        return AgentResult(
            outputs={'patch_plan': first_id, 'code_changes': changes_all, 'tests': sorted(tests_all)},
            evidence=(EvidenceRef('REMEDIATION_REPORT', '1.0.0', 'remediation://' + report_id, payload,
                                  {'causes': len(results), 'fixes_applied': applied_total}),),
            metrics={**{'report_id': report_id}, **report['totals'],
                     'gate': (verdict or {}).get('result', 'not_run')},
            reasoning=items[0]['provenance'])

    def _store_report(self, job_id, report):
        rid = uid()
        payload = json.dumps(report, sort_keys=True, default=str)
        with self.repo.connect() as c:
            self.repo.insert(c, 'validation_reports', {
                'id': rid, 'job_id': job_id, 'report_type': REPORT_TYPE, 'version': '1.0.0', 'payload': payload,
                'content_hash': digest(payload), 'status': 'ANALYSED', 'created_at': now(), 'reference_id': ''})
        return rid


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(RemediationAgent))
