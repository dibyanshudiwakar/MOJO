class PolicyError(PermissionError): pass
def gate_enabled(repo,gate):
 """Whether ``approval_policies`` says this gate is switched on.

 ``FactoryService._transition_policy`` has always honoured this flag, but the
 artefact-bound approval checks in ``MappingAndPlanningService``,
 ``PlanningGenerationService`` and the worker's pre-flight check queried the
 ``approvals`` table directly, so a gate disabled in the database still blocked
 planning and code generation. This is the single place that answers the
 question, so disabling a gate is one row in the database and takes effect on
 every path.

 A missing row means the gate is *not* disabled, matching
 ``_transition_policy``'s ``if p and not p['enabled']`` -- an unknown gate stays
 enforced rather than silently opening.
 """
 row=repo.one('select enabled from approval_policies where gate=?',(gate,))
 return True if row is None else bool(row['enabled'])
class ApprovalPolicyService:
 def __init__(self,repo): self.repo=repo
 def evaluate(self,request,actor_roles):
  p=self.repo.one('select * from approval_policies where gate=? and enabled=1',(request.gate,))
  if not p: raise PolicyError('gate_disabled_or_unknown')
  allowed=p['allowed_decisions'].split(',')
  if request.decision not in allowed: raise PolicyError('decision_not_allowed')
  if p['required_role'] not in actor_roles or request.approver_role!=p['required_role']: raise PolicyError('required_approver_role_missing')
  found=self.repo.rows('select id from evidence where job_id=? and id in (%s)'%(','.join('?'*len(request.evidence_ids))),(request.job_id,*request.evidence_ids)) if request.evidence_ids else []
  if len(found)<p['min_evidence']: raise PolicyError('insufficient_valid_evidence')
  return {'outcome':'ALLOW','policy':request.gate,'required_role':p['required_role']}
