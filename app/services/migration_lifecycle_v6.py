import json, re, shutil, subprocess
from pathlib import Path
from app.repository import uid, now, digest
from app.services.enterprise_v5 import SpringProjectGenerator, BuildRunner, DifferentialEvidenceEngine
from app.services.policy import gate_enabled

DEFAULT_MAPPINGS={
 'HTTPInput':{'pattern':'SPRING_MVC_CONTROLLER','module':'web','generated':True,'tests':['contract','negative']},
 'HTTPReply':{'pattern':'RESPONSE_ENTITY','module':'web','generated':True,'tests':['status_headers']},
 'SOAPInput':{'pattern':'SPRING_WS_ENDPOINT','module':'soap','generated':True,'tests':['wsdl_contract']},
 'SOAPReply':{'pattern':'SPRING_WS_RESPONSE','module':'soap','generated':True,'tests':['soap_fault']},
 'MQInput':{'pattern':'JMS_LISTENER','module':'messaging','generated':True,'tests':['message_contract','redelivery']},
 'MQOutput':{'pattern':'JMS_TEMPLATE','module':'messaging','generated':True,'tests':['outbound_message']},
 'KafkaConsumer':{'pattern':'KAFKA_LISTENER','module':'messaging','generated':True,'tests':['consumer_contract']},
 'KafkaProducer':{'pattern':'KAFKA_TEMPLATE','module':'messaging','generated':True,'tests':['producer_contract']},
 'FileInput':{'pattern':'SPRING_INTEGRATION_FILE_ADAPTER','module':'integration','generated':True,'tests':['file_ingress']},
 'FileOutput':{'pattern':'SPRING_INTEGRATION_FILE_HANDLER','module':'integration','generated':True,'tests':['file_egress']},
 'ComputeNode':{'pattern':'SPRING_SERVICE_MAPPER','module':'service','generated':False,'tests':['transformation']},
 'Compute':{'pattern':'SPRING_SERVICE_MAPPER','module':'service','generated':False,'tests':['transformation']},
 'Filter':{'pattern':'PREDICATE_FILTER','module':'service','generated':False,'tests':['branch_coverage']},
 'Route':{'pattern':'ROUTER_STRATEGY','module':'service','generated':False,'tests':['routing_matrix']},
 'Database':{'pattern':'SPRING_DATA_REPOSITORY','module':'persistence','generated':False,'tests':['transaction','rollback']},
 'TimeoutNotification':{'pattern':'SCHEDULED_TRIGGER','module':'scheduler','generated':True,'tests':['timing']},
 'Catch':{'pattern':'EXCEPTION_HANDLER','module':'error','generated':False,'tests':['error_path']},
 'Throw':{'pattern':'DOMAIN_EXCEPTION','module':'error','generated':False,'tests':['exception_contract']},
 'Mapping':{'pattern':'TYPED_MAPPER','module':'mapping','generated':False,'tests':['mapping_golden']},
 'XSLT':{'pattern':'XSLT_TRANSFORMER','module':'mapping','generated':True,'tests':['xslt_golden']},
 'ESQL':{'pattern':'TYPED_SERVICE_LOGIC','module':'service','generated':False,'tests':['esql_semantics']},
 'SCHEMA':{'pattern':'GENERATED_CONTRACT_MODEL','module':'model','generated':True,'tests':['schema_validation']},
 'POLICY':{'pattern':'EXTERNALIZED_CONFIGURATION','module':'config','generated':True,'tests':['configuration_binding']},
 # Outbound calls. HTTPRequest/SOAPRequest are the ``ComIbmWSRequest`` side of
 # the pair below -- see the note on ``_WS_NODE_ROLES``.
 'HTTPRequest':{'pattern':'REST_CLIENT_WRAPPER','module':'client','generated':True,'tests':['backend_contract']},
 'SOAPRequest':{'pattern':'SOAP_BACKEND_CONNECTOR','module':'soap','generated':False,'tests':['backend_contract']},
}

# ACE's generic HTTP input/reply/request nodes are named ``ComIbmWSInput`` /
# ``ComIbmWSReply`` / ``ComIbmWSRequest`` regardless of whether they carry a
# SOAP envelope or plain JSON -- "WS" is IIB's historical "Web Services" family
# name, not a SOAP marker. external-skills/iib-to-ms-migration/SKILL.md's own
# backend-type detection table draws this exact line on signal (SOAP envelope
# in ESQL vs. a plain external URL with no QL/RxClaim/SOAP routing), not on
# the node's class name -- so the name alone can never resolve these three.
# key(construct name) -> (key when plain HTTP/JSON, key when a SOAP envelope)
_WS_NODE_ROLES={'wsinput':('HTTPInput','SOAPInput'),'wsreply':('HTTPReply','SOAPReply'),'wsrequest':('HTTPRequest','SOAPRequest')}

# Every IIB service generates a Catch/Failure/Timeout subflow for its own input
# node, each just composing an error response (see common-patterns-and-gotchas.md
# and the per-backend-type reference files' ``*_TimeoutHandler_ComposeError``
# rows). ``CatchHandler`` already matched the 'Catch' key by accident (its name
# contains "catch"); Failure and Timeout do not contain any approved key's name
# even though they are the same construct -- only the HTTP status they must
# return differs (Timeout is specifically 408, never 500), which is a code-gen
# detail belonging to the EXCEPTION_HANDLER pattern, not a different one.
_HANDLER_SUFFIXES=('catchhandler','failurehandler','timeouthandler')

def mapping_key(name,approved,properties=None):
 """Resolve an ACE construct name to its approved mapping key, or None.

 ``properties`` is the node's own properties dict (``messageDomainProperty``,
 ``URLSpecifier``, ...) when known -- only the WS* roles above need it, to
 tell a SOAP use of the node from a plain HTTP/REST one. Every other
 construct resolves on the name alone, exactly as before.

 Shared so the specification agent derives target modules through the same
 normalisation the catalogue uses. Two copies of this matching would let a
 specification promise a module the mapping catalogue then refuses to produce.
 """
 normal=''.join(c for c in str(name) if c.isalnum()).lower()
 for role,(http_key,soap_key) in _WS_NODE_ROLES.items():
  if role in normal:
   domain=str((properties or {}).get('messageDomainProperty','')).upper()
   target=soap_key if domain not in ('','JSON') else http_key
   return target if target in approved else None
 if any(suffix in normal for suffix in _HANDLER_SUFFIXES):
  return 'Catch' if 'Catch' in approved else None
 return next((k for k in approved if ''.join(c for c in k if c.isalnum()).lower() in normal),None)

def is_presentation_only(node_type):
 """An ACE Toolkit canvas icon or routing-target label -- never a Spring pattern.

 ``utility:GIFFileGraphic`` is the flow's palette icon reference; ``ComIbmLabel``
 is a named anchor ``ComIbmRouteToLabel`` routes to, with no transformation
 logic of its own. Neither should ever reach ``DEFAULT_MAPPINGS`` -- a GIF has
 nothing to generate, and a Label's own "code" is nothing (the control-flow
 point it marks is sequenced by whatever follows it, not by the label itself).
 Checked by prefix, not substring, so ``ComIbmRouteToLabel`` (a real routing
 node, already resolved by the generic match above) is never caught by this.
 """
 return str(node_type).startswith('utility:') or str(node_type).startswith('ComIbmLabel.')

class MappingAndPlanningService:
 def __init__(self,repo):
  self.repo=repo;self.generator=SpringProjectGenerator();self.builder=BuildRunner();self.diff=DifferentialEvidenceEngine()
 def _approved(self,job_id,gate,content_hash):
  # A gate switched off in approval_policies counts as satisfied, so the
  # database flag is the one switch for gating on every path.
  if not gate_enabled(self.repo,gate):return {'id':'gate_disabled','gate':gate,'decision':'APPROVE'}
  for a in self.repo.rows('select * from approvals where job_id=? and gate=? order by created_at desc',(job_id,gate)):
   if a['decision']!='APPROVE':continue
   bindings=json.loads(a.get('artifact_bindings') or '{}')
   if content_hash in bindings.values():return a
  return None
 def _inputs(self,job_id,framework_id,analysis_id,specification_id):
  """Resolve and gate-check the planning inputs.

  Framework is optional: when ``framework_id`` is empty or resolves to no
  row, planning proceeds without it and the G1 gate check is skipped.
  Analysis and specification are always required.
  """
  f=self.repo.one('select * from framework_baselines where id=? and job_id=?',(framework_id,job_id)) if framework_id else None
  a=self.repo.one('select * from ace_analyses where id=? and job_id=?',(analysis_id,job_id))
  s=self.repo.one("select * from migration_specs where id=? and job_id=? and validation_status='VALID'",(specification_id,job_id))
  if not a or not s:raise ValueError('approved_planning_inputs_missing')
  if f and not self._approved(job_id,'G1',f['content_hash']):raise PermissionError('g1_framework_approval_required')
  if not self._approved(job_id,'G2',a['content_hash']) or not self._approved(job_id,'G2',s['content_hash']):raise PermissionError('g2_analysis_and_yaml_approval_required')
  return f,a,s
 def create_mapping_catalogue(self,job_id,framework_id,analysis_id,specification_id,version='1.0.0',overrides=None):
  f,a,s=self._inputs(job_id,framework_id,analysis_id,specification_id);model=json.loads(a['semantic_model']);approved=dict(DEFAULT_MAPPINGS);approved.update(overrides or {})
  fw_hash=f['content_hash'] if f else ''
  mappings=[];unsupported=[];risks=[]
  def add(source,kind,name,properties=None):
   key=mapping_key(name,approved,properties)
   if key:
    target=approved[key];mappings.append({'id':uid(),'source_reference':source,'ace_construct':name,'mapping_key':key,'spring_pattern':target['pattern'],'target_module':target['module'],'boundary':'GENERATED' if target['generated'] else 'HAND_WRITTEN_PROTECTED','tests':target['tests'],'framework_hash':fw_hash,'analysis_hash':a['content_hash'],'specification_hash':s['content_hash']})
   else:
    item={'id':uid(),'source_reference':source,'ace_construct':name or 'UNKNOWN','reason':'NO_APPROVED_SPRING_PATTERN','status':'OPEN','owner':'ACE_SME_AND_JAVA_ARCHITECT'};unsupported.append(item);risks.append({'id':uid(),'category':'UNSUPPORTED_MAPPING','severity':'HIGH','source_reference':source,'statement':'No approved Spring mapping for '+str(name),'response':'Clarify or implement approved plug-in mapping','owner':'ACE_SME_AND_JAVA_ARCHITECT','status':'OPEN'})
  for flow in model.get('flows',[])+model.get('subflows',[]):
   for node in flow.get('nodes',[]):
    ntype=node.get('type','UNKNOWN')
    if is_presentation_only(ntype):continue
    add(flow.get('path','')+'#'+str(node.get('id','')), 'NODE',ntype,node.get('properties') or {})
  for x in model.get('esql',[]):add(x.get('path',''),'ESQL','ESQL')
  for x in model.get('maps',[]):add(x.get('path',''),'MAP',x.get('type','Mapping'))
  for x in model.get('schemas',[]):add(x.get('path',''),'SCHEMA','SCHEMA')
  for x in model.get('policies',[]):add(x.get('path',''),'POLICY','POLICY')
  cid=uid();raw=json.dumps(mappings,sort_keys=True);ts=now()
  with self.repo.connect() as c:
   c.execute('insert into mapping_catalogues values(?,?,?,?,?,?,?,?,?,?,?)',(cid,job_id,version,framework_id,analysis_id,specification_id,raw,digest(raw),'DRAFT',ts,ts))
   for x in unsupported:c.execute('insert into unsupported_mappings values(?,?,?,?,?,?,?,?,?)',(x['id'],job_id,cid,x['source_reference'],x['ace_construct'],x['reason'],x['status'],x['owner'],ts))
   for x in risks:c.execute('insert into migration_risks values(?,?,?,?,?,?,?,?,?,?)',(x['id'],job_id,cid,x['category'],x['severity'],x['statement'],x['response'],x['owner'],x['status'],ts))
   # Clarification blocking is disabled — unsupported mappings are recorded
   # in unsupported_mappings but not inserted as open clarifications.
  return {'catalogue_id':cid,'mapping_count':len(mappings),'unsupported_count':len(unsupported),'risk_count':len(risks),'status':'DRAFT','content_hash':digest(raw)}
 def generate_plan(self,job_id,catalogue_id,version='1.0.0'):
  cat=self.repo.one('select * from mapping_catalogues where id=? and job_id=?',(catalogue_id,job_id))
  if not cat:raise ValueError('mapping_catalogue_not_found')
  if self.repo.one("select count(*) n from unsupported_mappings where catalogue_id=? and status='OPEN'",(catalogue_id,))['n']:raise ValueError('unsupported_mappings_block_plan')
  mappings=json.loads(cat['mappings']);tasks=[];previous=None
  for index,m in enumerate(mappings,1):
   tid=f'IMP-{index:03}';task={'id':tid,'sequence':index,'source_reference':m['source_reference'],'mapping_id':m['id'],'spring_pattern':m['spring_pattern'],'target_module':m['target_module'],'boundary':m['boundary'],'depends_on':[previous] if previous else [],'tests':m['tests'],'acceptance':['source trace recorded','unit tests pass','configuration externalized'],'security_checks':['no embedded secrets','no environment endpoint','dependency allow-list'],'rollback_checkpoint':f'pre-{tid}'};tasks.append(task);previous=tid
  risks=self.repo.rows('select * from migration_risks where catalogue_id=?',(catalogue_id,));acceptance=['compile passes','unit tests pass','static analysis passes','dependency and licence checks pass','SAST and secret scan pass','provenance manifest complete','differential test package prepared'];raw=json.dumps(tasks,sort_keys=True);pid=uid();ts=now()
  with self.repo.connect() as c:c.execute('insert into migration_plans(id,job_id,version,tasks,mappings,risks,acceptance,content_hash,created_at,analysis_id,framework_id,specification_id,status) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,job_id,version,raw,json.dumps(mappings),json.dumps(risks),json.dumps(acceptance),digest(raw),ts,cat['analysis_id'],cat['framework_id'],cat['specification_id'],'DRAFT'))
  return {'plan_id':pid,'task_count':len(tasks),'status':'DRAFT','content_hash':digest(raw),'implementation_sequence':[x['id'] for x in tasks]}
 def g3_package(self,job_id,plan_id,catalogue_id):
  p=self.repo.one('select * from migration_plans where id=? and job_id=?',(plan_id,job_id));c=self.repo.one('select * from mapping_catalogues where id=? and job_id=?',(catalogue_id,job_id))
  if not p or not c:raise ValueError('plan_or_catalogue_not_found')
  risks=self.repo.rows('select * from migration_risks where catalogue_id=?',(catalogue_id,));items=[{'kind':'MIGRATION_PLAN','id':p['id'],'version':p['version'],'hash':p['content_hash']},{'kind':'MAPPING_CATALOGUE','id':c['id'],'version':c['version'],'hash':c['content_hash']}];package={'gate':'G3','approver_role':'SOLUTION_ARCHITECT','decision_options':['APPROVE','REJECT','CLARIFY'],'plan':p,'mapping_catalogue':c,'risks':risks,'artefacts':items,'decision':'PENDING_APPROVAL','created_at':now()};raw=json.dumps(package,sort_keys=True,default=str);package_id=uid()
  with self.repo.connect() as db:db.execute('insert into approval_packages values(?,?,?,?,?,?,?,?,?)',(package_id,job_id,'G3','PENDING_APPROVAL',raw,digest(raw),json.dumps(items),'[]',now()))
  return {'package_id':package_id,'status':'PENDING_APPROVAL','content_hash':digest(raw),'artefacts':items}
 def generate_spring(self,job_id,plan_id,output_root,service_name,package_name):
  p=self.repo.one('select * from migration_plans where id=? and job_id=?',(plan_id,job_id))
  if not p:raise ValueError('plan_not_found')
  if not self._approved(job_id,'G3',p['content_hash']):raise PermissionError('g3_plan_approval_required')
  a=self.repo.one('select * from ace_analyses where id=?',(p['analysis_id'],))
  f=self.repo.one('select * from framework_baselines where id=?',(p['framework_id'],)) if p['framework_id'] else None
  s=self.repo.one('select * from migration_specs where id=?',(p['specification_id'],))
  if not a or not s:raise ValueError('generation_inputs_missing')
  # Framework is optional: when absent, generate with default conventions.
  framework={'standards':json.loads(f['standards']),'version':f['version'],'content_hash':f['content_hash']} if f else {'standards':{},'version':'0.0.0','content_hash':''}
  model=json.loads(a['semantic_model']);tasks=json.loads(p['tasks'] or '[]')
  result=self.generator.generate(output_root,service_name,package_name,model,plan_id,framework,tasks=tasks)
  manifest={'plan_id':plan_id,'analysis_id':a['id'],'analysis_hash':a['content_hash'],'specification_id':s['id'],'specification_hash':s['content_hash'],'framework_id':f['id'] if f else '','framework_hash':f['content_hash'] if f else '','files':result['manifest'],'generated_at':now()};mf=Path(result['root'])/'provenance-manifest.json';mf.write_text(json.dumps(manifest,indent=2))
  return {'project_root':result['root'],'file_count':len(result['manifest'])+1,'manifest':str(mf),'content_hash':digest(json.dumps(manifest,sort_keys=True)),'stub_sources':result.get('stub_sources',{}),'semantic_model':model}
 def quality_validate(self,job_id,plan_id,project_root,execute_build=False):
  root=Path(project_root);checks=[]
  checks.append(self._check('PROJECT_STRUCTURE',(root/'pom.xml').exists() or (root/'build.gradle').exists()))
  text='\n'.join(f.read_text(errors='ignore') for f in root.rglob('*') if f.is_file() and f.suffix.lower() in ('.java','.yml','.yaml','.properties') and f.stat().st_size<2_000_000)
  checks.append(self._check('SECRET_SCAN',not bool(re.search(r'(?i)(password|secret|token)\s*[:=]\s*["\'][^$][^"\']+',text))))
  checks.append(self._check('HARDCODED_ENDPOINT_SCAN',not bool(re.search(r'https?://(?!localhost|example)',text))))
  checks.append(self._check('PROVENANCE',(root/'provenance-manifest.json').exists()))
  for name in ('STATIC_ANALYSIS','DEPENDENCY_CHECK','LICENCE_CHECK','SAST'):
   checks.append({'name':name,'status':'NOT_CONFIGURED','evidence':'enterprise tool adapter required'})
  if execute_build:
   build=self.builder.run(root);checks.append({'name':'COMPILE_AND_UNIT_TEST','status':'PASS' if build['status']=='PASS' else 'FAIL','evidence':build})
  else:checks.append({'name':'COMPILE_AND_UNIT_TEST','status':'NOT_EXECUTED','evidence':'execute_build=false'})
  status='FAIL' if any(x['status']=='FAIL' for x in checks) else ('PASS_WITH_PENDING_TOOLS' if any(x['status'].startswith('NOT_') for x in checks) else 'PASS');rid=uid();payload={'checks':checks,'project_root':str(root),'plan_id':plan_id}
  with self.repo.connect() as db:db.execute('insert into quality_runs values(?,?,?,?,?,?,?,?)',(rid,job_id,plan_id,status,json.dumps(payload),digest(json.dumps(payload,sort_keys=True,default=str)),now(),str(root)))
  return {'quality_run_id':rid,'status':status,'checks':checks}
 def prepare_differential(self,job_id,plan_id,cases,policy):
  p=self.repo.one('select * from migration_plans where id=? and job_id=?',(plan_id,job_id))
  if not p:raise ValueError('plan_not_found')
  if not cases:raise ValueError('test_cases_required')
  package_id=uid();normalized=[]
  for case in cases:
   normalized.append({'id':case['id'],'input':case.get('input'),'headers':case.get('headers',{}),'timing':case.get('timing',{}),'side_effect_observations':case.get('side_effect_observations',[]),'secure_references':case.get('secure_references',[])})
  payload={'plan_id':plan_id,'cases':normalized,'policy':policy,'required_runtimes':['ACE','SPRING_BOOT'],'status':'READY_FOR_EXECUTION','created_at':now()};raw=json.dumps(payload,sort_keys=True)
  with self.repo.connect() as db:db.execute('insert into differential_packages values(?,?,?,?,?,?,?)',(package_id,job_id,plan_id,'READY_FOR_EXECUTION',raw,digest(raw),now()))
  return {'package_id':package_id,'case_count':len(cases),'status':'READY_FOR_EXECUTION','content_hash':digest(raw)}
 def record_differential(self,job_id,package_id,case_id,ace_result,spring_result,policy):
  result=self.diff.compare(case_id,ace_result,spring_result,policy);rid=uid();raw=json.dumps(result,sort_keys=True,default=str)
  with self.repo.connect() as db:
   self.repo.insert(db,'differential_evidence',{'id':rid,'job_id':job_id,'package_id':package_id,'case_id':case_id,'status':result['status'],'payload':raw,'content_hash':result['content_hash'],'created_at':now(),'disposition':'OPEN' if result['status']=='VARIANCE' else 'CLOSED'})
   self.repo.event(db,job_id,'differential-runner','DIFFERENTIAL_RECORDED',details={'evidence_id':rid,'package_id':package_id,'case_id':case_id,'status':result['status'],'content_hash':result['content_hash']})
  return {'evidence_id':rid,**result}
 DISPOSITIONS={'REMEDIATED','ACCEPTED_EXCEPTION'}
 def disposition_differential(self,job_id,evidence_id,disposition,reason,actor):
  """Close an open differential variance with an attributable decision.

  Promotion requires that no variance is still open. Accepting one rather than
  fixing it is the G4 ACCEPT_EXCEPTION decision, so that disposition is only
  available once such an approval exists.
  """
  if disposition not in self.DISPOSITIONS:raise ValueError('invalid_disposition:'+str(disposition))
  if not str(reason or '').strip():raise ValueError('disposition_reason_required')
  row=self.repo.one('select * from differential_evidence where id=? and job_id=?',(evidence_id,job_id))
  if not row:raise ValueError('differential_evidence_not_found')
  if disposition=='ACCEPTED_EXCEPTION' and not self.repo.one("select id from approvals where job_id=? and gate='G4' and decision='ACCEPT_EXCEPTION' limit 1",(job_id,)):
   raise PermissionError('g4_accept_exception_approval_required')
  detail={'evidence_id':evidence_id,'case_id':row['case_id'],'from_disposition':row['disposition'],'disposition':disposition,'reason':reason}
  with self.repo.connect() as db:
   db.execute('update differential_evidence set disposition=?,disposition_reason=?,dispositioned_by=?,dispositioned_at=? where id=?',(disposition,reason,actor,now(),evidence_id))
   self.repo.event(db,job_id,actor,'DIFFERENTIAL_DISPOSITIONED',details=detail)
  return self.repo.one('select * from differential_evidence where id=?',(evidence_id,))
 def _check(self,name,ok):return {'name':name,'status':'PASS' if ok else 'FAIL'}
