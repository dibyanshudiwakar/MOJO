import base64, hashlib, json, os, re, ssl, subprocess, time, urllib.request, urllib.error, uuid, xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from app.repository import uid,now,digest

class ACEParser:
 EXT={'.msgflow':'flows','.subflow':'subflows','.esql':'esql','.map':'maps','.xslt':'maps','.xsd':'schemas','.wsdl':'schemas','.policyxml':'policies'}
 PROTOCOLS={'HTTPInput':'HTTP','HTTPReply':'HTTP','SOAPInput':'SOAP','MQInput':'MQ','MQOutput':'MQ','KafkaConsumer':'KAFKA','KafkaProducer':'KAFKA','FileInput':'FILE','TimeoutNotification':'SCHEDULED','Database':'DATABASE'}
 def parse(self,root):
  root=Path(root);model={'flows':[],'subflows':[],'esql':[],'maps':[],'schemas':[],'policies':[],'interfaces':[],'dependencies':[],'behaviour':{'routing':[],'transformations':[],'transactions':[],'retries':[],'timeouts':[],'errorHandling':[],'sideEffects':[]},'ambiguities':[]}
  if not root.is_dir():raise ValueError('repository_not_accessible')
  for f in sorted(root.rglob('*')):
   if not f.is_file() or '.git' in f.parts:continue
   ext=f.suffix.lower();rel=str(f.relative_to(root));text=f.read_text(errors='ignore') if f.stat().st_size<5_000_000 else ''
   try:
    if ext in ('.msgflow','.subflow'):self._flow(model,rel,text,ext)
    elif ext=='.esql':self._esql(model,rel,text)
    elif ext in ('.map','.xslt'):self._map(model,rel,text,ext)
    elif ext in ('.xsd','.wsdl'):self._schema(model,rel,text,ext)
    elif ext=='.policyxml':self._policy(model,rel,text)
   except Exception as e:model['ambiguities'].append(self._ambiguity(rel,'parse_failure:'+type(e).__name__,'ACE_SME','analysis incomplete'))
  if not model['flows'] and not model['subflows']:model['ambiguities'].append(self._ambiguity('$','no_ace_flow_detected','ACE_SME','analysis blocked'))
  model['backend_type']=self._detect_backend_type(model)
  model['content_hash']=digest(json.dumps(model,sort_keys=True,separators=(',',':')));return model
 #: Master skill's own INTAKE detection table
 #: (external-skills/iib-to-ms-migration/SKILL.md Step 1 "Backend Type
 #: Detection"), applied in the same priority order: a signal earlier in this
 #: list wins over one later, matching the skill's own
 #: "don't default MQ straight to QL_CICS" caution. The SOAP check reuses
 #: _classify_node's WS*-node domain disambiguation rather than re-deriving
 #: the same signal from raw ESQL text a second time.
 def _detect_backend_type(self,m):
  soap=next((i for i in m['interfaces'] if i['protocol']=='SOAP'),None)
  if soap:
   return {'type':'SOAP','signal':'SOAP envelope on node %s (%s)'%(soap['node'],soap['source'])}
  queues=[n['properties'].get('queueName') for flow in m['flows']+m['subflows'] for n in flow['nodes']
          if 'mqoutput' in n['type'].lower() and n['properties'].get('queueName')]
  ql=next((q for q in queues if 'QL' in q.upper()),None)
  tran_code=any(re.search(r'\bTRAN(?:SACTION)?[_ ]?CODE\b|\bTRAN\b',e.get('source','') or '',re.I) for e in m['esql'])
  if ql and tran_code:
   return {'type':'QL_CICS','signal':'MQ output queue %s with a QL transaction code in ESQL'%ql}
  rxclaim=next((q for q in queues if 'RXCLAIM' in q.upper() or 'SBO' in q.upper()),None)
  if rxclaim:
   return {'type':'RXCLAIM_SBO','signal':'MQ output queue referencing RxClaim/SBO: %s'%rxclaim}
  if queues:
   return {'type':'MQ_COBOL','signal':'MQ output queue %s, no QL/RxClaim signal'%queues[0]}
  if any(i['protocol']=='HTTP' for i in m['interfaces']):
   return {'type':'REST_EXTERNAL_DIRECT','signal':'HTTP interface(s), no QL/RxClaim/SOAP routing'}
  return {'type':'UNDETERMINED','signal':'no recognised backend-type signal found'}
 def _attrs(self,element):return {k.split('}')[-1]:v for k,v in element.attrib.items()}
 def _flow(self,m,rel,text,ext):
  root=ET.fromstring(text);nodes=[];connections=[]
  for e in root.iter():
   a=self._attrs(e);typ=a.get('type') or a.get('xmi:type') or e.tag.split('}')[-1]
   nid=a.get('id') or a.get('xmi:id') or a.get('name') or f'n{len(nodes)+1}'
   if any(k in typ.lower() for k in ('node','input','output','compute','route','filter','throw','catch','database','mq','kafka','http','soap','file','timeout')):
    node={'id':nid,'type':typ,'properties':a};nodes.append(node);self._classify_node(m,rel,node)
   src=a.get('sourceNode') or a.get('source');dst=a.get('targetNode') or a.get('target')
   if src and dst:connections.append({'from':src,'to':dst})
  # Raw text travels with the flow so a later stage (code-generation's
  # business-logic synthesis) can read the real routing/ESQL-reference wiring
  # rather than only the structural metadata _classify_node derived from it.
  item={'path':rel,'nodes':nodes,'connections':connections,'source':text};m['subflows' if ext=='.subflow' else 'flows'].append(item)
  for n in nodes:
   if 'custom' in n['type'].lower():m['ambiguities'].append(self._ambiguity(rel,'unsupported_custom_node:'+n['type'],'ACE_SME','manual mapping required'))
 def _classify_node(self,m,rel,node):
  typ=node['type'];low=typ.lower();props=node['properties']
  for key,protocol in self.PROTOCOLS.items():
   if key.lower() in low:
    endpoint=props.get('URLSpecifier') or props.get('url') or props.get('path') or props.get('queueName') or props.get('topicName') or props.get('filePath')
    m['interfaces'].append({'source':rel,'node':node['id'],'protocol':protocol,'endpoint':endpoint or 'UNKNOWN'})
  # ComIbmWSInput/ComIbmWSReply/ComIbmWSRequest are ACE's generic HTTP node
  # family -- "WS" is IIB's historical "Web Services" name, used for both a
  # SOAP envelope and plain HTTP/JSON depending on messageDomainProperty, not
  # a SOAP marker by itself (same disambiguation as _WS_NODE_ROLES in
  # migration_lifecycle_v6.py). None of PROTOCOLS' keys match this family's
  # actual name, so without this these nodes never became an interface at all.
  if any(role in low for role in ('wsinput','wsreply','wsrequest')):
   domain=str(props.get('messageDomainProperty','')).upper()
   protocol='SOAP' if domain not in ('','JSON') else 'HTTP'
   endpoint=props.get('URLSpecifier') or props.get('url') or props.get('path')
   m['interfaces'].append({'source':rel,'node':node['id'],'protocol':protocol,'endpoint':endpoint or 'UNKNOWN'})
  if any(x in low for x in ('compute','mapping','xslt')):m['behaviour']['transformations'].append({'source':rel,'node':node['id'],'type':typ})
  if any(x in low for x in ('route','filter','switch')):m['behaviour']['routing'].append({'source':rel,'node':node['id'],'type':typ})
  if any(x in low for x in ('catch','failure','throw','error')):m['behaviour']['errorHandling'].append({'source':rel,'node':node['id'],'type':typ})
  if any(x in low for x in ('mqoutput','kafkaproducer','database','fileoutput')):m['behaviour']['sideEffects'].append({'source':rel,'node':node['id'],'type':typ})
  for k in ('timeout','timeoutSec','transactionMode','retryCount'):
   if k in props:
    target='timeouts' if 'timeout' in k.lower() else ('retries' if 'retry' in k.lower() else 'transactions');m['behaviour'][target].append({'source':rel,'node':node['id'],'value':props[k]})
 def _esql(self,m,rel,text):
  clean=re.sub(r'--.*?$|/\*.*?\*/','',text,flags=re.M|re.S)
  modules=re.findall(r'(?i)CREATE\s+(?:COMPUTE\s+|FILTER\s+|DATABASE\s+)?MODULE\s+([\w.]+)',clean)
  procedures=re.findall(r'(?i)CREATE\s+(?:EXTERNAL\s+)?PROCEDURE\s+([\w.]+)',clean)
  functions=re.findall(r'(?i)CREATE\s+FUNCTION\s+([\w.]+)',clean)
  shared=re.findall(r'(?i)DECLARE\s+([\w.]+)\s+SHARED\s+([^;]+)',clean)
  calls=re.findall(r'(?i)CALL\s+([\w.]+)',clean);propagates=len(re.findall(r'(?i)\bPROPAGATE\b',clean));throws=len(re.findall(r'(?i)\bTHROW\b',clean))
  dbrefs=sorted(set(re.findall(r'(?i)\b(?:Database|PASSTHRU)\.([\w.]+)|\bFROM\s+Database\.([\w.]+)',clean)))
  flatdb=sorted({x for pair in dbrefs for x in pair if x})
  # 'source' (the real ESQL, structural metadata above was derived *from* it)
  # is what lets a later stage translate the actual business logic rather
  # than reproduce only module/call names.
  m['esql'].append({'path':rel,'modules':modules,'procedures':procedures,'functions':functions,'sharedVariables':[{'name':a,'declaration':b.strip()} for a,b in shared],'calls':calls,'propagateCount':propagates,'throwCount':throws,'databases':flatdb,'source':text})
  m['dependencies'] += [{'from':rel,'to':c,'kind':'ESQL_CALL'} for c in calls]+[{'from':rel,'to':d,'kind':'DATABASE'} for d in flatdb]
  if propagates:m['behaviour']['routing'].append({'source':rel,'type':'ESQL_PROPAGATE','count':propagates})
  if throws:m['behaviour']['errorHandling'].append({'source':rel,'type':'ESQL_THROW','count':throws})
 def _map(self,m,rel,text,ext):
  root=ET.fromstring(text);templates=[]
  if ext=='.xslt':templates=[self._attrs(x).get('match') or self._attrs(x).get('name') for x in root.iter() if x.tag.split('}')[-1]=='template']
  m['maps'].append({'path':rel,'type':'XSLT' if ext=='.xslt' else 'GRAPHICAL_MAP','templates':[x for x in templates if x],'source':text});m['behaviour']['transformations'].append({'source':rel,'type':ext[1:].upper()})
 def _schema(self,m,rel,text,ext):
  root=ET.fromstring(text);names=sorted({self._attrs(x).get('name') for x in root.iter() if self._attrs(x).get('name')});m['schemas'].append({'path':rel,'type':ext[1:].upper(),'declarations':names,'source':text})
 def _policy(self,m,rel,text):
  root=ET.fromstring(text);props={}
  for e in root.iter():
   a=self._attrs(e);key=a.get('name') or e.tag.split('}')[-1];val=a.get('value') or (e.text or '').strip()
   if val:props[key]=val
  m['policies'].append({'path':rel,'properties':props})
  for k,v in props.items():
   if any(x in k.lower() for x in ('queue','topic','url','endpoint','database')):m['dependencies'].append({'from':rel,'to':v,'kind':'POLICY_REFERENCE'})
 def _ambiguity(self,ref,q,owner,impact):return {'status':'OPEN','question':q,'owner':owner,'impact':impact,'evidenceReference':ref}

class FrameworkConventionExtractor:
 def extract(self,root,version='1.0.0',effective_from=None):
  root=Path(root);rules={'build':{},'dependencies':[],'projectStructure':[],'api':[],'messaging':[],'security':[],'configuration':[],'logging':[],'errorHandling':[],'observability':[]};conflicts=[]
  if not root.is_dir():raise ValueError('framework_repository_not_accessible')
  pom=root/'pom.xml';gradle=next((root/x for x in ('build.gradle','build.gradle.kts') if (root/x).exists()),None)
  if pom.exists():
   try:
    x=ET.fromstring(pom.read_text(errors='ignore'));rules['build']={'tool':'MAVEN','javaVersion':self._xml_text(x,'java.version'),'springBootVersion':self._parent_version(x)};rules['dependencies']=sorted({self._xml_text(d,'artifactId') for d in x.iter() if d.tag.split('}')[-1]=='dependency'}- {None})
   except Exception:conflicts.append('invalid_pom_xml')
  elif gradle:
   t=gradle.read_text(errors='ignore');rules['build']={'tool':'GRADLE','javaVersion':self._first(t,[r'JavaVersion\.VERSION_(\d+)',r'languageVersion\s*=\s*JavaLanguageVersion\.of\((\d+)\)'])};rules['dependencies']=sorted(set(re.findall(r"['\"]([\w.-]+:[\w.-]+):",t)))
  else:conflicts.append('missing_supported_build_file')
  for f in root.rglob('*'):
   if not f.is_file():continue
   rel=str(f.relative_to(root));parts=set(f.parts);text=f.read_text(errors='ignore') if f.stat().st_size<2_000_000 else ''
   if 'src' in parts:rules['projectStructure'].append(rel)
   self._scan_java(rules,rel,text);self._scan_config(rules,rel,text)
  if not rules['security']:conflicts.append('security_standard_not_detected')
  if not rules['observability']:conflicts.append('observability_standard_not_detected')
  template=self._template(version,effective_from or now(),rules,conflicts);return {'version':version,'effective_from':effective_from or now(),'standards':rules,'conflicts':sorted(set(conflicts)),'template_md':template,'content_hash':digest(template)}
 def verify_golden(self,root,baseline):
  actual=self.extract(root,baseline['version'],baseline['effective_from']);required=set(baseline['standards'].get('dependencies',[]));found=set(actual['standards'].get('dependencies',[]));return {'status':'PASS' if required<=found and not actual['conflicts'] else 'FAIL','missingDependencies':sorted(required-found),'conflicts':actual['conflicts']}
 def _scan_java(self,rules,rel,t):
  checks={'api':['@RestController','@Controller','@RequestMapping'],'messaging':['@KafkaListener','@JmsListener','StreamBridge'],'security':['@EnableWebSecurity','SecurityFilterChain','@PreAuthorize'],'logging':['LoggerFactory','@Slf4j'],'errorHandling':['@ControllerAdvice','@ExceptionHandler'],'observability':['MeterRegistry','@Observed','Tracer']}
  for k,terms in checks.items():
   if any(x in t for x in terms):rules[k].append({'path':rel,'patterns':[x for x in terms if x in t]})
 def _scan_config(self,rules,rel,t):
  if rel.endswith(('.yml','.yaml','.properties')):
   rules['configuration'].append({'path':rel,'externalized':'${' in t or 'config.import' in t});
   if 'management.endpoints' in t or 'management.tracing' in t:rules['observability'].append({'path':rel,'patterns':['management']})
 def _template(self,v,e,r,c):
  lines=[f'# Framework Template {v}',f'Effective from: {e}','## Build',json.dumps(r['build'],sort_keys=True),'## Mandatory patterns']
  for k in ('api','messaging','security','configuration','logging','errorHandling','observability'):lines.append(f'- {k}: {"DETECTED" if r[k] else "REQUIRES_DECISION"}')
  lines+=['## Prohibited patterns','- Embedded secrets','- Environment-specific endpoints','- Test-specific branches','## Quality checks','- Compile','- Unit tests','- Static analysis','- Dependency and licence scan','- SAST and secret scan','## Conflicts']+[f'- {x}' for x in c]
  return '\n'.join(lines)+'\n'
 def _xml_text(self,e,name=None):
  if name is None:return (e.text or '').strip() or None
  for x in e.iter():
   if x.tag.split('}')[-1]==name:return (x.text or '').strip() or None
 def _parent_version(self,x):
  for p in x.iter():
   if p.tag.split('}')[-1]=='parent' and self._xml_text(p,'artifactId')=='spring-boot-starter-parent':return self._xml_text(p,'version')
 def _first(self,t,patterns):
  for p in patterns:
   m=re.search(p,t)
   if m:return m.group(1)

class SpringProjectGenerator:
 def generate(self,out_dir,service,package,model,plan_id,framework):
  out=Path(out_dir);root=out/service;pkg=root/'src/main/java'/Path(package.replace('.','/'));test=root/'src/test/java'/Path(package.replace('.','/'));res=root/'src/main/resources';pkg.mkdir(parents=True,exist_ok=True);test.mkdir(parents=True,exist_ok=True);res.mkdir(parents=True,exist_ok=True)
  spring=framework.get('standards',{}).get('build',{}).get('springBootVersion') or '3.3.0';java=framework.get('standards',{}).get('build',{}).get('javaVersion') or '21'
  files={root/'pom.xml':self._pom(package,service,spring,java),res/'application.yml':'spring:\n  application:\n    name: '+service+'\nace:\n  endpoint: ${ACE_ENDPOINT:}\n',pkg/(self._cls(service)+'Application.java'):self._app(package,service)}
  for i,flow in enumerate(model.get('flows',[])+model.get('subflows',[]),1):
   cls=self._cls(Path(flow['path']).stem)+'Flow';files[pkg/(cls+'.java')]=self._flow(package,cls,flow,plan_id);files[test/(cls+'Test.java')]=self._test(package,cls)
  manifest=[]
  for path,content in files.items():path.write_text(content);manifest.append({'path':str(path.relative_to(root)),'hash':digest(content),'plan_id':plan_id})
  (root/'generated-manifest.json').write_text(json.dumps(manifest,indent=2));return {'root':str(root),'manifest':manifest,'content_hash':digest(json.dumps(manifest,sort_keys=True))}
 def _pom(self,g,a,s,j):return f'''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>{s}</version></parent><groupId>{g}</groupId><artifactId>{a}</artifactId><version>1.0.0</version><properties><java.version>{j}</java.version></properties><dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency></dependencies><build><plugins><plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build></project>'''
 def _app(self,p,s):c=self._cls(s)+'Application';return f'package {p};\nimport org.springframework.boot.SpringApplication;\nimport org.springframework.boot.autoconfigure.SpringBootApplication;\n@SpringBootApplication public class {c} {{ public static void main(String[] a) {{ SpringApplication.run({c}.class,a); }} }}\n'
 def _flow(self,p,c,f,plan):return f'package {p};\nimport org.springframework.stereotype.Service;\n/** provenance plan={plan} source={f["path"]} */\n@Service public final class {c} {{ public Object process(Object input) {{ return input; }} }}\n'
 def _test(self,p,c):return f'package {p};\nimport org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.*;\nclass {c}Test {{ @Test void preservesInput() {{ Object x=new Object(); assertSame(x,new {c}().process(x)); }} }}\n'
 def _cls(self,x):return ''.join(w.capitalize() for w in re.split(r'[^A-Za-z0-9]+',x) if w)

class BuildRunner:
 def run(self,project,timeout=600):
  root=Path(project).resolve();cmd=['./mvnw','-q','verify'] if (root/'mvnw').exists() else (['mvn','-q','verify'] if (root/'pom.xml').exists() else (['./gradlew','test','build'] if (root/'gradlew').exists() else None))
  if not cmd:raise ValueError('supported_build_not_found')
  started=now()
  try:r=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=timeout,env={'PATH':os.getenv('PATH',''),'HOME':os.getenv('HOME','/tmp')});status='PASS' if r.returncode==0 else 'FAIL';return {'status':status,'command':cmd,'returncode':r.returncode,'stdout_hash':digest(r.stdout),'stderr_hash':digest(r.stderr),'started_at':started,'completed_at':now()}
  except subprocess.TimeoutExpired:return {'status':'TIMEOUT','command':cmd,'started_at':started,'completed_at':now()}

class RuntimeAdapter:
 def __init__(self,name,base_url,headers=None,ssl_context=None):self.name=name;self.base_url=base_url.rstrip('/');self.headers=headers or {};self.ssl_context=ssl_context or ssl.create_default_context()
 def invoke(self,case):
  path=case.get('path','/');body=json.dumps(case.get('body')).encode() if 'body' in case else None;headers={**self.headers,**case.get('headers',{})};headers.setdefault('X-Correlation-ID',case.get('correlation_id',str(uuid.uuid4())));headers.setdefault('Content-Type','application/json')
  req=urllib.request.Request(self.base_url+path,data=body,headers=headers,method=case.get('method','POST'))
  started=time.perf_counter()
  try:
   with urllib.request.urlopen(req,timeout=case.get('timeout',30),context=self.ssl_context) as r:raw=r.read();status=r.status;rh=dict(r.headers)
  except urllib.error.HTTPError as e:raw=e.read();status=e.code;rh=dict(e.headers)
  latency=(time.perf_counter()-started)*1000
  try:payload=json.loads(raw)
  except Exception:payload=raw.decode(errors='replace')
  return {'adapter':self.name,'status':status,'headers':rh,'body':payload,'latency_ms':latency,'correlation_id':headers['X-Correlation-ID'],'content_hash':digest(raw)}
class ACERuntimeAdapter(RuntimeAdapter):pass
class SpringBootRuntimeAdapter(RuntimeAdapter):pass

class DifferentialEvidenceEngine:
 MISSING=object()
 def compare(self,case_id,expected,actual,policy=None):
  policy=policy or {};left=self._normalize(expected,policy);right=self._normalize(actual,policy);variances=[];self._walk(left,right,'$',policy,variances)
  evidence={'case_id':case_id,'expected_hash':digest(json.dumps(left,sort_keys=True,default=str)),'actual_hash':digest(json.dumps(right,sort_keys=True,default=str)),'policy_hash':digest(json.dumps(policy,sort_keys=True)),'variances':variances,'status':'PASS' if not variances else 'VARIANCE','created_at':now()};evidence['content_hash']=digest(json.dumps(evidence,sort_keys=True));return evidence
 def _normalize(self,x,p):
  x=json.loads(json.dumps(x));
  for path in p.get('masks',[]):self._set(x,path,'***MASKED***')
  for path in p.get('ignore',[]):self._delete(x,path)
  return x
 def _walk(self,a,b,path,p,out):
  if a is self.MISSING or b is self.MISSING:out.append(self._v(path,a,b,'STRUCTURAL'));return
  if isinstance(a,bool) or isinstance(b,bool):
   # bool is a subclass of int, so this must precede the numeric branch or
   # True and 1 compare equal within tolerance and a genuine type divergence
   # between the ACE and Spring payloads is reported as equivalent.
   if type(a) is not type(b):out.append(self._v(path,a,b,'TYPE'))
   elif a!=b:out.append(self._v(path,a,b,'VALUE'))
   return
  if isinstance(a,(int,float)) and isinstance(b,(int,float)):
   tol=p.get('tolerances',{}).get(path,p.get('numeric_tolerance',0))
   if abs(a-b)>tol:out.append(self._v(path,a,b,'VALUE'))
  elif type(a)!=type(b):out.append(self._v(path,a,b,'TYPE'))
  elif isinstance(a,dict):
   for k in sorted(set(a)|set(b)):self._walk(a.get(k,self.MISSING),b.get(k,self.MISSING),path+'.'+k,p,out)
  elif isinstance(a,list):
   ordered=p.get('ordering',{}).get(path,p.get('ordering_significant',True))
   if not ordered:a,b=sorted(a,key=lambda x:json.dumps(x,sort_keys=True)),sorted(b,key=lambda x:json.dumps(x,sort_keys=True))
   for i in range(max(len(a),len(b))):self._walk(a[i] if i<len(a) else self.MISSING,b[i] if i<len(b) else self.MISSING,f'{path}[{i}]',p,out)
  elif a!=b:out.append(self._v(path,a,b,'VALUE'))
 def _v(self,path,a,b,kind):return {'path':path,'expected':'<MISSING>' if a is self.MISSING else a,'actual':'<MISSING>' if b is self.MISSING else b,'kind':kind,'severity':'HIGH' if kind in ('TYPE','STRUCTURAL') else 'MEDIUM','disposition':'OPEN'}
 def _parts(self,path):return [x for x in re.split(r'\.|\[|\]',path.lstrip('$').lstrip('.')) if x!='']
 def _set(self,x,path,val):
  cur=x;parts=self._parts(path)
  for k in parts[:-1]:cur=cur[int(k)] if isinstance(cur,list) else cur.get(k,{})
  if parts:
   if isinstance(cur,list):cur[int(parts[-1])]=val
   elif parts[-1] in cur:cur[parts[-1]]=val
 def _delete(self,x,path):
  cur=x;parts=self._parts(path)
  for k in parts[:-1]:cur=cur[int(k)] if isinstance(cur,list) else cur.get(k,{})
  if parts and isinstance(cur,dict):cur.pop(parts[-1],None)

class ConfidenceRemediationService:
 def score(self,evidence_coverage,reproducible,traceability,rule_strength,test_isolation,historical_validation):
  values=[float(evidence_coverage),float(reproducible),float(traceability),float(rule_strength),float(test_isolation),float(historical_validation)]
  if any(x<0 or x>1 for x in values):raise ValueError('confidence_component_out_of_range')
  weights=[.25,.2,.2,.15,.1,.1];score=sum(x*w for x,w in zip(values,weights));return {'score':round(score,4),'components':dict(zip(['evidence_coverage','reproducible','traceability','rule_strength','test_isolation','historical_validation'],values))}
 def eligibility(self,confidence,threshold,approved_pattern,bounded_impact,tests_present,prohibited_dependency=False):
  eligible=confidence>=threshold and approved_pattern and bounded_impact and tests_present and not prohibited_dependency;return {'eligible':bool(eligible),'route':'DIRECT_FIX' if eligible else 'CLARIFICATION_OR_ACE_INSPECTION'}
 def propose(self,variance,eligibility,iteration,rollback_pointer):
  if not eligibility['eligible']:return {'status':'CLARIFICATION_REQUIRED','question':'Additional evidence or ACE inspection is required','variance':variance}
  return {'status':'PROPOSED','iteration':iteration,'variance_path':variance['path'],'approved_pattern':'MAPPING_OR_CONFIGURATION_FIX','tests':['targeted_regression','impacted_suite'],'rollback_pointer':rollback_pointer}

class OIDCIdentityProvider:
 def __init__(self,issuer,audience,jwks_uri,role_claim='roles',project_claim='projects',environment_claim='environments',cache_seconds=300):self.issuer=issuer.rstrip('/');self.audience=audience;self.jwks_uri=jwks_uri;self.role_claim=role_claim;self.project_claim=project_claim;self.environment_claim=environment_claim;self.cache_seconds=cache_seconds;self._cache=(0,{})
 def authenticate(self,bearer):
  if not bearer.startswith('Bearer '):raise PermissionError('bearer_token_required')
  token=bearer[7:];h,p,s=token.split('.');header=json.loads(self._b64(h));claims=json.loads(self._b64(p));self._claims(claims);key=self._keys().get(header.get('kid'))
  if not key:raise PermissionError('signing_key_not_found')
  self._verify_rs256((h+'.'+p).encode(),self._b64(s),key)
  return {'identity':claims['sub'],'roles':tuple(claims.get(self.role_claim,[])),'project_scopes':tuple(claims.get(self.project_claim,['GLOBAL'])),'environments':tuple(claims.get(self.environment_claim,['DEV'])),'claims':claims}
 def _keys(self):
  if self._cache[0]>time.time():return self._cache[1]
  with urllib.request.urlopen(self.jwks_uri,timeout=10,context=ssl.create_default_context()) as r:data=json.load(r)
  keys={x['kid']:x for x in data.get('keys',[])};self._cache=(time.time()+self.cache_seconds,keys);return keys
 def _claims(self,c):
  aud=c.get('aud',[]);aud=[aud] if isinstance(aud,str) else aud
  if c.get('iss','').rstrip('/')!=self.issuer or self.audience not in aud or int(c.get('exp',0))<=int(time.time()) or not c.get('sub'):raise PermissionError('invalid_token_claims')
 def _verify_rs256(self,data,sig,jwk):
  if jwk.get('kty')!='RSA':raise PermissionError('unsupported_signing_key')
  try:
   from cryptography.hazmat.primitives.asymmetric import rsa,padding
   from cryptography.hazmat.primitives import hashes
   key=rsa.RSAPublicNumbers(int.from_bytes(self._b64(jwk['e']),'big'),int.from_bytes(self._b64(jwk['n']),'big')).public_key();key.verify(sig,data,padding.PKCS1v15(),hashes.SHA256())
  except ImportError:raise RuntimeError('cryptography_dependency_required')
  except Exception:raise PermissionError('invalid_token_signature')
 def _b64(self,v):return base64.urlsafe_b64decode(v+'='*(-len(v)%4))
