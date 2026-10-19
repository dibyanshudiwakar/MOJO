import json, re, subprocess
from pathlib import Path
from app.repository import uid, now, digest

class RegistryService:
 def __init__(self,repo): self.repo=repo
 def register_service(self,d,actor='system'):
  for k in ('name','owner'):
   if not str(d.get(k,'')).strip(): raise ValueError('missing_'+k)
  # A caller-supplied id previously allowed overwriting an arbitrary existing
  # registry row (insert or replace) and resetting its created_at.
  i=uid(); ts=now()
  if d.get('id'):
   existing=self.repo.one('select id,created_at from service_registry where id=?',(d['id'],))
   if not existing: raise ValueError('service_not_found')
   i=existing['id']
  inventory=d.get('runtime_inventory','{}')
  created=existing['created_at'] if d.get('id') else ts
  with self.repo.connect() as c:
   c.execute('delete from service_registry where id=?',(i,))
   self.repo.insert(c,'service_registry',{'id':i,'name':d['name'],'owner':d['owner'],'repository_uri':d.get('repository_uri',''),'runtime_inventory':inventory if isinstance(inventory,str) else json.dumps(inventory),'migration_status':d.get('migration_status','REGISTERED'),'project_scope':d.get('project_scope','GLOBAL'),'environment':d.get('environment','DEV'),'created_at':created,'updated_at':ts})
  return self.repo.one('select * from service_registry where id=?',(i,))

class AIRegistryService:
 def __init__(self,repo): self.repo=repo
 APPROVER_ROLES={'AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN'}
 def _status(self,d,principal):
  """Resolve a governed status. APPROVED is never self-asserted.

  Section 13 requires prompt, model and policy versions to be approved
  artefacts. The requested status is only honoured when the authenticated
  principal actually holds an approver role, and approved_by is taken from the
  token rather than the request body.
  """
  requested=str(d.get('status','DRAFT')).upper()
  if requested not in ('DRAFT','APPROVED','RETIRED'):raise ValueError('invalid_status')
  roles=set(getattr(principal,'roles',()) or ())
  if requested=='APPROVED' and not (self.APPROVER_ROLES & roles):
   raise PermissionError('ai_governance_approver_role_required')
  approver=getattr(principal,'identity','') if requested=='APPROVED' else ''
  return requested,approver
 def register_prompt(self,d,principal=None):
  for k in ('name','version','template'):
   if not str(d.get(k,'')).strip():raise ValueError('missing_'+k)
  status,approver=self._status(d,principal)
  i=uid();h=digest(d['template'])
  with self.repo.connect() as c:
   self.repo.insert(c,'prompt_registry',{'id':i,'name':d['name'],'version':d['version'],'template':d['template'],'content_hash':h,'status':status,'approved_by':approver,'effective_from':d.get('effective_from',now()),'created_at':now()})
  return self.repo.one('select * from prompt_registry where id=?',(i,))
 def register_model(self,d,principal=None):
  for k in ('name','version','provider_profile'):
   if not str(d.get(k,'')).strip():raise ValueError('missing_'+k)
  status,approver=self._status(d,principal)
  # Section 14: the gateway may only reach an approved provider profile.
  profile=self.repo.one("select id,category,enabled from connector_profiles where name=? and category='model-gateway'",(d['provider_profile'],))
  if not profile or not profile['enabled']:raise ValueError('approved_model_gateway_profile_required:'+str(d['provider_profile']))
  i=uid();policy=d.get('policy',{})
  with self.repo.connect() as c:
   self.repo.insert(c,'model_registry',{'id':i,'name':d['name'],'version':d['version'],'provider_profile':d['provider_profile'],'policy':json.dumps(policy),'region':d.get('region',''),'status':status,'approved_by':approver,'created_at':now()})
  return self.repo.one('select * from model_registry where id=?',(i,))
 # Section 14 data-loss-prevention rules. Previously the only check was a
 # caller-supplied contains_secrets boolean, so the request declared its own
 # compliance. These patterns are applied to the payload by the gateway.
 DLP_RULES=(
  ('PRIVATE_KEY',r'-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY'),
  ('CREDENTIAL_ASSIGNMENT',r'(?i)\b(?:password|passwd|secret|api[_-]?key|client_secret|access[_-]?token|private[_-]?key)\b\s*[:=]\s*\S'),
  ('BEARER_TOKEN',r'(?i)\bbearer\s+[A-Za-z0-9._-]{16,}'),
  ('JWT',r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'),
  ('CONNECTION_STRING',r'(?i)\b[a-z][a-z0-9+.-]*://[^\s:@/]+:[^\s:@/]+@'),
  ('PAN',r'\b(?:\d[ -]?){13,19}\b'),
 )
 def scan_payload(self,value):
  """Return DLP findings for an outbound payload."""
  text=value if isinstance(value,str) else json.dumps(value,sort_keys=True,default=str)
  return [rid for rid,rx in self.DLP_RULES if re.search(rx,text)]
 def gateway_request(self,d):
  required=('job_id','prompt_id','model_id','input_evidence')
  for k in required:
   if k not in d:raise ValueError('missing_'+k)
  p=self.repo.one("select * from prompt_registry where id=? and status='APPROVED'",(d['prompt_id'],));m=self.repo.one("select * from model_registry where id=? and status='APPROVED'",(d['model_id'],))
  if not p or not m:raise PermissionError('approved_prompt_and_model_required')
  findings=self.scan_payload(d['input_evidence'])
  if findings:raise PermissionError('dlp_violation:'+','.join(findings))
  # Provider allow-list and residency are properties of the approved profile.
  profile=self.repo.one("select * from connector_profiles where name=? and category='model-gateway' and enabled=1",(m['provider_profile'],))
  if not profile:raise PermissionError('model_gateway_profile_not_eligible')
  policy=json.loads(m['policy'] or '{}');region=m['region'] or json.loads(profile['config'] or '{}').get('region','')
  allowed=policy.get('allowed_regions')
  if allowed and region not in allowed:raise PermissionError('model_region_not_permitted:'+str(region))
  i=uid();payload={'prompt_hash':p['content_hash'],'model':m['name'],'version':m['version'],'input_evidence':d['input_evidence'],'region':region,'provider_profile':m['provider_profile'],'dlp':'PASSED','status':'PROVIDER_EXECUTION_REQUIRED'}
  with self.repo.connect() as c:
   self.repo.insert(c,'model_requests',{'id':i,'job_id':d['job_id'],'prompt_id':p['id'],'model_id':m['id'],'input_hash':digest(json.dumps(d['input_evidence'],sort_keys=True)),'response_metadata':json.dumps(payload),'status':'PROVIDER_EXECUTION_REQUIRED','correlation_id':d.get('correlation_id',uid()),'created_at':now()})
  return {'request_id':i,'prompt_id':p['id'],'model_id':m['id'],'prompt_template':p['template'],**payload}
 def complete_request(self,request_id,response,status='COMPLETED'):
  """Record a provider's answer against a governed request.

  ``gateway_request`` recorded PROVIDER_EXECUTION_REQUIRED and nothing could
  ever move a request past it, so a provider adapter had no way to return an
  answer through the governed path and the request row never reached a terminal
  state. The response is DLP-scanned too: a provider that echoes a secret back
  is the same disclosure as sending one, and only the request direction was
  ever checked.
  """
  r=self.repo.one('select * from model_requests where id=?',(request_id,))
  if not r:raise ValueError('model_request_not_found')
  if r['status']!='PROVIDER_EXECUTION_REQUIRED':raise ValueError('model_request_not_awaiting_execution:'+str(r['status']))
  if status not in ('COMPLETED','FAILED'):raise ValueError('invalid_model_request_status')
  findings=self.scan_payload(response) if status=='COMPLETED' else []
  if findings:
   with self.repo.connect() as c:c.execute('update model_requests set status=?,response_metadata=? where id=?',('BLOCKED',json.dumps({'dlp':'FAILED','findings':findings}),request_id))
   raise PermissionError('dlp_violation_in_response:'+','.join(findings))
  body=json.dumps(response,sort_keys=True,default=str)
  # The response itself, bounded. Only the hash was kept, which made a schema
  # failure unfalsifiable after the fact: the run said a required property was
  # missing and nothing anywhere said what the model had answered. The request
  # direction was already retained as input_evidence, so this makes the pair
  # symmetric. It is DLP-scanned immediately above, so what is stored here has
  # already passed the same rules as the request.
  meta=dict(json.loads(r['response_metadata'] or '{}'));meta.update({'response_hash':digest(body),'response':body[:RESPONSE_RETENTION_CHARS],'response_truncated_in_store':len(body)>RESPONSE_RETENTION_CHARS,'status':status,'completed_at':now()})
  with self.repo.connect() as c:c.execute('update model_requests set status=?,response_metadata=? where id=?',(status,json.dumps(meta),request_id))
  return {'request_id':request_id,'status':status,'response_hash':meta['response_hash']}

#: How much of a model response is retained on the request row. Enough to
#: diagnose a schema failure or an audit question, bounded so a pathological
#: answer cannot bloat the table.
RESPONSE_RETENTION_CHARS=20000

class KnowledgeService:
 def __init__(self,repo):self.repo=repo
 def index(self,d,principal=None):
  for k in ('job_id','kind','reference','content'):
   if not str(d.get(k,'')).strip():raise ValueError('missing_'+k)
  i=uid(); text=str(d['content']); tokens=sorted(set(re.findall(r'[A-Za-z0-9_.:/-]{3,}',text.lower())))
  with self.repo.connect() as c:
   self.repo.insert(c,'knowledge_index',{'id':i,'job_id':d['job_id'],'kind':d['kind'],'reference':d['reference'],'content_hash':digest(text),'tokens':json.dumps(tokens),'version':d.get('version','1'),'lineage':d.get('lineage',''),'created_at':now()})
  return {'id':i,'content_hash':digest(text),'token_count':len(tokens)}
 def search(self,query,job_id=''):
  terms=set(re.findall(r'[A-Za-z0-9_.:/-]{3,}',query.lower())); rows=self.repo.rows('select * from knowledge_index'+(' where job_id=?' if job_id else ''),(job_id,) if job_id else ())
  ranked=[]
  for x in rows:
   score=len(terms & set(json.loads(x['tokens'] or '[]')))
   if score:ranked.append({'score':score,**x})
  return sorted(ranked,key=lambda x:(-x['score'],x['reference']))

class ApprovalWorkspaceService:
 def __init__(self,repo):self.repo=repo
 def inbox(self,role,identity=''):
  q="select p.*,j.service,j.state from approval_packages p join jobs j on j.id=p.job_id where p.status='PENDING_APPROVAL' order by p.created_at desc"
  rows=self.repo.rows(q);out=[]
  for x in rows:
   policy=self.repo.one('select required_role from approval_policies where gate=?',(x['gate'],))
   if not role or (policy and policy['required_role']==role):out.append(x)
  return out
 def inbox_for(self,principal):
  """Packages this principal is actually entitled to act on.

  Derives the approver role from the authenticated token and applies project
  and environment scope, rather than trusting a caller-supplied role.
  """
  return self._split_inbox(principal)[0]
 def withheld_for(self,principal):
  """Pending packages this principal cannot see, and why.

  The inbox filters silently, so a person who holds none of the gate approver
  roles saw "No pending approvals" while packages were waiting -- the same
  screen as a genuinely clear queue. One entry per (gate, role, reason) with a
  count, so the page can say what is waiting and which role would reveal it.
  """
  summary={}
  for x,role,reason in self._split_inbox(principal)[1]:
   k=(x['gate'],role,reason)
   summary[k]=summary.get(k,0)+1
  return [{'gate':g,'required_role':r,'reason':why,'count':n} for (g,r,why),n in sorted(summary.items())]
 def _split_inbox(self,principal):
  q=("select p.*,j.service,j.state,j.project_scope,j.environment from approval_packages p "
     "join jobs j on j.id=p.job_id where p.status='PENDING_APPROVAL' order by p.created_at desc")
  roles=set(principal.roles);scopes=set(principal.project_scopes);envs=set(principal.environments);out=[];hidden=[]
  for x in self.repo.rows(q):
   policy=self.repo.one('select required_role from approval_policies where gate=?',(x['gate'],))
   role=policy['required_role'] if policy else ''
   if not policy or role not in roles:hidden.append((x,role,'role_not_held'));continue
   if 'GLOBAL' not in scopes and x.get('project_scope') not in scopes:hidden.append((x,role,'project_scope'));continue
   if x.get('environment') not in envs:hidden.append((x,role,'environment'));continue
   row=dict(x);row['role']=role;out.append(row)
  return out,hidden
 def gates(self):
  q="""select p.gate,p.required_role,p.allowed_decisions,p.min_evidence,p.enabled,
   coalesce(c.quorum,1) quorum,coalesce(c.timeout_minutes,0) timeout_minutes,
   coalesce(c.escalation_profile,'') escalation_profile,coalesce(c.bypass_role,'') bypass_role,
   coalesce(c.downstream_effect,'') downstream_effect,coalesce(c.features,'{}') features,
   coalesce(c.updated_at,'') updated_at
   from approval_policies p left join gate_controls c on c.gate=p.gate order by p.gate"""
  rows=self.repo.rows(q)
  for x in rows:
   try:x['features']=json.loads(x.get('features') or '{}')
   except Exception:x['features']={}
   x['enabled']=bool(x['enabled']);x['allowed_decisions']=[v for v in (x.get('allowed_decisions') or '').split(',') if v]
  return rows
 def configure_gate(self,d,actor='system'):
  gate=d['gate'];existing=self.repo.one('select * from approval_policies where gate=?',(gate,))
  if not existing:raise ValueError('unknown_gate')
  role=str(d.get('required_role',existing['required_role'])).strip()
  if not role:raise ValueError('required_role_required')
  decisions=d.get('allowed_decisions',existing['allowed_decisions'].split(','))
  if isinstance(decisions,str):decisions=[x.strip() for x in decisions.split(',') if x.strip()]
  if not decisions:raise ValueError('allowed_decisions_required')
  quorum=int(d.get('quorum',1));timeout=int(d.get('timeout_minutes',0));minimum=int(d.get('min_evidence',existing['min_evidence']))
  if quorum<1:raise ValueError('quorum_must_be_positive')
  if timeout<0:raise ValueError('timeout_must_be_nonnegative')
  if minimum<0:raise ValueError('min_evidence_must_be_nonnegative')
  payload={'gate':gate,'enabled':bool(d.get('enabled',True)),'required_role':role,'allowed_decisions':decisions,'min_evidence':minimum,'quorum':quorum,'timeout_minutes':timeout,'escalation_profile':str(d.get('escalation_profile','')).strip(),'bypass_role':str(d.get('bypass_role','')).strip(),'downstream_effect':str(d.get('downstream_effect','')).strip(),'features':d.get('features',{}),'updated_by':actor}
  with self.repo.connect() as c:
   c.execute('update approval_policies set enabled=?,required_role=?,allowed_decisions=?,min_evidence=? where gate=?',(1 if payload['enabled'] else 0,role,','.join(decisions),minimum,gate))
   c.execute('insert or replace into gate_controls values(?,?,?,?,?,?,?,?)',(gate,quorum,timeout,payload['escalation_profile'],payload['bypass_role'],payload['downstream_effect'],json.dumps(payload['features']),now()))
  return {'gate':gate,'status':'CONFIGURED','configuration':next(x for x in self.gates() if x['gate']==gate)}
class DeliveryAutomationService:
 def __init__(self,repo):self.repo=repo
 def git_operation(self,d):
  root=Path(d['repository_path']).resolve(); op=d['operation']
  if op=='status': cmd=['git','status','--porcelain']
  elif op=='branch' and d.get('branch'): cmd=['git','checkout','-b',d['branch']]
  elif op=='tag' and d.get('tag'): cmd=['git','tag','-a',d['tag'],'-m',d.get('message','migration snapshot')]
  else: raise ValueError('unsupported_git_operation')
  if not (root/'.git').exists():raise ValueError('git_repository_required')
  r=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=30)
  i=uid();status='COMPLETED' if r.returncode==0 else 'FAILED';meta={'returncode':r.returncode,'stdout_hash':digest(r.stdout),'stderr_hash':digest(r.stderr)}
  with self.repo.connect() as c:c.execute('insert into delivery_operations values(?,?,?,?,?,?,?,?,?,?)',(i,d.get('job_id',''),'source-control',op,str(root),status,digest(json.dumps(d,sort_keys=True)),json.dumps(meta),d.get('rollback_pointer',''),now()))
  return {'operation_id':i,'status':status,**meta}
 def request(self,d):
  # Safe execution contract: records an auditable request for an approved external adapter.
  for k in ('job_id','category','operation','profile_id'):
   if not d.get(k):raise ValueError('missing_'+k)
  p=self.repo.one("select * from connector_profiles where id=? and enabled=1",(d['profile_id'],))
  if not p or p['category']!=d['category']:raise ValueError('eligible_connector_required')
  i=uid();payload=d.get('payload',{});status='PENDING_EXTERNAL_EXECUTION'
  with self.repo.connect() as c:c.execute('insert into delivery_operations values(?,?,?,?,?,?,?,?,?,?)',(i,d['job_id'],d['category'],d['operation'],d['profile_id'],status,digest(json.dumps(payload,sort_keys=True)),json.dumps({'secret_reference':p['secret_reference'],'network_test':'NOT_EXECUTED'}),d.get('rollback_pointer',''),now()))
  return {'operation_id':i,'status':status,'network_test':'NOT_EXECUTED'}

class NotificationService:
 def __init__(self,repo):self.repo=repo
 def queue(self,d):
  for k in ('job_id','event_type','recipient'):
   if not d.get(k):raise ValueError('missing_'+k)
  i=uid();ts=now()
  with self.repo.connect() as c:
   self.repo.insert(c,'notifications',{'id':i,'job_id':d['job_id'],'event_type':d['event_type'],'recipient':d['recipient'],'payload':json.dumps(d.get('payload',{})),'status':'QUEUED','profile_id':d.get('profile_id',''),'created_at':ts,'updated_at':ts,'is_read':0,'title':d.get('title',''),'link':d.get('link','')})
  return self.repo.one('select * from notifications where id=?',(i,))
 def emit(self,job_id,event_type,recipient,title,payload=None,link=''):
  """Convenience: queue a notification with a human-readable title and optional deep-link."""
  return self.queue({'job_id':job_id,'event_type':event_type,'recipient':recipient,'title':title,'payload':payload or {},'link':link})
 def for_user(self,email,limit=50):
  """Notifications addressed to this user or to 'ALL', newest first."""
  return self.repo.rows("select * from notifications where recipient in (?,'ALL') order by created_at desc limit ?",(email,limit))
 def unread_count(self,email):
  r=self.repo.one("select count(*) n from notifications where recipient in (?,'ALL') and is_read=0",(email,))
  return r['n'] if r else 0
 def mark_read(self,notification_id,email):
  """Mark one notification as read. Only the recipient may mark it."""
  row=self.repo.one('select * from notifications where id=?',(notification_id,))
  if not row:raise ValueError('notification_not_found')
  if row['recipient'] not in (email,'ALL'):raise PermissionError('notification_not_yours')
  with self.repo.connect() as c:c.execute('update notifications set is_read=1,updated_at=? where id=?',(now(),notification_id))
  return self.repo.one('select * from notifications where id=?',(notification_id,))
 def mark_all_read(self,email):
  with self.repo.connect() as c:c.execute("update notifications set is_read=1,updated_at=? where recipient in (?,'ALL') and is_read=0",(now(),email))
  return {'status':'ok'}

class ObservabilityService:
 def __init__(self,repo):self.repo=repo
 def emit(self,d):
  i=uid();labels=d.get('labels',{})
  with self.repo.connect() as c:c.execute('insert into observability_events values(?,?,?,?,?,?,?,?,?)',(i,d.get('job_id',''),d['signal_type'],d['name'],float(d.get('value',0)),json.dumps(labels),d.get('correlation_id',''),d.get('severity','INFO'),now()))
  return {'id':i,'status':'RECORDED'}
 def summary(self):
  return {'signals':self.repo.rows('select signal_type,count(*) count from observability_events group by signal_type'),'delivery':self.repo.rows('select status,count(*) count from delivery_operations group by status'),'notifications':self.repo.rows('select status,count(*) count from notifications group by status')}

class SkillRegistryService:
 APPROVER_ROLES={'AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN','AGENT_REGISTRY_ADMIN'}
 VALID_STATUSES=('DRAFT','APPROVED','DEPRECATED')
 VALID_LANGUAGES=('java','python','nodejs')
 def __init__(self,repo):self.repo=repo
 def _resolve_status(self,d,principal):
  requested=str(d.get('status','DRAFT')).upper()
  if requested not in self.VALID_STATUSES:raise ValueError('invalid_status')
  roles=set(getattr(principal,'roles',()) or ())
  if requested=='APPROVED' and not (self.APPROVER_ROLES & roles):raise PermissionError('skill_approver_role_required')
  approver=getattr(principal,'identity','') if requested=='APPROVED' else ''
  approved_at=now() if requested=='APPROVED' else ''
  return requested,approver,approved_at
 def register_runtime(self,d,principal):
  name=str(d.get('name','')).strip()
  if not name:raise ValueError('missing_name')
  if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,62}',name):raise ValueError('invalid_runtime_name')
  lang=str(d.get('language','')).strip().lower()
  if lang not in self.VALID_LANGUAGES:raise ValueError('invalid_language:must_be_java_python_or_nodejs')
  ts=now();existing=self.repo.one('select id,created_at from skill_runtimes where name=?',(name,))
  i=existing['id'] if existing else uid();created=existing['created_at'] if existing else ts
  with self.repo.connect() as c:
   c.execute('delete from skill_runtimes where id=?',(i,))
   self.repo.insert(c,'skill_runtimes',{'id':i,'name':name,'display_name':d.get('display_name','') or name,'language':lang,'language_version':d.get('language_version',''),'framework':d.get('framework',''),'framework_version_range':d.get('framework_version_range',''),'build_tool':d.get('build_tool',''),'base_image':d.get('base_image',''),'config':json.dumps(d.get('config',{})) if isinstance(d.get('config'),dict) else (d.get('config') or '{}'),'enabled':1 if d.get('enabled',True) else 0,'created_at':created,'updated_at':ts,'created_by':getattr(principal,'identity','')})
  return self.repo.one('select * from skill_runtimes where id=?',(i,))
 def update_runtime(self,d,principal):
  i=d.get('id','')
  row=self.repo.one('select * from skill_runtimes where id=?',(i,))
  if not row:return None
  ts=now()
  with self.repo.connect() as c:
   c.execute('update skill_runtimes set display_name=?,language_version=?,framework=?,framework_version_range=?,build_tool=?,base_image=?,config=?,enabled=?,updated_at=? where id=?',(d.get('display_name',row['display_name']),d.get('language_version',row['language_version']),d.get('framework',row['framework']),d.get('framework_version_range',row['framework_version_range']),d.get('build_tool',row['build_tool']),d.get('base_image',row['base_image']),json.dumps(d.get('config',{})) if isinstance(d.get('config'),dict) else (d.get('config') or row['config']),1 if d.get('enabled',bool(row['enabled'])) else 0,ts,i))
  return self.repo.one('select * from skill_runtimes where id=?',(i,))
 def list_runtimes(self):
  rows=self.repo.rows('select * from skill_runtimes order by language,name')
  for r in rows:r['skill_count']=self.repo.one('select count(*) n from skills where target_runtime_id=?',(r['id'],))['n']
  return rows
 def register_skill(self,d,principal):
  for k in ('skill_key','version','target_runtime_id'):
   if not str(d.get(k,'')).strip():raise ValueError('missing_'+k)
  skill_key=str(d['skill_key']).strip();version=str(d['version']).strip()
  if not self.repo.one('select id from skill_runtimes where id=?',(d['target_runtime_id'],)):raise ValueError('target_runtime_not_found')
  if self.repo.one('select id from skills where skill_key=? and version=?',(skill_key,version)):raise ValueError('skill_version_already_exists')
  status,approved_by,approved_at=self._resolve_status(d,principal)
  source_path=d.get('source_path','');content_hash=digest(skill_key+version+(source_path or ''))
  i=uid();ts=now()
  with self.repo.connect() as c:
   self.repo.insert(c,'skills',{'id':i,'skill_key':skill_key,'version':version,'display_name':d.get('display_name','') or skill_key,'description':d.get('description',''),'target_runtime_id':d['target_runtime_id'],'toolchain_runtime_id':d.get('toolchain_runtime_id',''),'source_path':source_path,'content_hash':content_hash,'accepted_source_types':json.dumps(d.get('accepted_source_types',[])) if isinstance(d.get('accepted_source_types'),list) else (d.get('accepted_source_types') or '[]'),'produced_artifact_types':json.dumps(d.get('produced_artifact_types',[])) if isinstance(d.get('produced_artifact_types'),list) else (d.get('produced_artifact_types') or '[]'),'backend_types':json.dumps(d.get('backend_types',[])) if isinstance(d.get('backend_types'),list) else (d.get('backend_types') or '[]'),'status':status,'approved_by':approved_by,'approved_at':approved_at,'deprecated_by':'','deprecated_at':'','superseded_by_id':'','created_at':ts,'updated_at':ts,'created_by':getattr(principal,'identity','')})
  return self.repo.one('select * from skills where id=?',(i,))
 def update_skill_status(self,skill_id,status,principal):
  if status not in self.VALID_STATUSES:raise ValueError('invalid_status')
  roles=set(getattr(principal,'roles',()) or ())
  if status=='APPROVED' and not (self.APPROVER_ROLES & roles):raise PermissionError('skill_approver_role_required')
  row=self.repo.one('select * from skills where id=?',(skill_id,))
  if not row:raise ValueError('skill_not_found')
  ts=now()
  approved_by=getattr(principal,'identity','') if status=='APPROVED' else row['approved_by']
  approved_at=ts if status=='APPROVED' else row['approved_at']
  deprecated_by=getattr(principal,'identity','') if status=='DEPRECATED' else row['deprecated_by']
  deprecated_at=ts if status=='DEPRECATED' else row['deprecated_at']
  with self.repo.connect() as c:c.execute('update skills set status=?,approved_by=?,approved_at=?,deprecated_by=?,deprecated_at=?,updated_at=? where id=?',(status,approved_by,approved_at,deprecated_by,deprecated_at,ts,skill_id))
  return self.repo.one('select * from skills where id=?',(skill_id,))
 def list_skills(self,runtime_id=None,status=None):
  q='select s.*,r.display_name as runtime_display_name,r.language as runtime_language,r.language_version as runtime_language_version from skills s left join skill_runtimes r on s.target_runtime_id=r.id'
  params=[];wheres=[]
  if runtime_id:wheres.append('s.target_runtime_id=?');params.append(runtime_id)
  if status:wheres.append('s.status=?');params.append(status)
  if wheres:q+=' where '+' and '.join(wheres)
  q+=' order by s.created_at desc'
  return self.repo.rows(q,params)
 def bind_skill_to_agent(self,d,principal):
  for k in ('skill_id','agent_id'):
   if not str(d.get(k,'')).strip():raise ValueError('missing_'+k)
  if not self.repo.one('select id from skills where id=?',(d['skill_id'],)):raise ValueError('skill_not_found')
  if not self.repo.one('select agent_id from agent_registry where agent_id=?',(d['agent_id'],)):raise ValueError('agent_not_found')
  binding_type=str(d.get('binding_type','PRIMARY')).upper()
  if binding_type not in ('PRIMARY','ALTERNATE'):raise ValueError('invalid_binding_type')
  existing=self.repo.one('select id from skill_agent_bindings where skill_id=? and agent_id=?',(d['skill_id'],d['agent_id']))
  ts=now()
  if existing:
   with self.repo.connect() as c:c.execute('update skill_agent_bindings set binding_type=?,updated_at=? where id=?',(binding_type,ts,existing['id']))
   return self.repo.one('select * from skill_agent_bindings where id=?',(existing['id'],))
  i=uid()
  with self.repo.connect() as c:self.repo.insert(c,'skill_agent_bindings',{'id':i,'skill_id':d['skill_id'],'agent_id':d['agent_id'],'binding_type':binding_type,'created_at':ts,'updated_at':ts,'created_by':getattr(principal,'identity','')})
  return self.repo.one('select * from skill_agent_bindings where id=?',(i,))
 def list_bindings(self,skill_id=None,agent_id=None):
  q='select * from skill_agent_bindings';params=[];wheres=[]
  if skill_id:wheres.append('skill_id=?');params.append(skill_id)
  if agent_id:wheres.append('agent_id=?');params.append(agent_id)
  if wheres:q+=' where '+' and '.join(wheres)
  return self.repo.rows(q+' order by created_at desc',params)
