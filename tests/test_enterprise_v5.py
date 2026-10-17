import json
from pathlib import Path
from app.services.enterprise_v5 import ACEParser,FrameworkConventionExtractor,SpringProjectGenerator,DifferentialEvidenceEngine,ConfidenceRemediationService

def test_ace_parser_semantics(tmp_path):
 (tmp_path/'A.msgflow').write_text('<flow><node id="n1" type="MQInput" queueName="Q.IN"/><node id="n2" type="ComputeNode"/><connection source="n1" target="n2"/></flow>')
 (tmp_path/'A.esql').write_text('CREATE COMPUTE MODULE M; DECLARE C SHARED CHARACTER; CREATE PROCEDURE P() BEGIN PROPAGATE; END;')
 m=ACEParser().parse(tmp_path);assert m['flows'][0]['connections'];assert m['interfaces'][0]['protocol']=='MQ';assert m['esql'][0]['sharedVariables']

def test_framework_extract_and_golden(tmp_path):
 (tmp_path/'pom.xml').write_text('<project><parent><artifactId>spring-boot-starter-parent</artifactId><version>3.3.0</version></parent><properties><java.version>21</java.version></properties><dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>')
 src=tmp_path/'src/main/java';src.mkdir(parents=True);(src/'A.java').write_text('@RestController class A { SecurityFilterChain c; MeterRegistry m; LoggerFactory l; }')
 x=FrameworkConventionExtractor().extract(tmp_path);assert x['standards']['build']['tool']=='MAVEN';assert x['standards']['security'];assert 'Framework Template' in x['template_md']

def test_generation_diff_and_rca(tmp_path):
 model={'flows':[{'path':'A.msgflow'}],'subflows':[]};fw={'standards':{'build':{'springBootVersion':'3.3.0','javaVersion':'21'}}};g=SpringProjectGenerator().generate(tmp_path,'orders','com.example.orders',model,'P1',fw);assert Path(g['root'],'pom.xml').exists();assert g['manifest']
 d=DifferentialEvidenceEngine().compare('C1',{'a':1,'secret':'x','items':[1,2]},{'a':1.1,'secret':'y','items':[2,1]},{'masks':['$.secret'],'numeric_tolerance':.2,'ordering':{'$.items':False}});assert d['status']=='PASS'
 r=ConfidenceRemediationService();c=r.score(1,1,1,1,1,1);e=r.eligibility(c['score'],.8,True,True,True);assert e['eligible'];assert r.propose({'path':'$.a'},e,1,'tag/v1')['status']=='PROPOSED'

def test_backend_type_detection_rest_vs_soap(tmp_path):
 # ComIbmWSRequest to an external URL, JSON domain, no QL/RxClaim/MQ signal.
 (tmp_path/'A.msgflow').write_text(
  '<flow><node id="n1" type="ComIbmWSRequest.msgnode" URLSpecifier="https://backend/x" messageDomainProperty="JSON"/></flow>')
 model=ACEParser().parse(tmp_path)
 assert model['backend_type']['type']=='REST_EXTERNAL_DIRECT'

def test_backend_type_detection_soap_envelope(tmp_path):
 (tmp_path/'B.msgflow').write_text(
  '<flow><node id="n1" type="ComIbmWSRequest.msgnode" URLSpecifier="https://backend/x" messageDomainProperty="XMLNSC"/></flow>')
 model=ACEParser().parse(tmp_path)
 assert model['backend_type']['type']=='SOAP'

def test_merge_backfills_a_model_authored_ambiguity_missing_status():
 # agent/ace-analysis/semantic-model's own response schema requires only
 # 'question' on an ambiguity -- confirmed live against a real run that
 # authored 84 with no 'status' at all. migration-specification's
 # clarifications schema requires status/owner/impact/evidenceReference too,
 # so finalise() (merge's own tail call) must backfill them regardless of
 # which path produced the fragment.
 fragment={'ambiguities':[{'question':'q only, no status'}]}
 model=ACEParser().merge([fragment])
 a=model['ambiguities'][0]
 assert a=={'question':'q only, no status','status':'OPEN','owner':'ACE_SME',
           'impact':'','evidenceReference':''}
