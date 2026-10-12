class PolicyError(PermissionError): pass
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
