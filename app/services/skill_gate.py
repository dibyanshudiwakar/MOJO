"""The code-generation skill's own checks, run mechanically.

``agent-skills/iib-to-ms-code-generation`` says the code-generation agent "owns
the quality gate over what it produced", and ``references/gap-checklist.md``
says what that gate is: BLOCKING items (will fail to start or corrupt data),
HIGH and MEDIUM items, and, for HANDOFF, two *mandatory mechanical* checks. Each
item carries the shell command that checks it. This module is those commands.

The gate used to check something else: a ``pom.xml`` at the project root, a
secret regex, a URL regex, a provenance file. The first failed every multi-module
layout the skill prescribes, and the third failed a project for the Javadoc
mention of an XML namespace and for the line that disables DOCTYPE declarations
(the standard XXE protection). None of it was the skill's.

Severities are the skill's, not invented here:

* **BLOCKING** -> ``FAIL``. The skill says these stop the service starting or
  corrupt data.
* **HIGH / MEDIUM** -> ``GAP``. The skill's instruction for these is "auto-fix
  what's unambiguous; report the rest as named GAPs with a remediation path --
  never silently drop a flagged item", and HANDOFF-B says to print residual open
  items as named GAPs, not to refuse the hand-off. A GAP is recorded with its
  checklist id and lands in the run's action items; it does not fail the gate.

Every check is applied only where the skill applies it: backend-specific items
run only for the backend type the project declares.

What is deliberately *not* here: anything the skill says needs judgment (byte
offsets against a copybook, validation order against the ESQL, whether Akeyless
is genuinely required). Those cannot be settled by grep, and a check that guesses
is the failure this replaces.
"""
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2] / 'agent-skills' / 'iib-to-ms-code-generation'
BASELINE = SKILL / 'references' / 'templates' / 'config-map-baseline.properties'

#: Per finding list, per check, so one noisy check cannot bury the rest.
MAX_EVIDENCE = 12

#: A key at the start of a line, exactly as the skill's own ``keys()`` shell
#: function reads them (``grep -oE '^[a-zA-Z][a-zA-Z0-9._-]*='``).
KEY = re.compile(r'^([a-zA-Z][a-zA-Z0-9._-]*)=', re.M)

#: Hosts whose ``http://`` URLs identify a standard, a namespace or a parser
#: feature rather than a service to call. ``setFeature("http://apache.org/xml/
#: features/disallow-doctype-decl", true)`` is XXE protection and the gate used
#: to fail the project for having it.
SPEC_URIS = re.compile(
    r'https?://(?:localhost|example|www\.w3\.org|apache\.org/xml|xml\.org/sax|xmlns\.jcp\.org'
    r'|java\.sun\.com|schemas\.xmlsoap\.org|schemas\.xmlsoap|www\.springframework\.org'
    r'|maven\.apache\.org|json-schema\.org|javax\.xml|www\.opengis\.net)')
URL = re.compile(r'https?://\S+')

BLOCKING, HIGH, MEDIUM = 'BLOCKING', 'HIGH', 'MEDIUM'


# ---- reading a project ---------------------------------------------------------------------

def strip_java_comments(source):
    """Source with ``//``, ``/* */`` and Javadoc removed, string literals intact.

    String-aware, because ``"http://..."`` contains ``//`` and a naive strip
    would cut every URL in half and make the endpoint scan blind.
    """
    out, i, n = [], 0, len(source)
    while i < n:
        c, two = source[i], source[i:i + 2]
        if c == '"':
            j = i + 1
            while j < n and source[j] != '"':
                j += 2 if source[j] == '\\' else 1
            out.append(source[i:j + 1])
            i = j + 1
        elif c == "'":
            j = i + 1
            while j < n and source[j] != "'":
                j += 2 if source[j] == '\\' else 1
            out.append(source[i:j + 1])
            i = j + 1
        elif two == '//':
            while i < n and source[i] != '\n':
                i += 1
        elif two == '/*':
            end = source.find('*/', i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def strip_properties_comments(text):
    """Property and YAML text without whole-line ``#`` comments."""
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))


def keys(text):
    return set(KEY.findall(text))


XML_COMMENT = re.compile(r'<!--.*?-->', re.S)
ARTIFACT = re.compile(r'<artifactId>\s*([^<\s]+)\s*</artifactId>')


def artifacts(pom_text):
    """The artifactIds a pom declares, ignoring comments.

    A generated pom explains itself in comments ("NO framework-connector-qlcics,
    NO camel-spring-boot-starter (QL/CICS only)"), so searching the raw text for
    a dependency name finds the sentence saying it is absent.
    """
    return set(ARTIFACT.findall(XML_COMMENT.sub('', pom_text)))


class Project:
    """A generated repository root, read once."""

    def __init__(self, root):
        self.root = Path(root)
        children = [d for d in sorted(self.root.glob('*')) if d.is_dir()]
        self.parent_modules = [d for d in children if d.name.endswith('-parent')]
        self.web_modules = [d for d in children if (d / 'pom.xml').exists() and (d / 'src').is_dir()
                            and d not in self.parent_modules]
        if not self.web_modules and (self.root / 'pom.xml').exists():
            self.web_modules = [self.root]
        self._cache = {}

    def read(self, path):
        path = Path(path)
        if path not in self._cache:
            try:
                self._cache[path] = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                self._cache[path] = ''
        return self._cache[path]

    def rel(self, path):
        try:
            return Path(path).relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def java(self, *subdirs):
        """Main-source Java files, optionally only those under a package fragment."""
        out = []
        for web in self.web_modules:
            for f in sorted((web / 'src' / 'main' / 'java').rglob('*.java')):
                if not subdirs or any(('/' + s + '/') in f.as_posix() for s in subdirs):
                    out.append(f)
        return out

    def code(self, f):
        return strip_java_comments(self.read(f))

    def dependencies(self, web=None):
        """Declared artifactIds of one web module, or of all of them."""
        mods = [web] if web else self.web_modules
        return set().union(*(artifacts(self.read(w / 'pom.xml')) for w in mods)) if mods else set()

    def packaged(self, name):
        return [w / 'src' / 'main' / 'resources' / name for w in self.web_modules
                if (w / 'src' / 'main' / 'resources' / name).exists()]

    @property
    def config_maps(self):
        d = self.root / 'config' / 'config-map'
        return sorted(d.glob('application.*.properties')) if d.is_dir() else []

    def backend(self):
        """The backend type, as the project itself declares it.

        Read from the dependencies the generated code actually uses rather than
        passed in: the gate also runs from the feedback agent's re-validation,
        which has no semantic model to hand.
        """
        deps = self.dependencies()
        if 'framework-connector-qlcics' in deps:
            return 'QL_CICS'
        if 'framework-mqclient' in deps:
            return 'MQ_COBOL'
        return ''


# ---- the checks ----------------------------------------------------------------------------
# Each takes a Project and returns a list of findings; empty means the item holds.

def b4_framework_restclient(p):
    """[B4] ``framework-restclient`` is in the web pom regardless of backend type."""
    return ['%s has no framework-restclient dependency (source of EieHeader, ErrorUtil, ErrorStatus)'
            % p.rel(w / 'pom.xml') for w in p.web_modules
            if 'framework-restclient' not in p.dependencies(w)]


def b3_camel_starter(p):
    """[B3] QL_CICS only: ``camel-spring-boot-starter``, not ``camel-core`` alone."""
    return ['%s has no camel-spring-boot-starter' % p.rel(w / 'pom.xml') for w in p.web_modules
            if 'camel-spring-boot-starter' not in p.dependencies(w)]


def b1_ql_connector_uses_qlconnector(p):
    """[B1] QL_CICS only: the connector package uses QlConnector, no RestClient/RestTemplate/WebClient."""
    return ['%s uses a generic HTTP client' % p.rel(f) for f in p.java('connector')
            if re.search(r'\b(RestClient|RestTemplate|WebClient)\b', p.code(f))]


def rule4_no_resttemplate(p):
    """[Rule 4] No ``RestTemplate``; ``RestClientWrapper`` instead."""
    return ['%s uses RestTemplate' % p.rel(f) for f in p.java() if re.search(r'\bRestTemplate\b', p.code(f))]


def mq1_mq_connector_family(p):
    """[MQ1] MQ_COBOL only: ``framework-mqclient``/``MQUtility``, never the QL connector family."""
    found = []
    deps = p.dependencies()
    if 'framework-connector-qlcics' in deps:
        found.append('web pom depends on framework-connector-qlcics, a different connector family')
    if 'framework-mqclient' not in deps and not any('MQUtility' in p.code(f) for f in p.java()):
        found.append('neither framework-mqclient nor MQUtility is used')
    return found


def mq2_no_bindy(p):
    """[MQ2] MQ_COBOL only: no Bindy; manual EBCDIC offset writes instead."""
    return ['%s uses Camel Bindy' % p.rel(f) for f in p.java()
            if re.search(r'@FixedLengthRecord|BindyFixedLengthDataFormat', p.code(f))]


def m1_m2_config_map(p):
    """[M1][M2] ``config/config-map/application.{env}-{cluster}.properties``, outside src/main/resources."""
    found = []
    if not p.config_maps:
        found.append('config/config-map/ has no application.<env>-<cluster>.properties')
    for f in p.config_maps:
        if not re.fullmatch(r'application\.[a-z0-9]+-[a-z0-9]+\.properties', f.name):
            found.append('%s does not match application.{env}-{cluster}.properties' % p.rel(f))
    for w in p.web_modules:
        for f in (w / 'src' / 'main' / 'resources').glob('application.*-*.properties'):
            found.append('%s is bundled in the jar; config-maps must sit outside src/main/resources' % p.rel(f))
    return found


def m5_m16_packaged_properties(p):
    """[M5][M16] ``application.properties`` is tiny and holds the MVC keys; ``application-local`` is the big one."""
    found = []
    for web in p.web_modules:
        base = web / 'src' / 'main' / 'resources' / 'application.properties'
        local = web / 'src' / 'main' / 'resources' / 'application-local.properties'
        if not base.exists():
            found.append('%s is missing' % p.rel(base))
            continue
        text = p.read(base)
        if not re.search(r'^spring\.mvc\.throw-exception-if-no-handler-found\s*=\s*true', text, re.M):
            found.append('%s lacks spring.mvc.throw-exception-if-no-handler-found=true [M5]' % p.rel(base))
        lines = lambda path: len([x for x in p.read(path).splitlines() if x.strip() and not x.lstrip().startswith('#')])
        if local.exists() and lines(base) > lines(local):
            found.append('application.properties (%d lines) is larger than application-local.properties (%d): '
                         'they look inverted [M16]' % (lines(base), lines(local)))
    return found


def m7_m10_helm(p):
    """[M7][M10] The Helm chart: Chart.yaml, the four templates, one values file per config-map, SCREAMING_SNAKE keys."""
    deploy = p.root / 'config' / 'deploy-config'
    if not deploy.is_dir():
        return ['config/deploy-config/ is missing: the Helm chart is what actually deploys the service']
    found = []
    if not (deploy / 'Chart.yaml').exists():
        found.append('config/deploy-config/Chart.yaml is missing')
    have = {f.name.lower() for f in (deploy / 'templates').glob('*')} if (deploy / 'templates').is_dir() else set()
    for want in ('deployment.yaml', 'route.yaml', 'secret.yaml', 'service.yaml'):
        if want not in have:
            found.append('config/deploy-config/templates/%s is missing' % want.capitalize())
    envs = {re.sub(r'^application\.|\.properties$', '', f.name) for f in p.config_maps}
    values = {re.sub(r'^values\.|\.yaml$', '', f.name): f for f in deploy.glob('values.*.yaml')}
    for env in sorted(envs - set(values)):
        found.append('no values.%s.yaml for config-map %s' % (env, env))
    for env, f in sorted(values.items()):
        missing = [k for k in ('APP_NAME', 'PODS', 'CONTEXT_ROOT', 'ENV', 'CLUSTER') if not re.search(
            r'^%s\s*:' % k, p.read(f), re.M)]
        if missing:
            found.append('%s lacks %s (the org schema is flat SCREAMING_SNAKE_CASE)' % (p.rel(f), ', '.join(missing)))
    return found


def m9_github(p):
    """[M9] ``.github/config/DeployMetadata.json`` and ``.github/workflows/{build,deploy}.yaml`` at the repo root."""
    return ['%s is missing' % rel for rel in (
        '.github/config/DeployMetadata.json', '.github/workflows/build.yaml', '.github/workflows/deploy.yaml')
        if not (p.root / rel).exists()]


def a0_baseline_keys(p):
    """[HANDOFF-A0][M11] Every key in the skill's baseline template, in the packaged files and every config-map.

    The skill calls this "mandatory, mechanical, run first", and compares
    against the physical template file rather than a retyped list.
    """
    if not BASELINE.exists():
        return []
    want = keys(BASELINE.read_text(encoding='utf-8'))
    found = []
    packaged = set()
    for name in ('application.properties', 'application-local.properties'):
        for f in p.packaged(name):
            packaged |= keys(p.read(f))
    if p.web_modules and want - packaged:
        found.append('packaged application(-local).properties lacks: %s' % ', '.join(sorted(want - packaged)))
    by_missing = {}
    for f in p.config_maps:
        by_missing.setdefault(tuple(sorted(want - keys(p.read(f)))), []).append(f.name)
    for missing, names in by_missing.items():
        if missing:
            found.append('%d config-map(s) lack %d baseline key(s) (%s): %s' % (
                len(names), len(missing), ', '.join(names[:3]) + (' ...' if len(names) > 3 else ''),
                ', '.join(missing)))
    return found


def m15_reconcile_runtime_keys(p):
    """[HANDOFF-A][M15] Runtime keys in the packaged file also exist in every config-map.

    ``SPRING_CONFIG_LOCATION`` replaces the classpath defaults in a deployed
    pod, so a key present only in the packaged file silently vanishes there.
    ``management.endpoints.web.exposure.include`` is mandatory in each
    (otherwise ``info`` and ``metrics`` return 404); ``spring.profiles.active``
    is local-only and must never be copied.
    """
    found = []
    runtime = set()
    for f in p.packaged('application.properties'):
        runtime |= keys(p.read(f))
    runtime.discard('spring.profiles.active')
    for f in p.config_maps:
        have = keys(p.read(f))
        missing = sorted(runtime - have)
        if missing:
            found.append('%s lacks packaged key(s): %s' % (f.name, ', '.join(missing)))
        if 'management.endpoints.web.exposure.include' not in have:
            found.append('%s lacks management.endpoints.web.exposure.include (info/metrics return 404)' % f.name)
        if re.search(r'^spring\.profiles\.active\s*=\s*local', p.read(f), re.M):
            found.append('%s copies spring.profiles.active=local, which activates the local profile in prod' % f.name)
    return found


def dockerfile_template(p):
    """[Rule 3] A full Dockerfile at the repo root, matching the structural template's invariants."""
    path = p.root / 'Dockerfile'
    if not path.exists():
        return ['Dockerfile is missing from the repo root (not inside the web module)']
    text = '\n'.join(line for line in p.read(path).splitlines() if not line.lstrip().startswith('#'))
    found = []
    labels = re.findall(r'^LABEL\s+(\w+)=', text, re.M)
    for want in ('LINE_OF_BUSINESS', 'maintainer', 'Name', 'Version'):
        if want not in labels:
            found.append('Dockerfile lacks LABEL %s' % want)
    if not re.search(r'^USER\s+1001\b', text, re.M):
        found.append('Dockerfile does not drop to USER 1001')
    if re.search(r'^EXPOSE\b', text, re.M):
        found.append('Dockerfile has an EXPOSE directive (the template has none)')
    if not re.search(r'^COPY\s+\./[^\s]+-web/target/', text, re.M):
        found.append("Dockerfile's COPY does not take the web module's target/ jar")
    return found


def m12_root_name(p):
    """[M12] The top-level directory is ``EIPMS_cvs-v1-<service>-oci``."""
    return [] if re.fullmatch(r'EIPMS_cvs-v1-.+-oci', p.root.name) else [
        "top-level directory '%s' is not EIPMS_cvs-v1-<service>-oci" % p.root.name]


def m13_akeyless(p):
    """[M13] Akeyless scaffolding is live or absent, never present-but-disabled."""
    found = []
    for f in sorted((p.root / 'config' / 'deploy-config').glob('values.*.yaml')):
        # A key, not a mention: values files explain in comments that Akeyless is
        # *not* generated, and that is the correct, absent state.
        for value in re.findall(r'^\s*AKEYLESS_ENABLED\s*:\s*(\S+)', strip_properties_comments(p.read(f)), re.M):
            if value.strip('\'"').lower() != 'true':
                found.append('%s sets AKEYLESS_ENABLED to %s, present but not live' % (p.rel(f), value))
    return found


def m18_circuit_breaker(p):
    """[M18] ``application.circuitbreaker.*`` properties need an ``isCircuitBreakerError()`` branch."""
    configured = any('application.circuitbreaker.' in p.read(f) for f in
                     p.config_maps + [x for n in ('application.properties', 'application-local.properties')
                                      for x in p.packaged(n)])
    if configured and not any('isCircuitBreakerError' in p.code(f) for f in p.java()):
        return ['circuit-breaker properties are configured but no isCircuitBreakerError() branch exists']
    return []


def m19_no_token_validator(p):
    """[M19][Rule 9] No Kong/gateway token-validator reimplementation."""
    return ['%s references a token validator' % p.rel(f) for f in p.java()
            if re.search(r'(?i)tokenvalidator|kongeieusercontext|TokenValidat', p.code(f))]


def m20_controller_delegates_only(p):
    """[M20] A controller contains no error-response calls and no ``try`` blocks."""
    return ['%s has %s in the controller' % (p.rel(f), what) for f in p.java('controller')
            for what, pat in (('ErrorUtil.errorResponse', r'ErrorUtil\.errorResponse'), ('a try block', r'\btry\s*\{'))
            if re.search(pat, p.code(f))]


def m21_no_exception_package(p):
    """[M21][Rule 10] No custom exception class, no ``exception`` package, no ``@RestControllerAdvice``."""
    found = []
    for web in p.web_modules:
        for d in (web / 'src' / 'main' / 'java').rglob('*'):
            if d.is_dir() and d.name.lower() == 'exception':
                found.append('%s is an exception package' % p.rel(d))
    for f in p.java():
        code = p.code(f)
        if re.search(r'\bextends\s+(RuntimeException|Exception)\b', code):
            found.append('%s defines a custom exception' % p.rel(f))
        if '@RestControllerAdvice' in code:
            found.append('%s is a @RestControllerAdvice' % p.rel(f))
    return found


def m22_xml_package(p):
    """[M22] Backend XML models are a top-level ``model.xml`` package, never ``swagger.model.xml``."""
    return ['%s nests XML models under swagger/model' % p.rel(d) for web in p.web_modules
            for d in (web / 'src' / 'main' / 'java').rglob('xml') if d.is_dir() and d.parent.name == 'model'
            and d.parent.parent.name == 'swagger']


def m23_local_targets_qa(p):
    """[M23][Rule 12] ``application-local.properties`` internal URIs target qa/qa2, never dev/prod."""
    return ['%s:%d targets %s' % (p.rel(f), i, 'dev' if 'https://dev' in line else 'prod')
            for f in p.packaged('application-local.properties')
            for i, line in enumerate(p.read(f).splitlines(), 1)
            if not line.lstrip().startswith('#') and re.search(r'\.uri\s*=\s*https://(dev|prod)', line)]


def m24_no_parent_target(p):
    """[M24] No ``target/`` left under the parent module."""
    return ['%s exists' % p.rel(d / 'target') for d in p.parent_modules if (d / 'target').exists()]


def m26_settings_xml(p):
    """[M26][Rule 14] ``settings.xml`` at the repo root, identical to the skill's template."""
    from app.services import maven_settings
    path = p.root / maven_settings.NAME
    if not path.is_file():
        return ['%s is missing' % maven_settings.NAME]
    want = maven_settings.template()
    if want is not None and p.read(path).replace('\r\n', '\n') != want:
        return ['%s differs from the skill template' % maven_settings.NAME]
    return []


#: (id, severity, skill reference, applies-to backend or None, function, remediation)
CHECKS = [
    ('B4', BLOCKING, 'gap-checklist.md [B4]', None, b4_framework_restclient,
     'Add framework-restclient to the web pom; the build breaks without it.'),
    ('B3', BLOCKING, 'gap-checklist.md [B3]', 'QL_CICS', b3_camel_starter,
     'Add camel-spring-boot-starter; Bindy needs the auto-configured CamelContext.'),
    ('B1', BLOCKING, 'gap-checklist.md [B1]', 'QL_CICS', b1_ql_connector_uses_qlconnector,
     'Use QlConnector in the connector package.'),
    ('R4', MEDIUM, 'mandatory-migration-rules.md Rule 4', None, rule4_no_resttemplate,
     'Use RestClientWrapper. A legacy RestTemplate bean is only acceptable where the sibling repo does the same.'),
    ('MQ1', MEDIUM, 'gap-checklist.md [MQ1]', 'MQ_COBOL', mq1_mq_connector_family,
     'Use framework-mqclient / MQUtility, not the QL connector family.'),
    ('MQ2', MEDIUM, 'gap-checklist.md [MQ2]', 'MQ_COBOL', mq2_no_bindy,
     'Use manual EBCDIC offset writes; Bindy belongs to the QL backend.'),
    ('M1-M2', MEDIUM, 'gap-checklist.md [M1][M2]', None, m1_m2_config_map,
     'Put application.<env>-<cluster>.properties under config/config-map/.'),
    ('M5-M16', MEDIUM, 'gap-checklist.md [M5][M16]', None, m5_m16_packaged_properties,
     'Keep application.properties tiny (profile + MVC keys) and put the bulk in application-local.properties.'),
    ('M7-M10', MEDIUM, 'gap-checklist.md [M7][M10]; structural-conventions.md section 13', None, m7_m10_helm,
     'Generate config/deploy-config/ with the org flat values schema.'),
    ('M9', MEDIUM, 'gap-checklist.md [M9]', None, m9_github,
     'Generate .github/ from structural-conventions.md section 2.'),
    ('A0', MEDIUM, 'workflow HANDOFF-A0; gap-checklist.md [M11]', None, a0_baseline_keys,
     'Copy the block from references/templates/config-map-baseline.properties into the packaged file and every config-map.'),
    ('A', MEDIUM, 'workflow HANDOFF-A; gap-checklist.md [M15]', None, m15_reconcile_runtime_keys,
     'Copy each listed key into that config-map, or classify it local-only. Never copy spring.profiles.active.'),
    ('R3', MEDIUM, 'mandatory-migration-rules.md Rule 3', None, dockerfile_template,
     'Use the exact Dockerfile template at the repo root (structural-conventions.md section 3).'),
    ('M12', MEDIUM, 'gap-checklist.md [M12]', None, m12_root_name,
     'Name the top-level directory EIPMS_cvs-v1-<service>-oci.'),
    ('M13', MEDIUM, 'gap-checklist.md [M13]', None, m13_akeyless,
     'Either remove the Akeyless scaffolding or set AKEYLESS_ENABLED: true with the Rule 6 form.'),
    ('M18', MEDIUM, 'gap-checklist.md [M18]', None, m18_circuit_breaker,
     'Add the isCircuitBreakerError() branch or remove the dead properties; flag for human confirmation.'),
    ('M19', MEDIUM, 'gap-checklist.md [M19]; Rule 9', None, m19_no_token_validator,
     'Delete it; Kong enforces token validation upstream.'),
    ('M20', MEDIUM, 'gap-checklist.md [M20]', None, m20_controller_delegates_only,
     'Move the logic to the *Service layer; a controller is one service call and a ResponseEntity.'),
    ('M21', MEDIUM, 'gap-checklist.md [M21]; Rule 10', None, m21_no_exception_package,
     'Replace with a direct ErrorUtil.errorResponse(...) at the point of failure.'),
    ('M22', MEDIUM, 'gap-checklist.md [M22]', None, m22_xml_package,
     'Move the package up one level to model.xml.'),
    ('M23', MEDIUM, 'gap-checklist.md [M23]; Rule 12', None, m23_local_targets_qa,
     'Swap the hostname prefix to qa (or qa2).'),
    ('M24', MEDIUM, 'gap-checklist.md [M24]', None, m24_no_parent_target,
     'rm -rf <service>-oci-parent/target (not the web module\'s).'),
    ('M26', MEDIUM, 'gap-checklist.md [M26]; Rule 14', None, m26_settings_xml,
     'Copy references/templates/maven-settings.xml to the repo root as settings.xml.'),
]


def run(root):
    """Every applicable skill check as a list of gate-check dicts.

    ``status`` is ``PASS``, ``GAP`` or ``FAIL`` (BLOCKING only). A check the
    skill does not apply to this backend type is omitted rather than reported
    as passing, so the list says what was actually asserted.
    """
    project = Project(root)
    backend = project.backend()
    out = []
    for check_id, severity, ref, only, fn, remediation in CHECKS:
        if only and only != backend:
            continue
        findings = fn(project)
        status = 'PASS' if not findings else ('FAIL' if severity == BLOCKING else 'GAP')
        entry = {'name': 'SKILL_' + check_id.replace('-', '_'), 'status': status, 'severity': severity,
                 'skill_ref': ref}
        if findings:
            entry['evidence'] = findings[:MAX_EVIDENCE] + (
                ['... and %d more' % (len(findings) - MAX_EVIDENCE)] if len(findings) > MAX_EVIDENCE else [])
            entry['remediation'] = remediation
        out.append(entry)
    return out


def hardcoded_endpoints(files):
    """URLs in code and config that look like a service to call, not a standard.

    ``files`` are ``Path`` objects. Comments are ignored (the Javadoc line that
    names an XML namespace is documentation), and so are URLs that identify a
    spec, a namespace or a parser feature (:data:`SPEC_URIS`).
    """
    hits = []
    for f in files:
        try:
            text = Path(f).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        text = strip_java_comments(text) if Path(f).suffix.lower() == '.java' else strip_properties_comments(text)
        for i, line in enumerate(text.splitlines(), 1):
            for match in URL.finditer(line):
                if not SPEC_URIS.match(match.group(0)):
                    hits.append('%s:%d %s' % (Path(f).name, i, match.group(0)[:100]))
    return hits
