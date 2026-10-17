import json, os, uuid, hashlib
from datetime import datetime, timezone
from pathlib import Path
from app import persistence


SCHEMA="""
create table if not exists jobs(id text primary key,service text not null,owner text not null,source_revision text not null,state text not null,created_at text not null,rollback_pointer text,iteration integer default 1);
create table if not exists workflow_events(id text primary key,job_id text,actor text,event_type text,from_state text,to_state text,attempt integer,input_version text,output_version text,policy_decision text,correlation_id text,details text,created_at text);
create table if not exists approval_policies(gate text primary key,required_role text not null,allowed_decisions text not null,min_evidence integer not null default 1,enabled integer not null default 1);
create table if not exists approvals(id text primary key,job_id text,gate text,decision text,approver_role text,approver_identity text,comments text,conditions text,evidence_ids text,policy_decision text,rollback_pointer text,created_at text);
create table if not exists evidence(id text primary key,job_id text,kind text,version text,uri text,content_hash text,producer text,metadata text,immutable integer default 1,created_at text);
create table if not exists versions(id text primary key,job_id text,iteration integer,source_commit text,target_commit text,migration_spec_version text,framework_version text,agent_versions text,policy_version text,rollback_pointer text,status text,created_at text);
create table if not exists agent_registry(name text,version text,accepted_inputs text,produced_outputs text,required_role text,enabled integer default 1,primary key(name,version));
create table if not exists agent_runs(id text primary key,job_id text,agent_name text,agent_version text,status text,input_evidence text,output_evidence text,correlation_id text,error text,started_at text,completed_at text);
create table if not exists migration_specs(id text primary key,job_id text,version text,yaml_text text,content_hash text,validation_status text,validation_errors text,created_at text,unique(job_id,version));
create table if not exists clarifications(id text primary key,job_id text,stage text,question text,owner text,impact text,evidence_ref text,status text,answer text,created_at text,answered_at text);
create table if not exists comparisons(id text primary key,job_id text,case_id text,policy text,expected_hash text,actual_hash text,status text,created_at text);
create table if not exists variances(id text primary key,comparison_id text,job_id text,path text,expected text,actual text,severity text,disposition text,created_at text);
create table if not exists root_causes(id text primary key,job_id text,variance_ids text,hypothesis text,confidence real,supporting_evidence text,missing_evidence text,affected_scope text,status text,created_at text);

create table if not exists workflow_checkpoints(id text primary key,job_id text,state text,status text,attempt integer,payload text,last_error text,created_at text,updated_at text);
create table if not exists workflow_queue(id text primary key,job_id text,activity text,status text,attempt integer,max_attempts integer,idempotency_key text unique,payload text,available_at text,created_at text,updated_at text);
create table if not exists intake_manifests(id text primary key,job_id text,repository_uri text,branch text,revision text,inventory text,gaps text,content_hash text,created_at text);
create table if not exists dormancy_assessments(id text primary key,job_id text,classification text,confidence real,evidence text,rationale text,created_at text);
create table if not exists framework_baselines(id text primary key,job_id text,version text,template_md text,standards text,conflicts text,status text,content_hash text,created_at text);
create table if not exists ace_analyses(id text primary key,job_id text,version text,semantic_model text,analysis_md text,ambiguities text,dependencies text,content_hash text,created_at text);
create table if not exists migration_plans(id text primary key,job_id text,version text,tasks text,mappings text,risks text,acceptance text,content_hash text,created_at text);
create table if not exists generated_artifacts(id text primary key,job_id text,plan_id text,path text,content text,provenance text,region_policy text,content_hash text,created_at text);
create table if not exists audit_ledger(sequence integer primary key autoincrement,id text unique,job_id text,actor text,action text,payload_hash text,previous_hash text,entry_hash text unique,created_at text);
create table if not exists secrets(id text primary key,name text unique,secret_reference text not null,created_at text);
create table if not exists security_findings(id text primary key,job_id text,scanner text,severity text,rule_id text,path text,detail text,status text,created_at text);

create table if not exists connector_profiles(id text primary key,category text not null,name text not null,provider text not null,config text not null,secret_reference text,priority integer default 100,enabled integer default 1,status text default 'UNVERIFIED',last_checked_at text,created_at text,updated_at text,unique(category,name));
create table if not exists connector_operations(id text primary key,profile_id text,operation text,request_hash text,status text,response_metadata text,error text,correlation_id text,created_at text,completed_at text);
create table if not exists routing_policies(category text primary key,strategy text not null,failover_enabled integer default 1,health_required integer default 1,updated_at text);
create table if not exists metrics(id text primary key,job_id text,name text,value real,labels text,created_at text);
create table if not exists remediations(id text primary key,job_id text,root_cause_id text,proposal text,approved_pattern text,regression_tests text,status text,iteration integer,rollback_pointer text,created_at text);
create table if not exists unsupported_constructs(id text primary key,job_id text,analysis_id text,source_path text,node_id text,construct_type text,classification text,impact text,status text,owner text,evidence_ref text);
create table if not exists validation_reports(id text primary key,job_id text,report_type text,version text,payload text,content_hash text,status text,created_at text,reference_id text);
create table if not exists approval_packages(id text primary key,job_id text,gate text,status text,payload text,content_hash text,artefact_bindings text,conditions text,created_at text);
create table if not exists mapping_catalogues(id text primary key,job_id text,version text,framework_id text,analysis_id text,specification_id text,mappings text,content_hash text,status text,created_at text,updated_at text);
create table if not exists unsupported_mappings(id text primary key,job_id text,catalogue_id text,source_reference text,ace_construct text,reason text,status text,owner text,created_at text);
create table if not exists migration_risks(id text primary key,job_id text,catalogue_id text,category text,severity text,statement text,response text,owner text,status text,created_at text);
create table if not exists quality_runs(id text primary key,job_id text,plan_id text,status text,payload text,content_hash text,created_at text,project_root text);
create table if not exists differential_packages(id text primary key,job_id text,plan_id text,status text,payload text,content_hash text,created_at text);
create table if not exists differential_evidence(id text primary key,job_id text,package_id text,case_id text,status text,payload text,content_hash text,created_at text,disposition text);
create table if not exists service_registry(id text primary key,name text,owner text,repository_uri text,runtime_inventory text,migration_status text,project_scope text,environment text,created_at text,updated_at text);
create table if not exists prompt_registry(id text primary key,name text,version text,template text,content_hash text,status text,approved_by text,effective_from text,created_at text,unique(name,version));
create table if not exists model_registry(id text primary key,name text,version text,provider_profile text,policy text,region text,status text,approved_by text,created_at text,unique(name,version));
create table if not exists model_requests(id text primary key,job_id text,prompt_id text,model_id text,input_hash text,response_metadata text,status text,correlation_id text,created_at text);
create table if not exists knowledge_index(id text primary key,job_id text,kind text,reference text,content_hash text,tokens text,version text,lineage text,created_at text);
create table if not exists gate_controls(gate text primary key,quorum integer,timeout_minutes integer,escalation_profile text,bypass_role text,downstream_effect text,features text,updated_at text);
create table if not exists delivery_operations(id text primary key,job_id text,category text,operation text,target text,status text,request_hash text,response_metadata text,rollback_pointer text,created_at text);
create table if not exists notifications(id text primary key,job_id text,event_type text,recipient text,payload text,status text,profile_id text,created_at text,updated_at text);
create table if not exists observability_events(id text primary key,job_id text,signal_type text,name text,value real,labels text,correlation_id text,severity text,created_at text);
create table if not exists retention_policies(id text primary key,artefact_class text,retention_days integer,redaction_policy text,deletion_mode text,status text,updated_at text);
create table if not exists users(id text primary key,name text not null,email text not null unique,password_hash text not null,password_salt text not null,password_iterations integer not null,role text not null,granted_roles text default '',status text default 'ACTIVE',failed_attempts integer default 0,locked_until text default '',created_at text,updated_at text,last_login_at text default '');
create table if not exists user_integrations(id text primary key,user_id text not null references users(id),provider text not null,base_url text not null,token_encrypted text not null,token_fingerprint text default '',status text default 'UNVERIFIED',account_login text default '',scopes text default '',last_checked_at text default '',last_error text default '',created_at text,updated_at text,unique(user_id,provider));
create table if not exists job_repositories(id text primary key,job_id text not null,role text not null,repo_url text default '',branch text default '',enabled integer default 1,local_path text default '',resolved_commit text default '',push_mode text default 'LOCAL_OUTPUT',created_at text,updated_at text,unique(job_id,role));
create table if not exists pipelines(id text primary key,job_id text not null,name text default '',workspace_dir text not null,status text default 'DRAFT',created_by text default '',created_at text,updated_at text,started_at text default '',completed_at text default '');
create table if not exists pipeline_steps(id text primary key,pipeline_id text not null,sequence integer default 0,kind text not null,agent_id text default '',version text default '1.0.0',depends_on text default '[]',status text default 'PENDING',agent_run_id text default '',queue_id text default '',params text default '{}',outputs text default '{}',started_at text default '',completed_at text default '',error text default '',created_at text,updated_at text);
create table if not exists agent_llm_bindings(id text primary key,agent_id text not null unique,binding_mode text not null default 'DEFAULT',gateway_model_alias text default '',model_id text default '',status text not null default 'ACTIVE',created_by text default '',created_at text,updated_at text);
create table if not exists agent_llm_binding_history(id text primary key,agent_id text not null,action text not null,binding_mode text default '',gateway_model_alias text default '',model_id text default '',model_revision text default '',provider_type text default '',reason_for_change text default '',status text default '',actor text default '',created_at text);
create table if not exists agent_templates(id text primary key,name text not null unique,description text default '',agent_ids text not null default '[]',created_by text not null,created_at text not null,updated_at text not null);
create table if not exists skill_runtimes(id text primary key,name text not null unique,display_name text default '',language text not null,language_version text default '',framework text default '',framework_version_range text default '',build_tool text default '',base_image text default '',config text default '{}',enabled integer default 1,created_at text,updated_at text,created_by text default '');
create table if not exists skills(id text primary key,skill_key text not null,version text not null,display_name text default '',description text default '',target_runtime_id text not null,toolchain_runtime_id text default '',source_path text default '',content_hash text default '',accepted_source_types text default '[]',produced_artifact_types text default '[]',backend_types text default '[]',status text default 'DRAFT',approved_by text default '',approved_at text default '',deprecated_by text default '',deprecated_at text default '',superseded_by_id text default '',created_at text,updated_at text,created_by text default '',unique(skill_key,version));
create table if not exists skill_agent_bindings(id text primary key,skill_id text not null,agent_id text not null,binding_type text default 'PRIMARY',created_at text,updated_at text,created_by text default '',unique(skill_id,agent_id));
create table if not exists pipeline_step_logs(id text primary key,step_id text not null,level text not null,message text not null,created_at text not null);
create table if not exists pipeline_context(id text primary key,pipeline_id text not null,step_id text not null,agent_id text not null,job_id text not null,outputs text default '{}',evidence_ids text default '[]',metrics text default '{}',reasoning text default '{}',error text default '',skill_name text default '',skill_hash text default '',skill_summary text default '',run_id text default '',status text default '',created_at text not null,unique(pipeline_id,agent_id));
"""
def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())
def digest(x): return hashlib.sha256(x if isinstance(x,bytes) else str(x).encode()).hexdigest()
class Repository:
 def __init__(self,path=None,driver=None):
  self.driver=driver if driver is not None else persistence.build(dsn=path)
  self.path=getattr(self.driver,'dsn',path)
  self.init()
 def connect(self):
  """Open a transaction scope that commits, rolls back, and always closes."""
  return self.driver.session()
 def init(self):
  with self.connect() as c:
   c.executescript(SCHEMA)
   for g,r,d in [('G0','SERVICE_OWNER','PROCEED,STOP,CLARIFY'),('G1','JAVA_ARCHITECT','APPROVE,REJECT,CLARIFY'),('G2','ACE_SME','APPROVE,REJECT,CLARIFY'),('G3','PRODUCT_OWNER','APPROVE,REJECT,CLARIFY'),('G4','QA_LEAD','CONTINUE_FEEDBACK,STOP,ACCEPT_EXCEPTION'),('G5','RELEASE_MANAGER','REPEAT,STOP,PROMOTE')]: c.execute('insert or ignore into approval_policies values(?,?,?,?,1)',(g,r,d,1))
   self._upgrade_agent_registry(c)
   self._seed_agent_catalogue(c)
   self._upgrade_connector_profiles(c)
   self._upgrade_governance(c)
   self._upgrade_agent_llm_bindings(c)
   self._upgrade_skill_registry(c)
   self._upgrade_notifications(c)
   self._seed_skill_runtimes(c)
   self._seed_model_gateway(c)
 #: Aliases every agent in the catalogue can resolve to. Seeded together
 #: because AT-13 in AgentRegistry.dispatch arms for *every* agent as soon as
 #: model_registry holds any row: registering one alias and not the rest turns
 #: a silent fallback into a hard refusal on every other agent.
 GATEWAY_ALIASES=('reasoning-small','reasoning-large','coding','semantic-review','embeddings')
 def _seed_model_gateway(self,c):
  """Make the model path the default: prompts, aliases and a gateway profile.

  Without these three groups ``ReasoningGateway.reason`` returns
  ``no_approved_prompt_or_model`` on every call and all 15 agents run their
  deterministic fallback -- which looks like a healthy pipeline, because a
  recorded fallback is a successful run. A fresh database therefore came up
  with the model switched off and nothing saying so.

  ``approved_by`` is ``SYSTEM_BOOTSTRAP``, not a person. These rows are not a
  governance decision and must not read like one: the design has an
  ``AI_GOVERNANCE_ADMIN`` approve prompts through the governed endpoint, and a
  deployment that wants that control back sets
  ``FACTORY_SEED_MODEL_GATEWAY=0`` and approves them itself. The audit trail
  then says plainly which rows a human signed and which the bootstrap wrote.

  Idempotent by name: an existing row is never overwritten, so a prompt an
  administrator has since rejected or re-approved keeps its own status.
  """
  if (os.getenv('FACTORY_SEED_MODEL_GATEWAY','1') or '').strip().lower() in ('0','false','no'):return
  ts=now()
  if not c.execute("select 1 from connector_profiles where category='model-gateway' limit 1").fetchone():
   c.execute('insert into connector_profiles(id,category,name,provider,config,secret_reference,priority,enabled,status,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?)',
             (uid(),'model-gateway','model-primary','anthropic-compatible',json.dumps({'note':'base url and key come from the provider adapter, not from here'}),'',100,1,'VERIFIED',ts,ts))
  for alias in self.GATEWAY_ALIASES:
   if not c.execute('select 1 from model_registry where name=? limit 1',(alias,)).fetchone():
    c.execute('insert into model_registry(id,name,version,provider_profile,policy,region,status,approved_by,created_at) values(?,?,?,?,?,?,?,?,?)',
              (uid(),alias,'v1','model-primary','{}','','APPROVED','SYSTEM_BOOTSTRAP',ts))
  path=Path(__file__).resolve().parents[1]/'config'/'agent-prompts.v1.json'
  try:prompts=json.loads(path.read_text(encoding='utf-8'))['prompts']
  except (OSError,ValueError,KeyError):return
  for p in prompts:
   if c.execute('select 1 from prompt_registry where name=? and version=? limit 1',(p['name'],str(p['version']))).fetchone():continue
   c.execute('insert into prompt_registry(id,name,version,template,content_hash,status,approved_by,effective_from,created_at) values(?,?,?,?,?,?,?,?,?)',
             (uid(),p['name'],str(p['version']),p['template'],digest(p['template']),'APPROVED','SYSTEM_BOOTSTRAP',ts,ts))
 def _catalogue(self):
  p=Path(__file__).resolve().parents[1]/'config'/'agent-catalogue.v1.json'
  return json.loads(p.read_text(encoding='utf-8'))['agents']
 def contract_hash(self,a):
  """Public: also used by AgentAdminService when it writes a live registry row.

  Reusing the exact function the startup drift check hashes against means an
  admin-written row and a subsequently-updated catalogue file always agree.
  """
  keys=('agent_id','display_name','version','description','accepted_inputs','produced_outputs','required_role','tool_allowlist','dependencies','status','effective_from','approved_by','approval_evidence_id','supersedes_version','enabled')
  return digest(json.dumps({k:a.get(k) for k in keys},sort_keys=True,separators=(',',':')))
 def _upgrade_agent_registry(self,c):
  existing={r[1] for r in c.execute('pragma table_info(agent_registry)').fetchall()}
  # origin distinguishes a row seeded from the version-controlled catalogue
  # file from one written live through the agent-registry admin API, so the
  # startup drift check below can leave the latter alone rather than either
  # crashing on it or silently reverting it on every restart.
  additions={'agent_id':'text','display_name':'text','description':'text','tool_allowlist':'text','dependencies':'text','contract_hash':'text','status':'text','effective_from':'text','approved_by':'text','approval_evidence_id':'text','supersedes_version':'text','last_health_status':'text','last_health_at':'text',"origin":"text default 'CATALOGUE'",'default_model_alias':'text'}
  for col,typ in additions.items():
   if col not in existing:c.execute(f'alter table agent_registry add column {col} {typ}')
  c.execute("update agent_registry set agent_id=lower(replace(replace(name,' Agent',''),' ','-')) where agent_id is null")
  c.execute('update agent_registry set display_name=name where display_name is null')
  c.execute("update agent_registry set origin='CATALOGUE' where origin is null")
 def _seed_agent_catalogue(self,c):
  for a in self._catalogue():
   h=self.contract_hash(a); row=c.execute('select * from agent_registry where name=? and version=?',(a['display_name'],a['version'])).fetchone()
   if row and (row['origin'] or 'CATALOGUE')=='RUNTIME':
    # A live-administered row is authoritative over the file for this
    # (name,version) pair until someone deliberately re-syncs it -- seeding
    # must not silently overwrite an admin's change on every restart, nor
    # drift-check it against a file it has intentionally diverged from.
    continue
   values=(a['agent_id'],a['display_name'],a['description'],json.dumps(a['accepted_inputs']),json.dumps(a['produced_outputs']),a['required_role'],1 if a['enabled'] else 0,json.dumps(a['tool_allowlist']),json.dumps(a['dependencies']),h,a['status'],a['effective_from'],a['approved_by'],a['approval_evidence_id'],a['supersedes_version'],a.get('default_model_alias',''))
   if row:
    if row['contract_hash'] and row['contract_hash']!=h: raise RuntimeError('agent_contract_drift:'+a['agent_id']+':'+a['version'])
    c.execute("update agent_registry set agent_id=?,display_name=?,description=?,accepted_inputs=?,produced_outputs=?,required_role=?,enabled=?,tool_allowlist=?,dependencies=?,contract_hash=?,status=?,effective_from=?,approved_by=?,approval_evidence_id=?,supersedes_version=?,default_model_alias=? where name=? and version=?",values+(a['display_name'],a['version']))
   else:
    c.execute("insert into agent_registry(name,version,accepted_inputs,produced_outputs,required_role,enabled,agent_id,display_name,description,tool_allowlist,dependencies,contract_hash,status,effective_from,approved_by,approval_evidence_id,supersedes_version,last_health_status,last_health_at,origin,default_model_alias) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(a['display_name'],a['version'],json.dumps(a['accepted_inputs']),json.dumps(a['produced_outputs']),a['required_role'],1 if a['enabled'] else 0,a['agent_id'],a['display_name'],a['description'],json.dumps(a['tool_allowlist']),json.dumps(a['dependencies']),h,a['status'],a['effective_from'],a['approved_by'],a['approval_evidence_id'],a['supersedes_version'],'UNKNOWN','','CATALOGUE',a.get('default_model_alias','')))
 def _upgrade_connector_profiles(self,c):
  existing={r[1] for r in c.execute('pragma table_info(connector_profiles)').fetchall()}
  for col,typ in {'environment':'text','project_scope':'text','config_version':'integer default 1','updated_by':'text'}.items():
   if col not in existing:c.execute(f'alter table connector_profiles add column {col} {typ}')
  c.execute("update connector_profiles set environment='DEV' where environment is null")
  c.execute("update connector_profiles set project_scope='GLOBAL' where project_scope is null")
  c.execute('update connector_profiles set config_version=1 where config_version is null')

 def _upgrade_governance(self,c):
  upgrades={
   "jobs":{"project_scope":"text default 'GLOBAL'","environment":"text default 'DEV'"},
   # An approval is only valid for the iteration it was granted against, so
   # rework cannot be promoted on a signature given before it.
   "approvals":{"artifact_bindings":"text default '{}'","iteration":"integer default 1"},
   "clarifications":{"answered_by":"text default ''","run_id":"text default ''",
                     "suggested_answer":"text default ''","suggestion_mode":"text default ''",
                     "suggestion_confidence":"text default ''","suggestion_caveat":"text default ''",
                     "suggested_at":"text default ''"},
   "differential_evidence":{"dispositioned_by":"text default ''","dispositioned_at":"text default ''","disposition_reason":"text default ''"},
   # Section 8 requires a Variance to carry its normalisation and
   # classification alongside severity and disposition.
   "variances":{"kind":"text default 'VALUE'","normalisation":"text default '{}'"},
   "workflow_queue":{"lease_owner":"text","lease_until":"text","heartbeat_at":"text","cancel_requested":"integer default 0","last_error":"text default ''","original_payload":"text"},
   "audit_ledger":{"payload_json":"text default '{}'"},
   "migration_plans":{"analysis_id":"text","framework_id":"text","specification_id":"text","status":"text default 'DRAFT'"},
   # A run recorded only its agent's display name, so nothing tied it to the
   # catalogue's stable agent_id, to the principal that started it, or to the
   # prompt and model versions that produced its output. Without
   # operator_identity any caller could complete somebody else's run.
   "agent_runs":{"agent_id":"text","operator_identity":"text default ''","requested_tools":"text default '[]'","params_hash":"text default ''","outputs":"text default '{}'","model_provenance":"text default '{}'","iteration":"integer default 1","reasoning":"text default ''","effective_model_alias":"text default ''","binding_source":"text default ''"}
  }
  for table,cols in upgrades.items():
   existing={r[1] for r in c.execute('pragma table_info('+table+')').fetchall()}
   for col,typ in cols.items():
    if col not in existing:c.execute(f'alter table {table} add column {col} {typ}')
  c.execute("update jobs set project_scope='GLOBAL' where project_scope is null")
  c.execute("update jobs set environment='DEV' where environment is null")
  c.execute("update workflow_queue set original_payload=payload where original_payload is null")
  c.execute('update approvals set iteration=1 where iteration is null')
  c.execute('update agent_runs set agent_id=(select agent_id from agent_registry where agent_registry.name=agent_runs.agent_name) where agent_id is null')
 def _upgrade_agent_llm_bindings(self,c):
  existing={r[1] for r in c.execute('pragma table_info(agent_llm_bindings)').fetchall()}
  for col,typ in {'model_revision':"text default ''",'provider_type':"text default 'litellm_alias'",'reason_for_change':"text default ''",'activated_by':"text default ''",'capabilities':"text default ''",'licence_id':"text default ''",'data_region':"text default ''",'data_classification_allowed':"text default ''",'context_limit':"integer default 0",'output_limit':"integer default 0"}.items():
   if col not in existing:c.execute(f'alter table agent_llm_bindings add column {col} {typ}')
 def _upgrade_skill_registry(self,c):
  for col,typ in {'display_name':"text default ''",'language_version':"text default ''",'framework':"text default ''",'framework_version_range':"text default ''",'build_tool':"text default ''",'base_image':"text default ''",'created_by':"text default ''"}.items():
   if col not in {r[1] for r in c.execute('pragma table_info(skill_runtimes)').fetchall()}:c.execute(f'alter table skill_runtimes add column {col} {typ}')
  for col,typ in {'toolchain_runtime_id':"text default ''",'source_path':"text default ''",'content_hash':"text default ''",'accepted_source_types':"text default '[]'",'produced_artifact_types':"text default '[]'",'backend_types':"text default '[]'",'deprecated_by':"text default ''",'deprecated_at':"text default ''",'superseded_by_id':"text default ''",'updated_at':"text default ''",'created_by':"text default ''",'approved_at':"text default ''"}.items():
   if col not in {r[1] for r in c.execute('pragma table_info(skills)').fetchall()}:c.execute(f'alter table skills add column {col} {typ}')
  for col,typ in {'updated_at':"text default ''",'created_by':"text default ''"}.items():
   if col not in {r[1] for r in c.execute('pragma table_info(skill_agent_bindings)').fetchall()}:c.execute(f'alter table skill_agent_bindings add column {col} {typ}')
 def _upgrade_notifications(self,c):
  existing={r[1] for r in c.execute('pragma table_info(notifications)').fetchall()}
  for col,typ in {'is_read':"integer default 0",'title':"text default ''",'link':"text default ''"}.items():
   if col not in existing:c.execute(f'alter table notifications add column {col} {typ}')
 def _seed_skill_runtimes(self,c):
  defaults=[
   {'name':'java-21-spring-boot','display_name':'Java Runtime','language':'java','language_version':'21','framework':'spring-boot','framework_version_range':'3.2.x','build_tool':'maven','base_image':'eclipse-temurin:21-jre-alpine','config':'{"jvm_flags":"-XX:+UseContainerSupport","framework_starter":"3.2.2","cvs_phases":["1","2"]}'},
   {'name':'python-311-fastapi','display_name':'Python Runtime','language':'python','language_version':'3.11','framework':'fastapi','framework_version_range':'>=0.104.0','build_tool':'pip','base_image':'python:3.11-slim','config':'{}'},
   {'name':'nodejs-20-express','display_name':'Node.js Runtime','language':'nodejs','language_version':'20','framework':'express','framework_version_range':'>=4.18.0','build_tool':'npm','base_image':'node:20-alpine','config':'{}'},
  ]
  ts=now()
  for r in defaults:
   if not c.execute('select id from skill_runtimes where name=?',(r['name'],)).fetchone():
    self.insert(c,'skill_runtimes',{'id':uid(),'name':r['name'],'display_name':r['display_name'],'language':r['language'],'language_version':r['language_version'],'framework':r['framework'],'framework_version_range':r['framework_version_range'],'build_tool':r['build_tool'],'base_image':r['base_image'],'config':r['config'],'enabled':1,'created_at':ts,'updated_at':ts,'created_by':'system'})
 def event(self,c,job_id,actor,event_type,from_state='',to_state='',details=None,policy='{}',correlation=None):
  """Write one domain event on an existing transaction.

  Every governed act must leave a queryable event, not only the two the
  state machine happened to emit. Services holding a cursor call this so the
  event commits or rolls back with the act it describes.
  """
  eid=uid()
  self.insert(c,'workflow_events',{'id':eid,'job_id':job_id,'actor':actor,'event_type':event_type,'from_state':from_state,'to_state':to_state,'attempt':1,'input_version':'','output_version':'','policy_decision':policy if isinstance(policy,str) else json.dumps(policy,sort_keys=True,default=str),'correlation_id':correlation or eid,'details':json.dumps(details or {},sort_keys=True,default=str),'created_at':now()})
  return eid
 def insert(self,c,table,values):
  """Column-named insert executed on an existing transaction.

  Positional ``insert into t values(?,...)`` breaks silently whenever a column
  is added by an upgrade -- that is precisely how the clarification endpoint
  became unusable. Always name the columns.
  """
  cols=list(values)
  c.execute('insert into %s(%s) values(%s)'%(table,','.join(cols),','.join('?'*len(cols))),tuple(values[k] for k in cols))
  return values.get('id')
 def rows(self,q,a=()):
  with self.connect() as c:return [dict(x) for x in c.execute(q,a)]
 def one(self,q,a=()):
  with self.connect() as c:
   x=c.execute(q,a).fetchone(); return dict(x) if x else None
