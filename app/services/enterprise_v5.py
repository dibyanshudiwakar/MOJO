import base64, hashlib, json, os, re, ssl, subprocess, time, urllib.request, urllib.error, uuid, xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from app.repository import uid,now,digest

class ACEParser:
 EXT={'.msgflow':'flows','.subflow':'subflows','.esql':'esql','.map':'maps','.xslt':'maps','.xsd':'schemas','.wsdl':'schemas','.policyxml':'policies'}
 PROTOCOLS={'HTTPInput':'HTTP','HTTPReply':'HTTP','SOAPInput':'SOAP','MQInput':'MQ','MQOutput':'MQ','KafkaConsumer':'KAFKA','KafkaProducer':'KAFKA','FileInput':'FILE','TimeoutNotification':'SCHEDULED','Database':'DATABASE'}
 def parse(self,root):
  root=Path(root);model={'flows':[],'subflows':[],'esql':[],'maps':[],'schemas':[],'policies':[],'interfaces':[],'dependencies':[],'behaviour':{'routing':[],'transformations':[],'transactions':[],'retries':[],'timeouts':[],'errorHandling':[],'sideEffects':[]},'ambiguities':[],'backend_properties':{}}
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
    elif ext=='.properties':self._env_properties(model,rel,text)
   except Exception as e:model['ambiguities'].append(self._ambiguity(rel,'parse_failure:'+type(e).__name__,'ACE_SME','analysis incomplete'))
  if not model['flows'] and not model['subflows']:model['ambiguities'].append(self._ambiguity('$','no_ace_flow_detected','ACE_SME','analysis blocked'))
  model['backend_type']=self._detect_backend_type(model)
  model['content_hash']=digest(json.dumps(model,sort_keys=True,separators=(',',':')));return model
 #: <METHOD>#<Vendor>ServiceURL / <METHOD>#testHarness<Vendor>Url -- the real
 #: naming convention these env-properties files use (confirmed against the
 #: real DMRClaimStatus repo: "GET#MedForceServiceURL"/"GET#testHarnessMedForceUrl").
 #: Nothing read these files before this -- the generator had no real backend
 #: host to put in application-local.properties/config-map and left a TODO.
 _SERVICE_URL=re.compile(r'^\w+#(\w+)ServiceURL$')
 _TEST_HARNESS_URL=re.compile(r'^\w+#testHarness(\w+)Url$',re.I)
 def _env_properties(self,m,rel,text):
  env=Path(rel).stem.rsplit('.',1)[-1] if '.' in Path(rel).stem else ''
  if not env:return
  for line in text.splitlines():
   line=line.strip()
   if not line or line.startswith('#') or '=' not in line:continue
   k,_,v=line.partition('=');k=k.strip();v=v.strip()
   su=self._SERVICE_URL.match(k)
   if su:m['backend_properties'].setdefault(su.group(1),{}).setdefault(env,{})['service_url']=v
   th=self._TEST_HARNESS_URL.match(k)
   if th:m['backend_properties'].setdefault(th.group(1),{}).setdefault(env,{})['test_harness_url']=v
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

#: Environment/cluster suffixes a real repo's config-map and Helm values carry
#: one file per (structural-conventions.md §5's "full environment list" for
#: config-map/deploy-config specifically -- narrower than DeployMetadata.json's
#: 17-entry deployable-target superset, which includes targets with no
#: properties file of their own).
ENV_CLUSTERS=('dev-pbmaz','dev2-pbmaz','dev3-pbmaz','qa-pbmaz','qa2-pbmaz','qa3-pbmaz','stress-pbmaz','uat-pbmaz','prod-pbmaz','prod-pbmri')
#: Tier a config-map env belongs to, for the one shared logback-<tier>.xml
#: file every framework-core jar already bundles (rest-external-direct.md
#: §6 "<env> is the tier, not the full env-cluster suffix").
def _tier(env_cluster):
 e=env_cluster.split('-')[0]
 return 'prod' if e.startswith('prod') else ('qa' if e.startswith('qa') else ('dev' if e.startswith('dev') else e))


class SpringProjectGenerator:
 """Scaffold + per-construct Java emitter, driven by the plan's own mapping.

 Two tiers, matching DEFAULT_MAPPINGS' own GENERATED/HAND_WRITTEN_PROTECTED
 split (migration_lifecycle_v6.py): the repository/deployment scaffolding and
 the infrastructure classes (controller, API interface, DTOs) are produced
 deterministically from fixed templates (external-skills/iib-to-ms-migration/
 references/fixed-file-templates.md, structural-conventions.md) -- the skill's
 own instruction is to copy these, never hand-generate them. The business-logic
 classes (service, connector) get a real signature and package per
 rest-external-direct.md §8, but an honest TODO body citing the ESQL/node
 source that belongs there -- code-generation's business-logic-synthesis step
 fills those in from the real source captured on each task; this generator
 never invents transformation logic itself.

 Only REST_EXTERNAL_DIRECT has its own full layout so far (confirmed against
 the real DMRClaimStatus reference this was diagnosed from). SOAP/QL_CICS/
 RXCLAIM_SBO/MQ_COBOL fall back to a flat per-flow stub -- clearly weaker,
 but no worse than every backend type got before this change, and each
 connector reference's own §2/§8 package layout is the documented next step
 once a service of that type needs it.
 """
 def generate(self,out_dir,service,package,model,plan_id,framework,tasks=None):
  tasks=tasks or []
  backend=(model.get('backend_type') or {}).get('type','UNDETERMINED')
  out=Path(out_dir);root=out/service
  files={};stub_sources={}
  self._scaffold(files,root,service,package,model,framework)
  if backend=='REST_EXTERNAL_DIRECT':
   self._rest_sources(files,root,service,package,model,tasks,plan_id,stub_sources)
  else:
   self._fallback_sources(files,root,service,package,model,plan_id)
  manifest=[]
  for path,content in files.items():
   path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
   manifest.append({'path':str(path.relative_to(root)).replace('\\','/'),'hash':digest(content),'plan_id':plan_id})
  (root/'generated-manifest.json').write_text(json.dumps(manifest,indent=2))
  # Which generated file holds which task's HAND_WRITTEN_PROTECTED stub, and
  # the source_references cited in it -- so code-generation's business-logic
  # synthesis step can find exactly what to translate without re-parsing its
  # own TODO comment back out of the Java text.
  return {'root':str(root),'manifest':manifest,'content_hash':digest(json.dumps(manifest,sort_keys=True)),'stub_sources':stub_sources}

 # -- repository-level scaffolding (always produced, every backend type) ----
 def _scaffold(self,files,root,service,package,model,framework):
  java=framework.get('standards',{}).get('build',{}).get('javaVersion') or '21'
  fw_version=framework.get('version') or '3.2.2'
  artifact='cvs-v1-'+service.lower()+'-oci';parent=artifact+'-parent';web=artifact+'-web'
  backend_props=model.get('backend_properties') or {}
  files[root/parent/'pom.xml']=self._pom_parent(package,artifact,parent,web,java,fw_version)
  files[root/web/'pom.xml']=self._pom_web(package,parent,web,java)
  res=root/web/'src/main/resources'
  # Minimal: just the application name + active profile (structural-conventions.md
  # §1: "minimal: just spring.application.name + spring.profiles.active"). This was
  # previously YAML syntax written into a .properties file -- Spring's properties
  # loader reads key=value lines, so "spring:\n  application:\n    name: X" never
  # actually set the name at all.
  files[res/'application.properties']=f'spring.application.name={service}\nspring.profiles.active=local\n'
  files[res/'application-local.properties']=self._application_local_properties(backend_props)
  files[root/'Dockerfile']=self._dockerfile(artifact)
  files[root/'.dockerignore']='target/\n*.iml\n.idea/\n.git/\n'
  files[root/'README.md']=f'# {service}\n\nMigrated from IBM ACE. Generated by the ACE Migration Factory -- see provenance-manifest.json for the plan this came from.\n'
  files[root/'.github/config/DeployMetadata.json']=self._deploy_metadata()
  files[root/'.github/workflows/build.yaml']=self._github_build(service)
  files[root/'.github/workflows/deploy.yaml']=self._github_deploy(artifact)
  for env in ENV_CLUSTERS:
   files[root/'config/config-map'/('application.%s.properties'%env)]=self._config_map_properties(env,artifact,backend_props)
   files[root/'config/deploy-config'/('values.%s.yaml'%env)]=self._helm_values(artifact,env)
  files[root/'config/deploy-config/Chart.yaml']=f'apiVersion: v1\nname: {artifact}\nversion: 1.0.0\n'
  files[root/'config/deploy-config/templates/Deployment.yaml']=self._helm_deployment()
  files[root/'config/deploy-config/templates/Service.yaml']=self._helm_service()
  files[root/'config/deploy-config/templates/Route.yaml']=self._helm_route()
  files[root/'config/deploy-config/templates/Secret.yaml']=self._helm_secret()

 def _pom_parent(self,g,artifact,parent,web,java,fw_version):
  # The CVS framework-starter parent IS the real parent (confirmed against the
  # real DMRClaimStatus repo) -- it manages the Spring Boot version itself, so
  # a raw org.springframework.boot:spring-boot-starter-parent here was wrong,
  # not just incomplete. parent and web are *siblings*; the module path is
  # relative, matching how the real reference's own pom declares it.
  return f'''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
 <parent><groupId>com.cvshealth.eip.microservices.frameworks</groupId><artifactId>framework-starter</artifactId><version>{fw_version}</version></parent>
 <groupId>{g}</groupId><artifactId>{parent}</artifactId><version>1.0.0-SNAPSHOT</version><packaging>pom</packaging>
 <modules><module>../{web}</module></modules>
 <properties><framework.version>{fw_version}</framework.version><swagger.version>2.2.23</swagger.version><springdoc.version>2.8.4</springdoc.version><java.version>{java}</java.version></properties>
</project>
'''

 def _pom_web(self,g,parent,web,java):
  # framework-restclient is mandatory for every backend type, not only REST
  # (structural-conventions.md §4) -- and, confirmed against the real pom,
  # is also where EieHeader/ErrorUtil/AESEncryption/EIPMicroserviceLogger and
  # the @EIPMicroservice* annotations actually live; nothing else needs to be
  # declared for them.
  return f'''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
 <parent><groupId>{g}</groupId><artifactId>{parent}</artifactId><version>1.0.0-SNAPSHOT</version><relativePath>../{parent}</relativePath></parent>
 <artifactId>{web}</artifactId><packaging>jar</packaging>
 <dependencies>
  <dependency><groupId>com.cvshealth.eip.microservices.frameworks</groupId><artifactId>framework-restclient</artifactId></dependency>
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId><exclusions><exclusion><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-logging</artifactId></exclusion></exclusions></dependency>
  <dependency><groupId>org.springdoc</groupId><artifactId>springdoc-openapi-starter-webmvc-ui</artifactId><version>${{springdoc.version}}</version></dependency>
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope><exclusions><exclusion><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-logging</artifactId></exclusion></exclusions></dependency>
  <dependency><groupId>org.mockito</groupId><artifactId>mockito-core</artifactId><scope>test</scope></dependency>
  <dependency><groupId>org.mockito</groupId><artifactId>mockito-junit-jupiter</artifactId><scope>test</scope></dependency>
  <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><scope>test</scope></dependency>
 </dependencies>
 <build><plugins>
  <plugin><groupId>org.jacoco</groupId><artifactId>jacoco-maven-plugin</artifactId><version>0.8.12</version><configuration><rules><rule><element>BUNDLE</element><limits><limit><counter>LINE</counter><value>COVEREDRATIO</value><minimum>0.65</minimum></limit></limits></rule></rules></configuration></plugin>
  <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><configuration><release>{java}</release></configuration></plugin>
  <plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin>
 </plugins></build>
</project>
'''

 #: Maps a config-map env-cluster suffix (ENV_CLUSTERS, e.g. 'dev2-pbmaz') to
 #: the ACE repo's own per-environment *.properties suffix (e.g. 'DEV2') that
 #: ACEParser._env_properties keys backend_properties by. Deliberately an
 #: exact-match-or-nothing lookup, not a nearest-tier guess: the real
 #: reference's own uat-pbmaz config-map happens to carry the STRESS backend
 #: host, which is a one-off business decision (there is no dedicated UAT
 #: backend), not something a generic rule should silently reproduce for every
 #: service. Any cluster with no exact ACE counterpart is left as a flagged
 #: TODO for a human to assign deliberately.
 def _ace_env_key(self,env_cluster,available):
  base=env_cluster.split('-')[0].upper()
  return base if base in available else None

 def _restclient_block(self,vendor,envs,env_key,cert_path_fmt):
  client=vendor.lower();info=envs.get(env_key) or {}
  lines=['','#'+'='*77,f'# {vendor.upper()} CONNECTOR CONFIGURATION','#'+'='*77,
         'application.http.enabled=true',f'application.http.restclient.{client}.name={client}']
  uri=info.get('service_url')
  if uri:
   lines.append(f'application.http.restclient.{client}.uri={uri}')
  else:
   lines.append(f'# TODO: application.http.restclient.{client}.uri -- no {vendor} backend URL for '
                 f'env "{env_key or "?"}" was found in the ACE repo\'s own *.properties (environments found: '
                 f'{", ".join(sorted(envs)) or "none"}); assign the right one deliberately, do not guess')
  lines+=[f'application.http.restclient.{client}.connect-timeout=18000',
          f'application.http.restclient.{client}.request-timeout=18000',
          f'application.http.restclient.{client}.socket-timeout=18000',
          f'application.http.restclient.{client}.max-total-connections=50',
          f'application.http.restclient.{client}.default-max-per-route=25',
          f'application.http.restclient.{client}.default-keep-alive-time-millis=20000']
  harness=info.get('test_harness_url')
  if harness:
   lines.append(f'application.http.restclient.{client}.test-harness-host={harness}')
  lines.append('application.http.close-idle-connection-wait-time-secs=30')
  lines+=['',f'application.http.restclient.{client}.custom-ssl-trust-store-enabled=true',
          f'spring.ssl.bundle.pem.{client}.truststore.certificate='+cert_path_fmt.format(client=client),
          f'# TODO: application.{client}.client-id / application.{client}.client-secret -- paste ONLY the '
           'encrypted value from the CVS encryption endpoint here; never the plaintext IIB clientID/clientSecret',
          f'application.{client}.client-id=',f'application.{client}.client-secret=']
  return '\n'.join(lines)+'\n'

 def _application_local_properties(self,backend_props):
  base=('server.port=${PORT:8081}\nspring.main.banner-mode=off\nserver.servlet.context-path=/\n'
        'server.tomcat.relaxed-path-chars=^\napplication.jackson.serializationInclusion=ALWAYS\n'
        'application.jackson.enableLegacyErrorStructure=true\nmanagement.endpoints.web.exposure.include=health,info\n'
        '#CIRCUIT BREAKER PROPERTIES\napplication.circuitbreaker.failureRateThreshold=90.0\n'
        'application.circuitbreaker.slowCallDurationThreshold=8s\napplication.circuitbreaker.slowCallRateThreshold=80.0\n'
        'application.circuitbreaker.writableStackTraceEnabled=false\n\n'
        '# Logging\nlogging.level.com.cvshealth.eip.microservices=DEBUG\nlogging.level.org.springframework=WARN\n')
  blocks=''.join(self._restclient_block(v,envs,'DEV','classpath:{client}-truststore.crt.pem')
                 for v,envs in sorted(backend_props.items()))
  return base+blocks

 def _config_map_properties(self,env,artifact,backend_props):
  base=('server.port=${PORT:8080}\nspring.main.banner-mode=off\nserver.servlet.context-path=/\n'
        'server.tomcat.relaxed-path-chars=^\napplication.jackson.serializationInclusion=ALWAYS\n'
        'application.jackson.enableLegacyErrorStructure=true\nlogging.config=classpath:logback-%s.xml\n'
        'server.max-http-request-header-size=128KB\nserver.tomcat.max-http-response-header-size=128KB\n'
        'server.tomcat.max-http-header-count=200\n\nmanagement.endpoints.web.exposure.include=health,info\n\n'
        '#CIRCUIT BREAKER PROPERTIES\napplication.circuitbreaker.failureRateThreshold=90.0\n'
        'application.circuitbreaker.slowCallDurationThreshold=8s\napplication.circuitbreaker.slowCallRateThreshold=80.0\n'
        'application.circuitbreaker.writableStackTraceEnabled=false\n\n'
        '# Logging\nlogging.level.com.cvshealth.eip.microservices=%s\nlogging.level.org.springframework=WARN\n'
        )%(_tier(env),'WARN' if _tier(env)=='prod' else 'DEBUG')
  if not backend_props:
   return base
  base+=(f'\nspring.config.import=/os/spring-boot-{artifact}/creds/application-akeyless.properties,'
         f'optional:/os/spring-boot-{artifact}/creds/application-aes_config.properties\n')
  cert_fmt='/os/spring-boot-'+artifact+'/certs/{client}-truststore.crt.pem'
  for vendor,envs in sorted(backend_props.items()):
   base+=self._restclient_block(vendor,envs,self._ace_env_key(env,envs),cert_fmt)
  return base

 def _dockerfile(self,artifact):
  # Skeleton + the documented default decisions (fixed-file-templates.md §2):
  # IntegrationEngineeringTeam@CVSHealth.com maintainer, combined mkdir form,
  # the most frequent JVM-flag combination.
  return (f"FROM quay-registry.aetna.com/cvs-base-images/os-eng/redhat/ubi9/openjdk-micro:21\n"
          f"LABEL LINE_OF_BUSINESS='cvs-pss'\nLABEL maintainer='IntegrationEngineeringTeam@CVSHealth.com'\n"
          f"LABEL Name='{artifact}'\nLABEL Version='0.0.1-SNAPSHOT'\nUSER root\n\n"
          f"RUN mkdir -p /os/spring-boot-{artifact}/logs \\\n"
          f"    && chmod -R a+rw /os/spring-boot-{artifact} \\\n"
          f"    && chown -R 1001:0 /os/spring-boot-{artifact}\n\n"
          f"RUN mkdir -p /var/log && chmod 777 /var/log && chown 1001:0 /var/log\n\n"
          f"COPY ./{artifact}-web/target/{artifact}-web-*.jar /os/spring-boot-{artifact}/app.jar\n\n"
          f"WORKDIR /os/spring-boot-{artifact}\n\nUSER 1001\n\n"
          f'ENTRYPOINT ["java", "-XX:InitialRAMPercentage=25.0", "-XX:MaxRAMPercentage=50.0", "-XX:+UseG1GC", "-jar", "app.jar"]\n')

 def _deploy_metadata(self):
  envs=["Dev","Dev2","Dev3","QA","QA2","QA3","Stress","Prod","Prod2","Prod3","Prod4","Prod5","TRG","UAT","UAT2","DR","DR2"]
  return json.dumps({'environment_list':envs,'multiEnvDeploy':[],'artifact-repo-folder':'EIPMS',
                     'LibraryVariableSets':['OpenShift Deployment Global Variable Set','Global - Variable Set (Universal for All Projects)']},indent=4)

 def _github_build(self,service):
  return (f"---\nname: {service} CI Build\non:\n  push:\n    branches:\n      - 'main'\n      - 'master'\n"
          f"      - '*_Path1'\n      - '*_Path2'\n      - '*_Path3'\n    paths-ignore:\n      - '.github/**'\n      - '*.md'\n"
          f"jobs:\n  j2ee-oci-build:\n    uses: cvs-health-enterprise-code/legacy-ci-gha-workflow-actions/.github/workflows/j2ee-oci.yml@main\n"
          f"    with:\n      java-version: '21'\n      container-build-tool: 'docker'\n      environment: 'development'\n"
          f"      minimum-code-coverage: '70'\n      jacoco-version: '0.8.12'\n      maven-version: '3.9.12'\n"
          f"      SNYK_ORG: 'enterprise-services-layer-esl'\n      all-projects: 'true'\n    secrets: inherit\n")

 def _github_deploy(self,artifact):
  return (f"---\nname: {artifact} - Deploy\non:\n  workflow_dispatch:\n    inputs:\n      environment:\n"
          f"        required: true\n        type: choice\n        description: Environment\n"
          f"        options: [ Dev, Dev2, Dev3, QA, QA2, QA3, Stress, Prod, Prod2, Prod3,\n"
          f"                   Prod4, Prod5, TRG, UAT, UAT2, DR, DR2 ]\n      artifact:\n        required: true\n"
          f"        type: string\n        description: artifact to deploy (image tag)\n      app_name:\n"
          f"        required: true\n        type: string\n        default: {artifact}\n        description: Application Name\n"
          f"      change_request:\n        required: false\n        type: string\n"
          f"        description: Change Request ID. **Only for Prod Deployments**\n        default: ''\n"
          f"  repository_dispatch:\n    types: [ autodeploy-from-ci ]\n"
          f"permissions:\n  actions: read\n  contents: write\n  id-token: write\n"
          f"jobs:\n  Confirm-Workflow-Branch:\n    if: github.ref != 'refs/heads/master' && github.ref != 'refs/heads/main'\n"
          f"    runs-on:\n      group: cvs-linux-self-hosted\n    steps:\n      - name: Print Skip Message\n        run: |\n"
          f'          echo "This workflow is not intended to be run on branches other than master or main. Skipping the workflow."\n'
          f"          exit 1\n  Deploy:\n    needs: Confirm-Workflow-Branch\n    if: |\n      always() &&\n"
          f"      needs.Confirm-Workflow-Branch.result == 'skipped'\n    uses:\n"
          f"      cvs-health-enterprise-code/openshift-deploy/.github/workflows/deploy-to-openshift-reusable-workflow.yaml@main\n"
          f"    with:\n      environment: \"${{{{ github.event.inputs.environment || github.event.client_payload.environment }}}}\"\n"
          f"      artifact: \"${{{{ github.event.inputs.artifact || github.event.client_payload.artifact }}}}\"\n"
          f"      app_name: \"${{{{ github.event.inputs.app_name || github.event.client_payload.app_name }}}}\"\n"
          f"      change_request: \"${{{{ github.event.inputs.change_request }}}}\"\n    secrets: inherit\n")

 def _helm_values(self,artifact,env):
  return (f"APP_NAME: {artifact}\nREPO_NAME: {artifact}\nENV: {env.split('-')[0]}\nCLUSTER: {env.split('-',1)[1]}\n"
          f"CONTEXT_ROOT: /api/v1\nCONTAINER_PORT: 8080\nPODS: 1\nCPU_LIMIT: 500m\nMEM_LIMIT: 1Gi\nCPU_REQUEST: 250m\nMEM_REQUEST: 512Mi\n"
          f"HEALTH_CHECK_PATH: /actuator/health\nHEALTH_CHECK_PORT: 8080\n"
          f"READINESS_INITIAL_DELAY_SECONDS: 30\nREADINESS_TIMEOUT_SECONDS: 5\nREADINESS_SUCESS_THRESHOLD: 1\nREADINESS_FAILURE_THRESHOLD: 3\n"
          f"LIVENESS_INITIAL_DELAY_SECONDS: 60\nLIVENESS_PERIOD_SECONDS: 10\nLIVENESS_SUCESS_THRESHOLD: 1\nLIVENESS_FAILURE_THRESHOLD: 3\n"
          f"SITE_URL: {artifact}.{env}.cvshealth.com\n")

 def _helm_deployment(self):
  return '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Values.APP_NAME }}
  labels:
    app: {{ .Values.APP_NAME }}
    k8s-app: {{ .Values.APP_NAME }}
spec:
  replicas: {{ int .Values.PODS }}
  selector:
    matchLabels:
      k8s-app: {{ .Values.APP_NAME }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 25%
      maxSurge: 0%
  template:
    metadata:
      labels:
        app: {{ .Values.APP_NAME }}
        deployment: {{ .Values.APP_NAME }}
        k8s-app: {{ .Values.APP_NAME }}
    spec:
      volumes:
        - name: configmap-vol
          configMap:
            name: {{ .Values.APP_NAME }}
            items:
              - key: application.{{ .Values.ENV }}-{{ .Values.CLUSTER }}.properties
                path: application.properties
            defaultMode: 420
      containers:
        - resources:
            limits:
              cpu: {{ .Values.CPU_LIMIT }}
              memory: {{ .Values.MEM_LIMIT }}
            requests:
              cpu: {{ .Values.CPU_REQUEST }}
              memory: {{ .Values.MEM_REQUEST }}
          readinessProbe:
            httpGet:
              path: "{{ .Values.CONTEXT_ROOT }}{{ .Values.HEALTH_CHECK_PATH }}"
              port: {{ int .Values.HEALTH_CHECK_PORT }}
              scheme: HTTP
            initialDelaySeconds: {{ int .Values.READINESS_INITIAL_DELAY_SECONDS }}
            timeoutSeconds: {{ int .Values.READINESS_TIMEOUT_SECONDS }}
            successThreshold: {{ int .Values.READINESS_SUCESS_THRESHOLD }}
            failureThreshold: {{ int .Values.READINESS_FAILURE_THRESHOLD }}
          name: {{ .Values.APP_NAME }}
          livenessProbe:
            httpGet:
              path: "{{ .Values.CONTEXT_ROOT }}{{ .Values.HEALTH_CHECK_PATH }}"
              port: {{ int .Values.HEALTH_CHECK_PORT }}
              scheme: HTTP
            initialDelaySeconds: {{ int .Values.LIVENESS_INITIAL_DELAY_SECONDS }}
            periodSeconds: {{ int .Values.LIVENESS_PERIOD_SECONDS }}
            successThreshold: {{ int .Values.LIVENESS_SUCESS_THRESHOLD }}
            failureThreshold: {{ int .Values.LIVENESS_FAILURE_THRESHOLD }}
          imagePullPolicy: Always
          volumeMounts:
            - name: configmap-vol
              mountPath: "/etc/config/{{ .Values.APP_NAME }}/data/"
          image: quay-registry.aetna.com/cvs-pss/{{ .Values.REPO_NAME }}:{{ .Values.ENV }}
          ports:
            - containerPort: {{ int .Values.CONTAINER_PORT }}
              protocol: TCP
      restartPolicy: Always
      terminationGracePeriodSeconds: 30
      dnsPolicy: ClusterFirst
      schedulerName: default-scheduler
'''

 def _helm_service(self):
  return '''apiVersion: v1
kind: Service
metadata:
  name: {{ .Values.APP_NAME }}
  labels:
    app: {{ .Values.APP_NAME }}
spec:
  type: ClusterIP
  ports:
    - port: {{ int .Values.CONTAINER_PORT }}
      targetPort: {{ int .Values.CONTAINER_PORT }}
      protocol: TCP
  selector:
    k8s-app: {{ .Values.APP_NAME }}
'''

 def _helm_route(self):
  return '''apiVersion: v1
kind: Route
metadata:
  name: {{ .Values.APP_NAME }}
spec:
  host: {{ .Values.SITE_URL }}
  path: {{ .Values.CONTEXT_ROOT }}
  to:
    kind: Service
    name: {{ .Values.APP_NAME }}
  wildcardPolicy: None
'''

 def _helm_secret(self):
  return '''apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.APP_NAME }}
type: Opaque
data: {}
'''

 # -- REST_EXTERNAL_DIRECT Java source (rest-external-direct.md §8 layout) --
 def _context_root(self,model,service):
  # The service's own inbound endpoint, not an outbound call's URL -- an
  # interface entry's endpoint is a local path (starts with '/') only for
  # the node that receives a request; ComIbmWSRequest's endpoint is the
  # absolute URL of whatever backend it calls, which must never be mistaken
  # for this service's own route.
  for i in model.get('interfaces',[]):
   ep=i.get('endpoint','UNKNOWN')
   if i['protocol']=='HTTP' and ep.startswith('/'):
    # §5a: replace the IIB '/edb/' segment with '/cs/'.
    return ep.split('*')[0].rstrip('/').replace('/edb/','/cs/')
  return '/api/v1/cs/'+service.lower()

 def _by_module(self,tasks):
  by={}
  for t in tasks:
   by.setdefault(t.get('target_module',''),[]).append(t)
  return by

 def _protected_body(self,return_stmt,sources):
  refs='\n   * '.join(sources) or 'no source construct recorded'
  return (f'  /* TODO (business-logic-synthesis): translate the real logic from --\n'
          f'   * {refs}\n'
          f'   * This method intentionally returns a placeholder until that step runs. */\n'
          f'  {return_stmt}\n')

 #: A vendor name is taken verbatim from the ACE *.properties key it was
 #: extracted from (e.g. "MedForce" from `GET#MedForceServiceURL`) -- already
 #: real PascalCase there, so unlike _cls (built for kebab/snake *service*
 #: names, which it re-capitalizes word-by-word) this must only strip stray
 #: non-alnum characters, never change case.
 def _vendor_cls(self,vendor):
  return re.sub(r'[^A-Za-z0-9]+','',vendor) or vendor

 def _provider_const(self,vendor):
  return re.sub(r'[^A-Za-z0-9]+','',vendor).upper()

 def _rest_sources(self,files,root,service,package,model,tasks,plan_id,stub_sources):
  cls=self._cls(service);pkg=root/('cvs-v1-'+service.lower()+'-oci-web')/'src/main/java'/Path(package.replace('.','/'))
  test=root/('cvs-v1-'+service.lower()+'-oci-web')/'src/test/java'/Path(package.replace('.','/'))
  by=self._by_module(tasks)
  service_srcs=[t['source_reference'] for t in by.get('service',[])]
  connector_srcs=[t['source_reference'] for t in by.get('client',[])+by.get('soap',[])]
  context_root=self._context_root(model,service)
  # Vendor-based naming (connector-reference.md's own convention, confirmed
  # against the real MedForceConnector/MedForceProperties): only when exactly
  # one backend vendor was found in the ACE repo's *.properties -- with two+
  # vendors, or none, there is no single name to prefer over the service's own.
  backend_props=model.get('backend_properties') or {}
  vendor=next(iter(backend_props)) if len(backend_props)==1 else None
  connector_name=(self._vendor_cls(vendor)+'Connector') if vendor else (cls+'ConnectorService')

  files[pkg/(cls+'Application.java')]=self._app(package,service)
  files[pkg/'controller'/(cls+'Controller.java')]=self._controller(package,cls)
  files[test/'controller'/(cls+'ControllerTest.java')]=self._empty_test(package+'.controller',cls+'Controller')
  files[pkg/'swagger/api'/(cls+'Api.java')]=self._api_interface(package,cls,context_root)
  if vendor:
   vcls=self._vendor_cls(vendor)
   files[pkg/'config'/(vcls+'Properties.java')]=self._vendor_properties(package,vendor)
   files[test/'config'/(vcls+'PropertiesTest.java')]=self._empty_test(package+'.config',vcls+'Properties')
  service_path=pkg/'service'/(cls+'Service.java')
  files[service_path]=self._service(package,cls,vendor,connector_name,service_srcs)
  if service_srcs:stub_sources[str(service_path.relative_to(root)).replace('\\','/')]=service_srcs
  files[test/'service'/(cls+'ServiceTest.java')]=self._empty_test(package+'.service',cls+'Service')
  connector_path=pkg/'connector'/(connector_name+'.java')
  files[connector_path]=self._connector(package,cls,connector_name,vendor,connector_srcs)
  if connector_srcs:stub_sources[str(connector_path.relative_to(root)).replace('\\','/')]=connector_srcs
  files[test/'connector'/(connector_name+'Test.java')]=self._empty_test(package+'.connector',connector_name)
  files[pkg/'util'/(cls+'Util.java')]=self._util(package,cls)
  files[test/'util'/(cls+'UtilTest.java')]=self._empty_test(package+'.util',cls+'Util')
  files[pkg/'constants'/(cls+'Constants.java')]=self._constants(package,cls,vendor)
  # Every REST_EXTERNAL_DIRECT service gets this pair, generic and
  # business-logic-free (confirmed against the real DmrClaimStatusExceptionHandler/
  # ErrorResponse: a @RestControllerAdvice catching Spring's own
  # NoResourceFoundException, and the httpCode/httpMessage/moreInformation
  # shape every such handler returns) -- unlike the service/vendor response
  # DTOs below, there is no per-service field to guess here.
  files[pkg/'swagger/model/response/ErrorResponse.java']=self._error_response(package)
  files[test/'swagger/model/response/ErrorResponseTest.java']=self._empty_test(package+'.swagger.model.response','ErrorResponse')
  files[pkg/'exception'/(cls+'ExceptionHandler.java')]=self._exception_handler(package,cls)
  files[test/'exception'/(cls+'ExceptionHandlerTest.java')]=self._empty_test(package+'.exception',cls+'ExceptionHandler')
  for schema in model.get('schemas',[]):
   name=self._cls(Path(schema['path']).stem)
   files[pkg/'swagger/model/response'/(name+'.java')]=self._schema_model(package,name,schema)
   files[test/'swagger/model/response'/(name+'Test.java')]=self._empty_test(package+'.swagger.model.response',name)

 def _app(self,p,s):
  c=self._cls(s)+'Application'
  return (f'package {p};\n\nimport org.springframework.boot.SpringApplication;\n'
          f'import org.springframework.boot.autoconfigure.SpringBootApplication;\n'
          f'import org.springframework.context.annotation.ComponentScan;\n\n'
          f'@ComponentScan(basePackages = {{"com.cvshealth.*"}})\n@SpringBootApplication\n'
          f'public class {c} {{\n  public static void main(String[] args) {{ SpringApplication.run({c}.class, args); }}\n}}\n')

 def _controller(self,p,c):
  # Zero-logic delegate -- no error-response calls, no inline mapping
  # (rest-external-direct.md §5a/§8, Rule 9/10).
  return (f'package {p}.controller;\n\n'
          f'import com.cvshealth.eip.microservices.frameworks.annotations.EIPMicroserviceOperation;\n'
          f'import {p}.service.{c}Service;\nimport {p}.swagger.api.{c}Api;\n'
          f'import lombok.RequiredArgsConstructor;\nimport org.springframework.http.ResponseEntity;\nimport org.springframework.web.bind.annotation.RestController;\n\n'
          f'@RestController\n@RequiredArgsConstructor\npublic class {c}Controller implements {c}Api {{\n'
          f'  private final {c}Service service;\n\n'
          f'  @Override\n  @EIPMicroserviceOperation\n  public ResponseEntity<Object> get{c}(String id, String cvsConsumerCorrelationId) {{\n'
          f'    return ResponseEntity.ok(service.get{c}(id));\n  }}\n}}\n')

 def _api_interface(self,p,c,context_root):
  return (f'package {p}.swagger.api;\n\nimport org.springframework.http.MediaType;\nimport org.springframework.http.ResponseEntity;\n'
          f'import org.springframework.web.bind.annotation.*;\n\n'
          f'@RequestMapping(value = "${{CONTEXT_ROOT:{context_root}}}")\npublic interface {c}Api {{\n\n'
          f'  @GetMapping(value = "/{{id}}", produces = MediaType.APPLICATION_JSON_VALUE)\n'
          f'  ResponseEntity<Object> get{c}(\n'
          f'      @PathVariable String id,\n'
          f'      @RequestHeader(value = "Cvs-Consumer-Correlation-Id", required = false) String cvsConsumerCorrelationId);\n}}\n')

 def _vendor_properties(self,p,vendor):
  # @ConfigurationProperties(prefix="application.<vendor>") + @Data over
  # clientId/clientSecret -- confirmed against the real MedForceProperties.
  # The values themselves are never written here or anywhere generated: they
  # come from the encrypted application-local/config-map properties (or the
  # akeyless/aes_config import in config-map), never from this class.
  vcls=self._vendor_cls(vendor)
  return (f'package {p}.config;\n\nimport lombok.Data;\n'
          f'import org.springframework.boot.context.properties.ConfigurationProperties;\n'
          f'import org.springframework.context.annotation.Configuration;\n\n'
          f'@Configuration\n@ConfigurationProperties(prefix = "application.{vendor.lower()}")\n@Data\n'
          f'public class {vcls}Properties {{\n  private String clientId;\n  private String clientSecret;\n}}\n')

 def _service(self,p,c,vendor,connector_name,sources):
  # Orchestrates validate -> connector -> map; ErrorUtil.errorResponse(...)
  # calls belong here, inline, per Rule 10 -- no exception package. The
  # logger/EieHeader/connector/util field wiring is real and generic
  # (confirmed against DmrClaimStatusService's own constructor); only the
  # method body is business logic, left as an honest TODO stub.
  extra_imports=extra_fields=call_args=''
  if vendor:
   vcls=self._vendor_cls(vendor);client=vendor.lower()
   extra_imports=f'import {p}.config.{vcls}Properties;\nimport org.springframework.beans.factory.annotation.Value;\n'
   extra_fields=(f'  private final {vcls}Properties properties;\n\n'
                 f'  @Value("${{application.http.restclient.{client}.uri}}")\n  private String backendBaseUrl;\n\n'
                 f'  @Value("${{application.http.restclient.{client}.test-harness-host}}")\n  private String testHarnessBaseUrl;\n\n')
   call_args='backendBaseUrl, testHarnessBaseUrl, '
   call_method='call'+vcls
  else:
   call_method='call'+c+'Backend'
  return (f'package {p}.service;\n\n'
          f'import com.cvshealth.eip.microservices.frameworks.eieheader.EieHeader;\n'
          f'import com.cvshealth.eip.microservices.frameworks.logging.EIPMicroserviceLogger;\n'
          f'import {p}.connector.{connector_name};\n{extra_imports}'
          f'import {p}.util.{c}Util;\nimport lombok.RequiredArgsConstructor;\n'
          f'import org.springframework.stereotype.Service;\n\n@Service\n@RequiredArgsConstructor\npublic class {c}Service {{\n\n'
          f'  private final EIPMicroserviceLogger logger;\n  private final {connector_name} connector;\n'
          f'  private final EieHeader eieHeader;\n  private final {c}Util util;\n{extra_fields}'
          f'  public Object get{c}(String id) {{\n'
          f'{self._protected_body(f"return connector.{call_method}({call_args}id);",sources)}'
          f'  }}\n}}\n')

 def _connector(self,p,c,connector_name,vendor,sources):
  if vendor:
   # @EIPMicroserviceProviderCall + RestClientWrapper.executeRestCall, the
   # real shape confirmed against MedForceConnector.java -- but
   # EIPMicroserviceProvider.<CONST> only compiles if that constant already
   # exists in the framework-restclient jar's own enum, which this generator
   # cannot see or add to, hence the flagged NOTE rather than a silent guess.
   vcls=self._vendor_cls(vendor);const=self._provider_const(vendor)
   body=(f'return restClientWrapper.executeRestCall(RestClientRequest.builder().clientName({c}Constants.REST_CLIENT_NAME)'
         f'.url(url).testHarnessUrl(testHarnessUrl).methodType(RequestMethod.GET).build(), Object.class);')
   return (f'package {p}.connector;\n\n'
           f'import {p}.constants.{c}Constants;\n'
           f'import com.cvshealth.eip.microservices.frameworks.annotations.EIPMicroserviceProvider;\n'
           f'import com.cvshealth.eip.microservices.frameworks.annotations.EIPMicroserviceProviderCall;\n'
           f'import com.cvshealth.eip.microservices.frameworks.logging.EIPMicroserviceLogger;\n'
           f'import com.cvshealth.eip.microservices.frameworks.restclient.model.RestClientRequest;\n'
           f'import com.cvshealth.eip.microservices.frameworks.restclient.service.RestClientWrapper;\n'
           f'import lombok.RequiredArgsConstructor;\nimport org.springframework.http.ResponseEntity;\n'
           f'import org.springframework.stereotype.Component;\nimport org.springframework.web.bind.annotation.RequestMethod;\n\n'
           f'// NOTE: EIPMicroserviceProvider.{const} must already exist in the framework-restclient enum for\n'
           f'// this to compile -- if "{vendor}" is a new vendor, get that constant added there first.\n'
           f'@Component\n@RequiredArgsConstructor\npublic class {connector_name} {{\n\n'
           f'  private final RestClientWrapper restClientWrapper;\n  private final EIPMicroserviceLogger log;\n\n'
           f'  @EIPMicroserviceProviderCall(provider = EIPMicroserviceProvider.{const}, function = {c}Constants.{const}_FUNCTION_NAME)\n'
           f'  public ResponseEntity<Object> call{vcls}(String url, String testHarnessUrl, String id) {{\n'
           f'{self._protected_body(body,sources)}'
           f'  }}\n}}\n')
  return (f'package {p}.connector;\n\n'
          f'import com.cvshealth.eip.microservices.frameworks.restclient.service.RestClientWrapper;\n'
          f'import lombok.RequiredArgsConstructor;\nimport org.springframework.stereotype.Component;\n\n'
          f'@Component\n@RequiredArgsConstructor\npublic class {connector_name} {{\n\n'
          f'  private final RestClientWrapper restClientWrapper;\n\n'
          f'  public Object call{c}Backend(String id) {{\n'
          f'{self._protected_body("return null;",sources)}'
          f'  }}\n}}\n')

 def _util(self,p,c):
  return f'package {p}.util;\n\nimport org.springframework.stereotype.Component;\n\n@Component\npublic class {c}Util {{\n}}\n'

 def _constants(self,p,c,vendor):
  # One merged class, package "constants" (plural) -- confirmed against the
  # real DmrClaimStatusConstants.java, which is a single class, not split
  # into a generic Constants.java plus a per-connector *ConnectorConstants.
  vendor_consts=''
  if vendor:
   const=self._provider_const(vendor)
   vendor_consts=(f'  public static final String REST_CLIENT_NAME = "{vendor.lower()}";\n'
                  f'  public static final String {const}_FUNCTION_NAME = "{c}";\n\n')
  return (f'package {p}.constants;\n\npublic final class {c}Constants {{\n  private {c}Constants() {{ }}\n\n'
          f'{vendor_consts}'
          f'  public static final String HEADER_CONTENT_TYPE = "Content-Type";\n'
          f'  public static final String HEADER_CONTENT_TYPE_VALUE = "application/json";\n}}\n')

 def _schema_model(self,p,name,schema):
  fields=', '.join('String '+(d or 'field%d'%i) for i,d in enumerate(schema.get('declarations') or ['value'],1))
  return (f'package {p}.swagger.model.response;\n\nimport com.fasterxml.jackson.annotation.JsonInclude;\n\n'
          f'@JsonInclude(JsonInclude.Include.NON_NULL)\npublic record {name}({fields}) {{\n}}\n')

 def _error_response(self,p):
  # httpCode/httpMessage/moreInformation, @AllArgsConstructor+@Builder --
  # confirmed against the real ErrorResponse.java; identical for every
  # service, nothing here is business-specific.
  return (f'package {p}.swagger.model.response;\n\nimport com.fasterxml.jackson.annotation.JsonProperty;\n'
          f'import lombok.AllArgsConstructor;\nimport lombok.Builder;\nimport lombok.Data;\nimport lombok.NoArgsConstructor;\n\n'
          f'import java.io.Serializable;\n\n@Data\n@Builder\n@NoArgsConstructor\n@AllArgsConstructor\n'
          f'public class ErrorResponse implements Serializable {{\n\n'
          f'  private static final long serialVersionUID = 1L;\n\n'
          f'  @JsonProperty("httpCode")\n  private String httpCode;\n\n'
          f'  @JsonProperty("httpMessage")\n  private String httpMessage;\n\n'
          f'  @JsonProperty("moreInformation")\n  private String moreInformation;\n}}\n')

 def _exception_handler(self,p,c):
  # @RestControllerAdvice catching Spring's own NoResourceFoundException --
  # confirmed against the real DmrClaimStatusExceptionHandler.java. This is
  # the one handler every such service has regardless of its own business
  # exceptions, which are not something this generator can know about or
  # safely invent.
  return (f'package {p}.exception;\n\nimport {p}.swagger.model.response.ErrorResponse;\n'
          f'import org.springframework.http.HttpStatus;\nimport org.springframework.http.ResponseEntity;\n'
          f'import org.springframework.web.bind.annotation.ExceptionHandler;\n'
          f'import org.springframework.web.bind.annotation.RestControllerAdvice;\n'
          f'import org.springframework.web.servlet.resource.NoResourceFoundException;\n\n'
          f'@RestControllerAdvice\npublic class {c}ExceptionHandler {{\n\n'
          f'  @ExceptionHandler(NoResourceFoundException.class)\n'
          f'  public ResponseEntity<ErrorResponse> handleNoResourceFound(NoResourceFoundException ex) {{\n'
          f'    return ResponseEntity.status(HttpStatus.NOT_FOUND)\n'
          f'        .body(new ErrorResponse(String.valueOf(HttpStatus.NOT_FOUND.value()), '
          f'HttpStatus.NOT_FOUND.getReasonPhrase(), "No resources match requested URI"));\n  }}\n}}\n')

 def _empty_test(self,p,c):
  # Rule 11: every generated JUnit test class is an empty skeleton -- no
  # @Test methods, assertions or mocks, until iib-ms-testing populates them.
  return f'package {p};\n\nclass {c}Test {{\n}}\n'

 # -- fallback for backend types without their own layout yet --------------
 def _fallback_sources(self,files,root,service,package,model,plan_id):
  pkg=root/'src/main/java'/Path(package.replace('.','/'));test=root/'src/test/java'/Path(package.replace('.','/'))
  files[pkg/(self._cls(service)+'Application.java')]=self._app(package,service)
  for flow in model.get('flows',[])+model.get('subflows',[]):
   cls=self._cls(Path(flow['path']).stem)+'Flow'
   files[pkg/(cls+'.java')]=self._flow(package,cls,flow,plan_id)
   files[test/(cls+'Test.java')]=self._empty_test(package,cls)

 def _flow(self,p,c,f,plan):return f'package {p};\nimport org.springframework.stereotype.Service;\n/** provenance plan={plan} source={f["path"]} */\n@Service public final class {c} {{ public Object process(Object input) {{ return input; }} }}\n'
 #: Word-boundary tokenizer for _cls -- ACE service names arrive already in
 #: PascalCase with no separators (e.g. "DMRClaimStatus", "GetGrievancesAppeal"),
 #: so splitting only on non-alnum (the old behaviour) left each of those as
 #: one single token, and ''.capitalize() on a whole token *lowercases* every
 #: letter after the first -- "DMRClaimStatus" became "Dmrclaimstatus"
 #: (confirmed live: this, not the application.properties YAML bug, is what
 #: actually produced the wrong class/service name the user reported). This
 #: pattern finds camelCase/acronym word boundaries within a token too, so
 #: "DMRClaimStatus" tokenizes as ["DMR","Claim","Status"] and each piece's
 #: own .capitalize() ("Dmr","Claim","Status") reproduces the real reference's
 #: own "DmrClaimStatus" convention (acronyms get title-cased like any other
 #: word, matching normal Java naming style).
 _WORD=re.compile(r'[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+')
 def _cls(self,x):
  return ''.join(w.capitalize() for part in re.split(r'[^A-Za-z0-9]+',x) if part for w in self._WORD.findall(part))

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
