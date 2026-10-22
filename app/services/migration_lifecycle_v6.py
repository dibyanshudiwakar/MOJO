import json, os, re, shutil, subprocess
from pathlib import Path
from app.repository import uid, now, digest
from app.services.enterprise_v5 import SpringProjectGenerator, BuildRunner, DifferentialEvidenceEngine
from app.services import skill_gate, build_check
from app.services.feedback import is_externalized_config
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

def _long(path):
 """``path``, written through Windows' extended-length form when needed.

 The skill's prescribed layout is deep on purpose -- an ``EIPMS_<service>``
 root, then a ``*-web`` module, then ``src/main/java`` and a five-segment
 package -- roughly 150 characters before the workspace root is even counted.
 On Windows that crosses MAX_PATH (260) for any realistic workspace and the
 write fails with FileNotFoundError on a directory that was just created, which
 reads as a logic bug and is not one. The extended-length prefix lifts the
 limit; everywhere else this is the path unchanged.
 """
 if os.name != 'nt':return path
 prefix='\\\\?\\'
 text=str(path)
 if text.startswith(prefix) or len(text)<250:return path
 return Path(prefix+text) if Path(text).is_absolute() else path


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
 def planning_inputs(self,job_id,framework_id,analysis_id,specification_id):
  """Gate-check and return the planning inputs, for a caller that maps them itself.

  Kept separate from mapping so the gate enforcement runs identically whether
  the catalogue is model-authored or computed: G1/G2 approval binding is not
  something a mapping strategy gets to skip.
  """
  return self._inputs(job_id,framework_id,analysis_id,specification_id)
 def record_mapping_catalogue(self,job_id,framework_id,analysis_id,specification_id,catalogue,version='1.0.0'):
  """Persist a mapping catalogue, however it was produced.

  The hashes that tie each mapping to the artefacts it was derived from are
  stamped here rather than taken from the catalogue: they are what a G3
  approval binds, and a model-supplied hash would be a guess.
  """
  f,a,s=self._inputs(job_id,framework_id,analysis_id,specification_id)
  fw_hash=f['content_hash'] if f else ''
  # Optional schema fields are defaulted here, not read optionally downstream:
  # generate_plan and the G3 package index tests/boundary directly, so a model
  # omitting one would raise KeyError mid-plan instead of degrading.
  mappings=[{'mapping_key':'','tests':[],'boundary':'GENERATED','target_module':'service',**m,'id':m.get('id') or uid(),'framework_hash':fw_hash,'analysis_hash':a['content_hash'],'specification_hash':s['content_hash']} for m in catalogue.get('mappings') or []]
  unsupported=[{**x,'id':x.get('id') or uid(),'status':x.get('status') or 'OPEN','owner':x.get('owner') or 'ACE_SME_AND_JAVA_ARCHITECT','reason':x.get('reason') or 'NO_APPROVED_SPRING_PATTERN'} for x in catalogue.get('unsupported') or []]
  risks=[{**r,'id':r.get('id') or uid(),'status':r.get('status') or 'OPEN','owner':r.get('owner') or 'ACE_SME_AND_JAVA_ARCHITECT','source_reference':r.get('source_reference') or '','response':r.get('response') or ''} for r in catalogue.get('risks') or []]
  cid=uid();raw=json.dumps(mappings,sort_keys=True);ts=now()
  with self.repo.connect() as c:
   c.execute('insert into mapping_catalogues values(?,?,?,?,?,?,?,?,?,?,?)',(cid,job_id,version,framework_id,analysis_id,specification_id,raw,digest(raw),'DRAFT',ts,ts))
   for x in unsupported:c.execute('insert into unsupported_mappings values(?,?,?,?,?,?,?,?,?)',(x['id'],job_id,cid,x.get('source_reference',''),x.get('ace_construct','UNKNOWN'),x['reason'],x['status'],x['owner'],ts))
   for x in risks:c.execute('insert into migration_risks values(?,?,?,?,?,?,?,?,?,?)',(x['id'],job_id,cid,x.get('category','UNSUPPORTED_MAPPING'),x.get('severity','HIGH'),x.get('statement',''),x['response'],x['owner'],x['status'],ts))
   # Clarification blocking is disabled — unsupported mappings are recorded
   # in unsupported_mappings but not inserted as open clarifications.
  return {'catalogue_id':cid,'mapping_count':len(mappings),'unsupported_count':len(unsupported),'risk_count':len(risks),'status':'DRAFT','content_hash':digest(raw)}
 def compute_mapping_catalogue(self,job_id,framework_id,analysis_id,specification_id,overrides=None):
  """The deterministic mapping: DEFAULT_MAPPINGS over the model's constructs.

  The fallback. It can only map a construct that already has an entry in that
  dict, which is exactly why mapping is worth moving to the model -- a
  construct the dict does not know becomes an unsupported entry that blocks
  the plan, however ordinary it is.
  """
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
  return {'mappings':mappings,'unsupported':unsupported,'risks':risks}
 def create_mapping_catalogue(self,job_id,framework_id,analysis_id,specification_id,version='1.0.0',overrides=None):
  """Deterministic end to end, for callers with no agent context to reason with."""
  return self.record_mapping_catalogue(job_id,framework_id,analysis_id,specification_id,self.compute_mapping_catalogue(job_id,framework_id,analysis_id,specification_id,overrides),version)
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
  risks=self.repo.rows('select * from migration_risks where catalogue_id=?',(catalogue_id,));items=[{'kind':'MIGRATION_PLAN','id':p['id'],'version':p['version'],'hash':p['content_hash']},{'kind':'MAPPING_CATALOGUE','id':c['id'],'version':c['version'],'hash':c['content_hash']}];package={'gate':'G3','approver_role':'PRODUCT_OWNER','decision_options':['APPROVE','REJECT','CLARIFY'],'plan':p,'mapping_catalogue':c,'risks':risks,'artefacts':items,'decision':'PENDING_APPROVAL','created_at':now()};raw=json.dumps(package,sort_keys=True,default=str);package_id=uid()
  with self.repo.connect() as db:db.execute('insert into approval_packages values(?,?,?,?,?,?,?,?,?)',(package_id,job_id,'G3','PENDING_APPROVAL',raw,digest(raw),json.dumps(items),'[]',now()))
  return {'package_id':package_id,'status':'PENDING_APPROVAL','content_hash':digest(raw),'artefacts':items}
 def generation_inputs(self,job_id,plan_id):
  """Gate-check and return the generation inputs, for a caller that authors the files itself.

  Separated from ``generate_spring`` so the G3 check runs identically whether
  the project is model-authored or generated: an unapproved plan must not be
  buildable by choosing a different authoring strategy.
  """
  p=self.repo.one('select * from migration_plans where id=? and job_id=?',(plan_id,job_id))
  if not p:raise ValueError('plan_not_found')
  if not self._approved(job_id,'G3',p['content_hash']):raise PermissionError('g3_plan_approval_required')
  a=self.repo.one('select * from ace_analyses where id=?',(p['analysis_id'],))
  f=self.repo.one('select * from framework_baselines where id=?',(p['framework_id'],)) if p['framework_id'] else None
  s=self.repo.one('select * from migration_specs where id=?',(p['specification_id'],))
  if not a or not s:raise ValueError('generation_inputs_missing')
  return p,a,f,s
 def record_generated_project(self,job_id,plan_id,output_root,root_name,files,action_items=(),stub_sources=None):
  """Persist a model-authored project exactly as a generated one.

  ``files`` is ``[(relative_path, content)]``. Every write is re-resolved
  under the workspace root before it happens: the paths came from a model, so
  containment is enforced here rather than assumed from the schema's pattern,
  which is only a cheap filter and does not stop ``..``.

  The manifest, the provenance manifest, the content hash and the
  ``generated_artifacts`` rows are built the same way ``generate_spring``
  builds them, so a gate, the output panel and variance analysis cannot tell
  the two paths apart.
  """
  from app.services import workspace as _ws
  p,a,f,s=self.generation_inputs(job_id,plan_id)
  base=_ws.resolve(Path(output_root)/(root_name or ''),must_exist=False,create=True)
  manifest=[];refused=[]
  for rel,content in files:
   try:target=_ws.resolve(base/rel,must_exist=False)
   except Exception as exc:refused.append('%s:%s' % (rel,type(exc).__name__));continue
   if base not in target.parents and target!=base:refused.append(rel+':path_outside_project');continue
   _long(target.parent).mkdir(parents=True,exist_ok=True);_long(target).write_text(content,encoding='utf-8')
   manifest.append({'path':target.relative_to(base).as_posix(),'hash':digest(content),'plan_id':plan_id})
  result={'root':str(base),'manifest':manifest,'content_hash':digest(json.dumps(manifest,sort_keys=True))}
  (base/'generated-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
  self._register_artifacts(job_id,plan_id,result)
  model=json.loads(a['semantic_model'])
  items=list(action_items)+[('path refused: '+x) for x in refused]
  provenance={'plan_id':plan_id,'analysis_id':a['id'],'analysis_hash':a['content_hash'],'specification_id':s['id'],'specification_hash':s['content_hash'],'framework_id':f['id'] if f else '','framework_hash':f['content_hash'] if f else '','files':manifest,'action_items':items,'authored_by':'MODEL','ace_endpoints':[x for x in model.get('interfaces',[]) if x.get('endpoint') not in ('','UNKNOWN',None)],'generated_at':now()}
  mf=base/'provenance-manifest.json';mf.write_text(json.dumps(provenance,indent=2),encoding='utf-8')
  return {'project_root':str(base),'file_count':len(manifest)+1,'manifest':str(mf),'content_hash':digest(json.dumps(provenance,sort_keys=True)),'action_items':items,'refused':refused,'stub_sources':stub_sources or {},'semantic_model':model}
 def generate_spring(self,job_id,plan_id,output_root,service_name,package_name,source_root=None):
  """Write the target project from the approved plan.

  ``source_root`` is the ACE repository the analysis was taken from. It is
  optional and only ever read: the generator translates each ESQL module's own
  SET assignments into the transformer bodies, which the semantic model cannot
  carry because it records module *names* rather than statements. Without it
  the project is still generated and wired -- the transformers simply arrive
  without translated assignments, which the README then lists as an action.
  """
  p=self.repo.one('select * from migration_plans where id=? and job_id=?',(plan_id,job_id))
  if not p:raise ValueError('plan_not_found')
  if not self._approved(job_id,'G3',p['content_hash']):raise PermissionError('g3_plan_approval_required')
  a=self.repo.one('select * from ace_analyses where id=?',(p['analysis_id'],))
  f=self.repo.one('select * from framework_baselines where id=?',(p['framework_id'],)) if p['framework_id'] else None
  s=self.repo.one('select * from migration_specs where id=?',(p['specification_id'],))
  if not a or not s:raise ValueError('generation_inputs_missing')
  # Framework is optional: when absent, generate with default conventions.
  framework={'standards':json.loads(f['standards']),'version':f['version'],'content_hash':f['content_hash']} if f else {'standards':{},'version':'0.0.0','content_hash':''}
  # The plan carries the approved Spring pattern per ACE construct, and the
  # generator emits what the plan says rather than re-deciding from the node's
  # name -- so what G3 authorised and what is written are the same thing.
  plan={'tasks':json.loads(p['tasks'] or '[]'),'mappings':json.loads(p['mappings'] or '[]')}
  model=json.loads(a['semantic_model']);result=self.generator.generate(output_root,service_name,package_name,model,plan_id,framework,source_root=source_root,plan=plan)
  self._register_artifacts(job_id,plan_id,result)
  manifest={'plan_id':plan_id,'analysis_id':a['id'],'analysis_hash':a['content_hash'],'specification_id':s['id'],'specification_hash':s['content_hash'],'framework_id':f['id'] if f else '','framework_hash':f['content_hash'] if f else '','files':result['manifest'],'action_items':result.get('action_items',[]),'generated_roles':result.get('roles',[]),'ace_endpoints':[x for x in model.get('interfaces',[]) if x.get('endpoint') not in ('','UNKNOWN',None)],'generated_at':now()};mf=Path(result['root'])/'provenance-manifest.json';mf.write_text(json.dumps(manifest,indent=2))
  # stub_sources/semantic_model: code-generation's business-logic synthesis
  # reads these to fill a placeholder body from the real ACE source. This was
  # {} from when the generator moved to springgen.py, which meant the synthesis
  # stage returned 'no_stubs' on every run and the model was never asked for
  # the logic at all -- silently, because an empty dict is a valid answer.
  # springgen now reports the transformer bodies it could not fully translate
  # and the routing predicates it left accepting everything, each with the ACE
  # source reference the real implementation comes from.
  return {'project_root':result['root'],'file_count':len(result['manifest'])+1,'manifest':str(mf),'content_hash':digest(json.dumps(manifest,sort_keys=True)),'action_items':result.get('action_items',[]),'stub_sources':result.get('stub_sources',{}),'semantic_model':model}
 #: Generated text worth keeping in the database. Build output and binaries are
 #: on disk and in git; what the UI renders and what variance analysis diffs
 #: against the IIB source is source and configuration.
 REGISTERED_SUFFIXES=('.java','.xml','.yml','.yaml','.properties','.md','.json')
 def _register_artifacts(self,job_id,plan_id,result):
  """Record each generated file in ``generated_artifacts``.

  Nothing wrote this table on the agent path, which had two visible
  consequences: the agent output panel resolves a code-generation run through
  it and therefore showed an empty file list, and ``variance-analysis``
  skipped every structural check with ``no_generated_artifacts``. The project
  was on disk the whole time -- it was simply never registered.

  Re-registering is idempotent per (job, plan, path): a re-run of the same
  plan replaces its rows rather than accumulating a second copy.
  """
  root=Path(result['root']);rows=[]
  for item in result['manifest']:
   path=item['path'];file=root/path
   if not file.suffix.lower() in self.REGISTERED_SUFFIXES:continue
   # Read through the same extended-length form the writer used. A plain read of
   # a path past MAX_PATH raises FileNotFoundError on Windows, and skipping it
   # here dropped every file deep in the package tree -- all the Java -- so the
   # hardcoding check reported "no_ms_java_files" for a project full of it.
   try:content=_long(file).read_text(encoding='utf-8')
   except OSError:continue
   rows.append((uid(),job_id,plan_id,path,content,json.dumps({'plan_id':plan_id,'path':path,'content_hash':item['hash']}),'GENERATED_REGION_LOCKED',item['hash'],now()))
  with self.repo.connect() as c:
   c.execute('delete from generated_artifacts where job_id=? and plan_id=?',(job_id,plan_id))
   c.executemany('insert into generated_artifacts values(?,?,?,?,?,?,?,?,?)',rows)
  return len(rows)
 def quality_validate(self,job_id,plan_id,project_root,execute_build=False):
  root=Path(project_root);checks=[]
  # A build file at the root (a flat project) or in a module directory directly
  # under it (the multi-module layout the skill prescribes: a repository root
  # holding ``<service>-parent/pom.xml`` and ``<service>-web/pom.xml``, and no
  # pom.xml of its own). This asked for the root only, so it failed every
  # skill-compliant project -- it was written for the flat tree the old
  # generator produced, before the layout came from the skill.
  checks.append(self._check('PROJECT_STRUCTURE',any(
   p.exists() for p in (root/'pom.xml',root/'build.gradle',*root.glob('*/pom.xml'),*root.glob('*/build.gradle')))))
  config_files=[f for f in root.rglob('*') if f.is_file() and f.suffix.lower() in ('.java','.yml','.yaml','.properties') and f.stat().st_size<2_000_000]
  text='\n'.join(f.read_text(errors='ignore') for f in config_files)
  checks.append(self._check('SECRET_SCAN',not bool(re.search(r'(?i)(password|secret|token)\s*[:=]\s*["\'][^$][^"\']+',text))))
  # Externalized deployment config (config-map/, application-local.*) is
  # exempt from this one check -- see is_externalized_config.
  # Comments are ignored, and so are URLs that name a standard, a namespace or a
  # parser feature. This was a bare ``https?://`` regex over everything, which
  # failed a project for a Javadoc mention of an XML namespace and for the line
  # that disables DOCTYPE declarations (the standard XXE protection).
  endpoints=skill_gate.hardcoded_endpoints([f for f in config_files if not is_externalized_config(f)])
  checks.append({'name':'HARDCODED_ENDPOINT_SCAN','status':'FAIL' if endpoints else 'PASS',**({'evidence':endpoints[:12]} if endpoints else {})})
  checks.append(self._check('PROVENANCE',(root/'provenance-manifest.json').exists()))
  for name in ('STATIC_ANALYSIS','DEPENDENCY_CHECK','LICENCE_CHECK','SAST'):
   checks.append({'name':name,'status':'NOT_CONFIGURED','evidence':'enterprise tool adapter required'})
  # Build and start the service: "generated" has to mean "runs". ``execute_build``
  # True/False is an explicit caller choice; the agents pass 'auto', which builds
  # whenever Maven and Java are installed. See app/services/build_check.py.
  checks.extend(build_check.checks(root,execute_build))
  # The code-generation skill's own checklist (gap-checklist.md), run mechanically.
  # BLOCKING items fail the gate; the rest are named GAPs, which is what the
  # skill says to do with them. See app/services/skill_gate.py.
  checks.extend(skill_gate.run(root))
  status='FAIL' if any(x['status']=='FAIL' for x in checks) else ('PASS_WITH_GAPS' if any(x['status']=='GAP' for x in checks) else ('PASS_WITH_PENDING_TOOLS' if any(x['status'].startswith('NOT_') for x in checks) else 'PASS'));rid=uid();payload={'checks':checks,'project_root':str(root),'plan_id':plan_id}
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
