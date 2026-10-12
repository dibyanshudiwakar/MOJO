import json
from app.repository import uid,now
class RCAService:
 def __init__(self,repo): self.repo=repo
 def record_root_cause(self,job_id,variance_ids,hypothesis,confidence,supporting,missing,scope):
  if not 0<=float(confidence)<=1: raise ValueError('confidence_out_of_range')
  rid=uid()
  with self.repo.connect() as c:c.execute('insert into root_causes values(?,?,?,?,?,?,?,?,?,?)',(rid,job_id,json.dumps(variance_ids),hypothesis,float(confidence),json.dumps(supporting),json.dumps(missing),scope,'PROPOSED',now()))
  return {'id':rid,'status':'PROPOSED'}
 def record_remediation(self,job_id,root_cause_id,proposal,approved_pattern,tests,iteration,rollback_pointer):
  if not approved_pattern or not tests or not rollback_pointer: raise ValueError('remediation_requires_pattern_tests_and_rollback')
  rid=uid()
  with self.repo.connect() as c:c.execute('insert into remediations values(?,?,?,?,?,?,?,?,?,?)',(rid,job_id,root_cause_id,proposal,approved_pattern,json.dumps(tests),'PROPOSED',iteration,rollback_pointer,now()))
  return {'id':rid,'status':'PROPOSED'}
