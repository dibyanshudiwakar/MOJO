import json,tempfile,yaml
from pathlib import Path
import pytest
from app.repository import Repository,uid,now,digest
from app.service import FactoryService
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

def seed():
 d=tempfile.TemporaryDirectory();r=Repository(str(Path(d.name)/'v6.db'));j=FactoryService(r).create_job({'service':'Orders','owner':'Retail','sourceRevision':'abc','rollbackPointer':'tag/v1'},'tester')
 model={'flows':[{'path':'Orders.msgflow','nodes':[{'id':'in','type':'HTTPInput','properties':{}},{'id':'map','type':'ComputeNode','properties':{}}],'connections':[{'from':'in','to':'map'}]}],'subflows':[],'esql':[{'path':'Orders.esql'}],'maps':[],'schemas':[{'path':'Order.xsd'}],'policies':[],'interfaces':[],'dependencies':[],'behaviour':{},'ambiguities':[]}
 ah=digest(json.dumps(model,sort_keys=True));fh=digest('framework');spec={'metadata':{},'source':{},'interfaces':{},'behaviour':{},'target':{},'mappings':[],'tests':[],'clarifications':[],'governance':{}};sy=yaml.safe_dump(spec);sh=digest(sy);aid=uid();fid=uid();sid=uid()
 with r.connect() as c:
  c.execute('insert into ace_analyses values(?,?,?,?,?,?,?,?,?)',(aid,j['id'],'1',json.dumps(model),'analysis','[]','[]',ah,now()))
  c.execute('insert into framework_baselines values(?,?,?,?,?,?,?,?,?)',(fid,j['id'],'1','template',json.dumps({'build':{'springBootVersion':'3.3.0','javaVersion':'21'}}),'[]','APPROVED',fh,now()))
  c.execute('insert into migration_specs values(?,?,?,?,?,?,?,?)',(sid,j['id'],'1',sy,sh,'VALID','[]',now()))
  for gate,bindings in [('G1',{'framework':fh}),('G2',{'analysis':ah,'specification':sh})]:
   c.execute('insert into approvals(id,job_id,gate,decision,approver_role,approver_identity,comments,conditions,evidence_ids,policy_decision,rollback_pointer,created_at,artifact_bindings) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid(),j['id'],gate,'APPROVE','AUTHORISED','reviewer','approved','','[]','{}','tag/v1',now(),json.dumps(bindings)))
 return d,r,j,aid,fid,sid

def test_mapping_plan_g3_generation_quality_and_diff(tmp_path):
 d,r,j,aid,fid,sid=seed();svc=MappingAndPlanningService(r);cat=svc.create_mapping_catalogue(j['id'],fid,aid,sid);assert cat['unsupported_count']==0 and cat['mapping_count']>=4
 plan=svc.generate_plan(j['id'],cat['catalogue_id']);assert plan['task_count']==cat['mapping_count'];pkg=svc.g3_package(j['id'],plan['plan_id'],cat['catalogue_id']);assert pkg['status']=='PENDING_APPROVAL'
 with pytest.raises(PermissionError):svc.generate_spring(j['id'],plan['plan_id'],tmp_path,'orders','com.example.orders')
 with r.connect() as c:c.execute('insert into approvals(id,job_id,gate,decision,approver_role,approver_identity,comments,conditions,evidence_ids,policy_decision,rollback_pointer,created_at,artifact_bindings) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid(),j['id'],'G3','APPROVE','SOLUTION_ARCHITECT','architect','approved','','[]','{}','tag/v1',now(),json.dumps({'plan':plan['content_hash']})))
 generated=svc.generate_spring(j['id'],plan['plan_id'],tmp_path,'orders','com.example.orders');assert Path(generated['manifest']).exists()
 quality=svc.quality_validate(j['id'],plan['plan_id'],generated['project_root'],False);assert quality['status']=='PASS_WITH_PENDING_TOOLS'
 dp=svc.prepare_differential(j['id'],plan['plan_id'],[{'id':'C1','input':{'id':1},'headers':{'x':'y'}}],{'masks':['$.headers.date']});assert dp['case_count']==1
 ev=svc.record_differential(j['id'],dp['package_id'],'C1',{'status':200,'body':{'id':1}},{'status':200,'body':{'id':2}},{});assert ev['status']=='VARIANCE';d.cleanup()

def test_unapproved_inputs_are_blocked():
 d,r,j,aid,fid,sid=seed()
 with r.connect() as c:c.execute("delete from approvals where gate='G1'")
 with pytest.raises(PermissionError,match='g1'):MappingAndPlanningService(r).create_mapping_catalogue(j['id'],fid,aid,sid)
 d.cleanup()
