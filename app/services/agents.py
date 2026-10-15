import json
from app.repository import uid,now
from app.agents import load as load_implementation
from app.providers import default_provider
from app.agents.skills import SkillIntegrityError, library, skill_for, skills_required
from app.services.evidence import EvidenceService
from app.services.factory_v3 import AuditLedger
from app.services.agent_llm_bindings import AgentLLMBindingService
class AgentRegistry:
 def __init__(self,repo): self.repo=repo;self.evidence=EvidenceService(repo);self.ledger=AuditLedger(repo);self.bindings=AgentLLMBindingService(repo)
 def contract(self,agent_id,version):
  a=self.repo.one('select * from agent_registry where agent_id=? and version=? and enabled=1 and status=?',(agent_id,version,'APPROVED'))
  if not a: raise ValueError('agent_not_registered_or_approved')
  return a
 def start(self,job_id,agent_id,version,input_evidence,roles,requested_tools=None,identity=''):
  a=self.contract(agent_id,version)
  if a['required_role'] not in roles: raise PermissionError('required_agent_role_missing')
  allowed=set(json.loads(a['tool_allowlist'] or '[]')); requested=set(requested_tools or [])
  if not requested.issubset(allowed): raise PermissionError('tool_not_allowed')
  # An input evidence id that resolves to nothing means the run is reading
  # something the factory never registered, so its output cannot be traced to
  # an immutable input. Only registered, immutable evidence for this job counts.
  ids=list(dict.fromkeys(input_evidence or []))
  if ids:
   marks=','.join('?'*len(ids));found={x['id'] for x in self.repo.rows(f'select id from evidence where job_id=? and immutable=1 and id in ({marks})',(job_id,*ids))}
   missing=sorted(set(ids)-found)
   if missing: raise ValueError('input_evidence_not_registered:'+','.join(missing))
  run=uid(); corr=uid(); job=self.repo.one('select iteration from jobs where id=?',(job_id,))
  with self.repo.connect() as c:
   # Named columns: the positional insert this replaced broke the moment the
   # governance upgrade added run ownership and provenance columns.
   self.repo.insert(c,'agent_runs',{'id':run,'job_id':job_id,'agent_name':a['name'],'agent_version':version,'status':'RUNNING','input_evidence':json.dumps(ids),'output_evidence':'[]','correlation_id':corr,'error':'','started_at':now(),'completed_at':'','agent_id':agent_id,'operator_identity':identity or '','requested_tools':json.dumps(sorted(requested)),'iteration':int((job or {}).get('iteration') or 1)})
  return {'id':run,'agent_id':agent_id,'correlation_id':corr,'status':'RUNNING','allowed_tools':sorted(allowed)}
 def complete(self,run_id,output_evidence,error='',identity=None,outputs=None,provenance=None,reasoning=''):
  r=self.repo.one('select * from agent_runs where id=?',(run_id,))
  if not r: raise ValueError('agent_run_not_found')
  if r['status']!='RUNNING': raise ValueError('agent_run_not_running:'+str(r['status']))
  # A run belongs to the principal that started it. Any caller who could name a
  # run id could previously complete somebody else's work and attach their own
  # output evidence to it.
  owner=r.get('operator_identity') or ''
  if identity is not None and owner and identity!=owner: raise PermissionError('agent_run_not_owned')
  status='FAILED' if error else 'COMPLETED'
  with self.repo.connect() as c:c.execute('update agent_runs set status=?,output_evidence=?,error=?,completed_at=?,outputs=?,model_provenance=?,reasoning=? where id=?',(status,json.dumps(output_evidence),error,now(),json.dumps(outputs or {},default=str),json.dumps(provenance or {},default=str),reasoning,run_id))
  return {'id':run_id,'status':status}
 def dependency_status(self,job_id,agent_id):
  """Which of an agent's prerequisite agents have completed for this job.

  ``dependencies`` was published in the contract and drawn in the graph, but
  nothing ever read it, so ``code-generation`` could run against a job that had
  never been planned. Reported rather than asserted so a caller can see what is
  outstanding before attempting a run.
  """
  a=self.repo.one('select dependencies from agent_registry where agent_id=?',(agent_id,))
  if not a: raise ValueError('agent_not_registered_or_approved')
  required=json.loads(a['dependencies'] or '[]')
  done={x['agent_id'] for x in self.repo.rows("select distinct agent_id from agent_runs where job_id=? and status='COMPLETED'",(job_id,))}
  return {'agent_id':agent_id,'required':required,'satisfied':[x for x in required if x in done],'outstanding':[x for x in required if x not in done]}
 def conformance(self,agent_id=None):
  """Compare each implementation's declarations against its approved contract.

  The catalogue governs what an agent may reach; the implementation declares
  what it does reach. A worker that requests a tool or returns an output its
  approved contract never granted is drift in exactly the sense
  ``Repository._seed_agent_catalogue`` refuses for a changed contract hash --
  the difference is only that nothing was checking this half.
  """
  rows=self.repo.rows('select * from agent_registry where enabled=1'+(' and agent_id=?' if agent_id else ''),(agent_id,) if agent_id else ())
  out=[]
  for a in rows:
   item={'agent_id':a['agent_id'],'version':a['version'],'status':'CONFORMANT','findings':[]}
   try:impl=load_implementation(a['agent_id'])
   except ValueError as e:
    item['status']='NOT_IMPLEMENTED';item['findings'].append(str(e));out.append(item);continue
   tools=set(impl.TOOLS)-set(json.loads(a['tool_allowlist'] or '[]'))
   outputs=set(impl.OUTPUTS)-set(json.loads(a['produced_outputs'] or '[]'))
   if impl.AGENT_ID!=a['agent_id']:item['findings'].append('agent_id_mismatch:'+impl.AGENT_ID)
   if impl.VERSION!=a['version']:item['findings'].append('version_mismatch:'+impl.VERSION)
   # The skill an implementation declares must be the one the manifest assigns it.
   # '' means undeclared (not checked); None means "deliberately none". File hashes are
   # not checked here -- a damaged skill is guidance lost, not contract drift.
   declared=getattr(impl,'SKILL','')
   if declared!='':
    try:expected=library().skill_name(a['agent_id'])
    except SkillIntegrityError:expected=declared  # manifest unreadable: cannot verify, so not drift
    if declared!=expected:item['findings'].append('skill_mismatch:declared='+str(declared)+',manifest='+str(expected))
   if tools:item['findings'].append('tool_not_in_contract:'+','.join(sorted(tools)))
   if outputs:item['findings'].append('output_not_in_contract:'+','.join(sorted(outputs)))
   if item['findings']:item['status']='DRIFT'
   item['implementation']=impl.__module__+':'+impl.__name__;out.append(item)
  return out
 def implementations(self):
  report=self.conformance()
  return {'status':'UP' if all(x['status']=='CONFORMANT' for x in report) else 'DEGRADED','agents':report}
 def dispatch(self,job_id,agent_id,version,roles,identity,params=None,input_evidence=None,provider=None,channel='API'):
  """Run an agent under its approved contract and record everything it produced.

  This is the worker flow ``docs/AGENTS_DEVELOPMENT.md`` specifies: authenticate
  as an approved principal, validate dependencies, start a governed run, execute,
  register output artefacts as evidence, complete with references or a failure
  reason, and emit audit and workflow events. Dependency and capability checks
  live here rather than in ``start`` because ``start`` is the contract-level
  admission check that the ``/api/v2/agents/start`` route exposes for an external
  worker that performs its own execution.
  """
  a=self.contract(agent_id,version)
  drift=[x for x in self.conformance(agent_id) if x['status']!='CONFORMANT']
  if drift: raise PermissionError('agent_implementation_drift:'+json.dumps(drift[0]['findings']))
  deps=self.dependency_status(job_id,agent_id)
  if deps['outstanding']: raise PermissionError('agent_dependency_not_satisfied:'+','.join(deps['outstanding']))
  impl=load_implementation(agent_id)(self.repo)
  # Opt-in strict mode: an agent that declares a skill does not run without it. Checked
  # before start() so a refusal leaves no RUNNING row behind.
  if skills_required() and getattr(impl,'SKILL',''):
   try:skill_for(agent_id)
   except SkillIntegrityError as e:raise PermissionError('agent_skill_unavailable:'+str(e))
  run=self.start(job_id,agent_id,version,input_evidence or [],roles,impl.TOOLS,identity)
  job=self.repo.one('select iteration from jobs where id=?',(job_id,))
  effective_alias,binding_source=self.bindings.resolve(agent_id,a.get('default_model_alias') or '')
  # AT-05 fail-closed: an ACTIVE override whose alias has been removed from (or
  # never entered) model_registry must not silently fall back to the default.
  if binding_source=='AGENT_OVERRIDE' and effective_alias:
   approved=self.repo.one("select id from model_registry where name=? and status='APPROVED' order by created_at desc limit 1",(effective_alias,))
   if not approved: raise PermissionError('active_llm_override_unavailable_in_model_registry:'+effective_alias)
  # AT-13 fail-closed for DEFAULT path: when model_registry is populated (the
  # feature is in use), the effective alias must also appear there as APPROVED.
  # The guard is skipped when the registry is empty so deployments that have not
  # yet registered their models continue to work without disruption.
  if binding_source=='DEFAULT' and effective_alias:
   if self.repo.one('select id from model_registry limit 1'):
    if not self.repo.one("select id from model_registry where name=? and status='APPROVED' order by created_at desc limit 1",(effective_alias,)):
     raise PermissionError('default_model_alias_not_in_approved_model_registry:'+effective_alias+':add_this_alias_in_model_registry_with_status_APPROVED_before_dispatch')
  # AT-11 checkpoint pinning: stamp the resolved binding onto the run record
  # immediately so it is permanently associated with this attempt regardless of
  # any future binding change or run failure.
  with self.repo.connect() as c:
   c.execute('update agent_runs set effective_model_alias=?,binding_source=? where id=?',(effective_alias,binding_source,run['id']))
  if provider is None: provider=default_provider()
  context=impl.context(job_id,identity=identity,roles=roles,params=params or {},correlation_id=run['correlation_id'],iteration=int((job or {}).get('iteration') or 1),provider=provider,channel=channel,default_model_alias=a.get('default_model_alias') or '',effective_model_alias=effective_alias,binding_source=binding_source)
  try:result=impl.execute(context)
  except Exception as e:
   self.complete(run['id'],[],type(e).__name__+':'+str(e),identity)
   with self.repo.connect() as c:self.repo.event(c,job_id,identity,'AGENT_RUN_FAILED',details={'run_id':run['id'],'agent_id':agent_id,'version':version,'error':type(e).__name__+':'+str(e)},correlation=run['correlation_id'])
   raise
  allowed=set(json.loads(a['tool_allowlist'] or '[]'));declared=set(json.loads(a['produced_outputs'] or '[]'))
  extra=set(result.outputs)-declared
  if extra: raise PermissionError('agent_output_not_in_contract:'+','.join(sorted(extra)))
  # Writing evidence and raising a clarification are contract capabilities, not
  # conveniences. An agent whose allow-list grants neither must not acquire them
  # by returning them in its result.
  if result.evidence and 'evidence.write' not in allowed: raise PermissionError('tool_not_allowed:evidence.write')
  if result.clarifications and 'clarification.create' not in allowed: raise PermissionError('tool_not_allowed:clarification.create')
  # The skill that worked with this run, for every agent -- not only those that reason
  # through the gateway. Recorded on the run, on each artefact and in the audit entry.
  skill_prov=context.skill_provenance()
  registered=[]
  for ref in result.evidence:
   meta=dict(ref.metadata or {});meta.update({'agent_id':agent_id,'agent_version':version,'run_id':run['id'],'model_provenance':result.reasoning or {},'skill':skill_prov})
   registered.append(self.evidence.register(job_id,ref.kind,ref.version,ref.uri,ref.content,agent_id,meta))
  opened=[]
  # Clarification blocking is disabled — agents may still return
  # clarifications in their result but they are not persisted or acted on.
  evidence_ids=[x['id'] for x in registered]
  self.complete(run['id'],evidence_ids,result.error,identity,result.outputs,{**skill_prov,**(result.reasoning or {})},(result.reasoning or {}).get('mode',''))
  detail={'run_id':run['id'],'agent_id':agent_id,'version':version,'status':result.status,'outputs':result.outputs,'evidence_ids':evidence_ids,'clarification_ids':opened,'requested_tools':sorted(impl.TOOLS),'params_hash':context.params_hash,'reasoning':result.reasoning,'channel':channel,'contract_hash':a['contract_hash'],'effective_model_alias':effective_alias,'binding_source':binding_source,'skill':skill_prov}
  with self.repo.connect() as c:
   self.repo.event(c,job_id,identity,'AGENT_RUN_COMPLETED',details=detail,correlation=run['correlation_id'])
   self.ledger.append(job_id,identity,'AGENT_RUN_COMPLETED',detail,cursor=c)
  return {'run_id':run['id'],'agent_id':agent_id,'version':version,'status':result.status,'outputs':result.outputs,'evidence':registered,'clarification_ids':opened,'next_state':result.next_state,'commands':list(result.commands),'metrics':result.metrics,'reasoning':result.reasoning,'correlation_id':run['correlation_id'],'error':result.error}
 def health(self):
  rows=self.repo.rows('select agent_id,version,enabled,status,last_health_status,last_health_at from agent_registry order by agent_id')
  return {'status':'UP' if all(x['enabled'] and x['status']=='APPROVED' for x in rows) else 'DEGRADED','agents':rows}
 def graph(self):
  rows=self.repo.rows('select agent_id,display_name,dependencies from agent_registry where enabled=1 order by agent_id')
  return {'nodes':[{'id':x['agent_id'],'label':x['display_name']} for x in rows],'edges':[{'from':d,'to':x['agent_id']} for x in rows for d in json.loads(x['dependencies'] or '[]')]}
