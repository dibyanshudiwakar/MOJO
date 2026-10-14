import json, hashlib, re, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from app.repository import uid, now, digest

class WorkflowOrchestrator:
 def __init__(self,repo):self.repo=repo
 def schedule(self,job_id,activity,payload=None,max_attempts=3,idempotency_key=None):
  key=idempotency_key or f'{job_id}:{activity}';qid=uid();raw=json.dumps(payload or {})
  try:
   with self.repo.connect() as c:c.execute('insert into workflow_queue(id,job_id,activity,status,attempt,max_attempts,idempotency_key,payload,available_at,created_at,updated_at,original_payload,cancel_requested) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(qid,job_id,activity,'READY',0,max_attempts,key,raw,now(),now(),now(),raw,0))
  except Exception:return self.repo.one('select * from workflow_queue where idempotency_key=?',(key,))
  return self.repo.one('select * from workflow_queue where id=?',(qid,))
 def recover_expired(self):
  ts=now()
  with self.repo.connect() as c:c.execute("update workflow_queue set status='RETRY',lease_owner=null,lease_until=null,available_at=?,last_error='lease_expired',updated_at=? where status='RUNNING' and lease_until<?",(ts,ts,ts))
 def claim(self,worker_id='local-worker',lease_seconds=60,activity=None):
  """Claim the oldest ready activity, optionally restricted to one ``activity`` name.

  A dedicated worker that only knows how to run one kind of activity must not
  be able to steal a queue row scheduled for a different purpose (an external
  worker manually polling ``/api/v3/workflow/claim`` for its own activities,
  say) -- passing ``activity`` scopes the claim to that name; omitting it
  keeps the original behaviour for every existing caller.
  """
  self.recover_expired();ts=datetime.now(timezone.utc);until=(ts+timedelta(seconds=max(5,int(lease_seconds)))).isoformat()
  with self.repo.connect() as c:
   c.execute('begin immediate')
   if activity:
    r=c.execute("select * from workflow_queue where status in ('READY','RETRY') and cancel_requested=0 and available_at<=? and activity=? order by created_at limit 1",(ts.isoformat(),activity)).fetchone()
   else:
    r=c.execute("select * from workflow_queue where status in ('READY','RETRY') and cancel_requested=0 and available_at<=? order by created_at limit 1",(ts.isoformat(),)).fetchone()
   if not r:return None
   changed=c.execute("update workflow_queue set status='RUNNING',attempt=attempt+1,lease_owner=?,lease_until=?,heartbeat_at=?,updated_at=? where id=? and status in ('READY','RETRY')",(worker_id,until,ts.isoformat(),ts.isoformat(),r['id'])).rowcount
   if changed!=1:return None
  return self.repo.one('select * from workflow_queue where id=?',(r['id'],))
 def heartbeat(self,qid,worker_id,lease_seconds=60):
  until=(datetime.now(timezone.utc)+timedelta(seconds=max(5,int(lease_seconds)))).isoformat()
  with self.repo.connect() as c:n=c.execute("update workflow_queue set heartbeat_at=?,lease_until=?,updated_at=? where id=? and status='RUNNING' and lease_owner=?",(now(),until,now(),qid,worker_id)).rowcount
  if n!=1:raise PermissionError('activity_lease_not_owned')
  return self.repo.one('select * from workflow_queue where id=?',(qid,))
 def complete(self,qid,success=True,error='',worker_id=None):
  q=self.repo.one('select * from workflow_queue where id=?',(qid,))
  if not q:raise ValueError('activity_not_found')
  if q['status']!='RUNNING':raise ValueError('activity_not_running')
  if worker_id and q.get('lease_owner')!=worker_id:raise PermissionError('activity_lease_not_owned')
  status='CANCELLED' if q.get('cancel_requested') else ('COMPLETED' if success else ('RETRY' if q['attempt']<q['max_attempts'] else 'DEAD_LETTER'))
  at=(datetime.now(timezone.utc)+timedelta(seconds=2**q['attempt'])).isoformat() if status=='RETRY' else now()
  with self.repo.connect() as c:c.execute('update workflow_queue set status=?,available_at=?,last_error=?,lease_owner=null,lease_until=null,updated_at=? where id=?',(status,at,error,now(),qid))
  return self.repo.one('select * from workflow_queue where id=?',(qid,))
 def cancel(self,qid):
  with self.repo.connect() as c:
   q=c.execute('select status from workflow_queue where id=?',(qid,)).fetchone()
   if not q:raise ValueError('activity_not_found')
   status='CANCELLED' if q['status'] in ('READY','RETRY') else q['status'];c.execute('update workflow_queue set cancel_requested=1,status=?,updated_at=? where id=?',(status,now(),qid))
  return self.repo.one('select * from workflow_queue where id=?',(qid,))
 def replay_dead_letter(self,qid,new_key=None):
  q=self.repo.one('select * from workflow_queue where id=?',(qid,))
  if not q or q['status']!='DEAD_LETTER':raise ValueError('dead_letter_not_found')
  return self.schedule(q['job_id'],q['activity'],json.loads(q['original_payload'] or '{}'),q['max_attempts'],new_key or q['idempotency_key']+':replay:'+uid())
 def checkpoint(self,job_id,state,payload=None):
  i=uid();ts=now()
  with self.repo.connect() as c:c.execute('insert into workflow_checkpoints values(?,?,?,?,?,?,?,?,?)',(i,job_id,state,'ACTIVE',1,json.dumps(payload or {}),'',ts,ts))
  return self.repo.one('select * from workflow_checkpoints where id=?',(i,))
 def pause(self,job_id):
  with self.repo.connect() as c:c.execute("update workflow_checkpoints set status='PAUSED',updated_at=? where job_id=? and status='ACTIVE'",(now(),job_id))
  return {'job_id':job_id,'status':'PAUSED'}
 def resume(self,job_id):
  with self.repo.connect() as c:c.execute("update workflow_checkpoints set status='ACTIVE',updated_at=? where job_id=? and status='PAUSED'",(now(),job_id))
  return self.repo.one('select * from workflow_checkpoints where job_id=? order by created_at desc',(job_id,))
class IntakeService:
 EXT={'.msgflow':'flows','.subflow':'subflows','.esql':'esql','.xsd':'schemas','.wsdl':'schemas','.map':'maps','.xslt':'maps','.policyxml':'policies'}
 def __init__(self,repo):self.repo=repo
 def scan(self,job_id,path,branch='main',revision=''):
  from app.services import workspace as _ws
  try:root=_ws.resolve(path,must_exist=True)
  except Exception:raise ValueError('repository_not_accessible')
  if not root.is_dir():raise ValueError('repository_not_accessible')
  inv={k:[] for k in set(self.EXT.values())};inv['files']=[]
  for f in root.rglob('*'):
   if f.is_file() and '.git' not in f.parts:
    rel=str(f.relative_to(root));inv['files'].append({'path':rel,'hash':hashlib.sha256(f.read_bytes()).hexdigest()})
    if f.suffix.lower() in self.EXT:inv[self.EXT[f.suffix.lower()]].append(rel)
  gaps=[] if inv['flows'] else ['no_message_flows_detected']
  if (root/'.git').exists():
   try:
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','--abbrev-ref','HEAD'],text=True).strip()
    if actual!=branch:gaps.append('branch_mismatch:'+actual)
    revision=revision or subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
   except Exception:gaps.append('git_metadata_unavailable')
  i=uid();raw=json.dumps(inv,sort_keys=True)
  with self.repo.connect() as c:c.execute('insert into intake_manifests values(?,?,?,?,?,?,?,?,?)',(i,job_id,str(root),branch,revision,raw,json.dumps(gaps),digest(raw),now()))
  return self.repo.one('select * from intake_manifests where id=?',(i,))

class DormancyService:
 def __init__(self,repo):self.repo=repo
 def assess(self,job_id,traffic_count=None,last_seen_days=None,deployed=None,already_migrated=None):
  e={'traffic_count':traffic_count,'last_seen_days':last_seen_days,'deployed':deployed,'already_migrated':already_migrated};known=sum(v is not None for v in e.values());confidence=known/4
  cls='ALREADY_MIGRATED' if already_migrated else ('UNKNOWN' if known<2 else ('INACTIVE' if deployed is False else ('DORMANT' if traffic_count==0 and (last_seen_days or 0)>=90 else ('ACTIVE' if (traffic_count or 0)>0 else 'UNKNOWN'))))
  i=uid();r='classification uses supplied evidence only'
  with self.repo.connect() as c:c.execute('insert into dormancy_assessments values(?,?,?,?,?,?,?)',(i,job_id,cls,confidence,json.dumps(e),r,now()))
  return self.repo.one('select * from dormancy_assessments where id=?',(i,))

class FrameworkService:
 def __init__(self,repo):self.repo=repo
 def analyze(self,job_id,path,version='1.0.0'):
  root=Path(path);build=[x for x in ('pom.xml','build.gradle','build.gradle.kts') if (root/x).exists()];conflicts=[] if build else ['missing_supported_build_file'];deps=[]
  if (root/'pom.xml').exists():deps=sorted(set(re.findall(r'<artifactId>([^<]+)',(root/'pom.xml').read_text(errors='ignore'))))
  standards={'build':build,'dependencies':deps};template='# Framework Template '+version+'\n\n## Build\n'+('\n'.join('- '+x for x in build) or '- UNRESOLVED')+'\n\n## Mandatory\n- Externalized configuration\n- Structured logging\n- Tests and security scans\n\n## Prohibited\n- Embedded secrets\n- Environment-specific endpoints\n- Test-specific branches\n'
  i=uid();
  with self.repo.connect() as c:c.execute('insert into framework_baselines values(?,?,?,?,?,?,?,?,?)',(i,job_id,version,template,json.dumps(standards),json.dumps(conflicts),'PENDING_APPROVAL',digest(template),now()))
  return self.repo.one('select * from framework_baselines where id=?',(i,))

class ACEAnalysisService:
 def __init__(self,repo):self.repo=repo
 def analyze(self,job_id,path,version='1.0.0'):
  root=Path(path);m={'flows':[],'esql':[],'schemas':[],'maps':[],'policies':[]};amb=[]
  for f in root.rglob('*'):
   if not f.is_file():continue
   rel=str(f.relative_to(root));ext=f.suffix.lower();text=f.read_text(errors='ignore') if f.stat().st_size<2000000 else ''
   if ext in ('.msgflow','.subflow'):
    nodes=re.findall(r'(?:type|xmi:type)=["\']([^"\']+)',text);m['flows'].append({'path':rel,'nodes':nodes})
    for n in nodes:
     if 'Custom' in n:amb.append({'status':'OPEN','question':'Unsupported custom node '+n,'owner':'ACE_SME','impact':'manual mapping required'})
   elif ext=='.esql':m['esql'].append({'path':rel,'modules':re.findall(r'(?i)CREATE\s+(?:COMPUTE\s+)?MODULE\s+(\w+)',text),'procedures':re.findall(r'(?i)CREATE\s+PROCEDURE\s+(\w+)',text)})
   elif ext in ('.xsd','.wsdl'):m['schemas'].append(rel)
   elif ext in ('.map','.xslt'):m['maps'].append(rel)
   elif ext=='.policyxml':m['policies'].append(rel)
  if not m['flows']:amb.append({'status':'OPEN','question':'No ACE flow detected','owner':'ACE_SME','impact':'analysis blocked'})
  deps=[{'from':x['path'],'to':x['nodes']} for x in m['flows']];raw=json.dumps(m,sort_keys=True);md=f"# ACE Analysis {version}\n\nFlows: {len(m['flows'])}\nESQL: {len(m['esql'])}\nAmbiguities: {len(amb)}\n";i=uid()
  with self.repo.connect() as c:c.execute('insert into ace_analyses values(?,?,?,?,?,?,?,?,?)',(i,job_id,version,raw,md,json.dumps(amb),json.dumps(deps),digest(raw),now()))
  return self.repo.one('select * from ace_analyses where id=?',(i,))

class PlanningGenerationService:
 def __init__(self,repo):self.repo=repo
 def _approved(self,job_id,gate,evidence_hash):
  for x in self.repo.rows('select * from approvals where job_id=? and gate=? order by created_at desc',(job_id,gate)):
   if x['decision']=='APPROVE' and evidence_hash in json.loads(x.get('artifact_bindings') or '{}').values():return True
  return False
 def plan(self,job_id,analysis_id,framework_id,specification_id,version='1.0.0'):
  a=self.repo.one('select * from ace_analyses where id=? and job_id=?',(analysis_id,job_id));f=self.repo.one('select * from framework_baselines where id=? and job_id=?',(framework_id,job_id));sp=self.repo.one("select * from migration_specs where id=? and job_id=? and validation_status='VALID'",(specification_id,job_id))
  if not a or not f or not sp:raise ValueError('approved_inputs_missing')
  if not self._approved(job_id,'G1',f['content_hash']):raise PermissionError('framework_not_approved')
  if not self._approved(job_id,'G2',a['content_hash']) or not self._approved(job_id,'G2',sp['content_hash']):raise PermissionError('analysis_or_specification_not_approved')
  model=json.loads(a['semantic_model']);tasks=[];maps=[]
  for n,x in enumerate(model['flows'],1):
   tid=f'PLAN-{n:03}';tasks.append({'id':tid,'source':x['path'],'boundary':'GENERATED','acceptance':['compile','test','traceability']});maps.append({'source':x['path'],'pattern':'spring-integration-flow'})
  i=uid();raw=json.dumps(tasks,sort_keys=True)
  with self.repo.connect() as c:c.execute('insert into migration_plans(id,job_id,version,tasks,mappings,risks,acceptance,content_hash,created_at,analysis_id,framework_id,specification_id,status) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(i,job_id,version,raw,json.dumps(maps),'[]',json.dumps(['build','scan','differential']),digest(raw),now(),analysis_id,framework_id,specification_id,'DRAFT'))
  return self.repo.one('select * from migration_plans where id=?',(i,))
 def generate(self,job_id,plan_id,package='com.example.migration'):
  p=self.repo.one('select * from migration_plans where id=? and job_id=?',(plan_id,job_id));out=[]
  if not p:raise ValueError('plan_not_found')
  approved=any(x['decision']=='APPROVE' and p['content_hash'] in json.loads(x.get('artifact_bindings') or '{}').values() for x in self.repo.rows("select * from approvals where job_id=? and gate='G3'",(job_id,)))
  if not approved:raise PermissionError('plan_not_approved')
  files={'pom.xml':'<project><modelVersion>4.0.0</modelVersion><groupId>'+package+'</groupId><artifactId>migration-service</artifactId><version>1.0.0</version></project>','src/main/resources/application.yml':'spring:\n  application:\n    name: migration-service\nexternal:\n  endpoint: ${EXTERNAL_ENDPOINT}\n'}
  appcls='MigrationApplication';files['src/main/java/'+package.replace('.','/')+'/'+appcls+'.java']='package '+package+';\npublic class '+appcls+' { public static void main(String[] args) {} }\n'
  for task in json.loads(p['tasks']):
   cls='Generated'+task['id'].replace('-','');files['src/main/java/'+package.replace('.','/')+'/'+cls+'.java']='package '+package+';\n// GENERATED REGION provenance='+task['id']+' source='+task['source']+'\npublic final class '+cls+' {}\n'
  for path,content in files.items():
   i=uid();prov={'plan_id':plan_id,'path':path}
   with self.repo.connect() as c:c.execute('insert into generated_artifacts values(?,?,?,?,?,?,?,?,?)',(i,job_id,plan_id,path,content,json.dumps(prov),'GENERATED_REGION_LOCKED',digest(content),now()))
   out.append(self.repo.one('select * from generated_artifacts where id=?',(i,)))
  return out
class AuditLedger:
 def __init__(self,repo):self.repo=repo
 def _write(self,c,job_id,actor,action,payload):
  prev=c.execute('select entry_hash from audit_ledger order by sequence desc limit 1').fetchone();ph=prev['entry_hash'] if prev else '';ts=now();raw=json.dumps(payload,sort_keys=True,separators=(',',':'),default=str);payload_hash=digest(raw);h=digest(ph+job_id+actor+action+payload_hash+ts);i=uid()
  c.execute('insert into audit_ledger(id,job_id,actor,action,payload_hash,previous_hash,entry_hash,created_at,payload_json) values(?,?,?,?,?,?,?,?,?)',(i,job_id,actor,action,payload_hash,ph,h,ts,raw))
  return {'id':i,'job_id':job_id,'actor':actor,'action':action,'payload_hash':payload_hash,'previous_hash':ph,'entry_hash':h,'created_at':ts,'payload_json':raw}
 def append(self,job_id,actor,action,payload,cursor=None):
  """Append one ledger entry, optionally on a caller-supplied transaction.

  A governed act and its ledger entry must commit together. Opening a second
  connection from inside an ``begin immediate`` scope would instead block on
  the writer lock, so callers already holding a cursor pass it in.
  """
  if cursor is not None:return self._write(cursor,job_id,actor,action,payload)
  with self.repo.connect() as c:entry=self._write(c,job_id,actor,action,payload)
  return self.repo.one('select * from audit_ledger where id=?',(entry['id'],))
 def verify(self):
  prev='';expected_sequence=1
  for x in self.repo.rows('select * from audit_ledger order by sequence'):
   if x['sequence']!=expected_sequence or x['previous_hash']!=prev:return False
   raw=x.get('payload_json') or '{}';ph=digest(raw)
   if ph!=x['payload_hash']:return False
   expected=digest(prev+(x['job_id'] or '')+(x['actor'] or '')+(x['action'] or '')+ph+x['created_at'])
   if expected!=x['entry_hash']:return False
   prev=x['entry_hash'];expected_sequence+=1
  return True
class SecurityService:
 def __init__(self,repo):self.repo=repo
 def secret_reference(self,name,reference):
  if re.search(r'(?i)(password|secret|token)=',reference):raise ValueError('raw_secret_not_allowed')
  i=uid();
  with self.repo.connect() as c:c.execute('insert into secrets values(?,?,?,?)',(i,name,reference,now()))
  return {'id':i,'reference':reference}
 def scan(self,job_id,path,content):
  rules=[('SECRET','HIGH',r'(?i)(password|secret|token)\s*[=:]\s*["\'][^"\']+["\']'),('ENDPOINT','MEDIUM',r'https?://(?:localhost|\d+\.\d+\.\d+\.\d+)')];out=[]
  for rid,sev,rx in rules:
   for m in re.finditer(rx,content):
    i=uid();
    with self.repo.connect() as c:c.execute('insert into security_findings values(?,?,?,?,?,?,?,?,?)',(i,job_id,'BUILTIN',sev,rid,path,m.group(0)[:120],'OPEN',now()))
    out.append({'id':i,'severity':sev,'rule_id':rid})
  return out
 def metric(self,job_id,name,value,labels=None):
  i=uid();
  with self.repo.connect() as c:c.execute('insert into metrics values(?,?,?,?,?,?)',(i,job_id,name,float(value),json.dumps(labels or {}),now()))
  return {'id':i,'name':name,'value':float(value)}
