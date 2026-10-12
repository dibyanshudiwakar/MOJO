import json
from app.repository import uid,now,digest
class EvidenceService:
 def __init__(self,repo): self.repo=repo
 def register(self,job_id,kind,version,uri,content,producer,metadata=None):
  eid=uid(); h=digest(content)
  with self.repo.connect() as c:
   if not c.execute('select 1 from jobs where id=?',(job_id,)).fetchone():raise ValueError('job_not_found')
   self.repo.insert(c,'evidence',{'id':eid,'job_id':job_id,'kind':kind,'version':version,'uri':uri,'content_hash':h,'producer':producer,'metadata':json.dumps(metadata or {}),'immutable':1,'created_at':now()})
   self.repo.event(c,job_id,producer,'EVIDENCE_REGISTERED',details={'evidence_id':eid,'kind':kind,'version':version,'uri':uri,'content_hash':h})
  return {'id':eid,'content_hash':h,'kind':kind}
 def snapshot(self,job_id,**values):
  vid=uid()
  with self.repo.connect() as c:
   job=c.execute('select * from jobs where id=?',(job_id,)).fetchone()
   if not job: raise ValueError('job_not_found')
   self.repo.insert(c,'versions',{'id':vid,'job_id':job_id,'iteration':job['iteration'],'source_commit':values.get('source_commit',job['source_revision']),'target_commit':values.get('target_commit',''),'migration_spec_version':values.get('migration_spec_version',''),'framework_version':values.get('framework_version',''),'agent_versions':json.dumps(values.get('agent_versions',{})),'policy_version':values.get('policy_version','v1'),'rollback_pointer':values.get('rollback_pointer',job['rollback_pointer'] or ''),'status':'DRAFT','created_at':now()})
   self.repo.event(c,job_id,values.get('producer','system'),'VERSION_SNAPSHOT_TAKEN',details={'version_id':vid,'iteration':job['iteration']})
  return {'id':vid,'iteration':job['iteration']}
