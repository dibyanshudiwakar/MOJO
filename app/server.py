import json, os, re, sys, threading, time, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from app.repository import Repository, uid, now, digest
from app.services import learning
from app.service import FactoryService
from app.services.clarification_reuse import settle_siblings
from app.services.evidence import EvidenceService
from app.services.specification import SpecificationService
from app.services.differential import DifferentialService
from app.services.rca import RCAService
from app.services.agents import AgentRegistry
from app.services.connectors import ConnectorRegistry
from app.services.auth import IdentityService
from app.services.accounts import AccountService
from app.services.github_integration import GitHubIntegrationService
from app.services.repo_clone import RepoCloneService
from app.services.pipelines import PipelineService, PipelineError
from app.services.agent_admin import AgentAdminService, AgentAdminError
from app.services.agent_llm_bindings import AgentLLMBindingService, AgentLLMBindingError
from app.services.agent_templates import AgentTemplateService
from app.services import static_check_reports, workspace
from app.services.priority1 import PriorityOneService
from app.services.migration_lifecycle_v6 import MappingAndPlanningService
from app.services.platform_v7 import RegistryService,AIRegistryService,KnowledgeService,ApprovalWorkspaceService,DeliveryAutomationService,NotificationService,ObservabilityService,SkillRegistryService
from app.services.factory_v3 import WorkflowOrchestrator,IntakeService,DormancyService,FrameworkService,ACEAnalysisService,PlanningGenerationService,AuditLedger,SecurityService
from app.services.enterprise_v5 import ConfidenceRemediationService

BASE=Path(__file__).parent
ROOT=BASE.parent
repo=Repository(os.getenv('FACTORY_DB',str(ROOT/'factory-v2.db')))
factory=FactoryService(repo)
evidence=EvidenceService(repo)
specs=SpecificationService(repo,ROOT/'schemas/migration-specification.schema.json')
diff=DifferentialService(repo)
rca=RCAService(repo)
agents=AgentRegistry(repo)
workflow=WorkflowOrchestrator(repo); intake=IntakeService(repo); dormancy=DormancyService(repo); frameworks=FrameworkService(repo); analysis=ACEAnalysisService(repo); planning=PlanningGenerationService(repo); ledger=AuditLedger(repo); security=SecurityService(repo); connectors=ConnectorRegistry(repo); identity=IdentityService(); priority1=PriorityOneService(repo); lifecycle6=MappingAndPlanningService(repo); registry7=RegistryService(repo); ai7=AIRegistryService(repo); knowledge7=KnowledgeService(repo); approvals7=ApprovalWorkspaceService(repo); delivery7=DeliveryAutomationService(repo); notify7=NotificationService(repo); observe7=ObservabilityService(repo); skill7=SkillRegistryService(repo); confidence=ConfidenceRemediationService(); accounts=AccountService(repo,identity);accounts.prune_retired_roles(); github=GitHubIntegrationService(repo)
pipelines=PipelineService(repo); repo_clone=RepoCloneService(repo); agent_admin=AgentAdminService(repo); llm_bindings=AgentLLMBindingService(repo); templates_svc=AgentTemplateService(repo)
# Registration and sign-in cannot require a token, since obtaining one is what
# they are for. Everything else under /api/ stays authenticated.
PUBLIC_ROUTES={'/api/v2/health','/api/v2/auth/signup','/api/v2/auth/login','/api/v2/auth/roles'}
# Sign-in is the one unauthenticated endpoint that does real work, so it is the
# one place a caller can guess at. Per-address throttling bounds that.
THROTTLE={}; THROTTLE_LOCK=threading.Lock(); THROTTLE_MAX=10; THROTTLE_WINDOW=60
def throttle(key):
 cutoff=time.time()-THROTTLE_WINDOW
 with THROTTLE_LOCK:
  hits=[t for t in THROTTLE.get(key,()) if t>cutoff]
  if len(hits)>=THROTTLE_MAX:
   THROTTLE[key]=hits;raise PermissionError('too_many_attempts_retry_shortly')
  hits.append(time.time());THROTTLE[key]=hits
  if len(THROTTLE)>4096:
   for k in [k for k,v in THROTTLE.items() if not [t for t in v if t>cutoff]]:THROTTLE.pop(k,None)
# The remediation confidence threshold is governed configuration, not a request
# parameter -- a caller must not be able to lower the bar it is judged against.
CONFIDENCE_THRESHOLD=float(os.getenv('FACTORY_REMEDIATION_CONFIDENCE_THRESHOLD','0.8'))
try: connectors.load(ROOT/'config'/'integrations.json')
except FileNotFoundError: pass
TABLES={'jobs':'jobs','approvals':'approvals','evidence':'evidence','agents':'agent_runs','agent-registry':'agent_registry','specifications':'migration_specs','comparisons':'comparisons','variances':'variances','rca':'root_causes','remediations':'remediations','events':'workflow_events','versions':'versions','clarifications':'clarifications','checkpoints':'workflow_checkpoints','queue':'workflow_queue','intakes':'intake_manifests','dormancy':'dormancy_assessments','frameworks':'framework_baselines','analyses':'ace_analyses','plans':'migration_plans','generated':'generated_artifacts','audit-ledger':'audit_ledger','security-findings':'security_findings','metrics':'metrics','connectors':'connector_profiles','connector-operations':'connector_operations','routing-policies':'routing_policies','unsupported-constructs':'unsupported_constructs','validation-reports':'validation_reports','approval-packages':'approval_packages','mapping-catalogues':'mapping_catalogues','unsupported-mappings':'unsupported_mappings','migration-risks':'migration_risks','quality-runs':'quality_runs','differential-packages':'differential_packages','differential-evidence':'differential_evidence','service-registry':'service_registry','prompt-registry':'prompt_registry','model-registry':'model_registry','model-requests':'model_requests','knowledge-index':'knowledge_index','gate-controls':'gate_controls','delivery-operations':'delivery_operations','notifications':'notifications','observability-events':'observability_events','retention-policies':'retention_policies','skill-registry':'skills','skill-runtimes':'skill_runtimes','skill-agent-bindings':'skill_agent_bindings'}

#: Syntax highlighting hint per generated file. Everything used to be labelled
#: java/xml/yaml, so a Dockerfile, a README and a .properties file all rendered
#: as YAML.
_LANGUAGES = {'.java': 'java', '.xml': 'xml', '.yml': 'yaml', '.yaml': 'yaml',
              '.properties': 'properties', '.md': 'markdown', '.json': 'json',
              '.sql': 'sql', '.sh': 'bash'}


def _language(path):
    import posixpath
    name = posixpath.basename(path.replace('\\', '/'))
    if name == 'Dockerfile':
        return 'dockerfile'
    return _LANGUAGES.get('.' + name.rsplit('.', 1)[-1] if '.' in name else '', 'text')


def _resolve_agent_artefacts(r, agent_id, outputs, job_id):
    """Resolve an agent's output IDs to displayable content from service tables.

    Each agent writes its artefacts to specific service tables and returns the
    row IDs in ``AgentResult.outputs``.  This function looks up the actual
    content so the UI can render it formatted (markdown, YAML, source code,
    structured tables) instead of showing only raw IDs.
    """
    a = {}
    try:
        if agent_id == 'repository-intake':
            mid = outputs.get('intake_manifest', '')
            if mid:
                row = r.one('select inventory,gaps,repository_uri,branch,revision from intake_manifests where id=?', (mid,))
                if row:
                    inv = json.loads(row['inventory'] or '{}')
                    a = {'title': 'Repository Intake Report', 'format': 'report',
                         'sections': [
                             {'heading': 'Repository', 'content': '%s @ %s (%s)' % (row['repository_uri'], row['branch'], row['revision'] or 'HEAD')},
                             {'heading': 'Summary', 'content': '%d files · %d flows · %d ESQL · %d schemas · %d maps' % (
                                 len(inv.get('files', [])), len(inv.get('flows', [])), len(inv.get('esql', [])),
                                 len(inv.get('schemas', [])), len(inv.get('maps', [])))},
                             {'heading': 'Gaps', 'items': json.loads(row['gaps'] or '[]')},
                             {'heading': 'Flows', 'items': inv.get('flows', [])},
                             {'heading': 'ESQL Modules', 'items': inv.get('esql', [])},
                             {'heading': 'Schemas', 'items': inv.get('schemas', [])},
                         ]}
        elif agent_id == 'dormancy-intelligence':
            eid = outputs.get('evidence', '')
            if eid:
                row = r.one('select * from dormancy_assessments where id=?', (eid,))
                if row:
                    evidence = json.loads(row['evidence'] or '{}')
                    a = {'title': 'Dormancy Assessment', 'format': 'report',
                         'sections': [
                             {'heading': 'Classification', 'content': row['classification']},
                             {'heading': 'Confidence', 'content': '%.0f%%' % (float(row['confidence'] or 0) * 100)},
                             {'heading': 'Signals', 'table': [
                                 {'signal': 'Traffic count', 'value': str(evidence.get('traffic_count', '—'))},
                                 {'signal': 'Last seen (days)', 'value': str(evidence.get('last_seen_days', '—'))},
                                 {'signal': 'Deployed', 'value': str(evidence.get('deployed', '—'))},
                                 {'signal': 'Already migrated', 'value': str(evidence.get('already_migrated', '—'))},
                             ]},
                             {'heading': 'Rationale', 'content': row['rationale']},
                         ]}
        elif agent_id == 'framework-baseline':
            fid = outputs.get('framework_template', '')
            if fid:
                row = r.one('select template_md,standards,conflicts,version from framework_baselines where id=?', (fid,))
                if row:
                    a = {'title': 'Framework Baseline Template', 'format': 'markdown',
                         'markdown': row['template_md'],
                         'sections': [
                             {'heading': 'Conflicts', 'items': json.loads(row['conflicts'] or '[]')},
                             {'heading': 'Standards', 'json': json.loads(row['standards'] or '{}')},
                         ]}
        elif agent_id == 'ace-analysis':
            aid = outputs.get('analysis_document', '')
            if aid:
                row = r.one('select analysis_md,semantic_model,ambiguities,dependencies from ace_analyses where id=?', (aid,))
                if row:
                    model = json.loads(row['semantic_model'] or '{}')
                    a = {'title': 'ACE Analysis', 'format': 'markdown',
                         'markdown': row['analysis_md'],
                         'sections': [
                             {'heading': 'Ambiguities', 'items': [x.get('question', str(x)) for x in json.loads(row['ambiguities'] or '[]')]},
                             {'heading': 'Semantic Model Summary', 'table': [
                                 {'area': k, 'count': len(model.get(k, []))}
                                 for k in ('flows', 'subflows', 'esql', 'maps', 'schemas', 'policies', 'interfaces')
                             ]},
                         ]}
        elif agent_id == 'migration-specification':
            sid = outputs.get('migration_specification_yaml', '')
            if sid:
                row = r.one('select yaml_text,validation_status,validation_errors from migration_specs where id=?', (sid,))
                if row:
                    a = {'title': 'Migration Specification', 'format': 'yaml',
                         'yaml': row['yaml_text'],
                         'validation_status': row['validation_status'],
                         'validation_errors': json.loads(row['validation_errors'] or '[]')}
        elif agent_id == 'planning':
            pid = outputs.get('plan', '')
            mid = outputs.get('mapping', '')
            if pid:
                row = r.one('select tasks,risks,acceptance from migration_plans where id=?', (pid,))
                if row:
                    tasks = json.loads(row['tasks'] or '[]')
                    a = {'title': 'Migration Plan', 'format': 'plan',
                         'tasks': [{'id': t.get('id'), 'source': t.get('source_reference', ''),
                                    'pattern': t.get('spring_pattern', ''), 'module': t.get('target_module', ''),
                                    'boundary': t.get('boundary', ''), 'tests': t.get('tests', [])}
                                   for t in tasks],
                         'risk_register': outputs.get('risk_register', []),
                         'acceptance': json.loads(row['acceptance'] or '[]')}
            if mid:
                cat = r.one('select mappings from mapping_catalogues where id=?', (mid,))
                if cat:
                    mappings = json.loads(cat['mappings'] or '[]')
                    a.setdefault('title', 'Migration Plan')
                    a['mappings'] = [{'source': m.get('source_reference', ''), 'construct': m.get('ace_construct', ''),
                                      'pattern': m.get('spring_pattern', ''), 'module': m.get('target_module', '')}
                                     for m in mappings]
        elif agent_id == 'code-generation':
            a = {'title': 'Generated Spring Boot Project', 'format': 'code',
                 'files': [], 'quality': None}
            artifacts = r.rows('select path,content,provenance from generated_artifacts where job_id=? order by path', (job_id,))
            a['files'] = [{'path': x['path'], 'content': x['content'],
                           'language': _language(x['path'])} for x in artifacts[:200]]
            a['file_count'] = len(artifacts)
            qr = r.one('select status,payload from quality_runs where job_id=? order by created_at desc limit 1', (job_id,))
            if qr:
                a['quality'] = {'status': qr['status'], 'checks': json.loads(qr['payload'] or '{}').get('checks', [])}
            # What the generator could not derive. Recorded on the run's metrics
            # by the agent, so it is visible beside the code rather than only in
            # the generated README.
            run = r.one("select metrics from agent_runs where job_id=? and agent_id='code-generation'"
                        " and status='COMPLETED' order by created_at desc limit 1", (job_id,))
            if run:
                items = json.loads(run['metrics'] or '{}').get('action_items') or []
                if items:
                    a['sections'] = [{'heading': 'Needs a decision', 'items': items}]
        elif agent_id == 'variance-analysis':
            variances = r.rows('select path,expected,actual,severity,kind,disposition from variances where job_id=? order by severity,path', (job_id,))
            a = {'title': 'Variance Analysis', 'format': 'variances',
                 'items': [{'path': v['path'], 'expected': v['expected'][:200], 'actual': v['actual'][:200],
                            'severity': v['severity'], 'kind': v['kind'], 'disposition': v['disposition']}
                           for v in variances[:100]]}
        elif agent_id == 'rca':
            causes = r.rows('select hypothesis,confidence,affected_scope,status from root_causes where job_id=? order by confidence desc', (job_id,))
            a = {'title': 'Root Cause Analysis', 'format': 'rca',
                 'hypotheses': [{'hypothesis': c['hypothesis'], 'confidence': c['confidence'],
                                 'scope': c['affected_scope'], 'status': c['status']} for c in causes]}
        elif agent_id == 'remediation':
            rems = r.rows('select proposal,approved_pattern,regression_tests,status from remediations where job_id=? order by created_at desc', (job_id,))
            a = {'title': 'Remediation Proposals', 'format': 'remediation',
                 'proposals': [{'proposal': rem['proposal'][:500], 'pattern': rem['approved_pattern'],
                                'tests': json.loads(rem['regression_tests'] or '[]'), 'status': rem['status']}
                               for rem in rems]}
        elif agent_id == 'governance':
            a = {'title': 'Governance Evaluation', 'format': 'governance',
                 'decision': outputs.get('allow_deny_decision', {}),
                 'approval_requirement': outputs.get('approval_requirement', {}),
                 'evidence_requirement': outputs.get('evidence_requirement', {})}
        elif agent_id == 'knowledge-evidence':
            a = {'title': 'Knowledge & Evidence Index', 'format': 'knowledge',
                 'cited_context': outputs.get('cited_context', []),
                 'version_lineage': outputs.get('version_lineage', [])}
        elif agent_id == 'test-engineering':
            pkg_id = outputs.get('test_suites', '')
            if pkg_id:
                row = r.one('select payload from differential_packages where id=?', (pkg_id,))
                if row:
                    payload = json.loads(row['payload'] or '{}')
                    a = {'title': 'Test Engineering — Differential Test Package', 'format': 'test',
                         'cases': payload.get('cases', []),
                         'policy': payload.get('policy', {})}
    except Exception:
        pass
    return a or None


def _enrich_job_risks(r, items):
    """Compute a per-job risk level from signal tables and write it into each item dict.

    Runs 8 batch queries regardless of the number of jobs (never N×8).

    HIGH  — any of: failed agent run, HIGH-severity variance or EXCEPTION disposition,
            open HIGH migration risk, open HIGH security finding.
    MEDIUM — any of: open clarification, open MEDIUM migration risk,
             UNKNOWN dormancy classification, open unsupported mapping.
    LOW   — job has progressed past CREATED with no HIGH or MEDIUM signals.
    (no value) — brand-new job still at CREATED with no signals yet.
    """
    if not items:
        return
    ids = [j['id'] for j in items]
    ph = ','.join('?' * len(ids))
    high = (
        {x['job_id'] for x in r.rows(f"select distinct job_id from agent_runs where job_id in ({ph}) and status='FAILED'", ids)}
        | {x['job_id'] for x in r.rows(f"select distinct job_id from variances where job_id in ({ph}) and (severity='HIGH' or disposition='EXCEPTION')", ids)}
        | {x['job_id'] for x in r.rows(f"select distinct job_id from migration_risks where job_id in ({ph}) and severity='HIGH' and status='OPEN'", ids)}
        | {x['job_id'] for x in r.rows(f"select distinct job_id from security_findings where job_id in ({ph}) and severity='HIGH' and status='OPEN'", ids)}
    )
    med = (
        {x['job_id'] for x in r.rows(f"select distinct job_id from clarifications where job_id in ({ph}) and status='OPEN'", ids)}
        | {x['job_id'] for x in r.rows(f"select distinct job_id from migration_risks where job_id in ({ph}) and severity='MEDIUM' and status='OPEN'", ids)}
        | {x['job_id'] for x in r.rows(f"select distinct job_id from dormancy_assessments where job_id in ({ph}) and classification='UNKNOWN'", ids)}
        | {x['job_id'] for x in r.rows(f"select distinct job_id from unsupported_mappings where job_id in ({ph}) and status='OPEN'", ids)}
    )
    for j in items:
        jid = j['id']
        st = j.get('state', 'CREATED')
        if jid in high:
            j['risk'] = 'HIGH'
        elif jid in med:
            j['risk'] = 'MEDIUM'
        elif st and st != 'CREATED':
            j['risk'] = 'LOW'

def body(h):
    n=int(h.headers.get('Content-Length','0'))
    if n>2_000_000: raise ValueError('request_too_large')
    raw=h.rfile.read(n)
    return json.loads(raw or b'{}')

class H(BaseHTTPRequestHandler):
    server_version='ACEMigrationFactory'
    sys_version=''
    def end_headers(self):
        for k,v in {'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':getattr(self,'csp_override',None) or "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:",'Permissions-Policy':'camera=(), microphone=(), geolocation=()','Cache-Control':'no-store'}.items(): self.send_header(k,v)
        super().end_headers()
    def disconnected(self,e):
        """A client that went away is not a server fault.

        Report it as one line and stop: there is nobody left to send a status
        to, and attempting one is what produced the second failure.
        """
        self.close_connection=True
        # The path is unset when the abort happens before the request line is
        # parsed, which is the common case for a reset keep-alive connection.
        sys.stderr.write('client_disconnected path=%s error=%s\n'%(getattr(self,'path','-'),type(e).__name__)); sys.stderr.flush()
    def handle_one_request(self):
        """Read and dispatch one request, absorbing a reset connection.

        Requests are also lost *between* them: a client that drops an idle
        keep-alive connection makes the next ``rfile.readline`` raise, which is
        above do_GET/do_POST and so escaped into socketserver as an unhandled
        error report.
        """
        try: return super().handle_one_request()
        except (ConnectionError,TimeoutError) as e: return self.disconnected(e)
    def respond(self,status,ctype,payload):
        """Write one response, tolerating a client that has already gone.

        Every write went straight to the socket, so an aborted request raised
        out of the handler; the 500 path then failed writing to the same dead
        socket, so a single disconnect produced two tracebacks and a
        socketserver "Exception occurred during processing" report.
        """
        try:
            self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
        except (ConnectionError,TimeoutError) as e: self.disconnected(e)
    def sendj(self,s,d): return self.respond(s,'application/json',json.dumps(d,default=str).encode())
    def client_key(self):
        """Throttling key. Trusts a forwarded address only when configured to."""
        if os.getenv('FACTORY_TRUST_FORWARDED_FOR','').lower() in ('1','true','yes'):
            fwd=self.headers.get('X-Forwarded-For','')
            if fwd: return fwd.split(',')[0].strip()
        return self.client_address[0] if self.client_address else 'unknown'
    def principal(self): return identity.authenticate(self.headers.get('Authorization',''))
    def actor(self): return self.principal().identity
    def current_user(self, principal=None):
        """Resolve the signed-in factory account for user-owned integrations."""
        principal = principal or self.principal()
        if getattr(principal, 'user_id', ''):
            user = accounts.repo.one('select * from users where id=?', (principal.user_id,))
            # user_id and sub must name the same account. A mismatch means the
            # token was issued for a different identity than it claims — reject it
            # so audit events cannot be attributed to the wrong email.
            if user and user['email'] != principal.identity:
                raise PermissionError('token_identity_mismatch')
            return user  # None if account deleted (caller handles via stale_session)
        return accounts.by_email(principal.identity)
    def stale_session(self):
        """A cryptographically valid token naming an account that no longer exists.

        This happens whenever the database is reset or restored while a token
        issued against the old one is still sitting in a browser tab -- the
        signing key persists independently of the database, so the token keeps
        verifying, but the account it names is gone. It was previously reported
        as a plain 404 account_not_found, which the client has no handling for:
        the GitHub dialog would reopen and re-fail forever, since re-entering
        the same URL and token can never fix an identity problem. Reporting it
        as 401 routes it through the client's existing reauth path instead,
        which clears the stale token and returns to sign-in.
        """
        return self.sendj(401,{'error':'account_not_found_reauthenticate'})
    def roles(self): return self.principal().roles
    def require(self,*any_of):
        """Authenticate, and require at least one of the given roles."""
        pr=self.principal()
        if any_of and not (set(any_of) & set(pr.roles)):
            raise PermissionError('required_role_missing:'+'|'.join(sorted(any_of)))
        return pr
    def authorize_job(self,principal,job_id):
        """Section 14 project and environment isolation.

        This check existed in IdentityService but was never called, so a token
        scoped to one project could read and drive another project's jobs.
        """
        job=repo.one('select id,project_scope,environment from jobs where id=?',(job_id,))
        if not job: raise ValueError('job_not_found')
        identity.authorize_job(principal,job)
        return job
    def authorize_pipeline_owner(self,principal,pipeline):
        """Only the pipeline's creator, or an orchestration admin, may confirm/run/retry it.

        Any authenticated caller who could name a pipeline id could otherwise
        drive somebody else's migration -- clone their repos, run their
        agents -- with only a token of their own.
        """
        if principal.identity==pipeline['created_by']: return
        if {'ORCHESTRATOR','GOVERNANCE_ADMIN'} & set(principal.roles): return
        raise PermissionError('pipeline_owner_or_orchestrator_role_required')
    def path_arg(self,d,key,create=False,must_exist=True):
        """Confine a caller-supplied filesystem path to the workspace root."""
        return str(workspace.resolve(d.get(key),must_exist=must_exist,create=create))
    def agent_params(self,params):
        """Confine every path and outbound URL an agent parameter can carry.

        Agent parameters are a request body reaching a service directly, so the
        same confinement the older per-service routes apply must apply here or
        the dispatch endpoint would be a way around them.
        """
        out=dict(params or {})
        for key in ('path','golden_path','ace_path','framework_path','project_root'):
            if out.get(key): out[key]=self.path_arg(out,key)
        for key in ('output_root',):
            if out.get(key): out[key]=self.path_arg(out,key,create=True)
        for key in ('ace_base_url','spring_base_url'):
            if out.get(key): out[key]=workspace.runtime_url(out[key])
        return out
    def do_GET(self):
        try: return self.route_get()
        except workspace.WorkspaceError as e: return self.sendj(403,{'error':str(e)})
        except PermissionError as e: return self.sendj(403,{'error':str(e)})
        except (ValueError,KeyError) as e: return self.sendj(422,{'error':str(e)})
        # Reading or writing a vanished client is not an internal error, and
        # must be caught before the generic handler tries to report one.
        except (ConnectionError,TimeoutError) as e: return self.disconnected(e)
        except Exception as e:
            correlation=uid()
            traceback.print_exception(type(e),e,e.__traceback__,file=sys.stderr)
            sys.stderr.write('correlation_id=%s path=%s error=%s\n'%(correlation,self.path,type(e).__name__)); sys.stderr.flush()
            return self.sendj(500,{'error':'internal_error','correlation_id':correlation})
    def route_get(self):
        u=urlparse(self.path); p=u.path; q=parse_qs(u.query)
        if p=='/api/v2/health': return self.sendj(200,{'status':'UP','version':'7.2'})
        # The registration form needs its role options before anyone has a token.
        if p=='/api/v2/auth/roles': return self.sendj(200,{'items':accounts.assignable_roles(),'approver_items':accounts.requestable_roles()})
        # Every other API read is authenticated. Governance data (jobs,
        # approvals, evidence, the audit ledger, specifications, the prompt
        # registry, connector profiles) was previously world-readable.
        if p.startswith('/api/') and p not in PUBLIC_ROUTES:
            try: principal=self.principal()
            except PermissionError as e: return self.sendj(401,{'error':str(e)})
            if p=='/api/v2/'+'audit-ledger' and not ({'GOVERNANCE_ADMIN','AUDITOR'} & set(principal.roles)):
                return self.sendj(403,{'error':'required_role_missing:AUDITOR|GOVERNANCE_ADMIN'})
            if p in ('/api/v2/connectors','/api/v2/connector-operations','/api/v2/routing-policies') and 'CONNECTOR_ADMIN' not in principal.roles:
                return self.sendj(403,{'error':'required_role_missing:CONNECTOR_ADMIN'})
        if p=='/api/v2/admin/access':
            pr=self.principal()
            return self.sendj(200,accounts.access_overview(pr.roles))
        if p=='/api/v2/auth/me':
            pr=self.principal();user=self.current_user(pr)
            if not user: return self.stale_session()
            # Roles come from the account record as well as the token so the UI
            # reflects a grant made after the current token was issued.
            return self.sendj(200,{'user':accounts.public(user),'token_roles':list(pr.roles),
                                   'integrations':{'github':github.config(user['id'])}})
        if p=='/api/v2/integrations/github':
            pr=self.principal();user=self.current_user(pr)
            if not user: return self.stale_session()
            return self.sendj(200,github.config(user['id']))
        if p=='/api/v2/agent-registry/health': return self.sendj(200,agents.health())
        if p=='/api/v2/agent-registry/graph': return self.sendj(200,agents.graph())
        # Whether each approved contract has a conformant worker behind it, and
        # what a given agent is still waiting on for a job.
        if p=='/api/v2/agent-registry/implementations': return self.sendj(200,agents.implementations())
        m=re.fullmatch(r'/api/v2/agent-registry/([a-z-]+)/dependencies',p)
        if m:
            job_id=(q.get('job_id') or [''])[0]
            if not job_id: return self.sendj(422,{'error':'job_id_required'})
            self.authorize_job(self.principal(),job_id)
            return self.sendj(200,agents.dependency_status(job_id,m.group(1)))
        # One row per agent: the model it runs on today, where that comes from,
        # and the aliases an administrator may move it to.
        # What the runs have taught: per-agent trend inputs and recurring lessons.
        if p=='/api/v2/learning': return self.sendj(200,learning.overview(repo))
        if p=='/api/v2/agent-models':
            rows={}
            for a in repo.rows('select agent_id,display_name,default_model_alias,status,enabled from agent_registry order by agent_id,version'):
                rows[a['agent_id']]=a
            items=[]
            for agent_id,a in rows.items():
                default=a.get('default_model_alias') or ''
                binding=llm_bindings.get(agent_id)
                effective,source=llm_bindings.resolve(agent_id,default)
                items.append({'agent_id':agent_id,'display_name':a.get('display_name') or agent_id,'default_alias':default,
                              'effective_alias':effective,'source':source,
                              'binding_status':(binding or {}).get('status',''),'binding_alias':(binding or {}).get('gateway_model_alias',''),
                              'reason':(binding or {}).get('reason_for_change',''),'updated_by':(binding or {}).get('created_by',''),
                              'updated_at':(binding or {}).get('updated_at',''),'enabled':bool(a.get('enabled')),'status':a.get('status') or ''})
            aliases=[r['name'] for r in repo.rows("select distinct name from model_registry where status='APPROVED' order by name")]
            return self.sendj(200,{'items':items,'aliases':aliases})
        m=re.fullmatch(r'/api/v2/agents/([a-z0-9-]+)/llm-binding/history',p)
        if m:
            self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
            agent_id=m.group(1)
            return self.sendj(200,{'agent_id':agent_id,'items':llm_bindings.get_history(agent_id)})
        m=re.fullmatch(r'/api/v2/agents/([a-z0-9-]+)/llm-binding',p)
        if m:
            self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
            agent_id=m.group(1)
            a=repo.one('select agent_id,default_model_alias from agent_registry where agent_id=?',(agent_id,))
            if not a: return self.sendj(404,{'error':'agent_not_found'})
            binding=llm_bindings.get(agent_id)
            effective,source=llm_bindings.resolve(agent_id,a.get('default_model_alias') or '')
            return self.sendj(200,{'agent_id':agent_id,'default_model_alias':a.get('default_model_alias') or '','binding':binding,'effective_alias':effective,'binding_source':source})
        if p=='/api/v2/jobs':
            _STATE_PCT={'CREATED':0,'INTAKE_VALIDATED':8,'DORMANCY_ASSESSED':17,'FRAMEWORK_BASELINED':25,'ACE_ANALYSED':33,'PLAN_APPROVED':42,'IMPLEMENTING':50,'BUILD_VALIDATED':58,'DIFFERENTIAL_TESTING':67,'VARIANCE_REVIEW':75,'REMEDIATING':83,'READY_FOR_PROMOTION':92,'COMPLETED':100,
                        'WAITING_G0':25,'WAITING_G1':25,'WAITING_G2':33,'WAITING_G3':42,'WAITING_G4':75,'WAITING_G5':92}
            items=repo.rows('select * from jobs order by created_at desc')
            for row in items: row['progress']=_STATE_PCT.get(row.get('state','CREATED'),0)
            _enrich_job_risks(repo,items)
            return self.sendj(200,{'items':items})
        m=re.fullmatch(r'/api/v2/([a-z-]+)',p)
        if m and m.group(1) in TABLES:
            table=TABLES[m.group(1)]; where=''; args=[]
            if q.get('job_id') and 'job_id' in {x['name'] for x in repo.rows('pragma table_info('+table+')')}:
                where=' where job_id=?'; args=[q['job_id'][0]]
            order=' order by created_at desc' if 'created_at' in {x['name'] for x in repo.rows('pragma table_info('+table+')')} else ''
            return self.sendj(200,{'items':repo.rows('select * from '+table+where+order,args)})
        m=re.fullmatch(r'/api/v2/jobs/([^/]+)',p)
        if m:
            j=repo.one('select * from jobs where id=?',(m.group(1),))
            if not j: return self.sendj(404,{'error':'job_not_found'})
            identity.authorize_job(self.principal(),j)
            return self.sendj(200,j)
        if p=='/api/v2/pipelines':
            principal=self.principal();out=[]
            for row in pipelines.list_for_job((q.get('job_id') or [None])[0]):
                job=repo.one('select id,project_scope,environment from jobs where id=?',(row['job_id'],))
                try:
                    if job: identity.authorize_job(principal,job)
                except PermissionError: continue
                out.append(row)
            return self.sendj(200,{'items':out})
        m=re.fullmatch(r'/api/v2/pipelines/([^/]+)',p)
        if m:
            detail=pipelines.detail(m.group(1))
            self.authorize_job(self.principal(),detail['job_id'])
            return self.sendj(200,detail)
        # The HTML form of a stored parity / hardcoding report, rendered from its JSON on request.
        m=re.fullmatch(r'/api/v2/reports/([^/]+)/html',p)
        if m:
            row=repo.one("select * from validation_reports where id=? and report_type in ('PARITY_ANALYSIS','HARDCODING_ANALYSIS')",(m.group(1),))
            if not row: return self.sendj(404,{'error':'report_not_found'})
            self.authorize_job(self.principal(),row['job_id'])
            stored=json.loads(row['payload'] or '{}')
            page=static_check_reports.render_html(stored.get('report'),static_check_reports.meta_from_stored(stored,row))
            # This page has its own inline stylesheet and no script; give it exactly that policy, for this response only.
            self.csp_override=static_check_reports.CONTENT_SECURITY_POLICY
            try: return self.respond(200,'text/html; charset=utf-8',page.encode('utf-8'))
            finally: self.csp_override=None
        m=re.fullmatch(r'/api/v2/pipelines/([^/]+)/steps/([^/]+)/output',p)
        if m:
            detail=pipelines.detail(m.group(1))
            self.authorize_job(self.principal(),detail['job_id'])
            step=next((s for s in detail['steps'] if s['id']==m.group(2)),None)
            if not step: return self.sendj(404,{'error':'pipeline_step_not_found'})
            outputs=json.loads(step['outputs'] or '{}')
            model_info={}
            run_id=outputs.get('_run_id','')
            if run_id:
                ar=repo.one('select model_provenance,effective_model_alias,binding_source from agent_runs where id=?',(run_id,))
                if ar:
                    prov=json.loads(ar['model_provenance'] or '{}')
                    model_info={'model_alias':ar['effective_model_alias'] or '','binding_source':ar['binding_source'] or '','model_mode':prov.get('mode',''),'model_reason':prov.get('reason','')}
            # Structured step logs: [timestamp, level, message] tuples the UI
            # Logs tab already renders.  Written by StepLogger during execution.
            logs=[[r['created_at'],r['level'],r['message']] for r in repo.rows('select created_at,level,message from pipeline_step_logs where step_id=? order by created_at',(step['id'],))]
            # Resolved artefact content: the rich displayable output for this
            # agent (markdown, YAML, source code, structured tables) resolved
            # from the service tables using the output IDs.
            artefact=_resolve_agent_artefacts(repo,step.get('agent_id',''),outputs,detail['job_id']) if step.get('agent_id') else None
            # Clarifications this specific run raised, so the output panel's
            # Clarifications tab can show (and let the operator answer) just
            # the questions blocking this step, not every clarification on
            # the job.
            clarifications=repo.rows("select * from clarifications where run_id=? and status<>'DUPLICATE' order by created_at",(run_id,)) if run_id else []
            return self.sendj(200,{**step,'outputs':outputs,'params':json.loads(step['params'] or '{}'),**model_info,'logs':logs,'artefact':artefact,'clarifications':clarifications})
        # The inbox previously trusted ?role= from the query string, so any
        # caller could enumerate every pending approval package.
        if p=='/api/v2/skill-registry':return self.sendj(200,{'items':skill7.list_skills((q.get('runtime_id') or [None])[0],(q.get('status') or [None])[0])})
        if p=='/api/v2/skill-runtimes':return self.sendj(200,{'items':skill7.list_runtimes()})
        if p=='/api/v2/skill-agent-bindings':return self.sendj(200,{'items':skill7.list_bindings((q.get('skill_id') or [None])[0],(q.get('agent_id') or [None])[0])})
        if p=='/api/v7/notifications/mine':
            pr=self.principal();user=self.current_user(pr)
            if not user: return self.stale_session()
            return self.sendj(200,{'items':notify7.for_user(user['email']),'unread':notify7.unread_count(user['email'])})
        if p=='/api/v7/notifications/unread-count':
            pr=self.principal();user=self.current_user(pr)
            if not user: return self.stale_session()
            return self.sendj(200,{'count':notify7.unread_count(user['email'])})
        if p=='/api/v7/approval-inbox': return self.sendj(200,{'items':approvals7.inbox_for(self.principal()),'withheld':approvals7.withheld_for(self.principal())})
        if p=='/api/v7/gates': return self.sendj(200,{'items':approvals7.gates(),'source':'live_database','generated_at':now()})
        if p=='/api/v7/knowledge/search': return self.sendj(200,{'items':knowledge7.search((q.get('q') or [''])[0],(q.get('job_id') or [''])[0])})
        if p=='/api/v7/observability/summary': return self.sendj(200,observe7.summary())
        if p=='/api/v2/dashboard':
            counts={k:repo.one('select count(*) n from '+t)['n'] for k,t in TABLES.items()}
            # Override raw table counts with semantically correct KPI values.
            # jobs: active migrations only (exclude terminal states)
            counts['jobs']=repo.one("select count(*) n from jobs where state not in ('COMPLETED','ROLLED_BACK')")['n']
            # approvals: approval PACKAGES waiting for a human decision, not historical approval records
            counts['approvals']=repo.one("select count(*) n from approval_packages where status='PENDING_APPROVAL'")['n']
            # How many of those this viewer can actually act on. The total is
            # the factory's backlog; the approvals card lists only what the
            # viewer's roles allow, so without this the tile said 3 while the
            # card said "All approvals cleared" and neither explained the gap.
            counts['approvals_actionable']=len(approvals7.inbox_for(self.principal()))
            # evidence: immutable evidence from jobs that have real progress (exclude failed/rolled-back)
            counts['evidence']=repo.one("select count(*) n from evidence where immutable=1 and job_id in (select id from jobs where state not in ('COMPLETED','ROLLED_BACK','FAILED'))")['n']
            counts['running_agents']=repo.one("select count(*) n from agent_runs where status='RUNNING'")['n']
            counts['jobs_by_state']=repo.rows('select state,count(*) count from jobs group by state order by state')
            counts['open_clarifications']=repo.one("select count(*) n from clarifications where status='OPEN'")['n']
            return self.sendj(200,counts)
        if p=='/api/v2/agent-templates': return self.sendj(200,{'items':templates_svc.list()})
        m=re.fullmatch(r'/api/v2/agent-templates/([^/]+)',p)
        if m:
            t=templates_svc.get(m.group(1))
            if not t: return self.sendj(404,{'error':'template_not_found'})
            return self.sendj(200,t)
        if p=='/' or not p.startswith('/api/'): return self.serve_static(p)
        return self.sendj(404,{'error':'not_found'})
    def serve_static(self,p):
        """Serve only the bundled static assets.

        The handler previously delegated to SimpleHTTPRequestHandler, which
        serves the process working directory -- making the entire application
        source downloadable (GET /server.py, /services/auth.py). Requests are
        now resolved strictly inside the static directory.
        """
        rel='index.html' if p=='/' else (p[len('/static/'):] if p.startswith('/static/') else None)
        if not rel: return self.sendj(404,{'error':'not_found'})
        base=(BASE/'static').resolve()
        try: target=(base/rel).resolve()
        except (OSError,ValueError): return self.sendj(404,{'error':'not_found'})
        if target!=base and base not in target.parents: return self.sendj(404,{'error':'not_found'})
        if not target.is_file(): return self.sendj(404,{'error':'not_found'})
        ctype={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8','json':'application/json','svg':'image/svg+xml','png':'image/png','ico':'image/x-icon','woff2':'font/woff2'}.get(target.suffix.lstrip('.').lower(),'application/octet-stream')
        return self.respond(200,ctype,target.read_bytes())
    def do_HEAD(self):
        # Inherited HEAD also served the working directory.
        return self.sendj(405,{'error':'method_not_allowed'})
    def do_POST(self):
        p=urlparse(self.path).path
        try:
            d=body(self)
            if p=='/api/v2/auth/signup':
                throttle('signup:'+self.client_key())
                user=accounts.sign_up(d)
                ledger.append('',user['email'],'ACCOUNT_REGISTERED',{'role':user['role'],'roles':user['roles']})
                return self.sendj(201,{'user':user})
            if p=='/api/v2/auth/login':
                # Throttle on address and on the address+account pair, so one
                # account cannot be ground down from many clients either.
                throttle('login:'+self.client_key());throttle('login-account:'+str(d.get('email','')).strip().lower())
                out=accounts.login(d.get('email'),d.get('password'))
                ledger.append('',out['user']['email'],'ACCOUNT_SIGNED_IN',{'roles':out['user']['roles']})
                return self.sendj(200,out)
            if p=='/api/v2/auth/grant-role':
                pr=self.principal()
                out=accounts.grant_role(pr.roles,d.get('email'),d.get('role'))
                ledger.append('',pr.identity,'ACCOUNT_ROLE_GRANTED',{'email':out['email'],'role':d.get('role'),'roles':out['roles']})
                return self.sendj(200,{'user':out})
            if p=='/api/v2/auth/revoke-role':
                pr=self.principal()
                out=accounts.revoke_role(pr.roles,pr.identity,d.get('email'),d.get('role'))
                ledger.append('',pr.identity,'ACCOUNT_ROLE_REVOKED',{'email':out['email'],'role':d.get('role'),'roles':out['roles']})
                return self.sendj(200,{'user':out})
            if p=='/api/v2/auth/role-requests/decide':
                pr=self.principal()
                out=accounts.decide_request(pr.roles,pr.identity,d.get('id'),d.get('decision'))
                ledger.append('',pr.identity,'ACCOUNT_ROLE_REQUEST_DECIDED',out)
                return self.sendj(200,out)
            if p=='/api/v2/integrations/github':
                pr=self.principal();user=self.current_user(pr)
                if not user: return self.stale_session()
                github.save(user['id'],d.get('url'),d.get('token'))
                # Save and verify in one step: an unverified credential is not
                # useful, and the UI needs the failure detail to offer a retry.
                state=github.connect(user['id'],user_email=user['email'])
                ledger.append('',pr.identity,'GITHUB_INTEGRATION_CONFIGURED',{'url':state.get('url'),'status':state.get('status'),'fingerprint':state.get('token_fingerprint')})
                return self.sendj(200,state)
            if p=='/api/v2/integrations/github/test':
                pr=self.principal();user=self.current_user(pr)
                if not user: return self.stale_session()
                return self.sendj(200,github.connect(user['id'],user_email=user['email']))
            if p=='/api/v2/integrations/github/disconnect':
                pr=self.principal();user=self.current_user(pr)
                if not user: return self.stale_session()
                state=github.disconnect(user['id'])
                ledger.append('',pr.identity,'GITHUB_INTEGRATION_DISCONNECTED',state)
                return self.sendj(200,state)
            if p=='/api/v2/jobs': return self.sendj(201,factory.create_job(d,self.require().identity))
            if p=='/api/v2/pipelines/preview':
                self.principal()
                return self.sendj(200,pipelines.impact(d.get('selected_agent_ids',[])))
            if p=='/api/v2/pipelines':
                pr=self.require()
                job=factory.create_job(d.get('job',{}),pr.identity)
                workspace_dir=str(workspace.resolve(d['workspace_dir'],must_exist=False,create=True))
                created=pipelines.create(job['id'],workspace_dir,d.get('repos',{}),d.get('selected_agent_ids',[]),pr.identity,
                                         d.get('name',''),d.get('service_name','migrated-service'),d.get('package_name','com.example.migrated'))
                ledger.append(job['id'],pr.identity,'PIPELINE_CREATED',{'pipeline_id':created['id'],'agents':d.get('selected_agent_ids',[])})
                return self.sendj(201,created)
            m=re.fullmatch(r'/api/v2/pipelines/([^/]+)/confirm',p)
            if m:
                pr=self.principal();detail=pipelines.detail(m.group(1));self.authorize_pipeline_owner(pr,detail)
                return self.sendj(200,pipelines.confirm(m.group(1)))
            m=re.fullmatch(r'/api/v2/pipelines/([^/]+)/run',p)
            if m:
                pr=self.principal();detail=pipelines.detail(m.group(1));self.authorize_pipeline_owner(pr,detail)
                out=pipelines.run(m.group(1),workflow)
                ledger.append(detail['job_id'],pr.identity,'PIPELINE_RUN_STARTED',{'pipeline_id':m.group(1)})
                return self.sendj(200,out)
            m=re.fullmatch(r'/api/v2/pipelines/([^/]+)/pause',p)
            if m:
                pr=self.principal();detail=pipelines.detail(m.group(1));self.authorize_pipeline_owner(pr,detail)
                out=pipelines.pause(m.group(1),workflow)
                ledger.append(detail['job_id'],pr.identity,'PIPELINE_PAUSED',{'pipeline_id':m.group(1)})
                return self.sendj(200,out)
            m=re.fullmatch(r'/api/v2/pipelines/([^/]+)/resume',p)
            if m:
                pr=self.principal();detail=pipelines.detail(m.group(1));self.authorize_pipeline_owner(pr,detail)
                out=pipelines.resume(m.group(1),workflow)
                ledger.append(detail['job_id'],pr.identity,'PIPELINE_RESUMED',{'pipeline_id':m.group(1)})
                return self.sendj(200,out)
            m=re.fullmatch(r'/api/v2/pipelines/([^/]+)/cancel',p)
            if m:
                pr=self.principal();detail=pipelines.detail(m.group(1));self.authorize_pipeline_owner(pr,detail)
                out=pipelines.cancel(m.group(1),workflow)
                ledger.append(detail['job_id'],pr.identity,'PIPELINE_CANCELLED',{'pipeline_id':m.group(1)})
                return self.sendj(200,out)
            m=re.fullmatch(r'/api/v2/pipelines/([^/]+)/steps/([^/]+)/retry',p)
            if m:
                pr=self.principal();detail=pipelines.detail(m.group(1));self.authorize_pipeline_owner(pr,detail)
                pipelines.retry_step(m.group(2))
                pipelines.advance(m.group(1),workflow)
                # Returning the pre-advance() PENDING snapshot would misreport
                # a step that advance() just moved on to QUEUED.
                return self.sendj(200,repo.one('select * from pipeline_steps where id=?',(m.group(2),)))
            if p=='/api/v7/services': return self.sendj(201,registry7.register_service(d,self.require('SERVICE_REGISTRAR','GOVERNANCE_ADMIN').identity))
            # Prompt and model approval is a governed act. These endpoints
            # previously accepted status:"APPROVED" and approved_by from an
            # unauthenticated request body, so the model gateway's allow-list
            # could be populated by the caller it was meant to constrain.
            if p=='/api/v7/prompts': return self.sendj(201,ai7.register_prompt(d,self.require('AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN')))
            if p=='/api/v7/models': return self.sendj(201,ai7.register_model(d,self.require('AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN')))
            if p=='/api/v7/model-gateway/requests':
                pr=self.require('AGENT_OPERATOR','ORCHESTRATOR','RCA_ANALYST')
                self.authorize_job(pr,d.get('job_id',''))
                return self.sendj(202,ai7.gateway_request(d))
            if p=='/api/v7/knowledge/index': return self.sendj(201,knowledge7.index(d,self.require('KNOWLEDGE_CURATOR','GOVERNANCE_ADMIN')))
            if p=='/api/v7/gates/configure':
                principal=self.principal()
                if not ({'GOVERNANCE_ADMIN','GATE_ADMIN'} & set(principal.roles)): raise PermissionError('gate_admin_role_required')
                result=approvals7.configure_gate(d,principal.identity)
                ledger.append('',principal.identity,'GATE_CONFIGURATION_UPDATED',result)
                return self.sendj(200,result)
            if p=='/api/v7/delivery/git':
                # Was unauthenticated, on any caller-supplied path, with
                # unvalidated refs reaching git as argv.
                self.require('DELIVERY_ENGINEER','RELEASE_MANAGER')
                safe=dict(d);safe['repository_path']=self.path_arg(d,'repository_path')
                for k in ('branch','tag'):
                    if safe.get(k):safe[k]=workspace.git_ref(safe[k])
                return self.sendj(200,delivery7.git_operation(safe))
            if p=='/api/v7/delivery/request':
                self.require('DELIVERY_ENGINEER','RELEASE_MANAGER');return self.sendj(202,delivery7.request(d))
            if p=='/api/v7/notifications': self.require('NOTIFICATION_PUBLISHER','GOVERNANCE_ADMIN');return self.sendj(201,notify7.queue(d))
            if p=='/api/v7/notifications/mark-read':
                pr=self.principal();user=self.current_user(pr)
                if not user: return self.stale_session()
                return self.sendj(200,notify7.mark_read(d.get('id',''),user['email']))
            if p=='/api/v7/notifications/mark-all-read':
                pr=self.principal();user=self.current_user(pr)
                if not user: return self.stale_session()
                return self.sendj(200,notify7.mark_all_read(user['email']))
            if p=='/api/v7/observability': self.require('OBSERVABILITY_PUBLISHER','GOVERNANCE_ADMIN');return self.sendj(201,observe7.emit(d))
            if p=='/api/v3/workflow/schedule': return self.sendj(201,workflow.schedule(d['job_id'],d['activity'],d.get('payload'),d.get('max_attempts',3),d.get('idempotency_key')))
            if p=='/api/v3/workflow/claim': return self.sendj(200,workflow.claim(d.get('worker_id','local-worker'),d.get('lease_seconds',60),d.get('activity')) or {})
            if p=='/api/v3/workflow/heartbeat': return self.sendj(200,workflow.heartbeat(d['queue_id'],d['worker_id'],d.get('lease_seconds',60)))
            if p=='/api/v3/workflow/complete': return self.sendj(200,workflow.complete(d['queue_id'],d.get('success',True),d.get('error',''),d.get('worker_id')))
            if p=='/api/v3/workflow/cancel': return self.sendj(200,workflow.cancel(d['queue_id']))
            if p=='/api/v3/workflow/replay-dead-letter': return self.sendj(201,workflow.replay_dead_letter(d['queue_id'],d.get('idempotency_key')))
            if p=='/api/v3/workflow/checkpoint': return self.sendj(201,workflow.checkpoint(d['job_id'],d['state'],d.get('payload')))
            if p=='/api/v3/workflow/pause': return self.sendj(200,workflow.pause(d['job_id']))
            if p=='/api/v3/workflow/resume': return self.sendj(200,workflow.resume(d['job_id']))
            # Every filesystem path below arrives from the request body and is
            # confined to the approved workspace root. Unconfined, these were an
            # unauthenticated arbitrary read (and, for generate, write).
            if p=='/api/v3/intake':
                pr=self.require('REPOSITORY_INTAKE','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,intake.scan(d['job_id'],self.path_arg(d,'path'),d.get('branch','main'),d.get('revision','')))
            if p=='/api/v3/dormancy':
                pr=self.require('DORMANCY_ANALYST','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,dormancy.assess(d['job_id'],d.get('traffic_count'),d.get('last_seen_days'),d.get('deployed'),d.get('already_migrated')))
            if p=='/api/v3/frameworks':
                pr=self.require('FRAMEWORK_ANALYST','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,frameworks.analyze(d['job_id'],self.path_arg(d,'path'),d.get('version','1.0.0')))
            if p=='/api/v3/analyses':
                pr=self.require('ACE_ANALYST','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,analysis.analyze(d['job_id'],self.path_arg(d,'path'),d.get('version','1.0.0')))
            if p=='/api/v5/priority1/ace-analysis':
                pr=self.require('ACE_ANALYST','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,priority1.analyze_ace(d['job_id'],self.path_arg(d,'path'),d.get('version','1.0.0')))
            if p=='/api/v5/priority1/consistency':
                pr=self.require('ACE_ANALYST','SPECIFICATION_AUTHOR','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,priority1.validate_specification(d['job_id'],d['analysis_id'],d['specification_id']))
            if p=='/api/v5/priority1/framework-baseline':
                pr=self.require('FRAMEWORK_ANALYST','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                golden=self.path_arg(d,'golden_path') if d.get('golden_path') else None
                return self.sendj(201,priority1.baseline_framework(d['job_id'],self.path_arg(d,'path'),d.get('version','1.0.0'),d.get('effective_from'),golden))
            if p=='/api/v5/priority1/approval-package': return self.sendj(201,priority1.approval_package(d['job_id'],d['gate'],d.get('analysis_id'),d.get('specification_id'),d.get('framework_id')))
            if p=='/api/v6/mappings': return self.sendj(201,lifecycle6.create_mapping_catalogue(d['job_id'],d['framework_id'],d['analysis_id'],d['specification_id'],d.get('version','1.0.0'),d.get('overrides')))
            if p=='/api/v6/plans': return self.sendj(201,lifecycle6.generate_plan(d['job_id'],d['catalogue_id'],d.get('version','1.0.0')))
            if p=='/api/v6/g3-package': return self.sendj(201,lifecycle6.g3_package(d['job_id'],d['plan_id'],d['catalogue_id']))
            if p=='/api/v6/generate':
                pr=self.require('CODE_GENERATOR','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,lifecycle6.generate_spring(d['job_id'],d['plan_id'],self.path_arg(d,'output_root',create=True),d['service_name'],d['package_name']))
            if p=='/api/v6/quality':
                pr=self.require('CODE_GENERATOR','TEST_ENGINEER','ORCHESTRATOR');self.authorize_job(pr,d['job_id'])
                return self.sendj(201,lifecycle6.quality_validate(d['job_id'],d['plan_id'],self.path_arg(d,'project_root'),d.get('execute_build',False)))
            if p=='/api/v6/differential/prepare': return self.sendj(201,lifecycle6.prepare_differential(d['job_id'],d['plan_id'],d['cases'],d.get('policy',{})))
            if p=='/api/v6/differential/record': return self.sendj(201,lifecycle6.record_differential(d['job_id'],d['package_id'],d['case_id'],d['ace_result'],d['spring_result'],d.get('policy',{})))
            if p=='/api/v6/differential/disposition':
                pr=self.require('QA_LEAD','GOVERNANCE_ADMIN');self.authorize_job(pr,d['job_id'])
                return self.sendj(200,lifecycle6.disposition_differential(d['job_id'],d['evidence_id'],d.get('disposition',''),d.get('reason',''),pr.identity))
            # The confidence and remediation-eligibility rules existed but no
            # route reached them, so remediation was never actually scored.
            if p=='/api/v5/confidence/score':
                pr=self.require('RCA_ANALYST','ORCHESTRATOR','GOVERNANCE_ADMIN');self.authorize_job(pr,d['job_id'])
                out=confidence.score(d['evidence_coverage'],d['reproducible'],d['traceability'],d['rule_strength'],d['test_isolation'],d['historical_validation'])
                security.metric(d['job_id'],'remediation.confidence',out['score'],out['components'])
                ledger.append(d['job_id'],pr.identity,'REMEDIATION_CONFIDENCE_SCORED',out)
                return self.sendj(201,{**out,'threshold':CONFIDENCE_THRESHOLD})
            if p=='/api/v5/remediation/eligibility':
                pr=self.require('RCA_ANALYST','ORCHESTRATOR','GOVERNANCE_ADMIN');self.authorize_job(pr,d['job_id'])
                out=confidence.eligibility(float(d['confidence']),CONFIDENCE_THRESHOLD,bool(d.get('approved_pattern')),bool(d.get('bounded_impact')),bool(d.get('tests_present')),bool(d.get('prohibited_dependency',False)))
                ledger.append(d['job_id'],pr.identity,'REMEDIATION_ELIGIBILITY_EVALUATED',{**out,'confidence':float(d['confidence']),'threshold':CONFIDENCE_THRESHOLD})
                return self.sendj(200,{**out,'threshold':CONFIDENCE_THRESHOLD})
            if p=='/api/v5/remediation/propose':
                pr=self.require('RCA_ANALYST','GOVERNANCE_ADMIN');self.authorize_job(pr,d['job_id'])
                row=repo.one('select iteration,rollback_pointer from jobs where id=?',(d['job_id'],))
                eligibility=confidence.eligibility(float(d['confidence']),CONFIDENCE_THRESHOLD,bool(d.get('approved_pattern')),bool(d.get('bounded_impact')),bool(d.get('tests_present')),bool(d.get('prohibited_dependency',False)))
                out=confidence.propose(d['variance'],eligibility,row['iteration'],row['rollback_pointer'] or '')
                ledger.append(d['job_id'],pr.identity,'REMEDIATION_PROPOSED',out)
                return self.sendj(201,{**out,'eligibility':eligibility})
            if p=='/api/v3/plans': return self.sendj(201,planning.plan(d['job_id'],d['analysis_id'],d['framework_id'],d['specification_id'],d.get('version','1.0.0')))
            if p=='/api/v3/generate': return self.sendj(201,{'items':planning.generate(d['job_id'],d['plan_id'],d.get('package','com.example.migration'))})
            if p=='/api/v3/audit': return self.sendj(201,ledger.append(d['job_id'],self.actor(),d['action'],d.get('payload',{})))
            if p=='/api/v3/security/scan': return self.sendj(201,{'items':security.scan(d['job_id'],d['path'],d['content'])})
            if p=='/api/v3/secrets': return self.sendj(201,security.secret_reference(d['name'],d['reference']))
            if p=='/api/v3/metrics': return self.sendj(201,security.metric(d['job_id'],d['name'],d['value'],d.get('labels')))
            if p.startswith('/api/v3/connectors') and 'CONNECTOR_ADMIN' not in self.roles(): raise PermissionError('connector_admin_role_required')
            if p=='/api/v3/connectors':
                d['updated_by']=self.actor(); item=connectors.upsert(d); ledger.append('',self.actor(),'CONNECTOR_CONFIG_UPDATED',{'profile_id':item['id'],'version':item.get('config_version')}); return self.sendj(201,item)
            if p=='/api/v3/connectors/test':
                item=repo.one('select * from connector_profiles where id=?',(d['profile_id'],))
                if not item: return self.sendj(404,{'error':'connector_not_found'})
                cfg=json.loads(item['config']); endpoint=cfg.get('base_url') or cfg.get('endpoint') or cfg.get('issuer')
                result={'profile_id':item['id'],'validation':'PASSED' if endpoint else 'FAILED','network_test':'NOT_EXECUTED','message':'Configuration validated; external connectivity was not attempted.'}
                connectors.record(item['id'],'validate-configuration',{'endpoint_present':bool(endpoint)},'VALIDATED' if endpoint else 'INVALID',result); return self.sendj(200,result)
            if p=='/api/v3/connectors/load': return self.sendj(201,{'items':connectors.load(d['path'])})
            if p=='/api/v3/connectors/routing': return self.sendj(200,connectors.set_routing(d['category'],d.get('strategy','priority'),d.get('failover_enabled',True),d.get('health_required',True)))
            if p=='/api/v3/connectors/resolve': return self.sendj(200,{'items':connectors.resolve(d['category'])})
            if p=='/api/v3/connectors/health': return self.sendj(200,connectors.mark_health(d['profile_id'],d['healthy'],d.get('details')))
            if p=='/api/v3/connectors/operations': return self.sendj(201,connectors.record(d['profile_id'],d['operation'],d.get('request',{}),d['status'],d.get('response_metadata'),d.get('error','')))
            m=re.fullmatch(r'/api/v2/jobs/([^/]+)/transition',p)
            if m:
                # Driving the lifecycle was authenticated but not authorised:
                # any valid token could advance any job it could name.
                pr=self.require('ORCHESTRATOR','GOVERNANCE_ADMIN');self.authorize_job(pr,m.group(1))
                return self.sendj(200,factory.transition(m.group(1),d['target'],pr.identity,d))
            m=re.fullmatch(r'/api/v2/jobs/([^/]+)/rollback',p)
            if m:
                pr=self.require('RELEASE_MANAGER','GOVERNANCE_ADMIN');self.authorize_job(pr,m.group(1))
                return self.sendj(200,factory.rollback(m.group(1),pr.identity,d.get('reason',''),d.get('rollback_pointer')))
            if p=='/api/v2/approvals':
                pr=self.principal();self.authorize_job(pr,d.get('job_id',''))
                result=factory.approve(d,pr.roles,pr.identity)
                try: pipelines.resume_gate_step(d.get('job_id',''),workflow,d.get('gate',''))
                except Exception: pass
                # Also walk the lifecycle forward on pipelines that already
                # completed (e.g. framework-baseline was disabled — the pipeline
                # finished before the gate was cleared, so no step completion
                # event will fire to continue the walk).
                try: pipelines.resume_after_clarification(d.get('job_id',''))
                except Exception: pass
                return self.sendj(201,result)
            m=re.fullmatch(r'/api/v7/approval-packages/([^/]+)/approve',p)
            if m:
                pr=self.principal()
                pkg=repo.one('select * from approval_packages where id=?',(m.group(1),))
                if not pkg: return self.sendj(404,{'error':'approval_package_not_found'})
                self.authorize_job(pr,pkg['job_id'])
                policy=repo.one('select * from approval_policies where gate=?',(pkg['gate'],))
                if not policy: return self.sendj(422,{'error':'unknown_gate:'+str(pkg['gate'])})
                allowed=[v for v in (policy['allowed_decisions'] or '').split(',') if v]
                decision=str(d.get('decision','') or (allowed[0] if allowed else 'APPROVE')).strip()
                approver_role=next((r for r in (pr.roles or []) if r==policy['required_role']),'')
                if not approver_role: raise PermissionError('approver_role_required:'+str(policy['required_role']))
                artefact_bindings=json.loads(pkg['artefact_bindings'] or '{}')
                # artefact_bindings may be a dict {id:hash} (pipelines.py) or a list [{kind,id,hash,...}] (lifecycle/priority1).
                # The list format uses plan/catalogue IDs which are NOT in the evidence table.
                # The policy min_evidence check queries the evidence table, so we must include
                # at least one real evidence ID; we augment the list with any existing evidence for the job.
                if isinstance(artefact_bindings,list):
                    pkg_item_ids=[item['id'] for item in artefact_bindings if isinstance(item,dict) and 'id' in item]
                    artefact_bindings_dict={item['id']:item.get('hash','') for item in artefact_bindings if isinstance(item,dict) and 'id' in item}
                    real_ev=[r['id'] for r in repo.rows('select id from evidence where job_id=? limit 5',(pkg['job_id'],))]
                    evidence_ids=real_ev+[i for i in pkg_item_ids if i not in real_ev]
                else:
                    evidence_ids=list(artefact_bindings.keys())
                    artefact_bindings_dict=artefact_bindings
                approve_data={'job_id':pkg['job_id'],'gate':pkg['gate'],'decision':decision,'approver_role':approver_role,'approver_identity':pr.identity,'comments':d.get('comments',''),'conditions':d.get('conditions',''),'evidence_ids':evidence_ids,'artifact_bindings':artefact_bindings_dict}
                result=factory.approve(approve_data,pr.roles,pr.identity)
                with repo.connect() as c: c.execute("update approval_packages set status='APPROVED' where id=?",(m.group(1),))
                ledger.append(pkg['job_id'],pr.identity,'APPROVAL_PACKAGE_SIGNED',{'package_id':m.group(1),'gate':pkg['gate'],'decision':decision})
                try: pipelines.resume_gate_step(pkg['job_id'],workflow,pkg['gate'])
                except Exception: pass
                try: pipelines.resume_after_clarification(pkg['job_id'])
                except Exception: pass
                return self.sendj(200,result)
            m=re.fullmatch(r'/api/admin/jobs/([a-f0-9-]+)/retrigger-lifecycle',p)
            if m:
                self.require('GOVERNANCE_ADMIN','GATE_ADMIN')
                jid=m.group(1)
                j=repo.one('select id,state from jobs where id=?',(jid,))
                if not j: raise ValueError('job_not_found')
                pipelines.resume_after_clarification(jid)
                j=repo.one('select id,state from jobs where id=?',(jid,))
                return self.sendj(200,{'job_id':jid,'state':j['state']})
            if p=='/api/v2/evidence':
                pr=self.principal();self.authorize_job(pr,d['job_id'])
                return self.sendj(201,evidence.register(d['job_id'],d['kind'],d['version'],d['uri'],d['content'],d.get('producer',pr.identity),d.get('metadata')))
            if p=='/api/v2/versions':
                pr=self.principal();job_id=d.pop('job_id');self.authorize_job(pr,job_id)
                d.setdefault('producer',pr.identity); return self.sendj(201,evidence.snapshot(job_id,**d))
            if p=='/api/v2/specifications': return self.sendj(201,specs.save(d['job_id'],d['version'],d['yaml_text']))
            if p=='/api/v2/comparisons': return self.sendj(201,diff.compare(d['job_id'],d['case_id'],d['expected'],d['actual'],d.get('policy',{})))
            if p=='/api/v2/rca': return self.sendj(201,rca.record_root_cause(d['job_id'],d['variance_ids'],d['hypothesis'],d['confidence'],d.get('supporting_evidence',[]),d.get('missing_evidence',[]),d['affected_scope']))
            if p=='/api/v2/remediations': return self.sendj(201,rca.record_remediation(d['job_id'],d['root_cause_id'],d['proposal'],d['approved_pattern'],d['regression_tests'],d['iteration'],d['rollback_pointer']))
            if p=='/api/v2/clarifications':
                for k in ('job_id','stage','question','owner','impact'):
                    if not str(d.get(k,'')).strip(): raise ValueError('missing_'+k)
                if not repo.one('select id from jobs where id=?',(d['job_id'],)): raise ValueError('job_not_found')
                cid=uid()
                # Named columns: a positional insert here silently drifted out of
                # step with the table and made this endpoint unusable.
                with repo.connect() as c:c.execute('insert into clarifications(id,job_id,stage,question,owner,impact,evidence_ref,status,answer,created_at,answered_at,answered_by) values(?,?,?,?,?,?,?,?,?,?,?,?)',(cid,d['job_id'],d['stage'],d['question'],d['owner'],d['impact'],d.get('evidence_ref',''),'OPEN','',now(),'',''))
                ledger.append(d['job_id'],self.actor(),'CLARIFICATION_REQUIRED',{'clarification_id':cid,'stage':d['stage'],'question':d['question'],'owner':d['owner']})
                return self.sendj(201,repo.one('select * from clarifications where id=?',(cid,)))
            m=re.fullmatch(r'/api/v2/clarifications/([^/]+)/answer',p)
            if m:
                if not str(d.get('answer','')).strip(): raise ValueError('missing_answer')
                principal=self.principal()
                item=repo.one('select * from clarifications where id=?',(m.group(1),))
                if not item: return self.sendj(404,{'error':'clarification_not_found'})
                # Section 7: the answer must come from an authorised actor and is
                # versioned as an explicit input to subsequent steps.
                #
                # One an agent raised (run_id set) authorises against that
                # agent's own governed required_role, not the free-text
                # `owner` the model wrote on the clarification itself -- a
                # model-authored owner like "ACE Analyst (DMRClaimStatus ESQL
                # review)" names nobody's actual role, so checking it against
                # principal.roles would leave the question unanswerable by
                # anyone except CLARIFICATION_ADMIN. A clarification raised
                # directly through this endpoint (no run_id) has no agent run
                # to resolve a role from, so it still authorises by its own
                # `owner`, since that is the only role it was ever given.
                required_role=item['owner']
                if item.get('run_id'):
                    run=repo.one('select agent_id,agent_version from agent_runs where id=?',(item['run_id'],))
                    if run and run['agent_id']:
                        contract=repo.one('select required_role from agent_registry where agent_id=? and version=?',(run['agent_id'],run['agent_version']))
                        if contract: required_role=contract['required_role']
                if required_role not in principal.roles and 'CLARIFICATION_ADMIN' not in principal.roles:
                    raise PermissionError('clarification_owner_role_required:'+str(required_role))
                with repo.connect() as c:
                    n=c.execute("update clarifications set status='ANSWERED',answer=?,answered_at=?,answered_by=? where id=? and status='OPEN'",(d['answer'],now(),principal.identity,m.group(1))).rowcount
                if not n: raise ValueError('clarification_not_open')
                # Other open copies of this very question in the job are answered
                # by this: they close, rather than asking for the same answer again.
                settle_siblings(repo,item,d['answer'])
                ledger.append(item['job_id'],principal.identity,'CLARIFICATION_ANSWERED',{'clarification_id':m.group(1),'answer_hash':digest(d['answer'])})
                # A step parked at WAITING_CLARIFICATION resumes as soon as its
                # own run's clarifications are all answered -- independent of
                # whether every clarification on the whole job is resolved yet.
                try: pipelines.resume_clarification_steps(item['job_id'],workflow)
                except Exception: pass
                # If all clarifications for this job are now resolved, attempt to
                # resume the lifecycle that was stalled waiting for answers.
                remaining=repo.one("select count(*) as n from clarifications where job_id=? and status='OPEN'",(item['job_id'],))
                if remaining and remaining['n']==0:
                    try: pipelines.resume_after_clarification(item['job_id'])
                    except Exception: pass
                return self.sendj(200,repo.one('select * from clarifications where id=?',(m.group(1),)))
            m=re.fullmatch(r'/api/v2/agents/([a-z0-9-]+)/llm-binding/validate',p)
            if m:
                self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
                agent_id=m.group(1)
                a=repo.one('select agent_id from agent_registry where agent_id=?',(agent_id,))
                if not a: return self.sendj(404,{'error':'agent_not_found'})
                return self.sendj(200,llm_bindings.validate(agent_id))
            m=re.fullmatch(r'/api/v2/agents/([a-z0-9-]+)/llm-binding/activate',p)
            if m:
                principal=self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
                agent_id=m.group(1)
                a=repo.one('select agent_id from agent_registry where agent_id=?',(agent_id,))
                if not a: return self.sendj(404,{'error':'agent_not_found'})
                result=llm_bindings.activate(agent_id,principal.identity)
                ledger.append('',principal.identity,'AGENT_LLM_BINDING_ACTIVATED',{'agent_id':agent_id,'gateway_model_alias':result['gateway_model_alias']})
                return self.sendj(200,result)
            m=re.fullmatch(r'/api/v2/agents/([a-z0-9-]+)/llm-binding',p)
            if m:
                principal=self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
                agent_id=m.group(1)
                a=repo.one('select agent_id from agent_registry where agent_id=?',(agent_id,))
                if not a: return self.sendj(404,{'error':'agent_not_found'})
                result=llm_bindings.save(agent_id,d.get('binding_mode','DEFAULT'),d.get('gateway_model_alias',''),d.get('model_id',''),principal.identity,d.get('model_revision',''),d.get('provider_type','litellm_alias'),d.get('reason_for_change',''),d.get('capabilities',''),d.get('licence_id',''),d.get('data_region',''),d.get('data_classification_allowed',''),d.get('context_limit',0),d.get('output_limit',0))
                ledger.append('',principal.identity,'AGENT_LLM_BINDING_SAVED',{'agent_id':agent_id,'binding_mode':result['binding_mode'],'status':result['status'],'gateway_model_alias':result['gateway_model_alias']})
                return self.sendj(201,result)
            if p=='/api/v2/agent-registry':
                # Was a hard 405 -- the registry could only change via the
                # version-controlled catalogue file and a restart. Now a
                # governed admin can add or modify an agent's contract live;
                # Repository._seed_agent_catalogue leaves this row alone on
                # restart (origin='RUNTIME') so it neither reverts the change
                # nor crashes the app comparing it to the unmodified file.
                principal=self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
                result=agent_admin.create_or_update(d,principal.identity)
                ledger.append('',principal.identity,'AGENT_REGISTRY_UPDATED',{'agent_id':result['agent_id'],'version':result['version'],'catalogue_file_synced':result['catalogue_file_synced']})
                return self.sendj(201,result)
            if p=='/api/v2/model-registry':
                principal=self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
                name=(d.get('name') or '').strip()
                if not name: raise ValueError('name_required')
                version=(d.get('version') or '1.0').strip() or '1.0'
                status=(d.get('status') or 'DRAFT').strip()
                if status not in ('APPROVED','DRAFT','RETIRED'): raise ValueError('invalid_status')
                existing=repo.one('select id from model_registry where name=? and version=?',(name,version))
                with repo.connect() as c:
                    if existing:
                        c.execute('update model_registry set status=?,provider_profile=?,policy=?,region=?,approved_by=? where name=? and version=?',(status,d.get('provider_profile',''),d.get('policy',''),d.get('region',''),principal.identity,name,version))
                        row_id=existing['id']
                    else:
                        row_id=uid()
                        repo.insert(c,'model_registry',{'id':row_id,'name':name,'version':version,'status':status,'provider_profile':d.get('provider_profile',''),'policy':d.get('policy',''),'region':d.get('region',''),'approved_by':principal.identity,'created_at':now()})
                result={'id':row_id,'name':name,'version':version,'status':status,'approved_by':principal.identity}
                ledger.append('',principal.identity,'MODEL_REGISTRY_UPDATED',result)
                return self.sendj(201,result)
            # Runs the factory's own worker for an approved contract: dependency
            # ordering, evidence registration and the audit entry are the
            # registry's, not the caller's.
            if p=='/api/v2/agents/dispatch':
                pr=self.principal();self.authorize_job(pr,d.get('job_id',''))
                return self.sendj(201,agents.dispatch(d['job_id'],d['agent_id'],d.get('version','1.0.0'),pr.roles,pr.identity,self.agent_params(d.get('params')),d.get('input_evidence',[])))
            if p=='/api/v2/agents/orchestrate':
                pr=self.require('ORCHESTRATOR','GOVERNANCE_ADMIN');self.authorize_job(pr,d.get('job_id',''))
                return self.sendj(201,agents.dispatch(d['job_id'],'migration-orchestrator','1.0.0',pr.roles,pr.identity,self.agent_params(d.get('params'))))
            # The start/complete pair remains for an external worker that performs
            # its own execution. Both now carry the operator identity, so a run has
            # an owner and cannot be completed by another caller.
            if p=='/api/v2/agents/start':
                pr=self.principal();self.authorize_job(pr,d.get('job_id',''))
                return self.sendj(201,agents.start(d['job_id'],d['agent_id'],d['version'],d.get('input_evidence',[]),pr.roles,d.get('requested_tools',[]),pr.identity))
            m=re.fullmatch(r'/api/v2/agents/([^/]+)/complete',p)
            if m: return self.sendj(200,agents.complete(m.group(1),d.get('output_evidence',[]),d.get('error',''),self.actor()))
            if p=='/api/v2/agent-templates':
                return self.sendj(201,templates_svc.create(d,self.principal().identity))
            if p=='/api/v2/skill-runtimes':
                principal=self.require('AGENT_REGISTRY_ADMIN','AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN')
                result=skill7.register_runtime(d,principal)
                ledger.append('',principal.identity,'SKILL_RUNTIME_REGISTERED',{'name':result['name'],'language':result['language']})
                return self.sendj(201,result)
            if p=='/api/v2/skill-registry':
                principal=self.require('AGENT_REGISTRY_ADMIN','AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN')
                result=skill7.register_skill(d,principal)
                ledger.append('',principal.identity,'SKILL_REGISTERED',{'skill_key':result['skill_key'],'version':result['version'],'status':result['status']})
                return self.sendj(201,result)
            if p=='/api/v2/skill-agent-bindings':
                principal=self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
                result=skill7.bind_skill_to_agent(d,principal)
                ledger.append('',principal.identity,'SKILL_AGENT_BOUND',{'skill_id':result['skill_id'],'agent_id':result['agent_id'],'binding_type':result['binding_type']})
                return self.sendj(201,result)
            return self.sendj(404,{'error':'not_found'})
        except PermissionError as e: return self.sendj(403,{'error':str(e)})
        # A dependency being unreachable is not the caller's fault; 502 lets the
        # UI distinguish "your token is wrong" from "GitHub did not answer".
        except ConnectionError as e: return self.sendj(502,{'error':str(e)})
        except (AgentLLMBindingError,ValueError,KeyError,json.JSONDecodeError) as e: return self.sendj(422,{'error':str(e)})
        except (ConnectionError,TimeoutError) as e: return self.disconnected(e)
        except Exception as e:
            # Anything unanticipated must still produce a response. Previously a
            # sqlite/attribute error escaped the handler, dropping the
            # connection with no status at all. The correlation id lets the
            # operator tie the client's 500 to the server-side detail.
            correlation=uid()
            traceback.print_exception(type(e),e,e.__traceback__,file=sys.stderr)
            sys.stderr.write('correlation_id=%s path=%s error=%s\n'%(correlation,p,type(e).__name__))
            sys.stderr.flush()
            return self.sendj(500,{'error':'internal_error','correlation_id':correlation})
    def do_PUT(self):
        p=urlparse(self.path).path
        try:
            d=body(self)
            m=re.fullmatch(r'/api/v2/agent-templates/([^/]+)',p)
            if m:
                t=templates_svc.update(m.group(1),d)
                if not t: return self.sendj(404,{'error':'template_not_found'})
                return self.sendj(200,t)
            m=re.fullmatch(r'/api/v2/skill-registry/([^/]+)',p)
            if m:
                principal=self.require('AGENT_REGISTRY_ADMIN','AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN')
                result=skill7.update_skill_status(m.group(1),str(d.get('status','')).upper(),principal)
                ledger.append('',principal.identity,'SKILL_STATUS_UPDATED',{'id':m.group(1),'status':result['status']})
                return self.sendj(200,result)
            m=re.fullmatch(r'/api/v2/skill-runtimes/([^/]+)',p)
            if m:
                principal=self.require('AGENT_REGISTRY_ADMIN','AI_GOVERNANCE_ADMIN','GOVERNANCE_ADMIN')
                d['id']=m.group(1)
                result=skill7.update_runtime(d,principal)
                if not result:return self.sendj(404,{'error':'runtime_not_found'})
                ledger.append('',principal.identity,'SKILL_RUNTIME_UPDATED',{'id':m.group(1),'name':result['name']})
                return self.sendj(200,result)
            return self.sendj(404,{'error':'not_found'})
        except PermissionError as e: return self.sendj(403,{'error':str(e)})
        except (ValueError,KeyError,json.JSONDecodeError) as e: return self.sendj(422,{'error':str(e)})
        except (ConnectionError,TimeoutError) as e: return self.disconnected(e)
        except Exception as e:
            correlation=uid()
            traceback.print_exception(type(e),e,e.__traceback__,file=sys.stderr)
            sys.stderr.write('correlation_id=%s path=%s error=%s\n'%(correlation,p,type(e).__name__)); sys.stderr.flush()
            return self.sendj(500,{'error':'internal_error','correlation_id':correlation})
    def do_DELETE(self):
        p=urlparse(self.path).path
        try:
            m=re.fullmatch(r'/api/v2/agents/([a-z0-9-]+)/llm-binding',p)
            if m:
                principal=self.require('AGENT_REGISTRY_ADMIN','GOVERNANCE_ADMIN')
                agent_id=m.group(1)
                a=repo.one('select agent_id from agent_registry where agent_id=?',(agent_id,))
                if not a: return self.sendj(404,{'error':'agent_not_found'})
                llm_bindings.revert(agent_id,principal.identity)
                ledger.append('',principal.identity,'AGENT_LLM_BINDING_REVERTED',{'agent_id':agent_id})
                return self.sendj(200,{'agent_id':agent_id,'status':'reverted_to_default'})
            m=re.fullmatch(r'/api/v2/agent-templates/([^/]+)',p)
            if m:
                self.principal()
                if not templates_svc.get(m.group(1)): return self.sendj(404,{'error':'template_not_found'})
                templates_svc.delete(m.group(1))
                return self.sendj(200,{'id':m.group(1),'deleted':True})
            return self.sendj(404,{'error':'not_found'})
        except PermissionError as e: return self.sendj(403,{'error':str(e)})
        except (AgentLLMBindingError,ValueError,KeyError) as e: return self.sendj(422,{'error':str(e)})
        except Exception as e:
            correlation=uid()
            traceback.print_exception(type(e),e,e.__traceback__,file=sys.stderr)
            sys.stderr.write('correlation_id=%s path=%s error=%s\n'%(correlation,p,type(e).__name__)); sys.stderr.flush()
            return self.sendj(500,{'error':'internal_error','correlation_id':correlation})
    def log_message(self,*a): pass

if __name__=='__main__':
    os.chdir(BASE)
    # The container publishes its port deliberately, so the default stays
    # 0.0.0.0. A local run should not put the governance API on the LAN, so
    # start.ps1 sets HOST=127.0.0.1.
    host=os.getenv('HOST','0.0.0.0')
    if not os.getenv('FACTORY_JWT_HS256_SECRET'):
        # No longer a fault: the key is generated and persisted on first run, so
        # say where it went rather than warning that authentication is broken.
        sys.stderr.write('note: FACTORY_JWT_HS256_SECRET is unset; using the generated signing key beside the database\n')
    if not repo.one('select id from users limit 1'):
        sys.stderr.write('note: no accounts exist yet; register the first one in the console to receive administration roles\n')
    if os.getenv('FACTORY_DISABLE_WORKER','').lower() not in ('1','true','yes'):
        # A daemon thread rather than a separate process: the existing
        # ThreadingHTTPServer already runs concurrent request threads over
        # this same repo, so one more thread doing its own repo.connect()
        # calls needs no new concurrency primitive. FACTORY_DISABLE_WORKER
        # exists for a deployment that instead runs `python -m app.worker`
        # as its own process against the same database.
        from app.worker import WorkerLoop
        worker=WorkerLoop(repo,agents,pipelines,repo_clone,workflow)
        threading.Thread(target=worker.run_forever,name='pipeline-worker',daemon=True).start()
        sys.stderr.write('pipeline worker running in-process (set FACTORY_DISABLE_WORKER=1 to run it separately)\n')
    sys.stderr.write('ACE Migration Accelerator listening on http://%s:%s\n'%(host,os.getenv('PORT','8080')))
    sys.stderr.flush()
    ThreadingHTTPServer((host,int(os.getenv('PORT','8080'))),H).serve_forever()
