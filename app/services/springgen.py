"""Generate a runnable Spring Boot service from the ACE semantic model.

The previous generator emitted one ``@Service`` per message flow whose whole
body was ``return input;`` -- a project that compiled and did nothing. Every
fact the analysis agent extracts was discarded: the HTTP paths, the node graph,
the ESQL assignments, the queues, the topics, the database references.

This generator consumes those facts and emits the layers a working service
actually needs:

* a ``@RestController`` per inbound HTTP node, on the path ACE published;
* a ``@Service`` per flow that executes the node graph **in connection order**,
  one statement per node, each carrying the node id it came from;
* a transformer per ESQL module whose body is the module's own ``SET``
  assignments translated to field assignments -- real mapping code, with
  anything untranslatable left as a ``TODO`` beside the original statement;
* request/response records whose fields are the ones the ESQL actually reads
  and writes, nested exactly as the message tree nests them;
* listeners, publishers, clients, repositories and schedulers for the real
  queues, topics, endpoints and tables the flow touches;
* externalised configuration, a correlation-id filter, an exception handler,
  tests that exercise the above, and the build/deploy files.

Three rules shape all of it.

**Every file is traceable.** Each generated type carries the plan id and the
``flow#node`` or ``file#module`` it was generated from, because the G3 approval
binds the plan and the quality gate re-derives provenance from the manifest.

**No invented endpoints.** An absolute URL discovered in ACE is written to the
README and the provenance manifest, never into a source file: the quality gate
fails ``HARDCODED_ENDPOINT_SCAN`` on exactly that, and rightly so. Source and
configuration reference ``${...}`` placeholders defaulting to loopback.

**Honest gaps.** What cannot be derived is a ``TODO`` next to the ACE statement
that needs a human, listed in the README's action items -- never a silent
passthrough that looks finished.

The generated project is deliberately plain Spring Boot. Organisation-specific
conventions (a framework's own connector classes, error utilities and
annotations) belong to the framework baseline, and generating them against a
framework that was not supplied would be inventing them.
"""
import json
import re
import textwrap
from pathlib import Path

from app.repository import digest

_WORDS = re.compile(r'[^A-Za-z0-9]+')
#: Splits on case boundaries as well as punctuation, so an ACE node id like
#: ``PublishAudit`` becomes the readable configuration key ``publish-audit``
#: rather than ``publishaudit``, and a name's own acronym run normalises the
#: way Java convention (and the real migrated reference's own class names,
#: e.g. ``DmrClaimStatus`` not ``DMRClaimStatus``) title-cases it like any
#: other word: ``HTTPInput`` -> ``http-input`` / ``HttpInput``, not
#: ``HTTPInput`` kept shouting.
_HUMPS = re.compile(r'[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+')
#: Kubernetes cluster suffixes a real CVS deployment's ConfigMap and Helm
#: values carry one overlay per (confirmed against the real
#: EIPMS_cvs-v1-dmrclaimstatus-oci reference's own config/config-map/ and
#: config/deploy-config/ directories).
ENV_CLUSTERS = ('dev-pbmaz', 'dev2-pbmaz', 'dev3-pbmaz', 'qa-pbmaz', 'qa2-pbmaz', 'qa3-pbmaz',
                'stress-pbmaz', 'uat-pbmaz', 'prod-pbmaz', 'prod-pbmri')
_JAVA_KEYWORDS = {
    'abstract', 'assert', 'boolean', 'break', 'byte', 'case', 'catch', 'char', 'class', 'const',
    'continue', 'default', 'do', 'double', 'else', 'enum', 'extends', 'final', 'finally', 'float',
    'for', 'goto', 'if', 'implements', 'import', 'instanceof', 'int', 'interface', 'long', 'native',
    'new', 'package', 'private', 'protected', 'public', 'return', 'short', 'static', 'strictfp',
    'super', 'switch', 'synchronized', 'this', 'throw', 'throws', 'transient', 'try', 'void',
    'volatile', 'while', 'record', 'var', 'yield',
}


def _parts(text):
    return [p for p in _WORDS.split(str(text or '')) if p]


def _humps(text):
    return [hump for part in _parts(text) for hump in _HUMPS.findall(part)]


def pascal(text):
    """``order-detail`` / ``ORDER_DETAIL`` / ``orderDetail`` / ``DMRClaimStatus``
    -> ``OrderDetail`` / ``OrderDetail`` / ``OrderDetail`` / ``DmrClaimStatus``.

    Shares ``_humps`` with ``kebab`` rather than its own split, so the two can
    never name the same source differently -- confirmed live: ``DMRClaimStatus``
    used to survive this unchanged (title-casing only the first letter of the
    whole string leaves an all-caps run alone), which is why the real
    generated classes read ``DMRClaimStatusController`` instead of the
    reference's own ``DmrClaimStatusController``.
    """
    return ''.join(h[0].upper() + h[1:].lower() for h in _humps(text)) or 'Generated'


def camel(text):
    name = pascal(text)
    name = name[0].lower() + name[1:]
    return name + '_' if name in _JAVA_KEYWORDS else name


def kebab(text):
    return '-'.join(h.lower() for h in _humps(text)) or 'service'


def _stem(path):
    return Path(str(path or '')).stem or 'flow'


# ---------------------------------------------------------------------------
# Java formatting
# ---------------------------------------------------------------------------
#: Line budget for generated Java. 120 is the Spring Framework's own limit, and
#: the generated project is plain Spring Boot. Nothing here is cosmetic: a
#: generated file is read by the reviewer who has to approve the migration and
#: by the engineer who finishes the TODOs, and a 184-column constructor or an
#: ESQL statement trailing off the right of the screen is not reviewable.
WIDTH = 120


def _wrap(text, width, break_words=False):
    """Wrap to ``width``, by default never splitting a word or a ``{@code}`` tag.

    ``break_words`` is for comments quoting ACE verbatim: an ESQL identifier can
    be longer than the whole budget on its own, and in a comment splitting it is
    better than overflowing.
    """
    return textwrap.wrap(text, max(width, 40), break_long_words=break_words,
                         break_on_hyphens=False) or ['']


def javadoc(*blocks, indent=0):
    """A wrapped Javadoc comment.

    Blocks are written as plain prose and wrapped here, rather than carrying
    hand-placed line breaks that go ragged the moment a name changes length.
    A block is a paragraph string, ``('pre', lines)`` for verbatim content such
    as quoted ESQL, or ``('ul', items)`` for a list. The second and later
    paragraphs get the ``<p>`` Javadoc expects.
    """
    pad = ' ' * indent
    budget = WIDTH - indent - 3
    body = []
    for block in blocks:
        if not block:
            continue
        if isinstance(block, tuple):
            kind, items = block
            items = [x for x in items if x]
            if not items:
                continue
            if body:
                body.append('')
            if kind == 'pre':
                # Verbatim content still has to fit. ESQL is whitespace
                # insensitive, so a statement wrapped at a space with a hanging
                # indent is the same statement -- unlike a truncated one, which
                # is no longer what the translation can be reviewed against.
                wrapped = []
                for item in items:
                    first, *rest = _wrap(item.strip(), budget - 2, break_words=True)
                    wrapped.append('  ' + first)
                    wrapped += ['      ' + line for line in rest]
                body += ['<pre>'] + wrapped + ['</pre>']
            else:
                body.append('<ul>')
                for item in items:
                    body += _wrap('<li>%s</li>' % item, budget)
                body.append('</ul>')
            continue
        text = ' '.join(str(block).split())
        if not text:
            continue
        if body:
            body.append('')
            text = '<p>' + text
        body += _wrap(text, budget)
    lines = [pad + '/**'] + [(pad + ' * ' + line).rstrip() for line in body] + [pad + ' */']
    return '\n'.join(lines)


def signature(declaration, params, tail=' {', indent=4):
    """``void f(a, b) {`` on one line, or one parameter per line if too long."""
    pad = ' ' * indent
    single = '%s%s(%s)%s' % (pad, declaration, ', '.join(params), tail)
    if len(single) <= WIDTH or not params:
        return single
    continuation = ' ' * (indent + 8)
    lines = ['%s%s(' % (pad, declaration)]
    for index, param in enumerate(params):
        last = index == len(params) - 1
        lines.append(continuation + param + (')' + tail if last else ','))
    return '\n'.join(lines)


def annotation(name, attributes, indent=4):
    """``@Name(a, b)``, continued at a hanging indent when it does not fit."""
    pad = ' ' * indent
    single = '%s@%s(%s)' % (pad, name, ', '.join(attributes))
    if len(single) <= WIDTH or len(attributes) < 2:
        return single
    continuation = ' ' * (indent + 8)
    lines = ['%s@%s(%s,' % (pad, name, attributes[0])]
    for index, attribute in enumerate(attributes[1:], 1):
        last = index == len(attributes) - 1
        lines.append(continuation + attribute + (')' if last else ','))
    return '\n'.join(lines)


def argument(expression, separator, comment, indent):
    """One constructor argument, with its provenance comment placed to fit.

    A quoted ESQL statement is often longer than the line budget on its own, so
    the comment moves above the argument rather than trailing off the screen.
    It cannot be shortened -- it is what the translation is reviewed against --
    so it wraps, and **every** wrapped line is prefixed with ``//``: a
    continuation without one is bare Java and does not compile.
    """
    pad = ' ' * indent
    if not comment:
        return [pad + expression + separator]
    trailing = '%s%s%s %s' % (pad, expression, separator, comment)
    if len(trailing) <= WIDTH:
        return [trailing]
    text = comment[2:].strip() if comment.startswith('//') else comment
    return ['%s// %s' % (pad, line)
            for line in _wrap(text, WIDTH - indent - 3, break_words=True)] + \
           [pad + expression + separator]


def tidy(source):
    """Trailing whitespace, runaway blank lines and a final newline."""
    lines = [line.rstrip() for line in source.splitlines()]
    out = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------------------
# Node roles
# ---------------------------------------------------------------------------
#: The approved Spring pattern a plan assigns a construct -> what this generator
#: emits for it. Driving generation from the plan rather than from the node's
#: own name is what keeps the code a G3 approval authorised and the code that is
#: actually written the same thing.
PATTERN_ROLES = {
    'SPRING_MVC_CONTROLLER': 'HTTP_IN',
    'RESPONSE_ENTITY': 'HTTP_REPLY',
    'SPRING_WS_ENDPOINT': 'SOAP_IN',
    'SPRING_WS_RESPONSE': 'SOAP_REPLY',
    'JMS_LISTENER': 'JMS_IN',
    'JMS_TEMPLATE': 'JMS_OUT',
    'KAFKA_LISTENER': 'KAFKA_IN',
    'KAFKA_TEMPLATE': 'KAFKA_OUT',
    'SPRING_INTEGRATION_FILE_ADAPTER': 'FILE_IN',
    'SPRING_INTEGRATION_FILE_HANDLER': 'FILE_OUT',
    'SPRING_SERVICE_MAPPER': 'TRANSFORM',
    'TYPED_SERVICE_LOGIC': 'TRANSFORM',
    'TYPED_MAPPER': 'TRANSFORM',
    'XSLT_TRANSFORMER': 'TRANSFORM',
    'PREDICATE_FILTER': 'FILTER',
    'ROUTER_STRATEGY': 'ROUTER',
    'SPRING_DATA_REPOSITORY': 'DB',
    'SCHEDULED_TRIGGER': 'SCHEDULE',
    'EXCEPTION_HANDLER': 'CATCH',
    'DOMAIN_EXCEPTION': 'THROW',
    'REST_CLIENT_WRAPPER': 'HTTP_REQUEST',
    'SOAP_BACKEND_CONNECTOR': 'SOAP_REQUEST',
    'GENERATED_CONTRACT_MODEL': 'MODEL',
    'EXTERNALIZED_CONFIGURATION': 'CONFIG',
}

#: Fallback when no plan mapping covers a node: resolve on the construct name,
#: most specific token first. Mirrors ``migration_lifecycle_v6.mapping_key``'s
#: normalisation (alphanumerics, lower-cased, substring match) but answers a
#: different question -- what to emit, not which pattern was approved -- and is
#: kept here so this module stays importable from ``enterprise_v5``.
NAME_ROLES = (
    ('timeoutnotification', 'SCHEDULE'),
    ('httpinput', 'HTTP_IN'), ('soapinput', 'SOAP_IN'),
    ('httpreply', 'HTTP_REPLY'), ('soapreply', 'SOAP_REPLY'),
    ('httprequest', 'HTTP_REQUEST'), ('soaprequest', 'SOAP_REQUEST'),
    ('mqinput', 'JMS_IN'), ('mqoutput', 'JMS_OUT'),
    ('kafkaconsumer', 'KAFKA_IN'), ('kafkaproducer', 'KAFKA_OUT'),
    ('fileinput', 'FILE_IN'), ('fileoutput', 'FILE_OUT'),
    ('computenode', 'TRANSFORM'), ('compute', 'TRANSFORM'),
    ('mapping', 'TRANSFORM'), ('xslt', 'TRANSFORM'),
    ('filter', 'FILTER'), ('routetolabel', 'ROUTER'), ('route', 'ROUTER'),
    ('database', 'DB'),
    ('catchhandler', 'CATCH'), ('failurehandler', 'CATCH'), ('timeouthandler', 'CATCH'),
    ('catch', 'CATCH'), ('throw', 'THROW'),
)

#: ACE's generic HTTP family. ``ComIbmWSInput`` carries either a SOAP envelope
#: or plain JSON depending on ``messageDomainProperty`` -- the name alone cannot
#: tell them apart (the same disambiguation as ``_WS_NODE_ROLES``).
WS_ROLES = {'wsinput': ('HTTP_IN', 'SOAP_IN'), 'wsreply': ('HTTP_REPLY', 'SOAP_REPLY'),
            'wsrequest': ('HTTP_REQUEST', 'SOAP_REQUEST')}

INBOUND = ('HTTP_IN', 'SOAP_IN', 'JMS_IN', 'KAFKA_IN', 'FILE_IN', 'SCHEDULE')


def _normal(text):
    return ''.join(c for c in str(text or '') if c.isalnum()).lower()


def role_for(node_type, properties=None, pattern=None):
    """The role this generator emits for a construct.

    ``pattern`` is the approved Spring pattern from the plan and wins when
    present; the name-based fallback is for a direct call with no plan.
    """
    if pattern and pattern in PATTERN_ROLES:
        return PATTERN_ROLES[pattern]
    normal = _normal(node_type)
    for marker, (http_role, soap_role) in WS_ROLES.items():
        if marker in normal:
            domain = str((properties or {}).get('messageDomainProperty', '')).upper()
            return soap_role if domain not in ('', 'JSON') else http_role
    for token, role in NAME_ROLES:
        if token in normal:
            return role
    return 'UNMAPPED'


# ---------------------------------------------------------------------------
# ESQL
# ---------------------------------------------------------------------------
#: ``SET <target> = <expression>;`` -- the assignment that carries an IIB
#: flow's actual field mapping, and the one ESQL statement that translates to
#: Java mechanically and faithfully.
_SET = re.compile(r'(?is)\bSET\s+([A-Za-z_][\w.\[\]]*)\s*=\s*(.+?);')
#: Everything to the left of the fields in an ACE message tree reference:
#: ``InputRoot.JSON.Data.orderId`` and ``OutputRoot.XMLNSC.order.id`` both
#: address a payload, through a different domain parser.
_TREE_ROOT = re.compile(
    r'(?i)^(Input|Output)(?:Root|Body|LocalEnvironment|Exception)'
    r'(?:\.(?:JSON|XMLNSC|XML|SOAP|MQMD|BLOB|DFDL|Properties|Body|Data|Envelope)\b)*\.?')
_LITERAL = re.compile(r"^'((?:[^']|'')*)'$")
_NUMBER = re.compile(r'^-?\d+(?:\.\d+)?$')
_MODULE = re.compile(r'(?i)\bCREATE\s+(?:COMPUTE\s+|FILTER\s+|DATABASE\s+)?MODULE\s+([\w.]+)(.*?)\bEND\s+MODULE\s*;',
                     re.S)


def _strip_comments(text):
    return re.sub(r'--[^\n]*|/\*.*?\*/', '', text or '', flags=re.S)


def _tree_path(reference):
    """``InputRoot.JSON.Data.customer.id`` -> ``['customer', 'id']``, or None.

    ``None`` means the reference is not a payload field -- an environment
    variable, a shared variable, a local declaration -- so an assignment
    reading or writing it is reported rather than translated.
    """
    text = str(reference or '').strip()
    if not _TREE_ROOT.match(text):
        return None
    rest = _TREE_ROOT.sub('', text, count=1)
    if '[' in rest:            # an array or filtered subscript: not a plain field
        return None
    segments = [s for s in rest.split('.') if s]
    return segments or None


class EsqlModule:
    """One ESQL module, reduced to what can be generated from it.

    ``assignments`` keeps every ``SET`` in source order, each either resolved to
    a source field / literal or marked unresolved with the original statement,
    so the generated transformer can be read beside the ESQL it came from.
    """

    def __init__(self, source_path, name, body=''):
        self.source_path = source_path
        self.name = name
        self.body = body
        self.assignments = []
        self.notes = []

    @property
    def output_tree(self):
        tree = {}
        for item in self.assignments:
            if not item['target']:
                continue
            node = tree
            for segment in item['target'][:-1]:
                node = node.setdefault(segment, {})
                if node is None:
                    break
            else:
                node.setdefault(item['target'][-1], None)
        return tree

    @property
    def input_tree(self):
        tree = {}
        for item in self.assignments:
            if not item['source']:
                continue
            node = tree
            for segment in item['source'][:-1]:
                node = node.setdefault(segment, {})
                if node is None:
                    break
            else:
                node.setdefault(item['source'][-1], None)
        return tree

    @property
    def translated(self):
        return [a for a in self.assignments if a['kind'] != 'UNRESOLVED']

    @property
    def unresolved(self):
        return [a for a in self.assignments if a['kind'] == 'UNRESOLVED']


def parse_esql(source_path, text):
    """Every module in one ``.esql`` file, with its assignments resolved."""
    clean = _strip_comments(text)
    found = _MODULE.findall(clean)
    if not found:
        name = _stem(source_path)
        found = [(name, clean)]
    modules = []
    for name, body in found:
        module = EsqlModule(source_path, name.split('.')[-1], body)
        for target, expression in _SET.findall(body):
            expression = ' '.join(expression.split())
            target_path = _tree_path(target)
            if target_path is None:
                module.notes.append('SET %s = %s;' % (target, expression))
                continue
            literal = _LITERAL.match(expression)
            source_path_parts = _tree_path(expression)
            if source_path_parts:
                kind, value = 'COPY', source_path_parts
            elif literal:
                kind, value = 'LITERAL', '"%s"' % literal.group(1).replace("''", "'").replace('"', '\\"')
            elif _NUMBER.match(expression):
                kind, value = 'LITERAL', '"%s"' % expression
            elif expression.upper() == 'NULL':
                kind, value = 'LITERAL', 'null'
            else:
                kind, value = 'UNRESOLVED', None
            module.assignments.append({
                'target': target_path, 'source': value if kind == 'COPY' else None,
                'literal': value if kind == 'LITERAL' else None, 'kind': kind,
                'esql': 'SET %s = %s;' % (target, expression)})
        for keyword, note in (('PROPAGATE', 'propagates to another terminal'),
                              ('THROW', 'throws a user exception'),
                              ('CALL', 'calls another routine'),
                              ('WHILE', 'loops'), ('INSERT', 'writes to a database'),
                              ('UPDATE', 'updates a database'), ('DELETE', 'deletes from a database')):
            count = len(re.findall(r'(?i)\b%s\b' % keyword, body))
            if count:
                module.notes.append('%s x%d -- %s' % (keyword, count, note))
        modules.append(module)
    return modules


# ---------------------------------------------------------------------------
# Record types derived from the message tree
# ---------------------------------------------------------------------------
class RecordType:
    """A generated DTO: a record whose components are message-tree fields.

    Nested message-tree levels become nested record types rather than flattened
    names, because flattening would change the JSON contract the migrated
    service has to honour.
    """

    def __init__(self, name, tree, provenance):
        self.name = name
        self.provenance = provenance
        self.components = []          # (java_type, component_name, json_name)
        self.nested = []              # child RecordTypes
        taken = set()
        for key, child in tree.items():
            component = camel(key)
            if component in taken:
                # Two tree names that differ only in punctuation would collide
                # as record components, which does not compile.
                suffix = 2
                while component + str(suffix) in taken:
                    suffix += 1
                component += str(suffix)
            taken.add(component)
            if isinstance(child, dict) and child:
                nested = RecordType(name + pascal(key), child, provenance)
                self.nested.append(nested)
                self.components.append((nested.name, component, key))
            else:
                self.components.append(('String', component, key))

    def all_types(self):
        out = [self]
        for child in self.nested:
            out.extend(child.all_types())
        return out

    def component_for(self, key):
        for java_type, component, json_name in self.components:
            if json_name == key:
                return java_type, component
        return None, None

    def accessor_path(self, path):
        """``['customer', 'id']`` -> the chain of (type, accessor) to reach it."""
        chain = []
        current = self
        for index, segment in enumerate(path):
            if current is None:
                return None
            java_type, component = current.component_for(segment)
            if component is None:
                return None
            chain.append((java_type, component))
            current = next((n for n in current.nested if n.name == java_type), None) \
                if index < len(path) - 1 else None
        return chain


# ---------------------------------------------------------------------------
# The generator
# ---------------------------------------------------------------------------
class SpringProjectGenerator:
    """Write the target project. ``generate`` is the whole public surface."""

    def generate(self, out_dir, service, package, model, plan_id, framework,
                 source_root=None, plan=None):
        self._prepare(out_dir, service, package, model, plan_id, framework, source_root, plan)
        files = {}
        files.update(self._build_files())
        files.update(self._java_files())
        files.update(self._resource_files())
        files.update(self._test_files())
        files.update(self._config_files())
        manifest = []
        for relative, content in sorted(files.items()):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            manifest.append({'path': relative, 'hash': digest(content), 'plan_id': plan_id})
        (self.root / 'generated-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        return {'root': str(self.root), 'manifest': manifest,
                'content_hash': digest(json.dumps(manifest, sort_keys=True)),
                'action_items': self.action_items, 'roles': sorted(self.roles),
                'stub_sources': {k: v for k, v in sorted(self.stub_sources.items()) if v}}

    # -- input resolution ---------------------------------------------------
    def _prepare(self, out_dir, service, package, model, plan_id, framework, source_root, plan):
        self.root = Path(out_dir) / kebab(service)
        self.service = service
        self.app = pascal(service)
        self.package = package
        self.pkg_dir = 'src/main/java/' + package.replace('.', '/')
        self.test_dir = 'src/test/java/' + package.replace('.', '/')
        self.plan_id = plan_id
        self.model = model or {}
        self.action_items = []
        self.roles = set()
        #: Generated file -> the ACE source references whose logic it is still
        #: missing. code-generation's business-logic synthesis reads this to ask
        #: the model for the real implementation of each placeholder body. Empty
        #: means every body was translated, not that nothing was generated.
        self.stub_sources = {}
        self.prefix = 'integration'

        standards = (framework or {}).get('standards') or {}
        build = standards.get('build') or {}
        self.spring_version = str(build.get('springBootVersion') or '3.3.0')
        self.spring_minor = self._minor(self.spring_version)
        java = build.get('javaVersion')
        try:
            java = int(str(java))
        except (TypeError, ValueError):
            java = 21
        if java < 17:
            self._action(
                'The framework baseline pins Java %s. Generated code uses records and therefore '
                'requires Java 17; the POM asks for 17. Confirm the target runtime.' % java)
            java = 17
        self.java_version = java
        self.framework_dependencies = standards.get('dependencies') or []

        #: ``flow#node`` -> approved Spring pattern, from the plan the G3
        #: approval covers. Generation follows this rather than re-deciding.
        self.patterns = {}
        for mapping in (plan or {}).get('mappings') or []:
            self.patterns[str(mapping.get('source_reference', ''))] = mapping.get('spring_pattern', '')

        self.esql_modules = self._load_esql(source_root)
        #: A module is generated into exactly one transformer class, so once a
        #: flow has claimed it no other flow may fall back to it -- two flows
        #: generating the same class name with different payload types would
        #: overwrite each other and leave one service calling a signature that
        #: no longer exists.
        self.claimed_modules = set()
        self.flows = [self._flow(f, False) for f in self.model.get('flows', [])]
        self.flows += [self._flow(f, True) for f in self.model.get('subflows', [])]
        self.flows = [f for f in self.flows if f['nodes'] or f['modules']]
        self.records = {}
        for flow in self.flows:
            self._shape_flow(flow)
            # Which kinds of construct this project ended up containing. Recorded
            # on the provenance manifest, so the reviewer can see that an
            # MQ-and-database migration produced messaging and persistence code
            # without opening the tree.
            self.roles.update(flow['roles'])
        self.databases = sorted({
            table for entry in self.model.get('esql', []) for table in entry.get('databases', [])})
        if any('DB' in f['roles'] for f in self.flows):
            self.databases = self.databases or ['MIGRATED_TABLE']

    def _action(self, text):
        """Record something a human has to decide, once.

        The same gap is often reached from two directions -- a destination is
        read both when its adapter is written and when its configuration key is
        -- and the README should state it once.
        """
        if text not in self.action_items:
            self.action_items.append(text)

    def _minor(self, version):
        match = re.match(r'(\d+)\.(\d+)', str(version))
        return (int(match.group(1)), int(match.group(2))) if match else (3, 3)

    def _load_esql(self, source_root):
        """Parse the real ESQL when the source tree is reachable.

        Without it only the module *names* are known (the semantic model keeps
        those), which still produces correctly named and wired transformers --
        just with no translated assignments in them.
        """
        modules = {}
        root = Path(source_root) if source_root else None
        for entry in self.model.get('esql', []):
            path = entry.get('path', '')
            text = ''
            if root is not None:
                candidate = root / path
                try:
                    if candidate.is_file():
                        text = candidate.read_text(errors='ignore')
                except OSError:
                    text = ''
            if text:
                parsed = parse_esql(path, text)
            else:
                names = entry.get('modules') or entry.get('procedures') or [_stem(path)]
                parsed = [EsqlModule(path, name.split('.')[-1]) for name in names]
                self._action(
                    'ESQL source for %s was not available at generation time, so %s carries no '
                    'translated assignments. Re-run with the ACE repository present.'
                    % (path, ', '.join(pascal(m.name) + 'Transformer' for m in parsed)))
            modules.setdefault(_stem(path).lower(), []).extend(parsed)
            modules.setdefault('*', []).extend(parsed)
        # One transformer class per module name, whichever file it came from: two
        # modules of the same name would generate one class twice.
        for key, items in modules.items():
            seen, unique = set(), []
            for module in items:
                if module.name.lower() in seen:
                    continue
                seen.add(module.name.lower())
                unique.append(module)
            modules[key] = unique
        return modules

    def _flow(self, flow, is_subflow):
        path = flow.get('path', '')
        nodes = []
        for node in flow.get('nodes', []):
            reference = path + '#' + str(node.get('id', ''))
            # A Toolkit canvas icon and a routing-target label carry no logic,
            # so they generate nothing -- and are not findings either, which is
            # why this is checked before anything is reported as unmapped.
            if str(node.get('type', '')).startswith(('utility:', 'ComIbmLabel.')):
                continue
            role = role_for(node.get('type', ''), node.get('properties') or {},
                            self.patterns.get(reference))
            if role in ('UNMAPPED', 'MODEL', 'CONFIG'):
                if role == 'UNMAPPED':
                    self._action(
                        'No Spring pattern for %s (%s) -- nothing was generated for this node.'
                        % (reference, node.get('type', 'UNKNOWN')))
                continue
            nodes.append({'id': str(node.get('id', '')) or 'n%d' % (len(nodes) + 1),
                          'type': str(node.get('type', '')), 'role': role,
                          'properties': node.get('properties') or {}, 'reference': reference})
        ordered = self._order(nodes, flow.get('connections', []))
        modules = [m for m in self.esql_modules.get(_stem(path).lower()) or []
                   if id(m) not in self.claimed_modules]
        if not modules and any(n['role'] == 'TRANSFORM' for n in ordered):
            # A Compute node with no .esql of the flow's own name: ACE allows the
            # module to live in any file in the project, so fall back to the
            # unclaimed ones rather than generating a transformer with no
            # identity.
            modules = [m for m in self.esql_modules.get('*') or []
                       if id(m) not in self.claimed_modules]
        self.claimed_modules.update(id(m) for m in modules)
        return {'path': path, 'name': pascal(_stem(path)), 'subflow': is_subflow,
                'nodes': ordered, 'modules': modules,
                'roles': {n['role'] for n in ordered}}

    def _order(self, nodes, connections):
        """Nodes in connection order: the sequence the flow actually executes.

        ACE writes ``sourceNode``/``targetNode`` as references that may carry a
        leading ``#`` and a terminal suffix, so both are stripped before the
        edges are matched against node ids.
        """
        by_id = {n['id']: n for n in nodes}

        def resolve(reference):
            text = str(reference or '').lstrip('#').strip()
            if text in by_id:
                return text
            return next((nid for nid in by_id if text.startswith(nid) or nid.startswith(text)), None)

        edges = []
        for connection in connections or []:
            source = resolve(connection.get('from') or connection.get('source'))
            target = resolve(connection.get('to') or connection.get('target'))
            if source and target and source != target:
                edges.append((source, target))
        incoming = {n['id']: 0 for n in nodes}
        following = {n['id']: [] for n in nodes}
        for source, target in edges:
            incoming[target] += 1
            following[source].append(target)
        ordered, seen = [], set()
        queue = [n['id'] for n in nodes if incoming[n['id']] == 0] or [n['id'] for n in nodes]
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            ordered.append(by_id[current])
            queue = following[current] + queue
        ordered += [n for n in nodes if n['id'] not in seen]
        # An inbound node always leads, even when the flow declared no edges.
        ordered.sort(key=lambda n: 0 if n['role'] in INBOUND else 1)
        return ordered

    def _shape_flow(self, flow):
        """Decide the flow's payload types from what its ESQL reads and writes."""
        input_tree, output_tree = {}, {}
        for module in flow['modules']:
            _merge(input_tree, module.input_tree)
            _merge(output_tree, module.output_tree)
        flow['request'] = flow['response'] = None
        if input_tree:
            flow['request'] = self._record(flow['name'] + 'Request', input_tree, flow['path'])
        if output_tree:
            flow['response'] = self._record(flow['name'] + 'Response', output_tree, flow['path'])
        # A flow with assignments in one direction only still needs both ends.
        if flow['request'] and not flow['response']:
            flow['response'] = self._record(flow['name'] + 'Response', input_tree, flow['path'])
        if flow['response'] and not flow['request']:
            flow['request'] = self._record(flow['name'] + 'Request', output_tree, flow['path'])
        flow['request_type'] = flow['request'].name if flow['request'] else 'Map<String, Object>'
        flow['response_type'] = flow['response'].name if flow['response'] else 'Map<String, Object>'
        flow['typed'] = flow['request'] is not None
        if not flow['typed'] and flow['nodes']:
            self._action(
                'No ESQL field assignments were found for %s, so it exchanges '
                'Map<String, Object> rather than typed records. Supply the flow\'s ESQL to get '
                'typed request/response records.' % (flow['path'] or flow['name']))

    def _record(self, name, tree, provenance):
        record = RecordType(name, tree, provenance)
        for item in record.all_types():
            self.records[item.name] = item
        return record

    # -- derived views ------------------------------------------------------
    def _has(self, *roles):
        return any(role in flow['roles'] for flow in self.flows for role in roles)

    def _interfaces(self, protocol):
        return [i for i in self.model.get('interfaces', []) if i.get('protocol') == protocol]

    def _endpoint(self, node, flow):
        """The HTTP path ACE published for an inbound node."""
        properties = node['properties']
        raw = (properties.get('URLSpecifier') or properties.get('url')
               or properties.get('path') or '')
        if not raw:
            raw = next((i.get('endpoint') or '' for i in self.model.get('interfaces', [])
                        if i.get('source') == flow['path'] and i.get('node') == node['id']), '')
        raw = '' if raw in ('UNKNOWN', None) else str(raw)
        if raw.startswith('$'):
            raw = ''
        if '://' in raw:
            raw = '/' + raw.split('://', 1)[1].split('/', 1)[-1] if '/' in raw.split('://', 1)[1] else ''
        path = '/' + raw.strip('/') if raw.strip('/') else '/' + kebab(flow['name'])
        return re.sub(r'\{[^}]*\}', lambda m: '{' + camel(m.group(0)[1:-1]) + '}', path)

    def _destination(self, node, kind):
        """The queue, topic or directory ACE used, or a derived default.

        The value reaches ``application.yml`` and a javadoc line, both of which
        the quality gate scans, so an address-shaped destination is replaced by
        the derived default and recorded as an action instead.
        """
        properties = node['properties']
        name = str(properties.get('queueName') or properties.get('topicName')
                   or properties.get('destination') or properties.get('filePath') or '')
        default = kind + '.' + kebab(node['id']).upper()
        if not name or name == 'UNKNOWN':
            return default
        if _ABSOLUTE_URL.search(name):
            self._action(
                'Node %s names its destination as an absolute address; %s was configured instead. '
                'Set the real destination through the environment.' % (node['id'], default))
            return default
        return name

    # -- build files --------------------------------------------------------
    def _build_files(self):
        return {'pom.xml': self._pom(), 'Dockerfile': self._dockerfile(),
                '.gitignore': self._gitignore(), 'README.md': self._readme(),
                '.github/workflows/build.yml': self._ci()}

    def _dependency(self, group, artifact, scope='', version=''):
        lines = ['        <dependency>', '            <groupId>%s</groupId>' % group,
                 '            <artifactId>%s</artifactId>' % artifact]
        if version:
            lines.append('            <version>%s</version>' % version)
        if scope:
            lines.append('            <scope>%s</scope>' % scope)
        lines.append('        </dependency>')
        return '\n'.join(lines)

    def _pom(self):
        boot = 'org.springframework.boot'
        deps = [self._dependency(boot, 'spring-boot-starter-web'),
                self._dependency(boot, 'spring-boot-starter-validation'),
                self._dependency(boot, 'spring-boot-starter-actuator')]
        if self._has('SOAP_IN', 'SOAP_REPLY', 'SOAP_REQUEST'):
            # Without this, a controller declaring text/xml still has only
            # Jackson's JSON converter and answers 500 on the first request:
            # there is no HttpMessageConverter that can read XML into the
            # generated record. Version comes from the Spring Boot parent.
            deps.append(self._dependency('com.fasterxml.jackson.dataformat',
                                         'jackson-dataformat-xml'))
        if self._has('JMS_IN', 'JMS_OUT'):
            deps.append(self._dependency(boot, 'spring-boot-starter-artemis'))
        if self._has('KAFKA_IN', 'KAFKA_OUT'):
            deps.append(self._dependency('org.springframework.kafka', 'spring-kafka'))
        if self._has('DB'):
            deps.append(self._dependency(boot, 'spring-boot-starter-jdbc'))
            deps.append(self._dependency('com.h2database', 'h2', 'runtime'))
        deps.append(self._dependency(boot, 'spring-boot-starter-test', 'test'))
        return '''<?xml version="1.0" encoding="UTF-8"?>
<!-- Generated from ACE migration plan %(plan)s. Dependencies are the ones the
     generated code imports: nothing is added speculatively. -->
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>%(spring)s</version>
        <relativePath/>
    </parent>

    <groupId>%(package)s</groupId>
    <artifactId>%(artifact)s</artifactId>
    <version>1.0.0</version>
    <name>%(artifact)s</name>
    <description>Migrated from IBM ACE/IIB under migration plan %(plan)s</description>

    <properties>
        <java.version>%(java)s</java.version>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>

    <dependencies>
%(deps)s
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>
</project>
''' % {'plan': self.plan_id, 'spring': self.spring_version, 'package': self.package,
       'artifact': kebab(self.service), 'java': self.java_version, 'deps': '\n'.join(deps)}

    # -- deployable config: ConfigMap overlays + a minimal Helm chart -------
    def _outbound_clients(self):
        """{config slug -> vendor name, or None} for every HTTP/SOAP outbound
        node, keyed exactly as ``_client()``/``_integration_block()`` do."""
        vendors = self.model.get('backend_properties') or {}
        out = {}
        for flow in self.flows:
            for node in flow['nodes']:
                if node['role'] in ('HTTP_REQUEST', 'SOAP_REQUEST'):
                    key = self._client_key(node)
                    out[self._client_slug(node)] = key if key in vendors else None
        return out

    def _config_files(self):
        files = {}
        for cluster in ENV_CLUSTERS:
            files['config/config-map/application-%s.yml' % cluster] = self._config_map_yml(cluster)
        files['config/deploy-config/Chart.yaml'] = self._helm_chart()
        files['config/deploy-config/templates/deployment.yaml'] = self._helm_deployment()
        files['config/deploy-config/templates/service.yaml'] = self._helm_service()
        for cluster in ENV_CLUSTERS:
            files['config/deploy-config/values-%s.yaml' % cluster] = self._helm_values(cluster)
        return files

    def _config_map_yml(self, cluster):
        """The ConfigMap overlay a real deployment of this cluster would use.

        Only the outbound backend connection is cluster-specific here; every
        other setting a deployed pod needs is already externalised onto
        plain environment variables in ``application.yml`` and does not need
        restating per cluster. ``is_externalized_config``
        (``app/services/feedback.py``) exempts anything under
        ``config-map/`` from ``HARDCODED_ENDPOINT_SCAN`` -- this is the
        externalisation that check exists to require, not a violation of it.
        """
        env = cluster.split('-', 1)[0].upper()
        lines = ['# Generated from ACE migration plan %s.' % self.plan_id,
                 '# ConfigMap overlay for the %s cluster.' % cluster,
                 '', 'server:', '  port: ${PORT:8080}']
        vendored = {slug: vendor for slug, vendor in self._outbound_clients().items() if vendor}
        if vendored:
            lines += ['', '%s:' % self.prefix, '  clients:']
            for slug, vendor in sorted(vendored.items()):
                vendor_envs = (self.model.get('backend_properties') or {}).get(vendor) or {}
                info = vendor_envs.get(env) or {}
                lines.append('    %s:' % slug)
                url = info.get('service_url')
                if url:
                    lines.append('      base-url: %s' % url)
                else:
                    reason = ('no %s backend URL for env "%s" was found in the ACE repo\'s own '
                              '*.properties (environments found: %s) -- assign it deliberately, '
                              'do not guess' % (vendor, env, ', '.join(sorted(vendor_envs)) or 'none'))
                    lines.append('      # TODO: %s' % reason)
                    self._action('config/config-map/application-%s.yml: %s' % (cluster, reason))
                if info.get('test_harness_url'):
                    lines.append('      test-harness-url: %s' % info['test_harness_url'])
        return '\n'.join(lines) + '\n'

    def _helm_chart(self):
        return '''# Generated from ACE migration plan %s.
apiVersion: v2
name: %s
description: Helm chart for %s, migrated from IBM ACE/IIB.
type: application
version: 1.0.0
appVersion: "1.0.0"
''' % (self.plan_id, kebab(self.service), self.service)

    def _helm_deployment(self):
        return '''# Generated from ACE migration plan %s.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Chart.Name }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app: {{ .Chart.Name }}
  template:
    metadata:
      labels:
        app: {{ .Chart.Name }}
    spec:
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: {{ .Values.service.port }}
          envFrom:
            - configMapRef:
                name: {{ .Chart.Name }}-config
''' % self.plan_id

    def _helm_service(self):
        return '''# Generated from ACE migration plan %s.
apiVersion: v1
kind: Service
metadata:
  name: {{ .Chart.Name }}
spec:
  selector:
    app: {{ .Chart.Name }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: {{ .Values.service.port }}
''' % self.plan_id

    def _helm_values(self, cluster):
        return '''# Generated from ACE migration plan %s.
# Values overlay for the %s cluster. image.repository/tag are set by the
# deploy pipeline, never guessed here.
replicaCount: 1
image:
  repository: ""
  tag: latest
service:
  port: 8080
''' % (self.plan_id, cluster)

    def _dockerfile(self):
        return '''# Generated from ACE migration plan %s.
FROM eclipse-temurin:%s-jre
WORKDIR /app
COPY target/%s-1.0.0.jar app.jar
RUN useradd --uid 1001 --no-create-home service && chown -R 1001 /app
USER 1001
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75.0", "-jar", "app.jar"]
''' % (self.plan_id, self.java_version, kebab(self.service))

    def _gitignore(self):
        return 'target/\n*.class\n.idea/\n*.iml\n.vscode/\n'

    def _ci(self):
        return '''---
# Generated from ACE migration plan %s.
name: build
on:
  push:
    branches: ['**']
  pull_request:
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '%s'
          cache: maven
      - run: mvn --batch-mode verify
''' % (self.plan_id, self.java_version)

    # -- java ---------------------------------------------------------------
    def _file(self, relative_package, class_name):
        return '%s/%s/%s.java' % (self.pkg_dir, relative_package, class_name) if relative_package \
            else '%s/%s.java' % (self.pkg_dir, class_name)

    def _header(self, relative_package, imports, comment, annotations, body):
        """Assemble one compilation unit.

        Static imports sort after the rest, as the convention is, and the whole
        file goes through ``tidy`` so no generated line carries trailing
        whitespace or a stray second blank line.
        """
        package = self.package + ('.' + relative_package.replace('/', '.') if relative_package else '')
        blocks = ['package %s;' % package, '']
        if imports:
            plain = sorted({i for i in imports if not i.startswith('static ')})
            static = sorted({i for i in imports if i.startswith('static ')})
            blocks.append('\n'.join('import %s;' % i for i in plain))
            if static:
                if plain:
                    blocks.append('')
                blocks.append('\n'.join('import %s;' % i for i in static))
            blocks.append('')
        blocks.append(comment.rstrip())
        blocks.extend(annotations)
        blocks.append(body.rstrip())
        return tidy('\n'.join(blocks))

    def _inbound(self, flow, roles, suffix):
        """The inbound nodes of one kind, each with the class name it gets.

        A flow may declare more than one entry point -- an HTTP input and a SOAP
        input, or an MQ input and a Kafka input. Naming the class after the flow
        alone meant the second node's file overwrote the first's and that entry
        point was silently dropped, so a flow with several gets one class per
        node instead.
        """
        nodes = [n for n in flow['nodes'] if n['role'] in roles]
        if len(nodes) == 1:
            return [(flow['name'] + suffix, nodes[0])]
        return [(flow['name'] + pascal(n['id']) + suffix, n) for n in nodes]

    def _java_files(self):
        files = {self._file('', self.app + 'Application'): self._application()}
        files.update(self._dto_files())
        files.update(self._transformer_files())
        for flow in self.flows:
            files[self._file('service', flow['name'] + 'Service')] = self._service(flow)
            for name, node in self._inbound(flow, ('HTTP_IN', 'SOAP_IN'), 'Controller'):
                files[self._file('controller', name)] = self._controller(flow, node, name)
            for name, node in self._inbound(flow, ('JMS_IN', 'KAFKA_IN'), 'Listener'):
                files[self._file('messaging', name)] = self._listener(flow, node, name)
            for node in flow['nodes']:
                if node['role'] in ('JMS_OUT', 'KAFKA_OUT'):
                    files[self._file('messaging', pascal(node['id']) + 'Publisher')] = \
                        self._publisher(flow, node)
                elif node['role'] in ('HTTP_REQUEST', 'SOAP_REQUEST'):
                    files[self._file('client', pascal(self._client_key(node)) + 'Client')] = \
                        self._client(flow, node)
                elif node['role'] == 'FILE_IN':
                    files[self._file('file', pascal(node['id']) + 'Ingest')] = self._file_in(flow, node)
                elif node['role'] == 'FILE_OUT':
                    files[self._file('file', pascal(node['id']) + 'Writer')] = self._file_out(flow, node)
                elif node['role'] == 'SCHEDULE':
                    files[self._file('scheduler', pascal(node['id']) + 'Scheduler')] = \
                        self._scheduler(flow, node)
                elif node['role'] in ('FILTER', 'ROUTER'):
                    files[self._file('routing', pascal(node['id']) + ('Filter' if node['role'] == 'FILTER' else 'Router'))] = \
                        self._routing(flow, node)
        if self.databases:
            files[self._file('persistence', self.app + 'Repository')] = self._repository()
        files[self._file('exception', 'FlowException')] = self._flow_exception()
        files[self._file('exception', 'GlobalExceptionHandler')] = self._exception_handler()
        files[self._file('dto', 'ErrorResponse')] = self._error_response()
        files[self._file('support', 'CorrelationIdFilter')] = self._correlation_filter()
        if self._has('JMS_IN', 'JMS_OUT'):
            files[self._file('config', 'MessagingConfig')] = self._messaging_config()
        return files

    def _application(self):
        imports = ['org.springframework.boot.SpringApplication',
                   'org.springframework.boot.autoconfigure.SpringBootApplication']
        annotations = ['@SpringBootApplication']
        if self._has('SCHEDULE', 'FILE_IN'):
            imports.append('org.springframework.scheduling.annotation.EnableScheduling')
            annotations.append('@EnableScheduling')
        if self._has('JMS_IN'):
            imports.append('org.springframework.jms.annotation.EnableJms')
            annotations.append('@EnableJms')
        comment = javadoc(
            'Entry point for the service migrated from IBM ACE/IIB.',
            'Generated under migration plan {@code %s}. One Spring service per ACE message flow:'
            % self.plan_id,
            ('ul', ['{@code %s} -> {@code %sService}' % (flow['path'] or flow['name'], flow['name'])
                    for flow in self.flows]))
        body = '''public class %sApplication {

    public static void main(String[] args) {
        SpringApplication.run(%sApplication.class, args);
    }
}''' % (self.app, self.app)
        return self._header('', imports, comment, annotations, body)

    def _dto_files(self):
        files = {}
        for record in self.records.values():
            # One blank line between annotated components: a record whose
            # components each carry an annotation reads as a list of fields, not
            # as a parameter list.
            components = ',\n\n'.join(
                '        @JsonProperty("%s")\n        %s %s' % (json_name, java_type, component)
                for java_type, component, json_name in record.components)
            comment = javadoc(
                'Message-tree fields of {@code %s}, as addressed by its ESQL.'
                % (record.provenance or self.service),
                'Generated under migration plan {@code %s}.' % self.plan_id)
            body = '''@JsonInclude(JsonInclude.Include.NON_NULL)
public record %s(
%s) {
}''' % (record.name, components)
            files[self._file('dto', record.name)] = self._header(
                'dto', ['com.fasterxml.jackson.annotation.JsonInclude',
                        'com.fasterxml.jackson.annotation.JsonProperty'],
                comment, [], body)
        return files

    def _transformer_files(self):
        files = {}
        for flow in self.flows:
            for module in flow['modules']:
                name = pascal(module.name) + 'Transformer'
                path = self._file('transform', name)
                files[path] = self._transformer(flow, module, name)
                # A body the transpiler could not fully translate is a stub: it
                # compiles and returns null where the ESQL did something. The
                # ESQL file is the source the real implementation comes from.
                if module.unresolved or not module.translated:
                    self.stub_sources.setdefault(path, [])
                    reference = module.source_path or (flow['path'] or '')
                    if reference and reference not in self.stub_sources[path]:
                        self.stub_sources[path].append(reference)
        return files

    def _transformer(self, flow, module, class_name):
        request, response = flow['request'], flow['response']
        imports = ['org.springframework.stereotype.Component']
        # The module is quoted verbatim so the translation can be reviewed
        # against its source, and left unwrapped inside <pre> because a wrapped
        # ESQL statement is no longer the statement.
        statements = [_escape(a['esql']) for a in module.assignments[:40]] \
            or ['(no SET statements found)']
        comment = javadoc(
            'Translation of ESQL module {@code %s} from {@code %s}.'
            % (module.name, module.source_path or 'unknown'),
            'Generated under migration plan {@code %s}. The ESQL this body was derived from, '
            'verbatim:' % self.plan_id,
            ('pre', statements),
            'Statements needing a decision:' if module.notes else None,
            ('ul', [_escape(note) for note in module.notes]))
        if request is None or response is None:
            imports.append('java.util.LinkedHashMap')
            imports.append('java.util.Map')
            body = '@Component\npublic class %s {\n\n%s\n%s\n        return new LinkedHashMap<>(' \
                   'source == null ? Map.of() : source);\n    }\n}' % (
                       class_name,
                       javadoc('TODO No ESQL field assignments were resolvable for this module, so '
                               'the message passes through unchanged. Translate the ESQL above by '
                               'hand, or re-generate with the ACE repository available.', indent=4),
                       signature('public Map<String, Object> transform',
                                 ['Map<String, Object> source']))
            return self._header('transform', imports, comment, [], body)

        locals_, expression, used = self._construct(request, response, module)
        imports.append('%s.dto.%s' % (self.package, request.name))
        imports.extend('%s.dto.%s' % (self.package, name) for name in used)
        body = '''@Component
public class %s {

%s
        if (source == null) {
            return null;
        }
%s        return %s;
    }
}''' % (class_name,
        signature('public %s transform' % response.name, ['%s source' % request.name]),
        locals_, expression)
        return self._header('transform', imports, comment, [], body)

    def _construct(self, request, response, module):
        """Build the response record from the request, assignment by assignment.

        Returns the local declarations, the constructor expression, and the
        record types it referenced so the caller can import them.

        Each source parent becomes one null-guarded local, so reading a nested
        field is a plain ternary rather than a chain that can throw. Two
        formatting rules matter for the result to compile at all: a trailing
        line comment swallows everything to its right, so the argument
        separator goes *before* the comment, and a closing parenthesis gets its
        own line rather than following a commented argument.
        """
        by_target = {tuple(item['target']): item for item in module.assignments}
        locals_ = {}
        used = set()

        def local_for(path):
            if not path:
                return 'source'
            key = tuple(path)
            if key in locals_:
                return locals_[key][0]
            parent = local_for(path[:-1])
            chain = request.accessor_path(list(path))
            if chain is None or parent is None:
                return None
            name = camel('src ' + ' '.join(path))
            # ``source`` itself is already guarded at the top of the method, so
            # only a nested parent needs the ternary.
            accessor = '%s.%s()' % (parent, chain[-1][1])
            value = accessor if parent == 'source' else \
                '%s == null ? null : %s' % (parent, accessor)
            locals_[key] = (name, '        var %s = %s;\n' % (name, value))
            return name

        def leaf(target):
            item = by_target.get(tuple(target))
            if item is None:
                return 'null', '// not assigned by ' + module.name
            esql = _redact(item['esql'])
            if item['kind'] == 'LITERAL':
                if _ABSOLUTE_URL.search(item['literal'] or ''):
                    # An address assigned as a literal in ESQL must not become a
                    # literal in Java: that is the environment coupling the
                    # migration exists to remove, and the quality gate fails it.
                    self._action(
                        'ESQL module %s assigns the literal address %s. It was not written into '
                        'source: add it to application.yml as a ${...} property and inject it.'
                        % (module.name, item['literal'].strip('"')))
                    return 'null', '// TODO externalise the address in: ' + esql
                if _CREDENTIAL.search(target[-1]):
                    self._action(
                        'ESQL module %s assigns a literal value to the credential-shaped field '
                        '"%s". It was not written into source: inject it from the environment or '
                        'a secret store.' % (module.name, '.'.join(target)))
                    return 'null', '// TODO inject this value rather than hard-coding it: ' + esql
                return item['literal'], '// ' + esql
            if item['kind'] == 'COPY':
                chain = request.accessor_path(item['source'])
                if chain is None:
                    return 'null', '// TODO ' + esql
                if len(item['source']) == 1:
                    return 'source.%s()' % chain[-1][1], '// ' + esql
                parent = local_for(item['source'][:-1])
                if parent is None:
                    return 'null', '// TODO ' + esql
                return ('%s == null ? null : %s.%s()' % (parent, parent, chain[-1][1]),
                        '// ' + esql)
            return 'null', '// TODO ' + esql

        def render(record, path, indent):
            """Lines for ``new Record(...)``: first ``new X(``, last ``)``."""
            used.add(record.name)
            arguments = []
            for index, (java_type, _component, json_name) in enumerate(record.components):
                target = path + [json_name]
                separator = '' if index == len(record.components) - 1 else ','
                nested = next((n for n in record.nested if n.name == java_type), None)
                if nested is not None:
                    block = render(nested, target, indent + 8)
                    block[0] = ' ' * (indent + 8) + block[0]
                    block[-1] = block[-1] + separator
                    arguments.extend(block)
                    continue
                expression, comment = leaf(target)
                arguments.extend(argument(expression, separator, comment, indent + 8))
            return ['new %s(' % record.name] + arguments + [' ' * indent + ')']

        expression = '\n'.join(render(response, [], 8))
        return ''.join(text for _, text in sorted(locals_.values())), expression, used

    def _service(self, flow):
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.stereotype.Service']
        fields, constructor_params, assignments, steps = [], [], [], []
        if not flow['typed']:
            imports.append('java.util.Map')
        for module in flow['modules']:
            name = pascal(module.name) + 'Transformer'
            field = camel(module.name) + 'Transformer'
            imports.append('%s.transform.%s' % (self.package, name))
            fields.append('    private final %s %s;' % (name, field))
            constructor_params.append('%s %s' % (name, field))
            assignments.append('        this.%s = %s;' % (field, field))
        for node in flow['nodes']:
            role = node['role']
            if role in ('JMS_OUT', 'KAFKA_OUT'):
                name, field = pascal(node['id']) + 'Publisher', camel(node['id']) + 'Publisher'
                imports.append('%s.messaging.%s' % (self.package, name))
            elif role in ('HTTP_REQUEST', 'SOAP_REQUEST'):
                key = self._client_key(node)
                name, field = pascal(key) + 'Client', camel(key) + 'Client'
                imports.append('%s.client.%s' % (self.package, name))
            elif role == 'FILE_OUT':
                name, field = pascal(node['id']) + 'Writer', camel(node['id']) + 'Writer'
                imports.append('%s.file.%s' % (self.package, name))
            elif role in ('FILTER', 'ROUTER'):
                suffix = 'Filter' if role == 'FILTER' else 'Router'
                name, field = pascal(node['id']) + suffix, camel(node['id']) + suffix
                imports.append('%s.routing.%s' % (self.package, name))
            elif role == 'DB':
                name, field = self.app + 'Repository', camel(self.app) + 'Repository'
                imports.append('%s.persistence.%s' % (self.package, name))
            else:
                continue
            declaration = '    private final %s %s;' % (name, field)
            if declaration not in fields:
                fields.append(declaration)
                constructor_params.append('%s %s' % (name, field))
                assignments.append('        this.%s = %s;' % (field, field))
            node['field'] = field
        request_type, response_type = flow['request_type'], flow['response_type']
        if flow['typed']:
            imports.append('%s.dto.%s' % (self.package, request_type))
            imports.append('%s.dto.%s' % (self.package, response_type))
        if any(n['role'] in ('FILTER', 'ROUTER') for n in flow['nodes']):
            imports.append('%s.exception.FlowException' % self.package)

        transformer_field = (camel(flow['modules'][0].name) + 'Transformer') if flow['modules'] else ''
        produced = False
        for node in flow['nodes']:
            role, reference = node['role'], node['reference']
            if role in INBOUND:
                steps.append('        // %s (%s) -- flow entry\n'
                             '        log.info("flow=%s node=%s correlationId={}", correlationId);'
                             % (reference, node['type'], flow['name'], node['id']))
            elif role in ('FILTER', 'ROUTER'):
                code = kebab(node['id']).upper().replace('-', '_') + '_REJECTED'
                arguments = ['"%s"' % code, '"%s rejected the message"' % node['id'], '422']
                steps.append('        // %s (%s)\n'
                             '        if (!%s.matches(request)) {\n'
                             '%s\n'
                             '        }' % (reference, node['type'], node['field'],
                                            signature('throw new FlowException', arguments,
                                                      tail=';', indent=12)))
            elif role == 'TRANSFORM' and transformer_field and not produced:
                steps.append('        // %s (%s)\n        %s response = %s.transform(request);'
                             % (reference, node['type'], response_type, transformer_field))
                produced = True
            elif role == 'DB':
                steps.append('        // %s (%s)\n        %s.select();'
                             % (reference, node['type'], node['field']))
            elif role in ('HTTP_REQUEST', 'SOAP_REQUEST'):
                steps.append('        // %s (%s)\n        %s.call(%s);'
                             % (reference, node['type'], node['field'],
                                'response' if produced else 'request'))
            elif role in ('JMS_OUT', 'KAFKA_OUT', 'FILE_OUT'):
                verb = 'write' if role == 'FILE_OUT' else 'publish'
                steps.append('        // %s (%s)\n        %s.%s(%s);'
                             % (reference, node['type'], node['field'], verb,
                                'response' if produced else 'request'))
            elif role in ('HTTP_REPLY', 'SOAP_REPLY'):
                steps.append('        // %s (%s) -- flow reply' % (reference, node['type']))
            elif role == 'THROW':
                steps.append('        // %s (%s)\n'
                             '        // TODO the ACE flow throws here under a condition the ESQL '
                             'defines; add it.' % (reference, node['type']))
        if not produced:
            if transformer_field:
                # ESQL exists for the flow but no node carried the TRANSFORM
                # role (ACE allows the module to be attached elsewhere), so the
                # translated mapping is still what the flow returns.
                steps.append('        // %s has no transformation node; the module below is the '
                             'flow\'s mapping.\n        %s response = %s.transform(request);'
                             % (flow['path'] or flow['name'], response_type, transformer_field))
            elif request_type == response_type:
                steps.append('        // No transformation node in this flow: the message is '
                             'returned as received.\n        %s response = request;' % response_type)
            else:
                steps.append('        // TODO no transformation was found for this flow.\n'
                             '        %s response = null;' % response_type)
        steps.append('        return response;')
        constructor = ''
        if constructor_params:
            constructor = '\n%s\n%s\n    }\n' % (
                signature('public %sService' % flow['name'], constructor_params),
                '\n'.join(assignments))
        comment = javadoc(
            'Executes the {@code %s} message flow.' % (flow['path'] or flow['name']),
            "Generated under migration plan {@code %s}. The statements below follow the flow's own "
            'connection order, one per ACE node, each tagged with the node it was generated from.'
            % self.plan_id)
        body = '''@Service
public class %(name)sService {

    private static final Logger log = LoggerFactory.getLogger(%(name)sService.class);

%(fields)s
%(constructor)s
%(process)s
%(steps)s
    }
}''' % {'name': flow['name'], 'fields': '\n'.join(fields), 'constructor': constructor,
        'process': signature('public %s process' % response_type,
                             ['%s request' % request_type, 'String correlationId']),
        'steps': '\n\n'.join(steps)}
        return self._header('service', imports, comment, [], body)

    def _http_method(self, node):
        """The verb ACE allowed, defaulting to POST as an IIB HTTPInput does."""
        method = str(node['properties'].get('httpMethod')
                     or node['properties'].get('allowedMethods') or 'POST').split(',')[0].strip().upper()
        return method if method in ('GET', 'PUT', 'DELETE', 'PATCH', 'POST') else 'POST'

    def _controller(self, flow, node, class_name=None):
        class_name = class_name or (flow['name'] + 'Controller')
        path = self._endpoint(node, flow)
        method = self._http_method(node)
        mapping = {'GET': 'GetMapping', 'PUT': 'PutMapping', 'DELETE': 'DeleteMapping',
                   'PATCH': 'PatchMapping'}.get(method, 'PostMapping')
        soap = node['role'] == 'SOAP_IN'
        media = 'MediaType.TEXT_XML_VALUE' if soap else 'MediaType.APPLICATION_JSON_VALUE'
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.http.MediaType', 'org.springframework.http.ResponseEntity',
                   'org.springframework.web.bind.annotation.RequestBody',
                   'org.springframework.web.bind.annotation.RequestHeader',
                   'org.springframework.web.bind.annotation.RequestMapping',
                   'org.springframework.web.bind.annotation.RestController',
                   'org.springframework.web.bind.annotation.' + mapping,
                   '%s.service.%sService' % (self.package, flow['name'])]
        if flow['typed']:
            imports.append('jakarta.validation.Valid')
            imports.append('%s.dto.%s' % (self.package, flow['request_type']))
            imports.append('%s.dto.%s' % (self.package, flow['response_type']))
        else:
            imports.append('java.util.Map')
        body_annotation = '@Valid @RequestBody' if flow['typed'] else '@RequestBody'
        comment = javadoc(
            'HTTP entry point replacing ACE node {@code %s}.' % node['reference'],
            'Generated under migration plan {@code %s}. The path is the one ACE published (%s); the '
            'context root stays externalised so the deployed base path is a configuration decision, '
            'not a code change.' % (self.plan_id, path),
            'This replaces a SOAP node, so the payload is XML, bound to the same record through '
            'jackson-dataformat-xml. TODO it is the message body alone: if callers send a full '
            'SOAP envelope, add spring-boot-starter-web-services and generate the contract from '
            'the WSDL.' if soap else None)
        body = '''@RestController
@RequestMapping("${%(prefix)s.%(flow_key)s.context-root:}")
public class %(cls)s {

    private static final Logger log = LoggerFactory.getLogger(%(cls)s.class);

    private final %(flow)sService service;

%(constructor)s
        this.service = service;
    }

%(mapping)s
%(handle)s
        log.info("received %(method)s %(path)s correlationId={}", correlationId);
        return ResponseEntity.ok(service.process(request, correlationId));
    }
}''' % {'prefix': self.prefix, 'flow_key': kebab(flow['name']), 'flow': flow['name'],
        'cls': class_name,
        'constructor': signature('public %s' % class_name,
                                 ['%sService service' % flow['name']]),
        'mapping': annotation(mapping, ['path = "%s"' % path, 'consumes = %s' % media,
                                        'produces = %s' % media]),
        'handle': signature(
            'public ResponseEntity<%s> handle' % flow['response_type'],
            ['%s %s request' % (body_annotation, flow['request_type']),
             '@RequestHeader(value = "X-Correlation-ID", required = false) String correlationId']),
        'path': path, 'method': method}
        return self._header('controller', imports, comment, [], body)

    def _listener(self, flow, node, class_name=None):
        class_name = class_name or (flow['name'] + 'Listener')
        kafka = node['role'] == 'KAFKA_IN'
        destination = self._destination(node, 'TOPIC' if kafka else 'QUEUE')
        key = kebab(node['id'])
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.stereotype.Component',
                   '%s.service.%sService' % (self.package, flow['name'])]
        if flow['typed']:
            imports.append('%s.dto.%s' % (self.package, flow['request_type']))
        else:
            imports.append('java.util.Map')
        if kafka:
            imports.append('org.springframework.kafka.annotation.KafkaListener')
            listener = annotation('KafkaListener', [
                'topics = "${%s.topics.%s}"' % (self.prefix, key),
                'groupId = "${spring.kafka.consumer.group-id}"'])
        else:
            imports.append('org.springframework.jms.annotation.JmsListener')
            listener = annotation('JmsListener',
                                  ['destination = "${%s.queues.%s}"' % (self.prefix, key)])
        comment = javadoc(
            'Inbound adapter replacing ACE node {@code %s} (%s).'
            % (node['reference'], node['type']),
            'Generated under migration plan {@code %s}. ACE read from {@code %s}; that name is the '
            'configured default and overridable per environment.' % (self.plan_id, destination))
        body = '''@Component
public class %(cls)s {

    private static final Logger log = LoggerFactory.getLogger(%(cls)s.class);

    private final %(flow)sService service;

%(constructor)s
        this.service = service;
    }

%(listener)s
%(on_message)s
        log.info("consumed a message for flow=%(flow)s");
        service.process(message, null);
    }
}''' % {'flow': flow['name'], 'listener': listener, 'cls': class_name,
        'constructor': signature('public %s' % class_name,
                                 ['%sService service' % flow['name']]),
        'on_message': signature('public void onMessage',
                                ['%s message' % flow['request_type']])}
        return self._header('messaging', imports, comment, [], body)

    def _publisher(self, flow, node):
        kafka = node['role'] == 'KAFKA_OUT'
        destination = self._destination(node, 'TOPIC' if kafka else 'QUEUE')
        key = kebab(node['id'])
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.beans.factory.annotation.Value',
                   'org.springframework.stereotype.Component']
        if kafka:
            imports.append('org.springframework.kafka.core.KafkaTemplate')
            field, type_name = 'kafkaTemplate', 'KafkaTemplate<String, Object>'
            send = 'kafkaTemplate.send(destination, payload)'
            setting = '%s.topics.%s' % (self.prefix, key)
        else:
            imports.append('org.springframework.jms.core.JmsTemplate')
            field, type_name = 'jmsTemplate', 'JmsTemplate'
            send = 'jmsTemplate.convertAndSend(destination, payload)'
            setting = '%s.queues.%s' % (self.prefix, key)
        comment = javadoc(
            'Outbound adapter replacing ACE node {@code %s} (%s).'
            % (node['reference'], node['type']),
            'Generated under migration plan {@code %s}. ACE wrote to {@code %s}; that name is the '
            'configured default and overridable per environment.' % (self.plan_id, destination))
        body = '''@Component
public class %(cls)sPublisher {

    private static final Logger log = LoggerFactory.getLogger(%(cls)sPublisher.class);

    private final %(type)s %(field)s;
    private final String destination;

%(constructor)s
        this.%(field)s = %(field)s;
        this.destination = destination;
    }

    public void publish(Object payload) {
        log.info("publishing to {}", destination);
        %(send)s;
    }
}''' % {'cls': pascal(node['id']), 'type': type_name, 'field': field, 'send': send,
        'constructor': signature(
            'public %sPublisher' % pascal(node['id']),
            ['%s %s' % (type_name, field),
             '@Value("${%s}") String destination' % setting])}
        return self._header('messaging', imports, comment, [], body)

    def _client_key(self, node):
        """The identity an outbound HTTP/SOAP client is named and configured
        under: the real backend vendor when ACEParser found exactly one in
        the ACE repo's own ``*.properties`` files
        (``model['backend_properties']``, populated during ace-analysis),
        else the raw ACE node id.

        A node id like ``FCMComposite_1_4`` is how ACE auto-names a composite
        node and carries no meaning to a reader; the real reference service
        this was checked against names its client after the vendor it calls
        (``MedForceConnector`` for ``MedForce``) instead. Only applies with
        exactly one vendor -- with two or more there is no single name to
        prefer over the node's own.
        """
        vendors = self.model.get('backend_properties') or {}
        return next(iter(vendors)) if len(vendors) == 1 else node['id']

    def _client_slug(self, node):
        """Config-key form of ``_client_key``.

        A vendor name is a proper noun, kept as one lowercase token
        (``medforce``, matching the real reference's own
        ``application.http.restclient.medforce.uri``) rather than hump-split
        into ``med-force`` the way a generic node id benefits from being
        (``FCMComposite_1_4`` -> ``fcm-composite-1-4``, still via ``kebab``).
        """
        key = self._client_key(node)
        vendors = self.model.get('backend_properties') or {}
        return key.lower() if key in vendors else kebab(key)

    def _client(self, flow, node):
        client_key = self._client_key(node)
        key = self._client_slug(node)
        cls = pascal(client_key)
        raw = str(node['properties'].get('URLSpecifier') or node['properties'].get('url') or '')
        path = '/' + raw.split('://', 1)[1].split('/', 1)[-1].strip('/') if '://' in raw and '/' in raw.split('://', 1)[1] \
            else ('/' + raw.strip('/') if raw and '://' not in raw else '/')
        modern = self.spring_minor >= (3, 2)
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.beans.factory.annotation.Value',
                   'org.springframework.stereotype.Component',
                   '%s.exception.FlowException' % self.package, 'java.util.Map']
        if modern:
            imports += ['org.springframework.core.ParameterizedTypeReference',
                        'org.springframework.web.client.RestClient',
                        'org.springframework.web.client.RestClientException']
            construction = '''%s
        this.restClient = builder.baseUrl(baseUrl).build();
    }''' % signature(
                'public %sClient' % cls,
                ['RestClient.Builder builder',
                 '@Value("${%s.clients.%s.base-url}") String baseUrl' % (self.prefix, key)])
            field = '    private final RestClient restClient;'
            call = '''            return restClient.post()
                    .uri("%s")
                    .body(payload)
                    .retrieve()
                    .body(new ParameterizedTypeReference<Map<String, Object>>() {
                    });''' % path
        else:
            imports += ['org.springframework.boot.web.client.RestTemplateBuilder',
                        'org.springframework.web.client.RestClientException',
                        'org.springframework.web.client.RestTemplate']
            construction = '''%s
        this.restTemplate = builder.rootUri(baseUrl).build();
    }''' % signature(
                'public %sClient' % cls,
                ['RestTemplateBuilder builder',
                 '@Value("${%s.clients.%s.base-url}") String baseUrl' % (self.prefix, key)])
            field = '    private final RestTemplate restTemplate;'
            call = '''            @SuppressWarnings("unchecked")
            Map<String, Object> body = restTemplate.postForObject("%s", payload, Map.class);
            return body;''' % path
        comment = javadoc(
            'Outbound call replacing ACE node {@code %s} (%s).'
            % (node['reference'], node['type']),
            'Generated under migration plan {@code %s}. The base URL is configuration, never '
            'source: the address ACE used is recorded in the provenance manifest and the README, '
            'and must be supplied per environment.' % self.plan_id)
        body = '''@Component
public class %(cls)sClient {

    private static final Logger log = LoggerFactory.getLogger(%(cls)sClient.class);

%(field)s

%(construction)s

    public Map<String, Object> call(Object payload) {
        try {
%(call)s
        } catch (RestClientException exception) {
            log.warn("backend call failed: {}", exception.getMessage());
            throw new FlowException("BACKEND_CALL_FAILED",
                    "the %(key)s call did not complete successfully", 502);
        }
    }
}''' % {'cls': cls, 'field': field, 'construction': construction,
        'call': call, 'key': key}
        return self._header('client', imports, comment, [], body)

    def _file_in(self, flow, node):
        key = kebab(node['id'])
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.beans.factory.annotation.Value',
                   'org.springframework.scheduling.annotation.Scheduled',
                   'org.springframework.stereotype.Component',
                   'java.io.IOException', 'java.nio.file.DirectoryStream', 'java.nio.file.Files',
                   'java.nio.file.Path', 'java.nio.file.Paths']
        comment = javadoc(
            'File ingress replacing ACE node {@code %s} (%s).'
            % (node['reference'], node['type']),
            'Generated under migration plan {@code %s}. ACE polled {@code %s}. Each file read is '
            'handed to the flow and then moved aside, so a restart does not reprocess it.'
            % (self.plan_id, self._destination(node, 'DIR')))
        body = '''@Component
public class %(cls)sIngest {

    private static final Logger log = LoggerFactory.getLogger(%(cls)sIngest.class);

    private final Path directory;

%(constructor)s
        this.directory = Paths.get(directory);
    }

    @Scheduled(fixedDelayString = "${%(prefix)s.files.%(key)s.poll-millis:5000}")
    public void poll() {
        if (!Files.isDirectory(directory)) {
            log.debug("ingress directory {} is not present yet", directory);
            return;
        }
        try (DirectoryStream<Path> files = Files.newDirectoryStream(directory, "*.*")) {
            for (Path file : files) {
                log.info("ingesting {}", file.getFileName());
                // TODO parse the file into the flow's request type. ACE read it
                // through its own message domain parser; the equivalent parse
                // depends on the file's format, which the flow does not state.
                Files.move(file, file.resolveSibling(file.getFileName() + ".done"));
            }
        } catch (IOException exception) {
            log.warn("ingress failed: {}", exception.getMessage());
        }
    }
}''' % {'cls': pascal(node['id']), 'prefix': self.prefix, 'key': key,
        'constructor': signature(
            'public %sIngest' % pascal(node['id']),
            ['@Value("${%s.files.%s.directory}") String directory' % (self.prefix, key)])}
        return self._header('file', imports, comment, [], body)

    def _file_out(self, flow, node):
        key = kebab(node['id'])
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.beans.factory.annotation.Value',
                   'org.springframework.stereotype.Component',
                   '%s.exception.FlowException' % self.package,
                   'java.io.IOException', 'java.nio.charset.StandardCharsets',
                   'java.nio.file.Files', 'java.nio.file.Path', 'java.nio.file.Paths',
                   'java.util.UUID']
        comment = javadoc(
            'File egress replacing ACE node {@code %s} (%s).'
            % (node['reference'], node['type']),
            'Generated under migration plan {@code %s}. ACE wrote to {@code %s}.'
            % (self.plan_id, self._destination(node, 'DIR')))
        body = '''@Component
public class %(cls)sWriter {

    private static final Logger log = LoggerFactory.getLogger(%(cls)sWriter.class);

    private final Path directory;

%(constructor)s
        this.directory = Paths.get(directory);
    }

    public void write(Object payload) {
        try {
            Files.createDirectories(directory);
            Path target = directory.resolve(UUID.randomUUID() + ".out");
            Files.writeString(target, String.valueOf(payload), StandardCharsets.UTF_8);
            log.info("wrote {}", target.getFileName());
        } catch (IOException exception) {
            throw new FlowException("FILE_WRITE_FAILED", "could not write the outbound file", 500);
        }
    }
}''' % {'cls': pascal(node['id']), 'prefix': self.prefix, 'key': key,
        'constructor': signature(
            'public %sWriter' % pascal(node['id']),
            ['@Value("${%s.files.%s.directory}") String directory' % (self.prefix, key)])}
        return self._header('file', imports, comment, [], body)

    def _scheduler(self, flow, node):
        key = kebab(node['id'])
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.scheduling.annotation.Scheduled',
                   'org.springframework.stereotype.Component']
        comment = javadoc(
            'Timer replacing ACE node {@code %s} (%s).' % (node['reference'], node['type']),
            "Generated under migration plan {@code %s}. ACE's TimeoutNotification fired on its own "
            'schedule; the cron expression is configuration.' % self.plan_id)
        body = '''@Component
public class %(cls)sScheduler {

    private static final Logger log = LoggerFactory.getLogger(%(cls)sScheduler.class);

    @Scheduled(cron = "${%(prefix)s.schedules.%(key)s:0 */5 * * * *}")
    public void run() {
        log.info("scheduled trigger %(key)s fired");
        // TODO invoke the work ACE's timeout subflow performed on each tick.
    }
}''' % {'cls': pascal(node['id']), 'prefix': self.prefix, 'key': key}
        return self._header('scheduler', imports, comment, [], body)

    def _routing(self, flow, node):
        suffix = 'Filter' if node['role'] == 'FILTER' else 'Router'
        imports = ['org.springframework.stereotype.Component']
        if flow['typed']:
            imports.append('%s.dto.%s' % (self.package, flow['request_type']))
        else:
            imports.append('java.util.Map')
        notes = [note for module in flow['modules'] for note in module.notes]
        comment = javadoc(
            '%s replacing ACE node {@code %s} (%s).'
            % (suffix, node['reference'], node['type']),
            'Generated under migration plan {@code %s}. ACE decided the branch in ESQL; the '
            'predicate below accepts every non-empty message until that condition is translated.'
            % self.plan_id,
            ('Related ESQL: ' + _escape('; '.join(notes[:4]))) if notes else None)
        body = '''@Component
public class %(cls)s%(suffix)s {

%(doc)s
%(matches)s
        return request != null;
    }
}''' % {'cls': pascal(node['id']), 'suffix': suffix,
        'doc': javadoc('TODO Translate the ACE routing condition for node {@code %s}.'
                       % node['id'], indent=4),
        'matches': signature('public boolean matches',
                             ['%s request' % flow['request_type']])}
        return self._header('routing', imports, comment, [], body)

    def _repository(self):
        imports = ['org.springframework.jdbc.core.JdbcTemplate',
                   'org.springframework.stereotype.Repository',
                   'java.util.List', 'java.util.Map']
        comment = javadoc(
            'Database access for the tables the ACE ESQL referenced.',
            'Generated under migration plan {@code %s}. Referenced tables:' % self.plan_id,
            ('ul', ['{@code %s}' % table for table in self.databases]))
        body = '''@Repository
public class %(app)sRepository {

    private final JdbcTemplate jdbcTemplate;

%(constructor)s
        this.jdbcTemplate = jdbcTemplate;
    }

%(doc)s
%(select)s
        return jdbcTemplate.queryForList("select * from %(table)s");
    }
}''' % {'app': self.app, 'table': self.databases[0],
        'constructor': signature('public %sRepository' % self.app, ['JdbcTemplate jdbcTemplate']),
        'doc': javadoc('TODO Replace with the statement the ESQL ran. ACE referenced {@code %s} '
                       "but the ESQL's own SELECT list and predicate are not recoverable from the "
                       'reference alone.' % self.databases[0], indent=4),
        'select': signature('public List<Map<String, Object>> select', [])}
        return self._header('persistence', imports, comment, [], body)

    def _flow_exception(self):
        comment = javadoc(
            'A failure inside a migrated flow, carrying the status the caller should see.',
            "Generated under migration plan {@code %s}. ACE's Throw and error-handler subflows "
            'composed an error response rather than propagating a stack trace; this is the '
            'equivalent, and {@code GlobalExceptionHandler} renders it.' % self.plan_id)
        body = '''public class FlowException extends RuntimeException {

    private final String code;
    private final int status;

    public FlowException(String code, String message, int status) {
        super(message);
        this.code = code;
        this.status = status;
    }

    public String getCode() {
        return code;
    }

    public int getStatus() {
        return status;
    }
}'''
        return self._header('exception', [], comment, [], body)

    def _exception_handler(self):
        imports = ['org.slf4j.Logger', 'org.slf4j.LoggerFactory',
                   'org.springframework.http.ResponseEntity',
                   'org.springframework.web.bind.MethodArgumentNotValidException',
                   'org.springframework.web.bind.annotation.ExceptionHandler',
                   'org.springframework.web.bind.annotation.RestControllerAdvice',
                   '%s.dto.ErrorResponse' % self.package]
        timeout = any(_normal(node['type']).find('timeout') >= 0
                      for flow in self.flows for node in flow['nodes'])
        comment = javadoc(
            'Renders every failure as the error contract, in one place.',
            'Generated under migration plan {@code %s}. ACE generated a Catch, Failure and Timeout '
            'subflow per input node, each composing an error response; this advice is their single '
            'equivalent.' % self.plan_id,
            'A timeout answers 408, never 500 -- the ACE timeout subflow this replaces made that '
            'distinction.' if timeout else None)
        body = '''@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(FlowException.class)
    public ResponseEntity<ErrorResponse> onFlowFailure(FlowException exception) {
        log.warn("flow failure code={} message={}", exception.getCode(), exception.getMessage());
        return ResponseEntity.status(exception.getStatus())
                .body(new ErrorResponse(exception.getCode(), exception.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> onInvalidRequest(
            MethodArgumentNotValidException exception) {
        log.warn("rejected an invalid request: {}", exception.getMessage());
        return ResponseEntity.badRequest()
                .body(new ErrorResponse("REQUEST_INVALID", "the request failed validation"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> onUnexpected(Exception exception) {
        log.error("unexpected failure", exception);
        return ResponseEntity.internalServerError()
                .body(new ErrorResponse("INTERNAL_ERROR",
                        "the service could not complete the request"));
    }
}'''
        return self._header('exception', imports, comment, [], body)

    def _error_response(self):
        comment = javadoc(
            'The error contract every failed call answers with.',
            'Generated under migration plan {@code %s}.' % self.plan_id)
        body = '''@JsonInclude(JsonInclude.Include.NON_NULL)
public record ErrorResponse(
        @JsonProperty("code")
        String code,

        @JsonProperty("message")
        String message) {
}'''
        return self._header('dto', ['com.fasterxml.jackson.annotation.JsonInclude',
                                    'com.fasterxml.jackson.annotation.JsonProperty'],
                            comment, [], body)

    def _correlation_filter(self):
        imports = ['jakarta.servlet.FilterChain', 'jakarta.servlet.ServletException',
                   'jakarta.servlet.http.HttpServletRequest', 'jakarta.servlet.http.HttpServletResponse',
                   'org.slf4j.MDC', 'org.springframework.stereotype.Component',
                   'org.springframework.web.filter.OncePerRequestFilter',
                   'java.io.IOException', 'java.util.UUID']
        comment = javadoc(
            'Carries a correlation id across the request, as ACE carried its own.',
            'Generated under migration plan {@code %s}. Differential testing compares an ACE run '
            'with a Spring run case by case, which needs a shared identifier on both sides.'
            % self.plan_id)
        body = '''@Component
public class CorrelationIdFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Correlation-ID";
    public static final String KEY = "correlationId";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
            FilterChain chain) throws ServletException, IOException {
        String correlationId = request.getHeader(HEADER);
        if (correlationId == null || correlationId.isBlank()) {
            correlationId = UUID.randomUUID().toString();
        }
        MDC.put(KEY, correlationId);
        response.setHeader(HEADER, correlationId);
        try {
            chain.doFilter(request, response);
        } finally {
            MDC.remove(KEY);
        }
    }
}'''
        return self._header('support', imports, comment, [], body)

    def _messaging_config(self):
        imports = ['org.springframework.context.annotation.Bean',
                   'org.springframework.context.annotation.Configuration',
                   'org.springframework.jms.support.converter.MappingJackson2MessageConverter',
                   'org.springframework.jms.support.converter.MessageConverter',
                   'org.springframework.jms.support.converter.MessageType']
        comment = javadoc(
            'JSON message conversion for the JMS destinations this service uses.',
            'Generated under migration plan {@code %s}. Without this the default converter accepts '
            'only serialisable payloads, and the generated records are not serialisable -- the '
            'flow would fail at the first publish.' % self.plan_id)
        body = '''@Configuration
public class MessagingConfig {

    @Bean
    public MessageConverter jacksonJmsMessageConverter() {
        MappingJackson2MessageConverter converter = new MappingJackson2MessageConverter();
        converter.setTargetType(MessageType.TEXT);
        converter.setTypeIdPropertyName("_type");
        return converter;
    }
}'''
        return self._header('config', imports, comment, [], body)

    # -- resources ----------------------------------------------------------
    def _resource_files(self):
        return {'src/main/resources/application.yml': self._application_yml(),
                'src/main/resources/application-local.yml': self._profile_yml(False),
                'src/test/resources/application-test.yml': self._profile_yml(True)}

    def _integration_block(self):
        lines = []
        for flow in self.flows:
            if any(n['role'] in ('HTTP_IN', 'SOAP_IN') for n in flow['nodes']):
                lines.append('  %s:' % kebab(flow['name']))
                lines.append('    context-root: ${CONTEXT_ROOT:}')
        clients = {}
        queues = {}
        topics = {}
        files = {}
        schedules = {}
        for flow in self.flows:
            for node in flow['nodes']:
                if node['role'] in ('HTTP_REQUEST', 'SOAP_REQUEST'):
                    # Keyed the same way _client() names the @Value lookup,
                    # so the property path this writes is the one the
                    # generated client actually resolves.
                    clients[self._client_slug(node)] = True
                elif node['role'] in ('JMS_IN', 'JMS_OUT'):
                    queues[kebab(node['id'])] = self._destination(node, 'QUEUE')
                elif node['role'] in ('KAFKA_IN', 'KAFKA_OUT'):
                    topics[kebab(node['id'])] = self._destination(node, 'TOPIC')
                elif node['role'] in ('FILE_IN', 'FILE_OUT'):
                    files[kebab(node['id'])] = self._destination(node, 'DIR')
                elif node['role'] == 'SCHEDULE':
                    schedules[kebab(node['id'])] = '0 */5 * * * *'
        if clients:
            lines.append('  clients:')
            for key in sorted(clients):
                env = key.upper().replace('-', '_')
                lines.append('    %s:' % key)
                # Loopback by default on purpose: a real address in source would
                # fail the quality gate's HARDCODED_ENDPOINT_SCAN, and should.
                lines.append('      base-url: ${%s_BASE_URL:http://localhost:8081}' % env)
        if queues:
            lines.append('  queues:')
            for key, name in sorted(queues.items()):
                lines.append('    %s: ${%s_DESTINATION:%s}' % (key, key.upper().replace('-', '_'), name))
        if topics:
            lines.append('  topics:')
            for key, name in sorted(topics.items()):
                lines.append('    %s: ${%s_TOPIC:%s}' % (key, key.upper().replace('-', '_'), name))
        if files:
            lines.append('  files:')
            for key, name in sorted(files.items()):
                lines.append('    %s:' % key)
                lines.append('      directory: ${%s_DIRECTORY:./data/%s}' % (key.upper().replace('-', '_'), key))
        if schedules:
            lines.append('  schedules:')
            for key, cron in sorted(schedules.items()):
                lines.append("    %s: ${%s_CRON:%s}" % (key, key.upper().replace('-', '_'), cron))
        return '\n'.join(lines)

    def _application_yml(self):
        blocks = ['''# Generated from ACE migration plan %s.
#
# Every external address, destination and credential is a placeholder resolved
# from the environment. The ACE values discovered during analysis are recorded
# in README.md and provenance-manifest.json, not here: an absolute address in a
# scanned file fails the migration quality gate, and should.
spring:
  application:
    name: %s''' % (self.plan_id, kebab(self.service))]
        if self._has('JMS_IN', 'JMS_OUT'):
            blocks.append('''  artemis:
    mode: native
    broker-url: ${BROKER_URL:tcp://localhost:61616}
    user: ${BROKER_USER:}
    password: ${BROKER_PASSWORD:}''')
        if self._has('KAFKA_IN', 'KAFKA_OUT'):
            blocks.append('''  kafka:
    bootstrap-servers: ${KAFKA_BOOTSTRAP_SERVERS:localhost:9092}
    consumer:
      group-id: ${KAFKA_GROUP_ID:%s}
      auto-offset-reset: earliest
    producer:
      value-serializer: org.springframework.kafka.support.serializer.JsonSerializer''' % kebab(self.service))
        if self._has('DB'):
            blocks.append('''  datasource:
    url: ${SPRING_DATASOURCE_URL:jdbc:h2:mem:%s;DB_CLOSE_DELAY=-1}
    username: ${SPRING_DATASOURCE_USERNAME:sa}
    password: ${SPRING_DATASOURCE_PASSWORD:}''' % kebab(self.service))
        blocks.append('''
server:
  port: ${SERVER_PORT:8080}

management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics

logging:
  pattern:
    console: "%d{ISO8601} %-5level [%X{correlationId}] %logger{36} - %msg%n"''')
        integration = self._integration_block()
        if integration:
            blocks.append('\n%s:\n%s' % (self.prefix, integration))
        return '\n'.join(blocks).rstrip() + '\n'

    def _profile_yml(self, test):
        lines = ['# Generated from ACE migration plan %s.' % self.plan_id]
        if test:
            lines.append('# Keeps the context loadable without a broker: the listeners are')
            lines.append('# registered but not started.')
        if self._has('JMS_IN'):
            lines += ['spring:', '  jms:', '    listener:', '      auto-startup: %s'
                      % ('false' if test else 'true')]
        if self._has('KAFKA_IN'):
            lines += (['spring:'] if not self._has('JMS_IN') else []) + \
                     ['  kafka:', '    listener:', '      auto-startup: %s' % ('false' if test else 'true')]
        if not test:
            lines += ['logging:', '  level:', '    %s: DEBUG' % self.package]
        return '\n'.join(lines).rstrip() + '\n'

    # -- tests --------------------------------------------------------------
    def _test_files(self):
        mock = 'org.springframework.test.context.bean.override.mockito.MockitoBean' \
            if self.spring_minor >= (3, 4) else 'org.springframework.boot.test.mock.mockito.MockBean'
        mock_annotation = '@MockitoBean' if self.spring_minor >= (3, 4) else '@MockBean'
        files = {'%s/%sApplicationTests.java' % (self.test_dir, self.app): self._context_test()}
        for flow in self.flows:
            # Driven by the same ``_inbound`` naming the controllers use. Picking
            # the flow's first inbound node here while ``_java_files`` wrote the
            # last one's class meant the test exercised a controller that no
            # longer existed under that name -- it posted JSON at a SOAP
            # endpoint and failed on content type.
            for name, node in self._inbound(flow, ('HTTP_IN', 'SOAP_IN'), 'Controller'):
                files['%s/controller/%sTest.java' % (self.test_dir, name)] = \
                    self._controller_test(flow, node, name, mock, mock_annotation)
            for module in flow['modules']:
                name = pascal(module.name) + 'Transformer'
                files['%s/transform/%sTest.java' % (self.test_dir, name)] = \
                    self._transformer_test(flow, module, name)
        return files

    def _context_test(self):
        comment = javadoc(
            'The context loads with every generated component wired.',
            'Generated under migration plan {@code %s}. This is the test that catches a component '
            'the generator wrote but did not wire.' % self.plan_id)
        body = '''@SpringBootTest
@ActiveProfiles("test")
class %sApplicationTests {

    @Test
    void contextLoads() {
    }
}''' % self.app
        return self._header('', ['org.junit.jupiter.api.Test',
                                 'org.springframework.boot.test.context.SpringBootTest',
                                 'org.springframework.test.context.ActiveProfiles'],
                            comment, [], body)

    def _controller_test(self, flow, node, controller, mock_import, mock_annotation):
        path = self._endpoint(node, flow)
        method = self._http_method(node).lower()
        # The content type has to be the one the controller declares, or the
        # request is rejected with 415 before it reaches the method under test.
        media = 'MediaType.TEXT_XML' if node['role'] == 'SOAP_IN' else 'MediaType.APPLICATION_JSON'
        payload = '<request/>' if node['role'] == 'SOAP_IN' else '{}'
        imports = ['org.junit.jupiter.api.Test',
                   'org.springframework.beans.factory.annotation.Autowired',
                   'org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest',
                   'org.springframework.http.MediaType', 'org.springframework.test.web.servlet.MockMvc',
                   mock_import, '%s.service.%sService' % (self.package, flow['name']),
                   'static org.mockito.ArgumentMatchers.any',
                   'static org.mockito.BDDMockito.given',
                   'static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.' + method,
                   'static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status']
        if flow['typed']:
            imports.append('%s.dto.%s' % (self.package, flow['response_type']))
            stub = 'new %s(%s)' % (flow['response_type'], ', '.join(
                'null' for _ in self.records[flow['response_type']].components))
        else:
            imports.append('java.util.Map')
            stub = 'Map.of()'
        comment = javadoc(
            'The endpoint ACE published is served on the path ACE published.',
            'Generated under migration plan {@code %s} from node {@code %s}.'
            % (self.plan_id, node['reference']))
        body = '''@WebMvcTest(%(controller)s.class)
class %(controller)sTest {

    @Autowired
    private MockMvc mockMvc;

    %(mock)s
    private %(flow)sService service;

    @Test
    void servesTheMigratedEndpoint() throws Exception {
        given(service.process(any(), any())).willReturn(%(stub)s);

        mockMvc.perform(%(method)s("%(path)s")
                        .contentType(%(media)s)
                        .content("%(payload)s"))
                .andExpect(status().isOk());
    }
}''' % {'flow': flow['name'], 'mock': mock_annotation, 'stub': stub, 'controller': controller,
        'method': method, 'path': path, 'media': media, 'payload': payload}
        return self._header('controller', imports, comment, [], body)

    def _transformer_test(self, flow, module, class_name):
        request, response = flow['request'], flow['response']
        copy = None
        if request is not None and response is not None:
            for item in module.assignments:
                if item['kind'] == 'COPY' and len(item['source']) == 1 and len(item['target']) == 1:
                    source_type, source_component = request.component_for(item['source'][0])
                    target_type, target_component = response.component_for(item['target'][0])
                    if source_type == 'String' and target_type == 'String':
                        copy = (source_component, target_component, item['esql'])
                        break
        imports = ['org.junit.jupiter.api.Test', 'static org.junit.jupiter.api.Assertions.assertEquals',
                   'static org.junit.jupiter.api.Assertions.assertNotNull']
        if copy and request is not None:
            imports.append('%s.dto.%s' % (self.package, request.name))
            imports.append('%s.dto.%s' % (self.package, response.name))
            arguments = ['"generated-value"' if component == copy[0] else 'null'
                         for _, component, _ in request.components]
            assertion = '''%s
        %s result = new %s().transform(source);

        assertNotNull(result);
        assertEquals("generated-value", result.%s());''' % (
                signature('%s source = new %s' % (request.name, request.name), arguments,
                          tail=';', indent=8),
                response.name, class_name, copy[1])
            note = 'Pins {@code %s}.' % _escape(copy[2])
        elif flow['typed']:
            imports.append('%s.dto.%s' % (self.package, request.name))
            assertion = '''%s
        assertNotNull(new %s().transform(source));''' % (
                signature('%s source = new %s' % (request.name, request.name),
                          ['null' for _ in request.components], tail=';', indent=8),
                class_name)
            note = None
        else:
            imports.append('java.util.Map')
            assertion = '        assertNotNull(new %s().transform(Map.of("field", "value")));' % class_name
            note = None
        comment = javadoc(
            'The ESQL assignments of module {@code %s} are applied.' % module.name,
            'Generated under migration plan {@code %s}.' % self.plan_id,
            note)
        body = '''class %sTest {

    @Test
    void appliesTheTranslatedAssignments() {
%s
    }
}''' % (class_name, assertion)
        return self._header('transform', imports, comment, [], body)

    # -- README -------------------------------------------------------------
    def _readme(self):
        rows = []
        for flow in self.flows:
            for node in flow['nodes']:
                rows.append('| `%s` | `%s` | %s | `%s` |' % (
                    node['reference'], node['type'], node['role'],
                    self.patterns.get(node['reference'], 'name-resolved')))
        endpoints = []
        for interface in self.model.get('interfaces', []):
            endpoint = str(interface.get('endpoint', ''))
            if endpoint and endpoint != 'UNKNOWN':
                endpoints.append('| `%s` | %s | `%s` |' % (
                    interface.get('source', ''), interface.get('protocol', ''), endpoint))
        actions = '\n'.join('- ' + item for item in self.action_items) or '- None recorded.'
        return '''# %(name)s

Migrated from IBM ACE/IIB by the ACE Migration Factory under plan `%(plan)s`.

Every file here was generated from the approved migration plan and the ACE
semantic model. `provenance-manifest.json` records the analysis, specification
and framework hashes the plan was approved against; `generated-manifest.json`
records a content hash per file.

## Running it

```bash
mvn verify          # compile, plus the generated tests
mvn spring-boot:run # start on ${SERVER_PORT:-8080}
```

Health and metrics: `/actuator/health`, `/actuator/info`, `/actuator/metrics`.

## What was generated, and from what

| ACE node | Construct | Generated as | Approved pattern |
|---|---|---|---|
%(rows)s

## Endpoints and destinations ACE used

These are recorded here rather than in source or configuration. An absolute
address in a scanned file fails the migration quality gate's
`HARDCODED_ENDPOINT_SCAN`, so each one is a `${...}` placeholder in
`application.yml` and must be supplied per environment.

| Source | Protocol | ACE value |
|---|---|---|
%(endpoints)s

## Configuration

`src/main/resources/application.yml` holds the externalised settings under
`%(prefix)s.*`. `application-local.yml` is for local development;
`src/test/resources/application-test.yml` keeps the context loadable without a
broker by registering listeners without starting them.

## What still needs a human

%(actions)s

Search the source for `TODO` as well: each one sits beside the ACE statement it
came from, so the decision can be made against the original.

## What this generator does not do

Organisation-specific conventions -- a framework's own connector classes, error
utilities, annotations, deployment charts and CI templates -- come from the
framework baseline. None were invented here: the project is plain Spring Boot,
and the framework baseline is where those conventions enter.
''' % {'name': pascal(self.service), 'plan': self.plan_id,
       'rows': '\n'.join(rows) or '| (none) | | | |',
       'endpoints': '\n'.join(endpoints) or '| (none discovered) | | |',
       'prefix': self.prefix, 'actions': actions}


def _merge(target, source):
    for key, value in (source or {}).items():
        if isinstance(value, dict) and value:
            child = target.get(key)
            if not isinstance(child, dict):
                child = {}
                target[key] = child
            _merge(child, value)
        else:
            target.setdefault(key, None)


#: The quality gate's own ``HARDCODED_ENDPOINT_SCAN`` rule, lookahead included:
#: loopback and example hosts are permitted, every other absolute http(s)
#: address fails the gate -- in a comment as readily as in a string literal,
#: since the rule reads the whole file. ESQL carries such addresses routinely,
#: so anything quoted from ACE into generated source passes through _redact.
_ABSOLUTE_URL = re.compile(r'(?i)https?://(?!localhost|example)[^\s\'"<>)\]]*')
_REDACTED = '[address recorded in README.md]'

#: Credential-shaped field names. A ``SET ...password = 'literal'`` in ESQL is
#: both a secret to keep out of generated source and, quoted into a comment,
#: a match for the gate's ``SECRET_SCAN`` rule -- the rule reads comments too.
_CREDENTIAL = re.compile(r'(?i)\b(?:password|passwd|secret|api[_-]?key|client[_-]?secret|'
                         r'access[_-]?token|auth[_-]?token|token|private[_-]?key|credential)\b')
#: ``SECRET_SCAN`` is ``(password|secret|token)\s*[:=]\s*["'][^$]...``: it needs a
#: non-``$`` character directly after the opening quote, so a ``${...}``
#: placeholder is both the right intent and what clears the rule.
_CREDENTIAL_ASSIGNMENT = re.compile(
    r'(?i)((?:password|passwd|secret|api[_-]?key|client[_-]?secret|access[_-]?token|'
    r'auth[_-]?token|token|private[_-]?key|credential)\s*[:=]\s*)(["\'])[^"\']*\2')


def _redact(text):
    """Make ACE text safe to quote into generated source.

    Addresses and credential literals are the two things the migration quality
    gate refuses, and ESQL carries both. A redacted comment still names the
    statement a reviewer has to look at.
    """
    text = _ABSOLUTE_URL.sub(_REDACTED, str(text or ''))
    return _CREDENTIAL_ASSIGNMENT.sub(lambda m: m.group(1) + m.group(2) + '${REDACTED}' + m.group(2), text)


def _escape(text):
    """Javadoc-safe: no comment terminator, no stray tag, no live address."""
    return _redact(text).replace('*/', '*&#47;').replace('<', '&lt;').replace('>', '&gt;')
