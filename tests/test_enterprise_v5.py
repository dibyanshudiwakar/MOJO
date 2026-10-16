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

def test_rest_generation_produces_the_layered_package_structure(tmp_path):
 model={'flows':[{'path':'A.msgflow'}],'subflows':[],'schemas':[],
       'interfaces':[{'protocol':'HTTP','node':'n1','endpoint':'/pbmclaim/api/v1/edb/orders','source':'A.msgflow'}],
       'backend_type':{'type':'REST_EXTERNAL_DIRECT','signal':'test'}}
 tasks=[{'source_reference':'A.msgflow#n2','target_module':'service'},
        {'source_reference':'A.msgflow#n3','target_module':'client'}]
 g=SpringProjectGenerator().generate(tmp_path,'orders','com.example.orders',model,'P1',{},tasks=tasks)
 root=Path(g['root'])
 web=root/'cvs-v1-orders-oci-web'/'src/main/java/com/example/orders'
 controller=(web/'controller/OrdersController.java').read_text()
 service=(web/'service/OrdersService.java').read_text()
 connector=(web/'connector/OrdersConnectorService.java').read_text()
 api=(web/'swagger/api/OrdersApi.java').read_text()
 assert 'implements OrdersApi' in controller and 'OrdersService service' in controller
 assert 'OrdersConnectorService connector' in service and 'A.msgflow#n2' in service
 assert 'A.msgflow#n3' in connector
 assert '/pbmclaim/api/v1/cs/orders' in api
 assert (root/'pom.xml').exists() and (root/'Dockerfile').exists()
 assert (root/'config/deploy-config/Chart.yaml').exists()
 assert (root/'config/config-map/application.dev-pbmaz.properties').exists()
