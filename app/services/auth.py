import os, json, hmac, hashlib, base64, time, secrets as _secrets
from pathlib import Path
from dataclasses import dataclass

def _b64(v):
 padding='='*(-len(v)%4);return base64.urlsafe_b64decode(v+padding)
def _seg(obj):
 return base64.urlsafe_b64encode(json.dumps(obj,separators=(',',':'),sort_keys=True).encode()).rstrip(b'=')
def _resolve_secret(explicit):
 """Return the token signing key, generating one on first run if needed.

 A signing key must exist server-side for HS256 to work at all, but it must not
 be a value operators paste into a URL or a test fixture. When
 FACTORY_JWT_HS256_SECRET is unset the key is generated once and persisted
 beside the database with owner-only permissions, so tokens survive a restart
 without anyone hardcoding a shared secret.
 """
 if explicit:return explicit.encode()
 env=os.getenv('FACTORY_JWT_HS256_SECRET','')
 if env:return env.encode()
 db=os.getenv('FACTORY_DB') or str(Path(__file__).resolve().parents[2]/'factory-v2.db')
 keyfile=Path(db).with_suffix('.signing-key')
 try:
  if keyfile.exists():
   existing=keyfile.read_text(encoding='utf-8').strip()
   if existing:return existing.encode()
  generated=_secrets.token_urlsafe(48)
  keyfile.parent.mkdir(parents=True,exist_ok=True);keyfile.write_text(generated,encoding='utf-8')
  try:os.chmod(keyfile,0o600)
  except OSError:pass
  return generated.encode()
 except OSError:
  # An unwritable location must not silently fall back to a predictable key.
  return _secrets.token_urlsafe(48).encode()
@dataclass(frozen=True)
class Principal:
 identity:str; roles:tuple; project_scopes:tuple; environments:tuple; user_id:str=''
class IdentityService:
 def __init__(self,secret=None,issuer=None,audience=None):
  self.secret=_resolve_secret(secret);self.issuer=issuer or os.getenv('FACTORY_JWT_ISSUER','ace-factory-idp');self.audience=audience or os.getenv('FACTORY_JWT_AUDIENCE','ace-factory')
 def issue(self,subject,roles=(),project_scopes=('GLOBAL',),environments=('DEV',),ttl_seconds=28800,extra=None):
  """Mint a signed token for an authenticated account.

  Tokens are only ever produced here, after AccountService has verified a
  password. Previously the only way to obtain one was to hand-compute an HMAC
  with the shared secret, which meant the secret had to be distributed to
  anyone who needed access.
  """
  if not self.secret:raise PermissionError('identity_provider_not_configured')
  if not subject:raise ValueError('subject_required')
  issued=int(time.time())
  claims={'iss':self.issuer,'aud':self.audience,'sub':subject,'iat':issued,'exp':issued+int(ttl_seconds),
          'roles':list(roles),'projects':list(project_scopes),'environments':list(environments)}
  claims.update(extra or {})
  signing=_seg({'alg':'HS256','typ':'JWT'})+b'.'+_seg(claims)
  sig=base64.urlsafe_b64encode(hmac.new(self.secret,signing,hashlib.sha256).digest()).rstrip(b'=')
  return {'access_token':(signing+b'.'+sig).decode(),'token_type':'Bearer','expires_in':int(ttl_seconds),'expires_at':claims['exp']}
 def authenticate(self,authorization):
  if not authorization or not authorization.startswith('Bearer '):raise PermissionError('bearer_token_required')
  if not self.secret:raise PermissionError('identity_provider_not_configured')
  token=authorization[7:];parts=token.split('.')
  if len(parts)!=3:raise PermissionError('invalid_token')
  signing='.'.join(parts[:2]).encode();expected=hmac.new(self.secret,signing,hashlib.sha256).digest()
  if not hmac.compare_digest(expected,_b64(parts[2])):raise PermissionError('invalid_token_signature')
  header=json.loads(_b64(parts[0]));claims=json.loads(_b64(parts[1]))
  if header.get('alg')!='HS256' or claims.get('iss')!=self.issuer:raise PermissionError('invalid_token_claims')
  aud=claims.get('aud',[]);aud=[aud] if isinstance(aud,str) else aud
  if self.audience not in aud or int(claims.get('exp',0))<=int(time.time()):raise PermissionError('invalid_token_claims')
  identity=claims.get('sub')
  if not identity:raise PermissionError('invalid_token_subject')
  return Principal(identity,tuple(claims.get('roles',[])),tuple(claims.get('projects',['GLOBAL'])),tuple(claims.get('environments',['DEV'])),str(claims.get('user_id','')))
 def authorize_job(self,principal,job):
  if job['project_scope'] not in principal.project_scopes and 'GLOBAL' not in principal.project_scopes:raise PermissionError('project_scope_denied')
  if job['environment'] not in principal.environments:raise PermissionError('environment_scope_denied')
  return True
