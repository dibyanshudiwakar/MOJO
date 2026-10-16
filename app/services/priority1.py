import csv, io, json, yaml
from pathlib import Path
from app.repository import uid, now, digest
from app.services.enterprise_v5 import ACEParser, FrameworkConventionExtractor

SUPPORTED_NODE_TOKENS={
 'httpinput','httpreply','soapinput','soapreply','mqinput','mqoutput','kafkaconsumer','kafkaproducer',
 'fileinput','fileoutput','computenode','compute','filter','route','database','timeoutnotification',
 'catch','throw','mapping','xslt'
}

class AnalysisSpecificationConsistencyValidator:
 def validate(self,model,spec):
  if isinstance(model,str):model=json.loads(model)
  if isinstance(spec,str):spec=yaml.safe_load(spec)
  findings=[]
  def names(items):
   out=set()
   for x in items or []:
    if isinstance(x,str):out.add(x)
    elif isinstance(x,dict):out.add(str(x.get('path') or x.get('name') or x.get('id') or ''))
   return {x for x in out if x}
  pairs=[('flows',names(model.get('flows')),names(spec.get('source',{}).get('flows'))),
         ('esql',names(model.get('esql')),names(spec.get('source',{}).get('esql'))),
         ('maps',names(model.get('maps')),names(spec.get('source',{}).get('maps'))),
         ('schemas',names(model.get('schemas')),names(spec.get('source',{}).get('schemas'))),
         ('policies',names(model.get('policies')),names(spec.get('source',{}).get('policies')))]
  for area,left,right in pairs:
   for x in sorted(left-right):findings.append({'severity':'HIGH','type':'ANALYSIS_ONLY','area':area,'reference':x})
   for x in sorted(right-left):findings.append({'severity':'HIGH','type':'YAML_ONLY','area':area,'reference':x})
  model_clar={str(x.get('question')) for x in model.get('ambiguities',[]) if isinstance(x,dict)}
  yaml_clar={str(x.get('question')) for x in spec.get('clarifications',[]) if isinstance(x,dict)}
  for x in sorted(model_clar-yaml_clar):findings.append({'severity':'HIGH','type':'MISSING_CLARIFICATION','area':'clarifications','reference':x})
  return {'status':'PASS' if not findings else 'FAIL','finding_count':len(findings),'findings':findings,'analysis_hash':digest(json.dumps(model,sort_keys=True)),'specification_hash':digest(yaml.safe_dump(spec,sort_keys=True))}

class PriorityOneService:
 REQUIRED_AREAS=('flows','subflows','esql','maps','schemas','policies','interfaces','dependencies')
 def __init__(self,repo):self.repo=repo;self.parser=ACEParser();self.framework=FrameworkConventionExtractor();self.consistency=AnalysisSpecificationConsistencyValidator()
 def _job(self,job_id):
  j=self.repo.one('select * from jobs where id=?',(job_id,))
  if not j:raise ValueError('job_not_found')
  return j
 def compute_semantic_model(self,path):
  """The deterministic parse: the fallback when the model cannot read the source.

  Separated from recording so both paths persist through one code path. The
  model-authored route calls ``ACEParser.sources``/``merge`` instead and hands
  the result to ``record_analysis``, which hashes it identically.
  """
  return self.parser.parse(path)
 def analyze_ace(self,job_id,path,version='1.0.0'):
  """Deterministic end to end, for callers with no agent context to reason with."""
  self._job(job_id);return self.record_analysis(job_id,self.compute_semantic_model(path),version)
 def record_analysis(self,job_id,model,version='1.0.0'):
  """Persist a semantic model, however it was produced.

  Everything downstream -- the content hash a G2 approval binds, the coverage
  report, the unsupported-construct rows -- is derived here from the model
  itself, so a model-authored analysis and a parsed one are indistinguishable
  to the gate and to the next agent.
  """
  self._job(job_id);unsupported=[]
  for flow in model.get('flows',[])+model.get('subflows',[]):
   for node in flow.get('nodes',[]):
    typ=str(node.get('type',''));normal=''.join(c for c in typ.lower() if c.isalnum())
    if 'custom' in normal or 'vendor' in normal or not any(t in normal for t in SUPPORTED_NODE_TOKENS):
     unsupported.append({'id':uid(),'source_path':flow.get('path',''),'node_id':node.get('id',''),'construct_type':typ or 'UNKNOWN','classification':'UNSUPPORTED','impact':'Manual mapping or plug-in development required','status':'OPEN','owner':'ACE_SME','evidence_ref':flow.get('path','')+'#'+str(node.get('id',''))})
  coverage=[]
  for area in self.REQUIRED_AREAS:
   count=len(model.get(area,[]));coverage.append({'area':area,'detected':count,'status':'COVERED' if count else 'NOT_DETECTED'})
  score=round(100*sum(x['status']=='COVERED' for x in coverage)/len(coverage),2)
  analysis_md=self._analysis_md(version,model,coverage,unsupported)
  analysis_id=uid();raw=json.dumps(model,sort_keys=True);ts=now()
  with self.repo.connect() as c:
   c.execute('insert into ace_analyses values(?,?,?,?,?,?,?,?,?)',(analysis_id,job_id,version,raw,analysis_md,json.dumps(model.get('ambiguities',[])),json.dumps(model.get('dependencies',[])),digest(raw),ts))
   for x in unsupported:c.execute('insert into unsupported_constructs values(?,?,?,?,?,?,?,?,?,?,?)',(x['id'],job_id,analysis_id,x['source_path'],x['node_id'],x['construct_type'],x['classification'],x['impact'],x['status'],x['owner'],x['evidence_ref']))
   # Clarification blocking is disabled — ambiguities are stored in the
   # analysis record itself but not inserted as open clarifications.
   report_id=uid();c.execute('insert into validation_reports values(?,?,?,?,?,?,?,?,?)',(report_id,job_id,'PARSER_COVERAGE',version,json.dumps({'score':score,'items':coverage}),digest(json.dumps(coverage,sort_keys=True)), 'PASS' if score==100 else 'REVIEW_REQUIRED',ts,analysis_id))
  return {'analysis_id':analysis_id,'coverage_report_id':report_id,'coverage_score':score,'unsupported_count':len(unsupported),'ambiguity_count':len(model.get('ambiguities',[])),'status':'REVIEW_REQUIRED'}
 def validate_specification(self,job_id,analysis_id,specification_id):
  a=self.repo.one('select * from ace_analyses where id=? and job_id=?',(analysis_id,job_id));s=self.repo.one('select * from migration_specs where id=? and job_id=?',(specification_id,job_id))
  if not a or not s:raise ValueError('analysis_or_specification_not_found')
  result=self.consistency.validate(a['semantic_model'],s['yaml_text']);rid=uid()
  with self.repo.connect() as c:c.execute('insert into validation_reports values(?,?,?,?,?,?,?,?,?)',(rid,job_id,'ANALYSIS_YAML_CONSISTENCY',s['version'],json.dumps(result),digest(json.dumps(result,sort_keys=True)),result['status'],now(),analysis_id))
  return {'report_id':rid,**result}
 def compute_framework(self,path,version='1.0.0',effective_from=None):
  """The deterministic extraction: the fallback."""
  return self.framework.extract(path,version,effective_from)
 def baseline_framework(self,job_id,path,version='1.0.0',effective_from=None,golden_path=None):
  """Deterministic end to end, for callers with no agent context to reason with."""
  self._job(job_id);return self.record_framework(job_id,self.compute_framework(path,version,effective_from),version,golden_path or path)
 def record_framework(self,job_id,result,version='1.0.0',golden_path=None):
  """Persist a framework baseline, however its standards were read.

  The golden verification stays here and stays deterministic: it is a set
  comparison between required and found dependencies, and it is the evidence
  the G1 approval rests on.
  """
  self._job(job_id);verify=self.framework.verify_golden(golden_path,result) if golden_path else {'status':'PASS','missingDependencies':[],'conflicts':[]};bid=uid();ts=now()
  with self.repo.connect() as c:
   c.execute('insert into framework_baselines values(?,?,?,?,?,?,?,?,?)',(bid,job_id,version,result['template_md'],json.dumps(result['standards']),json.dumps(result['conflicts']),'PENDING_APPROVAL',result['content_hash'],ts))
   rid=uid();payload={'standards':result['standards'],'conflicts':result['conflicts'],'golden_verification':verify};c.execute('insert into validation_reports values(?,?,?,?,?,?,?,?,?)',(rid,job_id,'FRAMEWORK_EXTRACTION',version,json.dumps(payload),digest(json.dumps(payload,sort_keys=True)),verify['status'],ts,bid))
  return {'framework_id':bid,'report_id':rid,'version':version,'conflicts':result['conflicts'],'golden_verification':verify,'status':'PENDING_APPROVAL'}
 def approval_package(self,job_id,gate,analysis_id=None,specification_id=None,framework_id=None):
  if gate not in ('G1','G2'):raise ValueError('priority1_gate_required')
  j=self._job(job_id);items=[];conditions=[]
  if gate=='G1':
   f=self.repo.one('select * from framework_baselines where id=? and job_id=?',(framework_id,job_id))
   if not f:raise ValueError('framework_not_found')
   items=[{'kind':'FRAMEWORK_TEMPLATE','id':f['id'],'version':f['version'],'hash':f['content_hash']}]
   if json.loads(f['conflicts'] or '[]'):conditions.append('Resolve or explicitly accept all framework conflicts')
   review={'framework':f,'reports':self.repo.rows("select * from validation_reports where job_id=? and reference_id=?",(job_id,framework_id))}
  else:
   a=self.repo.one('select * from ace_analyses where id=? and job_id=?',(analysis_id,job_id));s=self.repo.one('select * from migration_specs where id=? and job_id=?',(specification_id,job_id))
   if not a or not s:raise ValueError('analysis_or_specification_not_found')
   items=[{'kind':'ACE_ANALYSIS','id':a['id'],'version':a['version'],'hash':a['content_hash']},{'kind':'MIGRATION_SPECIFICATION','id':s['id'],'version':s['version'],'hash':s['content_hash']}]
   unsupported=self.repo.one("select count(*) n from unsupported_constructs where job_id=? and status='OPEN'",(job_id,))['n']
   if unsupported:conditions.append('Resolve or accept unsupported constructs through governed policy')
   review={'analysis':a,'specification':s,'coverage':self.repo.rows("select * from validation_reports where job_id=? and report_type='PARSER_COVERAGE'",(job_id,)),'consistency':self.repo.rows("select * from validation_reports where job_id=? and report_type='ANALYSIS_YAML_CONSISTENCY'",(job_id,)),'unsupported':self.repo.rows('select * from unsupported_constructs where job_id=?',(job_id,)),'clarifications':self.repo.rows('select * from clarifications where job_id=?',(job_id,))}
  package={'gate':gate,'job':{'id':j['id'],'service':j['service'],'source_revision':j['source_revision']},'artefacts':items,'conditions':conditions,'review':review,'decision':'PENDING_APPROVAL','rollback_pointer':j.get('rollback_pointer') or '','created_at':now()};pid=uid();raw=json.dumps(package,sort_keys=True,default=str)
  with self.repo.connect() as c:c.execute('insert into approval_packages values(?,?,?,?,?,?,?,?,?)',(pid,job_id,gate,'PENDING_APPROVAL',raw,digest(raw),json.dumps(items),json.dumps(conditions),now()))
  return {'package_id':pid,'gate':gate,'status':'PENDING_APPROVAL','conditions':conditions,'content_hash':digest(raw)}
 def _analysis_md(self,version,m,c,u):
  lines=[f'# ACE Analysis {version}','',f'- Flows: {len(m.get("flows",[]))}',f'- Subflows: {len(m.get("subflows",[]))}',f'- ESQL: {len(m.get("esql",[]))}',f'- Interfaces: {len(m.get("interfaces",[]))}',f'- Dependencies: {len(m.get("dependencies",[]))}',f'- Ambiguities: {len(m.get("ambiguities",[]))}',f'- Unsupported constructs: {len(u)}','','## Coverage']
  lines += [f'- {x["area"]}: {x["status"]} ({x["detected"]})' for x in c]
  return '\n'.join(lines)+'\n'
