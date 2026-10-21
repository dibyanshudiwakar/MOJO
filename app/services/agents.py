import json
from app.repository import uid,now
from app.agents import load as load_implementation
from app.agents.artefact import ArtefactUnavailable, STRUCTURED_EFFORT, StepCancelled, produce, run_parallel
from app.agents.reasoning import UNAVAILABLE
from app.providers import default_provider
from app.agents.skills import SkillIntegrityError, library, skill_for, skills_required
from app.services.evidence import EvidenceService
from app.services.factory_v3 import AuditLedger
from app.services import automode, precedents, learning
from app.services.clarification_reuse import PriorAnswers, ACTOR as REUSE_ACTOR, origin as reuse_origin
from app.services.agent_llm_bindings import AgentLLMBindingService

#: Questions per suggested-answers request. Small enough that the answer stays
#: well inside any alias's max_tokens however many clarifications a run raises.
SUGGESTION_BATCH = 12
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
  return {'id':run,'agent_id':agent_id,'correlation_id':corr,'status':'RUNNING','allowed_tools':sorted(allowed),'started_at':(self.repo.one('select started_at from agent_runs where id=?',(run,)) or {}).get('started_at','')}
 def complete(self,run_id,output_evidence,error='',identity=None,outputs=None,provenance=None,reasoning='',status=''):
  r=self.repo.one('select * from agent_runs where id=?',(run_id,))
  if not r: raise ValueError('agent_run_not_found')
  if r['status']!='RUNNING': raise ValueError('agent_run_not_running:'+str(r['status']))
  # A run belongs to the principal that started it. Any caller who could name a
  # run id could previously complete somebody else's work and attach their own
  # output evidence to it.
  owner=r.get('operator_identity') or ''
  if identity is not None and owner and identity!=owner: raise PermissionError('agent_run_not_owned')
  # ``status`` is only ever passed for a cancellation. Derived from ``error``
  # otherwise, which is every other caller: a run that reported an error
  # FAILED, one that did not COMPLETED. A cancellation carries a message *and*
  # is not a failure, so it is the one case the two-way derivation cannot
  # express -- and recording it as FAILED would put a fault on the agent's
  # health record for something an operator chose.
  status=status or ('FAILED' if error else 'COMPLETED')
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
 def dispatch(self,job_id,agent_id,version,roles,identity,params=None,input_evidence=None,provider=None,channel='API',logger=None,cancelled=None):
  """Run an agent under its approved contract and record everything it produced.

  This is the worker flow ``docs/AGENTS_DEVELOPMENT.md`` specifies: authenticate
  as an approved principal, validate dependencies, start a governed run, execute,
  register output artefacts as evidence, complete with references or a failure
  reason, and emit audit and workflow events. Dependency and capability checks
  live here rather than in ``start`` because ``start`` is the contract-level
  admission check that the ``/api/v2/agents/start`` route exposes for an external
  worker that performs its own execution.

  ``logger`` is an optional ``StepLogger`` from the pipeline worker.  When
  present, each governance check and execution milestone writes a structured
  log entry visible in the step's Logs tab.
  """
  _log_info = logger.info if logger else (lambda m: None)
  _log_warn = logger.warn if logger else (lambda m: None)
  a=self.contract(agent_id,version)
  _log_info('Contract validated for %s v%s (role: %s)' % (agent_id, version, a['required_role']))
  drift=[x for x in self.conformance(agent_id) if x['status']!='CONFORMANT']
  if drift: raise PermissionError('agent_implementation_drift:'+json.dumps(drift[0]['findings']))
  _log_info('Conformance check passed — implementation matches contract')
  deps=self.dependency_status(job_id,agent_id)
  if deps['outstanding']: raise PermissionError('agent_dependency_not_satisfied:'+','.join(deps['outstanding']))
  if deps['required']:
   _log_info('Dependencies satisfied: %s' % ', '.join(deps['satisfied']))
  else:
   _log_info('No dependencies required')
  impl=load_implementation(agent_id)(self.repo)
  # Opt-in strict mode: an agent that declares a skill does not run without it. Checked
  # before start() so a refusal leaves no RUNNING row behind.
  if skills_required() and getattr(impl,'SKILL',''):
   try:skill_for(agent_id)
   except SkillIntegrityError as e:raise PermissionError('agent_skill_unavailable:'+str(e))
  run=self.start(job_id,agent_id,version,input_evidence or [],roles,impl.TOOLS,identity)
  _log_info('Run started: %s (correlation: %s)' % (run['id'][:12], run['correlation_id'][:12]))
  job=self.repo.one('select iteration from jobs where id=?',(job_id,))
  effective_alias,binding_source=self.bindings.resolve(agent_id,a.get('default_model_alias') or '')
  _log_info('LLM binding resolved: %s (%s)' % (effective_alias or '(none)', binding_source))
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
  context=impl.context(job_id,identity=identity,roles=roles,params=params or {},correlation_id=run['correlation_id'],iteration=int((job or {}).get('iteration') or 1),provider=provider,channel=channel,default_model_alias=a.get('default_model_alias') or '',effective_model_alias=effective_alias,binding_source=binding_source,logger=logger,agent_id=agent_id,cancelled=cancelled)
  _log_info('Executing agent %s…' % agent_id)
  try:result=impl.execute(context)
  except StepCancelled as e:
   # A decision, not a fault. Recorded CANCELLED rather than FAILED so the run
   # history distinguishes "the operator stopped this" from "this broke", and
   # so agent health does not count a cancellation against the agent. The run
   # row is closed either way -- leaving it RUNNING is what produced rows
   # claiming work was in progress that nothing was doing.
   self.complete(run['id'],[],str(e),identity,status='CANCELLED')
   with self.repo.connect() as c:self.repo.event(c,job_id,identity,'AGENT_RUN_CANCELLED',details={'run_id':run['id'],'agent_id':agent_id,'version':version,'reason':str(e)},correlation=run['correlation_id'])
   _log_warn('Agent %s cancelled: %s' % (agent_id, e))
   raise
  except Exception as e:
   self.complete(run['id'],[],type(e).__name__+':'+str(e),identity)
   learning.record_run(self.repo,run_id=run['id'],job_id=job_id,agent_id=agent_id,agent_version=version,status='FAILED',
                       started_at=run.get('started_at',''),error=type(e).__name__+':'+str(e))
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
   ev=self.evidence.register(job_id,ref.kind,ref.version,ref.uri,ref.content,agent_id,meta)
   registered.append(ev)
   _log_info('Evidence registered: %s → %s' % (ref.kind, ev['id'][:12]))
  # Each clarification an agent raises is persisted with the run that raised
  # it, so a pipeline step parked on CLARIFICATION_REQUIRED (see
  # PipelineService.park_for_clarification) can look its own questions up by
  # run_id instead of guessing at job-wide stage matching.
  #
  # Nothing in this loop may log. StepLogger._write opens its *own* connection
  # to append to pipeline_step_logs, and SQLite permits a single writer: the
  # connection below takes the write lock on the first insert, the logger's
  # connection then waits out busy_timeout (30s) and raises "database is
  # locked". A self-deadlock, not contention -- it needs no second process and
  # it fired on the first clarification of the run.
  #
  # Observed on a real ace-analysis of IDCardProfile, which raised 95
  # clarifications: evidence registered at 04:12:10, step failed at 04:12:46.
  # One summary line after the transaction says as much as 95 lines inside it
  # did, and costs one write instead of 95.
  # tests/test_no_logging_inside_a_transaction.py pins this for the codebase.
  opened=[]
  with self.repo.connect() as c:
   for cl in result.clarifications:
    cid=uid()
    self.repo.insert(c,'clarifications',{'id':cid,'job_id':job_id,'stage':cl.stage,'question':cl.question,'owner':cl.owner,'impact':cl.impact,'evidence_ref':cl.evidence_ref,'status':'OPEN','answer':'','created_at':now(),'answered_at':'','answered_by':'','run_id':run['id']})
    opened.append(cid)
  if opened:
   _log_info('Clarifications raised: %d (%s)' % (
    len(opened),','.join(sorted({cl.stage for cl in result.clarifications}))))
  # A question answered before is not asked again. Re-running an agent raises
  # its questions afresh, worded slightly differently each time, and one job
  # held the same four questions 4, 4, 3 and 2 times -- each copy waiting on its
  # own answer. See app/services/clarification_reuse.py for what counts as the
  # same question.
  #
  # The earlier answer always travels with the new question (as its suggestion,
  # pointing back at the original). It is *recorded as the answer* only where
  # auto mode would record one: for an agent that is going on anyway. A blocked
  # agent's questions stay open for the same reason as in automode -- answering
  # them resumes the step, which re-runs the agent, which raises them again.
  reused={}
  if result.clarifications:
   prior=PriorAnswers(self.repo,job_id)
   for i,cl in enumerate(result.clarifications):
    hit=prior.match(cl.stage,cl.question,cl.evidence_ref)
    if hit:reused[i]=hit
  taken=0
  if reused:
   take=automode.enabled() and not automode.blocks(result.status)
   with self.repo.connect() as c:
    for i,hit in reused.items():
     c.execute('update clarifications set suggested_answer=?,suggestion_mode=?,suggestion_confidence=?,suggestion_caveat=?,suggested_at=?,reused_from=? where id=?',
               (hit['answer'],'PRIOR_ANSWER','HIGH','Answered earlier by %s' % (hit['answered_by'] or 'a person'),now(),reuse_origin(hit),opened[i]))
     if take:
      # DUPLICATE, not ANSWERED: the question was already answered, and a
      # second ANSWERED row is what kept the lists growing with the same
      # questions. The row stays for the audit trail -- it says which answer it
      # reused -- but no list shows it.
      c.execute("update clarifications set status='DUPLICATE',answer=?,answered_at=?,answered_by=? where id=? and status='OPEN'",
                (hit['answer'],now(),REUSE_ACTOR,opened[i]))
      taken+=1
   _log_info('Reused an earlier answer for %d of %d clarification(s)%s' % (
    len(reused),len(opened),'' if take else ' — offered as suggestions, not recorded'))
  if result.clarifications:
   # Eager, batched: one extra model call for the *whole* run proposes a
   # draft answer for every clarification it just raised, so the operator
   # sees a pre-filled, editable suggestion the moment the tab opens rather
   # than a blank box -- without one round-trip per question (ace-analysis
   # alone can raise dozens in a single run).
   #
   # The only produce() call in the codebase whose failure is caught. Every
   # other one is producing an artefact a gate approves, so failing the agent
   # is the correct outcome -- there is no deterministic fallback and a
   # substitute would conceal a model outage. This one is producing a *draft
   # answer for a human to edit or dismiss*: the clarifications themselves are
   # already raised and persisted above, the agent's own artefact is already
   # recorded, and a blank suggestion box is exactly what the operator saw
   # before this feature existed. Taking down a completed run over a missing
   # convenience would be the mirror of the fallback problem -- failing where
   # nothing is actually wrong.
   #
   # Nothing is substituted: an unavailable suggestion is absent, not invented,
   # and the reason is logged.
   #
   # Asked in batches of SUGGESTION_BATCH, not all at once. The answer grows
   # with the number of questions while max_tokens does not: 95 questions from
   # one ace-analysis overran 32768 tokens and the response was cut mid-JSON.
   # That is not a transient failure -- the same request truncates identically
   # on every retry -- so the request itself has to be bounded. Each batch
   # also fails on its own, so one bad batch costs its own suggestions only.
   suggestions=[]
   by_ordinal={}
   sugg_prov={'mode':UNAVAILABLE,'reason':''}
   questions=[{'question':cl.question,'impact':cl.impact,'stage':cl.stage}
              for i,cl in enumerate(result.clarifications) if i not in reused]
   starts=list(range(0,len(questions),SUGGESTION_BATCH))
   def ask(start):
    # Each batch stands alone and fails alone, so the failure is returned, not
    # raised: run_parallel stops everything on the first exception.
    batch=questions[start:start+SUGGESTION_BATCH]
    try:
     answer,prov=produce(context,'agent/clarification/suggested-answers',{'questions':batch},effort=STRUCTURED_EFFORT)
     return start,len(batch),answer,prov,None
    except ArtefactUnavailable as exc:
     return start,len(batch),None,None,exc
   # Batches are independent, so they are asked concurrently; results are read
   # back in batch order.
   for start,size,answer,prov,exc in run_parallel(context,'agent/clarification/suggested-answers',
                                                  [(lambda s=s:ask(s)) for s in starts]):
    if exc is not None:
     sugg_prov['reason']=exc.reason
     _log_warn('Suggested answers unavailable for questions %d-%d (%s) — those answer boxes stay empty' % (
      start+1,start+size,exc.reason))
     continue
    suggestions.extend(answer.get('suggestions') or [])
    batch_dicts=[s for s in (answer.get('suggestions') or []) if isinstance(s,dict)]
    if len(batch_dicts)==size:
     for j,s in enumerate(batch_dicts):by_ordinal[start+j]=s
    sugg_prov={**prov,'mode':prov.get('mode',sugg_prov['mode'])}
   by_question={str(s.get('question') or '').strip().lower():s for s in suggestions if isinstance(s,dict)}
   # In auto mode a suggestion for an agent that is *going on anyway* is not a
   # proposal awaiting review -- it is the decision. Accepted here, attributed
   # to AUTO_MODE so the audit trail never implies a person reviewed it, and
   # carrying the model's own confidence and caveat so the LOW ones can be read
   # off as a review queue.
   #
   # Not accepted for an agent that stopped. ``automode.blocks`` reads the
   # agent's own status, and a blocked agent produced no artefact: answering
   # its questions would resume the step, re-run the agent, raise the same
   # questions and park again -- a loop of whole agent runs, because no agent
   # reads these answers and resuming means re-running. See
   # app/services/automode.py.
   auto=automode.enabled() and not automode.blocks(result.status)
   decided=0
   taken_before=taken
   if by_question or by_ordinal:
    with self.repo.connect() as c:
     q_idx=0
     for i,(cl,cid) in enumerate(zip(result.clarifications,opened)):
      if i in reused:continue
      s=by_question.get(str(cl.question or '').strip().lower())
      if s is None:s=by_ordinal.get(q_idx)
      q_idx+=1
      if not s or not str(s.get('suggested_answer') or '').strip():continue
      # The same question in different words, or about a sibling construct --
      # which no text comparison can tell from a genuinely different one. The
      # model was shown the earlier decisions (each with a ref) and says which,
      # if any, already settles this. Only a ref it was actually shown counts,
      # and only an answer it is not unsure of; the answer recorded is the
      # person's, never the model's paraphrase.
      earlier=precedents.settled_by(context,s.get('settled_by')) if auto else None
      if earlier and str(s.get('confidence') or '').upper()!='LOW':
       c.execute("update clarifications set status='DUPLICATE',answer=?,answered_at=?,answered_by=?,reused_from=?,suggested_answer=?,suggestion_mode=?,suggestion_confidence=?,suggestion_caveat=?,suggested_at=? where id=? and status='OPEN'",
                 (earlier['answer'],now(),REUSE_ACTOR,earlier['id'],earlier['answer'],'PRIOR_ANSWER',s.get('confidence',''),
                  'Same question as an earlier decision (%s)' % s.get('settled_by'),now(),cid))
       taken+=1
       continue
      c.execute('update clarifications set suggested_answer=?,suggestion_mode=?,suggestion_confidence=?,suggestion_severity=?,suggestion_caveat=?,suggested_at=? where id=?',
                (s['suggested_answer'],sugg_prov.get('mode',''),s.get('confidence',''),s.get('severity',''),s.get('caveat',''),now(),cid))
      if auto and automode.accepts(s.get('confidence'),s.get('severity')):
       c.execute("update clarifications set status='ANSWERED',answer=?,answered_at=?,answered_by=? where id=? and status='OPEN'",
                 (s['suggested_answer'],now(),automode.ACTOR,cid))
       decided+=1
   _log_info('Suggested answers: %d/%d (%s)' % (len(by_question),len(opened)-len(reused),sugg_prov.get('mode','')))
   if taken>taken_before:
    _log_info('%d repeated question(s) settled by earlier decisions — not raised again' % (taken-taken_before))
   if decided:
    # One ledger entry for the batch rather than one per decision: the
    # clarification rows carry the per-question detail, and 95 ledger entries
    # for one run buries the chain it is supposed to make readable.
    detail={'run_id':run['id'],'agent_id':agent_id,'decided':decided,'of':len(opened)-taken,
            'actor':automode.ACTOR,
            'by_confidence':{k:sum(1 for s in by_question.values()
                                   if str(s.get('confidence') or '').upper()==k)
                             for k in ('HIGH','MEDIUM','LOW')}}
    with self.repo.connect() as c:
     self.repo.event(c,job_id,identity,'CLARIFICATIONS_AUTO_ANSWERED',details=detail,correlation=run['correlation_id'])
     self.ledger.append(job_id,automode.ACTOR,'CLARIFICATIONS_AUTO_ANSWERED',detail,cursor=c)
    _log_warn('Auto mode decided %d of %d clarification(s) — %s' % (
     decided,len(opened)-taken,
     ', '.join('%s %s'%(v,k.lower()) for k,v in detail['by_confidence'].items() if v)))
    if decided<len(opened)-taken:
     # Said outright so the operator is not left to infer it from the Logs and
     # the Clarifications page: these are the ones a person is being asked.
     _log_warn('%d clarification(s) left for you — critical and not confidently answered' % (len(opened)-taken-decided))
  evidence_ids=[x['id'] for x in registered]
  learning.record_run(self.repo,run_id=run['id'],job_id=job_id,agent_id=agent_id,agent_version=version,status=result.status,
                      started_at=run.get('started_at',''),metrics=result.metrics,reasoning=result.reasoning,outputs=result.outputs,
                      clarifications=len(opened),error=result.error)
  self.complete(run['id'],evidence_ids,result.error,identity,result.outputs,{**skill_prov,**(result.reasoning or {})},(result.reasoning or {}).get('mode',''))
  # An agent that finished its artefact but left questions for a person is not
  # finished as far as the pipeline is concerned. Auto mode lets it produce the
  # artefact without waiting, but the questions put to a person are real, and a
  # step showing COMPLETED with 59 of them open said otherwise. So the *step*
  # parks (CLARIFICATION_REQUIRED) while the *run* stays COMPLETED, and the
  # outcome says the artefact is done: when the last question is answered the
  # step completes with what it already produced rather than re-running the
  # agent, which for ace-analysis is half an hour for an artefact that exists.
  still_open=self.repo.one("select count(*) n from clarifications where run_id=? and status='OPEN'",(run['id'],))['n'] if opened else 0
  outcome_status=result.status
  artefact_complete=False
  if still_open and not automode.blocks(result.status) and result.status=='COMPLETED':
   outcome_status='CLARIFICATION_REQUIRED'
   artefact_complete=True
   _log_warn('Waiting on %d clarification(s) — the artefact is complete and the step finishes when they are answered' % still_open)
  _log_info('Run completed: %s — %s' % (run['id'][:12], result.status))
  detail={'run_id':run['id'],'agent_id':agent_id,'version':version,'status':result.status,'outputs':result.outputs,'evidence_ids':evidence_ids,'clarification_ids':opened,'requested_tools':sorted(impl.TOOLS),'params_hash':context.params_hash,'reasoning':result.reasoning,'channel':channel,'contract_hash':a['contract_hash'],'effective_model_alias':effective_alias,'binding_source':binding_source,'skill':skill_prov}
  with self.repo.connect() as c:
   self.repo.event(c,job_id,identity,'AGENT_RUN_COMPLETED',details=detail,correlation=run['correlation_id'])
   self.ledger.append(job_id,identity,'AGENT_RUN_COMPLETED',detail,cursor=c)
  return {'run_id':run['id'],'agent_id':agent_id,'version':version,'status':outcome_status,'artefact_complete':artefact_complete,'outputs':result.outputs,'evidence':registered,'clarification_ids':opened,'next_state':result.next_state,'commands':list(result.commands),'metrics':result.metrics,'reasoning':result.reasoning,'correlation_id':run['correlation_id'],'error':result.error}
 def health(self):
  rows=self.repo.rows('select agent_id,version,enabled,status,last_health_status,last_health_at from agent_registry order by agent_id')
  return {'status':'UP' if all(x['enabled'] and x['status']=='APPROVED' for x in rows) else 'DEGRADED','agents':rows}
 def graph(self):
  rows=self.repo.rows('select agent_id,display_name,dependencies from agent_registry where enabled=1 order by agent_id')
  return {'nodes':[{'id':x['agent_id'],'label':x['display_name']} for x in rows],'edges':[{'from':d,'to':x['agent_id']} for x in rows for d in json.loads(x['dependencies'] or '[]')]}
