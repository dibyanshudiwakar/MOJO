import json, yaml
from jsonschema import Draft202012Validator
from app.repository import uid,now,digest
class SpecificationService:
 def __init__(self,repo,schema_path): self.repo=repo; self.schema=json.load(open(schema_path,encoding='utf-8')); self.validator=Draft202012Validator(self.schema)
 def validate(self,text):
  try:data=yaml.safe_load(text)
  except Exception as e:return None,[{'path':'$','message':str(e)}]
  errors=[{'path':'$.'+'.'.join(map(str,e.path)),'message':e.message} for e in self.validator.iter_errors(data)]
  return data,errors
 def save(self,job_id,version,text):
  data,errors=self.validate(text); sid=uid(); status='VALID' if not errors else 'INVALID'
  with self.repo.connect() as c:c.execute('insert into migration_specs values(?,?,?,?,?,?,?,?)',(sid,job_id,version,text,digest(text),status,json.dumps(errors),now()))
  return {'id':sid,'status':status,'errors':errors}
