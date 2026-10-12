import json,re
from urllib.parse import urlparse
from app.repository import uid,now,digest
CATEGORIES={'source-control','cicd','artifact-repository','deployment','ace-runtime','telemetry','service-registry','identity','secrets-vault','object-storage','model-gateway','security-scanner','observability','workflow-queue','notification'}
SENSITIVE=('password','secret','token','api_key','client_secret','private_key')
class ConnectorRegistry:
 def __init__(self,repo):self.repo=repo
 def validate(self,p):
  if p.get('category') not in CATEGORIES:raise ValueError('unsupported_connector_category')
  if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,62}',p.get('name','')):raise ValueError('invalid_connector_name')
  cfg=p.get('config') or {}
  if any(k.lower() in SENSITIVE for k in cfg):raise ValueError('credentials_must_use_secret_reference')
  for key in ('base_url','endpoint','issuer'):
   if key in cfg and isinstance(cfg[key],str) and '://' in cfg[key]:
    u=urlparse(cfg[key]);
    if u.scheme not in ('https','queue','vault','bootstrap'):raise ValueError('insecure_or_unsupported_endpoint')
  if p.get('provider')!='reference-only' and not p.get('secret_reference'):raise ValueError('secret_reference_required')
  if p.get('environment','DEV') not in ('DEV','TEST','STAGE','PROD'):raise ValueError('invalid_environment')
  return True
 def upsert(self,p):
  self.validate(p);ts=now();existing=self.repo.one('select id,created_at,config_version from connector_profiles where category=? and name=?',(p['category'],p['name']));i=existing['id'] if existing else uid();created=existing['created_at'] if existing else ts
  with self.repo.connect() as c:c.execute('insert or replace into connector_profiles(id,category,name,provider,config,secret_reference,priority,enabled,status,last_checked_at,created_at,updated_at,environment,project_scope,config_version,updated_by) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(i,p['category'],p['name'],p['provider'],json.dumps(p.get('config',{})),p.get('secret_reference',''),int(p.get('priority',100)),1 if p.get('enabled',True) else 0,p.get('status','UNVERIFIED'),p.get('last_checked_at',''),created,ts,p.get('environment','DEV'),p.get('project_scope','GLOBAL'),int(existing['config_version'] if existing else 0)+1,p.get('updated_by','api-user')))
  return self.repo.one('select * from connector_profiles where id=?',(i,))
 def load(self,path):
  data=json.load(open(path,encoding='utf-8'));items=[self.upsert(x) for x in data.get('profiles',[])]
  default=data.get('routing',{}).get('default',{})
  for cat in CATEGORIES:self.set_routing(cat,default.get('strategy','priority'),default.get('failover_enabled',True),default.get('health_required',True))
  return items
 def set_routing(self,category,strategy='priority',failover=True,health_required=True):
  if category not in CATEGORIES:raise ValueError('unsupported_connector_category')
  if strategy not in ('priority','round-robin'):raise ValueError('unsupported_routing_strategy')
  with self.repo.connect() as c:c.execute('insert or replace into routing_policies values(?,?,?,?,?)',(category,strategy,1 if failover else 0,1 if health_required else 0,now()))
  return self.repo.one('select * from routing_policies where category=?',(category,))
 def candidates(self,category):
  policy=self.repo.one('select * from routing_policies where category=?',(category,)) or {'health_required':1,'failover_enabled':1}
  rows=self.repo.rows('select * from connector_profiles where category=? and enabled=1 order by priority,name',(category,))
  if policy['health_required']:rows=[x for x in rows if x['status'] in ('HEALTHY','UNVERIFIED')]
  return rows[:1] if not policy['failover_enabled'] else rows
 def mark_health(self,profile_id,healthy,details=None):
  status='HEALTHY' if healthy else 'UNHEALTHY'
  with self.repo.connect() as c:c.execute('update connector_profiles set status=?,last_checked_at=?,updated_at=? where id=?',(status,now(),now(),profile_id))
  return self.repo.one('select * from connector_profiles where id=?',(profile_id,))
 def record(self,profile_id,operation,request,status,response_metadata=None,error=''):
  i=uid();corr=uid();ts=now()
  with self.repo.connect() as c:c.execute('insert into connector_operations values(?,?,?,?,?,?,?,?,?,?)',(i,profile_id,operation,digest(json.dumps(request,sort_keys=True)),status,json.dumps(response_metadata or {}),error,corr,ts,now()))
  return self.repo.one('select * from connector_operations where id=?',(i,))
 def resolve(self,category):
  rows=self.candidates(category)
  if not rows:raise ValueError('no_eligible_connector:'+category)
  return rows
