import json, os, socket, struct, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import pytest
from app.repository import Repository, uid, now, digest
from app.service import FactoryService
from app.services.evidence import EvidenceService
from app.services.policy import PolicyError
from app.services.factory_v3 import AuditLedger
from app.services.migration_lifecycle_v6 import MappingAndPlanningService
from app.services.platform_v7 import ApprovalWorkspaceService
from app.domain.preconditions import PreconditionError


def setup():
 d=tempfile.TemporaryDirectory();r=Repository(str(Path(d.name)/'gov.db'));f=FactoryService(r)
 j=f.create_job({'service':'Orders','owner':'Retail','sourceRevision':'abc','rollbackPointer':'tag/v1'},'tester')
 return d,r,f,j,EvidenceService(r)


def approve(f,ev,job,gate,decision,role,identity):
 """Record a fully bound approval: real evidence, hash-bound to the decision."""
 e=ev.register(job['id'],gate,'1','mem://%s/%s'%(gate,identity),gate+decision+identity,'agent')
 return f.approve({'job_id':job['id'],'gate':gate,'decision':decision,'approver_role':role,'approver_identity':identity,'evidence_ids':[e['id']],'artifact_bindings':{e['id']:e['content_hash']}},(role,),identity)


def state(r,job_id,value):
 with r.connect() as c:c.execute('update jobs set state=? where id=?',(value,job_id))


def seed_promotion_artifacts(r,job_id,variance=True):
 """The differential package, its result and a quality run for one case."""
 pkg=uid();payload=json.dumps({'plan_id':'p1','cases':[{'id':'C1'}]},sort_keys=True);quality=json.dumps({'checks':[]})
 with r.connect() as c:
  r.insert(c,'differential_packages',{'id':pkg,'job_id':job_id,'plan_id':'p1','status':'READY_FOR_EXECUTION','payload':payload,'content_hash':digest(payload),'created_at':now()})
  r.insert(c,'differential_evidence',{'id':uid(),'job_id':job_id,'package_id':pkg,'case_id':'C1','status':'VARIANCE' if variance else 'MATCH','payload':'{}','content_hash':digest('{}'),'created_at':now(),'disposition':'OPEN' if variance else 'CLOSED'})
  r.insert(c,'quality_runs',{'id':uid(),'job_id':job_id,'plan_id':'p1','status':'PASS_WITH_PENDING_TOOLS','payload':quality,'content_hash':digest(quality),'created_at':now(),'project_root':'.'})
 return pkg


def test_promotion_refuses_an_empty_evidence_bundle():
 """A correctly signed G5 approval is not by itself grounds for promotion."""
 d,r,f,j,ev=setup();state(r,j['id'],'READY_FOR_PROMOTION')
 approve(f,ev,j,'G5','PROMOTE','RELEASE_MANAGER','rm@example')
 with pytest.raises(PreconditionError,match='differential_package_missing'):f.transition(j['id'],'COMPLETED','orchestrator')
 # Nor does an empty package standing in for one.
 empty=json.dumps({'plan_id':'p1','cases':[]},sort_keys=True)
 with r.connect() as c:r.insert(c,'differential_packages',{'id':uid(),'job_id':j['id'],'plan_id':'p1','status':'READY_FOR_EXECUTION','payload':empty,'content_hash':digest(empty),'created_at':now()})
 with pytest.raises(PreconditionError,match='differential_package_has_no_cases'):f.transition(j['id'],'COMPLETED','orchestrator')
 assert r.one('select state from jobs where id=?',(j['id'],))['state']=='READY_FOR_PROMOTION'
 d.cleanup()


def test_promotion_refuses_a_missing_version_snapshot_and_open_high_findings():
 d,r,f,j,ev=setup();seed_promotion_artifacts(r,j['id'],variance=False);state(r,j['id'],'READY_FOR_PROMOTION')
 approve(f,ev,j,'G5','PROMOTE','RELEASE_MANAGER','rm@example')
 with pytest.raises(PreconditionError,match='version_snapshot_missing_for_iteration'):f.transition(j['id'],'COMPLETED','orchestrator')
 ev.snapshot(j['id'])
 with r.connect() as c:r.insert(c,'security_findings',{'id':uid(),'job_id':j['id'],'scanner':'BUILTIN','severity':'HIGH','rule_id':'SECRET','path':'a.java','detail':'x','status':'OPEN','created_at':now()})
 with pytest.raises(PreconditionError,match='open_high_security_findings'):f.transition(j['id'],'COMPLETED','orchestrator')
 with r.connect() as c:c.execute("update security_findings set status='RESOLVED' where job_id=?",(j['id'],))
 assert f.transition(j['id'],'COMPLETED','orchestrator')['state']=='COMPLETED'
 d.cleanup()


def test_open_variance_must_be_dispositioned_under_a_g4_exception():
 d,r,f,j,ev=setup();seed_promotion_artifacts(r,j['id'],variance=True);ev.snapshot(j['id']);state(r,j['id'],'READY_FOR_PROMOTION')
 approve(f,ev,j,'G5','PROMOTE','RELEASE_MANAGER','rm@example')
 with pytest.raises(PreconditionError,match='differential_variances_undispositioned'):f.transition(j['id'],'COMPLETED','orchestrator')
 svc=MappingAndPlanningService(r);dev=r.one('select id from differential_evidence where job_id=?',(j['id'],))
 with pytest.raises(PermissionError,match='g4_accept_exception_approval_required'):
  svc.disposition_differential(j['id'],dev['id'],'ACCEPTED_EXCEPTION','known formatting difference','qa@example')
 approve(f,ev,j,'G4','ACCEPT_EXCEPTION','QA_LEAD','qa@example')
 with pytest.raises(ValueError,match='disposition_reason_required'):
  svc.disposition_differential(j['id'],dev['id'],'ACCEPTED_EXCEPTION','','qa@example')
 out=svc.disposition_differential(j['id'],dev['id'],'ACCEPTED_EXCEPTION','known formatting difference','qa@example')
 assert out['dispositioned_by']=='qa@example' and out['disposition']=='ACCEPTED_EXCEPTION'
 assert f.transition(j['id'],'COMPLETED','orchestrator')['state']=='COMPLETED'
 d.cleanup()


def test_gate_quorum_counts_distinct_approvers():
 d,r,f,j,ev=setup();ApprovalWorkspaceService(r).configure_gate({'gate':'G0','quorum':2},'admin')
 f.transition(j['id'],'INTAKE_VALIDATED','orchestrator');f.transition(j['id'],'DORMANCY_ASSESSED','orchestrator')
 approve(f,ev,j,'G0','PROCEED','SERVICE_OWNER','owner-a@example')
 with pytest.raises(PolicyError,match='quorum_1_of_2'):f.transition(j['id'],'FRAMEWORK_BASELINED','orchestrator')
 approve(f,ev,j,'G0','PROCEED','SERVICE_OWNER','owner-a@example')
 with pytest.raises(PolicyError,match='quorum_1_of_2'):f.transition(j['id'],'FRAMEWORK_BASELINED','orchestrator')
 approve(f,ev,j,'G0','PROCEED','SERVICE_OWNER','owner-b@example')
 assert f.transition(j['id'],'FRAMEWORK_BASELINED','orchestrator')['state']=='FRAMEWORK_BASELINED'
 d.cleanup()


def test_skipping_the_dormancy_assessment_does_not_bypass_g0():
 d,r,f,j,ev=setup();f.transition(j['id'],'INTAKE_VALIDATED','orchestrator')
 with pytest.raises(PolicyError,match='G0'):f.transition(j['id'],'FRAMEWORK_BASELINED','orchestrator')
 approve(f,ev,j,'G0','PROCEED','SERVICE_OWNER','owner@example')
 assert f.transition(j['id'],'FRAMEWORK_BASELINED','orchestrator')['state']=='FRAMEWORK_BASELINED'
 d.cleanup()


def test_rework_advances_the_iteration_and_retires_prior_approvals():
 d,r,f,j,ev=setup();seed_promotion_artifacts(r,j['id'],variance=True);state(r,j['id'],'VARIANCE_REVIEW')
 # Both G4 decisions are signed against iteration 1. The exception must not
 # survive the rework it was granted alongside.
 approve(f,ev,j,'G4','ACCEPT_EXCEPTION','QA_LEAD','qa@example')
 approve(f,ev,j,'G4','CONTINUE_FEEDBACK','QA_LEAD','qa@example')
 assert f.transition(j['id'],'REMEDIATING','orchestrator')['iteration']==1
 assert f.transition(j['id'],'IMPLEMENTING','orchestrator')['iteration']==2
 assert r.one("select id from workflow_events where job_id=? and event_type='ITERATION_ADVANCED'",(j['id'],))
 for target in ('BUILD_VALIDATED','DIFFERENTIAL_TESTING','VARIANCE_REVIEW'):f.transition(j['id'],target,'orchestrator')
 with pytest.raises(PolicyError,match='G4'):f.transition(j['id'],'READY_FOR_PROMOTION','orchestrator')
 approve(f,ev,j,'G4','ACCEPT_EXCEPTION','QA_LEAD','qa@example')
 assert f.transition(j['id'],'READY_FOR_PROMOTION','orchestrator')['state']=='READY_FOR_PROMOTION'
 d.cleanup()


def test_rollback_records_pointer_event_delivery_operation_and_ledger():
 d,r,f,j,ev=setup();state(r,j['id'],'IMPLEMENTING')
 with pytest.raises(ValueError,match='rollback_reason_required'):f.rollback(j['id'],'rm@example','')
 out=f.rollback(j['id'],'rm@example','build regression in the generated target')
 assert out['state']=='ROLLED_BACK' and out['rollback_pointer']=='tag/v1'
 assert r.one("select id from workflow_events where job_id=? and event_type='JOB_ROLLED_BACK'",(j['id'],))
 assert r.one("select target from delivery_operations where job_id=? and operation='rollback'",(j['id'],))['target']=='tag/v1'
 assert r.one("select id from audit_ledger where job_id=? and action='JOB_ROLLED_BACK'",(j['id'],)) and AuditLedger(r).verify()
 with pytest.raises(ValueError,match='illegal_transition'):f.rollback(j['id'],'rm@example','again')
 d.cleanup()


def test_rollback_requires_a_recorded_pointer():
 d=tempfile.TemporaryDirectory();r=Repository(str(Path(d.name)/'np.db'));f=FactoryService(r)
 j=f.create_job({'service':'Orders','owner':'Retail','sourceRevision':'abc'},'tester');state(r,j['id'],'IMPLEMENTING')
 with pytest.raises(ValueError,match='rollback_pointer_required'):f.rollback(j['id'],'rm@example','withdrawn')
 assert f.rollback(j['id'],'rm@example','withdrawn','tag/v0')['rollback_pointer']=='tag/v0'
 d.cleanup()


def test_governed_acts_emit_domain_events_and_ledger_entries():
 d,r,f,j,ev=setup()
 e=ev.register(j['id'],'ANALYSIS','1','mem://a','content','agent')
 f.approve({'job_id':j['id'],'gate':'G2','decision':'APPROVE','approver_role':'ACE_SME','approver_identity':'sme@example','evidence_ids':[e['id']],'artifact_bindings':{e['id']:e['content_hash']}},('ACE_SME',),'sme@example')
 ev.snapshot(j['id'])
 events={x['event_type'] for x in r.rows('select event_type from workflow_events where job_id=?',(j['id'],))}
 assert {'JOB_CREATED','EVIDENCE_REGISTERED','APPROVAL_RECORDED','VERSION_SNAPSHOT_TAKEN'}<=events
 actions={x['action'] for x in r.rows('select action from audit_ledger where job_id=?',(j['id'],))}
 assert {'JOB_CREATED','APPROVAL_RECORDED'}<=actions and AuditLedger(r).verify()
 with pytest.raises(ValueError,match='job_not_found'):ev.register('missing','ANALYSIS','1','mem://x','c','agent')
 d.cleanup()


def test_confidence_rules_are_bounded_and_reject_self_certification():
 from app.services.enterprise_v5 import ConfidenceRemediationService
 svc=ConfidenceRemediationService()
 assert svc.score(1,1,1,1,1,1)['score']==1.0
 with pytest.raises(ValueError,match='confidence_component_out_of_range'):svc.score(1.5,1,1,1,1,1)
 assert svc.eligibility(.5,.8,True,True,True)['route']=='CLARIFICATION_OR_ACE_INSPECTION'
 assert svc.eligibility(.9,.8,True,True,True,prohibited_dependency=True)['eligible'] is False
 assert svc.propose({'path':'$.x'},svc.eligibility(.9,.8,True,True,True),2,'tag/v1')['iteration']==2


def test_server_imports_and_exposes_the_governance_routes(tmp_path):
 """The confidence rules and the rollback path are now reachable over HTTP."""
 script=(
  'import os,sys\n'
  'sys.path.insert(0,%r)\n'
  'os.environ["FACTORY_DB"]=%r\n'
  'import app.server as s\n'
  'assert s.confidence is not None\n'
  'src=open(s.__file__,encoding="utf-8").read()\n'
  'routes=["/api/v5/confidence/score","/api/v5/remediation/eligibility","/api/v5/remediation/propose","/rollback","/api/v6/differential/disposition"]\n'
  'print(all(x in src for x in routes))\n'
 )%(str(Path(__file__).parents[1]),str(tmp_path/'srv.db'))
 out=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
 assert out.returncode==0,out.stderr
 assert out.stdout.strip()=='True'


def test_a_disconnected_client_is_reported_once_and_not_as_an_internal_error(tmp_path):
 """An aborted request must not raise out of the handler.

 The response went straight to the socket, so a client that closed early raised
 ConnectionAbortedError; the 500 path then failed writing to the same dead
 socket, producing two tracebacks per aborted request.
 """
 script=(
  'import email.message,io,os,sys\n'
  'sys.path.insert(0,%r)\n'
  'os.environ["FACTORY_DB"]=%r\n'
  'import app.server as s\n'
  'class Dead(io.RawIOBase):\n'
  '    def write(self,b): raise ConnectionAbortedError(10053,"aborted by host")\n'
  'h=s.H.__new__(s.H)\n'
  'h.wfile=Dead(); h.rfile=io.BytesIO(); h.path="/api/v2/dashboard"\n'
  'h.headers=email.message.Message()\n'
  'h.requestline="GET /api/v2/dashboard HTTP/1.1"; h.request_version="HTTP/1.1"\n'
  'h._headers_buffer=[]; h.close_connection=False\n'
  'h.sendj(200,{"jobs":0})\n'
  'assert h.close_connection is True, "connection was not marked closed"\n'
  'try:\n'
  '    h._headers_buffer=[]; h.close_connection=False\n'
  '    h.do_GET()\n'
  'except Exception as e:\n'
  '    print("RAISED:"+type(e).__name__); raise SystemExit(1)\n'
  'print("OK")\n'
 )%(str(Path(__file__).parents[1]),str(tmp_path/'disconnect.db'))
 out=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
 assert out.returncode==0,out.stdout+out.stderr
 assert out.stdout.strip().endswith('OK')
 # One line per aborted request, and no traceback or internal_error report.
 assert out.stderr.count('client_disconnected')==2,out.stderr
 assert 'Traceback' not in out.stderr,out.stderr
 assert 'correlation_id' not in out.stderr,out.stderr


def test_a_live_server_absorbs_reset_connections(tmp_path):
 """Reset sockets against a real server, covering the read path too.

 A client can abort before the request line is read as well as mid-response,
 and that read happens above do_GET, inside handle_one_request.
 """
 root=Path(__file__).parents[1]
 with socket.socket() as probe:
  probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
 env=dict(os.environ,FACTORY_DB=str(tmp_path/'reset.db'),PORT=str(port),HOST='127.0.0.1',FACTORY_JWT_HS256_SECRET='test-secret')
 proc=subprocess.Popen([sys.executable,'-m','app.server'],cwd=str(root),env=env,stderr=subprocess.PIPE,text=True)
 base='http://127.0.0.1:%d'%port
 try:
  for _ in range(100):
   try:
    urllib.request.urlopen(base+'/api/v2/health',timeout=2).read();break
   except Exception:time.sleep(.3)
  else:pytest.fail('server_did_not_start')
  for _ in range(10):
   s=socket.create_connection(('127.0.0.1',port))
   # SO_LINGER 0 makes close() send RST, so the next server read or write fails.
   s.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
   s.sendall(b'GET /api/v2/dashboard HTTP/1.1\r\nHost: localhost\r\n\r\n')
   s.close()
  time.sleep(1)
  assert b'UP' in urllib.request.urlopen(base+'/api/v2/health',timeout=5).read()
 finally:
  proc.terminate()
  try:_,err=proc.communicate(timeout=10)
  except subprocess.TimeoutExpired:
   proc.kill();_,err=proc.communicate()
 assert 'Traceback' not in err,err
 assert 'Exception occurred during processing' not in err,err
 assert err.count('client_disconnected')>=1,err
