import json,tempfile
from pathlib import Path
from app.repository import Repository
from app.service import FactoryService
from app.services.priority1 import PriorityOneService
from app.services.specification import SpecificationService

def setup():
 d=tempfile.TemporaryDirectory();r=Repository(str(Path(d.name)/'p1.db'));f=FactoryService(r);j=f.create_job({'service':'Order','owner':'Team','sourceRevision':'abc','rollbackPointer':'tag/v1'},'tester');return d,r,j

def write_repo(root):
 ace=root/'ace';ace.mkdir();(ace/'Order.msgflow').write_text('<flow><node id="n1" type="MQInput" queueName="Q.IN"/><node id="n2" type="VendorCustomNode"/><connection source="n1" target="n2"/></flow>');(ace/'Order.esql').write_text('CREATE COMPUTE MODULE M; DECLARE C SHARED CHARACTER;');(ace/'Order.xsd').write_text('<schema><element name="Order"/></schema>');return ace

def test_priority1_ace_reports_and_g2_package(tmp_path):
 d,r,j=setup();ace=write_repo(tmp_path);svc=PriorityOneService(r);a=svc.analyze_ace(j['id'],ace);assert a['unsupported_count']==1;assert r.one('select count(*) n from unsupported_constructs')['n']==1
 spec=SpecificationService(r,Path(__file__).parents[1]/'schemas/migration-specification.schema.json');text=(Path(__file__).parents[1]/'examples/migration-specification.yaml').read_text().replace('flows: []','flows: [Order.msgflow]').replace('esql: []','esql: [Order.esql]').replace('schemas: []','schemas: [Order.xsd]');sp=spec.save(j['id'],'1',text);assert sp['status']=='VALID'
 report=svc.validate_specification(j['id'],a['analysis_id'],sp['id']);assert report['status'] in ('PASS','FAIL')
 pack=svc.approval_package(j['id'],'G2',analysis_id=a['analysis_id'],specification_id=sp['id']);assert pack['status']=='PENDING_APPROVAL' and pack['conditions'];d.cleanup()

def test_priority1_framework_and_g1_package(tmp_path):
 d,r,j=setup();fw=tmp_path/'fw';fw.mkdir();(fw/'pom.xml').write_text('<project><parent><artifactId>spring-boot-starter-parent</artifactId><version>3.3.0</version></parent><dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>');src=fw/'src/main/java';src.mkdir(parents=True);(src/'A.java').write_text('@RestController class A { SecurityFilterChain security; MeterRegistry metrics; LoggerFactory log; }')
 svc=PriorityOneService(r);b=svc.baseline_framework(j['id'],fw);assert b['status']=='PENDING_APPROVAL';pack=svc.approval_package(j['id'],'G1',framework_id=b['framework_id']);assert pack['gate']=='G1';d.cleanup()
