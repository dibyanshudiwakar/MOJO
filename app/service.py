import json
from datetime import datetime, timezone, timedelta
from app.repository import uid,now,digest
from app.domain.state_machine import validate_transition
from app.domain.preconditions import evidence_bundle
from app.domain.model import ApprovalRequest
from app.services.policy import ApprovalPolicyService,PolicyError
from app.services.factory_v3 import AuditLedger
GATE_TRANSITIONS={
 ('DORMANCY_ASSESSED','FRAMEWORK_BASELINED'):('G0',{'PROCEED'}),
 # The state machine also permits skipping the dormancy assessment. Without
 # this pair that route reached FRAMEWORK_BASELINED with no G0 approval at all.
 ('INTAKE_VALIDATED','FRAMEWORK_BASELINED'):('G0',{'PROCEED'}),
 ('FRAMEWORK_BASELINED','ACE_ANALYSED'):('G1',{'APPROVE'}),
 ('ACE_ANALYSED','PLAN_APPROVED'):('G2',{'APPROVE'}),
 ('PLAN_APPROVED','IMPLEMENTING'):('G3',{'APPROVE'}),
 ('VARIANCE_REVIEW','REMEDIATING'):('G4',{'CONTINUE_FEEDBACK'}),
 ('VARIANCE_REVIEW','READY_FOR_PROMOTION'):('G4',{'ACCEPT_EXCEPTION'}),
 ('READY_FOR_PROMOTION','COMPLETED'):('G5',{'PROMOTE'}),
 # WAITING_Gx → post-gate target: same gate, same allowed decisions.
 ('WAITING_G0','FRAMEWORK_BASELINED'):('G0',{'PROCEED'}),
 # When framework-baseline was explicitly deselected the job skips
 # FRAMEWORK_BASELINED and lands directly at ACE_ANALYSED after G0.
 ('WAITING_G0','ACE_ANALYSED'):('G0',{'PROCEED'}),
 ('WAITING_G1','ACE_ANALYSED'):('G1',{'APPROVE'}),
 ('WAITING_G2','PLAN_APPROVED'):('G2',{'APPROVE'}),
 ('WAITING_G3','IMPLEMENTING'):('G3',{'APPROVE'}),
 ('WAITING_G4','REMEDIATING'):('G4',{'CONTINUE_FEEDBACK'}),
 ('WAITING_G4','READY_FOR_PROMOTION'):('G4',{'ACCEPT_EXCEPTION'}),
 ('WAITING_G5','COMPLETED'):('G5',{'PROMOTE'})}
# Maps each WAITING state to the post-gate targets the approval unlocks.
_WAITING_TARGETS={
 # FRAMEWORK_BASELINED first; ACE_ANALYSED is the fallback when framework-baseline
 # was explicitly deselected and the approve() helper detects that.
 'WAITING_G0':['FRAMEWORK_BASELINED','ACE_ANALYSED'],
 'WAITING_G1':['ACE_ANALYSED'],
 'WAITING_G2':['PLAN_APPROVED'],
 'WAITING_G3':['IMPLEMENTING'],
 'WAITING_G4':['REMEDIATING','READY_FOR_PROMOTION'],
 'WAITING_G5':['COMPLETED'],
}
# Re-entering implementation is rework: it opens a new iteration, which retires
# every approval granted against the previous one.
ITERATION_ADVANCING={'REMEDIATING','BUILD_VALIDATED','FAILED'}
# Subset of AGENT_LIFECYCLE_TRANSITIONS needed here to avoid a circular import.
_LIFECYCLE_AGENT_SVC={'FRAMEWORK_BASELINED':'framework-baseline','ACE_ANALYSED':'ace-analysis','PLAN_APPROVED':'planning','IMPLEMENTING':'code-generation'}
def _explicitly_disabled_agents(repo,job_id):
 """Return agent IDs that were explicitly deselected in the latest active pipeline for job_id."""
 pl=repo.one("select id from pipelines where job_id=? and status in ('DRAFT','READY','RUNNING','PAUSED','WAITING_APPROVAL') order by created_at desc limit 1",(job_id,))
 if not pl:return set()
 rows=repo.rows("select agent_id,error from pipeline_steps where pipeline_id=? and status='SKIPPED'",(pl['id'],))
 return {r['agent_id'] for r in rows if 'skip_kind:explicitly_disabled' in str(r['error'] or '')}
def _col(row,name,default=None):
 return row[name] if name in row.keys() else default
class FactoryService:
 def __init__(self,repo):self.repo=repo;self.policy=ApprovalPolicyService(repo);self.ledger=AuditLedger(repo)
 def create_job(self,d,actor):
  for k in ('service','owner','sourceRevision'):
   if not str(d.get(k,'')).strip():raise ValueError('missing_'+k)
  env=d.get('environment','DEV');scope=d.get('project_scope','GLOBAL')
  if env not in ('DEV','TEST','STAGE','PROD'):raise ValueError('invalid_environment')
  jid=uid()
  with self.repo.connect() as c:
   c.execute('insert into jobs(id,service,owner,source_revision,state,created_at,rollback_pointer,iteration,project_scope,environment) values(?,?,?,?,?,?,?,?,?,?)',(jid,d['service'],d['owner'],d['sourceRevision'],'CREATED',now(),d.get('rollbackPointer',''),1,scope,env))
   self.repo.event(c,jid,actor,'JOB_CREATED','','CREATED',d,'ALLOW')
   self.ledger.append(jid,actor,'JOB_CREATED',{'service':d['service'],'owner':d['owner'],'source_revision':d['sourceRevision'],'environment':env,'project_scope':scope},cursor=c)
  return self.repo.one('select * from jobs where id=?',(jid,))
 def _gate_controls(self,c,gate):
  r=c.execute('select quorum,timeout_minutes from gate_controls where gate=?',(gate,)).fetchone()
  if not r:return 1,0
  return max(1,int(r['quorum'] or 1)),max(0,int(r['timeout_minutes'] or 0))
 def _valid_approvals(self,c,jid,gate,allowed,iteration,timeout_minutes):
  """Approvals that independently satisfy the gate, newest first.

  An approval counts only when its decision is permitted, it was granted
  against the job's current iteration, it has not timed out, and every
  evidence id it cites resolves to an immutable row whose hash still matches
  the binding recorded at approval time.
  """
  cutoff=(datetime.now(timezone.utc)-timedelta(minutes=timeout_minutes)).isoformat() if timeout_minutes else ''
  out=[]
  for a in c.execute('select * from approvals where job_id=? and gate=? order by created_at desc',(jid,gate)).fetchall():
   if a['decision'] not in allowed:continue
   if int(_col(a,'iteration',1) or 1)!=int(iteration):continue
   if cutoff and str(a['created_at'] or '')<cutoff:continue
   ids=json.loads(a['evidence_ids'] or '[]')
   if not ids:continue
   marks=','.join('?'*len(ids));e=c.execute(f'select id,content_hash from evidence where job_id=? and immutable=1 and id in ({marks})',(jid,*ids)).fetchall()
   if len(e)!=len(set(ids)):continue
   bindings=json.loads(_col(a,'artifact_bindings','{}') or '{}')
   if any(not any(x['id']==eid and x['content_hash']==h for x in e) for eid,h in bindings.items()):continue
   out.append((a,ids,bindings))
  return out
 def _transition_policy(self,c,jid,current,target,iteration):
  required=GATE_TRANSITIONS.get((current,target))
  if not required:return {'outcome':'ALLOW','reason':'no_gate_required'}
  gate,allowed=required;p=c.execute('select * from approval_policies where gate=?',(gate,)).fetchone()
  if p and not p['enabled']:return {'outcome':'ALLOW','gate':gate,'reason':'gate_disabled_by_policy'}
  quorum,timeout=self._gate_controls(c,gate)
  # Quorum counts distinct approvers. The configured value was recorded and
  # displayed but never enforced, so one signature satisfied any gate.
  distinct={}
  for a,ids,bindings in self._valid_approvals(c,jid,gate,allowed,iteration,timeout):
   distinct.setdefault(str(a['approver_identity'] or a['id']),(a,ids,bindings))
  if len(distinct)<quorum:raise PolicyError('required_gate_approval_missing:%s:quorum_%d_of_%d'%(gate,len(distinct),quorum))
  return {'outcome':'ALLOW','gate':gate,'quorum':quorum,'iteration':iteration,'approvals':[{'approval_id':a['id'],'approver_identity':a['approver_identity'],'approver_role':a['approver_role'],'decision':a['decision'],'evidence_ids':ids,'artifact_bindings':b} for a,ids,b in distinct.values()]}
 def transition(self,jid,target,actor,details=None):
  with self.repo.connect() as c:
   c.execute('begin immediate');j=c.execute('select * from jobs where id=?',(jid,)).fetchone()
   if not j:raise ValueError('job_not_found')
   validate_transition(j['state'],target)
   iteration=int(j['iteration'] or 1)
   decision=self._transition_policy(c,jid,j['state'],target,iteration)
   bundle=evidence_bundle(c,j,target)
   advance=target=='IMPLEMENTING' and j['state'] in ITERATION_ADVANCING
   if advance:iteration+=1
   c.execute('update jobs set state=?,iteration=? where id=?',(target,iteration,jid))
   payload=dict(details or {});payload['policy_decision']=decision;payload['evidence_bundle']=bundle;payload['iteration']=iteration
   self.repo.event(c,jid,actor,'STATE_CHANGED',j['state'],target,payload,decision)
   if advance:
    advanced={'from_iteration':iteration-1,'to_iteration':iteration,'trigger_state':j['state']}
    self.repo.event(c,jid,actor,'ITERATION_ADVANCED',j['state'],target,advanced)
    self.ledger.append(jid,actor,'ITERATION_ADVANCED',advanced,cursor=c)
   self.ledger.append(jid,actor,'STATE_CHANGED',{'from_state':j['state'],'to_state':target,'iteration':iteration,'policy_decision':decision,'evidence_bundle':bundle},cursor=c)
  return self.repo.one('select * from jobs where id=?',(jid,))
 def rollback(self,jid,actor,reason,pointer=None):
  """Return a job to its recorded rollback point.

  ROLLED_BACK was reachable in the state machine but nothing could drive a job
  into it, so an implementation that had to be withdrawn had no terminal state
  and left no record of what it was withdrawn to.
  """
  if not str(reason or '').strip():raise ValueError('rollback_reason_required')
  with self.repo.connect() as c:
   c.execute('begin immediate');j=c.execute('select * from jobs where id=?',(jid,)).fetchone()
   if not j:raise ValueError('job_not_found')
   validate_transition(j['state'],'ROLLED_BACK')
   target=str(pointer or j['rollback_pointer'] or '').strip()
   if not target:raise ValueError('rollback_pointer_required')
   detail={'from_state':j['state'],'iteration':int(j['iteration'] or 1),'rollback_pointer':target,'reason':reason}
   c.execute('update jobs set state=?,rollback_pointer=? where id=?',('ROLLED_BACK',target,jid))
   c.execute('insert into delivery_operations values(?,?,?,?,?,?,?,?,?,?)',(uid(),jid,'lifecycle','rollback',target,'REQUESTED',digest(json.dumps(detail,sort_keys=True)),json.dumps({'execution':'NOT_EXECUTED','reason':'delivery adapter performs the revert'}),target,now()))
   self.repo.event(c,jid,actor,'JOB_ROLLED_BACK',j['state'],'ROLLED_BACK',detail)
   self.ledger.append(jid,actor,'JOB_ROLLED_BACK',detail,cursor=c)
  return self.repo.one('select * from jobs where id=?',(jid,))
 def approve(self,d,actor_roles,actor=None):
  bindings=d.pop('artifact_bindings',{})
  r=ApprovalRequest(**d);decision=self.policy.evaluate(r,actor_roles);aid=uid();j=self.repo.one('select * from jobs where id=?',(r.job_id,))
  if not j:raise ValueError('job_not_found')
  evidence=self.repo.rows('select id,content_hash from evidence where job_id=?',(r.job_id,));actual={x['id']:x['content_hash'] for x in evidence}
  if bindings and any(k in actual and actual[k]!=v for k,v in bindings.items()):raise PolicyError('artifact_binding_mismatch')
  if set(bindings)-set(r.evidence_ids):raise PolicyError('artifact_binding_not_in_evidence')
  iteration=int(j.get('iteration') or 1);identity=actor or r.approver_identity
  detail={'approval_id':aid,'gate':r.gate,'decision':r.decision,'approver_role':r.approver_role,'approver_identity':r.approver_identity,'evidence_ids':r.evidence_ids,'artifact_bindings':bindings,'iteration':iteration}
  with self.repo.connect() as c:
   self.repo.insert(c,'approvals',{'id':aid,'job_id':r.job_id,'gate':r.gate,'decision':r.decision,'approver_role':r.approver_role,'approver_identity':r.approver_identity,'comments':r.comments,'conditions':r.conditions,'evidence_ids':json.dumps(r.evidence_ids),'policy_decision':json.dumps(decision),'rollback_pointer':j['rollback_pointer'] or '','created_at':now(),'artifact_bindings':json.dumps(bindings,sort_keys=True),'iteration':iteration})
   self.repo.event(c,r.job_id,identity,'APPROVAL_RECORDED',j['state'],j['state'],detail,decision)
   self.ledger.append(r.job_id,identity,'APPROVAL_RECORDED',detail,cursor=c)
  result={'id':aid,'iteration':iteration,'policy_decision':decision,'artifact_bindings':bindings}
  # If the job is blocked at a WAITING_Gx state and this approval satisfies
  # the gate quorum, auto-advance the lifecycle so the pipeline can resume.
  j2=self.repo.one('select state from jobs where id=?',(r.job_id,))
  if j2 and j2['state'] in _WAITING_TARGETS:
   disabled=_explicitly_disabled_agents(self.repo,r.job_id)
   for target in _WAITING_TARGETS[j2['state']]:
    req=_LIFECYCLE_AGENT_SVC.get(target)
    if req and req in disabled:
     continue  # agent was explicitly deselected — skip this state
    try:
     self.transition(r.job_id,target,identity,{'source':'auto_advance_after_approval','approval_id':aid})
     break
    except Exception as _exc:
     import sys;sys.stderr.write('auto_advance failed job=%s target=%s err=%s\n'%(r.job_id,target,_exc));sys.stderr.flush()
  return result
