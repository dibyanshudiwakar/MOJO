"""Governance Agent.

Answers, for a job as it stands: which transitions are legal, which gate each
one requires, whether that gate is satisfied, and what evidence is still missing.
Read-only apart from its audit entry -- it decides nothing, it reports what the
governed rules already decide.

Every rule is evaluated by the code that enforces it, never re-implemented here:
``state_machine.TRANSITIONS`` for legality, ``service.GATE_TRANSITIONS`` for which
gate guards an edge, ``FactoryService._valid_approvals`` and ``_gate_controls``
for quorum, timeout, iteration and artefact-binding validity, and
``preconditions.evidence_bundle`` for the evidence a late transition needs. A
second implementation of any of these would be a governance answer that could
disagree with the gate itself, which is worse than no answer.
"""
from app.agents.artefact import produce
from app.agents.base import AgentResult, BaseAgent
from app.domain.preconditions import PreconditionError, evidence_bundle
from app.domain.state_machine import TRANSITIONS
from app.service import GATE_TRANSITIONS, FactoryService
from app.services.factory_v3 import AuditLedger


class GovernanceAgent(BaseAgent):
    AGENT_ID = 'governance'
    SKILL = 'iib-to-ms-governance'
    TOOLS = ('policy.read', 'policy.evaluate', 'audit.write')
    OUTPUTS = ('allow_deny_decision', 'approval_requirement', 'evidence_requirement')
    #: Explains a decision already made; it never makes one. The module note
    #: above rules out a second implementation of any governance rule, and a
    #: model that could answer ALLOW/DENY would be exactly that. The prompt is
    #: told so, the answer lands in ``metrics`` only, and ``outputs`` and
    #: ``next_state`` below are untouched by it.
    PROMPT = 'agent/governance/gate-assessment'

    def _evaluate(self, c, factory, job, target):
        """What ``target`` requires from here, and whether it is satisfied."""
        state = job['state']
        iteration = int(job['iteration'] or 1)
        out = {'target': target, 'legal': target in TRANSITIONS.get(state, set()),
               'gate': '', 'quorum': 0, 'approvals': 0, 'satisfied': True, 'reasons': []}
        if not out['legal']:
            out['satisfied'] = False
            out['reasons'].append('illegal_transition:%s->%s' % (state, target))
            return out
        required = GATE_TRANSITIONS.get((state, target))
        if required:
            gate, allowed = required
            policy = c.execute('select * from approval_policies where gate=?', (gate,)).fetchone()
            quorum, timeout = factory._gate_controls(c, gate)
            out.update({'gate': gate, 'quorum': quorum, 'timeout_minutes': timeout,
                        'allowed_decisions': sorted(allowed),
                        'required_role': policy['required_role'] if policy else '',
                        'min_evidence': policy['min_evidence'] if policy else 0,
                        'enabled': bool(policy['enabled']) if policy else True})
            if policy and not policy['enabled']:
                out['reasons'].append('gate_disabled_by_policy:' + gate)
            else:
                distinct = {str(a['approver_identity'] or a['id'])
                            for a, _ids, _b in factory._valid_approvals(c, job['id'], gate, allowed, iteration, timeout)}
                out['approvals'] = len(distinct)
                out['approvers'] = sorted(distinct)
                if len(distinct) < quorum:
                    out['satisfied'] = False
                    out['reasons'].append('required_gate_approval_missing:%s:quorum_%d_of_%d' % (gate, len(distinct), quorum))
        try:
            out['evidence_bundle'] = evidence_bundle(c, job, target)
        except PreconditionError as exc:
            out['satisfied'] = False
            out['evidence_bundle'] = {}
            out['reasons'].append(str(exc))
        out['decision'] = 'ALLOW' if out['satisfied'] else 'DENY'
        return out

    def _assess(self, context, job, evaluations, decision):
        """The model's reading of each candidate transition.

        A recommendation, never the decision. ``_evaluate`` stays the authority
        because a gate is enforcement: it turns on the approver's role, the
        quorum and the artefact hash each approval binds, and ACE source text
        reaches these payloads -- so a check a model can be argued out of is not
        a control. What the model adds is a judgement the rules cannot make,
        about whether the evidence genuinely supports the transition, and that
        is worth recording precisely where it contradicts the decision.

        One request per candidate target, each falling back to the evaluation's
        own reasons when the gateway cannot answer.
        """
        out, provenance = [], {}
        for evaluation in evaluations:
            target = evaluation['target']
            # ``evidence_bundle`` is omitted: it is a bundle of content hashes,
            # and a long hex run reads as a PAN to the data-loss scan.
            payload = {'state': job['state'], 'target_state': target,
                       'evaluation': {k: v for k, v in evaluation.items()
                                      if k != 'evidence_bundle'}}

            def fallback(e=evaluation, t=target):
                return {'recommendation': 'ALLOW' if e['decision'] == 'ALLOW' else 'DENY',
                        'target_state': t,
                        'rationale': '; '.join(e.get('reasons') or []) or 'no blocking reason',
                        'evidence_sufficient': e['decision'] == 'ALLOW',
                        'missing_evidence': list(e.get('reasons') or [])}

            assessment, provenance = produce(context, self.PROMPT, payload, fallback)
            recommendation = assessment.get('recommendation', '')
            out.append({'target': target, 'decision': decision.get(target, ''),
                        'recommendation': recommendation,
                        'rationale': assessment.get('rationale', ''),
                        'evidence_sufficient': assessment.get('evidence_sufficient'),
                        'missing_evidence': assessment.get('missing_evidence', []),
                        # Only a straight contradiction counts.
                        # INSUFFICIENT_EVIDENCE is the model declining to judge,
                        # which is not the same as disagreeing.
                        'disagrees': recommendation in ('ALLOW', 'DENY')
                        and recommendation != decision.get(target, '')})
        return out, provenance

    def execute(self, context):
        job = context.job()
        factory = FactoryService(self.repo)
        requested = context.param('target_state', '')
        targets = [requested] if requested else sorted(TRANSITIONS.get(job['state'], set()))
        with self.repo.connect() as c:
            evaluations = [self._evaluate(c, factory, job, t) for t in targets]
        decision = {x['target']: x['decision'] for x in evaluations}
        approval = {x['target']: {'gate': x['gate'], 'quorum': x['quorum'],
                                  'approvals': x['approvals'],
                                  'required_role': x.get('required_role', ''),
                                  'allowed_decisions': x.get('allowed_decisions', [])}
                    for x in evaluations if x['gate']}
        requirement = {x['target']: {'evidence_bundle': x.get('evidence_bundle', {}),
                                     'min_evidence': x.get('min_evidence', 0),
                                     'reasons': x['reasons']} for x in evaluations}
        allowed = sorted(t for t, d in decision.items() if d == 'ALLOW')
        detail = {'state': job['state'], 'iteration': int(job['iteration'] or 1),
                  'decision': decision, 'approval_requirement': approval,
                  'evidence_requirement': requirement}
        AuditLedger(self.repo).append(context.job_id, context.identity, 'GOVERNANCE_EVALUATED', detail)
        assessments, provenance = self._assess(context, job, evaluations, decision)
        metrics = {'state': job['state'], 'allowed_targets': allowed,
                   'blocked_targets': sorted(t for t, d in decision.items() if d == 'DENY'),
                   'evaluations': evaluations,
                   'assessments': assessments,
                   # Targets where the model read the evidence differently from
                   # the enforced decision. Not an error on either side: a model
                   # saying DENY where the gate says ALLOW is the kind of thing
                   # a reviewer should see before promotion.
                   'disagreements': sorted(a['target'] for a in assessments
                                           if a.get('disagrees'))}
        return AgentResult(
            outputs={'allow_deny_decision': decision, 'approval_requirement': approval,
                     'evidence_requirement': requirement},
            next_state=allowed[0] if len(allowed) == 1 else '',
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(GovernanceAgent))
