import tempfile, subprocess
import pytest
from pathlib import Path
from app.domain.model import Actor
from app.repository import Repository
from app.service import FactoryService
from app.services.connectors import ConnectorRegistry
from app.services.platform_v7 import RegistryService,AIRegistryService,KnowledgeService,ApprovalWorkspaceService,DeliveryAutomationService,NotificationService,ObservabilityService

APPROVER=Actor('board@example',('AI_GOVERNANCE_ADMIN',))
UNPRIVILEGED=Actor('dev@example',('CODE_GENERATOR',))

def _gateway_profile(r):
 """An approved model-gateway connector profile the registry can point at."""
 return ConnectorRegistry(r).upsert({'category':'model-gateway','name':'model-primary','provider':'generic-http','secret_reference':'vault://models/primary','config':{'base_url':'https://models.example.invalid','region':'approved-region'}})

def test_v7_registry_ai_knowledge_approval_and_observability(tmp_path):
 r=Repository(str(tmp_path/'v7.db'));_gateway_profile(r)
 job=FactoryService(r).create_job({'service':'Orders','owner':'Retail','sourceRevision':'abc'},'tester')
 service=RegistryService(r).register_service({'name':'Orders','owner':'Retail','repository_uri':'git://orders'},'registrar')
 assert service['migration_status']=='REGISTERED'
 ai=AIRegistryService(r)
 p=ai.register_prompt({'name':'rca','version':'1','template':'Use evidence only','status':'APPROVED'},APPROVER)
 m=ai.register_model({'name':'approved-model','version':'1','provider_profile':'model-primary','status':'APPROVED','region':'approved-region'},APPROVER)
 assert p['approved_by']=='board@example' and m['approved_by']=='board@example'
 req=ai.gateway_request({'job_id':job['id'],'prompt_id':p['id'],'model_id':m['id'],'input_evidence':['ev1']})
 assert req['status']=='PROVIDER_EXECUTION_REQUIRED' and req['dlp']=='PASSED'
 k=KnowledgeService(r);k.index({'job_id':job['id'],'kind':'analysis','reference':'analysis://1','content':'Order MQ mapping evidence','lineage':'v1'})
 assert k.search('MQ evidence',job['id'])[0]['reference']=='analysis://1'
 assert ApprovalWorkspaceService(r).configure_gate({'gate':'G3','quorum':2})['status']=='CONFIGURED'
 assert NotificationService(r).queue({'job_id':job['id'],'event_type':'GateRequested','recipient':'architect'})['status']=='QUEUED'
 assert ObservabilityService(r).emit({'job_id':job['id'],'signal_type':'metric','name':'workflow.duration','value':2.5})['status']=='RECORDED'

def test_v7_ai_registry_approval_is_governed(tmp_path):
 """APPROVED status may not be self-asserted by the caller (section 13)."""
 r=Repository(str(tmp_path/'gov.db'));_gateway_profile(r)
 ai=AIRegistryService(r)
 with pytest.raises(PermissionError,match='ai_governance_approver_role_required'):
  ai.register_prompt({'name':'rca','version':'1','template':'x','status':'APPROVED'},UNPRIVILEGED)
 draft=ai.register_prompt({'name':'rca','version':'1','template':'x'},UNPRIVILEGED)
 assert draft['status']=='DRAFT' and draft['approved_by']==''
 model=ai.register_model({'name':'m','version':'1','provider_profile':'model-primary'},UNPRIVILEGED)
 # An unapproved prompt or model cannot be used by the gateway.
 with pytest.raises(PermissionError,match='approved_prompt_and_model_required'):
  ai.gateway_request({'job_id':'j','prompt_id':draft['id'],'model_id':model['id'],'input_evidence':['x']})

def test_v7_model_gateway_blocks_secret_bearing_payloads(tmp_path):
 """Section 14 DLP is applied by the gateway, not declared by the caller."""
 r=Repository(str(tmp_path/'dlp.db'));_gateway_profile(r)
 ai=AIRegistryService(r)
 p=ai.register_prompt({'name':'rca','version':'1','template':'t','status':'APPROVED'},APPROVER)
 m=ai.register_model({'name':'m','version':'1','provider_profile':'model-primary','status':'APPROVED','region':'approved-region'},APPROVER)
 for payload in (['password = hunter2'],['Authorization: Bearer abcdefghijklmnopqrstuvwxyz'],
                 ['-----BEGIN RSA PRIVATE KEY-----'],['postgres://user:pw@host/db']):
  with pytest.raises(PermissionError,match='dlp_violation'):
   ai.gateway_request({'job_id':'j','prompt_id':p['id'],'model_id':m['id'],'input_evidence':payload})

def test_v7_unapproved_model_gateway_profile_is_rejected(tmp_path):
 r=Repository(str(tmp_path/'prof.db'))
 with pytest.raises(ValueError,match='approved_model_gateway_profile_required'):
  AIRegistryService(r).register_model({'name':'m','version':'1','provider_profile':'unknown-profile'},APPROVER)

def test_v7_delivery_contracts(tmp_path):
 r=Repository(str(tmp_path/'v7.db')); repo=tmp_path/'git';repo.mkdir();subprocess.run(['git','init'],cwd=repo,check=True,capture_output=True)
 x=DeliveryAutomationService(r).git_operation({'job_id':'j1','repository_path':str(repo),'operation':'status','rollback_pointer':'tag/v1'})
 assert x['status']=='COMPLETED'


def test_v7_live_gate_configuration(tmp_path):
 r=Repository(str(tmp_path/'live.db'));svc=ApprovalWorkspaceService(r)
 assert len(svc.gates())==6
 out=svc.configure_gate({'gate':'G3','enabled':True,'required_role':'SOLUTION_ARCHITECT','allowed_decisions':['APPROVE','REJECT','CLARIFY'],'min_evidence':2,'quorum':2,'timeout_minutes':120,'escalation_profile':'architecture-board','bypass_role':'CHIEF_ARCHITECT','downstream_effect':'ENABLE_GENERATION'},'admin')
 assert out['status']=='CONFIGURED'
 g=next(x for x in svc.gates() if x['gate']=='G3')
 assert g['quorum']==2 and g['timeout_minutes']==120 and g['required_role']=='SOLUTION_ARCHITECT'
 assert g['allowed_decisions']==['APPROVE','REJECT','CLARIFY']
