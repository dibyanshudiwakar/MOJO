import tempfile,json
from pathlib import Path
import pytest
from app.repository import Repository
from app.service import FactoryService
from app.services.evidence import EvidenceService
from app.services.policy import PolicyError
from app.services.specification import SpecificationService
from app.services.differential import DifferentialService
from app.services.rca import RCAService

# Agent counts are derived from the governed catalogue rather than hardcoded, so
# adding a contract does not silently turn these invariants into stale numbers.
def _catalogue_agent_ids():
    import json, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / 'config' / 'agent-catalogue.v1.json'
    return {a['agent_id'] for a in json.loads(path.read_text(encoding='utf-8'))['agents']}


CATALOGUE_AGENT_COUNT = len(_catalogue_agent_ids())


def _catalogue_display_names():
    import json, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / 'config' / 'agent-catalogue.v1.json'
    return {a['display_name'] for a in json.loads(path.read_text(encoding='utf-8'))['agents']}


def setup():
 d=tempfile.TemporaryDirectory();r=Repository(str(Path(d.name)/'x.db'));f=FactoryService(r);j=f.create_job({'service':'Order','owner':'Team','sourceRevision':'abc','rollbackPointer':'tag/v1'},'tester');return d,r,f,j

def test_state_machine_and_blocking():
 """INTAKE_VALIDATED -> FRAMEWORK_BASELINED is blocked by G0, not by a clarification.

 This asserted that an open clarification blocked the transition. Clarification
 blocking has since been deliberately removed (see the comments in
 AgentRegistry.dispatch, PriorityOneService.analyze_ace and
 MappingAndPlanningService.create_mapping_catalogue), so the clarification is
 now inert and the edge is held by the G0 gate instead -- which raises
 PolicyError, a PermissionError rather than the ValueError this expected.
 """
 d,r,f,j=setup();f.transition(j['id'],'INTAKE_VALIDATED','tester')
 with r.connect() as c:r.insert(c,'clarifications',{'id':'c1','job_id':j['id'],'stage':'INTAKE','question':'Question','owner':'Owner','impact':'Impact','evidence_ref':'ev','status':'OPEN','answer':'','created_at':'now','answered_at':'','answered_by':''})
 with pytest.raises(PolicyError,match='required_gate_approval_missing:G0'):f.transition(j['id'],'FRAMEWORK_BASELINED','tester')
 d.cleanup()

def test_policy_requires_role_and_evidence():
 d,r,f,j=setup();e=EvidenceService(r).register(j['id'],'ANALYSIS','1','mem://a','content','agent')
 req={'job_id':j['id'],'gate':'G2','decision':'APPROVE','approver_role':'ACE_SME','approver_identity':'sme@example','evidence_ids':[e['id']]}
 assert f.approve(req,('ACE_SME',))['policy_decision']['outcome']=='ALLOW'
 with pytest.raises(PolicyError):f.approve(req,('VIEWER',))
 d.cleanup()

def test_schema_comparison_and_rca():
 d,r,f,j=setup();s=SpecificationService(r,Path(__file__).parents[1]/'schemas/migration-specification.schema.json');text=(Path(__file__).parents[1]/'examples/migration-specification.yaml').read_text();assert s.save(j['id'],'1',text)['status']=='VALID'
 result=DifferentialService(r).compare(j['id'],'TC1',{'x':1},{'x':2},{'numeric_tolerance':0});assert result['variance_count']==1
 v=r.one('select id from variances');root=RCAService(r).record_root_cause(j['id'],[v['id']],'Mapping difference',.8,[],[], '$.x');assert root['status']=='PROPOSED'
 with pytest.raises(ValueError):RCAService(r).record_remediation(j['id'],root['id'],'fix','','[]',1,'')
 d.cleanup()


def test_architecture_agents_are_seeded_idempotently():
 d=tempfile.TemporaryDirectory(); path=str(Path(d.name)/'agents.db')
 r=Repository(path)
 agents=r.rows('select name,version,required_role,enabled from agent_registry order by name')
 assert len(agents)==CATALOGUE_AGENT_COUNT
 # Derived from the catalogue: the invariant is that the seeded registry mirrors
 # the governed file exactly, not that it holds one particular list of names.
 assert {a['name'] for a in agents}==_catalogue_display_names()
 assert all(a['version']=='1.0.0' and a['required_role']!='AGENT_OPERATOR' and a['enabled']==1 for a in agents)
 Repository(path)
 assert r.one('select count(*) n from agent_registry')['n']==CATALOGUE_AGENT_COUNT
 d.cleanup()


def test_v22_agent_contracts_roles_tools_graph_and_health():
 from app.services.agents import AgentRegistry
 d,r,f,j=setup(); registry=AgentRegistry(r)
 rows=r.rows('select * from agent_registry')
 assert len(rows)==CATALOGUE_AGENT_COUNT and len({x['agent_id'] for x in rows})==CATALOGUE_AGENT_COUNT
 assert all(x['contract_hash'] and x['status']=='APPROVED' and x['required_role']!='AGENT_OPERATOR' for x in rows)
 run=registry.start(j['id'],'code-generation','1.0.0',[],('CODE_GENERATOR',),['repository.write'])
 assert run['status']=='RUNNING'
 with pytest.raises(PermissionError): registry.start(j['id'],'code-generation','1.0.0',[],('CODE_GENERATOR',),['policy.evaluate'])
 assert registry.health()['status']=='UP'
 graph=registry.graph(); assert len(graph['nodes'])==CATALOGUE_AGENT_COUNT and any(e['to']=='code-generation' for e in graph['edges'])
 d.cleanup()

def test_v22_contract_drift_is_rejected():
 d=tempfile.TemporaryDirectory(); path=str(Path(d.name)/'drift.db'); r=Repository(path)
 with r.connect() as c:c.execute("update agent_registry set contract_hash='tampered' where agent_id='planning'")
 with pytest.raises(RuntimeError,match='agent_contract_drift'): Repository(path)
 d.cleanup()

def test_v3_high_priority_reference_services(tmp_path):
 from app.services.factory_v3 import WorkflowOrchestrator,IntakeService,DormancyService,FrameworkService,ACEAnalysisService,PlanningGenerationService,AuditLedger,SecurityService
 d,r,f,j=setup(); ace=tmp_path/'ace';ace.mkdir();(ace/'Order.msgflow').write_text('<node xmi:type="ComputeNode"/>');(ace/'Order.esql').write_text('CREATE COMPUTE MODULE OrderMain;');(ace/'order.xsd').write_text('<schema/>')
 fw=tmp_path/'fw';fw.mkdir();(fw/'pom.xml').write_text('<project><artifactId>spring-boot-starter-web</artifactId></project>')
 o=WorkflowOrchestrator(r);q=o.schedule(j['id'],'INTAKE',{'path':str(ace)});assert o.schedule(j['id'],'INTAKE',{})['id']==q['id'];claimed=o.claim();assert claimed['attempt']==1;assert o.complete(claimed['id'],False,'transient')['status']=='RETRY';cp=o.checkpoint(j['id'],'CREATED');assert o.pause(j['id'])['status']=='PAUSED';assert o.resume(j['id'])['status']=='ACTIVE'
 im=IntakeService(r).scan(j['id'],ace);assert 'Order.msgflow' in json.loads(im['inventory'])['flows']
 da=DormancyService(r).assess(j['id'],0,120,True,False);assert da['classification']=='DORMANT'
 fb=FrameworkService(r).analyze(j['id'],fw);assert fb['status']=='PENDING_APPROVAL'
 an=ACEAnalysisService(r).analyze(j['id'],ace);assert len(json.loads(an['semantic_model'])['flows'])==1
 pg=PlanningGenerationService(r)
 with pytest.raises((ValueError,PermissionError,TypeError)):pg.plan(j['id'],an['id'],fb['id'],None)
 ledger=AuditLedger(r);ledger.append(j['id'],'tester','CREATED',{});ledger.append(j['id'],'tester','INTAKE',{});assert ledger.verify()
 sec=SecurityService(r);assert sec.scan(j['id'],'x.py',"password='bad'")[0]['severity']=='HIGH';assert sec.secret_reference('db','vault://db/password')['reference'].startswith('vault://');assert sec.metric(j['id'],'workflow.duration',1.2)['value']==1.2
 d.cleanup()

def test_v31_multi_system_connector_configuration(tmp_path):
 from app.services.connectors import ConnectorRegistry,CATEGORIES
 d,r,f,j=setup(); cr=ConnectorRegistry(r)
 items=cr.load(Path(__file__).parents[1]/'config/integrations.example.json')
 assert {x['category'] for x in items}==CATEGORIES
 assert len(cr.resolve('source-control'))==2
 primary=cr.resolve('source-control')[0]; cr.mark_health(primary['id'],False)
 assert cr.resolve('source-control')[0]['name']=='git-secondary'
 with pytest.raises(ValueError):cr.upsert({'category':'cicd','name':'bad-system','provider':'generic-http','config':{'base_url':'http://unsafe','token':'x'}})
 assert cr.record(cr.resolve('cicd')[0]['id'],'trigger',{'branch':'main'},'ACCEPTED')['request_hash']
 d.cleanup()

def test_v32_environment_scoped_profiles_and_configuration_validation():
 from app.services.connectors import ConnectorRegistry
 d,r,f,j=setup();cr=ConnectorRegistry(r)
 x=cr.upsert({'category':'cicd','name':'pipeline-dev','provider':'generic-http','environment':'DEV','project_scope':'Retail','secret_reference':'vault://cicd/dev','config':{'base_url':'https://ci.example.invalid'}})
 assert x['environment']=='DEV' and x['project_scope']=='Retail' and x['config_version']==1
 y=cr.upsert({'category':'cicd','name':'pipeline-dev','provider':'generic-http','environment':'TEST','project_scope':'Retail','secret_reference':'vault://cicd/test','config':{'base_url':'https://ci-test.example.invalid'}})
 assert y['config_version']==2 and y['environment']=='TEST'
 with pytest.raises(ValueError):cr.upsert({'category':'cicd','name':'pipeline-prod','provider':'generic-http','environment':'INVALID','secret_reference':'vault://x','config':{'base_url':'https://x.invalid'}})
 d.cleanup()


def test_governed_transition_requires_bound_approval():
 d,r,f,j=setup();f.transition(j['id'],'INTAKE_VALIDATED','tester');f.transition(j['id'],'DORMANCY_ASSESSED','tester')
 with pytest.raises(PermissionError,match='G0'):f.transition(j['id'],'FRAMEWORK_BASELINED','tester')
 e=EvidenceService(r).register(j['id'],'DORMANCY','1','mem://d','evidence','agent')
 f.approve({'job_id':j['id'],'gate':'G0','decision':'PROCEED','approver_role':'SERVICE_OWNER','approver_identity':'owner','evidence_ids':[e['id']],'artifact_bindings':{e['id']:e['content_hash']}},('SERVICE_OWNER',))
 assert f.transition(j['id'],'FRAMEWORK_BASELINED','tester')['state']=='FRAMEWORK_BASELINED'
 d.cleanup()

def test_workflow_leases_cancel_replay_and_audit_tamper():
 from app.services.factory_v3 import WorkflowOrchestrator,AuditLedger
 d,r,f,j=setup();o=WorkflowOrchestrator(r);q=o.schedule(j['id'],'X',{'a':1},1,'k');c=o.claim('w',10);assert c['lease_owner']=='w';assert o.heartbeat(c['id'],'w')['status']=='RUNNING';assert o.complete(c['id'],False,'bad','w')['status']=='DEAD_LETTER';assert o.replay_dead_letter(c['id'])['status']=='READY'
 q2=o.schedule(j['id'],'Y',{},1,'k2');assert o.cancel(q2['id'])['status']=='CANCELLED'
 a=AuditLedger(r);x=a.append(j['id'],'tester','A',{'x':1});assert a.verify()
 with r.connect() as db:db.execute("update audit_ledger set payload_json='{}' where id=?",(x['id'],))
 assert not a.verify();d.cleanup()
